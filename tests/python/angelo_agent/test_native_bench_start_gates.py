# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The GUI start-up gate is outside the trial digest, and it only waits (maintainer ruling 2026-10-30).

``bench_integrity.test_code`` is what a trial was sent and graded with. ``qa_boot`` (``START_GATES``) decides only
WHEN a GUI suite or a trial driver starts acting, once Angelo's start-up has settled, so it is left out: 1.0.7's gate
fix ea689431e37 changed only qa_boot.py, and the release's tests-only rule could not take it. These tests pin what
that rests on, for every start-up gate:
- it imports nothing of the harness (no prompt, send, scenario or grading module) and writes no file;
- every harness module that imports it uses only its waits (``WAITS``);
- no grading module reaches it;
- and the digest follows every other harness module but not it.
"""

import ast
import functools
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import bench_integrity as integrity  # noqa: E402

ROOT = HERE.parents[2]
# What a harness module may use of a start-up gate: whether the start-up has settled, what it still waits for, and
# holding a suite's own timer until it has (with its bound).
WAITS = {"started", "waiting_on", "gated", "GATE_SECONDS"}
# What a gate may import: Python, Blender, the add-on it inspects, and (of this folder) only HELPERS.
GATE_IMPORTS = {"sys", "time", "functools", "bpy", "addon_utils", "angelo_agent", "angelo_default_layout"}
# Modules of this folder a gate may use: the runner's timeout scale (qa_waits.timeout_scale). They stay in the digest
# and import only the standard library, so through them a gate still reaches nothing a trial sends or grades.
HELPERS = {"qa_waits"}
STDLIB = set(sys.stdlib_module_names)


@functools.lru_cache(maxsize=1)
def sources():
    return tuple(integrity.test_code_sources(ROOT))


def tree(name):
    return ast.parse((HERE / name).read_text(encoding="utf-8"))


def imported(module_tree):
    names = set()
    for node in ast.walk(module_tree):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names.add(node.module or "")
    return names


def gate_uses(module_tree, gate):
    """``(names used on the gate module, whether it was imported other than as a module)``."""
    aliases, loose = set(), False
    for node in ast.walk(module_tree):
        if isinstance(node, ast.Import):
            aliases |= {alias.asname or alias.name for alias in node.names if alias.name == gate}
        elif isinstance(node, ast.ImportFrom) and node.module == gate:
            loose = True
    used = {node.attr for node in ast.walk(module_tree)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in aliases}
    return used, loose


class StartGateTests(unittest.TestCase):
    def test_each_gate_imports_no_harness_module_and_writes_nothing(self):
        local = {path.stem for path in HERE.glob("*.py")}
        for name in integrity.START_GATES:
            with self.subTest(gate=name):
                gate = tree(name)
                roots = {module.split(".")[0] for module in imported(gate)}
                self.assertEqual(roots & local - HELPERS, set(), "a gate that imports the harness is the harness")
                self.assertLessEqual(roots, GATE_IMPORTS | HELPERS)
                for helper in roots & HELPERS:
                    self.assertLessEqual({module.split(".")[0] for module in imported(tree(helper + ".py"))}, STDLIB)
                    self.assertIn(HERE / (helper + ".py"), sources(), "a gate's helper stays in the digest")
                calls = {ast.unparse(node.func) for node in ast.walk(gate) if isinstance(node, ast.Call)}
                self.assertFalse({"open", "print"} & calls or any(call.endswith(("write_text", "write_bytes", ".write"))
                                                                   for call in calls), calls)

    def test_the_harness_uses_only_the_gates_waits(self):
        for path in sources():
            if path.parent != HERE:
                continue
            module_tree = ast.parse(path.read_text(encoding="utf-8"))
            for name in integrity.START_GATES:
                used, loose = gate_uses(module_tree, Path(name).stem)
                with self.subTest(module=path.name, gate=name):
                    self.assertFalse(loose, "import the gate as a module, so its uses are visible")
                    self.assertLessEqual(used, WAITS)

    def test_the_check_sees_a_use_beyond_the_waits(self):
        used, loose = gate_uses(ast.parse("import qa_boot\nqa_boot.started()\nqa_boot.PROMPT\n"), "qa_boot")
        self.assertEqual((used - WAITS, loose), ({"PROMPT"}, False))
        self.assertEqual(gate_uses(ast.parse("from qa_boot import started\n"), "qa_boot"), (set(), True))

    def test_no_grading_module_reaches_a_gate(self):
        graders = [path for path in sources() if path.parent != HERE]
        self.assertTrue(graders)
        for path in graders:
            self.assertFalse({Path(name).stem for name in integrity.START_GATES}
                             & imported(ast.parse(path.read_text(encoding="utf-8"))), path.name)

    def test_the_digest_follows_the_harness_but_not_a_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for path in list(sources()) + [HERE / name for name in integrity.START_GATES]:
                copy = root / path.relative_to(ROOT)
                copy.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, copy)
            folder_tests = root / "tests/python/angelo_agent"
            before = integrity.test_code.__wrapped__(root)["sha256"]
            for name in integrity.START_GATES:
                (folder_tests / name).write_text((folder_tests / name).read_text() + "\n# changed\n")
            self.assertEqual(integrity.test_code.__wrapped__(root)["sha256"], before)
            harness = folder_tests / "bench_integrity.py"
            harness.write_text(harness.read_text() + "\n# changed\n")
            self.assertNotEqual(integrity.test_code.__wrapped__(root)["sha256"], before)
        self.assertNotIn(HERE / "qa_boot.py", sources())


if __name__ == "__main__":
    unittest.main()
