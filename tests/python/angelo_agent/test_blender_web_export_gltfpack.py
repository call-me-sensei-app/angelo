# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The web export's KTX2 route end to end, through a gltfpack stand-in (doc/angelo/web_export.rst, "Textures").

Run in Angelo, in background (``--factory-startup``)::

    <Angelo> --background --factory-startup --python tests/python/angelo_agent/test_blender_web_export_gltfpack.py

The real exporter writes the scene (a textured mesh lit by an HDRI World), then ``gltfpack_stand_in.py``, found on
PATH as gltfpack would be, drops every extra and rewrites the buffer as gltfpack does. Checked: the export calls it
with texture compression and the mesh and node flags its request asks for; the file it leaves still carries
Angelo's look in the scene's extras, with the World's picture as a bufferView holding the Radiance file that was
made; the size budget reads it; the unpacked file is gone. A gltfpack that fails gives a WebP file with the look and
the ``ktx2_failed`` finding. The KTX2 encoding itself is gltfpack's and is not exercised. Writes ``results.json`` in
``ANGELO_QA_DIR``.
"""

import json
import os
import stat
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-web-gltfpack-qa"))
OUT.mkdir(parents=True, exist_ok=True)

import addon_utils  # noqa: E402

# Enabled, as in the app: the glTF exporter calls the scene hook (the look in the extras) only of enabled add-ons.
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
from angelo_agent import web_budget, web_export  # noqa: E402

checks, observed = {}, {}
report = {"success": False, "checks": checks, "observed": observed}


def check(name, value, detail=None):
    checks[name] = bool(value)
    print(("PASS " if value else "FAIL ") + name + ("" if value or detail is None else ": %s" % (detail,)), flush=True)


def build(folder):
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.object
    texture = bpy.data.images.new("Checker", 64, 64)
    texture.generated_type = "COLOR_GRID"
    texture.filepath_raw, texture.file_format = os.path.join(folder, "checker.png"), "PNG"
    texture.save()
    material = bpy.data.materials.new("Checker")
    tree = material.node_tree
    node = tree.nodes.new("ShaderNodeTexImage")
    node.image = texture
    tree.links.new(node.outputs[0], tree.nodes["Principled BSDF"].inputs["Base Color"])
    cube.data.materials.append(material)
    sky = bpy.data.images.new("Sky", 64, 32, float_buffer=True)
    sky.pixels = [value for row in range(32) for _ in range(64) for value in (1.0 - row / 32.0, 0.6, 0.3, 1.0)]
    sky.filepath_raw, sky.file_format = os.path.join(folder, "sky.hdr"), "HDR"
    sky.save()
    world = bpy.data.worlds.new("HDRI")
    environment = world.node_tree.nodes.new("ShaderNodeTexEnvironment")
    environment.image = bpy.data.images.load(sky.filepath_raw)
    world.node_tree.links.new(environment.outputs[0], world.node_tree.nodes["Background"].inputs["Color"])
    bpy.context.scene.world = world


def export(path, **request):
    scene = bpy.context.scene
    waiting = web_export.pending_export(None, scene, bpy.context.view_layer, path,
                                        dict({"meshes": "MESHOPT", "textures": "KTX2", "merge": True,
                                              "selection_only": False}, **request))
    while (found := waiting.step()) is None:
        waiting.job.done.wait(1.0)
    return found


def calls(log):
    return [json.loads(line) for line in Path(log).read_text().splitlines()] if Path(log).exists() else []


try:
    folder = tempfile.mkdtemp(prefix="angelo-web-gltfpack-")
    tools = Path(folder, "bin")
    tools.mkdir()
    if os.name == "nt":   # Found on PATH through PATHEXT, and run by CreateProcess, as a gltfpack.exe would be.
        stand_in = tools / "gltfpack.cmd"
        stand_in.write_text('@"%s" -I "%s" %%*\r\n' % (sys.executable, HERE / "gltfpack_stand_in.py"))
    else:
        stand_in = tools / "gltfpack"
        stand_in.write_text('#!/bin/sh\nexec "%s" -I "%s" "$@"\n' % (sys.executable, HERE / "gltfpack_stand_in.py"))
        stand_in.chmod(stand_in.stat().st_mode | stat.S_IXUSR)
    log = os.path.join(folder, "calls.jsonl")
    os.environ.update(PATH=str(tools) + os.pathsep + os.environ.get("PATH", ""), ANGELO_GLTFPACK_LOG=log)
    build(folder)

    path = os.path.join(folder, "packed.glb")
    result = export(path)
    arguments = calls(log)[-1] if calls(log) else []
    check("gltfpack_called_with_texture_compression", "-tc" in arguments and "-cc" in arguments
          and "-kn" not in arguments, arguments)
    check("textures_are_ktx2", result["settings"]["textures"] == "KTX2" and
          not any(item["code"] == "ktx2_failed" for item in result["findings"]), result["settings"])
    document, binary = web_budget.read_glb(Path(path).read_bytes())
    look = document["scenes"][document.get("scene", 0)].get("extras", {}).get("angelo_look", {})
    view = document["bufferViews"][look["environment"]["bufferView"]] if "environment" in look else None
    picture = binary[view.get("byteOffset", 0):view.get("byteOffset", 0) + view["byteLength"]] if view else b""
    check("look_survives_gltfpack", look.get("toneMapping") and picture.startswith(b"#?RADIANCE"),
          {key: value for key, value in look.items() if key != "environment"})
    environment = result["settings"]["environment"]
    check("picture_is_the_one_made", len(picture) == environment.get("bytes"), environment)
    budget = web_budget.measure(path)
    triangles = sum(len(obj.data.loop_triangles) for obj in bpy.context.scene.objects if obj.type == "MESH"
                    and not obj.data.calc_loop_triangles())
    check("budget_reads_the_packed_file", budget["environment_bytes"] == len(picture) and
          budget["triangles"] == triangles and budget["textures"]["count"] >= 1,
          {"environment_bytes": (budget["environment_bytes"], len(picture)), "triangles": (budget["triangles"], triangles),
           "textures": budget["textures"]["count"]})
    check("unpacked_file_removed", not os.path.exists(path + ".unpacked.glb") and
          not any(name.endswith(".unpacked.glb") for name in os.listdir(folder)))

    export(os.path.join(folder, "named.glb"), meshes="NONE", merge=False)
    arguments = calls(log)[-1]
    check("mesh_and_node_flags_follow_the_request", "-cc" not in arguments and "-kn" in arguments, arguments)

    os.environ["ANGELO_GLTFPACK_FAIL"] = "1"
    path = os.path.join(folder, "fallback.glb")
    result = export(path)
    document, _binary = web_budget.read_glb(Path(path).read_bytes())
    look = document["scenes"][document.get("scene", 0)].get("extras", {}).get("angelo_look", {})
    check("failed_gltfpack_falls_back_to_webp", result["settings"]["textures"] == "WEBP" and
          any(item["code"] == "ktx2_failed" for item in result["findings"]) and "environment" in look,
          {"settings": result["settings"]["textures"], "findings": [item["code"] for item in result["findings"]],
           "look": sorted(look), "environment": result["settings"]["environment"]})
    observed["calls"] = calls(log)
    report["success"] = all(checks.values())
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
failed = sorted(name for name, passed in checks.items() if not passed)
print("WEB_EXPORT_GLTFPACK_BACKGROUND failed checks:", json.dumps(failed))
assert report["success"], failed
