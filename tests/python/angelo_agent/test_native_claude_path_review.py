# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Regression tests for the Claude path: streamed replies, name-bound checks and compact handoffs.

Dependency-free host reproductions: no Blender, no Claude or Codex process, no
model. Each finding test reproduced a defect and passes since its fix; a test
named ``..._pin`` pins behaviour verified as sound. The findings: a streamed
reply of a plan that never ran stayed in the transcript (reply_overlay), a
check bound to a created object by name passed on a suffixed name
(program_spec._created_name_targets), compact handoffs dropped a created
handle and isolated data (program_completion.live_rows, commit_facts), and
four PlanReply/plan_progress edge cases.
"""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from claude_protocol_fixtures import ClaudeAdapterFixture, NativeInitializeFixture  # noqa: E402
from test_native_cache_diet import activity  # noqa: E402
from test_native_coverage_identifiers import SCENE, SCENE_REV, table as coverage_table  # noqa: E402
from test_native_round_savings import HANDLE, REVISION, committed  # noqa: E402
from test_native_turn_diet import entry, observation  # noqa: E402
from angelo_agent import context_ledger  # noqa: E402
from angelo_agent.action_catalog import affected_references  # noqa: E402
from angelo_agent.claude_stream import PlanReply  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.identifiers import IdentifierError, IdentifierTable, expand_call  # noqa: E402
from angelo_agent.plan_progress import PlanProgress  # noqa: E402
from angelo_agent.program_checks import required_checks  # noqa: E402
from angelo_agent.program_spec import validate_program  # noqa: E402
from angelo_agent.protocol import RpcError  # noqa: E402
from angelo_agent.routing import handoff  # noqa: E402


def scanned(text, size):
    reply, shown = PlanReply(), ""
    for start in range(0, len(text), size):
        shown += reply.feed(text[start:start + size])
    return shown


class ControllerRows:
    """A real Controller's chat rows for a sequence of provider events (controller._pump_events)."""

    def make_controller(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.controller = Controller(self.directory.name)
        self.addCleanup(self.controller.close)
        self.controller.set_document(("review-claude-path", 1))

    def rows(self, events):
        for kind, data in events:
            self.controller.emit(kind, data)
        self.controller.pump_events()
        return [message.get("text", "") for message in self.controller.state["messages"]
                if message["role"] == "assistant"]


# A plan the runtime's schema check refused (an empty program), whose reply claims work that never ran.
REFUSED = {"operations": [{"tool": "angelo_scene_program", "arguments": {"mode": "APPLY", "operations": []}}],
           "reply": "Deleted the three cubes."}


class StreamedReplyWithdrawalTests(ControllerRows, ClaudeAdapterFixture, unittest.TestCase):
    """Finding A (fixed: streamed replies are drawn, never stored, reply_overlay); the defect:

    A streamed plan reply was withdrawn only when the round returned a plan.

    ``ClaudeProvider._turn`` (providers.py:1148-1157) emits the replacing or
    withdrawing ``message_complete`` only after a successful result. A round
    that raises (runtime error result, schema retries exhausted, process
    failure, timeout: providers.py:1109-1124) or returns None (Stop,
    superseded owner) leaves every streamed reply delta in the
    ``claude-assistant`` row (controller.py:1863-1877), which is persisted and
    handed off as an assistant turn (routing.handoff). Without reply streaming
    only a plan Claude Code returned could put a reply on screen.
    """

    def setUp(self):
        ClaudeAdapterFixture.setUp(self)
        self.make_controller()

    def refused_then_gave_up(self, process, _):
        text, emit = json.dumps(REFUSED), process.stdout.emit
        emit({"type": "stream_event", "event": {"type": "message_start", "message": {"id": "m1", "usage": {}}}})
        emit({"type": "stream_event", "event": {"type": "content_block_start", "index": 0, "content_block": {
            "type": "tool_use", "name": "StructuredOutput", "id": "t0"}}})
        for start in range(0, len(text), 11):
            emit({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {
                "type": "input_json_delta", "partial_json": text[start:start + 11]}}})
        emit({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "StructuredOutput", "id": "t0", "input": REFUSED}]}})
        emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t0", "is_error": True,
              "content": "Output does not match required schema: /operations/0/arguments/operations: "
                         "must NOT have fewer than 1 items"}]}})
        emit({"type": "result", "subtype": "error_max_structured_output_retries", "is_error": True,
              "session_id": "fixture-native", "errors": ["Output does not match required schema"]})

    def test_a_refused_plans_reply_is_not_left_when_the_round_fails(self):
        """The runtime refused the plan and gave up: nothing ran, yet the chat keeps "Deleted the three cubes."."""
        self.popen_mock.return_value = NativeInitializeFixture(on_user=self.refused_then_gave_up)
        with self.assertRaises(RpcError):
            self.turn(None, "Delete the cubes")
        streamed = [(kind, data) for kind, data in self.host.events if kind in ("delta", "message_complete")]
        self.assertTrue(streamed, "precondition: the refused plan's reply streamed")
        # What Controller._run emits next for a raised round (controller.py:1739-1743).
        rows = self.rows(streamed + [("message", {"role": "error", "text": "Output does not match required schema"}),
                                     ("finished", {"status": "failed"})])
        self.assertFalse([row for row in rows if "Deleted" in row],
                         "the reply of a plan that never ran stays in the chat (and in later handoffs)")

    def test_a_refused_plans_reply_is_saved_nowhere_not_even_in_its_call_row(self):
        """Found by test_blender_reply_stream: the runtime's record of the refused call (its activity row, with the
        schema verdict) carried the whole plan, reply included, into the saved transcript and the row's details."""
        self.popen_mock.return_value = NativeInitializeFixture(on_user=self.refused_then_gave_up)
        with self.assertRaises(RpcError):
            self.turn(None, "Delete the cubes")
        self.rows(list(self.host.events) + [("finished", {"status": "failed"})])
        call = next(m for m in self.controller.state["messages"] if m.get("tool") == "StructuredOutput")
        self.assertEqual((call["status"], call["arguments"]), ("failed", {"operations": REFUSED["operations"]}))
        self.assertNotIn("Deleted", json.dumps(self.controller.state))

    def test_a_stop_mid_reply_leaves_no_partial_claim(self):
        """Stop while the reply streams: the plan never runs, the half-written reply stays."""
        rows = self.rows([("delta", {"item": "claude-assistant", "text": "Deleted the three", "replace": True}),
                          ("finished", {"status": "interrupted"})])
        self.assertFalse([row for row in rows if row], "a partial reply of a plan that never ran stays in the chat")


