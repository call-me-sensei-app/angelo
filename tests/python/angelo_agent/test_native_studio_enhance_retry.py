# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""AI Enhance's one-shot requests (enhance, the parts check, Fit to My Image) wait out a busy provider, without Blender."""

import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import studio_enhance  # noqa: E402
from angelo_agent.studio_enhance import BUSY, ProviderBusy, _busy, _run, with_capacity_retries  # noqa: E402

AT_CAPACITY = "RpcError: Provider overloaded (serverOverloaded): Selected model is at capacity. Please try a different model."


class Attempts:
    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), 0

    def __call__(self):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class RetryTests(unittest.TestCase):
    def test_a_busy_provider_is_asked_again_until_it_answers(self):
        attempts = Attempts(ProviderBusy(AT_CAPACITY), ProviderBusy(AT_CAPACITY), "answer")
        self.assertEqual(with_capacity_retries(attempts, threading.Event(), waits=(0, 0, 0), rng=lambda: 0.0), "answer")
        self.assertEqual(attempts.calls, 3)

    def test_a_provider_that_stays_busy_ends_with_the_busy_notice(self):
        attempts = Attempts(*(ProviderBusy(AT_CAPACITY) for _ in range(4)))
        with self.assertRaises(ValueError) as raised:
            with_capacity_retries(attempts, threading.Event(), waits=(0, 0, 0), rng=lambda: 0.0)
        self.assertEqual(str(raised.exception), BUSY)
        self.assertEqual(attempts.calls, 4, "the first ask and one after each wait")

    def test_stop_ends_the_wait_at_once(self):
        cancel = threading.Event()
        cancel.set()
        attempts = Attempts(ProviderBusy(AT_CAPACITY), "never asked")
        with self.assertRaises(ValueError):
            with_capacity_retries(attempts, cancel, waits=(30,))
        self.assertEqual(attempts.calls, 1)

    def test_other_failures_are_not_asked_again(self):
        attempts = Attempts(ValueError("The parts check returned no result. Nothing changed."), "never asked")
        with self.assertRaises(ValueError):
            with_capacity_retries(attempts, threading.Event(), waits=(0,), rng=lambda: 0.0)
        self.assertEqual(attempts.calls, 1)

    def test_the_waits_grow_and_stay_bounded(self):
        self.assertEqual(list(studio_enhance.BUSY_WAITS), sorted(studio_enhance.BUSY_WAITS))
        self.assertLessEqual(sum(studio_enhance.BUSY_WAITS) + 3 * studio_enhance.BUSY_JITTER, 120)


class ClassificationTests(unittest.TestCase):
    def test_busy_and_throttled_are_transient(self):
        for text in (AT_CAPACITY, "exceeded retry limit, last status: 429 Too Many Requests", "Overloaded (529)"):
            self.assertTrue(_busy(text), text)

    def test_limits_sign_in_and_unknown_are_not(self):
        for text in ("You've hit your usage limit", "Please sign in again", "invalid_grant", "something else", "", None):
            self.assertFalse(_busy(text), text)


class FakeProvider:
    def __init__(self, error=None, status="completed"):
        self.error, self.status = error, status

    def connect(self):
        return "thread"

    def run(self, identity, prompt, observation):
        if self.error:
            raise RuntimeError(self.error)
        return self.status


class RunTests(unittest.TestCase):
    def test_a_raised_busy_error_is_provider_busy(self):
        with self.assertRaises(ProviderBusy):
            _run(FakeProvider(AT_CAPACITY), SimpleNamespace(error=""), "prompt")

    def test_a_failed_turn_with_a_busy_error_is_provider_busy(self):
        with self.assertRaises(ProviderBusy):
            _run(FakeProvider(status="failed"), SimpleNamespace(error=AT_CAPACITY), "prompt")

    def test_after_a_timeout_nothing_is_asked_again(self):
        expired = threading.Event()
        expired.set()
        self.assertEqual(_run(FakeProvider(AT_CAPACITY), SimpleNamespace(error=""), "prompt", expired), "stopped")

    def test_other_errors_pass_through(self):
        with self.assertRaises(RuntimeError):
            _run(FakeProvider("Please sign in again"), SimpleNamespace(error=""), "prompt")


if __name__ == "__main__":
    unittest.main()
