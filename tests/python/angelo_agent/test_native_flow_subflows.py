# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Recipes and Subflow nodes: interfaces, expansion, reuse, planning and runs, without Blender."""

import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent.flow_spec import FlowError, apply_edit, interface_of, normalize, validate_flow_tool
from angelo_agent.flow_graph import BLOCKED, MISSING, READY, LibraryIndex, evaluate, plan, scope_nodes, select
from angelo_agent.flow_runner import FlowRun
from angelo_agent.flow_subflows import expand, members, used_recipes
from test_native_flows import IMAGE_ACCOUNT, MESH_ACCOUNT, SKETCH, FakeHost, catalog, generated, library

OTHER_SKETCH = str(uuid.UUID(int=4))


def turnaround_recipe():
    """Sketch (input) + Style (input) → 2-variant sheet (review) → 3D model (output)."""
    return normalize({"nodes": [
        {"id": "Sketch", "type": "IMAGE", "fields": {"source": None}},
        {"id": "Style", "type": "TEXT", "fields": {"text": "clean toon style"}},
        {"id": "Sheet", "type": "GENERATE_IMAGE", "fields": {"connection_id": IMAGE_ACCOUNT, "model": "image-model",
                                                             "prompt": "Character sheet", "variants": 2}},
        {"id": "Mesh", "type": "GENERATE_3D", "fields": {"connection_id": MESH_ACCOUNT, "model": "mesh-model"}},
    ], "links": [
        {"from": "Sketch", "to": "Sheet", "input": "references"},
        {"from": "Style", "to": "Sheet", "input": "prompt"},
        {"from": "Sheet", "to": "Mesh", "input": "reference"},
    ], "recipe": {"inputs": ["Sketch", "Style"], "output": "Mesh"}})


RECIPES = {"Turnaround": turnaround_recipe()}
INTERFACES = {name: interface_of(flow) for name, flow in RECIPES.items()}


def outer_flow(second_sketch=SKETCH, **hero):
    """Two uses of the recipe, each placed in the scene; Style is left at the recipe's default."""
    return normalize({"nodes": [
        {"id": "Hero sketch", "type": "IMAGE", "fields": {"source": {"kind": "ASSET", "id": SKETCH}}},
        {"id": "Villain sketch", "type": "IMAGE", "fields": {"source": {"kind": "ASSET", "id": second_sketch}}},
        {"id": "Hero", "type": "SUBFLOW", "fields": {"recipe": "Turnaround"}, **hero},
        {"id": "Villain", "type": "SUBFLOW", "fields": {"recipe": "Turnaround"}},
        {"id": "Place hero", "type": "PLACE_IN_SCENE"},
    ], "links": [
        {"from": "Hero sketch", "to": "Hero", "input": "Sketch"},
        {"from": "Villain sketch", "to": "Villain", "input": "Sketch"},
        {"from": "Hero", "to": "Place hero", "input": "model"},
    ]}, INTERFACES)


def assets_with_both_sketches():
    assets = library()
    assets[OTHER_SKETCH] = dict(assets[SKETCH], id=OTHER_SKETCH)
    return assets


def states(flow, assets):
    return evaluate(flow, LibraryIndex(assets), catalog=catalog)


