/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Work one thread hands another (doc/angelo/script_confinement.rst, "Work handed to another thread").
 *
 * A thread pool or a queue consumer that already runs (Angelo's own) cannot tell who put a callable on its queue: a
 * builtin a confined script put there would run unconfined. So Angelo's pools and consumers run only `WorkItem`s,
 * through `run_work`:
 *
 * - `WorkItem(function, args, kwargs)` records, in C, when it is made, whether the thread that made it was confined;
 *   it has no attribute a script can set, and its type is immutable.
 * - `run_work(item, *extra)` runs it, confined when its maker was; anything but a `WorkItem` is refused.
 *
 * So a script can only put work on Angelo's pools that runs confined, however it reaches the queue.
 */

#include <Python.h>

#include "bpy_angelo_guard_confine_intern.hh"

namespace blender::angelo_guard {

struct WorkItem {
  PyObject_HEAD
  PyObject *function;
  PyObject *args;   /* tuple */
  PyObject *kwargs; /* dict or null */
  bool made_confined;
};

static PyObject *work_new(PyTypeObject *type, PyObject *args, PyObject *kwargs)
{
  static const char *keywords[] = {"function", "args", "kwargs", nullptr};
  PyObject *function, *call_args = nullptr, *call_kwargs = nullptr;
  if (!PyArg_ParseTupleAndKeywords(
          args, kwargs, "O|OO:WorkItem", const_cast<char **>(keywords), &function, &call_args, &call_kwargs))
  {
    return nullptr;
  }
  if (!PyCallable_Check(function)) {
    PyErr_SetString(PyExc_TypeError, "WorkItem: not callable");
    return nullptr;
  }
  PyObject *tuple = (call_args == nullptr || call_args == Py_None) ? PyTuple_New(0) : PySequence_Tuple(call_args);
  if (tuple == nullptr) {
    return nullptr;
  }
  PyObject *dict = nullptr;
  if (call_kwargs != nullptr && call_kwargs != Py_None) {
    dict = PyDict_Copy(call_kwargs);
    if (dict == nullptr) {
      Py_DECREF(tuple);
      return nullptr;
    }
  }
  WorkItem *self = (WorkItem *)type->tp_alloc(type, 0);
  if (self == nullptr) {
    Py_DECREF(tuple);
    Py_XDECREF(dict);
    return nullptr;
  }
  self->function = Py_NewRef(function);
  self->args = tuple;
  self->kwargs = dict;
  self->made_confined = confined_now();
  return (PyObject *)self;
}

static int work_traverse(PyObject *self, visitproc visit, void *arg)
{
  WorkItem *item = (WorkItem *)self;
  Py_VISIT(item->function);
  Py_VISIT(item->args);
  Py_VISIT(item->kwargs);
  return 0;
}

static int work_clear(PyObject *self)
{
  WorkItem *item = (WorkItem *)self;
  Py_CLEAR(item->function);
  Py_CLEAR(item->args);
  Py_CLEAR(item->kwargs);
  return 0;
}

static void work_dealloc(PyObject *self)
{
  PyObject_GC_UnTrack(self);
  work_clear(self);
  Py_TYPE(self)->tp_free(self);
}

static PyObject *work_made_confined(PyObject *self, void * /*closure*/)
{
  return PyBool_FromLong(((WorkItem *)self)->made_confined);
}

static PyGetSetDef work_getset[] = {
    {"made_confined", work_made_confined, nullptr, "Whether a confined thread made it (read-only).", nullptr},
    {nullptr, nullptr, nullptr, nullptr, nullptr},
};

PyTypeObject work_item_type = {
    /*ob_base*/ PyVarObject_HEAD_INIT(nullptr, 0)
    /*tp_name*/ "_bpy._angelo_guard.WorkItem",
    /*tp_basicsize*/ sizeof(WorkItem),
};

int work_item_type_ready()
{
  work_item_type.tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC;
  work_item_type.tp_doc =
      "WorkItem(function, args=(), kwargs=None): work for another thread; it runs confined when a confined thread made "
      "it (run_work).";
  work_item_type.tp_new = work_new;
  work_item_type.tp_traverse = work_traverse;
  work_item_type.tp_clear = work_clear;
  work_item_type.tp_dealloc = work_dealloc;
  work_item_type.tp_getset = work_getset;
  return PyType_Ready(&work_item_type);
}

PyObject *run_work(PyObject * /*self*/, PyObject *args)
{
  if (PyTuple_GET_SIZE(args) < 1 || Py_TYPE(PyTuple_GET_ITEM(args, 0)) != &work_item_type) {
    refuse(GUARD, "run_work: not a WorkItem");
    return nullptr;
  }
  WorkItem *item = (WorkItem *)PyTuple_GET_ITEM(args, 0);
  PyObject *extra = PyTuple_GetSlice(args, 1, PyTuple_GET_SIZE(args));
  PyObject *call_args = extra ? PySequence_Concat(item->args, extra) : nullptr;
  Py_XDECREF(extra);
  if (call_args == nullptr) {
    return nullptr;
  }
  PyObject *result;
  if (item->made_confined) {
    const RegionMark mark = region_enter(); /* As a trampoline: a region, its leftovers confined. */
    result = PyObject_Call(item->function, call_args, item->kwargs);
    region_leave(mark, nullptr);
  }
  else {
    result = PyObject_Call(item->function, call_args, item->kwargs);
  }
  Py_DECREF(call_args);
  return result;
}

}  // namespace blender::angelo_guard
