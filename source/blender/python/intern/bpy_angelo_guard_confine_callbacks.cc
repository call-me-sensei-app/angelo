/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Callbacks Python or a library calls with another thread's data (doc/angelo/script_confinement.rst,
 * "Callbacks with other threads' data"): `sys.excepthook` and `sys.unraisablehook` (an exception, its traceback and the object it came from),
 * `threading.excepthook` (a thread's uncaught exception) and an asyncio event loop's task factory and exception
 * handler (every coroutine the loop runs, its result, and every error it reports). A script that stored its own
 * callable there, even one that runs no code of its own (`list.append` on its list), would be handed what Angelo's
 * other threads do while the script runs, and for a loop, afterwards too: a loop's attributes are no module's names,
 * so no journal puts them back. As for the tracers (trace_hooks.cc):
 *
 * - C keeps the value Angelo's own code (never confined code) last gave each slot: for the module names, from the
 *   journal's dict watcher; for a loop, in the C descriptor each of its two names becomes on `BaseEventLoop`.
 * - Code that isn't confined never calls any other value: where the interpreter calls a hook it raises an audit
 *   event first (`sys.excepthook`, `sys.unraisablehook`), refused there after Angelo's own value has run; for a
 *   thread's exception, each thread's `_invoke_excepthook` is a C function that calls Angelo's value (or Python's
 *   default) itself; a loop's own methods read Angelo's value from the descriptor. The module names are put back.
 * - Confined code keeps its own: a script's own thread, loop or error reaches the hook it set.
 */

#include <Python.h>

#include <cstring>

#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_journal.hh"

