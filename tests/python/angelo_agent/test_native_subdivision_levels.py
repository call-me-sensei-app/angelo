# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Subdivision render levels and adaptive settings: schema, derived checks, the render ceiling and the revision.

Dependency-free. The live modifier, what a stage evaluates, Undo, Redo and
rollback are covered by test_blender_subdivision_levels.py in the compiled app.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent.action_catalog import estimated_work, validate_operation  # noqa: E402
from angelo_agent.action_schema_types import (MAX_EVALUATED_VERTICES, MAX_RENDER_VERTICES,  # noqa: E402
                                              MAX_SUBDIVISION_LEVELS, MAX_SUBDIVISION_RENDER_LEVELS, adaptive_values)
from angelo_agent.derived_checks import derive_checks  # noqa: E402
from angelo_agent.scene_read_fields import MODIFIER_FIELDS, REVISION_MODIFIERS  # noqa: E402
from angelo_agent.validation_spec import validate_check  # noqa: E402
from test_native_survey_fixes import CONTEXT, _budget, _mesh  # noqa: E402

OBJ = "scn:" + "a" * 32 + ":7"
ADAPTIVE = ("use_adaptive_subdivision", "adaptive_space", "adaptive_pixel_size", "adaptive_object_edge_length")


def subsurf(kind="modifier_add", **settings):
    name = {"name": "Subdivision"} if kind == "modifier_add" else {"modifier": "Subdivision"}
    return {"kind": kind, "target": OBJ, "parameters": dict(name, type="SUBSURF", settings=settings)}


class SchemaTests(unittest.TestCase):
    def test_render_levels_reach_six_and_viewport_levels_three(self):
        self.assertEqual((MAX_SUBDIVISION_LEVELS, MAX_SUBDIVISION_RENDER_LEVELS), (3, 6))
        settings = validate_operation(subsurf(levels=2, render_levels=6))["parameters"]["settings"]
        self.assertEqual((settings["levels"], settings["render_levels"]), (2, 6))
        for refused in ({"levels": 4}, {"render_levels": 7}, {"render_levels": -1}, {"render_levels": 2.5}):
            with self.subTest(settings=refused), self.assertRaises(ValueError):
                validate_operation(subsurf(**refused))

    def test_adaptive_is_one_closed_field_and_null_keeps_the_modifiers_value(self):
        # Left out or null: kept, one canonical form (action_schemas.SETTINGS_NOTE, action_schema_types.without_nulls).
        for written in ({}, {"adaptive": None}):
            settings = validate_operation(subsurf(**written))["parameters"]["settings"]
            self.assertNotIn("adaptive", settings, "No field when a plan does not dice adaptively")
            self.assertEqual(adaptive_values(settings), {})
        for adaptive in ({"enabled": True}, {"enabled": True, "space": None, "pixel_size": None}):
            settings = validate_operation(subsurf(adaptive=adaptive))["parameters"]["settings"]
            self.assertEqual(settings["adaptive"], {"enabled": True})
            self.assertEqual(adaptive_values(settings), {"use_adaptive_subdivision": True}, "A null member keeps its value")
        settings = validate_operation(subsurf(adaptive={"enabled": True, "space": "OBJECT", "pixel_size": 2,
                                                        "object_edge_length": 0.01}))["parameters"]["settings"]
        self.assertEqual(adaptive_values(settings), dict(zip(ADAPTIVE, (True, "OBJECT", 2.0, 0.01))))
        # Blender's own interface ranges: no dicing finer than half a pixel or a millimetre.
        for refused in ({"pixel_size": 0.4}, {"pixel_size": 1001}, {"object_edge_length": 0.0005}, {"space": "VIEW"},
                        {"enabled": 1}, {"camera": "Camera"}):
            with self.subTest(adaptive=refused), self.assertRaises(ValueError):
                validate_operation(subsurf(adaptive=dict({"enabled": True}, **refused)))
        for refused in ({"adaptive": {"space": "PIXEL"}}, {"use_adaptive_subdivision": True}, {"adaptive": True}):
            with self.subTest(settings=refused), self.assertRaises(ValueError):
                validate_operation(subsurf(**refused))

    def test_every_typed_subdivision_setting_is_in_the_revision(self):
        self.assertIn("SUBSURF", REVISION_MODIFIERS)
        self.assertTrue({"levels", "render_levels", "subdivision_type", *ADAPTIVE} <= set(MODIFIER_FIELDS["SUBSURF"]))


