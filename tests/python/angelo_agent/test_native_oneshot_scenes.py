# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The tree and cottage one-shot tests: their briefs, stand-ins and the check kinds they add (no Blender).

The stand-ins are built and graded in stock Blender by each test's reference
job (the brief's checks must fail on the factory scene and pass on the
stand-in); these tests pin what can be pinned on the host: the amended
briefs, the numbers the stand-ins are made from against the numbers the
checks ask for, and the counting kinds on stand-in meshes.
"""

import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import oneshot_checks  # noqa: E402
import oneshot_form_checks as form  # noqa: E402
import oneshot_kit  # noqa: E402
import scenario_build_kit as build_kit  # noqa: E402
import scenario_checks  # noqa: E402
import scenario_oneshot_cottage as cottage  # noqa: E402
import scenario_oneshot_tree as tree  # noqa: E402
import scenario_specs as specs  # noqa: E402
from test_native_scenario_checks import Offset  # noqa: E402

try:
    import numpy
except ImportError:   # The counting kinds read meshes through NumPy, as Blender's own Python does; CI installs it.
    numpy = None

IDENTITY = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]
PICTURE_SHA = "7aa30417d806ee899c03ffc575a4e2f3220986888372fd365f613ba18604dc20"


def mesh(vertices, faces):
    """A stand-in evaluated mesh: vertices with ``co`` and the edges of its faces."""
    edges = {tuple(sorted((face[index], face[(index + 1) % len(face)]))) for face in faces for index in range(len(face))}
    return NS(vertices=[NS(co=tuple(vertex)) for vertex in vertices], edges=[NS(vertices=edge) for edge in sorted(edges)])


def instance(name, data, is_instance=False, parent=None, matrix=IDENTITY):
    return NS(object=NS(type="MESH", data=data, original=NS(name=name)), is_instance=is_instance,
              parent=NS(original=NS(name=parent)) if parent else None, matrix_world=matrix)


def boxes(*centres, size=0.2):
    return oneshot_kit.merged([build_kit.box_geometry((size, size, size), centre) for centre in centres])


class BriefTests(unittest.TestCase):
    def test_both_are_registered_with_their_limits_and_one_picture(self):
        self.assertEqual(specs.ONESHOT_TASK_SETS[:4], ("oneshot-bridge", "oneshot-footbridge", "oneshot-tree",
                                                       "oneshot-cottage"))
        for module, limits, still in ((tree, (2400.0, 900.0, 25.0), "tree-final.png"),
                                      (cottage, (3000.0, 900.0, 30.0), "cottage-final.png")):
            with self.subTest(test=module.ID):
                self.assertEqual((module.LIMITS["wall_s"], module.LIMITS["job_wait_s"], module.LIMITS["cost_usd"]), limits)
                self.assertEqual(module.FINAL_STILL, {"name": still, "size": (2560, 1440)})
                self.assertEqual(module.GROUND_TRUTH, "stand-in")       # The picture is the look, not the stand-in.
                self.assertEqual(module.TURNS[0]["references"], ["picture"])
                picture = module.SOURCE_IMAGES["picture"]
                self.assertEqual((picture["file"], picture["sha256"]), ("maintainer-reference-2026-10-11.png", PICTURE_SHA))
                self.assertEqual(specs.ONESHOT_SOURCES[module.ID], ("oneshot_kit.py",))
                self.assertEqual(specs.wall_cap(specs.ONESHOT_TASK_SETS[specs.ONESHOT_NAMES.index(module.ID)]),
                                 limits[0])
        self.assertEqual((tree.SOURCE_IMAGES["picture"]["box"], tree.SOURCE_IMAGES["picture"]["scale"]),
                         ((520, 235, 660, 365), 4))
        self.assertEqual((cottage.SOURCE_IMAGES["picture"]["box"], cottage.SOURCE_IMAGES["picture"]["scale"]),
                         ((600, 205, 900, 395), 3))

    def test_the_tree_brief_says_how_many_leaves_once_and_never_how(self):
        prompt = tree.PROMPT.lower()
        self.assertEqual(prompt.count("a few thousand leaves or leaf clusters"), 1)
        self.assertEqual(prompt.count("thousand"), 1)
        for word in ("instance", "particle", "scatter", "array", "duplicate", "card", "node"):
            with self.subTest(word=word):
                self.assertNotIn(word, prompt)                       # The route is what is measured.
        for stated in ("(10.5, -10.5, 2.6)", "(0, 0, 3.3)", "35 mm", "35 to 45 degrees", "Between 5.5 and 7 m tall",
                       "crown 4 to 6 m across", "0.35 to 0.5 m thick", "first 1.8 m", "at least five main limbs",
                       "never one solid ball", "camera's left"):
            self.assertIn(stated, tree.PROMPT)

    def test_the_cottage_brief_asks_for_terracotta_against_the_picture_and_keeps_door_and_lantern(self):
        prompt = cottage.PROMPT
        self.assertIn("terracotta tiles laid in courses: the picture's roof is slate; I want terracotta.", prompt)
        self.assertNotIn("grey-brown", prompt)
        for stated in ("'Cottage Door': a timber door 0.9 m wide and 2 m tall", "'Lantern': a wall lantern",
                       "'Lantern Light'", "(-9, -13, 2.2)", "(0, 0, 2.6)", "8 m along X by 5.5 m along Y",
                       "2.6 m high to the eaves", "about 5.4 m above the ground", "rising to about 6.5 m",
                       "at least four", "centred 2.4 m either side of the door", "sills 1 m above", "8 to 15 degrees",
                       "from the front left (-X, -Y)"):
            self.assertIn(stated, prompt)
        roof = next(c for c in cottage.CHECKS if c["kind"] == "surface" and c["target"] == cottage.ROOF)
        self.assertEqual(roof["color"], ["warm"])                    # Slate, as painted, fails; terracotta passes.
        self.assertEqual(scenario_checks.colour_family(oneshot_kit.TERRACOTTA), "warm")
        for slate in ((0.22, 0.23, 0.25), (0.2, 0.2, 0.2), (0.16, 0.18, 0.22)):
            self.assertNotIn(scenario_checks.colour_family(slate), roof["color"])

    def test_checks_and_stand_ins_share_their_numbers(self):
        for module in (tree, cottage):
            camera = next(c for c in module.CHECKS if c["kind"] == "camera")
            self.assertEqual((camera["location"], camera["look_at"]), (list(module.HERO["location"]),
                                                                       list(module.HERO["target"])))
            low, high = next(c for c in module.CHECKS if c["kind"] == "light" and c["type"] == "SUN")["elevation_deg"]
            self.assertTrue(low <= module.SUN_ELEVATION <= high)
            side = next(c for c in module.CHECKS if c["kind"] == "light_from")
            self.assertLessEqual(form.bearing_difference(module.SUN_AZIMUTH, side["azimuth_deg"]), side["within_deg"])
            scene = next(c for c in module.CHECKS if c["kind"] == "scene")["values"]
            self.assertEqual((scene["render.resolution_x"], scene["render.resolution_y"]), module.FINAL_STILL["size"])
            self.assertEqual(module.CHECKS[0]["kind"], "hygiene")
        # Twelve measure the trunk, six keep its first 1.8 m clear of branches and leaves, three read the ground.
        self.assertEqual(sum(c["kind"] == "probe" for c in tree.CHECKS), 21)
        ring = next(c for c in tree.CHECKS if c["kind"] == "ring")
        self.assertEqual((ring["targets"], ring["from"]), ([tree.TRUNK, tree.BRANCH], 1.7))   # Below the lowest fork.
        self.assertEqual(ring["radii"], [0.45])     # Outside the trunk the probes allow (0.36 m), inside the side branches.
        crown = next(c for c in tree.CHECKS if c["kind"] == "bbox" and c["target"] == tree.FOLIAGE)
        self.assertGreaterEqual(crown["size"][2][1], 7.2)     # An object that only places leaves counts from its origin.
        self.assertTrue({"pieces", "ring"} <= {c["kind"] for c in tree.CHECKS})
        self.assertTrue({"groups", "visible"} <= {c["kind"] for c in cottage.CHECKS})
        self.assertEqual(cottage.LANTERN, oneshot_kit.cottage_plan(cottage.SIZE)["lantern"])
        self.assertEqual(tree.rise_height(0.0, 0.0), 0.0)
        self.assertTrue(-3.0 <= tree.rise_height(5.0, 0.0) <= -0.05)
        self.assertTrue(-0.35 <= tree.rise_height(1.2, 1.2) <= 0.25)

    def test_door_size_measures_the_named_leaf_not_its_surroundings(self):
        size = next(check for check in cottage.CHECKS if check["label"].startswith("a door 0.9"))
        for name in ("Cottage Door", "Cottage Door.001", "Cottage Door.005"):
            self.assertTrue(scenario_checks.matches(name, size["target"]), name)
        for name in ("Cottage Doorstep", "Cottage Door Frame", "Cottage Door stone jamb"):
            self.assertFalse(scenario_checks.matches(name, size["target"]), name)
        # The brief's exact names: a differently named overlay is another part, not the door that must be seen.
        visible = next(check for check in cottage.CHECKS if check["label"] == "the door can be seen from the front")
        self.assertFalse(scenario_checks.matches("Cottage Door Oak Plank 01", visible["target"]))
        self.assertTrue(scenario_checks.matches("Cottage Door.001", visible["target"]))
        # One ray, as every other probe: a seam the build leaves at that point is part of the build (no sampling).
        self.assertNotIn("origins", visible)
        self.assertEqual(visible["origin"], [0.0, -cottage.HALF_Y - 0.6, 1.1])


class KitTests(unittest.TestCase):
    def test_a_tube_is_rings_along_a_line_capped_at_both_ends(self):
        vertices, faces = oneshot_kit.tube_geometry([(0, 0, 0), (0, 0, 1), (0.2, 0, 2)], [0.2, 0.15, 0.1], segments=8)
        self.assertEqual((len(vertices), len(faces)), (24, 2 * 8 + 2))
        self.assertTrue(all(abs(math.hypot(x, y) - 0.2) < 1e-9 and z == 0 for x, y, z in vertices[:8]))
        self.assertEqual(sorted(len(face) for face in faces)[-2:], [8, 8])
        shifted = oneshot_kit.merged([(vertices, faces), (vertices, faces)])
        self.assertEqual((len(shifted[0]), max(max(face) for face in shifted[1])), (48, 47))

    def test_the_tree_is_what_the_brief_describes(self):
        plan = oneshot_kit.tree_plan(tree.HEIGHT, tree.SEED)
        self.assertEqual(plan, oneshot_kit.tree_plan(tree.HEIGHT, tree.SEED))            # Seeded: the same every time.
        points, radii = plan["trunk"]
        self.assertTrue(0.35 <= 2 * radii[1] <= 0.5)                                       # Thick at the base,
        self.assertEqual(radii, sorted(radii, reverse=True))                               # tapering,
        self.assertGreaterEqual(plan["fork"], 1.8)                                         # clear for 1.8 m.
        self.assertEqual(len(plan["limbs"]), 7)
        self.assertTrue(all(len(tubes) == 4 for tubes in plan["limbs"]))                   # Each limb divides.
        vertices, faces = oneshot_kit.leaf_cards(plan["tips"], tree.LEAVES, plan["scale"], tree.SEED)
        self.assertEqual((len(faces), len(vertices)), (tree.LEAVES, 4 * tree.LEAVES))
        heights = [vertex[2] for vertex in vertices]
        self.assertGreater(min(heights), 1.8)
        self.assertTrue(5.5 <= max(heights) <= 7.0)
        across = max(vertex[0] for vertex in vertices) - min(vertex[0] for vertex in vertices)
        self.assertTrue(3.6 <= across <= 6.0)
        small = oneshot_kit.tree_plan(4.5, 3)
        self.assertAlmostEqual(small["fork"] / plan["fork"], 4.5 / tree.HEIGHT)            # Other sizes keep the shape.

    def test_the_cottage_is_what_the_brief_describes(self):
        plan = oneshot_kit.cottage_plan(cottage.SIZE, cottage.EAVES, cottage.RIDGE)
        self.assertEqual(plan["half"], (4.0, 2.75))
        self.assertEqual(plan["door"][3:], (0.9, 2.0))
        front = [window for window in plan["windows"] if window[3] == "-y"]
        self.assertEqual(sorted(window[0] for window in front), [-cottage.WINDOW_X, cottage.WINDOW_X])
        self.assertTrue(all(abs(window[2] - window[5] / 2 - cottage.SILL) < 1e-9 for window in front))
        self.assertGreaterEqual(len(plan["windows"]), 4)
        self.assertEqual(plan["chimney"][3], cottage.CHIMNEY_TOP)
        self.assertLess(plan["chimney"][0], -2.0)                                          # At the -X end.
        walls = oneshot_kit._walls(plan)[0]
        self.assertEqual((min(v[0] for v in walls), max(v[0] for v in walls)), (-4.0, 4.0))
        self.assertAlmostEqual(max(v[2] for v in walls), cottage.RIDGE - 0.12)
        roof = oneshot_kit._roof(plan)[0]
        self.assertAlmostEqual(min(v[1] for v in roof), -2.75 - oneshot_kit.OVERHANG)
        self.assertTrue(cottage.RIDGE <= max(v[2] for v in roof) <= cottage.RIDGE + 0.1)
        self.assertGreater(plan["slope"], 1.0)                                             # Steeper than 45 degrees.

    @unittest.skipIf(numpy is None, "NumPy is not installed")
    def test_the_stand_ins_count_as_the_checks_count(self):
        plan = oneshot_kit.tree_plan(tree.HEIGHT, tree.SEED)
        leaves = mesh(*oneshot_kit.leaf_cards(plan["tips"], tree.LEAVES, plan["scale"], tree.SEED))
        self.assertEqual(form.island_count(leaves), tree.LEAVES)
        low, high = next(c for c in tree.CHECKS if c["kind"] == "pieces")["count"]
        self.assertTrue(low <= tree.LEAVES <= high)
        parts = []
        for x, y, z, outward, width, height in oneshot_kit.cottage_plan()["windows"]:
            frame, pane = oneshot_kit._window((x, y, z), outward, width, height)
            parts += [instance("Cottage Window", mesh(*frame)), instance("Cottage Window Glass", mesh(*pane))]
        rule = next(c for c in cottage.CHECKS if c["kind"] == "groups")
        passed, details = form._check_groups(None, None, NS(object_instances=parts), rule)
        self.assertEqual((passed, details["groups"], details["sized"], details["pieces"]), (True, 5, 5, 35))


@unittest.skipIf(numpy is None, "NumPy is not installed")
class PiecesTests(unittest.TestCase):
    def test_pieces_are_what_edges_join(self):
        self.assertEqual(form.island_count(mesh(*boxes((0, 0, 0), (1, 0, 0), (2, 0, 0)))), 3)
        chain = mesh([(index, 0, 0) for index in range(200)], [(index, index + 1) for index in range(199)])
        self.assertEqual(form.island_count(chain), 1)                           # A long piece settles too.
        loose = mesh([(0, 0, 0), (1, 0, 0), (2, 0, 0), (9, 9, 9)], [(0, 1, 2)])
        self.assertEqual(form.island_count(loose), 1)                           # A vertex no edge uses is no piece.
        self.assertEqual(form.island_count(mesh([], [])), 0)

    def graph(self, *items):
        return NS(object_instances=list(items))

    def test_leaves_count_the_same_however_they_are_made(self):
        rule = {"target": {"prefix": "Tree Foliage"}, "count": [1500, 5000000]}
        cards = [(x * 0.3, y * 0.3, 4.0) for x in range(40) for y in range(40)]
        one_mesh = self.graph(instance("Tree Foliage", mesh(*boxes(*cards, size=0.1))), instance("Tree Trunk", mesh(
            *boxes((0, 0, 1)))))
        passed, details = form._check_pieces(None, None, one_mesh, rule)
        self.assertEqual((passed, details["pieces"], details["mesh_pieces"], details["instances"]),
                         (True, 1600, 1600, 0))
        leaf = mesh(*boxes((0, 0, 0)))
        instanced = self.graph(instance("Tree Foliage", mesh([], [])), *[
            instance("Tree Leaf", leaf, is_instance=True, parent="Tree Foliage") for _ in range(1700)])
        passed, details = form._check_pieces(None, None, instanced, rule)
        self.assertEqual((passed, details["pieces"], details["instances"]), (True, 1700, 1700))
        objects = self.graph(*[instance("Tree Foliage.%03d" % index, leaf) for index in range(1500)])
        self.assertTrue(form._check_pieces(None, None, objects, rule)[0])

    def test_a_few_blobs_are_not_foliage_however_many_vertices_they_have(self):
        rule = {"target": {"prefix": "Tree Foliage"}, "count": [1500, 5000000]}
        ring = [(math.cos(index / 500), math.sin(index / 500), 0.0) for index in range(3000)]
        blob = mesh(ring, [tuple(range(3000))])
        passed, details = form._check_pieces(None, None, self.graph(*[instance("Tree Foliage %d" % index, blob)
                                                                      for index in range(6)]), rule)
        self.assertEqual((passed, details["pieces"], details["vertices"]), (False, 6, 18000))
        # The older ``detail`` kind counts vertices, which a ball of that many passes: why ``pieces`` exists.
        blob.polygons = [1]
        self.assertTrue(oneshot_checks._check_detail(None, None, self.graph(*[
            instance("Tree Foliage %d" % index, blob) for index in range(6)]), dict(rule, elements=[1500, 5000000]))[0])

    def test_too_large_to_count_is_inconclusive_not_a_pass(self):
        with mock.patch.object(form, "MAX_VERTICES", 10):
            passed, details = form._check_pieces(None, None, self.graph(instance("Tree Foliage", mesh(*boxes(
                (0, 0, 0), (1, 0, 0))))), {"target": {"prefix": "Tree Foliage"}, "count": [1, 9]})
        self.assertFalse(passed)
        self.assertIn("inconclusive", details["reason"])


class RingTests(unittest.TestCase):
    def test_neighbouring_hits_are_one_limb(self):
        self.assertEqual(form.runs([False, True, True, False, True, False]), 2)
        self.assertEqual(form.runs([True, False, False, True]), 1)              # The ring closes on itself.
        self.assertEqual(form.runs([True] * 5), 1)
        self.assertEqual((form.runs([False] * 5), form.runs([])), (0, 0))

    RULE = {"targets": [{"prefix": "Tree Trunk"}, {"prefix": "Tree Branch"}], "centre": [0.0, 0.0],
            "radii": [0.45, 0.6], "from": 1.7, "to": 7.5, "rays": 180, "count": [5, 400]}

    def ring(self, crossing, rule=None):
        """``crossing``: ``{radius: {target prefix: [bearing in degrees, ...]}}``; a ray within 4 degrees of a
        bearing meets wood of that name where it crosses the cylinder of that radius."""
        calls = []

        def probe(scene, depsgraph, origin, direction, distance, target):
            calls.append((origin, direction, distance, target["prefix"]))
            radius = round(math.hypot(origin[0], origin[1]), 6)
            bearing = math.degrees(math.atan2(origin[1], origin[0])) % 360.0
            limbs = crossing.get(radius, {}).get(target["prefix"], ())
            return {"hit": any(form.bearing_difference(bearing, limb) <= 4.0 for limb in limbs)}
        with mock.patch.object(scenario_checks, "probe", probe):
            return form._check_ring(None, None, None, rule or self.RULE) + (calls,)

    def test_limbs_are_counted_where_they_cross_a_cylinder_around_the_axis(self):
        six = {"Tree Branch": [0, 60, 120, 180, 240, 300]}
        passed, details, calls = self.ring({0.45: six, 0.6: six})
        self.assertEqual((passed, details["most"], details["separate_at_each_radius"]), (True, 6, {"0.45": 6, "0.6": 6}))
        origin, direction, distance, _ = calls[0]
        self.assertEqual((origin, direction, round(distance, 6)), ((0.45, 0.0, 1.7), (0.0, 0.0, 1.0), 5.8))
        four = {"Tree Branch": [0, 90, 180, 270]}
        self.assertEqual(self.ring({0.45: four, 0.6: four})[:1], (False,))
        self.assertEqual(self.ring({})[1]["most"], 0)

    def test_the_fork_may_belong_to_the_trunk_and_a_shared_base_is_not_held_against_the_tree(self):
        stubs = {"Tree Trunk": [0, 72, 144, 216, 288]}                 # The trunk object carries the first fork.
        passed, details, _ = self.ring({0.45: stubs, 0.6: {"Tree Branch": [0, 72, 144, 216, 288]}})
        self.assertEqual((passed, details["most"]), (True, 5))
        shared = {0.45: {"Tree Branch": [0, 90, 180, 270]},            # Two limbs leave as one and part by 0.6 m.
                  0.6: {"Tree Branch": [0, 80, 100, 180, 270]}}
        passed, details, _ = self.ring(shared)
        self.assertEqual((passed, details["separate_at_each_radius"]), (True, {"0.45": 4, "0.6": 5}))
        one = dict(self.RULE, target={"prefix": "Tree Branch"})
        del one["targets"]
        self.assertEqual(self.ring({0.45: stubs}, one)[1]["most"], 0)  # A single target is only that name.

    @staticmethod
    def limb(bearing, start=0.05, end=1.0, height=2.3, rise=0.8):
        """A limb as a line of wood from ``start`` to ``end`` metres out along a bearing, rising as it goes."""
        angle = math.radians(bearing)
        points = [((start + (end - start) * t) * math.cos(angle), (start + (end - start) * t) * math.sin(angle),
                   height + rise * t) for t in (0.0, 0.25, 0.5, 0.75, 1.0)]
        return mesh(points, [(index, index + 1) for index in range(4)])

    @unittest.skipIf(numpy is None, "NumPy is not installed")
    def test_limbs_crossing_at_overlapping_angles_are_counted_as_the_pieces_they_are(self):
        # A recorded tree: five limbs from the fork, two crossing the cylinder at nearly one bearing.
        bearings = [0, 72, 180, 186, 290]
        rule = dict(self.RULE, radii=[0.45], core=0.25)
        graph = NS(object_instances=[instance("Tree Branches", self.limb(b, height=2.3 + 0.3 * (b == 186)))
                                     for b in bearings])
        with mock.patch.object(scenario_checks, "probe", lambda *args: {"hit": any(
                form.bearing_difference(math.degrees(math.atan2(args[2][1], args[2][0])) % 360, b) <= 4.0
                for b in bearings)}):
            passed, details = form._check_ring(None, None, graph, rule)
            self.assertEqual(details["separate_at_each_radius"], {"0.45": 4})   # The rays merge 180 and 186.
            self.assertEqual((passed, details["pieces_from_core"], details["most"]), (True, 5, 5))
            # A side branch leaving a limb outside the trunk is not a limb; a limb that stays inside is not either.
            extra = [instance("Tree Branches", self.limb(30, start=0.5, end=1.2)),
                     instance("Tree Branches", self.limb(120, start=0.05, end=0.4))]
            busy = NS(object_instances=graph.object_instances[:4] + extra)
            self.assertEqual(form._check_ring(None, None, busy, rule)[1]["pieces_from_core"], 4)
            # Limbs built as one mesh with the trunk are one piece: the rays' count stands.
            star = mesh([(0.0, 0.0, 2.3)] + [(math.cos(math.radians(b)), math.sin(math.radians(b)), 3.0)
                                             for b in bearings], [(0, index) for index in range(1, 6)])
            passed, details = form._check_ring(None, None, NS(object_instances=[instance("Tree Trunk", star)]), rule)
            self.assertEqual((details["pieces_from_core"], details["most"]), (1, 4))


@unittest.skipIf(numpy is None, "NumPy is not installed")
class GroupsTests(unittest.TestCase):
    RULE = {"target": {"prefix": "Cottage Window"}, "gap": 0.25, "size": [0.4, 2.6], "count": [4, 60]}

    def window(self, x, y=0.0, name="Cottage Window"):
        """Frame, bars and glass as separate pieces 0.3 m apart: one window, about 1 m across."""
        return instance(name, mesh(*boxes((x - 0.4, y, 1.5), (x, y, 1.5), (x + 0.4, y, 1.5), (x, y, 1.9), (x, y, 1.1))))

    def test_touching_pieces_are_one_window_in_one_object_or_many(self):
        separate = NS(object_instances=[self.window(x) for x in (-6, -3, 0, 3)])
        passed, details = form._check_groups(None, None, separate, self.RULE)
        self.assertEqual((passed, details["groups"], details["pieces"]), (True, 4, 20))
        centres = [(x + dx, 0.0, 1.5 + dz) for x in (-6, -3, 0, 3)
                   for dx, dz in ((-0.4, 0), (0, 0), (0.4, 0), (0, 0.4), (0, -0.4))]
        together = NS(object_instances=[instance("Cottage Windows", mesh(*boxes(*centres)))])   # All in one mesh.
        passed, details = form._check_groups(None, None, together, self.RULE)
        self.assertEqual((passed, details["groups"], details["pieces"]), (True, 4, 20))

    def test_three_windows_or_one_shared_pane_are_not_four(self):
        three = NS(object_instances=[self.window(x) for x in (-3, 0, 3)])
        self.assertEqual(form._check_groups(None, None, three, self.RULE)[0], False)
        pane = instance("Cottage Window Glow", mesh(*boxes((0, 0, 1.5), size=9.0)))   # One pane behind them all.
        passed, details = form._check_groups(None, None, NS(object_instances=[self.window(x) for x in (-3, 0, 3, 6)]
                                                            + [pane]), self.RULE)
        self.assertEqual((passed, details["sized"]), (False, 1))    # Three are one 9 m group with it; one is clear.
        other = NS(object_instances=[self.window(x, name="Shutter") for x in (-6, -3, 0, 3)])
        self.assertEqual(form._check_groups(None, None, other, self.RULE)[1]["pieces"], 0)

    def test_an_instance_is_placed_by_its_own_matrix(self):
        moved = [[1.0, 0.0, 0.0, 10.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]
        data = self.window(0).object.data
        both = NS(object_instances=[instance("Cottage Window", data), instance("Cottage Window", data, matrix=moved)])
        bounds = form.piece_bounds(both, self.RULE["target"])
        self.assertEqual(len(bounds), 10)
        self.assertAlmostEqual(max(high[0] for _, high in bounds), 10.5)
        self.assertEqual(len(form.grouped(bounds, 0.25)), 2)

    def test_too_many_pieces_are_inconclusive(self):
        with mock.patch.object(form, "MAX_GROUPED_PIECES", 3):
            passed, details = form._check_groups(None, None, NS(object_instances=[self.window(0)]), self.RULE)
        self.assertEqual((passed, details["reason"]), (False, "inconclusive: more than 3 pieces to group"))


class InstancedLeavesTests(unittest.TestCase):
    """Leaves placed as instances: the leaf carries the material, the object that places it has no faces."""

    def scene(self, leaf_material):
        import shader_fakes
        from test_native_oneshot_checks import Objects, holder
        green = shader_fakes.material("Leaf Green", base=(0.1, 0.3, 0.06, 1))
        placer = holder("Tree Foliage", data=NS(polygons=[1, 2], materials=[]))
        placer.evaluated_get = lambda depsgraph: NS(material_slots=[], data=NS(polygons=[], materials=[]))
        leaf = NS(type="MESH", original=NS(name="Tree Leaf"), material_slots=[NS(material=green)] if leaf_material else [])
        graph = NS(object_instances=[NS(object=leaf, is_instance=True, parent=NS(original=NS(name="Tree Foliage")))
                                     for _ in range(3)])
        return NS(objects=Objects([placer])), graph

    def test_the_leafs_material_is_the_foliages(self):
        rule = {"target": {"prefix": "Tree Foliage"}, "color": ["green"]}
        scene, graph = self.scene(leaf_material=True)
        passed, details = oneshot_checks._check_surface(None, scene, graph, rule)
        self.assertEqual((passed, details["material"]), (True, "Leaf Green"))
        scene, graph = self.scene(leaf_material=False)
        passed, details = oneshot_checks._check_surface(None, scene, graph, rule)
        self.assertEqual((passed, details["objects_without"]), (False, ["Tree Foliage"]))
        other = NS(object_instances=[NS(object=graph.object_instances[0].object, is_instance=True,
                                        parent=NS(original=NS(name="Bush")))])
        self.assertEqual(oneshot_checks._instanced_materials(other, rule["target"]), {})   # Someone else's instances.
        self.assertEqual(oneshot_checks._instanced_materials(None, rule["target"]), {})

    def test_an_object_that_only_places_instances_needs_no_material_of_its_own(self):
        scene, graph = self.scene(leaf_material=True)
        tidy = {"kind": "hygiene", "max_without_material": 0}
        with mock.patch("scenario_hygiene.orphans", lambda bpy: {}), \
                mock.patch("scenario_hygiene.saved_orphans", lambda bpy: {}):
            passed, details = oneshot_checks._check_hygiene(None, scene, graph, tidy)
            self.assertEqual((passed, details["meshes_without_material"]), (True, []))
            scene.objects[0].evaluated_get = lambda depsgraph: NS(material_slots=[], data=NS(polygons=[1], materials=[]))
            passed, details = oneshot_checks._check_hygiene(None, scene, graph, tidy)   # It shows faces of its own.
            self.assertEqual((passed, details["meshes_without_material"]), (False, ["Tree Foliage"]))


class LightFromTests(unittest.TestCase):
    def scene(self, *suns):
        return NS(objects=[NS(type="LIGHT", name=name, matrix_world=Offset(z_axis=axis)) for name, axis in suns])

    def test_a_light_comes_from_its_own_z_axis(self):
        rule = {"target": {"name": "Sun"}, "azimuth_deg": 225.0, "within_deg": 60.0}
        passed, details = form._check_light_from(None, self.scene(("Sun", (-0.5, -0.5, 0.7))), None, rule)
        self.assertEqual((passed, details["azimuth_deg"], details["off_deg"]), (True, 225.0, 0.0))
        passed, details = form._check_light_from(None, self.scene(("Sun", (0.6, 0.6, 0.5))), None, rule)
        self.assertEqual((passed, details["lights"][0]["off_deg"]), (False, 180.0))     # From the camera's right.
        passed, details = form._check_light_from(None, self.scene(("Sun", (0.0, 0.0, 1.0))), None, rule)
        self.assertEqual((passed, details["lights"][0]["reason"]), (False, "shines straight down"))
        self.assertEqual(form._check_light_from(None, self.scene(("Key", (-1, -1, 1))), None, rule),
                         (False, {"lights": "no matching light"}))
        self.assertEqual((form.bearing_difference(350, 10), form.bearing_difference(-150, 225)), (20.0, 15.0))

    def test_the_kinds_are_registered_for_every_grader(self):
        self.assertEqual(set(form.CHECKS), {"pieces", "ring", "groups", "light_from"})
        self.assertTrue(set(form.CHECKS) <= set(scenario_checks.CHECKS))


if __name__ == "__main__":
    unittest.main()
