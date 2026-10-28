# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``test_blender_worker_python`` reads a worker's saved part only once the worker owes it no save.

A hosting worker saves its part on its main thread once idle (``worker_chat_part.save_when_idle``), after the edit's
answer reached Angelo; a lane worker saves inside the edit's reply. The suite read the part file right after an
answer, so on a loaded machine it read the part's previous version: Mac 2, 78e17c6e1a1, @hosted
``a_hidden_load_is_contained`` compared the part after a hidden load with an empty "before" read taken ahead of the
seed's save (the product was right: the part held the seed, as last saved). The same read-after-answer shape is in
the patched rerun's check, which failed on earlier Mac 2 runs. Pinned here, without Blender, on the suite's own code:
its state probe waits until no save is due and its part reads go through that probe; every other read of a file is
of a file no worker saves. The in-Blender proof is the suite itself, with ``ANGELO_QA_LATE_SAVE=1`` (each save forced
as late as it may land).
"""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

SUITE = Path(__file__).resolve().parent / "test_blender_worker_python.py"
TREE = ast.parse(SUITE.read_text(encoding="utf-8"))
FUNCTIONS = {node.name: node for node in TREE.body if isinstance(node, ast.FunctionDef)}
COMPOUND = (ast.For, ast.While, ast.If, ast.With, ast.Try, ast.FunctionDef)
SAVE_SECONDS = next(ast.literal_eval(node.value) for node in TREE.body if isinstance(node, ast.Assign)
                    and [ast.unparse(target) for target in node.targets] == ["SAVE_SECONDS"])


def suite_helpers(answers, files):
    """The suite's ``probe``, ``part_objects`` and ``file_objects``, run on a fake worker: ``answers`` its QA state
    answers in order (the last repeats), ``files`` what each read of a path finds (it changes when the fake saves)."""
    asked, reads, clock = [], [], [0.0]

    class Load:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            reads.append(self.path)
            return SimpleNamespace(objects=list(files[self.path])), None

        def __exit__(self, *_):
            return False

    def ask(op):
        asked.append(op)
        answer = answers[min(len(asked) - 1, len(answers) - 1)]
        if not answer.get("save_pending"):
            files.update(answer.get("saved", {}))
        return {"value": answer}

    def monotonic():
        clock[0] += 1.0
        return clock[0]
    chat = SimpleNamespace(executor=SimpleNamespace(ask=ask), part="part.blend", pump=lambda: None)
    namespace = {"time": SimpleNamespace(monotonic=monotonic), "SAVE_SECONDS": SAVE_SECONDS,
                 "bpy": SimpleNamespace(data=SimpleNamespace(libraries=SimpleNamespace(load=Load)))}
    module = ast.Module(body=[FUNCTIONS[name] for name in ("probe", "part_objects", "file_objects")], type_ignores=[])
    exec(compile(module, str(SUITE), "exec"), namespace)   # noqa: S102 - the suite's own helpers, as written.
    return namespace, chat, asked, reads


class SavedReads(unittest.TestCase):
    def test_a_read_waits_until_the_worker_saved_its_part(self):
        files = {"part.blend": []}   # The part before its seed's save (a new part starts empty).
        pending = {"save_pending": True, "objects": ["QA Base"]}
        saved = {"save_pending": False, "objects": ["QA Base"], "saved": {"part.blend": ["QA Base"]}}
        helpers, chat, asked, _reads = suite_helpers([pending, pending, saved], files)
        self.assertEqual(helpers["part_objects"](chat), ["QA Base"])
        self.assertEqual(asked, ["qa_state"] * 3)

    def test_a_lane_worker_is_read_at_once(self):
        files = {"part.blend": ["QA Base"]}
        helpers, chat, asked, _reads = suite_helpers([{"save_pending": False}], files)
        self.assertEqual(helpers["part_objects"](chat), ["QA Base"])
        self.assertEqual(asked, ["qa_state"])

    def test_a_save_that_never_lands_fails_the_case(self):
        helpers, chat, _asked, reads = suite_helpers([{"save_pending": True}], {"part.blend": []})
        with self.assertRaisesRegex(AssertionError, "did not save its part"):
            helpers["part_objects"](chat)
        self.assertEqual(reads, [])   # Never read as if it were saved.

    def test_undo_and_redo_are_asked_once(self):
        helpers, chat, asked, _reads = suite_helpers([{"save_pending": True, "outcome": ["FINISHED"]}], {})
        self.assertEqual(helpers["probe"](chat, "qa_undo")["outcome"], ["FINISHED"])
        self.assertEqual(asked, ["qa_undo"])

    def test_every_read_of_a_part_goes_through_the_probe(self):
        """A file read straight (``file_objects``) only of a file no worker saves, or of a part right after the
        statement that asked its worker for its state (``probe``, ``qa_state``): nothing the worker does between."""
        loads = [node for node in ast.walk(TREE) if isinstance(node, ast.Attribute) and node.attr == "load"
                 and isinstance(node.value, ast.Attribute) and node.value.attr == "libraries"]
        self.assertEqual(len(loads), 1, "one file read in the suite: file_objects")
        reads, unprobed, by_name = [], [], []
        for function in FUNCTIONS.values():
            for block in (node for node in ast.walk(function) if isinstance(getattr(node, "body", None), list)):
                for field in ("body", "orelse", "finalbody"):
                    statements = getattr(block, field, None) or []
                    for index, statement in enumerate(statements):
                        if isinstance(statement, COMPOUND) or not any(map(_reads_a_part, ast.walk(statement))):
                            continue   # A compound statement's own blocks are scanned as blocks.
                        reads.append(function.name)
                        before = statements[index - 1] if index else None
                        if before is None or not any(map(_asks_state, ast.walk(before))):
                            unprobed.append((function.name, statement.lineno))
            assigned = {ast.unparse(target): ast.unparse(node.value) for node in ast.walk(function)
                        if isinstance(node, ast.Assign) for target in node.targets}
            by_name += [(function.name, node.args[0].id) for node in ast.walk(function) if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name) and node.func.id == "file_objects"
                        and isinstance(node.args[0], ast.Name) and ".part" in assigned.get(node.args[0].id, "")]
        self.assertIn("part_objects", reads, "the scan found the suite's part reads")
        self.assertEqual(unprobed, [])
        self.assertEqual(by_name, [], "a part read through a name, past the probe rule")

    def test_the_hidden_load_case_needs_the_seed_in_the_saved_part(self):
        """Its check can tell the loaded (empty) file from the part only when the part holds the seed."""
        calls = sorted((node for node in ast.walk(FUNCTIONS["hidden_load_contained"]) if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)), key=lambda node: (node.lineno, node.col_offset))
        names = [(node.func.id, node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else None)
                 for node in calls]
        guard = names.index(("check", "the_seed_is_in_the_saved_part"))
        self.assertLess(guard, [name for name, _ in names].index("run_reviewed"))
        self.assertIn("QA Base", ast.unparse(calls[guard].args[1]))


def _reads_a_part(node):
    """``file_objects(<chat>.part)``: a worker's part file read straight."""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "file_objects"
            and isinstance(node.args[0], ast.Attribute) and node.args[0].attr == "part")


def _asks_state(node):
    """``probe(chat)`` asking the worker's state (``qa_state``, the default), which waits until no save is due."""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "probe"
            and all(isinstance(argument, ast.Constant) and argument.value == "qa_state"
                    for argument in node.args[1:]))


if __name__ == "__main__":
    unittest.main()
