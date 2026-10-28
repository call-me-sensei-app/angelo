# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Generation Flow templates: listing, role resolution and the flows they insert, without Blender."""

import json
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent.flow_spec import FlowError
from angelo_agent.flow_templates import FORMAT, PARTS_SHEET_PROMPT, TEMPLATES, instantiate, listing, studio_ids, template

OPENAI, GEMINI, MESHY, TRIPO = (str(uuid.UUID(int=i)) for i in range(11, 15))


def entry(connection, model, mode, kind, ready=True):
    return {"connection": connection, "id": model, "mode": mode, "kind": kind, "ready": ready}


SUNBURST = entry(OPENAI, "gpt-image-2.5-sunburst", "openai_images_api", "IMAGE")
FLARE = entry(OPENAI, "gpt-image-2.5-flare", "openai_images_api", "IMAGE")
NANO = entry(GEMINI, "gemini-nano-banana-2.1", "gemini_images_api", "IMAGE")
MESHY7 = entry(MESHY, "meshy-7", "meshy_api", "3D")
TRIPO_P2 = entry(TRIPO, "P2-20260801", "tripo_api", "3D")


def node(flow, name):
    return next(n for n in flow["nodes"] if n["id"] == name)


class TemplateTests(unittest.TestCase):
    def test_listing_names_inputs_and_versions(self):
        listed = {t["id"]: t for t in listing()}
        self.assertEqual(list(listed), ["image_to_3d", "image_reference_3d", "character-in-parts",
                                        "character-in-parts-from-text"], "one table, in menu order")
        self.assertEqual(studio_ids(), ["character-in-parts", "character-in-parts-from-text"],
                         "Media Studio's Workflows tab runs the templates marked studio")
        self.assertEqual([t["id"] for t in listing() if t["studio"]], studio_ids())
        self.assertIsNone(template("nope"))
        self.assertEqual(listed["character-in-parts"]["inputs"][0]["kind"], "IMAGE")
        self.assertEqual(listed["character-in-parts-from-text"]["inputs"][0]["kind"], "TEXT")
        self.assertTrue(all(t["format"] == FORMAT and t["version"] >= 1 for t in TEMPLATES.values()))

    def test_roles_prefer_the_listed_models_and_their_provider_options(self):
        flow, unresolved = instantiate("character-in-parts", [NANO, FLARE, SUNBURST, TRIPO_P2, MESHY7])
        self.assertEqual(unresolved, [])
        sheet, parts = node(flow, "Parts sheet"), node(flow, "Parts 3D")
        self.assertEqual((sheet["connection_id"], sheet["model"]), (OPENAI, "gpt-image-2.5-sunburst"))
        self.assertEqual(sheet["options"]["size"], "3840x1920")
        self.assertEqual(sheet["prompt"], PARTS_SHEET_PROMPT)
        self.assertTrue(sheet["review"], "the parts appear for review before any 3D is paid for")
        self.assertEqual((parts["connection_id"], parts["model"]), (MESHY, "meshy-7"))

    def test_other_providers_get_their_own_options(self):
        flow, _ = instantiate("character-in-parts", [NANO, TRIPO_P2])
        sheet = node(flow, "Parts sheet")
        self.assertEqual((sheet["model"], sheet["options"]), ("gemini-nano-banana-2.1", {"aspect": "16:9", "resolution": "4K"}))
        self.assertEqual(node(flow, "Parts 3D")["model"], "P2-20260801", "a user's only 3D account still runs")

    def test_missing_accounts_leave_steps_to_choose(self):
        flow, unresolved = instantiate("character-in-parts", [entry(OPENAI, "gpt-image-2.5-sunburst", "openai_images_api", "IMAGE", ready=False)])
        self.assertEqual(unresolved, ["mesh", "sheet"])
        self.assertEqual(node(flow, "Parts sheet")["connection_id"], "")

    def test_the_text_template_starts_from_a_description(self):
        flow, _ = instantiate("character-in-parts-from-text", [SUNBURST, MESHY7])
        image = node(flow, "Character image")
        self.assertEqual(image["type"], "GENERATE_IMAGE")
        self.assertEqual(image["options"], {"quality": "high", "aspect": "3:4"})
        self.assertIn({"from": "Description", "to": "Character image", "input": "prompt"}, flow["links"])

    def test_the_image_templates_resolve_their_generators_and_end_in_the_scene(self):
        flow, unresolved = instantiate("image_to_3d", [SUNBURST, MESHY7])
        self.assertEqual(unresolved, [])
        self.assertEqual((node(flow, "3D model")["connection_id"], node(flow, "3D model")["model"]), (MESHY, "meshy-7"))
        self.assertEqual([n["type"] for n in flow["nodes"]], ["IMAGE", "GENERATE_3D", "PLACE_IN_SCENE"])
        flow, unresolved = instantiate("image_reference_3d", [NANO, MESHY7])
        reference = node(flow, "Reference")
        self.assertEqual((reference["model"], reference["variants"], reference["review"]), ("gemini-nano-banana-2.1", 2, True))
        flow, unresolved = instantiate("image_reference_3d", [])
        self.assertEqual(unresolved, ["mesh", "reference"], "no account: each generator asks for one")

    def test_unknown_template(self):
        with self.assertRaises(FlowError):
            instantiate("nope", [])


class DeclaredFormTests(unittest.TestCase):
    """The character-parts node types never grow a request that declares every tool on every turn."""

    def test_every_turn_declarations_leave_the_part_types_out(self):
        from angelo_agent.flow_spec import PART_TYPES
        from angelo_agent.tool_specs import codex_tools, dynamic_tools
        every_turn = {"voice": dynamic_tools(None), "function calling": codex_tools(None, defer=False)}
        for path, tools in every_turn.items():
            build = next(tool for tool in tools if tool["name"] == "angelo_flow_build")
            text = json.dumps(build)
            for kind in PART_TYPES:
                self.assertNotIn(kind, text, path)

    def test_no_step_of_any_template_covers_another(self):
        """Steps side by side are at least their drawn width apart (1.0.6: Character in parts' Parts 3D step covered the
        Split step's output, its hand-typed x 280 units after a 300-unit node)."""
        from angelo_agent.flow_spec import NODE_WIDTHS, NODE_TYPES
        self.assertEqual(set(NODE_WIDTHS), set(NODE_TYPES), "every node type has its width")
        for template_id, found in TEMPLATES.items():
            nodes = sorted(found["flow"]["nodes"], key=lambda n: tuple(n["location"]))
            for left, right in zip(nodes, nodes[1:]):
                if left["location"][1] == right["location"][1]:
                    with self.subTest(template=template_id, left=left["id"], right=right["id"]):
                        self.assertGreaterEqual(right["location"][0] - left["location"][0],
                                                NODE_WIDTHS[left["type"]] + 40)

    def test_on_demand_form_names_them(self):
        from angelo_agent.flow_spec import PART_TYPES
        from angelo_agent.tool_specs import TOOL_MAP, codex_tools
        namespace = next(tool for tool in codex_tools(None, defer=True) if tool.get("type") == "namespace")
        loaded = next(tool for tool in namespace["tools"] if tool["name"] == "angelo_flow_build")
        for build in (TOOL_MAP["angelo_flow_build"], loaded):
            for kind in PART_TYPES:
                self.assertIn(kind, json.dumps(build))


if __name__ == "__main__":
    unittest.main()
