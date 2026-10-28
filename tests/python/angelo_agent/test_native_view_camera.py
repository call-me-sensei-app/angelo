# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host contract tests for angelo_view's free cameras and reference pairs.

A views item {location, look_at, lens | ortho_scale} is a camera drawn from
matrices like an axis view; with ``reference`` its picture is an attached image
beside the view. Covered here without Blender: the camera math against
camera_create's look_at convention and Blender's projection, clip planes,
validation that names the field, the picture budget, the attached-image
lookup, the WebP header and the pair layout. test_blender_view_camera.py proves
pixels, read-only state and memory in the compiled app.
"""

import importlib.util
import json
import math
import random
import struct
import sys
import tempfile
import unittest
import uuid
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.action_camera_light_spec import look_at_euler  # noqa: E402
from angelo_agent.claude_evidence import ClaudeVisualEvidence  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.references import MAX_TOOL_IMAGES  # noqa: E402
from angelo_agent.request_path import IMAGE_FRAMINGS  # noqa: E402
from angelo_agent.tool_specs import (TOOL_MAP, codex_tools, dynamic_tools, native_plan_schema,  # noqa: E402
                                      validate_call)
from angelo_agent.view_framing import (AXIS_DIRECTIONS, DEFAULT_CLIP_END, DEFAULT_CLIP_START,  # noqa: E402
                                        DEPTH_RESOLUTION, FAR_RATIO, axis_basis, box_depths, free_camera,
                                        free_clip, look_at_basis, output_size)
from angelo_agent.view_spec import (CAMERA, MAX_PAIRS, MAX_VIEWS, VIEWS_NOTE, framing_kind,  # noqa: E402
                                    validate_view)

try:
    import numpy
except ImportError:  # CI installs it (run_host_tests --max-skips 0).
    numpy = None

HERO = {"location": [-8.6, -26.0, 0.6], "look_at": [1.5, 0.0, 0.4], "lens": 24}


def euler_matrix(euler):
    """Blender's XYZ Euler rotation, Rz(c) @ Ry(b) @ Rx(a), row-major."""
    a, b, c = euler
    rx = [[1, 0, 0], [0, math.cos(a), -math.sin(a)], [0, math.sin(a), math.cos(a)]]
    ry = [[math.cos(b), 0, math.sin(b)], [0, 1, 0], [-math.sin(b), 0, math.cos(b)]]
    rz = [[math.cos(c), -math.sin(c), 0], [math.sin(c), math.cos(c), 0], [0, 0, 1]]

    def product(p, q):
        return [[sum(p[r][k] * q[k][col] for k in range(3)) for col in range(3)] for r in range(3)]
    return product(rz, product(ry, rx))


def project(camera, point):
    """Normalized device coordinates of a world point through a free camera's matrices."""
    view = [sum(camera["view_matrix"][r][k] * (list(point) + [1.0])[k] for k in range(4)) for r in range(4)]
    clip = [sum(camera["projection_matrix"][r][k] * view[k] for k in range(4)) for r in range(4)]
    return [value / clip[3] for value in clip[:3]]


def box(low, high):
    """The eight corners of an axis-aligned box, as Object.bound_box lists a box's corners."""
    return [[x, y, z] for x in (low[0], high[0]) for y in (low[1], high[1]) for z in (low[2], high[2])]


def views(*items, **extra):
    return validate_call("angelo_view", dict({"views": list(items)}, **extra))


