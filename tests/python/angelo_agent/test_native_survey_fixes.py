# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host-contract defects a survey of everyday requests found (64 requests in one chat, toolset 29).

Dependency-free: identifier expansion, the stage's failure text, the modifier
budget against a stand-in ``bpy``, the typed torus and the observed binding of
undeclared program dependencies. The Blender halves (material edit in place,
LINEAR animation staged and live, evaluated dimensions, the torus kernel and
the budget's soundness against real evaluation) are in
``test_blender_survey_fixes.py``. Every test here failed before its fix.
"""

import importlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import numpy  # noqa: F401 (loaded before patch.dict snapshots sys.modules, so it is never reloaded)

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from claude_protocol_fixtures import host  # noqa: E402,F401 (installs the bpy stand-ins the add-on imports need)
from angelo_agent.action_catalog import estimated_work, validate_operation  # noqa: E402
from angelo_agent.identifiers import IdentifierTable, expand_call  # noqa: E402
from angelo_agent.plan_schema import PLAN_CORE_ACTIONS, deferred_references  # noqa: E402
from angelo_agent.program_verification import failure_text  # noqa: E402
from angelo_agent.sculpt_spec import SCULPT_NAMES  # noqa: E402

HANDLE = "scn:" + "c" * 32 + ":159"
OTHER = "scn:" + "c" * 32 + ":136"


class SculptHandleTests(unittest.TestCase):
    """angelo_inspect_sculpt answered "Choose an existing polygon mesh" for Eval Cube: Claude passed ``o3``
    as every target, and the sculpt tools look their object up by name."""

    def test_a_known_short_handle_names_the_sculpt_object(self):
        table = IdentifierTable()
        table.short_handle(OTHER)
        table.short_handle(HANDLE)  # o2
        for name in sorted(SCULPT_NAMES):
            with self.subTest(tool=name):
                self.assertEqual(expand_call(name, {"object": "o2"}, table)["object"], HANDLE)
        self.assertEqual(expand_call("angelo_inspect_sculpt", {"object": "Eval Cube", "probes": []}, table)["object"],
                         "Eval Cube", "Names stay names")
        self.assertEqual(expand_call("angelo_inspect_sculpt", {"object": "o9", "probes": []}, table)["object"], "o9",
                         "An unknown short form may be a name")

    def test_the_sculpt_tools_resolve_a_handle_themselves(self):
        """sculpt.target resolves a name or a handle as angelo_set_transform does (sculpt_preflight.named), inside
        the edit's preflight. The dispatch no longer turns a handle into its object's name: an object named like
        another's handle then resolved as that other object (test_blender_sculpt_handles proves both in the
        app)."""
        root = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
        dispatch = (root / "blender_tools.py").read_text().split("if name in SCULPT_NAMES:", 1)[1].split(
            "return sculpt.execute", 1)[0]
        self.assertNotIn("transform_object", dispatch)
        self.assertIn("transform_object(window, value)", (root / "sculpt_preflight.py").read_text())
        target = (root / "sculpt.py").read_text().split("def target(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("obj = named(window, name)", target)


class FailureTextTests(unittest.TestCase):
    """A failed expected check returned only its kind ("Expected check failed: dimensions"), so the model
    resubmitted the same plan (spin_turntable failed twice the same way)."""

    def test_a_failed_check_carries_what_it_observed(self):
        report = {"checks": [{"passed": False, "status": "fail", "observed": {"dimensions": [1, 1, 1], "space": "WORLD"}}]}
        self.assertEqual(failure_text("dimensions", report),
                         'dimensions (observed {"dimensions":[1,1,1],"space":"WORLD"})')
        reason = {"checks": [{"passed": False, "status": "inconclusive", "reason": "Muted curves are unsupported"}]}
        self.assertEqual(failure_text("animation", reason), "animation: Muted curves are unsupported")
        long = {"checks": [{"passed": False, "observed": {"samples": list(range(500))}}]}
        self.assertLess(len(failure_text("animation", long)), 450)
        self.assertEqual(failure_text("dimensions", {"checks": [{"passed": True}]}), "dimensions")


def _budget(test):
    """action_budget against a stand-in bpy (as test_native_operator_run_families), OpenSubdiv assumed."""
    package = sys.modules["angelo_agent"]
    before = set(vars(package))
    test.addCleanup(lambda: [delattr(package, name) for name in set(vars(package)) - before])
    bpy = NS(types=NS(), data=NS(), context=NS(), ops=NS())
    with patch.dict(sys.modules, {"bpy": bpy, "mathutils": NS(Vector=tuple), "bmesh": NS()}):
        budget = importlib.import_module("angelo_agent.action_budget")
    patcher = patch.object(budget, "_require_subdivision", lambda level: None)
    patcher.start()
    test.addCleanup(patcher.stop)
    return budget


class _Elements(list):
    def foreach_get(self, name, values):
        values[:] = 0  # Origin coordinates: only the counts matter here.


def _mesh(vertices, edges, faces, loops, modifiers=()):
    data = NS(vertices=_Elements([0] * vertices), edges=[0] * edges, polygons=[0] * faces, loops=[0] * loops,
              is_editmode=False, shape_keys=None)
    return _Object(type="MESH", data=data, modifiers=list(modifiers), dimensions=(2, 2, 2))


class _Object:
    """Hashable by identity, like an ID."""

    def __init__(self, **fields):
        vars(self).update(fields)


def _sharp_bevel(segments):
    return NS(type="BEVEL", segments=segments, miter_outer="MITER_SHARP", miter_inner="MITER_SHARP")


CONTEXT = NS(assert_staged=lambda obj: obj, require_private=lambda block: block, owned_ids=[], node_bounds={})


class MaterialEditTests(unittest.TestCase):
    """recolor_material: naming 'Red Plastic' made 'Red Plastic.002' and orphaned the original."""

    def test_a_chosen_name_edits_in_place_and_a_default_name_never_does(self):
        from angelo_agent.node_edit_commit import edits_in_place
        for kind, default in (("material_principled", "Angelo Material"), ("shader_nodes", "Angelo Shader")):
            with self.subTest(kind=kind):
                self.assertTrue(edits_in_place({"kind": kind, "parameters": {"name": "Red Plastic"}}))
                self.assertFalse(edits_in_place({"kind": kind, "parameters": {"name": default}}),
                                 "A second default-named material is a new one, as before")
        self.assertFalse(edits_in_place({"kind": "node_edit", "parameters": {"name": "Red Plastic"}}))

    def test_the_kernel_and_staging_share_the_rule(self):
        addon = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
        self.assertIn("if not edits_in_place(operation):", (addon / "action_nodes.py").read_text())
        self.assertIn('op["kind"] == "node_edit" or edits_in_place(op)', (addon / "node_edit_commit.py").read_text())
        self.assertIn('outcome.get("edited_in_place")', (addon / "program_objects.py").read_text(),
                      "Commit transfers an edited material (node_edit_commit.transfer_trees)")


class TopologyBudgetTests(unittest.TestCase):
    """Survey: Subdivision 2 (render 3) over a 3-segment Bevel, and a Boolean hole, refused as over budget."""

    def setUp(self):
        self.budget = _budget(self)

    def bound(self, obj, extra=None):
        return self.budget._modifier_bound(CONTEXT, obj, extra=extra)

    def test_the_survey_stack_fits_and_counts_viewport_levels(self):
        # Eval Cube then: two extruded boxes (24/40/20, 80 corners), Bevel 3 segments, Subdivision 2 (render 3).
        cube = _mesh(24, 40, 20, 80, [_sharp_bevel(3), NS(type="SUBSURF", levels=2, render_levels=3)])
        self.assertEqual(self.bound(cube), 80 * 20 * 16)  # Was 80 * 128 * 64 = 655,360: refused.
        cutter = _mesh(64, 96, 34, 192)  # A 32-segment cylinder.
        context = NS(**dict(vars(CONTEXT), resolve=lambda handle: cutter))
        holed = self.budget._modifier_bound(context, cube, extra={"type": "BOOLEAN", "settings": {"operand": "$cut"}})
        self.assertEqual(holed, 25600 + 192 + 192)  # Was 25,600 x 192 x 2 more: refused.

    def test_the_survey_cube_takes_its_subdivision_and_its_hole(self):
        # The survey's Eval Cube when the Boolean ran: 224 vertices and 228 quads after a join, Bevel 3 segments,
        # Subdivision 2 (render 3). Main refused the Subdivision; twice-both-operands still refused the hole.
        cube = _mesh(224, 450, 228, 912, [_sharp_bevel(3), NS(type="SUBSURF", levels=2, render_levels=3)])
        self.assertEqual(self.bound(cube), 912 * 20 * 16)
        cutter = _mesh(64, 96, 34, 192)
        context = NS(**dict(vars(CONTEXT), resolve=lambda handle: cutter))
        holed = self.budget._modifier_bound(context, cube, extra={"type": "BOOLEAN", "settings": {"operand": "$cut"}})
        self.assertEqual(holed, 912 * 20 * 16 + 192 + 192)
        self.assertLess(holed, self.budget.MAX_EVALUATED_VERTICES)

    def test_the_real_bound_still_holds_at_the_edge(self):
        limit = self.budget.MAX_EVALUATED_VERTICES
        edge = _mesh(limit // 64, 1, 1, 1, [NS(type="SUBSURF", levels=3, render_levels=3)])
        self.assertEqual(self.bound(edge), limit // 64 * 64)
        over = _mesh(limit // 64 + 1, 1, 1, 1, [NS(type="SUBSURF", levels=3, render_levels=3)])
        with self.assertRaisesRegex(ValueError, "evaluated topology budget"):
            self.bound(over)
        # Render levels up to 6 are typed now (test_native_subdivision_levels); beyond them a stack is still refused.
        with self.assertRaisesRegex(ValueError, "bounded native capability"):
            self.bound(_mesh(8, 12, 6, 24, [NS(type="SUBSURF", levels=1, render_levels=7)]))
        with self.assertRaisesRegex(ValueError, "bounded native capability"):
            self.bound(_mesh(8, 12, 6, 24, [NS(type="SUBSURF", levels=4, render_levels=4)]))
        # A fifth xyz Mirror on a cube still overflows (24 x 2^15).
        mirrors = [NS(type="MIRROR", use_axis=(True, True, True))] * 5
        with self.assertRaisesRegex(ValueError, "evaluated topology budget"):
            self.bound(_mesh(8, 12, 6, 24, mirrors))

    def test_bevel_factors_cover_blender_worst_cases(self):
        # Blender 5.2 worst cases over primitives and Suzanne (a cube each time): sharp (s+1)^2 + 1/3,
        # arc or patch miters (2s+1)(s+1). The factors stay above both and far below the old 8 (s+1)^2.
        for segments in range(1, 9):
            with self.subTest(segments=segments):
                self.assertGreaterEqual(self.budget.bevel_factor(segments), (segments + 1) ** 2 + 1 / 3)
                self.assertGreaterEqual(self.budget.bevel_factor(segments, True), (2 * segments + 1) * (segments + 1))
                self.assertLess(self.budget.bevel_factor(segments, True), 8 * (segments + 1) ** 2)
        mitered = NS(type="BEVEL", segments=3, miter_outer="MITER_ARC", miter_inner="MITER_SHARP")
        self.assertEqual(self.bound(_mesh(8, 12, 6, 24, [mitered])), 24 * 36)
        self.assertEqual(self.bound(_mesh(8, 12, 6, 24), {"type": "BEVEL", "settings": {"segments": 3}}), 24 * 20)

    def test_loops_count_when_ngons_dominate(self):
        # A 32-gon fan: 4F underestimates its corners.
        self.assertEqual(self.bound(_mesh(32, 32, 1, 32)), 64)
        self.assertEqual(self.bound(_mesh(16, 8, 1, 200)), 200)

    def test_estimated_work_matches_the_budget(self):
        operation = validate_operation({"kind": "modifier_add", "target": HANDLE, "parameters": {
            "type": "SUBSURF", "settings": {"levels": 2, "render_levels": 3}}})
        self.assertEqual(estimated_work(operation, {"vertices": 8, "edges": 12, "faces": 6})["evaluated_vertices"], 24 * 16)
        bevel = validate_operation({"kind": "modifier_add", "target": HANDLE, "parameters": {
            "type": "BEVEL", "settings": {"segments": 3}}})
        self.assertEqual(estimated_work(bevel, {"vertices": 8, "edges": 12, "faces": 6})["evaluated_vertices"], 24 * 20)


class TorusTests(unittest.TestCase):
    """"Add a donut (a torus)": no typed torus, so Claude reached for operator_run (refused, NOT_ALLOWED)."""

    def test_a_typed_torus_with_blender_defaults(self):
        operation = validate_operation({"kind": "create_primitive", "target": None,
                                        "parameters": {"primitive": "TORUS", "alias": "donut"}})
        params = operation["parameters"]
        self.assertEqual((params["major_radius"], params["minor_radius"], params["major_segments"],
                          params["minor_segments"]), (1, .25, 48, 12))
        work = estimated_work(operation)
        self.assertEqual((work["vertices"], work["edges"], work["faces"]), (576, 1152, 576))
        for bad in ({"minor_segments": 2}, {"major_segments": 129}, {"minor_radius": 0}, {"segments": 32}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_operation({"kind": "create_primitive", "target": None,
                                    "parameters": dict({"primitive": "TORUS"}, **bad)})

    def test_create_primitive_stays_a_core_kind(self):
        self.assertIn("create_primitive", PLAN_CORE_ACTIONS)
        plan = [{"tool": "angelo_scene_program", "arguments": {"operations": [
            {"kind": "create_primitive", "target": None, "parameters": {"primitive": "TORUS"}}]}}]
        self.assertEqual(deferred_references(plan), {})


class ObservedDependencyTests(unittest.TestCase):
    """Survey (donut turn): unparenting the table legs named the legs only; their parent, the Table Top, was in
    the model's latest observation, yet the program was refused until it was "declared in the read set"."""

    def setUp(self):
        from angelo_agent import program_admission
        self.admission = program_admission
        patcher = patch.dict(sys.modules, {"angelo_agent.scene_queries": NS(object_handle=lambda obj: obj.handle)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_observed_dependency_binds_to_the_revision_the_context_saw(self):
        table = IdentifierTable()
        table.short_handle(HANDLE)
        table.observe({"objects": [{"handle": HANDLE, "object_revision": "f" * 64}]})
        top = NS(handle=HANDLE, name="Table Top")
        self.assertEqual(self.admission.observed_binding(NS(_identifiers=table), top), (HANDLE, "f" * 64))

    def test_an_unobserved_dependency_is_still_refused_by_name(self):
        unseen = NS(handle=OTHER, name="Table Top")
        for controller in (None, NS(_identifiers=None), NS(_identifiers=IdentifierTable())):
            with self.subTest(controller=controller), self.assertRaisesRegex(Exception, "Declare every existing parent"):
                self.admission.observed_binding(controller, unseen)

    def test_bind_binds_dependencies_to_observed_revisions_only(self):
        source = (Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/native_programs.py").read_text()
        bind = source.split("    def bind(self, program):", 1)[1].split("    def _scene_handle", 1)[0]
        self.assertIn("observed_binding(self.controller, obj, self.scene)", bind)  # The scene names one outside it.
        self.assertIn("self._revision(obj, handle) != expected", bind, "A changed dependency still fails closed")
        self.assertIn("+ tuple(inferred)", bind, "Inferred dependencies are staged and verified like declared ones")


if __name__ == "__main__":
    unittest.main()
