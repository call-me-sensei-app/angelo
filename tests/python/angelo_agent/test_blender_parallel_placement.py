# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How a merge writes each placed root, and what it reports of keys and drivers (``worker_placement``, ``worker_keys``;
break-it E2E-15 H-1, L-1, L-2, L-3), through the lead chat's real merge of part files, without workers.

Parts are saved as a worker leaves them and merged as an Auto edit (admission, approval, the approved-change Undo
operator), then a frame change plays their keys. Checked, for every rotation mode and both Euler solutions of a turn
(the one Blender writes and the other, an upside-down part, X = pi; a pitch past 90 degrees), quaternions of either
sign and length, and axis-angles about either side: a placement that turns nothing keeps every rotation channel
exactly as built; a turning one writes the rotation nearest the built channels, and every placed matrix is exact; keys
on some channels then never put a copy in neither pose without a warning, and a copy they leave in its placed pose or
its keys' own pose is never warned. A root only evaluation gives is judged against what its stored channels hold (near
a 90 degree pitch, non-uniform), while a placement no basis can hold stays a skew, keyed or not; a driven root under a
constraint acting alike as built and as placed is its drivers', and is still counted off now by them; a child of a
bone its rig's drivers turn is listed as moving later with that reason. A part whose worker died is merged with a
warning saying so, whatever its chat's turn says. Keys that move between two times, merged at another frame than the
part was saved at, are judged at later frames against where the placement carries the animation (break-it E2E-17
H-1, M-1), across their whole keyed range whatever the lead's frame: a two-key full turn, tilted or mirrored, merged
before its keys start (E2E-18 M-1). Many keyed copies stay within the keys check's time budget, and copies past it are
counted, never judged by guess (E2E-18 M-2). No provider or paid call.

Run only in a disposable instance with the exact Angelo binary:
``--background --factory-startup --disable-autoexec --python-exit-code 1``.
"""

import json
import math
import os
import random
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bmesh
import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-parallel-placement-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_ENABLE_PARALLEL_AGENTS="1")
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import ui, worker_keys, worker_sessions  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.controller import ToolDispatchBudget, pump_controller_tools  # noqa: E402
from angelo_agent.headless_main import _register  # noqa: E402

report = {"checks": {}, "details": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "platform": sys.platform}
MOVE, SCALE, TURN = {"location": [5, 0, 0]}, {"location": [0, -8, 0], "scale": [2, 2, 2]}, {
    "location": [0, 8, 0], "rotation": [0.4, 0, 1.2]}
PLACEMENTS = [MOVE, TURN, SCALE]
Z_TURN = {"location": [0, 3, 0], "rotation": [0, 0, 0.6]}   # Turns about Z only: a world Limit on z acts alike.
SKEW = {"location": [0, 6, 0], "scale": [2, 1, 1]}   # Non-uniform across a turned root: no basis can hold it.
TILT = {"location": [0, 4, 0], "rotation": [1.5707963, 0, 0]}   # A quarter turn about X.
LEAD_FRAME, LATER = 10, (11, 20, 31)   # The lead merges at another frame than the parts were saved at (1).
JUDGED = LATER + (45, 70, 95)   # Where the truth is read: inside the turntable's keys too (20-120).
MIRROR = {"location": [0, -4, 0], "scale": [-1, 1, 1]}
ANIMATED, AT = ("Spin", "Bob", "Sweep", "Turntable"), {}   # AT: {frame: {name: the worker's own world at that frame}}
BAKED, BAKED_COPIES = 30, 64   # Roots keyed on location and rotation at every frame, each placed and turned 64 times.
ORDERS = ("XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX")
BUILT, CHANNELS = {}, {}


def check(name, passed, details=None):
    report["checks"][name] = bool(passed)   # Exactly true or false: the native runner reads nothing else.
    report["details"][name] = details


def cube(scene, name, size=0.3):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    return obj


def channels(obj):
    return {path: tuple(getattr(obj, path))
            for path in ("rotation_euler", "rotation_quaternion", "rotation_axis_angle")}


def drop_keys(obj, path, keep):
    """Keys on ``path`` left only on the indices in ``keep`` (as typed keyframe_delete leaves them)."""
    bag = obj.animation_data.action.layers[0].strips[0].channelbag(obj.animation_data.action_slot)
    for curve in [c for c in bag.fcurves if c.data_path == path and c.array_index not in keep]:
        bag.fcurves.remove(curve)


def rotations(seed=15, count=1):
    """Turns in both Euler solutions of every order: Blender's own, and (a + pi, pi - b, c + pi), the same turn."""
    pick = random.Random(seed)
    found = []
    for order in ORDERS:
        for n in range(count):
            angles = Euler([pick.uniform(-1.4, 1.4) for _ in range(3)], order)
            other = Euler((angles[0] + math.pi, math.pi - angles[1], angles[2] + math.pi), order)
            found += [("%s-%d-own" % (order, n), order, tuple(angles)),
                      ("%s-%d-other" % (order, n), order, tuple(other))]
    return found


