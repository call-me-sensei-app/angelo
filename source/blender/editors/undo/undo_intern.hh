/* SPDX-FileCopyrightText: 2023 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup edundo
 */

#pragma once

#include "BKE_undo_system.hh"

#include "angelo_asset_noundo_delta.hh"

namespace blender {

/* internal exports only */

struct GHash;
struct Main;
struct UndoType;
struct MemFileUndoData;
namespace ed::sculpt_paint::undo {
struct MainUndoState;
}

struct MemFileUndoStep {
  UndoStep step;
  MemFileUndoData *data;
  ed::sculpt_paint::undo::MainUndoState *angelo_sculpt_state;
  ed::undo::asset::AssetNoUndoDelta *angelo_asset_noundo_delta;
  ed::undo::asset::AssetNoUndoSide angelo_asset_noundo_side;
};

/* memfile_undo.cc */

/** Export for ED_undo_sys. */
void ED_memfile_undosys_type(UndoType *ut);

/**
 * Give the depsgraphs a memfile read took from the previous Main back to `bmain`, their evaluated
 * copies following the IDs they copy: after a complete read the IDs it changed, after a failed
 * decode (`resync_all`) every ID. Clears the read's temporary ID tags. Null `depsgraphs`: nothing.
 */
void memfile_undo_depsgraphs_give_back(Main *bmain, GHash *depsgraphs, bool resync_all);

}  // namespace blender