class RecipeInterfaceTests(unittest.TestCase):
    def test_interface_lists_input_kinds_and_output_kind(self):
        self.assertEqual(INTERFACES["Turnaround"], {"inputs": {"Sketch": "IMAGE", "Style": "TEXT"}, "output": "MODEL"})
        self.assertIsNone(interface_of(outer_flow()))

    def test_invalid_interfaces_are_refused(self):
        recipe = turnaround_recipe()
        base = {"nodes": [{"id": n["id"], "type": n["type"], "fields": {k: v for k, v in n.items() if k not in {"id", "type"}}}
                          for n in recipe["nodes"]], "links": recipe["links"]}
        for interface in ({"inputs": ["Sheet"], "output": "Mesh"}, {"inputs": [], "output": "Style"},
                          {"inputs": ["Nope"], "output": "Mesh"}, {"inputs": "Sketch", "output": "Mesh"}, {"output": "Mesh"}):
            with self.subTest(interface=interface), self.assertRaises(FlowError):
                normalize(dict(base, recipe=interface))

    def test_links_into_a_subflow_follow_its_recipe(self):
        definition = {"nodes": [
            {"id": "Words", "type": "TEXT", "fields": {"text": "x"}},
            {"id": "Use", "type": "SUBFLOW", "fields": {"recipe": "Turnaround"}}]}
        normalize(dict(definition, links=[{"from": "Words", "to": "Use", "input": "Style"}]), INTERFACES)
        for link, interfaces in (({"from": "Words", "to": "Use", "input": "Sketch"}, INTERFACES),  # Wrong kind.
                                 ({"from": "Words", "to": "Use", "input": "Mask"}, INTERFACES),  # No such input.
                                 ({"from": "Words", "to": "Use", "input": "Style"}, {})):  # Recipe not found.
            with self.subTest(link=link), self.assertRaises(FlowError):
                normalize(dict(definition, links=[link]), interfaces)

    def test_a_recipe_cannot_contain_a_subflow_yet(self):
        with self.assertRaises(FlowError):
            apply_edit(outer_flow(), {"recipe": {"inputs": [], "output": "Hero"}}, INTERFACES)

    def test_edits_keep_or_clear_the_interface(self):
        recipe = turnaround_recipe()
        kept = apply_edit(recipe, {"nodes": [{"id": "Sheet", "fields": {"prompt": "Sheet v2"}}]})
        self.assertEqual(kept["recipe"], recipe["recipe"])
        removed = apply_edit(recipe, {"remove": ["Style"]})
        self.assertEqual(removed["recipe"]["inputs"], ["Sketch"])
        self.assertNotIn("recipe", apply_edit(recipe, {"recipe": None}))
        with self.assertRaises(FlowError):
            apply_edit(recipe, {"remove": ["Mesh"]})  # Its output; set another output first.

    def test_tool_arguments(self):
        validate_flow_tool("angelo_flow_build", {"flow": "F", "expected_revision": None,
                                                 "recipe": {"inputs": [], "output": "Mesh"}})
        with self.assertRaises(FlowError):
            validate_flow_tool("angelo_flow_build", {"flow": "F", "expected_revision": None, "recipe": "Mesh"})


class ExpansionTests(unittest.TestCase):
    def test_inputs_replace_sources_and_output_links_leave_from_the_output_step(self):
        flow = expand(outer_flow(), RECIPES)
        names = [node["id"] for node in flow["nodes"]]
        self.assertNotIn("Hero", names)
        self.assertNotIn("Hero/Sketch", names)  # Replaced by the linked outer image.
        self.assertIn("Hero/Style", names)  # Unlinked input: the recipe's default.
        links = {(l["from"], l["to"], l["input"]) for l in flow["links"]}
        self.assertIn(("Hero sketch", "Hero/Sheet", "references"), links)
        self.assertIn(("Hero/Style", "Hero/Sheet", "prompt"), links)
        self.assertIn(("Hero/Mesh", "Place hero", "model"), links)
        sheet = next(node for node in flow["nodes"] if node["id"] == "Hero/Sheet")
        self.assertEqual((sheet["subflow"], sheet["step"], sheet["label"]), ("Hero", "Sheet", "Hero › Sheet"))
        self.assertEqual(members(flow, "Hero"), {"Hero/Style", "Hero/Sheet", "Hero/Mesh"})
        self.assertEqual(used_recipes(outer_flow()), ["Turnaround"])

    def test_flows_without_subflows_are_unchanged(self):
        recipe = RECIPES["Turnaround"]
        self.assertIs(expand(recipe, RECIPES), recipe)

    def test_each_use_keeps_its_own_choice(self):
        choice = str(uuid.uuid4())
        flow = expand(outer_flow(chosen={"Sheet": choice}), RECIPES)
        chosen = {node["id"]: node.get("chosen") for node in flow["nodes"]}
        self.assertEqual((chosen["Hero/Sheet"], chosen["Villain/Sheet"]), (choice, None))

    def test_unusable_subflows_block_only_their_dependents(self):
        flow = normalize({"nodes": [
            {"id": "Missing", "type": "SUBFLOW", "fields": {"recipe": "Gone"}},
            {"id": "Unset", "type": "SUBFLOW", "fields": {}},
            {"id": "Plain", "type": "GENERATE_IMAGE", "fields": {"connection_id": IMAGE_ACCOUNT, "model": "image-model",
                                                                 "prompt": "A tree"}}], "links": []})
        result = states(expand(flow, RECIPES), library())
        self.assertEqual(result["Missing"]["state"], BLOCKED)
        self.assertIn("isn't in this file", result["Missing"]["reason"])
        self.assertEqual(result["Unset"]["reason"], "choose a recipe")
        plain = dict(RECIPES["Turnaround"], recipe=None)
        uses_plain = normalize({"nodes": [{"id": "Use", "type": "SUBFLOW", "fields": {"recipe": "Plain"}}], "links": []})
        self.assertIn("has no recipe interface", expand(uses_plain, {"Plain": plain})["nodes"][0]["problem"])
        self.assertEqual(result["Plain"]["state"], MISSING)

    def test_overlong_inner_names_are_explained(self):
        flow = normalize({"nodes": [{"id": "S" * 60, "type": "SUBFLOW", "fields": {"recipe": "Turnaround"}}], "links": []},
                         INTERFACES)
        expanded = expand(flow, RECIPES)
        self.assertIn("longer than 63 characters", expanded["nodes"][0]["problem"])

    def test_inner_names_never_collide_with_outer_nodes(self):
        flow = normalize({"nodes": [{"id": "Hero", "type": "SUBFLOW", "fields": {"recipe": "Turnaround"}},
                                    {"id": "Hero/Sheet", "type": "TEXT", "fields": {"text": "x"}}], "links": []}, INTERFACES)
        problem = next(n for n in expand(flow, RECIPES)["nodes"] if n["id"] == "Hero")["problem"]
        self.assertIn("already named 'Hero/Sheet'", problem)


