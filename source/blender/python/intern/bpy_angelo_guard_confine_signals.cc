/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The rest of the process-wide state a confined script could leave behind (doc/angelo/script_confinement.rst,
 * "Work that outlives the script"), next to bpy_angelo_guard_confine_process.cc:
 *
 * - put back when the last confined region ends: signal dispositions (`signal.signal`: an ignored SIGTERM or SIGINT
 *   would outlive the script and pass to every program Angelo starts), the wakeup descriptor, the default socket
 *   timeout and the warning filters (`warnings.simplefilter("error")` would turn Angelo's own warnings into errors);
 * - put back when the region on the same thread ends: that thread's signal mask (`signal.pthread_sigmask`), which
 *   programs started from the thread inherit;
 * - refused while confined: what can't be read back or put back (`signal.siginterrupt`, interval timers and
 *   `signal.alarm`, which deliver signals after the script), a disposition Python did not set (a native handler,
 *   which `signal.signal` could never restore), and any of the above from code confined outside a region (a generator
 *   the script left, resumed later), which no region end would put back.
 *
 * Dispositions can be set only on the main thread: a region that ends on another thread puts them back the next time
 * the main thread runs Python (`Py_AddPendingCall`), and a region starting before then keeps what was first noted.
 */

#include <Python.h>

#include <map>
#include <optional>

#ifndef _WIN32
#  include <csignal>
#  include <pthread.h>
#endif

#include "bpy_angelo_guard_confine_intern.hh"

