/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Python entry points a confined script could escape through without an audit event the hook could refuse
 * (doc/angelo/script_confinement.rst), replaced by C functions whose originals stay in C (never in `__self__` or a
 * closure a script could read):
 *
 * - `os.open`/`posix.open` with `dir_fd`: the event carries the name, not the folder the descriptor names;
 * - `_posixsubprocess.fork_exec`: Python 3.13 does not audit it (subprocess audits before calling it);
 * - thread starts (`_thread.start_new_thread`, `start_new`, `start_joinable_thread`, threading's own reference),
 *   `atexit.register`, `threading._register_atexit`, `weakref.finalize`, `signal.signal`, `codecs.register`,
 *   `codecs.register_error`: what a confined thread passes runs confined;
 * - `_imp.create_builtin` and `create_dynamic` for a module already loaded, or one whose functions are replaced here
 *   (loaded or not): a second module object made from its spec would hand a script the original functions back;
 * - `_imp.exec_builtin` and `exec_dynamic`: a multi-phase extension module a confined import created fills its own
 *   namespace there, and the journal keeps that as its definition (a reload executes the existing module, whose
 *   functions stay replaced: not refused).
 */

#include <Python.h>

#include <algorithm>
#include <cstring>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_paths.hh"

namespace blender::angelo_guard {

enum Original {
  OPEN,
  FORK_EXEC,
  START_NEW_THREAD,
  START_NEW,
  START_JOINABLE,
  THREADING_START_JOINABLE,
  THREADING_REGISTER_ATEXIT,
  ATEXIT_REGISTER,
  SIGNAL,
  CODECS_REGISTER,
  CODECS_REGISTER_ERROR,
  CREATE_BUILTIN,
  CREATE_DYNAMIC,
  EXEC_BUILTIN,
  EXEC_DYNAMIC,
  GET_FROZEN_OBJECT,
  ORIGINALS,
};
static PyObject *originals[ORIGINALS] = {nullptr};

static PyObject *call_original(Original which, PyObject *args, PyObject *kwargs)
{
  return PyObject_Call(originals[which], args, kwargs);
}

/** `args` with the callable at `position` (or keyword `keyword`) given the trampoline, when confined. */
static PyObject *call_deferring(Original which, PyObject *args, PyObject *kwargs, Py_ssize_t position,
                                const char *keyword)
{
  if (!confined_now()) {
    return call_original(which, args, kwargs);
  }
  PyObject *new_args = PyTuple_GetSlice(args, 0, PyTuple_GET_SIZE(args));
  PyObject *new_kwargs = kwargs ? PyDict_Copy(kwargs) : nullptr;
  if (new_args == nullptr) {
    Py_XDECREF(new_kwargs);
    return nullptr;
  }
  if (PyTuple_GET_SIZE(new_args) > position) {
    PyObject *function = PyTuple_GET_ITEM(new_args, position);
    if (PyCallable_Check(function)) {
      PyObject *deferred = defer(function);
      if (deferred == nullptr) {
        Py_DECREF(new_args);
        Py_XDECREF(new_kwargs);
        return nullptr;
      }
      PyTuple_SET_ITEM(new_args, position, deferred); /* Steals; the slice's reference goes. */
      Py_DECREF(function);
    }
  }
  else if (keyword != nullptr && new_kwargs != nullptr) {
    PyObject *function = PyDict_GetItemString(new_kwargs, keyword);
    if (function != nullptr && PyCallable_Check(function)) {
      PyObject *deferred = defer(function);
      if (deferred == nullptr) {
        Py_DECREF(new_args);
        Py_DECREF(new_kwargs);
        return nullptr;
      }
      PyDict_SetItemString(new_kwargs, keyword, deferred);
      Py_DECREF(deferred);
    }
  }
  PyObject *result = PyObject_Call(originals[which], new_args, new_kwargs);
  Py_DECREF(new_args);
  Py_XDECREF(new_kwargs);
  return result;
}

static PyObject *wrapped_open(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  PyObject *dir_fd = kwargs ? PyDict_GetItemString(kwargs, "dir_fd") : nullptr;
  if (dir_fd != nullptr && dir_fd != Py_None && PyTuple_GET_SIZE(args) >= 2 && confined_now()) {
    const int folder = int(PyLong_AsLong(dir_fd));
    const long flags = PyLong_AsLong(PyTuple_GET_ITEM(args, 1));
    PyErr_Clear();
    PyObject *path = nullptr;
    if (!PyUnicode_FSDecoder(PyTuple_GET_ITEM(args, 0), &path)) {
      PyErr_Clear();
      refuse(PRIVATE, "os.open"); /* Fail closed. */
      return nullptr;
    }
    const char *text = PyUnicode_AsUTF8(path);
    const bool write = (flags & (0x1 | 0x2 | 0x40 | 0x200 | 0x400 | 0x8)) != 0;
    const Refusal refusal = text ? folders().judge(text, write, folder) : Refusal::Private;
    Py_DECREF(path);
    PyErr_Clear();
    if (refusal == Refusal::Private) {
      refuse(PRIVATE, "os.open");
      return nullptr;
    }
    if (refusal == Refusal::Startup) {
      refuse(STARTUP, "os.open");
      return nullptr;
    }
    if (refusal == Refusal::ToolOutput) {
      refuse(TOOL_OUTPUT, "os.open");
      return nullptr;
    }
    if (refusal == Refusal::Network) {
      refuse(NETWORK, "os.open");
      return nullptr;
    }
  }
  return call_original(OPEN, args, kwargs);
}

static PyObject *wrapped_fork_exec(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  if (confined_now()) {
    refuse(PROGRAMS, "fork_exec");
    return nullptr;
  }
  return call_original(FORK_EXEC, args, kwargs);
}

static PyObject *wrapped_start_new_thread(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  return call_deferring(START_NEW_THREAD, args, kwargs, 0, "function");
}

static PyObject *wrapped_start_new(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  return call_deferring(START_NEW, args, kwargs, 0, "function");
}

static PyObject *wrapped_start_joinable(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  return call_deferring(START_JOINABLE, args, kwargs, 0, "function");
}

static PyObject *wrapped_threading_start_joinable(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  return call_deferring(THREADING_START_JOINABLE, args, kwargs, 0, "function");
}

/** `threading._register_atexit(func, *args)`: run when the interpreter's threads are joined at exit; it wraps what it
 * is given in a partial of its own, so a confined thread's callable is confined as it is registered. */
static PyObject *wrapped_threading_register_atexit(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  return call_deferring(THREADING_REGISTER_ATEXIT, args, kwargs, 0, nullptr);
}

/* `weakref.finalize(obj, func, *args)`: a confined thread's `func` (installed code too: the arguments are the
 * script's) runs confined when `obj` dies, and never at interpreter exit, where nothing could confine it. Its object
 * is too small for the allocation record (`__slots__ = ()`), so it is judged as it is made. */
static PyObject *original_finalize_init = nullptr;

static PyObject *wrapped_finalize_init(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  if (!confined_now() || PyTuple_GET_SIZE(args) < 3) {
    return PyObject_Call(original_finalize_init, args, kwargs);
  }
  PyObject *new_args = PyTuple_GetSlice(args, 0, PyTuple_GET_SIZE(args));
  if (new_args == nullptr) {
    return nullptr;
  }
  PyObject *function = PyTuple_GET_ITEM(new_args, 2);
  if (PyCallable_Check(function)) {
    PyObject *deferred = defer(function);
    if (deferred == nullptr) {
      Py_DECREF(new_args);
      return nullptr;
    }
    PyTuple_SET_ITEM(new_args, 2, deferred); /* Steals; the slice's reference goes. */
    Py_DECREF(function);
  }
  PyObject *result = PyObject_Call(original_finalize_init, new_args, kwargs);
  if (result != nullptr) {
    PyObject_SetAttrString(PyTuple_GET_ITEM(new_args, 0), "atexit", Py_False);
    PyErr_Clear();
  }
  Py_DECREF(new_args);
  return result;
}

static PyMethodDef finalize_init_definition = {
    "__init__", (PyCFunction)(void (*)(void))wrapped_finalize_init, METH_VARARGS | METH_KEYWORDS, nullptr};

static void wrap_finalize()
{
  PyObject *weakref = PyImport_ImportModule("weakref");
  PyObject *finalize = weakref ? PyObject_GetAttrString(weakref, "finalize") : nullptr;
  PyObject *init = finalize && original_finalize_init == nullptr ? PyObject_GetAttrString(finalize, "__init__") :
                                                                    nullptr;
  PyObject *function = init ? PyCFunction_NewEx(&finalize_init_definition, nullptr, nullptr) : nullptr;
  PyObject *method = function ? PyInstanceMethod_New(function) : nullptr;
  if (method != nullptr && PyObject_SetAttrString(finalize, "__init__", method) == 0) {
    original_finalize_init = Py_NewRef(init);
  }
  Py_XDECREF(method);
  Py_XDECREF(function);
  Py_XDECREF(init);
  Py_XDECREF(finalize);
  Py_XDECREF(weakref);
  PyErr_Clear();
}

static PyObject *wrapped_atexit_register(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  return call_deferring(ATEXIT_REGISTER, args, kwargs, 0, nullptr);
}

static PyObject *wrapped_signal(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  if (confined_now() && signal_change_noted(args) < 0) {
    return nullptr; /* Noted to put back when the region ends, or refused (signals.cc). */
  }
  return call_deferring(SIGNAL, args, kwargs, 1, "handler");
}

PyObject *guard_original_signal()
{
  return originals[SIGNAL];
}

static PyObject *wrapped_codecs_register(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  return call_deferring(CODECS_REGISTER, args, kwargs, 0, nullptr);
}

/** `codecs.register_error(name, handler)`: a confined thread's handler runs confined, as a search function does. */
static PyObject *wrapped_codecs_register_error(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  return call_deferring(CODECS_REGISTER_ERROR, args, kwargs, 1, nullptr);
}

/** `_imp.get_frozen_object(name)`: the code of a module Python ships frozen (`runpy`, `zipimport`, `os`, the rest of
 * the frozen set), unmarshalled from bytes compiled into the binary: installed code, so its unmarshal passes the
 * provenance check (bpy_angelo_guard_confine_provenance.cc) whoever imports it. Bytes the caller supplies (`data`)
 * stay judged as any other unmarshal. */
thread_local int loading_frozen = 0;

static PyObject *wrapped_get_frozen_object(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  PyObject *data = PyTuple_GET_SIZE(args) > 1 ? PyTuple_GET_ITEM(args, 1) :
                   kwargs != nullptr            ? PyDict_GetItemString(kwargs, "data") :
                                                  nullptr;
  const bool shipped = data == nullptr || data == Py_None;
  loading_frozen += shipped ? 1 : 0;
  PyObject *code = call_original(GET_FROZEN_OBJECT, args, kwargs);
  loading_frozen -= shipped ? 1 : 0;
  return code;
}

/** Every extension or built-in module is made here, however it is imported (`importlib.import_module` raises no
 * `import` event): a module that opens files natively (NATIVE_FILE_MODULES) is refused while confined, and so is a
 * second module object for one already loaded, or for one whose functions Angelo replaced, loaded or not (deleted from
 * `sys.modules`, or the same extension file under another name): it would carry the originals
 * (bpy_angelo_guard_confine_wrap_owners.cc). The spec is read once, into one of Angelo's own, so what is checked is
 * what is made. */
/** A spec's text field (`name`, `origin`), or empty: read for the family check in any context. */
static std::string spec_text(PyObject *spec, const char *field)
{
  PyObject *value = spec != nullptr ? PyObject_GetAttrString(spec, field) : nullptr;
  const char *text = value != nullptr && PyUnicode_Check(value) ? PyUnicode_AsUTF8(value) : nullptr;
  std::string result = text != nullptr ? text : "";
  Py_XDECREF(value);
  PyErr_Clear();
  return result;
}

static PyObject *guarded_imp(Original which, PyObject *args, PyObject *kwargs)
{
  if (region_seeded && confined_now()) {
    refuse_seeded_import(); /* An extension's native initialization could call what the script placed. */
    return nullptr;
  }
  if (!confined_now() || PyTuple_GET_SIZE(args) < 1) {
    /* Any context: a module of the sub-interpreter family made under any name (an alias spec raises no `import`
     * event) has every hot path ask for good (the re-read of f3b1e5f, MUST-1). */
    if (PyTuple_GET_SIZE(args) >= 1) {
      PyObject *given = PyTuple_GET_ITEM(args, 0);
      if (module_is_one_of(spec_text(given, "name"), spec_text(given, "origin"), true, INTERPRETER_MODULES)) {
        confine_ask_always();
      }
    }
    extension_initializing++;
    PyObject *made = call_original(which, args, kwargs);
    extension_initializing--;
    return made;
  }
  std::string text;
  PyObject *spec = steady_spec(PyTuple_GET_ITEM(args, 0), text);
  if (spec == nullptr) {
    refuse(GUARD, "an import whose spec names no module"); /* Fail closed: what would be made can't be told. */
    return nullptr;
  }
  /* By full name, last component or file, from Angelo's own copy of the spec: an alias (`x._dbm`) is the same
   * extension (the re-read of f3b1e5f, MUST-1). */
  const std::string origin = spec_text(spec, "origin");
  if (module_is_one_of(text, origin, true, INTERPRETER_MODULES)) {
    confine_ask_always();
  }
  const bool native = module_is_one_of(text, origin, true, NATIVE_FILE_MODULES);
  PyObject *key = PyUnicode_FromStringAndSize(text.data(), Py_ssize_t(text.size()));
  const bool loaded = key && PyDict_Contains(PyImport_GetModuleDict(), key) == 1;
  Py_XDECREF(key);
  PyErr_Clear();
  const bool wrapped = names_a_wrapped_module(text.c_str(), which == CREATE_DYNAMIC);
  if (native || loaded || wrapped) {
    Py_DECREF(spec);
    if (native) {
      refuse(NATIVE, "a module that opens files natively");
    }
    else {
      refuse(GUARD, "reload");
    }
    return nullptr;
  }
  /* A new tuple (a whole-tuple slice is the caller's own): the steady spec, then the rest as given. */
  PyObject *steady_args = PyTuple_New(PyTuple_GET_SIZE(args));
  if (steady_args == nullptr) {
    Py_DECREF(spec);
    return nullptr;
  }
  PyTuple_SET_ITEM(steady_args, 0, spec); /* Steals. */
  for (Py_ssize_t i = 1; i < PyTuple_GET_SIZE(args); i++) {
    PyTuple_SET_ITEM(steady_args, i, Py_NewRef(PyTuple_GET_ITEM(args, i)));
  }
  extension_initializing++;
  PyObject *made = call_original(which, steady_args, kwargs);
  extension_initializing--;
  Py_DECREF(steady_args);
  return made;
}

/* -------------------------------------------------------------------- */
/** \name An extension module's own definitions
 *
 * A multi-phase extension module (pybind11's, any `PyModuleDef_Init`) is created empty, put in `sys.modules`, then
 * filled by its C code in `_imp.exec_dynamic`/`exec_builtin`, with no Python frame of its own: what it adds there is
 * its definition, not the script's rebinding, so the journal keeps it (bpy_angelo_guard_confine_journal.cc). Only
 * a module this thread's confined import just created gets the window, once, and only for what is not written by
 * agent code running inside it (an import hook the script left).
 * \{ */

thread_local PyObject *defining_namespace = nullptr;
thread_local int extension_initializing = 0;
/** Modules confined `create_*` calls made on this thread and not yet executed (weak references). */
static thread_local std::vector<PyObject *> *fresh_modules = nullptr;

static void note_fresh(PyObject *module)
{
  if (module == nullptr || !PyModule_Check(module)) {
    return;
  }
  if (fresh_modules == nullptr) {
    fresh_modules = new std::vector<PyObject *>();
  }
  /* Drop the dead ones first. */
  auto dead_end = std::remove_if(fresh_modules->begin(), fresh_modules->end(), [](PyObject *reference) {
    PyObject *target = nullptr;
    const bool dead = PyWeakref_GetRef(reference, &target) != 1;
    Py_XDECREF(target);
    if (dead) {
      Py_DECREF(reference);
    }
    PyErr_Clear();
    return dead;
  });
  fresh_modules->erase(dead_end, fresh_modules->end());
  if (PyObject *reference = PyWeakref_NewRef(module, nullptr)) {
    fresh_modules->push_back(reference);
  }
  PyErr_Clear();
}

/** Whether `module` is one a confined `create_*` made and nobody executed yet; it is not from now on. */
static bool take_fresh(PyObject *module)
{
  if (fresh_modules == nullptr) {
    return false;
  }
  for (auto it = fresh_modules->begin(); it != fresh_modules->end(); ++it) {
    PyObject *target = nullptr;
    const int alive = PyWeakref_GetRef(*it, &target);
    Py_XDECREF(target);
    PyErr_Clear();
    if (alive == 1 && target == module) {
      Py_DECREF(*it);
      fresh_modules->erase(it);
      return true;
    }
  }
  return false;
}

static PyObject *wrapped_create_builtin(PyObject * /*s*/, PyObject *a, PyObject *k)
{
  PyObject *module = guarded_imp(CREATE_BUILTIN, a, k);
  if (confined_now()) {
    note_fresh(module);
  }
  return module;
}
static PyObject *wrapped_create_dynamic(PyObject * /*s*/, PyObject *a, PyObject *k)
{
  PyObject *module = guarded_imp(CREATE_DYNAMIC, a, k);
  if (confined_now()) {
    note_fresh(module);
  }
  return module;
}

static PyObject *exec_defining(Original which, PyObject *args, PyObject *kwargs)
{
  if (region_seeded && confined_now()) {
    refuse_seeded_import();
    return nullptr;
  }
  PyObject *module = PyTuple_GET_SIZE(args) > 0 ? PyTuple_GET_ITEM(args, 0) : nullptr;
  if (module == nullptr || !PyModule_Check(module) || !confined_now() || !take_fresh(module)) {
    extension_initializing++;
    PyObject *result = call_original(which, args, kwargs);
    extension_initializing--;
    return result;
  }
  PyObject *previous = defining_namespace;
  defining_namespace = PyModule_GetDict(module); /* Borrowed; the module lives through the call. */
  extension_initializing++;
  PyObject *result = call_original(which, args, kwargs);
  extension_initializing--;
  defining_namespace = previous;
  return result;
}

static PyObject *wrapped_exec_builtin(PyObject * /*s*/, PyObject *a, PyObject *k)
{
  return exec_defining(EXEC_BUILTIN, a, k);
}
static PyObject *wrapped_exec_dynamic(PyObject * /*s*/, PyObject *a, PyObject *k)
{
  return exec_defining(EXEC_DYNAMIC, a, k);
}

/** \} */

struct Replacement {
  const char *modules[2]; /* Every module that holds the name (os's `open` is posix's). */
  const char *name;
  Original which;
  PyMethodDef definition;
};

#define WRAP(function) (PyCFunction)(void (*)(void))(function), METH_VARARGS | METH_KEYWORDS

static Replacement replacements[] = {
    {{"posix", "os"}, "open", OPEN, {"open", WRAP(wrapped_open), nullptr}},
    {{"nt", "os"}, "open", OPEN, {"open", WRAP(wrapped_open), nullptr}},
    {{"_posixsubprocess", nullptr}, "fork_exec", FORK_EXEC, {"fork_exec", WRAP(wrapped_fork_exec), nullptr}},
    {{"_thread", nullptr},
     "start_new_thread",
     START_NEW_THREAD,
     {"start_new_thread", WRAP(wrapped_start_new_thread), nullptr}},
    {{"_thread", nullptr}, "start_new", START_NEW, {"start_new", WRAP(wrapped_start_new), nullptr}},
    {{"_thread", nullptr},
     "start_joinable_thread",
     START_JOINABLE,
     {"start_joinable_thread", WRAP(wrapped_start_joinable), nullptr}},
    {{"threading", nullptr},
     "_start_joinable_thread",
     THREADING_START_JOINABLE,
     {"_start_joinable_thread", WRAP(wrapped_threading_start_joinable), nullptr}},
    {{"threading", nullptr},
     "_register_atexit",
     THREADING_REGISTER_ATEXIT,
     {"_register_atexit", WRAP(wrapped_threading_register_atexit), nullptr}},
    {{"atexit", nullptr}, "register", ATEXIT_REGISTER, {"register", WRAP(wrapped_atexit_register), nullptr}},
    {{"_signal", "signal"}, "signal", SIGNAL, {"signal", WRAP(wrapped_signal), nullptr}},
    {{"_codecs", "codecs"}, "register", CODECS_REGISTER, {"register", WRAP(wrapped_codecs_register), nullptr}},
    {{"_codecs", "codecs"},
     "register_error",
     CODECS_REGISTER_ERROR,
     {"register_error", WRAP(wrapped_codecs_register_error), nullptr}},
    {{"_imp", nullptr},
     "create_builtin",
     CREATE_BUILTIN,
     {"create_builtin", WRAP(wrapped_create_builtin), nullptr}},
    {{"_imp", nullptr},
     "create_dynamic",
     CREATE_DYNAMIC,
     {"create_dynamic", WRAP(wrapped_create_dynamic), nullptr}},
    {{"_imp", nullptr}, "exec_builtin", EXEC_BUILTIN, {"exec_builtin", WRAP(wrapped_exec_builtin), nullptr}},
    {{"_imp", nullptr}, "exec_dynamic", EXEC_DYNAMIC, {"exec_dynamic", WRAP(wrapped_exec_dynamic), nullptr}},
    {{"_imp", nullptr},
     "get_frozen_object",
     GET_FROZEN_OBJECT,
     {"get_frozen_object", WRAP(wrapped_get_frozen_object), nullptr}},
};

/** Every other reference to an original in a loaded module (a `from _thread import start_new_thread`, a
 * `functools.wraps` wrapper's `__wrapped__`: `signal.signal.__wrapped__` is `_signal.signal`) points to its
 * replacement too, so a script finds no original to call. One walk over every loaded module's namespace serves every
 * replacement made at install (`replace_references_begin`/`_end`), and every function's defaults and keyword
 * defaults and every class of Python source (an attribute, its methods' defaults) hold the wrapper too, so no captured
 * original is left to call (head-16 read): the install wraps dozens of entry points, and a
 * walk per entry point cost a quarter of a second of window start. Nothing is asked for an attribute it may lack: a
 * function's `__wrapped__` is read from its own dict, so no AttributeError is made and thrown away. */
static int batching = 0;
static std::vector<std::pair<PyObject *, PyObject *>> batched; /* (original, wrapper), strong references. */

static void walk_references(const std::unordered_map<PyObject *, PyObject *> &replace)
{
  PyObject *modules = PyImport_GetModuleDict();
  PyObject *snapshot = PyDict_Values(modules);
  if (snapshot == nullptr) {
    PyErr_Clear();
    return;
  }
  auto wrapper_of = [&](PyObject *value) -> PyObject * {
    const auto found = replace.find(value);
    return found == replace.end() ? nullptr : found->second;
  };
  /* An original a function captured as a default (`def _send(self, buf, write=_write)`, `_close=os.close`: every
   * multiprocessing.connection.Connection) or a keyword default (head-16 read: calling one skips every wrapper). */
  auto replace_in_defaults = [&](PyObject *function) {
    PyFunctionObject *code = (PyFunctionObject *)function;
    if (code->func_defaults != nullptr && PyTuple_Check(code->func_defaults)) {
      PyObject *defaults = code->func_defaults;
      bool changed = false;
      for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(defaults) && !changed; i++) {
        changed = wrapper_of(PyTuple_GET_ITEM(defaults, i)) != nullptr;
      }
      if (changed) {
        if (PyObject *fresh = PyTuple_New(PyTuple_GET_SIZE(defaults))) {
          for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(defaults); i++) {
            PyObject *item = PyTuple_GET_ITEM(defaults, i);
            PyObject *wrapper = wrapper_of(item);
            PyTuple_SET_ITEM(fresh, i, Py_NewRef(wrapper ? wrapper : item));
          }
          PyFunction_SetDefaults(function, fresh);
          Py_DECREF(fresh);
        }
      }
    }
    if (code->func_kwdefaults != nullptr && PyDict_Check(code->func_kwdefaults)) {
      PyObject *key, *item;
      Py_ssize_t at = 0;
      std::vector<std::pair<PyObject *, PyObject *>> found;
      while (PyDict_Next(code->func_kwdefaults, &at, &key, &item)) {
        if (PyObject *wrapper = wrapper_of(item)) {
          found.emplace_back(key, wrapper);
        }
      }
      for (auto &[name, wrapper] : found) {
        PyDict_SetItem(code->func_kwdefaults, name, wrapper);
      }
    }
    PyErr_Clear();
  };
  /* A class of Python source holding an original as an attribute (`_write = os.write`) or in a method's defaults. */
  std::unordered_set<PyObject *> visited_classes;
  auto replace_in_class = [&](PyObject *type) {
    PyObject *names = ((PyTypeObject *)type)->tp_dict;
    if (names == nullptr) {
      return;
    }
    PyObject *key, *item;
    Py_ssize_t at = 0;
    std::vector<std::pair<PyObject *, PyObject *>> found;
    while (PyDict_Next(names, &at, &key, &item)) {
      if (PyObject *wrapper = wrapper_of(item)) {
        found.emplace_back(Py_NewRef(key), Py_NewRef(wrapper));
      }
      else if (PyFunction_Check(item)) {
        replace_in_defaults(item);
      }
      else if (Py_IS_TYPE(item, &PyStaticMethod_Type) || Py_IS_TYPE(item, &PyClassMethod_Type)) {
        PyObject *inner = PyObject_GetAttrString(item, "__func__");
        if (inner != nullptr && PyFunction_Check(inner)) {
          replace_in_defaults(inner);
        }
        Py_XDECREF(inner);
      }
    }
    for (auto &[name, wrapper] : found) {
      PyObject_SetAttr(type, name, wrapper); /* A heap type's own: updates its slots too. */
      Py_DECREF(name);
      Py_DECREF(wrapper);
    }
    PyErr_Clear();
  };
  for (Py_ssize_t i = 0; i < PyList_GET_SIZE(snapshot); i++) {
    PyObject *module = PyList_GET_ITEM(snapshot, i);
    if (!PyModule_Check(module)) {
      continue;
    }
    PyObject *names = PyModule_GetDict(module); /* Borrowed. */
    if (names == nullptr) {
      PyErr_Clear();
      continue;
    }
    PyObject *key, *value;
    Py_ssize_t position = 0;
    std::vector<std::pair<PyObject *, PyObject *>> set_items; /* key -> new value, new references */
    while (PyDict_Next(names, &position, &key, &value)) {
      if (PyObject *wrapper = wrapper_of(value)) {
        set_items.emplace_back(Py_NewRef(key), Py_NewRef(wrapper));
      }
      else if (PyAnySet_Check(value)) {
        /* A set naming what supports a feature (`os.supports_dir_fd`), checked by identity (`os.open in …`):
         * the wrapper is in it too. */
        std::vector<PyObject *> added;
        if (PyObject *items = PyObject_GetIter(value)) {
          while (PyObject *item = PyIter_Next(items)) {
            if (PyObject *wrapper = wrapper_of(item)) {
              added.push_back(wrapper);
            }
            Py_DECREF(item);
          }
          Py_DECREF(items);
        }
        PyErr_Clear();
        if (added.empty()) {
          continue;
        }
        if (PyFrozenSet_Check(value)) {
          PyObject *grown = PySet_New(value);
          for (PyObject *wrapper : added) {
            if (grown != nullptr) {
              PySet_Add(grown, wrapper);
            }
          }
          if (PyObject *frozen = grown ? PyFrozenSet_New(grown) : nullptr) {
            set_items.emplace_back(Py_NewRef(key), frozen);
          }
          Py_XDECREF(grown);
        }
        else {
          for (PyObject *wrapper : added) {
            PySet_Add(value, wrapper);
          }
        }
        PyErr_Clear();
      }
      else if (PyFunction_Check(value)) {
        PyObject *own = ((PyFunctionObject *)value)->func_dict; /* `__wrapped__` lives there, if anywhere. */
        PyObject *wrapped = own != nullptr ? PyDict_GetItemString(own, "__wrapped__") : nullptr; /* Borrowed. */
        if (PyObject *wrapper = wrapped ? wrapper_of(wrapped) : nullptr) {
          PyDict_SetItemString(own, "__wrapped__", wrapper);
        }
        replace_in_defaults(value);
      }
      else if (PyType_Check(value) && PyType_HasFeature((PyTypeObject *)value, Py_TPFLAGS_HEAPTYPE) &&
               !PyType_HasFeature((PyTypeObject *)value, Py_TPFLAGS_IMMUTABLETYPE) &&
               visited_classes.insert(value).second)
      {
        replace_in_class(value);
      }
    }
    for (auto &[name, replacement] : set_items) {
      PyDict_SetItem(names, name, replacement);
      Py_DECREF(name);
      Py_DECREF(replacement);
    }
    PyErr_Clear();
  }
  Py_DECREF(snapshot);
  PyErr_Clear();
}

