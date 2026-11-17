# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No liveness probe in Angelo can disturb the process it asks about (audit 2026-11-14 F1, as a class).

``os.kill(pid, 0)`` asks "is it alive?" on POSIX. On Windows signal 0 is ``signal.CTRL_C_EVENT``, and CPython's
``os.kill`` then calls ``GenerateConsoleCtrlEvent(CTRL_C_EVENT, pid)``: it sends Ctrl+C to the console process group
with that id (a live Angelo, or whatever now has the id, and everything sharing its console group), or raises for an
id that names no group, which reads as "gone". It answers nothing. (``TerminateProcess`` is what CPython calls for
other signal values.) Every such probe in Angelo's own code must sit behind a Windows branch that answers first
(``os.name == "nt"`` / ``sys.platform == "win32"``,
then OpenProcess and GetExitCodeProcess or a wait), as ``engine_link_protocol.process_alive`` does; anything else asks
that function. Read from the sources (no Blender), so a new probe anywhere fails here.
"""
import suite_isolation  # noqa: F401

import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
SOURCES = (sorted((ROOT / "scripts/addons_core/angelo_agent").glob("*.py"))
           + sorted((ROOT / "scripts/modules").glob("angelo_*.py"))
           + sorted((ROOT / "scripts/startup").glob("angelo*.py"))
           + sorted((ROOT / "tests/python/angelo_agent").glob("*.py")))
WINDOWS = ('os.name == "nt"', "os.name == 'nt'", 'sys.platform == "win32"', "sys.platform == 'win32'")


def probes(path):
    """``(line, guarded)`` for each ``os.kill(<pid>, 0)`` in ``path``: guarded when the function holding it checks for
    Windows on an earlier line."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    found = []
    for function in [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for node in ast.walk(function):
            if not (isinstance(node, ast.Call) and ast.unparse(node.func) == "os.kill" and len(node.args) == 2
                    and isinstance(node.args[1], ast.Constant) and node.args[1].value == 0):
                continue
            before = "\n".join(source.splitlines()[function.lineno - 1:node.lineno - 1])
            found.append((node.lineno, any(check in before for check in WINDOWS)))
    return found


class ProbeTests(unittest.TestCase):
    def test_every_signal_zero_probe_answers_windows_first(self):
        unguarded = ["%s:%d" % (path.relative_to(ROOT), line) for path in SOURCES
                     for line, guarded in probes(path) if not guarded]
        self.assertEqual(unguarded, [], "On Windows os.kill(pid, 0) sends Ctrl+C to that console process group and "
                         "answers nothing: ask engine_link_protocol.process_alive (or answer Windows first)")

    def test_the_scan_finds_a_probe(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            probe = Path(folder) / "probe.py"
            probe.write_text("import os\ndef alive(pid):\n    os.kill(pid, 0)\n", encoding="utf-8")
            self.assertEqual(probes(probe), [(3, False)])


if __name__ == "__main__":
    unittest.main()