namespace blender::angelo_guard {

/* -------------------------------------------------------------------- */
/** \name What the script changed, as it was before
 * \{ */

static std::map<long, PyObject *> dispositions; /* Signal number -> handler before the first confined change. */
static std::optional<long> wakeup_was;
static bool restore_queued = false;
static PyObject *timeout_was = nullptr;          /* `socket.getdefaulttimeout()`, once changed. */
static PyObject *warnings_module = nullptr, *filters_list = nullptr, *filters_copy = nullptr, *action_was = nullptr;
#ifndef _WIN32
static thread_local std::optional<sigset_t> mask_was;
#endif
static PyObject *original_getsignal = nullptr, *original_set_wakeup_fd = nullptr, *original_setdefaulttimeout = nullptr,
                *original_pthread_sigmask = nullptr;
static unsigned long main_thread = 0;

static PyObject *attribute(const char *module_name, const char *name)
{
  PyObject *module = PyImport_ImportModule(module_name);
  PyObject *value = module ? PyObject_GetAttrString(module, name) : nullptr;
  Py_XDECREF(module);
  PyErr_Clear();
  return value;
}

static bool on_main_thread()
{
  return main_thread != 0 && PyThread_get_thread_ident() == main_thread;
}

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

/** Outside a region nothing would put a change back: refused (-1, ScriptConfined set); 0 to go on. */
static int region_or_refuse(const char *what, bool this_thread)
{
  if (this_thread ? depth > 0 : process_snapshot_taken()) {
    return 0;
  }
  return refuse(PROCESS, what);
}

int signal_change_noted(PyObject *args)
{
  if (region_or_refuse("signal", false) < 0) {
    return -1;
  }
  const long number = PyTuple_GET_SIZE(args) > 0 ? PyLong_AsLong(PyTuple_GET_ITEM(args, 0)) : -1;
  if (number == -1 && PyErr_Occurred()) {
    PyErr_Clear();
    return 0; /* The original call reports a bad argument. */
  }
  if (dispositions.count(number) != 0 || original_getsignal == nullptr) {
    return 0;
  }
  PyObject *argument = PyLong_FromLong(number);
  PyObject *handler = argument ? PyObject_CallOneArg(original_getsignal, argument) : nullptr;
  Py_XDECREF(argument);
  if (handler == nullptr) {
    PyErr_Clear();
    return 0;
  }
  if (handler == Py_None) {
    /* A native handler (Blender's crash handler, `faulthandler`): `signal.signal` could not put it back. */
    Py_DECREF(handler);
    return refuse(PROCESS, "signal");
  }
  dispositions[number] = handler; /* Owns the reference. */
  return 0;
}

void signals_snapshot()
{
  if (restore_queued && on_main_thread()) {
    signals_restore_dispositions(nullptr);
  }
  Py_CLEAR(warnings_module);
  Py_CLEAR(filters_list);
  Py_CLEAR(filters_copy);
  Py_CLEAR(action_was);
  static PyObject *name = PyUnicode_InternFromString("warnings");
  warnings_module = name ? PyImport_GetModule(name) : nullptr; /* Only if loaded: nothing to put back otherwise. */
  PyErr_Clear();
  if (warnings_module != nullptr) {
    filters_list = PyObject_GetAttrString(warnings_module, "filters");
    filters_copy = filters_list ? PySequence_List(filters_list) : nullptr;
    action_was = PyObject_GetAttrString(warnings_module, "defaultaction");
    PyErr_Clear();
  }
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Putting it back
 * \{ */

void signals_restore_dispositions(PyObject *findings)
{
  restore_queued = false;
  bool changed = false;
  for (auto it = dispositions.begin(); it != dispositions.end();) {
    PyObject *number = PyLong_FromLong(it->first);
    PyObject *now = number && original_getsignal ? PyObject_CallOneArg(original_getsignal, number) : nullptr;
    if (now != nullptr && now != it->second && guard_original_signal() != nullptr) {
      Py_XDECREF(PyObject_CallFunctionObjArgs(guard_original_signal(), number, it->second, nullptr));
      changed = true;
    }
    Py_XDECREF(now);
    Py_XDECREF(number);
    PyErr_Clear();
    Py_DECREF(it->second);
    it = dispositions.erase(it);
  }
  if (wakeup_was && original_set_wakeup_fd != nullptr) {
    PyObject *descriptor = PyLong_FromLong(*wakeup_was);
    PyObject *now = descriptor ? PyObject_CallOneArg(original_set_wakeup_fd, descriptor) : nullptr;
    changed = changed || (now != nullptr && PyLong_AsLong(now) != *wakeup_was);
    Py_XDECREF(now);
    Py_XDECREF(descriptor);
    PyErr_Clear();
    wakeup_was.reset();
  }
  if (changed) {
    found(findings, "the signal handlers");
  }
}

static int restore_later(void * /*unused*/)
{
  if (restore_queued) {
    signals_restore_dispositions(nullptr);
  }
  return 0;
}

static void warnings_put_back(PyObject *findings)
{
  if (warnings_module == nullptr || filters_list == nullptr || filters_copy == nullptr) {
    return;
  }
  bool changed = false;
  PyObject *now = PyObject_GetAttrString(warnings_module, "filters");
  if (now != filters_list) {
    PyObject_SetAttrString(warnings_module, "filters", filters_list);
    changed = true;
  }
  Py_XDECREF(now);
  if (PyObject_RichCompareBool(filters_list, filters_copy, Py_EQ) != 1) {
    PySequence_SetSlice(filters_list, 0, PY_SSIZE_T_MAX, filters_copy);
    changed = true;
  }
  PyObject *action = PyObject_GetAttrString(warnings_module, "defaultaction");
  if (action_was != nullptr && action != action_was) {
    PyObject_SetAttrString(warnings_module, "defaultaction", action_was);
    changed = true;
  }
  Py_XDECREF(action);
  PyErr_Clear();
  if (changed) {
    /* The filters' cached version (`warnings._filters_mutated`, `_filters_mutated_lock_held` from 3.14). */
    for (const char *name : {"_filters_mutated", "_filters_mutated_lock_held"}) {
      if (PyObject *mutated = PyObject_GetAttrString(warnings_module, name)) {
        Py_XDECREF(PyObject_CallNoArgs(mutated));
        Py_DECREF(mutated);
        break;
      }
      PyErr_Clear();
    }
    found(findings, "the warning filters");
  }
  PyErr_Clear();
}

void signals_restore(PyObject *findings)
{
  if (!dispositions.empty() || wakeup_was) {
    if (on_main_thread()) {
      signals_restore_dispositions(findings);
    }
    else if (!restore_queued) {
      restore_queued = Py_AddPendingCall(restore_later, nullptr) == 0;
    }
  }
  if (timeout_was != nullptr && original_setdefaulttimeout != nullptr) {
    PyObject *now = attribute("_socket", "getdefaulttimeout");
    PyObject *value = now ? PyObject_CallNoArgs(now) : nullptr;
    if (value != nullptr && PyObject_RichCompareBool(value, timeout_was, Py_EQ) != 1) {
      Py_XDECREF(PyObject_CallOneArg(original_setdefaulttimeout, timeout_was));
      found(findings, "the default socket timeout");
    }
    Py_XDECREF(value);
    Py_XDECREF(now);
    Py_CLEAR(timeout_was);
    PyErr_Clear();
  }
  warnings_put_back(findings);
  Py_CLEAR(warnings_module);
  Py_CLEAR(filters_list);
  Py_CLEAR(filters_copy);
  Py_CLEAR(action_was);
}

void signals_thread_restore(PyObject *findings)
{
#ifndef _WIN32
  if (!mask_was) {
    return;
  }
  sigset_t now;
  pthread_sigmask(SIG_BLOCK, nullptr, &now);
  bool differs = false;
  for (int number = 1; number < NSIG; number++) {
    differs = differs || sigismember(&now, number) != sigismember(&*mask_was, number);
  }
  if (differs) {
    pthread_sigmask(SIG_SETMASK, &*mask_was, nullptr);
    found(findings, "the signal mask");
  }
  mask_was.reset();
#else
  (void)findings;
#endif
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Wrappers
 * \{ */

static PyObject *wrapped_set_wakeup_fd(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  const bool confined = confined_now();
  if (confined && region_or_refuse("set_wakeup_fd", false) < 0) {
    return nullptr;
  }
  PyObject *previous = PyObject_Call(original_set_wakeup_fd, args, kwargs);
  if (previous != nullptr && confined && !wakeup_was) {
    wakeup_was = PyLong_AsLong(previous);
    PyErr_Clear();
  }
  return previous;
}

static PyObject *wrapped_setdefaulttimeout(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  if (confined_now()) {
    if (region_or_refuse("setdefaulttimeout", false) < 0) {
      return nullptr;
    }
    if (timeout_was == nullptr) {
      PyObject *getter = attribute("_socket", "getdefaulttimeout");
      timeout_was = getter ? PyObject_CallNoArgs(getter) : nullptr;
      Py_XDECREF(getter);
      PyErr_Clear();
    }
  }
  return PyObject_Call(original_setdefaulttimeout, args, kwargs);
}

static PyObject *wrapped_pthread_sigmask(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
#ifndef _WIN32
  if (confined_now()) {
    if (region_or_refuse("pthread_sigmask", true) < 0) {
      return nullptr;
    }
    if (!mask_was) {
      sigset_t now;
      pthread_sigmask(SIG_BLOCK, nullptr, &now);
      mask_was = now;
    }
  }
#endif
  return PyObject_Call(original_pthread_sigmask, args, kwargs);
}

/** Refused while confined: a signal later, or a flag that can't be read back. */
static const char *const REFUSED_SIGNAL[] = {"siginterrupt", "setitimer", "alarm"};
static PyObject *refused_signal_originals = nullptr; /* dict: name -> original */

static PyObject *wrapped_refused_signal(PyObject *name, PyObject *args, PyObject *kwargs)
{
  if (confined_now()) {
    refuse(PROCESS, PyUnicode_AsUTF8(name));
    return nullptr;
  }
  PyObject *original = PyDict_GetItemWithError(refused_signal_originals, name); /* Borrowed. */
  return original ? PyObject_Call(original, args, kwargs) : nullptr;
}

#define SIGNALS_WRAP(function) (PyCFunction)(void (*)(void))function, METH_VARARGS | METH_KEYWORDS
static PyMethodDef set_wakeup_fd_definition = {"set_wakeup_fd", SIGNALS_WRAP(wrapped_set_wakeup_fd), nullptr};
static PyMethodDef setdefaulttimeout_definition = {
    "setdefaulttimeout", SIGNALS_WRAP(wrapped_setdefaulttimeout), nullptr};
static PyMethodDef pthread_sigmask_definition = {"pthread_sigmask", SIGNALS_WRAP(wrapped_pthread_sigmask), nullptr};
static PyMethodDef refused_signal_definition = {"signal_state", SIGNALS_WRAP(wrapped_refused_signal), nullptr};
#undef SIGNALS_WRAP

static void wrap(const char *module_name, const char *name, PyMethodDef *definition, PyObject **original)
{
  *original = attribute(module_name, name);
  if (*original == nullptr) {
    return;
  }
  if (PyObject *wrapper = PyCFunction_NewEx(definition, nullptr, nullptr)) {
    put_in(module_name, name, wrapper, *original);
    Py_DECREF(wrapper);
  }
  PyErr_Clear();
}

void install_signal_wrappers()
{
  if (refused_signal_originals != nullptr) {
    return;
  }
  refused_signal_originals = PyDict_New();
  PyObject *main = attribute("threading", "main_thread");
  PyObject *thread = main ? PyObject_CallNoArgs(main) : nullptr;
  PyObject *ident = thread ? PyObject_GetAttrString(thread, "ident") : nullptr;
  main_thread = ident ? PyLong_AsUnsignedLong(ident) : 0;
  Py_XDECREF(ident);
  Py_XDECREF(thread);
  Py_XDECREF(main);
  PyErr_Clear();
  original_getsignal = attribute("_signal", "getsignal");
  wrap("_signal", "set_wakeup_fd", &set_wakeup_fd_definition, &original_set_wakeup_fd);
  wrap("_socket", "setdefaulttimeout", &setdefaulttimeout_definition, &original_setdefaulttimeout);
  wrap("_signal", "pthread_sigmask", &pthread_sigmask_definition, &original_pthread_sigmask);
  for (const char *name : REFUSED_SIGNAL) {
    PyObject *original = attribute("_signal", name);
    PyObject *key = PyUnicode_FromString(name);
    PyObject *wrapper = original && key ? PyCFunction_NewEx(&refused_signal_definition, key, nullptr) : nullptr;
    if (wrapper != nullptr) {
      PyDict_SetItem(refused_signal_originals, key, original);
      put_in("_signal", name, wrapper, original);
    }
    Py_XDECREF(wrapper);
    Py_XDECREF(key);
    Py_XDECREF(original);
    PyErr_Clear();
  }
}

/** \} */

}  // namespace blender::angelo_guard
