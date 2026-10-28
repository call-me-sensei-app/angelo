# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Media Studio model written without its preview gets one in a later session (``studio_assets*``, 1.0.6).

Where a background Blender has no usable GPU context, the writer's Workbench preview render ends the process (the
Windows build VM, 1.0.5). The real background writer runs here with that failure forced
(``ANGELO_QA_FORCE_PREVIEW_FAILURE``: "exit" ends the writer at the render, "raise" makes the render raise). Checked:
the model is written and loads, without a preview, and the manifest records it; renders stay off for the rest of
that session and the same session never tries it again; the next session, able to render, writes it again with a
real preview; a later session leaves it alone; a render that raises costs only that preview and leaves renders on.
Fixtures this test writes; no provider or network. Receipt: ``studio-previews.json``.
"""

import json
import os
import sys
import tempfile
import threading
from pathlib import Path

import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-studio-previews-qa"))
OUT.mkdir(parents=True, exist_ok=True)
FORCE = "ANGELO_QA_FORCE_PREVIEW_FAILURE"   # studio_assets_convert.FORCE_FAILURE
checks, details = {}, {}
calls = []


def check(name, value, detail=None):
    checks[name] = bool(value)
    if detail is not None:
        details[name] = detail


def fixture(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5)
    bpy.ops.export_scene.gltf(filepath=str(path), export_format='GLB')
    bpy.ops.wm.read_factory_settings(use_empty=True)


def preview_size(blend):
    """The model's asset preview size as Blender reads it from the asset file, (0, 0) without one."""
    with bpy.data.libraries.load(str(blend), link=False, assets_only=True) as (source, target):
        target.objects = list(source.objects[:1])
    obj = target.objects[0]
    size = tuple(obj.preview.image_size) if obj.preview else (0, 0)
    bpy.data.batch_remove([obj] + ([obj.data] if obj.data else []))
    return size


def main():
    from angelo_agent import studio_assets, studio_assets_host as host
    from angelo_agent.studio_state import StudioLibrary
    bpy.context.preferences.use_preferences_save = False

    def run(binary, batch, stop, yield_to):
        calls.append([item["id"] for item in batch])
        results = host.convert_batch(binary, batch, stop, yield_to)
        print("ANGELO_WRITER_RESULTS", json.dumps(results), flush=True)
        return results
    with tempfile.TemporaryDirectory(prefix="angelo-studio-previews-") as scratch:
        scratch = Path(scratch)
        os.environ["ANGELO_LIBRARY_DIR"] = str(scratch / "media-studio")
        fixture(scratch / "model.glb")
        library, directory = host.directories()
        host.ensure_registered()
        studio = StudioLibrary(library)

        def add(prompt):
            record = studio.add("3D", prompt, "Fixture model", status="ready")
            (studio.result_directory(record["id"]) / "model.glb").write_bytes((scratch / "model.glb").read_bytes())
            record.update(files=["model.glb"], thumbnail="", progress=100)   # No provider picture: a render.
            studio.update(record)
            return record["id"], Path(studio_assets.item(library, record, studio.folders)["blend"])
        names = {"3D": "Models", "IMAGE": "Images"}

        def sync(session):
            calls.clear()
            changed = host.sync(library, directory, names, bpy.app.binary_path, session, threading.Event(), run)
            manifest = studio_assets.read_manifest(directory)
            print("ANGELO_WRITER_MANIFEST", session, json.dumps(manifest), flush=True)
            return changed, manifest

        # Session one: the render ends the writer, as on a machine without a usable GPU context.
        identity, blend = add("A stone ball")
        os.environ[FORCE] = "exit"
        host._state["previews"] = True
        changed, manifest = sync("one")
        entry = manifest.get(identity, {})
        check("written_without_its_preview", changed and blend.is_file() and preview_size(blend) == (0, 0)
              and entry.get("preview_missing") and not entry.get("failed"), entry)
        check("renders_off_for_the_session", host._state["previews"] is False)
        changed, _manifest = sync("one")
        check("not_again_in_the_same_session", not changed and calls == [], list(calls))

        # Session two, able to render: written again with a real preview.
        del os.environ[FORCE]
        host._state["previews"] = True
        changed, manifest = sync("two")
        size = preview_size(blend)
        check("next_session_makes_its_preview", changed and calls == [[identity]] and size[0] > 0 and size[1] > 0
              and "preview_missing" not in manifest.get(identity, {}), [list(calls), size, manifest.get(identity)])
        changed, _manifest = sync("three")
        check("then_left_alone", not changed and calls == [], list(calls))

        # A render that raises costs that preview alone: renders stay on, the model is written.
        other, other_blend = add("A wooden ball")
        os.environ[FORCE] = "raise"
        changed, manifest = sync("four")
        del os.environ[FORCE]
        check("a_raising_render_keeps_renders_on", changed and other_blend.is_file()
              and manifest.get(other, {}).get("preview_missing") and host._state["previews"] is True,
              manifest.get(other))

    report = {"success": all(checks.values()), "checks": checks, "details": details}
    (OUT / "studio-previews.json").write_text(json.dumps(report, indent=2, default=str))
    print("ANGELO_STUDIO_PREVIEWS", json.dumps(report, default=str), flush=True)


main()
