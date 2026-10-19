# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where a Claude turn ends, it tells the person the self-checks still open, and nothing else: no job is ever held
or owed for a check, and no name round is asked for (turn_endings).

Pinned with the real ClaudeProvider loop and mocked rounds, as in test_native_self_checks.
"""

from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from claude_protocol_fixtures import host  # noqa: E402
from angelo_agent import turn_endings  # noqa: E402
from angelo_agent.providers import ClaudeProvider  # noqa: E402
from angelo_agent.self_checks import SelfChecks  # noqa: E402
from angelo_agent.turn_endings import TurnEndings  # noqa: E402

BUILD = {"tool": "angelo_execute_python", "arguments": {"description": "Build the castle.", "code": "result = 1"}}
STILL = {"tool": "angelo_job_start", "arguments": {"kind": "RENDER_STILL", "output": "renders/castle-final.png"}}
STARTED = {"success": True, "job": "j1", "status": "queued"}


def ran(passed):
    return {"success": True, "result": 1, "output": "", "self_checks": [
        {"name": "crown", "measured": 5.5 if passed else 3.5, "target": 5.5, "tolerance": 0.05, "passed": passed}]}


def plan(*operations, verify=False, reply=""):
    return {"reply": reply, "operations": list(operations), "verify": verify}


class EndingTests(unittest.TestCase):
    def emitted(self, endings, current=True):
        rows = []
        status = endings.done(lambda owner, kind, data: rows.append(data), None, current)
        return status, rows

    def test_a_completed_ending_lists_the_open_checks_as_facts(self):
        checks = SelfChecks()
        checks.read([BUILD], [ran(False)])
        status, rows = self.emitted(TurnEndings(checks))
        self.assertEqual(status, "completed")
        self.assertEqual(rows[0]["text"], "The script's self-checks still fail:\n"
                                          "crown: measured 3.5, target 5.5 (tolerance 0.05)")
        self.assertEqual(rows[0]["i18n"][0], "The script's self-checks still fail:\n{failures}")

    def test_nothing_open_nothing_said_and_a_stop_says_nothing(self):
        checks = SelfChecks()
        self.assertEqual(self.emitted(TurnEndings(checks)), ("completed", []))
        checks.read([BUILD], [ran(False)])
        self.assertEqual(self.emitted(TurnEndings(checks), current=False), ("interrupted", []))
        self.assertEqual(self.emitted(TurnEndings()), ("completed", []))

    def test_no_job_is_ever_owed_and_nothing_is_asked(self):
        for gone in ("JOBS_NOTE", "JOB", "SETTINGS", "stated_names"):
            self.assertFalse(hasattr(turn_endings, gone), gone)
        for gone in ("read", "ask"):   # Nothing is owed where a turn would end (no stated-names round).
            self.assertFalse(hasattr(TurnEndings, gone), gone)


class LoopTests(unittest.TestCase):
    def test_a_still_planned_beside_a_failing_build_starts_and_nothing_is_asked_for_it(self):
        context = host()
        context.allowed_tools = None
        context.request_observation = Mock(side_effect=lambda callback: callback({"context_version": 2, "objects": []}))
        provider = ClaudeProvider(context, {})
        provider._turn = Mock(side_effect=[plan(BUILD, STILL), plan(reply="Built; the crown check was wrong.")])
        provider._native_call = Mock(side_effect=[ran(False), STARTED] + [{"context_version": 2, "objects": []}] * 8)
        self.assertEqual(provider.run(None, "Build a castle and render it", observation={}), "completed")
        tools = [call.args[0] for call in provider._native_call.call_args_list]
        self.assertEqual(tools.count("angelo_job_start"), 1)
        prompts = [call.args[1] for call in provider._turn.call_args_list]
        self.assertFalse(any(prompt.startswith("A job you planned") for prompt in prompts))
        texts = [data["text"] for kind, data in context.events if kind == "message" and data.get("role") == "system"]
        self.assertFalse(any(text.startswith("A job planned") for text in texts))

    def test_a_plan_ended_by_a_decline_still_says_the_checks_its_script_left_open(self):
        # The plan's reply was shown before it ran; the decline ends the turn before any round, so the person reads
        # the open checks the model never saw.
        context = host()
        context.allowed_tools = None
        provider = ClaudeProvider(context, {})
        provider._turn = Mock(side_effect=[plan(BUILD, STILL, reply="Built and rendering.")])
        declined = {"error": "The user declined this operation. Do not repeat it.", "error_code": "DECLINED"}
        provider._native_call = Mock(side_effect=[ran(False), declined])
        self.assertEqual(provider.run(None, "Build a castle and render it", observation={}), "interrupted")
        texts = [data["text"] for kind, data in context.events if kind == "message" and data.get("role") == "system"]
        self.assertTrue(texts[-1].startswith("The script's self-checks still fail:\ncrown"))


if __name__ == "__main__":
    unittest.main()
