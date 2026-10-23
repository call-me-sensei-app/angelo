# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's start-up is over: the one boot gate every GUI suite waits on before it acts (1.0.5).

Director's layout and the default layout settle on timers (``director._timer``, ``ui._layout_timer``). Manual's Media
Studio and Generation Flows tabs are built by switching the window through them for a moment (``studio_workspaces``),
all after Angelo's start-up script (``angelo_default_layout``) has run its own timer that builds Director: a suite that
asked for Manual before it ran was put back in Director (asset_shelf_add and shelf_empty, d-final2 line),
and the window has no 3D view while it does: the session's first build is part of the start-up, under its cover,
once Director's layout has settled (``startup_build_pending``, at most ``STARTUP_LIMIT`` seconds); a later one (a file
loaded without the tabs) runs once the app has been quiet for a second. A suite that waited on the two timers alone
acted between Director settling and the build (test_blender_director_navigation: StopIteration at "connect" on the
a1c3a0dc6cf drag-class line). ``started`` also waits while a build runs, holds the layout, or is owed: the start-up's,
or a later one about to start; a later build held back by something the suite itself shows (a card, the welcome) is
not waited for, since the suite is what lets it go. test_native_qa_boot keeps every suite on this gate.
"""


def started(window=None):
    """Whether Angelo's start-up layout is done in ``window`` (default: the first window)."""
    return not waiting_on(window)


def waiting_on(window=None):
    """What the start-up still holds, in words (empty once ``started``): a suite records it when its boot times out,
    so the report says what it waited for."""
    import sys
    import bpy
    from angelo_agent import director, ui
    reasons = []
    layout = sys.modules.get("angelo_default_layout")   # Angelo's start-up script: it builds Director on a timer.
    pending = getattr(layout, "_timer", None)
    if pending is not None and bpy.app.timers.is_registered(pending):
        reasons.append("Angelo's start-up layout timer")
    if director._timer is not None:
        reasons.append("Director's layout timer")
    if ui._layout_timer is not None:
        reasons.append("the layout timer")
    try:
        from angelo_agent import studio_workspaces as tabs
    except ImportError:
        return reasons   # A build without Manual's Studio and Flows tabs (1.0.4).
    if getattr(tabs, "_building", False):
        reasons.append("Manual's tab build running")
    if getattr(tabs, "_holding", None) is not None:
        reasons.append("Manual's tab build holding the layout")
    if getattr(tabs, "startup_build_pending", lambda: False)():
        reasons.append("the start-up tab build owed")   # Part of the start-up: owed until it ran (or its limit).
    if window is None:
        windows = bpy.context.window_manager.windows
        window = windows[0] if len(windows) else None
    pending = getattr(tabs, "missing", lambda: [])()
    quiet = getattr(tabs, "startup_quiet", None)
    if pending and window is not None and quiet is not None and quiet(window):
        reasons.append("a tab build about to start (missing: %s)" % ", ".join(pending))
    return reasons
