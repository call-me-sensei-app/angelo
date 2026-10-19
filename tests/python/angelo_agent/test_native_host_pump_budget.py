# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every host test that pumps the controller passes its own dispatch budget.

``Controller.pump_tools`` stops admitting work once 8 ms of a UI tick are
spent (``ToolDispatchBudget``). On a loaded host, a full suite beside a build,
that slice can end before the first queued call is admitted, so a test that
asserts after one pump passes or fails with the machine's load. Host tests
pass ``ToolDispatchBudget(seconds=60)`` to ``pump_tools`` and
``pump_controller_tools``: one pump keeps the tick's admission
and execution limits, and its wall-clock slice no longer decides the outcome.
A test of the time slice itself passes its own budget and clock. The product's
budget is unchanged.
"""

import ast
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from native_suite_other_files import HELPERS  # noqa: E402


# The pumps and the position of their ``budget`` parameter.
PUMPS = {"pump_tools": 3, "pump_controller_tools": 6}


def unbudgeted_pumps(path):
    """``[line]`` of pump calls in ``path`` that leave the budget to one tick's 8 ms default."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
        if (name in PUMPS and len(node.args) <= PUMPS[name]
                and not any(keyword.arg in ("budget", None) for keyword in node.keywords)):
            found.append(node.lineno)
    return found


class HostPumpBudgetTests(unittest.TestCase):
    def test_host_tests_and_their_helpers_never_depend_on_the_ticks_time_slice(self):
        paths = sorted(HERE.glob("test_native_*.py")) + sorted(HERE / name for name in HELPERS)
        found = {path.name: unbudgeted_pumps(path) for path in paths if path.is_file()}
        self.assertEqual({name: lines for name, lines in found.items() if lines}, {},
                         "pass budget=ToolDispatchBudget(seconds=60) (see this module's docstring)")

    def test_the_scan_finds_a_default_budget_and_accepts_an_explicit_one(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            sample = Path(folder) / "sample.py"
            sample.write_text("c.pump_tools(e, d)\nc.pump_tools(e, d, budget=b)\nc.pump_tools(e, d, None, b)\n"
                              "c.pump_tools(e, d, **options)\npump_controller_tools(cs, 0, e, d, a)\n"
                              "pump_controller_tools(cs, 0, e, d, a, budget=b)\n")
            self.assertEqual(unbudgeted_pumps(sample), [1, 5])


if __name__ == "__main__":
    unittest.main()
