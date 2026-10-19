# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual collector to pure replay diagnostics, without Blender or scene mutation."""

import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import program_checks, program_material_facts as collector
from angelo_agent.program_checks import resolve_obligations
from angelo_agent.program_material_obligations import _Replay
from angelo_agent.node_edit_spec import NodeEditError
from angelo_agent.refusal_codes import RefusalError, refusal_code
from test_native_material_obligations import A, B, op, material, program, facts as bound_facts


class Material:
    def __init__(self, name, uid, queried=False):
        self.name, self.session_uid = name, uid
        self.queried, self.tree_reads, self.use_fake_user = queried, 0, False

    @property
    def node_tree(self):
        self.tree_reads += 1
        if not self.queried:
            raise AssertionError("Unqueried material graph getter ran")
        return None


class Slots(list):
    walks = 0

    def __iter__(self):
        self.walks += 1
        return super().__iter__()


def collect(request, names):
    queried = {operation["parameters"].get("material", operation["parameters"].get("name"))
               for operation in request["operations"]}
    blocks = [Material(name, index + 1, name in queried) if name is not None else None
              for index, name in enumerate(names)]
    slots = Slots(SimpleNamespace(material=block, link="OBJECT") for block in blocks)
    objects = {A: SimpleNamespace(material_slots=slots, data=None),
               B: SimpleNamespace(material_slots=Slots(), data=None)}
    binding = {"program": request, "handles": (A, B), "originals": objects}
    with patch.object(collector, "_users", return_value=((), False, "a" * 64)), \
            patch.object(collector, "_user_maps", return_value=({}, {})):
        facts = collector.collect_material_facts(None, binding)
    return facts, blocks, slots


def assignment(source=A, name="Missing"):
    return op("material_assign", B, source=source, material=name)


def missing_text(names, requested="Missing"):
    available = sorted({name[:64] for name in names if name is not None})[:8]
    return ("material_assign MATERIAL_NOT_FOUND: the source has no material named %r; its materials: %s"
            % (requested[:64], ", ".join(repr(name) for name in available) or "none"))


def shader(target, shader_id):
    return op("shader_nodes", target, name="Paint", nodes=[
        {"id": shader_id, "type": "BSDF_PRINCIPLED"}, {"id": "out", "type": "OUTPUT_MATERIAL"}],
        links=[{"from_node": shader_id, "from_socket": "BSDF", "to_node": "out", "to_socket": "Surface"}])


