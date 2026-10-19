# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Claude plan's reply streams into the chat as it is written (claude_stream.PlanReply).

Dependency-free: the stream reader, the controller's chat rows and the Claude
provider over the unbilled stdio fixture; no Claude process, Blender or model.
Without streaming an answer shows only with its finished plan; most Opus plans
with a reply are answers (operations ``[]``), whose reply starts about twenty
characters into the plan.
"""

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from claude_protocol_fixtures import ClaudeAdapterFixture, NativeInitializeFixture  # noqa: E402
import fake_provider_cli  # noqa: E402
import reply_stream_fixtures as fixture_rounds  # noqa: E402
from angelo_agent.claude_stream import MAX_REPLY_CHARS, PlanReply, RoundStream  # noqa: E402
from angelo_agent import reply_overlay  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402

ANSWER = {"operations": [], "reply": "The scene has 2 mesh objects: Cube and Brass Marker.\nMESHES: Cube, Brass Marker"}
SCRIPT = ('import bpy\nd = {"reply": "not this"}\nprint("}]\\\\")\nm = bpy.data.materials.new("Grey Stone")')
EDIT = {"operations": [{"tool": "angelo_execute_python", "arguments": {"description": "Deck", "code": SCRIPT}},
                       {"tool": "angelo_scene_program", "arguments": {"mode": "APPLY", "operations": [
                           {"kind": "property_set", "parameters": {"values": {"reply": "nor this"}}}]}}],
        "reply": "Caveat: é \U0001F600 \"quoted\" back\\slash."}


def scanned(text, size):
    reply, shown = PlanReply(), ""
    for start in range(0, len(text), size):
        shown += reply.feed(text[start:start + size])
    return shown


def stream_events(*plans, size=9):
    """The chat events of one round whose StructuredOutput calls carry ``plans``, in fragments of ``size``."""
    stream, events = RoundStream(), []

    def feed(payload):
        events.extend(stream.events({"type": "stream_event", "event": payload}, {}))
    feed({"type": "message_start", "message": {"id": "m1", "usage": {}}})
    for index, plan in enumerate(plans):
        feed({"type": "content_block_start", "index": index,
              "content_block": {"type": "tool_use", "name": "StructuredOutput", "id": "t%d" % index}})
        text = json.dumps(plan)
        for start in range(0, len(text), size):
            feed({"type": "content_block_delta", "index": index,
                  "delta": {"type": "input_json_delta", "partial_json": text[start:start + size]}})
        feed({"type": "content_block_stop", "index": index})
    return stream, [data for kind, data in events if kind == "delta"]


class ScannerTests(unittest.TestCase):
    def test_the_top_level_reply_is_decoded_however_the_text_arrives(self):
        for plan in (ANSWER, EDIT, {"reply": "First", "operations": []}):
            for text in (json.dumps(plan), json.dumps(plan, ensure_ascii=False, separators=(",", ":"))):
                for size in (1, 2, 5, 13, len(text)):
                    with self.subTest(plan=plan["reply"][:12], size=size, ascii=text.isascii()):
                        self.assertEqual(scanned(text, size), plan["reply"])

    def test_nested_replies_null_and_no_reply_show_nothing(self):
        for plan in ({"operations": [{"tool": "x", "arguments": {"reply": "nested"}}]}, {"operations": [], "reply": None},
                     {"operations": [{"tool": "angelo_execute_python", "arguments": {"code": SCRIPT}}]}):
            with self.subTest(plan=plan):
                self.assertEqual(scanned(json.dumps(plan), 3), "")

    def test_text_that_is_not_json_ends_the_stream_and_the_reply_is_bounded(self):
        self.assertEqual(scanned('{"operations": [], "reply": "ok \\uZZZZ more"}', 4), "ok ")
        self.assertEqual(len(scanned(json.dumps({"operations": [], "reply": "x" * (MAX_REPLY_CHARS + 50)}), 512)),
                         MAX_REPLY_CHARS)


class RoundStreamTests(unittest.TestCase):
    def test_an_answer_streams_from_its_first_words_and_the_first_fragment_starts_the_row_over(self):
        stream, deltas = stream_events(ANSWER)
        self.assertEqual("".join(delta["text"] for delta in deltas), ANSWER["reply"])
        self.assertTrue(len(deltas) > 3, "Fragment by fragment, not with the finished plan")
        self.assertEqual([delta.get("replace", False) for delta in deltas], [True] + [False] * (len(deltas) - 1))
        self.assertTrue(all(delta["item"] == "claude-assistant" and delta["plan_reply"] for delta in deltas),
                        "Every fragment is display only until the plan arrives (reply_overlay)")

    def test_a_later_plan_call_replaces_the_earlier_calls_reply(self):
        _, deltas = stream_events(dict(ANSWER, reply="Wrong shape."), EDIT)
        starts = [index for index, delta in enumerate(deltas) if delta.get("replace")]
        self.assertEqual(len(starts), 2)
        self.assertEqual("".join(delta["text"] for delta in deltas[starts[1]:]), EDIT["reply"])

    def test_a_plan_without_a_reply_streams_nothing(self):
        _, deltas = stream_events({"operations": EDIT["operations"]})
        self.assertEqual(deltas, [])


class ControllerRowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.controller = Controller(self.directory.name)
        self.controller.set_document(("reply-stream", 1))

    def tearDown(self):
        self.controller.close()
        self.directory.cleanup()

    def rows(self):
        self.controller.pump_events()
        return [message for message in self.controller.state["messages"] if message["role"] == "assistant"]

    def drawn(self):
        return [message["text"] for message in reply_overlay.shown(self.controller, self.controller.state["messages"])
                if message["role"] == "assistant"]

    def test_a_streamed_reply_is_drawn_never_stored_and_the_plan_says_what_stays(self):
        emit = self.controller.emit
        emit("message_complete", {"item": "claude-assistant", "text": "Earlier round.", "plan_operations": True})
        emit("delta", {"item": "claude-assistant", "text": "The scene ", "plan_reply": True, "replace": True})
        emit("delta", {"item": "claude-assistant", "text": "has 2 meshes.", "plan_reply": True})
        self.assertEqual([row["text"] for row in self.rows()], ["Earlier round."], "Stored: only what a plan said")
        self.assertEqual(self.drawn(), ["The scene has 2 meshes."], "Drawn in its row while it streams")
        saved = json.dumps(self.controller.state)
        self.assertNotIn("2 meshes", saved, "Never in what a save writes")
        emit("message_complete", {"item": "claude-assistant", "text": "", "plan_operations": False})
        self.assertEqual([row["text"] for row in self.rows()], ["Earlier round."], "No reply: the row stays as it was")
        self.assertEqual(self.drawn(), ["Earlier round."])
        emit("delta", {"item": "claude-assistant", "text": "Done", "plan_reply": True, "replace": True})
        emit("message_complete", {"item": "claude-assistant", "text": "Done: 2 meshes.", "plan_operations": False})
        self.assertEqual([row["text"] for row in self.rows()], ["Done: 2 meshes."], "The plan's reply is stored")
        emit("delta", {"item": "claude-assistant", "text": "Half a claim", "plan_reply": True, "replace": True})
        self.rows()
        self.assertEqual(self.drawn(), ["Half a claim"])
        emit("finished", {"status": "failed"})
        self.assertEqual([row["text"] for row in self.rows()], ["Done: 2 meshes."])
        self.assertEqual(self.drawn(), ["Done: 2 meshes."], "A reply whose plan never arrived goes with the request")

    def test_a_reply_with_no_row_yet_is_drawn_after_the_last_and_a_key_never_shows(self):
        self.controller.api_key = "sk-test-fake-0123456789"  # A fake key: no credential is read.
        emit = self.controller.emit
        emit("delta", {"item": "claude-assistant", "text": "Key: sk-tes", "plan_reply": True, "replace": True})
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.drawn(), ["Key: "], "A tail that could begin the key waits for the next fragment")
        emit("delta", {"item": "claude-assistant", "text": "t-fake-0123456789 ok", "plan_reply": True})
        self.rows()
        self.assertEqual(self.drawn(), ["Key: [REDACTED] ok"])
        self.controller._request_generation += 1  # Stop: a newer request; its old reply is not drawn.
        self.assertEqual(self.drawn(), [])

    def test_other_text_deltas_are_stored_redacted_whole(self):
        """Codex replies and Claude text: a key split across two fragments is never stored."""
        self.controller.api_key = "sk-test-fake-0123456789"  # A fake key: no credential is read.
        emit = self.controller.emit
        emit("delta", {"item": "codex-plan", "text": "Your key is sk-test-fa"})
        emit("delta", {"item": "codex-plan", "text": "ke-0123456789."})
        self.assertEqual([row["text"] for row in self.rows()], ["Your key is [REDACTED]."])

    def test_without_replace_an_empty_final_keeps_the_row_as_before(self):
        emit = self.controller.emit
        emit("delta", {"item": "codex-plan", "text": "Built."})
        emit("message_complete", {"item": "codex-plan", "text": ""})
        self.assertEqual([row["text"] for row in self.rows()], ["Built."])


class ProviderTests(ClaudeAdapterFixture, unittest.TestCase):
    def answer(self, process, _):
        """One round as Claude Code streams it: partial messages, the finished block, the result."""
        text = json.dumps(ANSWER)
        process.stdout.emit({"type": "stream_event", "event": {"type": "message_start", "message": {"id": "m1", "usage": {}}}})
        process.stdout.emit({"type": "stream_event", "event": {"type": "content_block_start", "index": 0, "content_block": {
            "type": "tool_use", "name": "StructuredOutput", "id": "t0"}}})
        for start in range(0, len(text), 11):
            process.stdout.emit({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {
                "type": "input_json_delta", "partial_json": text[start:start + 11]}}})
        process.stdout.emit({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "StructuredOutput", "id": "t0", "input": ANSWER}]}})
        process.stdout.emit({"type": "result", "subtype": "success", "is_error": False, "session_id": "fixture-native",
                             "structured_output": ANSWER})

    def test_the_reply_shows_before_the_plan_is_finished_and_the_final_row_replaces_it(self):
        self.popen_mock.return_value = NativeInitializeFixture(on_user=self.answer)
        plan = self.turn(None, "How many meshes?")
        self.assertEqual(plan["reply"], ANSWER["reply"])
        kinds = [(kind, data) for kind, data in self.host.events if kind in ("delta", "message_complete")]
        first_final = next(index for index, (kind, _) in enumerate(kinds) if kind == "message_complete")
        self.assertGreater(first_final, 0, "Reply text reached the chat before the finished plan")
        self.assertEqual("".join(data["text"] for kind, data in kinds[:first_final]), ANSWER["reply"])
        self.assertEqual(kinds[first_final][1], {"item": "claude-assistant", "text": ANSWER["reply"],
                                                  "plan_operations": False})
        self.assertTrue(all(data.get("plan_reply") for _, data in kinds[:first_final]))


class StandInStreamTests(unittest.TestCase):
    """The stand-in ``claude`` CLI of the GUI suites streams plans the way the real reader expects.

    ``test_blender_reply_stream.py`` proves the chat with these rounds; this
    proves the rounds themselves: the stand-in process's stdout, read by the
    app's own ``RoundStream``, streams the reply in fragments and records every
    plan call, as Claude Code 2.1.293 does with fine-grained tool streaming.
    """

    def played(self, events):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "round-0.json").write_text(json.dumps(events), encoding="utf-8")
            user = json.dumps({"type": "user", "message": {"role": "user", "content": "Go"}}) + "\n"
            output = subprocess.run([sys.executable, fake_provider_cli.__file__, "claude", "-p"], input=user,
                                    capture_output=True, text=True, timeout=30, check=True,
                                    env=dict(os.environ, ANGELO_FAKE_CLI_ROUNDS=folder, ANGELO_FAKE_CLI_STATE=folder))
        stream, plans, emitted, lines = RoundStream(), {}, [], [json.loads(line) for line in output.stdout.splitlines()]
        for event in lines:
            emitted += stream.events(event, plans)
        return lines, plans, [data for kind, data in emitted if kind == "delta"]

    def test_an_answer_streams_in_fragments_and_the_runtime_returns_its_plan(self):
        events = fixture_rounds.answered("msg_grow", [fixture_rounds.GROW], 10, 0)
        lines, plans, deltas = self.played(events)
        reply = fixture_rounds.GROW["reply"]
        self.assertGreater(len(deltas), 20, "Fragments, not the whole reply at once")
        self.assertEqual("".join(delta["text"] for delta in deltas), reply)
        self.assertTrue(deltas[0].get("replace") and all(delta["plan_reply"] for delta in deltas))
        self.assertEqual(plans, {"toolu_msg_grow_0": fixture_rounds.GROW})
        self.assertEqual(lines[-1]["structured_output"], fixture_rounds.GROW)
        fragments = [line["event"]["delta"]["partial_json"] for line in lines
                     if line.get("event", {}).get("delta", {}).get("type") == "input_json_delta"]
        self.assertEqual(json.loads("".join(fragments)), fixture_rounds.GROW)
        self.assertLessEqual(max(map(len, fragments)), 10)

    def test_a_second_call_starts_its_reply_over_and_both_calls_are_recorded(self):
        withdrawn = list(fixture_rounds.WITHDRAWN)
        _, plans, deltas = self.played(fixture_rounds.answered("msg_withdrawn", withdrawn, 8, 0))
        starts = [index for index, delta in enumerate(deltas) if delta.get("replace")]
        self.assertEqual(len(starts), 2)
        self.assertEqual("".join(delta["text"] for delta in deltas[starts[1]:]), withdrawn[1]["reply"])
        self.assertEqual(list(plans.values()), withdrawn)

    def test_a_refused_call_is_dropped_and_the_round_gives_up(self):
        events = fixture_rounds.round_events("msg_refused", [fixture_rounds.REFUSED], 6, 0,
                                             fixture_rounds.refused("msg_refused"))
        lines, plans, deltas = self.played(events)
        self.assertEqual("".join(delta["text"] for delta in deltas), fixture_rounds.REFUSED["reply"])
        self.assertEqual(plans, {}, "A call the schema check refused never counts")
        self.assertEqual((lines[-1]["subtype"], lines[-1]["is_error"]), ("error_max_structured_output_retries", True))

    def test_a_gated_reply_sends_each_fragment_only_after_the_last_was_acknowledged(self):
        """The key turn's reply waits on the suite, so a slow frame on a busy machine never misses a state."""
        fragments = fake_provider_cli.plan_fragments(fixture_rounds.KEYED, fixture_rounds.KEYED_CHARS)
        with tempfile.TemporaryDirectory() as folder:
            gate = Path(folder, "gate")
            events = fixture_rounds.answered("msg_key", [fixture_rounds.KEYED], fixture_rounds.KEYED_CHARS, 0,
                                             gate=gate)
            Path(folder, "round-0.json").write_text(json.dumps(events), encoding="utf-8")
            command = [sys.executable, fake_provider_cli.__file__, "claude", "-p"]
            environment = dict(os.environ, ANGELO_FAKE_CLI_ROUNDS=folder, ANGELO_FAKE_CLI_STATE=folder)
            with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                  env=environment) as process:
                lines = queue.Queue()
                threading.Thread(target=lambda: [lines.put(json.loads(line)) for line in process.stdout],
                                 daemon=True).start()
                process.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": "Go"}}) + "\n")
                process.stdin.close()
                try:
                    for number, fragment in enumerate(fragments):
                        line = lines.get(timeout=30)
                        while line.get("event", {}).get("delta", {}).get("type") != "input_json_delta":
                            line = lines.get(timeout=30)
                        self.assertEqual(line["event"]["delta"]["partial_json"], fragment)
                        deadline = time.monotonic() + 30
                        while fixture_rounds.gate_sent(gate) != number and time.monotonic() < deadline:
                            time.sleep(0.01)
                        self.assertEqual(fixture_rounds.gate_sent(gate), number)
                        with self.assertRaises(queue.Empty, msg="fragment %d came unacknowledged" % (number + 1)):
                            lines.get(timeout=0.1)
                        fixture_rounds.acknowledge(gate, number)
                    self.assertEqual(process.wait(timeout=30), 0)
                finally:
                    process.kill()
        self.assertGreater(len(fragments), 10)

    def test_the_keyed_reply_holds_back_its_split_key_in_several_states_then_shows_it_whole(self):
        streams = fixture_rounds.keyed_streams(PlanReply())
        self.assertEqual(streams[-1], fixture_rounds.KEYED["reply"])
        self.assertGreaterEqual(sum(bool(fixture_rounds.held_back(text)) for text in streams), 2)
        self.assertTrue(any(fixture_rounds.KEY in text for text in streams))


