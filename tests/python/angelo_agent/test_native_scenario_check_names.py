# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A standalone scenario's check names are its contract: none may be recorded only to fail.

The release kit validates a scenario's ``state.json`` against every name its source passes to ``check("…", …)``
(rel/release_web_106.required_checks reads them with ``ast``), and each must be recorded ``ok``. A name whose every
call records a constant false value can therefore never pass: at 93d6a3d3336, ``gui_web_preview.py`` recorded
``framing_driven`` only on its no-browser path, ``check("framing_driven", False, …)``, so the Mac web-preview step
failed on every run with a browser. Every standalone scenario of this folder
(``native_suite_other_files.EXCLUDED``) that records named checks is held to the rule here, whatever the kit
validates today; native suites are not, since they pass when every check they did record holds.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
from pathlib import Path
import unittest

import native_suite_other_files

HERE = Path(__file__).resolve().parent


def check_calls(source):
    """{name: [(line, records_only_failure)]} for every ``check("name", value, …)`` call with a literal name."""
    calls = {}
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "check" and node.args
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            value = node.args[1] if len(node.args) > 1 else None
            failing = value is None or (isinstance(value, ast.Constant) and not value.value)
            calls.setdefault(node.args[0].value, []).append((node.lineno, failing))
    return calls


def failure_only(source):
    """The names every one of whose calls records a constant false value, each with its lines."""
    return {name: [line for line, _ in sites] for name, sites in check_calls(source).items()
            if all(failing for _, failing in sites)}


def records_named_checks(source):
    """Whether the module defines its own top-level ``check`` recorder (a scenario judged by its check names)."""
    return any(isinstance(node, ast.FunctionDef) and node.name == "check" for node in ast.parse(source).body)


class ScenarioCheckNameTests(unittest.TestCase):
    def test_no_scenario_check_name_is_recorded_only_to_fail(self):
        scenarios = []
        for name in sorted(native_suite_other_files.EXCLUDED):
            path = HERE / name
            if path.suffix != ".py" or not path.is_file():
                continue
            source = path.read_text(encoding="utf-8")
            if not records_named_checks(source):
                continue
            scenarios.append(name)
            with self.subTest(scenario=name):
                self.assertEqual(failure_only(source), {}, "a check name recorded only as a failure can never pass")
        self.assertIn("gui_web_preview.py", scenarios)   # The rule must reach the scenario the release kit validates.

    def test_the_rule_catches_a_name_recorded_only_on_a_failure_path(self):
        source = '''
def check(name, value, detail=None):
    pass

def framing(browser):
    if browser is None:
        check("framing_driven", False, "no browser")
        return
    check("frame_all", browser.pressed)
    check("never_set")
    check("off", 0)
'''
        self.assertEqual(failure_only(source), {"framing_driven": [7], "never_set": [10], "off": [11]})
        self.assertTrue(records_named_checks(source))

    def test_a_name_also_recorded_with_a_real_value_passes(self):
        source = '''
def framing(browser, held):
    if browser is None:
        check("framing_driven", False, "no browser")
        return
    check("framing_driven", all(held))
    check("constant_true", True)
'''
        self.assertEqual(failure_only(source), {})
        self.assertFalse(records_named_checks(source))   # No recorder of its own: not a scenario under the rule.


if __name__ == "__main__":
    unittest.main()
