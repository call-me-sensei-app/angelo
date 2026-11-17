# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Left alone, Angelo redraws nothing: no region of the window is drawn while nobody touches the app and nothing in it
changes, on Director, Layout, Media Studio and Generation Flows.

Evidence (an-blender53 on Mac 1, 2026-11-16, 3becd249274 and back to the 1.1.0 measurement build 08e2b0f7842): on the
Director tab the chat area was redrawn every 2 s for as long as the app ran, start-up file, nobody at the machine.
Each redraw took about 130 MB of graphics memory that Metal released a second later ("owned unmapped (graphics)" in
vmmap, 222-322 MB <> 426 MB), so the footprint stepped 0.80 <> 0.93 GB without end and never settled; the GPU and
the main thread woke for it every time. The Generation Flows pump asked for all of them (``flow_host``: a redraw at
every refresh, changed or not; ``test_native_flow_idle_redraw``). This suite is the rule for every surface, whoever
asks: an idle window is not drawn.

Real default startup (Director), no input, CLI, account, network or inference. A draw handler on every editor type
and region type counts each region draw (``ED_region_do_draw`` runs them for any region). Per tab: the window shows
it and comes to rest (its first draws, anything that starts with it: no region drawn for ``REST_SECONDS``), then
``IDLE_SECONDS`` of counting with nothing done; after that every area is asked to redraw once, and each must be
counted, so a count of zero is a window that was not drawn and never a counter that does not see it. The report
names every region drawn while idle and every region drawn before the window rested, with its draw times.

Who asked is in the report too (2026-11-16: a Linux round failed three times in four, one draw of Media Studio or
two of the whole window, and the regions and times alone could not say whether Angelo or the window system had
drawn them). Every call from Python of an RNA function named ``*redraw*`` made while a tab is counted is kept with
the stack that made it (``sys.monitoring``), and a moment in which every area of the window drew at once is listed
as a whole-window draw. A draw nobody asked for by name came from a property write's notifier, from Blender or
from the window system (another window uncovering this one redraws all of it). That record is proved as the
counter is: the suite's own requests at the end of each tab must be in it.

The suite needs the display to itself, so it is declared serial and the runner runs it beside no other suite.
With that record, on Linux (Xvfb, no compositor, the 3e36393 build): beside seven other suites on one display 0 of
5 runs passed, each with the whole Director window drawn once or twice about 9 s into the count and no redraw
asked from Python; alone on a display of its own 10 of 10 passed, and 5 of 5 under eight busy loops. The window
system had drawn it: nothing for Angelo to stop, and nothing this suite may count. A second runner on the same
display is the same neighbour; give each runner its own display.

Rest is found, not assumed, and the count begins once Angelo's start-up is over (2026-11-16). A fixed six seconds
stood here; in the release round on 9d7a54eab7f three header regions of Layout's 3D view drew 8 ms after the count
began. Alone on that build, with every draw kept: the same three regions drew twice, 4.96 s apart, and then not
once in 129 further idle seconds (150 s counted on Layout). They are the Media Studio asset library's deferred
start (``studio_assets_host``: its first sync ``SYNC_DELAY`` after start-up, its Asset Browser refresh a tick
later), which the 3D view's headers show and Director's hidden headers do not: start-up work, on whichever tab
the suite had reached. So the suite waits for that start to be over (``assets_started``) before its first tab. A
window that keeps drawing never rests: that fails its own check (``the_window_comes_to_rest``) within
``REST_LIMIT``, and a redraw that repeats within ``IDLE_SECONDS`` is counted after any rest, as the fault was.

Run through ``run_native_suites.py --gui --include test_blender_idle_redraw``; check ``idle-redraw.json`` for
``success`` and ``phase == "complete"``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import time
import traceback
from pathlib import Path

import bpy

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

product_env.clear('DISABLE_DEFAULT_CHAT')
from angelo_agent import director, studio_workspaces as tabs  # noqa: E402
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
import qa_waits  # noqa: E402

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-idle-redraw-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "idle-redraw.json"
IDLE_SECONDS = 10.0    # Counted with nothing done. The fault drew every 2 s: five draws of the chat in this time.
REST_SECONDS = 3.0     # At rest: no region drawn for this long. Longer than the 2 s the fault left between draws.
REST_LIMIT = 30.0      # A tab's arrival (a fade, a first evaluation, a tooltip) is over well within this.
ASKED_SECONDS = 1.5    # After every area is asked to redraw: long enough for the draw, on a loaded machine too.
TABS = ("Director", "Layout", tabs.TABS["studio"], tabs.TABS["flows"])
state = {"phase": "boot", "success": False, "checks": {}, "tabs": {}}
checks = state["checks"]
started = time.monotonic()
drawn = []          # [seconds since the tab was shown, editor, region, area pointer] of every region draw.
requests = []       # [seconds since the tab was shown, the call, the Python stack] of every redraw asked by name.
REQUESTS_KEPT = 200
BURST_SECONDS = 0.05   # Draws this close together are one frame of the window.
counting = {"since": None}   # When the tab was shown; None between tabs.


