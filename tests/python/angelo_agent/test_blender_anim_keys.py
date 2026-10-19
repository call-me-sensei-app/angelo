# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Key editing through real staged programs: configure, edit, F-Curve modifiers and copy/paste.

Covers every keyframe_edit operation and the extended keyframe_configure on
real channelbags, fcurve_modifier add/configure/remove (the object stays
bindable afterwards), keyframe_copy between objects, staging isolation,
PREVIEW/accept, stale revisions, the shared-Action refusal and explicit
SINGLE_USER isolation, a camera whose data shares its Action, commit and one
Undo/Redo step each, and the measured cost of the key-table rewrites at the
key budget. Run only in a disposable instance with the exact Angelo binary:
``--background --factory-startup --disable-autoexec --python-exit-code 1``.
No provider or paid call.
"""

import json
import math
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-anim-keys-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent.anim_channels import channelbag  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator  # noqa: E402

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "timings": {}}
window = bpy.context.window_manager.windows[0]


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def settle(job):
    for _ in range(400):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("cooperative native program did not settle")


def commit(job):
    result = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    report.setdefault("results", []).append({key: job.result.get(key) for key in ("status", "error")})
    return "FINISHED" in result and job.result.get("status") == "committed"


def obj(name="Cube"):
    return window.scene.objects[name]


def handle(item):
    return queries.object_handle(item)


def program(operations, writes, reads=(), mode="APPLY"):
    entries = lambda items: [{"handle": handle(item), "revision": queries.object_revision(window, item)} for item in items]
    return validate_call("angelo_scene_program", {"mode": mode, "read_set": entries(reads), "operations": operations,
                                                  "write_set": entries(writes)})


def op(kind, item, **parameters):
    return {"kind": kind, "target": handle(item), "parameters": parameters}


def run(arguments):
    job = native.begin(window, "angelo_scene_program", arguments)
    return job, settle(job)


def passed(job):
    """Every derived or declared check passed (a program without checks has none to fail)."""
    return all(row.get("passed") for row in job.result.get("checks") or ())


def curve(item, path="location", index=0):
    bag = channelbag(item)
    return bag.fcurves.find(path, index=index) if bag is not None else None


def keys(item, path="location", index=0):
    found = curve(item, path, index)
    return [(round(p.co.x, 3), round(p.co.y, 4)) for p in found.keyframe_points] if found else []


def undo():
    bpy.ops.ed.undo()
    queries.invalidate()


def redo():
    bpy.ops.ed.redo()
    queries.invalidate()


def baseline(message):
    bpy.ops.ed.undo_push(message=message)
    queries.invalidate()


def blocks(item):
    """Per-dependency RNA of the fingerprint, for a readable difference when an Undo check fails."""
    from angelo_agent.program_dependencies import _mutable_dependencies, _rna_values
    return {block.bl_rna.identifier + ":" + block.name: _rna_values(block) for block in _mutable_dependencies(item)}


def difference(left, right, path=""):
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            found = difference(left.get(key), right.get(key), path + "." + str(key))
            if found:
                return found
        return None
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        for index, (a, b) in enumerate(zip(left, right)):
            found = difference(a, b, path + "[%d]" % index)
            if found:
                return found
        return None
    return None if left == right else [path, str(left)[:200], str(right)[:200]]


def committed(name, operations, writes, reads=(), expect=None):
    """Commit, check the live result, then one Undo restores and one Redo replays it; leaves it undone."""
    target = writes[0].name  # Python references go stale after Undo: look objects up by name.
    before, before_blocks = native._fingerprint(writes[0]), blocks(writes[0])
    job, staged = run(program(operations, writes, reads))
    check(name + "_ready_without_live_edit", staged.get("ready_to_commit") and native._fingerprint(obj(target)) == before
          and no_stages())
    check(name + "_commits", commit(job) and passed(job))
    after = native._fingerprint(obj(target))
    if expect is not None:
        check(name + "_live_result", expect())
    undo()
    if native._fingerprint(obj(target)) != before:
        report.setdefault("undo_differences", {})[name] = difference(before_blocks, blocks(obj(target)))
    check(name + "_one_undo", native._fingerprint(obj(target)) == before and no_stages())
    redo()
    check(name + "_one_redo", native._fingerprint(obj(target)) == after)
    undo()
    return job


def refused(name, operations, writes, code, reads=()):
    try:
        job, result = run(program(operations, writes, reads))
        message = result.get("error") or ""
        ok = result.get("status") == "failed" and code in message
    except ValueError as error:  # Refused at validation, before staging.
        message, ok = str(error), code in str(error)
    report.setdefault("refusals", {})[name] = message[:300]
    check(name, ok and no_stages())


def animate():
    cube = obj()
    cube.animation_data_clear()
    for frame, x in ((1, 0), (10, 2), (20, 4)):
        cube.location = (x, 0, 0)
        cube.rotation_euler = (0, 0, 0)
        cube.keyframe_insert("location", frame=frame)
    cube.location = (0, 0, 0)
    other = bpy.data.objects.new("Other", bpy.data.meshes.new("Other"))
    window.scene.collection.objects.link(other)
    window.view_layer.update()
    baseline("anim keys baseline")


def configure_and_edit():
    cube = obj()
    committed("configure_all_channels", [op("keyframe_configure", cube, interpolation="LINEAR"),
                                         op("keyframe_configure", cube, property="location", extrapolation="LINEAR")],
              [cube], expect=lambda: all(p.interpolation == "LINEAR" for p in curve(obj()).keyframe_points)
              and curve(obj()).extrapolation == "LINEAR" and curve(obj(), index=2).extrapolation == "LINEAR")
    committed("configure_range_key_type", [op("keyframe_configure", obj(), property="location", index=0,
                                              frame_range=[5, 15], key_type="BREAKDOWN")], [obj()],
              expect=lambda: [p.type for p in curve(obj()).keyframe_points] == ["KEYFRAME", "BREAKDOWN", "KEYFRAME"])
    committed("edit_offset_merges", [op("keyframe_edit", obj(), operation="OFFSET", property="location",
                                        frame_range=[10, 20], offset=-9)], [obj()],
              expect=lambda: keys(obj()) == [(1, 2.0), (11, 4.0)])
    committed("edit_scale_mirror", [op("keyframe_edit", obj(), operation="SCALE", property="location", index=0,
                                       factor=-1, pivot=10)], [obj()],
              expect=lambda: keys(obj()) == [(0, 4.0), (10, 2.0), (19, 0.0)])
    value = curve(obj()).evaluate(5)
    committed("edit_insert_keeps_value", [op("keyframe_edit", obj(), operation="INSERT", property="location", index=0,
                                             frames=[5])], [obj()],
              expect=lambda: len(keys(obj())) == 4 and abs(dict(keys(obj()))[5] - value) < 1e-3)
    committed("edit_delete_channel", [op("keyframe_edit", obj(), operation="DELETE", property="location", index=1)],
              [obj()], expect=lambda: curve(obj(), index=1) is None and len(keys(obj())) == 3)
    committed("edit_bake", [op("keyframe_edit", obj(), operation="BAKE", property="location", index=0,
                               frame_range=[1, 20], step=1)], [obj()], expect=lambda: len(keys(obj())) == 20)
    committed("edit_snap_after_scale", [op("keyframe_edit", obj(), operation="SCALE", property="location", factor=1.37,
                                           pivot=1),
                                        op("keyframe_edit", obj(), operation="SNAP", property="location")], [obj()],
              expect=lambda: all(float(x).is_integer() for x, _ in keys(obj())))
    refused("configure_missing_frame_refused", [op("keyframe_configure", obj(), property="location", frames=[7],
                                                   interpolation="LINEAR")], [obj()], "KEY_NOT_FOUND")
    refused("unknown_channel_refused", [op("keyframe_edit", obj(), operation="DELETE", property="scale")], [obj()],
            "CHANNEL_NOT_FOUND")
    refused("insert_bound_refused", [op("keyframe_edit", obj(), operation="BAKE", property="location",
                                        frame_range=[1, 90000], step=1)], [obj()], "TOO_MANY_KEYS")


def filters():
    """Clean, decimate, smooth and the Euler filter on dense channels."""
    cube = obj()
    for frame in range(1, 42):
        cube.location = (math.sin(frame * .3), 1.0 if frame < 20 else 1.0 + 1e-6, 0)
        cube.rotation_euler = (0, 0, math.radians(frame * 20 % 360))
        cube.keyframe_insert("location", frame=frame)
        cube.keyframe_insert("rotation_euler", frame=frame)
    window.scene.frame_set(window.scene.frame_current)  # Animated values written back before the baseline.
    baseline("anim keys dense")
    committed("clean_flat_channel", [op("keyframe_edit", obj(), operation="CLEAN", property="location", index=1,
                                        threshold=.001)], [obj()], expect=lambda: len(keys(obj(), index=1)) == 1)
    committed("decimate_ratio", [op("keyframe_edit", obj(), operation="DECIMATE", property="location", index=0,
                                    ratio=.5)], [obj()], expect=lambda: 21 <= len(keys(obj())) <= 23)
    before = keys(obj())
    committed("smooth_interior", [op("keyframe_edit", obj(), operation="SMOOTH", property="location", index=0)], [obj()],
              expect=lambda: keys(obj())[0] == before[0] and keys(obj())[-1] == before[-1] and keys(obj()) != before)
    committed("euler_filter_continuous", [op("keyframe_edit", obj(), operation="EULER_FILTER")], [obj()],
              expect=lambda: all(abs(b - a) < math.pi for (_, a), (_, b) in zip(keys(obj(), "rotation_euler", 2),
                                                                                 keys(obj(), "rotation_euler", 2)[1:])))


def modifiers():
    cube = obj()
    job = committed("fcurve_modifier_add", [op("fcurve_modifier", cube, property="location", index=0, type="NOISE",
                                               name="Shake", settings={"strength": 0.5, "phase": 2.0})], [cube],
                    expect=lambda: curve(obj()).modifiers[0].name == "Shake" and abs(curve(obj()).modifiers[0].strength - .5) < 1e-6)
    check("fcurve_modifier_derived_check", any(row.get("kind") == "fcurve_modifier" for row in job.result["checks"]))
    redo()  # Keep the modifier: a later program must still bind the object.
    revision = queries.object_revision(window, obj())
    check("modified_channel_stays_bindable", isinstance(revision, str) and len(revision) == 64)
    committed("fcurve_modifier_configure_remove", [
        op("fcurve_modifier", obj(), property="location", index=0, mode="CONFIGURE", name="Shake", settings={"depth": 2}),
        op("fcurve_modifier", obj(), property="location", index=0, mode="ADD", type="CYCLES", name="Loop"),
        op("fcurve_modifier", obj(), property="location", index=0, mode="REMOVE", name="Shake")], [obj()],
        expect=lambda: [m.name for m in curve(obj()).modifiers] == ["Loop"])
    refused("fcurve_modifier_cost_refused", [op("fcurve_modifier", obj(), property="location", index=0,
                                                mode="CONFIGURE", name="Shake", settings={"depth": 30})],
            [obj()], "FMODIFIER_COST")
    committed("bake_modifier_in", [op("keyframe_edit", obj(), operation="BAKE", property="location", index=0,
                                      frame_range=[1, 10])], [obj()], expect=lambda: not len(curve(obj()).modifiers))
    undo()  # The Shake modifier is gone again.
    check("modifier_undone", not len(curve(obj()).modifiers))


def copy_keys():
    cube, other = obj(), obj("Other")
    committed("copy_keys_to_other", [op("keyframe_copy", other, source=handle(cube), property="location", offset=10)],
              [other], reads=[cube], expect=lambda: keys(obj("Other"))[0][0] == keys(obj())[0][0] + 10
              and len(keys(obj("Other"))) == len(keys(obj())) and obj("Other").animation_data.action.name == "OtherAction")
    check("copy_source_untouched", obj().animation_data.action.users == 1)
    committed("duplicate_keys_in_time", [op("keyframe_copy", obj(), source=handle(obj()), property="location", index=0,
                                            frame_range=[1, 10], offset=100, mode="REPLACE_RANGE")], [obj()],
              expect=lambda: any(x > 100 for x, _ in keys(obj())))


def shared_actions():
    cube, other = obj(), obj("Other")
    other.animation_data_create().action = cube.animation_data.action
    window.view_layer.update()
    baseline("anim keys shared")
    refused("shared_action_refused", [op("keyframe_edit", obj(), operation="OFFSET", property="location", offset=3)],
            [obj()], "ACTION_SHARED")
    refused("shared_action_delete_refused", [op("keyframe_delete", obj(), property="location", frame=1)], [obj()],
            "ACTION_SHARED")
    shared = obj().animation_data.action
    job = committed("single_user_then_edit", [op("action_set", obj(), mode="SINGLE_USER"),
                                              op("keyframe_edit", obj(), operation="OFFSET", property="location", offset=3)],
                    [obj()], expect=lambda: obj().animation_data.action != obj("Other").animation_data.action
                    and obj("Other").animation_data.action == shared)
    check("single_user_reported", "SINGLE_USER" in json.dumps(job.result.get("isolated_shared_data")))
    obj("Other").animation_data_clear()
    window.view_layer.update()
    baseline("anim keys unshared")


def camera_data_shares_action():
    camera = obj("Camera")
    camera.keyframe_insert("location", frame=1)
    camera.data.lens = 70
    camera.data.keyframe_insert("lens", frame=1)
    window.scene.frame_set(window.scene.frame_current)
    baseline("anim keys camera")
    check("camera_data_shares_action", camera.data.animation_data.action == camera.animation_data.action)
    committed("camera_object_keys_keep_data_sharing", [op("keyframe_edit", obj("Camera"), operation="OFFSET",
                                                          property="location", offset=4)], [obj("Camera")],
              expect=lambda: obj("Camera").data.animation_data.action == obj("Camera").animation_data.action
              and obj("Camera").data.animation_data.action_slot.identifier.startswith("CA")
              and keys(obj("Camera"))[0][0] == 5)
    committed("camera_data_channel", [op("keyframe_configure", obj("Camera"), property="data.lens",
                                         interpolation="CONSTANT")], [obj("Camera")],
              expect=lambda: channelbag(obj("Camera").data).fcurves.find("lens").keyframe_points[0].interpolation
              == "CONSTANT")


def preview_and_stale():
    cube = obj()
    arguments = program([op("keyframe_edit", cube, operation="OFFSET", property="location", offset=2)], [cube],
                        mode="PREVIEW")
    job, preview = run(arguments)
    check("preview_retained", preview.get("status") == "preview" and no_stages())
    accepted = native.begin(window, "angelo_accept_preview", {"preview_id": preview["preview_id"]})
    settle(accepted)
    check("preview_accepted_commits", commit(accepted))
    undo()
    stale = program([op("keyframe_configure", obj(), property="location", frames=[1], key_type="EXTREME")], [obj()])
    curve(obj()).keyframe_points[0].type = "JITTER"  # A manual edit after observation.
    queries.invalidate()
    job, result = run(stale)
    check("stale_key_type_refused", result.get("status") == "failed" and no_stages())
    curve(obj()).keyframe_points[0].type = "KEYFRAME"
    queries.invalidate()


def timings():
    """Programs on one channel of n and 4n keys (4n near the 8,000-key budget), against a transform on the same
    object (the program's own binding, staging and verification cost), and the kernel alone."""
    import numpy as np
    from angelo_agent.action_kernels import StagedActionContext, apply_operation
    for count in (2000, 7900):
        name = "Dense%d" % count
        item = bpy.data.objects.new(name, bpy.data.meshes.new(name))
        window.scene.collection.objects.link(item)
        item.keyframe_insert("location", frame=0, index=0)
        points = curve(item).keyframe_points
        points.add(count - 1)
        co = np.empty(count * 2, dtype=np.float32)
        co[0::2], co[1::2] = np.arange(count), np.sin(np.arange(count) * .01)
        points.foreach_set("co", co)
        curve(item).update()
        window.scene.frame_set(window.scene.frame_current)
        baseline("anim keys dense timing")
        cases = (("transform", {}, {"location": [0, 0, 1]}), ("OFFSET", {"offset": 3}, None),
                 ("DECIMATE", {"ratio": .5}, None), ("SMOOTH", {}, None), ("CLEAN", {"threshold": .0001}, None),
                 ("SCALE", {"factor": 2.0}, None), ("INSERT", {"frames": list(range(1, 64))}, None))
        for operation, parameters, transform in cases:
            operations = ([op("transform", obj(name), **transform)] if transform else
                          [op("keyframe_edit", obj(name), operation=operation, property="location", index=0, **parameters)])
            arguments = program(operations, [obj(name)])
            started = time.perf_counter()
            job, result = run(arguments)
            report["timings"]["program_%s_%d" % (operation, count)] = round(time.perf_counter() - started, 3)
            if not result.get("ready_to_commit"):
                report["timing_error"] = [operation, count, result.get("error")]
            check("timing_%s_%d_ready" % (operation.lower(), count), result.get("ready_to_commit"))
            job.cancel()
            if transform:
                continue
            # The kernel alone, on a private copy (no binding, staging or verification).
            scene = bpy.data.scenes.new("__timing")
            collection = bpy.data.collections.new("__timing")
            scene.collection.children.link(collection)
            copy = obj(name).copy()
            copy.data = obj(name).data.copy()
            copy.animation_data.action = obj(name).animation_data.action.copy()
            collection.objects.link(copy)
            context = StagedActionContext(scene, collection, objects={"o": copy},
                                          owned_ids=[copy.animation_data.action])
            mesh = copy.data
            started = time.perf_counter()
            apply_operation(context, {"kind": "keyframe_edit", "target": "o", "parameters": dict(
                operation=operation, property="location", index=0, **parameters)})
            report["timings"]["kernel_%s_%d" % (operation, count)] = round(time.perf_counter() - started, 4)
            action = copy.animation_data.action
            bpy.data.objects.remove(copy)
            bpy.data.meshes.remove(mesh)
            bpy.data.actions.remove(action)
            bpy.data.collections.remove(collection)
            bpy.data.scenes.remove(scene)
    kernel = [value for key, value in report["timings"].items() if key.startswith("kernel_")]
    check("kernel_timings_bounded", max(kernel) < 1)
    refused("key_budget_refused", [op("keyframe_edit", obj("Dense7900"), operation="BAKE", property="location",
                                      frame_range=[8000, 8200])], [obj("Dense7900")], "TOO_MANY_KEYS")


def kernel(names, kind, parameters, prepare=None, extras=None, target="o0"):
    """Run one kernel on private copies of ``names`` (handles o0, o1...): the refusals a revision cannot reach."""
    from angelo_agent.action_kernels import StagedActionContext, apply_operation
    scene = bpy.data.scenes.new("__kernel")
    collection = bpy.data.collections.new("__kernel")
    scene.collection.children.link(collection)
    copies, owned = {}, []
    try:
        for index, name in enumerate(names):
            copy = obj(name).copy()
            if copy.data is not None:
                copy.data = obj(name).data.copy()
                owned.append(copy.data)
            if copy.animation_data is not None and copy.animation_data.action is not None:
                copy.animation_data.action = copy.animation_data.action.copy()
                owned.append(copy.animation_data.action)
            collection.objects.link(copy)
            copies["o%d" % index] = copy
        if prepare is not None:
            prepare(copies)
        context = StagedActionContext(scene, collection, objects=copies, owned_ids=owned, extras=extras or {})
        return apply_operation(context, {"kind": kind, "target": target, "parameters": parameters})
    finally:
        for copy in copies.values():
            bpy.data.objects.remove(copy)
        for block in owned:
            getattr(bpy.data, "actions" if isinstance(block, bpy.types.Action) else "meshes"
                    if isinstance(block, bpy.types.Mesh) else "cameras").remove(block)
        bpy.data.collections.remove(collection)
        bpy.data.scenes.remove(scene)


def kernel_refused(name, code, *arguments, **keywords):
    try:
        kernel(*arguments, **keywords)
        message = ""
    except ValueError as error:
        message = str(error)
    report.setdefault("kernel_refusals", {})[name] = message[:200]
    check(name, code in message)


def refusal_table():
    """Every key-kind refusal the program paths above do not reach, on private copies."""
    from unittest.mock import patch
    from angelo_agent import anim_channels, anim_fmodifiers

    def driver(copies):
        copies["o0"].driver_add("scale", 0)

    def tweak(copies):
        animation = copies["o0"].animation_data
        track = animation.nla_tracks.new()
        track.strips.new("Tweak", 1, animation.action).select = True
        animation.nla_tracks.active = track
        animation.use_tweak_mode = True
        assert animation.use_tweak_mode

    def envelope(copies):
        copies["o0"].animation_data.action.layers[0].strips[0].channelbags[0].fcurves.find(
            "location", index=0).modifiers.new("ENVELOPE").name = "Env"

    def custom(copies):
        copies["o1"]["prop"] = 1.0
        copies["o1"].keyframe_insert('["prop"]', frame=1)
        copies["o1"].animation_data.action.name = "__kernel_custom"

    location = {"operation": "OFFSET", "property": "location", "offset": 1}
    # Drivers are refused before any kernel runs (action_budget); the kernels' own DRIVEN guard is a second wall.
    kernel_refused("driven_refused", "drivers", ["Cube"], "keyframe_edit", location, prepare=driver)
    kernel_refused("tweak_mode_refused", "TWEAK_MODE", ["Cube"], "keyframe_edit", location, prepare=tweak)
    kernel_refused("no_action_refused", "KEYS_NOT_FOUND", ["Other"], "keyframe_edit", location)
    kernel_refused("data_channel_without_data_refused", "KEYS_NOT_FOUND", ["Cube"], "keyframe_configure",
                   {"property": "data.lens", "interpolation": "LINEAR"})
    pin = bpy.data.objects.new("Pin", None)  # An empty: no data to key.
    window.scene.collection.objects.link(pin)
    pin.keyframe_insert("location", frame=1)
    kernel_refused("data_channel_of_empty_refused", "NO_DATA", ["Pin"], "keyframe_configure",
                   {"property": "data.size", "interpolation": "LINEAR"})
    with patch.object(anim_channels, "MAX_CHANNELS", 1):
        kernel_refused("channel_bound_refused", "TOO_MANY_CHANNELS", ["Cube"], "keyframe_edit", location)
    kernel_refused("assigned_action_edit_refused", "ACTION_ASSIGNED", ["Cube"], "keyframe_edit", location,
                   extras={"action_assigned": {"o0": 1}})
    kernel_refused("euler_without_rotation_refused", "CHANNEL_NOT_FOUND", ["Camera"], "keyframe_edit",
                   {"operation": "EULER_FILTER", "property": None})
    kernel_refused("configure_empty_range_refused", "KEY_NOT_FOUND", ["Cube"], "keyframe_configure",
                   {"property": "location", "frame_range": [500, 600], "interpolation": "LINEAR"})
    modifier = {"property": "location", "index": 0, "mode": "CONFIGURE", "name": "Missing", "settings": {"strength": 1}}
    kernel_refused("fmodifier_missing_refused", "FMODIFIER_NOT_FOUND", ["Cube"], "fcurve_modifier", modifier)
    kernel_refused("envelope_refused", "FMODIFIER_UNSUPPORTED", ["Cube"], "fcurve_modifier",
                   dict(modifier, name="Env", settings={"mute": True}), prepare=envelope)
    generator = {"property": "location", "index": 0, "mode": "ADD", "type": "GENERATOR", "name": "Poly"}
    kernel_refused("coefficients_length_refused", "RNA_ARRAY_LENGTH", ["Cube"], "fcurve_modifier",
                   dict(generator, settings={"coefficients": [1.0, 2.0, 3.0]}))
    kernel_refused("fmodifier_name_taken_refused", "NAME_TAKEN", ["Cube"], "fcurve_modifier",
                   dict(modifier, mode="ADD", type="NOISE", name="Env", settings={}), prepare=envelope)
    with patch.object(anim_fmodifiers, "MAX_MODIFIERS", 1):
        kernel_refused("fmodifier_bound_refused", "TOO_MANY_FMODIFIERS", ["Cube"], "fcurve_modifier",
                       dict(modifier, mode="ADD", type="NOISE", name="Noise 2", settings={}), prepare=envelope)
    kernel_refused("copy_channel_mismatch_refused", "CHANNEL_MISMATCH", ["Cube", "Other"], "keyframe_copy",
                   {"source": "o1", "property": '["prop"]'}, prepare=custom)
    kernel_refused("copy_source_without_keys_refused", "CHANNEL_NOT_FOUND", ["Cube", "Other"], "keyframe_copy",
                   {"source": "o1", "property": "location"})
    kernel_refused("copy_empty_range_refused", "KEY_NOT_FOUND", ["Other", "Cube"], "keyframe_copy",
                   {"source": "o1", "property": "location", "frame_range": [500, 600]})
    kernel_refused("copy_onto_shared_refused", "ACTION_SHARED", ["Cube", "Cube"], "keyframe_copy",
                   {"source": "o1", "property": "location"}, extras={"action_shared": {"o0": {"object": 1}}})


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    animate()
    configure_and_edit()
    copy_keys()
    shared_actions()
    filters()
    modifiers()
    camera_data_shares_action()
    preview_and_stale()
    refusal_table()
    timings()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "anim-keys.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_ANIM_KEYS", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
