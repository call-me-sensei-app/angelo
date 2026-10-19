# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A task's usage over all its attempts keeps an unrecorded field unknown (``eval_receipts.attempts_since``).

Auto's escalation re-runs a request, so a task can have several receipts.
Summing each usage field over them must follow the ``bench_totals`` rules: a
field one attempt that reached the provider did not record is unknown
(None), never a partial sum that silently understates the cost or tokens;
an attempt that sent nothing adds nothing. Plain receipt dicts in the
telemetry store's shape; no process, account or model is involved.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import eval_receipts  # noqa: E402

CHAT = "chat-1"


def receipt(usage, turns=1):
    return {"session_id": CHAT, "status": "completed", "counts": {"provider_turn_requests": turns} if turns else {},
            "usage": usage}


def attempts(*receipts):
    marker = {"session_id": CHAT, "status": "completed"}
    controller = SimpleNamespace(telemetry=SimpleNamespace(receipts=[marker, *receipts]), state={"id": CHAT})
    return eval_receipts.attempts_since(controller, marker)


class AttemptsUsageTests(unittest.TestCase):
    def test_a_field_one_attempt_did_not_record_is_unknown_not_a_partial_sum(self):
        first = receipt({"input_tokens": 1000, "output_tokens": 50, "cost_usd": 0.02, "turns": 1})
        escalated = receipt({"input_tokens": 3000, "output_tokens": 70, "turns": 1})   # No cost_usd reported.
        result = attempts(first, escalated)
        self.assertEqual(result["attempts"], 2)
        usage = result["usage_all_attempts"]
        self.assertEqual((usage["input_tokens"], usage["output_tokens"], usage["turns"]), (4000, 120, 2))
        self.assertIsNone(usage["cost_usd"], "0.02 would understate the task: the re-run's cost is unknown")

    def test_an_attempt_without_any_usage_leaves_every_field_unknown(self):
        result = attempts(receipt({"input_tokens": 1000, "output_tokens": 50, "cost_usd": 0.02}), receipt(None))
        self.assertEqual(result["usage_all_attempts"],
                         {"input_tokens": None, "output_tokens": None, "cost_usd": None})

    def test_an_attempt_that_sent_nothing_adds_nothing(self):
        result = attempts(receipt({"input_tokens": 1000, "output_tokens": 50, "cost_usd": 0.02}), receipt(None, 0))
        self.assertEqual(result["usage_all_attempts"], {"input_tokens": 1000, "output_tokens": 50, "cost_usd": 0.02})

    def test_interrupted_turns_is_reported_only_when_nonzero(self):
        result = attempts(receipt({"input_tokens": 1000, "turns": 1}),
                          receipt({"input_tokens": 500, "turns": 1, "interrupted_turns": 1}))
        self.assertEqual(result["usage_all_attempts"], {"input_tokens": 1500, "turns": 2, "interrupted_turns": 1})

    def test_one_attempt_is_its_own_usage(self):
        usage = {"input_tokens": 1000, "output_tokens": 50, "cost_usd": 0.02}
        self.assertEqual(attempts(receipt(usage))["usage_all_attempts"], usage)
        self.assertIsNone(attempts()["usage_all_attempts"])


if __name__ == "__main__":
    unittest.main()
