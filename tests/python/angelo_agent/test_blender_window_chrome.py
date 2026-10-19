# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The compiled app's window chrome: the top bar and the OS title bar carry Angelo's teal.

A real window, factory startup, no events, provider, account or scene edit. Measured in the app:

* the default theme and the bundled light theme give the top bar the chrome colour
  (``window_chrome.chrome()``), and the top bar's pixels are that colour while the status bar and
  an editor header keep their own;
* the menu titles are drawn in a colour that reads on it: the theme's pulldown text in the dark
  theme, the top bar's header text in the light theme, where the shared pulldown text is dark;
* macOS: AppKit's title bar of every main window has the same colour with light title text, a
  window that only holds an editor keeps Blender's own title-bar colour, and a theme with its own
  top bar colour is followed, with the title text chosen by luminance (a saturated blue gets
  light text).

Run through ``run_native_suites.py --gui --include test_blender_window_chrome``; check
``window-chrome.json`` for ``success`` and ``phase == "complete"``, and look at ``dark.png``,
``light.png`` and (macOS) ``title-bar.png``, the window frame as AppKit draws it.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy
import numpy as np

assert "--factory-startup" in sys.argv and not bpy.app.background
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-window-chrome-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import window_chrome  # noqa: E402

bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
CHROME = window_chrome.chrome()
MACOS = sys.platform == "darwin"
# Theme colours a user might pick: a saturated blue whose HSV value is high but which needs light
# title text, and a pale yellow that needs dark title text.
USER_BLUE, USER_PALE = (0x20, 0x60, 0xC0), (0xE8, 0xD9, 0xA0)
DARK_APPEARANCE, LIGHT_APPEARANCE = "NSAppearanceNameVibrantDark", "NSAppearanceNameVibrantLight"
state = {"success": False, "phase": "boot", "checks": {}, "measured": {}, "chrome": window_chrome.hex_color(CHROME)}
if not MACOS:
    state["title_bar"] = "Not read on this platform: only AppKit reports a window's title-bar colour."


def check(name, passed):
    state["checks"][name] = bool(passed)


def write():
    (OUT / "window-chrome.json").write_text(json.dumps(state, indent=2))


def window():
    return bpy.context.window_manager.windows[0]


def theme():
    return bpy.context.preferences.themes[0]


def as_bytes(color):
    return tuple(round(channel * 255) for channel in color[:3])


def area(kind):
    return next(area for area in window().screen.areas if area.type == kind)


def strips():
    """The window rows of the top bar and the status bar. Python does not list these global areas; they are
    the strips above and below the screen's own areas."""
    areas = window().screen.areas
    top, bottom = max(a.y + a.height for a in areas), min(a.y for a in areas)
    return range(top + 2, window().height), range(0, bottom - 2)


def capture(name):
    """Redraw and save the window; ``rows[y][x] -> (r, g, b)`` bytes, y up, decoded by Blender's own loader."""
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        data = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(data)
    finally:
        bpy.data.images.remove(image)
    return np.rint(data.reshape(height, width, 4)[:, :, :3] * 255).astype(int)


def fill_of(row):
    """A strip's own colour: the most common pixel of one row that runs clear of its widgets."""
    colors, counts = np.unique(row, axis=0, return_counts=True)
    return tuple(int(channel) for channel in colors[counts.argmax()])


def menu_text(rows):
    """The brightest pixel among the top bar's application icon and menu titles (left of the workspace tabs)."""
    top_bar, _ = strips()
    scale = bpy.context.preferences.system.ui_scale
    block = rows[top_bar.start:top_bar.stop, int(4 * scale):int(235 * scale)].reshape(-1, 3)
    return tuple(int(channel) for channel in max(block, key=window_chrome.luminance))


def close_to(first, second, tolerance=6):
    return all(abs(a - b) <= tolerance for a, b in zip(first, second))


def drawn_as(pixel, color):
    """A flat fill read back from a screenshot. The window is drawn into a half-float buffer and read as
    bytes, which lands some values one level low (measured: #181818 reads 23, #076573 reads 6, 100, 114)."""
    return close_to(pixel, color, tolerance=1)


def title_bars():
    """macOS: the title bar of every visible app window, as AppKit holds it."""
    import macos_window
    return [macos_window.title_bar(native) for native, _, visible in macos_window.windows() if visible]


def set_top_bar(color):
    theme().topbar.space.header = (*(channel / 255 for channel in color), 1.0)


def apply_preset(name):
    path = next(Path(folder) / name for folder in bpy.utils.preset_paths("interface_theme") if (Path(folder) / name).is_file())
    with bpy.context.temp_override(window=window()):
        bpy.ops.script.execute_preset(filepath=str(path), menu_idname="USERPREF_MT_interface_theme_presets")


