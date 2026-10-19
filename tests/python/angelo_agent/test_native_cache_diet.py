# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Per-turn context deltas, compact handoff receipts, context rebase and a stable prefix.

Pure composition units plus the real Controller and ClaudeProvider over the
unbilled stdio fixture: no Claude process, credentials or inference.
"""

import copy
from contextlib import ExitStack
import json
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from claude_protocol_fixtures import NativeInitializeFixture, SUPPORTED  # noqa: E402
from angelo_agent import context_ledger, context_rebase, memory_context, task_memory  # noqa: E402
from angelo_agent.chat_settings import normalize_models  # noqa: E402
from angelo_agent.claude_outcomes import compact_receipt  # noqa: E402
from angelo_agent.controller import Controller, ToolDispatchBudget  # noqa: E402
from angelo_agent.providers import ClaudeProvider  # noqa: E402
from angelo_agent.routing import TOOLSET_VERSION, handoff, route_key  # noqa: E402
from angelo_agent.sessions import SessionStore  # noqa: E402
from test_native_round_savings import HANDLE, REVISION, committed, program, staged_preview  # noqa: E402

CLAUDE = normalize_models([{"value": "sonnet", "displayName": "Claude Sonnet", "isDefault": True,
                            "supportedEffortLevels": ["low", "medium", "high"]}], "claude")
DOC = ("scratch", 1)
COLLABORATORS = [{"connection_id": str(uuid.UUID(int=7)), "name": "Second account", "provider": "claude_subscription",
                  "billing": "Subscription", "models": [{"id": "opus", "name": "Opus"}, {"id": "sonnet", "name": "Sonnet"}]}]
LEGACY_OBSERVATION = {"scene": "Scene", "object_count": 1, "objects": [
    {"name": "Cube", "type": "MESH", "handle": HANDLE, "location": [0.0, 0.0, 0.0], "selected": True}],
    "truncated": False, "workspace_mode": "MANUAL", "rich_queries": ["angelo_scene_search", "angelo_scene_object",
                                                                   "angelo_scene_relationships", "angelo_scene_delta"],
    "native_exec_ms": 0.1}
V2_OBSERVATION = {"context_version": 2, "scene": "Scene", "object_count": 1, "objects": [
    {"name": "Cube", "type": "MESH", "handle": HANDLE, "location": [0.0, 0.0, 0.0], "selected": True,
     "active": True, "object_revision": REVISION}], "truncated": False, "order": "ACTIVE_THEN_SELECTED_" * 4,
    "revision_note": "Exact handle/object_revision pairs may bind programs directly. " * 6,
    "rich_queries": ["angelo_scene_search", "angelo_scene_object"],
    "changes_since_last_turn": {"status": "TRACKED", "hint": "Changed since your last turn; hints only. " * 3,
                                "objects": [], "selection_changed": False}}


def full_result():
    """A committed program result as large as the benchmark's (about 4 KB)."""
    result = committed()
    result.update(delta={"added": [{"name": "Eval Cube", "type": "MESH", "vertices": 8}], "removed": [],
                         "modified": [], "object_count_before": 3, "object_count_after": 4,
                         "geometry": [{"name": "Eval Cube", "bounds": [[-1, -1, -1], [1, 1, 1]],
                                       "matrix": [1.0] * 16, "mesh_counts": {"vertices": 8}}] * 4,
                         "scope": "Exact bound staged-object delta " * 8},
                  operation_results=[{"kind": "create_primitive", "object": "Eval Cube", "vertices": 8,
                                      "edges": 12, "faces": 6, "padding": "x" * 1200}],
                  native_exec_ms=10.4, native_dispatch_wait_ms=21.7)
    result["live_revisions"]["note"] = "Post-commit handles and exact object revisions " * 6
    return result


def preview_result():
    """A certified preview without automatic preview pixels (none exist here)."""
    return {key: value for key, value in staged_preview().items() if key != "views"}


def activity(call_id, result, *, tool="angelo_scene_program", status="completed"):
    return {"role": "activity", "call_id": call_id, "tool": tool, "arguments": {}, "status": status, "result": result}


SUMMARY = {"role": "system", "text": "Host-verified result (no further model round was needed):\nCommitted.",
           "host_summary": True}


