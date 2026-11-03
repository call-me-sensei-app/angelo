# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The baked-material engine proofs' input, made in a background Angelo (``run.py`` starts it).

    Angelo --background --factory-startup --python-exit-code 1 --python make_assets.py -- OUT

Bakes the DEV and HELD_OUT targets of ``test_blender_baked_export`` (GLTF packing, as the job does) plus an
unbound twin sharing the first target's mesh, saves the scene, and writes the two files the engines read through
Angelo's own writers: ``link.glb`` from the engine link's background write-back (Godot, Unity, Unreal) and
``web.glb`` from the web export (three.js). ``truth.json`` says which object should carry which material.
"""

import json
import sys
import time
from pathlib import Path

import bpy

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import test_blender_baked_export as T  # noqa: E402

arguments = sys.argv[sys.argv.index("--") + 1:]
out, kits = Path(arguments[0]).resolve(), arguments[1:]
out.mkdir(parents=True, exist_ok=True)
T.fx.scene_setup()
T.ensure_addon()
T.Case.folder = out / "maps"
T.Case.folder.mkdir(exist_ok=True)
truth = {"baked": {}, "own": {}, "refused": {}, "maps": {}}
if not kits:   # The suite's DEV and HELD_OUT targets, each with its procedural material.
    targets = T.Case.targets + T.HeldOutCases.targets
    for index, (name, kind, variant, modifier) in enumerate(targets):
        obj = T.shape(name, kind, T.procedural(name + " Paint", variant), modifier)
        obj.location.x = 1.6 * index
        _obj, material, numbers = T.Case.bake(obj, "GLTF")
        truth["baked"][name], truth["maps"][name] = material.name, numbers["channels"]
    first = bpy.data.objects[targets[0][0]]
else:   # Held-out kit models (.glb), side by side, each mesh baked from its own materials.
    for index, kit in enumerate(kits):
        before = set(bpy.data.objects)
        bpy.ops.import_scene.gltf(filepath=kit)
        added = sorted(set(bpy.data.objects) - before, key=lambda item: item.name)
        for obj in added:
            if obj.parent is None:
                obj.location.x += 2.5 * index
        for obj in added:
            if obj.type != "MESH":
                continue
            try:
                _obj, material, numbers = T.Case.bake(obj, "GLTF")
                truth["baked"][obj.name], truth["maps"][obj.name] = material.name, numbers["channels"]
            except Exception as error:   # A bake refusal (no UVs, an empty slot): recorded, never hidden.
                truth["refused"][obj.name] = str(error)[:160]
    first = bpy.data.objects[sorted(truth["baked"])[0]]
twin = bpy.data.objects.new(first.name + " Twin", first.data)   # Shares the mesh, never baked: keeps its own.
bpy.context.scene.collection.objects.link(twin)
twin.location.y = 1.8
truth["own"][twin.name] = first.data.materials[0].name
bpy.context.scene.render.engine = 'BLENDER_EEVEE'   # As a person's scene usually is; not part of any check.

from angelo_agent import engine_link_assets, engine_link_writeback, web_export  # noqa: E402
blend = out / "work.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(blend), copy=True)
found = engine_link_assets.baked_plan(None)
truth["plan"] = {"used": found["used"], "stale": found["stale"], "packing": found["packing"]}
writer = engine_link_writeback.WriteBack(bpy.app.binary_path)
writer.request("link", str(blend), str(out / "link.glb"), "gltf", dict(engine_link_assets.GLTF_DURABLE),
               baked=found["used"])
done, started = [], time.monotonic()
while not done and time.monotonic() - started < 180:
    done = writer.finished()
    time.sleep(0.05)
truth["write_back"] = {"result": done, "seconds": round(time.monotonic() - started, 2)}
report = web_export.export(None, bpy.context.scene, bpy.context.view_layer, str(out / "web.glb"),
                           {"meshes": "AUTO", "textures": "AUTO", "merge": False, "selection_only": False})
truth["web"] = web_export.for_agent(report)["baked"]
(out / "truth.json").write_text(json.dumps(truth, indent=2) + "\n")
assert done == [("link", None)], done
print("ASSETS " + json.dumps(truth))
