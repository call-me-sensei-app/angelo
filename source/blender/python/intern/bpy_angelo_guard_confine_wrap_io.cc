/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * More entry points no audit event covers, replaced by C functions whose originals stay in C
 * (doc/angelo/script_confinement.rst; bpy_angelo_guard_confine_wrap.cc has the first set):
 *
 * - `builtins.compile`: code it makes while confined is the agent's, by object (provenance, not names);
 * - `builtins.__build_class__`: a class statement with the script's body makes the script's class;
 * - `_imp._fix_co_filename`: renames code; only importlib's own bytecode loader may, while confined;
 * - the descriptor I/O of `os` (`read`, `write`, `pread`, `sendfile`, `splice`, `ftruncate`, …): a descriptor
 *   Angelo holds open on a private file or a socket is neither read, written nor sent on by a confined thread;
 * - `sys.monitoring`'s tools and callbacks: a confined thread cannot leave a callback the interpreter calls later;
 * - `socket.socketpair`: a connected pair inside this process (asyncio's event loop wakes itself through one) is
 *   allowed, with the socket events it raises itself (on Windows it pairs through the loopback); no other socket;
 * - native file entry points and SQLite: bpy_angelo_guard_confine_wrap_native.cc.
 */

#include <Python.h>

#include <sys/stat.h>

#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_journal.hh"
#include "bpy_angelo_guard_confine_paths.hh"

namespace blender::angelo_guard {

/* -------------------------------------------------------------------- */
/** \name A table of originals, called by index (the wrapper's `__self__` is that index: nothing to call)
 * \{ */

struct IoWrap {
  const char *modules[2]; /* Where the name lives (os's are posix's or nt's). */
  const char *name;
  signed char reads[2];  /* Positions of descriptors it reads (-1: none). */
  signed char writes[2]; /* Positions of descriptors it writes or changes. */
  PyObject *original;
};

static IoWrap io_wraps[] = {
    {{"posix", "nt"}, "read", {0, -1}, {-1, -1}, nullptr},
    {{"posix", "nt"}, "readv", {0, -1}, {-1, -1}, nullptr},
    {{"posix", "nt"}, "pread", {0, -1}, {-1, -1}, nullptr},
    {{"posix", "nt"}, "preadv", {0, -1}, {-1, -1}, nullptr},
    {{"posix", "nt"}, "write", {-1, -1}, {0, -1}, nullptr},
    {{"posix", "nt"}, "writev", {-1, -1}, {0, -1}, nullptr},
    {{"posix", "nt"}, "pwrite", {-1, -1}, {0, -1}, nullptr},
    {{"posix", "nt"}, "pwritev", {-1, -1}, {0, -1}, nullptr},
    {{"posix", "nt"}, "sendfile", {1, -1}, {0, -1}, nullptr},
    {{"posix", "nt"}, "splice", {0, -1}, {1, -1}, nullptr},
    {{"posix", "nt"}, "copy_file_range", {0, -1}, {1, -1}, nullptr},
    {{"posix", "nt"}, "ftruncate", {-1, -1}, {0, -1}, nullptr},
    {{"posix", "nt"}, "fchmod", {-1, -1}, {0, -1}, nullptr},
    {{"posix", "nt"}, "fchown", {-1, -1}, {0, -1}, nullptr},
};

/** A descriptor's use by a confined thread: a socket never (raw sends are the network), a private file never, a
 * startup or tool-output file not for a change. */
static int judge_descriptor(PyObject *value, bool write)
{
  if (value == nullptr || !PyLong_Check(value)) {
    return 0;
  }
  const int fd = int(PyLong_AsLong(value));
  if (PyErr_Occurred()) {
    PyErr_Clear();
    return 0;
  }
#ifndef _WIN32
  struct stat info;
  if (fstat(fd, &info) == 0 && S_ISSOCK(info.st_mode)) {
    return refuse(NETWORK, "a socket's descriptor");
  }
#endif
  const std::optional<std::string> path = descriptor_path(fd);
  if (!path) {
    return 0; /* A pipe or a terminal: no file. */
  }
  switch (folders().judge(*path, write)) {
    case Refusal::Private:
      return refuse(PRIVATE, path->c_str());
    case Refusal::Startup:
      return refuse(STARTUP, path->c_str());
    case Refusal::ToolOutput:
      return refuse(TOOL_OUTPUT, path->c_str());
    case Refusal::None:
      break;
  }
  return 0;
}

static PyObject *io_call(PyObject *index, PyObject *args, PyObject *kwargs)
{
  const IoWrap &wrap = io_wraps[PyLong_AsSsize_t(index)];
  if (confined_now()) {
    for (const signed char position : wrap.reads) {
      if (position >= 0 && position < PyTuple_GET_SIZE(args) &&
          judge_descriptor(PyTuple_GET_ITEM(args, position), false) != 0)
      {
        return nullptr;
      }
    }
    for (const signed char position : wrap.writes) {
      if (position >= 0 && position < PyTuple_GET_SIZE(args) &&
          judge_descriptor(PyTuple_GET_ITEM(args, position), true) != 0)
      {
        return nullptr;
      }
    }
  }
  return PyObject_Call(wrap.original, args, kwargs);
}

static PyMethodDef io_definition = {
    "descriptor_io", (PyCFunction)(void (*)(void))io_call, METH_VARARGS | METH_KEYWORDS, nullptr};

/** \} */

/* -------------------------------------------------------------------- */
/** \name compile, _fix_co_filename, sys.monitoring
 * \{ */

static PyObject *original_compile = nullptr;
static PyObject *original_fix_co_filename = nullptr;

static PyObject *wrapped_compile(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  PyObject *code = PyObject_Call(original_compile, args, kwargs);
  if (code != nullptr && PyCode_Check(code) && armed.load(std::memory_order_relaxed) && confined_now()) {
    /* The hook let an installed name through only for that file's own source: that code is installed. */
    const char *file = PyUnicode_AsUTF8(((PyCodeObject *)code)->co_filename);
    PyErr_Clear();
    if ((file == nullptr || file[0] == '<' || !folders().trusted(file)) && installed_import_globals() == nullptr) {
      taint(code); /* (Unless an installed import compiles it as it loads afresh, unseeded: journal.cc.) */
    }
  }
  return code;
}

static PyObject *wrapped_fix_co_filename(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  if (confined_now() && !in_bytecode_loader()) {
    refuse(GUARD, "_imp._fix_co_filename");
    return nullptr;
  }
  return PyObject_Call(original_fix_co_filename, args, kwargs);
}

static PyObject *monitoring_originals = nullptr; /* dict: name -> original */

static PyObject *wrapped_monitoring(PyObject *name, PyObject *args, PyObject *kwargs)
{
  if (confined_now()) {
    refuse(GUARD, "sys.monitoring");
    return nullptr;
  }
  PyObject *original = PyDict_GetItemWithError(monitoring_originals, name); /* Borrowed. */
  return original ? PyObject_Call(original, args, kwargs) : nullptr;
}

PyObject *monitoring_original(const char *name)
{
  PyObject *original = monitoring_originals ? PyDict_GetItemString(monitoring_originals, name) : nullptr;
  if (original == nullptr) { /* Not wrapped (yet): the module's own. */
    PyObject *monitoring = PySys_GetObject("monitoring"); /* Borrowed. */
    PyObject *held = monitoring ? PyObject_GetAttrString(monitoring, name) : nullptr;
    original = held;
    Py_XDECREF(held); /* The module keeps it alive. */
  }
  PyErr_Clear();
  return original;
}

static PyMethodDef compile_definition = {
    "compile", (PyCFunction)(void (*)(void))wrapped_compile, METH_VARARGS | METH_KEYWORDS, nullptr};
static PyMethodDef fix_definition = {"_fix_co_filename",
                                     (PyCFunction)(void (*)(void))wrapped_fix_co_filename,
                                     METH_VARARGS | METH_KEYWORDS,
                                     nullptr};
static PyMethodDef monitoring_definition = {
    "monitoring", (PyCFunction)(void (*)(void))wrapped_monitoring, METH_VARARGS | METH_KEYWORDS, nullptr};

/** \} */

/* `builtins.__build_class__`: a class statement whose body is the script's code makes the script's class, whatever
 * metaclass (Python code, installed: `ABCMeta`, `EnumType`) allocates it (bpy_angelo_guard_confine_types.cc). */
static PyObject *original_build_class = nullptr;

static PyObject *wrapped_build_class(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  PyObject *body = PyTuple_GET_SIZE(args) > 0 ? PyTuple_GET_ITEM(args, 0) : nullptr;
  const bool script = body != nullptr && PyFunction_Check(body) && confined_now() &&
                      agent_code((PyCodeObject *)PyFunction_GetCode(body));
  script_class_statement += script ? 1 : 0;
  PyObject *made = PyObject_Call(original_build_class, args, kwargs);
  script_class_statement -= script ? 1 : 0;
  return made;
}

static PyMethodDef build_class_definition = {"__build_class__",
                                             (PyCFunction)(void (*)(void))wrapped_build_class,
                                             METH_VARARGS | METH_KEYWORDS,
                                             nullptr};

static PyObject *original_socketpair = nullptr;
thread_local int making_socketpair = 0;

static PyObject *wrapped_socketpair(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  making_socketpair++;
  PyObject *result = PyObject_Call(original_socketpair, args, kwargs);
  making_socketpair--;
  return result;
}

static PyMethodDef socketpair_definition = {
    "socketpair", (PyCFunction)(void (*)(void))wrapped_socketpair, METH_VARARGS | METH_KEYWORDS, nullptr};

void put_in(const char *module_name, const char *name, PyObject *wrapper, PyObject *original)
{
  PyObject *module = PyImport_ImportModule(module_name);
  if (module == nullptr) {
    PyErr_Clear();
    return;
  }
  PyObject *held = PyObject_GetAttrString(module, name);
  PyErr_Clear();
  if (held == original) {
    PyObject_SetAttrString(module, name, wrapper);
  }
  Py_XDECREF(held);
  Py_DECREF(module);
  replace_references(original, wrapper);
}

int install_wrappers_io()
{
  for (size_t i = 0; i < sizeof(io_wraps) / sizeof(io_wraps[0]); i++) {
    IoWrap &wrap = io_wraps[i];
    for (const char *module_name : wrap.modules) {
      PyObject *module = PyImport_ImportModule(module_name);
      PyObject *current = module ? PyObject_GetAttrString(module, wrap.name) : nullptr;
      Py_XDECREF(module);
      PyErr_Clear();
      if (current == nullptr || wrap.original != nullptr) {
        Py_XDECREF(current);
        continue;
      }
      wrap.original = current;
      PyObject *index = PyLong_FromSize_t(i);
      PyObject *wrapper = index ? PyCFunction_NewEx(&io_definition, index, nullptr) : nullptr;
      Py_XDECREF(index);
      if (wrapper != nullptr) {
        put_in(module_name, wrap.name, wrapper, wrap.original);
        put_in("os", wrap.name, wrapper, wrap.original);
      }
      Py_XDECREF(wrapper);
    }
  }
  if (original_compile == nullptr) {
    PyObject *builtins = PyEval_GetBuiltins(); /* Borrowed. */
    original_compile = Py_XNewRef(PyDict_GetItemString(builtins, "compile"));
    if (PyObject *wrapper = original_compile ? PyCFunction_NewEx(&compile_definition, nullptr, nullptr) : nullptr) {
      PyDict_SetItemString(builtins, "compile", wrapper);
      replace_references(original_compile, wrapper);
      Py_DECREF(wrapper);
    }
  }
  if (original_build_class == nullptr) {
    PyObject *builtins = PyEval_GetBuiltins(); /* Borrowed. */
    original_build_class = Py_XNewRef(PyDict_GetItemString(builtins, "__build_class__"));
    if (PyObject *wrapper = original_build_class ?
                                PyCFunction_NewEx(&build_class_definition, nullptr, nullptr) :
                                nullptr)
    {
      PyDict_SetItemString(builtins, "__build_class__", wrapper);
      replace_references(original_build_class, wrapper);
      Py_DECREF(wrapper);
    }
  }
  if (original_fix_co_filename == nullptr) {
    PyObject *imp = PyImport_ImportModule("_imp");
    original_fix_co_filename = imp ? PyObject_GetAttrString(imp, "_fix_co_filename") : nullptr;
    Py_XDECREF(imp);
    if (PyObject *wrapper = original_fix_co_filename ? PyCFunction_NewEx(&fix_definition, nullptr, nullptr) :
                                                       nullptr)
    {
      put_in("_imp", "_fix_co_filename", wrapper, original_fix_co_filename);
      Py_DECREF(wrapper);
    }
  }
  if (monitoring_originals == nullptr) {
    PyObject *monitoring = PySys_GetObject("monitoring"); /* Borrowed. */
    monitoring_originals = PyDict_New();
    for (const char *name : {"use_tool_id", "free_tool_id", "clear_tool_id", "register_callback", "set_events",
                             "set_local_events", "restart_events"})
    {
      PyObject *original = monitoring ? PyObject_GetAttrString(monitoring, name) : nullptr;
      PyObject *key = PyUnicode_FromString(name);
      PyObject *wrapper = original && key ? PyCFunction_NewEx(&monitoring_definition, key, nullptr) : nullptr;
      if (wrapper != nullptr) {
        PyDict_SetItem(monitoring_originals, key, original);
        PyObject_SetAttrString(monitoring, name, wrapper);
      }
      Py_XDECREF(wrapper);
      Py_XDECREF(key);
      Py_XDECREF(original);
      PyErr_Clear();
    }
  }
  if (original_socketpair == nullptr) {
    PyObject *socket_module = PyImport_ImportModule("socket");
    original_socketpair = socket_module ? PyObject_GetAttrString(socket_module, "socketpair") : nullptr;
    Py_XDECREF(socket_module);
    if (PyObject *wrapper = original_socketpair ? PyCFunction_NewEx(&socketpair_definition, nullptr, nullptr) :
                                                  nullptr)
    {
      put_in("socket", "socketpair", wrapper, original_socketpair);
      Py_DECREF(wrapper);
    }
    PyErr_Clear();
  }
  install_native_entries();
  install_process_wrappers();
  provenance_install();
  PyErr_Clear();
  return 0;
}

}  // namespace blender::angelo_guard
