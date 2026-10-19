# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host tests of the request survey's result checks that need no Blender: topology, motion and dispatch.

The checks themselves read Blender data and are proven in the application by
``test_blender_survey_tasks.py`` (every check fails before its reference edit
and on a near miss, and passes after the edit). Here the pure parts run on
stand-in meshes, on objects whose place is a function of the frame, and on a
stand-in render setup.
"""

import math
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import survey_checks  # noqa: E402
import survey_checks_motion as motion  # noqa: E402
import survey_fixtures  # noqa: E402
import survey_truth  # noqa: E402


def mesh(vertex_count, faces):
    """A stand-in mesh: ``faces`` are vertex index tuples; edges are every face side, once."""
    keys = sorted({tuple(sorted((face[i], face[(i + 1) % len(face)]))) for face in faces for i in range(len(face))})
    polygons = [SimpleNamespace(vertices=face, edge_keys=[tuple(sorted((face[i], face[(i + 1) % len(face)])))
                                                          for i in range(len(face))]) for face in faces]
    return SimpleNamespace(vertices=[None] * vertex_count, edges=[SimpleNamespace(vertices=key) for key in keys],
                           polygons=polygons)


CUBE = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]


def torus(major=6, minor=4):
    return [(i * minor + j, ((i + 1) % major) * minor + j, ((i + 1) % major) * minor + (j + 1) % minor,
             i * minor + (j + 1) % minor) for i in range(major) for j in range(minor)]


def summary(pieces):
    return sorted(tuple(piece[key] for key in ("vertices", "edges", "faces", "closed", "euler")) for piece in pieces)


class TopologyTests(unittest.TestCase):
    def test_a_cube_is_one_closed_piece_without_a_handle(self):
        self.assertEqual(summary(survey_checks.topology(mesh(8, CUBE))), [(8, 12, 6, True, 2)])

    def test_a_torus_is_closed_with_one_handle(self):
        pieces = survey_checks.topology(mesh(24, torus()))
        self.assertEqual(summary(pieces), [(24, 48, 24, True, 0)])
        self.assertEqual(2 - 2 * 1, pieces[0]["euler"], "genus 1")

    def test_an_open_surface_is_not_closed(self):
        self.assertEqual(summary(survey_checks.topology(mesh(8, CUBE[1:]))), [(8, 12, 5, False, 1)])
        self.assertEqual(summary(survey_checks.topology(mesh(4, [(0, 1, 2, 3)]))), [(4, 4, 1, False, 1)])

    def test_pieces_are_counted_apart_with_their_own_vertices(self):
        shifted = lambda faces, offset: [tuple(index + offset for index in face) for face in faces]
        pieces = survey_checks.topology(mesh(40, CUBE + shifted(CUBE, 8) + shifted(torus(), 16)))
        self.assertEqual(summary(pieces), [(8, 12, 6, True, 2), (8, 12, 6, True, 2), (24, 48, 24, True, 0)])
        self.assertEqual(sorted(len(piece["indices"]) for piece in pieces), [8, 8, 24])
        self.assertEqual(sorted(index for piece in pieces for index in piece["indices"]), list(range(40)))


class StrokeTests(unittest.TestCase):
    def test_two_bounces_are_two_rises_and_two_falls(self):
        up_down = [0, .5, 1, .5, 0, .5, 1, .5, 0]
        self.assertEqual(motion.strokes(up_down, .25), (2, 2))
        self.assertEqual(motion.strokes([1 - value for value in up_down], .25), (2, 2), "starting in the air")
        self.assertEqual(motion.strokes([0, .1, .2, .3, .5, 1, .6, .2, 0], .25), (1, 1), "a slow rise still counts")

    def test_small_wiggles_and_still_objects_are_not_bounces(self):
        self.assertEqual(motion.strokes([0, 0, 0, 0], .25), (0, 0))
        self.assertEqual(motion.strokes([0, .5, 1, .95, 1, .5, 0], .25), (1, 1), "an overshoot is not a bounce")
        self.assertEqual(motion.strokes([0, 1, 2, 3], .25), (1, 0), "moving away is one rise")
        self.assertEqual(motion.strokes([1, .5, .55, 0], .25), (0, 1), "a small step back up is not a rise")
        self.assertEqual(motion.strokes([0, .5, .45, 1], .25), (1, 0), "nor a small step back down a fall")


class Frames:
    """A stand-in scene and depsgraph: an object's place is a function of the current frame."""

    def __init__(self, first=1, last=300):
        self.frame_start, self.frame_end, self.frame_current = first, last, first
        self.context = SimpleNamespace(evaluated_depsgraph_get=lambda: None)

    def frame_set(self, frame):
        self.frame_current = frame

    def progress(self):
        return (self.frame_current - self.frame_start) / (self.frame_end - self.frame_start)

    def moving(self, place):
        """An object whose evaluated world position is ``place(progress)``, progress running 0 to 1."""
        at = lambda: SimpleNamespace(**dict(zip("xyz", place(self.progress()))))
        world = lambda: SimpleNamespace(translation=SimpleNamespace(copy=at))
        return SimpleNamespace(evaluated_get=lambda depsgraph: SimpleNamespace(matrix_world=world()),
                               matrix_world=world())


