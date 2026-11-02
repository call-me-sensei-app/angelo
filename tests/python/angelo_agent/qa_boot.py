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
not waited for, since the suite is what lets it go. ``started`` also waits until Angelo's start-up cover is off the
window: ``_cover_watch`` removed it on a 0.25 s poll after the layout settled (every frame since 1.0.8), and a frame
captured in between showed the cover's flat grey over every editor (test_blender_canvas_states on the 1.0.7 cut
744f498d0a6: the "rows at rest" frame was the cover, so the hovered row read darker than it). test_native_qa_boot
keeps every suite on this gate.

A suite that enables Angelo itself (``addon_utils.enable("angelo_agent")``, with the default chat opted out) still gets
the session's first tab build: the add-on schedules it as soon as the layout is idle (``studio_workspaces.ensure``).
``gated`` holds such a suite's timer until the gate opens (1.0.8): a suite that acted at a fixed first second raced
that build on a loaded machine (a Help menu opened under it closed, app_identity on the 1.0.5 RC; an area converted
mid-build belonged to a tab, flow_setup_states).
"""

GATE_SECONDS = 60.0   # Base seconds (x the runner's timeout scale) a suite waits for the gate before it fails.


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
    if getattr(layout, "_cover", {}).get("since") is not None:   # Still painted: set by start_cover, cleared on removal.
        reasons.append("Angelo's start-up cover")
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


def gated(callback, limit=GATE_SECONDS, clock=None):
    """``callback`` as a timer callback whose first call waits until Angelo's start-up is over: register the result
    in place of ``callback`` (``bpy.app.timers.register(qa_boot.gated(tick), ...)``). While Angelo is enabled and the
    gate is closed it polls; once open, every call is ``callback``'s own. A gate still closed after ``limit`` seconds
    (x the runner's timeout scale) quits the app and raises with what it waited for, so the suite fails at once with
    that reason instead of acting under the build or idling until the runner's own timeout."""
    import time
    import qa_waits
    clock = clock or time.monotonic
    budget = limit * qa_waits.timeout_scale()
    gate = {"open": False, "since": None}

    def tick():
        if not gate["open"]:
            reasons = waiting_on() if _angelo_enabled() else []
            if reasons:
                now = clock()
                gate["since"] = gate["since"] if gate["since"] is not None else now
                if now - gate["since"] > budget:
                    import bpy
                    bpy.ops.wm.quit_blender()
                    raise RuntimeError("Angelo's start-up did not end in %.0f s: waiting on %s"
                                       % (budget, "; ".join(reasons)))
                return 0.1
            gate["open"] = True
        return callback()
    return tick


def _angelo_enabled():
    """Whether the host add-on is enabled: only then is there a start-up (or a first tab build) to wait for."""
    import addon_utils
    return bool(addon_utils.check("angelo_agent")[1])
