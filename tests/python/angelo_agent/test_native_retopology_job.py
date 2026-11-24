# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The agent's RETOPOLOGY job without Blender: its fields and bounds, its dispatch, the operator call it makes and the
receipt it reads back from the result's facts (doc/angelo/retopology.rst, doc/angelo/long_jobs.rst).

The running app is covered by ``test_blender_retopology``.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import job_retopology_spec as spec  # noqa: E402
from angelo_agent import job_spec  # noqa: E402
from angelo_agent.job_spec import JobRequestError, validate_job  # noqa: E402

HANDLE = "scn:" + "a" * 32 + ":1"


def start(**fields):
    return validate_job("angelo_job_start", {"kind": "RETOPOLOGY", **fields})


class Request(unittest.TestCase):
    def test_kind_is_offered_with_its_own_fields(self):
        self.assertIn("RETOPOLOGY", job_spec.KINDS)
        self.assertEqual(job_spec.FIELDS["RETOPOLOGY"], {"target", "faces", "symmetry"})
        self.assertEqual(job_spec.REQUIRED["RETOPOLOGY"], {"target"})
        schema = job_spec.JOB_TOOLS[0]["inputSchema"]["properties"]
        self.assertIn("RETOPOLOGY", schema["kind"]["enum"])
        self.assertEqual(schema["faces"]["minimum"], spec.MIN_FACES)
        self.assertEqual(schema["faces"]["maximum"], spec.MAX_FACES)
        self.assertEqual(schema["symmetry"]["enum"], list(spec.SYMMETRY))
        self.assertIn("RETOPOLOGY: target* faces symmetry", job_spec.fields_by_kind())

    def test_defaults(self):
        request = start(target=HANDLE)
        self.assertEqual(request["target"], HANDLE)
        self.assertEqual(request["faces"], spec.DEFAULT_FACES)
        self.assertEqual(request["symmetry"], "NONE")
        self.assertEqual(request["minutes"], job_spec.DEFAULT_MINUTES["RETOPOLOGY"])

    def test_values_are_kept(self):
        for faces in (spec.MIN_FACES, 8000, spec.MAX_FACES):
            for symmetry in spec.SYMMETRY:
                request = start(target=HANDLE, faces=faces, symmetry=symmetry)
                self.assertEqual((request["faces"], request["symmetry"]), (faces, symmetry))

    def test_bad_values_are_refused_with_the_bound(self):
        for faces in (spec.MIN_FACES - 1, spec.MAX_FACES + 1, 12.5, True, "8000", None):
            with self.assertRaises(JobRequestError) as caught:
                start(target=HANDLE, faces=faces)
            self.assertIn("faces", str(caught.exception))
        for symmetry in ("x", "XX", "W", 1, None, ["X"]):
            with self.assertRaises(JobRequestError) as caught:
                start(target=HANDLE, symmetry=symmetry)
            self.assertIn("symmetry", str(caught.exception))

    def test_target_is_required_and_other_kinds_fields_are_refused(self):
        with self.assertRaises(JobRequestError) as caught:
            validate_job("angelo_job_start", {"kind": "RETOPOLOGY", "faces": 8000})
        self.assertIn("target", str(caught.exception))
        for field, value in (("output", "a.glb"), ("samples", 4), ("resolution", [64, 64])):
            with self.assertRaises(JobRequestError) as caught:
                start(target=HANDLE, **{field: value})
            self.assertIn("RETOPOLOGY does not take", str(caught.exception))

    def test_faces_and_symmetry_belong_to_retopology_only(self):
        with self.assertRaises(JobRequestError) as caught:
            validate_job("angelo_job_start", {"kind": "RENDER_STILL", "output": "a.png", "faces": 8000})
        self.assertIn("kinds that take them: RETOPOLOGY", str(caught.exception))


class OperatorCall(unittest.TestCase):
    def test_arguments_map_the_request_onto_the_operator(self):
        call = spec.operator_arguments({"faces": 8000, "symmetry": "XZ"}, tag="j7")
        self.assertEqual(call["target_faces"], 8000)
        self.assertEqual(call["use_mesh_symmetry"], False)
        self.assertEqual(call["symmetry"], {"X", "Z"})
        self.assertEqual(call["keep_original"], True)     # The agent's job never replaces the person's mesh.
        self.assertEqual(call["as_background_job"], True)
        self.assertEqual(call["tag"], "j7")
        self.assertEqual(spec.operator_arguments({"faces": 500, "symmetry": "NONE"}, tag="j1")["symmetry"], set())

    def test_every_symmetry_value_maps_to_its_axes(self):
        for value in spec.SYMMETRY:
            axes = spec.operator_arguments({"faces": 100, "symmetry": value}, tag="j1")["symmetry"]
            self.assertEqual(axes, set() if value == "NONE" else set(value))


class Receipt(unittest.TestCase):
    FACTS = {"tag": "j7", "target_faces": 8000, "faces": 7912, "quad_ratio": 1.0, "vertices": 7914,
             "irregular_vertices": 52, "poles_3": 30, "poles_5": 20, "poles_6_plus": 2, "boundary_vertices": 0,
             "hausdorff_max_of_size": 0.0031, "hausdorff_mean_of_size": 0.0004, "symmetry_axes": 1, "shells": 3,
             "shells_dropped": 1, "feature_edges": 240, "milliseconds": 1834.2}

    def test_receipt_carries_every_fact_the_agent_verifies_with(self):
        receipt = spec.receipt("Statue_retopo", self.FACTS)
        self.assertEqual(receipt["object"], "Statue_retopo")
        self.assertEqual(receipt["faces"], 7912)
        self.assertEqual(receipt["target_faces"], 8000)
        self.assertEqual(receipt["quad_ratio"], 1.0)
        self.assertEqual(receipt["poles"], {"3": 30, "5": 20, "6+": 2})
        self.assertEqual(receipt["irregular_vertices"], 52)
        self.assertEqual(receipt["deviation_of_size"], {"max": 0.0031, "mean": 0.0004})
        self.assertEqual(receipt["parts"], {"kept": 2, "left_out": 1})
        self.assertEqual(receipt["seconds"], 1.8)
        self.assertNotIn("tag", receipt)

    def test_facts_of_another_job_are_not_read(self):
        self.assertIsNone(spec.find_result([("A_retopo", dict(self.FACTS, tag="j6"))], "j7"))
        self.assertEqual(spec.find_result([("A_retopo", dict(self.FACTS, tag="j6")),
                                           ("B_retopo", self.FACTS)], "j7")[0], "B_retopo")


class Wiring(unittest.TestCase):
    def test_dispatch_builds_the_retopology_runner(self):
        tree = ast.parse((ROOT / "scripts/addons_core/angelo_agent/job_dispatch.py").read_text())
        text = ast.unparse(tree)
        self.assertIn("'RETOPOLOGY'", text)
        self.assertIn("RetopologyRunner", text)

    def test_runner_watches_the_native_job_type(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/job_retopology.py").read_text()
        self.assertIn('job_type = "RETOPOLOGY"', source)
        native = (ROOT / "source/blender/python/intern/bpy_angelo_jobs.cc").read_text()
        self.assertIn('{"RETOPOLOGY", WM_JOB_TYPE_RETOPOLOGY, false}', native)


if __name__ == "__main__":
    unittest.main()
