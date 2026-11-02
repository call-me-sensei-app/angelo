# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's grid shows each picture in the colours of its file (``studio_draw.thumbnail``).

A real window, fixture items only (no provider, account or network). An isolated Studio library gets four flat
pictures in mid tones, where a linear value read as sRGB is far off (pure primaries such as the magenta and cyan of
the other Studio suites look the same either way): an orange and a teal PNG, a skin-tone JPEG and a grey 16-bit PNG
(loaded as float). The main 3D view becomes Media Studio on its Images tab; a screenshot of the window is read back
and the middle of each tile's picture is compared with the colour written to its file, each channel within
``TOLERANCE``. Before 1.0.8 the grid drew ``gpu.texture.from_image``'s scene-linear texels with the plain ``IMAGE``
shader: the orange showed red and every picture darker (``test_native_picture_colour`` pins the shader).

Run through ``run_native_suites.py --gui --include test_blender_studio_picture_colour_gui``; check
``studio-picture-colour-gui.json`` for ``success`` and ``phase == "complete"``.
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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-studio-picture-colour-gui-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("ANGELO_LIBRARY_DIR", str(OUT / "library"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
bpy.context.preferences.use_preferences_save = False
bpy.context.preferences.view.show_splash = False
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
from angelo_agent import editor_types, studio  # noqa: E402
from angelo_agent.studio_state import StudioLibrary  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

REPORT = OUT / "studio-picture-colour-gui.json"
TOLERANCE = 4 / 255   # 8-bit output rounding, on either side; the pre-1.0.8 error was 0.1 to 0.4 here.
# name, file, file options, colour as written (sRGB-encoded values, as an image editor stores them)
FIXTURES = (("orange", "orange.png", {"file_format": 'PNG'}, (0.90, 0.55, 0.20)),
            ("teal", "teal.png", {"file_format": 'PNG'}, (0.15, 0.55, 0.50)),
            ("skin", "skin.jpg", {"file_format": 'JPEG', "quality": 100}, (0.85, 0.65, 0.55)),
            ("grey", "grey16.png", {"file_format": 'PNG', "color_depth": '16'}, (0.50, 0.50, 0.50)))
state = {"phase": "boot", "success": False, "checks": {}, "details": {}, "screenshots": []}
checks, details = state["checks"], state["details"]


def check(name, value, detail=None):
    checks[name] = bool(value)
    if detail is not None:
        details[name] = detail
    return bool(value)


def main_window():
    return bpy.context.window_manager.windows[0]


def region_of(area):
    return next(r for r in area.regions if r.type == 'WINDOW')


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=main_window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return path


def wait(condition, seconds):
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            return False
        yield
    return True


def frames(count):
    for _ in range(count):
        yield


def stored(colour):
    """``colour`` as the fixture's file stores it: its 8-bit source rounds to 1/255 (the 16-bit file widens those
    values exactly; a JPEG at quality 100 of a flat colour stays within a step)."""
    return tuple(round(c * 255) / 255 for c in colour)


def fixtures():
    folder = OUT / "fx"
    folder.mkdir(exist_ok=True)
    library = StudioLibrary()
    made = {}
    scene = bpy.data.scenes.new("Fixture files")   # Its own output settings: the user's scene is left as it was.
    scene.display_settings.display_device = 'sRGB'
    scene.view_settings.view_transform, scene.view_settings.look = 'Standard', 'None'
    scene.view_settings.exposure, scene.view_settings.gamma = 0.0, 1.0
    for key, name, options, colour in FIXTURES:
        image = bpy.data.images.new(key, 256, 256)
        image.pixels.foreach_set((list(colour) + [1.0]) * (256 * 256))
        settings = scene.render.image_settings
        settings.file_format = options["file_format"]
        settings.color_mode = 'RGB'
        for k, v in options.items():
            setattr(settings, k, v)
        image.save_render(str(folder / name), scene=scene)   # Standard view: the file holds the values as written.
        bpy.data.images.remove(image)
        record = library.add("IMAGE", "A flat %s picture" % key, "Fixture image", status="ready")
        (library.result_directory(record["id"]) / name).write_bytes((folder / name).read_bytes())
        record.update(files=[name], thumbnail=name, progress=100)
        library.update(record)
        made[key] = record
    bpy.data.scenes.remove(scene)
    return made


def picture_centre(area, identity):
    """Window pixels of the middle of ``identity``'s picture in the grid (``studio_draw``: the picture is contained
    in ``(x + 2, y + 88, w - 4, h - 90)`` of the tile), or None while the tile is not wholly shown."""
    hit = next((r for r, action, value in studio.view(area).get("hits", []) if action == "asset" and value == identity),
               None)
    if hit is None:
        return None
    region = region_of(area)
    x, y, w, h = hit
    return region.x + x + w / 2, region.y + y + 88 + (h - 90) / 2


def sample(path, xy, radius=4):
    """The median colour of the screenshot's pixels within ``radius`` window pixels of ``xy``."""
    import numpy
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        values = numpy.empty(width * height * 4, dtype=numpy.float32)
        image.pixels.foreach_get(values)
    finally:
        bpy.data.images.remove(image)
    scale = width / main_window().width
    cx, cy, r = round(xy[0] * scale), round(xy[1] * scale), max(1, round(radius * scale))
    patch = values.reshape(height, width, 4)[cy - r:cy + r + 1, cx - r:cx + r + 1, :3].reshape(-1, 3)
    return [round(float(v), 4) for v in numpy.median(patch, axis=0)]


def run():
    window = main_window()
    state["phase"] = "fixtures"
    made = fixtures()
    studio.library().reload()
    area = max((a for a in window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    editor_types.select_console(window, area, generative=True)
    yield from frames(10)
    studio.view(area).update(kind="IMAGE", grid_scroll=0, query="")
    area.tag_redraw()
    state["phase"] = "grid"
    shown = yield from wait(lambda: all(picture_centre(area, r["id"]) for r in made.values()), 15)
    check("every_fixture_tile_is_shown", shown, {k: picture_centre(area, r["id"]) for k, r in made.items()})
    drawn = yield from wait(lambda: all(any(key[0] == r["id"] and entry for key, entry in studio._textures.items())
                                        for r in made.values()), 15)
    check("every_picture_is_drawn", drawn)
    yield from frames(6)
    shot = capture("studio-grid.png")
    state["phase"] = "colours"
    for key, name, options, colour in FIXTURES:
        expected = [round(c, 4) for c in stored(colour)]
        xy = picture_centre(area, made[key]["id"])
        seen = sample(shot, xy) if xy else None
        off = max(abs(a - b) for a, b in zip(seen, expected)) if seen else None
        check("%s_matches_its_file" % key, off is not None and off <= TOLERANCE,
              {"file": name, "expected": expected, "on_screen": seen, "max_channel_error": off})
    state["phase"] = "complete"


def tick(steps=[]):   # noqa: B006 - the generator lives across timer calls.
    try:
        if not steps:
            steps.append(run())
        next(steps[0])
        return .1
    except StopIteration:
        pass
    except Exception:
        state["error"] = traceback.format_exc()
    state["success"] = state["phase"] == "complete" and all(checks.values())
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_STUDIO_PICTURE_COLOUR_GUI", json.dumps({"success": state["success"], "phase": state["phase"]}),
          flush=True)
    bpy.ops.wm.quit_blender()
    return None


# Held until Angelo's start-up (the first tab build its enable schedules) is over: qa_boot.gated.
bpy.app.timers.register(qa_boot.gated(tick), first_interval=2)