def build_rotations(scene):
    """Roots in every mode and form; the last Euler angle (or the quaternion's w, the axis-angle's angle) keyed on
    some, location keyed on others, every channel on others."""
    roots = []
    for index, (name, order, angles) in enumerate(rotations()):
        obj = cube(scene, "E-" + name)
        obj.rotation_mode, obj.rotation_euler, obj.location = order, angles, (0.4 * index, 2.0, 0.0)
        roots.append(obj)
    turn = Euler((0.7, -0.3, 2.4)).to_quaternion()
    for name, quaternion in (("Q-pos", turn), ("Q-neg", -turn), ("Q-long", turn * 1.7)):
        obj = cube(scene, name)
        obj.rotation_mode, obj.rotation_quaternion, obj.location = "QUATERNION", quaternion, (0, 4.0, len(roots))
        roots.append(obj)
    axis, angle = turn.to_axis_angle()
    for name, value in (("A-pos", (angle, *axis)), ("A-neg", (-angle, *(-axis))),
                        ("A-wide", (angle + 2 * math.pi, *axis))):
        obj = cube(scene, name)
        obj.rotation_mode, obj.rotation_axis_angle, obj.location = "AXIS_ANGLE", value, (1.0, 4.0, len(roots))
        roots.append(obj)
    for obj in list(roots):
        for suffix, keyed in (("-last", "last"), ("-loc", "location"), ("-all", "all")):
            twin = obj.copy()
            twin.name = obj.name + suffix
            scene.collection.objects.link(twin)
            path = {"QUATERNION": "rotation_quaternion", "AXIS_ANGLE": "rotation_axis_angle"}.get(
                twin.rotation_mode, "rotation_euler")
            if keyed in ("last", "all"):
                twin.keyframe_insert(path, frame=1)
                if keyed == "last":
                    drop_keys(twin, path, {0 if path != "rotation_euler" else 2})
            if keyed in ("location", "all"):
                twin.keyframe_insert("location", frame=1)
            if keyed == "all":
                twin.keyframe_insert("scale", frame=1)
    scene.frame_set(1)
    bpy.context.view_layer.update()
    for obj in scene.objects:
        BUILT[obj.name], CHANNELS[obj.name] = obj.matrix_world.copy(), channels(obj)


def build_evaluated(scene):
    """Roots only evaluation gives: near a 90 degree pitch and non-uniform under a constraint that does not act (L-1);
    driven by their own place under a world Limit acting alike as built and as placed (L-2); a child of a bone its
    rig's drivers turn by the rig's place (L-3)."""
    for index, (pitch, scale) in enumerate(((math.pi / 2 - 1e-4, (-2, 0.5, 1.5)), (-math.pi / 2 + 1e-4, (0.1, 1, 4)))):
        obj = cube(scene, "Pitch-%d" % index)
        obj.rotation_euler, obj.scale, obj.location = (0.3 + index, pitch, 0.7), scale, (index, -3.0, 0.0)
        limit = obj.constraints.new("LIMIT_LOCATION")
        limit.use_max_z, limit.max_z, limit.owner_space = True, 100.0, "WORLD"   # Never acts here.
    clamp = cube(scene, "Clamp")
    clamp.location = (0.5, -5.0, -0.1)
    limit = clamp.constraints.new("LIMIT_LOCATION")
    limit.use_min_z, limit.min_z, limit.owner_space = True, 0.0, "WORLD"   # Clamps z -0.1 to 0, built and placed.
    driver = clamp.driver_add("rotation_euler", 2).driver
    variable = driver.variables.new()
    variable.type, variable.name = "TRANSFORMS", "x"
    variable.targets[0].id, variable.targets[0].transform_type = clamp, "LOC_X"
    variable.targets[0].transform_space = "WORLD_SPACE"
    driver.expression = "x * 0.3"
    rig_data = bpy.data.armatures.new("Rig")
    rig = bpy.data.objects.new("Rig", rig_data)
    scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    bone = rig_data.edit_bones.new("W")
    bone.head, bone.tail = (0, -7, 0), (0, -7, 1)
    bpy.ops.object.mode_set(mode="OBJECT")
    driver = rig.driver_add('pose.bones["W"].rotation_euler', 0).driver
    rig.pose.bones["W"].rotation_mode = "XYZ"
    variable = driver.variables.new()
    variable.type, variable.name = "TRANSFORMS", "x"
    variable.targets[0].id, variable.targets[0].transform_type = rig, "LOC_X"
    variable.targets[0].transform_space = "WORLD_SPACE"
    driver.expression = "x * 0.5"
    tyre = cube(scene, "Tyre", size=0.2)
    tyre.parent, tyre.parent_type, tyre.parent_bone = rig, "BONE", "W"
    tyre.matrix_world = Matrix.Translation((0, -7, 1.5))
    scene.frame_set(1)
    bpy.context.view_layer.update()


