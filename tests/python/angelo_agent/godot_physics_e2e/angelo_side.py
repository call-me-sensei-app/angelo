# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's side of test_blender_godot_physics_e2e: mark held-out models with game physics and export them.

    Angelo --background --factory-startup --python angelo_side.py -- ASSETS_DIR PROJECT_DIR [BLEND_COUNT]

Every .glb in ASSETS_DIR (models this feature was never developed on) is imported once per variant and marked by
one rule for every model, never by its name or size:

* ``own``: one body and its shape on the model's only mesh object (models with one mesh);
* ``wrap``: an empty above the model is the body, and every mesh object in the model gets the shape (Godot needs
  each shape directly below the body, so deeper meshes exercise the exporter's hoisting). A mesh that moves inside
  the body (parented to an animated bone, say) keeps its shape in place, and Angelo's ``shape_indirect`` finding
  says Godot makes it a separate body: ``flagged`` lists them, and the test checks Godot does exactly that;
* ``static``: a Mesh shape on every mesh object and no body (a static collider below each mesh).

Each model's variants go to ``<model>.glb`` in PROJECT_DIR, and the first BLEND_COUNT models' ``wrap`` rigid box also
to ``<model>_blend.blend``, which Godot imports through Angelo as its Blender (a background Angelo, where only the
glTF add-on writes physics). ``expected.json`` says what each variant's root must become in Godot.
"""

import json
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(bpy.utils.system_resource("SCRIPTS")) / "addons_core"))
from angelo_agent import game_physics  # noqa: E402  (the add-on needn't be enabled: its findings are plain Python)

argv = sys.argv[sys.argv.index("--") + 1:]
ASSETS, PROJECT = Path(argv[0]), Path(argv[1])
BLEND_COUNT = int(argv[2]) if len(argv) > 2 else 2
VARIANTS = (("own", "RIGID", "BOX"), ("wrap", "RIGID", "BOX"), ("wrap", "RIGID", "CONVEX"),
            ("wrap", "KINEMATIC", "CAPSULE"), ("wrap", "TRIGGER", "SPHERE"), ("wrap", "STATIC", "CYLINDER"),
            ("static", "NONE", "TRIMESH"))
GODOT_BODIES = {"RIGID": "RigidBody3D", "KINEMATIC": "AnimatableBody3D", "TRIGGER": "Area3D", "STATIC": "StaticBody3D"}
SPACING = 6.0


def clear():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def import_model(path, offset):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    new = [obj for obj in bpy.data.objects if obj not in before]
    roots = [obj for obj in new if obj.parent is None]
    for root in roots:
        root.location.x += offset
    return new, roots


def variant(path, index, style, body, shape):
    """Mark one imported copy; returns its expectation, or None when the style doesn't apply."""
    objects, roots = import_model(path, index * SPACING)
    meshes = [obj for obj in objects if obj.type == "MESH"]
    name = "%s_%s_%s" % (style, body.lower(), shape.lower())
    for number, mesh in enumerate(meshes):   # Stable names: Godot turns Blender's ".001" into "_001".
        mesh.name = "%s_m%d" % (name, number)
    if style == "own":
        if len(meshes) != 1:
            for obj in objects:
                bpy.data.objects.remove(obj)
            return None
        meshes[0].name = name
        meshes[0].angelo_physics.body, meshes[0].angelo_physics.shape = body, shape
        return {"root": name, "class": GODOT_BODIES[body], "shape": shape, "shapes": 1, "meshes": 1}
    if style == "wrap":
        wrapper = bpy.data.objects.new(name, None)
        bpy.context.scene.collection.objects.link(wrapper)
        wrapper.location.x = index * SPACING
        for root in roots:
            matrix = root.matrix_world.copy()
            root.parent = wrapper
            root.matrix_world = matrix
        wrapper.angelo_physics.body = body
        if body == "RIGID":
            wrapper.angelo_physics.mass = 2.5
        for mesh in meshes:
            mesh.angelo_physics.shape = shape
        flagged = sorted({item["subject"] for item in game_physics.scene_findings(bpy.context.scene, [wrapper] + meshes)
                          if item["code"] == "shape_indirect"})
        return {"root": name, "class": GODOT_BODIES[body], "shape": shape, "shapes": len(meshes) - len(flagged),
                "meshes": len(meshes), "mass": 2.5 if body == "RIGID" else None, "flagged": flagged}
    for mesh in meshes:
        mesh.angelo_physics.shape = shape
    return {"static_meshes": [mesh.name for mesh in meshes], "shape": shape}


def main():
    expected = {}
    models = sorted(ASSETS.glob("*.glb"))
    for number, path in enumerate(models):
        clear()
        rows = {}
        for index, (style, body, shape) in enumerate(VARIANTS):
            row = variant(path, index, style, body, shape)
            if row is not None:
                rows["%s_%s_%s" % (style, body.lower(), shape.lower())] = row
        stem = path.stem.replace("-", "_")
        bpy.ops.export_scene.gltf(filepath=str(PROJECT / (stem + ".glb")), export_format="GLB")
        expected[stem + ".glb"] = rows
        if number < BLEND_COUNT:
            clear()
            row = variant(path, 0, "wrap", "RIGID", "BOX")
            bpy.ops.wm.save_as_mainfile(filepath=str(PROJECT / (stem + "_blend.blend")))
            expected[stem + "_blend.blend"] = {"wrap_rigid_box": row}
    (PROJECT / "expected.json").write_text(json.dumps(expected, indent=2), encoding="utf-8")
    print("ANGELO_PHYSICS_ASSETS", len(models), flush=True)


main()
