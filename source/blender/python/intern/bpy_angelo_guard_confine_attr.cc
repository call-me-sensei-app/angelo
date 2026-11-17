/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * `ConfinedAttr`: what a class a confined script made holds becomes this descriptor at the script's end
 * (bpy_angelo_guard_confine_region.cc), so the class's dunders, destructor, properties and methods run confined
 * whoever reaches them, whenever.
 */

#include <Python.h>

#include <cstring>
#include <initializer_list>
#include <utility>
#include <vector>

#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_journal.hh"

namespace blender::angelo_guard {

/* -------------------------------------------------------------------- */
/** \name `ConfinedAttr`: a class attribute that runs confined
 * \{ */

struct ConfinedAttr {
  PyObject_HEAD
  PyObject *original;
};

static PyObject *attr_get(PyObject *self, PyObject *object, PyObject *type)
{
  PyObject *original = ((ConfinedAttr *)self)->original;
  descrgetfunc get = Py_TYPE(original)->tp_descr_get;
  PyObject *bound;
  const char *kind = Py_TYPE(original)->tp_name;
  if (get != nullptr && (PyFunction_Check(original) || strcmp(kind, "staticmethod") == 0 ||
                         strcmp(kind, "classmethod") == 0))
  {
    bound = get(original, object, type); /* Binding runs no code of the script's. */
  }
  else if (get != nullptr) {
    if (native_stack_check() < 0) {
      return nullptr;
    }
    const RegionMark mark = region_enter();
    bound = get(original, object, type); /* A property's getter runs here: confined. */
    region_leave(mark, nullptr);
  }
  else {
    bound = Py_NewRef(original);
  }
  if (bound == nullptr || !PyCallable_Check(bound)) {
    return bound;
  }
  PyObject *deferred = defer(bound);
  Py_DECREF(bound);
  return deferred;
}

static int attr_set(PyObject *self, PyObject *object, PyObject *value)
{
  PyObject *original = ((ConfinedAttr *)self)->original;
  descrsetfunc set = Py_TYPE(original)->tp_descr_set;
  if (set == nullptr) {
    PyErr_SetString(PyExc_AttributeError, "read-only attribute");
    return -1;
  }
  if (native_stack_check() < 0) {
    return -1;
  }
  const RegionMark mark = region_enter();
  const int result = set(original, object, value);
  region_leave(mark, nullptr);
  return result;
}

static PyObject *attr_call(PyObject *self, PyObject *args, PyObject *kwargs)
{
  if (native_stack_check() < 0) {
    return nullptr;
  }
  const RegionMark mark = region_enter();
  PyObject *result = PyObject_Call(((ConfinedAttr *)self)->original, args, kwargs);
  region_leave(mark, nullptr);
  return result;
}

static int attr_traverse(PyObject *self, visitproc visit, void *arg)
{
  Py_VISIT(((ConfinedAttr *)self)->original);
  return 0;
}

static int attr_clear(PyObject *self)
{
  Py_CLEAR(((ConfinedAttr *)self)->original);
  return 0;
}

static void attr_dealloc(PyObject *self)
{
  PyObject_GC_UnTrack(self);
  attr_clear(self);
  Py_TYPE(self)->tp_free(self);
}

PyTypeObject confined_attr_type = {PyVarObject_HEAD_INIT(nullptr, 0) "_bpy._angelo_guard.ConfinedAttr",
                                          sizeof(ConfinedAttr)};
PyTypeObject confined_data_attr_type = {
    PyVarObject_HEAD_INIT(nullptr, 0) "_bpy._angelo_guard.ConfinedDataAttr", sizeof(ConfinedAttr)};

int attr_types_ready()
{
  for (PyTypeObject *type : {&confined_attr_type, &confined_data_attr_type}) {
    if (type->tp_flags != 0) {
      continue;
    }
    type->tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_IMMUTABLETYPE;
    type->tp_doc = "A class attribute a confined script made: it runs confined whoever reaches it.";
    type->tp_descr_get = attr_get;
    type->tp_call = attr_call;
    type->tp_traverse = attr_traverse;
    type->tp_clear = attr_clear;
    type->tp_dealloc = attr_dealloc;
    if (type == &confined_data_attr_type) {
      type->tp_descr_set = attr_set;
    }
    if (PyType_Ready(type) != 0) {
      return -1;
    }
  }
  return 0;
}

PyObject *confined_attr_new(PyObject *original)
{
  PyTypeObject *type = Py_TYPE(original)->tp_descr_set ? &confined_data_attr_type : &confined_attr_type;
  ConfinedAttr *attr = PyObject_GC_New(ConfinedAttr, type);
  if (attr == nullptr) {
    return nullptr;
  }
  attr->original = Py_NewRef(original);
  PyObject_GC_Track((PyObject *)attr);
  return (PyObject *)attr;
}

PyObject *confined_attr_original(PyObject *value)
{
  const PyTypeObject *type = Py_TYPE(value);
  return type == &confined_attr_type || type == &confined_data_attr_type ? ((ConfinedAttr *)value)->original :
                                                                          nullptr;
}

bool confining(PyObject *value)
{
  PyTypeObject *type = Py_TYPE(value);
  return type == &deferred_type || type == &confined_attr_type || type == &confined_data_attr_type ||
         type == &work_item_type;
}

/** \} */

static bool installed_attr(PyObject *value, int level = 0);

/** `functools.lru_cache` keeps the function it calls in C, out of reach (its `__wrapped__` is only a copy anyone can
 * set): where, found once by wrapping two builtins and looking for them. */
static PyTypeObject *lru_type = nullptr;
static Py_ssize_t lru_function_offset = -1;

static void find_c_wrappers()
{
  static bool tried = false;
  if (tried) {
    return;
  }
  tried = true;
  PyObject *functools = PyImport_ImportModule("functools");
  PyObject *lru_cache = functools ? PyObject_GetAttrString(functools, "lru_cache") : nullptr;
  PyObject *builtins = PyEval_GetBuiltins(); /* Borrowed. */
  Py_ssize_t found = -1;
  PyTypeObject *kind = nullptr;
  for (const char *probe_name : {"len", "abs"}) {
    PyObject *probe = builtins ? PyDict_GetItemString(builtins, probe_name) : nullptr; /* Borrowed. */
    PyObject *wrapper = lru_cache && probe ? PyObject_CallOneArg(lru_cache, probe) : nullptr;
    Py_ssize_t here = -1;
    if (wrapper != nullptr) {
      const Py_ssize_t size = Py_TYPE(wrapper)->tp_basicsize;
      for (Py_ssize_t offset = sizeof(PyObject); offset + Py_ssize_t(sizeof(PyObject *)) <= size;
           offset += sizeof(PyObject *))
      {
        if (*(PyObject **)((char *)wrapper + offset) == probe) {
          here = here == -1 ? offset : -2; /* Exactly one place. */
        }
      }
      kind = kind == nullptr || kind == Py_TYPE(wrapper) ? Py_TYPE(wrapper) : nullptr;
    }
    Py_XDECREF(wrapper);
    found = (found == -1 || found == here) ? here : -2;
  }
  if (found >= 0 && kind != nullptr) {
    lru_type = (PyTypeObject *)Py_NewRef((PyObject *)kind);
    lru_function_offset = found;
  }
  Py_XDECREF(lru_cache);
  Py_XDECREF(functools);
  PyErr_Clear();
}

PyObject *lru_function(PyObject *value)
{
  find_c_wrappers();
  return lru_type != nullptr && Py_TYPE(value) == lru_type ? *(PyObject **)((char *)value + lru_function_offset) :
                                                              nullptr;
}

/** Whether a class attribute is installed code: an installed function (also inside a static or class method or a
 * property), a module-level builtin, a descriptor defined in C (a C type's own methods and slots), a class (one the
 * region made is confined itself), or an `lru_cache` of installed code (judged by the function it really calls). */
static bool installed_attr(PyObject *value, int level)
{
  if (level > 4) {
    return false;
  }
  PyTypeObject *type = Py_TYPE(value);
  if (type == &PyMethodDescr_Type || type == &PyWrapperDescr_Type || type == &PyClassMethodDescr_Type ||
      type == &PyGetSetDescr_Type || type == &PyMemberDescr_Type)
  {
    return true;
  }
  if (PyType_Check(value)) {
    return true; /* A class (`classmethod(GenericAlias)`): one the region made is confined itself. */
  }
  if (PyFunction_Check(value)) {
    return installed_function(value);
  }
  if (PyCFunction_Check(value)) {
    PyObject *self = PyCFunction_GET_SELF(value);
    return self == nullptr || PyModule_Check(self) || PyType_Check(self);
  }
  if (type == &PyStaticMethod_Type || type == &PyClassMethod_Type) {
    PyObject *function = PyObject_GetAttrString(value, "__func__");
    PyErr_Clear();
    const bool installed = function != nullptr && installed_attr(function, level + 1);
    Py_XDECREF(function);
    return installed;
  }
  if (type == &PyProperty_Type) {
    for (const char *part : {"fget", "fset", "fdel"}) {
      PyObject *function = PyObject_GetAttrString(value, part);
      PyErr_Clear();
      const bool installed = function == nullptr || function == Py_None || installed_attr(function, level + 1);
      Py_XDECREF(function);
      if (!installed) {
        return false;
      }
    }
    return true;
  }
  if (PyMethod_Check(value)) {
    return installed_function(PyMethod_GET_FUNCTION(value)) && !made_by_script(PyMethod_GET_SELF(value));
  }
  if (PyObject *function = lru_function(value)) {
    return installed_attr(function, level + 1);
  }
  return false; /* Anything else (a partial, an object whose class reads code or data it holds): its effect may be
                   * the script's choice. A class a module defined for itself is skipped whole (confine_class). */
}

bool installed_attribute(PyObject *value)
{
  return installed_attr(value);
}

bool agent_callable(PyObject *value, int level)
{
  if (value == nullptr || level > 4 || confining(value) || PyType_Check(value) || PyModule_Check(value)) {
    return false;
  }
  PyTypeObject *type = Py_TYPE(value);
  if (PyFunction_Check(value)) {
    return !installed_function(value);
  }
  if (made_by_script(value) && !installed_attr(value)) {
    return true; /* The script's own code made it from parts it chose (`partial(exec, …)`). */
  }
  if (PyMethod_Check(value)) {
    return agent_callable(PyMethod_GET_FUNCTION(value), level + 1) || made_by_script(PyMethod_GET_SELF(value));
  }
  if (PyObject *function = lru_function(value)) {
    return agent_callable(function, level + 1);
  }
  const char *wrapped[] = {nullptr, nullptr, nullptr};
  if (type == &PyStaticMethod_Type || type == &PyClassMethod_Type) {
    wrapped[0] = "__func__";
  }
  else if (type == &PyProperty_Type) {
    wrapped[0] = "fget", wrapped[1] = "fset", wrapped[2] = "fdel";
  }
  else if (strcmp(type->tp_name, "functools.partial") == 0) {
    wrapped[0] = "func", wrapped[1] = "args", wrapped[2] = "keywords";
  }
  bool agent = false;
  for (const char *part : wrapped) {
    PyObject *name = part ? PyUnicode_FromString(part) : nullptr;
    PyObject *held = name ? PyObject_GenericGetAttr(value, name) : nullptr; /* No `__getattr__` runs. */
    Py_XDECREF(name);
    PyErr_Clear();
    if (held != nullptr && (PyTuple_Check(held) || PyDict_Check(held))) {
      PyObject *items = PyTuple_Check(held) ? PySequence_List(held) : PyDict_Values(held);
      for (Py_ssize_t i = 0; items != nullptr && i < PyList_GET_SIZE(items); i++) {
        agent = agent || agent_callable(PyList_GET_ITEM(items, i), level + 1);
      }
      Py_XDECREF(items);
    }
    else {
      agent = agent || agent_callable(held, level + 1);
    }
    Py_XDECREF(held);
  }
  if (!agent && wrapped[0] == nullptr) {
    /* Any other callable object: by its class's `__call__` (a class the script wrote calls its own code). */
    PyObject *name = PyUnicode_FromString("__call__");
    PyObject *call = name ? _PyType_Lookup(type, name) : nullptr; /* Borrowed; no code runs. */
    Py_XDECREF(name);
    agent = call != nullptr && PyFunction_Check(call) && !installed_function(call);
  }
  PyErr_Clear();
  return agent;
}

/** Every callable or descriptor in a class the region made that is not installed code becomes a confining
 * descriptor (a class the standard library defines while a script imports it keeps its own methods). */
void confine_class(PyTypeObject *type)
{
  PyObject *names = type->tp_dict;
  if (names == nullptr || !(type->tp_flags & Py_TPFLAGS_HEAPTYPE)) {
    return;
  }
  /* A class installed code made (an import, `enum`'s functional API, `namedtuple`, a pybind11 module) keeps what it
   * holds, and the journal puts back what the script set on it afterwards: only the script's code there is confined.
   * A class the script made (its code, or a class statement whose body is its code) is confined whole. */
  const bool defined = !script_type(type);
  std::vector<std::pair<PyObject *, PyObject *>> changes;
  PyObject *key, *value;
  Py_ssize_t position = 0;
  while (PyDict_Next(names, &position, &key, &value)) {
    if (confining(value) || PyType_Check(value) || PyModule_Check(value)) {
      continue;
    }
    const char *kind = Py_TYPE(value)->tp_name;
    if (strcmp(kind, "member_descriptor") == 0 || strcmp(kind, "getset_descriptor") == 0) {
      continue; /* Made by the class statement itself (`__slots__`, `__dict__`, `__weakref__`). */
    }
    if (defined ? !agent_callable(value) :
                  ((!PyCallable_Check(value) && Py_TYPE(value)->tp_descr_get == nullptr) || installed_attr(value)))
    {
      continue;
    }
    PyObject *attr = confined_attr_new(value);
    if (attr == nullptr) {
      PyErr_Clear();
      continue;
    }
    changes.emplace_back(Py_NewRef(key), attr);
  }
  for (auto &[name, attr] : changes) {
    PyDict_SetItem(names, name, attr);
    Py_DECREF(name);
    Py_DECREF(attr);
  }
  if (!changes.empty()) {
    PyType_Modified(type);
  }
  PyErr_Clear();
}

}  // namespace blender::angelo_guard
