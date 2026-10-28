# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``--ask-why-on-miss`` (eval_ask_why): which checks it names, what it asks, how it stays out of the results.

Unbilled: the chat, the Claude Code stream and the trial are test doubles with the real objects' shapes. The
refusal fixture is the shape Claude Code 2.1.301 printed for two resumed sky questions
(doc/angelo/debug_mode.rst), not a live call.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import eval_ask_why as ask_why  # noqa: E402
import eval_diagnostics as diagnostics  # noqa: E402

SKY = {"label": "golden_hour: golden sky", "kind": "world", "passed": False,
       "details": {"world": "Golden Hour Sky", "sky": None, "color": [0.42, 0.46, 0.62], "strength": 0.8,
                   "problems": ["neither a low-sun Sky texture nor a warm background"]}}
ONESHOT = {"task": "brief", "status": "completed", "host_check_passed": False,
           "host_check": {"success": False, "passed": 47, "total": 48,
                          "results": [{"label": "valley: Valley exists", "passed": True, "details": {}}, SKY]}}
REFUSED = [{"type": "system", "subtype": "model_refusal_no_fallback", "api_refusal_category": "reasoning_extraction"},
           {"type": "assistant", "message": {"model": "<synthetic>", "content": [{"type": "text", "text": "API Error"}]}}]
REFUSED_RESULT = {"type": "result", "is_error": True, "stop_reason": "refusal",
                  "result": "API Error: Opus 5.5's safeguards flagged this message.\n\nDetails: `[reasoning_extraction]`"}


class CheckSelectionTests(unittest.TestCase):
    def test_only_failed_closed_checks_of_graded_tasks_are_named_with_their_values(self):
        verify = {"task": "bevel", "status": "completed", "host_check_passed": False,
                  "host_check": {"status": "fail", "checks": [
                      {"kind": "modifier", "target": "$eval", "passed": False, "status": "fail", "observed": {"width": 0}},
                      {"kind": "dimensions", "target": "$eval", "passed": True}]}}
        unchanged = {"task": "question", "status": "completed", "host_check_passed": False,
                     "host_check": {"scene_unchanged": False, "reply_missing": ["Cube"]}}
        skipped = {"task": "later", "status": "skipped", "host_check_passed": False}
        passed = dict(ONESHOT, host_check_passed=True)
        found = ask_why.failed_checks([ONESHOT, verify, unchanged, skipped, passed, {"host_check_passed": None}])
        self.assertEqual(found, [("brief", "golden_hour: golden sky", SKY["details"]),
                                 ("bevel", "modifier on $eval", {"observed": {"width": 0}, "status": "fail"}),
                                 ("question", "task check", {"scene_unchanged": False, "reply_missing": ["Cube"]})])
        self.assertEqual(ask_why.failed_checks(None), [])

    def test_the_question_names_each_check_and_asks_about_the_decision_only(self):
        text = ask_why.question(ask_why.failed_checks([ONESHOT]))
        self.assertTrue(text.startswith(ask_why.HEADER))
        self.assertIn("this chat is now read-only", text)
        self.assertIn('- brief: golden_hour: golden sky. Measured: {"world": "Golden Hour Sky"', text)
        self.assertIn("[0.42, 0.46, 0.62]", text)
        for asked in ("Which detail of the request or the reference pictures led you to this choice?",
                      "Did you notice, before you finished, that it did not match what was asked?",
                      "If you did, why did you keep it?", "no need to defend the result", "without calling tools"):
            self.assertIn(asked, text)
        # Never a request to reproduce or explain the thinking: that comes only from the thinking summaries.
        for never in ("step by step", "reasoning", "thinking", "thought", "candid", "explain"):
            self.assertNotIn(never, text.lower())
        self.assertNotIn("read-only", ask_why.question([("t", "c", {})], read_only=False))
        many = [("t", "check %d" % index, {"value": "x" * 900}) for index in range(20)]
        long = ask_why.question(many)
        self.assertIn("- and 8 more", long)
        self.assertLess(len(long), 8000)


