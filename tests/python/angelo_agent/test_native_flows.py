# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Generation Flow definitions, planning and runs, without Blender or providers."""

import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent.flow_spec import (FlowError, apply_edit, normalize, revision, validate_flow_tool,
                                    as_definition, MAX_NODES)
from angelo_agent.flow_graph import (LibraryIndex, evaluate, plan, select, check_selection, request_for,
                                     READY, MISSING, STALE, PARTIAL, RUNNING, FAILED, BLOCKED, WAITING)
from angelo_agent.flow_runner import FlowRun

IMAGE_ACCOUNT = str(uuid.UUID(int=1))
MESH_ACCOUNT = str(uuid.UUID(int=2))
SKETCH = str(uuid.UUID(int=3))


def catalog(connection, model):
    if connection == IMAGE_ACCOUNT and model == "image-model":
        return {"kind": "IMAGE", "ready": True, "name": "Image Model", "references_max": 4}
    if connection == MESH_ACCOUNT and model == "mesh-model":
        return {"kind": "3D", "ready": True, "name": "Mesh Model", "references_max": 1}
    return None


def hero_flow(**sheet):
    """Sketch + style text → 4-variant sheet (review) → turnaround → 3D → place."""
    sheet_fields = {"connection_id": IMAGE_ACCOUNT, "model": "image-model", "prompt": "Character sheet", "variants": 4}
    sheet_fields.update(sheet)
    return normalize({"nodes": [
        {"id": "Sketch", "type": "IMAGE", "fields": {"source": {"kind": "ASSET", "id": SKETCH}}},
        {"id": "Style", "type": "TEXT", "fields": {"text": "toon style"}},
        {"id": "Sheet", "type": "GENERATE_IMAGE", "fields": sheet_fields},
        {"id": "Turnaround", "type": "GENERATE_IMAGE", "fields": {"connection_id": IMAGE_ACCOUNT, "model": "image-model", "prompt": "front, side, back"}},
        {"id": "Mesh", "type": "GENERATE_3D", "fields": {"connection_id": MESH_ACCOUNT, "model": "mesh-model"}},
        {"id": "Place", "type": "PLACE_IN_SCENE"},
    ], "links": [
        {"from": "Sketch", "to": "Sheet", "input": "references"},
        {"from": "Style", "to": "Sheet", "input": "prompt"},
        {"from": "Sheet", "to": "Turnaround", "input": "references"},
        {"from": "Turnaround", "to": "Mesh", "input": "reference"},
        {"from": "Mesh", "to": "Place", "input": "model"},
    ]})


def library():
    return {SKETCH: {"id": SKETCH, "kind": "IMAGE", "status": "ready", "files": ["image.png"], "created": 0}}


def generated(assets, key, variant, status="ready", node="Sheet"):
    identity = str(uuid.uuid4())
    assets[identity] = {"id": identity, "kind": "IMAGE", "status": status, "files": ["image-1.png"] if status == "ready" else [],
                        "created": len(assets), "lineage": {"key": key, "variant": variant, "node": node}}
    return identity


