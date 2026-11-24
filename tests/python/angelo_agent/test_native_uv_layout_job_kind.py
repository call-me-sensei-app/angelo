# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The agent's UV_LAYOUT background job without Blender: its fields and bounds, the layout it asks for, its dispatch,
and how the uv_layout operation routes to it (doc/angelo/uv_layout.rst, doc/angelo/long_jobs.rst).

Coordinator ruling, 2026-11-15: the Quality packer's time does not grow with the input (4-75 s on development kits
of 4 to 160 parts), so no input bound keeps it inside a turn. Quality layouts are a job kind (add a job kind, never
a larger bound); Fast stays in the turn with a measured bound. The running app is covered by
``test_blender_uv_layout_job``.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import job_spec  # noqa: E402
from angelo_agent import uv_layout_ops  # noqa: E402
from angelo_agent.job_spec import JobRequestError, validate_job  # noqa: E402

HANDLE = "scn:" + "a" * 32 + ":1"
PARAMETERS = uv_layout_ops.PARAMETERS["properties"]


def start(**fields):
    return validate_job("angelo_job_start", {"kind": "UV_LAYOUT", **fields})


class Request(unittest.TestCase):
    def test_kind_is_offered_with_its_own_fields(self):
        self.assertIn("UV_LAYOUT", job_spec.KINDS)
        self.assertEqual(job_spec.FIELDS["UV_LAYOUT"], {"target", "layout"})
        self.assertEqual(job_spec.REQUIRED["UV_LAYOUT"], {"target"})
        schema = job_spec.JOB_TOOLS[0]["inputSchema"]["properties"]
        self.assertIn("UV_LAYOUT", schema["kind"]["enum"])
        self.assertIn("UV_LAYOUT: target* layout", job_spec.fields_by_kind())

    def test_layout_takes_exactly_the_operations_parameters(self):
        """One source: the job asks for what the uv_layout operation takes, so a call it refuses for the turn moves
        to the job unchanged."""
        layout = job_spec.JOB_TOOLS[0]["inputSchema"]["properties"]["layout"]
        self.assertEqual(layout["type"], "object")
        self.assertIs(layout["additionalProperties"], False)
        self.assertEqual(set(layout["properties"]), set(PARAMETERS))
        for name, field in layout["properties"].items():
            # Every turn carries it: the type or enum only; the rest is the operation's, the range enforced here.
            expected = {"enum": PARAMETERS[name]["enum"]} if "enum" in PARAMETERS[name] else {
                "type": PARAMETERS[name]["type"]}
            self.assertEqual(field, expected, name)

    def test_defaults_are_the_operations(self):
        request = start(target=HANDLE)
        self.assertEqual(request["target"], HANDLE)
        # Every default but mips: a margin nobody set stays missing, so the layout may lower it to leave the islands
        # room (uv_layout.rst, "Too little room"; the coordinator's decision, 2026-11-18).
        defaults = {name: field["default"] for name, field in PARAMETERS.items() if name != "mips"}
        self.assertEqual(request["layout"], defaults)
        self.assertEqual(request["layout"]["packer"], "QUALITY")
        self.assertEqual(request["minutes"], job_spec.DEFAULT_MINUTES["UV_LAYOUT"])

    def test_values_are_kept(self):
        request = start(target=HANDLE, layout={"texture_size": "4096", "use_density": True, "density": 512.0,
                                               "unwrap": "SMART", "stack": True, "group_by": "MATERIAL",
                                               "search": 0, "packer": "FAST", "mips": 2})
        self.assertEqual(uv_layout_ops.to_layout(request["layout"]),
                         {"texture_size": 4096, "density": 512.0, "mips": 2, "unwrap": "SMART", "stack": True,
                          "mirrored": False, "group_by": "MATERIAL", "search": 0, "packer": "FAST"})

    def test_a_stored_request_validates_again_unchanged(self):
        """A queued or approved job is validated again at its start: its stored layout must still be valid."""
        first = start(target=HANDLE, layout={"texture_size": "1024", "search": 0})
        again = validate_job("angelo_job_start", {"kind": "UV_LAYOUT", "target": first["target"],
                                                  "layout": first["layout"]})
        self.assertEqual(again["layout"], first["layout"])

    def test_bad_values_are_refused_naming_the_field(self):
        for field, value in (("texture_size", "1000"), ("texture_size", 2048), ("use_density", "yes"),
                             ("density", 0.5), ("density", "512"), ("density", True), ("mips", 14), ("mips", -1),
                             ("mips", 2.5), ("mips", True), ("unwrap", "CUBE"), ("stack", 1), ("mirrored", None),
                             ("group_by", "UDIM"), ("search", -1), ("search", 257), ("search", True),
                             ("packer", "SLOW")):
            with self.assertRaises(JobRequestError, msg="%s=%r" % (field, value)) as caught:
                start(target=HANDLE, layout={field: value})
            self.assertIn(field, str(caught.exception))
        with self.assertRaises(JobRequestError) as caught:
            start(target=HANDLE, layout={"rotate": False})
        self.assertIn("rotate", str(caught.exception))
        for layout in ("QUALITY", ["packer"], None):
            with self.assertRaises(JobRequestError) as caught:
                start(target=HANDLE, layout=layout)
            self.assertIn("layout", str(caught.exception))

    def test_a_schema_changed_in_place_reaches_no_check_or_default(self):
        """The range checks and the defaults read read-only copies taken as the kind registered, never the
        operation's schema, which an agent script could change in place (read_only.py; the reviews of c76f2f1 and
        a6edbf3)."""
        field = PARAMETERS["search"]
        saved = dict(field)
        try:
            field["maximum"] = 10 ** 9
            field["default"] = saved["default"] + 1
            with self.assertRaises(JobRequestError):
                start(target=HANDLE, layout={"search": saved["maximum"] + 1})
            self.assertEqual(start(target=HANDLE)["layout"]["search"], saved["default"])
            self.assertEqual(uv_layout_ops.to_layout({})["search"], saved["default"])
        finally:
            field.clear()
            field.update(saved)

    def test_target_is_required_and_other_kinds_fields_are_refused(self):
        with self.assertRaises(JobRequestError) as caught:
            validate_job("angelo_job_start", {"kind": "UV_LAYOUT", "layout": {}})
        self.assertIn("target", str(caught.exception))
        for field, value in (("output", "uvs"), ("samples", 4), ("resolution", [64, 64])):
            with self.assertRaises(JobRequestError) as caught:
                start(target=HANDLE, **{field: value})
            self.assertIn("UV_LAYOUT does not take", str(caught.exception))

    def test_layout_belongs_to_uv_layout_only(self):
        with self.assertRaises(JobRequestError) as caught:
            validate_job("angelo_job_start", {"kind": "RENDER_STILL", "output": "a.png", "layout": {}})
        self.assertIn("kinds that take them: UV_LAYOUT", str(caught.exception))


