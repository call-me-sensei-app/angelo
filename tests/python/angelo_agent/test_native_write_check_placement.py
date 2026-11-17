# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A write check sits with the write it guards (doc/angelo/agent_execution.rst, confinement). 206d684 put
``owned_paths.require_writable`` before writes across the add-on; where nothing is written on a legitimate path (no
output requested, no folder to keep a record in) a check placed ahead of that decision refused or failed work that
writes nothing: every texture bake without an output failed at finishing on the 2.0.0 cut's Mac GUI round
(3fc1f94), and a refresh record without a folder could not keep its marks in memory under the native guard. Host
tests have no native guard (``may_write`` is True without it), so these checks read the code, and the record runs
under a stand-in guard that refuses everything."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))


def checks():
    """(module, call, its block, its index there, its ancestors) for every ``require_writable`` call."""
    for path in sorted(ADDON.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and ast.unparse(node.func).endswith("require_writable") and node.args):
                continue
            ancestors, statement = [], node
            while statement in parents:
                statement = parents[statement]
                ancestors.append(statement)
            stmt = next(a for a in [node, *ancestors] if isinstance(a, ast.stmt))
            owner = parents[stmt]
            block = next((getattr(owner, field) for field in ("body", "orelse", "finalbody", "handlers")
                          if stmt in getattr(owner, field, ())), [])
            yield path.name, node, block, block.index(stmt) if stmt in block else -1, ancestors


class WriteChecksSitWithTheirWrites(unittest.TestCase):
    def test_no_check_runs_before_the_branch_that_decides_nothing_is_written(self):
        """A check on ``x`` followed in its block by ``if x is None`` / ``if not x`` checks a value the code then
        treats as "nothing to write here" (team_refresh_marks kept its marks in memory there)."""
        found = []
        for name, call, block, index, _ancestors in checks():
            checked = ast.unparse(call.args[0])
            for later in block[index + 1:]:
                test = ast.unparse(later.test) if isinstance(later, ast.If) else ""
                if checked + " is None" in test or test == "not " + checked:
                    found.append("%s:%d require_writable(%s)" % (name, call.lineno, checked))
        self.assertEqual(found, [])

    def test_a_runners_staging_folder_is_checked_only_where_it_has_one(self):
        """A job runner has staging only when its request names an output (job_runners: Staging made then)."""
        found = []
        for name, call, _block, _index, ancestors in checks():
            if "self.staging" not in ast.unparse(call.args[0]):
                continue
            guards = [ast.unparse(a.test) for a in ancestors if isinstance(a, ast.If)]
            if not any("self.output is not None" in g or "self.staging is not None" in g for g in guards):
                found.append("%s:%d" % (name, call.lineno))
        self.assertEqual(found, [])

    def test_without_the_native_guard_none_or_a_non_path_refuses_as_it_does_natively(self):
        """Host tests run without the native guard; may_write allowed everything there, None included, so a check on
        a value that can be None passed every host test and refused natively (both bugs above). Now only a path may."""
        from angelo_agent import owned_paths
        with patch.object(owned_paths, "_MAY_WRITE", None):
            self.assertEqual([owned_paths.may_write(value) for value in (None, 7, object(), "/x", Path("/x"))],
                             [False, False, False, True, True])
            with self.assertRaises(PermissionError) as refused:
                owned_paths.require_writable(None)
        self.assertEqual(refused.exception.refusal_code, "READ_ONLY")

    def test_a_refresh_record_without_a_folder_keeps_its_marks_in_memory_under_the_guard(self):
        from angelo_agent import owned_paths, team_refresh_marks
        value = {"v": team_refresh_marks._FORMAT, "accounts": {}}
        with patch.object(owned_paths, "_MAY_WRITE", lambda path: False):   # Writes nothing anywhere.
            record = team_refresh_marks.RefreshRecord(None, "https://console.example.test")
            record._save(value)
        self.assertEqual(record._memory, value)


if __name__ == "__main__":
    unittest.main()