class CompactReceiptTests(unittest.TestCase):
    def test_only_clean_positive_program_outcomes_are_compacted(self):
        receipt = compact_receipt("angelo_scene_program", full_result())
        self.assertEqual(receipt["status"], "committed")
        self.assertEqual(receipt["live_revisions"]["objects"][0]["object_revision"], REVISION)
        self.assertEqual(receipt["changes"], {"added": [{"name": "Eval Cube", "type": "MESH"}]})
        self.assertNotIn("note", json.dumps(receipt))
        self.assertLess(len(json.dumps(receipt)), 400)
        for key, value in (("success", False), ("partial_changes_possible", True), ("replayed", True),
                           ("cleanup_warning", True), ("journal_durable", False), ("error", "x"),
                           ("status", "failed"), ("final", False), ("checks", [])):
            with self.subTest(key=key):
                self.assertIsNone(compact_receipt("angelo_scene_program", dict(full_result(), **{key: value})))
        self.assertIsNone(compact_receipt("angelo_execute_python", full_result()), "Only staged programs")

    def test_a_preview_keeps_its_identity_and_view_ids_but_no_local_paths(self):
        preview = staged_preview()
        preview["views"] = [{"id": "view-1", "path": "/private/state/references/view-1.png", "name": "Staged"}]
        receipt = compact_receipt("angelo_scene_program", preview)
        self.assertEqual(receipt["preview_id"], preview["preview_id"])
        self.assertEqual(receipt["views"], [{"id": "view-1", "name": "Staged"}])
        self.assertNotIn("/private", json.dumps(receipt))

    def test_the_handoff_compacts_only_rows_the_host_itself_summarized(self):
        messages = [{"role": "user", "text": "Create it"}, {"role": "assistant", "text": "Submitting."},
                    activity("c1", full_result()), SUMMARY,
                    {"role": "user", "text": "Again"}, {"role": "assistant", "text": "Submitting."},
                    activity("c2", full_result()),
                    {"role": "system", "text": "Native plan completed. Tool read-backs are shown above."},
                    activity("c3", {"error": "Expected check failed: dimensions"}, status="failed")]
        text, ids = handoff(messages)
        rows = [json.loads(line) for line in text.split(":\n", 1)[1].splitlines()]
        tools = [row for row in rows if row["role"] == "tool_result"]
        self.assertIn("receipt", tools[0])
        self.assertIn("result", tools[1], "Not host-composed: the model must still see the read-back")
        self.assertIn("Expected check failed", tools[2]["result"], "Failures keep their actual result")
        self.assertEqual(ids, {"c1", "c2", "c3"})
        self.assertLess(len(json.dumps(tools[0])), len(json.dumps(tools[1])) / 4)

    def test_a_human_ui_accept_is_handed_off_with_the_hosts_own_summary(self):
        from program_completion_fixtures import completed_job
        with tempfile.TemporaryDirectory() as directory, ExitStack() as cleanup:
            controller = Controller(directory)
            cleanup.callback(controller.close)
            controller.set_document(("human-accept-fixture", 1))
            controller.busy = True
            arguments = {"preview_id": "5ff850ee-6313-4fd5-94fd-c62f452c26d9"}
            controller.request_tool("ui-preview/1", "angelo_accept_preview", arguments, lambda _: None)
            call = next(iter(controller.pending.values()))
            call.local = True
            controller.emit("message", {"role": "user", "text": "Accept this reviewed staged preview (human UI action; no model request).",
                            "request_kind": "native_preview_accept", "preview_id": arguments["preview_id"], "call_id": call.id})
            controller.native_jobs[call.id] = completed_job(program("PREVIEW")["arguments"], full_result())
            accepted = controller.finish_tool(call, full_result())
            controller.pump_events()
            messages = copy.deepcopy(controller.state["messages"])
            text, ids = handoff(messages)
            rows = [json.loads(line) for line in text.split(":\n", 1)[1].splitlines()]
            row = next(row for row in rows if row["role"] == "tool_result")
            self.assertEqual(row["receipt"]["live_revisions"]["objects"][0]["object_revision"], REVISION)
            summary = next(row["text"] for row in rows if row["role"] == "system")
            self.assertIn("Accepted the preview and committed it with one Blender Undo step", summary)
            self.assertEqual(ids, {call.id})
        # Historical JSON with no live-composed host summary cannot gain authority.
        text, _ = handoff([message for message in messages if not message.get("host_summary")])
        self.assertNotIn('"receipt"', text)
        failed = dict(accepted, success=False, status="failed", error="Stale preview")
        text, _ = handoff(messages[:1] + [activity("chat/ui-preview/1", failed, tool="angelo_accept_preview")])
        self.assertIn("Stale preview", text)
        self.assertNotIn('"receipt"', text)

    def test_a_reverted_row_keeps_its_full_result(self):
        messages = [activity("c1", full_result()), SUMMARY]
        messages[0]["reverted"] = True
        text, _ = handoff(messages)
        self.assertIn('"reverted": true', text)


def memory_state(results=(), objective=""):
    state = {"task_memory": task_memory.new_memory("profile")}
    state = task_memory.on_restore(state, DOC, reason="restart")
    for call_id, result in results:
        state = task_memory.on_verified_result(state, DOC, call_id=call_id, tool="angelo_scene_program", result=result)
    if objective:
        memory = state["task_memory"]
        memory["brief"]["objective"] = objective
    return state


