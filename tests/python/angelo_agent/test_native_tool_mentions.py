# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No chat is told about an Angelo tool it does not have.

Worker chats were told about angelo_view while it was not declared to them, and a worker offered a tool it could not
run planned with it, was refused and ended its turn without building its part (live trial 2026-10-24). The class:
an ``angelo_*`` tool named in a chat's instructions or tool descriptions that the chat neither declares nor defers.
For every kind of chat Angelo builds (``tool_mention_kinds``: a lead, a signed-in team member, a worker's own chat in
the lead's Angelo and in a worker Blender that hosts its chats, an app's task, a collaborator, each on every runtime
it can use, and a connected app at its levels), the names are read from the real text sent up front, and any such
name is a tool of the catalog. Without Blender; each environment in a fresh interpreter (the tool list is built at
import).

Every text is composed by ``tool_guidance`` (a block naming a tool the chat lacks is left out), and the blocks naming
a tool a worker lacks follow what a lead and its workers share, so a worker's prefix is the lead's byte for byte.
"""

import fnmatch
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
from angelo_agent.worker_spec import WORKER_BRIEFING as BRIEFING  # noqa: E402
NAME = re.compile(r"(?<![A-Za-z0-9])angelo_[a-z0-9_]*[a-z0-9]")   # more__angelo_job_start names angelo_job_start.
# A family written with a wildcard (angelo_studio_*, angelo_*sculpt*): told of it, a chat must have one of its tools.
WILDCARD = re.compile(r"(?<![A-Za-z0-9])angelo_[a-z0-9_*]*\*[a-z0-9_*]*")
ENVIRONMENTS = {"parallel off": {"ANGELO_ENABLE_PARALLEL_AGENTS": "0"},
                "parallel on": {"ANGELO_ENABLE_PARALLEL_AGENTS": "1"},
                "hosting worker": {"ANGELO_ENABLE_PARALLEL_AGENTS": "0", "ANGELO_PARALLEL_WORKER": "1",
                                   "ANGELO_HEADLESS": "1"}}
RUNTIMES = {"claude_plan", "claude_direct", "codex_openai", "codex_third_party"}


def survey(environ):
    """``tool_mention_kinds`` in a fresh interpreter under ``environ``."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("ANGELO_")}
    out = subprocess.run([sys.executable, "-B", str(HERE / "tool_mention_kinds.py")], env=dict(env, **environ),
                         capture_output=True, text=True, timeout=300, check=True)
    return json.loads(out.stdout.splitlines()[-1])


