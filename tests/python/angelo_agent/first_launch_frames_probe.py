# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The in-app machinery of ``test_blender_first_launch_frames.py`` (its phases: ``first_launch_frames_phases``).

Each phase is a generator of steps: a number waits that many seconds, an ``Until`` waits for a condition (or fails
the phase when it times out), a ``Sample`` captures the window every frame it can for some seconds while Angelo's
startup runs on. Every phase starts sampling at the first main-loop turn, so its first frame is the real first
frame. Frames are then checked for Blender's form (``splash_frames``); a few are kept as PNGs, the rest deleted.
A watcher records when each milestone appeared (seconds after the host started the process). Clicks are real
pointer events on the cards' own hit geometry. Checks are recorded, never asserted one by one.
"""

import json
import os
import sys
import time
import traceback
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import splash_frames  # noqa: E402  (Blender only)

ARGS = sys.argv[sys.argv.index("--") + 1:]


def _arg(name, default=None):
    return ARGS[ARGS.index(name) + 1] if name in ARGS else default


PHASE = _arg("--native-phase")
ROOT = Path(_arg("--fixture-root"))
OUT = ROOT / PHASE
BLEND = _arg("--blend")
EXPECTED_FAILURE = _arg("--expected-failure")   # The one error a fixture provokes on purpose (``failed-start``).
LAUNCHED = float(_arg("--launched-at", "0")) or time.time()
OUT.mkdir(parents=True, exist_ok=True)
report = {"phase": PHASE, "checks": {}, "frames": [], "captures": {}, "rects": {}, "timings": {}, "success": False}
splash_frames.instrument()   # Before Blender builds its startup splash (after --python, WM_init_splash_on_startup).
report["at_python"] = splash_frames.state()
report["timings"]["python"] = round(time.time() - LAUNCHED, 3)


class Until:
    def __init__(self, condition, what, limit=60, poll=0.2):
        self.condition, self.what, self.limit, self.poll = condition, what, limit, poll


class Sample:
    def __init__(self, seconds, tag="startup"):
        self.seconds, self.tag = seconds, tag


def after_launch():
    return round(time.time() - LAUNCHED, 3)


def check(name, condition):
    report["checks"][name] = bool(condition)


def window():
    return bpy.context.window_manager.windows[0]


def mod(name):
    return sys.modules.get(name)


def frame(tag):
    path = OUT / ("frame-%03d.png" % len(report["frames"]))
    state = splash_frames.state()
    with bpy.context.temp_override(window=window()):
        bpy.ops.screen.screenshot(filepath=str(path))
    state.update(tag=tag, after_launch=after_launch(), path=str(path), layout=layout_state())
    report["frames"].append(state)


def layout_state():
    """At a frame: whether Angelo's layout has settled (Director built, no dock moving), and the editor areas'
    rectangles (the screen's areas: the top and status bars are the window's own, outside them)."""
    w = window()
    director, ui = mod("angelo_agent.director"), mod("angelo_agent.ui")
    settled = bool(director and director.is_director(w) and director._timer is None
                   and (ui is None or ui._layout_timer is None))
    areas = [(a.x, a.y, a.width, a.height) for a in w.screen.areas]
    prefs = bpy.context.preferences.system
    return {"settled": settled, "areas": areas, "size": (w.width, w.height), "scale": prefs.ui_scale * prefs.pixel_size}


SPLASH_HALF = (265, 140)   # Points: half Blender's startup splash, centred in the window; it draws above the cover.
COVER_SHARE = 0.99         # Of the editor areas' pixels outside the splash.
COVER_TOLERANCE = 2        # 0-255 per channel: the cover is one rectangle of one colour.


def own_frame(state):
    """A frame before Angelo's layout settled shows Angelo's cover: over the whole of every editor area (their
    edges, the timeline's playhead strip and the action-zone arrows included) and outside the splash, one colour
    (``COVER_SHARE`` of the pixels within ``COVER_TOLERANCE``). The stock Layout scores about 0.6; per-region paint
    leaves the playhead, the arrows and a lighter band where regions overlap and fails too. A settled frame is
    Angelo's layout."""
    import numpy as np
    import popup_pixels
    layout = state.get("layout") or {}
    if layout.get("settled") or not state.get("path"):
        return {"own": True, "settled": True}
    rgb = popup_pixels.load(state["path"])
    height, width = rgb.shape[:2]
    k = width / max(1, layout["size"][0])
    s = layout["scale"] * k
    keep = np.zeros((height, width), dtype=bool)
    for x, y, w, h in layout["areas"]:
        keep[int(y * k):int((y + h) * k), int(x * k):int((x + w) * k)] = True
    cx, cy = width / 2, height / 2
    keep[int(cy - SPLASH_HALF[1] * s):int(cy + SPLASH_HALF[1] * s),
         int(cx - SPLASH_HALF[0] * s):int(cx + SPLASH_HALF[0] * s)] = False
    pixels = rgb[keep].astype(np.int32)
    if not len(pixels):
        return {"own": False, "share": 0.0}
    packed = (pixels[:, 0] << 16) | (pixels[:, 1] << 8) | pixels[:, 2]   # One integer per colour: a fast mode.
    mode = int(np.bincount(packed).argmax())
    fill = np.array([mode >> 16, (mode >> 8) & 255, mode & 255], dtype=np.int32)
    share = float(np.all(np.abs(pixels - fill) <= COVER_TOLERANCE, axis=1).mean())
    return {"own": share >= COVER_SHARE, "share": round(share, 3), "fill": [int(c) for c in fill]}


def capture(name):
    path = OUT / (name + ".png")
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    report["captures"][name] = str(path)
    return path


def analyse():
    """Check every captured frame for Blender's form; keep the first, any with the form and one a second."""
    kept = set()
    for state in report["frames"]:
        if "form_rows" in state:
            continue
        state["form_rows"] = splash_frames.form_rows(state["path"])
        state["own"] = own_frame(state)
        second = (state["tag"], int(state["after_launch"]))
        if (state is report["frames"][0] or state["form_rows"] >= splash_frames.FORM_ROWS or second not in kept
                or not state["own"]["own"]):
            kept.add(second)
        else:
            os.remove(state["path"])
            state["path"] = None


def own_frames(prefix="", tag=None):
    """Every frame (or every one tagged ``tag``) is Angelo's: its cover before the layout settles, then its layout."""
    frames = [f for f in report["frames"] if (tag is None or f["tag"] == tag) and "own" in f]
    report.setdefault("not_own_frames", []).extend(f["path"] for f in frames if not f["own"]["own"])
    check(prefix + "every_frame_is_angelos_own_layout_or_cover", frames and all(f["own"]["own"] for f in frames))


def no_form(prefix="", tag=None, blenders_menus=False):
    """No frame (or none tagged ``tag``) has a form; unless ``blenders_menus``, Blender's own classes never drew."""
    frames = [f for f in report["frames"] if tag is None or f["tag"] == tag]
    if not blenders_menus:
        check(prefix + "blenders_own_menus_never_drew", sum(splash_frames.stock_draws.values()) == 0)
    check(prefix + "no_blender_form_in_any_frame", frames and all(f["form_rows"] < splash_frames.FORM_ROWS
                                                                  for f in frames))


def card_ready(kind):
    host = mod("angelo_agent.card_host")
    return bool(host and host.is_active(kind) and host._active.plan is not None)


def card_actions():
    host = mod("angelo_agent.card_host")
    plan = host._active.plan if host and host._active else None
    return [(a, v) for _, a, v in plan.hits] if plan else []


def _region(area_pointer):
    area = next(a for a in window().screen.areas if a.as_pointer() == area_pointer)
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def _press(x, y):
    for kind, value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), ('LEFTMOUSE', 'RELEASE')):
        window().event_simulate(type=kind, value=value, x=x, y=y)


