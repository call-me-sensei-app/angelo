# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A job request's canonical form is a valid request that validates to itself, for every job kind.

The agent's ``angelo_job_start`` is validated three times on its way to a runner: the executor's ``validate_call``,
then ``_execute``'s on what that returned, then ``job_host._admit``'s ``validate_job`` on that. A canonical form that
renames or adds a field the agent's schema does not list (``BAKE_MAPS`` turned ``target`` into ``targets`` and added
``margin`` from 1.0.10 to the 2.0.0 candidate) is refused at the second step, so the agent could never start that
kind. Every kind is checked
here, so no kind can repeat it. Contract: doc/angelo/long_jobs.rst.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import job_spec  # noqa: E402
from angelo_agent.tool_specs import TOOL_MAP, validate_call  # noqa: E402

H = "scn:" + "a" * 32 + ":1"
H2 = "scn:" + "b" * 32 + ":2"
# The least each kind needs (job_spec.REQUIRED), and a fuller BAKE_MAPS, written as an agent writes them.
REQUESTS = {
    "RENDER_STILL": {"kind": "RENDER_STILL", "output": "renders/still.png"},
    "RENDER_ANIMATION": {"kind": "RENDER_ANIMATION", "output": "renders/shot", "frame_start": 1, "frame_end": 4},
    "BAKE_TEXTURE": {"kind": "BAKE_TEXTURE", "target": H},
    "BAKE_MAPS": {"kind": "BAKE_MAPS", "target": H, "output": "bakes/set"},
    "BAKE_MAPS high to low": {"kind": "BAKE_MAPS", "target": H, "sources": [H2], "output": "bakes/hi",
                              "packing": "ORM", "material": True, "resolution": [512, 512]},
    "BAKE_MAPS high to low for an engine": {"kind": "BAKE_MAPS", "target": H, "sources": [H2], "output": "bakes/hi",
                                            "for_engine": "UNREAL", "material": True, "resolution": [512, 512]},
    "BAKE_PHYSICS": {"kind": "BAKE_PHYSICS"},
    "BAKE_SIMULATION": {"kind": "BAKE_SIMULATION", "target": H},
    "EXPORT_USD": {"kind": "EXPORT_USD", "output": "exports/scene.usdc"},
    "EXPORT_ALEMBIC": {"kind": "EXPORT_ALEMBIC", "output": "exports/scene.abc"},
    "RETOPOLOGY": {"kind": "RETOPOLOGY", "target": H},
    "RETOPOLOGY with faces and symmetry": {"kind": "RETOPOLOGY", "target": H, "faces": 8000, "symmetry": "XZ"},
    "UV_LAYOUT": {"kind": "UV_LAYOUT", "target": H},
    "UV_LAYOUT every field": {"kind": "UV_LAYOUT", "target": H, "layout": {
        "texture_size": "4096", "use_density": True, "density": 512.0, "mips": 3, "unwrap": "SMART", "stack": True,
        "mirrored": True, "group_by": "MATERIAL", "search": 8, "packer": "FAST"}},
}


class CanonicalTests(unittest.TestCase):
    def test_every_kind_is_covered(self):
        kinds = {request["kind"] for request in REQUESTS.values()}
        # A region render is the person's own Render Result's: its request names a region of the last render.
        self.assertEqual(set(job_spec.KINDS) - kinds, {"RENDER_REGION"})

    def test_the_canonical_request_validates_to_itself(self):
        for label, arguments in REQUESTS.items():
            with self.subTest(request=label):
                canonical = job_spec.validate_job("angelo_job_start", dict(arguments))
                self.assertEqual(job_spec.validate_job("angelo_job_start", dict(canonical)), canonical)

    def test_the_agents_schema_takes_the_canonical_request(self):
        properties = set(TOOL_MAP["angelo_job_start"]["inputSchema"]["properties"])
        for label, arguments in REQUESTS.items():
            with self.subTest(request=label):
                canonical = job_spec.validate_job("angelo_job_start", dict(arguments))
                self.assertLessEqual(set(canonical), properties, "fields the agent's schema does not list")

    def test_the_executors_two_validations_agree(self):
        for label, arguments in REQUESTS.items():
            with self.subTest(request=label):
                first = validate_call("angelo_job_start", dict(arguments), host=True)
                self.assertEqual(validate_call("angelo_job_start", dict(first), host=True), first)

    def test_one_bake_maps_target_stays_target(self):
        canonical = job_spec.validate_job("angelo_job_start", dict(REQUESTS["BAKE_MAPS"]))
        self.assertEqual(canonical["target"], H)
        self.assertNotIn("targets", canonical)
        self.assertNotIn("margin", canonical)


if __name__ == "__main__":
    unittest.main()
