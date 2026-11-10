/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

struct _object;

namespace blender {
/* The ``_angelo_stage`` link functions (BKE_angelo_stage_links.hh). Each runs on the main thread
 * only and is refused while Undo restores Main; an invalid request is a ValueError and writes
 * nothing. */

/** ``relink(ids, pairs) -> (outside, journal)``: #BKE_angelo_stage_relink (replays once). */
_object *BPY_angelo_stage_relink(_object *self, _object *args);
/** ``unrelink(journal) -> (restored, skipped)``: #BKE_angelo_stage_unrelink. */
_object *BPY_angelo_stage_unrelink(_object *self, _object *journal);
/** ``links(ids) -> [(owner, holder, target, flags)]``: #BKE_angelo_stage_links. */
_object *BPY_angelo_stage_links(_object *self, _object *ids);
/** ``reach(roots, exclude, limit) -> (reached or None, animated)``: #BKE_angelo_stage_reach. */
_object *BPY_angelo_stage_reach(_object *self, _object *args);
/** ``stand_in(like)``: #BKE_angelo_stage_stand_in, or None. */
_object *BPY_angelo_stage_stand_in(_object *self, _object *like);
/** ``copy_plain(id)``: #BKE_angelo_stage_copy_plain, or None. */
_object *BPY_angelo_stage_copy_plain(_object *self, _object *id);
_object *BPY_angelo_stage_session_uid_last(_object *self, _object *args);
/** Add the ``LINK_*`` flag constants to the ``_angelo_stage`` module; false with an error set. */
bool BPY_angelo_stage_add_link_flags(_object *module);
}  // namespace blender
