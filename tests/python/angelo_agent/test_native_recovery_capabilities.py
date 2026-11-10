# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Readiness declarations never turn an incomplete native recovery scaffold into admission."""
import suite_isolation  # noqa: F401

from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import native_recovery as recovery  # noqa: E402
from angelo_agent.program_jobs import ProgramError  # noqa: E402


def declarations(**changes):
    values = {"schema_version": 1, "observer_limit": 16,
              **{field: True for field in recovery.READY_FIELDS}}
    values.update(changes)
    return values


def bridge(values):
    return NS(recovery_capabilities=Mock(return_value=values),
              **{method: Mock() for method in recovery.REQUIRED_METHODS})


class NativeRecoveryCapabilitiesTests(unittest.TestCase):
    def refuse(self, native):
        with self.assertRaises(ProgramError) as caught:
            recovery.require_ready(native)
        self.assertEqual(caught.exception.code, "UNSUPPORTED")
        for method in recovery.REQUIRED_METHODS:
            value = getattr(native, method, None)
            if isinstance(value, Mock):
                value.assert_not_called()

    def test_old_native_without_capability_export_refuses_before_effects(self):
        self.refuse(NS(prepare_recovery=Mock()))

    def test_each_unready_producer_refuses_despite_lifecycle_methods(self):
        for field in recovery.READY_FIELDS:
            with self.subTest(field=field):
                self.refuse(bridge(declarations(**{field: False})))

    def test_scaffold_with_all_readiness_false_refuses(self):
        self.refuse(bridge(declarations(**dict.fromkeys(recovery.READY_FIELDS, False))))

    def test_ready_declarations_without_actual_methods_refuse(self):
        for method in recovery.REQUIRED_METHODS:
            native = bridge(declarations())
            setattr(native, method, None)
            self.refuse(native)

    def test_schema_and_resource_policy_are_explicit_not_truthy(self):
        for changes in ({"schema_version": True}, {"schema_version": 2},
                        {"observer_limit": True}, {"observer_limit": 17}):
            with self.subTest(changes=changes):
                self.refuse(bridge(declarations(**changes)))

    def test_malformed_readiness_hooks_and_dict_subclasses_are_not_executed(self):
        class TruthHook:
            def __bool__(self):
                raise AssertionError("readiness coercion ran")
        class DictHook(dict):
            def get(self, *_args):
                raise AssertionError("dict subclass hook ran")
        self.refuse(bridge(declarations(exact_preimage_ready=TruthHook())))
        self.refuse(bridge(DictHook(declarations())))
        self.refuse(bridge(None))

    def test_native_read_failure_never_grants_readiness(self):
        native = bridge(declarations())
        native.recovery_capabilities.side_effect = RuntimeError("unavailable native observation")
        self.refuse(native)

    def test_complete_interface_check_never_executes_recovery_or_seals_effects(self):
        native = bridge(declarations())
        self.assertIs(recovery.require_ready(native), native)
        for method in recovery.REQUIRED_METHODS:
            getattr(native, method).assert_not_called()


if __name__ == "__main__":
    unittest.main()
