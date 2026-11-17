# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""When the surfaces of Generation Flows are redrawn, and what a refresh compares to decide (``flow_host.pump``).

The pump evaluates every flow again each ``flow_host.IDLE_REFRESH_SECONDS``, for changes nobody announces. That is a
read. Its surfaces (the Flows editors, and the chat and Media Studio areas, which show its jobs, runs and accounts)
are redrawn when what they show changed, and never for a refresh that found the same: redrawn at every refresh, an
idle chat was drawn every 2 s for as long as the app ran, each draw taking about 130 MB of graphics memory that Metal
released a second later (an-blender53 on Mac 1, 2026-11-16; ``test_blender_idle_redraw``).
"""

import bpy

from . import flow_nodes, live_windows


def shown(entry):
    """What a cached evaluation shows: everything but when it was made. Two evaluations that show the same compare
    equal (plain data throughout), so a refresh that found nothing new asks for no redraw."""
    return None if entry is None else {key: value for key, value in entry.items() if key != "at"}


def redraw():
    """Ask every Generation Flows editor, and every chat and Media Studio area, to draw again."""
    if bpy.app.background:
        return
    for _window, area in live_windows.window_areas(bpy.context.window_manager):
        if area.type == 'NODE_EDITOR' and getattr(area.spaces.active, "tree_type", "") == flow_nodes.TREE:
            area.tag_redraw()
        elif area.type == 'CONSOLE':
            area.tag_redraw()