def circling(turns_at, radius=lambda progress: 5.0):
    """A place on a circle about (1, 2): ``turns_at(progress)`` full turns made so far."""
    return lambda progress: (1 + radius(progress) * math.cos(2 * math.pi * turns_at(progress)),
                             2 + radius(progress) * math.sin(2 * math.pi * turns_at(progress)), 3.0)


class OrbitTests(unittest.TestCase):
    def orbit(self, turns_at, turns=1, **more):
        frames = Frames()
        objects = {"$cam": frames.moving(circling(turns_at, **more)), "$eval": frames.moving(lambda progress: (1, 2, 0))}
        passed, details = motion.check_orbit(frames, frames, objects.get, {
            "kind": "orbit", "target": "$cam", "around": "$eval", "turns": turns, "tolerance_deg": 25})
        self.assertEqual(frames.frame_current, 1, "the frame is restored")
        return passed, details

    def test_one_turn_over_the_whole_range_passes_either_way_and_eased(self):
        for name, turns_at in (("steady", lambda p: p), ("the other way", lambda p: -p),
                               ("eased", lambda p: 3 * p * p - 2 * p ** 3),
                               ("sharply eased", lambda p: 16 * p ** 5 if p < .5 else 1 - 16 * (1 - p) ** 5),
                               ("ending a frame early for a seamless loop", lambda p: p * 299 / 298)):
            with self.subTest(name):
                self.assertTrue(self.orbit(turns_at)[0], self.orbit(turns_at)[1])
        self.assertTrue(self.orbit(lambda p: 2 * p, turns=2)[0])
        self.assertTrue(self.orbit(lambda p: p, radius=lambda p: 5 + 2 * p)[0], "the distance may change a little")

    def test_a_turn_that_does_not_span_the_range_fails(self):
        cases = {"done by the middle, then held": lambda p: min(1.0, 2 * p),
                 "held, then made in the second half": lambda p: max(0.0, 2 * p - 1),
                 "done five sixths of the way through": lambda p: min(1.0, p * 6 / 5),
                 "started a sixth of the way in": lambda p: max(0.0, (p - 1 / 6) * 6 / 5),
                 "all but done by the middle, then a crawl": lambda p: 0.98 * min(1.0, 2 * p) + 0.02 * p}
        for name, turns_at in cases.items():
            with self.subTest(name):
                passed, details = self.orbit(turns_at)
                self.assertFalse(passed, details)
                self.assertAlmostEqual(details["degrees"], 360, delta=1, msg="the whole circle was still made")

    def test_other_motions_are_not_one_orbit(self):
        cases = {"half a turn": lambda p: p / 2, "two turns": lambda p: 2 * p, "no motion": lambda p: 0.0,
                 "there and back": lambda p: 1 - abs(2 * p - 1),
                 "a swing that ends a turn on": lambda p: p + 0.2 * math.sin(16 * math.pi * p)}
        for name, turns_at in cases.items():
            with self.subTest(name):
                self.assertFalse(self.orbit(turns_at)[0])
        swing = self.orbit(cases["a swing that ends a turn on"])[1]   # Only its way back and forth gives it away.
        self.assertTrue(abs(swing["degrees"] - 360) <= 25 and abs(swing["half_way"] - 180) <= 50
                        and min(swing["first_eighth"], swing["last_eighth"]) > 1, swing)
        self.assertFalse(self.orbit(lambda p: p, radius=lambda p: 5 - 4.5 * p)[0], "a spiral into the object")
        self.assertFalse(self.orbit(lambda p: p, radius=lambda p: 0.0)[0], "turning on the spot")
        frames = Frames()
        self.assertEqual(motion.check_orbit(frames, frames, {}.get, {"target": "$cam", "around": "$eval", "turns": 1}),
                         (False, {"reason": "object missing"}))


