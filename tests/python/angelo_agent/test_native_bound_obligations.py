# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Immutable native obligations and material provenance without Blender."""

import copy
import contextlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import program_material_facts as facts
from angelo_agent import program_material_provenance as provenance
from angelo_agent.program_checks import resolve_obligations
from angelo_agent.program_context import ProgramBinding, precondition_change
from angelo_agent.program_jobs import ProgramJob, ProgramError, PreviewStore, WriterLease
from angelo_agent.program_spec import validate_program
from test_native_program_jobs import Backend, program, finish


class Block(dict):
    def __init__(self, name, uid, **values):
        super().__init__(values)
        self.name, self.session_uid, self.id_type = name, uid, "MATERIAL"
        self.node_tree = None
        self.use_fake_user = False

    def __hash__(self):
        return id(self)

    def __eq__(self, other):
        return self is other

    def as_pointer(self):
        return id(self)


def context(blocks, slots):
    obj = SimpleNamespace(material_slots=[SimpleNamespace(material=block) for block in slots])
    return SimpleNamespace(owned_ids=set(blocks), objects={"$a": obj}, aliases={}, extras={},
                           resolve=lambda target: obj)


class ProvenanceTests(unittest.TestCase):
    def test_socket_role_rejects_runtime_getter_before_reading_it(self):
        class Socket:
            identifier = name = "Geometry"
            enabled = True
            bl_rna = SimpleNamespace(properties={"is_multi_input": SimpleNamespace(type="BOOLEAN", is_runtime=True)})

            @property
            def is_multi_input(self):
                raise AssertionError("The add-on getter ran")

        tree = SimpleNamespace(nodes={"Join": SimpleNamespace(inputs=[Socket()])})
        with self.assertRaisesRegex(ProgramError, "built-in stored"):
            facts._input_role(tree, "Join", "Geometry", [100, 100])

    def test_live_source_text_never_replaces_captured_native_copy(self):
        from angelo_agent.node_edit_spec import SOURCE
        original = Block("Paint", 10)
        private = Block("Paint.001", 11, **{SOURCE: 10})
        bundle = SimpleNamespace(requires_binding=True, facts=lambda: {"resources": {"live:10": {}}})
        stage = SimpleNamespace(binding={"obligations": bundle}, extras={})
        facts.record_clone(stage, original, private, "live")
        ctx = context([private], [private])
        ctx.extras = stage.extras
        stage.context = ctx
        facts.initialize_clones(stage)
        self.assertTrue(facts.matches_resource(ctx, private, "live:10"))
        forged = Block("Other", 12, **{SOURCE: 10})
        ctx.owned_ids.add(forged)
        self.assertFalse(facts.matches_resource(ctx, forged, "live:10"))
        forged[provenance.CLONE_MARKER] = private[provenance.CLONE_MARKER]
        self.assertFalse(facts.matches_resource(ctx, forged, "live:10"))

    def test_copy_transition_requires_the_captured_source_id(self):
        source, private, unrelated = Block("Paint", 1), Block("Paint.001", 2), Block("Other", 3)
        ctx = context([source, private, unrelated], [source])
        facts.record_copy(ctx, source, private, "Paint")
        self.assertTrue(provenance._from_source(ctx, private, (source.as_pointer(), source.session_uid)))
        self.assertFalse(provenance._from_source(ctx, private, (unrelated.as_pointer(), unrelated.session_uid)))
        unrelated[facts.MARKER] = private[facts.MARKER]
        self.assertFalse(provenance._from_source(ctx, unrelated, (source.as_pointer(), source.session_uid)))

    def test_registered_created_id_cannot_gain_live_transfer_authority(self):
        from angelo_agent.node_edit_spec import SOURCE
        material = Block("Paint", 1)
        ctx = context([material], [material])
        facts._register(ctx, "created:0", material, "Paint", transfer={})
        self.assertTrue(facts.matches_resource(ctx, material, "created:0"))
        material[SOURCE] = 900
        self.assertFalse(facts.matches_resource(ctx, material, "created:0"))
        self.assertIsNone(facts.logical_material_name(ctx, "$a", material))

    def test_unified_live_resource_prunes_redundant_ownerless_clone_before_file_save(self):
        from angelo_agent.node_edit_spec import SOURCE
        original = Block("Paint", 10)
        first, second = Block("Paint.001", 11, **{SOURCE: 10}), Block("Paint.002", 12, **{SOURCE: 10})
        bundle = SimpleNamespace(requires_binding=True, assert_integrity=lambda program: None,
                                 facts=lambda: {"resources": {"live:10": {}}}, effects=lambda: [],
                                 checks=lambda: [{"__host_material": {"resource": "live:10"}}])
        stage = SimpleNamespace(binding={"obligations": bundle, "program": {}}, extras={})
        facts.record_clone(stage, original, first, "live")
        facts.record_clone(stage, original, second, "live")
        ctx = context([first, second], [second])
        ctx.extras = stage.extras
        stage.context = ctx
        facts.initialize_clones(stage)
        facts.snapshot_provenance(stage)
        self.assertEqual(len(stage.extras[provenance.CLONES]), 1)
        self.assertNotIn(provenance.CLONE_MARKER, first)
        # A file containing the scene carries only the still-used second clone.
        ctx.owned_ids.remove(first)
        facts.restore_provenance(stage)
        self.assertTrue(facts.matches_resource(ctx, second, "live:10"))
        second[SOURCE] = 999
        with self.assertRaisesRegex(ProgramError, "source or transfer"):
            facts.snapshot_provenance(stage)
        with self.assertRaisesRegex(ProgramError, "source or transfer"):
            facts.restore_provenance(stage)

    def fixture(self):
        material = Block("Paint", 1)
        ctx = context([material], [material])
        facts._register(ctx, "created:0", material, "Paint")
        ctx.extras[facts.REGISTRY]["created:0"]["owners"] = [["$a", 0]]
        bundle = SimpleNamespace(assert_integrity=lambda program: None,
                                 effects=lambda: [{"resource": "created:0", "operation": 0}], checks=lambda: [])
        return material, ctx, SimpleNamespace(context=ctx, extras=ctx.extras,
                                               binding={"obligations": bundle, "program": {}}, objects=ctx.objects)

    def test_marker_text_does_not_authorize_a_different_native_id(self):
        material, ctx, stage = self.fixture()
        forged = Block("Paint", 2, **{facts.MARKER: material[facts.MARKER]})
        ctx.owned_ids = {forged}
        ctx.objects["$a"].material_slots[0].material = forged
        self.assertIsNone(facts.logical_material_name(ctx, "$a", forged))
        self.assertFalse(facts.matches_resource(ctx, forged, "created:0"))
        with self.assertRaisesRegex(ProgramError, "provenance"):
            facts.snapshot_provenance(stage)

    def test_actual_guard_report_is_positive_nested_completion_evidence(self):
        from angelo_agent.program_verification import ProgramVerificationMixin
        from angelo_agent.claude_outcomes import native_result_problem, _positive
        material, ctx, stage = self.fixture()
        backend = ProgramVerificationMixin()
        backend._update_stage = lambda stage: None
        predicate = {"kind": "material_assignment", "target": "$a", "material": "Paint", "all_faces": False,
                     "__host_material": {"resource": "created:0", "slot": 0}}
        report = backend.validate(stage, predicate)
        self.assertIsNone(native_result_problem(report, verification=True))
        self.assertTrue(_positive(report))
        del material[facts.MARKER]
        failed = backend.validate(stage, predicate)
        self.assertFalse(_positive(failed))
        self.assertIsNotNone(native_result_problem(failed, verification=True))

    def test_missing_duplicate_and_wrong_owner_provenance_fail(self):
        for corruption in ("missing", "duplicate", "owner"):
            with self.subTest(corruption=corruption):
                material, ctx, stage = self.fixture()
                if corruption == "missing":
                    del material[facts.MARKER]
                elif corruption == "duplicate":
                    duplicate = Block("Paint.001", 2, **{facts.MARKER: material[facts.MARKER]})
                    ctx.owned_ids.add(duplicate)
                else:
                    ctx.objects["$a"].material_slots = []
                with self.assertRaises(ProgramError):
                    facts.snapshot_provenance(stage)

    def test_hydration_requires_locator_marker_manifest_and_exact_owner_slots(self):
        material, ctx, stage = self.fixture()
        facts.snapshot_provenance(stage)
        hydrated = Block(material.name, 200, **dict(material))
        fresh = context([hydrated], [hydrated])
        fresh.extras = copy.deepcopy(ctx.extras)
        stage.context, stage.extras, stage.objects = fresh, fresh.extras, fresh.objects
        facts.restore_provenance(stage)
        self.assertTrue(facts.matches_resource(fresh, hydrated, "created:0"))
        fresh.objects["$a"].material_slots = []
        with self.assertRaisesRegex(ProgramError, "owner slots"):
            facts.restore_provenance(stage)

    def test_selected_slot_has_no_artificial_32_slot_limit(self):
        material = Block("Selected", 10)
        obj = SimpleNamespace(material_slots=[SimpleNamespace(material=None, link="DATA") for _ in range(1001)],
                              data=None)
        obj.material_slots[1000].material = material
        binding = {"handles": ("owner",), "originals": {"owner": obj},
                   "program": {"operations": [{"kind": "node_edit", "target": "owner",
                       "parameters": {"material": "Selected", "edits": []}}]}}
        with patch.object(facts, "_users", return_value=((), False, "a" * 64)), \
                patch.object(facts, "_user_maps", return_value=({}, {})):
            observed = facts.collect_material_facts(None, binding)
        self.assertEqual(observed["owners"][0]["slot_count"], 1001)
        self.assertEqual(observed["owners"][0]["slots"][0]["slot"], 1000)
        with self.assertRaisesRegex(ProgramError, "fingerprint budget"):
            facts.collect_material_facts(None, binding, [2, 100])


