/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Native file entry points the hook can't see (doc/angelo/script_confinement.rst), replaced by C functions whose
 * originals stay in C:
 *
 * - functions and methods of modules loaded before any script that open a path natively (`_cycles.osl_compile`,
 *   `blf.load`, the certificate loaders of `ssl.SSLContext`): their paths are judged like `open`'s;
 * - SQLite: every connection a confined thread opens can attach no database (`ATTACH`, `VACUUM INTO`, which
 *   attaches its target), and `Connection.setlimit` is refused to a confined thread, so the limit stays.
 *
 * - `faulthandler`'s writers: refused while confined (they write to any descriptor).
 *
 * Modules that open files natively and are not loaded yet are refused on import (`NATIVE_FILE_MODULES`).
 */

#include <Python.h>

#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_paths.hh"

namespace blender::angelo_guard {

/* -------------------------------------------------------------------- */
/** \name Native file entry points of modules already loaded
 * \{ */

struct PathEntry {
  const char *module;
  const char *type; /* null: a module function; else a method of this type (patched in its dict). */
  const char *name;
  signed char reads[2];
  signed char writes[2];
  const char *keywords[2]; /* Keyword names of the read paths, when given by keyword. */
  PyObject *original;
};

static PathEntry path_entries[] = {
    {"_cycles", nullptr, "osl_compile", {0, -1}, {1, -1}, {nullptr, nullptr}, nullptr},
    {"blf", nullptr, "load", {0, -1}, {-1, -1}, {"filepath", nullptr}, nullptr},
    {"_ssl", "_SSLContext", "load_cert_chain", {0, 1}, {-1, -1}, {"certfile", "keyfile"}, nullptr},
    {"_ssl", "_SSLContext", "load_verify_locations", {0, 1}, {-1, -1}, {"cafile", "capath"}, nullptr},
    {"_ssl", "_SSLContext", "load_dh_params", {0, -1}, {-1, -1}, {"path", nullptr}, nullptr},
};

int judge_entry_path(PyObject *value, bool write)
{
  if (value == nullptr || value == Py_None) {
    return 0;
  }
  PyObject *path = PyOS_FSPath(value);
  if (path == nullptr) {
    PyErr_Clear();
    return 0;
  }
  PyObject *text = PyUnicode_Check(path) ? Py_NewRef(path) : PyUnicode_DecodeFSDefaultAndSize(
                                                                 PyBytes_AS_STRING(path), PyBytes_GET_SIZE(path));
  Py_DECREF(path);
  const char *utf8 = text ? PyUnicode_AsUTF8(text) : nullptr;
  const Refusal refusal = utf8 ? folders().judge(utf8, write) : Refusal::Private;
  Py_XDECREF(text);
  PyErr_Clear();
  switch (refusal) {
    case Refusal::Private:
      return refuse(PRIVATE, "a native file entry point");
    case Refusal::Startup:
      return refuse(STARTUP, "a native file entry point");
    case Refusal::ToolOutput:
      return refuse(TOOL_OUTPUT, "a native file entry point");
    case Refusal::None:
      break;
  }
  return 0;
}

static PyObject *path_entry_call(PyObject *index, PyObject *args, PyObject *kwargs)
{
  const PathEntry &entry = path_entries[PyLong_AsSsize_t(index)];
  /* A method's `self` comes first in `args` (the wrapper is a module-level callable on the type's dict). */
  const Py_ssize_t shift = entry.type != nullptr ? 1 : 0;
  if (confined_now()) {
    for (int i = 0; i < 2; i++) {
      const signed char position = entry.reads[i];
      if (position >= 0) {
        PyObject *value = position + shift < PyTuple_GET_SIZE(args) ? PyTuple_GET_ITEM(args, position + shift) :
                          (kwargs && entry.keywords[i]) ? PyDict_GetItemString(kwargs, entry.keywords[i]) :
                                                          nullptr;
        if (judge_entry_path(value, false) != 0) {
          return nullptr;
        }
      }
    }
    for (const signed char position : entry.writes) {
      if (position >= 0 && position + shift < PyTuple_GET_SIZE(args) &&
          judge_entry_path(PyTuple_GET_ITEM(args, position + shift), true) != 0)
      {
        return nullptr;
      }
    }
  }
  return PyObject_Call(entry.original, args, kwargs);
}

static PyMethodDef path_entry_definition = {
    "native_path_entry", (PyCFunction)(void (*)(void))path_entry_call, METH_VARARGS | METH_KEYWORDS, nullptr};

/** A method wrapper for a type's dict: binds `self` like a function, then calls `path_entry_call`. */
static PyObject *method_entry(PyObject *self, PyObject *args, PyObject *kwargs, size_t index)
{
  PyObject *full = PyTuple_New(PyTuple_GET_SIZE(args) + 1);
  if (full == nullptr) {
    return nullptr;
  }
  PyTuple_SET_ITEM(full, 0, Py_NewRef(self));
  for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(args); i++) {
    PyTuple_SET_ITEM(full, i + 1, Py_NewRef(PyTuple_GET_ITEM(args, i)));
  }
  PyObject *key = PyLong_FromSize_t(index);
  PyObject *result = key ? path_entry_call(key, full, kwargs) : nullptr;
  Py_XDECREF(key);
  Py_DECREF(full);
  return result;
}