class FlowDefinitionTests(unittest.TestCase):
    def test_defaults_and_review_follow_variants(self):
        flow = hero_flow()
        sheet = next(n for n in flow["nodes"] if n["id"] == "Sheet")
        turnaround = next(n for n in flow["nodes"] if n["id"] == "Turnaround")
        self.assertTrue(sheet["review"])
        self.assertFalse(turnaround["review"])
        self.assertEqual(turnaround["variants"], 1)
        self.assertEqual(sheet["options"], {})

    def test_rejects_bad_graphs_before_anything_runs(self):
        base = as_definition(hero_flow())
        cases = {
            "cycle": {"from": "Turnaround", "to": "Sheet", "input": "references"},
            "kind": {"from": "Style", "to": "Sheet", "input": "references"},
            "input": {"from": "Sketch", "to": "Sheet", "input": "mask"},
            "missing": {"from": "Nope", "to": "Sheet", "input": "references"},
            "limit": {"from": "Sketch", "to": "Mesh", "input": "reference"},
        }
        for name, link in cases.items():
            with self.subTest(name), self.assertRaises(FlowError):
                normalize(dict(base, links=base["links"] + [link]))
        with self.assertRaises(FlowError):
            normalize(dict(base, nodes=base["nodes"] + [base["nodes"][0]]))
        with self.assertRaises(FlowError):
            normalize({"nodes": [{"id": "x", "type": "IMAGE", "fields": {"source": {"kind": "URL", "id": "http://insecure"}}}]})
        with self.assertRaises(FlowError):
            normalize({"nodes": [{"id": "G", "type": "GENERATE_IMAGE", "fields": {"connection_id": "nope", "model": "m"}}]})
        with self.assertRaises(FlowError):
            normalize({"nodes": [{"id": str(i) + "x", "type": "TEXT", "fields": {"text": "t"}} for i in range(MAX_NODES + 1)]})

    def test_edits_keep_fields_and_refuse_type_changes(self):
        flow = hero_flow()
        edited = apply_edit(flow, {"nodes": [{"id": "Sheet", "fields": {"prompt": "New sheet"}}]})
        sheet = next(n for n in edited["nodes"] if n["id"] == "Sheet")
        self.assertEqual((sheet["prompt"], sheet["variants"], sheet["model"]), ("New sheet", 4, "image-model"))
        self.assertNotEqual(revision(flow), revision(edited))
        self.assertEqual(revision(flow), revision(hero_flow()))
        with self.assertRaises(FlowError):
            apply_edit(flow, {"nodes": [{"id": "Sheet", "type": "TEXT", "fields": {"text": "x"}}]})
        removed = apply_edit(flow, {"remove": ["Turnaround"]})
        self.assertFalse(any("Turnaround" in (l["from"], l["to"]) for l in removed["links"]))
        unlinked = apply_edit(flow, {"unlink": [{"to": "Sheet", "input": "prompt"}],
                                     "nodes": [{"id": "Hat", "type": "TEXT", "fields": {"text": "a hat"}}],
                                     "links": [{"from": "Hat", "to": "Sheet", "input": "prompt"}]})
        self.assertEqual([l["from"] for l in unlinked["links"] if l["to"] == "Sheet" and l["input"] == "prompt"], ["Hat"])
        created = apply_edit({}, {"nodes": [{"id": "Note", "type": "TEXT", "fields": {"text": "hello"}}]})
        self.assertEqual(created["nodes"][0]["text"], "hello")

    def test_tool_arguments(self):
        with self.assertRaises(FlowError):
            validate_flow_tool("angelo_flow_run", {"flow": "F", "expected_revision": None, "scope": "NODE"})
        with self.assertRaises(FlowError):
            validate_flow_tool("angelo_flow_run", {"flow": "F", "expected_revision": "XYZ", "scope": "ALL"})
        with self.assertRaises(FlowError):
            validate_flow_tool("angelo_flow_choose", {"flow": "F", "expected_revision": None, "node": "A", "asset_id": SKETCH})
        with self.assertRaises(FlowError):
            validate_flow_tool("angelo_flow_inspect", {"flow": "F", "selection": []})
        args = validate_flow_tool("angelo_flow_run", {"flow": "F", "expected_revision": "0123456789ab", "scope": "DOWNSTREAM", "node": "Sheet"})
        self.assertEqual(args["node"], "Sheet")