class LifecycleTests(unittest.TestCase):
    def test_native_dispatch_hooks_preserve_one_call_and_exception_boundary(self):
        from angelo_agent.program_objects import ProgramObjectsMixin
        operation = {"kind": "create_primitive", "target": None, "parameters": {}}
        calls = []
        for fails in (False, True):
            ctx = context([], [])
            stage = SimpleNamespace(context=ctx, extras=ctx.extras, changes={},
                                    binding={"obligations": SimpleNamespace(effects=lambda: [])})
            backend = ProgramObjectsMixin()
            backend._override = lambda stage: contextlib.nullcontext()

            def apply(context, received):
                calls.append(received)
                if fails:
                    raise ProgramError("kernel failed")
                return {"changed": True}

            with patch.dict(sys.modules, {"angelo_agent.action_kernels": SimpleNamespace(apply_operation=apply)}):
                if fails:
                    with self.assertRaisesRegex(ProgramError, "kernel failed"):
                        backend.apply(stage, operation)
                    self.assertNotIn("obligation_operation_index", stage.extras)
                else:
                    self.assertEqual(backend.apply(stage, operation), {"changed": True})
                    self.assertEqual(stage.extras["obligation_operation_index"], 1)
        self.assertEqual(calls, [operation, operation])

    def test_fresh_bind_must_match_approval_before_stage_allocation(self):
        original = program()
        approved = resolve_obligations(original)
        changed = copy.deepcopy(original)
        changed["operations"][0]["parameters"]["location"] = [8, 3, 4]
        backend = Backend()
        backend.bind = lambda requested: {"handles": (), "obligations": resolve_obligations(changed)}
        job = ProgramJob(backend, original, approved_obligations=approved, writer=WriterLease())
        result = job.poll()
        self.assertFalse(result["success"])
        self.assertNotIn("stage", backend.calls)
        self.assertEqual(backend.discards, 0)

    def test_preview_retains_same_bundle_and_rejects_mutable_check_copy(self):
        previews, writer = PreviewStore(), WriterLease()
        job = ProgramJob(Backend(), program("PREVIEW"), previews=previews, writer=writer, owner="owner")
        result = finish(job)
        record = previews.get(result["preview_id"], "owner")
        self.assertIs(record["obligations"], job.obligations)
        self.assertIs(job.completion_obligations(), job.obligations)
        record["checks"].clear()
        with self.assertRaisesRegex(ProgramError, "obligations changed"):
            ProgramJob.accept(record["id"], "owner", previews=previews, writer=writer)
        self.assertIsNone(writer.owner)
        previews.clear()

    def test_completion_needs_actual_completed_positive_rows(self):
        job = ProgramJob(Backend(), program(), writer=WriterLease())
        self.assertIsNone(job.completion_obligations())
        finish(job)
        self.assertIsNone(job.completion_obligations())
        job.commit()
        self.assertIs(job.completion_obligations(), job.obligations)
        job.check_results[0]["passed"] = False
        self.assertIsNone(job.completion_obligations())

    def test_facts_change_is_not_a_frame_rebind(self):
        common = (1, 2, (1, 0), 3, (), None, "same")
        before = ProgramBinding(*common, SimpleNamespace(facts_digest="old"))
        after = ProgramBinding(*common, SimpleNamespace(facts_digest="new"))
        self.assertEqual(precondition_change(before, after)["kind"], "verification")
        self.assertFalse(precondition_change(before, after)["rebindable"])