class BounceTests(unittest.TestCase):
    def bounces(self, height, count=2):
        frames = Frames(1, 250)
        ball = frames.moving(lambda progress: (0, 0, height(frames.frame_current)))
        return motion.check_bounces(frames, frames, {"$sphere": ball}.get, {
            "target": "$sphere", "frames": [1, 48], "count": count, "min_height": 0.05})

    def test_the_asked_number_of_bounces_passes_and_one_more_or_fewer_fails(self):
        hop = lambda hops: lambda frame: abs(math.sin(math.pi * hops * (frame - 1) / 47))
        self.assertEqual([self.bounces(hop(hops))[0] for hops in (1, 2, 3, 4)], [False, True, False, False])
        self.assertTrue(self.bounces(hop(3), count=3)[0])
        self.assertTrue(self.bounces(lambda frame: 1 - hop(2)(frame))[0], "starting from the top of the bounce")
        self.assertEqual(self.bounces(lambda frame: 0.001 * hop(2)(frame))[1]["reason"],
                         "the object does not move up and down")
        self.assertFalse(self.bounces(lambda frame: hop(2)(min(frame, 24)))[0], "one bounce, then still")


class RenderTruthTests(unittest.TestCase):
    def truth(self, percentage, width=100, height=300):
        render = SimpleNamespace(engine="BLENDER_EEVEE", resolution_x=width, resolution_y=height,
                                 resolution_percentage=percentage)
        return survey_truth.truth(None, SimpleNamespace(render=render), {"kind": "render"})["accept"]

    def test_the_size_after_the_percentage_is_in_whole_pixels_as_the_renderer_counts(self):
        self.assertEqual(self.truth(100), [["EEVEE", 100, 300]])
        # 100 x 0.29 is 28.999... as a float; the renderer divides whole numbers and writes 29 pixels.
        self.assertEqual(self.truth(29), [["EEVEE", 100, 300], ["EEVEE", 29, 87]])
        self.assertEqual(self.truth(50, 1921, 1081), [["EEVEE", 1921, 1081], ["EEVEE", 960, 540]])