def measure(label, text_color):
    """Pixels and (macOS) the title bar for the current theme, whose menu titles should be ``text_color``."""
    rows = capture(label + ".png")
    top_bar, status_bar = strips()
    outliner = next(region for region in area('OUTLINER').regions if region.type == 'HEADER')
    # One row just inside each strip's top edge: above the tabs, the menu titles and the status text.
    measured = {"top_bar": fill_of(rows[top_bar.stop - 2]), "status_bar": fill_of(rows[status_bar.stop - 2]),
                "outliner_header": fill_of(rows[outliner.y + outliner.height - 2, outliner.x:outliner.x + outliner.width]),
                "menu_text": menu_text(rows)}
    check(label + "_theme_top_bar_is_the_chrome", as_bytes(theme().topbar.space.header) == CHROME)
    check(label + "_top_bar_pixels_are_the_chrome", drawn_as(measured["top_bar"], CHROME))
    check(label + "_status_bar_keeps_its_colour", drawn_as(measured["status_bar"], as_bytes(theme().statusbar.space.header))
          and not drawn_as(measured["status_bar"], CHROME))
    check(label + "_editor_header_keeps_its_colour", drawn_as(measured["outliner_header"], as_bytes(theme().outliner.space.header))
          and not drawn_as(measured["outliner_header"], CHROME))
    measured["menu_text_contrast"] = round(window_chrome.contrast(measured["menu_text"], CHROME), 2)
    check(label + "_menu_titles_are_drawn_in_the_expected_colour", close_to(measured["menu_text"], text_color))
    check(label + "_menu_titles_read_on_the_chrome", window_chrome.contrast(text_color, CHROME) >= window_chrome.READABLE)
    if MACOS:
        measured["title_bars"] = titles = title_bars()
        check(label + "_title_bar_is_the_chrome", [bar["background"] for bar in titles] == [CHROME] and titles[0]["transparent"])
        check(label + "_title_text_is_light", titles[0]["appearance"] == DARK_APPEARANCE)
    state["measured"][label] = measured


def dark():
    interface = theme().user_interface
    check("default_theme_leaves_the_status_bar_and_editors", as_bytes(theme().statusbar.space.header) == (0x18, 0x18, 0x18)
          and as_bytes(theme().view_3d.space.header) == (0x30, 0x30, 0x30))
    measure("dark", as_bytes(interface.wcol_pulldown.text))
    if MACOS:
        import macos_window
        native = next(native for native, _, visible in macos_window.windows() if visible)
        state["title_bar_captured"] = macos_window.capture_frame(native, OUT / "title-bar.png")
    set_top_bar(USER_BLUE)


def user_blue():
    if MACOS:
        state["measured"]["user_blue"] = bar = title_bars()[0]
        check("a_themes_own_top_bar_colour_is_followed", bar["background"] == USER_BLUE)
        check("a_saturated_colour_gets_light_title_text", bar["appearance"] == DARK_APPEARANCE)
    set_top_bar(USER_PALE)


def user_pale():
    if MACOS:
        state["measured"]["user_pale"] = bar = title_bars()[0]
        check("a_pale_colour_gets_dark_title_text", bar["background"] == USER_PALE and bar["appearance"] == LIGHT_APPEARANCE)
    apply_preset("Angelo_Light.xml")


def light():
    # Angelo Light's pulldown text is dark; the top bar falls back to its own header text.
    pulldown = as_bytes(theme().user_interface.wcol_pulldown.text)
    check("light_pulldown_text_would_not_read_on_the_chrome", window_chrome.contrast(pulldown, CHROME) < window_chrome.READABLE)
    measure("light", as_bytes(theme().topbar.space.header_text))
    apply_preset("Angelo_Dark.xml")


def windows():
    check("dark_preset_restores_the_chrome", as_bytes(theme().topbar.space.header) == CHROME)
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.window_new_main()
    with bpy.context.temp_override(window=window(), area=area('OUTLINER')):
        bpy.ops.wm.window_new()


def finish():
    state["windows"] = len(bpy.context.window_manager.windows)
    check("three_windows_are_open", state["windows"] == 3)
    if MACOS:
        state["measured"]["windows"] = titles = title_bars()
        backgrounds = [bar["background"] for bar in titles]
        check("every_main_window_has_the_chrome_title_bar", backgrounds.count(CHROME) == 2)
        # Blender's own rule for a window that is not a main one: the default background when it opens,
        # its editor's header colour once the screen is known. Never the top bar's colour.
        blenders = {as_bytes(theme().outliner.space.header), as_bytes(theme().properties.space.back)}
        others = [background for background in backgrounds if background != CHROME]
        check("an_editor_window_keeps_blenders_title_bar", len(others) == 1 and others[0] in blenders)
    state["success"] = all(state["checks"].values())
    state["phase"] = "complete"


STEPS = [dark, user_blue, user_pale, light, windows, finish]


def tick():
    try:
        step = STEPS.pop(0)
        state["phase"] = step.__name__
        step()
        write()
        if STEPS:
            return 1.0  # The theme change, or the new window, is drawn before the next step reads it.
        print("ANGELO_WINDOW_CHROME_OK" if state["success"] else "ANGELO_WINDOW_CHROME_FAILED", json.dumps(state["checks"]), flush=True)
    except Exception as error:
        state.update(success=False, phase="failed", error=str(error), traceback=traceback.format_exc())
        write()
        print(state["traceback"], flush=True)
    if os.environ.get("ANGELO_QA_KEEP_OPEN") != "1":
        bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(tick, first_interval=1.5)