class RoundReplyTests(ControllerRows, unittest.TestCase):
    def setUp(self):
        self.make_controller()

    def test_a_withdrawn_later_round_keeps_the_reply_of_the_round_that_ran(self):
        """Finding A2: a later round's withdrawal erases the reply of an earlier round whose plan ran.

        Every round of a request writes the same ``claude-assistant`` row. A
        later round's streamed reply replaces the row, and its withdrawal
        (``problem``, providers.py:1148-1151, ``replace``) blanks it, so the
        reply of the plan that ran vanishes; chat_view then draws nothing and
        routing.handoff drops the row. Without reply streaming the row kept it.
        """
        rows = self.rows([
            ("message_complete", {"item": "claude-assistant", "text": "Adding a red cube and a lamp.",
                                  "plan_operations": True}),
            ("delta", {"item": "claude-assistant", "text": "Also moving it.", "replace": True}),
            ("message_complete", {"item": "claude-assistant", "text": "", "plan_operations": False, "replace": True})])
        self.assertEqual(rows, ["Adding a red cube and a lamp."])

    def test_a_key_split_across_reply_fragments_is_redacted(self):
        """Finding A5 (note): Controller.redact runs per fragment (controller.py:1876), so a key that spans
        two streamed fragments reaches the row whole. On success the final ``replace`` re-redacts the whole
        reply; on every path of finding A no final row comes, and the key is saved with the chat. The
        per-fragment pattern serves Codex deltas; reply streaming routes Claude plan replies through it.
        (A fake key: no credential is read.)"""
        self.controller.api_key = "sk-review-fake-0123456789"
        rows = self.rows([("delta", {"item": "claude-assistant", "text": "Your key is sk-review-fa", "replace": True}),
                          ("delta", {"item": "claude-assistant", "text": "ke-0123456789."})])
        self.assertFalse([row for row in rows if "sk-review-fake-0123456789" in row])


