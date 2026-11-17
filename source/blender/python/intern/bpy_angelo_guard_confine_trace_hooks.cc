/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The tracer and profiler every new thread installs (doc/angelo/script_confinement.rst, "Tracers").
 *
 * `threading.settrace(f)` only stores `f` in `threading._trace_hook` (and `setprofile` in `_profile_hook`); each
 * thread `threading` starts later calls `sys.settrace(_trace_hook)` from `Thread._bootstrap_inner`, in that new
 * thread, which is not confined. A script that stored a hook there would see the calls, returns and exceptions
 * (tokens among them) of every thread Angelo starts, for as long as the thread lives. `sys.settrace` itself is
 * refused to confined code (hook.cc); a module's namespace can be changed with no audit event, so here:
 *
 * - C keeps the value Angelo's own code (never confined code) last gave each of the two names: the journal's dict
 *   watcher tells it of every change to `threading`'s namespace before it happens.
 * - Every thread start, confined or not, puts that value back first, so a thread Angelo starts runs its own tracer.
 * - The thread's own `sys.settrace` / `sys.setprofile` from `_bootstrap_inner` is refused when the name holds any
 *   other value: a script that stored one between a start and the new thread's first step gains nothing.
 * - A region's end puts the names back too (the journal undoes the script's rebinding of them).
 */

#include <Python.h>

#include <cstring>

#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_journal.hh"

namespace blender::angelo_guard {

namespace {

const char *const NAMES[2] = {"_trace_hook", "_profile_hook"};
PyObject *threading_names = nullptr; /* Strong: the namespace `Thread._bootstrap_inner` reads them from. */
PyObject *trusted[2] = {nullptr, nullptr}; /* Strong: Angelo's own values (None when it set none). */

int which(PyObject *key)
{
  if (key == nullptr || !PyUnicode_Check(key)) {
    return -1;
  }
  const Py_ssize_t length = PyUnicode_GET_LENGTH(key);
  for (int i = 0; i < 2; i++) {
    if (length == Py_ssize_t(strlen(NAMES[i])) && PyUnicode_CompareWithASCIIString(key, NAMES[i]) == 0) {
      return i;
    }
  }
  return -1;
}

}  // namespace

void trace_hooks_know()
{
  callbacks_know(); /* The other callbacks other threads' data reaches (callbacks.cc). */
  if (threading_names != nullptr) {
    return;
  }
  PyObject *modules = journal_modules();
  PyObject *threading = modules != nullptr ? PyDict_GetItemString(modules, "threading") : nullptr; /* Borrowed. */
  if (threading == nullptr || !PyModule_CheckExact(threading)) {
    PyErr_Clear();
    return; /* Not loaded yet: the next region's start looks again. */
  }
  threading_names = Py_NewRef(PyModule_GetDict(threading));
  for (int i = 0; i < 2; i++) {
    PyObject *value = PyDict_GetItemString(threading_names, NAMES[i]);
    trusted[i] = Py_NewRef(value != nullptr ? value : Py_None);
  }
  PyErr_Clear();
}

void trace_hooks_changed(PyObject *dict, PyObject *key, PyObject *new_value)
{
  callbacks_changed(dict, key, new_value);
  if (threading_names == nullptr || dict != threading_names) {
    return;
  }
  const int i = which(key);
  if (i < 0 || confined_now()) {
    return; /* Confined code's value is never Angelo's (and the journal undoes it). */
  }
  Py_SETREF(trusted[i], Py_NewRef(new_value != nullptr ? new_value : Py_None));
}

bool trace_hooks_put_back()
{
  if (threading_names == nullptr || Py_IsFinalizing()) {
    return false;
  }
  bool changed = false;
  for (int i = 0; i < 2; i++) {
    PyObject *now = PyDict_GetItemString(threading_names, NAMES[i]);
    if (now != trusted[i] && !(now == nullptr && trusted[i] == Py_None)) {
      PyDict_SetItemString(threading_names, NAMES[i], trusted[i]);
      changed = true;
    }
  }
  PyErr_Clear();
  return changed;
}

int trace_hook_refused(const char *event)
{
  const int i = strcmp(event, "sys.settrace") == 0 ? 0 : (strcmp(event, "sys.setprofile") == 0 ? 1 : -1);
  if (i < 0 || threading_names == nullptr) {
    return 0;
  }
  PyObject *now = PyDict_GetItemString(threading_names, NAMES[i]);
  PyErr_Clear();
  if (now == nullptr || now == trusted[i] || now == Py_None) {
    return 0;
  }
  /* The name holds a value Angelo never gave it: refused where `threading` installs it in a new thread. */
  PyFrameObject *frame = PyEval_GetFrame(); /* Borrowed. */
  if (frame == nullptr) {
    return 0;
  }
  PyCodeObject *code = PyFrame_GetCode(frame);
  PyObject *globals = PyFrame_GetGlobals(frame);
  const bool bootstrap = globals == threading_names && code != nullptr &&
                         PyUnicode_CompareWithASCIIString(code->co_name, "_bootstrap_inner") == 0;
  Py_XDECREF(code);
  Py_XDECREF(globals);
  PyErr_Clear();
  if (!bootstrap) {
    return 0;
  }
  trace_hooks_put_back();
  return refuse(GUARD, i == 0 ? "a tracer a script gave threading" : "a profiler a script gave threading");
}

}  // namespace blender::angelo_guard
