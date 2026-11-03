# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo ships per-model instruction text: each runtime's text is pinned to its bytes.

A Claude session reads the Claude per-model text (claude_text, claude_plan_guidance) and a Codex thread the
facts-only text (providers.INSTRUCTIONS, codex_plan, agent_guidance). On the facts-only text Opus 5.5 often lost a
closing brace of its StructuredOutput plan, and did not on the Claude text. A change to either text is a change of
what that model was measured on, so it fails here first.

Facts are not per-model: both runtimes' texts include the one shared statement of each (the Blender API facts,
agent_guidance.BLENDER_API_FACTS; the scene settings' paths, codex_reference.scene_settings_line). The per-model split
once left them in the Codex text only, and Opus, never told the version, wrote Blender 4.x names; the parity tests
below keep any split from dropping one.
"""

import hashlib
import itertools
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))

from angelo_agent import providers  # noqa: E402
from angelo_agent.claude_plan_guidance import CLAUDE_PLAN_INSTRUCTIONS  # noqa: E402


def digest(text):
    data = text.encode()
    return hashlib.sha256(data).hexdigest()[:12], len(data)


class PerModelTextTests(unittest.TestCase):
    def test_a_claude_session_reads_rc10_s_bytes(self):
        # The base and plan text a Claude session appends (claude_session).
        # The 1.0 text plus angelo_job_start's arguments as a fact (agent_integration.rst, toolset 34): 12,070 -> 12,728 B.
        # Plus the facts the Codex text already stated: the Blender API facts after the script sentences and the scene
        # settings' paths before the job sentence: 12,728 -> 13,785 B.
        # Plus the Sky Texture's sun_rotation frame and its scattering types' colours in the shared facts: +300 B.
        # Plus the render job's scene format and frame-pattern name (VFX audit A2, job guidance): +21 B, 14,085 -> 14,106.
        # Plus the job's format MP4 in its arguments (long_jobs.rst, "Video output"): +6 B, 14,112.
        # Plus BAKE_MAPS with its packing and material in angelo_job_start's arguments: +80 B, 14,192.
        # Plus RENDER_REGION and its region in the job arguments (render_region.rst): +54 B, 14,246.
        # The job, .blend-file, delegate and Director blocks after the plan text (claude_text.CLAUDE_TAIL), so a lead
        # and its workers, which lack those tools, share the rest byte for byte: the same 14,246 B, reordered. The
        # sculpting and Studio sentence there too (a worker has neither family): the same 14,246 B.
        from angelo_agent.claude_text import claude_tail
        self.assertEqual(digest(providers.CLAUDE_INSTRUCTIONS + CLAUDE_PLAN_INSTRUCTIONS + claude_tail(None)),
                         ("d5f702ddc9c8", 14246))

    def test_both_runtimes_state_the_job_start_arguments_in_one_form(self):
        from angelo_agent.codex_reference import job_arguments
        from angelo_agent.claude_text import claude_tail
        line = job_arguments()
        self.assertIn(line, claude_tail(None))
        # A fact only: nothing says where or when to call it (a "put it in a plan" wording lowered quality).
        self.assertNotIn("angelo_job_start needs no lookup", providers.CLAUDE_INSTRUCTIONS)
        self.assertNotIn("put it in", providers.CLAUDE_INSTRUCTIONS)
        codex = providers.codex_instructions(short_identifiers=True, deferred=True, plan=True, team=True)
        self.assertIn("angelo_job_start arguments (", codex)
        # Only the job start is stated: the job family's guidance and the media families stay deferred.
        from angelo_agent.agent_guidance import JOB_GUIDANCE
        self.assertNotIn(JOB_GUIDANCE[:80], providers.CLAUDE_INSTRUCTIONS + CLAUDE_PLAN_INSTRUCTIONS)

    def test_a_codex_team_plan_thread_reads_the_facts_bytes(self):
        # The facts text, reference included.
        text = providers.codex_instructions(short_identifiers=True, deferred=True, plan=True, team=True)
        # The sky facts (sun_rotation's frame, the scattering types' colours): 24,958 -> 25,258 B.
        # The render job's scene format and frame-pattern name (VFX audit A2, job guidance): +21 B, 25,258 -> 25,279;
        # the web export among the deferred families: +16 B, 25,295; hand rigging among them (toolset 43): +14 B, 25,309;
        # the job's format MP4 (long_jobs.rst, "Video output"): +6 B, 25,315.
        # Which fields each mode takes, for the reference's marker_edit, camera and light (mode_fields): +480 B, 25,795.
        # The Angelo Library among the deferred families (toolset 45): +20 B, 25,815.
        # BAKE_MAPS with its packing and material in angelo_job_start's arguments: +80 B, 25,895.
        # RENDER_REGION and its region in the job arguments (render_region.rst): +70 B, 25,965.
        # The deferred families' sentence and the job, delegate, .blend-file and Director blocks after the reference
        # (codex_text.codex_tail), shared with workers up to there: the same 25,965 B, reordered.
        # angelo_skeleton_check among the deferred families (toolset 47, engine_skeleton.rst): +23 B, 25,988.
        # The game-ready check too (toolset 48, game_ready_check.rst): +22 B, 26,010.
        # The game-engine preflight among them too (toolset 49, engine_preflight.rst): +27 B, 26,037.
        # The engine live link among them too (toolset 50, engine_link.rst, "The agent"): +22 B, 26,059.
        self.assertEqual(digest(text), ("bae00ed344fd", 26059))
        # On a runtime that shows tools' pictures without image() (image_proof.codex_guidance): that sentence only,
        # +29 B. Every other runtime keeps the text above.
        forwarded = providers.codex_instructions(short_identifiers=True, deferred=True, plan=True, team=True,
                                                 forwarded=True)
        self.assertEqual(digest(forwarded), ("33b100a26753", 26088))

    def test_the_codex_shared_base_keeps_its_bytes(self):
        # Unchanged when the API facts moved into the shared constant: the Codex text includes it in the same place.
        # The sky facts: 15,380 -> 15,680 B.
        # The Studio guidance states video models and multi-view 3D (TRELLIS 2) instead of "do not invent video" and
        # "3D supports one", which contradicted the tools (audit 2026-11-01, features.rst): +177 B, 15,857. Plan
        # threads defer this guidance, so their text is unchanged.
        # Each family's guidance after the shared text (codex_text.codex_tail): the same 15,857 B, reordered.
        self.assertEqual(digest(providers.INSTRUCTIONS), ("f15522a46dcc", 15857))

    def test_every_runtime_text_states_each_shared_fact_once(self):
        from angelo_agent.agent_guidance import BLENDER_API_FACTS
        from angelo_agent.codex_reference import SCENE_PATHS, scene_settings_line
        for name, text in assembled_texts():
            with self.subTest(text=name):
                self.assertEqual(text.count(BLENDER_API_FACTS), 1)
                if name == "codex voice" or name.startswith("mcp "):
                    # Voice and external Codex threads read the base without the program reference, so they lack the
                    # scene settings' paths too: a known gap, left while the Codex bytes stay as measured. An MCP
                    # app's typed edits carry the direct-tools schemas instead of the program reference.
                    continue
                self.assertEqual(text.count(scene_settings_line()), 1)
                self.assertEqual([path for path in SCENE_PATHS if path not in text], [])

    def test_an_old_api_name_the_codex_text_states_reaches_every_claude_text(self):
        # The class, not these names: any Blender 4.x name in the host's tables that one runtime's text speaks about,
        # wherever in that text it is written, every other runtime's text states too.
        texts = dict(assembled_texts())
        codex = [text for name, text in texts.items() if name.startswith("codex")]
        stated = {old for old in old_api_names() if any(old in text for text in codex)}
        self.assertTrue(stated)
        for name, text in texts.items():
            with self.subTest(text=name):
                self.assertEqual(sorted(old for old in stated if old not in text), [])


def old_api_names():
    """Every Blender 4.x name the host's script checks know (script_api_notes, script_api_removed)."""
    from angelo_agent import script_api_notes, script_api_removed
    names = {row.get("string") or row.get("attribute") for row in script_api_notes.RENAMED}
    names |= {value for _, value in script_api_removed.VALUES} | set(script_api_removed.ATTRIBUTES)
    return names | set(script_api_removed.OPERATORS)


def assembled_texts():
    """(name, text) for every instruction text a runtime reads: each Claude launch and each Codex thread's text."""
    from angelo_agent.claude_transport import claude_session
    for direct, tools in itertools.product((False, True), (("WebSearch", "WebFetch"), ())):
        context = SimpleNamespace(turn_options=None, state={"provider": "claude_subscription", "options": {}},
                                  native_tools=tools, claude_direct_tools=direct)
        yield "claude direct=%s web=%s" % (direct, bool(tools)), "".join(claude_session("claude", context)[1]["request"]["systemPrompt"])
    yield "codex voice", providers.INSTRUCTIONS
    for flags in itertools.product((False, True), repeat=5):
        options = dict(zip(("short_identifiers", "deferred", "plan", "team", "forwarded"), flags))
        yield "codex %s" % options, providers.codex_instructions(**options)
    # An external app's model on Angelo's MCP server writes scripts too (its python level): it reads the same facts.
    from angelo_agent.external_clients import LEVELS
    from angelo_agent.external_mcp_tools import instructions
    for count in range(1, len(LEVELS) + 1):
        yield "mcp %s" % ",".join(LEVELS[:count]), instructions(frozenset(LEVELS[:count]))


if __name__ == "__main__":
    unittest.main()