class PlanReplyScannerTests(unittest.TestCase):
    PLANS = [
        {"operations": [{"tool": "angelo_execute_python", "arguments": {
            "description": "a \"reply\": \"no\"", "code": 'd = {"reply": "not this"}\nprint("}]\\\\", r"\\u00e9")'}},
            {"tool": "angelo_scene_program", "arguments": {"mode": "APPLY", "operations": [
                {"kind": "property_set", "parameters": {"values": {"reply": "nor this", "x": [1, {"reply": "no"}]}}}]}}],
         "reply": "Caveat: é \U0001F600 \"quoted\" back\\slash, tab\t, slash/ and   done."},
        {"reply": "First \U0001F9F1\U0001F9F1", "operations": [], "verify": False},
        {"verify": True, "operations": [{"tool": "t", "arguments": {"reply": "x"}}], "reply": ""},
    ]

    def test_the_top_level_reply_at_every_split_point_pin(self):
        """Verified sound: any chunking, escapes and surrogate pairs split across chunks, nested "reply" keys."""
        for plan in self.PLANS:
            for text in (json.dumps(plan), json.dumps(plan, ensure_ascii=False, separators=(",", ":"))):
                for cut in range(len(text) + 1):
                    reply = PlanReply()
                    with self.subTest(reply=plan["reply"][:10], ascii=text.isascii(), cut=cut):
                        self.assertEqual(reply.feed(text[:cut]) + reply.feed(text[cut:]), plan["reply"])
                for size in range(1, 8):
                    with self.subTest(reply=plan["reply"][:10], ascii=text.isascii(), size=size):
                        self.assertEqual(scanned(text, size), plan["reply"])

    def test_a_reply_that_is_not_a_string_streams_nothing(self):
        """Finding A3 (note): an array or object reply leaves the scanner armed; the next key's name streams.

        claude_stream.PlanReply.feed: ``{`` / ``[`` after the colon keep
        ``_state == "start"``, and the closing bracket returns to depth 1, so
        the next key's opening quote is taken as the reply's (claude_stream.py
        ``if self.depth == 1 and self._state == "start"``). Claude Code refuses
        such a plan, but its streamed text is shown first (and stays, finding A).
        """
        for text in ('{"reply": ["x"], "operations": []}', '{"reply": {}, "operations": []}'):
            with self.subTest(text=text):
                self.assertEqual(scanned(text, 1), "")

    def test_only_a_key_that_decodes_to_reply_streams(self):
        """Finding A4 (note): key text drops escaped characters, so ``re\\"ply`` and ``rep\\nly`` read as reply.

        The key accumulator skips the escape and the escaped character
        (``elif self.escape: self.escape = False``) instead of decoding it.
        """
        for text in ('{"re\\"ply": "streamed", "operations": []}', '{"rep\\nly": "streamed", "operations": []}'):
            with self.subTest(text=text):
                self.assertNotIn("reply", json.loads(text))
                self.assertEqual(scanned(text, 3), "")


