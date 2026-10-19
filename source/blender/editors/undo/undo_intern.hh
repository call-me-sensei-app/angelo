/* SPDX-FileCopyrightText: 2023 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup edundo
 */

#pragma once

#include "BKE_undo_system.hh"

namespace blender {

/* internal exports only */

struct UndoType;
struct MemFileUndoData;
namespace ed::sculpt_paint::undo {
struct MainUndoState;
}

struct MemFileUndoStep {
  UndoStep step;
  MemFileUndoData *data;
  ed::sculpt_paint::undo::MainUndoState *angelo_sculpt_state;
};

/* memfile_undo.cc */

/** Export for ED_undo_sys. */
void ED_memfile_undosys_type(UndoType *ut);

}  // namespace blender
