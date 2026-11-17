/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * State of the whole process a confined script could change for everything Angelo does after it, and for the
 * programs Angelo starts later (doc/angelo/script_confinement.rst, "Work that outlives the script"). Python's own
 * mapping (`os.environ`) is the sweep's; this is the state under it, which no mapping shows:
 *
 * - refused while confined, inside a region or not (`process_change_refused`), whatever installed code the script
 *   steers into making the change: the environment (`os.putenv`, `os.unsetenv`, `os.environ` and its data:
 *   bpy_angelo_guard_confine_environ.cc), the working folder (`os.chdir`, `os.fchdir`) and the file-creation mask
 *   (`os.umask`), which carry into every program Angelo starts and every relative path it opens;
 * - put back when the last confined region ends, and refused from code confined outside a region (a generator the
 *   script left, resumed later), where no region end would put them back: the C locale, the interpreter's
 *   process-wide settings (garbage collection and its thresholds, the recursion limit, the switch interval, the
 *   integer-digit limit, the dynamic-loading flags, the thread stack size), and the C environment native code
 *   changed (every difference, whoever made it; the time zone `time.tzset` read from it);
 * - refused while confined, because they can't be put back or they carry into every program Angelo starts:
 *   resource limits, priority, CPU affinity and scheduling, the process's identity and session, `chroot` and
 *   namespaces; a callback a confined thread registers to run at `fork` runs confined (an import's own too).
 *
 * Signals, the signal mask, the default socket timeout and the warning filters are bpy_angelo_guard_confine_signals.cc's.
 *
 * Another thread of Angelo's changing the same state while a script runs would see its change put back too.
 */

#include <Python.h>

#include <clocale>
#include <cstring>
#include <map>
#include <optional>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

#ifdef _WIN32
#  include <direct.h>
#  include <stdlib.h>
#else
#  include <sys/stat.h>
#  include <unistd.h>
extern char **environ;
#endif

#include "bpy_angelo_guard_confine_intern.hh"

namespace blender::angelo_guard {

#ifdef _WIN32
using OsText = std::wstring;
#else
using OsText = std::string;
#endif

/* -------------------------------------------------------------------- */
/** \name What the first region saw (the GIL serializes regions' starts and ends)
 * \{ */

struct Snapshot {
  std::map<OsText, OsText> environment;
  OsText folder;
  long recursion = 0, digits = 0, dlopen = -1, stack = 0;
  double interval = 0.0;
  bool collecting = true;
  PyObject *thresholds = nullptr, *debug = nullptr;
  std::optional<long> umask_was; /* Noted by the wrapper at the first confined change. */
  std::optional<std::string> locale_was;
  bool taken = false;
};
static Snapshot snapshot;

static std::map<OsText, OsText> environment_now()
{
  std::map<OsText, OsText> found;
#ifdef _WIN32
  for (wchar_t **entry = _wenviron; entry != nullptr && *entry != nullptr; entry++) {
    const OsText text = *entry;
    const size_t equals = text.find(L'=', 1); /* Windows keeps "=C:=C:\\" entries: their name starts with '='. */
#else
  for (char **entry = environ; entry != nullptr && *entry != nullptr; entry++) {
    const OsText text = *entry;
    const size_t equals = text.find('=');
#endif
    if (equals != OsText::npos) {
      found[text.substr(0, equals)] = text.substr(equals + 1);
    }
  }
  return found;
}

static OsText folder_now()
{
#ifdef _WIN32
  wchar_t *folder = _wgetcwd(nullptr, 0);
#else
  char *folder = getcwd(nullptr, 0);
#endif
  OsText result = folder ? folder : OsText();
  free(folder);
  return result;
}

/** A setting read through its Python getter (`sys.getswitchinterval`, `gc.get_threshold`…): new reference or null. */
static PyObject *call(const char *module_name, const char *function, PyObject *argument = nullptr)
{
  PyObject *module = PyImport_ImportModule(module_name);
  PyObject *callable = module ? PyObject_GetAttrString(module, function) : nullptr;
  PyObject *result = callable ? (argument ? PyObject_CallOneArg(callable, argument) :
                                            PyObject_CallNoArgs(callable)) :
                                nullptr;
  Py_XDECREF(callable);
  Py_XDECREF(module);
  PyErr_Clear();
  return result;
}

static long long_of(PyObject *value, long otherwise)
{
  const long result = value ? PyLong_AsLong(value) : otherwise;
  Py_XDECREF(value);
  PyErr_Clear();
  return result;
}

void process_snapshot()
{
  Py_CLEAR(snapshot.thresholds);
  Py_CLEAR(snapshot.debug);
  snapshot = Snapshot();
  snapshot.environment = environment_now();
  snapshot.folder = folder_now();
  snapshot.recursion = Py_GetRecursionLimit();
  snapshot.collecting = PyGC_IsEnabled();
  snapshot.thresholds = gc_call("get_threshold");
  snapshot.debug = gc_call("get_debug");
  PyObject *interval = call("sys", "getswitchinterval");
  snapshot.interval = interval ? PyFloat_AsDouble(interval) : 0.0;
  Py_XDECREF(interval);
  snapshot.digits = long_of(call("sys", "get_int_max_str_digits"), 0);
#ifndef _WIN32
  snapshot.dlopen = long_of(call("sys", "getdlopenflags"), -1);
#endif
  snapshot.stack = long_of(call("_thread", "stack_size"), 0);
  PyErr_Clear();
  signals_snapshot();
  snapshot.taken = true;
}

bool process_snapshot_taken()
{
  return snapshot.taken;
}

int process_change_refused(const char *what, bool always)
{
  /* No exemption for an installed module's own top level: a script chooses what runs there (`runpy.run_module` with
   * `init_globals`, a rebound builtin an import calls), so while any confined frame is on the stack it is the
   * script's change. */
  if (!confined_now()) {
    return 0;
  }
  if (!always && process_snapshot_taken()) {
    return 0; /* Put back when the last region ends. */
  }
  return refuse(PROCESS, what);
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Putting it back
 * \{ */

static void found(PyObject *findings, const char *what)
{
  if (findings != nullptr) {
    if (PyObject *text = PyUnicode_FromFormat("process: %s put back", what)) {
      PyList_Append(findings, text);
      Py_DECREF(text);
    }
  }
  PyErr_Clear();
}

/** Whether the C library's time zone, read from `TZ` by `time.tzset`, may differ from what the environment says. */
static bool time_zone_changed(const std::map<OsText, OsText> &now)
{
#ifdef _WIN32
  const OsText name = L"TZ";
#else
  const OsText name = "TZ";
#endif
  const auto before = snapshot.environment.find(name);
  const auto here = now.find(name);
  return (before == snapshot.environment.end()) != (here == now.end()) ||
         (here != now.end() && here->second != before->second);
}

static void environment_put_back(PyObject *findings)
{
  const std::map<OsText, OsText> now = environment_now();
  [[maybe_unused]] const bool time_zone = time_zone_changed(now);
  int changed = 0;
  for (const auto &[name, value] : now) {
    if (snapshot.environment.count(name) == 0) {
#ifdef _WIN32
      _wputenv_s(name.c_str(), L"");
#else
      unsetenv(name.c_str());
#endif
      changed++;
    }
  }
  for (const auto &[name, value] : snapshot.environment) {
    auto here = now.find(name);
    if (here == now.end() || here->second != value) {
#ifdef _WIN32
      _wputenv_s(name.c_str(), value.c_str());
#else
      setenv(name.c_str(), value.c_str(), 1);
#endif
      changed++;
    }
  }
  if (changed > 0) {
    found(findings, "the environment");
  }
#ifndef _WIN32
  if (time_zone) {
    Py_XDECREF(call("time", "tzset")); /* The zone the script's `time.tzset` read goes with its `TZ`. */
    PyErr_Clear();
  }
#endif
}

static void set(const char *module_name, const char *function, PyObject *argument)
{
  const bool was_busy = busy;
  busy = true; /* Our own: never refused by the settings' wrappers. */
  Py_XDECREF(call(module_name, function, argument));
  busy = was_busy;
  Py_XDECREF(argument);
}

static void process_restore_now(PyObject *findings);

void process_restore(PyObject *findings)
{
  const bool was_busy = busy;
  busy = true; /* The confinement's own calls: never refused by the settings' wrappers (confined frames may be on the
                * stack: a step of a generator a script left ends here). */
  process_restore_now(findings);
  busy = was_busy;
}

static void process_restore_now(PyObject *findings)
{
  if (!snapshot.taken) {
    return;
  }
  snapshot.taken = false;
  PyObject *raised = PyErr_GetRaisedException();
  environment_put_back(findings);
  if (!snapshot.folder.empty() && folder_now() != snapshot.folder) {
#ifdef _WIN32
    _wchdir(snapshot.folder.c_str());
#else
    if (chdir(snapshot.folder.c_str()) != 0) {
      /* Gone or unreachable: nothing to put back. */
    }
#endif
    found(findings, "the working folder");
  }
  if (snapshot.umask_was) {
#ifdef _WIN32
    _umask(int(*snapshot.umask_was));
#else
    umask(mode_t(*snapshot.umask_was));
#endif
    found(findings, "the file-creation mask");
  }
  if (snapshot.locale_was) {
    setlocale(LC_ALL, snapshot.locale_was->c_str());
    found(findings, "the locale");
  }
  if (Py_GetRecursionLimit() != snapshot.recursion) {
    Py_SetRecursionLimit(snapshot.recursion);
    found(findings, "the recursion limit");
  }
  if (bool(PyGC_IsEnabled()) != snapshot.collecting) {
    (snapshot.collecting ? PyGC_Enable : PyGC_Disable)();
    found(findings, "garbage collection");
  }
  for (const auto &[getter, setter, held] :
       {std::tuple{"get_threshold", "set_threshold", snapshot.thresholds},
        std::tuple{"get_debug", "set_debug", snapshot.debug}})
  {
    PyObject *now = gc_call(getter);
    if (held != nullptr && now != nullptr && PyObject_RichCompareBool(now, held, Py_EQ) == 0) {
      PyObject *arguments = PyTuple_Check(held) ? Py_NewRef(held) : PyTuple_Pack(1, held);
      PyObject *module = PyImport_ImportModule("gc");
      PyObject *function = module ? PyObject_GetAttrString(module, setter) : nullptr;
      Py_XDECREF(function ? PyObject_CallObject(function, arguments) : nullptr);
      Py_XDECREF(function);
      Py_XDECREF(module);
      Py_XDECREF(arguments);
      found(findings, "garbage collection");
    }
    Py_XDECREF(now);
    PyErr_Clear();
  }
  PyObject *interval = call("sys", "getswitchinterval");
  if (interval != nullptr && PyFloat_AsDouble(interval) != snapshot.interval) {
    set("sys", "setswitchinterval", PyFloat_FromDouble(snapshot.interval));
    found(findings, "the switch interval");
  }
  Py_XDECREF(interval);
  if (long_of(call("sys", "get_int_max_str_digits"), snapshot.digits) != snapshot.digits) {
    set("sys", "set_int_max_str_digits", PyLong_FromLong(snapshot.digits));
    found(findings, "the integer-digit limit");
  }
#ifndef _WIN32
  if (snapshot.dlopen >= 0 && long_of(call("sys", "getdlopenflags"), snapshot.dlopen) != snapshot.dlopen) {
    set("sys", "setdlopenflags", PyLong_FromLong(snapshot.dlopen));
    found(findings, "the dynamic-loading flags");
  }
#endif
  if (long_of(call("_thread", "stack_size"), snapshot.stack) != snapshot.stack) {
    set("_thread", "stack_size", PyLong_FromLong(snapshot.stack));
    found(findings, "the thread stack size");
  }
  signals_restore(findings);
  Py_CLEAR(snapshot.thresholds);
  Py_CLEAR(snapshot.debug);
  PyErr_Clear();
  PyErr_SetRaisedException(raised);
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Wrappers: what no event shows (`os.umask`, `_locale.setlocale`) and what is refused
 * \{ */

static PyObject *original_umask = nullptr, *original_setlocale = nullptr;

static PyObject *wrapped_umask(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  if (process_change_refused("os.umask (set a file's mode with os.chmod instead)", true) < 0) {
    return nullptr;
  }
  PyObject *previous = PyObject_Call(original_umask, args, kwargs);
  if (previous != nullptr && confined_now() && snapshot.taken && !snapshot.umask_was) {
    snapshot.umask_was = PyLong_AsLong(previous);
    PyErr_Clear();
  }
  return previous;
}

static PyObject *wrapped_setlocale(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  const bool changes = PyTuple_GET_SIZE(args) > 1 && PyTuple_GET_ITEM(args, 1) != Py_None;
  if (changes && process_change_refused("setlocale", false) < 0) {
    return nullptr;
  }
  if (changes && confined_now() && snapshot.taken && !snapshot.locale_was) {
    if (const char *now = setlocale(LC_ALL, nullptr)) {
      snapshot.locale_was = std::string(now);
    }
  }
  return PyObject_Call(original_setlocale, args, kwargs);
}

/** Changes to the whole process that can't be put back, or carry into every program Angelo starts. */
static const char *const REFUSED[] = {
    "nice",         "setpriority",   "sched_setaffinity", "sched_setscheduler", "sched_setparam",
    "setsid",       "setpgid",       "setpgrp",           "setuid",             "setgid",
    "seteuid",      "setegid",       "setreuid",          "setregid",           "setresuid",
    "setresgid",    "setgroups",     "initgroups",        "chroot",             "unshare",
    "setns",
};
static PyObject *refused_originals = nullptr; /* dict: name -> original */

static PyObject *wrapped_refused(PyObject *name, PyObject *args, PyObject *kwargs)
{
  if (confined_now()) {
    refuse(PROCESS, PyUnicode_AsUTF8(name));
    return nullptr;
  }
  PyObject *original = PyDict_GetItemWithError(refused_originals, name); /* Borrowed. */
  return original ? PyObject_Call(original, args, kwargs) : nullptr;
}

#ifndef _WIN32 /* Windows has no `os.register_at_fork` (no fork). */
/** `os.register_at_fork`: whatever a confined thread registers runs confined (the trampoline), an installed import's
 * own reseeding or lock reset included (a script can steer what an import's top level registers). */
static PyObject *original_register_at_fork = nullptr;

static PyObject *wrapped_register_at_fork(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  if (kwargs == nullptr || !confined_now()) {
    return PyObject_Call(original_register_at_fork, args, kwargs);
  }
  PyObject *deferring = PyDict_Copy(kwargs);
  PyObject *key, *value;
  Py_ssize_t position = 0;
  std::vector<std::pair<PyObject *, PyObject *>> changes;
  while (deferring && PyDict_Next(kwargs, &position, &key, &value)) {
    if (PyCallable_Check(value)) {
      PyObject *trampoline = defer(value);
      if (trampoline == nullptr) {
        Py_DECREF(deferring);
        return nullptr;
      }
      changes.emplace_back(key, trampoline);
    }
  }
  for (auto &[name, trampoline] : changes) {
    PyDict_SetItem(deferring, name, trampoline);
    Py_DECREF(trampoline);
  }
  PyObject *result = deferring ? PyObject_Call(original_register_at_fork, args, deferring) : nullptr;
  Py_XDECREF(deferring);
  return result;
}
#endif

/** The interpreter's process-wide settings: put back when the last region ends (`process_restore`), refused outside
 * one; `gc.freeze`/`unfreeze` refused while confined. `_thread.stack_size()` with no argument only reads. */
static const char *const SETTINGS[][2] = {
    {"gc", "enable"},
    {"gc", "disable"},
    {"gc", "set_threshold"},
    {"gc", "set_debug"},
    {"sys", "setrecursionlimit"},
    {"sys", "setswitchinterval"},
    {"sys", "set_int_max_str_digits"},
    {"sys", "setdlopenflags"},
    {"_thread", "stack_size"},
    {"gc", "freeze"},
    {"gc", "unfreeze"},
};
static PyObject *settings_originals = nullptr; /* dict: "module.name" -> original */

static PyObject *wrapped_setting(PyObject *key, PyObject *args, PyObject *kwargs)
{
  const bool reads = PyTuple_GET_SIZE(args) == 0 && (kwargs == nullptr || PyDict_GET_SIZE(kwargs) == 0) &&
                     PyUnicode_CompareWithASCIIString(key, "_thread.stack_size") == 0;
  /* The collector's permanent generation is the regions' own (what exists before a region is frozen while it
   * runs): never a confined thread's to move. */
  const bool always = PyUnicode_CompareWithASCIIString(key, "gc.freeze") == 0 ||
                      PyUnicode_CompareWithASCIIString(key, "gc.unfreeze") == 0;
  /* `busy`: the confinement's own call (a region putting the process back, freezing the collector). */
  if (!reads && !busy && process_change_refused(PyUnicode_AsUTF8(key), always) < 0) {
    return nullptr;
  }
  PyObject *original = PyDict_GetItemWithError(settings_originals, key); /* Borrowed. */
  return original ? PyObject_Call(original, args, kwargs) : nullptr;
}

static PyMethodDef setting_definition = {
    "process_setting", (PyCFunction)(void (*)(void))wrapped_setting, METH_VARARGS | METH_KEYWORDS, nullptr};

static void wrap_settings()
{
  settings_originals = PyDict_New();
  for (const auto &[module_name, name] : SETTINGS) {
    PyObject *module = settings_originals ? PyImport_ImportModule(module_name) : nullptr;
    PyObject *original = module ? PyObject_GetAttrString(module, name) : nullptr;
    PyObject *key = original ? PyUnicode_FromFormat("%s.%s", module_name, name) : nullptr;
    PyObject *wrapper = key ? PyCFunction_NewEx(&setting_definition, key, nullptr) : nullptr;
    if (wrapper != nullptr && PyDict_SetItem(settings_originals, key, original) == 0) {
      put_in(module_name, name, wrapper, original);
    }
    Py_XDECREF(wrapper);
    Py_XDECREF(key);
    Py_XDECREF(original);
    Py_XDECREF(module);
    PyErr_Clear();
  }
}

#ifndef _WIN32
static PyMethodDef register_at_fork_definition = {"register_at_fork",
                                                  (PyCFunction)(void (*)(void))wrapped_register_at_fork,
                                                  METH_VARARGS | METH_KEYWORDS,
                                                  nullptr};
#endif

static PyMethodDef umask_definition = {
    "umask", (PyCFunction)(void (*)(void))wrapped_umask, METH_VARARGS | METH_KEYWORDS, nullptr};
static PyMethodDef setlocale_definition = {
    "setlocale", (PyCFunction)(void (*)(void))wrapped_setlocale, METH_VARARGS | METH_KEYWORDS, nullptr};
static PyMethodDef refused_definition = {
    "process_state", (PyCFunction)(void (*)(void))wrapped_refused, METH_VARARGS | METH_KEYWORDS, nullptr};

static void wrap_one(const char *module_name, const char *name, PyMethodDef *definition, PyObject **original)
{
  PyObject *module = PyImport_ImportModule(module_name);
  *original = module ? PyObject_GetAttrString(module, name) : nullptr;
  Py_XDECREF(module);
  PyErr_Clear();
  if (*original == nullptr) {
    return;
  }
  if (PyObject *wrapper = PyCFunction_NewEx(definition, nullptr, nullptr)) {
    put_in(module_name, name, wrapper, *original);
    Py_DECREF(wrapper);
  }
  PyErr_Clear();
}

void install_process_wrappers()
{
  if (refused_originals != nullptr) {
    return;
  }
  refused_originals = PyDict_New();
#ifdef _WIN32
  const char *const os_module = "nt";
#else
  const char *const os_module = "posix";
#endif
  wrap_one(os_module, "umask", &umask_definition, &original_umask); /* `os.umask` too: every reference. */
  wrap_one("_locale", "setlocale", &setlocale_definition, &original_setlocale);
#ifndef _WIN32
  wrap_one(os_module, "register_at_fork", &register_at_fork_definition, &original_register_at_fork);
#endif
  install_signal_wrappers();
  wrap_settings();
  resume_install();
  install_environ_view();
  PyObject *module = PyImport_ImportModule(os_module);
  for (const char *name : REFUSED) {
    PyObject *original = module ? PyObject_GetAttrString(module, name) : nullptr;
    PyObject *key = PyUnicode_FromString(name);
    PyObject *wrapper = original && key ? PyCFunction_NewEx(&refused_definition, key, nullptr) : nullptr;
    if (wrapper != nullptr) {
      PyDict_SetItem(refused_originals, key, original);
      put_in(os_module, name, wrapper, original);
      put_in("os", name, wrapper, original);
    }
    Py_XDECREF(wrapper);
    Py_XDECREF(key);
    Py_XDECREF(original);
    PyErr_Clear();
  }
  Py_XDECREF(module);
  PyErr_Clear();
}

/** \} */

}  // namespace blender::angelo_guard
