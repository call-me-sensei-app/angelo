/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Angelo: the region a pointer event reaches, as the grant guard's input feed asks it (`wm_event_pointer_region`; the
 * review of f6b01a9..da9ba66, SHOULD 1; the maintainer: "Fix properly in 2.1.0"). Blender's own handler loop takes
 * the same two steps (`wm_event_area_takes_xy`, `wm_event_area_region_xy`), so these pin what both see: a temporary
 * region on top first; an area's overlapping regions only where they are opaque (a sidebar's panels, a transparent
 * header's buttons, the HUD, the asset shelf's content), the region beneath elsewhere; with region overlap off, the
 * sidebar as its own region; another window's regions never; and its cost per event.
 */

#include "testing/testing.h"

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdio>
#include <memory>
#include <vector>

#include "BLI_listbase.hh"

#include "BKE_screen.hh"

#include "DNA_screen_types.h"
#include "DNA_userdef_types.h"
#include "DNA_view2d_types.h"
#include "DNA_windowmanager_types.h"

#include "MEM_guardedalloc.h"

#include "interface_intern.hh"
#include "wm_angelo_pointer_region.hh"

namespace blender::ui::tests {

/** A 3D view of 800 x 600 at the window's origin, its regions laid out as Blender lays them out. */
class AngeloPointerRegion : public testing::Test {
 protected:
  wmWindow win{};
  bScreen screen{};
  ScrArea area{};
  std::vector<std::unique_ptr<ARegion>> regions;
  Block header_block;
  short widget_unit = 0;

  void SetUp() override
  {
    widget_unit = U.widget_unit;
    U.widget_unit = 20; /* The overlap margin is a third of it. */
    area.totrct = {0, 799, 0, 599};
    BLI_addtail(&screen.areabase, &area);
  }

  void TearDown() override
  {
    for (std::unique_ptr<ARegion> &region : regions) {
      BLI_listbase_clear(&region->runtime->uiblocks); /* The test's own block, never Blender's to free. */
      MEM_delete(region->runtime);
    }
    U.widget_unit = widget_unit;
  }

  /** A region at `rect` (window pixels) of `type`; `overlap` as `ED_region_is_overlap` sets it. Its View2D maps
   * region pixels to view space one to one, so `tot` (its content) is in region pixels. */
  ARegion *add(const eRegion_Type type,
              const rcti rect,
              const bool overlap,
              const eRegion_Alignment alignment,
              ListBaseT<ARegion> *to)
  {
    regions.push_back(std::make_unique<ARegion>());
    ARegion *region = regions.back().get();
    region->runtime = MEM_new<bke::ARegionRuntime>(__func__);
    region->runtime->visible = 1;
    region->regiontype = type;
    region->alignment = alignment;
    region->overlap = overlap;
    region->winrct = rect;
    const int width = BLI_rcti_size_x(&rect), height = BLI_rcti_size_y(&rect);
    region->v2d.mask = {0, width, 0, height};
    region->v2d.cur = {0.0f, float(width), 0.0f, float(height)};
    region->v2d.tot = region->v2d.cur;
    BLI_addtail(to, region);
    return region;
  }

  ARegion *main_region()
  {
    return add(RGN_TYPE_WINDOW, area.totrct, false, RGN_ALIGN_NONE, &area.regionbase);
  }

  /** The N panel over the main region's right 200 pixels; its panels fill its top 150. */
  ARegion *sidebar(const bool overlap = true)
  {
    ARegion *region = add(RGN_TYPE_UI, {600, 799, 0, 599}, overlap, RGN_ALIGN_RIGHT, &area.regionbase);
    region->v2d.tot.ymin = 449.0f;
    return region;
  }