namespace blender::angelo_guard {

namespace {

enum Slot { SYS_EXCEPTHOOK, SYS_UNRAISABLEHOOK, THREADING_EXCEPTHOOK, SLOTS };
const char *const SLOT_NAMES[SLOTS] = {"excepthook", "unraisablehook", "excepthook"};

PyObject *sys_names = nullptr;       /* Strong: `sys`'s namespace, where the interpreter reads its hooks. */
PyObject *threading_names = nullptr; /* Strong: `threading`'s, where each thread's exception reads its hook. */
PyObject *trusted[SLOTS] = {nullptr, nullptr, nullptr}; /* Strong: Angelo's values. */
PyObject *hook_args_type = nullptr;                      /* Strong: `_thread._ExceptHookArgs`. */
PyObject *default_thread_hook = nullptr;                 /* Strong: `_thread._excepthook`, Python's own. */
PyObject *thread_type = nullptr;                         /* Strong: `threading.Thread`. */

PyObject *names_of(const char *module)
{
  PyObject *modules = journal_modules();
  PyObject *found = modules != nullptr ? PyDict_GetItemString(modules, module) : nullptr; /* Borrowed. */
  PyErr_Clear();
  return found != nullptr && PyModule_CheckExact(found) ? PyModule_GetDict(found) : nullptr;
}

PyObject *current(Slot slot)
{
  PyObject *names = slot == THREADING_EXCEPTHOOK ? threading_names : sys_names;
  if (names == nullptr) {
    return nullptr;
  }
  PyObject *value = PyDict_GetItemString(names, SLOT_NAMES[slot]); /* Borrowed. */
  PyErr_Clear();
  return value;
}

bool holds_trusted(Slot slot)
{
  PyObject *value = current(slot);
  return trusted[slot] == nullptr || value == trusted[slot] || (value == nullptr && trusted[slot] == Py_None);
}

void put_back(Slot slot)
{
  if (trusted[slot] != nullptr && !holds_trusted(slot)) {
    PyDict_SetItemString(slot == THREADING_EXCEPTHOOK ? threading_names : sys_names, SLOT_NAMES[slot], trusted[slot]);
    PyErr_Clear();
  }
}

/* -------------------------------------------------------------------- */
/** \name A thread's uncaught exception
 * \{ */

const char *const ORIGINAL = "angelo_guard.invoke_excepthook";

void release_original(PyObject *capsule)
{
  Py_XDECREF((PyObject *)PyCapsule_GetPointer(capsule, ORIGINAL));
}

/** A thread's `_invoke_excepthook`: `threading`'s own closure (kept in a capsule, never readable) when the thread is
 * confined; otherwise Angelo's value is put back and called here, falling back to Python's default, so a script's
 * value never sees the exception. */
PyObject *invoke_excepthook(PyObject *capsule, PyObject *thread)
{
  PyObject *original = (PyObject *)PyCapsule_GetPointer(capsule, ORIGINAL);
  if (original == nullptr) {
    return nullptr;
  }
  if (confined_now() || hook_args_type == nullptr || default_thread_hook == nullptr) {
    return PyObject_CallOneArg(original, thread);
  }
  /* Never through the closure here, even when the name holds Angelo's value: it reads the name again, and a script
   * could change it in between. */
  put_back(THREADING_EXCEPTHOOK);
  PyObject *hook = trusted[THREADING_EXCEPTHOOK] != Py_None ? trusted[THREADING_EXCEPTHOOK] : default_thread_hook;
  PyObject *error = PyErr_GetHandledException(); /* New reference: what `_bootstrap_inner` caught. */
  PyObject *kind = error != Py_None ? (PyObject *)Py_TYPE(error) : Py_None;
  PyObject *traceback = error != Py_None ? PyException_GetTraceback(error) : nullptr;
  PyObject *fields = Py_BuildValue("(OOOO)", kind, error, traceback ? traceback : Py_None, thread);
  PyObject *args = fields ? PyObject_CallOneArg(hook_args_type, fields) : nullptr;
  PyObject *result = args ? PyObject_CallOneArg(hook, args) : nullptr;
  if (result == nullptr && args != nullptr && hook != default_thread_hook) {
    PyErr_Clear();
    result = PyObject_CallOneArg(default_thread_hook, args);
  }
  if (result == nullptr) {
    PyErr_WriteUnraisable(hook);
  }
  Py_XDECREF(result);
  Py_XDECREF(args);
  Py_XDECREF(fields);
  Py_XDECREF(traceback);
  Py_XDECREF(error);
  Py_RETURN_NONE;
}

PyMethodDef invoke_definition = {"_invoke_excepthook", (PyCFunction)invoke_excepthook, METH_O, nullptr};

bool wrapped(PyObject *value)
{
  return PyCFunction_Check(value) && PyCFunction_GET_FUNCTION(value) == (PyCFunction)invoke_excepthook;
}

void wrap_thread(PyObject *thread)
{
  if (thread_type == nullptr || !PyType_Check(thread_type) || !PyObject_TypeCheck(thread, (PyTypeObject *)thread_type))
  {
    return;
  }
  PyObject *name = PyUnicode_InternFromString("_invoke_excepthook");
  PyObject *original = name ? PyObject_GenericGetAttr(thread, name) : nullptr;
  if (original != nullptr && !wrapped(original)) {
    PyObject *capsule = PyCapsule_New(original, ORIGINAL, release_original); /* Takes `original`. */
    PyObject *wrapper = capsule ? PyCFunction_New(&invoke_definition, capsule) : nullptr;
    if (capsule == nullptr) {
      Py_DECREF(original);
    }
    if (wrapper != nullptr) {
      PyObject_GenericSetAttr(thread, name, wrapper);
    }
    Py_XDECREF(wrapper);
    Py_XDECREF(capsule);
  }
  else {
    Py_XDECREF(original);
  }
  Py_XDECREF(name);
  PyErr_Clear();
}

/** Every thread `threading` knows (started, or starting): its exception goes through C from now on. */
void wrap_live_threads()
{
  if (threading_names == nullptr) {
    return;
  }
  for (const char *table : {"_active", "_limbo"}) {
    PyObject *threads = PyDict_GetItemString(threading_names, table); /* Borrowed. */
    PyObject *values = threads != nullptr && PyDict_Check(threads) ? PyDict_Values(threads) : nullptr;
    for (Py_ssize_t i = 0; values != nullptr && i < PyList_GET_SIZE(values); i++) {
      wrap_thread(PyList_GET_ITEM(values, i));
    }
    Py_XDECREF(values);
  }
  PyErr_Clear();
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name An asyncio event loop's task factory and exception handler
 *
 * `BaseEventLoop` (every selector and proactor loop) reads both from its own attributes (`self._task_factory` in
 * `create_task`, `self._exception_handler` in `call_exception_handler`). Once `asyncio` is loaded, each name on the
 * class is a C data descriptor, which an instance's own namespace never shadows (`object.__setattr__` and
 * `loop.__dict__` included): it keeps two values per loop, Angelo's (the last one code that isn't confined set) and
 * the latest (whoever set it). Confined code reads the latest; everything else reads Angelo's, so asyncio's own
 * methods run unchanged, with no window between a check and the read.
 * \{ */

const char *const LOOP_ATTRIBUTES[2] = {"_task_factory", "_exception_handler"};
PyObject *loop_values = nullptr; /* weakref(loop) -> [Angelo's 0, latest 0, Angelo's 1, latest 1]. */

struct LoopSlot {
  PyObject_HEAD
  int which;
};

PyObject *forget_loop(PyObject * /*self*/, PyObject *reference)
{
  if (loop_values != nullptr && PyDict_DelItem(loop_values, reference) != 0) {
    PyErr_Clear();
  }
  Py_RETURN_NONE;
}

PyMethodDef forget_definition = {"forget_loop", (PyCFunction)forget_loop, METH_O, nullptr};

/** The loop's four values (borrowed), made the first time: from what its namespace held before the descriptor was
 * installed (a loop made earlier, outside any script), else None. Null when the loop can't be weakly referenced. */
PyObject *loop_entry(PyObject *loop)
{
  if (loop_values == nullptr && (loop_values = PyDict_New()) == nullptr) {
    return nullptr;
  }
  PyObject *probe = PyWeakref_NewRef(loop, nullptr);
  if (probe == nullptr) {
    return nullptr;
  }
  PyObject *entry = PyDict_GetItemWithError(loop_values, probe); /* Borrowed. */
  Py_DECREF(probe);
  if (entry != nullptr || PyErr_Occurred()) {
    return entry;
  }
  PyObject *names = PyObject_GenericGetDict(loop, nullptr);
  PyErr_Clear();
  PyObject *before[2];
  for (int i = 0; i < 2; i++) {
    before[i] = names != nullptr ? PyDict_GetItemString(names, LOOP_ATTRIBUTES[i]) : nullptr; /* Borrowed. */
    before[i] = before[i] != nullptr ? before[i] : Py_None;
  }
  entry = Py_BuildValue("[OOOO]", before[0], before[0], before[1], before[1]);
  Py_XDECREF(names);
  PyObject *forget = entry ? PyCFunction_New(&forget_definition, nullptr) : nullptr;
  PyObject *key = forget ? PyWeakref_NewRef(loop, forget) : nullptr; /* Removed with the loop. */
  const bool stored = key != nullptr && PyDict_SetItem(loop_values, key, entry) == 0;
  Py_XDECREF(key);
  Py_XDECREF(forget);
  Py_XDECREF(entry);
  return stored ? entry : nullptr; /* Held by the dict now. */
}

PyObject *loop_slot_get(PyObject *self, PyObject *loop, PyObject * /*type*/)
{
  if (loop == nullptr || loop == Py_None) {
    return Py_NewRef(self); /* Read on the class. */
  }
  const int which = ((LoopSlot *)self)->which;
  PyObject *entry = loop_entry(loop);
  if (entry == nullptr) {
    PyErr_Clear();
    return Py_NewRef(Py_None); /* A loop C can't keep apart: its default. */
  }
  return Py_NewRef(PyList_GET_ITEM(entry, 2 * which + (confined_now() ? 1 : 0)));
}

int loop_slot_set(PyObject *self, PyObject *loop, PyObject *value)
{
  const int which = ((LoopSlot *)self)->which;
  PyObject *entry = loop_entry(loop);
  if (entry == nullptr) {
    if (!PyErr_Occurred()) {
      PyErr_SetString(PyExc_TypeError, "an event loop that can't be weakly referenced");
    }
    return -1;
  }
  value = value != nullptr ? value : Py_None; /* `del`: back to the default. */
  PyList_SetItem(entry, 2 * which + 1, Py_NewRef(value));
  if (!confined_now()) {
    PyList_SetItem(entry, 2 * which, Py_NewRef(value)); /* Angelo's own choice. */
  }
  return 0;
}

PyType_Slot loop_slot_slots[] = {
    {Py_tp_descr_get, (void *)loop_slot_get},
    {Py_tp_descr_set, (void *)loop_slot_set},
    {0, nullptr},
};

PyType_Spec loop_slot_spec = {
    "_angelo_guard.LoopSlot",
    sizeof(LoopSlot),
    0,
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE | Py_TPFLAGS_DISALLOW_INSTANTIATION,
    loop_slot_slots,
};

bool loop_slots_installed = false;

/** Once `asyncio.base_events` is loaded, outside any script: `BaseEventLoop`'s two slots become C's. */
void install_loop_slots()
{
  if (loop_slots_installed) {
    return;
  }
  PyObject *events = names_of("asyncio.base_events");
  PyObject *loop_type = events ? PyDict_GetItemString(events, "BaseEventLoop") : nullptr; /* Borrowed. */
  if (loop_type == nullptr || !PyType_Check(loop_type) || confined_now()) {
    PyErr_Clear();
    return;
  }
  loop_slots_installed = true; /* Once, whatever follows. */
  PyObject *slot_type = PyType_FromSpec(&loop_slot_spec);
  PyObject *names = ((PyTypeObject *)loop_type)->tp_dict;
  for (int i = 0; slot_type != nullptr && i < 2; i++) {
    LoopSlot *slot = PyObject_New(LoopSlot, (PyTypeObject *)slot_type);
    if (slot == nullptr) {
      break;
    }
    slot->which = i;
    PyDict_SetItemString(names, LOOP_ATTRIBUTES[i], (PyObject *)slot); /* Changed here, in C: no journal sees it. */
    Py_DECREF(slot);
  }
  Py_XDECREF(slot_type);
  PyType_Modified((PyTypeObject *)loop_type);
  PyErr_Clear();
}

/** \} */

}  // namespace

void callbacks_know()
{
  if (sys_names == nullptr && !confined_now()) {
    PyObject *names = names_of("sys");
    if (names != nullptr) {
      sys_names = Py_NewRef(names);
      for (Slot slot : {SYS_EXCEPTHOOK, SYS_UNRAISABLEHOOK}) {
        PyObject *value = current(slot);
        trusted[slot] = Py_NewRef(value != nullptr ? value : Py_None);
      }
    }
  }
  if (threading_names == nullptr && !confined_now()) {
    PyObject *names = names_of("threading");
    PyObject *thread = names_of("_thread");
    PyObject *args_type = thread ? PyDict_GetItemString(thread, "_ExceptHookArgs") : nullptr; /* Borrowed. */
    PyObject *default_hook = thread ? PyDict_GetItemString(thread, "_excepthook") : nullptr;
    if (names != nullptr && args_type != nullptr && default_hook != nullptr) {
      threading_names = Py_NewRef(names);
      thread_type = Py_XNewRef(PyDict_GetItemString(names, "Thread"));
      hook_args_type = Py_NewRef(args_type);
      default_thread_hook = Py_NewRef(default_hook);
      PyObject *value = current(THREADING_EXCEPTHOOK);
      trusted[THREADING_EXCEPTHOOK] = Py_NewRef(value != nullptr ? value : Py_None);
    }
    PyErr_Clear();
  }
  install_loop_slots();
}

void callbacks_changed(PyObject *dict, PyObject *key, PyObject *new_value)
{
  if (dict == nullptr || key == nullptr || !PyUnicode_Check(key) || (dict != sys_names && dict != threading_names))
  {
    return;
  }
  for (Slot slot : {SYS_EXCEPTHOOK, SYS_UNRAISABLEHOOK, THREADING_EXCEPTHOOK}) {
    if ((slot == THREADING_EXCEPTHOOK) != (dict == threading_names) ||
        PyUnicode_CompareWithASCIIString(key, SLOT_NAMES[slot]) != 0)
    {
      continue;
    }
    if (!confined_now()) {
      Py_SETREF(trusted[slot], Py_NewRef(new_value != nullptr ? new_value : Py_None));
    }
    else if (slot == THREADING_EXCEPTHOOK) {
      /* A script's value: every thread that already exists reads it from now on, so each is given C's. */
      PyObject *raised = PyErr_GetRaisedException();
      wrap_live_threads();
      PyErr_SetRaisedException(raised);
    }
  }
}

void callbacks_thread_starting(PyObject *args)
{
  if (threading_names == nullptr || holds_trusted(THREADING_EXCEPTHOOK) || PyTuple_GET_SIZE(args) < 1) {
    return;
  }
  /* `threading`'s start passes the thread's bound `_bootstrap`: its thread is wrapped before it runs. */
  PyObject *function = PyTuple_GET_ITEM(args, 0);
  if (PyMethod_Check(function)) {
    wrap_thread(PyMethod_GET_SELF(function));
  }
}

int callback_refused(const char *event, PyObject *args)
{
  if (confined_now() || PyTuple_GET_SIZE(args) < 2) {
    return 0; /* A script's own error reaches the hook it set. */
  }
  const bool unraisable = strcmp(event, "sys.unraisablehook") == 0;
  const Slot slot = unraisable ? SYS_UNRAISABLEHOOK : SYS_EXCEPTHOOK;
  PyObject *hook = PyTuple_GET_ITEM(args, 0);
  if (trusted[slot] == nullptr || hook == trusted[slot] || hook == Py_None) {
    return 0;
  }
  /* A value Angelo never gave `sys`: Angelo's runs instead (or Python's own), then the interpreter is stopped
   * before it calls the script's. */
  put_back(slot);
  PyObject *own = trusted[slot] != Py_None ? trusted[slot] :
                                             PySys_GetObject(unraisable ? "__unraisablehook__" : "__excepthook__");
  if (own != nullptr) {
    PyObject *result = nullptr;
    if (unraisable) {
      result = PyObject_CallOneArg(own, PyTuple_GET_ITEM(args, 1));
    }
    else if (PyTuple_GET_SIZE(args) >= 4) {
      result = PyObject_CallFunctionObjArgs(
          own, PyTuple_GET_ITEM(args, 1), PyTuple_GET_ITEM(args, 2), PyTuple_GET_ITEM(args, 3), nullptr);
    }
    Py_XDECREF(result);
    PyErr_Clear();
  }
  if (unraisable) {
    return refuse(GUARD, "a hook a script gave sys.unraisablehook");
  }
  /* `PyErr_Print` skips the hook quietly only for a RuntimeError. */
  PyErr_SetString(PyExc_RuntimeError, "a hook a script gave sys.excepthook (Angelo's own reported the error)");
  return -1;
}

}  // namespace blender::angelo_guard
