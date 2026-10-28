# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a Media Studio or Generation Flows tab shows (``studio_workspaces``): one editor, filling the area."""

from . import live_windows


def fill(window, kind):
    """The tab's one editor, without Blender's header, sidebar or toolbar: Media Studio, or the flow canvas."""
    from . import flow_nodes
    from .editor_types import mark_angelo_owned, select_console, set_editor
    area = live_windows.areas(window)[0]
    if kind == "studio":
        select_console(window, area, generative=True)
    else:
        set_editor(window, area, ui_type=flow_nodes.TREE)
    mark_angelo_owned(window, area)
    from . import chrome_guard
    chrome_guard.guard(area, window)   # Hidden now and kept hidden by events (no polling).
    if kind == "flows":
        from . import flow_window
        flow_window.prepare_area(window, area)
