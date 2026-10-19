# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Camera-placement guards: what an orbit was measured from, checked when it commits, and the driver paths the
static walk reads.

Pure: the host-written ``measured_from`` field and its admission, the at-point
rule, the commit check (``camera_guards``) against stand-in backends, Blender's
camera-marker rule, and the path grammar and walk of ``dependency_paths``.
test_blender_camera_guards.py runs the check inside the Angelo binary.
"""

import copy
import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import camera_guards  # noqa: E402
from angelo_agent.dependency_paths import (INCOMPLETE, overlaps, path_root, path_steps, read_ids,  # noqa: E402
                                           reads_matrix, time_setting)
from angelo_agent.program_frames import placement_moves_with_time  # noqa: E402
from angelo_agent.program_jobs import ProgramError  # noqa: E402
from angelo_agent.program_orbits import resolve  # noqa: E402
from angelo_agent.program_spec import validate_program  # noqa: E402
from angelo_agent.refusal_codes import RefusalError  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from test_native_typed_review4 import constraint, driver, drivers, names, obj  # noqa: E402

EPOCH = "a" * 32
CAMERA = {"handle": "scn:%s:2" % EPOCH, "location": [7.0, -7.0, 5.0]}
OLD, SCENE = CAMERA["handle"], "scn:%s:1" % EPOCH
ORBIT = {"azimuth": math.pi, "elevation": .3}


def light(name, **parameters):
    return {"kind": "light_create", "target": None, "parameters": dict(name=name, look_at=[0, 0, 0], **parameters)}


def program(operations, **sets):
    return validate_program({"mode": "APPLY", "operations": copy.deepcopy(operations), **sets})


def entries(*handles):
    return [{"handle": handle, "revision": "r" * 64} for handle in handles]


class MeasuredFieldTests(unittest.TestCase):
    def test_a_resolved_program_states_the_camera_its_orbits_were_measured_from_once(self):
        resolved = resolve(program([light("Key", orbit=ORBIT), light("Rim", orbit=dict(ORBIT, azimuth=0.5))]), CAMERA)
        self.assertEqual(resolved["measured_from"], {"scene_camera": OLD, "cameras": [
            {"camera": OLD, "location": CAMERA["location"]}]})
        self.assertEqual(validate_program(resolved), resolved)  # Canonical: binding and review see the same text.

    def test_a_created_camera_is_measured_by_its_alias_where_the_program_states_it(self):
        made = {"kind": "camera_create", "target": None, "parameters": {"name": "Shot", "alias": "shot",
                                                                         "location": [0, -9, 3]}}
        switch = {"kind": "property_set", "target": SCENE, "parameters": {"values": {"camera": "$shot"}}}
        resolved = resolve(program([made, light("Sun", orbit=ORBIT), switch], write_set=entries(SCENE)), CAMERA)
        self.assertEqual(resolved["measured_from"]["scene_camera"], "$shot")
        self.assertEqual(resolved["measured_from"]["cameras"], [{"camera": "$shot", "location": [0.0, -9.0, 3.0]}])

    def test_a_program_without_orbits_carries_none(self):
        plain = program([light("Key", location=[1, 2, 3])])
        self.assertNotIn("measured_from", resolve(plain, CAMERA))

    def test_only_the_host_writes_it(self):
        resolved = resolve(program([light("Key", orbit=ORBIT)]), CAMERA)
        with self.assertRaisesRegex(ValueError, "Unexpected tool argument"):
            validate_call("angelo_scene_program", resolved)  # A model's call: no published schema lists it.
        self.assertEqual(validate_call("angelo_scene_program", resolved, host=True), resolved)

    def test_its_shape_is_closed(self):
        base = {"mode": "APPLY", "operations": [light("Key", location=[1, 2, 3])]}
        good = {"scene_camera": OLD, "cameras": [{"camera": OLD, "location": [1, 2, 3]}]}
        self.assertEqual(validate_program(dict(base, measured_from=good))["measured_from"]["cameras"][0]["location"],
                         [1.0, 2.0, 3.0])
        for bad in ({"scene_camera": OLD}, dict(good, extra=1), dict(good, cameras=[]),
                    dict(good, cameras=[{"camera": OLD, "location": [1, 2]}]),
                    dict(good, cameras=[{"camera": OLD, "location": [1, 2, float("nan")]}]),
                    dict(good, cameras=[{"camera": OLD, "location": [1, 2, True]}]),
                    dict(good, cameras=[{"camera": "$nobody", "location": [1, 2, 3]}]),
                    dict(good, cameras=good["cameras"] * 2)):
            with self.assertRaises(ValueError, msg=str(bad)):
                validate_program(dict(base, measured_from=bad))


class AtPointTests(unittest.TestCase):
    """An orbit placing the camera the program ends with is measured from the scene camera at its own point."""

    def setUp(self):
        self.made = {"kind": "camera_create", "target": None, "parameters": {
            "name": "New", "alias": "new", "look_at": [0, 0, 0], "orbit": ORBIT}}
        self.switch = {"kind": "property_set", "target": SCENE, "parameters": {"values": {"camera": "$new"}}}

    def test_deleting_the_measured_camera_afterwards_is_admitted_and_read_before_it_goes(self):
        delete = {"kind": "object_delete", "target": OLD, "parameters": {}}
        resolved = resolve(program([self.made, self.switch, delete], write_set=entries(SCENE, OLD)), CAMERA)
        self.assertEqual(resolved["measured_from"], {"scene_camera": "$new", "cameras": [
            {"camera": OLD, "location": CAMERA["location"]}]})

    def test_editing_the_measured_camera_afterwards_is_refused_before_approval(self):
        move = {"kind": "transform", "target": OLD, "parameters": {"location": [0, 0, 9]}}
        with self.assertRaisesRegex(RefusalError, r"operations\[2\] \(transform\) edits the scene camera"):
            resolve(program([self.made, self.switch, move], write_set=entries(SCENE, OLD)), CAMERA)


class Located:
    """An object as the check reads it: a world translation, evaluated or not."""

    def __init__(self, name, location, evaluated=None, hide_render=False):
        self.name, self.hide_render = name, hide_render
        self.matrix_world = NS(translation=list(location))
        self.evaluated = evaluated

    def evaluated_get(self, depsgraph):
        return NS(matrix_world=NS(translation=list(self.evaluated or self.matrix_world.translation)))


class Backend:
    def __init__(self, camera, objects, frame=(1, 0.0), markers=()):
        self.updates, self.objects = 0, objects
        self.scene = NS(camera=camera, frame_current=frame[0], frame_subframe=frame[1], timeline_markers=list(markers),
                        render=NS(frame_map_new=100, frame_map_old=100))
        self.frame = frame
        self.view_layer = NS(update=self._update, depsgraph=NS(objects=[]))

    def _update(self):
        self.updates += 1

    def _resolve(self, handle):
        if handle not in self.objects:
            raise ValueError(handle)
        return self.objects[handle]


def check_for(backend, measured, aliases=None, frame_written=False):
    backend.measured_cameras = dict(measured, frame_written=["frame_current"] if frame_written else [])
    stage = NS(context=NS(aliases=aliases or {}), originals={}, binding={"writes": []})
    return camera_guards.prepare(backend, stage)


class CommitCheckTests(unittest.TestCase):
    def setUp(self):
        self.camera = Located("Cam", [7, -7, 5])
        self.measured = {"scene_camera": OLD, "cameras": [{"camera": OLD, "location": [7.0, -7.0, 5.0]}]}

    def test_a_camera_where_it_was_measured_passes_and_one_that_moved_refuses(self):
        backend = Backend(self.camera, {OLD: self.camera})
        camera_guards.verify(check_for(backend, self.measured))
        self.camera.evaluated = [7, -7, 5 + 1e-6]  # Float32 noise of a re-evaluation: unchanged.
        camera_guards.verify(check_for(backend, self.measured))
        self.camera.evaluated = [7.01, -7, 5]
        with self.assertRaisesRegex(ProgramError, "CAMERA_MOVED.*'Cam'.*nothing this program wrote remains") as raised:
            camera_guards.verify(check_for(backend, self.measured))
        self.assertEqual(raised.exception.code, "CHECK_FAILED")

    def test_the_tolerance_is_float32_precision_far_from_the_origin_too(self):
        """Regression: 1e-5 of a coordinate let a 0.6 move pass 100,000 units out."""
        far = Located("Far", [100000.0, -7, 5])
        measured = {"scene_camera": OLD, "cameras": [{"camera": OLD, "location": [100000.0, -7.0, 5.0]}]}
        backend = Backend(far, {OLD: far})
        far.evaluated = [100000.0 + 2 ** -7, -7, 5]  # One float32 step at that magnitude.
        camera_guards.verify(check_for(backend, measured))
        far.evaluated = [100000.6, -7, 5]
        with self.assertRaisesRegex(ProgramError, "CAMERA_MOVED"):
            camera_guards.verify(check_for(backend, measured))
        far.evaluated = [float("-inf"), -7, 5]  # Regression: inf passed a relative comparison.
        with self.assertRaisesRegex(ProgramError, "not finite"):
            camera_guards.verify(check_for(backend, measured))

    def test_an_edit_after_approval_is_named_as_such(self):
        backend = Backend(self.camera, {OLD: self.camera})
        self.camera.evaluated = [9, -7, 5]  # Where it stands before any write: moved since it was measured.
        check = check_for(backend, self.measured)
        with self.assertRaisesRegex(ProgramError, "moved after it was measured, outside this program.*Submit the program again"):
            camera_guards.verify(check)

    def test_the_scene_must_look_through_the_camera_the_orbits_assumed(self):
        other = Located("Other", [0, 0, 9])
        backend = Backend(other, {OLD: self.camera})
        with self.assertRaisesRegex(ProgramError, "look through 'Cam'.*it looks through 'Other'"):
            camera_guards.verify(check_for(backend, self.measured))

    def test_a_camera_marker_switch_after_a_frame_move_is_seen(self):
        other = Located("Other", [0, 0, 9])
        markers = [NS(camera=self.camera, frame=1), NS(camera=other, frame=10)]
        backend = Backend(self.camera, {OLD: self.camera}, frame=(1, 0.0), markers=markers)
        backend.scene.frame_current = 12
        with self.assertRaisesRegex(ProgramError, "camera marker switches to it at frame 12"):
            camera_guards.verify(check_for(backend, self.measured, frame_written=True))
        backend.scene.frame_current = 5
        camera_guards.verify(check_for(backend, self.measured, frame_written=True))

    def test_a_deleted_camera_is_read_before_it_goes_and_a_vanished_one_refuses(self):
        backend = Backend(None, {OLD: self.camera})
        check = check_for(backend, dict(self.measured, scene_camera=None))
        camera_guards.before_deletions(check, (OLD,))
        self.assertEqual(backend.updates, 1)  # Evaluated once, with every other write, before the unlink.
        del backend.objects[OLD]
        camera_guards.verify(check)  # Already read: its unlink is the program's own deletion.
        with self.assertRaisesRegex(ProgramError, "no longer in the scene"):
            camera_guards.verify(check_for(backend, dict(self.measured, scene_camera=None)))

    def test_a_camera_the_program_made_and_deleted_stood_where_it_was_stated(self):
        measured = {"scene_camera": None, "cameras": [{"camera": "$shot", "location": [0.0, -9.0, 3.0]}]}
        camera_guards.verify(check_for(Backend(None, {}), measured))

    def test_the_frame_update_runs_for_orbits_until_their_cameras_settle(self):
        from unittest.mock import patch
        backend = Backend(self.camera, {OLD: self.camera})
        self.assertEqual(camera_guards.frame_update(check_for(backend, self.measured)), 0)  # No frame update.
        self.assertEqual(camera_guards.frame_update(check_for(backend, {}, frame_written=True)), 0)  # No orbit.
        self.assertEqual(backend.updates, 0)
        settling = iter([[0.0], [1.0], [1.0]])  # A dependency cycle that moves one step, then settles.
        with patch.object(camera_guards, "_matrices", lambda objects, depsgraph: next(settling)):
            self.assertEqual(camera_guards.frame_update(check_for(backend, self.measured, frame_written=True)), 2)
        self.assertEqual(backend.updates, 2)
        cycling = iter([[step % 5] for step in range(10)])  # Regression: a period-5 cycle never settles.
        with patch.object(camera_guards, "_matrices", lambda objects, depsgraph: next(cycling)):
            with self.assertRaisesRegex(ProgramError, "does not settle"):
                camera_guards.frame_update(check_for(backend, self.measured, frame_written=True))


class Depsgraph:
    def __init__(self, objects):
        self._objects = objects

    @property
    def objects(self):
        return self._objects()


class MarkerRuleTests(unittest.TestCase):
    def scene(self, frame, markers, subframe=0.0, old=100, new=100):
        return NS(frame_current=frame, frame_subframe=subframe, timeline_markers=markers,
                  render=NS(frame_map_new=new, frame_map_old=old))

    def test_blenders_marker_rule(self):
        first, second, hidden = NS(hide_render=False), NS(hide_render=False), NS(hide_render=True)
        markers = [NS(camera=second, frame=10), NS(camera=first, frame=1), NS(camera=hidden, frame=12),
                   NS(camera=None, frame=11)]
        self.assertIs(camera_guards.marker_camera(self.scene(5, markers)), first)
        self.assertIs(camera_guards.marker_camera(self.scene(10, markers)), second)
        self.assertIs(camera_guards.marker_camera(self.scene(15, markers)), second)  # Hidden for render: skipped.
        self.assertIs(camera_guards.marker_camera(self.scene(-3, markers)), first)  # Before every marker: the first.
        self.assertIsNone(camera_guards.marker_camera(self.scene(5, [])))
        # Blender's framelen is frame_map_old / frame_map_new (regression: the ratio was inverted).
        self.assertIs(camera_guards.marker_camera(self.scene(19, markers, old=50, new=100)), first)  # Time 9.5.
        self.assertIs(camera_guards.marker_camera(self.scene(5, markers, old=100, new=50)), second)  # Time 10.


class FrameUpdateSettingsTests(unittest.TestCase):
    """A preview-range or time-map write makes Blender run its frame update after commit too."""

    def test_the_settings_are_every_scene_setting_whose_update_sends_the_frame_notifier(self):
        import re
        from angelo_agent.program_frames import FRAME_UPDATE_PATHS
        source = Path(__file__).resolve().parents[3] / "source/blender/makesrna/intern/rna_scene.cc"
        if not source.exists():
            self.skipTest("Blender's RNA source is not in this checkout")
        found, struct, name = set(), None, None
        for line in source.read_text(encoding="utf-8").splitlines():
            defined = re.search(r'srna = RNA_def_struct\(brna, "(\w+)"', line)
            if defined:
                struct = defined.group(1)
            prop = re.search(r'RNA_def_property\(srna, "(\w+)"', line)
            if prop:
                name = prop.group(1)
            if re.search(r"RNA_def_property_update\(prop, NC_SCENE \| ND_FRAME\s*,", line) and struct in (
                    "Scene", "RenderSettings"):  # ND_FRAME exactly: wm_event_system runs the update for it alone.
                found.add(("render." if struct == "RenderSettings" else "") + name)
        self.assertEqual(found, set(FRAME_UPDATE_PATHS))

    def test_a_program_writing_one_gets_the_frame_rules(self):
        from angelo_agent.program_frames import frame_updates
        preview = {"kind": "property_set", "target": SCENE, "parameters": {"values": {"use_preview_range": True}}}
        camera = dict(CAMERA, moves_with_time=True)
        with self.assertRaisesRegex(RefusalError, "writes use_preview_range, after which Blender evaluates"):
            resolve(program([preview, light("Rim", orbit=ORBIT)], write_set=entries(SCENE)), camera)
        self.assertEqual(frame_updates(program([preview], write_set=entries(SCENE))), ["use_preview_range"])


class Evaluated:
    """An ID whose animation, when Blender evaluates it (a TIME tag and an update), sets ``animated`` channels."""

    def __init__(self, name, values, animated, paths):
        self.name, self.values, self.animated, self.paths, self.tagged = name, dict(values), animated, paths, False
        self.animation_data = NS(drivers=[], action=None, nla_tracks=[])

    def path_resolve(self, path, coerce=True):
        return self.values[path]

    def update_tag(self, refresh=()):
        self.tagged = "TIME" in refresh

    def __setattr__(self, name, value):
        if name in ("name", "values", "animated", "paths", "tagged", "animation_data"):
            object.__setattr__(self, name, value)
        else:
            self.values[name] = value


class UnkeyedWriteFactTests(unittest.TestCase):
    """What an animation replaces is measured with Blender's evaluator, not re-modelled."""

    def channels(self, *owners):
        from unittest.mock import patch
        from angelo_agent import animated_values
        with patch("angelo_agent.bone_state.animated_paths", lambda owner: owner.paths):
            return animated_values.Channels(owners)

    def test_only_what_the_evaluation_changes_is_stated_and_the_stored_values_are_written_back(self):
        from angelo_agent.animated_values import WRITE_FACT, evaluate, held_by
        keyed = Evaluated("Keyed", {"location": [0.0, 0.0, 0.0]}, {"location": [0.0, 0.0, 0.0]}, {"location"})
        still = Evaluated("Still", {"location": [5.0, 0.0, 0.0]}, {}, {"location"})  # Its curves give nothing new.
        channels = self.channels(keyed, still)
        keyed.values["location"] = [2.0, 1.0, 0.0]  # The program's writes.
        still.values["location"] = [5.0, 2.0, 0.0]
        stored = channels.read()
        changed = set(channels.changes(channels.values, stored))
        self.assertEqual(changed, {(0, 0), (0, 1), (1, 1)})

        def update():  # Blender evaluates the tagged IDs' animation at the scene's time and flushes it.
            for owner in (keyed, still):
                if owner.tagged:
                    owner.values.update({path: list(value) for path, value in owner.animated.items()})
                    owner.tagged = False
        now = evaluate(channels, {slot for slot, _ in changed}, NS(update=update))
        pairs = channels.changes(stored, now, only=changed)
        self.assertEqual(pairs, [(0, 0), (0, 1)])  # Still's write is not replaced; Keyed's y is replaced too.
        self.assertEqual(keyed.values["location"], (2.0, 1.0, 0.0))  # The committed values, written back.
        report = channels.stated(pairs, stored, now, WRITE_FACT, extra=held_by)
        self.assertEqual([(item["id"], item["path"], item["index"], item["value"], item["next"], item["held_by"])
                          for item in report["channels"]],
                         [("Keyed", "location", 0, 2.0, 0.0, "keys"), ("Keyed", "location", 1, 1.0, 0.0, "keys")])
        self.assertEqual((report["count"], report["truncated"]), (2, False))

    def test_a_long_list_says_how_many_and_that_it_is_cut(self):
        from angelo_agent.animated_values import FRAME_FACT, MAX_LISTED
        many = Evaluated("Many", {"value": [0.0] * (MAX_LISTED + 8)}, {}, {"value"})
        channels = self.channels(many)
        before = channels.read()
        many.values["value"] = [1.0] * (MAX_LISTED + 8)
        after = channels.read()
        report = channels.stated(channels.changes(before, after), before, after, FRAME_FACT)
        self.assertEqual((report["count"], report["truncated"], len(report["channels"])),
                         (MAX_LISTED + 8, True, MAX_LISTED))