class DerivedCheckTests(unittest.TestCase):
    def test_add_and_configure_state_the_levels_and_the_adaptive_settings_written(self):
        for kind in ("modifier_add", "modifier_configure"):
            with self.subTest(kind=kind):
                levels, adaptive = derive_checks([validate_operation(subsurf(
                    kind, levels=1, render_levels=5, adaptive={"enabled": True, "pixel_size": 1.5}))])
                self.assertEqual(levels, {"kind": "modifier", "target": OBJ, "name": "Subdivision", "type": "SUBSURF",
                                          "values": {"levels": 1, "render_levels": 5}})  # What it states.
                self.assertEqual(adaptive["kind"], "modifier_values")
                self.assertEqual(adaptive["values"], {"use_adaptive_subdivision": True, "adaptive_pixel_size": 1.5})
                self.assertEqual(validate_check(levels)["values"]["render_levels"], 5)
                validate_check(adaptive)
        (only,) = derive_checks([validate_operation(subsurf(levels=1))])
        self.assertEqual(only["kind"], "modifier", "No adaptive check when no adaptive setting is written")

    def test_a_later_configure_replaces_the_earlier_check(self):
        checks = derive_checks([validate_operation(subsurf(levels=1, render_levels=2)),
                                validate_operation(subsurf("modifier_configure", levels=2, render_levels=6))])
        self.assertEqual([check["values"]["render_levels"] for check in checks], [6])

    def test_other_typed_types_derive_a_check_for_configure_too(self):
        (check,) = derive_checks([validate_operation({"kind": "modifier_configure", "target": OBJ, "parameters": {
            "type": "BEVEL", "modifier": "Bevel", "settings": {"width": 0.1, "segments": 3}}})])
        self.assertEqual((check["kind"], check["name"], check["values"]["segments"]), ("modifier", "Bevel", 3))


