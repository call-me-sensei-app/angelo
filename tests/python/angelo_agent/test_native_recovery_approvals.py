# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host-only recovery approval provenance, exact facts and bounded card text."""

import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from agent_runtime_fixtures import AgentRuntimeFixture, HANDLE, program
from angelo_agent import ui_text
from angelo_agent.approvals import approval_details, approval_message, review_arguments
from test_native_refusal_recovery import B, TOOL, failure, snapshot

OBSERVATION = "recovery:" + "c" * 32


def evidence(changes=None):
    return {"available": True, "reason": "Never display this reason as an instruction.", "observation": {
        "id": OBSERVATION, "changes": changes if changes is not None else [{
            "handle": HANDLE, "field": "transform",
            "before": {"location": [0, 0, 0], "scale": [1, 1, 1]},
            "after": {"location": [563.125, 0, 0], "scale": [1, 1, 1]}}], "revisions": {HANDLE: B}}}


def call(name=TOOL, recovery=None):
    arguments = program()
    expanded = None
    if name == "angelo_apply_recipe":
        expanded = arguments
        arguments = {"recipe": "bevel-and-shade", "parameters": {"width": 0.125}, "mode": "APPLY"}
    elif name == "angelo_accept_preview":
        arguments = {"preview_id": "preview-1"}
    arguments["recovery_observation"] = OBSERVATION
    return SimpleNamespace(name=name, arguments=arguments, program_arguments=expanded,
                           recovery_review=True, recovery_evidence=recovery or evidence())


