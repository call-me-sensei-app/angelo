# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Studio's prompt editor reads its own text only, on every Claude and Codex connection.

The editor (studio_enhance) runs the chat's own providers in a text-only context. Its Claude launch used to append the
chat agent's plan text (about 3.5 KB on scenes, sculpting and views) after the editor's ~350 bytes (seen in the
studio-claude probe's recorded request). The editor's text is now the session's whole Angelo text on either runtime, and no
line of any chat-agent text reaches it, whichever field (appendSystemPrompt or systemPrompt) a build sends it in.
"""

import ast
import itertools
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))

from angelo_agent import claude_direct, codex_plan, providers  # noqa: E402
from angelo_agent.claude_text import CLAUDE_BASE_INSTRUCTIONS, CLAUDE_PLAN_INSTRUCTIONS  # noqa: E402
from angelo_agent.claude_transport import claude_session  # noqa: E402
from angelo_agent.agent_guidance import NO_WEB_SEARCH  # noqa: E402
from angelo_agent.provider_registry import CUSTOM_PROVIDER_MODES, SPECS, runtime_kind, spec_for  # noqa: E402
from angelo_agent.studio_enhance import OWN_TEXTS, EnhancementContext  # noqa: E402

MODES = tuple(sorted(mode for mode, spec in SPECS.items() if spec["runtime"]))   # Every coding connection.


def agent_texts():
    """Every text the chat agent reads on either runtime: the Claude base/plan/direct-tools texts, voice, and each
    Codex base (full, deferred, team, short identifiers, plan)."""
    yield from (CLAUDE_BASE_INSTRUCTIONS, CLAUDE_PLAN_INSTRUCTIONS, claude_direct.INSTRUCTIONS, providers.INSTRUCTIONS,
                codex_plan.PLAN_GUIDANCE)
    for short, deferred, plan, team in itertools.product((False, True), repeat=4):
        yield providers.codex_instructions(short_identifiers=short, deferred=deferred, plan=plan, team=team)


def agent_lines():
    """The chat agent's distinctive lines (24+ characters), each a leak if the editor's text holds it."""
    return {line.strip() for text in agent_texts() for line in text.splitlines() if len(line.strip()) >= 24}


def editor(mode, directory, use="editor"):
    return EnhancementContext(directory, {"mode": mode}, {}, "", "", [], threading.Event(), use=use)


def claude_prompt(request):
    """The Angelo text of a Claude initialize request, in either field a build sends it in."""
    if "systemPrompt" in request:
        prompt, = request["systemPrompt"]
        return prompt
    return request["appendSystemPrompt"]


def launches():
    """(use, mode, its Angelo text, Claude's plan schema or None) for every text-only use (studio_enhance.OWN_TEXTS:
    the prompt editor, the parts check) on every connection it can use."""
    with tempfile.TemporaryDirectory() as directory:
        for use, mode in itertools.product(OWN_TEXTS, MODES):
            context = editor(mode, directory, use)
            if runtime_kind(mode) == "claude":
                request = claude_session("claude", context)[1]["request"]
                yield use, mode, claude_prompt(request), request.get("jsonSchema")
            else:
                parameters, _ = providers.CodexProvider(context, {})._thread_parameters([])
                yield use, mode, parameters["baseInstructions"], None


class StudioEditorPromptTests(unittest.TestCase):
    def test_each_launch_sends_exactly_its_own_text(self):
        for use, mode, prompt, _ in launches():
            with self.subTest(use=use, mode=mode):
                instructions, claude_reply = OWN_TEXTS[use]
                own = instructions + ("" if runtime_kind(mode) == "codex" else claude_reply)
                # A third-party Codex connection without web search says so after any thread's base: a fact of the
                # connection, not of the chat agent.
                if mode in CUSTOM_PROVIDER_MODES and not spec_for(mode).get("web_search"):
                    own += NO_WEB_SEARCH
                self.assertEqual(prompt, own)

    def test_no_chat_agent_line_reaches_the_editor(self):
        lines = agent_lines()
        self.assertGreater(len(lines), 100)   # The sweep reads the real texts, not an empty set.
        for use, mode, prompt, _ in launches():
            with self.subTest(use=use, mode=mode):
                self.assertEqual(sorted(line for line in lines if line in prompt), [])
                self.assertNotIn("You are Angelo", prompt)

    def test_claude_s_structured_reply_offers_the_editor_no_tools(self):
        # The one StructuredOutput call the Claude text names: its operations can hold nothing.
        for use, mode, _, schema in launches():
            if schema is not None:
                with self.subTest(use=use, mode=mode):
                    self.assertEqual(schema["properties"]["operations"]["maxItems"], 0)
                    self.assertEqual(schema["properties"]["reply"], {"type": "string"})

    def test_every_use_tells_claude_where_its_answer_goes(self):
        # Claude Code always ends with the StructuredOutput call; a use that didn't say its answer goes in reply
        # could end with an empty reply (an empty answer, or a failed parts check).
        for use, (_, claude_reply) in OWN_TEXTS.items():
            with self.subTest(use=use):
                self.assertIn("StructuredOutput", claude_reply)
                self.assertIn("reply", claude_reply)
                self.assertIn("operations is []", claude_reply)

    def test_only_studio_enhance_gives_a_context_its_own_text(self):
        # A context with its own base drops the chat agent's text, so its text must come from OWN_TEXTS, where every
        # use states its Claude reply too. Any other module setting base_instructions would bypass that.
        addon = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
        setters = set()
        for path in addon.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, (ast.AnnAssign, ast.AugAssign)) else []
                if any(isinstance(t, ast.Attribute) and t.attr == "base_instructions" for t in targets):
                    setters.add(path.name)
                if (isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "setattr"
                        and len(node.args) > 1 and isinstance(node.args[1], ast.Constant) and node.args[1].value == "base_instructions"):
                    setters.add(path.name)
        self.assertEqual(setters, {"studio_enhance.py"})

    def test_a_codex_editor_thread_ends_without_a_typed_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            for mode in MODES:
                if runtime_kind(mode) == "codex":
                    with self.subTest(mode=mode):
                        self.assertFalse(codex_plan.plan_mode(editor(mode, directory), False))

    def test_the_chat_agent_still_reads_its_plan_text(self):
        # Only a context with its own base drops the agent's text: an ordinary Claude chat keeps it.
        context = SimpleNamespace(turn_options=None, state={"provider": "claude_subscription", "options": {}})
        self.assertTrue(claude_prompt(claude_session("claude", context)[1]["request"]).endswith(CLAUDE_PLAN_INSTRUCTIONS))


if __name__ == "__main__":
    unittest.main()
