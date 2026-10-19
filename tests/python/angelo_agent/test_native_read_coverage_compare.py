# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The coverage comparison names every lost answer and fails on any non-catalogued loss."""

import unittest

import read_coverage_compare as compare


def record(entries, reads=10):
    return {"entries": entries, "reads": reads, "seconds": 1.0}


class CompareTests(unittest.TestCase):
    def test_catalogued_losses_are_named_and_allowed(self):
        result = compare.compare(record({"scene@0|bound_box": "V:aa", "scene@0": "K:STRUCT"}),
                                 record({"scene@0|bound_box": "W:NOT_EVALUATED", "scene@0": "K:STRUCT"}))
        self.assertEqual(result["catalogued"], [{"read": "scene@0|bound_box", "reason": "NOT_EVALUATED"}])
        self.assertEqual(result["regression"], [])

    def test_any_other_loss_is_a_regression(self):
        result = compare.compare(
            record({"a@0|x": "V:aa", "a@0|y": "V:bb", "b@0": "K:STRUCT", "c@0": "V:cc"}),
            record({"a@0|x": "W:NATIVE_READER_REQUIRED", "a@0|y": "U:RuntimeError", "b@0": "M:DENIED"}))
        reasons = {entry["read"]: entry["reason"] for entry in result["regression"]}
        self.assertEqual(reasons, {"a@0|x": "NATIVE_READER_REQUIRED", "a@0|y": "UNREADABLE:RuntimeError",
                                   "b@0": "DENIED", "c@0": "NOT_VISITED"})

    def test_changed_values_and_gains_are_reported_for_review(self):
        result = compare.compare(record({"a@0|x": "V:aa", "a@0|y": "W:LOADS_FILE"}),
                                 record({"a@0|x": "V:bb", "a@0|y": "V:cc"}))
        self.assertEqual(result["changed"], [{"read": "a@0|x"}])
        self.assertEqual(result["gained"], [{"read": "a@0|y", "baseline": "W:LOADS_FILE"}])
        self.assertEqual(result["summary"]["regression"], 0)

    def test_paths_under_a_withheld_ancestor_inherit_its_reason(self):
        baseline = record({"scene.view_layers@0|[0].layer_collection": "K:STRUCT",
                           "scene.view_layers[0].layer_collection@0|name": "V:aa",
                           "scene.view_layers[0].layer_collection.children@0": "K:COLLECTION",
                           "scene.objects@0|[1]": "V:bb", "scene.objects[1]@0|name": "V:cc"})
        candidate = record({"scene.view_layers@0|[0].layer_collection": "W:VIEW_LAYER_OUT_OF_SYNC",
                            "scene.objects@0|[1]": "W:DENIED"})
        result = compare.compare(baseline, candidate)
        self.assertEqual(result["catalogued"], [
            {"read": "scene.view_layers@0|[0].layer_collection", "reason": "VIEW_LAYER_OUT_OF_SYNC"},
            {"read": "scene.view_layers[0].layer_collection.children@0", "reason": "VIEW_LAYER_OUT_OF_SYNC",
             "under": "scene.view_layers[0].layer_collection"},
            {"read": "scene.view_layers[0].layer_collection@0|name", "reason": "VIEW_LAYER_OUT_OF_SYNC",
             "under": "scene.view_layers[0].layer_collection"}])
        self.assertEqual(result["regression"], [
            {"read": "scene.objects@0|[1]", "reason": "DENIED"},
            {"read": "scene.objects[1]@0|name", "reason": "DENIED", "under": "scene.objects[1]"}])

    def test_an_incomplete_sweep_is_not_conclusive(self):
        import json
        import tempfile
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            base, cand = Path(folder, "base.json"), Path(folder, "cand.json")
            base.write_text(json.dumps(dict(record({"a@0": "V:aa"}), complete=False)))
            cand.write_text(json.dumps(dict(record({"a@0": "V:aa"}), complete=True)))
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()) as errors:
                self.assertEqual(compare.main([str(base), str(cand)]), 2)
            self.assertIn("INCOMPLETE", errors.getvalue())
            base.write_text(json.dumps(dict(record({"a@0": "V:aa"}), complete=True)))
            with redirect_stdout(StringIO()):
                self.assertEqual(compare.main([str(base), str(cand)]), 0)


if __name__ == "__main__":
    unittest.main()