class FlowPlanningTests(unittest.TestCase):
    def test_states_reuse_and_staleness(self):
        flow, assets = hero_flow(), library()
        states = evaluate(flow, LibraryIndex(assets), catalog=catalog)
        self.assertEqual(states["Sheet"]["state"], MISSING)
        self.assertEqual(states["Sheet"]["prompt"], "Character sheet\n\ntoon style")
        self.assertEqual(states["Turnaround"]["state"], WAITING)
        key = states["Sheet"]["key"]
        first = generated(assets, key, 0)
        states = evaluate(flow, LibraryIndex(assets), catalog=catalog)
        self.assertEqual((states["Sheet"]["state"], states["Sheet"]["chosen"]), (PARTIAL, first))
        self.assertEqual(states["Turnaround"]["state"], MISSING)
        for variant in (1, 2, 3):
            generated(assets, key, variant)
        self.assertEqual(evaluate(flow, LibraryIndex(assets), catalog=catalog)["Sheet"]["state"], READY)
        edited = apply_edit(flow, {"nodes": [{"id": "Style", "fields": {"text": "realistic"}}]})
        states = evaluate(edited, LibraryIndex(assets), catalog=catalog, last_keys={"Sheet": key})
        self.assertEqual(states["Sheet"]["state"], STALE)
        running = evaluate(edited, LibraryIndex(assets), catalog=catalog)["Sheet"]["key"]
        generated(assets, running, 0, status="generating")
        self.assertEqual(evaluate(edited, LibraryIndex(assets), catalog=catalog)["Sheet"]["state"], RUNNING)

    def test_failures_and_blocks_are_explained(self):
        flow, assets = hero_flow(), library()
        key = evaluate(flow, LibraryIndex(assets), catalog=catalog)["Sheet"]["key"]
        generated(assets, key, 0, status="failed")
        self.assertEqual(evaluate(flow, LibraryIndex(assets), catalog=catalog)["Sheet"]["state"], FAILED)
        broken = apply_edit(flow, {"nodes": [{"id": "Sheet", "fields": {"model": "unknown"}}]})
        state = evaluate(broken, LibraryIndex(assets), catalog=catalog)["Sheet"]
        self.assertEqual(state["state"], BLOCKED)
        self.assertIn("catalog", state["reason"])
        wrong_kind = apply_edit(flow, {"nodes": [{"id": "Sheet", "fields": {"connection_id": MESH_ACCOUNT, "model": "mesh-model"}}]})
        self.assertIn("not IMAGE", evaluate(wrong_kind, LibraryIndex(assets), catalog=catalog)["Sheet"]["reason"])
        missing_source = evaluate(flow, LibraryIndex({}), catalog=catalog)
        self.assertEqual(missing_source["Sheet"]["state"], BLOCKED)

    def test_plan_selection_and_review_stops(self):
        flow, assets = hero_flow(), library()
        states = evaluate(flow, LibraryIndex(assets), catalog=catalog)
        prices = {"image-model": 0.13, "mesh-model": None}
        run_plan = plan(flow, states, price=lambda node: prices[node["model"]])
        lines = {line["node"]: line for line in run_plan["lines"]}
        self.assertEqual([lines[n]["action"] for n in ("Sheet", "Turnaround", "Mesh")], ["GENERATE"] * 3)
        self.assertEqual(lines["Turnaround"]["approval"]["references"], [["run", "Sheet"]])
        self.assertEqual(lines["Mesh"]["depends_on"], ["Turnaround"])
        selection = select(run_plan, flow)
        self.assertEqual([l["node"] for l in selection["now"]], ["Sheet"])
        self.assertEqual([l["node"] for l in selection["later"]], ["Turnaround", "Mesh"])
        self.assertEqual((selection["count"], selection["total"]), (4, 0.52))
        everything = select(run_plan, flow, stops={"Sheet": False})
        self.assertEqual((everything["count"], everything["total"], everything["unknown_prices"]), (6, 0.65, 1))
        self.assertEqual(select(run_plan, flow, excluded=["Turnaround"], stops={"Sheet": False})["count"], 4)
        self.assertEqual(select(run_plan, flow, excluded=["Sheet"])["now"], [])
        with self.assertRaises(FlowError):
            check_selection(select(run_plan, flow, excluded=["Sheet"]))
        # Narrow scopes: a step whose input is outside the run and empty is blocked.
        node_only = plan(flow, states, scope="NODE", node="Turnaround")
        self.assertEqual(node_only["lines"][0]["action"], "BLOCKED")
        upstream = plan(flow, states, scope="UPSTREAM", node="Turnaround")
        self.assertEqual([l["node"] for l in upstream["lines"]], ["Sheet", "Turnaround"])

    def test_unchanged_steps_are_reused(self):
        flow, assets = hero_flow(variants=1), library()
        key = evaluate(flow, LibraryIndex(assets), catalog=catalog)["Sheet"]["key"]
        generated(assets, key, 0)
        run_plan = plan(flow, evaluate(flow, LibraryIndex(assets), catalog=catalog))
        lines = {line["node"]: line for line in run_plan["lines"]}
        self.assertEqual(lines["Sheet"]["action"], "REUSE")
        self.assertEqual(lines["Turnaround"]["action"], "GENERATE")
        self.assertEqual(lines["Turnaround"]["depends_on"], [])


