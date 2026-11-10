# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual ProcessOwner observation control flow with modeled rows; no OS reads/signals/browser."""
import suite_isolation  # noqa: F401

from contextlib import ExitStack
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent import fab_browser_ownership as owned

PROGRAM = '/Applications/OwnershipFixture.app/Contents/MacOS/Chrome'
MARKER = 'd' * 32


def row(*, start='original birth', program=PROGRAM):
    return dict(pid=702, parent=701, group=701, start=start, program=program, zombie=False)


class Clock:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


class OwnershipObservationRaceTests(unittest.TestCase):
    def owner(self, *, pinned=True):
        owner = owned.ProcessOwner(701, MARKER, PROGRAM, '/private/fixture-owned-profile')
        if pinned:
            owner.known[702] = owned.identity(row())
        return owner

    def environment(self, stack, snapshots, markers=(True, False)):
        clock = Clock()
        stack.enter_context(patch.object(owned, 'time', clock))
        snapshot = stack.enter_context(patch.object(owned, 'snapshot', side_effect=snapshots))
        if callable(markers):
            marker = stack.enter_context(patch.object(owned, 'marker_and_profile', side_effect=markers))
        else:
            marker = stack.enter_context(patch.object(owned, 'marker_and_profile', return_value=markers))
        kill = stack.enter_context(patch.object(owned.os, 'kill'))
        return clock, snapshot, marker, kill

    def test_between_snapshot_drift_then_two_fresh_empty_inspections_drains_without_signal(self):
        # Old-source witness: an ordinary completion assertion fails, not an import/API error.
        changed = row(program='(exiting helper)')
        owner = self.owner()
        original_pins = dict(owner.known)
        snapshots = [{702: row()}, {702: changed}, {}, {}]
        completed, error_code = False, None
        with ExitStack() as stack:
            clock, snapshot, marker, kill = self.environment(stack, snapshots)
            try:
                owner.drain()
                completed = True
            except owned.OwnershipUnavailable as error:
                error_code = error.code
            kill.assert_not_called()
        self.assertTrue(completed, 'coherent fresh empty proof refused: ' + str(error_code))
        self.assertEqual(snapshot.call_count, 4)
        self.assertEqual(marker.call_count, 1)
        self.assertEqual(owner.known, original_pins)
        self.assertEqual(clock.waits, [.05])
        self.assertTrue(all(call.args == (8.0,) for call in snapshot.call_args_list))

    def test_untracked_drift_is_not_adopted_or_signaled_before_fresh_empty_proof(self):
        owner = self.owner(pinned=False)
        with ExitStack() as stack:
            clock, snapshot, marker, kill = self.environment(
                stack, [{702: row()}, {702: row(start='different observed birth')}, {}, {}])
            owner.drain()
            kill.assert_not_called()
        self.assertEqual(owner.known, {})
        self.assertEqual(snapshot.call_count, 4)
        self.assertEqual(marker.call_count, 1)
        self.assertEqual(clock.waits, [.05])

    def test_retry_returns_owned_rows_only_after_a_new_full_coherent_marker_inspection(self):
        owner = self.owner(pinned=False)
        observed_pins = []
        def marker(*args):
            observed_pins.append(dict(owner.known))
            return True, False
        with ExitStack() as stack:
            clock, snapshot, markers, kill = self.environment(
                stack, [{702: row()}, {702: row(start='changed birth')},
                        {702: row()}, {702: row()}], marker)
            result = owner._observe(8.0)
            kill.assert_not_called()
        self.assertEqual(result, [row()])
        self.assertEqual(observed_pins, [{}, {}])
        self.assertEqual(owner.known, {702: owned.identity(row())})
        self.assertEqual(markers.call_count, 2)
        self.assertEqual(snapshot.call_count, 4)
        self.assertEqual(clock.waits, [.05])

    def test_persistent_during_inspection_drift_refuses_with_original_deadline_and_pins(self):
        owner = self.owner()
        original_pins = dict(owner.known)
        calls = []
        def snapshot(deadline):
            calls.append(deadline)
            return {702: row() if len(calls) % 2 else row(program='changed executable')}
        with ExitStack() as stack:
            clock, snapshots, marker, kill = self.environment(stack, snapshot)
            with self.assertRaises(owned.OwnershipUnavailable) as failure:
                owner.drain()
            kill.assert_not_called()
        self.assertEqual(failure.exception.code, 'IDENTITY_CHANGED')
        self.assertGreater(len(clock.waits), 1)
        self.assertLessEqual(clock.now, 8.0)
        self.assertEqual(set(calls), {8.0})
        self.assertEqual(owner.known, original_pins)

    def test_known_pid_reuse_remains_immediate_hard_refusal_without_marker_or_signal(self):
        owner = self.owner()
        original_pins = dict(owner.known)
        with ExitStack() as stack:
            clock, snapshot, marker, kill = self.environment(stack, [{702: row(start='reused PID birth')}])
            with self.assertRaises(owned.OwnershipUnavailable) as failure:
                owner.drain()
            marker.assert_not_called()
            kill.assert_not_called()
        self.assertEqual(failure.exception.code, 'TRACKED_IDENTITY_CHANGED')
        self.assertEqual(snapshot.call_count, 1)
        self.assertEqual(clock.waits, [])
        self.assertEqual(owner.known, original_pins)

    def test_during_inspection_birth_change_becomes_hard_refusal_if_reused_pid_stays_live(self):
        owner = self.owner()
        replacement = row(start='reused PID birth')
        original_pins = dict(owner.known)
        with ExitStack() as stack:
            clock, snapshot, marker, kill = self.environment(
                stack, [{702: row()}, {702: replacement}, {702: replacement}])
            with self.assertRaises(owned.OwnershipUnavailable) as failure:
                owner.drain()
            kill.assert_not_called()
        self.assertEqual(failure.exception.code, 'TRACKED_IDENTITY_CHANGED')
        self.assertEqual(snapshot.call_count, 3)
        self.assertEqual(marker.call_count, 1)
        self.assertEqual(clock.waits, [.05])
        self.assertEqual(owner.known, original_pins)

    def test_single_empty_retry_does_not_override_a_second_unreadable_full_inspection(self):
        owner = self.owner()
        original_pins = dict(owner.known)
        calls = []
        marker_calls = []
        def snapshot(deadline):
            calls.append(deadline)
            if len(calls) == 2:
                return {702: row(program='changed executable')}
            if len(calls) == 3:
                return {}
            return {702: row()}
        def marker(*args):
            marker_calls.append(args)
            return (True, False) if len(marker_calls) == 1 else (None, False)
        with ExitStack() as stack:
            clock, snapshots, markers, kill = self.environment(stack, snapshot, marker)
            with self.assertRaises(owned.OwnershipUnavailable) as failure:
                owner.drain()
            kill.assert_not_called()
        self.assertEqual(failure.exception.code, 'HELPER_UNREADABLE')
        self.assertLessEqual(clock.now, 8.0)
        self.assertEqual(set(calls), {8.0})
        self.assertEqual(owner.known, original_pins)
        self.assertEqual(owner.last_refusal['reason'], 'HELPER_UNREADABLE')

    def test_inspection_timeout_after_drift_still_refuses_without_reclassifying_unreadable_as_gone(self):
        owner = self.owner()
        original_pins = dict(owner.known)
        with ExitStack() as stack:
            clock, snapshot, marker, kill = self.environment(
                stack, [{702: row()}, {702: row(program='changed executable')},
                        owned.OwnershipUnavailable('Process inspection timeout')])
            with self.assertRaises(owned.OwnershipUnavailable) as failure:
                owner.drain()
            kill.assert_not_called()
        self.assertEqual(failure.exception.code, 'INSPECTION_TIMEOUT')
        self.assertEqual(snapshot.call_count, 3)
        self.assertEqual(clock.waits, [.05])
        self.assertEqual(owner.known, original_pins)

    def test_direct_inspect_and_signal_identity_guards_remain_strict(self):
        for changed in (row(program='changed executable'), row(start='reused PID birth')):
            with self.subTest(changed=changed):
                owner = self.owner()
                with ExitStack() as stack:
                    clock, snapshot, marker, kill = self.environment(stack, [{702: row()}, {702: changed}])
                    with self.assertRaises(owned.OwnershipUnavailable) as failure:
                        owner.inspect(8.0)
                    kill.assert_not_called()
                self.assertEqual(failure.exception.code, 'IDENTITY_CHANGED')
                self.assertEqual(clock.waits, [])
                with ExitStack() as stack:
                    clock, snapshot, marker, kill = self.environment(stack, [{702: changed}])
                    with self.assertRaises(owned.OwnershipUnavailable) as failure:
                        owner._signal(row(), 15, 8.0)
                    marker.assert_not_called()
                    kill.assert_not_called()
                self.assertEqual(failure.exception.code, 'PID_REUSED')

    def test_signal_marker_and_final_identity_are_still_required(self):
        for marked, final, reason in (
                (False, row(), 'PID_UNPROVED'),
                (None, row(), 'PID_UNPROVED'),
                (True, row(start='reused after marker'), 'PID_UNPROVED')):
            with self.subTest(marked=marked, final=final):
                with ExitStack() as stack:
                    clock, snapshot, marker, kill = self.environment(
                        stack, [{702: row()}, {702: final}], (marked, False))
                    with self.assertRaises(owned.OwnershipUnavailable) as failure:
                        self.owner()._signal(row(), 15, 8.0)
                    kill.assert_not_called()
                self.assertEqual(failure.exception.code, reason)


if __name__ == '__main__':
    unittest.main()
