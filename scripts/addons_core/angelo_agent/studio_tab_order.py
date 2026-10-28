# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The order of Manual's tab strip: Director · Media Studio · Generation Flows first (``studio_workspaces``)."""

import bpy


def strip():
    """The workspaces as the tab strip shows them, or None on a build without ``WorkSpace.angelo_tab_order``.
    ``bpy.data.workspaces`` is always alphabetical; Blender's tabs sort by the workspace's order, then by name."""
    workspaces = list(bpy.data.workspaces)
    if workspaces and not hasattr(workspaces[0], "angelo_tab_order"):
        return None
    return sorted(workspaces, key=lambda w: (w.angelo_tab_order, w.name))


def in_order():
    """Whether the strip reads Director · Media Studio · Generation Flows first (studio_entry_points.rst §3), the
    pair never split by another tab; a tab the file lacks is skipped. False when the order cannot be read
    (``place`` is idempotent)."""
    from . import studio_workspaces as tabs
    from .director_layout import is_director_workspace
    shown = strip()
    if shown is None:
        return False
    names = [w.name for w in shown]
    wanted = [w.name for w in bpy.data.workspaces if is_director_workspace(w)][:1] + [
        t.name for t in (tabs.tab("studio"), tabs.tab("flows")) if t is not None]
    return names[:len(wanted)] == wanted


def place():
    """Put Media Studio right after Director (first without one) and Generation Flows right after it, without
    switching the window (``WorkSpace.angelo_move_after``, 1.0.5 native). Switching to Director to move it, as
    ``workspace.reorder_to_front`` needs, enters Director mode in Manual and left the pair split ("Media Studio ·
    Director · Generation Flows", 1.0.5 review). Returns False on a build without the call."""
    from . import studio_workspaces as tabs
    from .director_layout import is_director_workspace
    studio, flows = tabs.tab("studio"), tabs.tab("flows")
    first = studio or flows
    if first is None:
        return True
    if not hasattr(first, "angelo_move_after"):
        return False
    director = next((w for w in bpy.data.workspaces if is_director_workspace(w)), None)
    if director is not None:
        director.angelo_move_after(anchor=None)
    anchor = director
    for workspace in (studio, flows):
        if workspace is not None:
            workspace.angelo_move_after(anchor=anchor)
            anchor = workspace
    return True