class GateTruthTests(unittest.TestCase):
    """The two truths the gate sets' questions are read against, on stand-in objects."""

    def setUp(self):
        self.bpy = SimpleNamespace(context=SimpleNamespace(view_layer=SimpleNamespace(update=lambda: None)))

    def test_mesh_names_are_the_scene_s_meshes_sorted(self):
        objects = [SimpleNamespace(name=name, type=kind) for name, kind in (
            ("Cube", "MESH"), ("Light", "LIGHT"), ("Brass Marker", "MESH"), ("Camera", "CAMERA"))]
        scene = SimpleNamespace(objects=objects)
        self.assertEqual(survey_truth.truth(None, scene, {"kind": "object_names", "type": "MESH"}),
                         {"accept": [["Brass Marker", "Cube"]]})
        self.assertEqual(survey_truth.truth(None, scene, {"kind": "object_names"}),
                         {"accept": [["Brass Marker", "Camera", "Cube", "Light"]]})
        self.assertEqual(survey_truth.truth(None, SimpleNamespace(objects=objects[1:2]),
                                            {"kind": "object_names", "type": "MESH"}), {"accept": [[]]})

    def test_dimensions_and_location_are_the_object_s_own_in_metres(self):
        cube = SimpleNamespace(name="Eval Cube", dimensions=(1.5000001, 1.5, 2.0), location=(-1.0, 0.0, 0.9999999),
                               matrix_world=SimpleNamespace(translation=(-1.0, 0.0, 0.9999999)))
        objects = SimpleNamespace(get={"Eval Cube": cube}.get)
        spec = {"kind": "dimensions_location", "object": "Eval Cube"}
        self.assertEqual(survey_truth.truth(self.bpy, SimpleNamespace(objects=objects), spec),
                         {"accept": [[1.5, 1.5, 2.0, -1.0, 0.0, 1.0]], "detail": {"object": "Eval Cube"}})
        cube.matrix_world.translation = (4.0, 0.0, 1.0)   # Parented: where it is in the world is as true.
        self.assertEqual(survey_truth.truth(self.bpy, SimpleNamespace(objects=objects), spec)["accept"],
                         [[1.5, 1.5, 2.0, -1.0, 0.0, 1.0], [1.5, 1.5, 2.0, 4.0, 0.0, 1.0]])
        self.assertEqual(survey_truth.truth(self.bpy, SimpleNamespace(objects=SimpleNamespace(get={}.get)), spec),
                         {"ungraded": "the object the question names is missing"})

    def test_the_probe_is_a_mesh_when_the_question_is_about_meshes_and_leaves_nothing_behind(self):
        made, removed = {}, []
        mesh = mock.Mock(users=0)
        mesh.name = "Brass Marker"
        probe = mock.Mock()
        probe.name = "Brass Marker"
        class Objects(list):   # bpy.data.objects: iterable, and looked up by name.
            new = staticmethod(lambda name, block: (made.setdefault("object", (name, block)), setattr(
                probe, "data", block), setattr(probe, "session_uid", 41))[0] and probe)
            get = staticmethod(lambda name: next((obj for obj in everything if obj.name == name), None))
            remove = staticmethod(lambda block: removed.append("object"))
        everything = Objects([probe])
        data = SimpleNamespace(
            meshes=SimpleNamespace(new=lambda name: made.setdefault("mesh", mesh),
                                   remove=lambda block: removed.append("mesh")),
            objects=everything)
        bpy = SimpleNamespace(data=data, context=self.bpy.context)
        scene = SimpleNamespace(objects=SimpleNamespace(get=lambda name: None),
                                collection=SimpleNamespace(objects=SimpleNamespace(link=lambda obj: None)))
        steps, restore = survey_fixtures.apply(bpy, scene, {"kind": "probe_object", "name": "Brass Marker",
                                                            "mesh": True})
        self.assertEqual((steps, made["object"]), (["fixture:probe_object"], ("Brass Marker", mesh)))
        self.assertEqual(len(mesh.from_pydata.call_args.args[2]), 1, "one face")
        probe.name = "Renamed by the turn"   # Found again by its identity, so it is not left in the scene.
        restore()
        self.assertEqual(removed, ["object", "mesh"])
        made.clear()
        survey_fixtures.apply(bpy, scene, {"kind": "probe_object", "name": "Brass Marker"})
        self.assertEqual(made["object"], ("Brass Marker", None), "an Empty, as the survey's probe is")
        with self.assertRaises(survey_fixtures.FixtureError):
            taken = SimpleNamespace(objects=SimpleNamespace(get=lambda name: probe))
            survey_fixtures.apply(bpy, taken, {"kind": "probe_object", "name": "Brass Marker", "mesh": True})

    def test_the_cube_is_moved_for_the_turn_and_put_back(self):
        cube = SimpleNamespace(location=(-2.0, -1.0, 0.0))
        scene = SimpleNamespace(objects={"Eval Cube": cube})
        fixture = {"kind": "object_offset", "object": "Eval Cube", "location": [0.25, -0.5, 0.75]}
        steps, restore = survey_fixtures.apply(self.bpy, scene, fixture)
        self.assertEqual((steps, list(cube.location)), (["fixture:object_offset"], [-1.75, -1.5, 0.75]))
        restore()
        self.assertEqual(cube.location, (-2.0, -1.0, 0.0))
        self.assertIn("object_offset", survey_fixtures.TRANSIENT)
        with self.assertRaises(survey_fixtures.FixtureError):
            survey_fixtures.apply(self.bpy, SimpleNamespace(objects={}), fixture)


