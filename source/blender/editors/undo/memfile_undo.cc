/* SPDX-FileCopyrightText: 2023 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup edundo
 *
 * Wrapper between `ED_undo.hh` and `BKE_undo_system.hh` API's.
 */

#include <memory>

#include "BLI_sys_types.hh"

#include "BLI_ghash.hh"
#include "BLI_listbase.hh"

#include "DNA_ID.h"
#include "DNA_collection_types.h"
#include "DNA_mesh_types.h"
#include "DNA_node_types.h"
#include "DNA_object_types.h"
#include "DNA_scene_types.h"
#include "DNA_windowmanager_types.h"

#include "BKE_blender_undo.hh"
#include "BKE_compositor.hh"
#include "BKE_context.hh"
#include "BKE_lib_id.hh"
#include "BKE_image.hh"
#include "BKE_lib_query.hh"
#include "BKE_main.hh"
#include "BKE_node.hh"
#include "BKE_object_types.hh"
#include "BKE_paint.hh"
#include "BKE_preview_image.hh"
#include "BKE_report.hh"
#include "BKE_scene.hh"
#include "BKE_scene_runtime.hh"
#include "BKE_sculpt_undo.hh"
#include "BKE_undo_system.hh"

#include "../depsgraph/DEG_depsgraph.hh"

#include "WM_api.hh"
#include "WM_types.hh"

#include "ED_render.hh"
#include "ED_sculpt.hh"
#include "ED_undo.hh"
#include "ED_util.hh"

#include "../blenloader/BLO_undofile.hh"

#include "undo_intern.hh"

