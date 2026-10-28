# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The web export's contracts without Blender (doc/angelo/web_export.rst): the tool's arguments, the findings for
what won't carry over to Three.js, the size budget read from a .glb, the tool's place in the families, and the
pinned Three.js build the preview serves."""

import hashlib
import json
import re
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import web_budget, web_compat  # noqa: E402
from angelo_agent.web_export_spec import CHECK, EXPORT, PREVIEW, WEB_NAMES, validate_web  # noqa: E402


def glb(document, binary=b""):
    """A .glb of ``document`` and ``binary`` (both padded as the format requires)."""
    text = json.dumps(document).encode("utf-8")
    text += b" " * (-len(text) % 4)
    binary += b"\0" * (-len(binary) % 4)
    chunks = struct.pack("<II", len(text), 0x4E4F534A) + text
    if binary:
        chunks += struct.pack("<II", len(binary), 0x004E4942) + binary
    return struct.pack("<III", 0x46546C67, 2, 12 + len(chunks)) + chunks


def png(width, height):
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height) + b"\0" * 9


class ArgumentTests(unittest.TestCase):
    def test_defaults_are_the_one_step_preset(self):
        self.assertEqual(validate_web(EXPORT, {}), {"meshes": "AUTO", "textures": "AUTO", "merge": True,
                                                   "selection_only": False, "output": "web/scene.glb"})
        self.assertEqual(validate_web(CHECK, {"merge": False})["merge"], False)

    def test_output_is_a_workspace_relative_glb(self):
        self.assertEqual(validate_web(EXPORT, {"output": "site\\hero"})["output"], "site/hero.glb")
        for bad in ("/etc/x.glb", "../up.glb", "a/../../b.glb", "C:/x.glb", " ", "a\nb.glb"):
            with self.assertRaises(ValueError, msg=bad):
                validate_web(EXPORT, {"output": bad})

    def test_closed_choices(self):
        for field, value in (("meshes", "ZIP"), ("textures", "AVIF"), ("merge", "yes")):
            with self.assertRaises(ValueError, msg=field):
                validate_web(CHECK, {field: value})
        with self.assertRaises(ValueError):
            validate_web(PREVIEW, {"action": "RESTART"})
        self.assertEqual(validate_web(PREVIEW, {"action": "STOP"}), {"action": "STOP"})


class FindingTests(unittest.TestCase):
    def material(self, *types, **flags):
        return dict({"name": "Mat", "uses_nodes": True, "surface": True,
                     "nodes": [{"type": kind, "label": kind[len("ShaderNode"):]} for kind in types]}, **flags)

    def codes(self, facts):
        return [item["code"] for item in web_compat.findings(facts)]

    def test_a_principled_texture_graph_carries_over(self):
        facts = {"materials": [self.material("ShaderNodeBsdfPrincipled", "ShaderNodeTexImage", "ShaderNodeNormalMap",
                                             "ShaderNodeMapping", "ShaderNodeSeparateColor")],
                 "lights": [{"name": "Key", "type": "SUN"}]}
        self.assertEqual(self.codes(facts), [])

    def test_procedural_nodes_are_named_with_the_bake_fix(self):
        found = web_compat.findings({"materials": [self.material("ShaderNodeBsdfPrincipled", "ShaderNodeTexNoise",
                                                                  "ShaderNodeValToRGB", "ShaderNodeMixRGB")]})
        self.assertEqual([item["code"] for item in found], ["material_nodes", "material_partial"])
        text = web_compat.english(found[0])
        self.assertIn("TexNoise", text["message"])
        self.assertIn("Bake", text["fix"])

    def test_the_exporters_own_groups_are_not_findings(self):
        material = self.material("ShaderNodeBsdfPrincipled")
        material["nodes"].append({"type": "ShaderNodeGroup", "label": "glTF", "group": "glTF Material Output"})
        self.assertEqual(self.codes({"materials": [material]}), [])

    def test_every_kind_of_loss_is_reported_errors_first(self):
        facts = {
            "materials": [self.material("ShaderNodeBsdfPrincipled", "ShaderNodeTexNoise", "ShaderNodeMath",
                                        displacement=True, volume=True, subsurface=True),
                          dict(self.material(), name="Empty", surface=False),
                          dict(self.material(), name="Toon", web_shader={"kind": "TOON", "problem": None}),
                          dict(self.material(), name="Lost", web_shader={"kind": "FILE", "problem": "missing"}),
                          dict(self.material(), name="Node", web_shader={"kind": "FILE", "problem": "tsl"})],
            "objects": [{"name": "Smoke", "type": "VOLUME"}, {"name": "Blob", "type": "META"},
                        {"name": "Grass", "type": "MESH", "particles": [{"name": "Hair", "kind": "HAIR"}],
                         "modifiers": ["OCEAN", "SUBSURF"]}],
            "lights": [{"name": "Panel", "type": "AREA"}, {"name": "Bulb", "type": "POINT", "soft": True}],
            "world": {"name": "Sky", "lit": True},
            "images": [{"name": "Tiles", "source": "TILED"}, {"name": "Clip", "source": "MOVIE"},
                       {"name": "Gone", "source": "FILE", "missing": True},
                       {"name": "Huge", "source": "FILE", "width": 8192, "height": 4096}],
            "effects": {"motion_blur": True, "depth_of_field": ["Cam"], "compositor": True},
            "triangles": 2_000_000,
        }
        found = web_compat.findings(facts)
        self.assertEqual({item["code"] for item in found}, set(web_compat.TEMPLATES))
        severities = [item["severity"] for item in found]
        self.assertEqual(severities, sorted(severities, key=web_compat.SEVERITIES.index))
        for item in found:   # Every template fills from its own parameters.
            self.assertNotIn("{", web_compat.english(item)["message"] + web_compat.english(item)["fix"])

    def test_a_web_shader_replaces_its_stand_ins_findings(self):
        material = self.material("ShaderNodeBsdfPrincipled", "ShaderNodeTexVoronoi", displacement=True,
                                 web_shader={"kind": "RIM", "problem": None})
        self.assertEqual(self.codes({"materials": [material]}), ["web_shader"])
        material["web_shader"]["problem"] = "too_large"   # Unusable: the web shows the nodes, so they count again.
        self.assertEqual(sorted(self.codes({"materials": [material]})),
                         ["material_displacement", "material_nodes", "web_shader_file"])

    def test_the_page_translates_templates_not_names(self):
        finding = web_compat.findings({"lights": [{"name": "Panel", "type": "AREA"}]})[0]
        shown = web_compat.render(finding, lambda text: "«" + text + "»")
        self.assertIn("Panel", shown["message"])   # The object's name is data, never translated.
        self.assertTrue(shown["fix"].startswith("«") and shown["message"].startswith("«"))

    def test_no_finding_claims_parity(self):
        for _severity, message, fix in web_compat.TEMPLATES.values():
            self.assertNotRegex((message + fix).lower(), r"identical|exactly the same|matches blender")


class BudgetTests(unittest.TestCase):
    def test_triangles_draw_calls_and_textures_come_from_the_file(self):
        image = png(1024, 512)
        document = {
            "asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0, 1]}],
            "nodes": [{"mesh": 0, "children": [2]}, {"mesh": 1}, {"mesh": 0}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1},
                                       {"attributes": {"POSITION": 0}, "indices": 1}]},
                       {"primitives": [{"attributes": {"POSITION": 0}, "mode": 5}]}],
            "accessors": [{"count": 8}, {"count": 36}],
            "images": [{"name": "Albedo", "mimeType": "image/png", "bufferView": 0}],
            "bufferViews": [{"byteOffset": 0, "byteLength": len(image)}],
            "extensionsRequired": ["EXT_meshopt_compression"],
        }
        data = glb(document, image)
        budget = web_budget.measure_bytes(data)
        self.assertEqual(budget["bytes"], len(data))
        self.assertEqual(budget["triangles"], 2 * 2 * 12 + 6)   # Mesh 0 drawn twice (two primitives), a strip of 8.
        self.assertEqual(budget["draw_calls"], 2 * 2 + 1)
        self.assertEqual(budget["textures"]["images"][0]["width"], 1024)
        self.assertEqual(budget["textures"]["largest"], 1024)
        self.assertEqual(budget["extensions_required"], ["EXT_meshopt_compression"])

    def test_gpu_instancing_draws_once_per_node(self):
        document = {"scenes": [{"nodes": [0]}], "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
                    "nodes": [{"mesh": 0, "extensions": {"EXT_mesh_gpu_instancing": {"attributes": {"TRANSLATION": 1}}}}],
                    "accessors": [{"count": 3}, {"count": 100}]}
        budget = web_budget.measure_bytes(glb(document))
        self.assertEqual((budget["triangles"], budget["draw_calls"]), (100, 1))

    def test_image_sizes_of_every_web_format(self):
        webp_lossless = b"RIFF\0\0\0\0WEBPVP8L\0\0\0\0\x2f" + (511 | (255 << 14)).to_bytes(4, "little") + b"\0" * 8
        ktx2 = b"\xabKTX 20\xbb\r\n\x1a\n" + struct.pack("<III", 0, 1, 2048) + struct.pack("<I", 1024) + b"\0" * 8
        jpeg = b"\xff\xd8\xff\xc0\x00\x11\x08" + struct.pack(">HH", 300, 400) + b"\0" * 12
        self.assertEqual(web_budget.image_size(webp_lossless), (512, 256))
        self.assertEqual(web_budget.image_size(ktx2), (2048, 1024))
        self.assertEqual(web_budget.image_size(jpeg), (400, 300))
        self.assertEqual(web_budget.image_size(b"nonsense"), (0, 0))

    def test_not_a_glb_is_refused(self):
        with self.assertRaises(web_budget.BudgetError):
            web_budget.measure_bytes(b"{\"asset\": {}}" + b" " * 20)


class FamilyTests(unittest.TestCase):
    def test_deferred_and_offered_by_what_each_does(self):
        from angelo_agent.external_clients import EDIT_TOOLS, READ_TOOLS
        from angelo_agent.plan_kinds import DEFERRED_TOOLS
        from angelo_agent.tool_specs import TOOL_MAP
        self.assertEqual(WEB_NAMES, {CHECK, EXPORT, PREVIEW})
        self.assertLessEqual(WEB_NAMES, set(TOOL_MAP))
        self.assertLessEqual(WEB_NAMES, DEFERRED_TOOLS)   # A turn that never exports sends only the names.
        self.assertTrue(TOOL_MAP[CHECK]["annotations"]["readOnlyHint"])   # No card for a check.
        self.assertIn(CHECK, READ_TOOLS)
        for name in (EXPORT, PREVIEW):
            self.assertFalse(TOOL_MAP[name]["annotations"]["readOnlyHint"])   # Approved: a file, a server.
            self.assertIn(name, EDIT_TOOLS)
            self.assertNotIn(name, READ_TOOLS)

    def test_every_tool_has_its_card_and_titles(self):
        for path, needle in (("approvals.py", 'name.startswith("angelo_web_")'),
                             ("chat_titles.py", '"angelo_web_preview"'), ("activity_text.py", '"angelo_web_check"')):
            self.assertIn(needle, (ADDON / path).read_text(encoding="utf-8"), path)

    def test_the_executor_routes_it_without_a_scene_diff(self):
        source = (ADDON / "blender_tools.py").read_text(encoding="utf-8")
        self.assertIn("and not name.startswith('angelo_web_'))  # Web: reads only.", source)
        self.assertIn('else web_host).execute(', source)


class BundledThreeTests(unittest.TestCase):
    VENDOR = ADDON / "web_preview/vendor/three"

    def test_every_file_matches_the_pinned_build(self):
        listed = {}
        for line in (self.VENDOR / "MANIFEST").read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#"):
                digest, path = line.split("  ", 1)
                listed[path] = digest
        on_disk = {str(path.relative_to(self.VENDOR)) for path in self.VENDOR.rglob("*")
                   if path.is_file() and path.name not in {"MANIFEST", "LICENSE"}}
        self.assertEqual(set(listed), on_disk)
        for path, digest in listed.items():
            self.assertEqual(hashlib.sha256((self.VENDOR / path).read_bytes()).hexdigest(), digest, path)
        self.assertIn("MIT License", (self.VENDOR / "LICENSE").read_text(encoding="utf-8"))

    def test_the_page_loads_nothing_from_the_network(self):
        page = ADDON / "web_preview"
        for path in (page / "index.html", page / "app.js", page / "app.css", page / "shaders.js"):
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"""(src|href|url|from|import)\s*[=(]?\s*["']?(https?:)?//""", path.name)
        imports = re.findall(r'from "([^"]+)"', (page / "app.js").read_text(encoding="utf-8") +
                             (page / "shaders.js").read_text(encoding="utf-8"))
        for name in imports:
            self.assertTrue(name in {"three", "./shaders.js"} or name.startswith("three/addons/"), name)
            if name.startswith("three/addons/"):
                self.assertTrue((self.VENDOR / "examples/jsm" / name[len("three/addons/"):]).is_file(), name)


if __name__ == "__main__":
    unittest.main()
