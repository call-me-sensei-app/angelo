# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A sheet split again with the same layout reuses its crops, so part models already made stay reused. No Blender.

Undo after Place Character (or a file reopened without the stored split) leaves a Split Sheet node without its split;
the sheet is split again. Fresh crop assets would change every part step's input key, and the parts already paid
for would read as missing. ``flow_split_host.existing_crop`` finds the earlier crop by its lineage instead.
"""

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
try:
    import numpy  # noqa: F401  (flow_split_host's split runs on numpy)
except ImportError:  # pragma: no cover
    numpy = None

if numpy is not None:
    with mock.patch.dict(sys.modules, {"bpy": types.ModuleType("bpy")}):
        from angelo_agent import flow_split_host


def rules_digest(path, functions=None):
    """A digest of a module's code (or of some of its top-level functions) as tokens: comments, blank lines and
    docstrings don't count, so only a change to what runs changes it. The same on every Python the suite runs on."""
    import ast
    import hashlib
    import io
    import json
    import tokenize
    source = Path(path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    docstrings, kept = set(), None
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                docstrings.update(range(first.lineno, first.end_lineno + 1))
    if functions:
        kept = {line for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in functions
                for line in range(node.lineno, node.end_lineno + 1)}
    ignored = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER}
    tokens = [(t.type, t.string) for t in tokenize.generate_tokens(io.StringIO(source).readline)
              if t.type not in ignored and t.start[0] not in docstrings and (kept is None or t.start[0] in kept)]
    return hashlib.sha256(json.dumps(tokens).encode()).hexdigest()[:12]


def crop(identity, **lineage):
    base = {"source": "split", "flow_id": "f", "node": "Split", "split_key": "k1", "part": "hair", "view": "front"}
    return {"id": identity, "status": "ready", "files": ["image.png"], "lineage": dict(base, **lineage)}


@unittest.skipIf(numpy is None, "needs numpy")
class CropReuseTests(unittest.TestCase):
    def test_the_same_split_reuses_its_crop_and_nothing_else_does(self):
        library = types.SimpleNamespace(assets={"a": crop("a"), "b": crop("b", split_key="k2"), "c": crop("c", view="back"),
                                                "d": crop("d", part="body"), "e": dict(crop("e", split_key="k9"), status="failed"),
                                                "f": dict(crop("f", split_key="k8"), lineage={"source": "upload", "split_key": "k8",
                                                                                            "part": "hair", "view": "front"})})
        index = flow_split_host.crop_index(library)
        wanted = dict(crop("x")["lineage"], sheet="any sheet")
        self.assertEqual(flow_split_host.existing_crop(index, wanted), "a")
        self.assertEqual(flow_split_host.existing_crop(index, dict(wanted, split_key="k3")), None, "a new sheet or layout")
        self.assertEqual(flow_split_host.existing_crop(index, dict(wanted, view="back")), "c")
        self.assertEqual(flow_split_host.existing_crop(index, dict(wanted, flow_id="a new run's flow")), "a",
                         "a run started again from the same sheet (a new flow) reuses the crops too")
        self.assertIsNone(flow_split_host.existing_crop(index, dict(wanted, split_key="k9")), "a failed import isn't a crop")
        self.assertIsNone(flow_split_host.existing_crop(index, dict(wanted, split_key="k8")), "only a split's own crops")

    def test_the_split_rules_are_part_of_the_split_key(self):
        """Crops are reused by split key, so the key must change when the split's own rules do: everything that
        decides a crop's pixels (flow_parts, and how flow_split_host reads the sheet and writes crops), compared as
        code tokens without comments or docstrings."""
        from angelo_agent.flow_graph import SPLIT_ALGORITHM, split_key
        addon = ROOT / "scripts/addons_core/angelo_agent"
        digests = (rules_digest(addon / "flow_parts.py"), rules_digest(addon / "flow_split_host.py", {"_pixels", "_png", "_work"}))
        self.assertEqual((SPLIT_ALGORITHM, digests), (2, ("1ae362dcca6f", "d55a997fddc7")),
                         "the split's code changed: bump flow_graph.SPLIT_ALGORITHM and record the new digests")
        node = {"rows": 2, "slots": [{"id": "body", "kind": "BODY"}]}
        with mock.patch("angelo_agent.flow_graph.SPLIT_ALGORITHM", 3):
            changed = split_key("sheet", node)
        self.assertNotEqual(split_key("sheet", node), changed, "a new split algorithm is a new key")

    def test_a_new_split_key_reuses_a_crop_with_the_same_bytes(self):
        """Changed split rules make a new key; a part whose cut-out didn't change keeps its crop asset, so its 3D model
        (keyed by that asset) isn't paid for again. A part whose pixels changed is a new crop."""
        with tempfile.TemporaryDirectory() as folder:
            old, new, other = Path(folder, "old.png"), Path(folder, "new.png"), Path(folder, "other.png")
            old.write_bytes(b"same pixels")
            new.write_bytes(b"same pixels")
            other.write_bytes(b"other pixels")
            files = {"a": old, "b": other}
            library = types.SimpleNamespace(assets={"a": dict(crop("a"), lineage=dict(crop("a")["lineage"], sheet="S")),
                                                    "b": dict(crop("b", part="body"), lineage=dict(crop("b", part="body")["lineage"], sheet="S"))},
                                            checked_file=lambda asset, name: files[asset["id"]])
            index = flow_split_host.crop_index(library)
            wanted = dict(crop("x")["lineage"], sheet="S", split_key="a new key")
            self.assertEqual(flow_split_host.existing_crop(index, wanted, library, new), "a")
            self.assertIsNone(flow_split_host.existing_crop(index, dict(wanted, part="body"), library, new),
                              "another part's crop with other bytes")
            self.assertIsNone(flow_split_host.existing_crop(index, dict(wanted, sheet="T"), library, new), "another sheet")

    def test_a_crop_whose_file_is_gone_is_cut_again(self):
        def checked(asset, name):
            if asset["id"] == "gone":
                raise ValueError("missing")
        library = types.SimpleNamespace(assets={"gone": crop("gone")}, checked_file=checked)
        index = flow_split_host.crop_index(library)
        self.assertIsNone(flow_split_host.existing_crop(index, crop("x")["lineage"], library))

    def test_collect_looks_for_the_crop_before_importing_one(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/flow_split_host.py").read_text()
        collect = source.split("\ndef collect(", 1)[1].split("\ndef ", 1)[0]
        self.assertLess(collect.index('existing_crop(index, lineage, library, part[view]["path"])'),
                        collect.index("library.import_image("))
        self.assertEqual(collect.count("crop_index(library)"), 1, "one index per collect, never a scan per crop")
        self.assertIn('"split_key": job["key"]', collect)


if __name__ == "__main__":
    unittest.main()
