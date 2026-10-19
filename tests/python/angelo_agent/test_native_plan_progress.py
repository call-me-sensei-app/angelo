# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a plan being written will make, shown while it streams: display only, for both runtimes."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import claude_stream, codex_plan, plan_progress  # noqa: E402
from angelo_agent.chat_titles import live_label  # noqa: E402
from angelo_agent.claude_stream import STATUS_PLAN, STATUS_THINKING, RoundStream  # noqa: E402
from angelo_agent.plan_progress import PlanProgress  # noqa: E402
from angelo_agent.strict_schema import encode  # noqa: E402


def cube(name, alias):
    return {"kind": "create_primitive", "parameters": {"primitive": "CUBE", "name": name, "alias": alias}}


PLAN = {"reply": "", "operations": [
    {"tool": "angelo_scene_program", "arguments": {"mode": "APPLY", "operations": [
        cube("Bridge Deck", "deck"),
        {"kind": "modifier_add", "target": "$deck", "parameters": {"name": "Soft Edges", "type": "BEVEL",
                                                                    "settings": {"width": .02}}},
        {"kind": "material_principled", "target": "$deck", "parameters": {"name": "Bridge Stone", "inputs": {}}},
        cube("Railing Posts", "posts"),
        {"kind": "transform", "target": "$posts", "parameters": {"location": [0, 0, 1]}}],
        "expected_checks": [{"kind": "transform", "target": "$posts", "property": "location", "expected": [0, 0, 1],
                             "tolerance": .001},
                            {"kind": "object_name", "target": "$deck", "expected": "Bridge Deck"}]}},
    {"tool": "angelo_scene_program", "arguments": {"mode": "APPLY", "operations": [
        {"kind": "light_create", "parameters": {"name": "Sun", "location": [1, 2, 3]}},
        {"kind": "camera_create", "parameters": {"name": "Deck Camera"}},
        {"kind": "property_set", "parameters": {"values": {"frame_end": 120}}}]}}]}
TEXT = json.dumps(PLAN, separators=(",", ":"))


def strict_text(plan):
    """The plan as a Codex model writes it: a kind the strict format does not spell out has its target written."""
    spelled = json.loads(json.dumps(plan))
    for item in spelled["operations"]:
        for operation in item["arguments"]["operations"]:
            operation.setdefault("target", None)
    return json.dumps(encode(spelled, codex_plan.ordinary_schema()), separators=(",", ":"))


def scanned(text, size):
    progress = PlanProgress()
    for start in range(0, len(text), size):
        progress.feed(text[start:start + size])
    return progress