  const ARegion *at(const int x, const int y)
  {
    const int xy[2] = {x, y};
    return wm_event_pointer_region(&win, &screen, xy);
  }
};

TEST_F(AngeloPointerRegion, TheSidebarTakesThePointerOnlyOverItsPanels)
{
  /* Sidebars first (Blender checks overlapping regions before the region beneath). */
  ARegion *ui = sidebar();
  ARegion *main = main_region();
  EXPECT_EQ(at(700, 500), ui) << "over its panels: the sidebar's";
  EXPECT_EQ(at(700, 100), main) << "below its panels (transparent): the 3D view's, as Blender routes it";
  EXPECT_EQ(at(300, 300), main);
}

TEST_F(AngeloPointerRegion, WithRegionOverlapOffTheSidebarIsItsOwnRegion)
{
  ARegion *main = add(RGN_TYPE_WINDOW, {0, 599, 0, 599}, false, RGN_ALIGN_NONE, &area.regionbase);
  ARegion *ui = sidebar(false);
  EXPECT_EQ(at(700, 100), ui) << "no overlap: all of it is the sidebar's, panels or not";
  EXPECT_EQ(at(300, 100), main);
}

TEST_F(AngeloPointerRegion, ATransparentHeaderTakesThePointerOnlyOnItsButtons)
{
  ARegion *header = add(RGN_TYPE_HEADER, {0, 799, 574, 599}, true, RGN_ALIGN_TOP, &area.regionbase);
  header->v2d.keeptot = V2D_KEEPTOT_STRICT;
  ARegion *main = main_region();
  EXPECT_EQ(at(100, 586), main) << "no button there: the 3D view's";
  /* A button at header pixels x 90..129: the block maps region pixels to itself. */
  header_block.winmat[0][0] = 2.0f / 800.0f;
  header_block.winmat[1][1] = 2.0f / 26.0f;
  header_block.winmat[3][0] = -1.0f;
  header_block.winmat[3][1] = -1.0f;
  header_block.winmat[2][2] = 1.0f;
  header_block.winmat[3][3] = 1.0f;
  header_block.buttons_ptrs.append(std::make_unique<Button>());
  Button &button = *header_block.buttons_ptrs.last();
  button.type = ButtonType::But;
  button.rect = {90.0f, 129.0f, 2.0f, 22.0f};
  BLI_addtail(&header->runtime->uiblocks, &header_block);
  EXPECT_EQ(at(100, 586), header) << "on its button: the header's";
  EXPECT_EQ(at(300, 586), main) << "beside it: still the 3D view's";
}

TEST_F(AngeloPointerRegion, TheHudAndTheAssetShelfTakeThePointerOverTheirContent)
{
  ARegion *hud = add(RGN_TYPE_HUD, {10, 209, 10, 109}, true, RGN_ALIGN_FLOAT, &area.regionbase);
  ARegion *shelf = add(RGN_TYPE_ASSET_SHELF, {220, 799, 0, 99}, true, RGN_ALIGN_BOTTOM, &area.regionbase);
  shelf->v2d.tot.xmax = 300.0f; /* Its tiles fill its left 300 pixels. */
  ARegion *main = main_region();
  EXPECT_EQ(at(100, 50), hud);
  EXPECT_EQ(at(300, 50), shelf) << "over its tiles";
  EXPECT_EQ(at(700, 50), main) << "past them (transparent)";
}

TEST_F(AngeloPointerRegion, ATemporaryRegionOnTopTakesThePointerFirst)
{
  ARegion *main = main_region();
  ARegion *menu = add(RGN_TYPE_TEMPORARY, {100, 299, 100, 299}, false, RGN_ALIGN_NONE, &screen.regionbase);
  ARegion *submenu = add(RGN_TYPE_TEMPORARY, {250, 449, 150, 349}, false, RGN_ALIGN_NONE, &screen.regionbase);
  EXPECT_EQ(at(150, 150), menu);
  EXPECT_EQ(at(275, 200), submenu) << "the one drawn last is on top";
  EXPECT_EQ(at(600, 500), main);
  submenu->runtime->visible = 0;
  EXPECT_EQ(at(275, 200), menu) << "one not drawn takes nothing";
}

TEST_F(AngeloPointerRegion, AnotherWindowsRegionsAreNeverThisOnes)
{
  ARegion *main = main_region();
  bScreen other{};
  ScrArea other_area{};
  other_area.totrct = {0, 799, 0, 599};
  BLI_addtail(&other.areabase, &other_area);
  regions.push_back(std::make_unique<ARegion>());
  ARegion *other_main = regions.back().get();
  other_main->runtime = MEM_new<bke::ARegionRuntime>(__func__);
  other_main->runtime->visible = 1;
  other_main->winrct = other_area.totrct;
  BLI_addtail(&other_area.regionbase, other_main);
  wmWindow other_win{};
  const int xy[2] = {300, 300};
  EXPECT_EQ(wm_event_pointer_region(&other_win, &other, xy), other_main);
  EXPECT_EQ(at(300, 300), main);
}

TEST_F(AngeloPointerRegion, BetweenAreasAndOutsideNothingTakesIt)
{
  main_region();
  EXPECT_EQ(at(900, 300), nullptr);
  EXPECT_EQ(at(-5, 300), nullptr);
  EXPECT_FALSE(wm_event_area_takes_xy(&win, &area, std::array<int, 2>{900, 300}.data()));
}

TEST_F(AngeloPointerRegion, CostPerEvent)
{
  /* The latency rule: the feed asks this for every move and press. A 3D view with a toolbar, header, tool header,
   * sidebar, asset shelf and HUD, a popup open elsewhere, the pointer over the main region (the longest path). */
  add(RGN_TYPE_TOOLS, {0, 59, 0, 599}, true, RGN_ALIGN_LEFT, &area.regionbase)->v2d.tot.ymin = 400.0f;
  add(RGN_TYPE_HEADER, {0, 799, 574, 599}, true, RGN_ALIGN_TOP, &area.regionbase)->v2d.keeptot = V2D_KEEPTOT_STRICT;
  add(RGN_TYPE_TOOL_HEADER, {0, 799, 548, 573}, true, RGN_ALIGN_TOP, &area.regionbase)->v2d.keeptot =
      V2D_KEEPTOT_STRICT;
  sidebar();
  add(RGN_TYPE_ASSET_SHELF, {60, 599, 0, 99}, true, RGN_ALIGN_BOTTOM, &area.regionbase)->v2d.tot.xmax = 200.0f;
  add(RGN_TYPE_HUD, {10, 209, 110, 209}, true, RGN_ALIGN_FLOAT, &area.regionbase);
  ARegion *main = main_region();
  add(RGN_TYPE_TEMPORARY, {700, 799, 0, 50}, false, RGN_ALIGN_NONE, &screen.regionbase);
  std::vector<double> samples;
  for (int batch = 0; batch < 41; batch++) {
    const auto start = std::chrono::steady_clock::now();
    for (int i = 0; i < 20000; i++) {
      const int xy[2] = {300 + (i & 63), 300 + (i & 31)};
      if (wm_event_pointer_region(&win, &screen, xy) != main) {
        FAIL() << "routed elsewhere";
      }
    }
    const std::chrono::duration<double, std::nano> took = std::chrono::steady_clock::now() - start;
    samples.push_back(took.count() / 20000);
  }
  std::sort(samples.begin(), samples.end());
  printf("ANGELO_ROUTING_COST p50 %.1f ns p75 %.1f ns\n", samples[samples.size() / 2],
         samples[samples.size() * 3 / 4]);
  EXPECT_LT(samples[samples.size() / 2], 2000.0) << "well under a microsecond is expected";
}

}  // namespace blender::ui::tests
