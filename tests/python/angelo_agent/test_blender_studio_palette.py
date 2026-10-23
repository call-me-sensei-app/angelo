# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio wears the shared design tokens in the running app (1.0.5, doc/angelo/studio_entry_points.rst §4).

Real default startup. No CLI, account, network or inference. Media Studio opens in its own window
(``studio_toggle.open_media_studio``) and is captured on both tabs, with an empty library and with a populated grid
(three imported images, a finished and a generating 3D model), in English and German at UI scale 1 and 2, then in
the Angelo Light theme. *Before* captures draw the same layout with the earlier violet values patched in, so the pair
shows only the palette change. Checked by pixels in each capture: no violet left (hue 250-320 degrees, saturated),
where the before captures are mostly violet (the measure sees it); the selected tab's label and the Create column's
text read against what they sit on (contrast ratio at least 4.5, WCAG AA) in the dark and the Light themes; the strip
between Refresh and Import is plain background in every capture (ux-coherence D1: a stray "…" sat there).

Run through ``run_native_suites.py --gui --include test_blender_studio_palette``; check ``studio-palette.json`` for
``success`` and ``phase == "complete"``.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))   # qa_frames

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

product_env.clear('DISABLE_DEFAULT_CHAT')
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-studio-palette-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "studio-palette.json"
prefs = bpy.context.preferences
prefs.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "captures": {}}
checks = state["checks"]
started = time.monotonic()
# The palette before 1.0.5's swap, for the before captures only (studio.py's former constants).
VIOLET = {"BG": (0.047, 0.028, 0.073, 1), "PANEL": (0.081, 0.047, 0.115, 1), "FIELD": (0.065, 0.038, 0.096, 1),
          "LINE": (0.17, 0.12, 0.23, 1), "HOVER": (0.15, 0.105, 0.225, 1), "ACCENT": (0.33, 0.25, 0.93, 1),
          "ACCENT_HOVER": (0.33, 0.25, 0.93, 1), "ACCENT_TEXT": (0.93, 0.91, 0.97, 1),
          "FOCUS": (0.33, 0.25, 0.93, 1), "SELECTED": (0.16, 0.105, 0.31, 1), "TEXT": (0.93, 0.91, 0.97, 1),
          "MUTED": (0.60, 0.55, 0.68, 1), "GREEN": (0.42, 0.80, 0.63, 1), "ERROR": (1.0, 0.55, 0.55, 1),
          "SCRIM": (0, 0, 0, .65)}
SHOTS = []   # (name, palette, populated, tab, language, scale, theme), in capture order.
for populated in (False, True):
    for tab in ("IMAGE", "3D"):
        SHOTS.append(("before", "violet", populated, tab, "en_US", 1.0, "dark"))
        for language in ("en_US", "de_DE"):
            for scale in (1.0, 2.0):
                SHOTS.append(("after", "tokens", populated, tab, language, scale, "dark"))
        SHOTS.append(("after", "tokens", populated, tab, "ar_EG", 1.0, "dark"))
        SHOTS.append(("light", "tokens", populated, tab, "en_US", 1.0, "light"))


def windows():
    return bpy.context.window_manager.windows


def studio_area():
    """Media Studio's area wherever it shows: its own window (Director) or Manual's Media Studio tab."""
    from angelo_agent import studio
    return next((a for w in windows() for a in (w.screen.areas if w.screen else ()) if studio.is_studio(a)), None)


def studio_window():
    area = studio_area()
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


def violet_share(px):
    """Share of pixels whose hue is violet (250-320 degrees) and visibly coloured (saturation above 0.25)."""
    import numpy
    rgb = px.reshape(-1, 3).astype(numpy.float64) / 255.0
    high, low = rgb.max(axis=1), rgb.min(axis=1)
    span = numpy.where(high > low, high - low, 1.0)
    r, g, b = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    hue = numpy.where(high == r, ((g - b) / span) % 6, numpy.where(high == g, (b - r) / span + 2, (r - g) / span + 4)) * 60
    saturation = numpy.where(high > 0, (high - low) / numpy.where(high > 0, high, 1.0), 0.0)
    violet = (hue >= 250) & (hue <= 320) & (saturation > .25) & (high > .05) & (high > low)
    return float(violet.mean())