class MemoryDeltaTests(unittest.TestCase):
    digest = staticmethod(context_ledger.digest)

    def test_a_full_section_counts_receipts_that_the_handoff_carries(self):
        state = memory_state([("c1", committed()), ("c2", committed())])
        full = memory_context.render_full(state, DOC)
        excluded = memory_context.render_full(state, DOC, exclude_results={"c1"})
        self.assertEqual(full.count("historical_native_result="), 2)
        self.assertEqual(excluded.count("historical_native_result="), 1)
        self.assertIn("historical_native_results_in_handoff=1", excluded)
        self.assertEqual(full, task_memory.render_context(state, DOC), "The public renderer is unchanged")

    def test_deltas_send_only_what_changed(self):
        state = memory_state([("c1", committed())])
        received = memory_context.received_lines(state, DOC, self.digest)
        text, after = memory_context.render_delta(state, DOC, received, self.digest)
        self.assertEqual(text, "", "Nothing changed")
        later = memory_state([("c1", committed()), ("c2", committed())])
        text, after = memory_context.render_delta(later, DOC, received, self.digest, known_results={"c2"})
        self.assertEqual(text, "", "Only the revision moved (a receipt of a result the context has): nothing sent")
        self.assertEqual(after[self.digest("revision")], received[self.digest("revision")],
                         "... and the revision line stays unreceived, to go with the next real change")
        briefed_later = memory_state([("c1", committed()), ("c2", committed())], objective="Stay under 2 m")
        text, _ = memory_context.render_delta(briefed_later, DOC, after, self.digest, known_results={"c2"})
        self.assertIn("revision=", text)
        self.assertIn("untrusted", text, "Every memory block keeps its untrusted-data label")
        unknown, _ = memory_context.render_delta(later, DOC, received, self.digest)
        self.assertIn("historical_native_result=", unknown, "A receipt the context never saw is sent")
        briefed = memory_state([("c1", committed()), ("c2", committed())], objective="Keep the cube under 2 m")
        text, _ = memory_context.render_delta(briefed, DOC, after, self.digest)
        self.assertIn('objective="Keep the cube under 2 m"', text, "A changed brief field is sent whole")

    def test_a_restore_reports_stale_receipts_as_a_count_with_the_reason(self):
        state = memory_state([("c1", committed()), ("c2", committed())])
        received = memory_context.received_lines(state, DOC, self.digest)
        restored = task_memory.on_restore(state, DOC, reason="undo")
        text, _ = memory_context.render_delta(restored, DOC, received, self.digest)
        self.assertIn("Undo: prior native state is stale", text)
        self.assertIn("receipts_now_stale=2", text)
        self.assertNotIn("historical_native_result=", text)

    def test_an_empty_memory_is_one_line_and_later_deltas_send_what_changed(self):
        """Regression: every new chat's first message carried 574 characters of an empty memory."""
        empty = memory_state()
        full = memory_context.render_full(empty, DOC)
        revision = "revision=%d" % empty["task_memory"]["revision"]
        self.assertEqual(full, "\n\n" + memory_context.EMPTY % revision)
        self.assertIn("untrusted", full)
        self.assertLess(len(full), 130)
        self.assertEqual(task_memory.render_context(empty, DOC), full, "The public renderer says the same")
        received = memory_context.received_lines(empty, DOC, self.digest)
        self.assertEqual(memory_context.render_delta(empty, DOC, received, self.digest)[0], "", "Nothing changed")
        later = memory_state([("c1", committed())])
        text, _ = memory_context.render_delta(later, DOC, received, self.digest, known_results={"c1"})
        self.assertIn("stale=", text)
        self.assertIn("revision=", text)
        self.assertNotIn("objective=", text, "Fields still empty are not resent")
        for state in (later, memory_state(objective="Keep it under 2 m")):
            with self.subTest(state=state["task_memory"]["revision"]):
                self.assertIn(memory_context.HEADER[0], memory_context.render_full(state, DOC), "Not empty: full section")

    def test_unbound_memory_is_said_once(self):
        state = {"task_memory": task_memory.new_memory("profile")}
        text, after = memory_context.render_delta(state, ("other", 2), {}, self.digest)
        self.assertEqual(text, memory_context.UNBOUND)
        self.assertEqual(memory_context.render_delta(state, ("other", 2), after, self.digest)[0], "")