def window():
    return bpy.context.window_manager.windows[0]


def busy():
    return tabs._building or tabs._holding is not None


def assets_started():
    """Whether the Media Studio asset library's deferred start is over: off (no first sync will come), or its first
    sync begun and nothing waiting to be refreshed for two of its ticks. Until then it redraws the 3D view's headers."""
    from angelo_agent import studio_assets_host as host
    if host._state["registered"] is None:   # Not asked yet, or its record cannot be read now (asked again a minute on).
        return host._state["asked"] > 0
    if not host._state["registered"]:
        return True
    if not host._state.get("prefetched") or host._state["changed"]:
        state.pop("assets_quiet_since", None)
        return False
    return time.monotonic() - state.setdefault("assets_quiet_since", time.monotonic()) >= 2 * host.TICK + 1.0


def count(editor, region):
    """A draw handler: this region was drawn."""
    since = counting["since"]
    if since is None:
        return
    area = bpy.context.area
    drawn.append([round(time.monotonic() - since, 3), getattr(area, "ui_type", None) or editor, region,
                  area.as_pointer() if area is not None else 0])


def watch_every_region():
    """A counter on every region type of every editor type; the pairs that took one."""
    regions = [item.identifier for item in bpy.types.Region.bl_rna.properties["type"].enum_items]
    watched = []
    spaces = [getattr(bpy.types, name) for name in dir(bpy.types) if name.startswith("Space")]
    for space in spaces:
        if not (isinstance(space, type) and issubclass(space, bpy.types.Space) and hasattr(space, "draw_handler_add")):
            continue
        for region in regions:
            try:
                space.draw_handler_add(count, (space.__name__, region), region, 'POST_PIXEL')
            except (TypeError, ValueError, RuntimeError, AttributeError):   # This editor has no such region.
                continue
            watched.append("%s/%s" % (space.__name__, region))
    return watched


def watch_requests():
    """Keep every call from Python of an RNA function named ``*redraw*`` (``Area.tag_redraw``, ``Region.tag_redraw``
    ...) made while a tab is counted, with the stack that made it. True when this Python can watch calls."""
    monitoring = getattr(sys, "monitoring", None)
    tool = next((number for number in (3, 4) if monitoring and monitoring.get_tool(number) is None), None)
    if tool is None:
        return False

    def called(_code, _offset, target, _first):
        since = counting["since"]
        if since is None or type(target).__name__ != "bpy_func" or len(requests) >= REQUESTS_KEPT:
            return None
        call = repr(target)
        if "redraw" not in call:
            return None
        frame, stack = sys._getframe(1), []
        while frame is not None and len(stack) < 8:
            code = frame.f_code
            stack.append("%s:%d %s" % (os.path.basename(code.co_filename), frame.f_lineno, code.co_name))
            frame = frame.f_back
        requests.append([round(time.monotonic() - since, 3), call[:80], stack])
        return None
    monitoring.use_tool_id(tool, "angelo-idle-redraw")
    monitoring.register_callback(tool, monitoring.events.CALL, called)
    monitoring.set_events(tool, monitoring.events.CALL)
    return True


def whole_window(rows, areas):
    """The times at which every area of the window drew in one frame: rows are [second, editor, region, area]."""
    found, frame = [], []
    for row in sorted(rows) + [None]:
        if row is not None and (not frame or row[0] - frame[-1][0] <= BURST_SECONDS):
            frame.append(row)
            continue
        if frame and set(areas) <= {item[3] for item in frame}:
            found.append(frame[0][0])
        frame = [row] if row is not None else []
    return found


def shown():
    """The areas the window shows now: pointer -> editor."""
    return {area.as_pointer(): area.ui_type for area in window().screen.areas}


def slug(tab):
    return tab.lower().replace(" ", "_")


def by_region(rows):
    found = {}
    for second, editor, region, _area in rows:
        found.setdefault("%s/%s" % (editor, region), []).append(second)
    return found


