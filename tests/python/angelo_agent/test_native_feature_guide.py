# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's features stay discoverable by people and by the agent together (doc/angelo/features.rst). No Blender.

Covered: every + menu feature (``chat_destinations.SHIPPED``: rows and the Video and Render groups' members) has an
entry in the agent's list (``feature_guide``), and every entry this tree has (its ``requires`` operators defined in
the add-on) is reachable from the + menu, has a section in the people's guide and an existing contract; a feature
from an unmerged branch is offered only where its operator is registered; the ``FEATURES`` lookup answers by name
with the exact schema of ``angelo_app_feature``, lists unknown names, and only offers what the build has; the
agent's tool validates its actions; the engine group of the + menu offers every engine's plugin, each with an
adapter and a bundled plugin.
"""

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
DOCS = ROOT / "doc/angelo"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import app_feature_spec, chat_destinations as cd, feature_guide  # noqa: E402
from test_native_chat_destinations import registrations  # noqa: E402

# + menu entries that are no feature of their own: attaching a picture or file to the message, and the two built-in
# groups (Video, Render) whose members are the features.
NOT_FEATURES = {"attach", "video_tools", "render_tools"}


def defined_operators():
    """Every ``bl_idname = "angelo.…"`` an add-on module defines (what a build of this tree registers)."""
    found = set()
    for path in ADDON.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "bl_idname" for t in node.targets):
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    found.add(node.value.value)
    return found


def headings(text):
    lines = text.splitlines()
    return {lines[i - 1] for i, line in enumerate(lines) if i and line and set(line) == {"="}
            and len(line) == len(lines[i - 1])}


class CoverageTests(unittest.TestCase):
    def test_every_menu_feature_is_known_to_the_agent(self):
        known = {menu for guide in feature_guide.GUIDES for menu in guide.menu}
        self.assertEqual(set(cd.SHIPPED) - NOT_FEATURES - known, set(),
                         "+ menu features with no feature_guide entry")

    def test_every_offered_feature_is_reachable_documented_and_contracted(self):
        operators, registered = defined_operators(), registrations()
        guide_text = (DOCS / (feature_guide.DOCUMENT + ".rst")).read_text(encoding="utf-8")
        sections = headings(guide_text)
        offered = feature_guide.offered(operators)
        self.assertIn("engine_link", [guide.id for guide in offered])
        for guide in offered:
            with self.subTest(feature=guide.id):
                self.assertTrue(guide.menu, "reachable from no + menu row")
                for menu in guide.menu:
                    self.assertIn(menu, cd.SHIPPED)   # A row of its own or a group's member.
                    self.assertIn(menu, registered, "no module registers + menu feature " + menu)
                self.assertIn(guide.heading, sections, "no section in features.rst")
                self.assertTrue((DOCS / (guide.doc + ".rst")).is_file(), guide.doc)
                self.assertTrue(guide.what and guide.reach and guide.agent, "says what, where and how")

    def test_a_branch_feature_is_offered_only_where_its_operator_is(self):
        later = [guide for guide in feature_guide.GUIDES if guide.requires]
        self.assertTrue(later)
        for guide in later:
            self.assertNotIn(guide, feature_guide.offered(set()))
            self.assertIn(guide, feature_guide.offered(set(guide.requires)))
        index = feature_guide.index(set(), {"running": ["Unity"], "installed": [], "live_link_on": False})
        self.assertEqual({entry["name"] for entry in index["features"]},
                         {guide.id for guide in feature_guide.GUIDES if not guide.requires})
        self.assertEqual(index["engines"]["running"], ["Unity"])


class LookupTests(unittest.TestCase):
    def test_engine_link_by_name_carries_the_tool_schema_and_this_computer(self):
        from angelo_agent import plan_schema
        engines = {"running": ["Godot"], "installed": ["Unity"], "live_link_on": False}
        answer = plan_schema.lookup("FEATURES", ["engine_link", "teleporter"], features_available=set(),
                                    engines=engines)
        entry = answer["features"][0]
        self.assertEqual(answer["unknown_names"], ["teleporter"])
        self.assertEqual(entry["tool"], {"name": "angelo_app_feature",
                                         "input_schema": app_feature_spec.APP_FEATURE_TOOLS[0]["inputSchema"]})
        self.assertEqual(entry["this_computer"], engines)
        for word in ("Add Angelo Link", "Turn on the live link", "Unity", "Unreal"):
            self.assertIn(word, entry["where"])
        # A branch feature this build lacks reads as unknown, never described.
        self.assertEqual(plan_schema.lookup("FEATURES", ["uv_checker"], features_available=set())["unknown_names"],
                         ["uv_checker"])

    def test_feature_names_pass_the_lookup_pattern(self):
        from angelo_agent.execution_spec import NAME_PATTERN, validate_execution
        for guide in feature_guide.GUIDES:
            self.assertRegex(guide.id, NAME_PATTERN)
        self.assertEqual(validate_execution("angelo_agent_capabilities", {"family": "FEATURES"}),
                         {"family": "FEATURES"})

    def test_the_tool_validates_each_action(self):
        valid = app_feature_spec.validate_app_feature
        name = app_feature_spec.TOOL
        self.assertEqual(valid(name, {"feature": "ENGINE_LINK", "action": "ON"}),
                         {"feature": "ENGINE_LINK", "action": "ON"})
        self.assertEqual(valid(name, {"feature": "ENGINE_LINK", "action": "ADD_TO_PROJECT", "engine": "UNITY"}),
                         {"feature": "ENGINE_LINK", "action": "ADD_TO_PROJECT", "engine": "UNITY"})
        for args in ({"feature": "ENGINE_LINK", "action": "ADD_TO_PROJECT"},
                     {"feature": "ENGINE_LINK", "action": "ON", "engine": "GODOT"},
                     {"feature": "ENGINE_LINK", "action": "EXPLODE"}, {"feature": "UV_CHECKER", "action": "ON"},
                     {"feature": "ENGINE_LINK", "action": "ON", "path": "/tmp"}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                valid(name, args)

    def test_the_tool_is_deferred_approved_and_never_an_apps(self):
        from angelo_agent.plan_kinds import DEFERRED_TOOLS
        from angelo_agent.tool_specs import BOUNDED_MUTATIONS, TOOL_MAP, dynamic_tools
        from angelo_agent.external_clients import NEVER_FOR_APPS
        self.assertIn(name := app_feature_spec.TOOL, DEFERRED_TOOLS)
        self.assertFalse(TOOL_MAP[name]["annotations"]["readOnlyHint"])
        self.assertNotIn(name, BOUNDED_MUTATIONS)   # Auto never runs it without the card.
        self.assertIn(name, NEVER_FOR_APPS)
        self.assertNotIn(name, {tool["name"] for tool in dynamic_tools()})   # Voice threads: the + menu row.


class ExampleTests(unittest.TestCase):
    """The guide teaches only calls the host accepts (audit 2026-11-01: it told the agent angelo_studio_generate
    takes kind VIDEO, which its validator refuses, and that import_image imports into the scene)."""

    def test_every_example_passes_its_tools_own_validator(self):
        from angelo_agent.action_catalog import validate_operation
        from angelo_agent.tool_specs import TOOL_MAP, validate_call
        seen = 0
        for guide in feature_guide.GUIDES:
            for example in guide.examples:
                with self.subTest(feature=guide.id, example=example):
                    self.assertTrue(set(example) in ({"tool", "arguments"}, {"operation"}), example)
                    if "operation" in example:
                        validate_operation(example["operation"])
                    else:
                        self.assertIn(example["tool"], TOOL_MAP)
                        validate_call(example["tool"], example["arguments"])
                    seen += 1
        self.assertGreaterEqual(seen, 10)

    def test_every_feature_the_agent_can_use_has_an_example(self):
        # The rest are the person's view settings or clicks, or families whose own lookup carries their calls.
        without = {"installed_engines", "ai_render", "select_through", "uv_checker", "generation_flows", "base_meshes",
                   "feedback"}   # Sending feedback is the person's click on the website's form; the agent has no call.
        for guide in feature_guide.GUIDES:
            with self.subTest(feature=guide.id):
                self.assertEqual(bool(guide.examples), guide.id not in without, "add a validated example")

    def test_every_tool_the_guide_names_exists(self):
        from angelo_agent.tool_specs import TOOL_MAP
        for guide in feature_guide.GUIDES:
            for name in re.findall(r"angelo_[a-z0-9_]+\*?", guide.agent + " " + guide.reach):
                with self.subTest(feature=guide.id, name=name):
                    if name.endswith(("*", "_")):   # A family: angelo_studio_*, angelo_rig_hand_*.
                        self.assertTrue(any(tool.startswith(name.rstrip("*")) for tool in TOOL_MAP), name)
                    else:
                        self.assertIn(name, TOOL_MAP)

    def test_the_lookup_returns_the_examples_with_their_note(self):
        entry = feature_guide.lookup(["video"])[0]["features"][0]
        self.assertEqual(entry["examples"][0]["tool"], "angelo_studio_generate")
        self.assertNotIn("kind", entry["examples"][0]["arguments"])
        self.assertIn("placeholders", entry["examples_note"])


class EngineMenuTests(unittest.TestCase):
    """``engine_link_ui`` imports Blender, so its menu table is read from its source."""

    def setUp(self):
        self.tree = ast.parse((ADDON / "engine_link_ui.py").read_text(encoding="utf-8"))

    def _dict(self, name):
        for node in self.tree.body:
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == name:
                return {key.value: value for key, value in zip(node.value.keys, node.value.values)}
        raise AssertionError(name)

    def test_every_engine_has_an_install_row_an_adapter_and_its_plugin(self):
        adapters = {key: value.value for key, value in self._dict("ENGINE_ADAPTERS").items()}
        self.assertEqual(set(adapters), set(app_feature_spec.ENGINES))
        for table in ("INSTALLED", "NOT_PROJECT", "INSTALL_ROW"):
            self.assertEqual(set(self._dict(table)), set(adapters), table)
        for engine, module in adapters.items():
            source = (ADDON / (module + ".py")).read_text(encoding="utf-8")
            folder = re.search(r'PLUGIN_SOURCE = Path\(__file__\)\.with_name\("([^"]+)"\) / "([^"]+)"', source)
            self.assertIsNotNone(folder, module)
            self.assertTrue((ADDON / folder.group(1) / folder.group(2)).is_dir(), engine)


if __name__ == "__main__":
    unittest.main()
