# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the Game Physics panel's draw spends on a large skinned mesh (doc/angelo/game_physics.rst).

    Angelo --background --factory-startup --python bench_game_physics_panel.py -- OUT.json [ROUNDS] [CHARACTER.glb] \
        [SOURCES]

The draw's work besides Blender's layout is the fitted size (``game_physics_ui.fitted``) and the findings
(``game_physics.scene_findings``); a background Angelo has no layout to draw into, so those are timed as the draw
calls them. It runs on any build, so the same script times the build before the shared fit (fitting.py) and after.
A rig (a kinematic body) holds a posed, keyed armature deforming a ~130k-vertex sphere with a fitted box, once with
only its Armature modifier (``plain``) and once with a Subdivision Surface after it (``subdivided``). Arms, per
round, median and range in microseconds:

* ``draw_fitted_plain`` / ``draw_fitted_subdivided`` and ``draw_findings``: what one draw spends;
* ``frame``: a frame change; ``pose_update``: a pose change and its evaluation (which runs the depsgraph handlers,
  as playback does) and, with the shared fit, the timer's check, the cache holding entries;
* with the shared fit only: ``measure_plain`` / ``measure_subdivided``, the timer's one-off measurement (never in a
  draw), and ``settle_after_frame``, the timer's check after the frames.

A CHARACTER.glb with many animations (optional) adds ``draw_findings_character``. SOURCES, a checkout, times its
physics sources in the same app instead of the bundled ones: they are loaded over the glTF add-on's at startup
(a signed app's bundle can't be edited). Run the two alternately (ABAB), alone on a quiet machine.
"""

import json
import statistics
import sys
import time
from pathlib import Path

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
OUT = Path(argv[0])
ROUNDS = int(argv[1]) if len(argv) > 1 else 15
CHARACTER = Path(argv[2]) if len(argv) > 2 and argv[2] else None
SOURCES = Path(argv[3]) / "scripts/addons_core" if len(argv) > 3 else None
CALLS = 200


def load_sources(addons):
    """The checkout's physics modules in place of the bundled ones (``props`` stays: its classes are registered)."""
    import importlib
    import io_scene_gltf2.angelo_physics as package
    package.__path__.insert(0, str(addons / "io_scene_gltf2/angelo_physics"))
    for name in ("shapes", "fitting"):
        module = sys.modules.get("io_scene_gltf2.angelo_physics." + name)
        module = importlib.reload(module) if module else importlib.import_module("io_scene_gltf2.angelo_physics." + name)
        if name == "fitting":
            module.register()


sys.path.insert(0, str(SOURCES if SOURCES else Path(bpy.utils.system_resource("SCRIPTS")) / "addons_core"))
if SOURCES:
    load_sources(SOURCES)
from angelo_agent import game_physics, game_physics_ui as ui  # noqa: E402
from io_scene_gltf2.angelo_physics import shapes  # noqa: E402
try:
    from io_scene_gltf2.angelo_physics import fitting  # noqa: E402  (the shared fit; absent before it)
except ImportError:
    fitting = None


def rig(name, subdivide):
    body = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(body)
    body.angelo_physics.body = "KINEMATIC"
    armature = bpy.data.objects.new(name + "Armature", bpy.data.armatures.new(name + "Armature"))
    bpy.context.scene.collection.objects.link(armature)
    armature.parent = body
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode="EDIT")
    bone = armature.data.edit_bones.new("Bone")
    bone.head, bone.tail = (0, 0, 0), (0, 0, 1)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.mesh.primitive_uv_sphere_add(segments=512, ring_count=256)
    mesh = bpy.context.object
    mesh.name = name + "Mesh"
    from mathutils import Matrix
    mesh.data.transform(Matrix.Diagonal((0.3, 0.3, 1.0, 1.0)))   # Long along the bone: a pose changes its bounds.
    mesh.parent = armature
    group = mesh.vertex_groups.new(name="Bone")
    group.add(list(range(len(mesh.data.vertices))), 1.0, "REPLACE")
    mesh.modifiers.new("Armature", "ARMATURE").object = armature
    if subdivide:
        mesh.modifiers.new("Subdivision", "SUBSURF").levels = 1
    mesh.angelo_physics.shape = "BOX"
    pose = armature.pose.bones["Bone"]
    pose.rotation_mode = "XYZ"
    for frame, angle in ((1, 0.0), (50, 1.5)):
        pose.rotation_euler = (angle, 0, 0)
        pose.keyframe_insert("rotation_euler", frame=frame)
    return body, mesh


