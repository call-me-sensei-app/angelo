# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host-only regressions for complete, fresh native stress evidence; no Blender launch."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).with_name('stress_blender_multires_undo.py')
SPEC = importlib.util.spec_from_file_location('multires_stress_runner', SOURCE)
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def passing_report():
    return {'success': True, 'paid_requests': 0,
            'checks': {name: True for name in RUNNER.CRITICAL_MULTIRES_CHECKS}}


class MultiresStressRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='multires-runner-test-')
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)

    def invoke(self, child, **environment):
        arguments = ['--blender', sys.executable, '--output', str(self.output), '--runs', '1', '--workers', '1']
        with patch.dict(os.environ, environment), patch.object(RUNNER.subprocess, 'run', side_effect=child), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as exit_value:
                RUNNER.main(arguments)
        return exit_value.exception.code, json.loads((self.output / 'results.json').read_text())

    @staticmethod
    def child_report(value=None, code=0):
        def child(command, **kwargs):
            folder = Path(kwargs['env']['ANGELO_QA_DIR'])
            if value is not None:
                (folder / 'sculpt-engine-fixes.json').write_text(json.dumps(value))
            return SimpleNamespace(returncode=code)
        return child

    def test_factory_flags_filter_removal_and_independent_state(self):
        observed = {}

        def child(command, **kwargs):
            observed.update(command=command, environment=kwargs['env'])
            return self.child_report(passing_report())(command, **kwargs)

        code, report = self.invoke(child, ANGELO_ENGINE_SURFACE='BASE')
        self.assertEqual(code, 0)
        self.assertTrue(report['success'])
        self.assertNotIn('ANGELO_ENGINE_SURFACE', observed['environment'])
        for flag in ('--factory-startup', '--disable-autoexec', '--python-exit-code'):
            self.assertIn(flag, observed['command'])
        for key in ('BLENDER_USER_CONFIG', 'ANGELO_STATE_DIR', 'ANGELO_CONNECTIONS_DIR'):
            self.assertTrue(Path(observed['environment'][key]).is_dir())
            self.assertTrue(Path(observed['environment'][key]).is_relative_to(Path(report['invocation'])))

    def test_base_only_report_is_rejected(self):
        value = {'success': True, 'paid_requests': 0, 'checks': {'BASE_ordinary_artist_stroke_one_undo': True}}
        code, report = self.invoke(self.child_report(value))
        self.assertEqual(code, 1)
        self.assertFalse(report['success'])
        self.assertEqual(report['runs'][0]['missing_critical_checks'], list(RUNNER.CRITICAL_MULTIRES_CHECKS))

    def test_reused_output_never_consumes_or_overwrites_old_child_report(self):
        code, first = self.invoke(self.child_report(passing_report()))
        self.assertEqual(code, 0)
        old = Path(first['runs'][0]['report'])
        before = old.read_bytes()
        code, second = self.invoke(self.child_report())
        self.assertEqual(code, 1)
        self.assertFalse(second['success'])
        self.assertNotEqual(first['invocation'], second['invocation'])
        self.assertNotEqual(first['runs'][0]['report'], second['runs'][0]['report'])
        self.assertEqual(old.read_bytes(), before)
        self.assertTrue((Path(first['invocation']) / 'results.json').is_file())
        self.assertTrue((Path(second['invocation']) / 'results.json').is_file())

    def test_each_required_critical_check_must_exist_and_pass(self):
        for name in RUNNER.CRITICAL_MULTIRES_CHECKS:
            for present in (False, True):
                with self.subTest(name=name, present=present):
                    value = passing_report()
                    if present:
                        value['checks'][name] = False
                    else:
                        del value['checks'][name]
                    code, report = self.invoke(self.child_report(value))
                    self.assertEqual(code, 1)
                    self.assertFalse(report['success'])

    def test_malformed_reports_and_non_boolean_check_values_fail_closed(self):
        values = [None, [], {'success': True, 'checks': []}, {'success': True, 'checks': {}},
                  dict(passing_report(), paid_requests=1), dict(passing_report(), paid_requests=False)]
        for invalid in (1, 'true', None, []):
            value = passing_report()
            value['checks'][RUNNER.CRITICAL_MULTIRES_CHECKS[0]] = invalid
            values.append(value)
        for value in values:
            with self.subTest(value=value):
                code, report = self.invoke(self.child_report(value))
                self.assertEqual(code, 1)
                self.assertFalse(report['success'])

    def test_invalid_json_nonzero_exit_timeout_and_launch_error(self):
        def invalid_json(command, **kwargs):
            (Path(kwargs['env']['ANGELO_QA_DIR']) / 'sculpt-engine-fixes.json').write_text('{')
            return SimpleNamespace(returncode=0)

        def timeout(command, **kwargs):
            raise subprocess.TimeoutExpired(command, 180)

        def launch_error(command, **kwargs):
            raise OSError('fixture launch refused')

        for child in (invalid_json, self.child_report(passing_report(), code=1), timeout, launch_error):
            with self.subTest(child=child):
                code, report = self.invoke(child)
                self.assertEqual(code, 1)
                self.assertFalse(report['success'])

    def test_native_assertion_log_rejects_otherwise_passing_report(self):
        def child(command, **kwargs):
            kwargs['stdout'].write('Assertion failed: native regression\n')
            return self.child_report(passing_report())(command, **kwargs)

        code, report = self.invoke(child)
        self.assertEqual(code, 1)
        self.assertTrue(report['runs'][0]['native_assertions'])


if __name__ == '__main__':
    unittest.main()
