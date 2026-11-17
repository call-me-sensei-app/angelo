/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The trampoline (doc/angelo/script_confinement.rst): `Deferred(function)` runs `function` as a confined region,
 * whoever calls it. What a confined script leaves to run later (a timer, a handler, a thread, a callback, a
 * callable it left in Angelo's objects) is held in this form.
 */

#include <Python.h>

#include <cstring>
#include <string>
#include <utility>
#include <vector>

#include "bpy_angelo_guard_authority.hh"
#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_journal.hh"
#include "bpy_angelo_guard_confine_paths.hh"
#include "bpy_angelo_guard_sha256.hh"

namespace blender::angelo_guard {

/* -------------------------------------------------------------------- */
/** \name The trampoline: `Deferred`
 * \{ */

struct Deferred {
  PyObject_HEAD
  PyObject *function;
};

static PyObject *deferred_call(PyObject *self, PyObject *args, PyObject *kwargs)
{
  /* A region: what it rebinds goes back and what it leaves is confined, as at a script's end. */
  if (native_stack_check() < 0) {
    return nullptr;
  }
  const RegionMark mark = region_enter();
  PyObject *result = PyObject_Call(((Deferred *)self)->function, args, kwargs);
  region_leave(mark, nullptr);
  return result;
}

static int deferred_traverse(PyObject *self, visitproc visit, void *arg)
{
  Py_VISIT(((Deferred *)self)->function);
  return 0;
}

static int deferred_clear(PyObject *self)
{
  Py_CLEAR(((Deferred *)self)->function);
  return 0;
}

static void deferred_dealloc(PyObject *self)
{
  PyObject_GC_UnTrack(self);
  deferred_clear(self);
  Py_TYPE(self)->tp_free(self);
}

static PyObject *deferred_richcompare(PyObject *self, PyObject *other, int op)
{
  /* Equal to what it runs: `handlers.remove(function)` and `function in handlers` keep working. */
  if (op != Py_EQ && op != Py_NE) {
    Py_RETURN_NOTIMPLEMENTED;
  }
  PyObject *mine = ((Deferred *)self)->function;
  PyObject *theirs = Py_TYPE(other) == &deferred_type ? ((Deferred *)other)->function : other;
  const bool equal = mine == theirs;
  return PyBool_FromLong(op == Py_EQ ? equal : !equal);
}

static Py_hash_t deferred_hash(PyObject *self)
{
  if (native_stack_check() < 0) {
    return -1;
  }
  depth++; /* The function's own `__hash__` is the script's: confined (N3). */
  const Py_hash_t hash = PyObject_Hash(((Deferred *)self)->function);
  depth--;
  return hash;
}

static PyObject *deferred_repr(PyObject *self)
{
  return PyUnicode_FromFormat("<confined %R>", ((Deferred *)self)->function);
}

PyTypeObject deferred_type = {
    /*ob_base*/ PyVarObject_HEAD_INIT(nullptr, 0)
    /*tp_name*/ "_bpy._angelo_guard.Deferred",
    /*tp_basicsize*/ sizeof(Deferred),
};

PyObject *deferred_function(PyObject *value)
{
  return Py_TYPE(value) == &deferred_type ? ((Deferred *)value)->function : nullptr;
}

PyObject *defer(PyObject *function)
{
  if (Py_TYPE(function) == &deferred_type) {
    Py_INCREF(function);
    return function;
  }
  Deferred *self = PyObject_GC_New(Deferred, &deferred_type);
  if (self == nullptr) {
    return nullptr;
  }
  Py_INCREF(function);
  self->function = function;
  PyObject_GC_Track((PyObject *)self);
  return (PyObject *)self;
}

int deferred_type_ready()
{
  deferred_type.tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC;
  deferred_type.tp_doc = "A callable an agent script left for later: it runs confined.";
  deferred_type.tp_call = deferred_call;
  deferred_type.tp_traverse = deferred_traverse;
  deferred_type.tp_clear = deferred_clear;
  deferred_type.tp_dealloc = deferred_dealloc;
  deferred_type.tp_richcompare = deferred_richcompare;
  deferred_type.tp_hash = deferred_hash;
  deferred_type.tp_repr = deferred_repr;
  return PyType_Ready(&deferred_type);
}

/** \} */

}  // namespace blender::angelo_guard
