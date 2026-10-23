# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No GUI suite or QA helper reads a Git LFS file from the repository (1.0.5).

An LFS file is a short text pointer wherever its content was not fetched: a ``git archive`` snapshot (the Mac slots
test one) and a worktree that skips LFS. test_blender_studio read the brand artwork as its fixture picture and failed
"Fixture artwork did not decode" on studio-disc-full's 6ceaef365ff snapshot. A suite makes its own fixtures (painted,
written, exported); this pins that for every path joined to the repository root in a suite or helper.
"""

import ast
import fnmatch
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]


def lfs_patterns(text):
    """The ``.gitattributes`` patterns stored in Git LFS."""
    return [line.split()[0] for line in text.splitlines()
            if line.strip() and not line.startswith("#") and "filter=lfs" in line.split()[1:]]


def repo_roots(tree):
    """Names bound to the repository root: ``Path(__file__)...parents[3]`` from tests/python/angelo_agent."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Subscript):
            text = ast.unparse(node.value)
            if "__file__" in text and text.endswith(".parents[3]"):
                names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def lfs_reads(source, patterns):
    """(line, path) of every string joined to a repository-root name that names an LFS-stored file."""
    tree = ast.parse(source)
    roots, found = repo_roots(tree), []
    for node in ast.walk(tree):
        if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and isinstance(node.left, ast.Name)
                and node.left.id in roots and isinstance(node.right, ast.Constant)
                and isinstance(node.right.value, str)):
            name = Path(node.right.value).name
            if any(fnmatch.fnmatch(name, pattern) for pattern in patterns):
                found.append((node.lineno, node.right.value))
    return found


def unreachable_helpers(source, helpers):
    """(line, module) of each sibling helper a suite imports where its folder is not yet on ``sys.path``: Blender
    runs a suite with ``--python`` and does not add the suite's folder (test_blender_studio: ModuleNotFoundError
    'qa_operator', d-final line). Module-level imports need the module-level insert before them; imports inside a
    function need one anywhere at module level."""
    tree = ast.parse(source)
    def adds_folder(node):   # sys.path.insert/append(...) or sys.path[:0] = [...], beyond the add-on's own folder.
        text = ast.unparse(node)
        if not text.startswith(("sys.path.insert", "sys.path.append", "sys.path[:0]")):
            return False
        return "__file__" in text or "HERE" in text or "addons_core" not in text
    inserts = [node.lineno for node in tree.body if isinstance(node, (ast.Expr, ast.Assign)) and adds_folder(node)]
    top = {id(node) for node in tree.body}
    found = []
    for node in ast.walk(tree):
        names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                 else [node.module] if isinstance(node, ast.ImportFrom) and node.level == 0 else [])
        for name in names:
            if name and name.split(".")[0] in helpers:
                ok = any(line < node.lineno for line in inserts) if id(node) in top else bool(inserts)
                if not ok:
                    found.append((node.lineno, name))
    return found


class SuiteFixtureTests(unittest.TestCase):
    def test_every_suite_puts_its_folder_on_the_path_before_a_qa_helper(self):
        helpers = {p.stem for p in HERE.glob("qa_*.py")}   # The shared QA helpers every suite may import.
        found = {p.name: bad for p in sorted(HERE.glob("test_blender_*.py"))
                 if (bad := unreachable_helpers(p.read_text(encoding="utf-8"), helpers))}
        self.assertEqual(found, {}, "sys.path.insert(0, str(Path(__file__).resolve().parent)) before the import")

    def test_the_path_check_sees_a_helper_imported_too_early(self):
        early = "import sys\nimport qa_operator\nsys.path.insert(0, str(Path(__file__).resolve().parent))\n"
        late = "import sys\nsys.path.insert(0, str(Path(__file__).resolve().parent))\nimport qa_operator\n"
        inner = "import sys\nsys.path.insert(0, str(Path(__file__).resolve().parent))\ndef f():\n    import qa_fixtures\n"
        self.assertEqual(unreachable_helpers(early, {"qa_operator"}), [(2, "qa_operator")])
        self.assertEqual(unreachable_helpers(late, {"qa_operator"}), [])
        self.assertEqual(unreachable_helpers(inner, {"qa_fixtures"}), [])

    def test_no_suite_reads_a_git_lfs_file_from_the_repository(self):
        patterns = lfs_patterns((REPO / ".gitattributes").read_text(encoding="utf-8"))
        self.assertIn("*.png", patterns)
        files = sorted(HERE.glob("test_blender_*.py")) + sorted(HERE.glob("qa_*.py"))
        found = {p.name: reads for p in files if (reads := lfs_reads(p.read_text(encoding="utf-8"), patterns))}
        self.assertEqual(found, {}, "make the fixture in the suite (painted, written or exported), never read LFS")

    def test_the_check_sees_a_repository_picture_and_spares_other_roots(self):
        patterns = ["*.png", "*.blend"]
        repo = ("from pathlib import Path\nROOT = Path(__file__).resolve().parents[3]\n"
                "image = ROOT / 'release/branding/art.png'\n")
        temporary = "import tempfile\nfrom pathlib import Path\nROOT = Path(tempfile.mkdtemp())\nx = ROOT / 'a.blend'\n"
        text = "from pathlib import Path\nROOT = Path(__file__).resolve().parents[3]\nx = ROOT / 'scripts/a.py'\n"
        self.assertEqual(lfs_reads(repo, patterns), [(3, "release/branding/art.png")])
        self.assertEqual(lfs_reads(temporary, patterns), [])
        self.assertEqual(lfs_reads(text, patterns), [])


if __name__ == "__main__":
    unittest.main()