class PathGrammarTests(unittest.TestCase):
    def test_quoted_keys_may_hold_brackets_dots_and_escaped_quotes(self):
        self.assertEqual(path_steps('["Shared [K5b]"].material.roughness'),
                         [('["Shared [K5b]"]', False), (".material", False), (".roughness", False)])
        self.assertEqual(path_steps('nodes["a\\"].]b"].inputs[0]'),
                         [("nodes", False), ('["a\\"].]b"]', False), (".inputs", False), ("[0]", True)])
        self.assertEqual(path_steps("matrix_world[3][0]"), [("matrix_world", False), ("[3]", True), ("[0]", True)])
        for broken in ('["open', "a..b", "a[x]", ".a", "a b", '["a"]x'):
            self.assertIsNone(path_steps(broken), broken)
        self.assertEqual(path_root('["rig]"].location'), '["rig]"]')
        self.assertEqual(path_root("a..b"), "*")

    def test_overlaps_and_time(self):
        self.assertTrue(overlaps("render", "render.resolution_x"))
        self.assertTrue(overlaps('nodes["A"]', 'nodes["A"].inputs[0]'))
        self.assertFalse(overlaps("render.resolution_x", "render.resolution_y"))
        self.assertFalse(overlaps("render.fps", "render.fps_base"))
        self.assertTrue(overlaps("*", "world"))
        self.assertTrue(time_setting("frame_current") and time_setting("frame_float") and time_setting("*"))
        self.assertFalse(time_setting("render.resolution_x") or time_setting("world"))


