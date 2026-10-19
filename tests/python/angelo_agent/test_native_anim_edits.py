# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Animation editing kinds without Blender: schemas, every spec refusal, families, derived checks,
the pure key filters, the NLA admission rules and the predicates on stand-ins.

Real channelbags, commits, rollbacks and Undo are covered by
test_blender_anim_keys.py and test_blender_anim_structure.py.
"""

import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import anim_filters as filters  # noqa: E402
from angelo_agent.action_catalog import affected_references, resource_dependencies, validate_operation  # noqa: E402
from angelo_agent.anim_channels import AnimationError, guard, in_selection  # noqa: E402
from angelo_agent.anim_nla import admit  # noqa: E402
from angelo_agent.anim_structure_spec import merge_markers  # noqa: E402
from angelo_agent.derived_checks import derive_checks  # noqa: E402
from angelo_agent.plan_schema import DEFERRED_ACTIONS, DEFERRED_CHECKS  # noqa: E402
from angelo_agent.validation_anim_edits import fcurve_modifier, markers, nla_structure  # noqa: E402
from angelo_agent.validation_spec import check_references, validate_check  # noqa: E402

OBJ, SRC, SCENE = ("scn:" + "a" * 32 + ":%d" % index for index in (7, 8, 9))
ACTION = "dat:" + "b" * 32 + ":ACTION:12"
KINDS = ("keyframe_configure", "keyframe_edit", "fcurve_modifier", "keyframe_copy", "action_set", "nla_edit", "marker_edit")
READER = SimpleNamespace(spend=lambda amount: None, context={"targets": {SRC: "camera"}})


def op(kind, target=OBJ, **parameters):
    return {"kind": kind, "target": target, "parameters": parameters}


class SchemaTests(unittest.TestCase):
    def test_kinds_and_checks_are_deferred(self):
        for kind in KINDS:
            self.assertIn(kind, DEFERRED_ACTIONS)
        for check in ("fcurve_modifier", "markers", "keyframes", "nla"):
            self.assertIn(check, DEFERRED_CHECKS)

    def test_families_and_references(self):
        cases = {"key": (op("keyframe_edit", operation="OFFSET", offset=2), ["object", "animation"]),
                 "data": (op("keyframe_configure", property="data.lens", interpolation="LINEAR"),
                          ["object", "animation", "data"]),
                 "stash": (op("action_set", mode="NEW", stash_previous=True), ["object", "animation", "nla_stack"]),
                 "rename": (op("action_set", mode="RENAME", name="Walk"), ["object"]),
                 "nla": (op("nla_edit", operation="TRACK_ADD", track="Base"), ["object", "nla_stack"]),
                 "markers": (op("marker_edit", SCENE, operation="ADD", name="Hit", frame=3), ["scene", "markers"])}
        for name, (operation, families) in cases.items():
            with self.subTest(case=name):
                self.assertEqual(resource_dependencies(operation)["mutates"], families)
        self.assertEqual(affected_references(op("keyframe_copy", source=SRC)), (OBJ, SRC))
        self.assertEqual(affected_references(op("marker_edit", SCENE, operation="BIND_CAMERA", marker="Hit",
                                                camera=SRC)), (SCENE, SRC))

    def test_every_spec_refusal(self):
        refusals = [
            op("keyframe_configure", index=1, interpolation="LINEAR"),  # An index without a property.
            op("keyframe_configure", property="location"),  # Nothing to configure.
            op("keyframe_configure", property="location", frames=[1], frame_range=[1, 2], easing="AUTO"),
            op("keyframe_configure", property="location", frames=[1], extrapolation="LINEAR"),
            op("keyframe_configure", property="location", frame_range=[5, 1], interpolation="LINEAR"),
            op("keyframe_configure", property="pose bones", interpolation="LINEAR"),  # Not a data path.
            op("keyframe_edit", operation="OFFSET", factor=2),  # Another operation's field.
            op("keyframe_edit", operation="OFFSET"),  # No movement.
            op("keyframe_edit", operation="DELETE", frames=[1], frame_range=[1, 2]),
            op("keyframe_edit", operation="SCALE", factor=0),
            op("keyframe_edit", operation="SCALE"),
            op("keyframe_edit", operation="INSERT"),
            op("keyframe_edit", operation="DECIMATE"), op("keyframe_edit", operation="DECIMATE", ratio=.5, error=1),
            op("keyframe_edit", operation="BAKE"), op("keyframe_edit", operation="BAKE", frame_range=[3, 3]),
            op("keyframe_edit", operation="EULER_FILTER", frame_range=[1, 2]),
            op("keyframe_edit", operation="EULER_FILTER", property="location"),
            op("fcurve_modifier", property="location"),  # ADD without a type.
            op("fcurve_modifier", property="location", mode="REMOVE"),  # No name.
            op("fcurve_modifier", property="location", mode="REMOVE", name="N", settings={"strength": 1}),
            op("fcurve_modifier", property="location", mode="CONFIGURE", name="N"),
            op("fcurve_modifier", property="location", type="NOISE", settings={"type": "CYCLES"}),
            op("fcurve_modifier", property="location", type="GENERATOR", settings={"poly_order": 40}),
            op("fcurve_modifier", property="location", type="GENERATOR", settings={"coefficients": [0.0] * 20}),
            op("fcurve_modifier", property="location", type="ENVELOPE"),
            op("fcurve_modifier", property="location", type="NOISE", settings={"node_tree.x": 1}),
            op("keyframe_copy", source=OBJ),  # Onto itself without an offset.
            op("keyframe_copy", source=SRC, property="data.lens"),
            op("action_set", mode="ASSIGN"), op("action_set", mode="ASSIGN", action="Walk"),
            op("action_set", mode="RENAME"), op("action_set", mode="SINGLE_USER", name="X"),
            op("action_set", mode="SLOT", slot="OBCube", new_slot="Other"),
            op("nla_edit", operation="TRACK_ADD"), op("nla_edit", operation="TRACK_ADD", track="A", strip="B"),
            op("nla_edit", operation="TRACK_SET", track="A"),
            op("nla_edit", operation="STRIP_ADD", track="A", frame=1),
            op("nla_edit", operation="STRIP_SET", track="A", strip="B"),
            op("nla_edit", operation="STRIP_SET", track="A", strip="B", settings={"frame_start": 9.0, "frame_end": 3.0}),
            op("nla_edit", operation="STRIP_SET", track="A", strip="B",
               settings={"action_frame_start": 9.0, "action_frame_end": 3.0}),
            op("nla_edit", operation="STRIP_SET", track="A", strip="B", settings={"blend_type": "OVERLAY"}),
            op("marker_edit", SCENE, operation="SET", marker="Hit"),
            op("marker_edit", SCENE, operation="ADD", name="Hit"),
            op("marker_edit", SCENE, operation="BIND_CAMERA", marker="Hit"),
            op("marker_edit", SCENE, operation="ADD", name="Hit", frame=1, camera=SCENE),
            op("marker_edit", SCENE, operation="REMOVE", marker="Hit", frame=3),
        ]
        for operation in refusals:
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                validate_operation(operation)

    def test_canonical_operations_validate_again(self):
        """Canonical operations carry every field at its default; validating them again must not refuse them."""
        for operation in (op("keyframe_edit", operation="SMOOTH", passes=2),
                          op("nla_edit", operation="STRIP_SET", track="A", strip="B", settings={"repeat": 2.0}),
                          op("marker_edit", SCENE, operation="BIND_CAMERA", marker="Hit", camera=None),
                          op("action_set", mode="ASSIGN", action=ACTION, stash_previous=True)):
            canonical = validate_operation(operation)
            self.assertEqual(validate_operation(canonical), canonical)


class DerivedCheckTests(unittest.TestCase):
    def derived(self, *operations):
        return derive_checks([validate_operation(operation) for operation in operations])

    def test_key_checks(self):
        checks = self.derived(op("keyframe_configure", property="location", frames=[1, 10], key_type="BREAKDOWN",
                                 extrapolation="LINEAR"),
                              op("fcurve_modifier", property="location", index=2, type="NOISE", name="Shake",
                                 settings={"strength": .5}))
        self.assertEqual(checks, [
            {"kind": "keyframes", "target": OBJ, "property": "location", "index": -1, "frames": [1, 10],
             "key_type": "BREAKDOWN", "extrapolation": "LINEAR"},
            {"kind": "fcurve_modifier", "target": OBJ, "property": "location", "index": 2, "name": "Shake",
             "values": {"strength": .5}}])
        for check in checks:
            self.assertEqual(validate_check(check), dict(check, **({"absent": []} if check["kind"] == "keyframes" else {})))
        self.assertEqual(self.derived(op("keyframe_edit", operation="INSERT", property="location", frames=[4]))[0]["frames"], [4])
        self.assertEqual(self.derived(op("keyframe_edit", operation="DELETE", property="location", frames=[4]))[0]["absent"], [4])

    def test_later_edits_drop_stale_key_checks(self):
        configure = op("keyframe_configure", property="location", frames=[1], interpolation="LINEAR")
        self.assertEqual(self.derived(configure, op("keyframe_edit", operation="OFFSET", offset=2)), [])
        self.assertEqual(self.derived(configure, op("keyframe_configure", interpolation="CONSTANT")), [])
        kept = self.derived(configure, op("keyframe_configure", property="scale", frames=[1], easing="EASE_IN"))
        self.assertEqual([check["property"] for check in kept], ["location", "scale"])
        modifier = op("fcurve_modifier", property="location", type="CYCLES", name="Loop")
        self.assertEqual(self.derived(modifier, op("keyframe_edit", operation="BAKE", frame_range=[1, 9])), [])

    def test_structure_checks_and_marker_merge(self):
        checks = self.derived(op("marker_edit", SCENE, operation="ADD", name="Start", frame=1),
                              op("marker_edit", SCENE, operation="SET", marker="Start", name="Begin", frame=2),
                              op("marker_edit", SCENE, operation="ADD", name="Hit", frame=9))
        self.assertEqual(checks, [{"kind": "markers", "target": SCENE, "present": [
            {"name": "Begin", "frame": 2}, {"name": "Hit", "frame": 9}], "absent": ["Start"]}])
        self.assertEqual(merge_markers({"present": [{"name": "A", "frame": 1}], "absent": []},
                                       {"target": SCENE, "absent": ["A"]})["present"], [])
        nla = self.derived(op("nla_edit", operation="STRIP_SET", track="Base", strip="Walk", settings={"repeat": 2.0}))
        self.assertEqual(nla[0]["strip"], {"track": "Base", "name": "Walk", "values": {"repeat": 2.0}})
        self.assertEqual(self.derived(op("action_set", mode="NEW", name="Idle"))[0]["action"], "Idle")
        self.assertEqual(self.derived(op("action_set", mode="UNLINK", stash_previous=True))[0]["active_action"], False)
        self.assertEqual(check_references(validate_check({"kind": "markers", "target": SCENE, "present": [
            {"name": "Hit", "camera": SRC}]})), (SCENE, SRC))
        for bad in ({"kind": "markers", "target": SCENE}, {"kind": "markers", "target": SCENE, "present": [
                {"name": "A"}], "absent": ["A"]}, {"kind": "keyframes", "target": OBJ, "property": "location",
                                                  "absent": [1], "key_type": "JITTER"}):
            with self.subTest(check=bad), self.assertRaises(ValueError):
                validate_check(bad)


class FilterTests(unittest.TestCase):
    def test_clean_keeps_shape_and_unselected_keys(self):
        points = [(1, 0), (2, 0), (3, 0), (4, 1), (5, 1), (6, 1)]
        # Blender keeps a flat run's ends but drops a last key equal to the one before (the hold is the same).
        self.assertEqual(filters.clean(points, .001), [0, 2, 3])
        self.assertEqual(filters.clean(points, .001, [False] * 3 + [True] * 3), [0, 1, 2, 3])
        self.assertEqual(filters.clean([(1, 2)], .1), [0])

    def test_smooth_is_blenders_weighted_mean(self):
        values, left, right = filters.smooth([0, 0, 12, 0, 0], [0, 0, 12, 0, 0], [0, 0, 12, 0, 0])
        self.assertEqual(values[0], 0)
        self.assertAlmostEqual(values[2], (2 * 12 / 12 + 2 * 12 / 12) / 2)
        self.assertEqual(filters.smooth([1, 2], [1, 2], [1, 2])[0], [1, 2])

    def test_decimate_ratio_and_error(self):
        points = [(frame, math.sin(frame * .3)) for frame in range(41)]
        kept = filters.decimate(points, ratio=.5)
        self.assertEqual(len(points) - len(kept), math.ceil(39 * .5))
        self.assertEqual((kept[0], kept[-1]), (0, 40))
        line = [(frame, 2.0 * frame) for frame in range(10)]
        self.assertEqual(filters.decimate(line, error=1e-9), [0, 9])
        self.assertEqual(filters.decimate(points[:2], ratio=1), [0, 1])

    def test_euler_filters(self):
        offsets = filters.euler_single([0.0, 6.0, 0.1])
        self.assertAlmostEqual(offsets[1], -2 * math.pi)
        self.assertEqual(filters.euler_single([0.0, 6.0]), [0.0, 0.0])
        self.assertEqual(filters.euler_multi([[0, 0, 0], [1, 1, 1]], lambda euler, previous: [9, 9, 9]),
                         [[0, 0, 0], [9, 9, 9]])

    def test_frame_selection(self):
        self.assertTrue(in_selection(10.00001, [10], None))
        self.assertFalse(in_selection(11, [10], None))
        self.assertTrue(in_selection(5, (), [1, 5]))
        self.assertTrue(in_selection(99, (), None))


class GuardTests(unittest.TestCase):
    def test_shared_and_assigned_actions_are_refused(self):
        context = SimpleNamespace(extras={"action_shared": {OBJ: {"object": 2, "data": 0}},
                                          "action_assigned": {SRC: 5}})
        with self.assertRaisesRegex(AnimationError, "ACTION_SHARED"):
            guard(context, OBJ, "object")
        guard(context, OBJ, "data")
        with self.assertRaisesRegex(AnimationError, "ACTION_ASSIGNED"):
            guard(context, SRC, "object")

    def test_nla_admission(self):
        def strip(**changes):
            return SimpleNamespace(**dict(dict(type="CLIP", use_animated_influence=False, use_animated_time=False,
                                               fcurves=[], modifiers=[]), **changes))

        def animation(*strips, tweak=False, tracks=1):
            return SimpleNamespace(use_tweak_mode=tweak, nla_tracks=[SimpleNamespace(strips=list(strips))] * tracks)

        admit(animation(strip()))
        admit(None)
        for case, code in ((animation(strip(), tweak=True), "TWEAK_MODE"), (animation(strip(type="META")), "STRIP_UNSUPPORTED"),
                           (animation(strip(type="TRANSITION")), "STRIP_UNSUPPORTED"),
                           (animation(strip(use_animated_influence=True)), "STRIP_ANIMATED"),
                           (animation(strip(modifiers=[1])), "STRIP_ANIMATED"),
                           (animation(strip(), tracks=65), "TOO_MANY_TRACKS"),
                           (animation(*[strip()] * 257), "TOO_MANY_STRIPS")):
            with self.subTest(code=code), self.assertRaisesRegex(AnimationError, code):
                admit(case)

    def test_nla_snapshot_restores_the_active_track(self):
        """Blender's nla_tracks.new() activates each new track; a restore puts the snapshot's active one back."""
        from angelo_agent.anim_nla import move_track, restore, snapshot

        class Track(SimpleNamespace):
            @property
            def active(self):
                return self.owner.active is self

            def as_pointer(self):
                return id(self)

        class Tracks(list):
            active = None

            def new(self):
                track = Track(owner=self, name="", mute=False, is_solo=False, lock=False, select=False, strips=[])
                self.append(track)
                self.active = track  # rna_NlaTrack_new: BKE_nlatrack_set_active
                return track

            def remove(self, track):
                list.remove(self, track)
                if self.active is track:
                    self.active = None

        def stack(names, active):
            animation = SimpleNamespace(nla_tracks=Tracks())
            for name in names:
                animation.nla_tracks.new().name = name
            animation.nla_tracks.active = next((track for track in animation.nla_tracks if track.name == active), None)
            return animation

        for active in ("Base", "Layer", None):
            with self.subTest(active=active):
                animation = stack(("Base", "Layer", "Top"), active)
                previous = snapshot(animation)
                restore(animation, [row for row in previous if row["name"] != "Layer"])  # A committed removal...
                restore(animation, previous)  # ...rolled back.
                self.assertEqual(snapshot(animation), previous)
                self.assertEqual(getattr(animation.nla_tracks.active, "name", None), active)
        animation = stack(("Base", "Layer", "Top"), "Layer")
        moved = move_track(animation, animation.nla_tracks[1], 0)
        self.assertEqual([track.name for track in animation.nla_tracks], ["Layer", "Base", "Top"])
        self.assertIs(animation.nla_tracks.active, moved)

    def test_fmodifier_coefficients_must_be_retained(self):
        from angelo_agent import anim_fmodifiers

        class Generator:
            type, poly_order, depth = "GENERATOR", 1, 0

            @property
            def coefficients(self):
                return [0.0, 0.0]  # A value Blender did not keep.

            @coefficients.setter
            def coefficients(self, value):
                pass

        with self.assertRaisesRegex(AnimationError, "VALUE_NOT_RETAINED"):
            anim_fmodifiers._write(SimpleNamespace(), None, Generator(), {"coefficients": [1.0, 2.0]})


def curve(path, index, modifiers=()):
    return SimpleNamespace(data_path=path, array_index=index, modifiers=list(modifiers))


def owner(curves):
    bag = SimpleNamespace(fcurves=curves)
    strip = SimpleNamespace(channelbag=lambda slot: bag)
    action = SimpleNamespace(layers=[SimpleNamespace(strips=[strip])])
    return SimpleNamespace(animation_data=SimpleNamespace(action=action, action_slot=object()), data=None)


class PredicateTests(unittest.TestCase):
    def test_fcurve_modifier_presence(self):
        noise = SimpleNamespace(name="Shake")
        obj = owner([curve("location", 0, [noise]), curve("location", 1)])
        check = {"property": "location", "index": 0, "name": "Shake"}
        self.assertTrue(fcurve_modifier(READER, obj, check)[0])
        self.assertFalse(fcurve_modifier(READER, obj, dict(check, index=-1))[0])
        self.assertFalse(fcurve_modifier(READER, obj, dict(check, exists=False))[0])
        self.assertTrue(fcurve_modifier(READER, obj, dict(check, index=1, exists=False))[0])
        self.assertFalse(fcurve_modifier(READER, obj, dict(check, property="scale"))[0])

    def test_nla_structure(self):
        strip = SimpleNamespace(name="Walk", frame_start=30.0, frame_end=48.0, repeat=2.0, blend_type="ADD", mute=False)
        tracks = [SimpleNamespace(name="Base", strips=[strip]), SimpleNamespace(name="Layer", strips=[])]
        obj = SimpleNamespace(animation_data=SimpleNamespace(nla_tracks=tracks, action=SimpleNamespace(name="Idle.001")))
        reader = SimpleNamespace(spend=lambda amount: None, context={"action_names": {"Idle.001": "Idle"}})
        good = {"action": "Idle", "track_names": ["Base"], "absent_tracks": ["Gone"],
                "strip": {"track": "Base", "name": "Walk", "values": {"repeat": 2.0, "blend_type": "ADD"}}}
        self.assertTrue(nla_structure(reader, obj, good)[0])
        for bad in ({"action": "Idle.001"}, {"track_names": ["Gone"]}, {"absent_tracks": ["Layer"]},
                    {"absent_strips": ["Walk"]}, {"strip": {"track": "Layer", "name": "Walk"}},
                    {"strip": {"track": "Base", "name": "Walk", "values": {"repeat": 3.0}}}):
            with self.subTest(check=bad):
                self.assertFalse(nla_structure(reader, obj, bad)[0])

    def test_markers(self):
        camera = "camera"
        scene = SimpleNamespace(bl_rna=SimpleNamespace(identifier="Scene"), timeline_markers=[
            SimpleNamespace(name="Hit", frame=24, camera=camera), SimpleNamespace(name="Twin", frame=1, camera=None),
            SimpleNamespace(name="Twin", frame=2, camera=None)])
        self.assertTrue(markers(READER, scene, {"present": [{"name": "Hit", "frame": 24, "camera": SRC}],
                                                "absent": ["Gone"]})[0])
        for bad in ({"present": [{"name": "Hit", "frame": 25}]}, {"present": [{"name": "Hit", "camera": None}]},
                    {"present": [{"name": "Twin"}]}, {"absent": ["Hit"]}):
            with self.subTest(check=bad):
                self.assertFalse(markers(READER, scene, dict({"present": [], "absent": []}, **bad))[0])
        cube = SimpleNamespace(bl_rna=SimpleNamespace(identifier="Object"), timeline_markers=[])
        self.assertFalse(markers(READER, cube, {"present": [{"name": "Hit"}], "absent": []})[0])


if __name__ == "__main__":
    unittest.main()
