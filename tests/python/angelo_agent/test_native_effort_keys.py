# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reasoning effort is in no cache, warm-up or prewarm key (caches are per model).

Effort is a request parameter, not prompt bytes: a warm opening, a cached
prefix and a prewarmed runtime serve every effort of their model and prefix.
The keys pinned here: a Codex chat's warm-opening variant
(``codex_connect.variant_key``: stored openings, warm-at-connect and the warm
after a turn all use it), its ``cacheAffinityKey`` (``cache_affinity``), the
runtime prewarm's launch key (``runtime_prewarm.key``) and the match a Claude
session is reused on (``ClaudeProvider._runtime_binding``; Claude Code's
running session takes the next turn's effort, ``claude_transport.apply_effort``).
Each guard fails if effort is ever added to that key.
"""

import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))

from angelo_agent import cache_affinity, codex_connect, runtime_prewarm  # noqa: E402
from angelo_agent.chat_settings import EFFORTS  # noqa: E402
from angelo_agent.providers import ClaudeProvider  # noqa: E402
from angelo_agent.routing import TOOLSET_VERSION  # noqa: E402

START = {"cwd": "/state/workspace", "modelProvider": "openai", "sandbox": "read-only", "approvalPolicy": "on-request",
         "serviceTier": "default", "model": "gpt-6.1-sol", "baseInstructions": "Build what the person asks.",
         "dynamicTools": [{"name": "angelo_scene_search"}]}
TURN = {"model": "gpt-6.1-sol", "effort": "low", "outputSchema": {"type": "object"}, "serviceTierForTurn": "default"}
ACCOUNT = ("codex_subscription", hashlib.sha256(b"account").hexdigest())
EVERY_EFFORT = EFFORTS + (None,)


def keys_named(value, name):
    """Whether ``name`` is a key anywhere in ``value`` (dicts and lists, any depth)."""
    if isinstance(value, dict):
        return name in value or any(keys_named(item, name) for item in value.values())
    return isinstance(value, list) and any(keys_named(item, name) for item in value)


class CodexKeyTests(unittest.TestCase):
    def test_turn_options_differing_only_in_effort_give_one_variant_and_one_affinity_key(self):
        variants = {codex_connect.variant_key(START, dict(TURN, effort=effort)) for effort in EVERY_EFFORT}
        affinity = {cache_affinity.derive(cache_affinity.inputs(ACCOUNT, START, dict(TURN, effort=effort),
                                                                TOOLSET_VERSION)) for effort in EVERY_EFFORT}
        self.assertEqual(len(variants), 1, "A warm opening serves every effort of its model and prefix")
        self.assertEqual(len(affinity), 1, "One cacheAffinityKey per account, model, prefix and toolset")

    def test_guard_effort_is_no_input_of_either_key(self):
        self.assertNotIn("effort", codex_connect.VARIANT_TURN_FIELDS)
        self.assertFalse(keys_named(codex_connect.variant_inputs(START, TURN), "effort"))
        self.assertFalse(keys_named(cache_affinity.inputs(ACCOUNT, START, TURN, TOOLSET_VERSION), "effort"))
        # What does shape the opening still keys it: the model and the response format.
        key = codex_connect.variant_key(START, TURN)
        self.assertNotEqual(key, codex_connect.variant_key(START, dict(TURN, model="gpt-6-astra")))
        self.assertNotEqual(key, codex_connect.variant_key(START, dict(TURN, outputSchema={})))


class LaunchKeyTests(unittest.TestCase):
    def test_guard_the_prewarm_key_and_the_claude_session_match_ignore_effort(self):
        self.assertIn("effort", runtime_prewarm.EFFORT_FREE)
        options = {"model": "sonnet", "effort": "low", "fast": False, "approval": "AUTO_BOUNDED"}
        launches = {tuple(sorted(runtime_prewarm.launch_options(dict(options, effort=effort)).items()))
                    for effort in EVERY_EFFORT}
        self.assertEqual(len(launches), 1)

        def binding(effort):
            context = SimpleNamespace(turn_options=dict(options, effort=effort), state={"options": {}},
                                      allowed_tools=None, native_tools=("WebSearch", "WebFetch"), instructions="")
            return ClaudeProvider._runtime_binding(None, context)
        self.assertEqual(len({repr(binding(effort)) for effort in EVERY_EFFORT}), 1)
        self.assertFalse(keys_named(json.loads(json.dumps(binding("high")[0])), "effort"))
        self.assertNotEqual(binding("low"), ClaudeProvider._runtime_binding(None, SimpleNamespace(
            turn_options=dict(options, fast=True), state={"options": {}}, allowed_tools=None,
            native_tools=("WebSearch", "WebFetch"), instructions="")), "A launch setting still relaunches")


if __name__ == "__main__":
    unittest.main()
