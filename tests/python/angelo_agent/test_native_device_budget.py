# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The device budget check without Blender (doc/angelo/device_budget.rst): device profiles as data, what a written
.glb costs on a device (GPU texture memory as the device stores it, bones, influences, morph targets) and the
verdict per budget the profile sets.

The test files are built here from the glTF 2.0 layout, several unlike assets (a skinned figure with morphs, a static
prop with many materials, an instanced set), so no assertion depends on one character."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import device_budget  # noqa: E402

PROFILES = ROOT / "scripts/addons_core/angelo_agent/device_profiles"


def glb(document, binary=b""):
    text = json.dumps(document).encode("utf-8")
    text += b" " * (-len(text) % 4)
    binary += b"\0" * (-len(binary) % 4)
    chunks = struct.pack("<II", len(text), 0x4E4F534A) + text
    if binary:
        chunks += struct.pack("<II", len(binary), 0x004E4942) + binary
    return struct.pack("<III", 0x46546C67, 2, 12 + len(chunks)) + chunks


def png(width, height):
    """The first bytes of a PNG: enough for its size to be read (``web_budget.image_size``)."""
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height) + b"\0" * 5


class Asset:
    """A glTF document under construction; accessors hold counts only (the measure never reads their bytes)."""

    def __init__(self):
        self.doc = {"asset": {"version": "2.0"}, "scenes": [{"nodes": []}], "scene": 0, "nodes": [], "meshes": [],
                    "accessors": [], "bufferViews": [], "materials": [], "textures": [], "images": [], "skins": []}
        self.binary = b""

    def accessor(self, count, kind="VEC3"):
        self.doc["accessors"].append({"count": count, "type": kind, "componentType": 5126})
        return len(self.doc["accessors"]) - 1

    def image(self, width, height):
        blob = png(width, height)
        self.doc["bufferViews"].append({"buffer": 0, "byteOffset": len(self.binary), "byteLength": len(blob)})
        self.binary += blob + b"\0" * (-len(blob) % 4)
        self.doc["images"].append({"bufferView": len(self.doc["bufferViews"]) - 1, "mimeType": "image/png"})
        self.doc["textures"].append({"source": len(self.doc["images"]) - 1})
        return len(self.doc["textures"]) - 1

    def material(self, **slots):
        material = {"pbrMetallicRoughness": {}}
        for slot, texture in slots.items():
            if slot in ("baseColorTexture", "metallicRoughnessTexture"):
                material["pbrMetallicRoughness"][slot] = {"index": texture}
            else:
                material[slot] = {"index": texture}
        self.doc["materials"].append(material)
        return len(self.doc["materials"]) - 1

    def mesh(self, vertices, triangles, materials, joints_sets=0, targets=0):
        primitives = []
        for material in materials:
            attributes = {"POSITION": self.accessor(vertices), "NORMAL": self.accessor(vertices)}
            for index in range(joints_sets):
                attributes[f"JOINTS_{index}"] = self.accessor(vertices, "VEC4")
                attributes[f"WEIGHTS_{index}"] = self.accessor(vertices, "VEC4")
            primitive = {"attributes": attributes, "indices": self.accessor(triangles * 3, "SCALAR"),
                         "material": material}
            if targets:
                primitive["targets"] = [{"POSITION": self.accessor(vertices), "NORMAL": self.accessor(vertices)}
                                        for _ in range(targets)]
            primitives.append(primitive)
        self.doc["meshes"].append({"primitives": primitives})
        return len(self.doc["meshes"]) - 1

    def node(self, **fields):
        self.doc["nodes"].append(fields)
        index = len(self.doc["nodes"]) - 1
        self.doc["scenes"][0]["nodes"].append(index)
        return index

    def skin(self, joints):
        first = len(self.doc["nodes"])
        for _ in range(joints):
            self.doc["nodes"].append({})
        self.doc["skins"].append({"joints": list(range(first, first + joints))})
        return len(self.doc["skins"]) - 1

    def bytes(self):
        if self.binary:
            self.doc["buffers"] = [{"byteLength": len(self.binary)}]
        return glb(self.doc, self.binary)