class RenderCeilingTests(unittest.TestCase):
    """The staged bound counts viewport levels; the render-time bound is held to its own ceiling."""

    def setUp(self):
        self.budget = _budget(self)
        self.staged = NS(**dict(vars(CONTEXT), render_baselines={}))

    def bound(self, obj, extra=None, replacing=None, context=None):
        return self.budget._modifier_bound(context or self.staged, obj, extra=extra, replacing=replacing)

    def test_the_staged_bound_ignores_render_levels(self):
        self.assertEqual(MAX_RENDER_VERTICES, 64 * MAX_EVALUATED_VERTICES)
        for render in range(7):
            with self.subTest(render=render):
                cube = _mesh(8, 12, 6, 24, [NS(type="SUBSURF", levels=2, render_levels=render)])
                self.assertEqual(self.bound(cube), 24 * 16)
        work = estimated_work(validate_operation(subsurf(levels=2, render_levels=6)), {"vertices": 8, "edges": 12, "faces": 6})
        self.assertEqual(work["evaluated_vertices"], 24 * 16)

    def test_a_typed_write_may_not_pass_the_ceiling(self):
        dense = _mesh(10201, 20200, 10000, 40000)  # A 100 x 100 grid: bound 40,400.
        self.bound(dense)
        fits = {"type": "SUBSURF", "settings": {"levels": 0, "render_levels": 4}}
        self.assertEqual(self.bound(dense, extra=fits), 40400)
        self.assertLessEqual(40400 * 256, MAX_RENDER_VERTICES)
        with self.assertRaisesRegex(ValueError, r"render-time topology budget: about 41369600 .*limit 32000000"):
            self.bound(dense, extra={"type": "SUBSURF", "settings": {"levels": 0, "render_levels": 5}})

    def test_the_old_caps_worst_case_is_exactly_the_ceiling(self):
        # Render level 3 over viewport level 0 on a mesh at the staged budget: what the cap of 3 could leave.
        stack = NS(type="SUBSURF", levels=0, render_levels=0)
        edge = _mesh(8, 1, 1, MAX_EVALUATED_VERTICES, [stack])  # Loop-scale bound: exactly the staged budget.
        self.assertEqual(self.bound(edge), MAX_EVALUATED_VERTICES)
        at_the_cap = {"type": "SUBSURF", "settings": {"levels": 0, "render_levels": 3}}
        self.assertEqual(self.bound(edge, extra=at_the_cap, replacing=stack), MAX_EVALUATED_VERTICES)
        with self.assertRaisesRegex(ValueError, "render-time topology budget"):
            self.bound(edge, extra={"type": "SUBSURF", "settings": {"levels": 0, "render_levels": 4}}, replacing=stack)

    def test_every_subdivision_modifier_of_the_stack_and_its_operands_counts(self):
        dense = _mesh(10201, 20200, 10000, 40000, [NS(type="SUBSURF", levels=0, render_levels=2)])
        self.bound(dense)
        with self.assertRaisesRegex(ValueError, "render-time topology budget"):
            self.bound(dense, extra={"type": "SUBSURF", "settings": {"levels": 0, "render_levels": 4}})
        cutter = _mesh(10201, 20200, 10000, 40000, [NS(type="SUBSURF", levels=0, render_levels=5)])
        context = NS(**dict(vars(self.staged), resolve=lambda handle: cutter))
        with self.assertRaisesRegex(ValueError, "render-time topology budget"):
            self.bound(_mesh(8, 12, 6, 24), extra={"type": "BOOLEAN", "settings": {"operand": "$cut"}}, context=context)

    def test_an_object_already_above_the_ceiling_can_be_edited_and_lowered_not_raised(self):
        stack = NS(type="SUBSURF", levels=0, render_levels=6)
        dense = _mesh(10201, 20200, 10000, 40000, [stack])
        self.assertEqual(self.bound(dense), 40400)  # First seen as it is: 165 million at render time.
        lower = {"type": "SUBSURF", "settings": {"levels": 0, "render_levels": 5}}
        self.assertEqual(self.bound(dense, extra=lower, replacing=stack), 40400)
        with self.assertRaisesRegex(ValueError, "render-time topology budget"):
            self.bound(dense, extra={"type": "ARRAY", "settings": {"count": 2}})

    def test_binding_a_live_object_refuses_only_what_typed_kinds_cannot_express(self):
        # No staged context (native_programs' read-only preflight): nothing evaluates render levels there.
        dense = _mesh(10201, 20200, 10000, 40000, [NS(type="SUBSURF", levels=0, render_levels=6)])
        self.assertEqual(self.bound(dense, context=CONTEXT), 40400)
        for levels, render in ((0, 7), (4, 4), (4, 0)):
            with self.subTest(levels=levels, render=render), self.assertRaisesRegex(ValueError, "bounded native capability"):
                self.bound(_mesh(8, 12, 6, 24, [NS(type="SUBSURF", levels=levels, render_levels=render)]), context=CONTEXT)

    def test_a_prospective_stack_never_becomes_the_baseline(self):
        dense = _mesh(10201, 20200, 10000, 40000)
        with self.assertRaisesRegex(ValueError, "render-time topology budget"):
            self.bound(dense, extra={"type": "SUBSURF", "settings": {"levels": 0, "render_levels": 6}})
        self.assertNotIn(dense, self.staged.render_baselines)


if __name__ == "__main__":
    unittest.main()
