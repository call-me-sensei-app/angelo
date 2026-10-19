# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Intended obligations never replace native staged eligibility or owner identity."""

import copy
import importlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import material_sharing, node_edit_spec, program_material_facts
from angelo_agent.program_checks import resolve_obligations
from angelo_agent.program_completion import capture_program
from angelo_agent.program_jobs import ProgramJob, WriterLease
from test_native_material_identity import Material, Context, owner
from test_native_material_obligations import A, B, op, program, facts
from test_native_program_jobs import Backend, finish


def isolation():
    return op("node_edit", A, material="Paint", shared="ISOLATE", edits=[
        {"op": "set", "node": "Principled BSDF", "inputs": {"Roughness": .7}}])


def bound(shared_data=True):
    observed = facts([A], {A: [("Paint", 7)]})
    observed["resources"]["live:7"]["sharing"][A].update(others=1, data_shared=shared_data)
    return observed


class EligibilityTests(unittest.TestCase):
    def setUp(self):
        native_types = SimpleNamespace(Material=Material, World=type("World", (), {}))
        modules = patch.dict(sys.modules, {"bpy": SimpleNamespace(types=native_types)})
        modules.start()
        self.addCleanup(modules.stop)
        self.kernel = importlib.import_module("angelo_agent.node_edit")

    def context(self, *, assigned=False, shared_data=True):
        paint = Material("Paint stage", 2 if assigned else 1, **{node_edit_spec.SOURCE: 7})
        ctx = Context(objects={A: owner(paint)}, aliases={"$late": owner(paint)} if assigned else {},
                      facts={A: {"7": {"name": "Paint", "others": 1, "who": "Other", "data_shared": shared_data}}})
        if assigned:
            material_sharing.note_assignment(ctx, paint)
        return ctx, paint

    def test_assignment_guard_precedes_shared_data_and_pure_intent_does_not_decide_policy(self):
        request = program([op("create_primitive", None, primitive="CUBE", alias="late"),
                           op("material_assign", "$late", source=A, material="Paint"), isolation()], [A])
        # The bound obligations refuse it before approval, as the kernel does at staging (material_rules
        # .shared_in_program); it was once refused only after approval.
        with self.assertRaises(node_edit_spec.NodeEditError) as early:
            resolve_obligations(request, bound())
        self.assertEqual(early.exception.code, "NODE_TREE_SHARED")
        self.assertIn("assigned to another object earlier", str(early.exception))
        ctx, paint = self.context(assigned=True)
        before = dict(paint.markers)
        with self.assertRaises(node_edit_spec.NodeEditError) as refused:
            self.kernel._material(ctx, A, ctx.resolve(A), request["operations"][-1]["parameters"], True)
        self.assertEqual(refused.exception.code, "NODE_TREE_SHARED")
        self.assertIn("assigned to another object earlier", str(refused.exception))
        self.assertEqual(paint.markers, before)
        self.assertEqual(ctx.copies, [])

    def test_an_edited_material_another_object_got_is_refused_before_approval_and_never_copied(self):
        """Regression: edit, assign, edit again failed after approval, after the kernel
        silently copied the edited material for one object. Refused before approval with what works instead, and by
        the kernel without a copy; the shapes that committed still bind."""
        bound_facts = facts([A, B], {A: [("Paint", 7)]})
        again = program([op("material_principled", A, name="Paint", inputs={"Roughness": .3}),
                         op("material_assign", B, source=A, material="Paint"),
                         op("material_principled", A, name="Paint", inputs={"Metallic": .9})], [A, B])
        with self.assertRaisesRegex(Exception, "NODE_TREE_SHARED: 'Paint' was edited earlier in this program") as early:
            resolve_obligations(again, bound_facts)
        self.assertIn("ALL_USERS", str(early.exception))
        self.assertIn("before assigning or copying it", str(early.exception))
        for shared in ("ALL_USERS",):  # The same edit for every user binds, as it committed.
            together = copy.deepcopy(again)
            together["operations"][2]["parameters"]["shared"] = shared
            resolve_obligations(program(together["operations"], [A, B]), bound_facts)
        resolve_obligations(program([op("material_principled", A, name="Paint", inputs={"Roughness": .3}),
                                     op("material_principled", A, name="Paint", inputs={"Metallic": .9})], [A, B]),
                            bound_facts)  # Edited twice, held by nobody else: binds.
        edited = Material("Paint stage", 2, **{node_edit_spec.EDITED: 7})
        ctx = Context(objects={A: owner(edited), B: owner(edited)},
                      facts={A: {"7": {"name": "Paint", "others": 0, "who": "", "data_shared": False}}})
        with self.assertRaisesRegex(node_edit_spec.NodeEditError, "NODE_TREE_SHARED.*edited earlier"):
            self.kernel._material(ctx, A, ctx.resolve(A), {"material": "Paint", "shared": "REFUSE"}, True)
        self.assertEqual(ctx.copies, [])  # Never a silent per-object copy.

    def test_actual_shared_data_refusal_cannot_commit_restage_or_mint_completion(self):
        request = program([isolation()], [A])
        bundle = resolve_obligations(request, bound())
        self.assertEqual(bundle.effects()[0]["mode"], "isolate")
        ctx, paint = self.context()
        kernel = self.kernel

        class KernelBackend(Backend):
            def bind(self, requested):
                return dict(super().bind(requested), obligations=bundle)

            def apply(self, stage, operation):
                return kernel._material(ctx, A, ctx.resolve(A), operation["parameters"], True)

        backend, writer = KernelBackend(), WriterLease()
        source, markers = copy.deepcopy(backend.source), dict(paint.markers)
        job = ProgramJob(backend, request, approved_obligations=bundle, writer=writer)
        result = finish(job)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["error"].startswith("node_edit DATA_SHARED:"))
        self.assertFalse(result["scene_changed"] or result.get("partial_changes_possible"))
        self.assertEqual(backend.source, source)
        self.assertEqual(paint.markers, markers)
        self.assertEqual(backend.commits, 0)
        self.assertEqual((backend.calls.count("bind"), backend.calls.count("stage")), (1, 1))
        self.assertEqual(backend.discards, 1)
        self.assertTrue(job.closed)
        self.assertIsNone(writer.owner)
        self.assertIsNone(job.completion_obligations())
        self.assertIsNone(capture_program(job))

    def test_tree_and_animation_guards_keep_their_native_precedence_over_assignment_and_data(self):
        for code in ("NO_NODE_TREE", "ANIMATED_TREE"):
            with self.subTest(code=code):
                ctx, paint = self.context(assigned=True)
                if code == "NO_NODE_TREE":
                    paint.node_tree = None
                else:
                    paint.animation_data = SimpleNamespace(action=object(), drivers=[], nla_tracks=[])
                with self.assertRaises(node_edit_spec.NodeEditError) as refused:
                    self.kernel._material(ctx, A, ctx.resolve(A), isolation()["parameters"], True)
                self.assertEqual(refused.exception.code, code)
                self.assertEqual(ctx.copies, [])

    def test_valid_live_isolation_still_reaches_the_same_native_policy(self):
        request = program([isolation()], [A])
        self.assertEqual(resolve_obligations(request, bound(False)).effects()[0]["mode"], "isolate")
        ctx, paint = self.context(shared_data=False)
        result = self.kernel._material(ctx, A, ctx.resolve(A), request["operations"][0]["parameters"], True)
        self.assertIs(result, paint)
        self.assertEqual(result[node_edit_spec.MODE], "ASSIGN")
        self.assertEqual(result[node_edit_spec.EDITED], 7)

    def test_actual_bound_scene_identity_refuses_material_and_modifier_before_fact_reads(self):
        for selector in ({"material": "Paint"}, {"modifier": "Geometry"}):
            request = program([op("node_edit", A, **selector, edits=[{"op": "remove", "node": "Unused"}])], [A])
            binding = {"program": request, "handles": (), "originals": {}, "scene": {"handle": A}}
            with patch.object(program_material_facts, "collect_material_facts", side_effect=AssertionError("fact read")):
                with self.assertRaises(node_edit_spec.NodeEditError) as early:
                    program_material_facts.resolve_binding(None, binding)
            ctx = Context()
            ctx.scene_handles = (A,)
            with self.assertRaises(node_edit_spec.NodeEditError) as actual:
                self.kernel.apply(ctx, request["operations"][0])
            self.assertEqual(early.exception.code, "OWNER_TYPE_MISMATCH")
            self.assertIs(type(early.exception), type(actual.exception))
            self.assertEqual(str(early.exception), str(actual.exception))
            # The same textual target remains an object when the real bound
            # scene is different. No handle spelling grants a type.
            binding.update(handles=(A,), scene={"handle": B})
            with patch.object(program_material_facts, "collect_material_facts", return_value=bound(False)):
                self.assertTrue(program_material_facts.resolve_binding(None, binding).checks())

    def test_world_selector_keeps_its_existing_bound_scene_path(self):
        request = program([op("node_edit", A, world=True, edits=[{"op": "remove", "node": "Unused"}])], [A])
        binding = {"program": request, "handles": (), "originals": {}, "scene": {"handle": A}}
        with patch.object(program_material_facts, "collect_material_facts", side_effect=AssertionError("fact read")):
            self.assertTrue(program_material_facts.resolve_binding(None, binding).checks()[0]["world"])


if __name__ == "__main__":
    unittest.main()