class CameraMathTests(unittest.TestCase):
    def test_the_basis_is_camera_create_look_at_for_any_direction(self):
        rng = random.Random(9)
        cases = [((0, 0, 10), (0, 0, 0)), ((0, 0, -10), (0, 0, 0)), ((3, 4, 5), (3, 4, -2)),
                 ((1, 2, 3), (1, 2, 30)), ((5, -5, 2), (0, 0, 0))]
        cases += [(tuple(rng.uniform(-50, 50) for _ in range(3)), tuple(rng.uniform(-50, 50) for _ in range(3)))
                  for _ in range(200)]
        for location, look_at in cases:
            with self.subTest(location=location, look_at=look_at):
                rotation = euler_matrix(look_at_euler(location, look_at))
                for column, axis in enumerate(look_at_basis(location, look_at)):
                    for row in range(3):
                        self.assertAlmostEqual(axis[row], rotation[row][column], places=9)

    def test_straight_down_and_up_are_oriented_as_top_and_bottom(self):
        # The degenerate up vector: looking along world Z, world +X is the picture's right.
        for location, axis in (((2, 3, 40), "TOP"), ((2, 3, -40), "BOTTOM")):
            got, want = look_at_basis(location, (2, 3, 0)), axis_basis(AXIS_DIRECTIONS[axis])
            with self.subTest(axis=axis):
                for a, b in zip(got, want):
                    self.assertEqual([round(v, 9) + 0 for v in a], [round(v, 9) + 0 for v in b])
        nearly = look_at_basis((1e-12, 0, 5), (0, 0, 0))  # Within the degenerate threshold: still +X right.
        self.assertEqual([round(v, 9) for v in nearly[0]], [1.0, 0.0, 0.0])
        self.assertEqual([round(v, 9) for v in nearly[1]], [0.0, 1.0, 0.0])

    def test_look_at_projects_to_the_centre_and_the_lens_spans_the_longer_side(self):
        for aspect in (16 / 9, 1.0, 9 / 16):
            camera = free_camera(dict(HERO, lens=24.0), aspect)
            with self.subTest(aspect=aspect):
                x, y, _z = project(camera, HERO["look_at"])
                self.assertAlmostEqual(x, 0.0, places=9)
                self.assertAlmostEqual(y, 0.0, places=9)
                # A point at the edge of a 36 mm sensor behind a 24 mm lens, along the picture's longer side.
                right, up, forward = look_at_basis(HERO["location"], HERO["look_at"])
                depth = 10.0
                spread = depth * 18.0 / 24.0
                centre = [c - f * depth for c, f in zip(HERO["location"], forward)]
                edge = [c + spread * (r if aspect >= 1 else u) for c, r, u in zip(centre, right, up)]
                ndc = project(camera, edge)
                self.assertAlmostEqual(ndc[0 if aspect >= 1 else 1], 1.0, places=9)
                self.assertAlmostEqual(abs(ndc[1 if aspect >= 1 else 0]), 0.0, places=9)
        self.assertEqual(camera["details"]["sensor_width_mm"], 36.0)
        self.assertEqual(camera["projection"], "PERSPECTIVE")

    def test_ortho_scale_spans_the_longer_side_and_starts_at_the_camera(self):
        item = {"location": [0, 0, 60], "look_at": [0, 0, 0], "ortho_scale": 64.0}
        for aspect in (2.0, 0.5):
            camera = free_camera(item, aspect, 59.0, 61.0)
            with self.subTest(aspect=aspect):
                self.assertEqual(camera["projection"], "ORTHOGRAPHIC")
                self.assertEqual(camera["details"]["clip_start"], 0.0)
                ndc = project(camera, (32.0, 0, 0) if aspect >= 1 else (0, 32.0, 0))
                self.assertAlmostEqual(ndc[0 if aspect >= 1 else 1], 1.0, places=9)
                shorter = project(camera, (0, 32.0 / aspect, 0) if aspect >= 1 else (32.0 * aspect, 0, 0))
                self.assertAlmostEqual(shorter[1 if aspect >= 1 else 0], 1.0, places=9)
        # Straight down with Y up in the picture, as the TOP view.
        self.assertGreater(project(free_camera(item, 1.0), (0, 10, 0))[1], 0)
        self.assertGreater(project(free_camera(item, 1.0), (10, 0, 0))[0], 0)

    def test_lens_and_ortho_scale_differ_only_in_projection(self):
        persp = free_camera({"location": [5, -5, 5], "look_at": [0, 0, 0], "lens": 50.0}, 1.5)
        ortho = free_camera({"location": [5, -5, 5], "look_at": [0, 0, 0], "ortho_scale": 6.0}, 1.5)
        self.assertEqual(persp["view_matrix"], ortho["view_matrix"])
        self.assertNotEqual(persp["projection_matrix"], ortho["projection_matrix"])
        self.assertEqual(ortho["projection_matrix"][3], [0.0, 0.0, 0.0, 1.0])
        self.assertEqual(persp["projection_matrix"][3], [0.0, 0.0, -1.0, 0.0])
        self.assertEqual(ortho["details"]["ortho_scale"], 6.0)
        self.assertNotIn("lens_mm", ortho["details"])

    def test_any_pose_lens_and_aspect_projects_look_at_to_the_centre_and_the_sensor_to_the_edge(self):
        rng = random.Random(11)
        for trial in range(300):
            location = [rng.uniform(-500, 500) for _ in range(3)]
            look_at = [rng.uniform(-500, 500) for _ in range(3)]
            if math.dist(location, look_at) < 0.01:
                continue
            aspect = rng.choice((0.25, 9 / 16, 1.0, 4 / 3, 16 / 9, 4.0))
            orthographic = trial % 3 == 0
            item = dict(location=location, look_at=look_at, **(
                {"ortho_scale": rng.uniform(0.01, 2000)} if orthographic else {"lens": rng.uniform(1, 5000)}))
            right, up, forward = look_at_basis(location, look_at)
            depth = math.dist(location, look_at)
            camera = free_camera(item, aspect, depth * 0.5, depth * 2, False)
            with self.subTest(trial=trial):
                x, y, z = project(camera, look_at)
                self.assertAlmostEqual(x, 0.0, places=6)
                self.assertAlmostEqual(y, 0.0, places=6)
                self.assertLess(abs(z), 1.0)
                half = item["ortho_scale"] / 2 if orthographic else depth * 18.0 / item["lens"]
                axis = right if aspect >= 1 else up
                edge = [t + half * a for t, a in zip(look_at, axis)]
                self.assertAlmostEqual(project(camera, edge)[0 if aspect >= 1 else 1], 1.0, places=5)

    def test_a_box_outside_the_frame_does_not_pull_the_near_plane_in(self):
        """A 4 mm cube just beside the camera, out of frame, set clip_start and raised a false note: only
        boxes the frame can show count, for either projection."""
        location, look_at = (0.0, -10.0, 1.0), (0.0, 0.0, 1.0)
        right, up, forward = look_at_basis(location, look_at)
        ground = box((-1500.0, -1500.0, -1.0), (1500.0, 1500.0, 0.0))
        beside = box((0.4, -9.984, 0.98), (0.404, -9.98, 0.984))      # 0.4 to the right, 16 mm ahead.
        ahead = box((-0.002, -9.98, 0.998), (0.002, -9.976, 1.002))   # On the axis, 20 mm ahead.
        for side in ((right, up, 0.36, 0.2, True), (right, up, 0.3, 0.17, False)):
            with self.subTest(perspective=side[4]):
                self.assertIsNone(box_depths(ground + beside, location, forward, side)[0])
                self.assertAlmostEqual(box_depths(ground + ahead, location, forward, side)[0], 0.02, places=4)
                self.assertIsNotNone(box_depths(ground + beside, location, forward)[0])   # Unculled: counted.

    def test_clip_planes_hold_every_box_and_bound_the_depth_ratio(self):
        rng = random.Random(4)
        for trial in range(400):
            location = [rng.uniform(-20, 20) for _ in range(3)]
            look_at = [rng.uniform(-20, 20) for _ in range(3)]
            if math.dist(location, look_at) < 0.01:
                continue
            scale = 10 ** rng.uniform(-3, 4)  # Scenes from millimetres to kilometres.
            corners = []
            for _box in range(rng.randint(1, 5)):
                low = [scale * rng.uniform(-30, 30) for _ in range(3)]
                corners += box(low, [v + scale * rng.uniform(0, 10) for v in low])
            front, farthest, straddles = box_depths(corners, location, look_at_basis(location, look_at)[2])
            for orthographic in (False, True):
                start, end = free_clip(front, farthest, straddles, orthographic)
                with self.subTest(trial=trial, orthographic=orthographic):
                    self.assertGreater(end, start)
                    if farthest <= 0:
                        continue
                    reach = farthest * 1.05
                    self.assertGreaterEqual(end, 2 * reach * (1 - 1e-12))  # Generous: shadows need it.
                    if orthographic:
                        self.assertEqual(start, 0.0)
                        continue
                    self.assertGreaterEqual(end, start * FAR_RATIO * (1 - 1e-12))
                    self.assertLessEqual(reach / start, DEPTH_RESOLUTION * (1 + 1e-9))
                    if not straddles and front is not None:
                        self.assertAlmostEqual(start, max(front / 2, reach / DEPTH_RESOLUTION))
                    else:  # Never past Blender's 0.1 nor past half a box wholly in front, but for the depth floor.
                        self.assertLessEqual(start, max(min(0.1, front / 2 if front else 0.1),
                                                        reach / DEPTH_RESOLUTION) * (1 + 1e-12))
        self.assertEqual(free_clip(None, None, False, False), (DEFAULT_CLIP_START, DEFAULT_CLIP_END))
        self.assertEqual(free_clip(None, -1.0, False, True), (0.0, DEFAULT_CLIP_END))
        self.assertEqual(box_depths([], (0, 0, 0), (0, 0, 1)), (None, None, False))

    def test_near_subjects_kept_and_far_geometry_resolved_in_scenes_the_review_built(self):
        """An eye-level camera at (0, 0, 1.7) looking along +Y at a 0.3 cube 0.6 ahead."""
        location, forward = (0.0, 0.0, 1.7), look_at_basis((0, 0, 1.7), (0, 1, 1.7))[2]
        cube = box((-0.15, 0.45, 1.55), (0.15, 0.75, 1.85))

        def clip(*boxes):
            return free_clip(*box_depths([c for b in boxes for c in b], location, forward), False)
        # A 400 m ground: Blender's new camera exactly, so Workbench draws what a camera object draws.
        self.assertEqual(clip(box((-200, -200, 0), (200, 200, 0)), cube), (0.1, 1000.0))
        # A 20 km ground and a 3 km sky dome around the camera: the cube stays (its corner is 0.45 deep).
        for extent in (10000.0, 3000.0):
            start, end = clip(box((-extent, -extent, 0), (extent, extent, extent * (extent < 5000))), cube)
            self.assertLess(start, 0.45)
            self.assertGreaterEqual(end, 2 * extent)
        # Nothing reaches the camera: half the nearest box's depth, or the depth floor when a 2 km box stands
        # 50 km away (the cube is then cut, which the result states: view_free_camera.notes).
        self.assertAlmostEqual(clip(cube)[0], 0.225)
        start, _end = clip(cube, box((-1000, 50000, 0), (1000, 52000, 2000)))
        self.assertAlmostEqual(start, 52000 * 1.05 / DEPTH_RESOLUTION)
        # A box nearly touching the camera's plane cannot ruin the precision of a 30 m scene.
        start, end = clip(box((-0.0005, 1e-6, 1.6995), (0.0005, 0.001, 1.7005)), box((-10, 5, 0), (10, 30, 5)))
        self.assertAlmostEqual(start, 30 * 1.05 / DEPTH_RESOLUTION)
        # A truncated scan: twice the farthest box seen, and a box may straddle unseen.
        self.assertEqual(free_clip(3.0, 40.0, False, False, complete=False), (0.08, 800.0))

    def test_look_at_sets_the_direction_and_never_the_clip_planes(self):
        corners = box((-300, -300, -1), (300, 300, 0))  # A ground all round.
        location = [0.0, -2.0, 1.6]
        clips = set()
        for distance in (0.0002, 0.01, 0.1, 40.0, 4000.0):
            look_at = [0.0, -2.0 + distance, 1.6]
            camera = free_camera({"location": location, "look_at": look_at, "lens": 50.0}, 1.5,
                                 *box_depths(corners, location, look_at_basis(location, look_at)[2]))
            clips.add((camera["details"]["clip_start"], camera["details"]["clip_end"]))
        self.assertEqual(clips, {(DEFAULT_CLIP_START, DEFAULT_CLIP_END)})

    def test_a_free_camera_keeps_its_values_for_reuse(self):
        camera = free_camera(dict(HERO, reference=str(uuid.uuid4())), 16 / 9, 5.0, 40.0, False)
        self.assertEqual(camera["free"], {"location": HERO["location"], "look_at": HERO["look_at"], "lens": 24})


