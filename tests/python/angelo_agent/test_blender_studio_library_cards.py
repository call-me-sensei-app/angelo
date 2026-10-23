# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's library row asks in centred Angelo cards in the running app (1.0.5 review L1-L4).

Real default startup; no CLI, account, network or inference. The Asset Browser library is seeded with two copies
(files Angelo would have written) in the isolated state folder, Media Studio opens in its own window, and with real
clicks on the cards' buttons:

* Turn off asks in a card centred over Media Studio (its fill is the card colour, its confirm coral while it deletes);
  unticking Also delete its copies turns the confirm teal; Turn off keeps the copies and the row says
  "Off · {size} kept" with Free up space;
* Free up space asks in a destructive card that says Media Studio keeps everything; it frees the copies;
* removing the library in Preferences asks in a card over the Preferences window;
* Turn off's card in German and Arabic.

Captures ``library-*.png``. Run through ``run_native_suites.py --gui --include test_blender_studio_library_cards``;
check ``studio-library-cards.json`` for ``success`` and ``phase == "complete"``.
"""

import json
import os
import tempfile
import time
import traceback
import uuid
from pathlib import Path

import bpy

import sys  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_frames, inside the app.

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

product_env.clear('DISABLE_DEFAULT_CHAT')
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-studio-library-cards-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "studio-library-cards.json"
prefs = bpy.context.preferences
prefs.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "observed": {}}
checks = state["checks"]
started = time.monotonic()
COPY_BYTES = 2 * 1024 * 1024


def windows():
    return bpy.context.window_manager.windows


def studio_area():
    from angelo_agent import studio
    return next((a for w in windows() for a in (w.screen.areas if w.screen else ()) if studio.is_studio(a)), None)


def window_of(area):
    return next((w for w in windows() if w.screen and area in w.screen.areas[:]), None) if area else None


def capture(name, target):
    path = OUT / name
    with bpy.context.temp_override(window=target):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return path


def pixels(path):
    import numpy
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        values = numpy.empty(width * height * 4, dtype=numpy.float32)
        image.pixels.foreach_get(values)
    finally:
        bpy.data.images.remove(image)
    return (values.reshape(height, width, 4)[..., :3] * 255).round().astype(numpy.int32)


def rgb(color):
    return [round(c * 255) for c in color[:3]]


def near(a, b, tolerance=8):
    return max(abs(int(x) - int(y)) for x, y in zip(a, b)) <= tolerance


def seed_copies():
    """Two copies in the library's folder, as the writer leaves them (the ids are fresh UUIDs, files Angelo names)."""
    from angelo_agent import studio_assets_host as host, studio_assets_space as space
    _library, directory = host.directories()
    folder = directory / "models"
    folder.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        (folder / (str(uuid.uuid4()) + ".blend")).write_bytes(b"\0" * (COPY_BYTES // 2))
    space.forget_size()


def card():
    from angelo_agent import card_host
    return card_host._active if card_host.is_active("studio_library") else None


def drawn_capture(name, target):
    """Whether a capture of ``target`` shows drawn content, not one flat colour (qa_frames): a window kept behind other
    applications captured blank grey while the window server showed its card (d-round line, d8e801755eb)."""
    import qa_frames
    return qa_frames.drawn(pixels(capture(name, target)))


def drawn_x(region, x):
    """Where layout x ``x`` is drawn: a right-to-left interface (Arabic) mirrors Angelo's canvases across the region,
    and card_host mirrors the pointer back before hit-testing (canvas_rtl), so samples and clicks mirror too."""
    from angelo_agent import ui_text
    return region.width - x if ui_text.is_rtl() else x


def judge_card(name, target, area, confirm_style):
    """Capture the visible card and read it: centred over ``area``, card-coloured, its confirm in ``confirm_style``."""
    from angelo_agent import card_draw, card_kit as kit
    active = card()
    path = capture(name, target)
    px = pixels(path)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    x, y, w, h = active.plan.card
    centre = (region.x + x + w / 2, region.y + y + h / 2)
    middle = (region.x + region.width / 2, region.y + region.height / 2)
    fill = px[int(region.y + y + h - 8), int(region.x + x + w / 2)]
    confirm = next(op for op in active.plan.ops if op[0] == "button" and op[3] != "secondary")
    bx, by, bw, bh = confirm[1]
    button = px[int(region.y + by + bh * 0.2), int(region.x + drawn_x(region, bx + 6))]
    key = name.rsplit(".", 1)[0]
    state["observed"][key] = {"card": list(active.plan.card), "region": [region.width, region.height],
                              "fill": fill.tolist(), "button": button.tolist(), "style": confirm[3]}
    checks[key + "_is_centred"] = abs(centre[0] - middle[0]) <= 2 and abs(centre[1] - middle[1]) <= 12 * active.plan.scale
    checks[key + "_is_a_card"] = near(fill, rgb(kit.CARD))
    checks[key + "_confirm_is_" + confirm_style] = confirm[3] == confirm_style and near(
        button, rgb(card_draw.BUTTON_FILL[confirm_style][0]), 14)


def click(action, area):
    """A real click on the visible card's control ``action``."""
    active = card()
    region = next(r for r in area.regions if r.type == 'WINDOW')
    (x, y, w, h), _action, _value = next(hit for hit in active.plan.hits if hit[1] == action)
    import qa_input   # The pointer arrives, then the press a pass later; mirrored where the card is (drawn_x).
    qa_input.press(window_of(area), region.x + drawn_x(region, x + w / 2), region.y + y + h / 2)


def invoke(operator, area):
    target = window_of(area)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=target, screen=target.screen, area=area, region=region):
        getattr(bpy.ops.angelo, operator)('INVOKE_DEFAULT')


def finish(error=None):
    prefs.view.language = "en_US"
    if error:
        state["error"] = "".join(traceback.format_exception(error)).replace("Traceback (most recent", "Trace (most recent")
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_STUDIO_LIBRARY_CARDS", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(phase, condition, limit=20):
    since = state.setdefault("since", {}).setdefault(phase, time.monotonic())
    if condition():
        return True
    if time.monotonic() - since > limit:
        raise AssertionError("Timed out in " + phase)
    return False


def go(phase, delay=.6):
    state["phase"] = phase
    return delay


def tick():
    from angelo_agent import studio_assets_host as host, studio_assets_space as space, studio_library_ops as ops
    phase = state["phase"]
    try:
        assert time.monotonic() - started < 240, "Suite timed out"
        area = studio_area()
        if phase == "boot":
            seed_copies()
            host.ensure_registered()
            bpy.ops.angelo.open_media_studio(kind='AUTO')
            return go("opened", 1.0)
        if phase == "opened":
            if not wait(phase, lambda: studio_area() is not None and space.size(host.directories()[1])):
                return .3
            capture("library-0-on.png", window_of(area))
            invoke("studio_library_off", area)
            return go("off_card")
        if phase == "off_card":
            if not wait(phase, lambda: card() is not None and card().plan is not None):
                return .2
            if not wait(phase + "_drawn", lambda: drawn_capture("library-1-turn-off.png", window_of(area))):
                return .3   # A window behind others can capture blank for a while: judge a drawn frame.
            judge_card("library-1-turn-off.png", window_of(area), area, "destructive")
            click("free", area)   # Untick Also delete its copies.
            return go("off_keep", .5)
        if phase == "off_keep":
            if not wait(phase + "_drawn", lambda: drawn_capture("library-2-turn-off-keep.png", window_of(area))):
                return .3   # A window behind others can capture blank for a while: judge a drawn frame.
            judge_card("library-2-turn-off-keep.png", window_of(area), area, "primary")
            click("confirm", area)
            return go("off_done", 1.0)
        if phase == "off_done":
            if not wait(phase, lambda: card() is None and not ops.active()):
                return .3
            checks["turn_off_kept_the_copies"] = bool(space.measure(host.directories()[1]))
            capture("library-3-off-kept.png", window_of(area))
            invoke("studio_library_free", area)
            return go("free_card")
        if phase == "free_card":
            if not wait(phase, lambda: card() is not None and card().plan is not None):
                return .2
            if not wait(phase + "_drawn", lambda: drawn_capture("library-4-free-up.png", window_of(area))):
                return .3   # A window behind others can capture blank for a while: judge a drawn frame.
            judge_card("library-4-free-up.png", window_of(area), area, "destructive")
            click("confirm", area)
            return go("freed", 1.0)
        if phase == "freed":
            if not wait(phase, lambda: card() is None and not space.measure(host.directories()[1])):
                return .3
            checks["free_up_freed_the_copies"] = True
            capture("library-5-freed.png", window_of(area))
            host.turn_on()
            seed_copies()
            return go("prefs_open", 1.5)
        if phase == "prefs_open":
            if not wait(phase, lambda: ops.active()):
                return .3
            bpy.ops.screen.userpref_show(section='FILE_PATHS')
            return go("prefs_remove", 1.0)
        if phase == "prefs_remove":
            from angelo_agent import studio_assets
            libraries = prefs.filepaths.asset_libraries
            entry = studio_assets.find_library(libraries, host.directories()[1])
            if entry is not None:
                libraries.remove(entry)   # The person's own removal, as the Preferences row's minus button does.
            return go("prefs_card", 1.0)
        if phase == "prefs_card":
            if not wait(phase, lambda: card() is not None and card().plan is not None, limit=25):
                return .3
            prefs_window = next(w for w in windows() if any(a.type == 'PREFERENCES' for a in w.screen.areas))
            prefs_area = next(a for a in prefs_window.screen.areas if a.type == 'PREFERENCES')
            checks["the_preferences_question_shows_in_preferences"] = card().area == prefs_area.as_pointer()
            if not wait(phase + "_drawn", lambda: drawn_capture("library-6-preferences.png", prefs_window)):
                return .3   # A window behind others can capture blank for a while: judge a drawn frame.
            judge_card("library-6-preferences.png", prefs_window, prefs_area, "destructive")
            click("cancel", prefs_area)
            state["languages"] = ["de_DE", "ar_EG"]
            return go("language", 1.0)
        if phase == "language":
            if not state["languages"]:
                state["phase"] = "complete"
                finish()
                return None
            if card() is not None:
                return .3
            language = state["languages"][0]
            if prefs.view.language != language:
                prefs.view.language = language
                return .6
            host.turn_on()
            seed_copies()
            return go("language_card_open", 1.0)
        if phase == "language_card_open":
            if not wait(phase + state["languages"][0], lambda: ops.active() and space.size(host.directories()[1])):
                return .3
            invoke("studio_library_off", area)
            return go("language_card")
        if phase == "language_card":
            if not wait(phase + state["languages"][0], lambda: card() is not None and card().plan is not None):
                return .2
            code = state["languages"].pop(0)[:2]
            if not wait(phase + "_drawn", lambda: drawn_capture("library-7-turn-off-%s.png" % code, window_of(area))):
                return .3   # A window behind others can capture blank for a while: judge a drawn frame.
            judge_card("library-7-turn-off-%s.png" % code, window_of(area), area, "destructive")
            shown = " ".join(op[1] for op in card().plan.ops if op[0] == "text")
            checks["turn_off_is_translated_%s" % code] = (ops.iface_(ops.TURN_OFF_TITLE) != ops.TURN_OFF_TITLE
                                                          and ops.TURN_OFF_TITLE not in shown)
            click("cancel", area)
            return go("language", .8)
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=1.0, persistent=True)