class UncheckedReceiptTests(unittest.TestCase):
    """Finding B: the one-line unverified entry drops evidence its full result carried."""

    @staticmethod
    def entry(result):
        rows = [{"role": "user", "text": "Create it"}, activity("c1", result)]
        text, _ = handoff(rows, identifiers=IdentifierTable(), delta=True)
        return json.loads(text.splitlines()[-1])

    def test_a_created_object_whose_revision_was_omitted_keeps_its_handle(self):
        """B1: program_completion.unchecked_receipt keeps a ``live`` row only when ``observed(handle) !=
        row["object_revision"]``. A BUDGET-omitted row (allowed as clean, ``allow_revision_budget``) has no
        revision, and an unobserved handle's ``observed`` is None, so None != None drops the row: the created
        object's handle and its revision_omitted reason vanish. The full result it replaces carried both."""
        result = committed(checks=[])
        row = result["live_revisions"]["objects"][0]
        row.pop("object_revision")
        row["revision_omitted"] = {"reason": "BUDGET", "detail": "cooperative_post_commit_budget"}
        entry = self.entry(result)
        self.assertIn("unverified", entry, "precondition: classified as a clean unchecked commit")
        self.assertIn('"o1"', json.dumps(entry), "the created object's handle is lost from the handoff")

    def test_isolated_shared_data_is_kept(self):
        """B2: commit reports ``isolated_shared_data`` when an edited object stopped sharing its data
        (program_commit.py:384-385; action_camera_light_spec tells the model it is reported). No uncertain
        word is in its key, so the commit is clean, and the unverified entry drops it."""
        result = committed(checks=[], modified=[{"name": "Eval Cube", "fields": ["material_slots"]}])
        result["isolated_shared_data"] = [{"name": "Eval Cube", "data_users_before": 2}]
        entry = self.entry(result)
        self.assertIn("unverified", entry, "precondition: classified as a clean unchecked commit")
        self.assertIn("isolated", json.dumps(entry), "the object no longer shares its data: not said")

    def test_uncertain_or_checked_results_keep_the_full_result_pin(self):
        """Verified sound: unverified effects, a non-budget omission, cleanup warnings and checks stay whole."""
        uncertain = committed(checks=[])
        uncertain["live_revisions"]["objects"][0]["revision_omitted"] = {"reason": "QUERY_FAILED"}
        for result in (dict(committed(checks=[]), unverified_effects=[{"name": "Eval Cube"}]), uncertain,
                       dict(committed(checks=[]), cleanup_warning=True), dict(committed(checks=[]), checks=None),
                       dict(committed(checks=[]), verification={"status": "partial", "unknowns": []})):
            with self.subTest(keys=sorted(set(result) - set(committed(checks=[])))):
                self.assertIn("result", self.entry(result))


RIG = {"kind": "armature_create", "target": None,
       "parameters": {"name": "Rig", "bones": [{"name": "Root", "head": [0, 0, 0], "tail": [0, 0, 1]}]}}
EXISTS = {"kind": "object_exists", "target": "Rig", "expected": True}


class CreatedNameBindingTests(unittest.TestCase):
    @staticmethod
    def table_with_rig():
        table = IdentifierTable()
        context_ledger.compact_observation(observation(entry(HANDLE, REVISION, name="Rig", type="ARMATURE")), table)
        return table

    def test_a_check_bound_to_a_created_armature_by_name_requires_that_name(self):
        """Finding C: a Claude context may bind a check's name to the creation
        (identifiers._infer_program -> program_spec.observed_check_references), where it was once refused
        ("'Rig' is not an object handle"). program_spec._created_name_targets binds target "Rig" to
        $created_1 and relies on a derived object_name check to fail a suffixed creation, but
        armature_create derives no checks (derived_checks._reference excludes it) and its kernel suffixes a
        taken name (action_object._armature -> program_names.name_created). With an existing "Rig" the new
        armature commits as "Rig.001" and the check named "Rig" passes on it."""
        arguments = expand_call("angelo_scene_program", {"mode": "APPLY", "operations": [copy.deepcopy(RIG)],
                                                         "expected_checks": [dict(EXISTS)]}, self.table_with_rig())
        program = validate_program("angelo_scene_program", arguments)
        bound = program["expected_checks"][0]["target"]
        self.assertTrue(bound.startswith("$"), "precondition: the name was bound to the creation")
        names = [check for check in required_checks(program)
                 if check["kind"] == "object_name" and check["target"] == bound]
        self.assertEqual([check.get("expected") for check in names], ["Rig"],
                         "no obligation that the creation got the name the check named")

    def test_a_named_primitive_bound_by_name_must_get_that_name_pin(self):
        """Verified sound: create_primitive's derived object_name check fails a suffixed creation."""
        cube = {"kind": "create_primitive", "target": None,
                "parameters": {"primitive": "CUBE", "name": "Rig", "size": 2, "location": [0, 0, 0]}}
        arguments = expand_call("angelo_scene_program", {"mode": "APPLY", "operations": [cube],
                                                         "expected_checks": [dict(EXISTS)]}, self.table_with_rig())
        program = validate_program("angelo_scene_program", arguments)
        bound = program["expected_checks"][0]["target"]
        self.assertIn({"kind": "object_name", "target": bound, "expected": "Rig"}, required_checks(program))
        for operations, target in (([cube, copy.deepcopy(cube)], "Rig"), ([cube], "Other")):
            with self.subTest(target=target, creations=len(operations)):
                with self.assertRaises(IdentifierError):
                    expand_call("angelo_scene_program", {"mode": "APPLY", "operations": operations,
                                "expected_checks": [dict(EXISTS, target=target)]}, self.table_with_rig())