class CameraItemValidationTests(unittest.TestCase):
    def test_defaults_normalization_and_idempotence(self):
        checked = views({"location": [1, 2, 3], "look_at": [0, 0, 0]})
        self.assertEqual(checked["views"], [{"location": [1.0, 2.0, 3.0], "look_at": [0.0, 0.0, 0.0], "lens": 50.0}])
        self.assertEqual(validate_call("angelo_view", checked), checked)
        self.assertEqual(framing_kind(checked["views"][0]), CAMERA)
        identity = str(uuid.uuid4())
        paired = views(dict(HERO, reference=identity.upper()), shading="LIT")
        self.assertEqual(paired["views"][0]["reference"], identity)
        ortho = views({"location": [0, 0, 9], "look_at": [0, 0, 0], "ortho_scale": 4})
        self.assertEqual(ortho["views"][0], {"location": [0.0, 0.0, 9.0], "look_at": [0.0, 0.0, 0.0],
                                             "ortho_scale": 4.0})
        mixed = views("FRONT", {"location": [1, 2, 3], "look_at": [0, 0, 0]}, "SCENE_CAMERA")
        self.assertEqual([framing_kind(item) for item in mixed["views"]], ["AXIS", CAMERA, "SCENE_CAMERA"])
        for shading in ("SOLID", "WIREFRAME", "MATERIAL", "ATTRIBUTE", "LIT"):
            views({"location": [1, 2, 3], "look_at": [0, 0, 0]}, shading=shading)
        strip = views({"location": [1, 2, 3], "look_at": [0, 0, 0]}, frames=[1, 10])
        self.assertEqual(strip["frames"], [1, 10])

    def test_invalid_camera_items_name_their_field(self):
        good = {"location": [1, 2, 3], "look_at": [0, 0, 0]}
        cases = [
            (dict(good, location=[1, 2]), r"views\[0\]\.location is \[x, y, z\]"),
            (dict(good, location=[1, 2, math.inf]), r"views\[0\]\.location"),
            (dict(good, location=[1, 2, True]), r"views\[0\]\.location"),
            (dict(good, look_at=[0, 0, 1e7]), r"views\[0\]\.look_at"),
            (dict(good, look_at="origin"), r"views\[0\]\.look_at"),
            ({"look_at": [0, 0, 0]}, r"views\[0\]\.location"),
            ({"location": [1, 2, 3]}, r"views\[0\]\.look_at"),
            (dict(good, look_at=[1, 2, 3.00001]), r"views\[0\]\.look_at must be at least 0\.0001 from location"),
            (dict(good, lens=0.5), r"views\[0\]\.lens \(mm\) is a number from 1 to 5000"),
            (dict(good, lens="50mm"), r"views\[0\]\.lens"),
            (dict(good, ortho_scale=0), r"views\[0\]\.ortho_scale \(world units\) is a number from 0\.001 to 100000"),
            (dict(good, lens=35, ortho_scale=6), r"views\[0\] has lens and ortho_scale"),
            (dict(good, fov=40), r"views\[0\] has unknown field fov"),
            (dict(good, reference="hero.jpg"), r"views\[0\]\.reference is the reference_id"),
            (dict(good, reference=7), r"views\[0\]\.reference"),
        ]
        for item, pattern in cases:
            with self.subTest(item=item):
                with self.assertRaisesRegex(ValueError, pattern):
                    views(item)
        with self.assertRaisesRegex(ValueError, r"views\[1\]\.lens"):
            views("FRONT", dict(good, lens=-1))
        huge = 10 ** 400  # Past a float's range: refused as invalid, never an OverflowError.
        for item, pattern in ((dict(good, lens=huge), r"views\[0\]\.lens"),
                              (dict(good, ortho_scale=huge), r"views\[0\]\.ortho_scale"),
                              (dict(good, location=[huge, 0, 0]), r"views\[0\]\.location")):
            with self.subTest(item=str(item)[:60]), self.assertRaisesRegex(ValueError, pattern):
                views(item)

    def test_every_tool_refuses_an_integer_past_a_floats_range_as_invalid(self):
        huge = 10 ** 400
        for name, args in (("angelo_view", {"views": ["SCENE_CAMERA"], "shading": "RENDERED",
                                            "region": [0, 0, huge, 1]}),
                           ("angelo_create_cube", {"name": "Cube", "size": huge, "location": [0, 0, 0]}),
                           ("angelo_set_transform", {"object": "Cube", "location": [huge, 0, 0]})):
            with self.subTest(tool=name), self.assertRaises(ValueError):
                validate_call(name, args)

    def test_how_many_items_a_call_may_have(self):
        pair = [dict(HERO, location=[i, -26.0, 0.6], reference=str(uuid.uuid4())) for i in range(MAX_PAIRS + 1)]
        self.assertEqual(len(views(*pair[:MAX_PAIRS])["views"]), MAX_PAIRS)
        with self.assertRaisesRegex(ValueError, "Request 1-3 views per call, or 1-6 free cameras"):
            views(*pair)
        plain = [{"location": [i, 5, 5], "look_at": [0, 0, 0]} for i in range(MAX_VIEWS + 1)]
        with self.assertRaisesRegex(ValueError, "only free cameras that all have reference may be up to 6 "
                                                "\\(this call has 4 views\\)"):
            views(*plain)
        with self.assertRaisesRegex(ValueError, "this call has 4 views"):
            views(*(pair[:3] + ["FRONT"]))
        with self.assertRaisesRegex(ValueError, "Repeated view"):
            views(plain[0], dict(plain[0]))
        with self.assertRaisesRegex(ValueError, "Repeated view"):  # Equal after the default lens is filled in.
            views(plain[0], dict(plain[0], lens=50))
        self.assertEqual(len(views(plain[0], dict(plain[0], lens=35))["views"]), 2)

    def test_combinations_that_cannot_hold_a_free_camera_or_a_pair(self):
        item = {"location": [1, 2, 3], "look_at": [0, 0, 0]}
        with self.assertRaisesRegex(ValueError, "RENDERED renders the scene's own camera"):
            views(item, shading="RENDERED")
        with self.assertRaisesRegex(ValueError, r"views\[0\]\.reference pairs one view with an image; frames"):
            views(dict(item, reference=str(uuid.uuid4())), frames=[1, 2])
        with self.assertRaisesRegex(ValueError, "frames makes one strip from one views item"):
            views(item, "FRONT", frames=[1, 2])
        with self.assertRaisesRegex(ValueError, "image shows an existing picture"):
            views(item, image="LAST_JOB")