class InAppTests(unittest.TestCase):
    class Chat:
        """The controller's shape: a class ``send`` (what the trial's wrapper guards), options, approvals, rows."""

        def __init__(self):
            self.state = {"id": "chat-1", "messages": [{"role": "user", "text": "Build it"},
                                                       {"role": "assistant", "text": "Done."}]}
            self.pending, self.busy, self._worker, self.debug_thinking = {}, False, None, True
            self.options, self.declined, self.sent, self.stopped = {}, [], [], False
            self.telemetry = SimpleNamespace(receipts=[{"session_id": "chat-1", "usage": {"input_tokens": 1}}])

        def send(self, text, observation=None, *, recovery_evidence=None):
            self.sent.append(text)
            self.busy = True

        def set_options(self, **changes):
            self.options.update(changes)

        def decline_tool(self, key):
            self.declined.append(key)
            self.pending.pop(key)

        def stop(self):
            self.stopped = True

    def trial(self, tasks, folder, aborted=None):
        chat = self.Chat()
        chat.send = mock.Mock(side_effect=RuntimeError("A one-shot test sends exactly one message"))
        report = {"trial": 6, "tasks": tasks, **({"aborted": aborted} if aborted else {})}
        trial = SimpleNamespace(controller=chat, report=report, window=SimpleNamespace(scene=None), phase="send",
                                finish=mock.Mock(return_value=None))
        environ = {diagnostics.ASK_ENV: "1", "ANGELO_QA_DIR": folder}
        return trial, chat, environ

    def test_no_follow_up_without_the_flag_a_miss_or_a_chat_that_ran_normally(self):
        with tempfile.TemporaryDirectory() as folder:
            trial, chat, environ = self.trial([ONESHOT], folder)
            diagnostics.after_trial(trial, None, {})
            trial.finish.assert_called_once()
            self.assertNotIn("ask_why", trial.report)
            for tasks, aborted, reason in (([dict(ONESHOT, host_check_passed=True)], None, "no closed check failed"),
                                           ([ONESHOT], "rate limit", "the trial was stopped: rate limit")):
                trial, chat, environ = self.trial(tasks, folder, aborted)
                diagnostics.after_trial(trial, None, environ)
                self.assertEqual(trial.report["ask_why"], {"asked": False, "reason": reason})
                self.assertEqual(chat.sent, [])

    def test_the_follow_up_is_read_only_declines_what_asks_and_records_the_reply(self):
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(ask_why.InAppAskWhy, "digest", return_value="same"):
            trial, chat, environ = self.trial([ONESHOT], folder)
            diagnostics.after_trial(trial, None, environ)
            self.assertEqual((trial.phase, chat.options), ("why", {"approval": "READ_ONLY"}))
            self.assertEqual(len(chat.sent), 1, "the chat's own send, past the graded message's guard")
            chat.send.assert_not_called()
            chat.pending["c1"] = SimpleNamespace(id="c1", approval=True, executing=False, authorized=False)
            self.assertEqual(trial.why.tick(), .1)
            self.assertEqual(chat.declined, ["c1"])
            chat.state["messages"] += [{"role": "user", "text": chat.sent[0]},
                                       {"role": "thinking", "text": "They want the honest version."},
                                       {"role": "activity", "tool": "angelo_inspect_scene"},
                                       {"role": "assistant", "text": "I matched the front picture's sky."}]
            chat.telemetry.receipts.append({"session_id": "chat-1",
                                            "usage": {"input_tokens": 9000, "output_tokens": 300}})
            chat.busy = False
            trial.why.tick()
            trial.finish.assert_called_once()
            summary = trial.report["ask_why"]
            self.assertEqual((summary["outcome"], summary["declined"], summary["scene_unchanged"], summary["tools"]),
                             ("answered", 1, True, ["angelo_inspect_scene"]))
            self.assertEqual(summary["usage"], {"input_tokens": 9000, "output_tokens": 300})
            page = (Path(folder) / "why.md").read_text()
            for part in ("## Failed checks", "golden_hour: golden sky", "## Thinking summaries (debug thinking)",
                         "They want the honest version.", "## Reply", "I matched the front picture's sky.",
                         '"approval": "READ_ONLY"', "not a timing or benchmark claim"):
                self.assertIn(part, page)

    def test_a_turn_past_its_time_is_stopped_then_recorded(self):
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(ask_why.InAppAskWhy, "digest", side_effect=["before", "after"]):
            trial, chat, environ = self.trial([ONESHOT], folder)
            diagnostics.after_trial(trial, None, environ)
            trial.why.deadline = 0
            trial.why.tick()
            self.assertTrue(chat.stopped)
            trial.why.deadline = 0
            trial.why.tick()
            summary = trial.report["ask_why"]
            self.assertEqual((summary["outcome"], summary["scene_unchanged"]),
                             ("stopped after 300 s without a reply", False))