def figure():
    """A skinned figure: body and hair meshes, 3 materials, morphs on the body, 8 influences on the hair."""
    asset = Asset()
    skin_tex, normal_tex, hair_tex = asset.image(2048, 2048), asset.image(2048, 2048), asset.image(1024, 512)
    skin = asset.material(baseColorTexture=skin_tex, normalTexture=normal_tex)
    cloth = asset.material(baseColorTexture=skin_tex)
    hair = asset.material(baseColorTexture=hair_tex)
    skin_index = asset.skin(60)
    asset.node(mesh=asset.mesh(5000, 9000, [skin, cloth], joints_sets=1, targets=12), skin=skin_index)
    asset.node(mesh=asset.mesh(3000, 4000, [hair], joints_sets=2), skin=asset.skin(75))
    return asset.bytes()


def prop():
    """A static prop: no skin, 5 materials, one odd-sized texture."""
    asset = Asset()
    tex = asset.image(1000, 600)
    materials = [asset.material(baseColorTexture=tex) for _ in range(5)]
    asset.node(mesh=asset.mesh(800, 1200, materials))
    return asset.bytes()


def profile(**budgets):
    return {"schema": 1, "id": "test", "name": "Test phone", "fps": 30,
            "texture_formats": {"default": "ASTC_6x6", "normal": "ASTC_4x4"}, "budgets": budgets}