class TurnBlockTests(unittest.TestCase):
    def delta(self, first):
        """A second message against the ledger the first one produced."""
        return context_ledger.TurnBlocks(context_ledger.usable(first.ledger("native"), "native"))

    def test_identical_observations_become_one_line_and_volatile_values_do_not_count(self):
        first = context_ledger.TurnBlocks()
        self.assertIn('"rich_queries"', first.observation(LEGACY_OBSERVATION))
        second = self.delta(first)
        self.assertEqual(second.observation(dict(LEGACY_OBSERVATION, native_exec_ms=7.5)),
                         context_ledger.OBSERVATION_UNCHANGED)
        moved = copy.deepcopy(LEGACY_OBSERVATION)
        moved["objects"][0]["location"] = [1.0, 0.0, 0.0]
        text = self.delta(first).observation(moved)
        self.assertIn('"location":[1.0,0.0,0.0]', text)

    def test_a_changed_observation_lists_only_its_changed_entries(self):
        scene = copy.deepcopy(LEGACY_OBSERVATION)
        scene["objects"] += [{"name": "Prop %d" % index, "type": "MESH", "handle": "scn:%s:%d" % ("a" * 32, 200 + index),
                              "location": [float(index), 0.0, 0.0], "selected": False} for index in range(12)]
        scene["object_count"] = len(scene["objects"])
        first = context_ledger.TurnBlocks()
        whole = first.observation(scene)
        later = copy.deepcopy(scene)
        later["objects"][3]["location"] = [9.0, 9.0, 9.0]
        removed = later["objects"].pop()
        added = {"name": "Eval Cube", "type": "MESH", "handle": "scn:%s:999" % ("a" * 32),
                 "location": [0.0, 0.0, 0.0], "selected": False}
        later["objects"].append(added)
        text = self.delta(first).observation(later)
        sent = json.loads(text.split("\n", 3)[3])
        self.assertEqual(sent["objects"], [{"handle": later["objects"][3]["handle"], "location": [9.0, 9.0, 9.0]},
                                           added], "A known entry sends only its changed fields; a new one whole")
        self.assertEqual(sent["unchanged_objects"], len(later["objects"]) - 2)
        self.assertEqual(sent["objects_no_longer_listed"], [removed["handle"]])
        self.assertNotIn("objects_scope", sent, "The delta convention is stated once, in the instructions")
        self.assertNotIn("object_count", sent, "An unchanged state field is not repeated")
        self.assertLess(len(text), len(whole) / 2)
        counted = copy.deepcopy(later)
        counted["object_count"] += 1
        self.assertEqual(json.loads(self.delta(first).observation(counted).split("\n", 3)[3])["object_count"],
                         counted["object_count"], "A changed state field is sent")
        replaced = copy.deepcopy(scene)
        for entry in replaced["objects"]:
            entry["handle"] = entry["handle"].replace("a" * 32, "b" * 32)  # A reload: every handle is new.
        self.assertNotIn("unchanged_objects", self.delta(first).observation(replaced), "Never larger than whole")

    def test_a_mentioned_object_is_listed_even_when_unchanged(self):
        scene = copy.deepcopy(LEGACY_OBSERVATION)
        mentioned = {"name": "Eval Cube", "type": "MESH", "handle": "scn:%s:999" % ("a" * 32),
                     "location": [0.0, 0.0, 0.0], "selected": False, "mentioned": True}
        scene["objects"].append(mentioned)
        first = context_ledger.TurnBlocks()
        first.observation(scene)
        later = copy.deepcopy(scene)
        later["objects"][0]["location"] = [5.0, 0.0, 0.0]
        sent = json.loads(self.delta(first).observation(later).split("\n", 3)[3])
        # A changed entry is sent as its changed fields (toolset 20); a mentioned one whole.
        self.assertEqual(sent["objects"], [{"handle": later["objects"][0]["handle"], "location": [5.0, 0.0, 0.0]},
                                           mentioned])
        self.assertEqual(sent["unchanged_objects"], len(later["objects"]) - 2)
        again = self.delta(first).observation(copy.deepcopy(scene))
        self.assertNotEqual(again, context_ledger.OBSERVATION_UNCHANGED, "Never 'unchanged' when it names an object")
        self.assertEqual(json.loads(again.split("\n", 3)[3])["objects"], [mentioned])
        quiet = copy.deepcopy(scene)
        quiet["objects"][-1]["mentioned"] = False
        quiet_first = context_ledger.TurnBlocks()
        quiet_first.observation(quiet)
        self.assertEqual(self.delta(quiet_first).observation(copy.deepcopy(quiet)), context_ledger.OBSERVATION_UNCHANGED)

    def test_a_changed_turn_context_omits_only_its_unchanged_notes(self):
        first = context_ledger.TurnBlocks()
        full = first.observation(V2_OBSERVATION)
        changed = copy.deepcopy(V2_OBSERVATION)
        changed["selected_count"] = 0
        text = self.delta(first).observation(changed)
        self.assertNotIn("revision_note", text)
        self.assertNotIn("Changed since your last turn", text)
        self.assertNotIn("static_notes", text, "Unchanged notes are omitted; the instructions say so once")
        self.assertIn('"status":"TRACKED"', text)
        self.assertLess(len(text), len(full) - 400)
        renamed = dict(changed, revision_note="A different note")
        self.assertIn("A different note", self.delta(first).observation(renamed), "Changed notes are sent")
        epoch = dict(changed, changes_since_last_turn={"status": "DOCUMENT_EPOCH_CHANGED", "hint": "Reloaded."})
        self.assertIn("Reloaded.", self.delta(first).observation(epoch), "Event hints are never omitted")

    def test_workspace_and_collaborators_are_sent_once_and_again_only_when_they_change(self):
        first = context_ledger.TurnBlocks()
        self.assertIn("/projects/a", first.workspace("/projects/a"))
        self.assertIn("Second account", first.collaborators(COLLABORATORS))
        second = self.delta(first)
        self.assertEqual(second.workspace("/projects/a"), "")
        self.assertEqual(second.collaborators(COLLABORATORS), "")
        self.assertIn("/projects/b", self.delta(first).workspace("/projects/b"))
        changed = [dict(COLLABORATORS[0], name="Renamed account")]
        self.assertIn("Renamed account", self.delta(first).collaborators(changed))
        self.assertEqual(self.delta(first).collaborators([]), context_ledger.NO_COLLABORATORS)
        never = context_ledger.TurnBlocks()
        never.collaborators([])
        self.assertEqual(self.delta(never).collaborators([]), "", "Nothing to retract")

    def test_the_collaborator_list_is_compact_with_model_ids_as_keys(self):
        """Regression: one 13-model Claude connection took 857 characters as [{"id", "name"}] rows with spaces."""
        listed = [dict(COLLABORATORS[0], models={"opus": "Opus 5.5", "claude-opus-4-6": "Opus 4.6"})]  # ui's shape
        text = context_ledger.TurnBlocks().collaborators(listed)
        self.assertEqual(text, context_ledger.COLLABORATORS_HEADER + json.dumps(listed, separators=(",", ":")))
        self.assertIn('"models":{"opus":"Opus 5.5","claude-opus-4-6":"Opus 4.6"}', text)
        self.assertIn("exact connection_id and model id, a key of models", text)
        self.assertNotIn(", ", text.split(":\n", 1)[1].replace("(recommended)", ""), "No separator spaces")

    def test_ledgers_bind_one_native_context_and_survive_persistence(self):
        first = context_ledger.TurnBlocks()
        first.workspace("/projects/a")
        ledger = first.ledger("native-a")
        self.assertIsNone(context_ledger.usable(ledger, "native-b"))
        self.assertIsNone(context_ledger.usable(dict(ledger, version=99), "native-a"))
        self.assertNotIn("workspace", context_ledger.clean(dict(ledger, workspace="not-a-digest")))
        self.assertIsNone(first.ledger(None))
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory, str(uuid.uuid4()))
            state = store.new("claude_subscription")
            state["runtime_sessions"]["claude_subscription"] = {"native_id": "native-a", "synced_length": 2,
                                                                "toolset_version": TOOLSET_VERSION,
                                                                "received": ledger, "context_tokens": 41000}
            store.save(state)
            loaded = store.load()["runtime_sessions"]["claude_subscription"]
            self.assertEqual(loaded["received"], ledger)
            self.assertEqual(loaded["context_tokens"], 41000)
            state["runtime_sessions"]["claude_subscription"]["toolset_version"] = 14
            store.save(state)
            self.assertNotIn("received", store.load()["runtime_sessions"]["claude_subscription"],
                             "A context that is never resumed keeps no ledger")

    def test_compaction_is_a_shrinking_prompt(self):
        self.assertTrue(context_ledger.compacted(60000, 30000))
        self.assertFalse(context_ledger.compacted(60000, 59000))
        self.assertFalse(context_ledger.compacted(None, 1000))


