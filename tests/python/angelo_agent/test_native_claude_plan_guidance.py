# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Claude's plan instructions (claude_plan_guidance) state facts, not a workflow.

Angelo ships per-model text (claude_text, test_native_per_model_text): a
Claude session reads the Claude per-model plan text, and Codex threads read the
facts-only text. Neither carries sentences that tell the model how to work (how
long to think, to look at a build and then render a still, to build with the
script kit and end with its checks). The model decides how to build; the host gives
it correct facts and full visibility (doc/angelo/agent_integration.rst,
"Toolset 39"). The kit stays in every script's namespace, undescribed in
Claude's text, so its code is still checked.
"""

import ast
import inspect
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))

from angelo_agent import agent_guidance  # noqa: E402
from angelo_agent.claude_plan_guidance import CLAUDE_PLAN_INSTRUCTIONS, SCENE_ENTRY  # noqa: E402
from angelo_agent.codex_reference import program_reference  # noqa: E402
from angelo_agent.providers import CLAUDE_INSTRUCTIONS, INSTRUCTIONS, codex_instructions  # noqa: E402
from angelo_agent.script_kit import ScriptKit  # noqa: E402

# One phrase per workflow sentence that the shipped text must not carry.
WORKFLOW_PHRASES = ("150 words", "think for at most", "settle its names and structure", "never by hand in thinking",
                    "never rehearse first", "To judge a build by its look", "start it once the scene looks right",
                    "omit verify after one", "Every script already has kit", "kit.",
                    "Build hard-surface solids and openings with it", "End a build with kit checks", "Parent in place")


def flat(text):
    return " ".join(text.split())


def codex_texts():
    """Each Codex thread's instructions without its TypeScript reference (code mode's call surface), by variant."""
    yield "Codex shared base", INSTRUCTIONS
    for short in (False, True):
        for deferred in (False, True):
            for plan in (False, True):
                for team in (False, True):
                    text = codex_instructions(short_identifiers=short, deferred=deferred, plan=plan, team=team)
                    reference = "\n" + program_reference(short_identifiers=short, plan=plan)
                    assert text.endswith(reference)
                    yield "Codex %s" % ((short, deferred, plan, team),), text[:-len(reference)]


def kit_calls(text):
    """Each ``kit.name(...)`` the text writes, with its balanced argument text."""
    for match in re.finditer(r"kit\.(\w+)\(", text):
        depth, end = 1, match.end()
        while depth:
            depth += {"(": 1, ")": -1}.get(text[end], 0)
            end += 1
        yield match.group(1), text[match.end():end - 1]


class PlanTextTests(unittest.TestCase):
    def test_no_workflow_sentence_of_the_castle_line_is_in_claude_s_text(self):
        text = flat(CLAUDE_INSTRUCTIONS + CLAUDE_PLAN_INSTRUCTIONS)
        for phrase in WORKFLOW_PHRASES:
            self.assertNotIn(phrase, text)

    def test_no_text_describes_a_self_check_gate_or_a_job_that_never_overwrites(self):
        # Self-checks are information (self_checks); the job output rule is the job tools' (job_spec, JOB_GUIDANCE).
        text = flat(CLAUDE_INSTRUCTIONS + CLAUDE_PLAN_INSTRUCTIONS)
        for phrase in ("sends the plan back", "never overwrites its output", "reply only when every check passes",
                       "four correction rounds", "cannot end as done", "returns a script once, unrun"):
            self.assertNotIn(phrase, text)
        self.assertFalse(hasattr(agent_guidance, "RENDER_IN_PLAN"))

    def test_the_plan_text_keeps_its_protocol_and_scene_facts(self):
        text = flat(CLAUDE_PLAN_INSTRUCTIONS)
        for fact in ("Answer only via one StructuredOutput call", "Targets are o<n> handles or $aliases",
                     "All steps are validated before the first runs", SCENE_ENTRY.split("\n")[0]):
            self.assertIn(flat(fact), text)


class KitTextTests(unittest.TestCase):
    def test_every_kit_call_the_text_names_exists_with_its_keywords_and_defaults(self):
        # Claude's text names no kit call (WORKFLOW_PHRASES) and Codex's names none either; any text that does
        # must state the kit's real keywords and defaults.
        texts = [("Claude", CLAUDE_INSTRUCTIONS + CLAUDE_PLAN_INSTRUCTIONS), *codex_texts()]
        for label, text in texts:
            for name, arguments in kit_calls(text.replace("\n", " ")):
                parameters = inspect.signature(getattr(ScriptKit, name)).parameters
                for keyword, value in re.findall(r"(\w+)=([^,()]+|\([^)]*\))", arguments):
                    self.assertIn(keyword, parameters, (label, name, keyword))
                    try:
                        stated = ast.literal_eval(value.strip())
                    except (ValueError, SyntaxError):
                        continue   # A shape such as unit=(length, depth, height), not a default.
                    self.assertEqual(parameters[keyword].default, stated, (label, name, keyword))

    def test_kit_row_takes_its_unit_and_gap_from_the_script(self):
        # A default would be one request's sizes, silently given to every script that left them out.
        parameters = inspect.signature(ScriptKit.row).parameters
        for name in ("unit", "gap"):
            self.assertIs(parameters[name].default, inspect.Parameter.empty, name)


class NoInlineArgumentJsonTests(unittest.TestCase):
    """No instruction text shows a tool's arguments as inline JSON outside the {tool, arguments} wrapper: with such
    fragments in the text, Opus plans lost the wrapper's closing brace, and without them they did not. The operation
    shape itself, {tool, arguments}, and
    result shapes stay. Codex's TypeScript reference is code mode's call surface, not a plan wrapper, so it is out."""

    def test_no_tool_name_is_followed_by_a_json_object_or_list(self):
        for label, text in (("Claude", CLAUDE_INSTRUCTIONS + CLAUDE_PLAN_INSTRUCTIONS), *codex_texts()):
            self.assertEqual(re.findall(r"angelo_\w+ \{|\[\{", text), [], label)


if __name__ == "__main__":
    unittest.main()