#define METHOD_ENTRY(i) \
  [](PyObject *self, PyObject *args, PyObject *kwargs) -> PyObject * { return method_entry(self, args, kwargs, i); }
static PyMethodDef method_definitions[] = {
    {}, {}, {"load_cert_chain", (PyCFunction)(void (*)(void))(PyCFunctionWithKeywords)METHOD_ENTRY(2),
             METH_VARARGS | METH_KEYWORDS, nullptr},
    {"load_verify_locations", (PyCFunction)(void (*)(void))(PyCFunctionWithKeywords)METHOD_ENTRY(3),
     METH_VARARGS | METH_KEYWORDS, nullptr},
    {"load_dh_params", (PyCFunction)(void (*)(void))(PyCFunctionWithKeywords)METHOD_ENTRY(4),
     METH_VARARGS | METH_KEYWORDS, nullptr},
};
#undef METHOD_ENTRY

static void install_path_entries()
{
  for (size_t i = 0; i < sizeof(path_entries) / sizeof(path_entries[0]); i++) {
    PathEntry &entry = path_entries[i];
    if (entry.original != nullptr) {
      continue;
    }
    PyObject *module = PyImport_ImportModule(entry.module);
    if (module == nullptr) {
      PyErr_Clear();
      continue;
    }
    if (entry.type == nullptr) {
      entry.original = PyObject_GetAttrString(module, entry.name);
      PyObject *index = PyLong_FromSize_t(i);
      PyObject *wrapper = entry.original && index ? PyCFunction_NewEx(&path_entry_definition, index, nullptr) :
                                                    nullptr;
      if (wrapper != nullptr) {
        put_in(entry.module, entry.name, wrapper, entry.original);
      }
      Py_XDECREF(wrapper);
      Py_XDECREF(index);
    }
    else {
      PyObject *type = PyObject_GetAttrString(module, entry.type);
      PyObject *names = type && PyType_Check(type) ? ((PyTypeObject *)type)->tp_dict : nullptr;
      PyObject *original = names ? PyDict_GetItemString(names, entry.name) : nullptr; /* Borrowed. */
      PyObject *guarded = original ? PyDescr_NewMethod((PyTypeObject *)type, &method_definitions[i]) : nullptr;
      if (guarded != nullptr) {
        entry.original = Py_NewRef(original);
        PyDict_SetItemString(names, entry.name, guarded); /* Immutable to Python; changed here, in C. */
        PyType_Modified((PyTypeObject *)type);
      }
      Py_XDECREF(guarded);
      Py_XDECREF(type);
    }
    Py_DECREF(module);
    PyErr_Clear();
  }
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name SQLite
 * \{ */

static PyObject *original_setlimit = nullptr;
static constexpr int SQLITE_LIMIT_ATTACHED = 7;

static initproc original_init = nullptr;

/** `Connection.__init__` (the slot and the attribute): a confined thread's connection gets the limit once it is
 * open (the connect audit event comes before the connection counts as initialized). */
static int limited_init(PyObject *self, PyObject *args, PyObject *kwargs)
{
  const int result = original_init(self, args, kwargs);
  if (result == 0 && confined_now()) {
    sqlite_connected(self);
  }
  return result;
}

static PyObject *init_method(PyObject *self, PyObject *args, PyObject *kwargs)
{
  if (limited_init(self, args, kwargs) != 0) {
    return nullptr;
  }
  Py_RETURN_NONE;
}

static PyMethodDef init_definition = {
    "__init__", (PyCFunction)(void (*)(void))init_method, METH_VARARGS | METH_KEYWORDS, nullptr};

void sqlite_connected(PyObject *connection)
{
  if (connection == nullptr || original_setlimit == nullptr) {
    return;
  }
  /* No database can be attached to a confined thread's connection: `ATTACH` and `VACUUM INTO` (which attaches its
   * target) read or write files no hook sees. */
  PyObject *result = PyObject_CallFunction(original_setlimit, "Oii", connection, SQLITE_LIMIT_ATTACHED, 0);
  Py_XDECREF(result);
  PyErr_Clear();
}

static PyObject *guarded_setlimit(PyObject *connection, PyObject *args)
{
  if (confined_now()) {
    refuse(GUARD, "sqlite3.Connection.setlimit");
    return nullptr;
  }
  PyObject *full = PyTuple_New(PyTuple_GET_SIZE(args) + 1);
  if (full == nullptr) {
    return nullptr;
  }
  PyTuple_SET_ITEM(full, 0, Py_NewRef(connection));
  for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(args); i++) {
    PyTuple_SET_ITEM(full, i + 1, Py_NewRef(PyTuple_GET_ITEM(args, i)));
  }
  PyObject *result = PyObject_Call(original_setlimit, full, nullptr);
  Py_DECREF(full);
  return result;
}

static PyMethodDef setlimit_definition = {"setlimit", guarded_setlimit, METH_VARARGS, nullptr};

static void seal_sqlite()
{
  PyObject *module = PyImport_ImportModule("_sqlite3");
  PyObject *type = module ? PyObject_GetAttrString(module, "Connection") : nullptr;
  Py_XDECREF(module);
  if (type == nullptr || !PyType_Check(type) || original_setlimit != nullptr) {
    Py_XDECREF(type);
    PyErr_Clear();
    return;
  }
  PyObject *names = ((PyTypeObject *)type)->tp_dict;
  PyObject *original = names ? PyDict_GetItemString(names, "setlimit") : nullptr; /* Borrowed. */
  PyObject *guarded = original ? PyDescr_NewMethod((PyTypeObject *)type, &setlimit_definition) : nullptr;
  if (guarded != nullptr) {
    original_setlimit = Py_NewRef(original);
    PyDict_SetItemString(names, "setlimit", guarded); /* The type is immutable to Python; C changes it here. */
    original_init = ((PyTypeObject *)type)->tp_init;
    ((PyTypeObject *)type)->tp_init = limited_init;
    if (PyObject *init = PyDescr_NewMethod((PyTypeObject *)type, &init_definition)) {
      PyDict_SetItemString(names, "__init__", init); /* What a subclass's `super().__init__` reaches. */
      Py_DECREF(init);
    }
    PyType_Modified((PyTypeObject *)type);
  }
  Py_XDECREF(guarded);
  Py_DECREF(type);
  PyErr_Clear();
}

/** \} */

/* Entry points refused outright while confined, which raise no audit event to judge: `faulthandler` writes
 * tracebacks straight to any descriptor it is given (one Angelo holds on a private file, too), outside every check;
 * Windows' `_overlapped.ConnectPipe` connects to any local named pipe (a service's), and `msvcrt.SetErrorMode` is
 * process state every program Angelo starts inherits. A module absent on this platform is skipped. */
struct RefusedEntry {
  const char *module;
  const char *name;
  const char *const *reason;
  PyObject *original;
};

static RefusedEntry refused_entries[] = {
    {"faulthandler", "enable", &GUARD, nullptr},
    {"faulthandler", "dump_traceback", &GUARD, nullptr},
    {"faulthandler", "dump_traceback_later", &GUARD, nullptr},
    {"faulthandler", "register", &GUARD, nullptr},
    /* Process state no region end could put back: Angelo's crash reports and their file. */
    {"faulthandler", "disable", &GUARD, nullptr},
    {"faulthandler", "unregister", &GUARD, nullptr},
    {"faulthandler", "cancel_dump_traceback_later", &GUARD, nullptr},
    {"_overlapped", "ConnectPipe", &NETWORK, nullptr},
    {"msvcrt", "SetErrorMode", &PROCESS, nullptr},
    {"_wmi", "exec_query", &PROGRAMS, nullptr}, /* Also audited; refused here whatever the audit table says. */
};

static PyObject *refused_call(PyObject *index, PyObject *args, PyObject *kwargs)
{
  const RefusedEntry &entry = refused_entries[PyLong_AsSsize_t(index)];
  if (confined_now()) {
    refuse(*entry.reason, entry.name);
    return nullptr;
  }
  return PyObject_Call(entry.original, args, kwargs);
}

static PyMethodDef refused_definition = {
    "refused_while_confined", (PyCFunction)(void (*)(void))refused_call, METH_VARARGS | METH_KEYWORDS, nullptr};

static void install_refused_entries()
{
  for (size_t i = 0; i < sizeof(refused_entries) / sizeof(refused_entries[0]); i++) {
    RefusedEntry &entry = refused_entries[i];
    PyObject *module = entry.original == nullptr ? PyImport_ImportModule(entry.module) : nullptr;
    entry.original = module ? PyObject_GetAttrString(module, entry.name) : entry.original;
    Py_XDECREF(module);
    PyObject *index = module && entry.original ? PyLong_FromSize_t(i) : nullptr;
    if (PyObject *wrapper = index ? PyCFunction_NewEx(&refused_definition, index, nullptr) : nullptr) {
      put_in(entry.module, entry.name, wrapper, entry.original);
      Py_DECREF(wrapper);
    }
    Py_XDECREF(index);
    PyErr_Clear();
  }
}

/* `_winapi.CreateNamedPipe` (Windows): a confined thread may make only a pipe this process connects to itself, judged
 * by its arguments whoever calls: a fresh name (`FILE_FLAG_FIRST_PIPE_INSTANCE`) in this machine's local namespace
 * (`\\.\pipe\`), one instance. A server other processes connect to (a listener's unlimited instances), a remote
 * name and a pipe that already exists are refused (the network). bpy_angelo_guard_confine_hook.cc lets this thread
 * connect once to the name it made. */
static PyObject *original_create_named_pipe = nullptr;

static PyObject *wrapped_create_named_pipe(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  if (!confined_now()) {
    return PyObject_Call(original_create_named_pipe, args, kwargs);
  }
  PyObject *name = PyTuple_GET_SIZE(args) > 0 ? PyTuple_GET_ITEM(args, 0) : nullptr;
  const char *text = (name != nullptr && PyUnicode_Check(name)) ? PyUnicode_AsUTF8(name) : nullptr;
  PyErr_Clear();
  const long open_mode = PyTuple_GET_SIZE(args) > 1 ? PyLong_AsLong(PyTuple_GET_ITEM(args, 1)) : 0;
  const long instances = PyTuple_GET_SIZE(args) > 3 ? PyLong_AsLong(PyTuple_GET_ITEM(args, 3)) : 0;
  PyErr_Clear();
  constexpr long FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000;
  const std::string key = text ? pipe_key(text) : std::string();
  const bool local = key.rfind("\\\\.\\pipe\\", 0) == 0 && key.size() > 9;
  if (kwargs != nullptr || !local || !(open_mode & FILE_FLAG_FIRST_PIPE_INSTANCE) || instances != 1) {
    refuse(NETWORK, text ? text : "_winapi.CreateNamedPipe");
    return nullptr;
  }
  local_pipe_creating(true);
  PyObject *result = PyObject_Call(original_create_named_pipe, args, kwargs);
  local_pipe_creating(false);
  if (result != nullptr) {
    local_pipe_created(key);
  }
  return result;
}

static PyMethodDef create_named_pipe_definition = {"CreateNamedPipe",
                                                   (PyCFunction)(void (*)(void))wrapped_create_named_pipe,
                                                   METH_VARARGS | METH_KEYWORDS,
                                                   nullptr};

static void install_local_pipe()
{
  PyObject *winapi = PyImport_ImportModule("_winapi");
  if (winapi == nullptr) {
    PyErr_Clear(); /* Not Windows. */
    return;
  }
  original_create_named_pipe = PyObject_GetAttrString(winapi, "CreateNamedPipe");
  PyObject *wrapper = original_create_named_pipe ?
                          PyCFunction_NewEx(&create_named_pipe_definition, nullptr, nullptr) :
                          nullptr;
  if (wrapper != nullptr) {
    put_in("_winapi", "CreateNamedPipe", wrapper, original_create_named_pipe);
  }
  Py_XDECREF(wrapper);
  Py_DECREF(winapi);
  PyErr_Clear();
}

void install_native_entries()
{
  install_local_pipe();
  seal_sqlite();
  install_path_entries();
  install_refused_entries();
  install_pipeline_entries(); /* bpy_angelo_guard_confine_wrap_pipeline.cc */
}

}  // namespace blender::angelo_guard
