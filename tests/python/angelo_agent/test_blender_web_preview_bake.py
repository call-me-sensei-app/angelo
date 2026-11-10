# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A live preview export made while the World's picture is being made carries the World the page shows, in the app
(doc/angelo/web_export.rst, "Waiting for it").

Run in Angelo, in background (``--factory-startup``)::

    <Angelo> --background --factory-startup --python tests/python/angelo_agent/test_blender_web_preview_bake.py

A cube lit only by its World, exported by the real exporter as the live preview exports (``require_environment``
off, the preview's ``shown``), with real pictures: a Sky baked by a background Angelo, then an HDRI read on a worker
thread, each worker held until the export meant to be made during it is written. For each: the first export, before any picture, is ``{"type": "pending"}``; once the picture lands the file
carries it; after an edit of the World, while its new picture is being made, the file a page (re)loading then reads
carries the earlier picture marked pending, never no light; when the new picture lands the file carries it. Writes
``results.json`` in ``ANGELO_QA_DIR``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-web-preview-bake-qa"))
OUT.mkdir(parents=True, exist_ok=True)

import addon_utils  # noqa: E402

addon_utils.enable("angelo_agent", default_set=False, persistent=False)
from angelo_agent import web_budget, web_environment, web_export  # noqa: E402

REQUEST = {"meshes": "NONE", "textures": "WEBP", "merge": True, "selection_only": False}
GATE = threading.Event()   # Held, a picture's worker waits before making it: an export is surely made meanwhile.


def held(worker):
    def wait_then_make(*args):
        GATE.wait(130.0)
        return worker(*args)
    return wait_then_make


web_environment._from_image = held(web_environment._from_image)
web_environment._wait_for_bake = held(web_environment._wait_for_bake)
checks, observed = {}, {}
report = {"success": False, "checks": checks, "observed": observed}


def check(name, value, detail=None):
    checks[name] = bool(value)
    print(("PASS " if value else "FAIL ") + name + ("" if value or detail is None else ": %s" % (detail,)), flush=True)


def environment_of(path):
    """The file's environment as a page reads it, its bufferView replaced by the bytes it holds."""
    document, binary = web_budget.read_glb(Path(path).read_bytes())
    environment = document["scenes"][document.get("scene", 0)]["extras"]["angelo_look"]["environment"]
    if environment and isinstance(environment.get("bufferView"), int):
        view = document["bufferViews"][environment["bufferView"]]
        start = view.get("byteOffset", 0)
        environment = dict(environment, bufferView=bytes(binary[start:start + view["byteLength"]]))
    return environment


class Preview:
    """Exports as the live preview does, numbered, into ``folder``."""

    def __init__(self, folder):
        self.folder, self.shown, self.count = folder, {}, 0

    def export(self):
        self.count += 1
        path = os.path.join(self.folder, "preview-%d.glb" % self.count)
        landed = web_environment.landed()
        found = web_export.export(None, bpy.context.scene, bpy.context.view_layer, path, REQUEST,
                                  require_environment=False, shown=self.shown)
        return found["settings"]["environment"].get("status"), environment_of(path), landed

    def until_landed(self, landed, seconds=130.0):
        GATE.set()
        end = time.monotonic() + seconds
        while not web_environment.ready_since(landed) and time.monotonic() < end:
            time.sleep(0.2)
        GATE.clear()
        return web_environment.ready_since(landed)


def hdri(folder, name, top):
    image = bpy.data.images.new(name, 64, 32, float_buffer=True)
    image.pixels = [value for row in range(32) for _ in range(64)
                    for value in (*(channel * (1.0 - row / 40.0) for channel in top), 1.0)]
    image.filepath_raw, image.file_format = os.path.join(folder, name + ".hdr"), "HDR"
    image.save()
    return bpy.data.images.load(image.filepath_raw)


def run(kind, preview, edit):
    status, first, landed = preview.export()
    check(kind + "_first_export_is_pending_with_nothing_to_stand_in",
          status == "pending" and first == {"type": "pending"}, (status, first))
    check(kind + "_picture_lands", preview.until_landed(landed))
    status, ready, _landed = preview.export()
    picture = (ready or {}).get("bufferView", b"")
    check(kind + "_ready_export_carries_the_picture", status == "ready" and picture.startswith(b"#?RADIANCE")
          and "pending" not in ready, (status, {key: value for key, value in (ready or {}).items() if key != "bufferView"}))
    edit()
    status, during, landed = preview.export()
    observed[kind + "_during"] = {key: value for key, value in (during or {}).items() if key != "bufferView"}
    check(kind + "_mid_bake_export_carries_the_picture_shown", status == "pending" and during is not None
          and during.get("type") == "equirect" and during.get("pending") is True
          and during.get("bufferView") == picture, observed[kind + "_during"])
    check(kind + "_new_picture_lands", preview.until_landed(landed))
    status, after, _landed = preview.export()
    check(kind + "_new_picture_replaces_it", status == "ready" and after.get("bufferView", b"").startswith(b"#?RADIANCE")
          and after["bufferView"] != picture and "pending" not in after, status)


try:
    folder = tempfile.mkdtemp(prefix="angelo-web-preview-bake-")
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)   # The cube's only light is its World.
    bpy.ops.mesh.primitive_cube_add()

    world = bpy.data.worlds.new("Sky")
    sky = world.node_tree.nodes.new("ShaderNodeTexSky")
    world.node_tree.links.new(sky.outputs[0], world.node_tree.nodes["Background"].inputs["Color"])
    bpy.context.scene.world = world
    run("sky", Preview(folder), lambda: setattr(sky, "sun_elevation", sky.sun_elevation + 0.4))

    world = bpy.data.worlds.new("HDRI")
    environment = world.node_tree.nodes.new("ShaderNodeTexEnvironment")
    environment.image = hdri(folder, "warm", (1.0, 0.6, 0.3))
    world.node_tree.links.new(environment.outputs[0], world.node_tree.nodes["Background"].inputs["Color"])
    bpy.context.scene.world = world
    other = hdri(folder, "cool", (0.3, 0.6, 1.0))
    run("hdri", Preview(folder), lambda: setattr(environment, "image", other))
    report["success"] = all(checks.values())
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    GATE.set()
    web_environment.stop()
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
failed = sorted(name for name, passed in checks.items() if not passed)
print("WEB_PREVIEW_BAKE_BACKGROUND failed checks:", json.dumps(failed))
assert report["success"], failed
