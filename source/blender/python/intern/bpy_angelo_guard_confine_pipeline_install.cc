/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Installing the pipeline gates (bpy_angelo_guard_confine_wrap_pipeline.cc): on a module's functions, a class's
 * methods (and every subclass that overrides them), or a static type's `tp_init`/`tp_new` slot; when the library's
 * extension module is made (`_imp` wrapped on top of the confinement's own wrappers) and for modules loaded before
 * the confinement.
 */

#include <Python.h>

#include <cstring>
#include <deque>
#include <string>
#include <vector>

#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_pipeline.hh"

namespace blender::angelo_guard {

/* -------------------------------------------------------------------- */
/** \name Static types' slots (`tp_init`, `tp_new`)
 * \{ */

static constexpr int SLOT_GATES = 8;
static Gate *slot_gates[SLOT_GATES];
static int slot_count = 0;

static PyObject *with_first(PyObject *first, PyObject *args)
{
  PyObject *full = PyTuple_New(PyTuple_GET_SIZE(args) + 1);
  if (full != nullptr) {
    PyTuple_SET_ITEM(full, 0, Py_NewRef(first));
    for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(args); i++) {
      PyTuple_SET_ITEM(full, i + 1, Py_NewRef(PyTuple_GET_ITEM(args, i)));
    }
  }
  return full;
}

static bool slot_refused(Gate &gate, PyObject *first, PyObject *args, PyObject *kwargs)
{
  if (!confined_now()) {
    return false;
  }
  PyObject *full = with_first(first, args);
  PyObject *call_kwargs = Py_XNewRef(kwargs);
  const bool refused = full == nullptr || gate_check(gate, &full, &call_kwargs) != 0;
  Py_XDECREF(full);
  Py_XDECREF(call_kwargs);
  return refused;
}

template<int I> static int init_trampoline(PyObject *self, PyObject *args, PyObject *kwargs)
{
  Gate &gate = *slot_gates[I];
  return slot_refused(gate, self, args, kwargs) ? -1 : ((initproc)gate.slot)(self, args, kwargs);
}

template<int I> static PyObject *new_trampoline(PyTypeObject *type, PyObject *args, PyObject *kwargs)
{
  Gate &gate = *slot_gates[I];
  return slot_refused(gate, (PyObject *)type, args, kwargs) ? nullptr :
                                                              ((newfunc)gate.slot)(type, args, kwargs);
}

template<int... I> struct Trampolines {
  static constexpr initproc inits[] = {init_trampoline<I>...};
  static constexpr newfunc news[] = {new_trampoline<I>...};
};
using SlotTrampolines = Trampolines<0, 1, 2, 3, 4, 5, 6, 7>;

static bool install_slot(Gate &gate, PyTypeObject *type)
{
  const bool init = gate.kind == Kind::Init;
  void *current = init ? (void *)type->tp_init : (void *)type->tp_new;
  for (int i = 0; i < slot_count; i++) {
    if (current == (init ? (void *)SlotTrampolines::inits[i] : (void *)SlotTrampolines::news[i])) {
      return true; /* Gated already. */
    }
  }
  if (slot_count == SLOT_GATES || current == nullptr) {
    return false;
  }
  const int index = slot_count++;
  note_install_site(gate.module); /* The module that defines it: never made again while confined. */
  slot_gates[index] = &gate;
  gate.slot = current;
  if (init) {
    type->tp_init = SlotTrampolines::inits[index];
  }
  else {
    type->tp_new = SlotTrampolines::news[index];
  }
  PyType_Modified(type);
  return true;
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Installing the gates
 * \{ */

/** Gates made at install for every class a wildcard row or a subclass reaches (stable addresses). */
static std::deque<Gate> &made_gates()
{
  static std::deque<Gate> gates_made;
  return gates_made;
}

static bool install_on_type(Gate &gate, PyTypeObject *type);

static void install_on_subclasses(const Gate &gate, PyTypeObject *type)
{
  PyObject *subclasses = PyObject_CallMethod((PyObject *)type, "__subclasses__", nullptr);
  for (Py_ssize_t i = 0; subclasses && PyList_Check(subclasses) && i < PyList_GET_SIZE(subclasses); i++) {
    PyTypeObject *subclass = (PyTypeObject *)PyList_GET_ITEM(subclasses, i);
    PyObject *own = subclass->tp_dict ? PyDict_GetItemString(subclass->tp_dict, gate.name) : nullptr;
    if (own != nullptr && !gated(own)) {
      made_gates().push_back(gate);
      install_on_type(made_gates().back(), subclass);
    }
    else {
      install_on_subclasses(gate, subclass);
    }
  }
  Py_XDECREF(subclasses);
  PyErr_Clear();
}

/** Whether the gate is on `type` now (put there, or there already). */
static bool install_on_type(Gate &gate, PyTypeObject *type)
{
  const bool heap = (type->tp_flags & Py_TPFLAGS_HEAPTYPE) != 0;
  if (gate.kind == Kind::New || (gate.kind == Kind::Init && !heap)) {
    return install_slot(gate, type);
  }
  PyObject *raw = type->tp_dict ? PyDict_GetItemString(type->tp_dict, gate.name) : nullptr; /* Borrowed. */
  if (raw == nullptr) {
    return false;
  }
  if (gated(raw)) {
    return true;
  }
  if (!(PyCallable_Check(raw) || Py_TYPE(raw) == &PyClassMethod_Type || Py_TYPE(raw)->tp_descr_get != nullptr)) {
    return false; /* A value of that name that is no function (a wildcard row's other classes). */
  }
  PyObject *original = nullptr;
  if (gate.kind == Kind::Static) {
    original = Py_TYPE(raw) == &PyStaticMethod_Type ? PyObject_GetAttrString(raw, "__func__") : Py_NewRef(raw);
  }
  else if (gate.kind == Kind::ClassMethod) {
    original = Py_NewRef(raw);
  }
  else {
    original = PyInstanceMethod_Check(raw) ? Py_NewRef(PyInstanceMethod_GET_FUNCTION(raw)) : Py_NewRef(raw);
  }
  PyObject *object = original ? gate_object_new(gate) : nullptr;
  PyObject *held = object == nullptr               ? nullptr :
                   gate.kind == Kind::Static      ? PyStaticMethod_New(object) :
                   gate.kind == Kind::ClassMethod ? PyClassMethod_New(object) :
                                                    Py_NewRef(object);
  if (held != nullptr) {
    Py_XSETREF(gate.original, Py_NewRef(original));
    Py_XSETREF(gate.raw, Py_NewRef(raw));
    if (heap) {
      PyObject_SetAttrString((PyObject *)type, gate.name, held); /* Updates the slots (`__init__`). */
    }
    else {
      PyDict_SetItemString(type->tp_dict, gate.name, held); /* Immutable to Python; changed here, in C. */
      note_install_site(gate.module); /* The module that defines it: never made again while confined. */
      PyType_Modified(type);
    }
  }
  const bool done = held != nullptr && gated(PyDict_GetItemString(type->tp_dict, gate.name));
  Py_XDECREF(held);
  Py_XDECREF(object);
  Py_XDECREF(original);
  PyErr_Clear();
  install_on_subclasses(gate, type);
  return done;
}

static PyObject *owner_type(PyObject *module, const char *owner)
{
  PyObject *current = Py_NewRef(module);
  std::string path = owner;
  size_t start = 0;
  while (current != nullptr && start <= path.size()) {
    const size_t dot = path.find('.', start);
    const std::string part = path.substr(start, dot == std::string::npos ? std::string::npos : dot - start);
    PyObject *next = PyObject_GetAttrString(current, part.c_str());
    Py_DECREF(current);
    current = next;
    if (dot == std::string::npos) {
      break;
    }
    start = dot + 1;
  }
  PyErr_Clear();
  if (current != nullptr && !PyType_Check(current)) {
    Py_CLEAR(current);
  }
  return current;
}

/** Whether the gate is in `module` now. A multi-phase module has nothing in it until it is executed: then not yet. */
static bool install_gate(Gate &gate, PyObject *module)
{
  if (gate.owner == nullptr) {
    PyObject *original = PyObject_GetAttrString(module, gate.name);
    PyErr_Clear();
    if (original == nullptr || Py_TYPE(original) == &gate_type) {
      const bool there = original != nullptr;
      Py_XDECREF(original);
      return there;
    }
    if (PyObject *object = gate_object_new(gate)) {
      Py_XSETREF(gate.original, Py_NewRef(original));
      Py_XSETREF(gate.object, Py_NewRef(object));
      PyObject_SetAttrString(module, gate.name, object);
      note_install_site(gate.module);
      replace_references(original, object); /* A package's copy (`from ._usdUtils import *`). */
      Py_DECREF(object);
    }
    Py_DECREF(original);
    PyErr_Clear();
    return true;
  }
  if (strcmp(gate.owner, "*") == 0) {
    PyObject *names = PyModule_GetDict(module);
    PyObject *key, *value;
    Py_ssize_t position = 0;
    std::vector<PyTypeObject *> types;
    while (names && PyDict_Next(names, &position, &key, &value)) {
      if (PyType_Check(value) && ((PyTypeObject *)value)->tp_dict &&
          PyDict_GetItemString(((PyTypeObject *)value)->tp_dict, gate.name) != nullptr)
      {
        types.push_back((PyTypeObject *)value);
      }
    }
    for (PyTypeObject *type : types) {
      made_gates().push_back(gate);
      install_on_type(made_gates().back(), type);
    }
    return !types.empty();
  }
  bool done = false;
  if (PyObject *type = owner_type(module, gate.owner)) {
    done = install_on_type(gate, (PyTypeObject *)type);
    Py_DECREF(type);
  }
  return done;
}

/* -------------------------------------------------------------------- */
/** \name USD without the confined resolver
 *
 * The table's USD rows rely on the confined resolver to judge what composition reaches. When USD runs any other
 * resolver (`usd_confined` false: the plugin missing from an install, `PXR_AR_DISABLE_PLUGIN_RESOLVER`), nothing
 * USD does is judged, so no USD is a confined script's: every `pxr` binding is one Boost.Python function type
 * (functions, methods, constructors, property accessors), and its call refuses a confined frame, whichever module
 * or object reached it (a module loaded before the script, a stage Angelo made).
 * \{ */

static ternaryfunc pxr_call = nullptr;

static PyObject *pxr_closed_call(PyObject *self, PyObject *args, PyObject *kwargs)
{
  if (confined_now()) {
    refuse(NATIVE, "USD (its resolver is not the confined one)");
    return nullptr;
  }
  return pxr_call(self, args, kwargs);
}

/** The Boost.Python function type of a `pxr` extension module (from any of its functions), or null. */
static PyTypeObject *pxr_function_type(PyObject *module)
{
  PyObject *dict = PyModule_GetDict(module); /* Borrowed. */
  PyObject *key, *value;
  Py_ssize_t position = 0;
  while (dict != nullptr && PyDict_Next(dict, &position, &key, &value)) {
    PyTypeObject *type = Py_TYPE(value);
    if (type->tp_call != nullptr && strcmp(type->tp_name, "Boost.Python.function") == 0) {
      return type;
    }
  }
  return nullptr;
}

static void close_pxr(const char *module_name, PyObject *module)
{
  if (usd_confined || pxr_call != nullptr || strncmp(module_name, "pxr.", 4) != 0) {
    return;
  }
  PyTypeObject *type = pxr_function_type(module);
  if (type == nullptr || type->tp_call == pxr_closed_call) {
    return;
  }
  pxr_call = type->tp_call;
  type->tp_call = pxr_closed_call;
  note_install_site(module_name); /* The pxr module it was found in. */
  type->tp_flags &= ~Py_TPFLAGS_HAVE_VECTORCALL; /* Every call through `tp_call`. */
  PyType_Modified(type);
}

/** \} */

/** Gate every row of `module_name` in `module`, once per module object. Never journaled: the gates are the
 * confinement's own, even when a confined script's import made the module. */
static void gate_module(const char *module_name, PyObject *module)
{
  if (module_name == nullptr || module == nullptr || !PyModule_Check(module)) {
    return;
  }
  close_pxr(module_name, module);
  const bool was_quiet = quiet;
  quiet = true;
  for (size_t i = 0; i < gate_count; i++) {
    Gate &gate = gates[i];
    if (strcmp(gate.module, module_name) != 0 || gate.installed_in == module) {
      continue;
    }
    if (install_gate(gate, module)) {
      Py_XSETREF(gate.installed_in, Py_NewRef(module));
    }
  }
  special_install(module, module_name);
  quiet = was_quiet;
  PyErr_Clear();
}

static std::string spec_name(PyObject *spec)
{
  PyObject *name = spec ? PyObject_GetAttrString(spec, "name") : nullptr;
  std::string text = text_of(name);
  Py_XDECREF(name);
  PyErr_Clear();
  return text;
}

static std::string module_name_of(PyObject *module)
{
  PyObject *spec = PyObject_GetAttrString(module, "__spec__");
  std::string text = spec && spec != Py_None ? spec_name(spec) : std::string();
  Py_XDECREF(spec);
  PyErr_Clear();
  if (text.empty()) {
    PyObject *name = PyObject_GetAttrString(module, "__name__");
    text = text_of(name);
    Py_XDECREF(name);
    PyErr_Clear();
  }
  return text;
}

enum Hook { CREATE_DYNAMIC, EXEC_DYNAMIC, CREATE_BUILTIN, EXEC_BUILTIN, HOOKS };
static const char *const hook_names[HOOKS] = {"create_dynamic", "exec_dynamic", "create_builtin", "exec_builtin"};
static PyObject *hook_originals[HOOKS] = {};

static PyObject *hooked(PyObject *index, PyObject *args, PyObject *kwargs)
{
  const long which = PyLong_AsLong(index);
  const bool create = which == CREATE_DYNAMIC || which == CREATE_BUILTIN;
  PyObject *first = PyTuple_GET_SIZE(args) > 0 ? PyTuple_GET_ITEM(args, 0) : nullptr;
  std::string name;
  PyObject *call_args = Py_NewRef(args);
  if (create && first != nullptr && confined_now()) {
    /* This hook is the outermost `_imp` wrapper: the spec is read here, once, into a spec of Angelo's own, and that
     * is what every check below and inside (guarded_imp) sees and what is made (head 14, SHOULD-1: a name that
     * changes between reads passed the pxr gating as another module). */
    PyObject *steady = steady_spec(first, name);
    if (steady == nullptr) {
      Py_DECREF(call_args);
      refuse(GUARD, "an import whose spec names no module");
      return nullptr;
    }
    Py_SETREF(call_args, PyTuple_New(PyTuple_GET_SIZE(args)));
    if (call_args == nullptr) {
      Py_DECREF(steady);
      return nullptr;
    }
    PyTuple_SET_ITEM(call_args, 0, steady); /* Steals. */
    for (Py_ssize_t i = 1; i < PyTuple_GET_SIZE(args); i++) {
      PyTuple_SET_ITEM(call_args, i, Py_NewRef(PyTuple_GET_ITEM(args, i)));
    }
  }
  else if (create) {
    name = spec_name(first);
  }
  if (create) {
    /* Any context, at creation, under any name: a module of the sub-interpreter family (by full name, last component
     * or origin file, native.hh) has every hot path ask for good. `create_builtin` with an alias raises no `import`
     * event (the re-read of f3b1e5f, MUST-1 and SHOULD-2: `_interpreters` may be built in on Windows). */
    PyObject *made_from = PyTuple_GET_SIZE(call_args) > 0 ? PyTuple_GET_ITEM(call_args, 0) : nullptr;
    PyObject *origin = made_from != nullptr ? PyObject_GetAttrString(made_from, "origin") : nullptr;
    const std::string origin_text = text_of(origin);
    Py_XDECREF(origin);
    PyErr_Clear();
    if (module_is_one_of(name, origin_text, true, INTERPRETER_MODULES)) {
      confine_ask_always();
    }
  }
  if (create && !usd_confined && name.rfind("pxr.", 0) == 0 && confined_now()) {
    /* Without the confined resolver USD's composition would read any file: no USD for scripts (fail closed). */
    Py_DECREF(call_args);
    refuse(NATIVE, name.c_str());
    return nullptr;
  }
  PyObject *result = PyObject_Call(hook_originals[which], call_args, kwargs);
  Py_DECREF(call_args);
  if (result == nullptr) {
    return nullptr;
  }
  /* Single-phase modules are complete when made; multi-phase ones once executed. */
  PyObject *module = create ? result : first;
  if (module != nullptr && PyModule_Check(module)) {
    gate_module(create ? name.c_str() : module_name_of(module).c_str(), module);
  }
  return result;
}

static PyMethodDef hook_definition = {
    "pipeline_module_hook", (PyCFunction)(void (*)(void))hooked, METH_VARARGS | METH_KEYWORDS, nullptr};

void install_hooks()
{
  PyObject *imp = PyImport_ImportModule("_imp");
  for (int i = 0; imp != nullptr && i < HOOKS; i++) {
    if (hook_originals[i] != nullptr) {
      continue;
    }
    PyObject *current = PyObject_GetAttrString(imp, hook_names[i]);
    PyObject *index = current ? PyLong_FromLong(i) : nullptr;
    PyObject *wrapper = index ? PyCFunction_NewEx(&hook_definition, index, nullptr) : nullptr;
    if (wrapper != nullptr) {
      hook_originals[i] = Py_NewRef(current);
      put_in("_imp", hook_names[i], wrapper, current);
    }
    Py_XDECREF(wrapper);
    Py_XDECREF(index);
    Py_XDECREF(current);
  }
  Py_XDECREF(imp);
  PyErr_Clear();
}

/** Modules loaded before the confinement (the person used USD earlier in the session). */
void gate_loaded_modules()
{
  PyObject *modules = PyImport_GetModuleDict();
  for (size_t i = 0; i < gate_count; i++) {
    PyObject *module = PyDict_GetItemString(modules, gates[i].module); /* Borrowed. */
    if (module != nullptr) {
      gate_module(gates[i].module, module);
    }
  }
  PyErr_Clear();
}

}  // namespace blender::angelo_guard
