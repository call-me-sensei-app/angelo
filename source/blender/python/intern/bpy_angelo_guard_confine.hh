/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * The confinement of agent scripts, kept in C (doc/angelo/script_confinement.rst).
 *
 * A thread is confined while it runs agent code: from `_angelo_guard.run_confined` until it returns, inside a
 * trampoline (`_angelo_guard.defer`: what a script leaves for later), or while a frame of agent code is on its
 * stack. The audit hook, the operator gate, the registration points (timers, message bus, draw handlers) and the
 * file gate (`BLI_fileops_gate_set`) read it. No Python object holds any of it.
 */

struct _object;

namespace blender {
struct bContext;
struct ID;
struct PointerRNA;
struct PropertyRNA;
struct wmOperatorType;

/** Register the confinement's audit hook (a C callback). Call before Python starts (`BPY_python_start`). */
void BPY_angelo_guard_confine_install();
/** Add the confinement's methods to `_bpy._angelo_guard`. */
void BPY_angelo_guard_confine_add(_object *module);

/** Whether this thread runs agent code now: its depth, or a frame of agent code on its stack. Needs the GIL. */
bool BPY_angelo_guard_confined();
/** A confined script writing to Preferences or the keymap (any property, item, slice or function of theirs): the
 * refusal, or null. */
const char *BPY_angelo_guard_write_refusal(const PointerRNA *ptr);
/** A function that moves the person's pointer or opens a popup under it (`Window.cursor_warp`,
 * `WindowManager.popup_menu`...), called by an agent script: its reason, else null (the 2.0.0
 * break-it review's clickjacking: the person's next click must go where they aimed it). */
const char *BPY_angelo_guard_input_refusal(const PointerRNA *ptr, const char *function);
/** A value a confined script sets that brings Python into the project: a driver expression that needs Python (or the
 * driver's type made scripted over one), a style module's script, or switching on a style module. Null: allowed. */
const char *BPY_angelo_guard_value_refusal(const PointerRNA *ptr,
                                           PropertyRNA *prop,
                                           const char *string_value,
                                           int int_value,
                                           bool pointer_set);
/** `bpy.data.libraries.write` while confined, with a library the script linked that brings code which runs when a file
 * opens: the refusal, or null. */
const char *BPY_angelo_guard_library_write_refusal();
/** Around a call that runs without the GIL (an operator): when this thread is confined (agent code on its stack
 * included), it stays confined inside, where its frames can't be read. Returns whether it held; then release. */
bool BPY_angelo_guard_hold();
void BPY_angelo_guard_release();
/** Arm the confinement's checks now (before a driver's Python first runs). */
void BPY_angelo_guard_arm();
/** This thread's depth alone (no GIL needed): the file gate and window-manager jobs read it. */
bool BPY_angelo_guard_thread_confined();
/** The depth to give a job thread a confined thread started (`WM_jobs_start`): 0 when not confined. */
int BPY_angelo_guard_thread_depth();
/** Run with `depth` added to this thread's depth (a job's thread: what its starter had). */
void BPY_angelo_guard_thread_enter(int depth);
void BPY_angelo_guard_thread_leave(int depth);

/** `function` (a new reference) wrapped to run confined wherever it runs later, when this thread is confined; else
 * `function` itself (a new reference). For registration points: timers, the message bus, draw handlers. */
_object *BPY_angelo_guard_defer_if_confined(_object *function);

/** Why a confined thread may not run operator `ot` with properties `ptr` (an English refusal), or null when it may
 * or the thread is not confined: the denied operators (programs, installs, preferences, grants), every file and
 * folder path property, and the file an operator writes from context (a text's, an image's, the render output). */
const char *BPY_angelo_guard_operator_refusal(bContext *C, wmOperatorType *ot, PointerRNA *ptr);
/** Why a confined thread may not set file or folder path property `prop` to `value`, or null. Every RNA path a
 * Blender writer later uses (a text's or image's file, the render output, a cache folder, a library) is judged here,
 * when it is set. */
const char *BPY_angelo_guard_path_property_refusal(PropertyRNA *prop, const char *value);
/** A confined thread sets the string property `prop` of `ptr` (data, never a function's parameter) to `value`: a
 * path is set as asked (it is tagged as the script's where every property is set, and writers judge what they
 * write from it); any other string may only lead down from where a writer joins it (no `..` part, leading separator,
 * drive or share). The refusal, or null (output.cc). */
const char *BPY_angelo_guard_output_path_refusal(const PointerRNA *ptr, PropertyRNA *prop, const char *value);
/* Round 3 (the re-read of f9f24cc6ffd, "the data decides, not the place"): what a writer asks. Any thread, no GIL.
 * A writer judges a write iff any input its output path is built from is script-set (`script_set` of its owner and
 * property), or its owner is script-originated, or its job started inside a script region; then it asks
 * `output_allowed` with the path it is about to write and the property it read it from. A person's job on the
 * person's own data is never judged. */

/** Whether a confined thread set the property `prop` (its RNA identifier) of data of the RNA type `type` (or a type
 * refining it) in the data-block `owner`, or `owner` is script-originated (all a null `type` or `prop` asks).
 * False for a null `owner`. */
bool BPY_angelo_guard_script_set(const ID *owner, const char *type, const char *prop);
/** Whether the data-block `id` was created, copied, appended or linked inside a script region (by its session
 * uid): every path value it holds counts as script-set. False for null. */
bool BPY_angelo_guard_script_originated(const ID *id);
/** Whether a writer may write `path` (absolute, as expanded: frame, view, extension, templates), read from the
 * property `prop` of `data` in `owner` (the tag): a place C says Angelo may write, what the person typed into THAT
 * property (a typed pattern covers its own expansions: frames, views, camera), or a new file below an accepted
 * anchor (the open document's folder, a person-named file's folder), which C makes and records. */
bool BPY_angelo_guard_output_allowed(const char *path, const ID *owner, const void *data, const char *prop);

/** The folder judge for writers that write into a folder they read from data (a cache, a bake, a sequence of
 * proxies): a folder C says Angelo may write, the folder the person typed into THAT property, or a NEW folder below
 * an accepted anchor, which C makes here (each part, following no link) and records as Angelo's. Any thread. */
bool BPY_angelo_guard_folder_allowed(const char *dir, const ID *owner, const void *data, const char *prop);
/** The window manager opened or saved the document (main thread, after load-post / a successful save): its folder
 * is the create-beside anchor when the person did it, and none when a script's turn did (beside.cc). */
void BPY_angelo_guard_document_settled();
/** The refusal for a confined thread making code run when a file opens (a text's `use_module` set True), or null. */
const char *BPY_angelo_guard_load_time_refusal(PointerRNA *ptr, PropertyRNA *prop);
/** Whether `idname` (`ANGELO_OT_x` or `angelo.x`) is a grant operator (the registry the grant guard passed to
 * `add_denied_operators`). No GIL needed: the interface refuses to draw such a button while confined. */
bool BPY_angelo_guard_is_grant_operator(const char *idname);
/** Set `ScriptConfined(reason, what)` as the current Python exception. */
void BPY_angelo_guard_raise(const char *reason, const char *what);

/** Whether a confined thread (depth, no GIL) is refused `path`; `write` for any change. For the file gate. */
bool BPY_angelo_guard_path_refused(const char *path, bool write);


}  // namespace blender
