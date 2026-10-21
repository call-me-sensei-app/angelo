# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which system prompt each route sends: Angelo's own text, whole, on every Claude launch.

A Claude session's initialize request carries Angelo's instructions as ``systemPrompt`` (its whole system prompt), not
``appendSystemPrompt`` (which kept Claude Code's coding-CLI prompt, about 5.2 KB on a subscription, ahead of them).
Claude Code adds its billing block and one identity line itself on every request, whichever field is sent (probed
unbilled on 2.1.292 and 2.1.301 with ``claude_prompt_probe.py``), so Angelo sends neither. Codex threads are untouched:
test_native_per_model_text pins their bytes.
"""

import itertools
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))

from angelo_agent import runtime_environment  # noqa: E402
from angelo_agent.claude_plan_guidance import CLAUDE_PLAN_INSTRUCTIONS  # noqa: E402
from angelo_agent.claude_text import CLAUDE_BASE_INSTRUCTIONS  # noqa: E402
from angelo_agent.claude_transport import claude_session  # noqa: E402
from angelo_agent.provider_registry import SPECS  # noqa: E402

CLAUDE_ROUTES = tuple(sorted(mode for mode, spec in SPECS.items() if spec["runtime"] == "claude"))
# Lines of Claude Code's own prompt (2.1.301) that must never reach a session through Angelo's text.
CODING_CLI = ("Claude Code", "in a terminal", "file_path:line_number", "surrounding code")


def launches():
    """(name, initialize request) for every Claude route, plan and direct-tools, with and without web search."""
    for route, direct, tools in itertools.product(CLAUDE_ROUTES, (False, True), (("WebSearch", "WebFetch"), ())):
        context = SimpleNamespace(turn_options=None, state={"provider": route, "options": {}},
                                  native_tools=tools, claude_direct_tools=direct)
        yield "%s direct=%s web=%s" % (route, direct, bool(tools)), claude_session("claude", context)[1]["request"]


class ClaudeSystemPromptTests(unittest.TestCase):
    def test_every_claude_launch_sends_angelo_s_text_as_its_whole_system_prompt(self):
        for name, request in launches():
            with self.subTest(launch=name):
                self.assertNotIn("appendSystemPrompt", request)
                self.assertEqual(len(request["systemPrompt"]), 1)
                prompt = request["systemPrompt"][0]
                self.assertTrue(prompt.startswith("You are Angelo, a Blender scene assistant, not a coding assistant."))
                self.assertEqual([line for line in CODING_CLI if line in prompt], [])
                self.assertIs(request["systemPromptSnapshot"], False)

    def test_the_plan_route_s_system_prompt_is_exactly_the_claude_text(self):
        # The bytes test_native_per_model_text pins, and nothing else: no runtime text before or after them.
        context = SimpleNamespace(turn_options=None, state={"provider": "claude_subscription", "options": {}})
        prompt, = claude_session("claude", context)[1]["request"]["systemPrompt"]
        self.assertEqual(prompt, CLAUDE_BASE_INSTRUCTIONS + CLAUDE_PLAN_INSTRUCTIONS)

    def test_a_context_s_own_base_is_the_start_of_the_whole_prompt(self):
        context = SimpleNamespace(turn_options=None, state={"provider": "claude_api", "options": {}},
                                  base_instructions="Return only the prompt.")
        prompt, = claude_session("claude", context)[1]["request"]["systemPrompt"]
        self.assertTrue(prompt.startswith("Return only the prompt."))


class ClaudeSessionToolsTests(unittest.TestCase):
    def test_every_session_turns_the_advisor_tool_off(self):
        environment = runtime_environment.session_environment({}, SimpleNamespace(prompt_cache_ttl=None))
        self.assertEqual(environment["CLAUDE_CODE_DISABLE_ADVISOR_TOOL"], "1")

    def test_an_inherited_advisor_setting_is_dropped(self):
        # A person's shell could hold "0"; the session's own "1" is the only value a child sees.
        self.assertTrue(runtime_environment.dropped("CLAUDE_CODE_DISABLE_ADVISOR_TOOL"))
        self.assertNotIn("CLAUDE_CODE_DISABLE_ADVISOR_TOOL",
                         runtime_environment.inherited({"CLAUDE_CODE_DISABLE_ADVISOR_TOOL": "0", "PATH": "/bin"}))


if __name__ == "__main__":
    unittest.main()