def build_skewed(scene):
    """Turned roots a non-uniform placement skews: one keyed on its place (only evaluation gives it), one plain."""
    for name in ("Skew Keyed", "Skew Plain"):
        obj = cube(scene, name)
        obj.rotation_euler, obj.location = (0, 0, 0.5), (0, 1.0 if name == "Skew Plain" else 0.0, 0)
        if name == "Skew Keyed":
            obj.keyframe_insert("location", frame=1)
    scene.frame_set(1)


def build_animated(scene):
    """Roots whose keys move between two times, saved at frame 1: Spin turns on Z (keyed Z only), Bob rises (keyed
    location z only), Sweep travels (location keyed in full), Turntable makes one full turn on Z from frame 20 to 120
    (equal poses at both keys, after the lead's frame). Each one's world at the judged frames is recorded."""
    spin, bob, sweep, turntable = (cube(scene, name) for name in ANIMATED)
    turntable.location = (3, 9, 0)
    for frame, z in ((20, 0.0), (120, 2 * math.pi)):
        turntable.rotation_euler[2] = z
        turntable.keyframe_insert("rotation_euler", index=2, frame=frame)
    spin.rotation_euler, spin.location = (0.4, 0, 0), (0, 9, 0)
    bob.rotation_euler, bob.location = (0, 0, 0.7), (1, 9, 0)
    sweep.location = (2, 9, 0)
    for frame, z, rise, travel in ((1, 0.0, 0.0, (2, 9, 0)), (30, 3.0, 2.0, (4, 10, 1))):
        spin.rotation_euler[2], bob.location[2], sweep.location = z, rise, travel
        spin.keyframe_insert("rotation_euler", index=2, frame=frame)
        bob.keyframe_insert("location", index=2, frame=frame)
        sweep.keyframe_insert("location", frame=frame)
    for frame in JUDGED:
        scene.frame_set(frame)
        AT[frame] = {name: bpy.data.objects[name].matrix_world.copy() for name in ANIMATED}
    scene.frame_set(1)


def build_baked(scene):
    """``BAKED`` roots keyed on location and rotation (not scale) at every frame 1-60, as a baked animation."""
    for index in range(BAKED):
        obj = cube(scene, "Baked %02d" % index, size=0.1)
        for frame in range(1, 61):
            obj.location, obj.rotation_euler = (index * 0.3, frame * 0.01, 0), (0, 0, frame * 0.05)
            obj.keyframe_insert("location", frame=frame)
            obj.keyframe_insert("rotation_euler", frame=frame)
    scene.frame_set(1)


def write_part(path, build):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    build(bpy.context.scene)
    bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
    return path


def merge(parts):
    """The lead merges ``parts`` ({number: (name, path, placements)}) as an Auto edit; its answer."""
    bpy.ops.wm.read_factory_settings()
    bpy.context.preferences.edit.use_global_undo = True
    _register(bpy, ui)
    window = bpy.context.window_manager.windows[0]
    window.scene.frame_set(LEAD_FRAME)
    lead = ui.get_controller()
    lead.state["options"] = dict(lead.state.get("options") or {}, approval="AUTO_BOUNDED")
    lead.set_document(ui.document_key())
    lead.fence = lead.open_external_read()
    worker_sessions._started[lead.state["id"]] = []
    for number, (name, path, _placements) in parts.items():
        worker_sessions._parts["qa-%d" % number] = (number, name, Path(path))
        worker_sessions._started[lead.state["id"]].append("qa-%d" % number)
    worker_sessions.died["qa-2"] = "The worker ended before it answered"   # Its worker crashed (E2E-15 M-2).
    answers = []
    arguments = {"parts": [{"worker": n, "placements": placements} for n, (_n, _p, placements) in parts.items()]}
    lead.request_tool("call-merge", "angelo_workers_merge", arguments, answers.append)

    def apply_mutation(target, call):
        with bpy.context.temp_override(window=window):
            bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=target.state["id"])
    started = time.perf_counter()
    while not answers and time.perf_counter() - started < 300:
        lead.pump_events()
        pump_controller_tools([lead], 0, BlenderExecutor(window), ui.document_key(), apply_mutation,
                              budget=ToolDispatchBudget(seconds=60))
    assert answers, "the merge was not answered"
    return answers[0], window


