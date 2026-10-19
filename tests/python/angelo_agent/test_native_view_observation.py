# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host contract tests for angelo_view: schema, framing, PNG, cache and delivery.

No Blender, provider or network access. The native GUI fixture
test_blender_view_observation.py proves actual pixels and artist-state safety.
"""

import json
import math
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import uuid
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import view_cache
from angelo_agent.view_cache import ViewCameraStore, ViewImageCache, image_key
from angelo_agent.view_framing import (AXIS_DIRECTIONS, MIN_HALF_WIDTH, axis_basis, basis_from_view_matrix,
                                        camera_matrix, orthographic_fit, output_size, view_matrix)
from angelo_agent.view_png import encode_png
from angelo_agent.view_spec import VIEW_NAMES, framing_kind, validate_view
from angelo_agent.tool_specs import (BOUNDED_MUTATIONS, COLLABORATOR_TOOLS, TOOL_MAP, dynamic_tools,
                                      native_plan_schema, validate_call)
from angelo_agent.routing import TOOLSET_VERSION
from angelo_agent.controller import Controller
from angelo_agent.providers import ClaudeProvider, RpcError, _ClaudeSculptEvidence, tool_content

HANDLE = "scn:" + "a" * 32 + ":7"
OTHER = "scn:" + "a" * 32 + ":8"


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def chunks(png):
    offset, found = 8, []
    while offset < len(png):
        length = struct.unpack(">I", png[offset:offset + 4])[0]
        kind, data = png[offset + 4:offset + 8], png[offset + 8:offset + 8 + length]
        crc = struct.unpack(">I", png[offset + 8 + length:offset + 12 + length])[0]
        found.append((kind, data, crc == zlib.crc32(kind + data) & 0xFFFFFFFF))
        offset += 12 + length
    return found


def camera(offset=0.0):
    identity = [[1.0, 0, 0, offset], [0, 1.0, 0, 0], [0, 0, 1.0, 0], [0, 0, 0, 1.0]]
    return {"view_matrix": identity, "projection_matrix": identity}


def key(**changes):
    values = dict(scene_uid=1, epoch="e", view_layer=5, frame=1, viewport=(False,), targets=[HANDLE],
                  revisions=["r" * 64], shading="SOLID", size=(512, 512), camera=camera())
    values.update(changes)
    return image_key(**values)


class ViewContractTests(unittest.TestCase):
    def test_registered_read_only_collaborator_tool_and_version(self):
        self.assertEqual(VIEW_NAMES, {"angelo_view"})
        tool = TOOL_MAP["angelo_view"]
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertIn("angelo_view", COLLABORATOR_TOOLS)
        self.assertNotIn("angelo_view", BOUNDED_MUTATIONS)
        self.assertGreaterEqual(TOOLSET_VERSION, 13)
        self.assertIn("angelo_view", {t["name"] for t in dynamic_tools(COLLABORATOR_TOOLS)})
        variants = native_plan_schema()["properties"]["operations"]["items"]
        self.assertIn("angelo_view", json.dumps(variants))
        # 1,185 before the free camera item and its note (view_spec.CAMERA_ITEM, VIEWS_NOTE): 1,802 with them; 2,006
        # with a pair's compare (PAIR, OVERLAY, OUTLINE) and the MATCAP, CURVATURE and DEPTH looks.
        self.assertLess(len(json.dumps(tool["inputSchema"])), 2050, "Keep the view schema compact")

    def test_defaults_and_normalized_view_ids(self):
        view_id = str(uuid.uuid4()).upper()
        checked = validate_call("angelo_view", {"views": ["FRONT", view_id]})
        self.assertEqual(checked, {"targets": [], "views": ["FRONT", view_id.lower()], "shading": "SOLID",
                                   "resolution": 512})
        self.assertEqual(validate_call("angelo_view", checked), checked)
        self.assertEqual([framing_kind(v) for v in ["ISO", "FIT", "SCENE_CAMERA", view_id.lower()]],
                         ["AXIS", "FIT", "SCENE_CAMERA", "REUSE"])
        full = {"targets": [HANDLE, OTHER], "views": ["TOP", "FIT", "SCENE_CAMERA"], "shading": "MATERIAL",
                "resolution": 1024}
        self.assertEqual(validate_view("angelo_view", full), full)

    def test_bounds_and_malformed_arguments_are_rejected(self):
        bad = [
            {}, {"views": []}, {"views": ["FRONT", "BACK", "TOP", "ISO"]}, {"views": ["FRONT", "FRONT"]},
            {"views": ["SIDE"]}, {"views": [7]}, {"views": "FRONT"}, {"views": ["../image.png"]},
            {"views": ["FRONT"], "targets": ["Cube"]}, {"views": ["FRONT"], "targets": [HANDLE, HANDLE]},
            {"views": ["FRONT"], "targets": ["scn:" + "a" * 32 + ":" + str(i + 1) for i in range(17)]},
            {"views": ["FRONT"], "targets": HANDLE}, {"views": ["FRONT"], "resolution": 500},
            {"views": ["FRONT"], "resolution": 2048}, {"views": ["FRONT"], "resolution": True},
            {"views": ["FRONT"], "resolution": 512.0}, {"views": ["FRONT"], "shading": "RENDERED"},
            {"views": ["FRONT"], "isolate": True}, {"views": ["FRONT"], "path": "/tmp/x.png"},
        ]
        for args in bad:
            with self.subTest(args=args), self.assertRaises(ValueError):
                validate_call("angelo_view", args)
        with self.assertRaises(ValueError):
            validate_view("angelo_sculpt_view", {"views": ["FRONT"]})


class FramingTests(unittest.TestCase):
    def test_axis_bases_are_rigid_and_match_blender_numpad_views(self):
        expected = {"FRONT": ((1, 0, 0), (0, 0, 1)), "BACK": ((-1, 0, 0), (0, 0, 1)),
                    "RIGHT": ((0, 1, 0), (0, 0, 1)), "LEFT": ((0, -1, 0), (0, 0, 1)),
                    "TOP": ((1, 0, 0), (0, 1, 0)), "BOTTOM": ((1, 0, 0), (0, -1, 0))}
        for name, direction in AXIS_DIRECTIONS.items():
            right, up, forward = axis_basis(direction)
            with self.subTest(axis=name):
                for a, b in ((right, up), (up, forward), (right, forward)):
                    self.assertAlmostEqual(dot(a, b), 0, places=9)
                for vector in (right, up, forward):
                    self.assertAlmostEqual(dot(vector, vector), 1, places=9)
                for a, b in zip(cross(right, up), forward):
                    self.assertAlmostEqual(a, b, places=9)
                if name in expected:
                    for got, want in zip((right, up), expected[name]):
                        self.assertEqual([round(v, 9) + 0 for v in got], [float(v) for v in want])

    def test_fit_contains_every_point_with_margin_and_clip_range(self):
        points = [(x, y, z) for x in (-1, 3) for y in (-.5, .5) for z in (0, 2)]
        for name, direction in AXIS_DIRECTIONS.items():
            basis = axis_basis(direction)
            center, half, distance, depth = orthographic_fit(points, basis)
            world = camera_matrix(center, basis, distance)
            inverse = view_matrix(world)
            with self.subTest(axis=name):
                for point in points:
                    local = [dot(inverse[row][:3], point) + inverse[row][3] for row in range(3)]
                    self.assertLessEqual(abs(local[0]), half / 1.15 + 1e-9)
                    self.assertLessEqual(abs(local[1]), half / 1.15 + 1e-9)
                    self.assertGreaterEqual(-local[2], half - 1e-9)  # nearer geometry is cut away
                    self.assertLessEqual(-local[2], depth - half + 1e-9)
                product = [[sum(world[r][k] * inverse[k][c] for k in range(4)) for c in range(4)] for r in range(4)]
                for r in range(4):
                    for c in range(4):
                        self.assertAlmostEqual(product[r][c], float(r == c), places=9)

    def test_degenerate_invalid_and_viewport_framing(self):
        _center, half, _distance, _depth = orthographic_fit([(1, 2, 3)], axis_basis((0, -1, 0)))
        self.assertEqual(half, MIN_HALF_WIDTH)
        for points in ([], [(0, 0, math.nan)], [(0, 0)]):
            with self.subTest(points=points), self.assertRaises(ValueError):
                orthographic_fit(points, axis_basis((0, -1, 0)))
        with self.assertRaises(ValueError):
            axis_basis((0, 0, 0))
        world = camera_matrix((0, 0, 0), axis_basis(AXIS_DIRECTIONS["ISO"]), 5)
        right, up, forward = basis_from_view_matrix(view_matrix(world))
        for got, want in zip((right, up, forward), axis_basis(AXIS_DIRECTIONS["ISO"])):
            for a, b in zip(got, want):
                self.assertAlmostEqual(a, b, places=9)
        self.assertEqual(output_size(512, 16 / 9), (512, 288))
        self.assertEqual(output_size(512, 9 / 16), (288, 512))
        self.assertEqual(output_size(256, 1.0), (256, 256))
        for aspect in (0, -1, math.inf, math.nan):
            with self.assertRaises(ValueError):
                output_size(512, aspect)


class PngTests(unittest.TestCase):
    def test_valid_opaque_rgb_png_with_flipped_rows(self):
        width, height = 3, 2
        rgba = bytes(range(width * height * 4))
        png = encode_png(rgba, width, height)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        found = chunks(png)
        self.assertEqual([kind for kind, _data, _ok in found], [b"IHDR", b"IDAT", b"IEND"])
        self.assertTrue(all(ok for _kind, _data, ok in found))
        self.assertEqual(struct.unpack(">IIBBBBB", found[0][1]), (3, 2, 8, 2, 0, 0, 0))
        rows = zlib.decompress(found[1][1])
        top, bottom = rows[:1 + width * 3], rows[1 + width * 3:]
        self.assertEqual(top[0], 0)
        self.assertEqual(list(top[1:]), [12, 13, 14, 16, 17, 18, 20, 21, 22])  # GPU row 1 becomes PNG row 0
        self.assertEqual(list(bottom[1:]), [0, 1, 2, 4, 5, 6, 8, 9, 10])
        upright = zlib.decompress(chunks(encode_png(rgba, width, height, bottom_up=False))[1][1])
        self.assertEqual(list(upright[1:10]), [0, 1, 2, 4, 5, 6, 8, 9, 10])

    def test_rejects_inconsistent_or_unbounded_buffers(self):
        for args in ((bytes(15), 2, 2), (bytes(16), 0, 4), (bytes(16), True, 4), (bytes(4), 4097, 1)):
            with self.subTest(size=args[1:]), self.assertRaises(ValueError):
                encode_png(*args)


class CacheTests(unittest.TestCase):
    def setUp(self):
        view_cache.clear()

    def tearDown(self):
        view_cache.clear()

    def test_keys_cover_every_pixel_input_and_refuse_unproven_targets(self):
        self.assertEqual(key(), key())
        for change in (dict(revisions=["s" * 64]), dict(camera=camera(.5)), dict(shading="MATERIAL"),
                       dict(size=(256, 256)), dict(epoch="f"), dict(frame=2), dict(viewport=(True,)),
                       dict(view_layer=6), dict(scene_uid=2), dict(targets=[OTHER])):
            with self.subTest(change=change):
                self.assertNotEqual(key(**change), key())
        self.assertIsNone(key(revisions=[None]))
        self.assertIsNone(key(targets=[HANDLE, OTHER], revisions=["r" * 64]))
        self.assertIsNotNone(key(targets=[], revisions=[]))

    def test_identical_requests_coalesce_and_bounds_hold(self):
        cache = ViewImageCache(limit=2, byte_limit=10)
        stored = camera()
        self.assertTrue(cache.put(key(), b"1234", 4, 4, stored))
        hit = cache.get(key())
        self.assertEqual(hit["png"], b"1234")
        hit["camera"]["view_matrix"][0][0] = 99
        self.assertEqual(cache.get(key())["camera"]["view_matrix"][0][0], 1.0)
        self.assertIsNone(cache.get(key(frame=3)))
        self.assertIsNone(cache.get(None))
        cache.put(key(frame=2), b"5678", 4, 4, stored)
        cache.put(key(frame=3), b"90", 4, 4, stored)
        self.assertEqual(len(cache), 2)
        self.assertIsNone(cache.get(key()))  # least recently used is evicted
        self.assertFalse(cache.put(key(frame=4), b"x" * 11, 4, 4, stored))
        cache.put(key(frame=5), b"abcdefgh", 4, 4, stored)
        self.assertLessEqual(cache.stats()["bytes"], 10)
        stats = cache.stats()
        self.assertEqual((stats["hits"], stats["uncacheable"]), (2, 1))
        self.assertGreaterEqual(stats["evictions"], 2)

    def test_scene_changes_and_lifecycle_clear_invalidate(self):
        view_cache.images.put(key(), b"png", 1, 1, camera())
        view_cache.images.put(key(scene_uid=2), b"png", 1, 1, camera())
        self.assertEqual(view_cache.note_scene_change(1), 1)
        self.assertIsNone(view_cache.images.get(key()))
        self.assertIsNotNone(view_cache.images.get(key(scene_uid=2)))
        view_cache.cameras.remember("view", dict(camera(), epoch="e", scene_uid=1))
        view_cache.clear()
        self.assertEqual((len(view_cache.images), len(view_cache.cameras)), (0, 0))

    def test_reuse_cameras_are_bounded_scoped_and_copied(self):
        store = ViewCameraStore(limit=3)
        store.remember("baseline", dict(camera(), epoch="e", scene_uid=1))
        for index in range(5):
            store.resolve("baseline", epoch="e", scene_uid=1)  # an active baseline stays alive
            store.remember(str(index), dict(camera(index), epoch="e", scene_uid=1))
        self.assertEqual(len(store), 3)
        resolved = store.resolve("baseline", epoch="e", scene_uid=1)
        resolved["view_matrix"][0][3] = 42
        self.assertEqual(store.resolve("baseline", epoch="e", scene_uid=1)["view_matrix"][0][3], 0)
        with self.assertRaisesRegex(ValueError, "unknown or expired"):
            store.resolve("0", epoch="e", scene_uid=1)
        for epoch, scene in (("other", 1), ("e", 2)):
            with self.assertRaisesRegex(ValueError, "another document or scene"):
                store.resolve("baseline", epoch=epoch, scene_uid=scene)

    def test_lifecycle_hooks_clear_the_view_cache(self):
        root = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
        ui = (root / "ui.py").read_text(encoding="utf-8")
        for function in ("def document_load_pre", "def invalidate_workspace_state", "def unregister"):
            body = ui.split(function, 1)[1].split("\ndef ", 1)[0]
            with self.subTest(function=function):
                self.assertIn("view_cache.clear()", body)
        tools = (root / "blender_tools.py").read_text(encoding="utf-8")
        handler = tools.split("def track_scene_changes", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("view_cache.note_scene_change(scene.session_uid)", handler)


def view(views=("FRONT",), **extra):
    return {"tool": "angelo_view", "arguments": dict({"views": list(views)}, **extra)}


def record(host, name):
    value = host.references.new_observation(name)
    Path(value["path"]).write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    return value


class DeliveryTests(unittest.TestCase):
    def test_codex_receives_pixels_and_text_only_models_get_an_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            host = Controller(directory)
            image = record(host, "Front")
            content = tool_content(host, "angelo_view", {"success": True, "views": [image]})
            self.assertEqual(content[1]["type"], "inputImage")
            self.assertTrue(content[1]["imageUrl"].startswith("data:image/png;base64,"))
            controller = SimpleNamespace(references=host.references, models=[{"id": "text", "default": True, "images": False}],
                                         settings={}, state={"options": {}}, turn_options={})
            with self.assertRaisesRegex(ValueError, "image-capable model"):
                tool_content(controller, "angelo_view", {"views": [image]})
            image["path"] = "/etc/passwd"
            with self.assertRaises(ValueError):
                tool_content(host, "angelo_view", {"views": [image]})
            host.close()

    def test_claude_receives_view_pixels_and_refreshes_them_after_a_later_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            host = Controller(directory)
            old, fresh = record(host, "Old"), record(host, "Fresh")
            edit = {"tool": "angelo_set_transform", "arguments": {"object": "Cube", "location": [0, 1, 0]}}
            provider = ClaudeProvider(host, {})
            provider._turn = Mock(side_effect=[{"reply": "Look, then edit", "operations": [view(), edit], "verify": True},
                                               {"reply": "Verified", "operations": [], "verify": False}])
            provider._native_call = Mock(side_effect=[{"success": True, "views": [old]}, {"success": True},
                                                      {"success": True, "views": [fresh]}, {"objects": []}])
            self.assertEqual(provider.run(None, "Check the look", observation={}), "completed")
            self.assertEqual(provider._turn.call_args.kwargs["references"], [fresh])
            self.assertEqual(provider._native_call.call_args_list[2].args, ("angelo_view", view()["arguments"]))
            self.assertIn("stale_visual_evidence_discarded", provider._turn.call_args.args[1])
            host.close()

    def test_view_images_share_the_bounded_plan_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            host = Controller(directory)
            provider = ClaudeProvider(host, {})
            axes = ("FRONT", "BACK", "LEFT", "RIGHT", "TOP", "BOTTOM", "ISO")
            operations = [view((axis,), shading=shading) for shading in ("SOLID", "MATERIAL") for axis in axes]
            provider._turn = Mock(return_value={"reply": "Too many", "operations": operations, "verify": True})
            provider._native_call = Mock()
            with self.assertRaisesRegex(ValueError, "None of this plan's operations were run"):
                provider.run(None, "Look everywhere", observation={})
            provider._native_call.assert_not_called()
            self.assertIn("has 12 of 12 left", provider._turn.call_args.args[1])
            self.assertIn("this plan asks for 14 new ones", provider._turn.call_args.args[1])
            host.close()

    def test_refresh_never_merges_different_image_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            host = Controller(directory)
            evidence = _ClaudeSculptEvidence(host.references)
            evidence.admit(view(("FRONT", "ISO")), {"views": [record(host, "F"), record(host, "I")]})
            sculpt = {"tool": "angelo_sculpt_view", "arguments": {"object": "Cube", "views": ["FRONT"], "resolution": 512}}
            evidence.admit(sculpt, {"views": [record(host, "S")]})
            evidence.invalidate()
            operations = evidence.refresh_operations()
            self.assertEqual(sorted(op["tool"] for op in operations), ["angelo_sculpt_view", "angelo_view"])
            self.assertEqual(next(op for op in operations if op["tool"] == "angelo_view")["arguments"]["views"],
                             ["FRONT", "ISO"])
            host.close()


if __name__ == "__main__":
    unittest.main()