class MaterialDiagnosticTests(unittest.TestCase):
    def assert_correctable(self, error):
        """A refusal the model corrects in its next round: the exact RefusalError type (native preparation reads only
        that) with the closed VALIDATION code, never UNKNOWN, which raises the request's recovery barrier."""
        self.assertIs(type(error), RefusalError)
        self.assertEqual(refusal_code(error), "VALIDATION")

    def test_node_lookup_uses_full_name_sort_before_first_eight_and_display_truncation(self):
        for names in (["Paint"], [None, None], ["A" * 64 + "two", "A" * 64 + "one",
                                              "B", "C", "D", "E", "F", "G", "H", "I"]):
            with self.subTest(names=names):
                request = program([op("node_edit", A, material="Missing", edits=[
                    {"op": "remove", "node": "Unused"}])], [A, B])
                facts, blocks, slots = collect(request, names)
                with self.assertRaises(NodeEditError) as caught:
                    resolve_obligations(request, facts)
                expected = sorted({name for name in names if name is not None})[:8]
                self.assertIs(type(caught.exception), NodeEditError)
                self.assertEqual(caught.exception.code, "MATERIAL_NOT_FOUND")
                self.assertEqual(str(caught.exception),
                    "node_edit MATERIAL_NOT_FOUND: no material named 'Missing' on this object; its materials: "
                    + (", ".join(repr(name[:64]) for name in expected) or "none"))
                self.assertEqual(slots.walks, 1)
                self.assertTrue(all(block.tree_reads == 0 for block in blocks if block))

    def test_node_lookup_diagnostics_follow_prior_replacement_and_alias_ancestry(self):
        for alias in (False, True):
            operations = [material(A, "New", slot=0)]
            if alias:
                operations.append(op("object_duplicate", None, source=A, alias="copy"))
            operations.append(op("node_edit", "$copy" if alias else A, material="Paint", edits=[
                {"op": "remove", "node": "Unused"}]))
            request = program(operations, [A, B])
            facts, _blocks, _slots = collect(request, ["Paint", "Other"])
            with self.assertRaises(NodeEditError) as caught:
                resolve_obligations(request, facts)
            self.assertEqual(str(caught.exception),
                "node_edit NEW_OBJECT: build a new object's graph with shader_nodes or geometry_nodes, or edit it in a later program"
                if alias else "node_edit MATERIAL_NOT_FOUND: no material named 'Paint' on this object; its materials: 'New', 'Other'")

    def test_missing_material_on_new_object_keeps_native_error_precedence(self):
        with self.assertRaises(NodeEditError) as caught:
            program([op("create_primitive", None, primitive="CUBE", alias="fresh"),
                     op("node_edit", "$fresh", material="Paint", edits=[
                         {"op": "remove", "node": "Unused"}])], [A, B])
        self.assertIs(type(caught.exception), NodeEditError)
        self.assertEqual(caught.exception.code, "NEW_OBJECT")
        self.assertEqual(str(caught.exception), "node_edit NEW_OBJECT: build a new object's graph with shader_nodes or "
                         "geometry_nodes, or edit it in a later program")

    def test_missing_group_preserves_exact_native_error(self):
        request = program([op("node_edit", A, material="Paint", group="Noise", edits=[
            {"op": "remove", "node": "Unused"}])], [A, B])
        with self.assertRaises(NodeEditError) as caught:
            resolve_obligations(request, bound_facts([A, B], {A: [("Paint", 10)]}))
        self.assertIs(type(caught.exception), NodeEditError)
        self.assertEqual(caught.exception.code, "GROUP_NOT_FOUND")
        self.assertEqual(str(caught.exception), "node_edit GROUP_NOT_FOUND: no group node with a group is named 'Noise'")

    def test_also_on_named_edit_preserves_current_kind_error(self):
        for operation in (material(A), shader(A, "first")):
            operation["parameters"]["also"] = [B]
            request = program([operation], [A, B])
            with self.assertRaises(ValueError) as caught:
                resolve_obligations(request, bound_facts([A, B], {A: [("Paint", 10)]}))
            self.assert_correctable(caught.exception)
            self.assertEqual(str(caught.exception),
                "%s ALSO_NEW_MATERIAL: also assigns a material this operation creates; material_assign gives an existing "
                "material to more objects" % operation["kind"])

    def test_invalid_placement_and_named_edit_slots_preserve_native_messages(self):
        for kind in ("material_principled", "shader_nodes"):
            operation = material(A) if kind == "material_principled" else shader(A, "first")
            operation["parameters"]["slot"] = 1
            request = program([operation], [A, B])
            with self.assertRaises(ValueError) as caught:
                resolve_obligations(request, bound_facts([A, B]))
            self.assert_correctable(caught.exception)
            self.assertEqual(str(caught.exception),
                             "Material slot must be existing or the next append slot, with at most 32 slots")
            held = bound_facts([A, B], {A: [("Paint", 10), ("Other", 11), ("Paint", 10)]})
            with self.assertRaises(ValueError) as caught:
                resolve_obligations(request, held)
            self.assert_correctable(caught.exception)
            self.assertEqual(str(caught.exception),
                "%s: 'Paint' is already in slot 0 of this object and is edited in place; use slot -1 or 0" % kind)
            operation["parameters"]["slot"] = 2
            bundle = resolve_obligations(program([operation], [A, B]), held)
            self.assertEqual(bundle.effects()[0]["slot"], 2)

    def test_present_node_material_omits_diagnostics(self):
        request = program([op("node_edit", A, material="Paint", edits=[
            {"op": "remove", "node": "Unused"}])], [A, B])
        facts, _blocks, _slots = collect(request, ["Other", "Paint"])
        self.assertNotIn("diagnostic_names", facts["owners"][0])

    def test_assignment_keeps_its_own_truncation_order_when_node_lookup_retains_full_names(self):
        names = ["A" * 64 + "one", "A" * 64 + "two", "B", "C", "D", "E", "F", "G", "H"]
        request = program([assignment(), op("node_edit", A, material="Missing", edits=[
            {"op": "remove", "node": "Unused"}])], [A, B])
        facts = self.assert_missing(request, names)
        self.assertEqual(facts["owners"][0]["diagnostic_names"][0][1], names[0])

    def test_changed_user_diagnostic_is_proven_and_commit_refuses_before_hydration(self):
        from angelo_agent import native_programs as native
        from angelo_agent.program_jobs import ProgramJob, WriterLease
        from test_native_program_jobs import Backend, program as basic_program, finish

        request = basic_program()
        before = {"resources": {"live:1": {"name": "Paint", "sharing": {A: {"users_digest": "old"}}}}}
        after = copy.deepcopy(before)
        after["resources"]["live:1"]["sharing"][A]["users_digest"] = "new"
        old, new = resolve_obligations(request, before), resolve_obligations(request, after)
        backend, writer = Backend(), WriterLease()
        source = copy.deepcopy(backend.source)
        job = ProgramJob(backend, request, writer=writer)
        finish(job)
        backend.verify = lambda binding: native.BlenderProgramBackend.verify(
            backend, {"handles": (), "scene": None, "obligations": old})
        with patch.object(native, "resolve_binding", return_value=new), \
                patch.object(backend, "resume_stage", create=True, side_effect=AssertionError("hydrated")) as resumed, \
                patch.object(backend, "create_stage", side_effect=AssertionError("restaged")) as restaged:
            result = job.commit()
        self.assertEqual(result["status"], "failed")
        self.assertIn("users of material 'Paint' changed", result["error"])
        self.assertIn("review the program, and stage it again", result["error"])
        self.assertFalse(result["scene_changed"] or result.get("partial_changes_possible"))
        self.assertFalse(resumed.called or restaged.called)
        self.assertEqual(backend.source, source)
        self.assertEqual(backend.commits, 0)
        self.assertTrue(job.closed)
        self.assertIsNone(writer.owner)
        after["resources"]["live:1"]["sharing"][A]["users_digest"] = "old"
        after["resources"]["live:1"]["name"] = "Other"
        self.assertEqual(collector.binding_change_message(old, resolve_obligations(request, after)),
                         "The bound material verification changed; stage a fresh program")

    def test_redefined_material_keeps_current_kind_and_exact_native_error(self):
        pairs = [(material(A), material(B, color=[.1, .2, .3, 1])),
                 (shader(A, "first"), shader(B, "second")),
                 (material(A), shader(B, "second")), (shader(A, "first"), material(B))]
        for first, second in pairs:
            with self.subTest(kind=second["kind"]):
                request = program([first, second], [A, B])
                facts, _blocks, _slots = collect(request, [])
                with self.assertRaises(ValueError) as caught:
                    resolve_obligations(request, facts)
                self.assert_correctable(caught.exception)
                self.assertEqual(str(caught.exception),
                    "%s MATERIAL_REDEFINED: 'Paint' was stated earlier in this program with other values; state a "
                    "material once and reuse it with material_assign, or choose another name" % second["kind"])

    def test_names_only_reuse_and_same_target_rewrite_remain_admitted(self):
        pairs = [(material(A), material(A, color=[.1, .2, .3, 1])),
                 (shader(A, "first"), shader(A, "second")),
                 (material(A), op("material_principled", B, name="Paint")),
                 (shader(A, "first"), op("material_principled", B, name="Paint"))]
        for first, second in pairs:
            with self.subTest(kind=first["kind"], target=second["target"]):
                request = program([first, second], [A, B])
                facts, _blocks, _slots = collect(request, [])
                bundle = resolve_obligations(request, facts)
                initial, final = bundle.effects()
                self.assertEqual(final["mode"], "edit" if second["target"] == A else "reuse")
                self.assertEqual(final["resource"], initial["resource"])

    def assert_missing(self, request, names, final_names=None, requested="Missing"):
        facts, blocks, slots = collect(request, names)
        with self.assertRaises(ValueError) as caught:
            resolve_obligations(request, facts)
        self.assertEqual(str(caught.exception), missing_text(names if final_names is None else final_names, requested))
        self.assert_correctable(caught.exception)
        self.assertEqual(slots.walks, 1, "Diagnostic collection must reuse the existing slot walk")
        self.assertTrue(all(block.tree_reads == 0 for block in blocks if block and not block.queried))
        self.assertEqual([slot.material for slot in list.__iter__(slots)], blocks)
        return facts

    def test_unqueried_actual_source_name_is_available_without_graph_reads(self):
        facts = self.assert_missing(program([assignment()], [A, B]), ["Paint"])
        self.assertEqual(facts["resources"], {})
        self.assertEqual(facts["owners"][0]["slots"], [])
        self.assertEqual(facts["owners"][0]["diagnostic_names"], [[0, "Paint"]])
        self.assertNotIn("diagnostic_names", facts["owners"][1])

    def test_slot_replacement_uses_final_names_not_initial_or_cached_top_eight(self):
        names = ["Paint", None]
        request = program([material(A, "New", slot=0), assignment()], [A, B])
        self.assert_missing(request, names, ["New"])
        names = ["A%02d" % index for index in range(10)]
        request = program([material(A, "Z0", slot=0), material(A, "Z1", slot=1), assignment()], [A, B])
        self.assert_missing(request, names, ["Z0", "Z1", *names[2:]])

    def test_duplicate_alias_replays_names_on_its_own_slots(self):
        request = program([op("object_duplicate", None, source=A, alias="copy"),
                           material("$copy", "New", slot=0), assignment("$copy")], [A, B])
        self.assert_missing(request, ["Paint"], ["New"])

    def test_preceding_placement_can_remove_an_initially_present_requested_name(self):
        request = program([material(A, "New", slot=0), assignment(name="Paint")], [A, B])
        self.assert_missing(request, ["Paint", "Other"], ["New", "Other"], requested="Paint")
        request = program([material(A, "New", slot=0),
                           op("object_duplicate", None, source=A, alias="copy"),
                           assignment("$copy", "Paint")], [A, B])
        self.assert_missing(request, ["Paint", "Other"], ["New", "Other"], requested="Paint")

    def test_copy_as_replacement_and_also_placement_update_source_diagnostics(self):
        request = program([op("material_assign", A, source=None, material="Paint", copy_as="Copy", slot=0),
                           assignment(name="Paint")], [A, B])
        self.assert_missing(request, ["Paint", "Other"], ["Copy", "Other"], requested="Paint")
        request = program([material(B, "New", also=[A]), assignment(name="Paint")], [A, B])
        facts, _blocks, _slots = collect(request, ["Paint", "Other"])
        bundle = resolve_obligations(request, facts)
        self.assertEqual(bundle.facts()["owners"][0]["diagnostic_names"], [[0, "Paint"], [1, "Other"]])

    def test_batch_aliases_share_initial_diagnostics_without_traversing_or_mutating_them(self):
        from angelo_agent.action_catalog import validate_operation

        class ObservedTuple(tuple):
            walks = 0

            def __iter__(self):
                self.walks += 1
                return super().__iter__()

            def __deepcopy__(self, memo):
                raise AssertionError("Alias replay traversed immutable diagnostic facts")

        request = program([assignment()], [A, B])
        facts, _blocks, _slots = collect(request, ["Paint", "Other"])
        replay = _Replay(request, facts)
        initial = ObservedTuple(replay.owners[A]["diagnostic_names"])
        replay.owners[A]["diagnostic_names"] = initial
        replay.resources["created:initial"] = {"name": "Initial edit"}
        replay.place(A, "created:initial", 1)
        batch = validate_operation(op("batch", None, sources=[A], array=[{"step": [1, 0, 0], "count": 1025}]))
        replay.objects(batch)
        aliases = [owner for target, owner in replay.owners.items() if target.startswith("$")]
        self.assertEqual(len(aliases), 1024)
        self.assertTrue(all(owner["diagnostic_names"] is initial for owner in aliases))
        self.assertTrue(all(owner["diagnostic_replacements"] is replay.owners[A]["diagnostic_replacements"]
                            for owner in aliases))
        self.assertEqual(initial.walks, 0)
        replay.resources["created:test"] = {"name": "New"}
        replay.place("$copy1_0_2", "created:test", 0)
        self.assertEqual(replay.owners["$copy1_0_2"]["diagnostic_replacements"], {0: "New", 1: "Initial edit"})
        self.assertIsNot(replay.owners["$copy1_0_2"]["diagnostic_replacements"],
                         replay.owners[A]["diagnostic_replacements"])
        self.assertEqual(replay.owners[A]["diagnostic_replacements"], {1: "Initial edit"})
        self.assertEqual(replay.owners["$copy1_0_3"]["diagnostic_replacements"], {1: "Initial edit"})
        self.assertEqual(initial.walks, 0)

    def test_truncation_precedes_deduplication_sorting_and_first_eight(self):
        names = ["Z", "a" * 64 + "two", "A", "a" * 64 + "one", "雪" * 65,
                 "B", "C", "D", "E", "F", "G", "H", None, "A", "\"quoted\n"]
        self.assert_missing(program([assignment()], [A, B]), names)
        self.assert_missing(program([assignment()], [A, B]), [None, None], [])

    def test_present_source_omits_diagnostics_even_after_provisional_overflow(self):
        request = program([assignment(name="Paint")], [A, B])
        with patch.object(program_checks, "MAX_CHECK_BYTES", 90):
            facts, _blocks, slots = collect(request, ["Unqueried" * 8] * 20 + ["Paint"])
        self.assertEqual(slots.walks, 1)
        self.assertNotIn("diagnostic_names", facts["owners"][0])
        bundle = resolve_obligations(request, facts)
        self.assertNotIn(b"diagnostic_names", bundle.facts_encoded)

    def test_diagnostics_share_existing_byte_leaf_and_final_combined_limits(self):
        request = program([assignment()], [A, B])
        for limit, value in (("MAX_CHECK_BYTES", 90), ("MAX_CHECK_LEAVES", 3)):
            with self.subTest(limit=limit), patch.object(program_checks, limit, value):
                with self.assertRaisesRegex(ValueError, "^PROGRAM_CHECK_BUDGET:"):
                    collect(request, ["\x01雪\\\"" * 16, "Another"])
        accounting = [0, 0]
        diagnostic = collector._DiagnosticNames({"Missing"}, accounting)
        diagnostic.observe(7, "\x01雪\\\"" * 16)
        pair = diagnostic.names[0]
        self.assertEqual(accounting, [len(json.dumps(pair, ensure_ascii=False,
                                                      separators=(",", ":")).encode("utf-8")) + 1, 2])
        facts, _blocks, _slots = collect(request, ["Paint"])
        with patch.object(program_checks, "MAX_CHECK_BYTES", 100):
            with self.assertRaisesRegex(ValueError, "^PROGRAM_CHECK_BUDGET:"):
                resolve_obligations(request, facts)

    def test_initially_missing_name_created_later_keeps_budgeted_diagnostic_facts(self):
        request = program([material(A, "Missing", slot=0), assignment()], [A, B])
        facts, _blocks, _slots = collect(request, ["Paint"])
        bundle = resolve_obligations(request, facts)
        self.assertEqual(bundle.facts()["owners"][0]["diagnostic_names"], [[0, "Paint"]])
        bundle.assert_integrity(request)


if __name__ == "__main__":
    unittest.main()