class ScanTests(unittest.TestCase):
    def test_operations_are_counted_and_created_objects_named_however_the_text_arrives(self):
        for size in (1, 7, 64, len(TEXT)):
            with self.subTest(chunk=size):
                progress = scanned(TEXT, size)
                self.assertEqual(progress.operations, 8, "Checks are not operations, whatever their kind is called")
                self.assertEqual(progress.names, ["Bridge Deck", "Railing Posts", "Sun", "Deck Camera"],
                                 "A modifier's or a material's name is not an object")
                self.assertEqual(progress.detail(), "Bridge Deck, Railing Posts, Sun +5")

    def test_a_codex_plan_is_read_through_its_json_text_fields(self):
        """Kinds the strict format does not spell out carry their parameters as JSON text inside the JSON."""
        strict = strict_text(PLAN)
        self.assertIn('\\"name\\":\\"Sun\\"', strict)
        for size in (1, 11, len(strict)):
            with self.subTest(chunk=size):
                progress = scanned(strict, size)
                self.assertEqual((progress.operations, progress.names),
                                 (8, ["Bridge Deck", "Railing Posts", "Sun", "Deck Camera"]))

    def test_nothing_is_shown_before_the_first_object_and_edits_alone_show_nothing(self):
        self.assertEqual(scanned(TEXT[:TEXT.index("Bridge Deck")], 5).detail(), "")
        edits = json.dumps({"operations": [{"kind": "transform", "target": "o3", "parameters": {"location": [0, 0, 1]}}]})
        self.assertEqual((scanned(edits, 9).operations, scanned(edits, 9).detail()), (1, ""))

    def test_a_reviewed_script_shows_its_own_description_before_its_code(self):
        """A recorded footbridge one-shot on Opus: one script for 9-10 s, nothing named until it ended."""
        script = {"tool": "angelo_execute_python", "arguments": {
            "description": "Create arched stone footbridge (4 m deck,\n4 posts, 2 rails) with a grey stone material.",
            "code": "import bpy\nob = bpy.data.objects.new('Bridge_Deck', None)\n" * 40}}
        plans = {"script alone": {"operations": [script]},
                 "after a checked program": dict(PLAN, operations=PLAN["operations"][:1] + [script])}
        for label, plan in plans.items():
            text = json.dumps(plan)
            for size in (1, 9, 200):
                with self.subTest(plan=label, chunk=size):
                    progress = scanned(text[:text.index('"code"')], size)
                    self.assertIn("Create arched stone footbridge (4 m deck, 4 posts, 2 rails) with a grey stone "
                                  "material.", progress.names, "Whole, its line break a space")
        other = json.dumps({"operations": [{"tool": "angelo_job_start", "arguments": {"description": "Render it"}}]})
        self.assertEqual(scanned(other, 7).names, [], "Only a script's description names it")

    def test_text_that_is_not_a_plan_has_no_operations(self):
        for text in ("The bridge is 24 m long.", '{"reply":"A \\"kind\\" answer about names","operations":[]}', "", "{" * 9000):
            progress = scanned(text, 13)
            self.assertEqual((progress.operations, progress.detail()), (0, ""))
        self.assertFalse(PlanProgress().feed(None))

    def test_the_scan_is_bounded(self):
        many = json.dumps({"operations": [cube("Post %03d" % index, "p%d" % index) for index in range(200)]})
        progress = scanned(many + "x" * 100000, 4096)
        self.assertEqual(progress.operations, 200)
        self.assertEqual(len(progress.names), plan_progress.MAX_NAMES)
        self.assertLessEqual(len(progress._buffer), plan_progress.MAX_TAIL)
        self.assertEqual(progress.detail(), "Post 000, Post 001, Post 002 +197")

    def test_a_status_carries_its_detail_and_a_plain_status_is_itself(self):
        self.assertEqual(plan_progress.status(STATUS_PLAN, ""), STATUS_PLAN)
        carried = plan_progress.status(STATUS_PLAN, "Bridge Deck +2")
        self.assertEqual(plan_progress.split(carried), (STATUS_PLAN, "Bridge Deck +2"))
        self.assertEqual(plan_progress.split(STATUS_THINKING), (STATUS_THINKING, ""))


class LiveRowTests(unittest.TestCase):
    def test_the_detail_follows_the_translated_label_with_the_separator_tool_rows_use(self):
        self.assertEqual(live_label(STATUS_PLAN), "Preparing the next steps")
        self.assertEqual(live_label(plan_progress.status(STATUS_PLAN, "Bridge Deck, Sun +5")),
                         "Preparing the next steps · Bridge Deck, Sun +5")
        self.assertEqual(live_label("Anything else"), "Thinking")
        self.assertEqual(live_label(plan_progress.status(STATUS_THINKING, "Planning the bridge")),
                         "Thinking · Planning the bridge")

    def test_a_reasoning_summary_shows_its_latest_complete_title_bounded(self):
        title = plan_progress.summary_title
        self.assertEqual(title("**Planning program creation**\n\nI need to create a final program"),
                         "Planning program creation")
        self.assertEqual(title("**Considering structure**\n\nbody **Finalizing\tdetails**\n\nmore"), "Finalizing details")
        self.assertEqual(title("**Still arriving"), "")
        self.assertEqual(title("no titles here, only *emphasis*"), "")
        self.assertEqual(title(None), "")
        long = title("**" + "word " * 40 + "**")
        self.assertEqual(len(long), plan_progress.MAX_TITLE)
        self.assertTrue(long.endswith("…"))
        self.assertEqual(title("**Old**" + "x" * 5000 + "**New**"), "New", "only a bounded tail is scanned")

    def test_only_the_generic_thinking_status_takes_a_title(self):
        self.assertEqual(plan_progress.THINKING, STATUS_THINKING)
        summary = "**Planning the bridge**\n\nA deck and four posts."
        self.assertEqual(plan_progress.thinking_status(STATUS_THINKING, summary),
                         plan_progress.status(STATUS_THINKING, "Planning the bridge"))
        earlier = plan_progress.status(STATUS_THINKING, "Planning the bridge")
        self.assertEqual(plan_progress.thinking_status(earlier, summary + " **Placing posts**"),
                         plan_progress.status(STATUS_THINKING, "Placing posts"))
        writing = plan_progress.status(STATUS_PLAN, "Bridge Deck +3")
        for current in (writing, "Connecting", "Approval required"):
            self.assertEqual(plan_progress.thinking_status(current, summary), current)
        self.assertEqual(plan_progress.thinking_status(STATUS_THINKING, "no title yet"), STATUS_THINKING)


