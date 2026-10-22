# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A script being written shows under the live row (script_stream, script_overlay, claude_stream).

Dependency-free: the scanner, the Claude stream reader on the plan route and the direct route, the
controller's overlay and the live block; no Claude process, Blender or model. Display only: nothing of the
code is stored, and a plan's script is still reviewed and run from the complete call.
"""

import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import chat_rows, claude_stream, plan_progress, plan_steps, script_overlay  # noqa: E402
from angelo_agent.claude_stream import STATUS_PLAN, STATUS_THINKING, RoundStream  # noqa: E402
from angelo_agent.script_stream import MAX_TAIL, SHOWN_LINES, ScriptText  # noqa: E402

KEY = "sk-ant-fake-7Hq2-Wn9c-Rt4x-Lp8z"  # Not a credential: a made-up key the chat must never draw.
CODE = ('import bpy\n'
        'd = {"code": "not a script", "tool": "angelo_scene_program"}\n'
        'print("}]\\\\ \\"quoted\\" é \U0001F600")\n'
        '\tm = bpy.data.materials.new("Grey Stone")\n'
        'for i in range(4):\n'
        '    bpy.ops.mesh.primitive_cube_add(location=(i, 0, 0))\n'
        'result = {"made": 4}')
SCRIPT = {"tool": "angelo_execute_python", "arguments": {"description": "Deck and posts", "code": CODE}}
PLAN = {"operations": [
    {"tool": "angelo_scene_program", "arguments": {"mode": "APPLY", "operations": [
        {"kind": "create_primitive", "parameters": {"primitive": "CUBE", "name": "Base", "code": "not this"}}]}},
    SCRIPT], "reply": "code: not this either"}


def scanned(text, size, direct=False):
    scan = ScriptText(direct=direct)
    for start in range(0, len(text), size):
        scan.feed(text[start:start + size])
    return scan


def texts(plan):
    return [json.dumps(plan), json.dumps(plan, ensure_ascii=False, separators=(",", ":")), json.dumps(plan, indent=2)]


class ScannerTests(unittest.TestCase):
    def test_the_code_is_decoded_however_the_text_arrives(self):
        for text in texts(PLAN):
            for size in (1, 2, 3, 7, 64, len(text)):
                with self.subTest(size=size, ascii=text.isascii(), indent="\n" in text):
                    scan = scanned(text, size)
                    self.assertEqual(scan.tail, CODE)
                    self.assertEqual(scan.line_count(), CODE.count("\n") + 1)
                    self.assertEqual(scan.shown_lines(SHOWN_LINES), CODE.split("\n")[-SHOWN_LINES:])
                    self.assertEqual(scan.scripts, 1)

    def test_every_prefix_shows_a_prefix_of_the_code(self):
        text = json.dumps(PLAN)
        scan = ScriptText()
        for character in text:
            scan.feed(character)
            self.assertTrue(CODE.startswith(scan.tail), scan.tail)

    def test_code_anywhere_else_is_never_shown(self):
        for plan in ({"operations": [{"tool": "angelo_scene_program", "arguments": {"code": "x = 1"}}]},
                     {"operations": [], "reply": "code"}, {"code": "x = 1", "operations": []},
                     {"operations": [{"tool": "angelo_execute_python", "arguments": {"description": "d",
                                                                                     "base": "s1", "edits": []}}]},
                     {"operations": [{"tool": "angelo_execute_python", "arguments": {"nested": {"code": "x"}}}]},
                     {"operations": [{"tool": "angelo_execute_python", "code": "x = 1"}]}):
            with self.subTest(plan=plan):
                scan = scanned(json.dumps(plan), 3)
                self.assertEqual((scan.scripts, scan.shown_lines(5), scan.line_count()), (0, [], 0))

    def test_a_script_written_before_its_tool_still_shows(self):
        plan = {"operations": [{"arguments": {"code": "a = 1\nb = 2"}, "tool": "angelo_execute_python"}]}
        self.assertEqual(scanned(json.dumps(plan), 4).shown_lines(5), ["a = 1", "b = 2"])

    def test_a_later_script_starts_over(self):
        second = {"tool": "angelo_execute_python", "arguments": {"description": "Lights", "code": "light = 1"}}
        scan = scanned(json.dumps({"operations": [SCRIPT, second]}), 11)
        self.assertEqual((scan.scripts, scan.tail, scan.line_count()), (2, "light = 1", 1))

    def test_a_direct_call_reads_its_own_top_level_code(self):
        call = {"description": "Deck", "code": CODE, "nested": {"code": "no"}}
        for size in (1, 5, 300):
            self.assertEqual(scanned(json.dumps(call), size, direct=True).tail, CODE)
        self.assertEqual(scanned(json.dumps({"operations": [SCRIPT]}), 5, direct=True).scripts, 0)

    def test_the_text_kept_is_bounded_and_a_cut_line_is_never_shown(self):
        long = "\n".join("line_%05d = %d" % (i, i) for i in range(5000))
        scan = scanned(json.dumps({"operations": [{"tool": "angelo_execute_python", "arguments": {"code": long}}]}),
                       997)
        self.assertLessEqual(len(scan.tail), MAX_TAIL)
        self.assertEqual(scan.line_count(), 5000)
        self.assertEqual(scan.shown_lines(3), ["line_04997 = 4997", "line_04998 = 4998", "line_04999 = 4999"])
        self.assertTrue(all(line.startswith("line_") for line in scan.shown_lines(400)))

    def test_text_that_is_not_json_ends_the_scan(self):
        scan = scanned('{"operations": [{"tool": "angelo_execute_python", "arguments": {"code": "a\\uZZZZ b"}}]}', 4)
        self.assertTrue(scan.done)
        self.assertEqual(scan.tail, "a")
        self.assertFalse(scan.feed("more"))

    def test_a_line_just_ended_shows_no_empty_line(self):
        scan = scanned(json.dumps({"operations": [SCRIPT]})[:json.dumps({"operations": [SCRIPT]}).index("for i") ], 50)
        self.assertNotEqual(scan.shown_lines(5)[-1], "")
        self.assertEqual(scan.preview()["lines"], scan.line_count())


class ReviewFindingTests(unittest.TestCase):
    """Found by the break-it review of this change (2026-10-21), each with its reproduction."""

    def test_surrogates_never_drop_text_or_reach_the_screen_unpaired(self):
        for raw, shown in (("x = '\\ud83dAB'", "x = '\ufffdAB'"), ("y = '\\udc00'", "y = '\ufffd'"),
                           ("z = '\\ud83d\\ude00'", "z = '\U0001F600'")):
            with self.subTest(raw=raw):
                text = '{"operations": [{"tool": "angelo_execute_python", "arguments": {"code": "%s"}}]}' % raw
                for size in (1, 3, len(text)):
                    self.assertEqual(scanned(text, size).tail, shown)
        controller = SimpleNamespace(busy=True, status=STATUS_PLAN, _request_generation=1, api_key="",
                                     redact=lambda text: text)
        script_overlay.update(controller, "script_preview", {"lines": 1, "text": "a \udc00 b"})
        self.assertEqual(script_overlay.shown(controller)[1], ["a \ufffd b"], "Never handed to blf unpaired")

    def test_only_a_plans_operations_array_holds_a_script(self):
        for plan in ({"operations": {"x": {"arguments": {"code": "x = 1"}}}},
                     {"operations": [[{"arguments": {"code": "x = 1"}}]]}):
            self.assertEqual(scanned(json.dumps(plan), 4).scripts, 0, plan)
        malformed = '{"operations": [{"tool": "angelo_execute_python", "arguments": {"code": "real" "bogus"}}]}'
        scan = scanned(malformed, 5)
        self.assertEqual((scan.scripts, scan.tail), (1, "real"), "A second string is no value of the key")

    def test_one_long_fragment_is_scanned_in_linear_time(self):
        import time as clock
        code = "x = 1\n" * 40000
        text = json.dumps({"operations": [{"tool": "angelo_execute_python", "arguments": {"code": code}}]})
        begun = clock.perf_counter()
        scan = ScriptText()
        scan.feed(text)
        self.assertEqual(scan.line_count(), 40000)
        self.assertLess(clock.perf_counter() - begun, 2.0)


def events(stream, payloads, plans=None):
    out = []
    for payload in payloads:
        event = payload if payload.get("type") in ("user", "result", "assistant") else {
            "type": "stream_event", "event": payload}
        out += stream.events(event, plans if plans is not None else {})
    return out


def start(index, name, tool_id):
    return {"type": "content_block_start", "index": index,
            "content_block": {"type": "tool_use", "name": name, "id": tool_id}}


def deltas(index, text, size):
    return [{"type": "content_block_delta", "index": index, "delta": {"type": "input_json_delta",
                                                                       "partial_json": text[start:start + size]}}
            for start in range(0, len(text), size)]


MESSAGE = {"type": "message_start", "message": {"id": "m1", "usage": {}}}


def fast_clock():
    """A clock a second later at every reading: no update is held back for the twice-a-second limit."""
    clock = [0.0]

    def tick():
        clock[0] += 1.0
        return clock[0]
    return patch.object(claude_stream.time, "monotonic", side_effect=tick)


def previews(emits):
    return [data for kind, data in emits if kind == "script_preview"]


def statuses(emits):
    return [data["text"] for kind, data in emits if kind == "status"]


class PlanRouteTests(unittest.TestCase):
    def test_the_first_lines_show_at_once_and_later_ones_at_most_twice_a_second(self):
        stream, clock, emits = RoundStream(), [100.], []
        text = json.dumps(PLAN)
        with patch.object(claude_stream.time, "monotonic", side_effect=lambda: clock[0]):
            emits += events(stream, [MESSAGE, start(0, "StructuredOutput", "t0")])
            for delta in deltas(0, text, 6):
                clock[0] += 0.025  # About 240 characters a second, as Opus 5.5 writes a plan's script.
                emits += events(stream, [delta])
        shown = previews(emits)
        self.assertEqual(shown[0], {}, "A plan call starts with no lines of an earlier one")
        self.assertEqual(shown[-1], {}, "The written script's lines go")
        shown = shown[:-1]
        self.assertTrue(shown[1]["text"] and CODE.startswith(shown[1]["text"]), "The first characters, at once")
        self.assertTrue(all(CODE.split("\n")[min(item["lines"], CODE.count("\n") + 1) - 1].startswith(
            item["text"].split("\n")[-1]) for item in shown[1:]))
        self.assertLessEqual(len(shown) - 1, len(text) / 6 * 0.025 / claude_stream.THINKING_SECONDS + 2)
        self.assertEqual(plan_steps.rows(statuses(emits)[-1])[0][-1][2], "Deck and posts", "Its step still shows")

    def test_the_plan_reply_and_steps_are_unchanged(self):
        stream = RoundStream()
        with fast_clock():
            emits = events(stream, [MESSAGE, start(0, "StructuredOutput", "t0")] + deltas(0, json.dumps(PLAN), 9))
        reply = "".join(data["text"] for kind, data in emits if kind == "delta")
        self.assertEqual(reply, PLAN["reply"])
        self.assertEqual([row[2] for row in plan_steps.rows(statuses(emits)[-1])[0]], ["Base", "Deck and posts"])

    def test_a_written_script_leaves_the_well_while_later_steps_and_the_reply_stream(self):
        stream, plans = RoundStream(), {}
        plan = {"operations": [{"tool": "angelo_execute_python", "arguments": {"description": "Deck",
                                                                               "code": "a = 1\nb = 2\nc = 3"}},
                               {"tool": "angelo_scene_program", "arguments": {"mode": "APPLY", "operations": [
                                   {"kind": "create_primitive", "parameters": {"primitive": "CUBE", "name": "Base"}}]}}],
                "reply": "Built the deck and its base."}
        text = json.dumps(plan)
        cut = text.index('"}}, {') + 1  # Just after the code string closed.
        with fast_clock():
            writing = events(stream, [MESSAGE, start(0, "StructuredOutput", "t0")] + deltas(0, text[:cut - 2], 4))
            written = events(stream, deltas(0, text[cut - 2:], 4))
        self.assertEqual(previews(writing)[-1]["lines"], 3)
        self.assertEqual(previews(written), [{}], "Written: its lines go at once, and only once")
        self.assertEqual([row[2] for row in plan_steps.rows(statuses(written)[-1])[0]], ["Deck", "Base"],
                         "Its step stays")

    def test_a_later_plan_call_starts_its_lines_over(self):
        stream = RoundStream()
        events(stream, [MESSAGE, start(0, "StructuredOutput", "t0")] + deltas(0, json.dumps(PLAN), 40))
        later = events(stream, [start(1, "StructuredOutput", "t1")])
        self.assertEqual(previews(later), [{}])


class DirectRouteTests(unittest.TestCase):
    def setUp(self):
        self.clock = fast_clock()
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def test_a_script_call_shows_its_step_and_code_and_its_end_takes_them_away(self):
        stream = RoundStream()
        call = json.dumps({"description": "Deck and posts", "code": CODE})
        emits = events(stream, [MESSAGE, start(0, "mcp__angelo__angelo_execute_python", "c0")] + deltas(0, call, 7))
        self.assertEqual(statuses(emits)[:2], [STATUS_THINKING, STATUS_PLAN])
        self.assertEqual(plan_steps.rows(statuses(emits)[-1])[0], [("script", "angelo_execute_python",
                                                                    "Deck and posts", 1)])
        self.assertEqual(previews(emits)[-1], {}, "Its code is complete: its lines go")
        self.assertGreaterEqual(previews(emits)[-2]["lines"], CODE.count("\n"))
        ended = events(stream, [{"type": "content_block_stop", "index": 0}])
        self.assertEqual((statuses(ended), previews(ended)), ([STATUS_THINKING], [{}]))
        self.assertEqual(events(stream, deltas(0, "x", 1)), [], "Nothing after its end")

    def test_an_edit_call_shows_its_step(self):
        stream = RoundStream()
        call = json.dumps({"mode": "APPLY", "operations": [
            {"kind": "create_primitive", "parameters": {"primitive": "CUBE", "name": "Post_FL"}},
            {"kind": "create_primitive", "parameters": {"primitive": "CUBE", "name": "Post_FR"}}]})
        emits = events(stream, [MESSAGE, start(0, "mcp__angelo__angelo_scene_program", "c0")] + deltas(0, call, 9))
        self.assertEqual(plan_steps.rows(statuses(emits)[-1])[0], [("create", "create_primitive", "Post", 2)])
        self.assertEqual(previews(emits), [])

    def test_a_call_without_an_index_shows_nothing_and_raises_nothing(self):
        stream = RoundStream()
        bad = [{"type": "content_block_start", "content_block": {
                    "type": "tool_use", "name": "mcp__angelo__angelo_execute_python", "id": "c0"}},
               {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": '{"code": "x'}}]
        self.assertEqual(events(stream, [MESSAGE] + bad)[1:], [])

    def test_a_lookup_or_another_tool_shows_nothing(self):
        stream = RoundStream()
        for index, name in enumerate(("mcp__angelo__angelo_agent_capabilities", "mcp__angelo__no_such_tool",
                                      "mcp__other__angelo_execute_python", "WebSearch")):
            emits = events(stream, [start(index, name, "c%d" % index)] + deltas(index, json.dumps({"code": "x"}), 3)
                           + [{"type": "content_block_stop", "index": index}])
            self.assertEqual(emits, [], name)

    def test_a_call_cut_off_by_a_new_message_takes_its_step_away(self):
        stream = RoundStream()
        events(stream, [MESSAGE, start(0, "mcp__angelo__angelo_execute_python", "c0")]
               + deltas(0, json.dumps({"description": "Deck", "code": "a = 1"}), 5))
        emits = events(stream, [{"type": "message_start", "message": {"id": "m2", "usage": {}}}])
        self.assertEqual((statuses(emits), previews(emits)), ([STATUS_THINKING], [{}]))


class ControllerTests(unittest.TestCase):
    def setUp(self):
        from angelo_agent.controller import Controller
        self.directory = tempfile.TemporaryDirectory()
        self.c = Controller(self.directory.name, session_id=str(uuid.uuid4()))
        self.c.set_document(("script-stream", 1))

    def tearDown(self):
        self.c.close()
        self.directory.cleanup()

    def test_lines_show_only_while_a_plan_is_written_are_redacted_and_never_kept(self):
        self.c.api_key = KEY
        thread = SimpleNamespace(start=lambda: None, is_alive=lambda: False)
        with patch("angelo_agent.controller.threading.Thread", return_value=thread):
            self.c.send("Build a deck")
            self.c.emit("status", {"text": STATUS_PLAN})
            self.c.emit("script_preview", {"lines": 3, "text": 'key = "%s"\nk2 = "%s' % (KEY, KEY[:9])})
            self.c.pump_events()
            lines, code = script_overlay.shown(self.c)
            self.assertEqual(lines, 3)
            self.assertNotIn(KEY, "\n".join(code))
            self.assertFalse(any(KEY[:i] in code[-1] for i in range(3, len(KEY))), "A key's start is held back")
            block = chat_rows.live_block(self.c)
            self.assertEqual(block[1]["script"], (lines, code))
            self.assertEqual(block[3], chat_rows.LIVE + chat_rows.code_height())
            self.c.emit("status", {"text": STATUS_THINKING})
            self.c.pump_events()
            self.assertIsNone(script_overlay.shown(self.c), "The plan arrived: its lines go with its status")
            self.c.emit("status", {"text": STATUS_PLAN})
            self.c.emit("script_preview", {})
            self.c.pump_events()
            self.assertIsNone(script_overlay.shown(self.c), "A new call has no lines yet")
            self.c.emit("script_preview", {"lines": 1, "text": "import bpy"})
            self.c.emit("finished", {"status": "completed"})
            self.c.pump_events()
        self.assertIsNone(script_overlay.shown(self.c))
        self.assertNotIn("import bpy", json.dumps(self.c.state), "Nothing of the code is stored")
        self.assertNotIn("_script_preview", self.c.__dict__)
        self.assertNotIn("_script_preview_drawn", self.c.__dict__)

    def test_no_cut_ever_leaves_part_of_a_key(self):
        self.c.api_key = KEY
        self.c.busy, self.c.status = True, STATUS_PLAN
        pieces = {KEY[i:i + 5] for i in range(len(KEY) - 4)}
        lines = {"a key across the line cut": "x = 1\n" + "#" * 230 + ' = "%s"\n' % KEY + "y = 2",
                 "a key across the tail's start": "t = '" + KEY + "'" + "z" * (MAX_TAIL + 50),
                 "a key across the tail's start, then a space": "t='" + KEY + "' " + "z" * (MAX_TAIL + 50)}
        for name, code in lines.items():
            with self.subTest(name):
                scan = ScriptText()
                text = json.dumps({"operations": [{"tool": "angelo_execute_python", "arguments": {"code": code}}]})
                for start in range(0, len(text), 97):
                    scan.feed(text[start:start + 97])
                    script_overlay.update(self.c, "script_preview", scan.preview())
                    drawn = "\n".join((script_overlay.shown(self.c) or (0, []))[1])
                    self.assertFalse([piece for piece in pieces if piece in drawn], (start, drawn[-300:]))

    def test_a_redraw_between_previews_redacts_nothing_again_and_a_new_key_does(self):
        self.c.busy, self.c.status = True, STATUS_PLAN
        script_overlay.update(self.c, "script_preview", {"lines": 1, "text": 'k = "%s"' % KEY})
        with patch.object(self.c, "redact", wraps=self.c.redact) as redact:
            first = script_overlay.shown(self.c)
            self.assertEqual(script_overlay.shown(self.c), first)
            self.assertEqual(redact.call_count, 1)
            self.c.api_key = KEY
            self.assertNotIn(KEY, script_overlay.shown(self.c)[1][0])
            self.assertEqual(redact.call_count, 2)

    def test_an_older_requests_lines_never_show(self):
        self.c.busy, self.c.status = True, STATUS_PLAN
        script_overlay.update(self.c, "script_preview", {"lines": 2, "text": "a\nb"})
        self.assertEqual(script_overlay.shown(self.c), (2, ["a", "b"]))
        self.c._request_generation += 1
        self.assertIsNone(script_overlay.shown(self.c))


class LiveBlockTests(unittest.TestCase):
    def test_the_well_sits_under_the_steps_and_is_as_tall_from_its_first_line(self):
        progress = plan_progress.PlanProgress()
        progress.feed(json.dumps({"operations": [SCRIPT]}))
        controller = SimpleNamespace(voice=None, voice_status="", busy=True, _request_generation=1, api_key="",
                                     redact=lambda text: text,
                                     status=plan_progress.status(STATUS_PLAN, progress.detail(),
                                                                 plan_steps.encode(progress)))
        script_overlay.update(controller, "script_preview", {"lines": 1, "text": "i"})
        one = chat_rows.live_block(controller)
        script_overlay.update(controller, "script_preview", {"lines": 9, "text": "\n".join("abcde")})
        five = chat_rows.live_block(controller)
        self.assertEqual(one[3], five[3], "The row does not grow line by line")
        self.assertEqual(five[3], chat_rows.LIVE + chat_rows.PLANNED + 6 + chat_rows.code_height())
        self.assertEqual(five[1]["text"], "Preparing the next steps")
        self.assertEqual(chat_rows.code_caption(9), "Writing the script: 9 lines")
        self.assertEqual(chat_rows.code_caption(1), "Writing the script: 1 line")
        controller.voice, controller.voice_status = object(), "Listening"
        self.assertIsNone(chat_rows.live_block(controller)[1]["script"])


class ThinkingDisplayTests(unittest.TestCase):
    def test_summarized_thinking_ships_off_and_one_constant_turns_it_on(self):
        from angelo_agent import debug_thinking
        chat = SimpleNamespace(debug_thinking=False)
        self.assertIs(debug_thinking.SHOWN_TO_EVERYONE, False, "Off for 1.0.4: the maintainer's decision, 2026-10-22")
        self.assertEqual(debug_thinking.claude_arguments(chat), [])
        self.assertFalse(debug_thinking.enabled(chat), "It is no debug mode: the run stays a real-user run")
        with patch.object(debug_thinking, "SHOWN_TO_EVERYONE", True):   # The A/B's ON arm.
            self.assertEqual(debug_thinking.claude_arguments(chat), ["--thinking-display", "summarized"])


class ProgressWatchTests(unittest.TestCase):
    def test_the_first_script_line_is_progress(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from eval_receipts import ProgressWatch
        watch = ProgressWatch(0.0)
        watch.observe(STATUS_THINKING, 1.0)
        watch.observe(STATUS_PLAN, 40.0)
        watch.observe(STATUS_PLAN, 40.5, script=(1, ["import bpy"]))
        watch.observe(STATUS_PLAN, 41.0, script=(9, ["piece.name = 'Post'"]))
        watch.observe(STATUS_PLAN, 41.2, script=(9, ["piece.name = 'Post'"]))
        self.assertEqual(watch.record(), {"first_plan_ms": 40000.0, "first_progress_ms": 40500.0,
                                          "first_script_ms": 40500.0, "first_plan_row_ms": 40500.0,
                                          "longest_still_ms": 39000.0})

    def test_blender_mcps_first_script_call_is_timed_from_its_start(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from bench_mcp_report import analyze_mcp_events

        def stream(event):
            return {"type": "stream_event", "event": event}
        events = [(1.0, stream({"type": "content_block_start", "index": 0, "content_block": {
                      "type": "tool_use", "id": "a", "name": "ToolSearch"}})),
                  (20.0, stream({"type": "content_block_start", "index": 0, "content_block": {
                      "type": "tool_use", "id": "b", "name": "mcp__blender__get_objects_summary"}})),
                  (22.0, stream({"type": "content_block_start", "index": 1, "content_block": {
                      "type": "tool_use", "id": "c", "name": "mcp__blender__execute_blender_code"}}))]
        facts = analyze_mcp_events(events, 0.0)
        self.assertEqual((facts["first_tool_call_ms"], facts["first_script_call_ms"]), (1000.0, 22000.0))


class ShippedDefaultTests(unittest.TestCase):
    def test_the_well_ships_off_and_then_draws_nothing(self):
        with patch.object(script_overlay, "SHOWN", False):
            controller = SimpleNamespace(busy=True, status=STATUS_PLAN, _request_generation=1, api_key="",
                                         redact=lambda text: text)
            script_overlay.update(controller, "script_preview", {"lines": 2, "text": "a\nb"})
            self.assertIsNone(script_overlay.shown(controller))


def setUpModule():
    """The well ships off (script_overlay.SHOWN); these tests are of the well itself, so they turn it on."""
    from unittest import mock
    patcher = mock.patch.object(script_overlay, "SHOWN", True)
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


if __name__ == "__main__":
    unittest.main()
