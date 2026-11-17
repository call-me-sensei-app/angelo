# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which workspaces are Angelo's half-made tabs, and removing one without changing any window's tab or the active
object's mode (``studio_workspaces``, ``director_layout``).

Evidence (an-blender53 on Mac 1, 2026-11-16, 758b07e8b77: a sculpt file reopened with Load UI off while the tab build
ran): Blender's Duplicate Workspace copies a workspace's properties, so Director's workspace, copied from the window's
half-made Media Studio tab, carried that tab's markers; the next build took it for a half-made tab and deleted it
under the window. Blender moves a window showing a deleted workspace to its neighbour in the strip with the
neighbour's object mode (``ED_workspace_delete``): the sculpt went into Edit Mode, and the finished tabs built next
took Edit Mode from there. So:

* a copy Angelo makes drops every marker its source carried before it gets its own (``drop_copied``);
* a half-made tab is only one a tab build made and named (``half_made``); any other workspace carrying the marker
  (a copy) loses it and is kept (``stray``);
* no window shows a tab when it is deleted (``remove``).

Delete Workspace deletes the context's workspace, which is the window's own: ``temp_override(workspace=...)`` does
not change it (probed on the 2.0.0 build: the context still read the window's workspace, and the delete removed it;
the evidence's stock Layout tab went so, with "the half-made tab was not removed" in the log). So a tab is deleted
only through a window that shows it, in the same pass that switch queues that window's way out.
"""

import bpy

from . import live_windows
from .legacy_names import MANUAL_WORKSPACE, WORKSPACE_DIRECTOR

TAB_KEY = "angelo_tab"                # As studio_workspaces': which destination a tab is.
UNFINISHED = "angelo_tab_unfinished"  # On a tab a build is still making; its value is the build's home (or True).
TABS = {"studio": "Media Studio", "flows": "Generation Flows"}   # i18n: workspace data names, never translated
COPIED = (TAB_KEY, UNFINISHED) + WORKSPACE_DIRECTOR + MANUAL_WORKSPACE   # What makes a workspace one of Angelo's.


def drop_copied(workspace):
    """A copy Angelo just made: none of its source's markers (it is not that tab, nor Director)."""
    for key in COPIED:
        if key in workspace:
            del workspace[key]


def _director(workspace):
    return any(key in workspace for key in WORKSPACE_DIRECTOR)


def half_made(workspace):
    """A tab a build made and has not finished: marked, of a known kind, by its tab's own name, never Director's."""
    kind = workspace.get(TAB_KEY)
    return UNFINISHED in workspace and kind in TABS and workspace.name == TABS[kind] and not _director(workspace)


def stray(workspace):
    """Carries a tab's marker it was never given (a copy of a tab): it loses them and is kept, never deleted."""
    return (UNFINISHED in workspace and not half_made(workspace)) or (TAB_KEY in workspace and _director(workspace))


def clear_strays():
    """Drop the tab markers every ``stray`` workspace carries. Returns their names."""
    names = []
    for workspace in bpy.data.workspaces:
        if stray(workspace):
            names.append(workspace.name)
            for key in (TAB_KEY, UNFINISHED):
                if key in workspace:
                    del workspace[key]
    return names


def home_of(workspace, home=None):
    """Where a window showing the half-made ``workspace`` goes before it is deleted: ``home`` (the build's), else the
    home its build recorded on it, else an ordinary workspace. Never a half-made one."""
    recorded = workspace.get(UNFINISHED)
    for name in (home, recorded if isinstance(recorded, str) else None):
        candidate = bpy.data.workspaces.get(name) if name else None
        if candidate is not None and candidate != workspace and UNFINISHED not in candidate:
            return candidate
    return next((w for w in bpy.data.workspaces
                 if w != workspace and TAB_KEY not in w and UNFINISHED not in w), None)


def _object_mode(window, workspace):
    """The mode the window's active object is in, else ``workspace``'s. A switch changes the object's mode only when
    the two workspaces' modes differ, and then to the new one's: given the object's own mode, the workspace the window
    goes to changes nothing, even when a load left the shown workspace's mode apart from the object's."""
    view_layer = getattr(window, "view_layer", None)
    active = getattr(getattr(view_layer, "objects", None), "active", None)
    return getattr(active, "mode", None) or workspace.object_mode


def remove(window, workspace, home=None, kept_modes=None):
    """Delete the half-made tab ``workspace`` (Blender's Delete Workspace on it), never under a window that shows it.
    Only once a window shows it (the operator acts on the window's own workspace); ``window`` is unused then.

    Each window showing it goes first to ``home_of``, given the active object's mode (``_object_mode``), and the
    delete is queued on the last of them: Blender applies the windows' notifiers
    window by window, each window's in order, so every switch applies before it. ``kept_modes`` (the build's
    ``_kept_modes``) forgets the workspace the windows stay on. Returns whether the delete was queued."""
    name = workspace.name
    if not half_made(workspace):   # Never a workspace Angelo did not make as a tab.
        print("Angelo: %r is not a half-made tab: kept" % name)
        return False
    shown = [w for w in live_windows.every_window(bpy.context.window_manager) if w.workspace == workspace]
    if not shown:   # Still marked: a build shows it first (studio_workspaces._removed), then removes it.
        return False
    target = home_of(workspace, home)
    if target is None:   # Still marked: a later build removes it once the window shows something else.
        print("Angelo: a half-made %r tab is shown and there is no other workspace to show" % name)
        return False
    if kept_modes is not None:
        kept_modes.pop(target.name, None)   # The window stays there, in the person's mode.
    mode = _object_mode(shown[-1], workspace)
    if target.object_mode != mode:
        target.object_mode = mode
    for each in shown:
        each.workspace = target
    window = shown[-1]
    try:
        with bpy.context.temp_override(window=window):
            if bpy.context.workspace != workspace:   # The operator deletes the window's own workspace: only this one.
                raise RuntimeError("the context is on another workspace")
            bpy.ops.workspace.delete()
        return True
    except (RuntimeError, TypeError) as error:   # Still marked unfinished: the next build removes it.
        print("Angelo: a half-made %r tab could not be removed:" % name, error)
        return False