namespace blender {

/* -------------------------------------------------------------------- */
/** \name Implements ED Undo System
 * \{ */

static bool memfile_undosys_poll(bContext *C)
{
  /* other poll functions must run first, this is a catch-all. */

  if ((U.uiflag & USER_GLOBALUNDO) == 0) {
    return false;
  }

  /* Allow a single memfile undo step (the first). */
  UndoStack *ustack = ED_undo_stack_get();
  if ((ustack->step_active != nullptr) && (ED_undo_is_memfile_compatible(C) == false)) {
    return false;
  }
  return true;
}

static bool memfile_undosys_step_encode(bContext * /*C*/, Main *bmain, UndoStep *us_p)
{
  MemFileUndoStep *us = reinterpret_cast<MemFileUndoStep *>(us_p);

  /* Important we only use 'main' from the context (see: BKE_undosys_stack_init_from_main). */
  UndoStack *ustack = ED_undo_stack_get();

  if (bmain->is_memfile_undo_flush_needed) {
    ED_editors_flush_edits_ex(bmain, true);
  }

  /* can be null, use when set. */
  MemFileUndoStep *us_prev = reinterpret_cast<MemFileUndoStep *>(
      BKE_undosys_step_find_by_type(ustack, BKE_UNDOSYS_TYPE_MEMFILE));
  us->data = BKE_memfile_undo_encode(bmain, us_prev ? us_prev->data : nullptr);
  us->step.data_size = us->data->undo_size;

  /* Store the fact that we should not re-use old data with that undo step, and reset the Main
   * flag. */
  us->step.use_old_bmain_data = !bmain->use_memfile_full_barrier;
  bmain->use_memfile_full_barrier = false;

  return true;
}

static int memfile_undosys_step_id_reused_cb(LibraryIDLinkCallbackData *cb_data)
{
  ID *self_id = cb_data->self_id;
  ID *owner_id = cb_data->owner_id;
  ID **id_pointer = cb_data->id_pointer;
  /* Embedded IDs do not get tagged with #ID_TAG_UNDO_OLD_ID_REUSED_UNCHANGED currently (could be,
   * but would add extra processing, and by definition they always share that state with their
   * owner, as they are stored as 'regular data' in blend-files, not as independent IDs).
   *
   * NOTE: It seems that local IDs using embedded ones are never 'reused unchanged', this was
   * never caught before. However, if using `self_id` here, this assert gets triggered with
   * upcoming packed data. Probably because while packed data remains unchanged, it is handled like
   * regular local data by undo code, and like regular linked data. */
  BLI_assert((owner_id->tag & ID_TAG_UNDO_OLD_ID_REUSED_UNCHANGED) != 0);
  UNUSED_VARS_NDEBUG(owner_id);

  ID *id = *id_pointer;
  if (id != nullptr && !ID_IS_LINKED(id) && (id->tag & ID_TAG_UNDO_OLD_ID_REUSED_UNCHANGED) == 0) {
    bool do_stop_iter = true;
    if (self_id->id_type() == ID_OB) {
      Object *ob_self = id_cast<Object *>(self_id);
      if (ob_self->type == OB_ARMATURE) {
        if (ob_self->data == id) {
          BLI_assert(id->id_type() == ID_AR);
          if (ob_self->pose != nullptr) {
            /* We have a changed/re-read armature used by an unchanged armature object: our beloved
             * Bone pointers from the object's pose need their usual special treatment. */
            ob_self->pose->flag |= POSE_RECALC;
          }
        }
        else {
          /* Cannot stop iteration until we checked ob_self->data pointer... */
          do_stop_iter = false;
        }
      }
    }

    return do_stop_iter ? IDWALK_RET_STOP_ITER : IDWALK_RET_NOP;
  }

  return IDWALK_RET_NOP;
}

static void memfile_cancel_intermediate_previews(Main *bmain)
{
  if (bmain) {
    for (wmWindowManager &wm : bmain->wm) {
      /* This also frees suspended RENDER_PREVIEW customdata while its Main/IDs still live.
       * The undo variant marks interrupted previews for regeneration, as the decode's own
       * #ED_preview_kill_jobs_for_undo below would have. */
      ED_preview_kill_jobs_for_undo(&wm, bmain);
    }
  }
}

/**
 * Angelo: give the depsgraphs taken from the previous Main back to `bmain`, and make their
 * evaluated copies follow the IDs they copy.
 *
 * While the depsgraphs are taken out, nothing tags them: #DEG_id_tag_update finds no graph. Yet
 * the read changes IDs in place or frees them, and the hooks around it remap or remove IDs. An
 * evaluated copy made before keeps pointing where its original pointed then (a ToolSettings
 * brush or palette, an object's data), and the next relations build walks those pointers
 * (#DepsgraphNodeBuilder::update_invalid_cow_pointers skips only copies tagged for a new copy).
 *
 * - After a complete read (`resync_all` false), the read's own tags say which IDs changed:
 *   Blender's loop below tags those and the IDs using them.
 * - After a decode that failed, its Main installed or not (`resync_all` true), nothing says what
 *   changed: every evaluated copy is copied again. A failed read must never leave a graph
 *   pointing at IDs it freed, whatever IDs it changed.
 *
 * Either way the read's temporary tags are cleared, also when no depsgraphs were taken: a read
 * that does not reuse the old Main's data (`use_old_bmain_data` false, e.g. a step with an asset
 * adjunct) still moves the 'never undo' IDs over and tags them (#read_undo_reuse_noundo_local_ids),
 * and every way out of a decode that read comes through here. Left in place, the next read of a
 * library would find them and take them for IDs this undo reused (`lib_link_all`).
 */
void memfile_undo_depsgraphs_give_back(Main *bmain, GHash *depsgraphs, const bool resync_all)
{
  if (depsgraphs == nullptr) {
    ID *id;
    FOREACH_MAIN_ID_BEGIN (bmain, id) {
      id->tag &= ~(ID_TAG_UNDO_OLD_ID_REUSED_UNCHANGED | ID_TAG_UNDO_OLD_ID_REUSED_NOUNDO |
                   ID_TAG_UNDO_OLD_ID_REREAD_IN_PLACE);
    }
    FOREACH_MAIN_ID_END;
    return;
  }
  /* Restore previous depsgraphs into current bmain. */
  BKE_scene_undo_depsgraphs_restore(bmain, depsgraphs);

  /* We need to inform depsgraph about re-used old IDs that would be using newly read
   * data-blocks, at least evaluated copies need to be updated... */
  ID *id = nullptr;
  FOREACH_MAIN_ID_BEGIN (bmain, id) {
    if (id->tag & ID_TAG_UNDO_OLD_ID_REUSED_UNCHANGED) {
      BKE_library_foreach_ID_link(
          bmain, id, memfile_undosys_step_id_reused_cb, nullptr, IDWALK_READONLY);
    }

    if (id->id_type() == ID_SCE) {
      Scene *scene = reinterpret_cast<Scene *>(id);
      /* TODO: We should be able to restore these depsgraphs properly as part of
       * #BKE_scene_undo_depsgraphs_restore but this is currently only done for depsgraphs in the
       * scene.depsgraph_hash map. So the safest option is to just delete the following
       * depsgraphs for now. */
      if (bke::compositor::is_enabled(*scene, bke::compositor::ExecutionMode::Preview)) {
        /* Ensure undo calls from the UI update the interactive compositor preview depsgraph, see
         * #compo_initjob. */
        bke::CompositorRuntime &compositor_runtime = scene->runtime->compositor;
        DEG_graph_free(compositor_runtime.preview_depsgraph);
        compositor_runtime.preview_depsgraph = nullptr;
      }

      if (scene->runtime->sequencer.depsgraph) {
        /* Ensure that the depsgraph created in #get_depsgraph_for_scene_strip are updated. */
        bke::SequencerRuntime &seq_runtime = scene->runtime->sequencer;
        DEG_graph_free(seq_runtime.depsgraph);
        seq_runtime.depsgraph = nullptr;
      }
    }

    /* NOTE: Tagging `ID_RECALC_SYNC_TO_EVAL` here should not be needed in practice, since
     * modified IDs should already have other depsgraph update tags anyway.
     * However, for the sake of consistency, it's better to effectively use it,
     * since content of that ID pointer does have been modified. */
    const bool sync_to_eval = resync_all || (id->tag & ID_TAG_UNDO_OLD_ID_REREAD_IN_PLACE);
    uint recalc_flags = id->recalc | (sync_to_eval ? ID_RECALC_SYNC_TO_EVAL : IDRecalcFlag(0));
    /* Tag depsgraph to update data-block for changes that happened between the
     * current and the target state, see direct_link_id_restore_recalc(). */
    if (recalc_flags != 0) {
      DEG_id_tag_update_ex(bmain, id, recalc_flags);
    }

    bNodeTree *nodetree = bke::node_tree_from_id(id);
    if (nodetree != nullptr) {
      recalc_flags = nodetree->id.recalc;
      if (sync_to_eval) {
        recalc_flags |= ID_RECALC_SYNC_TO_EVAL;
      }
      if (recalc_flags != 0) {
        DEG_id_tag_update_ex(bmain, &nodetree->id, recalc_flags);
      }
    }
    if (id->id_type() == ID_SCE) {
      Scene *scene = id_cast<Scene *>(id);
      if (scene->master_collection != nullptr) {
        recalc_flags = scene->master_collection->id.recalc;
        if (sync_to_eval) {
          recalc_flags |= ID_RECALC_SYNC_TO_EVAL;
        }
        if (recalc_flags != 0) {
          DEG_id_tag_update_ex(bmain, &scene->master_collection->id, recalc_flags);
        }
      }
    }
  }
  FOREACH_MAIN_ID_END;

  FOREACH_MAIN_ID_BEGIN (bmain, id) {
    /* Clear temporary tag. */
    id->tag &= ~(ID_TAG_UNDO_OLD_ID_REUSED_UNCHANGED | ID_TAG_UNDO_OLD_ID_REUSED_NOUNDO |
                 ID_TAG_UNDO_OLD_ID_REREAD_IN_PLACE);

    /* We only start accumulating from this point, any tags set up to here
     * are already part of the current undo state. This is done in a second
     * loop because DEG_id_tag_update may set tags on other datablocks.
     * Angelo: not after a failed decode, whose Main is no undo state to accumulate from. */
    if (!resync_all) {
      id->recalc_after_undo_push = 0;
      bNodeTree *nodetree = bke::node_tree_from_id(id);
      if (nodetree != nullptr) {
        nodetree->id.recalc_after_undo_push = 0;
      }
      if (id->id_type() == ID_SCE) {
        Scene *scene = id_cast<Scene *>(id);
        if (scene->master_collection != nullptr) {
          scene->master_collection->id.recalc_after_undo_push = 0;
        }
      }
    }
  }
  FOREACH_MAIN_ID_END;
}

static UndoDecodeResult memfile_undosys_step_decode(
    bContext *C, Main *bmain, UndoStep *us_p, const eUndoStepDir undo_direction, bool /*is_final*/)
{
  BLI_assert(undo_direction != STEP_INVALID);
  MemFileUndoStep *us = reinterpret_cast<MemFileUndoStep *>(us_p);
#ifdef WITH_GTESTS
  if (us->angelo_sculpt_state) {
    ed::sculpt_paint::undo::main_undo_state_test_fault_begin_decode(
        *us->angelo_sculpt_state, bmain, CTX_wm_manager(C)->runtime->undo_stack);
  }
#endif
  const char *admission_error = nullptr;
  if (!BKE_sculpt_undo_runtime_preflight(*bmain, &admission_error)) {
    BKE_report(CTX_wm_reports(C), RPT_WARNING, admission_error);
    return UndoDecodeResult::Refused;
  }

  UndoStack *stack = CTX_wm_manager(C)->runtime->undo_stack;
  using SculptState = ed::sculpt_paint::undo::MainUndoState;
  std::unique_ptr<SculptState, decltype(&ed::sculpt_paint::undo::main_undo_state_free)> state_pin(
      ed::sculpt_paint::undo::main_undo_state_retain(us->angelo_sculpt_state),
      ed::sculpt_paint::undo::main_undo_state_free);
  if ((ed::sculpt_paint::undo::main_undo_state_has_grids(state_pin.get()) ||
       us->angelo_asset_noundo_delta) &&
      !BKE_undosys_restore_activate(stack, bmain, memfile_cancel_intermediate_previews))
  {
    BKE_report(CTX_wm_reports(C), RPT_WARNING, BKE_undosys_restore_refusal_message());
    return UndoDecodeResult::Refused;
  }
  const UndoStep *active_memfile = stack ? stack->step_active_memfile : nullptr;
  const bool from_sculpt_snapshot =
      active_memfile && active_memfile->type == BKE_UNDOSYS_TYPE_MEMFILE &&
      reinterpret_cast<const MemFileUndoStep *>(active_memfile)->angelo_sculpt_state != nullptr;
  if ((us->angelo_sculpt_state || from_sculpt_snapshot) &&
      !ed::sculpt_paint::undo::main_undo_state_suppress_old_grid_writeback(*bmain,
                                                                           &admission_error))
  {
    BKE_report(CTX_wm_reports(C), RPT_WARNING, admission_error);
    return UndoDecodeResult::Refused;
  }

  /* ImagePool releases can dereference original Image IDs. Dispose of stroke texture pools
   * while those IDs are still alive, including the raw-reader failure path. */
  for (Object &object : bmain->objects) {
    if (SculptSession *ss = object.runtime->sculpt_session) {
      if (ss->tex_pool) {
        BKE_image_pool_free(ss->tex_pool);
        ss->tex_pool = nullptr;
      }
    }
  }

  bool use_old_bmain_data = true;

  if (USER_DEVELOPER_TOOL_TEST(&U, use_undo_legacy) || !(U.uiflag & USER_GLOBALUNDO)) {
    use_old_bmain_data = false;
  }
  else if (undo_direction == STEP_REDO) {
    /* The only time we should have to force a complete redo is when current step is tagged as a
     * redo barrier.
     * If previous step was not a memfile one should not matter here, current data in old bmain
     * should still always be valid for unchanged data-blocks. */
    if (us_p->use_old_bmain_data == false) {
      use_old_bmain_data = false;
    }
  }
  else if (undo_direction == STEP_UNDO) {
    /* Here we do not care whether current step is an undo barrier, since we are coming from
     * 'the future' we can still re-use old data. However, if *next* undo step
     * (i.e. the one immediately in the future, the one we are coming from)
     * is a barrier, then we have to force a complete undo.
     * Note that non-memfile undo steps **should** not be an issue anymore, since we handle
     * fine-grained update flags now.
     */
    UndoStep *us_next = us_p->next;
    if (us_next != nullptr) {
      if (us_next->use_old_bmain_data == false) {
        use_old_bmain_data = false;
      }
    }
  }

  /* The adjunct can remove/recreate IDs outside the memfile. Do not carry extracted dependency
   * graphs across that change: their cached relations may still borrow a removed dependency.
   * Blender's normal image/cache transfer remains part of the checked memfile read. */
  if (us->angelo_asset_noundo_delta) {
    use_old_bmain_data = false;
  }

  if (BKE_undosys_restore_protects_stack(stack)) {
    BKE_undosys_restore_before_memfile_read(stack);
    memfile_cancel_intermediate_previews(bmain);
  }

  ed::sculpt_paint::undo::MainUndoBMeshRead *bmesh_read = nullptr;
  /* Angelo: every read while a Dyntopo BMesh is live, tagged step or not. Since Blender 5.3
   * (9407047c635, "Use undo cache management for SculptSession") the ID cache moves the session,
   * live BMesh included, onto an Object the read restores at its old address; 5.2 gave such an
   * Object a fresh runtime with no session. Unprotected, a read into an untagged step would leave
   * that BMesh (the state before the read) over the restored Mesh. Protected, a reused Object
   * keeps its BMesh and one read again retires it, as on 5.2 (#main_undo_bmesh_read_end). */
  const bool bmesh_protected = us->angelo_sculpt_state || from_sculpt_snapshot ||
                               ed::sculpt_paint::undo::main_undo_bmesh_any(*bmain);
  if (bmesh_protected) {
    /* A read with no Angelo Sculpt snapshot on either side keeps Blender's own behaviour for an
     * Object read again: its live edits flushed into the restored Mesh (see the reader). */
    const bool keep_read_again_edits = !us->angelo_sculpt_state && !from_sculpt_snapshot;
    bmesh_read = ed::sculpt_paint::undo::main_undo_bmesh_read_begin(
        *bmain, &admission_error, keep_read_again_edits);
    if (!bmesh_read) {
      BKE_report(CTX_wm_reports(C), RPT_WARNING, admission_error);
      return UndoDecodeResult::Refused;
    }
  }

  /* Extract depsgraphs from current bmain (which may be freed during undo step reading),
   * and store them for re-use. */
  GHash *depsgraphs = nullptr;
  if (use_old_bmain_data) {
    depsgraphs = BKE_scene_undo_depsgraphs_extract(bmain);
  }

  ED_editors_exit(bmain, false);
  /* Ensure there's no preview job running. Unfinished previews will be scheduled for regeneration
   * via #PRV_TAG_RESTART_RENDERING in BKE_previewimg_blend_read. */
  ED_preview_kill_jobs_for_undo(CTX_wm_manager(C), bmain);
  const bool need_preview_render_restart = bmain->need_preview_render_restart;

  const bool brush_before_ok = !us->angelo_asset_noundo_delta ||
      ed::undo::asset::asset_noundo_delta_before_decode(
          *us->angelo_asset_noundo_delta, *bmain, us->angelo_asset_noundo_side, &admission_error);
  /* A Brush hook can have changed Main before refusing. Use the same checked failure cleanup
   * as a failed decoder; never report this as an unchanged refusal. */
  const bool read_ok = brush_before_ok &&
      BKE_memfile_undo_decode(us->data, undo_direction, use_old_bmain_data, C);
  const char *bmesh_error = nullptr;
  const bool bmesh_ok = !bmesh_read || ed::sculpt_paint::undo::main_undo_bmesh_read_end(
                                         *CTX_data_main(C), bmesh_read, &bmesh_error);
  if (!read_ok || !bmesh_ok) {
    /* Reading can modify the old Main even when no replacement is installed. Own the extracted
     * depsgraphs again, discard borrowed Sculpt runtime, and stop; this is not a rollback. */
    bmain = CTX_data_main(C);
    if (read_ok) {
      /* Angelo: the Main installed is this memfile: record from it as after a successful read. */
      BKE_main_memfile_undo_synced(bmain);
    }
    if (BKE_undosys_restore_protects_stack(stack)) {
      BKE_undosys_restore_rebind_main(stack, bmain);
    }
    memfile_undo_depsgraphs_give_back(bmain, depsgraphs, true);
    const char *cleanup_error = nullptr;
    if (!ed::sculpt_paint::undo::main_undo_state_suppress_old_grid_writeback(*bmain,
                                                                             &cleanup_error))
    {
      BKE_report(CTX_wm_reports(C), RPT_ERROR, cleanup_error);
    }
    cleanup_error = nullptr;
    if (!ed::sculpt_paint::undo::main_undo_state_discard_runtime(*bmain, &cleanup_error)) {
      BKE_report(CTX_wm_reports(C), RPT_ERROR, cleanup_error);
    }
    BKE_report(
        CTX_wm_reports(C),
        RPT_ERROR,
        admission_error ? admission_error : bmesh_error ? bmesh_error :
                      "Undo could not read the document; recover a known-valid saved file before "
                      "continuing");
    return read_ok ? UndoDecodeResult::FailedStateInstalled : UndoDecodeResult::FailedNoMainInstalled;
  }

  for (UndoStep *us_iter = us_p->next; us_iter; us_iter = us_iter->next) {
    if (BKE_UNDOSYS_TYPE_IS_MEMFILE_SKIP(us_iter->type)) {
      continue;
    }
    us_iter->is_applied = false;
  }
  for (UndoStep *us_iter = us_p; us_iter; us_iter = us_iter->prev) {
    if (BKE_UNDOSYS_TYPE_IS_MEMFILE_SKIP(us_iter->type)) {
      continue;
    }
    us_iter->is_applied = true;
  }

  /* bmain has been freed. */
  bmain = CTX_data_main(C);
  /* Angelo: the Main just read is exactly this memfile, the one the next decode compares against:
   * no change in place yet. Synced here, right after the read: the hooks below (asset adjunct,
   * Sculpt state) change IDs through the usual functions, which record them. Changes made with no
   * step of their own are handled per ID in readfile (`read_undo_ids_to_reread`), never by a
   * whole read. */
  BKE_main_memfile_undo_synced(bmain);
  if (BKE_undosys_restore_protects_stack(stack)) {
    BKE_undosys_restore_rebind_main(stack, bmain);
  }
  if (us->angelo_asset_noundo_delta &&
      !ed::undo::asset::asset_noundo_delta_after_decode(
          *us->angelo_asset_noundo_delta, *bmain, us->angelo_asset_noundo_side, &admission_error))
  {
    memfile_undo_depsgraphs_give_back(bmain, depsgraphs, true);
    if (bmesh_protected) {
      ed::sculpt_paint::undo::main_undo_bmesh_read_drop_orphan_logs(*bmain);
    }
    BKE_report(CTX_wm_reports(C), RPT_ERROR, admission_error);
    return UndoDecodeResult::FailedStateInstalled;
  }
  ED_editors_init_for_undo(bmain);

  /* Cancelled preview jobs set this on the old bmain, carry it over to
   * restart previews that were interrupted. */
  bmain->need_preview_render_restart |= need_preview_render_restart;

  memfile_undo_depsgraphs_give_back(bmain, depsgraphs, false);

  if (state_pin) {
    const char *error = nullptr;
    if (!ed::sculpt_paint::undo::main_undo_state_restore(C, *state_pin, &error)) {
      /* Owner graph preparation can run callbacks. Never clean up through a Main pointer that
       * a callback replaced; the failure remains explicit and does not claim rollback. */
      bmain = CTX_data_main(C);
      const char *cleanup_error = nullptr;
      /* Preparation may have created dirty evaluated caches before a later owner failed.
       * Original-session disposal alone cannot prevent those caches from writing back. */
      if (!ed::sculpt_paint::undo::main_undo_state_suppress_old_grid_writeback(*bmain,
                                                                               &cleanup_error))
      {
        BKE_report(CTX_wm_reports(C), RPT_ERROR, cleanup_error);
      }
      cleanup_error = nullptr;
      if (!ed::sculpt_paint::undo::main_undo_state_discard_runtime(*bmain, &cleanup_error)) {
        BKE_report(CTX_wm_reports(C), RPT_ERROR, cleanup_error);
      }
      BKE_report(
          CTX_wm_reports(C), RPT_ERROR, error ? error : "Sculpt Undo state could not be restored");
      return UndoDecodeResult::FailedStateInstalled;
    }
  }
  if (bmesh_protected) {
    /* After the restore, which adopts the retained history of the logs it needs. */
    ed::sculpt_paint::undo::main_undo_bmesh_read_drop_orphan_logs(*CTX_data_main(C));
  }
  WM_event_add_notifier(C, NC_SCENE | ND_LAYER_CONTENT, CTX_data_scene(C));
  return UndoDecodeResult::Success;
}

static void memfile_undosys_step_free(UndoStep *us_p)
{
  /* To avoid unnecessary slow down, free backwards
   * (so we don't need to merge when clearing all). */
  MemFileUndoStep *us = reinterpret_cast<MemFileUndoStep *>(us_p);
  ed::sculpt_paint::undo::main_undo_state_free(us->angelo_sculpt_state);
  ed::undo::asset::asset_noundo_delta_free(us->angelo_asset_noundo_delta);
  us->angelo_asset_noundo_delta = nullptr;
  if (us_p->next != nullptr) {
    UndoStep *us_next_p = BKE_undosys_step_same_type_next(us_p);
    if (us_next_p != nullptr) {
      MemFileUndoStep *us_next = reinterpret_cast<MemFileUndoStep *>(us_next_p);
      BLO_memfile_merge(&us->data->memfile, &us_next->data->memfile);
    }
  }

  BKE_memfile_undo_free(us->data);
}

void ED_memfile_undosys_type(UndoType *ut)
{
  ut->identifier = "GLOBAL_UNDO";
  ut->poll = memfile_undosys_poll;
  ut->step_encode = memfile_undosys_step_encode;
  ut->step_decode_checked = memfile_undosys_step_decode;
  ut->step_free = memfile_undosys_step_free;

  ut->flags = 0;

  ut->step_size = sizeof(MemFileUndoStep);
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Utilities
 * \{ */
bool ED_undosys_autosave_compatible(UndoStack *ustack)
{
  if (!ustack->step_active) {
    return false;
  }

  return ELEM(ustack->step_active->type, BKE_UNDOSYS_TYPE_MEMFILE, BKE_UNDOSYS_TYPE_IMAGE);
}

void ED_undosys_stack_memfile_id_changed_tag(UndoStack *ustack, ID *id)
{
  UndoStep *us = ustack->step_active;
  if (id == nullptr || us == nullptr || us->type != BKE_UNDOSYS_TYPE_MEMFILE) {
    return;
  }

  MemFile *memfile = &(reinterpret_cast<MemFileUndoStep *>(us))->data->memfile;
  for (MemFileChunk &mem_chunk : memfile->chunks) {
    if (mem_chunk.id_session_uid == id->session_uid) {
      mem_chunk.is_identical_future = false;
      break;
    }
  }
}

/** \} */

}  // namespace blender
