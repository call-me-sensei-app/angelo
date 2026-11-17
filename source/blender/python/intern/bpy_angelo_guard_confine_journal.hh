/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * The namespace journal's entries, shared by its files (bpy_angelo_guard_confine_journal.cc, _planted.cc and
 * bpy_angelo_guard_confine_types.cc). Thread-local: each confined thread journals and undoes its own changes.
 */

#include <Python.h>

#include <vector>

#include "bpy_angelo_guard_confine_intern.hh"

namespace blender::angelo_guard {

struct JournalEntry {
  PyObject *dict; /* The namespace (a dict), or a class (`type_entry`). */
  PyObject *key;  /* null: the whole dict (cleared or cloned); `old` is then its copy. */
  PyObject *old;  /* null: the key was absent. */
  bool definition; /* Bound by the module's own top-level installed code while it was imported: kept. */
  bool type_entry; /* A class's attribute (bpy_angelo_guard_confine_types.cc). */
  bool tool = false; /* Angelo's own code changing its own package with no script frame running: kept, unless the
                      * value is one the script made. */
};

std::vector<JournalEntry> &journal_entries();
/** Whether this thread journals now: confined, and not the confinement's own work. */
bool journaling();
void journal_record(PyObject *owner, PyObject *key, PyObject *old, bool definition, bool type_entry,
                    bool tool = false);
/** Whether to put `key` back to `old` (the script's change), given what is there now; `registry`: a module-level
 * dict of data (a removed entry comes back only if it held code); `exact`: a module's namespace, data included (the
 * journal's own undo; never for a copy elsewhere or a Blender RNA type, where only code is judged). */
bool undo_wanted(PyObject *key, PyObject *old, PyObject *now, bool registry = false, bool exact = false,
                 PyObject *dict = nullptr);
/** "owner: name" for a finding (at most 20 named; `noted` counts). */
void note_finding(PyObject *findings, PyObject *owner_name, PyObject *key, int *noted);
void note(PyObject *findings, PyObject *dict, PyObject *key, int *noted);
void replace_planted(size_t mark, PyObject *findings, int *changed, int *noted);
/** Put back one class attribute entry (bpy_angelo_guard_confine_types.cc); true when it changed something. */
bool undo_type_entry(const JournalEntry &entry, PyObject *findings, int *noted);
/** Whether the script made a class: its own code made it (`made_by_script`), or a class statement whose body is its
 * code did (through a metaclass written in Python: `ABCMeta`, `EnumType`). Anything else (an installed import,
 * `namedtuple`, `enum`'s functional API, pybind11) is installed code's class (bpy_angelo_guard_confine_types.cc). */
bool script_type(PyTypeObject *type);
void script_types_reset();
/** `builtins.__build_class__` is running a class body that is the script's code (bpy_angelo_guard_confine_wrap_io.cc). */
extern thread_local int script_class_statement;
/** Whether a module is the agent's own: from a file outside the installed folders, or made by a script with no file
 * (bpy_angelo_guard_confine_modules.cc). */
bool agent_module(PyObject *module);
/** Whether `module` was put into `sys.modules` by anything but the import system loading installed parts (a module
 * the script registered itself, or one its own loader made): such an entry never outlives the region. */
bool module_assembled(PyObject *module);
/** Let go of those once no region is running. */
void assembled_release();
/** Whether `name` resolves, on the file system as the installed finders search it and without running any Python,
 * to what the import system loaded for `module`: never an installed file under a name the script chose. */
bool origin_honest(PyObject *name, PyObject *module);
/** Whether a change now is the script's code running while a region settles (journaled anyway, and undone after the
 * region has settled): sets the late flag. */
bool journaling_late();
/** The settling of an outermost region begins (its caller's frames are not the settling's) and ends. */
void settle_begin();
void settle_end();
/** Whether there were such late changes since the last call (and clear the flag). */
bool late_changes_take();
/** `sys.path` as the outermost region on this thread found it (taken at its start: origins.cc). */
void import_snapshot();
/** At install: what the path-based finder searches, and in which order (origins.cc). */
void origins_install();
/** Record what the import system loaded for `module` (its installed spec). */
void origins_note(PyObject *module, PyObject *spec);
/** Let go of those records once no region is running. */
void origins_release();
/** Whether a module named `module_name` is in the package of the module whose import `globals` defines. */
bool package_of_import(PyObject *globals, PyObject *module_name);
/** Plain installed code: an installed function, a module's or class's builtin, a weak reference. */
bool plain_installed(PyObject *value);
/** Whether placing `value` into installed code lets the script choose what a later import calls. */
bool seeds(PyObject *value);
/** An installed kind: an installed class, or an instance of one the script didn't make, holding no code of its own
 * beyond installed code (a finder, a loader, a path entry finder). */
bool installed_kind(PyObject *object);
/** The import system's search state and the interpreter's exit list as the outermost region found them, put back at
 * its end whatever kind of region it is (a run, a step of a generator the script left): steering.cc. */
void steering_snapshot();
/** Settle (run twice: after the undo, and again after the late undo); then let the snapshot go. */
void steering_settle(PyObject *findings);
void steering_release();
/** Any change to `sys.modules`, or a `__path__` written into a watched namespace (or one cleared or cloned, `key`
 * null): the next snapshot finds the packages again (the journal's dict watcher, which sees every such change). */
void steering_modules_changed(PyObject *dict, PyObject *key);
/** The tracer and profiler `threading` gives each new thread (trace_hooks.cc): learn Angelo's values (at the journal's
 * start, a region's start and any unconfined thread start, once `threading` is loaded); follow Angelo's own changes
 * (the dict watcher); put them back (every thread start); refuse a new thread's `sys.settrace` / `sys.setprofile`
 * from `threading` while the name holds a value Angelo never gave it. */
void trace_hooks_know();
void trace_hooks_changed(PyObject *dict, PyObject *key, PyObject *new_value);
bool trace_hooks_put_back();
int trace_hook_refused(const char *event);
/** Callbacks Python or a library calls with another thread's data (callbacks.cc): `sys.excepthook`,
 * `sys.unraisablehook`, `threading.excepthook`, an asyncio loop's task factory and exception handler. Learn Angelo's
 * values and install C's consumers (with `trace_hooks_know`, outside scripts); follow Angelo's own changes and give
 * every thread C's exception path once a script sets `threading.excepthook` (the dict watcher); wrap a thread that
 * starts while it holds the script's value; refuse the interpreter's call of a hook Angelo never gave `sys` when the
 * caller isn't confined (after Angelo's own has run). */
void callbacks_know();
void callbacks_changed(PyObject *dict, PyObject *key, PyObject *new_value);
void callbacks_thread_starting(PyObject *args);
int callback_refused(const char *event, PyObject *args);
/** Logging as the outermost region found it, put back at its end whatever kind of region it is: logging.cc. */
void logging_snapshot();
void logging_settle(PyObject *findings);
void logging_release();
/** Seed the region, naming the owner (a module or class name, or null for a module-level dict) and the key. */
void note_seed(PyObject *owner, PyObject *key);
/** Unseed (a region's start). */
void seed_reset();
/** Whether Angelo's own add-on code is changing a module of its own package (`target_module_name`) with no frame of
 * the script's running anywhere on the stack: its tool state, kept unless the value is one the script made (never
 * the script's own write, nor Angelo code the script called). */
bool angelo_writes(PyObject *target_module_name);
/** Whether the running Python frame's module is named `module_name` (a module changing its own classes). */
bool writer_in_module(PyObject *module_name);
/** Whether `sys.modules` is being changed by the import system loading installed parts (`_load_unlocked`, its spec
 * and loader installed kinds holding no code of their own), with no frame of the script's on the way. */
bool import_system_adding();
/** Stands for "no entry" among what `sys.modules` held. */
extern PyObject *const modules_absent;
/** Settle `sys.modules` from `before` (name -> what it held, or #modules_absent). */
void modules_settle(PyObject *before, PyObject *findings, int *changed, int *noted);
/** `sys.modules` as the journal watches it (null before the journal starts; never aborts during finalization). */
PyObject *journal_modules();
/** A confined change to `sys.modules` outside a region (`key` not changed yet): settled soon on the main thread. */
void modules_changed_outside(PyObject *key);
/** Changes code confined outside a region made to installed namespaces, undone now (a region starting first). */
void journal_settle_outside();
/** True while those, or `sys.modules`, are being put back: their own events are not noted again. */
extern bool outside_settling;
/** Install the journaling `type.__setattr__` (before Python starts, so every class and metaclass inherits it). */
void types_install();

}  // namespace blender::angelo_guard