class McpSideTests(unittest.TestCase):
    class Stream:
        """``ClaudeStream``'s shape: class methods the one-shot guard wraps on the instance."""

        def __init__(self, folder, lines, result):
            self.raw = (Path(folder) / "claude-stream.jsonl").open("w", encoding="utf-8")
            self.process = SimpleNamespace(poll=lambda: None)
            self.lines, self.result, self.sent = lines, result, []
            self.send = self.collect = mock.Mock(side_effect=RuntimeError("one message"))

        def send(self, text, images=()):
            self.sent.append(text)

        def collect(self, deadline, task):
            for line in self.lines + [self.result]:
                self.raw.write(json.dumps({"t_s": 1.0, "task": task, "message": line}) + "\n")
            return [(1.0, line) for line in self.lines + [self.result]], self.result, None

        def interrupt(self):
            pass

    def test_a_refusal_is_recorded_as_the_outcome_and_the_graded_stream_is_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            stream = self.Stream(folder, REFUSED, REFUSED_RESULT)
            graded = stream.raw
            usage = SimpleNamespace(turn=mock.Mock(return_value={"input_tokens": 57086}))
            output = {"trial": 6, "tasks": [ONESHOT]}
            summary = ask_why.ask_mcp(stream, usage, {"ask_why_on_miss": True, "debug_thinking": True}, folder, output)
            self.assertIs(stream.raw, graded, "the graded stream's file is back in place")
            graded.close()
            self.assertEqual((Path(folder) / "claude-stream.jsonl").read_text(), "")
            follow_up = (Path(folder) / ask_why.WHY_STREAM).read_text().splitlines()
            self.assertEqual({json.loads(line)["task"] for line in follow_up}, {ask_why.ASK_WHY_TASK})
            self.assertEqual(summary["outcome"], "refused by the provider (reasoning_extraction)")
            self.assertEqual((summary["refusal"], summary["usage"]), ("reasoning_extraction", {"input_tokens": 57086}))
            page = (Path(folder) / "why.md").read_text()
            self.assertIn("(refused by the provider (reasoning_extraction))", page)
            self.assertIn("> API Error: Opus 5.5's safeguards flagged this message.", page)
            self.assertNotIn("this chat is now read-only", stream.sent[0])

    def test_no_question_without_the_flag_or_a_running_client(self):
        self.assertIsNone(ask_why.ask_mcp(None, None, {}, "/nonexistent", {"tasks": [ONESHOT]}))
        output = {"tasks": [ONESHOT]}
        ask_why.ask_mcp(None, None, {"ask_why_on_miss": True}, "/nonexistent", output)
        self.assertEqual(output["ask_why"], {"asked": False, "reason": "Claude Code is not running"})

    def test_the_mcp_trial_ends_through_the_scenario_end_of_trial(self):
        source = (HERE / "bench_mcp_trial.py").read_text()
        body = source[source.index("def run_trial"):source.index("def probe_mcp_server")]
        self.assertLess(body.index("scenario.end_of_trial(client, stream, usage, folder, config, output)"),
                        body.index("stream.close()"))


class TranscriptSplitTests(unittest.TestCase):
    def test_the_graded_turns_and_the_follow_up_are_kept_apart_byte_for_byte(self):
        graded = [json.dumps({"type": "user", "message": {"content": "Build the bridge ✓"}}) + "\n",
                  json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Done"}]}}) + "\n"]
        asked = [json.dumps({"type": "user", "message": {"content": [{"type": "text", "text": ask_why.question(
                     [("brief", "sky", {})])}]}}) + "\n",
                 json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Why"}]}}) + "\n"]
        with tempfile.TemporaryDirectory() as folder:
            source, kept, why = (Path(folder) / name for name in ("session.jsonl", "graded.jsonl", "why.jsonl"))
            source.write_bytes("".join(graded + asked).encode())
            self.assertTrue(ask_why.split_transcript(source, kept, why))
            self.assertEqual((kept.read_bytes(), why.read_bytes()), ("".join(graded).encode(), "".join(asked).encode()))
            source.write_bytes("".join(graded).encode())
            why.unlink()
            self.assertFalse(ask_why.split_transcript(source, kept, why))
            self.assertEqual(kept.read_bytes(), source.read_bytes())
            self.assertFalse(why.exists())



class FollowUpTests(unittest.TestCase):
    """``--follow-up QUESTION``: a dev run's own question after grading, whatever the checks; never after an abort,
    and the run stays marked diagnostic (the gate tools refuse it)."""

    def test_the_question_reaches_the_app_and_is_asked_whatever_the_checks(self):
        import argparse
        parser = argparse.ArgumentParser()
        diagnostics.add_arguments(parser)
        args = parser.parse_args(["--follow-up", "Why did you do it that way?"])
        stamp = diagnostics.stamp(args)
        self.assertEqual(stamp, {"follow_up": "Why did you do it that way?"})
        self.assertEqual(diagnostics.arguments(stamp), ["--follow-up", "Why did you do it that way?"])
        self.assertEqual(diagnostics.only_known(dict(stamp, claim="x")), stamp)
        self.assertIn("a follow-up question", diagnostics.plan(args)["diagnostics"]["claim"])
        environ = {key: value for key, value in diagnostics.environment(args).items() if value}
        self.assertEqual(diagnostics.in_app_flags(environ), stamp)
        passed = {"tasks": [{"task": "t", "status": "completed", "host_check_passed": True}]}
        self.assertEqual(ask_why.due(passed, object(), environ), ([], None))
        self.assertEqual(ask_why.due(passed, object(), {})[1], "no closed check failed")
        self.assertIsNotNone(ask_why.due(dict(passed, aborted="limit"), object(), environ)[1])
        text = ask_why.follow_up_question("Why did you do it that way?")
        self.assertIn("read-only", text)
        self.assertIn("Why did you do it that way?", text)
        self.assertGreater(diagnostics.extra_seconds(args), 0)
        self.assertEqual(diagnostics.stamp(parser.parse_args([])), {})


if __name__ == "__main__":
    unittest.main()
