# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The bake check's pictures drawn in the running app (``bake_check_host``; doc/angelo/long_jobs.rst, "Bake check").

Background, Cycles on the CPU at the check's own size. The same material drawn twice compares at exactly 0 (one
seed, the denoiser); a material changed on one half of a sphere differs by much more than a person needs to tell,
with the worst tile on the changed half; a high-to-low pair whose source is a little larger shows its silhouette as
pixels only one picture covers. The person's Render Result keeps its slot and pixels, their materials and slots are
untouched, and nothing the check adds stays. Writes bake-check.json under ANGELO_QA_DIR.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import time
import traceback
from pathlib import Path

import bpy

from angelo_agent import bake_check_host  # noqa: E402

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-bake-check"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {"phase": "start", "success": False, "checks": {}, "details": {}, "facts": {}}
JUST_NOTICEABLE = 2.0   # CIE76: about the smallest difference a person tells apart side by side.


def write_report():
    (OUT / "bake-check.json").write_text(json.dumps(REPORT, indent=1, default=float), encoding="utf-8")


def check(name, passed, detail=""):
    REPORT["checks"][name] = bool(passed)
    REPORT["details"][name] = detail


def material(name, colour, half=None):
    """A Principled material of ``colour``; with ``half``, that colour where x > 0 on the object."""
    mat = bpy.data.materials.new(name)
    tree = mat.node_tree
    bsdf = tree.nodes["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.5
    if half is None:
        bsdf.inputs["Base Color"].default_value = colour
        return mat
    coords = tree.nodes.new("ShaderNodeTexCoord")
    xyz = tree.nodes.new("ShaderNodeSeparateXYZ")
    step = tree.nodes.new("ShaderNodeMath")
    step.operation = "GREATER_THAN"
    step.inputs[1].default_value = 0.0   # x > 0: the half facing +x.
    mix = tree.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.inputs["A"].default_value, mix.inputs["B"].default_value = colour, half
    tree.links.new(coords.outputs["Object"], xyz.inputs["Vector"])
    tree.links.new(xyz.outputs["X"], step.inputs[0])
    tree.links.new(step.outputs["Value"], mix.inputs["Factor"])
    tree.links.new(mix.outputs["Result"], bsdf.inputs["Base Color"])
    return mat


def sphere(name, radius, segments=48):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=radius, segments=segments, ring_count=segments // 2)
    obj = bpy.context.active_object
    obj.name = name
    bpy.ops.object.shade_smooth()
    return obj


def counts():
    return {kind: len(getattr(bpy.data, kind)) for kind in ("scenes", "objects", "worlds", "cameras", "lights",
                                                             "images", "meshes")}


def main():
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        scene = bpy.context.scene
        target = sphere("Target", 1.0)
        own = material("Own", (0.6, 0.25, 0.1, 1.0))
        target.data.materials.append(own)
        camera = bpy.data.objects.new("PersonCamera", bpy.data.cameras.new("PersonCamera"))
        scene.collection.objects.link(camera)
        camera.location = (0, -6, 0)
        camera.rotation_euler = (1.5708, 0, 0)
        scene.camera = camera
        scene.render.engine = "CYCLES"
        scene.cycles.samples, scene.render.resolution_x, scene.render.resolution_y = 1, 32, 32
        bpy.ops.render.render()   # The person's last render: its slot and pixels must stay.
        result = next(image for image in bpy.data.images if image.type == "RENDER_RESULT")
        person_slot = result.render_slots.active_index
        same = material("Same", (0.6, 0.25, 0.1, 1.0))
        changed = material("Changed", (0.6, 0.25, 0.1, 1.0), half=(0.1, 0.35, 0.7, 1.0))
        before = counts()
        REPORT["phase"] = "same"
        start = time.perf_counter()
        facts = bake_check_host.check(target, same)
        REPORT["facts"]["check_s"] = round(time.perf_counter() - start, 3)
        REPORT["facts"]["same"] = facts
        check("same_material_compares_at_zero", facts["delta_e_mean"] == 0.0 and facts["only_one_share"] == 0.0)
        REPORT["phase"] = "changed"
        facts = bake_check_host.check(target, changed)
        REPORT["facts"]["changed"] = facts
        worst = facts["worst"]
        check("a_changed_half_is_seen", facts["delta_e_mean"] > 5 * JUST_NOTICEABLE and worst["delta_e"] > 20.0,
              "mean %.2f, worst %.2f" % (facts["delta_e_mean"], worst["delta_e"]))
        REPORT["phase"] = "high_to_low"
        source = sphere("Source", 1.08, segments=96)
        source.data.materials.append(material("SourcePaint", (0.6, 0.25, 0.1, 1.0)))
        before_sources = counts()
        facts = bake_check_host.check(target, same, sources=[source])
        REPORT["facts"]["high_to_low"] = facts
        check("a_larger_source_shows_as_silhouette", 0.0 < facts["only_one_share"] < 0.4
              and facts["delta_e_mean"] < 5 * JUST_NOTICEABLE, "only one %.4f" % facts["only_one_share"])
        check("nothing_left", counts() == before_sources and before["scenes"] == counts()["scenes"],
              "%s -> %s" % (before_sources, counts()))
        check("persons_render_result_kept", result.render_slots.active_index == person_slot
              and result.name in bpy.data.images)
        check("persons_materials_untouched", [slot.material for slot in target.material_slots] == [own]
              and [slot.link for slot in target.material_slots] == ["DATA"])
        REPORT["phase"] = "complete"
        REPORT["failed_checks"] = [name for name, passed in REPORT["checks"].items() if not passed]
        REPORT["success"] = bool(REPORT["checks"]) and not REPORT["failed_checks"]
    except Exception:
        REPORT["error"] = traceback.format_exc()
    write_report()
    print("ANGELO_BAKE_CHECK", "PASS" if REPORT["success"] else "FAIL")
    sys.exit(0 if REPORT["success"] else 1)


main()
