# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The lead's capture shows every region of every editor as the window shows it (``live_view.window_pixels``, B-008).

While parallel-edit workers run, the agent's screenshot (``view_editor.screenshot``) is the window drawn again into an
offscreen without the live view and the rail (``live_view_draw.capture``). Before 1.0.8 that redraw drew each region
without laying it out first: the draw that follows a layout marks the region's UI blocks inactive and only active
blocks draw, so every region with a layout (panels, headers, the top bar) came out empty. The agent saw blank
Preferences in all 14 sections, a blank Properties editor and an empty Director top bar.

A real window in Director, Angelo's default view, with no account, provider or network:

* **walk**: the main 3D view becomes each editor in ``EDITORS`` in turn, with its sidebar and tool header shown where it
  has them; Properties goes through each of its tabs. Each step takes two captures in a row, because a second capture
  after the first was empty too. It then redraws and presents the window and takes Blender's own screenshot
  (``screen.screenshot``). Each visible region of the editor and of the top bar is compared, crop by crop.
* **preferences**: the Preferences window, each section in turn, compared the same way.
* **workers**: two stand-in workers bound to the rail (``rail_positions_fixture.bind``, no worker process), then the
  agent's own path (``view_editor.screenshot``) for Properties, the 3D view, the Director top bar and Preferences.
  The rail draws over the 3D view on screen and never in the capture, so that region is compared with the capture
  taken before the workers were bound.