void replace_references_begin()
{
  batching++;
}

void replace_references_end()
{
  if (--batching > 0 || batched.empty()) {
    return;
  }
  std::unordered_map<PyObject *, PyObject *> replace;
  for (const auto &[original, wrapper] : batched) {
    replace[original] = wrapper;
  }
  for (auto &[original, wrapper] : replace) { /* A wrapper wrapped again: its original points to the last. */
    for (int hops = 0; hops < 8 && replace.count(wrapper); hops++) {
      wrapper = replace[wrapper];
    }
  }
  walk_references(replace);
  for (auto &[original, wrapper] : batched) {
    Py_DECREF(original);
    Py_DECREF(wrapper);
  }
  batched.clear();
}

void replace_references(PyObject *original, PyObject *wrapper)
{
  note_wrapped_owner(original); /* Its module is never made again while confined (guarded_imp). */
  if (batching > 0) {
    batched.emplace_back(Py_NewRef(original), Py_NewRef(wrapper));
    return;
  }
  walk_references({{original, wrapper}});
}

int install_wrappers()
{
  steady_spec_prepare();
  for (Replacement &replacement : replacements) {
    PyObject *home = PyImport_ImportModule(replacement.modules[0]);
    if (home == nullptr) {
      PyErr_Clear(); /* This platform has no such module (nt on POSIX, _posixsubprocess on Windows). */
      continue;
    }
    PyObject *current = PyObject_GetAttrString(home, replacement.name);
    if (current == nullptr) {
      PyErr_Clear();
      Py_DECREF(home);
      continue;
    }
    if (originals[replacement.which] == nullptr) {
      originals[replacement.which] = Py_NewRef(current);
    }
    PyObject *wrapper = PyCFunction_NewEx(&replacement.definition, nullptr, nullptr);
    if (wrapper == nullptr) {
      Py_DECREF(current);
      Py_DECREF(home);
      return -1;
    }
    for (const char *module_name : replacement.modules) {
      if (module_name == nullptr) {
        continue;
      }
      PyObject *module = PyImport_ImportModule(module_name);
      if (module == nullptr) {
        PyErr_Clear();
        continue;
      }
      note_install_site(module_name);
      PyObject *held = PyObject_GetAttrString(module, replacement.name);
      PyErr_Clear();
      /* Only where the name still is the original (os.open is posix.open; never a different function). */
      if (held == originals[replacement.which]) {
        PyObject_SetAttrString(module, replacement.name, wrapper);
      }
      Py_XDECREF(held);
      Py_DECREF(module);
    }
    replace_references(originals[replacement.which], wrapper);
    Py_DECREF(wrapper);
    Py_DECREF(current);
    Py_DECREF(home);
  }
  wrap_finalize();
  PyErr_Clear();
  return 0;
}

}  // namespace blender::angelo_guard