class KeyTurnNavigationTests(unittest.TestCase):
    """How ``test_blender_reply_stream.py`` opens the key turn's rows matches how the chat folds them.

    The transcript is the key turn's as the app saves it (lookup plan call, its reply, the lookup it ran, the
    answering plan call): the lookup's row folds with the answering call into one closed work group
    (``chat_rows.group_blocks``), so the GUI suite opens the group ``fixture_rounds.work_group`` names first.
    """

    MESSAGES = [
        {"role": "user", "text": "Which key does this connection use?"},
        {"role": "activity", "tool": "StructuredOutput", "call_id": "plan-lookup", "status": "completed",
         "arguments": {"operations": [{"tool": "angelo_scene_search", "arguments": {"query": "[REDACTED]"}}],
                       "verify": True}},
        {"role": "assistant", "text": "The key you set up, [REDACTED], is used only for this connection."},
        {"role": "activity", "tool": "angelo_scene_search", "call_id": "lookup", "status": "completed",
         "arguments": {"query": "[REDACTED]"}},
        {"role": "activity", "tool": "StructuredOutput", "call_id": "plan-answer", "status": "completed",
         "arguments": {"operations": []}}]

    def folded(self, expanded=frozenset()):
        from angelo_agent import chat_rows
        blocks = [(message["role"], message, (), 36, message.get("call_id") or index, 0)
                  for index, message in enumerate(self.MESSAGES)]
        return [block[4] for block in chat_rows.group_blocks(blocks, set(expanded), live=False)]

    def test_the_lookup_folds_into_the_group_the_suite_opens_and_the_plan_call_stays_in_view(self):
        group = fixture_rounds.work_group(self.MESSAGES, "lookup")
        self.assertEqual(group, ("group", "lookup"))
        self.assertEqual(self.folded(), [0, "plan-lookup", 2, group])
        self.assertEqual(self.folded({group}), [0, "plan-lookup", 2, group, "lookup", "plan-answer"])

    def test_the_plan_calls_details_list_its_operations_never_its_reply(self):
        from angelo_agent import chat_rows
        plan = dict(self.MESSAGES[1], arguments=dict(self.MESSAGES[1]["arguments"],
                                                     reply=fixture_rounds.KEY_LOOKUP["reply"]))
        detail = chat_rows._details(plan, "activity")
        self.assertTrue(detail.startswith("Planned operations:\n"))
        self.assertIn("angelo_scene_search", detail)
        self.assertIn("[REDACTED]", detail)
        self.assertNotIn(fixture_rounds.KEY_LOOKUP["reply"], detail)


if __name__ == "__main__":
    unittest.main()
