# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The game-ready check without Blender (doc/angelo/game_ready_check.rst): the tool's arguments, its place among the
deferred read tools, that nothing of it loads until it is called, and its measurements on meshes whose answers are
known, with a brute-force NumPy reference standing in for the native kernels (``_bpy._angelo_reduce``). The native
kernels are checked against the same reference in the compiled app (test_blender_game_ready_check)."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import math
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import game_ready_collider as C, game_ready_metrics as G  # noqa: E402
from angelo_agent.game_ready_spec import CHECK, GAME_READY_NAMES, GAME_READY_TOOLS, validate_game_ready  # noqa: E402

H1 = "scn:" + "a" * 32 + ":1"
H2 = "scn:" + "a" * 32 + ":2"
H3 = "scn:" + "a" * 32 + ":3"


def closest_on_triangles(point, a, b, c):
    """Closest points on many triangles to one point (Ericson, Real-Time Collision Detection 5.1.5)."""
    ab, ac, ap = b - a, c - a, point - a
    d1, d2 = (ab * ap).sum(1), (ac * ap).sum(1)
    bp = point - b
    d3, d4 = (ab * bp).sum(1), (ac * bp).sum(1)
    cp = point - c
    d5, d6 = (ab * cp).sum(1), (ac * cp).sum(1)
    vc, vb, va = d1 * d4 - d3 * d2, d5 * d2 - d1 * d6, d3 * d6 - d5 * d4
    with np.errstate(divide="ignore", invalid="ignore"):
        denom = 1.0 / (va + vb + vc)
        v, w = vb * denom, vc * denom
        out = a + ab * v[:, None] + ac * w[:, None]
        edge_ab = (vc <= 0) & (d1 >= 0) & (d3 <= 0)
        out = np.where(edge_ab[:, None], a + ab * (d1 / (d1 - d3))[:, None], out)
        edge_ac = (vb <= 0) & (d2 >= 0) & (d6 <= 0)
        out = np.where(edge_ac[:, None], a + ac * (d2 / (d2 - d6))[:, None], out)
        edge_bc = (va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0)
        t = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        out = np.where(edge_bc[:, None], b + (c - b) * t[:, None], out)
    out = np.where(((d1 <= 0) & (d2 <= 0))[:, None], a, out)
    out = np.where(((d3 >= 0) & (d4 <= d3))[:, None], b, out)
    out = np.where(((d6 >= 0) & (d5 <= d6))[:, None], c, out)
    return out


class ReferenceKernels:
    """Brute force: every point against every triangle, every pixel centre against every triangle."""

    MAX_ATTRIBUTES = 32

    def surface(self, positions, tris):
        return np.asarray(positions, dtype=np.float64).reshape(-1, 3), np.asarray(tris, dtype=np.int64).reshape(-1, 3)

    def nearest(self, surface, points):
        positions, tris = surface
        points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        a, b, c = positions[tris[:, 0]], positions[tris[:, 1]], positions[tris[:, 2]]
        distances = np.empty(len(points), dtype=np.float32)
        index = np.empty(len(points), dtype=np.int32)
        closest = np.empty((len(points), 3), dtype=np.float32)
        for i, p in enumerate(points):
            q = closest_on_triangles(p, a, b, c)
            d = np.linalg.norm(q - p, axis=1)
            k = int(np.argmin(d))
            distances[i], index[i], closest[i] = d[k], k, q[k]
        return distances.tobytes(), index.tobytes(), closest.tobytes()

    def rasterize(self, positions, tris, views, resolution):
        positions = np.asarray(positions, dtype=np.float64).reshape(-1, 3)
        tris = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
        views = np.asarray(views, dtype=np.float64).reshape(-1, 8)
        centres = (np.stack(np.meshgrid(np.arange(resolution), np.arange(resolution)), -1).reshape(-1, 2) + 0.5)
        out = np.zeros((len(views), resolution * resolution), dtype=np.uint8)
        for v, row in enumerate(views):
            xy = np.stack([positions @ row[0:3] + row[3], positions @ row[4:7] + row[7]], 1)
            for tri in tris:
                a, b, c = xy[tri]
                area = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
                if area == 0:
                    continue
                if area < 0:
                    b, c = c, b
                inside = np.ones(len(centres), dtype=bool)
                for p, q in ((a, b), (b, c), (c, a)):
                    e = (q[0] - p[0]) * (centres[:, 1] - p[1]) - (q[1] - p[1]) * (centres[:, 0] - p[0])
                    dy, dx = q[1] - p[1], q[0] - p[0]
                    own = dy < 0 or (dy == 0 and dx < 0)
                    inside &= (e > 0) | ((e == 0) & own)
                out[v] = np.minimum(out[v].astype(np.int32) + inside, 255)
        return out.tobytes()


