/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * `_bpy._angelo_guard`: one module, two owners. The confinement adds its methods
 * (`BPY_angelo_guard_confine_add`), the authority its own (`BPY_angelo_guard_authority_add`).
 *
 * Nothing a script can reach here holds state or can be changed: the module's type refuses `setattr` and `delattr`
 * and gives a copy as its `__dict__`, and the functions only read or add to state kept in C
 * (doc/angelo/script_confinement.rst; MUST-5 of the grant guard's re-review: no cell, no closure, no attribute).
 */

#include <Python.h>

#include "bpy_angelo_guard.hh"
#include "bpy_angelo_guard_authority.hh"
#include "bpy_angelo_guard_confine.hh"

namespace blender {

static PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    "_bpy._angelo_guard",
    "Angelo's guard state, kept in C (doc/angelo/script_confinement.rst).",
    0,
    nullptr,
    nullptr,
    nullptr,
    nullptr,
    nullptr,
};

static int frozen_setattro(PyObject * /*self*/, PyObject *name, PyObject * /*value*/)
{
  PyErr_Format(PyExc_AttributeError, "_bpy._angelo_guard is read-only (%R)", name);
  return -1;
}

/* The module's contents as created, held only here: every lookup is served from it first, so even
 * `types.ModuleType.__setattr__(module, ...)` (which reaches the module's own dict past the type's `setattro`)
 * changes nothing Angelo's code calls. */
static PyObject *real_contents = nullptr;

static PyObject *frozen_getattro(PyObject *self, PyObject *name)
{
  if (real_contents != nullptr && PyUnicode_Check(name)) {
    if (PyUnicode_CompareWithASCIIString(name, "__dict__") == 0) {
      return PyDict_Copy(real_contents); /* A copy: `dir()` works, and a write to it changes nothing served. */
    }
    PyObject *found = PyDict_GetItemWithError(real_contents, name);
    if (found != nullptr) {
      return Py_NewRef(found);
    }
    if (PyErr_Occurred()) {
      return nullptr;
    }
  }
  return PyObject_GenericGetAttr(self, name);
}

/** A module subclass whose instances cannot be changed from Python. */
static PyTypeObject *frozen_module_type()
{
  static PyTypeObject *type = nullptr;
  if (type != nullptr) {
    return type;
  }
  PyType_Slot slots[] = {
      {Py_tp_setattro, (void *)frozen_setattro},
      {Py_tp_getattro, (void *)frozen_getattro},
      {0, nullptr},
  };
  PyType_Spec spec = {
      "_bpy._angelo_guard_module",
      0,
      0,
      Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE,
      slots,
  };
  PyObject *bases = PyTuple_Pack(1, (PyObject *)&PyModule_Type);
  type = (PyTypeObject *)PyType_FromSpecWithBases(&spec, bases);
  Py_XDECREF(bases);
  return type;
}

PyObject *BPY_angelo_guard_module()
{
  PyObject *result = PyModule_Create(&module);
  if (result == nullptr) {
    return nullptr;
  }
  BPY_angelo_guard_confine_add(result);
  BPY_angelo_guard_authority_add(result);
  Py_XSETREF(real_contents, PyDict_Copy(PyModule_GetDict(result)));
  if (PyTypeObject *frozen = frozen_module_type()) {
    /* Last: every method is in place. `__class__` assignment between module types is allowed once, here. */
    if (PyObject_SetAttrString(result, "__class__", (PyObject *)frozen) != 0) {
      PyErr_Print();
    }
  }
  else {
    PyErr_Print();
  }
  return result;
}

}  // namespace blender
