# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A handoff never shows a superseded post-commit revision as current (handoff_currency).

Pure tables and compositions over the real handoff, receipts and resolver: no Blender or model.
"""

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import context_ledger, handoff_currency  # noqa: E402
from angelo_agent.identifiers import IdentifierTable, expand_call  # noqa: E402
from angelo_agent.routing import handoff  # noqa: E402
from test_native_cache_diet import SUMMARY, activity  # noqa: E402
from test_native_round_savings import HANDLE, REVISION, committed  # noqa: E402
from test_native_turn_diet import NEWER, entry, observation  # noqa: E402

OTHER = HANDLE.rsplit(":", 1)[0] + ":160"
THIRD = "4" * 64


def observed_table(*entries):
    """A context's table after this message's observation (``compact_observation``), as the controller builds it."""
    table = IdentifierTable()
    context_ledger.compact_observation(observation(*entries), table)
    return table


def modified(revision, handle=HANDLE, **result):
    value = committed(**result)
    value["live_revisions"]["objects"] = [{"handle": handle, "name": "Eval Cube", "type": "MESH", "role": "MODIFIED",
                                           "object_revision": revision}]
    return value


def history(*results):
    rows = [{"role": "user", "text": "Change it"}]
    for index, result in enumerate(results):
        rows.append(activity("c%d" % index, result))
        if result.get("checks"):
            rows.append(SUMMARY)
    return rows


def entries(rows, table):
    text, _ = handoff(copy.deepcopy(rows), identifiers=table, delta=True)
    return [json.loads(line) for line in text.split(":\n", 1)[1].splitlines()]


def program(target="o1"):
    return {"mode": "APPLY", "operations": [{"kind": "transform", "target": target,
                                             "parameters": {"mode": "SET", "location": [0.0, 0.0, 1.0]}}]}


class HandoffCurrencyTests(unittest.TestCase):
    def test_an_object_changed_after_a_commit_has_no_conflicting_row_and_binds_the_observed_revision(self):
        table = observed_table(entry(HANDLE, NEWER, location=[0.25, 0.5, 0.75]))
        result = entries(history(modified(REVISION)), table)[-1]
        self.assertNotIn("live", result, "The commit's revision is older than the observation's")
        self.assertEqual(result["changed_since"], ["o1"])
        self.assertNotIn(REVISION[:8], json.dumps(result))
        bound = expand_call("angelo_scene_program", program(), table)
        self.assertEqual(bound["write_set"], [{"handle": HANDLE, "revision": NEWER}])

    def test_a_row_the_observation_shows_at_that_revision_is_left_out_as_a_repeat(self):
        result = entries(history(modified(REVISION)), observed_table(entry(HANDLE, REVISION)))[-1]
        self.assertEqual(set(result), {"role", "tool", "verified"})

    def test_a_row_newer_than_the_contexts_earlier_view_stays_when_the_observation_does_not_list_it(self):
        earlier = observed_table(entry(HANDLE, NEWER))
        table = IdentifierTable.load(earlier.to_dict("native"), "native")   # The next message's table.
        self.assertIsNone(table.observed(HANDLE, shown=True), "Shown revisions are this message's only")
        self.assertEqual(table.observed(HANDLE), NEWER)
        result = entries(history(modified(REVISION)), table)[-1]
        self.assertEqual(result["live"], [{"handle": "o1", "name": "Eval Cube", "object_revision": REVISION[:8]}])
        self.assertNotIn("changed_since", result)

    def test_a_later_commit_in_the_same_handoff_supersedes_an_earlier_row(self):
        first, second = entries(history(modified(REVISION), modified(NEWER)), IdentifierTable())[1:]
        self.assertEqual((first["changed_since"], "live" in first), (["o1"], False))
        self.assertEqual(second["live"][0]["object_revision"], NEWER[:8])

    def test_an_unchecked_commit_is_measured_the_same_way(self):
        result = entries(history(modified(REVISION, checks=[])), observed_table(entry(HANDLE, NEWER)))[-1]
        self.assertIn("unverified", result)
        self.assertEqual((result["changed_since"], "live" in result), (["o1"], False))

    def test_only_the_superseded_object_leaves_a_multi_object_receipt(self):
        result = modified(REVISION)
        result["live_revisions"]["objects"].append({"handle": OTHER, "name": "Post", "type": "MESH",
                                                    "role": "MODIFIED", "object_revision": THIRD})
        table = observed_table(entry(HANDLE, NEWER), entry(OTHER, None, name="Post"))
        shown = entries(history(result), table)[-1]
        self.assertEqual(shown["changed_since"], ["o1"])
        self.assertEqual([row["name"] for row in shown["live"]], ["Post"],
                         "An observation that shows no revision for an object never supersedes its row")

    def test_a_row_whose_revision_was_left_out_is_always_kept(self):
        rows = [{"handle": HANDLE, "name": "Eval Cube", "revision_omitted": "post-commit budget"}]
        currency = handoff_currency.Currency(observed_table(entry(HANDLE, NEWER)))
        self.assertEqual(handoff_currency.receipt_rows(rows, currency), (rows, []))

    def test_a_full_result_loses_only_its_superseded_rows_and_names_them_first(self):
        result = modified(REVISION)
        result["verification"] = {"status": "partial", "unknowns": [{"operation": 0, "reason": "unread"}]}
        result["after"] = {"objects": [{"handle": HANDLE, "object_revision": REVISION, "location": [0, 1, 0]},
                                       {"handle": OTHER, "object_revision": THIRD, "location": [5, 0, 0]}]}
        table = observed_table(entry(HANDLE, NEWER), entry(OTHER, THIRD, name="Post"))
        text = entries(history(result), table)[1]["result"]
        shown = json.loads(text)
        self.assertEqual(next(iter(shown)), "verification", "Partial status still leads")
        self.assertEqual(shown["changed_since"], ["o1"])
        self.assertEqual(shown["live_revisions"]["objects"], [])
        self.assertEqual([row["handle"] for row in shown["after"]["objects"]], ["o2"],
                         "A full result keeps a current row the observation repeats")
        self.assertNotIn("[0,1,0]", text)
        self.assertIs(handoff_currency.full_result(result), result, "No currency: unchanged")

    def test_an_observation_without_a_revision_clears_an_earlier_shown_one(self):
        table = observed_table(entry(HANDLE, NEWER))
        table.observe({"objects": [{"handle": HANDLE, "revision_omitted": {"reason": "UNSUPPORTED"}}]})
        self.assertIsNone(table.observed(HANDLE, shown=True))
        self.assertNotIn("shown", json.dumps(table.to_dict("native")))


if __name__ == "__main__":
    unittest.main()
