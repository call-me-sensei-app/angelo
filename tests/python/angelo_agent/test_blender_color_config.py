# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's colour-space writes work under every OCIO configuration a 5.3 Project can switch to (``color_spaces``).

A 5.3 Project can use one of the configurations built into OpenColorIO (``ocio://`` URIs; ACES 1.3 and 2.0), which
have no "Non-Color", "sRGB" or "Standard". This suite runs itself once per configuration, each in its own process
started with ``BLENDER_OCIO`` (as a Project's configuration is loaded), and there runs the real code:

* the helper on real settings: stored values, sRGB, an un-tone-mapped view, the kinds;
* the welcome banner, the connect screen's still, a library import's data map, a Studio tile picture and a baked
  colour map, each loaded as Angelo loads it;
* the engine check's colour-space fix on an image used as data;
* a three-frame MP4 from the video encode child.

Blender's own configuration is the held-out control. Run only in a disposable instance with the exact Angelo binary:
``--background --factory-startup --disable-autoexec --python-exit-code 1``. No provider or paid call.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import subprocess
import tempfile
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-color-config-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
CONFIGS = {"cg_aces": "ocio://cg-config-latest", "studio_aces": "ocio://studio-config-latest"}
CHILD = os.environ.get("ANGELO_QA_COLOR_CONFIG")   # The configuration this process runs under (unset: the parent).
report = {"checks": {}, "facts": {}, "paid_requests": 0, "version": bpy.app.version_string,
          "config": CHILD or "blender"}


def check(name, condition):
    report["checks"][name] = bool(condition)


def attempt(name, function):
    try:
        result = function()
        check(name, result is not False)
        return result
    except Exception as error:
        report["facts"][name + "_error"] = "%s: %s" % (type(error).__name__, str(error)[:300])
        check(name, False)
        return None


def picture(path, colour=(0.8, 0.3, 0.2, 1.0), side=8):
    image = bpy.data.images.new("QA picture", side, side)
    image.pixels.foreach_set(list(colour) * side * side)
    image.filepath_raw, image.file_format = str(path), 'PNG'
    image.save()
    bpy.data.images.remove(image)
    return path


def names():
    return {item.identifier for item in
            bpy.types.ColorManagedInputColorspaceSettings.bl_rna.properties["name"].enum_items}


def srgb_by_interop(settings):
    """The test's own sRGB, as Blender finds it (interop IDs), independent of the code under test."""
    for interop_id in ("srgb_rec709_scene", "srgb_rec709_display"):
        try:
            settings.interop_id = interop_id
            return True
        except TypeError:
            continue
    return False


def run_sites():
    spaces = names()
    report["facts"]["colour_spaces"] = sorted(spaces)[:40]
    if CHILD:   # The configuration really is the one asked for, not Blender's own fallen back to.
        check("configuration_loaded", "Non-Color" not in spaces)
    folder = OUT / "pictures"
    folder.mkdir(exist_ok=True)
    path = picture(folder / "picture.png")

    def helper():
        from angelo_agent import color_spaces
        image = bpy.data.images.load(str(path), check_existing=False)
        color_spaces.mark_stored(image.colorspace_settings)
        stored = color_spaces.kind(image.colorspace_settings)
        color_spaces.mark_srgb(image.colorspace_settings)
        srgb = color_spaces.kind(image.colorspace_settings), image.colorspace_settings.name
        bpy.data.images.remove(image)
        scene = bpy.context.scene
        untonemapped = color_spaces.view_untonemapped(scene.view_settings)
        report["facts"]["helper"] = {"stored": stored, "srgb": srgb, "view": scene.view_settings.view_transform}
        return stored == "data" and srgb[0] == "srgb" and untonemapped
    attempt("helper_on_real_settings", helper)

    def as_stored(source):
        """brand_picture's read (imbuf, no datablock) gives exactly the values an image datablock in the data colour
        space gives: the stored bytes over 255, under this configuration too."""
        import numpy
        from angelo_agent import brand_picture, color_spaces
        pixels, size = brand_picture.read(source)
        image = bpy.data.images.load(str(source), check_existing=False)
        try:
            color_spaces.mark_stored(image.colorspace_settings)
            loaded = tuple(image.size)
            expected = numpy.empty(loaded[0] * loaded[1] * 4, dtype=numpy.float32)
            image.pixels.foreach_get(expected)
        finally:
            bpy.data.images.remove(image)
        return loaded == size and bool(numpy.array_equal(pixels, expected))

    def welcome():
        from angelo_agent import welcome_screen
        welcome_screen.release()
        welcome_screen.prepare_banner()
        ready = welcome_screen.banner_ready() and welcome_screen.BANNER_PICTURE.holds_pixels()
        welcome_screen.release()
        return ready and as_stored(welcome_screen.BANNER)
    attempt("welcome_banner_loads", welcome)

    def connect():
        from angelo_agent import connect_hero
        connect_hero.SHOWCASE.release()
        connect_hero.SHOWCASE.prepare()
        ready = connect_hero.SHOWCASE.ready
        connect_hero.SHOWCASE.release()
        return ready and as_stored(connect_hero.SHOWCASE.path)
    attempt("connect_still_loads", connect)

    def library():
        from angelo_agent import library_import
        image = library_import._image(path, "QA data map", True)
        stored = image.colorspace_settings.is_data
        bpy.data.images.remove(image)
        return stored
    attempt("library_data_map_imports", library)

    def tile():
        from angelo_agent import studio_tile_previews
        return studio_tile_previews.decode(path)[1] == 8
    attempt("studio_tile_decodes", tile)

    def baked():
        from angelo_agent import bake_maps_material
        image = bake_maps_material._image(str(folder), [{"map": "QA colour", "file": path.name}], "QA colour", True, "PNG")
        stored = image.colorspace_settings.is_data
        bpy.data.images.remove(image)
        return not stored
    attempt("baked_colour_map_is_srgb", baked)

    def engine_fix():
        from angelo_agent import engine_fixes
        image = bpy.data.images.load(str(path), check_existing=False)
        image.name = "QA normal"
        srgb_by_interop(image.colorspace_settings)
        facts = {"images": [{"name": "QA normal", "roles": ["data"]}]}
        changes = engine_fixes.colour_space("QA normal", facts, None)
        stored = image.colorspace_settings.is_data
        bpy.data.images.remove(image)
        return stored and bool(changes)
    attempt("engine_fix_sets_data", engine_fix)

    def video():
        frames = folder / "frames"
        frames.mkdir(exist_ok=True)
        for frame in range(1, 4):
            picture(frames / ("frame_%04d.png" % frame), (frame / 4, 0.2, 0.6, 1.0), side=64)   # H.264's minimum.
        dest = OUT / "video.mp4"
        request = {"frames": [1, 3, 1], "directory": str(frames), "prefix": "frame_", "digits": 4, "suffix": "",
                   "extension": ".png", "size": [64, 64], "fps": 24, "fps_base": 1.0, "dest": str(dest),
                   "parent": os.getpid()}
        done = subprocess.run([bpy.app.binary_path, "--background", "--factory-startup", "--python",
                               str(ROOT / "scripts/addons_core/angelo_agent/video_encode_child.py"), "--",
                               json.dumps(request)], capture_output=True, text=True, timeout=180)
        report["facts"]["video_tail"] = (done.stdout + done.stderr)[-600:]
        return done.returncode == 0 and dest.exists() and dest.stat().st_size > 0
    attempt("video_encodes", video)


def run_configurations():
    """The parent: each configuration in its own process, as a Project's configuration is loaded."""
    for name, uri in CONFIGS.items():
        folder = OUT / name
        folder.mkdir(exist_ok=True)
        env = dict(os.environ, BLENDER_OCIO=uri, ANGELO_QA_COLOR_CONFIG=name, ANGELO_QA_DIR=str(folder))
        done = subprocess.run([bpy.app.binary_path, "--background", "--factory-startup", "--disable-autoexec",
                               "--python-exit-code", "1", "--python", str(Path(__file__).resolve())],
                              env=env, capture_output=True, text=True, timeout=600)
        (folder / "run.out").write_text(done.stdout + done.stderr)
        receipt = folder / "color-config.json"
        child = json.loads(receipt.read_text()) if receipt.exists() else {"checks": {}, "error": "no receipt"}
        report[name] = child
        for check_name, ok in child.get("checks", {}).items():
            check(name + ":" + check_name, ok)
        check(name + ":completed", child.get("success") is not None and not child.get("error"))


try:
    run_sites()
    if not CHILD:
        run_configurations()
    report["success"] = bool(report["checks"]) and all(report["checks"].values())
except Exception:
    report.update(success=False, error=traceback.format_exc())
report["failed"] = [name for name, ok in report["checks"].items() if not ok]
(OUT / "color-config.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_COLOR_CONFIG", json.dumps({key: report.get(key) for key in ("config", "success", "failed")}), flush=True)
assert report.get("success"), report.get("error") or report["failed"]