class FakeHost:
    """Studio-like adapter over an in-memory library, for the runner."""

    def __init__(self, flow, assets, slots=4):
        self.flow, self.assets, self.slots = flow, assets, slots
        self.started, self.stopped = [], []

    def states(self):
        return evaluate(self.flow, LibraryIndex(self.assets), catalog=catalog)

    def request(self, line):
        return request_for(self.flow, self.states(), line["node"])

    def next_variant(self, key):
        return len(LibraryIndex(self.assets).by_key.get(key, []))

    def start(self, line, spec, key, variant):
        identity = generated(self.assets, key, variant, status="generating")
        self.started.append((spec, identity))
        return identity

    def asset(self, identity):
        return self.assets.get(identity)

    def capacity(self):
        return self.slots - sum(1 for a in self.assets.values() if a["status"] == "generating")

    def chosen(self, node, key):
        return self.states()[node]["chosen"]

    def stop(self, identity):
        self.stopped.append(identity)

    def finish(self, status="ready"):
        for asset in self.assets.values():
            if asset["status"] == "generating":
                asset.update(status=status, files=["out.png"] if status == "ready" else [])


def approved(flow, assets, **choices):
    run_plan = plan(flow, evaluate(flow, LibraryIndex(assets), catalog=catalog))
    return select(run_plan, flow, **choices)


