# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scroll a View2D region (the Preferences page, a list) by a distance the suite chooses.

``view2d.scroll_up`` / ``scroll_down`` / ``scroll_left`` / ``scroll_right`` overwrite any ``deltax`` / ``deltay``
they are given with one wheel step (40 pixels times the UI scale) or, with ``page=True``, one page
(``view_scrolldown_exec`` in view2d_ops.cc). A distance passed to them is silently ignored: the cut's GUI round on
3fc1f94df29 had external_mcp ask for 6000 and then "the whole distance" and move 40 pixels a call, twenty-one calls,
so the MCP app row stayed 1583 pixels below the view. ``view2d.pan`` applies its deltas as given, clamped to the
region's content like any scroll. test_native_view2d_scroll_args keeps every suite on this module.
"""

import bpy

TOP = 10 ** 7   # Further than any page: the view stops at the content's edge.


def _pan(window, area, region, deltay):
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.view2d.pan(deltax=0, deltay=int(deltay))


def down(window, area, region, pixels):
    """Move the view ``pixels`` further down the content. Region y grows upward, so content below the view has a
    negative y: ``down(..., -y)`` brings its top edge to the view's bottom."""
    _pan(window, area, region, -abs(pixels))


def up(window, area, region, pixels=TOP):
    """Move the view ``pixels`` back up the content; with no distance, to the top."""
    _pan(window, area, region, abs(pixels))
