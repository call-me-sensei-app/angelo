/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * Shared between the confinement's files (module, hook, wrappers). Every piece of state here is C: nothing in
 * Python can reach or rebind it (doc/angelo/script_confinement.rst).
 */

#include <Python.h>

#include <cstdint>

#include <atomic>
#include <string>

#include "bpy_angelo_guard_confine_native.hh"

namespace blender {
struct ID;
}

namespace blender::angelo_guard {

/* The refusals, English (the agent reads them; the person's interface translates them: script_confinement.REASONS
 * and the catalogues hold the same text). */
extern const char *const PRIVATE;
extern const char *const PROGRAMS;
extern const char *const NATIVE;
extern const char *const GUARD;
extern const char *const STARTUP;
extern const char *const TOOL_OUTPUT;
extern const char *const AUTORUN;
extern const char *const OPERATOR;
extern const char *const WRITE;
extern const char *const GRANT;
/** GRANT's sentence for what aims the person's click (a pointer move, a popup): not a grant. */
extern const char POINTER[];
extern const char *const NETWORK;
extern const char *const PROCESS;
extern const char *const PREFERENCES;
extern const char *const DRIVER_CODE;
extern const char *const STYLE_MODULE;

/** The file name every agent script compiles under (script_source.SCRIPT_FILE) and a driver expression's. */
extern const char *const MARK;
extern const char *const DRIVER;

/** This thread's depth: inside `run_confined`, a trampoline, or a job a confined thread started. */
extern thread_local int depth;
/** A script ran on this thread outside `run_confined` (someone rebound the executor): confined until the next
 * `run_confined` ends, failing closed. */
extern thread_local bool pending;
/** Re-entrance: the hook's own work raises events too. */
extern thread_local bool busy;
/** The confinement's own work at a region's end: still confined, but not journaled. */
extern thread_local bool quiet;
/** The namespace of the extension module `_imp.exec_dynamic`/`exec_builtin` is filling on this thread (one a
 * confined import just created), or null: what its own C initialization adds there is its definition. */
extern thread_local PyObject *defining_namespace;
/** `_imp.create_*`/`exec_*` running on this thread: an extension module's native initialization (pybind11, nanobind,
 * any C extension) setting up its module and types, however it was loaded. */
extern thread_local int extension_initializing;

/** A confined region on this thread (bpy_angelo_guard_confine_region.cc): `region_enter` raises the depth and,
 * outermost, starts recording what the thread makes; `region_leave` lowers it and, outermost, puts back what the
 * region rebound (the journal, from `journal`) and makes what it left alive safe. */
struct RegionMark {
  size_t journal;
  bool outermost;
};
RegionMark region_enter();
void region_leave(const RegionMark &mark, PyObject *findings, PyObject *script_namespace = nullptr);
/** What a trampoline runs and what a confining descriptor holds (borrowed), or null. */
PyObject *deferred_function(PyObject *value);
PyObject *confined_attr_original(PyObject *value);
int region_types_ready();
/** Older objects are frozen while regions run (bpy_angelo_guard_confine_region.cc). */
extern bool regions_frozen;
/** What the outermost region made and left alive, made safe (bpy_angelo_guard_confine_leftovers.cc); `last`: no
 * other region runs, so older holders may be searched. */
void confine_leftovers(PyObject *findings, bool last, PyObject *script_namespace, PyObject *objects);
/** A `gc` function, called as the confinement (the hook lets these through). */
PyObject *gc_call(const char *function, PyObject *argument = nullptr);
/** Whether a confined region made `object`: known only while a region's end is settled (`settle_made`). */
bool made_in_region(PyObject *object);
/** Whether the script's own code made `object` (the innermost Python code running when it was allocated was agent
 * code); false for what installed code made, on the script's behalf or not. Settled like `made_in_region`. */
bool made_by_script(PyObject *object);
/** Forget the cached verdicts on which code is the script's (a code object was just tainted). */
void verdicts_forget();
/** The function a `functools.lru_cache` wrapper really calls (borrowed; its `__wrapped__` is only a settable copy),
 * or null for any other object (bpy_angelo_guard_confine_attr.cc). */
PyObject *lru_function(PyObject *value);
/** Match what confined threads allocated against the young objects (a list), so `made_in_region` answers; undo with
 * `unsettle_made`. `recorded_any`: anything recorded at all. `recording_reset`: forget it (no region runs). */
void settle_made(PyObject *young);
void unsettle_made();
bool recorded_any();
void recording_reset();
/** Wrap the object allocator to record what confined threads allocate; false when this interpreter's object layout
 * is not the one provenance assumes (then every script is refused: fail closed). */
bool recording_install();
bool recording_works();
/** `ConfinedAttr` (bpy_angelo_guard_confine_attr.cc). */
extern PyTypeObject confined_attr_type;
extern PyTypeObject confined_data_attr_type;
int attr_types_ready();
/** A new confining descriptor for a class attribute (data descriptor when `original` is one). */
PyObject *confined_attr_new(PyObject *original);
/** A class a region made: what in it is not installed code becomes a confining descriptor (attr.cc). */
void confine_class(PyTypeObject *type);
/** A function whose code is installed (not the agent's, under the installed folders). */
bool installed_function(PyObject *value);
/** A trampoline, a confining descriptor or a work item: already confined. */
bool confining(PyObject *value);
/** The load-time texts as a region starts (bpy_angelo_guard_confine_texts.cc): a .blend written while confined keeps
 * a text's run-at-load only if the text was so, with the same content, when the region started. */
void region_text_snapshot();
/** A text's content, hashed (bpy_angelo_guard_confine_texts.cc). */
uint64_t text_content(const void *text); /* `Text *` */
/** Python a project carries (bpy_angelo_guard_confine_carriers.cc): noted when the outermost region starts, kept or
 * made inert when written while confined and at the script's end; writing one refused while confined. */
void carriers_snapshot();
void carriers_sweep(PyObject *findings);
bool driver_kept(const void *fcurve);
bool style_module_kept(const void *module);
const char *new_library_refusal();
/** `expression` null: the driver's own; `type` < 0: the driver's own. */
const char *driver_refusal(const void *driver, const char *expression, int type);
bool load_time_kept(const void *text);
/** The person's trusted run-at-load content (bpy_angelo_autorun_trust.cc): the file gate's two entries. */
bool autorun_trusted(const void *text);
void autorun_trust(void *text);
/** Whether any agent code has run in this process: until then the hook returns at once. */
extern std::atomic<bool> armed;

/** `_angelo_guard.ScriptConfined` (a PermissionError subclass with `reason` and `what`). */
extern PyObject *script_confined_type;
/** `_angelo_guard.Deferred`: a callable run confined (the trampoline). */
extern PyTypeObject deferred_type;

/** Set `ScriptConfined(reason, what)` as the current exception; returns -1 (for the hook) . */
int refuse(const char *reason, const char *what);

/** Whether `code` is agent code: compiled under MARK or DRIVER, or tainted (made or run while confined). */
bool agent_code(PyCodeObject *code);
/** Record `code` and every code object it holds as agent code. */
void taint(PyObject *code);
/** Record a file name as agent code's (a text or file the confined thread compiled). */
void taint_name(PyObject *name);
/** Whether a frame of agent code is on this thread's stack (GIL). */
bool frames_confined();
/** Whether this thread is confined now (GIL). */
bool confined_now();

/** A new reference to `function` run confined (an existing trampoline comes back as it is). */
PyObject *defer(PyObject *function);

/** `_angelo_guard.WorkItem` and `run_work` (bpy_angelo_guard_confine_work.cc). */
extern PyTypeObject work_item_type;
int work_item_type_ready();
PyObject *run_work(PyObject *self, PyObject *args);

/** The namespace journal (bpy_angelo_guard_confine_journal.cc). */
size_t journal_mark();
void journal_undo(size_t mark, PyObject *findings);
void journal_watch_loaded();
/** Whether the journal watches this module namespace (an installed module's). */
bool journal_watches(PyObject *dict);
PyObject *guard_watch_modules(PyObject *self, PyObject *names);

/** Whether `name` (a code object's file name) marks agent code: MARK, DRIVER or a tainted name. */
bool agent_name(PyObject *name);

/** Where code comes from (bpy_angelo_guard_confine_provenance.cc): each returns -1 with ScriptConfined set when
 * refused (called while confined). */
void provenance_install();
/** Whether the running code is an installed import's own work (bpy_angelo_guard_confine_journal.cc): an extension's
 * native initialization (`extension_initializing`), or, walking out from the innermost frame, an installed module's
 * top level or the import system's frame executing a module (`_load_unlocked`, `_exec`) is reached with no agent
 * frame on the way. A module's top level, the helpers it calls (`Tf.PrepareModule`, `enum._convert_`, a class's
 * `__init_subclass__`), pybind11 setting its classes' methods, the import system's own `sys.modules` bookkeeping,
 * however the module was loaded; never what a script, or an import hook or loader it left, does. */
bool in_installed_import();
/** The namespace of the installed module whose top level is running (as `in_installed_import` finds it), borrowed;
 * null when it is not one (the import system's own frames, or agent code first). */
PyObject *installed_import_globals();
/** Set when the script itself changed installed code (a module's namespace, the builtins, an installed class) in
 * this thread's region: from then on no installed module is imported afresh in it (bpy_angelo_guard_confine_hook.cc),
 * so no import runs what the script chose. Reset when the outermost region starts. */
extern thread_local bool region_seeded;
/** Refuse a fresh import (or a module body run) in a seeded region, naming what seeded it (journal.cc). */
int refuse_seeded_import();
/** An installed file's (or a frozen module's) top level (journal.cc). */
bool installed_body(PyCodeObject *code);
void import_frames_install();
/** The whole process's state (bpy_angelo_guard_confine_process.cc): noted when the first confined region starts, put
 * back when the last one ends; and the wrappers for what no event shows or what is refused. */
void process_snapshot();
void process_restore(PyObject *findings);
void install_process_wrappers();
/** Suspended work a region left (bpy_angelo_guard_confine_resume.cc): `resume_install` takes the confinement's
 * `sys.monitoring` tool; `resume_note` notes a generator, coroutine or async generator the region made, so each later
 * step of it that starts outside a region runs as one (false: not one, or no tool). */
void resume_install();
/** `os.environ` and `os.environb` as views of the C environment (bpy_angelo_guard_confine_environ.cc). */
void install_environ_view();
/** `io.open_code`'s hook: code Angelo itself loads from a file a script wrote is refused (_hook.cc). */
void install_open_code_hook();
/** Windows' pipe pair a process makes for itself (bpy_angelo_guard_confine_hook.cc): the `CreateNamedPipe` wrapper
 * marks its own judged call, and the name it made, which one `CreateFile` of this thread may then connect to. */
void local_pipe_creating(bool creating);
void local_pipe_created(const std::string &name);
std::string pipe_key(const char *name);
bool environ_view(PyObject *object);
bool resume_ready();
bool resume_note(PyObject *object);
/** `sys.monitoring.<name>` as Python had it (borrowed; the wrappers in bpy_angelo_guard_confine_wrap_io.cc refuse a
 * confined thread's calls). */
PyObject *monitoring_original(const char *name);
/** -1 with RecursionError set when this thread's native stack is nearly used up: every trampoline entry asks first
 * (a script's callable can re-enter itself through C alone, faster than CPython's recursion count notices). */
int native_stack_check();
/** Whether the first region's snapshot is held (a region runs on some thread). */
bool process_snapshot_taken();
/** A change to the whole process from confined code (not an installed import's own work): -1 with ScriptConfined
 * set when refused; `always`: refused inside a region too (the environment, the working folder, the file-creation
 * mask), else only outside one (nothing would put it back). 0 to go on. */
int process_change_refused(const char *what, bool always);
/** Signals, the socket timeout and the warning filters (bpy_angelo_guard_confine_signals.cc): snapshot and restore
 * with the process's; this thread's signal mask when its region ends; -1 with ScriptConfined set when a confined
 * `signal.signal` is refused (outside a region, or over a native handler). */
void signals_snapshot();
void signals_restore(PyObject *findings);
void signals_restore_dispositions(PyObject *findings);
void signals_thread_restore(PyObject *findings);
int signal_change_noted(PyObject *args);
void install_signal_wrappers();
/** `_signal.signal` as Python had it (borrowed; bpy_angelo_guard_confine_wrap.cc). */
PyObject *guard_original_signal();
/** Whether `value` is evidently the script's code: a function of agent or uninstalled code, or a method, static or
 * class method, property, `lru_cache`, partial or callable object whose class calls one (attr.cc). */
bool agent_callable(PyObject *value, int level = 0);
/** Whether `value` is installed code as an attribute (an installed function, a method of one on an object the
 * script didn't make, a descriptor or builtin of a module or class): never a builtin bound to the script's data. */
bool installed_attribute(PyObject *value);
/** `weakref.finalize._registry` as this thread's outermost region found it, and every record added since judged at
 * its end: a genuine record runs confined and never at exit, anything else is removed (_leftovers.cc). */
void finalize_snapshot();
void finalize_settle(PyObject *findings);
PyObject *guard_add_bytecode_loaders(PyObject *self, PyObject *codes);
bool in_bytecode_loader();
int provenance_compile(PyObject *args);
int provenance_code_new(PyObject *args);
int provenance_marshal(const char *event, PyObject *args);
/** A confined thread's new SQLite connection: no database can be attached to it (bpy_angelo_guard_confine_wrap_io). */
void sqlite_connected(PyObject *connection);
/** Inside `_imp.get_frozen_object` for a frozen module's own bytes on this thread (bpy_angelo_guard_confine_wrap.cc). */
extern thread_local int loading_frozen;
/** Inside `socket.socketpair` on this thread (its own socket events are allowed). */
extern thread_local int making_socketpair;
/** The second set of wrappers (bpy_angelo_guard_confine_wrap_io.cc). */
int install_wrappers_io();
/** Native file entry points and SQLite (bpy_angelo_guard_confine_wrap_native.cc). */
void install_native_entries();
/** The pipeline modules' path gates (bpy_angelo_guard_confine_wrap_pipeline.cc). */
void install_pipeline_entries();
/** Put `wrapper` where `module_name.name` is `original`, and every other reference to it. */
void put_in(const char *module_name, const char *name, PyObject *wrapper, PyObject *original);
/** Judge a path argument (str, bytes or PathLike) for a confined thread: -1 with ScriptConfined set when refused. */
int judge_entry_path(PyObject *value, bool write);
/** Point every loaded module's reference to `original` (and `__wrapped__`) at `wrapper`. */
void replace_references(PyObject *original, PyObject *wrapper);
/** Between these, replacements are collected and the loaded modules walked once for all of them at the end. */
void replace_references_begin();
void replace_references_end();


/** Shared by the module's files (bpy_angelo_guard_confine.cc and its _run, _roots, _deferred). */
bool refuse_if_confined(const char *what);
bool name_is(PyObject *name, const char *text);
int deferred_type_ready();
bool add_each(PyObject *items, void (*add)(const std::string &));
PyObject *guard_run_confined(PyObject *self, PyObject *args, PyObject *kwargs);
PyObject *guard_set_compiler(PyObject *self, PyObject *function);
PyObject *guard_set_sweep(PyObject *self, PyObject *args);
PyObject *guard_add_roots(PyObject *self, PyObject *args);
PyObject *guard_add_open_roots(PyObject *self, PyObject *args);
PyObject *guard_judge_path(PyObject *self, PyObject *args);
/** What a script reads of the code around it (bpy_angelo_guard_confine_opaque.cc): frames not its own are opaque to
 * it, Angelo's package can't be imported by name. `angelo_package(name)` names the package at install; each module
 * of it the import system adds later is Angelo's too. */
PyObject *guard_angelo_package(PyObject *self, PyObject *name);
void opaque_module_added(PyObject *key, PyObject *module);
int install_wrappers_opaque();
/** `own_path`, `owned_path`, `disown_path`, `process_ours` (bpy_angelo_guard_confine_owned.cc). */
extern PyMethodDef owned_methods[];
/** Whether the nearest frame, walking out, is a script's before it is Angelo's (opaque.cc). */
bool script_calling();
/** Whether a frame of Angelo's own package is on this thread's stack (opaque.cc): a call that comes through Angelo's
 * code, not another add-on's or a test harness's own. */
bool angelo_on_stack();
/** Whether `key` (a resolved path) is, or lies inside, a file or folder Angelo made (owned.cc). */
bool owned_or_inside_owned(const std::string &key);
/** Whether `path` itself (not a folder above it) is a file or folder Angelo recorded, by identity (owned.cc). */
bool owned_exactly(const std::string &path);
/** Record `path` as Angelo's by identity (owned.cc); the caller decided it may. */
bool record_owned(const std::string &path);
/** Whether `path` was born since this process's first script region began (or its birth time is unknown): a
 * script could have made it (C's change record keeps no folder made). */
bool born_since_first_region(const std::string &path);
/** Where Angelo's own code may write a path from Python state (writable.cc): private, made, or named by the person. */
bool may_write(const std::string &path);
/** `may_write` for a writer that reads the property `*tag` (`typed_tag`): also a path the person typed there. */
bool may_write_for(const std::string &path, const std::string *tag);
/** writable.cc's own reading of a path, shared with beside.cc: the one spelling C judges (Windows' forms folded;
 * empty: refused), its resolved key (empty: unanswerable), whether its last part is a link, whether `key` lies in
 * `folder`, whether confinement acts in this process (a script region began), whether `key` is or lies in the open
 * document's folder, whether a folder is too wide to name whole, and whether a script made `plain` or a folder above
 * it. */
std::string write_plain(const std::string &path);
std::string write_key(const std::string &path, bool folder);
bool write_link_at(const std::string &path);
bool write_inside(const std::string &key, const std::string &folder);
bool write_confinement_active();
bool write_in_document_folder(const std::string &key);
bool too_wide(const std::string &key);
bool write_script_made_on_the_way(const std::string &plain);
/** tagged.cc: the file gate's round-3 callbacks (a data-block made on a confined thread; a string it set), and the
 * Python methods `script_set(owner_pointer, identifier)` and `script_originated(owner_pointer)`. */
void script_id_made(unsigned int session_uid, const char *source_file);
void script_property_set(unsigned int owner_session_uid, const void *struct_type, const char *identifier);
PyObject *guard_script_set(PyObject *self, PyObject *args);
PyObject *guard_script_originated(PyObject *self, PyObject *arg);
/** beside.cc: `output_allowed_for(path, owner_pointer, data_pointer, identifier)`, the writers' judge from Python. */
PyObject *guard_output_allowed_for(PyObject *self, PyObject *args);
PyObject *guard_folder_allowed_for(PyObject *self, PyObject *args);
/** The Python method `make_dir_beside` (beside.cc), in `writable_methods`. */
PyObject *guard_make_dir_beside(PyObject *self, PyObject *args);
/** The tag of the property `identifier` of the data at `data` in the data-block `owner`, as a typed path is kept
 * under it (writable.cc). */
std::string typed_tag(const ID *owner, const void *data, const char *identifier);
/** Record a path the person (or a program they connected) gave Angelo to write, for this process only: a file, or
 * (`persist` false: one call's) a folder of Blender's own. */
bool vouch(const std::string &path, bool folder, bool persist);
/** A script region began (owned.cc's region start): from now on `may_write` judges. */
void writable_region_began();

/** The writers' gate (bpy_angelo_guard_confine_writers.cc, `BLI_angelo_writers.hh`): how many outermost regions run
 * (a writer job that begins meanwhile begins inside one), and its installation. */
void writers_region_entered();
void writers_region_left();
void writers_install();
extern PyMethodDef writers_methods[];
extern PyMethodDef writable_methods[];
/** `os.kill` / `os.killpg`: a confined thread signals nothing; Angelo's own code only itself and its descendants. */
int signal_refused(const char *event, PyObject *args, bool confined);
/** This thread's outermost region begins: what Angelo records while it runs must be made since (_owned.cc). */
void owned_region_began();

/** The C audit hook. */
int hook(const char *event, PyObject *args, void *user_data);
/** Replace the Python entry points a script could use to escape unseen (os.open's descriptor folder,
 * `_posixsubprocess.fork_exec`, thread starts, atexit, signal handlers, codec searches). Originals stay in C. */
int install_wrappers();

}  // namespace blender::angelo_guard

namespace blender {
/** The denied operators' refusal for an operator (`WM_OT_url_open` and its Python form `wm.url_open`), or null. */
const char *BPY_angelo_guard_operator_refusal_id(const char *idname, const char *py_idname);
}  // namespace blender

namespace blender::angelo_guard {

}  // namespace blender::angelo_guard