class FlowRunTests(unittest.TestCase):
    def test_live_summary_does_not_report_terminal_failure_or_partial_completion(self):
        flow, assets = hero_flow(variants=1, review=False), library()
        selection = approved(flow, assets)
        host = FakeHost(flow, assets, slots=0)
        run = FlowRun("Hero", selection["now"], host)
        run.tick()
        self.assertEqual(run.result()["status"], "RUNNING", "waiting for capacity is not failure")
        host.slots = 4
        run.tick()
        self.assertEqual(run.result()["status"], "RUNNING", "a submitted generation is still running")
        host.finish()
        run.tick()
        self.assertEqual(run.steps["Sheet"]["state"], "DONE")
        self.assertFalse(run.finished)
        self.assertEqual(run.result()["status"], "RUNNING", "successful upstream work is not a terminal partial result")
        host.finish("failed")
        run.tick()
        self.assertTrue(run.finished)
        self.assertEqual(run.result()["status"], "PARTIAL", "terminal partial failure is still reported")

    def test_chain_binds_each_output_to_the_next_step(self):
        flow, assets = hero_flow(), library()
        selection = approved(flow, assets, stops={"Sheet": False})
        host = FakeHost(flow, assets)
        run = FlowRun("Hero", selection["now"], host, later=selection["later"])
        run.tick()
        self.assertEqual(len(host.started), 4)  # Sheet's four variants together.
        host.finish()
        run.tick()
        sheet_choice = run.steps["Sheet"]["chosen"]
        self.assertEqual(host.started[-1][0]["references"], [["asset", sheet_choice]])
        host.finish()
        run.tick()
        host.finish()
        run.tick()
        run.tick()
        self.assertTrue(run.finished)
        result = run.result()
        self.assertEqual((result["status"], result["generations_started"], result["automatic_retries"]), ("DONE", 6, 0))
        self.assertNotIn("review_stop", result)

    def test_review_stop_reports_the_choice_and_remaining_steps(self):
        flow, assets = hero_flow(), library()
        selection = approved(flow, assets)
        host = FakeHost(flow, assets)
        run = FlowRun("Hero", selection["now"], host, later=selection["later"], stops=selection["stops"])
        run.tick()
        host.finish()
        run.tick()
        result = run.result()
        self.assertEqual(result["review_stop"]["node"], "Sheet")
        self.assertEqual(len(result["review_stop"]["pick_from"]), 4)
        self.assertEqual(result["review_stop"]["remaining_steps"], ["Turnaround", "Mesh"])

    def test_edit_after_approval_is_refused_without_generating(self):
        flow, assets = hero_flow(), library()
        selection = approved(flow, assets)
        host = FakeHost(flow, assets)
        run = FlowRun("Hero", selection["now"], host)
        host.flow = apply_edit(flow, {"nodes": [{"id": "Sheet", "fields": {"prompt": "Something else"}}]})
        run.tick()
        self.assertEqual(host.started, [])
        self.assertEqual(run.steps["Sheet"]["state"], "FAILED")
        self.assertIn("changed after approval", run.steps["Sheet"]["error"])

    def test_failure_skips_dependents_and_partial_variants_continue(self):
        flow, assets = hero_flow(variants=1), library()
        selection = approved(flow, assets)
        host = FakeHost(flow, assets)
        run = FlowRun("Hero", selection["now"], host)
        run.tick()
        host.finish("failed")
        run.tick()
        self.assertEqual([run.steps[n]["state"] for n in ("Sheet", "Turnaround", "Mesh")], ["FAILED", "SKIPPED", "SKIPPED"])
        self.assertEqual(len(host.started), 1)
        flow, assets = hero_flow(variants=2, review=False), library()
        selection = approved(flow, assets)
        host = FakeHost(flow, assets)
        run = FlowRun("Hero", selection["now"], host)
        run.tick()
        first, second = [identity for _, identity in host.started]
        assets[first].update(status="ready", files=["a.png"])
        assets[second].update(status="failed", error="provider refused")
        run.tick()
        self.assertEqual(run.steps["Sheet"]["state"], "DONE")
        self.assertIn("1 of 2", run.steps["Sheet"]["error"])

    def test_capacity_and_stop(self):
        flow, assets = hero_flow(), library()
        selection = approved(flow, assets)
        host = FakeHost(flow, assets, slots=2)
        run = FlowRun("Hero", selection["now"], host)
        run.tick()
        self.assertEqual(host.started, [])  # Four variants wait for four free slots.
        host.slots = 4
        run.tick()
        run.stop()
        self.assertEqual(len(host.stopped), 4)
        self.assertEqual(run.result()["status"], "STOPPED")


if __name__ == "__main__":
    unittest.main()


class FlowPriceTests(unittest.TestCase):
    def test_every_price_names_a_source_and_unknowns_stay_unknown(self):
        from angelo_agent.flow_prices import PRICES, price, describe
        from angelo_agent.provider_registry import SPECS
        for (mode, model), entry in PRICES.items():
            self.assertTrue(entry["source"].startswith("https://"))
            self.assertIn(model, {m["id"] for m in SPECS[mode]["models"]})
        self.assertEqual(price({"model": "gemini-3-pro-image", "options": {"resolution": "4K"}}), 0.24)
        self.assertEqual(price({"model": "gpt-image-2.5-flare", "options": {"quality": "low", "aspect": "1:1", "resolution": "1K"}}), 0.0059)
        self.assertEqual(price({"model": "gpt-image-2.5-flare", "options": {}}), 0.1070)  # auto is estimated as high
        self.assertIsNone(price({"model": "gpt-image-2.5-flare", "options": {"size": "1600x1200"}}))
        self.assertIsNone(price({"model": "dola-seedream-5-0-pro-260628", "options": {"resolution": "4K"}}))
        self.assertEqual(price({"model": "v3.1-20260211", "options": {}, "_references": 1}), 0.30)
        self.assertEqual(price({"model": "v3.1-20260211", "options": {"texture": False}}), 0.10)
        self.assertIsNone(price({"model": "unknown", "options": {}}))
        self.assertEqual(describe(0, 2), "price unknown")
        self.assertTrue(describe(1.094, 1).startswith("at least ≈ $1.09"))