class SubflowRunTests(unittest.TestCase):
    def test_identical_uses_share_keys_and_reuse_outputs(self):
        assets = assets_with_both_sketches()
        same = expand(outer_flow(), RECIPES)
        result = states(same, assets)
        self.assertEqual(result["Hero/Sheet"]["key"], result["Villain/Sheet"]["key"])
        different = states(expand(outer_flow(second_sketch=OTHER_SKETCH), RECIPES), assets)
        self.assertNotEqual(different["Hero/Sheet"]["key"], different["Villain/Sheet"]["key"])
        # Outputs made by running the recipe on its own, with the same inputs, are reused here.
        recipe_flow = apply_edit(RECIPES["Turnaround"], {"nodes": [
            {"id": "Sketch", "fields": {"source": {"kind": "ASSET", "id": SKETCH}}}]})
        key = states(recipe_flow, assets)["Sheet"]["key"]
        self.assertEqual(key, result["Hero/Sheet"]["key"])
        for variant in range(2):
            generated(assets, key, variant)
        self.assertEqual(states(same, assets)["Villain/Sheet"]["state"], READY)

    def test_running_a_subflow_by_name_plans_its_steps_and_review_stop(self):
        assets = library()
        flow = expand(outer_flow(), RECIPES)
        self.assertEqual(scope_nodes(flow, "NODE", "Hero"), {"Hero/Style", "Hero/Sheet", "Hero/Mesh"})
        self.assertIn("Place hero", scope_nodes(flow, "DOWNSTREAM", "Hero"))
        self.assertIn("Hero sketch", scope_nodes(flow, "UPSTREAM", "Hero"))
        run_plan = plan(flow, states(flow, assets), scope="NODE", node="Hero")
        lines = {line["node"]: line for line in run_plan["lines"]}
        self.assertEqual(set(lines), {"Hero/Sheet", "Hero/Mesh"})
        self.assertEqual(lines["Hero/Sheet"]["label"], "Hero › Sheet")
        selection = select(run_plan, flow)
        self.assertEqual(([l["node"] for l in selection["now"]], [l["node"] for l in selection["later"]]),
                         (["Hero/Sheet"], ["Hero/Mesh"]))  # The sheet's variants stop for a pick.

    def test_runner_binds_inner_outputs_through_the_expanded_flow(self):
        assets = library()
        flow = expand(outer_flow(), RECIPES)
        selection = select(plan(flow, states(flow, assets), scope="NODE", node="Hero"), flow, stops={"Hero/Sheet": False})
        host = FakeHost(flow, assets)
        run = FlowRun("Outer", selection["now"], host, later=selection["later"])
        run.tick()
        self.assertEqual(len(host.started), 2)
        host.finish()
        run.tick()
        self.assertEqual(host.started[-1][0]["references"], [["asset", run.steps["Hero/Sheet"]["chosen"]]])
        host.finish()
        run.tick()
        run.tick()
        self.assertEqual(run.result()["status"], "DONE")
        self.assertEqual(states(flow, assets)["Place hero"]["state"], READY)


if __name__ == "__main__":
    unittest.main()
