# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Character parts in Generation Flows: Split Sheet, per-part 3D expansion and Place Character, without Blender."""

import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent.flow_spec import FlowError, apply_edit, normalize
from angelo_agent.flow_graph import (BLOCKED, MISSING, READY, WAITING, LibraryIndex, evaluate, plan, request_for,
                                     select, split_key)
from angelo_agent.flow_fanout import expand_parts, members
from angelo_agent.flow_runner import DONE, FlowRun
from test_native_flows import IMAGE_ACCOUNT, MESH_ACCOUNT, SKETCH, catalog, generated, library

SLOTS = [{"id": "body", "kind": "BODY"}, {"id": "hair", "kind": "HAIR"},
         {"id": "accessories", "kind": "ACCESSORY", "items": "split"}]


def character_flow(name="Parts 3D", **chosen):
    return normalize({"nodes": [
        {"id": "Character", "type": "IMAGE", "fields": {"source": {"kind": "ASSET", "id": SKETCH}}},
        {"id": "Parts sheet", "type": "GENERATE_IMAGE", "fields": {
            "connection_id": IMAGE_ACCOUNT, "model": "image-model", "prompt": "parts sheet", "review": True}},
        {"id": "Split", "type": "SPLIT_SHEET", "fields": {"slots": SLOTS}},
        {"id": name, "type": "GENERATE_3D_EACH", "fields": {"connection_id": MESH_ACCOUNT, "model": "mesh-model"},
         **({"chosen": chosen} if chosen else {})},
        {"id": "Place", "type": "PLACE_CHARACTER"},
    ], "links": [
        {"from": "Character", "to": "Parts sheet", "input": "references"},
        {"from": "Parts sheet", "to": "Split", "input": "sheet"},
        {"from": "Split", "to": name, "input": "parts"},
        {"from": "Split", "to": "Place", "input": "parts"},
        {"from": name, "to": "Place", "input": "models"},
    ]})


def crop(assets):
    identity = str(uuid.uuid4())
    assets[identity] = {"id": identity, "kind": "IMAGE", "status": "ready", "files": ["crop.png"], "created": len(assets),
                        "lineage": {"source": "split"}}
    return identity


def manifest(assets):
    return [{"id": "body", "slot": "body", "kind": "BODY", "item": 0, "asset": crop(assets), "box": [-200, 30, 200, 930]},
            {"id": "hair", "slot": "hair", "kind": "HAIR", "item": 0, "asset": crop(assets), "box": [-90, 20, 90, 300]},
            {"id": "accessories-1", "slot": "accessories", "kind": "ACCESSORY", "item": 0, "numbered": True,
             "asset": crop(assets), "box": [-60, 200, -20, 240]},
            {"id": "accessories-2", "slot": "accessories", "kind": "ACCESSORY", "item": 1, "numbered": True,
             "asset": crop(assets), "box": [20, 200, 60, 240]}]


def sheet_ready(flow, assets):
    """Generate the parts sheet in the fake library; returns its asset ID."""
    states = evaluate(flow, LibraryIndex(assets), catalog=catalog)
    return generated(assets, states["Parts sheet"]["key"], 0, node="Parts sheet")


def split_states(flow, assets, parts=None, sheet=None):
    states = evaluate(flow, LibraryIndex(assets), catalog=catalog)
    request = states["Split"].get("split_request")
    splits = {"Split": {"key": request["key"], "parts": parts or manifest(assets), "cell_height": 940}} if request else {}
    return evaluate(flow, LibraryIndex(assets), catalog=catalog, splits=splits), splits


class DefinitionTests(unittest.TestCase):
    def test_slots_need_one_body_unique_names_and_no_slash(self):
        bad = [[{"id": "hair", "kind": "HAIR"}],
               [{"id": "a", "kind": "BODY"}, {"id": "a", "kind": "HAIR"}],
               [{"id": "a/b", "kind": "BODY"}],
               [{"id": "a", "kind": "BODY"}, {"id": "b", "kind": "BODY"}],
               [{"id": "a", "kind": "BODY", "items": "many"}]]
        for slots in bad:
            with self.subTest(slots=slots), self.assertRaises(FlowError):
                normalize({"nodes": [{"id": "S", "type": "SPLIT_SHEET", "fields": {"slots": slots}}]})

    def test_per_part_step_makes_one_model_per_part(self):
        with self.assertRaises(FlowError):
            normalize({"nodes": [{"id": "P", "type": "GENERATE_3D_EACH", "fields": {"variants": 2}}]})
        node = next(n for n in character_flow()["nodes"] if n["id"] == "Parts 3D")
        self.assertEqual((node["variants"], node["review"]), (1, False))

    def test_sockets_refuse_wrong_kinds(self):
        flow = character_flow()
        with self.assertRaises(FlowError):
            apply_edit(flow, {"links": [{"from": "Character", "to": "Parts 3D", "input": "parts"}]})
        with self.assertRaises(FlowError):
            apply_edit(flow, {"links": [{"from": "Parts sheet", "to": "Place", "input": "models"}]})

    def test_per_part_choices_are_kept_by_part_name(self):
        choice = str(uuid.uuid4())
        flow = character_flow(hair=choice)
        node = next(n for n in flow["nodes"] if n["id"] == "Parts 3D")
        self.assertEqual(node["chosen"], {"hair": choice})