class OfflineClaude(ClaudeProvider):
    def connect(self):
        self.executable, self.env = "claude", {"FIXTURE": "only"}
        return self.controller.state["native_id"]


class ProgramExecutor:
    def __init__(self):
        self.calls, self.window, self.results = [], "window", []

    def supports_native_job(self, name):
        return False

    def precondition(self, name, arguments):
        return "unchanged"

    def execute(self, name, arguments):
        self.calls.append(name)
        result = self.results.pop(0) if self.results else (
            full_result() if name == "angelo_scene_program" else {"objects": [], "success": True})
        if name == "angelo_scene_program":
            from program_completion_fixtures import completed_job
            call = self.studio_context.executing_call
            self.studio_context.native_jobs[call.id] = completed_job(arguments, result)
        return result


class ControllerFixture(unittest.TestCase):
    """The real Controller, ClaudeProvider and the unbilled stdio fixture."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.session, self.connection = str(uuid.uuid4()), str(uuid.UUID(int=8))
        self.c = self.controller(session_id=self.session)
        self.executor, self.prompts, self.plans, self.launches = ProgramExecutor(), [], [], []
        self.input_tokens, self.totals = [], {"inputTokens": 0, "cacheReadInputTokens": 0,
                                              "cacheCreationInputTokens": 0, "outputTokens": 0}
        patches = [patch("angelo_agent.controller.ClaudeProvider", OfflineClaude),
                   patch("angelo_agent.child_process.popen", side_effect=self.launch),
                   patch("angelo_agent.child_process.run",
                         return_value=SimpleNamespace(returncode=0, stdout=SUPPORTED))]
        for value in patches:
            value.start()
            self.addCleanup(value.stop)

    def controller(self, session_id=None, connection_id=None):
        controller = Controller(self.directory.name, session_id=session_id)
        controller.set_document(DOC)
        controller.choose_connection("claude_subscription", {"model": ""}, "", connection_id or self.connection,
                                     CLAUDE, "sonnet")
        controller.state["options"]["approval"] = "AUTO_BOUNDED"
        controller.collaborators = list(COLLABORATORS)
        return controller

    def tearDown(self):
        self.c.close()
        self.directory.cleanup()

    def launch(self, command, **kwargs):
        self.launches.append(list(command))
        # A fresh process starts its running usage totals at zero.
        self.totals = dict.fromkeys(self.totals, 0)
        return NativeInitializeFixture(on_user=self.reply)

    def reply(self, process, event):
        content = event["message"]["content"]
        self.prompts.append(content if isinstance(content, str) else content[0]["text"])
        plan = self.plans.pop(0) if self.plans else {"reply": "Done.", "operations": [], "verify": False}
        result = {"type": "result", "subtype": "success", "is_error": False,
                  "session_id": "native-" + str(len(self.launches)), "structured_output": plan}
        if self.input_tokens:
            # Claude Code reports per-process running totals; one model call per round.
            self.totals["cacheReadInputTokens"] += self.input_tokens.pop(0)
            result["modelUsage"] = {"fixture": dict(self.totals)}
        process.stdout.emit(result)

    def turn(self, text, observation=None, controller=None):
        controller = controller or self.c
        controller.send(text, LEGACY_OBSERVATION if observation is None else observation)
        apply = lambda call: controller.execute_tool(call, self.executor, controller.document)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            controller.pump_events()
            controller.pump_tools(self.executor, controller.document, apply_mutation=apply,
                                  budget=ToolDispatchBudget(seconds=60))
            if not controller.busy and not (controller._worker and controller._worker.is_alive()):
                controller.pump_events()
                return self.prompts[-1]
            time.sleep(.002)
        self.fail("Claude fixture turn did not finish")

    def entry(self, controller=None):
        controller = controller or self.c
        return controller.state["runtime_sessions"][route_key(controller.state.get("connection_id"),
                                                              controller.state["provider"])]


class ControllerDeltaTests(ControllerFixture):
    def test_per_turn_byte_budgets_by_turn_type(self):
        read_only = self.turn("How many objects are there?")
        self.assertIn("Angelo structured task memory", read_only)
        self.assertIn("Available read-only collaborators", read_only)
        self.assertIn("File tools are scoped to this workspace", read_only)
        self.plans = [{"reply": "Creating.", "operations": [program()], "verify": False}]
        edit = self.turn("Create a 2 m cube named 'Eval Cube'.")
        self.assertNotIn("Available read-only collaborators", edit, "Unchanged list stays in the native history")
        self.assertNotIn("File tools are scoped", edit)
        self.assertIn(context_ledger.OBSERVATION_DELTA_UNCHANGED, edit)
        self.assertLess(len(edit.encode()), 400, "A delta message after a read-only turn")
        after_edit = self.turn("Now make it red.")
        self.assertIn('"live":[', after_edit, "The observation does not list the new object, so its identity stays")
        self.assertIn('"object_revision":"%s"' % REVISION[:8], after_edit, "The receipt keeps the post-commit revision")
        self.assertEqual(self.c._identifiers.revision(HANDLE, REVISION[:8]), REVISION, "... which the host maps back")
        self.assertIn('"verified":"Committed (one Undo step)', after_edit)
        self.assertNotIn("Host-verified result", after_edit, "One line replaces the receipt and the summary row")
        self.assertNotIn("operation_results", after_edit)
        self.assertNotIn("historical_native_result=", after_edit, "Its receipt is the handoff row")
        self.assertLess(len(after_edit.encode()), 900, "The message after a host-certified edit")
        self.assertLess(len(read_only.encode()), 2000, "A complete first message")
        recorded = len(json.dumps(full_result()))
        self.assertGreater(recorded, 4000, "The fixture result is as large as a real one")

    def test_a_failed_edit_hands_off_its_full_result(self):
        failure = dict(full_result(), success=False, status="failed", error="Expected check failed: dimensions")
        self.executor.results = [failure]
        self.plans = [{"reply": "Creating.", "operations": [program()], "verify": False}]
        self.turn("Create a cube.")
        text = self.turn("What went wrong?")
        self.assertIn("Expected check failed: dimensions", text)
        self.assertIn('"result":"', text)
        self.assertNotIn('"receipt"', text)

    def test_a_preview_hands_off_its_preview_id(self):
        self.executor.results = [preview_result()]
        self.plans = [{"reply": "Staging.", "operations": [program("PREVIEW")], "verify": False}]
        self.turn("Stage a preview.")
        text = self.turn("Accept it.")
        self.assertIn('"preview_id":"5ff850ee-6313-4fd5-94fd-c62f452c26d9"', text)

    def test_switching_connections_gets_full_context_and_switching_back_resumes_deltas(self):
        self.plans = [{"reply": "Creating.", "operations": [program()], "verify": False}]
        self.turn("Create a cube.")
        self.turn("Thanks.")
        other = str(uuid.UUID(int=9))
        self.c.choose_connection("claude_subscription", {"model": ""}, "", other, CLAUDE, "sonnet")
        switched = self.turn("Continue on the other account.")
        for block in ("Angelo structured task memory", "Available read-only collaborators",
                      "File tools are scoped", '"objects":[', "Shared conversation handoff", "Create a cube."):
            self.assertIn(block, switched)
        self.assertIn('"verified":"Committed', switched, "Provider switches get compact receipts too")
        self.c.choose_connection("claude_subscription", {"model": ""}, "", self.connection, CLAUDE, "sonnet")
        back = self.turn("And back.")
        self.assertNotIn("Available read-only collaborators", back)
        self.assertNotIn("Angelo structured task memory (", back)
        self.assertIn("Continue on the other account.", back, "The turns it missed")

    def test_a_restart_resumes_deltas_and_an_older_toolset_gets_full_context(self):
        self.turn("First.")
        self.c.save()
        self.c.close()
        self.c = self.controller(session_id=self.session)
        resumed = self.turn("After restart.")
        self.assertNotIn("Available read-only collaborators", resumed)
        self.assertIn("--resume=native-1", self.launches[-1])
        self.c.state["toolset_version"] = TOOLSET_VERSION - 1
        self.entry()["toolset_version"] = TOOLSET_VERSION - 1
        self.assertIn("Available read-only collaborators", self.turn("After an update."))

    def test_an_undelivered_or_stopped_message_keeps_the_previous_ledger(self):
        self.turn("First.")
        before = copy.deepcopy(self.entry()["received"])
        blocks = context_ledger.TurnBlocks(context_ledger.usable(before, self.c.state["native_id"]))
        blocks.workspace("/somewhere/else")
        for delivered, status in ((False, "completed"), (True, "interrupted")):
            self.c._pending_ledger = {"route": route_key(None, "claude_subscription"), "blocks": blocks,
                                      "native_id": self.c.state["native_id"]}
            if delivered:
                self.c.record_provider_accepted()
            self.c._commit_ledger(status)
            self.assertEqual(self.entry()["received"], before)

    def test_a_compacted_context_receives_full_blocks_again(self):
        self.input_tokens = [40000, 52000, 20000]
        self.turn("First.")
        self.turn("Second.")
        self.assertEqual(self.entry()["context_tokens"], 52000)
        self.assertIn("received", self.entry())
        self.turn("Third (the runtime compacted before it).")
        self.assertNotIn("received", self.entry())
        self.assertIn("Available read-only collaborators", self.turn("Fourth."))

    def test_codex_chat_threads_receive_deltas_and_a_compaction_resends_them(self):
        from test_native_routing import FakeProvider

        class CodexProvider(FakeProvider):
            """Accepts each message; reports prompt sizes and, on request, a compaction."""
            sizes, compact = [], False

            def run(self, native_id, text, observation=None):
                self.controller.record_provider_turn()
                self.controller.record_provider_accepted()
                for size in self.sizes:
                    self.controller.record_context_tokens(size)
                if self.compact:
                    self.controller.record_context_compaction()
                return super().run(native_id, text, observation)

        CodexProvider.instances = []
        codex = Controller(self.directory.name + "/codex")
        codex.set_document(DOC)
        codex.choose_connection("codex_subscription", {"model": ""}, "", None, [], "")
        codex.collaborators = list(COLLABORATORS)

        def turn(text, sizes=(), compact=False):
            CodexProvider.sizes, CodexProvider.compact = list(sizes), compact
            codex.send(text, LEGACY_OBSERVATION)
            deadline = time.monotonic() + 5
            while (codex.busy or (codex._worker and codex._worker.is_alive())) and time.monotonic() < deadline:
                codex.pump_events()
                time.sleep(.002)
            codex.pump_events()
            return CodexProvider.instances[-1].prompt

        with patch("angelo_agent.controller.CodexProvider", CodexProvider):
            first = turn("First.", sizes=[40000])
            self.assertIn("Available read-only collaborators", first)
            self.assertNotIn('"rich_queries"', first, "CODEX_CONTEXT_GUIDANCE carries the turn-context notes")
            second = turn("Second.", sizes=[42000])
            self.assertNotIn("Available read-only collaborators", second, "Unchanged list stays in the thread")
            self.assertLess(len(second), len(first))
            turn("Third.", compact=True)
            self.assertIn("Available read-only collaborators", turn("Fourth."), "thread/compacted resends blocks")
            turn("Fifth.", sizes=[60000, 20000])
            self.assertIn("Available read-only collaborators", turn("Sixth."), "A shrunken prompt resends blocks")
        key = codex.context_key()
        self.assertEqual(codex.state["runtime_sessions"][key]["context_tokens"], 20000)
        codex.close()


class ContextRebaseTests(ControllerFixture):
    def test_it_is_off_by_default(self):
        self.turn("First.")
        self.entry()["context_tokens"] = 10 ** 6
        self.turn("Second.")
        self.assertEqual(len(self.launches), 1, "The warm session continues")
        self.assertEqual(self.c.state["native_id"], "native-1")
        self.assertFalse(any(m.get("context_rebase") for m in self.c.state["messages"]))

    def test_a_large_context_continues_in_a_seeded_fresh_session(self):
        self.c.context_rebase_tokens = 50000
        self.input_tokens = [30000, 60000, 26000]
        self.plans = [{"reply": "Staging.", "operations": [program("PREVIEW")], "verify": False}]
        self.executor.results = [preview_result()]
        self.turn("Make a cube that is exactly 2 m wide; keep it centred.")
        self.turn("Would you like it red? Reply to my question.")
        self.assertEqual(self.entry()["context_tokens"], 60000)
        previews = [{"preview_id": "5ff850ee-6313-4fd5-94fd-c62f452c26d9", "summary": "Moves Eval Cube"}]
        with patch.object(Controller, "native_previews", return_value=previews):
            seeded = self.turn("Yes, make it red.")
        self.assertEqual(len(self.launches), 2, "A fresh process for the fresh session")
        self.assertFalse(any(arg.startswith("--resume") for arg in self.launches[-1]))
        self.assertIn("Context refresh", seeded)
        seed = json.loads(seeded.split(context_rebase.HEADER, 1)[1].split("\n\n", 1)[0])
        self.assertEqual(seed["user_requests_newest_first"],
                         ["Would you like it red? Reply to my question.", "Make a cube that is exactly 2 m wide; keep it centred."])
        self.assertEqual(seed["last_assistant_reply"], "Done.")
        self.assertEqual(seed["open_previews"], previews)
        for block in ("Angelo structured task memory", "Available read-only collaborators", "File tools are scoped",
                      '"objects":[', "Shared conversation handoff"):
            self.assertIn(block, seeded, "A rebased context starts complete")
        notice = [m for m in self.c.state["messages"] if m.get("context_rebase")]
        self.assertEqual(notice[0]["context_rebase"], {"previous_tokens": 60000})
        self.assertEqual(self.c.state["native_id"], "native-2")
        self.assertEqual(self.entry()["context_tokens"], 26000)
        self.assertNotIn("Context refresh", self.turn("Thanks."), "Once")

    def test_a_failed_first_send_after_a_rebase_seeds_the_next_one(self):
        self.c.context_rebase_tokens = 1000
        self.turn("Keep the cube 2 m wide.")
        self.entry()["context_tokens"] = 5000
        with patch.object(Controller, "_initial_request_checkpoint", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                self.c.send("Make it red.", LEGACY_OBSERVATION)
        self.assertIsNone(self.c.state["native_id"])
        seeded = self.turn("Make it red, please.")
        self.assertIn("Keep the cube 2 m wide.", seeded.split(context_rebase.HEADER, 1)[1])
        self.assertNotIn("rebase_pending", self.entry(), "Cleared once the fresh session has an ID")
        self.assertNotIn("Context refresh", self.turn("Thanks."))

    def test_it_never_runs_during_a_turn(self):
        self.c.context_rebase_tokens = 1000
        self.turn("First.")
        self.entry()["context_tokens"] = 5000
        self.c.busy = True
        with self.assertRaises(RuntimeError):
            self.c.send("Interjection", LEGACY_OBSERVATION)
        self.assertEqual(self.c.state["native_id"], "native-1")
        self.assertFalse(any(m.get("context_rebase") for m in self.c.state["messages"]))
        self.c.busy = False
        self.assertTrue(context_rebase.due(1000, 5000, native_id="n", runtime="claude"))
        self.assertFalse(context_rebase.due(1000, 5000, native_id="n", runtime="codex"))
        self.assertFalse(context_rebase.due(0, 5000, native_id="n", runtime="claude"))


class StablePrefixTests(ControllerFixture):
    def test_initialize_payload_is_identical_across_chats_workspaces_and_sessions(self):
        payloads = []
        original = NativeInitializeFixture.receive

        def receive(process, event):
            if event["type"] == "control_request":
                payloads.append(json.dumps(event["request"], sort_keys=True))
            return original(process, event)
        with patch.object(NativeInitializeFixture, "receive", receive):
            self.turn("First chat.")
            other = Controller(self.directory.name + "/elsewhere", session_id=str(uuid.uuid4()))
            other.set_document(("other", 2))
            other.choose_connection("claude_subscription", {"model": ""}, "", None, CLAUDE, "sonnet")
            self.turn("Second chat.", controller=other)
            other.close()
        self.assertEqual(len(payloads), 2)
        self.assertEqual(payloads[0], payloads[1], "Nothing per chat, workspace or session in the prefix")
        for value in (self.directory.name, self.c.state["id"]):
            self.assertNotIn(value, payloads[0])
            self.assertFalse(any(value in argument for argument in self.launches[0]))
        self.assertIn(str(self.c.files.root), self.prompts[0], "The workspace path is first-message context")


if __name__ == "__main__":
    unittest.main()