class FakeReview:
    """The parts of flow_tools.FlowReview the chat card reads (flow_tools needs Blender)."""

    def __init__(self, flow, run_plan):
        self.tree_name, self.flow, self.plan, self.digest = "Hero", flow, run_plan, run_plan["digest"]

    def selection(self, choices=None):
        choices = choices or {}
        return select(self.plan, self.flow, excluded=choices.get("excluded", ()), stops=choices.get("stops"))


def fake_wrap(value, width, size):
    words, lines, line = value.split(), [], ""
    for word in words:
        if len(line) + len(word) + 1 > width // 7:
            lines.append((line, 0))
            line = word
        else:
            line = (line + " " + word).strip()
    return lines + [(line, 0)]


class FlowCardTests(unittest.TestCase):
    def setUp(self):
        from types import SimpleNamespace
        flow, assets = hero_flow(), library()
        review = FakeReview(flow, plan(flow, evaluate(flow, LibraryIndex(assets), catalog=catalog)))
        self.call = SimpleNamespace(id="call", name="angelo_flow_run", precondition=review, approval=True,
                                    arguments={"flow": "Hero", "expected_revision": None, "scope": "ALL"}, usage_label="Fixture")

    def test_approval_card_geometry_and_buttons(self):
        from angelo_agent.flow_chat import approval_layout
        for width in (360, 600, 900):
            ops, height = approval_layout(self.call, 16, 800, width, fake_wrap, 20)
            card, hits = ops[0], [op[1:] for op in ops if op[0] == "hit"]
            self.assertEqual(card[0], "rect")
            for (x, y, w, h), _, _ in hits:
                self.assertTrue(card[1] <= x and x + w <= card[1] + card[3] and card[2] <= y and y + h <= card[2] + card[4], (width, x, y))
            self.assertEqual({h[1] for h in hits}, {"flow_toggle", "flow_stop", "approve", "decline", "flow_details"})
            self.assertGreater(height, 0)
            detailed, taller = approval_layout(self.call, 16, 800, width, fake_wrap, 20, details=True)
            self.assertGreater(taller, height)
            self.assertTrue(any("prompt: Character sheet" in op[1] for op in detailed if op[0] == "text"))

    def test_nothing_selected_has_no_run_button(self):
        from angelo_agent.flow_chat import approval_layout
        self.call.flow_selection = {"excluded": {"Sheet"}, "stops": {}}
        ops, _ = approval_layout(self.call, 16, 800, 600, fake_wrap, 20)
        self.assertNotIn("approve", {op[2] for op in ops if op[0] == "hit"})
        self.assertIn("Nothing selected", [op[1] for op in ops if op[0] == "text"])
        # The disabled button still answers the pointer with why (canvas_controls.DISABLED).
        self.assertIn(("disabled", "Select at least one step above to run"), {(op[2], op[3]) for op in ops if op[0] == "hit"})

    def test_approval_text_lists_every_paid_step(self):
        from angelo_agent.flow_approvals import message
        text = message("angelo_flow_run", dict(self.call.arguments, _review=self.call.precondition), "Fixture")
        self.assertIn("☑ Sheet ×4", text["body"])
        self.assertIn("☐ Turnaround ×1", text["body"])
        self.assertIn("NOT your coding subscription", text["warning"])
        build = message("angelo_flow_build", {"flow": "Hero", "expected_revision": None, "nodes": [{"id": "A"}]})
        self.assertIn("Nothing is generated", build["warning"])