class Layout(unittest.TestCase):
    def test_the_operations_parameters_become_the_layout(self):
        layout = uv_layout_ops.to_layout({})
        self.assertEqual(layout, {"texture_size": 2048, "density": None, "unwrap": "KEEP", "stack": False,
                                  "mirrored": False, "group_by": "NONE", "search": 0, "packer": "QUALITY"})

    def test_density_only_with_use_density(self):
        self.assertIsNone(uv_layout_ops.to_layout({"density": 300.0})["density"])
        self.assertEqual(uv_layout_ops.to_layout({"use_density": True, "density": 300.0})["density"], 300.0)

    def test_mips_stop_at_the_texture_size(self):
        """A 256 px texture has 8 mip levels below its full size: a margin of more would outgrow it."""
        self.assertEqual(uv_layout_ops.to_layout({"texture_size": "256", "mips": 13})["mips"], 8)
        self.assertEqual(uv_layout_ops.to_layout({"texture_size": "8192", "mips": 13})["mips"], 13)


def spec():
    """Imported per test, so the rest of this file still runs (and fails for its own reasons) without the module."""
    return importlib.import_module("angelo_agent.job_uv_layout_spec")


class Receipt(unittest.TestCase):
    LAYOUT = {"objects": ["Crate"], "islands": 42, "stacked": 6, "tiles": [1001, 1002], "efficiency": 0.6812,
              "density_px_per_m": {"low": 980.0, "high": 1040.5, "mean": 1012.0}, "margin_px": 16, "gap_px": 32,
              "mips_kept": 4, "texture_size": 2048, "search": 16, "packer": "QUALITY", "milliseconds": 41234.5}

    def test_receipt_carries_what_the_agent_verifies_with(self):
        receipt = spec().receipt(self.LAYOUT)
        self.assertEqual(receipt["objects"], ["Crate"])
        self.assertEqual(receipt["islands"], 42)
        self.assertEqual(receipt["stacked"], 6)
        self.assertEqual(receipt["tiles"], [1001, 1002])
        self.assertEqual(receipt["efficiency"], 0.6812)
        self.assertEqual(receipt["density_px_per_m"], {"low": 980.0, "high": 1040.5, "mean": 1012.0})
        self.assertEqual(receipt["packer"], "QUALITY")
        self.assertEqual(receipt["seconds"], 41.2)
        self.assertNotIn("milliseconds", receipt)

    def test_a_long_tile_list_is_capped_and_counted(self):
        receipt = spec().receipt(dict(self.LAYOUT, tiles=list(range(1001, 1001 + 100))))
        self.assertEqual(len(receipt["tiles"]), spec().MAX_LISTED_TILES)
        self.assertEqual(receipt["tile_count"], 100)


