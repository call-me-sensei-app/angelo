# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The agent's way into the picture viewer (ruling 2026-11-20, agent first): ``angelo_app_feature`` MARKUP / OPEN,
with a picture of this chat's ``reference_id`` or, without one, the 3D view as it is now. The person still draws and
sends; the call is approved on a card like every app feature. No Blender here (the host and card by their source)."""

import suite_isolation  # noqa: F401

import ast
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import app_feature_spec as spec, feature_guide  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402

PICTURE = "123e4567-e89b-42d3-a456-426614174000"


def source(name):
    return (ADDON / name).read_text(encoding="utf-8")


class SpecTests(unittest.TestCase):
    def test_open_with_a_picture_or_the_3d_view(self):
        self.assertEqual(spec.validate_app_feature(spec.TOOL, {"feature": "MARKUP", "action": "OPEN"}),
                         {"feature": "MARKUP", "action": "OPEN"})
        self.assertEqual(spec.validate_app_feature(spec.TOOL, {"feature": "MARKUP", "action": "OPEN",
                                                               "reference_id": PICTURE}),
                         {"feature": "MARKUP", "action": "OPEN", "reference_id": PICTURE})
        validate_call(spec.TOOL, {"feature": "MARKUP", "action": "OPEN", "reference_id": PICTURE})

    def test_a_picture_is_named_by_its_reference_id_only(self):
        for bad in ("../x.png", "/Users/a/b.png", PICTURE.upper(), "", 7):
            with self.subTest(reference_id=bad), self.assertRaises(ValueError):
                spec.validate_app_feature(spec.TOOL, {"feature": "MARKUP", "action": "OPEN", "reference_id": bad})
        for other in ({"feature": "ENGINE_LINK", "action": "ON", "reference_id": PICTURE},
                      {"feature": "MARKUP", "action": "ON"}, {"feature": "MARKUP", "action": "OPEN", "engine": "WEB"}):
            with self.subTest(arguments=other), self.assertRaises(ValueError):
                spec.validate_app_feature(spec.TOOL, other)

    def test_the_schema_and_description_name_it(self):
        tool = spec.APP_FEATURE_TOOLS[0]
        self.assertIn("MARKUP", tool["inputSchema"]["properties"]["feature"]["enum"])
        self.assertIn("reference_id", tool["inputSchema"]["properties"])
        self.assertIn("MARKUP: OPEN", tool["description"])


class HostTests(unittest.TestCase):
    def test_the_host_opens_the_viewer_for_the_chat_that_asked(self):
        host = source("app_feature_host.py")
        self.assertIn('if request["feature"] == "MARKUP":', host)
        self.assertIn("markup_overlay.open_reference(controller,", host)
        self.assertIn("app_feature_host.execute(self.window, name, arguments, self.studio_context)",
                      source("blender_tools.py"))

    def test_its_card_says_what_opens_and_that_nothing_is_sent_yet(self):
        self.assertIn('args["feature"] == "MARKUP"', source("app_feature_approvals.py"))


class GuideTests(unittest.TestCase):
    def test_the_guide_teaches_the_call_and_hands_over_its_schema(self):
        guide = next(guide for guide in feature_guide.GUIDES if guide.id == "markup")
        self.assertTrue(any(example.get("tool") == spec.TOOL for example in guide.examples))
        self.assertIn("angelo_app_feature", guide.agent)
        result, _found = feature_guide.lookup(["markup"])
        self.assertEqual(result["features"][0]["tool"]["name"], spec.TOOL, "the deferred tool's schema with it")


if __name__ == "__main__":
    unittest.main()