class FlowControllerTests(unittest.TestCase):
    """angelo_flow_run on the real Controller with a mocked Blender executor."""

    def setUp(self):
        import tempfile
        from unittest.mock import Mock
        from angelo_agent.controller import Controller
        from angelo_agent.studio_agent import StudioPending
        self.temp = tempfile.TemporaryDirectory()
        self.controller = Controller(str(Path(self.temp.name) / "state"))
        self.controller.set_document(("fixture", 1))
        self.controller.busy = True
        flow, assets = hero_flow(), library()
        self.review = FakeReview(flow, plan(flow, evaluate(flow, LibraryIndex(assets), catalog=catalog)))
        self.seen, self.answers, self.events = [], [], []
        self.result = None
        def execute(name, arguments):
            call = self.controller.executing_call
            self.seen.append((call.name, call.precondition is self.review, dict(getattr(call, "flow_selection", {}))))
            pending = StudioPending(lambda: self.result, lambda: None)
            pending.run_id, pending.timeout = "run-1", 4000
            return pending
        self.executor = Mock()
        self.executor.precondition.return_value = self.review
        self.executor.usage_label.return_value = "Fixture Gemini · API billing"
        self.executor.execute.side_effect = execute
        emit = self.controller.emit
        self.controller.emit = lambda kind, data: (self.events.append((kind, data)), emit(kind, data))[1]

    def tearDown(self):
        self.controller.cancel_tools("Fixture complete")
        self.controller.close()
        self.temp.cleanup()

    def pump(self):
        # One pump must dispatch the queued call however loaded the host is: the
        # default 8 ms budget is one UI tick and may legitimately defer it.
        from angelo_agent.controller import ToolDispatchBudget
        self.controller.pump_tools(self.executor, self.controller.document, budget=ToolDispatchBudget(seconds=60))

    def request(self):
        self.controller.request_tool("run", "angelo_flow_run", {"flow": "Hero", "expected_revision": None, "scope": "ALL"},
                                     self.answers.append)
        self.pump()
        return next(iter(self.controller.pending.values()))

    def test_run_needs_approval_even_in_auto_and_carries_card_choices(self):
        import time
        self.controller.state["options"] = {"approval": "AUTO_BOUNDED"}
        call = self.request()
        self.assertTrue(call.approval)
        self.assertFalse(call.automatic)
        self.assertEqual(call.usage_label, "Fixture Gemini · API billing")
        self.executor.usage_label.assert_called_with("angelo_flow_run", call.arguments, self.review)
        call.flow_selection = {"excluded": {"Mesh"}, "stops": {"Sheet": False}}
        self.controller.execute_tool(call, self.executor, self.controller.document)
        self.assertEqual(self.seen, [("angelo_flow_run", True, {"excluded": {"Mesh"}, "stops": {"Sheet": False}})])
        self.assertIsNone(self.controller.executing_call)
        self.assertIn(call.id, self.controller.media_jobs)
        self.assertGreater(call.expires, time.monotonic() + 3500)
        running = [d for k, d in self.events if k == "activity" and d.get("status") == "running"]
        self.assertEqual(running[-1]["result"]["run_id"], "run-1")
        self.result = {"run_id": "run-1", "status": "DONE", "steps": []}
        self.controller.pump_events()
        self.assertEqual(self.answers[0]["status"], "DONE")
        self.assertFalse(self.controller.media_jobs)

    def test_read_only_chat_refuses_runs_and_runs_skip_scene_checkpoints(self):
        self.controller.state["options"] = {"approval": "READ_ONLY"}
        self.controller.request_tool("run", "angelo_flow_run", {"flow": "Hero", "expected_revision": None, "scope": "ALL"},
                                     self.answers.append)
        self.pump()
        self.assertIn("read-only", self.answers[0]["error"])
        self.executor.execute.assert_not_called()
        self.assertFalse(self.controller.requires_mutation_checkpoint("angelo_flow_run"))
        self.assertTrue(self.controller.requires_mutation_checkpoint("angelo_flow_build"))
        from angelo_agent.tool_specs import BOUNDED_MUTATIONS
        self.assertIn("angelo_flow_build", BOUNDED_MUTATIONS)
        self.assertNotIn("angelo_flow_run", BOUNDED_MUTATIONS)
