/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * The authority behind Angelo's grants, kept in C (doc/angelo/grant_operators.rst, "Authority in C"). The state is
 * `bpy_angelo_guard_authority_core.hh`; these are its doors for Python and for the window manager.
 */

#include <array>
#include <cstdint>
#include <vector>

struct _object;

namespace blender {
struct ID;
/** Add the authority's methods to `_bpy._angelo_guard`. */
void BPY_angelo_guard_authority_add(_object *module);
/** Whether the script call `call_id` may run this exact text (its SHA-256): its exact approved text once, its chat
 * trusted, Always allow scripts, or its external client's allowed file. `run_confined` asks before it compiles. */
bool BPY_angelo_guard_authority_admits(const char *call_id, const uint8_t digest[32]);
/** Input the window manager receives from the operating system (or, in an app started with --enable-event-simulate,
 * simulates): pointer moves, presses and a window losing focus, in window pixels. What a click ticket needs. No
 * GIL needed. `target`: for a move or press, the region Blender's own routing gives it (`wm_event_pointer_region`);
 * only a surface drawn in that region takes it (`Authority::set_regions`'s owner). */
void BPY_angelo_guard_note_input(
    const void *window, int x, int y, int event_type, int event_value, const void *target = nullptr);
/** `window` came forward (the operating system's activation, before the window manager adds its synthetic pointer
 * move): only a press that arrives with it is the click that brought the window forward. No GIL needed. */
void BPY_angelo_guard_note_activate(const void *window);
/** An agent script began running (its outermost confined region): every Tab, press and focus before it stops
 * counting as the person's (the draw state focus is reported from may have changed). No GIL needed. */
void BPY_angelo_guard_agent_ran();
/** The person confirmed `path` (a file, or with `folder` a folder) in a file browser an Angelo operator opened:
 * Angelo's code may write there during that operator's run, whatever its Python state says (writable.cc). Nothing
 * while a script runs. */
void BPY_angelo_guard_note_picked(const char *path, bool folder);
/** The operator whose file browser named paths has run: its picks are forgotten (nothing outlives its run). */
void BPY_angelo_guard_picks_done();
/** The person typed `path` into the file or folder property `identifier` of `data` (in the data-block `owner`; the
 * interface's own edit or its file browse): Angelo's own code may write that exact file, or files directly in that
 * folder, until anything else sets the property. */
void BPY_angelo_guard_note_typed(
    const char *path, bool folder, const ID *owner, const void *data, const char *identifier);
/** The property was set by anything but the person's own edit (Python): what was typed there no longer counts. */
void BPY_angelo_guard_forget_typed(const ID *owner, const void *data, const char *identifier);
/** A window closed: its consent regions and focus history go. */
void BPY_angelo_guard_forget_window(const void *window);
/** Blender's own button for operator `idname` (or, with `property`, a property button: "prop.<identifier>") is about
 * to run for the event the window last received: a real left press or release mints that operator's "ui" ticket. A
 * key (Return, Space, a keymap item running `ui.button_execute`) or a run an agent script drives mints nothing. */
void BPY_angelo_guard_note_button(const char *idname, bool property, int event_type);
/** The pointer moved by code (`WM_cursor_warp`), never the person's move into a control. */
void BPY_angelo_guard_note_warp(const void *window, int x, int y);
/** Whether `window`'s pointer is in the window-pixel rect only because code put it there: a
 * Blender button arms for a grant ticket only by a move the person made into it. */
bool BPY_angelo_guard_warped_into(const void *window, int x, int y, int width, int height);
/** Whether the person's last real pointer move in `window` (a non-zero delta, not a warp) ended at
 * (x, y) at `since` or later, on `BLI_time_now_seconds`'s clock. */
bool BPY_angelo_guard_moved_here_since(const void *window, int x, int y, double since);
/** How long a grant control must be on screen where it is before a real move over it arms it. */
double BPY_angelo_guard_arm_seconds();
/** `window`'s frame reached the screen (its buffers swapped): controls drawn in it count their arm from now. */
void BPY_angelo_guard_note_presented(const void *window);
/** The next frame `window` will present: what a button places in `window` now, while it draws or while an event
 * moves it, is on screen from that frame's swap. Always the button's own window, never another one drawn last. */
uint64_t BPY_angelo_guard_drawing_frame(const void *window);
/** How many times `window` lost focus: a Blender button armed before the last time starts again, as a region does. */
uint64_t BPY_angelo_guard_window_epoch(const void *window);
/** When `window`'s focus epoch last changed (it lost focus or came forward), on `BLI_time_now_seconds`'s clock;
 * long ago for a window the guard has no record of. */
double BPY_angelo_guard_window_epoch_changed(const void *window);
/** A temporary region `key` of `window` (a menu, popover, dialog, pie, search box, tooltip) is drawn over (x, y,
 * width, height) window pixels; consent regions under it take no move and no press (`Authority::set_cover`). */
void BPY_angelo_guard_set_cover(const void *window, const void *key, int x, int y, int width, int height);
/** The temporary region `key` of `window` is gone: the consent regions it covered start again. */
void BPY_angelo_guard_clear_cover(const void *window, const void *key);
/** `window` drew exactly these `count` covers (`keys[i]` over x, y, width, height window pixels at `rects[4 * i]`;
 * `restart_only[i]`: an area's overlapping region, which refuses nothing and restarts what it covered when it goes or
 * moves): any other cover goes (a region freed without `BPY_angelo_guard_clear_cover`, a sidebar hidden). Returns
 * each restart-only cover that went or moved, its key and the rect (x, y, width, height) it was at: Blender's own
 * buttons there start again too (`UI_angelo_note_uncovered`). `visible`: every region the window shows (a canvas
 * menu's cover and a surface's consent regions in a region not among them go: `Authority::sync_covers`). */
struct BPY_AngeloUncovered {
  const void *key;
  std::array<int, 4> rect;
};
std::vector<BPY_AngeloUncovered> BPY_angelo_guard_sync_covers(const void *window,
                                                              const void *const *keys,
                                                              const int *rects,
                                                              const bool *restart_only,
                                                              int count,
                                                              const void *const *visible,
                                                              int visible_count);
/** What a control placed in `window` before its last focus change counts again from (`Authority::restart_from`):
 * the change while the window is in front, now while it is in the back or unknown; `BLI_time_now_seconds`'s clock. */
double BPY_angelo_guard_window_restart_from(const void *window);
/** When frame `frame` of `window` reached the screen (infinity while it hasn't; `drawn` in a window that never
 * swapped one), on `BLI_time_now_seconds`'s clock. */
double BPY_angelo_guard_shown(const void *window, uint64_t frame, double drawn);
/** The person's real press on Blender's own button for operator `idname` (or property "prop.<name>") that was not
 * armed: at (x, y, width, height) in window pixels. The guard answers it quietly with a hint on the button. */
void BPY_angelo_guard_note_unarmed_button(
    const void *window, const char *idname, int x, int y, int width, int height);
/** What `window`'s arming reads now, for a GUI suite's failure details only (`_angelo_jobs.window_guard`), as a
 * Python dict, or None with no record of `window`; the clock's now is under "now". GIL held. */
_object *BPY_angelo_guard_readout(const void *window);
/** Whether a button for operator `idname` must not be made now: a grant operator, while an agent script's own code
 * draws (its panel, menu, header or draw callback). No GIL needed by the caller. */
bool BPY_angelo_guard_draw_refuses(const char *idname);
}  // namespace blender