class Routing(unittest.TestCase):
    """The uv_layout operation names the job for the calls it never runs in the turn (revision 3's job_kind)."""

    def test_quality_layouts_go_to_the_job_and_fast_ones_stay(self):
        (declaration,) = uv_layout_ops.WRAPPED_OPS
        self.assertEqual(declaration["job_kind"], "UV_LAYOUT")
        self.assertEqual(declaration["job_if"], ("packer", {"QUALITY"}))
        self.assertIn(PARAMETERS["packer"]["default"], declaration["job_if"][1],
                      "the default layout is a Quality one: it runs as the job")
        self.assertNotIn("FAST", declaration["job_if"][1])
        # One shared turn budget (wrapped_op_budget.TURN_BUDGET_S, revision 5): never an operation's own.
        self.assertNotIn("budget_s", declaration)


class Wiring(unittest.TestCase):
    def test_dispatch_builds_the_uv_layout_runner(self):
        text = ast.unparse(ast.parse((ROOT / "scripts/addons_core/angelo_agent/job_dispatch.py").read_text()))
        self.assertIn("'UV_LAYOUT'", text)
        self.assertIn("UvLayoutRunner", text)

    def test_runner_drives_the_layout_and_locks_scene_changes(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/job_uv_layout.py").read_text()
        tree = ast.parse(source)
        runner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "UvLayoutRunner")
        methods = {n.name for n in runner.body if isinstance(n, ast.FunctionDef)}
        self.assertTrue({"admit", "describe", "_start", "poll", "stop", "_finish", "locks_interface"} <= methods)
        self.assertIn('job_type = "UV_PACK"', source)   # Pack Islands' window-manager job, in _angelo_jobs' table.
        self.assertIn("uv_layout_job.Layout", source)
        native = (ROOT / "source/blender/python/intern/bpy_angelo_jobs.cc").read_text()
        self.assertIn('{"UV_PACK", WM_JOB_TYPE_UV_PACK, false}', native)


if __name__ == "__main__":
    unittest.main()
