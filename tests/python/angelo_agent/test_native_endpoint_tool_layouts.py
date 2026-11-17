# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Test connection's requests keep the tool layout the runtimes really send (a drift guard).

Test connection (``endpoint_check_tools``) imitates the runtimes' requests so a
server is judged on the shape a chat uses; an imitation that drifts judges the
wrong thing (on 2026-11-13 the check sent a Responses Lite model's tools on the
developer message while Codex sends them in an ``additional_tools`` item, and a
working proxy read as one that drops custom tools). The runtimes' own layouts are
recorded by ``capture_endpoint_tool_layouts.py`` into
``fixtures/endpoint_tool_layouts.json`` for the pinned agent runtime; this test
fails when the pin moves until they are recorded again (doc/angelo/releasing.rst,
"Agent runtime"), and holds every request the check sends, the tool call and the
result sent back, to the layout of the runtime and shape it stands for.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
sys.path.insert(0, str(HERE))
import endpoint_tool_layout as layouts  # noqa: E402
from angelo_agent import agent_runtime, endpoint_check_tools as tools  # noqa: E402

FIXTURE = json.loads((HERE / "fixtures" / "endpoint_tool_layouts.json").read_text(encoding="utf-8"))
# Fork files no request depends on: the generated config schema its own tests compare, documentation.
NON_REQUEST = re.compile(r"(^|/)config\.schema\.json$|\.md$")
RECAPTURE = ("the agent runtime pin moved: run capture_endpoint_tool_layouts.py --write with the new build and commit "
             "the fixture (doc/angelo/releasing.rst, \"Agent runtime\")")
CALLS = {"responses": {"type": "custom_tool_call", "call_id": "c1", "name": tools.TOOL, "input": "ok"},
         "function": {"type": "function_call", "call_id": "c1", "name": tools.TOOL, "arguments": "{}"}}


class PinTests(unittest.TestCase):
    def test_the_layouts_come_from_the_pinned_runtime_or_a_proven_equivalent(self):
        """The fixture names the commit its codex was built from. Another commit than the pin counts only through its
        recorded difference: files no request depends on, verified against a fork checkout when one is given
        (``ANGELO_CODEX_FORK``)."""
        runtime, pinned = FIXTURE["runtime"], agent_runtime.PIN["fork"]["commit"]
        if runtime["codex_commit"] == pinned:
            self.assertNotIn("equivalent_to", runtime)
            return
        equivalence = runtime.get("equivalent_to") or {}
        self.assertEqual(equivalence.get("commit"), pinned, RECAPTURE)
        changed = equivalence.get("changed_files") or []
        self.assertTrue(changed, "an equivalence names the files that differ")
        self.assertEqual([f for f in changed if not NON_REQUEST.search(f)], [],
                         "the capture's build differs from the pin in files a request may depend on: " + RECAPTURE)
        fork = os.environ.get("ANGELO_CODEX_FORK")
        if fork:   # The recorded difference, checked against the fork itself.
            actual = subprocess.run(["git", "-C", fork, "diff", "--name-only", runtime["codex_commit"], pinned],
                                    check=True, capture_output=True, text=True).stdout.split()
            self.assertEqual(sorted(actual), changed)


class LayoutTests(unittest.TestCase):
    def assertSameLayout(self, format_id, body, recorded):
        self.assertEqual(layouts.differences(layouts.layout(format_id, body), FIXTURE["layouts"][recorded],
                                             format_id), [])

    def test_a_code_mode_models_requests_are_codex_own_form(self):
        shape = (tools.CUSTOM, True)
        made = {"responses": CALLS["responses"]}
        for body in (tools.responses_body("m", "p", "k", shape),
                     tools.responses_body("m", "p", "k", shape, turns=tools.answer_turns(made))):
            self.assertSameLayout("openai-responses", body, "responses_code_mode")

    def test_a_plain_models_requests_are_codex_own_form(self):
        made = {"responses": CALLS["function"]}
        for body in (tools.responses_body("m", "p", "k", tools.PLAIN),
                     tools.responses_body("m", "p", "k", tools.PLAIN, turns=tools.answer_turns(made))):
            self.assertSameLayout("openai-responses", body, "responses_plain")

    def test_gemini_and_messages_requests_are_the_runtimes_own_form(self):
        gemini_call = {"gemini": {"functionCall": {"name": tools.TOOL, "args": {}}}}
        for body in (tools.gemini_body("p"), tools.gemini_body("p", turns=tools.answer_turns(gemini_call))):
            self.assertSameLayout("gemini", body, "gemini")
        claude_call = {"messages": {"id": "t1", "name": tools.TOOL}, "input": {}}
        for body in (tools.messages_body("m", "p"), tools.messages_body("m", "p", turns=tools.answer_turns(claude_call))):
            self.assertSameLayout("anthropic-messages", body, "messages")

    def test_the_shapes_follow_the_recorded_forms(self):
        """A code-mode profile's catalog entry gives the custom Lite shape, which is what Codex sent for it."""
        recorded = FIXTURE["layouts"]["responses_code_mode"]
        self.assertIn(tools.CUSTOM, recorded["kinds"])
        self.assertNotEqual(recorded["carriers"], ["tools"])
        self.assertEqual(FIXTURE["layouts"]["responses_plain"]["carriers"], ["tools"])

    def test_the_guard_sees_a_drift(self):
        drifted = tools.responses_body("m", "p", "k", (tools.CUSTOM, True))
        drifted["input"][0]["type"] = "message"   # The form the check sent before 2026-11-13.
        self.assertTrue(layouts.differences(layouts.layout("openai-responses", drifted),
                                            FIXTURE["layouts"]["responses_code_mode"], "openai-responses"))


if __name__ == "__main__":
    unittest.main()
