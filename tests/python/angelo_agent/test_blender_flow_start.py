# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The start page with no flow open, live, in English and Arabic (1.0.5, ``flow_window`` + ``flow_start``).

Real default startup; no CLI, account, network or inference. For each language, Director's flow window (opened
through the shared opener ``flow_open.show``, then showing no flow) and Manual's Generation Flows tab draw the start
page: the bar's title is "Generation Flows" and its line under the title is the Flows why line ("Go further: chain
generation steps into a flow you can rerun", ``flow_open.MENU_WHY``), translated and never elided; the rows are the
Flows popup's (New Flow and the templates), every one shown or counted; the line's pixels are on the canvas where
the drawing put it, mirrored in Arabic. Captures: ``flow-start-{window,tab}-{en,ar}.png``.

Run through ``run_native_suites.py --gui --include test_blender_flow_start``; check ``flow-start.json`` for
``success`` and ``phase == "complete"``.
"""

import json
import os
import tempfile
import time
import traceback
from pathlib import Path

import bpy

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

product_env.clear('DISABLE_DEFAULT_CHAT')
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-flow-start-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "flow-start.json"
prefs = bpy.context.preferences
prefs.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "measured": {}}
checks = state["checks"]
started = time.monotonic()
LANGUAGES = (("en", "en_US"), ("ar", "ar_EG"))
PHASES = [(code, where) for code, _ in LANGUAGES for where in ("window", "tab")]


def window():
    return bpy.context.window_manager.windows[0]


def capture(name, target):
    path = OUT / name
    with bpy.context.temp_override(window=target):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return path


def pixels(path):
    """A capture's pixels as an (height, width, 3) array of 0-255 integers, bottom row first."""
    import numpy
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        values = numpy.empty(width * height * 4, dtype=numpy.float32)
        image.pixels.foreach_get(values)
    finally:
        bpy.data.images.remove(image)
    return (values.reshape(height, width, 4)[..., :3] * 255).round().astype(numpy.int32)


def text_share(px, rect, ground):
    """Share of ``rect``'s pixels (window coordinates) well away from the ``ground`` colour: drawn text."""
    import numpy
    x, y, w, h = (int(round(v)) for v in rect)
    block = px[max(0, y):y + h, max(0, x):x + w].reshape(-1, 3)
    if not len(block):
        return 0.0
    return float((numpy.abs(block - numpy.array(ground)).sum(axis=1) > 40).mean())


def settled():
    return qa_boot.started()   # Director's layout and Manual's tabs (qa_boot).


def finish(error=None):
    prefs.view.language = "en_US"
    if error:
        state["error"] = "".join(traceback.format_exception(error)).replace("Traceback (most recent", "Trace (most recent")
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_FLOW_START", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(phase, condition, limit=20):
    since = state.setdefault("since", {}).setdefault(phase, time.monotonic())
    if condition():
        return True
    if time.monotonic() - since > limit:
        raise AssertionError("Timed out in " + phase)
    return False


def start_area(where):
    """The window and the area showing the start page for ``where`` (window: Director's flow window; tab: Manual's
    Generation Flows tab), or (None, None)."""
    from angelo_agent import flow_open, studio_workspaces
    if where == "window":
        target = flow_open.flow_window(bpy.context.window_manager.windows)
    else:
        target = window() if studio_workspaces.active(window(), "flows") else None
    area = target.screen.areas[0] if target is not None and target.screen.areas else None
    if area is None or area.type != 'NODE_EDITOR':
        return None, None
    return target, area


