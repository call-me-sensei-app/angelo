# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo ships per-model instruction text: each runtime's text is pinned to its bytes.

A Claude session reads the Claude per-model text (claude_text, claude_plan_guidance) and a Codex thread the
facts-only text (providers.INSTRUCTIONS, codex_plan, agent_guidance). On the facts-only text Opus 5.5 often lost a
closing brace of its StructuredOutput plan, and did not on the Claude text. A change to either text is a change of
what that model was measured on, so it fails here first.
"""

import hashlib
from pathlib import Path
import sys
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
        self.assertEqual(digest(providers.CLAUDE_INSTRUCTIONS + CLAUDE_PLAN_INSTRUCTIONS), ("7711fd57d2aa", 12070))

    def test_a_codex_team_plan_thread_reads_the_facts_bytes(self):
        # The facts text, reference included.
        text = providers.codex_instructions(short_identifiers=True, deferred=True, plan=True, team=True)
        self.assertEqual(digest(text), ("2143be7246ec", 24958))

    def test_the_codex_shared_base_keeps_its_size(self):
        self.assertEqual(len(providers.INSTRUCTIONS.encode()), 15380)


if __name__ == "__main__":
    unittest.main()