class CameraItemSchemaTests(unittest.TestCase):
    def test_every_runtime_reads_the_item_and_its_note(self):
        tool = TOOL_MAP["angelo_view"]
        schema = tool["inputSchema"]["properties"]["views"]
        self.assertEqual(schema["maxItems"], MAX_PAIRS)
        self.assertEqual(schema["description"], VIEWS_NOTE)
        self.assertIn("free camera (views)", tool["description"])
        plan = json.dumps(native_plan_schema())
        self.assertIn(json.dumps(VIEWS_NOTE), plan)
        for declared in (dynamic_tools(), codex_tools(short_identifiers=True), codex_tools(defer=False)):
            view = next(t for t in declared if t.get("name") == "angelo_view")
            self.assertIn(json.dumps(VIEWS_NOTE), json.dumps(view["inputSchema"]))

    def test_the_note_states_facts_not_advice(self):
        for word in ("use ", "prefer", "should", "try ", "when you", "to match"):
            self.assertNotIn(word, VIEWS_NOTE.lower())
        for fact in ("36 mm sensor", "default 50", "ortho_scale", "Z up", "straight down: as TOP", "render aspect",
                     "reference_id", "left", "up to 6"):
            self.assertIn(fact, VIEWS_NOTE)

    @unittest.skipUnless(importlib.util.find_spec("jsonschema"), "jsonschema is not installed")
    def test_the_plan_schema_accepts_pairs_and_free_cameras(self):
        import jsonschema
        plan = {"operations": [{"tool": "angelo_view", "arguments": {
            "views": [dict(HERO, reference=str(uuid.uuid4())) for _ in range(MAX_PAIRS)], "shading": "LIT"}}]}
        jsonschema.validate(plan, native_plan_schema())
        jsonschema.validate({"operations": [{"tool": "angelo_view", "arguments": {
            "views": ["FRONT", {"location": [0, 0, 9], "look_at": [0, 0, 0], "ortho_scale": 4}]}}]},
            native_plan_schema())

    def test_request_receipts_count_free_camera_pictures(self):
        self.assertIn(CAMERA, IMAGE_FRAMINGS)


class PictureBudgetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.host = Controller(self.directory.name)
        self.evidence = ClaudeVisualEvidence(self.host.references)

    def tearDown(self):
        self.host.close()
        self.directory.cleanup()

    def record(self):
        value = self.host.references.new_observation("Reference pair · lit")
        Path(value["path"]).write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        return value

    def pairs(self, count, start=0):
        return {"tool": "angelo_view", "arguments": {"views": [
            {"location": [float(start + i), -26.0, 0.6], "look_at": [1.5, 0.0, 0.4], "lens": 24.0,
             "reference": str(uuid.UUID(int=start + i + 1))} for i in range(count)], "shading": "LIT"}}

    def test_each_pair_is_one_picture_of_the_twelve(self):
        self.assertEqual(MAX_TOOL_IMAGES, MAX_PAIRS)
        six = self.pairs(6)
        self.assertIsNone(self.evidence.problem([six]))
        self.evidence.admit(six, {"views": [self.record() for _ in range(6)]})
        self.assertIsNone(self.evidence.problem([self.pairs(6, start=6)]))
        problem = self.evidence.problem([self.pairs(6, start=6), self.pairs(1, start=20)])
        self.assertIn("has 6 of 12 left", problem)
        self.assertIn("this plan asks for 7 new ones", problem)

    def test_a_result_of_six_pictures_is_admitted_and_seven_is_refused(self):
        records = [self.record() for _ in range(MAX_TOOL_IMAGES + 1)]
        self.assertEqual(len(self.host.references.tool_images({"views": records[:MAX_TOOL_IMAGES]})), 6)
        with self.assertRaisesRegex(ValueError, "at most 6 images"):
            self.host.references.tool_images({"views": records})

    def test_kept_pairs_are_captured_again_in_calls_of_at_most_three(self):
        six = self.pairs(6)
        self.evidence.admit(six, {"views": [self.record() for _ in range(6)]})
        self.evidence.invalidate()
        operations = self.evidence.refresh_operations()
        self.assertEqual([len(operation["arguments"]["views"]) for operation in operations], [3, 3])
        for operation in operations:  # Each refresh call is valid on its own.
            validate_call("angelo_view", operation["arguments"])


class AttachedImageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.host = Controller(self.directory.name)
        self.store = self.host.references

    def tearDown(self):
        self.host.close()
        self.directory.cleanup()

    def attach(self, name, data):
        source = Path(self.directory.name, name)
        source.write_bytes(data)
        return self.store.import_images([str(source)])[0]

    def test_an_attachment_is_found_by_its_id_and_nothing_else(self):
        jpeg = self.attach("hero.jpg", b"\xff\xd8\xff\xe0" + bytes(64))
        self.assertEqual(self.store.attached_image(jpeg["id"]).name, jpeg["id"] + ".jpg")
        self.assertEqual(self.store.attached_image(jpeg["id"].upper()).name, jpeg["id"] + ".jpg")
        for bad in ("hero.jpg", "../" + jpeg["id"], None, 7, ""):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "Not an image reference_id"):
                self.store.attached_image(bad)
        with self.assertRaisesRegex(ValueError, "No image with that reference_id is attached"):
            self.store.attached_image(str(uuid.uuid4()))
        text = self.attach("notes.txt", b"words")
        with self.assertRaisesRegex(ValueError, "No image with that reference_id"):
            self.store.attached_image(text["id"])

    def test_a_link_is_never_followed(self):
        outside = Path(self.directory.name, "outside.png")
        outside.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(32))
        identity = str(uuid.uuid4())
        self.store.directory.mkdir(parents=True, exist_ok=True)
        (self.store.directory / (identity + ".png")).symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "No image with that reference_id"):
            self.store.attached_image(identity)


