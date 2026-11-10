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
 *
 * Main thread only.
 */

#include <Python.h>

#include <string>

#include "BLI_fileops.hh"

#include "BLO_readfile.hh"

#include "WM_api.hh"

#include "bpy_angelo_saved.hh"

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