K = ReferenceKernels()


def grid(n, size=1.0, z=0.0, uv_scale=1.0):
    """An n x n quad grid of 2n² triangles in the XY plane, UVs = position / size * uv_scale."""
    xs = np.linspace(0.0, size, n + 1)
    positions = np.array([(x, y, z) for y in xs for x in xs], dtype=np.float32)
    tris = []
    for j in range(n):
        for i in range(n):
            a, b, c, d = j * (n + 1) + i, j * (n + 1) + i + 1, (j + 1) * (n + 1) + i + 1, (j + 1) * (n + 1) + i
            tris += [(a, b, c), (a, c, d)]
    tris = np.array(tris, dtype=np.uint32)
    uv = positions[tris][:, :, :2] / size * uv_scale
    normals = np.tile(np.array([0.0, 0.0, 1.0], dtype=np.float32), (len(tris), 3, 1))
    return G.Geometry(positions, tris, uv, normals)


def cube(half=1.0, center=(0.0, 0.0, 0.0)):
    """A closed, outward-wound cube of 12 triangles."""
    c = np.asarray(center, dtype=np.float32)
    positions = np.array([(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], dtype=np.float32) * half + c
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    tris = [t for a, b, cc, d in quads for t in ((a, b, cc), (a, cc, d))]
    return G.Geometry(positions, np.array(tris, dtype=np.uint32))


class ArgumentTests(unittest.TestCase):
    def test_the_mesh_alone_is_enough_and_gets_the_defaults(self):
        self.assertEqual(validate_game_ready(CHECK, {"object": H1}), {
            "object": H1, "lods": [], "simplify": None, "switch_distances": None, "resolution": 1080,
            "fov_degrees": 60.0, "texture_size": 1024, "collider": None})

    def test_ratios_run_largest_first_once_each_and_both_methods_by_default(self):
        out = validate_game_ready(CHECK, {"object": H1, "simplify": {"ratios": [0.1, 0.5, 0.25, 0.5]}})
        self.assertEqual(out["simplify"]["ratios"], [0.5, 0.25, 0.1])
        self.assertEqual(out["simplify"]["methods"], ["MESHOPT", "DECIMATE"])
        self.assertEqual(validate_game_ready(CHECK, {"object": H1, "simplify": {
            "ratios": [0.5], "methods": ["DECIMATE"]}})["simplify"]["methods"], ["DECIMATE"])

    def test_one_switch_distance_per_lod(self):
        args = {"object": H1, "lods": [H2], "simplify": {"ratios": [0.5, 0.25]}, "switch_distances": [5, 10, 20]}
        self.assertEqual(validate_game_ready(CHECK, args)["switch_distances"], [5.0, 10.0, 20.0])
        with self.assertRaises(ValueError):
            validate_game_ready(CHECK, dict(args, switch_distances=[5, 10]))

    def test_refusals(self):
        for bad in ({}, {"object": "Cube"}, {"object": H1, "lods": [H1]}, {"object": H1, "lods": [H2, H2]},
                    {"object": H1, "collider": H1}, {"object": H1, "simplify": {"ratios": [1.0]}},
                    {"object": H1, "simplify": {"ratios": [0]}}, {"object": H1, "simplify": {"ratios": []}},
                    {"object": H1, "simplify": {"ratios": [0.5], "methods": ["QEM"]}},
                    {"object": H1, "simplify": {"ratios": [0.5], "lock_border": 1}},
                    {"object": H1, "simplify": {"ratios": [0.5], "uv_weight": -1}},
                    {"object": H1, "simplify": {"ratios": [0.5], "extra": 1}},
                    {"object": H1, "resolution": True}, {"object": H1, "fov_degrees": float("nan")},
                    {"object": H1, "switch_distances": [0]}, {"object": H1, "unknown": 1}):
            with self.assertRaises(ValueError, msg=repr(bad)):
                validate_game_ready(CHECK, bad)

    def test_the_schema_and_the_validator_name_the_same_fields(self):
        schema = GAME_READY_TOOLS[0]["inputSchema"]
        self.assertEqual(schema["required"], ["object"])
        out = validate_game_ready(CHECK, {"object": H1, "lods": [H2], "collider": H3, "resolution": 720,
                                          "fov_degrees": 90, "texture_size": 2048, "switch_distances": [3, 6],
                                          "simplify": {"ratios": [0.5], "max_error": 0.1, "lock_border": True,
                                                       "uv_weight": 2, "normal_weight": 0, "skin_weight": 3}})
        self.assertEqual(set(out), set(schema["properties"]))
        self.assertEqual(set(out["simplify"]), set(schema["properties"]["simplify"]["properties"]))
        self.assertTrue(GAME_READY_TOOLS[0]["annotations"]["readOnlyHint"])


class RegistrationTests(unittest.TestCase):
    def test_a_deferred_read_at_the_mcp_read_level(self):
        source = {name: (ADDON / name).read_text(encoding="utf-8") for name in (
            "plan_kinds.py", "external_clients.py", "tool_specs.py", "blender_tools.py", "chat_titles.py",
            "activity_text.py", "codex_text.py")}
        self.assertIn("GAME_READY_NAMES", source["plan_kinds.py"])
        self.assertRegex(source["external_clients.py"], r'READ_TOOLS = frozenset\(\{[^}]*"angelo_game_ready_check"')
        self.assertIn("TOOLS.extend(GAME_READY_TOOLS)", source["tool_specs.py"])
        self.assertIn('"angelo_game_ready_check"', source["chat_titles.py"])
        self.assertIn('"angelo_game_ready_check": "check"', source["activity_text.py"])
        self.assertIn('("the game-ready check", GAME_READY_NAMES)', source["codex_text.py"])
        self.assertEqual(GAME_READY_NAMES, {CHECK})

    def test_nothing_of_it_loads_until_it_is_called(self):
        """Only blender_tools names the host module, and only inside the call; the measuring modules are imported by
        the host module alone, so an add-on that never runs the check never imports them, NumPy included."""
        heavy = {"game_ready_host", "game_ready_mesh", "game_ready_metrics", "game_ready_collider"}
        for path in ADDON.glob("*.py"):
            if path.stem in heavy:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            top = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
            for node in top:
                names = [a.name for a in node.names] + ([node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                self.assertFalse(any(n.split(".")[-1] in heavy for n in names), "%s imports %s at load" % (
                    path.name, names))
            if path.name != "blender_tools.py":
                self.assertFalse(any(h in path.read_text(encoding="utf-8") for h in (
                    "import game_ready_host", "import game_ready_mesh", "import game_ready_metrics",
                    "import game_ready_collider")),
                                 path.name)
        self.assertIn("from . import game_ready_host", (ADDON / "blender_tools.py").read_text(encoding="utf-8"))
        # The spec imports nothing heavy: plan_kinds and tool_specs import it on every start.
        spec = ast.parse((ADDON / "game_ready_spec.py").read_text(encoding="utf-8"))
        imported = {a.name for n in ast.walk(spec) if isinstance(n, ast.Import) for a in n.names}
        imported |= {n.module for n in ast.walk(spec) if isinstance(n, ast.ImportFrom)}
        self.assertLessEqual(imported, {"math", "re", "scene_spec"})


class HausdorffTests(unittest.TestCase):
    def test_the_same_surface_is_zero_and_a_parallel_one_its_offset(self):
        source = grid(4)
        same = G.Comparison(source, G.Samples(source, count=256), grid(4), K, count=256)
        facts, worst = same.hausdorff(math.sqrt(2.0))
        self.assertAlmostEqual(worst, 0.0, places=6)
        self.assertEqual(facts["max_m"], 0.0)
        lifted = G.Comparison(source, G.Samples(source, count=256), grid(2, z=0.25), K, count=256)
        facts, worst = lifted.hausdorff(math.sqrt(2.0))
        self.assertAlmostEqual(worst, 0.25, places=5)
        self.assertAlmostEqual(facts["mean_m"], 0.25, places=4)
        self.assertAlmostEqual(facts["max_of_bounds"], 0.25 / math.sqrt(2.0), places=3)
        self.assertEqual(facts["points"], [9 + 256, 25 + 256])   # The LOD is a 2x2 grid, the source 4x4.

    def test_both_directions_count(self):
        """A LOD that lost half the surface is close to the source from its side, far from the source's side."""
        source = grid(4)
        half = grid(4)
        keep = half.corners()[:, :, 0].max(1) <= 0.5
        half = G.Geometry(half.positions, half.tris[keep], half.corner_uv[keep], half.corner_normal[keep])
        comparison = G.Comparison(source, G.Samples(source, count=512), half, K, count=256)
        facts, worst = comparison.hausdorff(1.0)
        self.assertEqual(facts["lod_to_source_max_m"], 0.0)
        self.assertAlmostEqual(facts["source_to_lod_max_m"], 0.5, places=4)
        self.assertAlmostEqual(worst, 0.5, places=4)

    def test_samples_are_deterministic_and_cover_every_vertex(self):
        source = grid(3)
        a, b = G.Samples(source, count=100), G.Samples(source, count=100)
        self.assertTrue(np.array_equal(a.points, b.points))
        self.assertEqual(a.vertex_count, 16)
        self.assertEqual(len(a.points), 116)


class AttributeTests(unittest.TestCase):
    def test_normals_uvs_and_weights_compare_at_the_nearest_point(self):
        source = grid(2)
        source.weights = np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (len(source.positions), 1))
        tilted = grid(2)
        tilted.corner_normal = np.tile(np.array([0.0, math.sin(0.1), math.cos(0.1)], dtype=np.float32),
                                       (len(tilted.tris), 3, 1))
        tilted.corner_uv = tilted.corner_uv + np.float32(0.01)
        tilted.weights = np.tile(np.array([[0.75, 0.25]], dtype=np.float32), (len(tilted.positions), 1))
        facts = G.Comparison(source, G.Samples(source, count=64), tilted, K, count=64).attributes()
        self.assertAlmostEqual(facts["normals"]["max"], math.degrees(0.1), places=2)
        self.assertAlmostEqual(facts["uv_shift"]["p50"], 0.01 * math.sqrt(2.0), places=4)
        self.assertAlmostEqual(facts["skin_weights"]["max"], 0.25, places=5)

    def test_skin_weights_match_groups_by_name_not_column(self):
        source, lod = grid(2), grid(2)
        n = len(source.positions)
        source.weights = np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (n, 1))
        source.weight_names = ["Hip", "Knee"]
        lod.weights = np.tile(np.array([[0.0, 1.0]], dtype=np.float32), (n, 1))   # The same weights, columns swapped.
        lod.weight_names = ["Knee", "Hip"]
        facts = G.Comparison(source, G.Samples(source, count=32), lod, K, count=32).attributes()
        self.assertAlmostEqual(facts["skin_weights"]["max"], 0.0, places=6)
        lod.weight_names = ["Knee", "Spine"]   # A group the source lacks weighs 0 there: half the influence moved.
        facts = G.Comparison(source, G.Samples(source, count=32), lod, K, count=32).attributes()
        self.assertAlmostEqual(facts["skin_weights"]["max"], 1.0, places=5)
        self.assertEqual(facts["skin_weights"]["groups"], 3)
        self.assertLessEqual(set(facts), set(G.glossary(1080, 60, 1024)))


class SilhouetteTests(unittest.TestCase):
    def test_the_same_mesh_is_one_and_a_smaller_one_its_area(self):
        source = cube()
        same, smaller = G.silhouettes(source, [cube(), cube(half=0.5)], K, resolution=48)
        self.assertEqual(same["iou_min"], 1.0)
        # A cube seen along a diagonal is a hexagon; half the size covers a quarter of it.
        self.assertAlmostEqual(smaller["iou_mean"], 0.25, delta=0.03)
        self.assertIn("7 orthographic views", G.glossary(1080, 60, 1024)["silhouette"])

    def test_the_views_are_distinct_directions(self):
        """An orthographic view and its opposite see the same outline: no direction is the reverse of another."""
        directions = np.array(G.VIEW_DIRECTIONS, dtype=float)
        directions /= np.linalg.norm(directions, axis=1, keepdims=True)
        dots = directions @ directions.T
        np.fill_diagonal(dots, 0.0)
        self.assertEqual(len(directions), 7)
        self.assertLess(np.abs(dots).max(), 0.99)

    def test_shared_edges_never_cover_a_pixel_twice(self):
        """The top-left rule: a square of two triangles whose diagonal runs through pixel centres counts once."""
        square = grid(1, size=4.0)
        view = np.array([[1, 0, 0, 0, 0, 1, 0, 0]], dtype=np.float32)
        counts = np.frombuffer(K.rasterize(square.positions, square.tris, view, 4), dtype=np.uint8)
        self.assertEqual(int(counts.max()), 1)
        self.assertEqual(int((counts == 1).sum()), 16)


class GlossaryTests(unittest.TestCase):
    def test_every_fact_says_what_it_measured_with_the_request_s_settings(self):
        glossary = G.glossary(720, 90, 2048, samples=1000)
        self.assertIn("720 px tall view with a 90 degree", glossary["pixel_error"])
        self.assertIn("at 2048 px", glossary["uv"])
        self.assertIn("1000 area-weighted points", glossary["hausdorff"])
        self.assertTrue(all(isinstance(v, str) and len(v) > 30 for v in glossary.values()))


class PixelErrorTests(unittest.TestCase):
    def test_pixels_follow_the_projection(self):
        facts = G.pixel_error(0.01, [10.0], 1080, 60.0)
        expected = 0.01 * 1080 / (2 * math.tan(math.radians(30)) * 10.0)
        self.assertAlmostEqual(facts["at_switch"][0]["px"], expected, places=3)
        self.assertAlmostEqual(facts["distance_at_1px_m"], expected * 10.0, places=2)
        self.assertNotIn("at_switch", G.pixel_error(0.01, None, 1080, 60.0))


class UvTests(unittest.TestCase):
    def test_an_undistorted_square(self):
        facts = G.uv_facts(grid(4, size=2.0), 1024, K, resolution=64)
        self.assertAlmostEqual(facts["stretch"]["max"], 1.0, places=4)
        self.assertAlmostEqual(facts["texel_density"]["p50"], 512.0, places=2)   # 1 UV unit over 2 m at 1024 px.
        self.assertEqual(facts["texel_density"]["spread"], 0.0)
        self.assertEqual(facts["overlap_of_used_area"], 0.0)
        self.assertEqual(facts["seam_edges"], 0)
        self.assertEqual(facts["mirrored_tris"], 0)

    def test_stretch_is_the_singular_value_ratio(self):
        square = grid(2)
        square.corner_uv = square.corner_uv * np.array([2.0, 1.0], dtype=np.float32)
        self.assertAlmostEqual(G.uv_facts(square, 1024, K, resolution=32)["stretch"]["p50"], 2.0, places=4)

    def test_stacked_islands_overlap_and_mirrors_and_seams_count(self):
        square = grid(2)
        right = square.corners()[:, :, 0].min(1) >= 0.5
        uv = square.corner_uv.copy()
        uv[right, :, 0] = 1.0 - uv[right, :, 0]   # Mirror the right half onto the left half.
        square.corner_uv = uv
        facts = G.uv_facts(square, 1024, K, resolution=64)
        self.assertEqual(facts["mirrored_tris"], int(right.sum()))
        self.assertGreater(facts["overlap_of_used_area"], 0.9)
        self.assertEqual(facts["seam_edges"], 0)   # Mirrored at x = 0.5, where both sides meet at u = 0.5.
        uv[right, :, 1] += 0.5   # Now the halves meet at different UVs: a seam 1 m long.
        facts = G.uv_facts(square, 1024, K, resolution=64)
        self.assertEqual(facts["seam_edges"], 2)
        self.assertAlmostEqual(facts["seam_length_m"], 1.0, places=5)

    def test_a_split_vertex_buffer_keeps_the_same_seams(self):
        """vertex_ids make a welded buffer's split vertices count as the vertices they came from."""
        square = grid(2)
        split_positions = square.positions[square.tris.reshape(-1)]
        split = G.Geometry(split_positions, np.arange(len(split_positions), dtype=np.uint32).reshape(-1, 3),
                           square.corner_uv, square.corner_normal, vertex_ids=square.tris.reshape(-1))
        self.assertEqual(G.topology(split)["open_edges"], G.topology(square)["open_edges"])
        self.assertEqual(G.uv_facts(split, 1024, K, resolution=16)["seam_edges"], 0)


class TopologyTests(unittest.TestCase):
    def test_open_and_non_manifold_edges(self):
        self.assertEqual(G.topology(grid(1))["open_edges"], 4)
        self.assertEqual(G.topology(cube())["open_edges"], 0)
        fin = cube()
        extra = np.array([[0, 1, 7]], dtype=np.uint32)   # A third triangle on the edge 0-1.
        fin = G.Geometry(fin.positions, np.concatenate([fin.tris, extra]))
        self.assertGreaterEqual(G.topology(fin)["non_manifold_edges"], 1)


class ColliderTests(unittest.TestCase):
    def test_a_box_around_the_mesh_has_its_excess_and_leaves_nothing_out(self):
        mesh = cube()
        hull = cube()
        facts = C.collider_fit(mesh, hull, cube(half=1.1), K, diagonal=2 * math.sqrt(3), count=512)
        self.assertTrue(facts["closed"])
        self.assertTrue(facts["convex"])
        self.assertAlmostEqual(facts["excess_over_hull"], 1.1 ** 3 - 1.0, places=3)
        self.assertEqual(facts["mesh_outside"], 0.0)
        self.assertAlmostEqual(facts["hull_distance"]["max_m"], 0.1 * math.sqrt(3.0), places=4)   # Corner to corner.

    def test_a_small_box_leaves_points_out_and_a_concave_collider_uses_the_winding_number(self):
        facts = C.collider_fit(cube(), cube(), cube(half=0.5), K, diagonal=2 * math.sqrt(3), count=512)
        self.assertGreater(facts["mesh_outside"], 0.5)
        self.assertAlmostEqual(facts["mesh_outside_max_m"], math.sqrt(3) * 0.5, places=3)
        # Two separate boxes: closed but not convex, so the inside test is the winding number.
        a, b = cube(half=0.6, center=(-0.5, 0, 0)), cube(half=0.6, center=(0.5, 0, 0))
        pair = G.Geometry(np.concatenate([a.positions, b.positions]),
                          np.concatenate([a.tris, b.tris + len(a.positions)]))
        facts = C.collider_fit(cube(half=0.5), cube(half=0.5), pair, K, diagonal=math.sqrt(3), count=512)
        self.assertFalse(facts["convex"])
        self.assertEqual(facts["mesh_outside"], 0.0)
        self.assertIn("winding", facts["inside_test"])

    def test_faces_wound_either_way_still_read_convex_with_its_volume(self):
        box = cube(half=1.1)
        flipped = box.tris.copy()
        flipped[::3] = flipped[::3, ::-1]   # Every third face turned inward.
        box = G.Geometry(box.positions, flipped)
        facts = C.collider_fit(cube(), cube(), box, K, diagonal=2 * math.sqrt(3), count=256)
        self.assertTrue(facts["convex"])
        self.assertFalse(facts["consistent_winding"])
        self.assertAlmostEqual(facts["volume_m3"], 2.2 ** 3, delta=0.01)   # Four significant digits.
        self.assertEqual(facts["mesh_outside"], 0.0)

    def test_a_large_non_convex_collider_skips_the_inside_test_and_says_why(self):
        a, b = cube(half=0.6, center=(-0.5, 0, 0)), cube(half=0.6, center=(0.5, 0, 0))
        pair = G.Geometry(np.concatenate([a.positions, b.positions]), np.concatenate([a.tris, b.tris + len(a.positions)]))
        saved = C.MAX_WINDING_WORK
        C.MAX_WINDING_WORK = 24 * 100   # Room for 100 points: below MIN_WINDING_POINTS.
        try:
            facts = C.collider_fit(cube(half=0.5), cube(half=0.5), pair, K, diagonal=math.sqrt(3), count=256)
        finally:
            C.MAX_WINDING_WORK = saved
        self.assertNotIn("mesh_outside", facts)
        self.assertIn("too many triangles", facts["inside_test"])

    def test_a_collider_without_faces_encloses_nothing(self):
        nothing = G.Geometry(np.zeros((4, 3), dtype=np.float32), np.zeros((0, 3), dtype=np.uint32))
        facts = C.collider_fit(cube(), cube(), nothing, K, diagonal=2.0, count=64)
        self.assertFalse(facts["closed"])
        self.assertFalse(facts["convex"])
        self.assertNotIn("mesh_outside", facts)
        self.assertNotIn("excess_over_hull", facts)
        self.assertIn("no faces", facts["inside_test"])

    def test_loose_vertices_do_not_spoil_convexity(self):
        box = cube(half=1.1)
        stray = G.Geometry(np.concatenate([box.positions, [[9.0, 9.0, 9.0]]]).astype(np.float32), box.tris)
        self.assertTrue(C.collider_fit(cube(), cube(), stray, K, diagonal=2 * math.sqrt(3), count=64)["convex"])

    def test_an_open_collider_is_said_to_be_open(self):
        facts = C.collider_fit(cube(), cube(), grid(2), K, diagonal=2.0, count=256)
        self.assertFalse(facts["closed"])
        self.assertNotIn("mesh_outside", facts)
        self.assertIn("open", facts["inside_test"])


if __name__ == "__main__":
    unittest.main()