class ClaudeStreamTests(unittest.TestCase):
    def statuses(self, chunks, clock):
        stream, shown = RoundStream(), []

        def event(payload):
            for kind, data in stream.events({"type": "stream_event", "event": payload}, {}):
                if kind == "status":
                    shown.append(data["text"])
        with patch.object(claude_stream.time, "monotonic", side_effect=lambda: clock[0]):
            event({"type": "message_start", "message": {"id": "m1", "usage": {}}})
            event({"type": "content_block_start", "index": 1,
                   "content_block": {"type": "tool_use", "name": "StructuredOutput", "id": "t1"}})
            for chunk in chunks:
                clock[0] += .2
                event({"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": chunk}})
        return shown

    def test_the_status_names_what_the_plan_makes_at_most_twice_a_second(self):
        chunks = [TEXT[start:start + 40] for start in range(0, len(TEXT), 40)]
        shown = self.statuses(chunks, [100.])
        self.assertEqual(shown[:2], [STATUS_THINKING, STATUS_PLAN], "The plain status first, as before")
        details = [plan_progress.split(text)[1] for text in shown[2:]]
        self.assertTrue(details and all(details))
        self.assertEqual(details[-1], "Bridge Deck, Railing Posts, Sun +5")
        self.assertLessEqual(len(details), len(chunks) * .2 / claude_stream.THINKING_SECONDS + 1)
        self.assertEqual(len(details), len(set(details)), "Only when what there is to show changed")

    def test_the_first_thing_to_show_shows_at_once_even_when_the_plan_arrives_whole(self):
        """Before the first redraw's half second had passed it waited (up to 0.5 s of nothing on screen)."""
        self.assertEqual(self.statuses([], [100.]), [STATUS_THINKING, STATUS_PLAN])
        stream, clock = RoundStream(), [100.]
        with patch.object(claude_stream.time, "monotonic", side_effect=lambda: clock[0]):
            stream.events({"type": "stream_event", "event": {"type": "content_block_start", "index": 0, "content_block": {
                "type": "tool_use", "name": "StructuredOutput", "id": "t1"}}}, {})
            emits = stream.events({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {
                "type": "input_json_delta", "partial_json": TEXT}}}, {})
        self.assertEqual([kind for kind, _ in emits], ["status"])
        self.assertEqual(plan_progress.split(emits[0][1]["text"]), (STATUS_PLAN, "Bridge Deck, Railing Posts, Sun +5"))

    def test_another_tools_input_is_never_read_as_a_plan(self):
        stream = RoundStream()
        emits = stream.events({"type": "stream_event", "event": {"type": "content_block_delta", "index": 3, "delta": {
            "type": "input_json_delta", "partial_json": TEXT}}}, {})
        self.assertEqual(emits, [])


class CodexStreamTests(unittest.TestCase):
    def test_message_deltas_emit_the_same_status_and_still_stream_only_the_reply(self):
        turns, events, clock = codex_plan.PlanTurns(), [], [50.]
        strict = strict_text(dict(PLAN, reply="Built."))
        with patch.object(codex_plan.time, "monotonic", side_effect=lambda: clock[0]):
            reply = ""
            for start in range(0, len(strict), 60):
                clock[0] += .3
                reply += turns.reply_delta("m1", strict[start:start + 60], lambda kind, data: events.append((kind, data)))
        self.assertEqual(reply, "Built.")
        self.assertTrue(events and all(kind == "status" for kind, _ in events))
        base, detail = plan_progress.split(events[-1][1]["text"])
        self.assertEqual(base, STATUS_PLAN)
        self.assertTrue(detail.startswith("Bridge Deck, Railing Posts"), detail)
        self.assertEqual(turns.progress["m1"][0].detail(), "Bridge Deck, Railing Posts, Sun +5")
        self.assertLessEqual(len(events), len(strict) / 60 * .3 / codex_plan.PROGRESS_SECONDS + 1, "At most twice a second")
        self.assertEqual(turns.reply_delta("m2", '{"reply":"Hi","operations":[]}'), "Hi", "Without an emitter, as before")
        turns.begin_turn()
        self.assertEqual(turns.progress, {})


if __name__ == "__main__":
    unittest.main()