def card_point(action, value=None):
    """Window coordinates of the visible card's control (mirrored back in a right-to-left language)."""
    from angelo_agent import canvas_rtl, card_host
    _area, region = _region(card_host._active.area)
    rect = next(r for r, a, v in reversed(card_host._active.plan.hits) if a == action and (value is None or v == value))
    x = canvas_rtl.pointer_x(rect[0] + rect[2] / 2, region.width)
    return int(region.x + x), int(region.y + rect[1] + rect[3] / 2)


def click_card(action, value=None):
    _press(*card_point(action, value))


def click_welcome(action):
    from angelo_agent import canvas_rtl, intro
    _area, region = _region(intro._open["area"])
    rect = next(r for r, a, v in intro._open["plan"]["hits"] if a == action)
    _press(int(region.x + canvas_rtl.pointer_x(rect[0] + rect[2] / 2, region.width)),
           int(region.y + rect[1] + rect[3] / 2))


def key(name):
    w = window()
    for value in ('PRESS', 'RELEASE'):
        w.event_simulate(type=name, value=value, x=w.width // 3, y=w.height // 2)


def control_rect(action):
    """The window rectangle ``(x0, y0, x1, y1)`` covering a card control's hits (for crops in the report)."""
    from angelo_agent import canvas_rtl, card_host
    _area, region = _region(card_host._active.area)
    rects = [r for r, a, _ in card_host._active.plan.hits if a == action]
    xs = [canvas_rtl.pointer_x(x, region.width) for r in rects for x in (r[0], r[0] + r[2])]
    return (int(region.x + min(xs)), int(region.y + min(r[1] for r in rects)),
            int(region.x + max(xs)), int(region.y + max(r[1] + r[3] for r in rects)))


def light():
    return bpy.context.preferences.themes[0].user_interface.wcol_regular.inner[0] > 0.5


def sequence():
    setup = mod("angelo_agent.splash_setup")
    return list(setup.sequence) if setup else []


def finish_welcome(expected=("splash", "setup", "welcome")):
    """Continue on the setup card: the preferences are saved and the welcome follows; close it."""
    from angelo_agent import intro, splash_setup
    click_card("continue")
    yield Until(lambda: intro.is_open() and "plan" in intro._open, "the welcome")
    yield 0.6
    report["sequence"] = sequence()
    check("continue_saves_and_the_welcome_follows", splash_setup.preferences_saved()
          and sequence() == list(expected))
    capture("welcome")
    click_welcome("start")
    yield Until(lambda: not intro.is_open(), "the welcome to close")


MILESTONES = {
    "addon_enabled": lambda: mod("angelo_agent.splash_setup") is not None,
    "director": lambda: window().workspace.name == "Director",
    "setup_card": lambda: card_ready("setup"),
    "start_panel": lambda: card_ready("start"),
    "showcase": lambda: bool(mod("angelo_agent.connect_hero") and mod("angelo_agent.connect_hero").is_shown()),
    "welcome": lambda: bool(mod("angelo_agent.intro") and mod("angelo_agent.intro").is_open()),
}


def watch():
    for name, condition in MILESTONES.items():
        if name not in report["timings"]:
            try:
                if condition():
                    report["timings"][name] = after_launch()
            except (ReferenceError, RuntimeError, IndexError):
                pass
    return 0.02


def finish(error=None):
    if error:
        report["error"] = error.replace("Traceback (most recent", "Trace (most recent")   # The host scans for it.
    report["stock_draws"] = dict(splash_frames.stock_draws)
    report["stand_in_draws"] = dict(splash_frames.stand_in_draws)
    report["success"] = not error and bool(report["checks"]) and all(report["checks"].values())
    report["failed_checks"] = [name for name, ok in report["checks"].items() if not ok]
    (ROOT / (PHASE + ".json")).write_text(json.dumps(report, indent=2, default=str))
    print("ANGELO_FIRST_LAUNCH_FRAMES_PHASE", json.dumps({"phase": PHASE, "success": report["success"]}), flush=True)
    bpy.ops.wm.quit_blender()


def start(phases, at_python=None):
    """Run ``phases[PHASE]``; ``at_python[PHASE]``, if any, first sets its fixture up now, before the splash exists."""
    setup = (at_python or {}).get(PHASE)
    if setup is not None:
        setup()
    steps, state = phases[PHASE](), {"until": None, "since": 0.0, "sample": None}

    def tick():
        try:
            assert time.time() - LAUNCHED < 240, "Phase timed out"
            sample = state["sample"]
            if sample is not None:
                frame(sample.tag)
                if time.monotonic() - state["since"] < sample.seconds:
                    return 0.0
                state["sample"] = None
            until = state["until"]
            if until is not None:
                if not until.condition():
                    assert time.monotonic() - state["since"] < until.limit, "Timed out waiting for " + until.what
                    return until.poll
                state["until"] = None
            step = next(steps)
            if isinstance(step, (Until, Sample)):
                state.update(since=time.monotonic(), **{"until" if isinstance(step, Until) else "sample": step})
                return 0.0
            return step
        except StopIteration:
            finish()
        except Exception:
            finish(traceback.format_exc())
        return None

    # Persistent: a phase opens a file, which drops every other timer.
    bpy.app.timers.register(watch, first_interval=0.0, persistent=True)
    bpy.app.timers.register(tick, first_interval=0.0, persistent=True)

