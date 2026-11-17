/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * What an agent script can read of the code around it (doc/angelo/script_confinement.rst, "Angelo's own code").
 *
 * - Frames that aren't the script's or one it called (walking out from the frame, the script's before Angelo's) are
 *   opaque to it: their `f_globals` is a stand-in holding only their module's name, their `f_locals` is empty, and
 *   `f_back` never leads to one, and `clear()` is refused (it would end a generator of Angelo's mid-step). So
 *   `sys._getframe(1).f_globals` (the namespace of Angelo's code that runs the script), a traceback's or a
 *   generator's frames give it nothing of the code that called it, while tracebacks still print (`linecache` reads
 *   the file by its name).
 * - Angelo's package (`angelo_package`) can't be imported by name from a script (`import`,
 *   `__import__`, `importlib.import_module`).
 *
 * An agent caller: walking out from the innermost frame, a frame of the script's code comes before any frame of
 * Angelo's (one whose globals are a namespace of Angelo's package, `angelo_package`). So Angelo's code a script calls
 * (`kit`) reads frames and imports its own modules as always, and a helper the script calls (`traceback`,
 * `inspect`) gets what the script would.
 *
 * Angelo's modules, classes and instances stay reachable in other ways (`sys.modules`, a registered class, a bound
 * method in a handler list): what their state can make Angelo destroy, signal or send its credentials to is checked
 * against facts C holds (bpy_angelo_guard_confine_owned.cc).
 */

#include <Python.h>

#include <cstring>
#include <string>
#include <unordered_set>

#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_journal.hh"

