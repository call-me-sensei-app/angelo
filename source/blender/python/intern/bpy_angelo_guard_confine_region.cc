/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Confined regions, and what a region leaves behind (doc/angelo/script_confinement.rst, "Work that outlives the
 * script").
 *
 * A region is a stretch of one thread that runs confined: `run_confined`, a trampoline (`Deferred`), a confined
 * work item, a confining descriptor's call. While one runs, C records every object its thread allocates: the
 * object allocator is wrapped, so provenance follows allocation, never a file name or a frame the script controls.
 * When the outermost region on a thread ends, what it made and left alive is made safe, still confined:
 *
 * - a class it made: every callable or descriptor in it becomes a confining descriptor (`ConfinedAttr`), so its
 *   dunders, destructor, properties and methods run confined whoever calls them;
 * - a weak reference it made with a callback: the callback becomes the trampoline;
 * - an object it made that holds a callable (a dict, list, set, cell, an instance's attributes or slots, a
 *   finalizer's record): the callable becomes the trampoline;
 * - a callable it made (or a tuple of them) that older objects hold: replaced there by its trampoline, through
 *   one pass over what refers to them (only when such a callable survives).
 *
 * A function or class of installed code the region made (a module it imported defining itself) stays as it is.
 */

#include <Python.h>

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <unordered_map>

#ifdef _WIN32
#  ifndef NOMINMAX
#    define NOMINMAX
#  endif
#  include <windows.h>
#else
#  include <pthread.h>
#endif
#include <utility>
#include <vector>

#include "bpy_angelo_guard_authority.hh"
#include "BKE_global.hh"

#include "bpy_angelo_guard_confine_changes.hh"
#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_journal.hh"
#include "bpy_angelo_guard_confine_paths.hh"
#include "bpy_angelo_guard_confine_record.hh"

namespace blender::angelo_guard {

/** A collection is running (our `gc.callbacks` entry), and the objects older than the regions are frozen. */
static bool collecting = false;
bool regions_frozen = false;
static Main *main_at_start = nullptr;

/* -------------------------------------------------------------------- */
/** \name Collections: never touch the collector's generations while it runs
 *
 * A finalizer (a script object's `__del__`, a weak reference's callback) can run inside a collection and start a
 * region. Freezing, unfreezing or listing objects then would splice the lists the collector is walking. A
 * `gc.callbacks` entry tells C when a collection runs; while one does, a region neither freezes nor scans, and the
 * scan of what it made waits for the next region that ends outside one.
 * \{ */

static PyObject *phase_callback = nullptr;

static PyObject *on_gc_phase(PyObject * /*self*/, PyObject *args)
{
  PyObject *phase = PyTuple_GET_SIZE(args) > 0 ? PyTuple_GET_ITEM(args, 0) : nullptr;
  collecting = phase != nullptr && PyUnicode_Check(phase) && PyUnicode_CompareWithASCIIString(phase, "start") == 0;
  Py_RETURN_NONE;
}

static PyMethodDef phase_definition = {"angelo_guard_gc_phase", on_gc_phase, METH_VARARGS, nullptr};

/** Our entry in `gc.callbacks`, put back if a script removed it. */
static void ensure_phase_callback()
{
  if (phase_callback == nullptr) {
    phase_callback = PyCFunction_New(&phase_definition, nullptr);
  }
  PyObject *module = PyImport_ImportModule("gc");
  PyObject *callbacks = module ? PyObject_GetAttrString(module, "callbacks") : nullptr;
  if (callbacks != nullptr && phase_callback != nullptr && PyList_Check(callbacks) &&
      PySequence_Contains(callbacks, phase_callback) == 0)
  {
    PyList_Append(callbacks, phase_callback);
  }
  Py_XDECREF(callbacks);
  Py_XDECREF(module);
  PyErr_Clear();
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Native stack headroom
 *
 * A script's callable held in a trampoline can call back into itself through C alone (a `__hash__` that is an
 * `lru_cache` keyed on the object: hash -> cache -> hash ...). Each turn adds the trampoline's frames to the C
 * stack, and CPython 3.13 counts recursion, not stack bytes: with the macOS 8 MB main stack the count allowed
 * the stack to run out first (SIGSEGV in test_blender_confinement_escapes, hash_held, 2026-11-15). Every
 * trampoline entry checks the headroom left on this thread's stack and raises `RecursionError` before it is gone.
 * \{ */

/** This thread's stack: its lowest address and how much must stay free (0: unknown, never refused). */
struct StackBounds {
  uintptr_t low = 0, margin = 0;
  bool known = false;
};
static thread_local StackBounds stack_bounds;

static void stack_bounds_find()
{
  stack_bounds.known = true;
  uintptr_t low = 0, size = 0;
#if defined(_WIN32)
  ULONG_PTR limit_low = 0, limit_high = 0;
  GetCurrentThreadStackLimits(&limit_low, &limit_high);
  low = limit_low;
  size = limit_high - limit_low;
#elif defined(__APPLE__)
  const uintptr_t high = uintptr_t(pthread_get_stackaddr_np(pthread_self()));
  size = pthread_get_stacksize_np(pthread_self());
  low = high - size;
#else
  pthread_attr_t attributes;
  if (pthread_getattr_np(pthread_self(), &attributes) == 0) {
    void *address = nullptr;
    size_t bytes = 0;
    if (pthread_attr_getstack(&attributes, &address, &bytes) == 0) {
      low = uintptr_t(address);
      size = bytes;
    }
    pthread_attr_destroy(&attributes);
  }
#endif
  if (low == 0 || size == 0) {
    return;
  }
  const uintptr_t margin = size / 8;
  stack_bounds.low = low;
  stack_bounds.margin = margin < 64 * 1024 ? 64 * 1024 : (margin > 1024 * 1024 ? 1024 * 1024 : margin);
}

int native_stack_check()
{
  if (!stack_bounds.known) {
    stack_bounds_find();
  }
  if (stack_bounds.low == 0) {
    return 0;
  }
  const char here = 0;
  const uintptr_t at = uintptr_t(&here);
  if (at > stack_bounds.low + stack_bounds.margin) {
    return 0;
  }
  PyErr_SetString(PyExc_RecursionError, "maximum recursion depth exceeded in a confined callable (native stack)");
  return -1;
}

/** \} */

RegionMark region_enter()
{
  if (depth == 0) {
    journal_settle_outside(); /* Before the mark: a region keeps only what it does itself. */
  }
  RegionMark mark{journal_mark(), depth == 0};
  if (mark.outermost) {
    seed_reset(); /* Nothing changed by this region yet (bpy_angelo_guard_confine_journal.cc). */
    owned_region_began(); /* What Angelo records while it runs is made since (_owned.cc). */
    import_snapshot();     /* sys.path as found: a name gained is resolved against it at the end. */
    finalize_snapshot();   /* What `weakref.finalize` held: a record added is judged at the end (_leftovers.cc). */
    steering_snapshot();   /* The import system's search state and the exit list (steering.cc). */
    logging_snapshot();    /* The root logger and Angelo's (logging.cc). */
    if (active_regions == 0) {
      process_snapshot(); /* The whole process's state, as the first region on any thread finds it. */
    }
    BPY_angelo_guard_agent_ran(); /* The person's Tab or press before it no longer vouches for focus. */
    if (active_regions == 0) {
      main_at_start = G_MAIN; /* A file loaded while regions run is noticed at the last one's end. */
    }
    if (active_regions++ == 0 && !collecting && !regions_frozen) {
      ensure_phase_callback();
      Py_XDECREF(gc_call("freeze")); /* What exists now is older than anything the region makes. */
      regions_frozen = true;
      PyErr_Clear();
    }
    region_text_snapshot();
    carriers_snapshot();
    writers_region_entered(); /* A writer job that begins now begins inside a region (_writers.cc). */
  }
  depth++;
  return mark;
}

void region_leave(const RegionMark &mark, PyObject *findings, PyObject *script_namespace)
{
  if (!mark.outermost) {
    depth--;
    return;
  }
  /* Still confined (anything that runs while the leftovers are made safe is), but not journaling our own work. */
  PyObject *raised = PyErr_GetRaisedException();
  quiet = true;
  settle_begin();
  const int was_enabled = PyGC_Disable(); /* No collection starts while the young objects are held. */
  /* What the region made, among the young objects (older ones are frozen while regions run): known before the
   * journal is undone (a class the script made keeps its own attributes) and while its leftovers are confined. */
  PyObject *objects = (!collecting && recorded_any()) ? gc_call("get_objects") : nullptr;
  PyErr_Clear();
  if (objects != nullptr) {
    settle_made(objects);
  }
  if (!pending) {
    journal_undo(mark.journal, findings);
  }
  finalize_settle(findings); /* After the undo: the registry the class holds again, if the script rebound it. */
  steering_settle(findings); /* After the undo: `sys.path` and its kin are the lists the region found again. */
  logging_settle(findings);
  carriers_sweep(findings); /* Python it brought into the project, made inert (main thread). */
  changes_settle(); /* What this thread wrote, by identity and content: a rename or copy later is still its. */
  const bool last = active_regions == 1;
  if (objects != nullptr) {
    confine_leftovers(findings, last, script_namespace, objects);
  }
  unsettle_made();
  Py_XDECREF(objects); /* The young objects go now (a `__del__` of the script's may run: still settling). */
  /* What the script's code changed while this region settled (a `__del__` as the undo or the release above let its
   * object go, a key's `__hash__`): journaled late, undone now, until it stops (bounded). */
  for (int pass = 0; pass < 8 && late_changes_take(); pass++) {
    if (!pending) {
      journal_undo(mark.journal, findings);
    }
  }
  /* Lists changed in place by such code are not journaled: settled once more, then let go. */
  steering_settle(findings);
  logging_settle(findings);
  steering_release();
  logging_release();
  settle_end();
  if (was_enabled) {
    PyGC_Enable();
  }
  quiet = false;
  depth--;
  signals_thread_restore(findings); /* This thread's signal mask: no other thread can set it. */
  const bool last_region = --active_regions == 0;
  writers_region_left();
  if (last_region) {
    process_restore(findings); /* After the collector is back as the script left it: put back as it was. */
    assembled_release();       /* `sys.modules` is settled: the modules the script registered can go. */
  }
  if (last_region && !collecting) {
    if (regions_frozen) {
      Py_XDECREF(gc_call("unfreeze"));
      regions_frozen = false;
      /* A file loaded during the regions (an agent turn opening a project): what the old file's Python held was
       * frozen with everything older, and unfrozen it lands in the oldest generation, which the collector seldom
       * visits. Collected now, so the old project's memory is given back. */
      if (G_MAIN != main_at_start) {
        Py_XDECREF(gc_call("collect"));
      }
    }
    recording_reset();
    script_types_reset();
  }
  PyErr_Clear();
  PyErr_SetRaisedException(raised);
}

int region_types_ready()
{
  return attr_types_ready();
}

/** \} */

}  // namespace blender::angelo_guard