def micro(function):
    start = time.perf_counter_ns()
    for _ in range(CALLS):
        function()
    return (time.perf_counter_ns() - start) / CALLS / 1000.0


def once(function):
    start = time.perf_counter_ns()
    function()
    return (time.perf_counter_ns() - start) / 1000.0


def frames(count=20):
    scene = bpy.context.scene
    start = time.perf_counter_ns()
    for index in range(count):
        scene.frame_set(1 + index % 49)
    return (time.perf_counter_ns() - start) / count / 1000.0


def pose_updates(armatures, count=20):
    """A pose change and its evaluation, as each frame of playback does, then the timer's check (with the shared
    fit): what keeping the cache costs while animating."""
    start = time.perf_counter_ns()
    for index in range(count):
        for armature in armatures:
            armature.pose.bones["Bone"].rotation_euler = (0.05 * (index % 30), 0, 0)
        bpy.context.view_layer.update()
        if fitting:
            fitting.settle()
    return (time.perf_counter_ns() - start) / count / 1000.0


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    body, plain = rig("Plain", False)
    _body2, subdivided = rig("Subdivided", True)
    bpy.context.scene.frame_set(25)
    character = None
    if CHARACTER is not None:
        before = set(bpy.data.objects)
        bpy.ops.import_scene.gltf(filepath=str(CHARACTER))
        new = [obj for obj in bpy.data.objects if obj not in before]
        character = bpy.data.objects.new("Character", None)
        bpy.context.scene.collection.objects.link(character)
        character.angelo_physics.body = "RIGID"
        for obj in new:
            if obj.parent is None:
                obj.parent = character
            if obj.type == "MESH":
                obj.angelo_physics.shape = "BOX"
    arms = {"draw_fitted_plain": lambda: ui.fitted(plain), "draw_fitted_subdivided": lambda: ui.fitted(subdivided),
            "draw_findings": lambda: game_physics.scene_findings(bpy.context.scene,
                                                                 [body] + list(body.children_recursive))}
    if character is not None:
        arms["draw_findings_character"] = lambda: game_physics.scene_findings(
            bpy.context.scene, [character] + list(character.children_recursive))
    armatures = [plain.parent, subdivided.parent]
    extra = ["frame", "pose_update"] + (["measure_plain", "measure_subdivided", "settle_after_frame"] if fitting else [])
    samples = {name: [] for name in list(arms) + extra}
    if fitting:   # What the panel's timer does after its first draw.
        fitting.fitted(plain, plain.angelo_physics)
        fitting.fitted(subdivided, subdivided.angelo_physics)
    for function in arms.values():   # Untimed warm-up.
        function()
    frames(5)
    for _round in range(ROUNDS):
        if fitting:
            samples["measure_plain"].append(once(lambda: fitting.fitted(plain, plain.angelo_physics)))
            samples["measure_subdivided"].append(once(lambda: fitting.fitted(subdivided, subdivided.angelo_physics)))
        samples["frame"].append(frames())
        samples["pose_update"].append(pose_updates(armatures))
        if fitting:
            suspects = len(fitting._suspect)   # How many the handler noted during those frames.
            samples["settle_after_frame"].append(once(fitting.settle))
            if plain.session_uid not in fitting._cache or subdivided.session_uid not in fitting._cache:
                raise SystemExit("playback dropped a cache entry (a pose isn't in the signature)")
        for name, function in arms.items():
            samples[name].append(micro(function))
    fit = ui.fitted(plain)
    report = {"rounds": ROUNDS, "calls_per_sample": CALLS, "vertices": len(plain.data.vertices),
              "shared_fit": fitting is not None, "version": bpy.app.version_string, "unit": "microseconds",
              "arms": {name: {"median": statistics.median(values), "min": min(values), "max": max(values)}
                       for name, values in samples.items() if values},
              "posed_bounds_box": list(shapes.fit_bounds("BOX", *shapes.bounds(plain.bound_box), "AUTO").size),
              "panel_box": list(fit.size) if fit else None,
              "suspects_after_frames": suspects if fitting else None}
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("ANGELO_PHYSICS_PANEL_BENCH", json.dumps(report["arms"]), flush=True)


main()