class FormatTests(unittest.TestCase):
    def test_block_formats_round_up_to_whole_blocks(self):
        # ASTC is always 16 bytes a block (Khronos Data Format spec, ASTC); 6x6 blocks over 1000x600 = 167x100.
        self.assertEqual(device_budget.texture_bytes(1000, 600, "ASTC_6x6", mips=False), 167 * 100 * 16)
        self.assertEqual(device_budget.texture_bytes(1024, 1024, "ASTC_4x4", mips=False), 1024 * 1024)
        self.assertEqual(device_budget.texture_bytes(1024, 1024, "ETC2_RGB", mips=False), 1024 * 1024 // 2)
        self.assertEqual(device_budget.texture_bytes(16, 16, "RGBA8", mips=False), 16 * 16 * 4)

    def test_a_full_mip_chain_is_counted_level_by_level(self):
        # 4x4 RGBA8: 4x4 + 2x2 + 1x1 texels; a block format pays at least one block for every small level.
        self.assertEqual(device_budget.texture_bytes(4, 4, "RGBA8"), (16 + 4 + 1) * 4)
        self.assertEqual(device_budget.texture_bytes(8, 8, "ASTC_8x8"), 16 * 4)   # 8x8, 4x4, 2x2, 1x1: 4 blocks.
        self.assertEqual(device_budget.texture_bytes(8, 2, "RGBA8"), (16 + 4 + 2 + 1) * 4)

    def test_an_unknown_format_is_refused_by_name(self):
        with self.assertRaisesRegex(device_budget.ProfileError, "PVRTC_9"):
            device_budget.texture_bytes(64, 64, "PVRTC_9")


class MeasureTests(unittest.TestCase):
    def test_a_skinned_figure(self):
        found = device_budget.measure_bytes(figure())
        self.assertEqual(found["triangles"], 9000 * 2 + 4000)
        self.assertEqual(found["draw_calls"], 3)
        self.assertEqual(found["vertices"], 5000 * 2 + 3000)
        self.assertEqual(found["bones"], {"largest_skin": 75, "skins": 2})
        self.assertEqual(found["influences"], 8)
        self.assertEqual(found["morph_targets"], {"largest_mesh": 12, "total": 12, "vertices": 12 * 5000 * 2})
        roles = {image["index"]: image["roles"] for image in found["textures"]["images"]}
        self.assertEqual(roles, {0: ["base_color"], 1: ["normal"], 2: ["base_color"]})

    def test_a_static_prop(self):
        found = device_budget.measure_bytes(prop())
        self.assertEqual((found["triangles"], found["draw_calls"]), (1200 * 5, 5))
        self.assertEqual(found["bones"], {"largest_skin": 0, "skins": 0})
        self.assertEqual(found["influences"], 0)
        self.assertEqual(found["morph_targets"], {"largest_mesh": 0, "total": 0, "vertices": 0})


class CheckTests(unittest.TestCase):
    def test_each_set_budget_has_a_verdict_and_unset_ones_none(self):
        report = device_budget.check(device_budget.measure_bytes(figure()),
                                     profile(triangles=20000, draw_calls=4))
        rows = {row["metric"]: row for row in report["rows"]}
        self.assertEqual((rows["triangles"]["value"], rows["triangles"]["budget"], rows["triangles"]["passed"]),
                         (22000, 20000, False))
        self.assertTrue(rows["draw_calls"]["passed"])
        self.assertIsNone(rows["bones"]["budget"])
        self.assertIsNone(rows["bones"]["passed"])
        self.assertFalse(report["passed"])
        self.assertEqual(report["failed"], ["triangles"])

    def test_no_budget_set_is_never_a_pass(self):
        report = device_budget.check(device_budget.measure_bytes(prop()), profile())
        self.assertIsNone(report["passed"])
        self.assertEqual(report["failed"], [])

    def test_texture_memory_uses_each_role_format_with_mips(self):
        report = device_budget.check(device_budget.measure_bytes(figure()), profile(texture_memory_mb=64))
        expected = (device_budget.texture_bytes(2048, 2048, "ASTC_6x6") + device_budget.texture_bytes(2048, 2048,
                    "ASTC_4x4") + device_budget.texture_bytes(1024, 512, "ASTC_6x6"))
        row = next(row for row in report["rows"] if row["metric"] == "texture_memory_mb")
        self.assertAlmostEqual(row["value"], round(expected / 2 ** 20, 2))
        # The largest contributor comes first, with its role and format.
        self.assertEqual(row["contributors"][0]["format"], "ASTC_4x4")

    def test_draw_calls_follow_the_profile_passes(self):
        # An outline or shadow pass draws each primitive again: an engine's choice the profile states.
        measured = device_budget.measure_bytes(prop())
        plain = device_budget.check(measured, profile(draw_calls=8))
        doubled = device_budget.check(measured, dict(profile(draw_calls=8), passes_per_primitive=2))
        self.assertEqual([row["value"] for row in plain["rows"] if row["metric"] == "draw_calls"], [5])
        self.assertEqual([row["value"] for row in doubled["rows"] if row["metric"] == "draw_calls"], [10])
        self.assertFalse(doubled["passed"])

    def test_a_texture_over_the_device_limit_is_named(self):
        report = device_budget.check(device_budget.measure_bytes(figure()),
                                     dict(profile(), max_texture_size=1024))
        self.assertEqual([item["index"] for item in report["oversize_textures"]], [0, 1])
        self.assertFalse(report["passed"])

    def test_files_are_summed_against_the_app_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = []
            for name, data in (("a.glb", figure()), ("b.glb", prop())):
                path = Path(folder) / name
                path.write_bytes(data)
                paths.append(str(path))
            total = sum(Path(path).stat().st_size for path in paths)
            report = device_budget.check_app(paths, profile(app_mb=total / 2 ** 20 - 0.001))
        self.assertFalse(report["passed"])
        self.assertEqual(report["bytes"], total)
        self.assertEqual([item["name"] for item in report["files"]], ["a.glb", "b.glb"])   # Names, never paths.


class ProfileTests(unittest.TestCase):
    def test_shipped_profiles_load_and_say_where_their_numbers_come_from(self):
        profiles, problems = device_budget.load_profiles([PROFILES])
        self.assertEqual(problems, [])
        # Several common device classes, none of them one team's targets: phones of each platform and tier, a
        # tablet, a standalone headset and a desktop.
        self.assertGreaterEqual(len(profiles), 5)
        for item in profiles.values():
            self.assertIn(item["fps"], (30, 60, 72, 90))
            self.assertTrue(item.get("basis"), item["id"])   # Every shipped number says its source.
            # Shipped profiles hold facts only: budgets are a team's own numbers, never a default.
            self.assertTrue(all(value is None for value in item["budgets"].values()), item["id"])
        formats = {item["texture_formats"]["default"].split("_")[0] for item in profiles.values()}
        self.assertTrue({"ASTC", "ETC2", "BC7"} <= formats, formats)

    def test_a_later_folder_overrides_by_id_and_a_bad_file_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            Path(first, "p.json").write_text(json.dumps(profile(triangles=1)))
            Path(second, "p.json").write_text(json.dumps(profile(triangles=2)))
            Path(second, "broken.json").write_text("{")
            Path(second, "wrong.json").write_text(json.dumps(dict(profile(), texture_formats={"default": "X"})))
            profiles, problems = device_budget.load_profiles([first, second, Path(first, "missing")])
        self.assertEqual(profiles["test"]["budgets"]["triangles"], 2)
        self.assertEqual(sorted(item["file"] for item in problems), ["broken.json", "wrong.json"])

    def test_unknown_budget_names_are_refused(self):
        with self.assertRaisesRegex(device_budget.ProfileError, "polys"):
            device_budget.validate_profile(profile(polys=5))


if __name__ == "__main__":
    unittest.main()
