/* SPDX-FileCopyrightText: 2023 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup edinterface
 *
 * Share between interface_region_*.cc files.
 */

#pragma once

#include "BLI_string_ref.hh"

namespace blender {

struct ARegion;
struct bContext;
struct bScreen;

namespace ui {

struct AngeloTooltipOwn;

/* interface_region_menu_popup.cc */

uint popup_menu_hash(StringRef str);

/* interface_regions.cc */

ARegion *region_temp_add(bScreen *screen);
/** What the region covered is on screen again from now (`ui_angelo_note_uncovered`). */
void region_temp_remove(bContext *C, bScreen *screen, ARegion *region);
/** Angelo: as `region_temp_remove` for a tooltip, whose close never restarts its own control while
 * it is unchanged since the tooltip opened (`own`; only compared, never read). */
void region_temp_remove_tooltip(bContext *C,
                                bScreen *screen,
                                ARegion *region,
                                const AngeloTooltipOwn &own);

}  // namespace ui
}  // namespace blender