class DispatchTests(unittest.TestCase):
    def test_a_failing_or_broken_check_fails_and_never_raises(self):
        def broken(bpy, scene, resolve, check):
            raise RuntimeError("the scene is not what the check expects")
        kinds = dict(survey_checks.KINDS, always=lambda *args: (True, {"seen": 1}), never=lambda *args: (False, {}),
                     broken=broken)
        with mock.patch.object(survey_checks, "KINDS", kinds):
            result = survey_checks.evaluate(None, None, [{"kind": "always"}, {"kind": "never", "label": "the look"},
                                                         {"kind": "broken"}], lambda alias: None)
            passing = survey_checks.evaluate(None, None, [{"kind": "always"}], lambda alias: None)
        self.assertEqual((result["success"], result["passed"], result["total"]), (False, 1, 3))
        self.assertEqual([entry["label"] for entry in result["results"]], ["always", "the look", "broken"])
        self.assertIn("RuntimeError: the scene is not", result["results"][2]["details"]["error"])
        self.assertTrue(passing["success"])
        self.assertFalse(survey_checks.evaluate(None, None, [], lambda alias: None)["success"], "no check, no pass")

    def test_an_undecided_check_is_marked_inconclusive_and_never_passes(self):
        kinds = dict(survey_checks.KINDS, sky=lambda *args: (False, {"reason": "inconclusive: an image texture"}),
                     closed=lambda *args: (False, {"status": "inconclusive", "reason": "unsupported"}),
                     never=lambda *args: (False, {"reason": "too dark"}))
        with mock.patch.object(survey_checks, "KINDS", kinds):
            result = survey_checks.evaluate(None, None, [{"kind": "sky"}, {"kind": "closed"}, {"kind": "never"}],
                                            lambda alias: None)
        self.assertFalse(result["success"])
        self.assertEqual([entry.get("inconclusive", False) for entry in result["results"]], [True, True, False])

    def test_targets_resolve_to_lists_of_the_wanted_type(self):
        light, mesh_object = SimpleNamespace(type="LIGHT"), SimpleNamespace(type="MESH")
        resolve = {"$one": mesh_object, "$many": [light, mesh_object], "$none": None}.get
        self.assertEqual(survey_checks._objects(resolve, "$one"), [mesh_object])
        self.assertEqual(survey_checks._objects(resolve, "$many", "MESH"), [mesh_object])
        self.assertEqual(survey_checks._objects(resolve, "$none"), [])
        self.assertEqual(survey_checks._objects(resolve, "$unknown"), [])

    def test_hue_reads_yellow_and_not_white_or_orange(self):
        self.assertTrue(survey_checks._is_hue((1, 0.9, 0, 1), [40, 75]))
        for other in ((1, 1, 1, 1), (1, 0.5, 0, 1), (0, 0, 0, 1), (0.2, 0.2, 1, 1)):
            self.assertFalse(survey_checks._is_hue(other, [40, 75]))


if __name__ == "__main__":
    unittest.main()