A region matches when at most ``MISMATCH_SHARE`` of its pixels differ by more than ``PIXEL_TOLERANCE`` in a channel.
Blender's screenshot is the reference only once it shows the step (``current_screenshot``: macOS presents nothing while
the window is occluded or the display sleeps); a step without one fails ``screen_shows_this_step`` and is not judged.
The capture must also hold content wherever the screenshot does (``not_empty_where_shown``: the bug's own signature).
``side-by-side/*.png`` holds capture | screenshot sheets, ``failed/*.png`` each failing region's crops.

Run through ``run_native_suites.py --gui --include test_blender_window_pixels_gui``; check
``window-pixels-gui.json`` for ``success`` and ``phase == "complete"``.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import addon_utils
import bpy

assert not bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-window-pixels-gui-qa"))
OUT.mkdir(parents=True, exist_ok=True)
SHEETS = OUT / "side-by-side"
SHEETS.mkdir(exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"))
os.environ.setdefault("ANGELO_DISABLE_INTRO", "1")
os.environ.setdefault("ANGELO_DISABLE_HERO", "1")
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import product_env  # noqa: E402
product_env.clear("DISABLE_DEFAULT_CHAT")   # Director with its chat, as Angelo starts.
bpy.context.preferences.use_preferences_save = False
bpy.context.preferences.view.show_splash = False
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
import qa_boot  # noqa: E402
import qa_frames  # noqa: E402  (a reference is a drawn frame)
import rail_positions_fixture as fx  # noqa: E402
from angelo_agent import director, live_view, live_view_draw, parallel_sessions, ui, view_editor  # noqa: E402
from angelo_agent.view_png import encode_png  # noqa: E402

REPORT = OUT / "window-pixels-gui.json"
PIXEL_TOLERANCE = 32       # 8-bit steps: a pixel differs only when a channel is off by more than this
MISMATCH_SHARE = 0.02      # of a region's pixels: a caret or a hover may differ, an empty panel differs by far more
CONTENT_STEP = 16          # 8-bit steps from the region's commonest colour that count as drawn content
SETTLE = 0.6               # seconds after a change before a capture
REFERENCE_WAIT = 6.0       # seconds a step waits for the screenshot to show it (``current_screenshot``)
# label, the walk area's ui_type; each shows its sidebar, tool header, channels ... where it has them (SHOWN)
EDITORS = (("3d_view", 'VIEW_3D'), ("outliner", 'OUTLINER'), ("shader_editor", 'ShaderNodeTree'),
           ("geometry_nodes", 'GeometryNodeTree'), ("text_editor", 'TEXT_EDITOR'), ("image_editor", 'IMAGE_EDITOR'),
           ("uv_editor", 'UV'), ("dope_sheet", 'DOPESHEET'), ("timeline", 'TIMELINE'), ("graph_editor", 'FCURVES'),
           ("drivers", 'DRIVERS'), ("nla", 'NLA_EDITOR'), ("sequencer", 'SEQUENCE_EDITOR'),
           ("movie_clip", 'CLIP_EDITOR'), ("spreadsheet", 'SPREADSHEET'), ("file_browser", 'FILES'), ("info", 'INFO'))
SHOWN = ("show_region_header", "show_region_tool_header", "show_region_toolbar", "show_region_ui",
         "show_region_channels")   # Not the HUD: it shows only after an operator.
PROPERTIES_TABS = ('TOOL', 'RENDER', 'OUTPUT', 'VIEW_LAYER', 'SCENE', 'WORLD', 'COLLECTION', 'OBJECT', 'MODIFIER',
                   'PARTICLES', 'PHYSICS', 'CONSTRAINT', 'DATA', 'MATERIAL', 'TEXTURE')
state = {"phase": "boot", "success": False, "checks": {}, "details": {}, "regions": [], "sheets": []}
checks, details = state["checks"], state["details"]
_last_seen = {}   # window pointer -> the previous step's screenshot


def check(name, value, detail=None):
    checks[name] = bool(value)
    if detail is not None:
        details[name] = detail
    return bool(value)


def pause(seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        yield


def until(condition, seconds):
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            return False
        yield
    return True


def rows(data, width, height):
    import numpy
    return numpy.frombuffer(data, dtype=numpy.uint8).reshape(height, width, 4)[..., :3].copy()


def capture(window, *, agent=False):
    """The lead's capture of ``window``: RGB rows, bottom row first. ``agent``: through the agent's own screenshot
    (``view_editor.screenshot``, the capture while workers run), else ``live_view_draw.capture`` directly."""
    if agent:
        return view_editor.screenshot(window, OUT / "agent.png")[..., :3].copy()
    width, height, data = live_view_draw.capture(window)
    return rows(data, width, height)


def screenshot(window):
    """Blender's own screenshot of ``window`` after a presented redraw: RGB rows, bottom row first."""
    import imbuf
    import numpy
    path = OUT / "screen.png"
    with bpy.context.temp_override(window=window):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        if "FINISHED" not in bpy.ops.screen.screenshot(filepath=str(path), check_existing=False):
            raise RuntimeError("Blender could not capture the window")
    buffer = imbuf.load(str(path))
    try:
        with buffer.with_buffer() as view:
            found = numpy.array(view, dtype=numpy.uint8)
    finally:
        buffer.free()
    return found[..., :3].copy()


def background(crop):
    """``crop``'s commonest colour."""
    import numpy
    colours, counts = numpy.unique(crop.reshape(-1, 3), axis=0, return_counts=True)
    return colours[counts.argmax()].astype(numpy.int16)


def content_share(crop, common):
    """The share of ``crop``'s pixels away from the colour ``common``: near 0 for an empty region. Both crops of a
    region are measured from the screen's background, never each from its own: a composite one level off moved a
    footer's 1-pixel line across ``CONTENT_STEP`` (16 levels from the capture's 48, 17 from the screen's 47)."""
    import numpy
    flat = crop.reshape(-1, 3)
    return float((numpy.abs(flat.astype(numpy.int16) - common).max(axis=1) > CONTENT_STEP).mean()) if len(flat) else 0.0


def compare(label, window, area, shot, seen):
    """Each visible region of ``area`` in the capture ``shot`` against the screenshot ``seen``."""
    import numpy
    if shot.shape != seen.shape:
        check(label + "__capture_has_the_screen_size", False, {"capture": shot.shape, "screen": seen.shape})
        return
    scale = seen.shape[1] / float(window.width)
    for region in area.regions:
        if region.width < 4 or region.height < 4:
            continue
        x0, y0 = round(region.x * scale), round(region.y * scale)
        x1, y1 = round((region.x + region.width) * scale), round((region.y + region.height) * scale)
        a, b = shot[y0:y1, x0:x1].astype(numpy.int16), seen[y0:y1, x0:x1].astype(numpy.int16)
        if a.size == 0:
            continue
        mismatch = float((numpy.abs(a - b).max(axis=2) > PIXEL_TOLERANCE).mean())
        common = background(b)
        drawn, shown = content_share(a, common), content_share(b, common)
        name = "%s__%s_%s" % (label, area.type.lower(), region.type.lower())
        record = {"region": name, "size": [x1 - x0, y1 - y0], "mismatch": round(mismatch, 4),
                  "content_capture": round(drawn, 4), "content_screen": round(shown, 4)}
        state["regions"].append(record)
        empty = not check(name + "__not_empty_where_shown", not (shown > 0.01 and drawn < shown * 0.5), record)
        differs = not check(name + "__matches_the_screen", mismatch <= MISMATCH_SHARE, record)
        if empty or differs:
            crops(name, a.astype(numpy.uint8), b.astype(numpy.uint8))


def crops(name, a, b):
    """A failing region's capture | screenshot crops, at full size, in ``failed/``."""
    import numpy
    folder = OUT / "failed"
    folder.mkdir(exist_ok=True)
    pair = numpy.concatenate([a, numpy.full((a.shape[0], 4, 3), 255, numpy.uint8), b], axis=1)[:4096, :4096]
    rgba = numpy.concatenate([pair, numpy.full(pair.shape[:2] + (1,), 255, numpy.uint8)], axis=2)
    (folder / (name + ".png")).write_bytes(encode_png(numpy.ascontiguousarray(rgba).tobytes(), rgba.shape[1],
                                                      rgba.shape[0], bottom_up=True))


def sheet(name, shot, seen):
    """``capture | screenshot`` side by side, at most 4096 pixels a side (``encode_png``)."""
    import numpy
    pair = numpy.concatenate([shot, numpy.full((shot.shape[0], 8, 3), 255, numpy.uint8), seen], axis=1)
    step = max(1, -(-max(pair.shape[:2]) // 4096))
    pair = numpy.ascontiguousarray(pair[::step, ::step])
    rgba = numpy.concatenate([pair, numpy.full(pair.shape[:2] + (1,), 255, numpy.uint8)], axis=2)
    (SHEETS / (name + ".png")).write_bytes(encode_png(rgba.tobytes(), rgba.shape[1], rgba.shape[0], bottom_up=True))
    state["sheets"].append(name + ".png")


def topbar(window):
    return next((a for a in window.screen.areas if a.type == 'TOPBAR'), None)


def current_screenshot(label, window, changed):
    """The screenshot once it shows this step. Blender's screenshot reads the last frame macOS presented: it lags while
    the window is occluded or the display sleeps (a run at 01:55 showed the Spreadsheet in every Properties tab), and
    by a tick in a window that was just changed (with workers on the rail, Preferences showed the section before).
    So the reference is a frame that two screenshots a tick apart agree on and that differs from the previous step's
    (each step changes what the window shows); without one in ``REFERENCE_WAIT``, the step is reported, not judged."""
    import numpy
    key = window.as_pointer()
    previous = _last_seen.get(key)
    seen, current = screenshot(window), False
    deadline = time.monotonic() + REFERENCE_WAIT
    while time.monotonic() < deadline:
        yield from pause(0.25)
        again = screenshot(window)
        steady, seen = numpy.array_equal(again, seen), again
        # A drawn window is never one flat colour: a just-opened window's stale frame (its creation clear) is no
        # reference (1.0.8: Metal screenshots read one frame behind, so a blank reference was accepted).
        if steady and qa_frames.drawn(seen) and not (changed and previous is not None
                                                      and numpy.array_equal(seen, previous)):
            current = True
            break
    _last_seen[key] = seen
    check(label + "__screen_shows_this_step", current)
    return seen if current else None


def measure(label, window, areas, *, agent=False, keep=False, changed=True):
    """Two captures in a row, then the screenshot; every region of ``areas`` compared for both captures."""
    yield from pause(SETTLE)
    first = capture(window, agent=agent)
    second = capture(window, agent=agent)
    seen = yield from current_screenshot(label, window, changed)
    if seen is None:
        return   # No reference for this step: never judge the capture against an old frame.
    for area in areas:
        if area is None:
            continue
        compare(label, window, area, first, seen)
        compare(label + "__again", window, area, second, seen)
    if keep:
        sheet(label, first, seen)


def in_context(window, area, owner, name, value):
    """``owner.name = value`` in the window's and area's context, as a click sets it: an editor's type and a region's
    visibility update the area from the context's window, and a timer has none (Blender crashed on ``show_region_hud``
    set from one, 1.0.7 and 1.0.8 alike)."""
    with bpy.context.temp_override(window=window, area=area):
        setattr(owner, name, value)


def show_regions(window, area):
    """Each region ``SHOWN`` the editor has, shown."""
    space = area.spaces.active
    for name in SHOWN:
        if hasattr(space, name) and not space.is_property_readonly(name):
            try:
                in_context(window, area, space, name, True)
            except (AttributeError, TypeError, RuntimeError):
                pass


def prepare(window, area, ui_type):
    in_context(window, area, area, "ui_type", ui_type)
    show_regions(window, area)
    space = area.spaces.active
    if ui_type == 'TEXT_EDITOR':
        text = bpy.data.texts.get("window pixels") or bpy.data.texts.new("window pixels")
        text.from_string("\n".join("line %d: the capture shows every region" % n for n in range(40)))
        space.text = text
    area.tag_redraw()


def restore(window, area, ui_type):
    in_context(window, area, area, "ui_type", ui_type)
    if ui_type == 'VIEW_3D':
        from angelo_agent import director_layout
        with bpy.context.temp_override(window=window, area=area):
            director_layout.configure(window)   # Director's own 3D view again: no header, toolbar or sidebar.


def walk(window, area):
    state["phase"] = "walk"
    original = area.ui_type
    try:
        for label, ui_type in EDITORS:
            prepare(window, area, ui_type)
            yield from measure(label, window, (area, topbar(window)), keep=label == "3d_view")
        prepare(window, area, 'PROPERTIES')
        space = area.spaces.active
        for tab in PROPERTIES_TABS:
            try:
                in_context(window, area, space, "context", tab)
            except TypeError:
                continue   # Not offered for the active object.
            yield from measure("properties_" + tab.lower(), window, (area,), keep=tab == 'OBJECT')
    finally:
        restore(window, area, original)


def preferences():
    """The Preferences window, opened as the user opens it."""
    before = {w.as_pointer() for w in bpy.context.window_manager.windows}
    with bpy.context.temp_override(window=ui.main_window()):
        bpy.ops.screen.userpref_show()
    found = []

    def opened():
        found[:] = [w for w in bpy.context.window_manager.windows if w.as_pointer() not in before]
        return bool(found)
    yield from until(opened, 10)
    return found[0] if found else None


def sections():
    return [item.identifier for item in bpy.types.Preferences.bl_rna.properties["active_section"].enum_items]


def preference_sections(window, label, *, agent=False, only=None):
    state["phase"] = label
    for section in only or sections():
        try:
            in_context(window, window.screen.areas[0], bpy.context.preferences, "active_section", section)
        except TypeError:
            continue   # A section this build does not offer (Developer Tools without developer extras).
        for area in window.screen.areas:
            area.tag_redraw()
        yield from measure("%s_%s" % (label, section.lower()), window, tuple(window.screen.areas), agent=agent,
                           keep=agent or section == 'INTERFACE')


def close(window):
    with bpy.context.temp_override(window=window):
        bpy.ops.wm.window_close()


def workers(window, area):
    """The agent's own capture path, with stand-in workers on the rail."""
    state["phase"] = "workers"
    lead, bound = ui.get_controller(), []
    yield from pause(SETTLE)
    alone = capture(window)   # The lead's view before any worker: what the capture must show with the rail up.
    fx.bind(lead, bound, 2)
    yield from pause(1.0)
    check("workers_route_the_agent_screenshot_through_the_capture", bool(parallel_sessions.sessions())
          and live_view.available() and hasattr(live_view._native(), "window_pixels"),
          {"sessions": list(parallel_sessions.sessions())})
    yield from measure("workers_3d_view", window, (topbar(window),), agent=True, keep=True)
    # The rail shows on screen and never in the capture: the 3D view is held to the lead's view without workers.
    compare("workers_3d_view_without_the_rail", window, area, capture(window, agent=True), alone)
    yield from measure("workers_director_top_bar", window, (topbar(window),), agent=True, keep=True, changed=False)
    original = area.ui_type
    prepare(window, area, 'PROPERTIES')
    in_context(window, area, area.spaces.active, "context", 'OBJECT')
    yield from measure("workers_properties", window, (area,), agent=True, keep=True)
    restore(window, area, original)
    prefs = yield from preferences()
    if check("workers_preferences_window_opened", prefs is not None):
        yield from preference_sections(prefs, "workers_preferences", agent=True,
                                       only=('INTERFACE', 'ADDONS', 'SYSTEM'))
        close(prefs)
    fx.unbind(window, bound, 0)


def run():
    from angelo_agent import parallel_access
    ok = yield from until(lambda: qa_boot.started() and ui.main_window() is not None
                          and director.is_director(ui.main_window()), 90)
    if not check("started_in_director", ok, qa_boot.waiting_on()):
        return
    window = ui.main_window()
    area = live_view_draw.target(window)
    if not check("main_3d_view_found", area is not None):
        return
    state["window"] = [window.width, window.height]
    state["phase"] = "director"
    yield from measure("director", window, tuple(window.screen.areas), keep=True)
    yield from walk(window, area)
    prefs = yield from preferences()
    if check("preferences_window_opened", prefs is not None):
        yield from preference_sections(prefs, "preferences")
        close(prefs)
    yield from pause(0.5)
    if check("parallel_edit_is_on_here", parallel_access.enabled(), "needs this machine above the bar"):
        yield from workers(window, live_view_draw.target(window))
    state["phase"] = "complete"


def tick(steps=[]):   # noqa: B006 - the generator lives across timer calls.
    try:
        if not steps:
            steps.append(run())
        next(steps[0])
        return .05
    except StopIteration:
        pass
    except Exception:   # noqa: BLE001 - recorded, the run fails.
        state["error"] = traceback.format_exc()
    state["success"] = state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed"] = sorted(name for name, ok in checks.items() if not ok)
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_WINDOW_PIXELS_GUI", json.dumps({"success": state["success"], "phase": state["phase"],
                                                  "failed": len(state["failed"])}), flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(tick, first_interval=2)