def check_start_page(code, where):
    """Read the drawn start page of ``where`` in language ``code``; True once it is drawn in that language."""
    from angelo_agent import card_kit, flow_menu, flow_open, flow_window
    from angelo_agent.ui_text import iface_, is_rtl
    target, area = start_area(where)
    bar = flow_window._bars.get(area.as_pointer()) if area is not None else None
    drawn = (bar or {}).get("drawn") or {}
    why = iface_(flow_open.MENU_WHY)
    status = drawn.get("status") or ""
    # This language's frame: its title, and its why line (or a cut one, which the check below reports).
    current = drawn.get("title") == iface_("Generation Flows") and (status == why or "…" in status)
    if area is None or area.spaces.active.node_tree is not None or not current or not drawn.get("start_rows"):
        return False   # Not drawn yet, or still in the previous language: the check below needs this frame.
    name = "%s_%s" % (where, code)
    path = capture("flow-start-%s-%s.png" % (where, code), target)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    s = prefs.system.ui_scale
    status_w = min(region.width - drawn["text_x"], 200 * s)
    x = drawn["text_x"]
    if is_rtl():   # The canvas mirrors: the line ends where it starts in English.
        x = region.width - drawn["text_x"] - status_w
    bottom = region.height - flow_window.HEIGHT * s
    band = (region.x + x, region.y + bottom + 6 * s, status_w, 9 * s)
    share = text_share(pixels(path), band, [round(c * 255) for c in card_kit.CARD[:3]])
    rows = [r["value"] for r in flow_menu.flow_rows() if r["kind"] == "row"]
    shown = drawn.get("start_rows") or []
    left = bar.get("start_left", 0)
    state["measured"][name] = {"status": drawn["status"], "title": drawn["title"], "rows": shown, "left": left,
                               "status_text_share": round(share, 3), "rtl": is_rtl()}
    checks[name + "_title_is_generation_flows"] = drawn["title"] == iface_("Generation Flows")
    checks[name + "_line_under_the_title_is_the_whole_why_line"] = drawn["status"] == why
    checks[name + "_the_why_line_is_drawn_where_it_belongs"] = share > 0.03
    checks[name + "_rows_are_the_flows_popups"] = shown == rows[:len(shown)] and {
        flow_open.NEW_ROW} <= set(shown) and any(v.startswith(flow_open.TEMPLATE_ROW) for v in shown)
    checks[name + "_every_row_is_shown_or_counted"] = len(shown) + left == len(rows)
    if code != "en":
        checks[name + "_is_translated"] = why != flow_open.MENU_WHY and is_rtl()
    return True


def tick():
    from angelo_agent import director, flow_nodes, flow_open, studio_workspaces
    phase = state["phase"]
    try:
        assert time.monotonic() - started < 240, "Suite timed out"
        if phase == "boot":
            if not wait(phase, lambda: settled() and director.is_director(window()) and not studio_workspaces._building
                        and studio_workspaces.tab("flows") is not None, limit=40):
                return .3
            state["step"] = 0
            state["phase"] = "open"
            return .2
        if phase == "open":
            code, where = PHASES[state["step"]]
            prefs.view.language = dict(LANGUAGES)[code]
            if where == "window":   # Director: the flow window, opened through the shared opener, then emptied.
                if not wait(phase + str(state["step"]), lambda: settled() and director.is_director(window())):
                    return .3
                target, area = start_area("window")
                if area is None:
                    tree = bpy.data.node_groups.new("QA start page", flow_nodes.TREE)
                    target = flow_open.show(window(), tree)
                    area = target.screen.areas[0]
                area.spaces.active.node_tree = None   # No flow open: the start page.
                area.tag_redraw()
            else:   # Manual: the Generation Flows tab, as the top bar's Flows shows it.
                if director.is_director(window()):
                    director.request_mode(window(), "MANUAL", persist=False)
                    return .5
                if not wait(phase + str(state["step"]), lambda: settled() and not director.is_director(window())):
                    return .3
                studio_workspaces.show(window(), "flows")
            state["phase"] = "check"
            return .6
        if phase == "check":
            code, where = PHASES[state["step"]]
            target, area = start_area(where)
            if area is not None:
                area.tag_redraw()
            if not wait(phase + str(state["step"]), lambda: check_start_page(code, where)):
                return .3
            state["step"] += 1
            if state["step"] == len(PHASES):
                state["phase"] = "complete"
                finish()
                return None
            if where == "tab":   # Back to Director for the next language's window.
                director.request_mode(window(), "DIRECTOR", persist=False)
            state["phase"] = "open"
            return .6
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5, persistent=True)