class QualifiedUnlinkTests(unittest.TestCase):
    def test_exact_destination_precedence_and_source_aliases_preserve_other_edges(self):
        from angelo_agent.program_node_link_checks import verify_absent_links

        class Socket:
            def __init__(self, name, identifier):
                self.name, self.identifier, self.enabled = name, identifier, True

            def as_pointer(self):
                return id(self)

        first, second, source = Socket("Value", "first"), Socket("Value", "second"), Socket("Value", "source-id")
        tree = SimpleNamespace(nodes={"Destination": SimpleNamespace(inputs=[first, second])},
                               links=[SimpleNamespace(to_socket=second, from_socket=source,
                                                      from_node=SimpleNamespace(name="Source"))])
        owner = SimpleNamespace(world=SimpleNamespace(node_tree=tree))
        check = {"kind": "node_graph", "target": "scene", "world": True}

        def verify(destination, **source):
            return verify_absent_links(owner, check, {"absent": [dict(to_node="Destination", to_socket=destination, **source)]})

        self.assertTrue(verify("Value")["passed"])  # The first enabled name, not every equal name.
        self.assertFalse(verify("second", from_node="Source", from_socket="Value")["passed"])
        self.assertFalse(verify("second", from_node="Source", from_socket="source-id")["passed"])
        self.assertTrue(verify("second", from_node="Other")["passed"])
        tree.links.clear()
        self.assertTrue(verify("second", from_node="Source")["passed"])


if __name__ == "__main__":
    unittest.main()