def finish(error=None):
    counting["since"] = None
    if error:
        state["error"] = "".join(traceback.format_exception(error)).replace("Traceback (most recent", "Trace (most recent")
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_IDLE_REDRAW", json.dumps({"success": state["success"], "phase": state["phase"],
                                            "failed": state["failed_checks"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(phase, condition, limit=30):
    since = state.setdefault("since", {}).setdefault("%s:%s" % (state.get("tab"), phase), time.monotonic())
    if condition():
        return True
    if time.monotonic() - since > limit * qa_waits.timeout_scale():
        raise AssertionError("Timed out in %s (%s)" % (phase, state.get("tab")))
    return False


def tick():
    phase, tab = state["phase"], state.get("tab")
    try:
        assert time.monotonic() - started < 340 * qa_waits.timeout_scale(), "Suite timed out"
        if phase == "boot":
            if not wait(phase, lambda: qa_boot.started() and not busy() and all(tabs.tab(k) for k in tabs.TABS)
                        and assets_started(), 120):
                return .3
            state["start_up_over_after"] = round(time.monotonic() - started, 1)
            state["watched"] = watch_every_region()   # Which editors it sees is proved per tab ("asked").
            state["redraw_requests_watched"] = watch_requests()
            state["started_in_director"] = director.is_director(window())
            state["tab"], state["phase"] = TABS[0], "show"
            return .2
        if phase == "show":
            if tab == "Director":
                checks["the_app_starts_on_director"] = (state["started_in_director"]
                                                        and window().workspace.name == "Director")
            elif director.is_director(window()):   # The other tabs are Manual's: as the person leaving Director.
                director.request_mode(window(), "MANUAL", persist=False)
                state["phase"] = "to_manual"
                return .3
            elif window().workspace.name != tab:
                window().workspace = bpy.data.workspaces[tab]
            state["phase"] = "shown"
            return .2
        if phase == "to_manual":
            if not wait(phase, lambda: qa_boot.started() and not busy() and not director.is_director(window()), 30):
                return .2
            state["phase"] = "show"
            return .2
        if phase == "shown":
            if not wait(phase, lambda: window().workspace.name == tab and qa_boot.started() and not busy(), 30):
                return .2
            del drawn[:], requests[:]
            counting["since"] = state["at"] = time.monotonic()
            state["phase"] = "settle"
            return .2
        if phase == "settle":
            # At rest when nothing was drawn for REST_SECONDS; the tab's own showing counts as a draw at 0.
            since_shown = time.monotonic() - state["at"]
            rested = since_shown - (drawn[-1][0] if drawn else 0.0) >= REST_SECONDS
            if not rested and since_shown < REST_LIMIT * qa_waits.timeout_scale():
                return .25
            state["tabs"][tab] = {"areas": sorted(shown().values()), "rest_seconds": REST_SECONDS,
                                  "at_rest_after": round(since_shown, 2) if rested else None,
                                  "drawn_before_rest": by_region(drawn),
                                  "asked_from_python_before_rest": list(requests)}
            checks["%s_the_window_comes_to_rest" % slug(tab)] = rested
            state["count_from"] = since_shown
            state["phase"], state["idle_at"] = "idle", time.monotonic()
            return IDLE_SECONDS
        if phase == "idle":
            if time.monotonic() - state["idle_at"] < IDLE_SECONDS:
                return .2
            idle = [[round(row[0] - state["count_from"], 3)] + row[1:] for row in drawn if row[0] >= state["count_from"]]
            record = state["tabs"][tab]
            record.update(idle_seconds=IDLE_SECONDS, seconds_counted=round(time.monotonic() - state["idle_at"], 2),
                          drawn_while_idle=by_region(idle), draws_while_idle=len(idle),
                          whole_window_draws_while_idle=whole_window(idle, shown()),
                          asked_from_python_while_idle=[[round(row[0] - state["count_from"], 3)] + row[1:]
                                                        for row in requests if row[0] >= state["count_from"]])
            checks["%s_no_region_is_drawn_while_idle" % slug(tab)] = not idle
            del drawn[:], requests[:]
            counting["since"] = time.monotonic()
            for area in window().screen.areas:   # The counter's own proof: asked, every area is seen drawing.
                area.tag_redraw()
            state["phase"], state["at"] = "asked", time.monotonic()
            return ASKED_SECONDS
        if phase == "asked":
            if time.monotonic() - state["at"] < ASKED_SECONDS:
                return .2
            record, areas = state["tabs"][tab], shown()
            seen = {row[3] for row in drawn}
            record["drawn_when_asked"] = by_region(drawn)
            record["areas_not_seen_drawing_when_asked"] = sorted(areas[p] for p in areas if p not in seen)
            checks["%s_every_area_is_counted_when_it_draws" % slug(tab)] = bool(areas) and set(areas) <= seen
            if state["redraw_requests_watched"]:   # The same proof for who asked: this suite's own requests are seen.
                record["requests_seen_when_asked"] = len(requests)
                checks["%s_a_redraw_asked_from_python_is_seen" % slug(tab)] = len(requests) >= len(areas)
            counting["since"] = None
            following = TABS.index(tab) + 1
            if following < len(TABS):
                state["tab"], state["phase"] = TABS[following], "show"
                return .3
            state["phase"] = "complete"
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(qa_boot.gated(tick), first_interval=.5, persistent=True)