class Block:
    """An ID whose data paths resolve to the values given (a value that is an ID returns itself as id_data)."""

    def __init__(self, name, values=None, kind="Object"):
        self.name, self.values = name, values or {}
        self.bl_rna = NS(identifier=kind, properties=[])
        self.id_data = self

    def path_resolve(self, path, coerce=True):
        if path not in self.values:
            raise ValueError(path)
        return self.values[path]


class PathReadTests(unittest.TestCase):
    def test_every_crossing_is_kept_in_order_with_returns(self):
        camera = Block("Cam")
        data = Block("CamData", kind="Camera")
        camera.values = {"data": data, 'data["owner"]': camera, 'data["owner"].rotation_euler': NS(id_data=camera),
                         'data["owner"].rotation_euler[2]': 0.0}
        read = read_ids(camera, 'data["owner"].rotation_euler[2]')
        self.assertEqual([block.name for block in read.blocks], ["Cam", "CamData", "Cam"])
        self.assertEqual(read.own, ["data"])
        within = read_ids(camera, "")
        self.assertTrue(within.within)

    def test_unresolved_paths_read_nothing_and_unwalkable_ones_are_incomplete(self):
        holder = Block("Holder", {'["k]"].location[0]': 1.0})  # Resolves whole, but a step of it does not.
        self.assertIs(read_ids(holder, '["k]"].location[0]'), INCOMPLETE)
        self.assertIsNone(read_ids(holder, "missing"))
        self.assertIs(read_ids(holder, "a..b"), INCOMPLETE)

    def test_only_location_reads_are_exempt_from_reading_a_matrix(self):
        def var(kind, transform="LOC_X"):
            return NS(type=kind), NS(transform_type=transform)
        own = lambda *steps: NS(own=list(steps))  # noqa: E731
        self.assertFalse(reads_matrix(*var("TRANSFORMS", "LOC_Z"), own()))
        self.assertTrue(reads_matrix(*var("TRANSFORMS", "ROT_Z"), own()))
        self.assertTrue(reads_matrix(*var("TRANSFORMS", "SCALE_X"), own()))  # World scale turns with rotation.
        self.assertFalse(reads_matrix(*var("LOC_DIFF"), own()))
        self.assertTrue(reads_matrix(*var("ROTATION_DIFF"), own()))
        self.assertTrue(reads_matrix(*var("CONTEXT_PROP"), own("camera")))
        self.assertFalse(reads_matrix(*var("SINGLE_PROP"), own("location", "[0]")))
        self.assertFalse(reads_matrix(*var("SINGLE_PROP"), own('["dist"]')))
        self.assertFalse(reads_matrix(*var("SINGLE_PROP"), own("data")))  # Leaves at once: the data block is walked.
        self.assertTrue(reads_matrix(*var("SINGLE_PROP"), own("delta_rotation_euler")))
        self.assertTrue(reads_matrix(*var("SINGLE_PROP"), own("matrix_world", "[3]")))
        self.assertTrue(reads_matrix(*var("SINGLE_PROP"), own()))


