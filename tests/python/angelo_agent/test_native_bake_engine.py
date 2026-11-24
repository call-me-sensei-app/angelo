# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A texture set baked for a game engine: one map from engine and delivery to packing, and the step a job chains.

Contract: doc/angelo/long_jobs.rst, "Texture sets" (``for_engine``) and "Baking as a step". The packing an engine
wants depends on how the files reach it: the engine links carry a glTF file, which holds only glTF's own packing
(``export_baked.GLTF_PACKINGS``), while files dropped into the engine's own material editor use its own packing
(Unreal ORM with a DirectX normal, Unity's mask map).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import bake_maps_spec as spec  # noqa: E402
from angelo_agent import engine_rules  # noqa: E402
from angelo_agent.job_spec import JOB_TOOLS, JobRequestError, fields_by_kind, validate_job  # noqa: E402

A, B = ("scn:" + letter * 32 + ":1" for letter in "ab")
# What a glTF file carries as baked (export_baked.GLTF_PACKINGS; that module needs Blender, so its value is read
# from its source rather than imported).
GLTF_PACKINGS = frozenset({"GLTF", "ORM", "SEPARATE"})


def start(person=False, **fields):
    return validate_job("angelo_job_start", dict(kind="BAKE_MAPS", **fields), person=person)


class EnginePackingTests(unittest.TestCase):
    def test_every_engine_angelo_targets_has_a_packing(self):
        self.assertEqual(set(spec.ENGINE_PACKING), set(engine_rules.TARGETS))
        for engine, packing in spec.ENGINE_PACKING.items():
            self.assertIn(packing, spec.PACKINGS, engine)

    def test_files_use_the_engines_own_material_conventions(self):
        self.assertEqual(spec.packing_for("UNREAL"), "UNREAL")
        self.assertEqual(spec.packing_for("UNITY"), "UNITY")
        # Godot's StandardMaterial3D ORM texture and three.js read glTF's occlusion/roughness/metallic order.
        self.assertEqual(spec.packing_for("GODOT"), "GLTF")
        self.assertEqual(spec.packing_for("WEB"), "GLTF")

    def test_a_link_carries_only_what_gltf_can(self):
        for engine in engine_rules.TARGETS:
            packing = spec.packing_for(engine, link=True)
            self.assertIn(packing, GLTF_PACKINGS, engine)
            self.assertNotIn(packing, spec.DIRECTX)

    def test_the_source_list_of_gltf_packings_is_the_one_tested_here(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/export_baked.py").read_text(encoding="utf-8")
        self.assertIn('GLTF_PACKINGS = frozenset({"GLTF", "ORM", "SEPARATE"})', source)

    def test_normal_convention_follows_the_packing(self):
        self.assertEqual(spec.normal_convention("UNREAL"), "DIRECTX")
        for packing in set(spec.PACKINGS) - {"UNREAL"}:
            self.assertEqual(spec.normal_convention(packing), "OPENGL", packing)

    def test_unknown_engine_is_refused_by_name(self):
        with self.assertRaisesRegex(ValueError, "GODOT, UNITY, UNREAL, WEB"):
            spec.packing_for("CRYENGINE")


class RequestTests(unittest.TestCase):
    def test_for_engine_sets_the_packing(self):
        request = start(target=A, output="m", for_engine="UNREAL")
        self.assertEqual(request["packing"], "UNREAL")
        self.assertEqual(request["for_engine"], "UNREAL")
        self.assertEqual(start(target=A, output="m", for_engine="GODOT")["packing"], "GLTF")

    def test_for_engine_with_a_link_bakes_what_the_link_sends(self):
        request = start(target=A, output="m", for_engine="UNITY", for_link=True)
        self.assertEqual(request["packing"], "GLTF")
        self.assertIs(request["for_link"], True)

    def test_a_packing_that_agrees_is_kept_and_one_that_does_not_is_refused(self):
        self.assertEqual(start(target=A, output="m", for_engine="UNITY", packing="UNITY")["packing"], "UNITY")
        with self.assertRaisesRegex(JobRequestError, "for_engine UNREAL bakes packing UNREAL, not UNITY"):
            start(target=A, output="m", for_engine="UNREAL", packing="UNITY")

    def test_refusals_state_the_choices(self):
        with self.assertRaisesRegex(JobRequestError, "for_engine must be one of: GODOT, UNITY, UNREAL, WEB"):
            start(target=A, output="m", for_engine="unreal")
        with self.assertRaisesRegex(JobRequestError, "for_link must be true or false"):
            start(target=A, output="m", for_engine="UNITY", for_link=1)
        with self.assertRaisesRegex(JobRequestError, "for_link needs for_engine"):
            start(target=A, output="m", for_link=True)

    def test_without_for_engine_nothing_changes(self):
        request = start(target=A, output="m")
        self.assertEqual(request["packing"], "SEPARATE")
        self.assertNotIn("for_engine", request)
        self.assertNotIn("for_link", request)

    def test_the_declaration_offers_the_engines(self):
        properties = JOB_TOOLS[0]["inputSchema"]["properties"]
        self.assertEqual(properties["for_engine"]["enum"], list(engine_rules.TARGETS))
        self.assertEqual(properties["for_link"], {"type": "boolean"})
        self.assertIn("packing for_engine for_link material", fields_by_kind())


class StepTests(unittest.TestCase):
    """The request a chaining job (Make game-ready) hands the job manager for its bake step."""

    def test_the_step_is_a_request_the_job_accepts_as_it_is(self):
        args = spec.engine_step(A, "UNREAL", "Game ready/Crate maps")
        self.assertEqual(args["kind"], "BAKE_MAPS")
        request = validate_job("angelo_job_start", args)
        self.assertEqual((request["packing"], request["for_engine"], request["material"]), ("UNREAL", "UNREAL", True))
        self.assertEqual(request["target"], A)
        self.assertEqual(validate_job("angelo_job_start", dict(request)), request)   # Validated again unchanged.

    def test_the_step_keeps_high_to_low_and_the_link(self):
        args = spec.engine_step(A, "GODOT", "maps", sources=[B], link=True, resolution=1024)
        request = validate_job("angelo_job_start", args)
        self.assertEqual(request["sources"], [B])
        self.assertEqual(request["packing"], "GLTF")
        self.assertEqual(request["resolution"], [1024, 1024])

    def test_the_receipt_keys_a_step_reads_are_declared(self):
        # The keys a chaining job reads from a finished BAKE_MAPS receipt, stated once (long_jobs.rst).
        self.assertEqual(spec.STEP_RECEIPT, ("outputs", "maps", "facts", "materials"))


if __name__ == "__main__":
    unittest.main()