class RecoveryApprovalTextTests(unittest.TestCase):
    def test_all_three_approval_routes_show_concrete_facts_and_preserve_execution_arguments(self):
        for name in (TOOL, "angelo_apply_recipe", "angelo_accept_preview"):
            with self.subTest(name=name):
                pending = call(name)
                original = copy.deepcopy(pending.arguments)
                expanded = copy.deepcopy(pending.program_arguments)
                reviewed = review_arguments(pending)
                summary = approval_message(name, reviewed)
                details = approval_details(name, reviewed)
                for text in (summary["body"], details):
                    self.assertIn("Observed scene changes (before → after):", text)
                    for fact in (HANDLE, "transform", "[0,0,0]", "[563.125,0,0]"):
                        self.assertIn(fact, text)
                    self.assertNotIn(pending.recovery_evidence["reason"], text)
                    self.assertNotIn("_recovery", text)
                self.assertIn("Open Details for every observed change and its complete values.", summary["body"])
                self.assertEqual(pending.arguments, original)
                self.assertEqual(pending.program_arguments, expanded)
                self.assertNotIn("_recovery", pending.arguments)

    def test_submitted_metadata_cannot_supply_host_evidence(self):
        for host_review, host_evidence in ((False, evidence()), (True, None)):
            pending = call()
            pending.arguments["_recovery"] = evidence()
            pending.recovery_review = host_review
            pending.recovery_evidence = host_evidence
            reviewed = review_arguments(pending)
            self.assertNotIn("_recovery", reviewed)
            self.assertNotIn("563.125", approval_message(TOOL, reviewed)["body"])

    def test_evidence_must_match_the_exact_cited_observation(self):
        for citation in ("recovery:" + "d" * 32, None):
            pending = call()
            pending.arguments["recovery_observation"] = citation
            reviewed = review_arguments(pending)
            self.assertNotIn("563.125", approval_message(TOOL, reviewed)["body"])
            self.assertNotIn("563.125", approval_details(TOOL, reviewed))
        pending = call()
        pending.recovery_evidence["available"] = False
        self.assertNotIn("563.125", approval_details(TOOL, review_arguments(pending)))

    def test_malformed_or_overbudget_snapshots_do_not_crash_or_present_recovery_facts(self):
        malformed = [None, [], {"available": True}, {"available": True, "observation": "wrong"}]
        for changes in ({"not": "a list"}, [None], [{"handle": HANDLE, "field": "transform"}],
                        [{"handle": HANDLE, "field": "transform", "before": 0, "after": float("nan")}],
                        [{"handle": HANDLE, "field": "transform", "before": 0, "after": "x" * 16384}],
                        evidence()["observation"]["changes"] * 65):
            malformed.append(evidence(changes))
        for snapshot in malformed:
            with self.subTest(snapshot=str(snapshot)[:120]):
                pending = call()
                pending.recovery_evidence = snapshot
                reviewed = review_arguments(pending)
                for text in (approval_message(TOOL, reviewed)["body"], approval_details(TOOL, reviewed)):
                    self.assertNotIn("Observed scene changes", text)

    def test_summary_prioritizes_changed_values_and_details_preserve_the_whole_field(self):
        changes = [{"handle": HANDLE, "field": "camera_light",
                    "before": {"unchanged": "x" * 2000, "nested": {"lens": 34.123456789}},
                    "after": {"unchanged": "x" * 2000, "nested": {"lens": 53.987654321}}}]
        pending = call(recovery=evidence(changes))
        reviewed = review_arguments(pending)
        summary = approval_message(TOOL, reviewed)["body"]
        self.assertIn("nested.lens", summary)
        self.assertIn("34.123456789 → 53.987654321", summary)
        self.assertNotIn("x" * 100, summary)
        details = approval_details(TOOL, reviewed)
        self.assertIn("x" * 2000, details)
        self.assertIn('"lens":53.987654321', details)

    def test_maximum_field_count_is_abbreviated_only_on_the_card(self):
        changes = [{"handle": HANDLE, "field": "setting:field_" + str(index),
                    "before": index, "after": index + 0.125} for index in range(64)]
        self.assertLess(len(json.dumps(changes).encode("utf-8")), 16384)
        reviewed = review_arguments(call(recovery=evidence(changes)))
        summary = approval_message(TOOL, reviewed)["body"]
        details = approval_details(TOOL, reviewed)
        self.assertEqual(summary.count("setting:field_"), 3)
        self.assertEqual(details.count("setting:field_"), 64)
        self.assertIn("63 → 63.125", details)
        self.assertLess(len(summary), 2000)

    def test_long_values_and_control_characters_cannot_impersonate_review_labels(self):
        before = "long value " * 300
        after = "manual\nchange\r\t\x7f\u202e\u2028" + "x" * 1000
        changes = [{"handle": HANDLE, "field": "setting:output", "before": before, "after": after}]
        reviewed = review_arguments(call(recovery=evidence(changes)))
        summary = approval_message(TOOL, reviewed)["body"]
        details = approval_details(TOOL, reviewed)
        self.assertLess(len(summary), 1200)
        self.assertIn("…", summary)
        self.assertIn(before, details)
        self.assertIn("x" * 1000, details)
        for text in (summary, details):
            for unsafe in ("\r", "\t", "\x7f", "\u202e", "\u2028"):
                self.assertNotIn(unsafe, text)
            self.assertIn("manual\\nchange\\r\\t\\u007f\\u202e\\u2028", text)

    def test_localized_chrome_keeps_observed_values_and_arguments_unchanged(self):
        messages = {
            "Observed scene changes (before → after):": "観測されたシーンの変更（変更前 → 変更後）:",
            "Open Details for every observed change and its complete values.": "すべての変更と完全な値は詳細を開いて確認してください。"}
        pending = call()
        original = copy.deepcopy(pending.arguments)
        with patch.dict(ui_text._pgettext, {"iface": lambda text, *args: messages.get(text, text)}):
            reviewed = review_arguments(pending)
            for text in (approval_message(TOOL, reviewed)["body"], approval_details(TOOL, reviewed)):
                self.assertIn(messages["Observed scene changes (before → after):"], text)
                self.assertIn("[563.125,0,0]", text)
                self.assertIn(HANDLE, text)
        self.assertEqual(pending.arguments, original)


class RecoveryApprovalControllerTests(AgentRuntimeFixture, unittest.TestCase):
    def test_a_cited_observation_never_builds_a_review_from_model_text(self):
        # The host no longer mints recovery ids (request_recovery): a citation is inert and shows no facts.
        self.c.recovery_barrier.observe(snapshot())
        self.c.recovery_barrier.refused(TOOL, program(), failure())
        self.c.recovery_barrier.read("angelo_scene_object", snapshot(B, location=563.125), {"object": HANDLE})
        self.c.state["options"]["approval"] = "ASK"
        arguments = program()
        arguments["write_set"][0]["revision"] = B
        arguments["recovery_observation"] = OBSERVATION
        answers = []
        self.c.request_tool("recovery-review", TOOL, arguments, answers.append)
        self.pump()
        pending = self.c.pending[self.c.state["id"] + "/recovery-review"]
        self.assertTrue(pending.approval)
        self.assertFalse(pending.recovery_review)
        reviewed = review_arguments(pending)
        self.assertNotIn("563.125", approval_message(TOOL, reviewed)["body"])
        self.assertNotIn("Observed scene changes", approval_details(TOOL, reviewed))
        self.assertEqual(answers, [])


if __name__ == "__main__":
    unittest.main()