class SeventhReviewWalkerTests(unittest.TestCase):
    def test_a_driver_reading_the_camera_back_through_a_pointer_counts_its_rotation(self):
        keyed = NS(layers=[NS(strips=[NS(channelbags=[NS(fcurves=[NS(data_path="rotation_euler")])])])])
        camera = obj("Cam", constraints=[constraint("TRACK_TO", target=obj("Subject"))])
        camera.values = {'["self"]': camera, '["self"].rotation_euler': NS(id_data=camera),
                         '["self"].rotation_euler[2]': 0.0}
        camera.path_resolve = lambda path, coerce=True: camera.values[path]
        camera.id_data = camera
        reading = driver("location", camera, variable="SINGLE_PROP")
        reading.driver.variables[0].targets[0].data_path = '["self"].rotation_euler[2]'
        camera.animation_data = NS(action=keyed, nla_tracks=[], drivers=[reading])
        self.assertTrue(placement_moves_with_time(camera))  # K8: it returns to the camera's keyed rotation.

    def test_a_context_path_is_walked_through_the_ids_it_crosses(self):
        ctl = obj("Ctl")
        ctl.id_data = ctl
        scene = Block("Shot", {'objects["Ctl"]': ctl, 'objects["Ctl"].location': NS(id_data=ctl),
                               'objects["Ctl"].location[0]': 0.0, "objects": NS(id_data=None)}, kind="Scene")
        scene.animation_data = None
        camera = obj("Cam", users_scene=[scene], animation=drivers(driver("location", None, variable="CONTEXT_PROP")))
        target = camera.animation_data.drivers[0].driver.variables[0].targets[0]
        target.context_property, target.data_path = "ACTIVE_SCENE", 'objects["Ctl"].location[0]'
        self.assertEqual(names(camera, scene_handle_of=lambda item: item.name), ["Ctl", "Shot#objects"])
        self.assertFalse(placement_moves_with_time(camera))  # K1: a still control, read through the scene.

    def test_a_scene_setting_marks_time_only_when_it_is_the_frame_or_keyed(self):
        scene = Block("Shot", kind="Scene")
        scene.values = {"render": NS(id_data=scene), "render.resolution_x": 1000}
        scene.animation_data = None
        camera = obj("Cam", users_scene=[scene], animation=drivers(driver("location", scene, variable="SINGLE_PROP")))
        camera.animation_data.drivers[0].driver.variables[0].targets[0].data_path = "render.resolution_x"
        self.assertFalse(placement_moves_with_time(camera))  # O4: a static setting is still.
        keyed = NS(layers=[NS(strips=[NS(channelbags=[NS(fcurves=[NS(data_path="render.resolution_x")])])])])
        scene.animation_data = NS(action=keyed, nla_tracks=[], drivers=[])
        self.assertTrue(placement_moves_with_time(camera))
        scene.animation_data = NS(action=NS(layers=[NS(strips=[NS(channelbags=[NS(fcurves=[NS(
            data_path="render.film_transparent")])])])]), nla_tracks=[], drivers=[])
        self.assertFalse(placement_moves_with_time(camera))  # A key on another setting reads nothing it reads.
        scene.values["frame_current"] = 1
        camera.animation_data.drivers[0].driver.variables[0].targets[0].data_path = "frame_current"
        self.assertTrue(placement_moves_with_time(camera))

    def test_a_path_that_resolves_but_cannot_be_walked_leaves_the_walk_incomplete(self):
        holder = obj("Holder")
        holder.path_resolve = lambda path, coerce=True: 1.0 if path == '["k]"].location[0]' else (
            _ for _ in ()).throw(ValueError(path))
        camera = obj("Cam", animation=drivers(driver("location", holder, variable="SINGLE_PROP")))
        camera.animation_data.drivers[0].driver.variables[0].targets[0].data_path = '["k]"].location[0]'
        self.assertIsNone(names(camera))


if __name__ == "__main__":
    unittest.main()
