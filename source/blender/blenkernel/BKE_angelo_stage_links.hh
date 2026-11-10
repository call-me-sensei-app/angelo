/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * \ingroup bke
 *
 * The links of an Angelo agent's private stage (doc/angelo/agent_execution.rst, "Links between
 * staged copies"). A program edits private copies of the person's data in a private scene whose
 * depsgraph is evaluated, and commit makes some copies live. Every ID pointer such a copy holds,
 * whatever its kind (an object's data, a mesh's texture space mesh, an ID property of either
 * store, a node socket, a driver or constraint target, a light linking collection), must name the
 * stage, data no evaluation writes, or still data the stage reads in place, and, once committed,
 * the ID the person keeps. #BKE_library_foreach_ID_link enumerates those pointers, so no list of
 * pointer kinds can miss one. Every walk here is fresh (#IDWALK_NOP): never answered from Main's
 * relations cache, which can be stale, and never including UI, internal runtime or library
 * pointers. Every function refuses IDs outside \a bmain.
 */

#include <cstdint>
#include <string>
#include <utility>

#include "BLI_function_ref.hh"
#include "BLI_span.hh"
#include "BLI_vector.hh"

namespace blender {

struct ID;
struct Main;
struct Scene;

/** One pointer slot #BKE_angelo_stage_relink changed: enough to restore exactly that slot. */
struct AngeloStageRelinkSlot {
  ID *owner;
  unsigned int owner_uid;
  ID **slot;
  ID *old_id;
  ID *new_id;
  int flags;
};

/**
 * In the IDs of \a ids only (and the embedded IDs they own), point every ID pointer that names the
 * first ID of a pair of \a pairs at its second ID, users and non-users alike, with exact user
 * counts (#BKE_libblock_relink_multiple). No other ID is written; only the user counts of the IDs
 * the pointers stop or start naming change, as for any assignment.
 *
 * \return false with \a r_error set (and nothing written) for an invalid request: an ID of
 * \a ids that is linked, embedded or not in \a bmain; a pair whose IDs differ in type, are the
 * same ID, include an embedded ID or are not in \a bmain; an ID named first by two pairs.
 *
 * \a r_journal receives every slot changed, in walk order (#BKE_angelo_stage_unrelink restores
 * them). \a r_outside receives every ID the IDs of \a ids still point at afterwards that is
 * neither one of them nor embedded in one of them, once each, in the order first met (the rows of
 * #BKE_angelo_stage_links that are neither #IDWALK_CB_EMBEDDED nor #IDWALK_CB_LOOPBACK).
 */
bool BKE_angelo_stage_relink(Main &bmain,
                             Span<ID *> ids,
                             Span<std::pair<ID *, ID *>> pairs,
                             Vector<ID *> &r_outside,
                             Vector<AngeloStageRelinkSlot> &r_journal,
                             std::string &r_error);

/**
 * Restore the slots of \a journal, newest first: a slot is written back only when a fresh walk of
 * its owner (still in \a bmain, same session UID) visits that exact slot and it still names the ID
 * relinked into it, so nothing is ever written through memory the owner no longer holds.
 * \return the number of slots restored; \a r_skipped counts the others.
 */
int BKE_angelo_stage_unrelink(Main &bmain, Span<AngeloStageRelinkSlot> journal, int &r_skipped);

/**
 * Every link of the IDs of \a ids, read only: one call of \a fn per non-null ID pointer that
 * #BKE_library_foreach_ID_link visits on each of them (once per given ID, in order), with the
 * owner (the given ID), the holder (the owner or the embedded ID that holds the pointer), the
 * target and the walk's #LibraryForeachIDCallbackFlag bits: exactly that walk's row set, with
 * #IDWALK_NOP. Writes nothing. False with \a r_error for an ID not in \a bmain.
 */
bool BKE_angelo_stage_links(Main &bmain,
                            Span<ID *> ids,
                            FunctionRef<void(ID *owner, ID *holder, ID *target, int flags)> fn,
                            std::string &r_error);

/**
 * Every ID the \a roots reach by any link (#BKE_angelo_stage_links, loop-backs not followed),
 * the roots and embedded IDs included, never entering an ID of \a exclude; \a r_animated lists
 * those that hold an Action, NLA tracks or drivers. False (lists incomplete) once more than
 * \a limit IDs are reached, or with \a r_error for an ID not in \a bmain.
 */
bool BKE_angelo_stage_reach(Main &bmain,
                            Span<ID *> roots,
                            Span<ID *> exclude,
                            int64_t limit,
                            Vector<ID *> &r_reached,
                            Vector<ID *> &r_animated,
                            std::string &r_error);

/**
 * Whether \a scene is an Angelo agent's private stage scene (its "_angelo_program_stage" ID
 * property). Its depsgraph builds only what its own IDs link to: never the cache files, masks and
 * movie clips of all of Main, which a person's scene builds whether or not anything links them.
 */
bool BKE_angelo_is_stage_scene(const Scene &scene);

/**
 * Whether a stage scene's evaluation is held: always, unless its "_angelo_stage_hold" ID property
 * is exactly 0, which the add-on sets only for an evaluation a check of every link and path has
 * just approved (stage_routes). A held stage's view-layer update and frame change return before
 * the depsgraph is rebuilt or evaluated (scene.cc), so nothing of the person's is ever built into
 * the stage's active graph unchecked, whatever started the evaluation. False for other scenes.
 */
bool BKE_angelo_stage_held(const Scene &scene);

/**
 * scene.cc, at the top of each pass of a view-layer update or frame change, after every pre
 * callback: true when \a scene is a held stage (return without rebuilding or evaluating);
 * otherwise a stage's release is consumed (it is held again), so one release lets exactly one
 * pass evaluate, whatever later skips the post callbacks. Always held off the main thread (no
 * check runs there). False for other scenes.
 */
bool BKE_angelo_stage_take_hold(Scene &scene);

/**
 * The newest session-wise UID given so far (lib_id.cc): every ID made later has a greater one,
 * until a file load resets the counter. What a stage's code made since its last check.
 */
uint32_t BKE_lib_libblock_session_uid_last();

/**
 * Whether the scope protecting Main (undo_system.cc) is a capture, a native commit whose own
 * writes run inside it, rather than an Undo restore traversal or a checkpoint preparation, which
 * replace or snapshot Main. Stage relinking is refused only in the latter.
 */
bool BKE_undosys_capture_scope_active();

/**
 * A new, empty local ID of \a like's type in \a bmain (#BKE_id_new; an Empty for an object) with
 * no users, which a stage points at instead of live data it must not evaluate or carry. Null for
 * a type that has no stand-in: libraries, window managers, workspaces and screens, scenes (a
 * stage bundle holds exactly one, its own), shape keys (owned by their data), embedded IDs.
 */
ID *BKE_angelo_stage_stand_in(Main &bmain, const ID &like);

/**
 * A plain local copy of \a id in \a bmain with no users: never in a library and never a library
 * override (#LIB_ID_COPY_NO_LIB_OVERRIDE), its shape keys copied. Null when \a id is embedded or
 * its type cannot be copied.
 */
ID *BKE_angelo_stage_copy_plain(Main &bmain, const ID &id);

/** Whether every ID of \a ids is in \a bmain (one pass over the lists of the types they have). */
bool BKE_angelo_stage_in_main(Main &bmain, Span<ID *> ids);

}  // namespace blender
