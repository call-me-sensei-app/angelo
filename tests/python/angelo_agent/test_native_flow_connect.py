# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A generation step without a connected account says so and points to AI Providers, like Media Studio.

``flow_graph.connection_needs`` names, per generator step, what it lacks:
``connect`` (no connected generation account of its kind at all),
``reconnect`` (its chosen account isn't connected) or ``choose`` (accounts are
connected, none chosen). Nodes, Subflow uses, the sidebar and a run attempt draw
"Connect in AI Providers" from it; the step's model-facing reason is unchanged.
Pure: no Blender, provider or account. ``test_blender_generation_connect.py``
shows the same in the app.
"""

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent.flow_graph import LibraryIndex, connect_steps, connection_needs, evaluate, need_for, plan  # noqa: E402
from angelo_agent.flow_spec import normalize  # noqa: E402
from angelo_agent.flow_subflows import expand  # noqa: E402

IMAGE_ACCOUNT, MESH_ACCOUNT = "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"


def catalog(connection, model):
    """A connected image account, a 3D account whose key was never verified, and setup rows without an account."""
    entries = {(IMAGE_ACCOUNT, "image-model"): {"kind": "IMAGE", "ready": True, "name": "Image Model"},
               (MESH_ACCOUNT, "mesh-model"): {"kind": "3D", "ready": False, "name": "Mesh Model"}}
    return entries.get((connection, model))


def flow(image=None, mesh=None, recipe=None):
    image_fields = {"prompt": "a cat"} | (image or {})
    mesh_fields = dict(mesh or {})
    return normalize({**({"recipe": recipe} if recipe else {}), "nodes": [
        {"id": "Text", "type": "TEXT", "fields": {"text": "toon"}},
        {"id": "Sheet", "type": "GENERATE_IMAGE", "fields": image_fields},
        {"id": "Mesh", "type": "GENERATE_3D", "fields": mesh_fields},
        {"id": "Place", "type": "PLACE_IN_SCENE"},
    ], "links": [{"from": "Text", "to": "Sheet", "input": "prompt"}, {"from": "Sheet", "to": "Mesh", "input": "reference"},
                 {"from": "Mesh", "to": "Place", "input": "model"}]})


class NeedTests(unittest.TestCase):
    def test_no_account_at_all_is_connect_for_every_generator(self):
        self.assertEqual(connection_needs(flow(), catalog, connected=set()), {"Sheet": "connect", "Mesh": "connect"})

    def test_a_connected_kind_only_needs_a_choice(self):
        self.assertEqual(connection_needs(flow(), catalog, connected={"IMAGE"}), {"Sheet": "choose", "Mesh": "connect"})

    def test_a_chosen_account_that_is_not_connected_needs_reconnecting(self):
        needs = connection_needs(flow(image={"connection_id": IMAGE_ACCOUNT, "model": "image-model"},
                                      mesh={"connection_id": MESH_ACCOUNT, "model": "mesh-model"}), catalog, connected={"IMAGE"})
        self.assertEqual(needs, {"Mesh": "reconnect"})

    def test_a_removed_account_is_connect_or_choose_by_what_is_left(self):
        gone = flow(image={"connection_id": "00000000-0000-0000-0000-000000000009", "model": "image-model"})
        self.assertEqual(connection_needs(gone, catalog, connected=set())["Sheet"], "connect")
        self.assertEqual(connection_needs(gone, catalog, connected={"IMAGE"})["Sheet"], "choose")

    def test_a_subflow_use_reports_its_strongest_inner_need(self):
        needs = {"Use/Sheet": "choose", "Use/Mesh": "connect", "Other": "reconnect"}
        self.assertEqual(need_for(needs, "Use"), "connect")
        self.assertEqual(need_for(needs, "Other"), "reconnect")
        self.assertIsNone(need_for(needs, "Us"), "Only the use's own steps count")
        self.assertIsNone(need_for(needs, "Text"))

    def test_expanded_recipe_steps_are_named_by_their_use(self):
        recipe = flow(recipe={"inputs": [], "output": "Sheet"})
        outer = normalize({"nodes": [{"id": "Use", "type": "SUBFLOW", "fields": {"recipe": "Recipe"}}], "links": []})
        needs = connection_needs(expand(outer, {"Recipe": recipe}), catalog, connected=set())
        self.assertEqual(need_for(needs, "Use"), "connect")


class RunAttemptTests(unittest.TestCase):
    def run_plan(self, definition, connected):
        states = evaluate(definition, LibraryIndex({}), catalog=catalog)
        return plan(definition, states), connection_needs(definition, catalog, connected=connected)

    def test_a_run_with_no_account_names_every_step_that_needs_one(self):
        run_plan, needs = self.run_plan(flow(), set())
        self.assertFalse([line for line in run_plan["lines"] if line["action"] == "GENERATE"])
        self.assertEqual([line["node"] for line in connect_steps(run_plan, needs)], ["Sheet", "Mesh"])

    def test_steps_that_only_need_a_choice_are_not_sent_to_ai_providers(self):
        run_plan, needs = self.run_plan(flow(), {"IMAGE", "3D"})
        self.assertEqual(connect_steps(run_plan, needs), [])

    def test_the_model_facing_reasons_are_unchanged(self):
        run_plan, _ = self.run_plan(flow(mesh={"connection_id": MESH_ACCOUNT, "model": "mesh-model"}), set())
        reasons = {line["node"]: line.get("reason") for line in run_plan["lines"]}
        self.assertEqual(reasons["Sheet"], "choose an account and model")
        self.assertEqual(reasons["Mesh"], "connect or check this account in AI Providers")


class SourceTests(unittest.TestCase):
    """Window paths the GUI fixture proves; these guard their wiring on every host."""

    def source(self, name, start, end):
        text = (ADDON / name).read_text()
        return text.split(start, 1)[1].split(end, 1)[0]

    def test_nodes_and_the_sidebar_point_to_ai_providers(self):
        for name, start, end in (("flow_nodes.py", "class _GeneratorNode", "\nclass ANGELO_FlowGenerateImageNode"),
                                 ("flow_nodes.py", "class ANGELO_FlowSubflowNode", "\nNODES ="),
                                 ("flow_ui.py", "class ANGELO_PT_flow", "\nCLASSES =")):
            body = self.source(name, start, end)
            self.assertIn("connect_notice(", body, (name, start))
        notice = self.source("flow_nodes.py", "\ndef connect_notice(", "\nclass ")
        self.assertIn('"angelo.flow_connect"', notice)
        self.assertIn('iface_("Connect in AI Providers")', notice)

    def test_the_flow_buttons_say_what_the_click_does(self):
        # angelo.settings' tooltip ("Connection setup lives here, not in the chat editor") is about the chat.
        tree = ast.parse((ADDON / "flow_ui.py").read_text())
        operator = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ANGELO_OT_flow_connect")
        tooltip = ast.get_docstring(operator)
        self.assertIn("AI Providers", tooltip)
        self.assertIn("generation account", tooltip)
        self.assertIn("ANGELO_OT_flow_connect", self.source("flow_ui.py", "\nCLASSES =", ")\n"))
        for name in ("flow_nodes.py", "flow_ui.py"):
            self.assertNotIn('"angelo.settings"', (ADDON / name).read_text(), name)  # Every button is flow_connect.
        self.assertIn("bpy.ops.angelo.settings('INVOKE_DEFAULT')", ast.get_source_segment(
            (ADDON / "flow_ui.py").read_text(), operator), "It opens AI Providers the way Media Studio's button does")

    def test_a_blocked_run_attempt_offers_ai_providers(self):
        invoke = self.source("flow_ui.py", "    def invoke(self, context, event):", "\n    def _selection(")
        self.assertIn("connect_steps(", invoke)
        self.assertIn("show_connect_notice(", invoke)

    def test_node_text_is_marked_whole_and_never_reaches_the_model(self):
        tree = ast.parse((ADDON / "flow_nodes.py").read_text())
        marked = {node.args[0].value for node in ast.walk(tree) if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Name) and node.func.id == "n_" and node.args
                  and isinstance(node.args[0], ast.Constant)}
        self.assertTrue({"No image generation account yet", "No 3D generation account yet",
                         "This step's account isn't connected", "A recipe step needs a generation account"} <= marked)
        self.assertNotIn("ui_text", (ADDON / "flow_graph.py").read_text(), "Plan reasons stay model-facing English")


if __name__ == "__main__":
    unittest.main()