def copies(name):
    return [bpy.data.objects[name if index == 0 else "%s.%03d" % (name, index)] for index in range(len(PLACEMENTS))]


def placement_matrix(placement):
    return Matrix.LocRotScale(Vector(placement.get("location", (0, 0, 0))),
                              Euler(placement.get("rotation", (0, 0, 0))).to_quaternion(),
                              Vector(placement.get("scale", (1, 1, 1))))


def far(a, b):
    return max(abs(a[i][j] - b[i][j]) for i in range(4) for j in range(4))


def listed(merged, field):
    value = merged.get(field) or []
    return {entry["object"]: entry for entry in (value.get("objects") if isinstance(value, dict) else value)}


def run_checks():
    folder = OUT / "parts"
    folder.mkdir(parents=True, exist_ok=True)
    rotation_part = write_part(folder / "rotations.blend", build_rotations)
    evaluated_part = write_part(folder / "evaluated.blend", build_evaluated)
    skewed_part = write_part(folder / "skewed.blend", build_skewed)
    animated_part = write_part(folder / "animated.blend", build_animated)
    baked_part = write_part(folder / "baked.blend", build_baked)
    spent, real = [0.0], worker_keys.keys_mix

    def timed(*args, **kwargs):   # The keys check's own time in the merge, every call.
        started = time.perf_counter()
        try:
            return real(*args, **kwargs)
        finally:
            spent[0] += time.perf_counter() - started
    worker_keys.keys_mix, seconds, worker_keys.SECONDS = timed, worker_keys.SECONDS, 0.4   # A budget the part outlasts.
    turns = [{"location": [i * 0.5, -20, 0], "rotation": [0.3, 0.1 * i, 0.2]} for i in range(BAKED_COPIES)]
    try:
        answer, window = merge({1: ("Rotations", rotation_part, PLACEMENTS),
                                2: ("Evaluated", evaluated_part, [MOVE, Z_TURN]), 3: ("Skewed", skewed_part, [SKEW]),
                                4: ("Animated", animated_part, [MOVE, TILT, MIRROR]), 5: ("Baked", baked_part, turns)})
    finally:
        worker_keys.keys_mix, worker_keys.SECONDS = real, seconds
    report["answer"] = answer
    merged = {entry["worker"]: entry for entry in answer["merged"]}
    depsgraph = bpy.context.view_layer.depsgraph
    kept, nearest, inexact = [], [], []
    for name in CHANNELS:
        for placement, copy in zip(PLACEMENTS, copies(name)):
            if far(copy.evaluated_get(depsgraph).matrix_world, placement_matrix(placement) @ BUILT[name]) > 1e-4:
                inexact.append(copy.name)
            if placement is not TURN and channels(copy) != CHANNELS[name]:
                kept.append(copy.name)   # A placement that turns nothing keeps every rotation channel as built.
            if placement is TURN:
                built, now = CHANNELS[name], channels(copy)
                if copy.rotation_mode == "QUATERNION":
                    near = Quaternion(now["rotation_quaternion"]).dot(Quaternion(built["rotation_quaternion"])) >= 0
                elif copy.rotation_mode == "AXIS_ANGLE":
                    near = Vector(now["rotation_axis_angle"][1:]).dot(Vector(built["rotation_axis_angle"][1:])) >= 0
                else:
                    near = all(abs(a - b) <= math.pi + 1e-6 for a, b in zip(now["rotation_euler"],
                                                                             built["rotation_euler"]))
                if not near:
                    nearest.append(copy.name)
    check("every_placed_matrix_is_exact", not inexact, inexact[:10])
    check("a_placement_that_turns_nothing_keeps_every_rotation_channel", not kept, kept[:10])
    check("a_turning_placement_writes_the_rotation_nearest_its_built_channels", not nearest, nearest[:10])
    warned = listed(merged[1], "placement_warnings")
    later = listed(merged[1], "moves_later")
    window.scene.frame_set(LEAD_FRAME + 1)   # The keys play.
    depsgraph = bpy.context.view_layer.depsgraph
    silent, false = [], []
    for name in CHANNELS:
        if not name.endswith(("-last", "-loc", "-all")):
            continue
        source = bpy.data.objects[name]
        for placement, copy in zip(PLACEMENTS, copies(name)):
            now = copy.evaluated_get(depsgraph).matrix_world
            placed = placement_matrix(placement) @ BUILT[name]
            own = BUILT[name]
            neither = far(now, placed) > 1e-3 and far(now, own) > 1e-3
            said = name in warned and warned[name].get("why") == worker_keys.NEITHER and (
                PLACEMENTS.index(placement) + 1) in warned[name].get("placements", [])
            if neither and not said:
                silent.append(copy.name)
            if not neither and said:
                false.append(copy.name)
        if source.name not in later:
            silent.append(source.name + " (not listed as moving later)")
    check("keys_that_leave_a_copy_in_neither_pose_are_warned", not silent, silent[:10])
    check("keys_that_leave_a_copy_placed_or_where_they_put_it_are_never_warned", not false, false[:10])
    others = listed(merged[2], "placement_warnings")
    moving = listed(merged[2], "moves_later")
    check("a_root_only_evaluation_gives_is_judged_against_its_stored_channels",
          not {"Pitch-0", "Pitch-1"} & set(others), {name: others.get(name) for name in ("Pitch-0", "Pitch-1")})
    check("a_driven_root_under_a_constraint_acting_alike_is_its_drivers",
          "Clamp" not in others and "Clamp" in moving, {"warned": others.get("Clamp"), "later": moving.get("Clamp")})
    from angelo_agent import worker_placement   # A miss now needs the rig's driver to react at once: its reason.
    reason = worker_placement.why(bpy.data.objects["Tyre"])
    check("a_child_of_a_bone_its_rig_drives_is_listed_and_given_that_reason",
          "Tyre" in moving and "rig" in reason and ("Tyre" not in others or others["Tyre"].get("why") == reason),
          {"warned": others.get("Tyre"), "later": moving.get("Tyre"), "why": reason})
    check("a_driven_root_off_now_is_counted_by_its_drivers", moving.get("Clamp", {}).get("off_now"),
          moving.get("Clamp"))
    skewed = listed(merged[3], "placement_warnings")
    check("a_placement_no_basis_can_hold_is_warned_skewed_keyed_or_not",
          all(skewed.get(name, {}).get("why", "").startswith("skewed") for name in ("Skew Keyed", "Skew Plain")),
          skewed)
    animated, silent, false = listed(merged[4], "placement_warnings"), [], []
    for index, placement in enumerate((MOVE, TILT, MIRROR)):   # Judged where they play (E2E-17 H-1, M-1; E2E-18 M-1).
        for name in ANIMATED:
            copy = bpy.data.objects[name if index == 0 else "%s.%03d" % (name, index)]
            neither = False
            for frame in JUDGED:
                window.scene.frame_set(frame)
                now = copy.evaluated_get(bpy.context.view_layer.depsgraph).matrix_world
                own = AT[frame][name]
                neither |= far(now, placement_matrix(placement) @ own) > 1e-3 and far(now, own) > 1e-3
            entry = animated.get(name, {})
            reason = (entry.get("why_by_placement") or {}).get(str(index + 1), entry.get("why"))
            said = index + 1 in entry.get("placements", []) and reason == worker_keys.NEITHER
            (silent if neither and not said else false if said and not neither else []).append(copy.name)
    check("animated_keys_are_judged_where_they_play_never_at_the_merge_frame_alone", not silent and not false,
          {"silent": silent, "false": false, "warned": animated})
    baked = listed(merged[5], "moves_later")
    unchecked = sum(entry.get("keys_unchecked", 0) for entry in baked.values())
    check("many_keyed_copies_stay_within_the_keys_budget_and_the_rest_are_counted",
          spent[0] <= 0.4 + 0.5 and unchecked > 0 and merged[5].get("moves_later_total") == BAKED,
          {"keys_check_s": round(spent[0], 3), "budget_s": 0.4, "unchecked": unchecked,
           "listed": merged[5].get("moves_later_total"), "merge_ms": answer.get("merge_ms")})
    check("a_part_whose_worker_died_is_merged_with_a_warning", "warning" not in merged[1]
          and merged[2].get("warning", "").startswith("This worker ended before it finished (The worker ended"),
          {1: merged[1].get("warning"), 2: merged[2].get("warning")})
    report["success"] = all(report["checks"].values())


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
(OUT / "parallel-placement.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_PARALLEL_PLACEMENT", json.dumps({"success": report.get("success"),
                                              "failed": [k for k, v in report["checks"].items() if not v]}))
sys.exit(0 if report.get("success") else 1)
