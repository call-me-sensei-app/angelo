/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Work a script left suspended runs as a region when it resumes (doc/angelo/script_confinement.rst, "Work that
 * outlives the script"). A callable a region leaves is wrapped (the trampoline), but a generator, coroutine or
 * async generator is resumed, not called: whatever holds it (Angelo's own code, an event loop, a later script) can
 * run its next step after the region ended. So each one of the script's code a region made and left alive is noted
 * when the region ends
 * (`resume_note`, from bpy_angelo_guard_confine_leftovers.cc), and a step of it that starts outside any region runs
 * inside one: entered when the step starts (`PY_START`, `PY_RESUME`, `PY_THROW`) and left when it yields, returns or
 * unwinds, with everything a region's end does (the journal, the leftovers pass, the process's state, the project's
 * Python, `sys.modules`), on whatever thread runs it. Its own leftovers are noted at that end in turn.
 *
 * The events come from `sys.monitoring` under a tool id of the confinement's own, through the original functions (a
 * script's calls are refused while confined: bpy_angelo_guard_confine_wrap_io.cc). With no free tool id, such work
 * is closed at the region's end instead (fail closed). The start and yield/return events are set locally, only on the code objects of noted
 * generators; the throw and unwind events can only be set for every code object, and return at once unless this
 * thread entered a step.
 */

#include <Python.h>

#include <vector>

#include "bpy_angelo_guard_confine_intern.hh"

namespace blender::angelo_guard {

static PyObject *noted = nullptr; /* id(generator) -> weak reference: the suspended work regions left. */
static PyObject *set_local_events = nullptr;
static long tool = -1;
static long local_events = 0;

struct Step {
  PyFrameObject *frame; /* Compared, never dereferenced. */
  RegionMark mark;
};
static thread_local std::vector<Step> steps;


static bool is_noted(PyFrameObject *frame)
{
  if (noted == nullptr || PyDict_GET_SIZE(noted) == 0) {
    return false;
  }
  PyObject *generator = PyFrame_GetGenerator(frame); /* New reference, or null. */
  if (generator == nullptr) {
    return false;
  }
  PyObject *key = PyLong_FromVoidPtr(generator);
  const bool found = key != nullptr && PyDict_Contains(noted, key) == 1;
  Py_XDECREF(key);
  Py_DECREF(generator);
  PyErr_Clear();
  return found;
}

/** A step starts (`PY_START`, `PY_RESUME`, `PY_THROW`): outside a region, a noted generator's runs inside one. */
static PyObject *step_starts(PyObject * /*self*/, PyObject *const * /*args*/, Py_ssize_t /*nargs*/)
{
  if (depth == 0 && !pending) {
    PyFrameObject *frame = PyEval_GetFrame(); /* Borrowed. */
    if (frame != nullptr && is_noted(frame)) {
      steps.push_back({frame, region_enter()});
    }
  }
  Py_RETURN_NONE;
}

/** A step ends (`PY_YIELD`, `PY_RETURN`, `PY_UNWIND`): the region its start entered ends with it. */
static PyObject *step_ends(PyObject * /*self*/, PyObject *const * /*args*/, Py_ssize_t /*nargs*/)
{
  if (!steps.empty() && steps.back().frame == PyEval_GetFrame()) {
    const RegionMark mark = steps.back().mark;
    steps.pop_back();
    PyObject *raised = PyErr_GetRaisedException();
    region_leave(mark, nullptr);
    PyErr_SetRaisedException(raised);
  }
  Py_RETURN_NONE;
}

static PyMethodDef starts_definition = {
    "angelo_step_starts", (PyCFunction)(void (*)(void))step_starts, METH_FASTCALL, nullptr};
static PyMethodDef ends_definition = {
    "angelo_step_ends", (PyCFunction)(void (*)(void))step_ends, METH_FASTCALL, nullptr};

static long event(PyObject *events, const char *name)
{
  PyObject *value = PyObject_GetAttrString(events, name);
  const long number = value ? PyLong_AsLong(value) : 0;
  Py_XDECREF(value);
  PyErr_Clear();
  return number;
}

void resume_install()
{
  if (tool >= 0) {
    return;
  }
  PyObject *monitoring = PySys_GetObject("monitoring"); /* Borrowed. */
  PyObject *events = monitoring ? PyObject_GetAttrString(monitoring, "events") : nullptr;
  if (events == nullptr) {
    PyErr_Clear();
    return;
  }
  long chosen = -1;
  for (const long id : {4L, 3L}) { /* Not the debugger's, coverage's, profiler's or optimizer's. */
    PyObject *done = PyObject_CallFunction(monitoring_original("use_tool_id"), "ls", id, "Angelo confinement");
    if (done != nullptr) {
      Py_DECREF(done);
      chosen = id;
      break;
    }
    PyErr_Clear();
  }
  if (chosen < 0) {
    Py_DECREF(events);
    return; /* Every free id taken (a debugger and a profiler at once): doc/angelo/script_confinement.rst. */
  }
  PyObject *starts = PyCFunction_New(&starts_definition, nullptr);
  PyObject *ends = PyCFunction_New(&ends_definition, nullptr);
  bool ok = starts != nullptr && ends != nullptr;
  for (const char *name : {"PY_START", "PY_RESUME", "PY_THROW"}) {
    PyObject *previous = ok ? PyObject_CallFunction(monitoring_original("register_callback"), "llO", chosen,
                                                    event(events, name), starts) :
                              nullptr;
    ok = ok && previous != nullptr;
    Py_XDECREF(previous);
  }
  for (const char *name : {"PY_YIELD", "PY_RETURN", "PY_UNWIND"}) {
    PyObject *previous = ok ? PyObject_CallFunction(monitoring_original("register_callback"), "llO", chosen,
                                                    event(events, name), ends) :
                              nullptr;
    ok = ok && previous != nullptr;
    Py_XDECREF(previous);
  }
  /* Throw and unwind can only be watched for every code object; the rest is set per noted generator's code. */
  PyObject *global = ok ? PyObject_CallFunction(monitoring_original("set_events"), "ll", chosen,
                                                event(events, "PY_THROW") | event(events, "PY_UNWIND")) :
                          nullptr;
  ok = ok && global != nullptr;
  Py_XDECREF(global);
  local_events = event(events, "PY_START") | event(events, "PY_RESUME") | event(events, "PY_YIELD") |
                 event(events, "PY_RETURN");
  set_local_events = ok ? Py_XNewRef(monitoring_original("set_local_events")) : nullptr;
  noted = ok ? PyDict_New() : nullptr;
  Py_XDECREF(starts);
  Py_XDECREF(ends);
  Py_DECREF(events);
  PyErr_Clear();
  tool = (ok && set_local_events != nullptr && noted != nullptr) ? chosen : -1;
}

bool resume_ready()
{
  return tool >= 0;
}

static PyObject *code_of(PyObject *object)
{
  for (const char *name : {"gi_code", "cr_code", "ag_code"}) {
    if (PyObject *code = PyObject_GetAttrString(object, name)) {
      if (PyCode_Check(code)) {
        return code;
      }
      Py_DECREF(code);
    }
    PyErr_Clear();
  }
  return nullptr;
}

bool resume_note(PyObject *object)
{
  if (!(PyGen_Check(object) || PyCoro_CheckExact(object) || PyAsyncGen_CheckExact(object))) {
    return false;
  }
  PyObject *code = code_of(object);
  if (code == nullptr || !agent_code((PyCodeObject *)code)) {
    Py_XDECREF(code);
    return false; /* An installed generator (os.walk) runs installed code: not the script's work. */
  }
  if (tool < 0 || noted == nullptr) {
    /* No tool to make its later steps regions (every free id taken): it can't run again (fail closed). */
    Py_DECREF(code);
    Py_XDECREF(PyObject_CallMethod(object, "close", nullptr));
    PyErr_Clear();
    return false;
  }
  PyObject *key = PyLong_FromVoidPtr(object);
  static PyMethodDef forget = {"forget",
                               [](PyObject *dead_key, PyObject * /*reference*/) -> PyObject * {
                                 if (noted != nullptr) {
                                   PyDict_DelItem(noted, dead_key);
                                 }
                                 PyErr_Clear();
                                 Py_RETURN_NONE;
                               },
                               METH_O,
                               nullptr};
  PyObject *callback = key ? PyCFunction_New(&forget, key) : nullptr;
  PyObject *reference = callback ? PyWeakref_NewRef(object, callback) : nullptr;
  bool done = false;
  if (reference != nullptr && PyDict_SetItem(noted, key, reference) == 0) {
    PyObject *set = PyObject_CallFunction(set_local_events, "lOl", tool, code, local_events);
    done = set != nullptr;
    Py_XDECREF(set);
  }
  Py_XDECREF(reference);
  Py_XDECREF(callback);
  Py_XDECREF(key);
  Py_XDECREF(code);
  PyErr_Clear();
  return done;
}

}  // namespace blender::angelo_guard