namespace blender::angelo_guard {

namespace {

std::string package; /* Angelo's package ("angelo_agent"), set once at install. */
std::unordered_set<PyObject *> angelo_dicts; /* Its modules' namespaces (held: never reused). */

PyObject *frame_originals[3] = {nullptr, nullptr, nullptr};
const char *const FRAME_NAMES[3] = {"f_globals", "f_locals", "f_back"};

PyObject *original_import = nullptr, *original_import_module = nullptr, *original_importlib_import = nullptr;

bool in_package(PyObject *name)
{
  if (package.empty() || !PyUnicode_Check(name)) {
    return false;
  }
  const char *text = PyUnicode_AsUTF8(name);
  if (text == nullptr) {
    PyErr_Clear();
    return false;
  }
  return strncmp(text, package.c_str(), package.size()) == 0 &&
         (text[package.size()] == '\0' || text[package.size()] == '.');
}

void hold_namespace(PyObject *module)
{
  PyObject *names = PyModule_Check(module) ? PyModule_GetDict(module) : nullptr;
  if (names != nullptr && angelo_dicts.insert(names).second) {
    Py_INCREF(names);
  }
  PyErr_Clear();
}

/** Whether the code running now, nearest first, is the script's before it is Angelo's. */
bool agent_caller()
{
  if (!confined_now() && !frames_confined()) {
    return false; /* No script on this thread: nothing to check. */
  }
  PyFrameObject *frame = (PyFrameObject *)Py_XNewRef(PyEval_GetFrame());
  bool agent = false;
  for (int hops = 0; frame != nullptr && hops < 512; hops++) {
    PyCodeObject *code = PyFrame_GetCode(frame);
    const bool script = agent_code(code);
    Py_DECREF(code);
    if (script) {
      agent = true;
      break;
    }
    PyObject *globals = PyFrame_GetGlobals(frame);
    const bool angelo = globals != nullptr && angelo_dicts.count(globals) != 0;
    Py_XDECREF(globals);
    if (angelo) {
      break;
    }
    PyFrameObject *back = PyFrame_GetBack(frame);
    Py_DECREF(frame);
    frame = back;
  }
  Py_XDECREF(frame);
  PyErr_Clear();
  return agent;
}

bool script_frame(PyObject *frame)
{
  PyCodeObject *code = PyFrame_GetCode((PyFrameObject *)frame);
  const bool script = agent_code(code);
  Py_DECREF(code);
  return script;
}

/** Whether a script may read `frame`: its own, or one it called (walking out from the frame, a frame of the script's
 * comes before any of Angelo's). Never Angelo's code, what Angelo's code called on the script's behalf, or what
 * called the script; a frame no longer running links to nothing and is never the script's either. */
bool script_may_read(PyFrameObject *frame)
{
  PyFrameObject *walk = (PyFrameObject *)Py_XNewRef(frame);
  bool readable = false;
  for (int hops = 0; walk != nullptr && hops < 512; hops++) {
    if (script_frame((PyObject *)walk)) {
      readable = true;
      break;
    }
    PyObject *globals = PyFrame_GetGlobals(walk);
    const bool angelo = globals != nullptr && angelo_dicts.count(globals) != 0;
    Py_XDECREF(globals);
    if (angelo) {
      break;
    }
    PyFrameObject *back = PyFrame_GetBack(walk);
    Py_DECREF(walk);
    walk = back;
  }
  Py_XDECREF(walk);
  PyErr_Clear();
  return readable;
}

/* -------------------------------------------------------------------- */
/** \name Frames
 * \{ */

template<int I> PyObject *checked_frame(PyObject *frame, void * /*closure*/)
{
  if (I != 2 && agent_caller() && !script_may_read((PyFrameObject *)frame)) {
    if (I == 0) {
      /* Only its module's name: `linecache` then reads the source by the file name. */
      PyObject *globals = PyFrame_GetGlobals((PyFrameObject *)frame);
      PyObject *copy = PyDict_New();
      PyObject *name = globals != nullptr ? PyDict_GetItemString(globals, "__name__") : nullptr;
      if (copy != nullptr && name != nullptr) {
        PyDict_SetItemString(copy, "__name__", name);
      }
      Py_XDECREF(globals);
      PyErr_Clear();
      return copy;
    }
    return PyDict_New();
  }
  if (I == 2 && (confined_now() || frames_confined()) && agent_caller()) {
    /* The caller, while it is the script's or one the script called: never what called the script. */
    PyFrameObject *back = PyFrame_GetBack((PyFrameObject *)frame);
    if (back == nullptr) {
      Py_RETURN_NONE;
    }
    if (!script_may_read(back)) {
      Py_DECREF(back);
      Py_RETURN_NONE;
    }
    return (PyObject *)back;
  }
  PyObject *original = frame_originals[I];
  return Py_TYPE(original)->tp_descr_get(original, frame, (PyObject *)Py_TYPE(frame));
}

PyGetSetDef frame_getsets[3] = {
    {"f_globals", checked_frame<0>, nullptr, nullptr, nullptr},
    {"f_locals", checked_frame<1>, nullptr, nullptr, nullptr},
    {"f_back", checked_frame<2>, nullptr, nullptr, nullptr},
};

PyObject *frame_clear_original = nullptr;

/** `frame.clear()`: refused to a script for a frame it may not read (an Angelo generator's: clearing it ends that
 * generator mid-step, so the work it does for the person stops). */
PyObject *checked_frame_clear(PyObject *frame, PyObject * /*unused*/)
{
  if (agent_caller() && !script_may_read((PyFrameObject *)frame)) {
    refuse(GUARD, "frame.clear");
    return nullptr;
  }
  return PyObject_CallOneArg(frame_clear_original, frame);
}

PyMethodDef frame_clear_def = {"clear", checked_frame_clear, METH_NOARGS,
                               "clear(): clear the frame's locals (a script: only its own frames)."};

PyObject *kwdefaults_original = nullptr;

/** `function.__kwdefaults__` read by a script for a function that isn't its own: a copy, so a default changed in
 * place (`f.__kwdefaults__['which'] = ...`, a program Angelo's code would start later) reaches nothing. Setting it is
 * the audit's (`object.__setattr__`); its own functions' are its own. */
PyObject *checked_kwdefaults(PyObject *function, void * /*closure*/)
{
  PyObject *value = Py_TYPE(kwdefaults_original)->tp_descr_get(kwdefaults_original, function,
                                                               (PyObject *)Py_TYPE(function));
  if (value == nullptr || !PyDict_Check(value) || !agent_caller()) {
    return value;
  }
  PyObject *code = PyFunction_GetCode(function); /* Borrowed. */
  if (code != nullptr && agent_code((PyCodeObject *)code)) {
    return value;
  }
  PyObject *copy = PyDict_Copy(value);
  Py_DECREF(value);
  return copy;
}

int set_kwdefaults(PyObject *function, PyObject *value, void * /*closure*/)
{
  return Py_TYPE(kwdefaults_original)->tp_descr_set(kwdefaults_original, function, value);
}

PyGetSetDef kwdefaults_getset = {"__kwdefaults__", checked_kwdefaults, set_kwdefaults, nullptr, nullptr};

bool install_function_defaults()
{
  PyObject *names = PyType_GetDict(&PyFunction_Type); /* New reference. */
  PyObject *member = names != nullptr ? PyDict_GetItemString(names, "__kwdefaults__") : nullptr; /* Borrowed. */
  bool done = member != nullptr && kwdefaults_original == nullptr && Py_TYPE(member)->tp_descr_get != nullptr &&
              Py_TYPE(member)->tp_descr_set != nullptr;
  PyObject *checked = done ? PyDescr_NewGetSet(&PyFunction_Type, &kwdefaults_getset) : nullptr;
  if (checked != nullptr) {
    kwdefaults_original = Py_NewRef(member);
    done = PyDict_SetItemString(names, "__kwdefaults__", checked) == 0;
  }
  Py_XDECREF(checked);
  PyType_Modified(&PyFunction_Type);
  Py_XDECREF(names);
  PyErr_Clear();
  return done;
}

bool install_frames()
{
  PyObject *names = PyType_GetDict(&PyFrame_Type); /* New reference (a static type's dict lives elsewhere). */
  bool done = names != nullptr;
  for (int i = 0; done && i < 3; i++) {
    PyObject *member = PyDict_GetItemString(names, FRAME_NAMES[i]); /* Borrowed. */
    PyObject *checked = member != nullptr ? PyDescr_NewGetSet(&PyFrame_Type, &frame_getsets[i]) : nullptr;
    done = checked != nullptr && frame_originals[i] == nullptr;
    if (done) {
      frame_originals[i] = Py_NewRef(member); /* The original lives only here. */
      done = PyDict_SetItemString(names, FRAME_NAMES[i], checked) == 0;
    }
    Py_XDECREF(checked);
  }
  PyObject *clear = done ? PyDict_GetItemString(names, "clear") : nullptr; /* Borrowed. */
  PyObject *checked_clear = clear != nullptr ? PyDescr_NewMethod(&PyFrame_Type, &frame_clear_def) : nullptr;
  if (checked_clear != nullptr && frame_clear_original == nullptr) {
    frame_clear_original = Py_NewRef(clear);
    done = PyDict_SetItemString(names, "clear", checked_clear) == 0;
  }
  Py_XDECREF(checked_clear);
  PyType_Modified(&PyFrame_Type);
  Py_XDECREF(names);
  PyErr_Clear();
  return done;
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Imports by name
 * \{ */

/** Refused: an agent caller importing Angelo's package by an absolute name. */
bool import_refused(PyObject *name, PyObject *level)
{
  if (name == nullptr || !PyUnicode_Check(name)) {
    return false;
  }
  if (level != nullptr && PyLong_Check(level) && PyLong_AsLong(level) != 0) {
    PyErr_Clear();
    return false; /* Relative: resolved in the importer's own package (a script has none of Angelo's). */
  }
  if (!in_package(name) || !agent_caller()) {
    return false;
  }
  refuse(GUARD, "importing Angelo's own code (use the script's own API instead)");
  return true;
}

PyObject *wrapped_import(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  PyObject *name = PyTuple_GET_SIZE(args) > 0 ? PyTuple_GET_ITEM(args, 0) : nullptr;
  PyObject *level = PyTuple_GET_SIZE(args) > 4 ? PyTuple_GET_ITEM(args, 4) : nullptr;
  if (name == nullptr && kwargs != nullptr) {
    name = PyDict_GetItemString(kwargs, "name");
  }
  if (level == nullptr && kwargs != nullptr) {
    level = PyDict_GetItemString(kwargs, "level");
  }
  if (import_refused(name, level)) {
    return nullptr;
  }
  return PyObject_Call(original_import, args, kwargs);
}

PyObject *wrapped_import_module(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  PyObject *name = PyTuple_GET_SIZE(args) > 0 ? PyTuple_GET_ITEM(args, 0) : nullptr;
  PyObject *package_arg = PyTuple_GET_SIZE(args) > 1 ? PyTuple_GET_ITEM(args, 1) : nullptr;
  if (name == nullptr && kwargs != nullptr) {
    name = PyDict_GetItemString(kwargs, "name");
  }
  if (package_arg == nullptr && kwargs != nullptr) {
    package_arg = PyDict_GetItemString(kwargs, "package");
  }
  const char *text = name != nullptr && PyUnicode_Check(name) ? PyUnicode_AsUTF8(name) : nullptr;
  PyErr_Clear();
  /* `import_module('.x', 'angelo_agent')`: the package names whose module it is. */
  PyObject *judged = (text != nullptr && text[0] == '.' && package_arg != nullptr) ? package_arg : name;
  if (import_refused(judged, nullptr)) {
    return nullptr;
  }
  return PyObject_Call(original_import_module, args, kwargs);
}

PyObject *wrapped_importlib_import(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  PyObject *name = PyTuple_GET_SIZE(args) > 0 ? PyTuple_GET_ITEM(args, 0) : nullptr;
  PyObject *level = PyTuple_GET_SIZE(args) > 4 ? PyTuple_GET_ITEM(args, 4) : nullptr;
  if (import_refused(name, level)) {
    return nullptr;
  }
  return PyObject_Call(original_importlib_import, args, kwargs);
}

PyMethodDef import_def = {"__import__", (PyCFunction)(void (*)(void))wrapped_import,
                          METH_VARARGS | METH_KEYWORDS, "__import__ (Angelo's package refused to scripts)."};
PyMethodDef import_module_def = {"import_module", (PyCFunction)(void (*)(void))wrapped_import_module,
                                 METH_VARARGS | METH_KEYWORDS,
                                 "import_module (Angelo's package refused to scripts)."};
PyMethodDef importlib_import_def = {"__import__", (PyCFunction)(void (*)(void))wrapped_importlib_import,
                                    METH_VARARGS | METH_KEYWORDS,
                                    "importlib.__import__ (Angelo's package refused to scripts)."};

/** Replace `module.name` with a wrapper (and every other reference to the original: batched by the caller). */
bool wrap(const char *module_name, const char *name, PyMethodDef *definition, PyObject **original)
{
  PyObject *module = PyImport_ImportModule(module_name);
  PyObject *current = module ? PyObject_GetAttrString(module, name) : nullptr;
  PyObject *wrapper = current ? PyCFunction_NewEx(definition, nullptr, nullptr) : nullptr;
  bool done = false;
  if (wrapper != nullptr && *original == nullptr) {
    *original = Py_NewRef(current);
    done = PyObject_SetAttrString(module, name, wrapper) == 0;
    replace_references(current, wrapper);
  }
  Py_XDECREF(wrapper);
  Py_XDECREF(current);
  Py_XDECREF(module);
  PyErr_Clear();
  return done;
}

/** \} */

}  // namespace

bool script_calling()
{
  return agent_caller();
}

bool angelo_on_stack()
{
  PyFrameObject *frame = (PyFrameObject *)Py_XNewRef(PyEval_GetFrame());
  bool angelo = false;
  for (int hops = 0; frame != nullptr && hops < 512 && !angelo; hops++) {
    PyObject *globals = PyFrame_GetGlobals(frame);
    angelo = globals != nullptr && angelo_dicts.count(globals) != 0;
    Py_XDECREF(globals);
    PyFrameObject *back = PyFrame_GetBack(frame);
    Py_DECREF(frame);
    frame = back;
  }
  Py_XDECREF(frame);
  PyErr_Clear();
  return angelo;
}

int install_wrappers_opaque()
{
  if (original_import != nullptr) {
    return 0;
  }
  const bool done = install_frames() && install_function_defaults() &&
                    wrap("builtins", "__import__", &import_def, &original_import) &&
                    wrap("importlib", "import_module", &import_module_def, &original_import_module) &&
                    wrap("importlib", "__import__", &importlib_import_def, &original_importlib_import);
  if (!done) {
    PyErr_SetString(PyExc_RuntimeError, "the frame and import checks could not be installed");
    return -1;
  }
  return 0;
}

void opaque_module_added(PyObject *key, PyObject *module)
{
  if (package.empty() || module == nullptr || !PyModule_Check(module) || !in_package(key)) {
    return;
  }
  if (confined_now() || frames_confined()) {
    /* Added while a script runs: only the import system loading the package's own file under its own name (a
     * lazy import of Angelo's); one a script assembled or loaded under the name is not Angelo's, and is dropped at
     * the region's end (modules.cc). */
    PyObject *names = PyModule_GetDict(module);
    PyObject *spec = names ? PyDict_GetItemString(names, "__spec__") : nullptr;
    if (spec == nullptr || !import_system_adding()) {
      PyErr_Clear();
      return;
    }
    origins_note(module, spec);
    if (!origin_honest(key, module)) {
      PyErr_Clear();
      return;
    }
  }
  hold_namespace(module);
}

PyObject *guard_angelo_package(PyObject * /*self*/, PyObject *name)
{
  if (refuse_if_confined("angelo_package")) {
    return nullptr;
  }
  if (!PyUnicode_Check(name) || !package.empty()) {
    Py_RETURN_NONE; /* Once, for one package. */
  }
  const char *text = PyUnicode_AsUTF8(name);
  if (text == nullptr) {
    return nullptr;
  }
  package = text;
  PyObject *modules = journal_modules();
  PyObject *items = modules != nullptr ? PyDict_Items(modules) : nullptr;
  for (Py_ssize_t i = 0; items != nullptr && i < PyList_GET_SIZE(items); i++) {
    PyObject *pair = PyList_GET_ITEM(items, i);
    if (in_package(PyTuple_GET_ITEM(pair, 0))) {
      hold_namespace(PyTuple_GET_ITEM(pair, 1));
    }
  }
  Py_XDECREF(items);
  PyErr_Clear();
  Py_RETURN_NONE;
}

}  // namespace blender::angelo_guard