def luminance(rgb):
    def channel(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(float(v)) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(px, rect):
    """WCAG contrast between a box's most common colour (what text sits on) and its farthest colour (the text)."""
    import numpy
    x, y, w, h = (int(round(v)) for v in rect)
    block = px[max(0, y):y + h, max(0, x):x + w].reshape(-1, 3)
    if not block.size:
        return 0.0
    keys = block[:, 0] * 65536 + block[:, 1] * 256 + block[:, 2]
    values, counts = numpy.unique(keys, return_counts=True)
    ground = numpy.array([values[counts.argmax()] // 65536, values[counts.argmax()] // 256 % 256,
                          values[counts.argmax()] % 256])
    far = block[numpy.abs(block - ground).sum(axis=1).argmax()]
    a, b = luminance(ground), luminance(far)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def uniform(px, rect):
    """Share of a box's pixels within 30 of its most common colour: 1.0 for plain background, lower with a glyph."""
    import numpy
    x, y, w, h = (int(round(v)) for v in rect)
    block = px[max(0, y):y + h, max(0, x):x + w].reshape(-1, 3)
    if not block.size:
        return None
    keys = block[:, 0] * 65536 + block[:, 1] * 256 + block[:, 2]
    values, counts = numpy.unique(keys, return_counts=True)
    mode = values[counts.argmax()]
    ground = numpy.array([mode // 65536, mode // 256 % 256, mode % 256])
    return float((numpy.abs(block - ground).sum(axis=1) <= 30).mean())


def refresh_gap(view, region):
    """The strip between the Refresh and Import buttons in window pixels (inset 3 px from each button), or None."""
    rects = {action: r for r, action, _value in view.get("hits", []) if action in {"refresh", "import"}}
    if len(rects) < 2:
        return None
    refresh, imported = rects["refresh"], rects["import"]
    left, right = refresh[0] + refresh[2] + 1, imported[0] - 1
    return (region.x + left, region.y + refresh[1] + 3, right - left, refresh[3] - 6) if right > left else None


def set_palette(name):
    from angelo_agent import studio, studio_draw
    if "tokens" not in state:
        state["tokens"] = {key: getattr(studio, key) for key in VIOLET}
    values = VIOLET if name == "violet" else state["tokens"]
    for module in (studio, studio_draw):
        for key, value in values.items():
            if hasattr(module, key):
                setattr(module, key, value)


def set_theme(look):
    from angelo_agent import setup_card
    path = setup_card.look_preset_path(bpy, look)
    if path:
        bpy.ops.script.execute_preset(filepath=path, menu_idname="USERPREF_MT_interface_theme_presets")
    state.setdefault("themes", {})[look] = bool(path)


def populate():
    """Three imported images and two 3D models (one finished, one generating) in Media Studio's library."""
    from angelo_agent import studio
    library = studio.library()
    for index, colour in enumerate(((0.85, 0.45, 0.20, 1), (0.30, 0.65, 0.35, 1), (0.55, 0.55, 0.55, 1))):
        image = bpy.data.images.new("Palette fixture %d" % index, 64, 64)
        image.pixels.foreach_set([c for _ in range(64 * 64) for c in colour])
        path = OUT / ("fixture-%d.png" % index)
        image.filepath_raw, image.file_format = str(path), 'PNG'
        image.save()
        bpy.data.images.remove(image)
        library.import_image(path)
    library.add("3D", "A mossy boulder", "Fixture", status="ready", progress=100)
    library.add("3D", "A wooden crate", "Fixture", status="generating", progress=40)


def finish(error=None):
    try:
        set_palette("tokens")
        set_theme("dark")
        prefs.view.language, prefs.view.ui_scale = "en_US", 1.0
    except Exception:   # noqa: BLE001 (restoring never hides the suite's own result)
        pass
    if error:
        state["error"] = "".join(traceback.format_exception(error)).replace("Traceback (most recent", "Trace (most recent")
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_STUDIO_PALETTE", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(phase, condition, limit=20):
    since = state.setdefault("since", {}).setdefault(phase, time.monotonic())
    if condition():
        return True
    if time.monotonic() - since > limit:
        raise AssertionError("Timed out in " + phase)
    return False


def tick():
    from angelo_agent import director, studio, studio_toggle, ui
    phase = state["phase"]
    try:
        assert time.monotonic() - started < 300, "Suite timed out"
        if phase == "boot":
            if not qa_boot.started():   # Director's layout and Manual's tabs (qa_boot).
                return .3
            studio_toggle.open_media_studio("image", windows()[0])
            state["phase"], state["shot"] = "shoot", 0
            return .8
        if phase == "shoot":
            if not wait(phase, lambda: studio_area() is not None and studio.view(studio_area()).get("hits")):
                return .2
            index = state["shot"]
            if index == len(SHOTS):
                state["phase"] = "complete"
                evaluate()
                finish()
                return None
            name, palette, populated, tab, language, scale, theme = SHOTS[index]
            if populated and not state.get("populated"):
                populate()
                state["populated"] = True
            set_palette(palette)
            if state.get("theme") != theme:
                set_theme(theme)
                state["theme"] = theme
            prefs.view.language, prefs.view.use_translate_interface, prefs.view.ui_scale = language, True, scale
            # A fresh Media Studio window for every shot: a window already open kept its last drawn picture until
            # the interface reloaded (preview slot 3024048c66f: the English "after" shots repeated the violet one).
            own = studio_toggle.opened_window(windows())
            if own is not None:   # Its own window (Director): closed, then opened fresh.
                studio_toggle.close_studio(own)
            state["phase"], state["tab"] = "reopen", tab
            return .4
        if phase == "reopen":
            if studio_toggle.opened_window(windows()) is not None:
                return .2
            state["draws"], state["opened_at"] = studio.draw_count, time.monotonic()
            studio_toggle.open_media_studio(state["tab"].lower(), windows()[0])
            state["phase"] = "drawn"
            return .8
        if phase == "drawn":
            # Drawn since this open, in the window that shows it: in Manual the switch to the Media Studio tab lands a
            # pass later, and hits left from an earlier visit are no proof of a new frame (the 35f5b544698 slot
            # captured the previous workspace: contrast 1.0, no violet even in the violet shots).
            # And 1.5 s since the open: Blender's screenshot of a second window captured before it has presented can
            # stay flat for good (studio-disc's probe: macOS's own capture of that window had full content).
            if not wait(phase + str(state["shot"]), lambda: studio_area() is not None
                        and studio.view(studio_area()).get("hits") and studio.draw_count >= state["draws"] + 2
                        and time.monotonic() - state["opened_at"] >= 1.5):
                return .2
            state["phase"] = "capture"
            return .3
        if phase == "capture":
            name, palette, populated, tab, language, scale, theme = SHOTS[state["shot"]]
            label = "%s-%s-%s-%s-x%d-%s" % (name, "grid" if populated else "empty", tab.lower(), language, scale, theme)
            target = studio_window()
            path = capture("studio-palette-%02d-%s.png" % (state["shot"], label), target)
            px = pixels(path)
            from qa_frames import drawn
            retries = state.setdefault("flat_retries", {}).setdefault(label, 0)
            if not drawn(px) and retries < 3:   # A flat readback: look again, 0.5 s apart; still flat fails below.
                state["flat_retries"][label] = retries + 1
                return .5
            area = studio_area()
            region = next(r for r in area.regions if r.type == 'WINDOW')
            view = studio.view(area)
            tab_rect = next(r for r, action, value in view["hits"] if action == "tab" and value == tab)
            create = view["geometry"]["create"]
            state["captures"][label] = {
                "palette": palette, "violet": round(violet_share(px), 4),
                "tab_contrast": round(contrast(px, (region.x + tab_rect[0], region.y + tab_rect[1], tab_rect[2], tab_rect[3])), 2),
                "create_contrast": round(contrast(px, (region.x + create[0], region.y + create[1] + create[3] - 120,
                                                        create[2], 110)), 2) if create[2] > 0 else None}
            from qa_frames import content_share
            state["captures"][label]["content"] = round(content_share(px), 4)   # A flat frame is no capture.
            gap = refresh_gap(view, region)
            state["captures"][label]["refresh_gap_uniform"] = round(uniform(px, gap), 3) if gap else None
            state["shot"] += 1
            state["phase"] = "shoot"
            return .1
    except Exception as error:   # noqa: BLE001 (the suite reports any failure in its receipt)
        finish(error)
        return None
    return .2


def evaluate():
    captures = state["captures"]
    before = [c for c in captures.values() if c["palette"] == "violet"]
    after = {k: c for k, c in captures.items() if c["palette"] == "tokens"}
    checks["every_capture_shows_studio_content"] = bool(captures) and all(
        c.get("content", 0) > 0.02 for c in captures.values())   # 35f5b544698: every shot was one flat colour.
    checks["the_measure_sees_the_old_violet"] = bool(before) and all(c["violet"] > 0.3 for c in before)
    checks["no_violet_left_in_any_capture"] = bool(after) and all(c["violet"] < 0.002 for c in after.values())
    checks["the_selected_tab_reads_in_dark_and_light"] = all(c["tab_contrast"] >= 4.5 for c in after.values())
    checks["the_create_column_reads_in_dark_and_light"] = all(
        c["create_contrast"] is None or c["create_contrast"] >= 4.5 for c in after.values())
    checks["light_theme_captured"] = state.get("themes", {}).get("light") is True and any(
        k.endswith("-light") for k in after)
    checks["every_shot_captured"] = len(captures) == len(SHOTS)
    gaps = [c.get("refresh_gap_uniform") for c in captures.values()]
    checks["nothing_between_refresh_and_import"] = any(g is not None for g in gaps) and all(
        g is None or g >= 0.97 for g in gaps)


bpy.app.timers.register(tick, first_interval=.5)
