/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * The file a document save wrote, as the save itself identified it (`_bpy._angelo_saved`, read by
 * the add-on's `document_file_identity.py`).
 *
 * - `saved_file()`: while a save's `save_post` handlers run, `(path, st_dev, st_ino, st_size,
 *   st_mtime_ns, st_birthtime_ns)` of the file it wrote, held open across its rename and
 *   identified through that handle (#BlendFileWriteParams::written), in the values CPython's
 *   `os.stat` reports (#BLI_file_identity_held); None otherwise. A handler's own stat of the path
 *   comes after the rename and could find a file another program put there since.
 * - `read_file()`: the same fields of the file the last document load (or other main read)
 *   read, identified on the descriptor it read through as it opened it (#BLO_angelo_last_read);
 *   None when there is none. A stat of the path before or after the load could be of another
 *   file than the one read.
 * - `write_only(path, ids)`: only the given datablocks of the open file written to `path`,
 *   uncompressed, each from its own data exactly as a save writes it (the save's writer over the
 *   same datablocks, never copies: #BlendFileWriteParams::only_ids), with nothing else of a save
 *   done (no handler, thumbnail, autopack or path remap); the Edit or Sculpt Mode data of an
 *   object written (or whose data is) flushed first, as a save does, and never another's. The
 *   parallel merge's privacy check (`worker_merge_verify.saved_copy`) reads what a save would
 *   write of the datablocks a merge keeps at their own cost, never the whole document's. Refused
 *   to a confined script and off the main thread.
 *
 * Main thread only.
 */

#include <Python.h>

#include <string>

#include "MEM_guardedalloc.h"

#include "BLI_fileops.hh"
#include "BLI_set.hh"
#include "BLI_threads.hh"

#include "BKE_global.hh"
#include "BKE_lib_id.hh"
#include "BKE_main.hh"
#include "BKE_report.hh"

#include "BLO_readfile.hh"
#include "BLO_writefile.hh"

#include "DNA_ID.h"
#include "DNA_object_types.h"

#include "ED_util.hh"

#include "WM_api.hh"

#include "bpy_angelo_guard_confine.hh"
#include "bpy_angelo_saved.hh"
#include "bpy_rna.hh"

namespace blender {

static PyObject *identity_tuple(const std::string &path, const FileIdentity &identity)
{
  PyObject *device = identity.device_signed ?
                         PyLong_FromLongLong(static_cast<long long>(identity.device)) :
                         PyLong_FromUnsignedLongLong(identity.device);
  /* `st_ino` is `(index_high << 64) | index_low`, as CPython builds a 128-bit Windows file ID. */
  PyObject *low = PyLong_FromUnsignedLongLong(identity.index_low);
  PyObject *high = PyLong_FromUnsignedLongLong(identity.index_high);
  PyObject *shift = PyLong_FromLong(64);
  PyObject *shifted = (high && shift) ? PyNumber_Lshift(high, shift) : nullptr;
  PyObject *inode = (shifted && low) ? PyNumber_Or(shifted, low) : nullptr;
  Py_XDECREF(low);
  Py_XDECREF(high);
  Py_XDECREF(shift);
  Py_XDECREF(shifted);
  /* As `os.fsdecode`: bytes that aren't UTF-8 (POSIX) are kept, escaped. */
  PyObject *name = PyUnicode_DecodeFSDefault(path.c_str());
  if (device == nullptr || inode == nullptr || name == nullptr) {
    Py_XDECREF(device);
    Py_XDECREF(inode);
    Py_XDECREF(name);
    return nullptr;
  }
  return Py_BuildValue("(NNNLLL)",
                       name,
                       device,
                       inode,
                       static_cast<long long>(identity.size),
                       static_cast<long long>(identity.modified_ns),
                       static_cast<long long>(identity.created_ns));
}

static PyObject *saved_file(PyObject * /*self*/, PyObject * /*args*/)
{
  std::string path;
  FileIdentity identity;
  if (!WM_angelo_saved_file(&path, &identity)) {
    Py_RETURN_NONE;
  }
  return identity_tuple(path, identity);
}

static PyObject *read_file(PyObject * /*self*/, PyObject * /*args*/)
{
  std::string path;
  FileIdentity identity;
  if (!BLO_angelo_last_read(&path, &identity)) {
    Py_RETURN_NONE;
  }
  return identity_tuple(path, identity);
}

static PyObject *write_only(PyObject * /*self*/, PyObject *args)
{
  PyObject *path_object = nullptr, *ids_object = nullptr;
  if (!PyArg_ParseTuple(args, "O&O:write_only", PyUnicode_FSConverter, &path_object, &ids_object)) {
    return nullptr;
  }
  const std::string path(PyBytes_AS_STRING(path_object));
  Py_DECREF(path_object);
  if (!BLI_thread_is_main()) {
    PyErr_SetString(PyExc_RuntimeError, "write_only: the open document is written on the main thread only");
    return nullptr;
  }
  if (BPY_angelo_guard_thread_confined()) {
    BPY_angelo_guard_raise("a confined script can't write the document's datablocks to a file",
                           "_bpy._angelo_saved.write_only");
    return nullptr;
  }
  Main *bmain = G_MAIN;
  Set<const ID *> in_main;
  ID *id;
  FOREACH_MAIN_ID_BEGIN (bmain, id) {
    in_main.add(id);
  }
  FOREACH_MAIN_ID_END;
  PyObject *items = PySequence_Fast(ids_object, "write_only: ids must be a sequence of datablocks");
  if (items == nullptr) {
    return nullptr;
  }
  Set<const ID *> only;
  const Py_ssize_t count = PySequence_Fast_GET_SIZE(items);
  for (Py_ssize_t index = 0; index < count; index++) {
    ID *item = nullptr;
    /* Only a datablock of the open file: never a pointer it no longer holds. */
    if (!pyrna_id_FromPyObject(PySequence_Fast_GET_ITEM(items, index), &item) || item == nullptr ||
        !in_main.contains(item))
    {
      Py_DECREF(items);
      PyErr_SetString(PyExc_TypeError, "write_only: every id must be a datablock of the open file");
      return nullptr;
    }
    only.add(item);
  }
  Py_DECREF(items);
  /* As a save does first, for what is written only: an object asked for, or whose data is, has its Edit or Sculpt
   * Mode data flushed to it. Never the person's own objects: flushing those costs their whole edit (a large mesh's
   * BMesh written back, a multires or dyntopo sculpt) on every merge, and changes their data. */
  for (Object &object : bmain->objects) {
    if (only.contains(&object.id) || (object.data && only.contains(static_cast<const ID *>(object.data)))) {
      ED_editors_flush_edits_for_object(bmain, &object);
    }
  }
  BlendFileWriteParams params{};
  params.remap_mode = BLO_WRITE_PATH_REMAP_NONE;
  params.use_save_as_copy = true;
  params.only_ids = &only;
  ReportList reports;
  BKE_reports_init(&reports, RPT_STORE);
  const bool written = BLO_write_file(
      bmain, path.c_str(), G.fileflags & ~G_FILE_COMPRESS, &params, &reports);
  std::string message;
  if (!written) {
    if (char *text = BKE_reports_string(&reports, RPT_ERROR)) {
      message = text;
      MEM_delete(text);
    }
  }
  BKE_reports_free(&reports);
  if (!written) {
    PyErr_Format(PyExc_OSError, "write_only: %s", message.empty() ? "the write failed" : message.c_str());
    return nullptr;
  }
  Py_RETURN_TRUE;
}

static PyMethodDef methods[] = {
    {"saved_file",
     saved_file,
     METH_NOARGS,
     "saved_file(): (path, st_dev, st_ino, st_size, st_mtime_ns, st_birthtime_ns) of the file the "
     "document save whose save_post handlers run now wrote, through a handle held across its "
     "rename; None outside "
     "them."},
    {"read_file",
     read_file,
     METH_NOARGS,
     "read_file(): (path, st_dev, st_ino, st_size, st_mtime_ns, st_birthtime_ns) of the file the "
     "last document load read, identified on the descriptor it read through; None when there is "
     "none."},
    {"write_only",
     write_only,
     METH_VARARGS,
     "write_only(path, ids): only these datablocks of the open file written to path, uncompressed, "
     "each as a save writes it, and nothing else of a save done; refused to a confined script."},
    {nullptr, nullptr, 0, nullptr},
};

static PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    "_bpy._angelo_saved",
    nullptr,
    0,
    methods,
    nullptr,
    nullptr,
    nullptr,
    nullptr,
};

PyObject *BPY_angelo_saved_module()
{
  return PyModule_Create(&module);
}

}  // namespace blender