class ToolMentions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.surveys = {name: survey(environ) for name, environ in ENVIRONMENTS.items()}
        # A tool name is any tool of any environment's catalog or any declared list (an app's launcher tools).
        cls.tools = set().union(*(set(found["catalog"]) | {name for kind in found["kinds"].values()
                                                           for name in kind["declared"]}
                                  for found in cls.surveys.values()))

    def undeclared(self):
        """``{environment: {kind: sorted names (or wildcard families) it is told of but does not declare}}``."""
        def lacking(entry):
            declared = set(entry["declared"])
            names = (set(NAME.findall(entry["text"])) & self.tools) - declared
            families = {pattern for pattern in WILDCARD.findall(entry["text"])
                        if fnmatch.filter(self.tools, pattern) and not fnmatch.filter(declared, pattern)}
            return sorted(names | families)
        return {environment: {kind: lacking(entry) for kind, entry in found["kinds"].items()}
                for environment, found in self.surveys.items()}

    def test_every_kind_of_chat_is_covered(self):
        kinds = {environment: set(found["kinds"]) for environment, found in self.surveys.items()}
        for role in ("lead", "team", "app_task", "collaborator"):
            self.assertTrue(all({role + "/" + runtime for runtime in RUNTIMES} <= found for found in kinds.values()
                                if "worker_hosted/claude_plan" not in found), role)
        self.assertTrue({"lead/voice", "team/voice"} <= kinds["parallel off"])
        self.assertTrue({"worker_lanes/" + runtime for runtime in RUNTIMES} <= kinds["parallel on"])
        self.assertEqual(kinds["hosting worker"], {"worker_hosted/" + runtime for runtime in RUNTIMES})
        apps = {"mcp_app/read", "mcp_app/read+edit", "mcp_app/read+edit+python+tasks"}
        self.assertTrue(apps <= kinds["parallel off"])
        for found in self.surveys.values():
            for kind, entry in found["kinds"].items():
                self.assertGreater(len(NAME.findall(entry["text"])), 3, kind + ": its text was read")
                self.assertTrue(entry["declared"], kind)

    def test_no_chat_is_told_of_a_tool_it_does_not_have(self):
        problems = ["%s, %s: told of %s without declaring them" % (environment, kind, names)
                    for environment, found in self.undeclared().items()
                    for kind, names in sorted(found.items()) if names]
        self.assertEqual(problems, [], "\n".join(problems))

    def test_a_lead_and_its_workers_share_their_instructions_up_to_the_workers_tail(self):
        """What names a tool a worker lacks follows the shared text (tool_guidance), so everything a worker reads
        before its own tail and briefing is the lead's prefix, byte for byte, and the prompt cache the lead filled
        serves it."""
        kinds = self.surveys["parallel on"]["kinds"]
        for runtime in sorted(RUNTIMES):
            with self.subTest(runtime=runtime):
                lead, worker = kinds["lead/" + runtime], kinds["worker_lanes/" + runtime]
                self.assertTrue(worker["instructions"].endswith(BRIEFING), "a worker's own text is last")
                own = worker["instructions"][:-len(BRIEFING)]
                shared = own[:own.rindex(worker["tail"])] if worker["tail"] else own
                self.assertTrue(lead["instructions"].startswith(shared), "the lead's prefix")
                self.assertTrue(lead["tail"] in lead["instructions"][len(shared) - 1:], "the lead's tail follows it")
                self.assertLessEqual(len(own) - len(shared), len(worker["tail"]) + 1, "nothing else is the worker's")
                hosted = self.surveys["hosting worker"]["kinds"]["worker_hosted/" + runtime]
                self.assertEqual(hosted["instructions"], worker["instructions"], "a hosted worker reads the same")


class ComposedTextTests(unittest.TestCase):
    """``tool_guidance``: blocks join back to the whole text, a kept block is never dropped, and a chat offered every
    tool reads every block and every description unchanged."""

    def test_blocks_and_descriptions(self):
        from angelo_agent import tool_guidance as guidance
        text = "Read with angelo_view. Edit with angelo_scene_program. Ask angelo_delegate."
        blocks = guidance.split(text, " Edit with angelo_scene_program.")
        self.assertEqual("".join(blocks), text)
        self.assertEqual(guidance.told(blocks, None), text)
        self.assertEqual(guidance.told(blocks, {"angelo_view"}), "Read with angelo_view. Ask angelo_delegate.",
                         "a kept block stays even when it names a tool the chat lacks (the mention test finds that)")
        with self.assertRaises(ValueError):
            guidance.split(text, "angelo_view", "angelo_view")
        self.assertEqual(guidance.described(text, None), text)
        self.assertEqual(guidance.described(text, {"angelo_view", "angelo_delegate"}),
                         "Read with angelo_view. Ask angelo_delegate.")
        self.assertEqual(guidance.named("tools.more__angelo_job_start, angelo_flow_*, angelo_view"),
                         {"angelo_job_start", "angelo_view"})
        catalog = {"angelo_view", "angelo_sculpt_view", "angelo_prepare_sculpt", "angelo_studio_list"}
        sculpting = "Sculpt with angelo_*sculpt* tools."
        self.assertEqual(guidance.families(sculpting + " Use angelo_nothing_*.", catalog),
                         [{"angelo_sculpt_view", "angelo_prepare_sculpt"}], "a pattern matching no tool names nothing")
        self.assertTrue(guidance.fits(sculpting, {"angelo_prepare_sculpt"}, catalog), "one member of the family is enough")
        self.assertFalse(guidance.fits(sculpting, {"angelo_view", "angelo_studio_list"}, catalog))
        self.assertEqual(guidance.told(("A. ", "Studio: angelo_studio_*."), {"angelo_view"}, catalog), "A. ")


if __name__ == "__main__":
    unittest.main()
