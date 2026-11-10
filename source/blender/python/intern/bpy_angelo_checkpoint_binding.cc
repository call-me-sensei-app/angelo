/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 */

#include <cstring>

#include "BLI_hash_xxh3.hh"
#include "BLI_memory_utils.hh"

#include "bpy_angelo_checkpoint_binding.hh"

namespace blender {
namespace {

struct CaptureHasher {
  PyObject_HEAD
  /** The capture capsule this hasher binds, kept alive while the hasher lives. */
  PyObject *capsule;
  WMCheckpointRole role;
  XXH3_128Stream *stream;
  /** Held by ``update`` while it feeds ``stream``; ``finalized`` is read and written with the GIL. */
  CheckpointHasherUse *use;
  bool finalized;
};

PyObject *hasher_type = nullptr;

CaptureHasher *as_hasher(PyObject *object)
{
  return hasher_type && Py_IS_TYPE(object, reinterpret_cast<PyTypeObject *>(hasher_type)) ?
             reinterpret_cast<CaptureHasher *>(object) :
             nullptr;
}

void hasher_dealloc(PyObject *self)
{
  CaptureHasher *hasher = reinterpret_cast<CaptureHasher *>(self);
  PyTypeObject *type = Py_TYPE(self);
  delete hasher->stream;
  hasher->stream = nullptr;
  delete hasher->use;
  hasher->use = nullptr;
  Py_CLEAR(hasher->capsule);
  type->tp_free(self);
  Py_DECREF(type);
}

/** Any thread, one at a time: the stream is fed with the GIL released, and the caller's references
 * keep the hasher and the buffer alive until it returns. */
PyObject *hasher_update(PyObject *self, PyObject *data)
{
  CaptureHasher *hasher = reinterpret_cast<CaptureHasher *>(self);
  if (!hasher->use->try_acquire()) {
    PyErr_SetString(PyExc_RuntimeError, "The checkpoint content hasher is being fed by another call");
    return nullptr;
  }
  BLI_SCOPED_DEFER([&]() { hasher->use->release(); });
  if (hasher->finalized) {
    PyErr_SetString(PyExc_ValueError, "The checkpoint content hasher was already compared");
    return nullptr;
  }
  Py_buffer view;
  if (PyObject_GetBuffer(data, &view, PyBUF_SIMPLE) != 0) {
    return nullptr;
  }
  XXH3_128Stream &stream = *hasher->stream;
  Py_BEGIN_ALLOW_THREADS;
  stream.update(view.buf, size_t(view.len));
  Py_END_ALLOW_THREADS;
  PyBuffer_Release(&view);
  Py_RETURN_NONE;
}

PyMethodDef hasher_methods[] = {
    {"update", hasher_update, METH_O, "update(buffer): append the next bytes read, in order."},
    {nullptr, nullptr, 0, nullptr},
};

PyType_Slot hasher_slots[] = {
    {Py_tp_dealloc, reinterpret_cast<void *>(hasher_dealloc)},
    {Py_tp_methods, hasher_methods},
    {Py_tp_doc,
     const_cast<char *>("A checkpoint component's content hasher, bound to one capture and role. "
                        "Created only by _angelo_checkpoint.capture_hasher.")},
    {0, nullptr},
};

PyType_Spec hasher_spec = {
    "_angelo_checkpoint.CaptureHasher",
    sizeof(CaptureHasher),
    0,
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_DISALLOW_INSTANTIATION,
    hasher_slots,
};

bool ensure_hasher_type()
{
  if (!hasher_type) {
    hasher_type = PyType_FromSpec(&hasher_spec);
  }
  return hasher_type != nullptr;
}

}  // namespace

PyObject *BPY_checkpoint_hasher_new(PyObject *capsule, const WMCheckpointRole role)
{
  if (!ensure_hasher_type()) {
    return nullptr;
  }
  PyTypeObject *type = reinterpret_cast<PyTypeObject *>(hasher_type);
  PyObject *object = type->tp_alloc(type, 0);
  if (!object) {
    return nullptr;
  }
  CaptureHasher *hasher = reinterpret_cast<CaptureHasher *>(object);
  hasher->capsule = Py_NewRef(capsule);
  hasher->role = role;
  hasher->stream = new XXH3_128Stream();
  hasher->use = new CheckpointHasherUse();
  hasher->finalized = false;
  return object;
}

const char *BPY_checkpoint_hasher_bind(PyObject *object,
                                       PyObject *capsule,
                                       const WMCheckpointRole role,
                                       const WMCheckpointContentBinding &binding)
{
  CaptureHasher *hasher = as_hasher(object);
  if (!hasher) {
    return "not a native content hasher";
  }
  if (hasher->capsule != capsule) {
    return "a hasher of another capture";
  }
  if (hasher->role != role) {
    return "a hasher of another component";
  }
  if (hasher->finalized) {
    return "a hasher already compared";
  }
  if (hasher->use->held()) {
    /* Its bytes are still arriving: never compare a partial hash, and spend it like any refusal. */
    hasher->finalized = true;
    return "a hasher still being fed";
  }
  /* Spent from here on, equal or not: a hasher is compared at most once. */
  hasher->finalized = true;
  if (!binding.present) {
    return "the component has no content binding";
  }
  if (hasher->stream->bytes() != binding.bytes) {
    return "the hasher read another length than was written";
  }
  const std::array<uint8_t, 16> digest = hasher->stream->finalize();
  if (std::memcmp(digest.data(), binding.hash.data(), digest.size()) != 0) {
    return "the published bytes differ from those written";
  }
  return nullptr;
}

}  // namespace blender