class ReferenceFactsTests(unittest.TestCase):
    """What a pair learns of its image before decoding it: size as displayed, orientation, refusals."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.host = Controller(self.directory.name)
        self.store = self.host.references

    def tearDown(self):
        self.host.close()
        self.directory.cleanup()

    def attach(self, name, data):
        source = Path(self.directory.name, name)
        source.write_bytes(data)
        return self.store.import_images([str(source)])[0]["id"]

    @staticmethod
    def jpeg(width, height, orientation=None, order="<"):
        """The first bytes of a JPEG (APP1 Exif with Orientation, SOF0); enough for every header check."""
        app1 = b""
        if orientation is not None:
            mark = b"II" if order == "<" else b"MM"
            entry = struct.pack(order + "HHI", 0x0112, 3, 1) + struct.pack(order + "HH", orientation, 0)
            tiff = mark + struct.pack(order + "HI", 42, 8) + struct.pack(order + "H", 1) + entry + bytes(4)
            body = b"Exif\x00\x00" + tiff
            app1 = b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body
        sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
        return b"\xff\xd8" + app1 + sof + b"\xff\xd9"

    def test_exif_orientation_gives_the_displayed_size_and_aspect(self):
        from angelo_agent.view_compare import jpeg_orientation, reference
        for orientation in range(1, 9):
            for order in "<>":
                identity = self.attach("photo.jpg", self.jpeg(400, 300, orientation, order))
                with self.subTest(orientation=orientation, order=order):
                    self.assertEqual(jpeg_orientation(self.store.attached_image(identity)), orientation)
                    pair = reference(self.store, identity, "views[0].reference")
                    displayed = [300, 400] if orientation >= 5 else [400, 300]
                    self.assertEqual(pair["source_size"], displayed)
                    self.assertEqual(pair["stored_size"], [400, 300])
                    self.assertAlmostEqual(pair["aspect"], displayed[0] / displayed[1])
        plain = self.attach("plain.jpg", self.jpeg(640, 480))
        self.assertEqual(reference(self.store, plain, "views[0].reference")["orientation"], 1)

    def test_low_bit_depth_pngs_are_read_and_unreadable_images_are_refused_without_a_path(self):
        from angelo_agent.refusal_codes import refusal_code
        from angelo_agent.view_compare import reference
        import picture_fakes
        palette = self.attach("palette.png", picture_fakes.png_header(64, 32, depth=4))
        self.assertEqual(reference(self.store, palette, "views[2].reference")["source_size"], [64, 32])
        for name, data in (("zero.png", picture_fakes.png_header(0, 10)), ("cut.jpg", b"\xff\xd8\xff\xe0\x00")):
            identity = self.attach(name, data)
            with self.subTest(name=name), self.assertRaises(ValueError) as caught:
                reference(self.store, identity, "views[2].reference")
            self.assertEqual(refusal_code(caught.exception), "VALIDATION")
            self.assertIn("views[2].reference", str(caught.exception))
            self.assertNotIn(str(self.store.directory), str(caught.exception))
        wide = self.attach("wide.png", picture_fakes.png_header(900, 100))
        with self.assertRaisesRegex(ValueError, r"views\[0\]\.reference is a 900x100 image"):
            reference(self.store, wide, "views[0].reference")

    @unittest.skipUnless(numpy is not None, "NumPy is not installed")
    def test_each_orientation_turns_the_stored_rows_as_photo_viewers_do(self):
        from angelo_agent.view_compare import _ORIENT
        rows = numpy.arange(2 * 3).reshape(2, 3, 1)
        expected = {2: numpy.fliplr(rows), 3: numpy.rot90(rows, 2), 4: numpy.flipud(rows),
                    5: rows.transpose(1, 0, 2), 6: numpy.rot90(rows, -1), 7: numpy.rot90(rows, 2).transpose(1, 0, 2),
                    8: numpy.rot90(rows, 1)}
        for orientation, want in expected.items():
            with self.subTest(orientation=orientation):
                self.assertEqual(_ORIENT[orientation](rows).tolist(), want.tolist())

    @unittest.skipUnless(numpy is not None, "NumPy is not installed")
    def test_an_image_several_pairs_share_is_decoded_once_per_size(self):
        from unittest.mock import patch
        from angelo_agent import view_compare
        pair = {"id": "a", "orientation": 1}
        plans = [{"pair": dict(pair), "width": 512, "height": 288} for _ in range(5)]
        plans.append({"pair": dict(pair), "width": 256, "height": 144})
        plans.append({"item": "FRONT", "width": 512, "height": 512})
        tile = numpy.zeros((1, 1, 4), numpy.uint8)
        with patch.object(view_compare, "tile", return_value=(tile, 0.0, None)) as decode:
            view_compare.prepare(plans)
        self.assertEqual(decode.call_count, 2)
        self.assertTrue(all("encode" in plan for plan in plans[:6]) and "encode" not in plans[6])


class PairPictureTests(unittest.TestCase):
    def test_webp_sizes_from_each_chunk_kind(self):
        from angelo_agent.view_compare import webp_size
        with tempfile.TemporaryDirectory() as directory:
            def write(chunk, body):
                path = Path(directory, "x.webp")
                payload = chunk + struct.pack("<I", len(body)) + body
                path.write_bytes(b"RIFF" + struct.pack("<I", 4 + len(payload)) + b"WEBP" + payload)
                return path
            lossy = write(b"VP8 ", b"\x00\x00\x00\x9d\x01\x2a" + struct.pack("<HH", 1280, 720) + bytes(8))
            self.assertEqual(webp_size(lossy), (1280, 720))
            bits = (1279) | (719 << 14)
            lossless = write(b"VP8L", b"\x2f" + bits.to_bytes(4, "little") + bytes(8))
            self.assertEqual(webp_size(lossless), (1280, 720))
            extended = write(b"VP8X", bytes(4) + (1279).to_bytes(3, "little") + (719).to_bytes(3, "little"))
            self.assertEqual(webp_size(extended), (1280, 720))
            other = Path(directory, "x.png")
            other.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(40))
            self.assertIsNone(webp_size(other))

    @unittest.skipUnless(numpy is not None, "NumPy is not installed")
    def test_the_reference_is_left_and_the_view_is_right_as_drawn(self):
        from angelo_agent.view_compare import GAP, encoder, pair_record, picture_size
        width, height = 5, 3
        reference = numpy.zeros((height, width, 4), numpy.uint8)
        reference[..., 0], reference[..., 3] = 200, 255
        drawn = numpy.zeros((height, width, 4), numpy.uint8)  # Bottom row first, as the GPU reads it.
        drawn[0, :, 1], drawn[..., 3] = 99, 255
        png = encoder(reference)(drawn.tobytes(), width, height)
        header = png[16:24]
        self.assertEqual(struct.unpack(">II", header), picture_size(width, height))
        self.assertEqual(picture_size(width, height), (2 * width + GAP, height))
        offset, data = 8, b""
        while offset < len(png):
            length = struct.unpack(">I", png[offset:offset + 4])[0]
            if png[offset + 4:offset + 8] == b"IDAT":
                data += png[offset + 8:offset + 8 + length]
            offset += 12 + length
        rows = numpy.frombuffer(zlib.decompress(data), numpy.uint8).reshape(height, 1 + (2 * width + GAP) * 3)
        pixels = rows[:, 1:].reshape(height, 2 * width + GAP, 3)
        self.assertTrue((pixels[:, :width, 0] == 200).all())
        self.assertTrue((pixels[:, width:width + GAP] == 24).all())
        self.assertTrue((pixels[-1, width + GAP:, 1] == 99).all(), "the view's bottom row is the picture's last")
        self.assertTrue((pixels[:-1, width + GAP:, 1] == 0).all())
        record = pair_record({"id": "abc", "source_size": [1280, 720], "transparent": 0.0, "orientation": 1}, 512, 288)
        turned = pair_record({"id": "abc", "source_size": [720, 1280], "orientation": 6}, 288, 512)
        self.assertEqual(turned["reference"]["exif_orientation"], 6)
        self.assertEqual((record["width"], record["height"], record["view_size"]), (1026, 288, [512, 288]))
        self.assertEqual(record["layout"], "REFERENCE_LEFT_VIEW_RIGHT")
        self.assertEqual(record["reference"], {"reference_id": "abc", "source_size": [1280, 720],
                                               "shown_size": [512, 288]})

    def test_a_pair_is_drawn_at_the_reference_aspect(self):
        self.assertEqual(output_size(512, 1280 / 720), (512, 288))
        camera = free_camera(HERO, 512 / 288)
        self.assertAlmostEqual(camera["projection_matrix"][1][1] / camera["projection_matrix"][0][0], 512 / 288)


if __name__ == "__main__":
    unittest.main()