class CameraPointerTests(unittest.TestCase):
    def test_both_camera_forms_fail_closed_alike_and_bind_alike_pin(self):
        """Verified sound: a later alias, a name or a null pointer are refused in either form, and the
        handle and {"object": handle} forms give the same canonical program, dependencies and checks."""
        write = [{"handle": SCENE, "revision": SCENE_REV}]
        for form in ("$cam", {"object": "$cam"}):
            with self.subTest(later_alias=form), self.assertRaises(ValueError):
                validate_program("angelo_scene_program", {"mode": "APPLY", "write_set": write, "operations": [
                    {"kind": "property_set", "target": SCENE, "parameters": {"values": {"camera": form}}},
                    {"kind": "camera_create", "target": None, "parameters": {"alias": "cam", "name": "Shot"}}]})
        short = [{"handle": "o3", "revision": SCENE_REV[:8]}]
        for form in ("Camera", {"object": "Camera"}):
            with self.subTest(name=form), self.assertRaises(IdentifierError):
                expand_call("angelo_scene_program", {"mode": "APPLY", "write_set": short, "operations": [
                    {"kind": "property_set", "target": "o3", "parameters": {"values": {"camera": form}}}]},
                    coverage_table())
        for form in ({"object": None}, {}, {"object": ""}, {"object": "o2", "x": 1}):
            with self.subTest(malformed=form), self.assertRaises(ValueError):
                validate_program("angelo_scene_program", {"mode": "APPLY", "write_set": write, "operations": [
                    {"kind": "property_set", "target": SCENE, "parameters": {"values": {"camera": form}}}]})
        outcomes = []
        for form in ("o2", {"object": "o2"}):
            arguments = expand_call("angelo_scene_program", {"mode": "APPLY", "write_set": short, "operations": [
                {"kind": "property_set", "target": "o3", "parameters": {"values": {"camera": form}}}],
                "expected_checks": [{"kind": "property", "target": "o3", "values": {"camera": form}}]},
                coverage_table())
            program = validate_program("angelo_scene_program", arguments)
            outcomes.append((program, affected_references(program["operations"][0]), required_checks(program)))
        self.assertEqual(outcomes[0], outcomes[1])


class StaticPrefixTests(unittest.TestCase):
    SCRIPT = ("import hashlib, json, sys; sys.path.insert(0, sys.argv[1]);"
              "from angelo_agent.claude_transport import initialization_request;"
              "from angelo_agent.tool_specs import native_plan_schema;"
              "from angelo_agent.providers import CLAUDE_INSTRUCTIONS, CLAUDE_PLAN_INSTRUCTIONS;"
              "print(hashlib.sha256(json.dumps(initialization_request(native_plan_schema(None), "
              "CLAUDE_INSTRUCTIONS + CLAUDE_PLAN_INSTRUCTIONS)).encode()).hexdigest())")

    def test_the_initialize_payload_bytes_do_not_depend_on_the_hash_seed_pin(self):
        """Verified sound: StablePrefixTests compares sort_keys dumps within one process, which cannot see
        key order or set iteration; the bytes written to Claude Code's stdin are the same across seeds."""
        digests = set()
        for seed in ("0", "1", "4242"):
            environment = dict(os.environ, PYTHONHASHSEED=seed)
            digests.add(subprocess.run([sys.executable, "-c", self.SCRIPT, str(ROOT / "scripts/addons_core")],
                                       env=environment, capture_output=True, text=True, check=True,
                                       timeout=120).stdout.strip())
        self.assertEqual(len(digests), 1)


class PlanProgressTests(unittest.TestCase):
    def test_a_script_is_named_by_its_own_description_never_by_text_in_its_code(self):
        """Finding D (note, display only): plan_progress takes the first ``"description":`` after a script's
        ``"tool"``, including one inside the code's JSON text when the code precedes the description."""
        code = 'import bpy\nmeta = {"description": "from the code"}\n'
        plan = {"operations": [{"tool": "angelo_execute_python",
                                "arguments": {"code": code, "description": "Deck and rails"}}]}
        progress = PlanProgress()
        progress.feed(json.dumps(plan))
        self.assertEqual(progress.names, ["Deck and rails"])


if __name__ == "__main__":
    unittest.main()