class EvaluationTests(unittest.TestCase):
    def test_before_the_sheet_only_the_sheet_is_planned(self):
        flow, assets = character_flow(), library()
        states = evaluate(flow, LibraryIndex(assets), catalog=catalog)
        self.assertEqual(states["Parts sheet"]["state"], MISSING)
        self.assertEqual([states[n]["state"] for n in ("Split", "Parts 3D", "Place")], [WAITING, WAITING, WAITING])
        lines = plan(flow, states)["lines"]
        self.assertEqual([(l["node"], l["action"]) for l in lines], [("Parts sheet", "GENERATE")])

    def test_a_ready_sheet_asks_for_a_split_keyed_by_sheet_and_layout(self):
        flow, assets = character_flow(), library()
        sheet = sheet_ready(flow, assets)
        states = evaluate(flow, LibraryIndex(assets), catalog=catalog)
        request = states["Split"]["split_request"]
        self.assertEqual(request["sheet"], sheet)
        self.assertEqual(request["key"], split_key(sheet, next(n for n in flow["nodes"] if n["id"] == "Split")))
        stale = {"Split": {"key": "another-sheet", "parts": manifest(assets)}}
        self.assertEqual(evaluate(flow, LibraryIndex(assets), catalog=catalog, splits=stale)["Split"]["state"], WAITING)
        failed = {"Split": {"key": request["key"], "error": "The sheet has no base body in its body column"}}
        self.assertEqual(evaluate(flow, LibraryIndex(assets), catalog=catalog, splits=failed)["Split"]["state"], BLOCKED)

    def test_split_expands_one_generate_3d_step_per_part_with_its_crop(self):
        flow, assets = character_flow(), library()
        sheet_ready(flow, assets)
        states, splits = split_states(flow, assets)
        self.assertEqual(states["Split"]["state"], READY)
        expanded = expand_parts(flow, states)
        names = members(expanded, "Parts 3D")
        self.assertEqual(names, ["Parts 3D/body", "Parts 3D/hair", "Parts 3D/accessories-1", "Parts 3D/accessories-2"])
        states = evaluate(expanded, LibraryIndex(assets), catalog=catalog, splits=splits)
        self.assertTrue(all(states[n]["state"] == MISSING for n in names))
        crops = [p["asset"] for p in splits["Split"]["parts"]]
        self.assertEqual([states[n]["references"] for n in names], [[("asset", c)] for c in crops])
        lines = [l for l in plan(expanded, states)["lines"] if l["action"] == "GENERATE"]
        self.assertEqual([l["node"] for l in lines], names, "one itemized, approved line per part")
        self.assertEqual(lines[1]["label"], "Parts 3D › hair")
        self.assertEqual(states["Place"]["state"], WAITING)

    def test_the_per_part_node_names_its_steps_in_every_run_scope(self):
        """After the split the per-part node is its steps (Studio's Make 3D, the node's buttons, the agent's scopes)."""
        from angelo_agent.flow_graph import scope_nodes
        flow, assets = character_flow(), library()
        sheet_ready(flow, assets)
        states, splits = split_states(flow, assets)
        expanded = expand_parts(flow, states)
        steps = set(members(expanded, "Parts 3D"))
        self.assertTrue(steps and steps <= scope_nodes(expanded, "NODE", "Parts 3D"))
        self.assertIn("Place", scope_nodes(expanded, "DOWNSTREAM", "Parts 3D"))
        self.assertIn("Parts sheet", scope_nodes(expanded, "UPSTREAM", "Parts 3D"))
        states = evaluate(expanded, LibraryIndex(assets), catalog=catalog, splits=splits)
        lines = [l["node"] for l in plan(expanded, states, scope="NODE", node="Parts 3D")["lines"] if l["action"] == "GENERATE"]
        self.assertEqual(set(lines), steps)
        with self.assertRaises(FlowError):
            scope_nodes(expanded, "NODE", "Nothing")

    def test_runs_parts_and_places_what_is_ready(self):
        flow, assets = character_flow(), library()
        sheet_ready(flow, assets)
        states, splits = split_states(flow, assets)
        expanded = expand_parts(flow, states)
        states = evaluate(expanded, LibraryIndex(assets), catalog=catalog, splits=splits)
        selection = select(plan(expanded, states), expanded)
        host = PartsHost(expanded, assets, splits)
        run = FlowRun("Character", selection["now"], host, later=selection["later"], stops=selection["stops"])
        run.tick()
        self.assertEqual(len(host.started), 4)
        host.finish(skip=1)  # The hair's generation fails; the others finish.
        run.tick()
        self.assertEqual(run.steps["Parts 3D/body"]["state"], DONE)
        states = host.states()
        place = states["Place"]
        self.assertEqual(place["state"], READY)
        self.assertEqual(set(place["value"]["models"]), {"body", "accessories-1", "accessories-2"})
        self.assertIn("hair", place["reason"])

    def test_a_fit_to_the_image_reaches_place_and_pays_for_nothing_again(self):
        flow, assets = character_flow(), library()
        sheet_ready(flow, assets)
        states, splits = split_states(flow, assets)
        expanded = expand_parts(flow, states)
        before = evaluate(expanded, LibraryIndex(assets), catalog=catalog, splits=splits)
        fit = {"size": [768, 1024], "body": [200, 50, 560, 990], "boxes": {"hair": [300, 40, 460, 200]}}
        fitted = {name: dict(found, grounding=fit) for name, found in splits.items()}
        host = PartsHost(expanded, assets, fitted)
        after = host.states()
        self.assertEqual(after["Split"]["value"]["grounding"], fit)
        self.assertEqual({name: state.get("key") for name, state in before.items()},
                         {name: state.get("key") for name, state in after.items()}, "no step's request changes")
        selection = select(plan(expanded, after), expanded)
        run = FlowRun("Character", selection["now"], host, later=selection["later"], stops=selection["stops"])
        run.tick()
        host.finish()
        run.tick()
        place = host.states()["Place"]
        self.assertEqual(place["state"], READY)
        self.assertEqual(place["value"]["grounding"], fit)
        self.assertIsNone(before["Split"]["value"]["grounding"], "no fit: Place uses the sheet's boxes")

    def test_a_new_sheet_ignores_the_old_split_and_parts(self):
        flow, assets = character_flow(), library()
        sheet_ready(flow, assets)
        states, splits = split_states(flow, assets)
        other = generated(assets, states["Parts sheet"]["key"], 1, node="Parts sheet")
        flow = apply_edit(flow, {"nodes": [{"id": "Parts sheet", "fields": {}}]})
        flow["nodes"][1]["chosen"] = other
        states = evaluate(flow, LibraryIndex(assets), catalog=catalog, splits=splits)
        self.assertEqual(states["Split"]["state"], WAITING, "the stored split belongs to the other sheet")
        self.assertIs(expand_parts(flow, states), flow)

    def test_too_long_part_names_block_the_step(self):
        flow, assets = character_flow(name="P" * 50), library()
        sheet_ready(flow, assets)
        states, _ = split_states(flow, assets)
        expanded = expand_parts(flow, states)
        node = next(n for n in expanded["nodes"] if n["id"] == "P" * 50)
        self.assertIn("longer than 63", node["problem"])
        self.assertEqual(evaluate(expanded, LibraryIndex(assets), catalog=catalog)["P" * 50]["state"], BLOCKED)


class PartsHost:
    """FakeHost for an expanded character flow: evaluation includes the stored split."""

    def __init__(self, flow, assets, splits):
        self.flow, self.assets, self.splits, self.started = flow, assets, splits, []

    def states(self):
        return evaluate(self.flow, LibraryIndex(self.assets), catalog=catalog, splits=self.splits)

    def request(self, line):
        return request_for(self.flow, self.states(), line["node"])

    def next_variant(self, key):
        return len(LibraryIndex(self.assets).by_key.get(key, []))

    def start(self, line, spec, key, variant):
        identity = generated(self.assets, key, variant, status="generating", node=line["node"])
        self.assets[identity]["kind"] = "3D"
        self.started.append((line["node"], identity))
        return identity

    def asset(self, identity):
        return self.assets.get(identity)

    def capacity(self):
        return 8

    def chosen(self, node, key):
        return self.states()[node]["chosen"]

    def stop(self, identity):
        pass

    def finish(self, skip=None):
        for index, (_, identity) in enumerate(self.started):
            failed = index == skip
            self.assets[identity].update(status="failed" if failed else "ready", files=[] if failed else ["model.glb"])


if __name__ == "__main__":
    unittest.main()
