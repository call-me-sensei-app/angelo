/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The confinement's folders, as Angelo registers them (doc/angelo/script_confinement.rst): private, startup,
 * tool-output, trusted and open folders and the private families, each add-only and refused while confined; and
 * `judge_path`, how a path would be judged for a confined thread.
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

bool add_each(PyObject *items, void (*add)(const std::string &))
{
  PyObject *iterator = PyObject_GetIter(items);
  if (iterator == nullptr) {
    return false;
  }
  while (PyObject *item = PyIter_Next(iterator)) {
    PyObject *text = PyOS_FSPath(item);
    if (text != nullptr && PyUnicode_Check(text)) {
      if (const char *utf8 = PyUnicode_AsUTF8(text)) {
        add(utf8);
      }
    }
    PyErr_Clear();
    Py_XDECREF(text);
    Py_DECREF(item);
  }
  Py_DECREF(iterator);
  return !PyErr_Occurred();
}

PyObject *guard_add_roots(PyObject * /*self*/, PyObject *args)
{
  PyObject *private_folders, *startup_folders, *families, *trusted, *tool_folders = nullptr;
  if (!PyArg_ParseTuple(
          args, "OOOO|O:add_roots", &private_folders, &startup_folders, &families, &trusted, &tool_folders))
  {
    return nullptr;
  }
  if (refuse_if_confined("add_roots")) {
    return nullptr;
  }
  /* Private, startup and tool-output roots refuse what they hold: one on a share nobody connected is held by its
   * text (`Roots::add`). Trusted roots admit, so such a root is dropped. */
  add_each(private_folders, [](const std::string &path) { folders().private_roots.add(path, true); });
  add_each(startup_folders, [](const std::string &path) { folders().startup_roots.add(path, true); });
  add_each(trusted, [](const std::string &path) { folders().trusted_roots.add(path); });
  if (tool_folders != nullptr) {
    add_each(tool_folders, [](const std::string &path) { folders().tool_roots.add(path, true); });
  }
  PyObject *iterator = PyObject_GetIter(families);
  if (iterator == nullptr) {
    return nullptr;
  }
  while (PyObject *pair = PyIter_Next(iterator)) {
    const char *parent = nullptr, *prefix = nullptr;
    PyObject *parent_path = nullptr;
    if (PyTuple_Check(pair) && PyTuple_GET_SIZE(pair) == 2) {
      parent_path = PyOS_FSPath(PyTuple_GET_ITEM(pair, 0));
      if (parent_path != nullptr && PyUnicode_Check(parent_path) && PyUnicode_Check(PyTuple_GET_ITEM(pair, 1))) {
        parent = PyUnicode_AsUTF8(parent_path);
        prefix = PyUnicode_AsUTF8(PyTuple_GET_ITEM(pair, 1));
      }
    }
    if (parent != nullptr && prefix != nullptr) {
      folders().families.add(parent, prefix);
    }
    PyErr_Clear();
    Py_XDECREF(parent_path);
    Py_DECREF(pair);
  }
  Py_DECREF(iterator);
  Py_RETURN_NONE;
}

/** `add_open_roots(folders, private_names)`: folders inside a private one that a confined script uses freely (each
 * chat's default workspace, opened as the file tools open it: `files.FileWorkspace(own=)`); below them, a folder
 * with one of `private_names` stays private. Add-only; refused while confined. */
PyObject *guard_add_open_roots(PyObject * /*self*/, PyObject *args)
{
  PyObject *open_folders, *private_names;
  if (!PyArg_ParseTuple(args, "OO:add_open_roots", &open_folders, &private_names)) {
    return nullptr;
  }
  if (refuse_if_confined("add_open_roots")) {
    return nullptr;
  }
  if (!add_each(private_names, [](const std::string &name) { folders().open_private_names.add(name); }) ||
      !add_each(open_folders, [](const std::string &path) { folders().open_roots.add(path); }))
  {
    return nullptr;
  }
  Py_RETURN_NONE;
}

PyObject *guard_judge_path(PyObject * /*self*/, PyObject *args)
{
  /* For tests and the Python fallback's parity: how a path would be judged for a confined thread. */
  PyObject *path;
  int write = 0;
  if (!PyArg_ParseTuple(args, "O&|p:judge_path", PyUnicode_FSDecoder, &path, &write)) {
    return nullptr;
  }
  const char *text = PyUnicode_AsUTF8(path);
  const Refusal refusal = text ? folders().judge(text, write) : Refusal::Private;
  Py_DECREF(path);
  return PyUnicode_FromString(refusal == Refusal::Private ? "private" :
                              refusal == Refusal::Startup    ? "startup" :
                              refusal == Refusal::ToolOutput ? "tool_output" :
                              refusal == Refusal::Network    ? "network" :
                                                               "");
}

}  // namespace blender::angelo_guard
