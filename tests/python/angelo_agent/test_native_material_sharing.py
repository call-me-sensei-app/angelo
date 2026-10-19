# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``material_assign`` and ``shared: ALL_USERS``: schemas, bindings, derived checks and what a model is told.

Dependency-free. Staging, commit, the users an edit reaches, Undo, Redo,
rollback and stale-state rejection are covered by
test_blender_material_sharing.py in the compiled app.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.program_checks import required_checks
from angelo_agent.action_catalog import (affected_references, resource_dependencies, validate_operation,  # noqa: E402
                                         written_references)
from angelo_agent import material_rules  # noqa: E402
from angelo_agent.claude_outcomes import certified_completion, compact_receipt, terse_receipt  # noqa: E402
from angelo_agent.derived_checks import derive_checks  # noqa: E402
from angelo_agent.node_edit_spec import NodeEditError  # noqa: E402
from angelo_agent.plan_schema import CLAUDE_CORE_ACTIONS, DEFERRED_ACTIONS, lookup  # noqa: E402
from angelo_agent.program_spec import validate_program  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_round_savings import committed, program  # noqa: E402
from test_native_complete_checks import material_binding_facts  # noqa: E402

OBJ = "scn:" + "a" * 32 + ":7"
SOURCE = "scn:" + "a" * 32 + ":9"
REV = "b" * 64


def op(kind, target=OBJ, **parameters):
    return {"kind": kind, "target": target, "parameters": parameters}


def edit(**parameters):
    return op("node_edit", edits=[{"op": "set", "node": "Principled BSDF", "inputs": {"Roughness": 0.2}}], **parameters)


class MaterialAssignTests(unittest.TestCase):
    def test_schema_defaults_and_references(self):
        assign = validate_operation(op("material_assign", material="Iron", source=SOURCE))
        self.assertEqual(assign["parameters"], {"material": "Iron", "source": SOURCE, "slot": -1, "face_indices": [],
                                                 "copy_as": None, "also": [], "index_scope": "INITIAL",
                                                 "topology_step": 0, "face_selector": None})
        self.assertEqual(affected_references(assign), (OBJ, SOURCE))
        self.assertEqual(written_references(assign), (OBJ,), "The source is only read")
        self.assertEqual(affected_references(op("material_assign", material="Iron")), (OBJ,))
        self.assertEqual(resource_dependencies(assign)["mutates"], ["object", "mesh", "materials"])

    def test_refusals(self):
        for operation in (op("material_assign"), op("material_assign", material=""),
                          op("material_assign", material="Iron", source=OBJ),
                          op("material_assign", material="Iron", copy_as="Iron"),
                          op("material_assign", material="Iron", source="Some Object Name!"),
                          op("material_assign", material="Iron", path="/tmp/x.blend"),
                          op("material_assign", None, material="Iron")):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                validate_operation(operation)

    def test_the_source_is_a_declared_object(self):
        program = {"mode": "APPLY", "write_set": [{"handle": OBJ, "revision": REV}],
                   "operations": [op("material_assign", material="Iron", source=SOURCE)]}
        with self.assertRaisesRegex(ValueError, "must appear in the read or write set"):
            validate_program(program)
        program["read_set"] = [{"handle": SOURCE, "revision": REV}]
        self.assertEqual(validate_program(program)["operations"][0]["parameters"]["source"], SOURCE)

    def test_derived_check_names_the_material_commit_gives(self):
        (check,) = derive_checks([validate_operation(op("material_assign", material="Iron", source=SOURCE))])
        self.assertEqual(check, {"kind": "material_assignment", "target": OBJ, "material": "Iron", "all_faces": False})
        (check,) = derive_checks([validate_operation(op("material_assign", material="Iron", copy_as="Iron Dark"))])
        self.assertEqual(check["material"], "Iron Dark")

    def test_assignment_keeps_transform_checks_and_drops_graph_checks(self):
        create = {"kind": "create_primitive", "target": None,
                  "parameters": {"primitive": "CUBE", "name": "Post", "alias": "post", "location": [1, 2, 3]}}
        kinds = [check["kind"] for check in derive_checks([validate_operation(create), validate_operation(
            op("material_assign", "$post", material="Iron", source=SOURCE))])]
        self.assertEqual(kinds, ["object_name", "transform", "dimensions", "material_assignment"])
        kinds = [check["kind"] for check in derive_checks([validate_operation(edit(material="Iron")), validate_operation(
            op("material_assign", material="Brass", source=SOURCE))])]
        self.assertEqual(kinds, ["material_assignment"], "The edited slot may hold another material now")

    def test_it_is_exact_for_claude_and_deferred_for_codex(self):
        self.assertIn("material_assign", CLAUDE_CORE_ACTIONS)
        self.assertIn("material_assign", DEFERRED_ACTIONS)
        (exact,) = lookup("ACTIONS", ["material_assign"])["actions"]
        self.assertEqual(exact["parameters"]["required"], ["material"])
        self.assertEqual(exact["mutates"], ["object", "mesh", "materials"])


NODES = {"nodes": [{"id": "bsdf", "type": "BSDF_PRINCIPLED"}, {"id": "out", "type": "OUTPUT_MATERIAL"}],
         "links": [{"from_node": "bsdf", "from_socket": "BSDF", "to_node": "out", "to_socket": "Surface"}]}


class AlsoTests(unittest.TestCase):
    """One material on several objects: the also field, and a name alone after the material was stated."""

    def test_the_three_material_kinds_take_also(self):
        for kind, parameters in (("material_principled", {"name": "Iron"}), ("shader_nodes", dict(NODES, name="Iron")),
                                 ("material_assign", {"material": "Iron"})):
            with self.subTest(kind=kind):
                self.assertEqual(validate_operation(op(kind, **parameters))["parameters"]["also"], [])
                several = validate_operation(op(kind, also=["$rail", SOURCE], **parameters))
                self.assertEqual(affected_references(several), (OBJ, "$rail", SOURCE))
                self.assertEqual(written_references(several), (OBJ, "$rail", SOURCE), "Every also object is written")
                for refused in ([OBJ], ["$rail", "$rail"], ["$part%d" % index for index in range(32)], ["Some Object Name!"], "$rail"):
                    with self.subTest(also=refused), self.assertRaises(ValueError):
                        validate_operation(op(kind, also=refused, **parameters))
        self.assertEqual(material_rules.MAX_ALSO, 31)

    def test_an_also_object_is_bound_like_a_target(self):
        program = {"mode": "APPLY", "write_set": [{"handle": OBJ, "revision": REV}],
                   "operations": [op("material_principled", name="Iron", also=[SOURCE])]}
        with self.assertRaisesRegex(ValueError, "must appear in the write set"):
            validate_program(dict(program, read_set=[{"handle": SOURCE, "revision": REV}]))
        program["write_set"].append({"handle": SOURCE, "revision": REV})
        validate_program(program)

    def test_every_also_object_gets_its_assignment_check(self):
        checks = derive_checks([validate_operation(op("material_principled", name="Iron", inputs={"Roughness": .4},
                                                      also=["$rail", SOURCE]))])
        self.assertEqual([(check["kind"], check["target"]) for check in checks],
                         [("material_assignment", OBJ), ("material_values", OBJ), ("material_assignment", "$rail"),
                          ("material_assignment", SOURCE)])
        self.assertEqual({check["material"] for check in checks}, {"Iron"})
        copied = derive_checks([validate_operation(op("material_assign", material="Iron", copy_as="Iron Dark",
                                                      also=["$rail"]))])
        self.assertEqual([(check["target"], check["material"]) for check in copied],
                         [(OBJ, "Iron Dark"), ("$rail", "Iron Dark")])

    def test_a_name_alone_after_its_statement_is_an_assignment(self):
        stated = validate_operation(op("material_principled", name="Iron", inputs={"Roughness": 0.4}))
        again = validate_operation(op("material_principled", "$rail", name="Iron"))
        self.assertTrue(material_rules.names_only(again))
        self.assertFalse(material_rules.names_only(stated))
        self.assertFalse(material_rules.names_only(validate_operation(op("material_assign", material="Iron"))))
        kinds = [(check["kind"], check["target"]) for check in derive_checks([stated, again])]
        self.assertEqual(kinds, [("material_assignment", OBJ), ("material_values", OBJ), ("material_assignment", "$rail")],
                         "The values are the first statement's")
        # Without an earlier statement a name alone makes Blender's default material and states no value
        # (action_schemas.SETTINGS_NOTE): only its assignment is checked.
        alone = [check["kind"] for check in derive_checks([again])]
        self.assertEqual(alone, ["material_assignment"])
        other = [check["kind"] for check in derive_checks([stated, validate_operation(
            op("material_principled", "$rail", name="Brass", inputs={"Metallic": 1.0}))])]
        self.assertEqual(other.count("material_values"), 2)


class SharedPolicyTests(unittest.TestCase):
    def test_material_kinds_take_refuse_or_all_users(self):
        for kind, parameters in (("material_principled", {"name": "Iron"}),
                                 ("shader_nodes", {"name": "Iron", "nodes": [
                                     {"id": "bsdf", "type": "BSDF_PRINCIPLED"}, {"id": "out", "type": "OUTPUT_MATERIAL"}],
                                     "links": [{"from_node": "bsdf", "from_socket": "BSDF", "to_node": "out",
                                                "to_socket": "Surface"}]})):
            with self.subTest(kind=kind):
                self.assertEqual(validate_operation(op(kind, **parameters))["parameters"]["shared"], "REFUSE")
                self.assertEqual(validate_operation(op(kind, shared="ALL_USERS", **parameters))["parameters"]["shared"],
                                 "ALL_USERS")
                with self.assertRaises(ValueError):
                    validate_operation(op(kind, shared="ISOLATE", **parameters))

    def test_node_edit_edits_only_a_materials_own_tree_for_all_users(self):
        self.assertEqual(validate_operation(edit(material="Iron", shared="ALL_USERS"))["parameters"]["shared"], "ALL_USERS")
        for parameters in ({"modifier": "Scatter"}, {"world": True}, {"material": "Iron", "group": "Rust"}):
            with self.subTest(parameters=parameters), self.assertRaises(NodeEditError) as refused:
                validate_operation(edit(shared="ALL_USERS", **parameters))
            self.assertEqual(refused.exception.code, "ALL_USERS_MATERIAL_ONLY")
        self.assertEqual(validate_operation(edit(modifier="Scatter", shared="ISOLATE"))["parameters"]["shared"], "ISOLATE")

    def test_a_shared_edit_binds_only_its_target(self):
        program = validate_call("angelo_scene_program", {"mode": "APPLY", "write_set": [{"handle": OBJ, "revision": REV}],
                                                         "operations": [op("material_principled", name="Iron",
                                                                           shared="ALL_USERS")]})
        self.assertEqual(program["read_set"], [])
        # A new material stating no value: the graph it gets (Principled BSDF into the output) is what is checked.
        self.assertEqual([check["kind"] for check in required_checks(program, material_facts=material_binding_facts(program))],
                         ["material_assignment", "node_graph"])


def shared_result(rows):
    """A committed, host-certified program result with one edit for all users."""
    result = committed()
    result["shared_edits"] = rows
    result["live_revisions"]["objects"].append({"handle": SOURCE, "name": "Post", "type": "MESH", "role": "SHARED",
                                                "object_revision": "d" * 64})
    return result


class EvidenceTests(unittest.TestCase):
    """Both runtimes are told which objects an edit for all users changed, and their new revisions."""

    def test_summary_and_receipts_name_the_users(self):
        result = shared_result([{"material": "Iron", "users": ["Cap", "Post", "Rail"], "count": 3, "truncated": False}])
        wanted = "material 'Iron' edited for all 3 users ('Cap', 'Post', 'Rail')"
        # The summary a Codex plan turn ends with and a Claude plan's person row.
        self.assertIn(wanted, certified_completion([program()], [result], read_only=lambda name: False))
        terse = terse_receipt("angelo_scene_program", result)  # A Claude context's handoff entry.
        self.assertIn(wanted, terse["verified"])
        self.assertEqual([row["name"] for row in terse["live"]], ["Eval Cube", "Post"])
        receipt = compact_receipt("angelo_scene_program", result)  # A Codex thread's handoff entry.
        self.assertEqual(receipt["shared_edits"], [{"material": "Iron", "users": ["Cap", "Post", "Rail"], "count": 3}])
        self.assertEqual([row["role"] for row in receipt["live_revisions"]["objects"]], ["CREATED", "SHARED"])

    def test_many_users_are_counted_not_listed(self):
        users = ["Post %02d" % index for index in range(1, 33)]
        result = shared_result([{"material": "Iron", "users": users, "count": 40, "truncated": True}])
        text = terse_receipt("angelo_scene_program", result)["verified"]
        self.assertIn("edited for all 40 users ('Post 01', ", text)
        self.assertIn("'Post 08' and 32 more)", text)
        self.assertEqual(len(compact_receipt("angelo_scene_program", result)["shared_edits"][0]["users"]), 8)

    def test_malformed_rows_are_ignored(self):
        for rows in (None, "Iron", [{"material": "Iron"}], [{"material": 3, "users": [], "count": 0}]):
            with self.subTest(rows=rows):
                self.assertNotIn("shared_edits", compact_receipt("angelo_scene_program", shared_result(rows)))
                self.assertNotIn("edited for all", terse_receipt("angelo_scene_program", shared_result(rows))["verified"])


class _Clone(dict):
    """A staged material clone: ID properties are its keys."""

    def __init__(self, name, **markers):
        super().__init__(markers)
        self.name = name


class PropertyPathSharingTests(unittest.TestCase):
    """property_set's active_material paths write the live material in place."""

    def context(self, **row):
        from types import SimpleNamespace
        from angelo_agent.material_sharing import PROPERTY_SHARING
        base = {"target": OBJ, "uid": 41, "material": "Paint", "others": 2, "who": "Fence B, Gate", "outside": None,
                "users": ["Fence A", "Fence B", "Gate"], "count": 3, "other_users": ["Fence B", "Gate"],
                "other_count": 2, "users_digest": "d" * 64}
        return SimpleNamespace(extras={PROPERTY_SHARING: [dict(base, **row)]})

    def code(self, context, clone, shared=None):
        from angelo_agent.material_sharing import check_property_edit
        from angelo_agent.rna_rules import PropertyPathError
        try:
            check_property_edit(context, OBJ, clone, "active_material.roughness", shared)
        except PropertyPathError as error:
            return error.code, error.refusal_code, str(error)
        return None

    def test_a_shared_material_is_refused_unless_for_all_users(self):
        from angelo_agent.material_sharing import PROPERTY_EDITS
        from angelo_agent.node_edit_spec import SOURCE
        clone = _Clone("Paint.001", **{SOURCE: 41})
        context = self.context()
        code, policy, text = self.code(context, clone)
        self.assertEqual((code, policy), ("MATERIAL_SHARED", "VALIDATION"), "Correctable: the remedy is a parameter")
        self.assertIn("shared ALL_USERS", text)
        self.assertIn("Fence B, Gate", text)
        self.assertIn("copy_as", text)
        self.assertIsNone(self.code(context, clone, "ALL_USERS"))
        self.assertEqual(context.extras[PROPERTY_EDITS]["41"]["users"], ["Fence A", "Fence B", "Gate"])
        self.assertIsNone(self.code(self.context(others=0), clone), "A material only its object uses")

    def test_users_it_cannot_name_and_unbound_materials_are_refused(self):
        from angelo_agent.node_edit_spec import EDITED, SOURCE
        outside = self.code(self.context(outside="object 'Far' outside this scene"), _Clone("P", **{SOURCE: 41}), "ALL_USERS")
        self.assertEqual(outside[:2], ("SHARED_OUTSIDE_SCENE", "UNSUPPORTED"))
        self.assertIn("Far", outside[2])
        self.assertEqual(self.code(self.context(), _Clone("Other", **{SOURCE: 99}))[:2], ("SHARING_UNKNOWN", "UNSUPPORTED"),
                         "A material the program was not bound with (an earlier step replaced it)")
        self.assertEqual(self.code(self.context(), _Clone("Edited", **{EDITED: 41}))[0], "SHARING_UNKNOWN",
                         "A material an earlier step edited")
        self.assertIsNone(self.code(self.context(), _Clone("New")), "Made by this program: nothing else uses it")

    def test_all_users_needs_a_material_path_and_the_binding_is_native(self):
        from angelo_agent.program_material_obligations import needs_material_binding
        canonical = validate_operation(op("property_set", values={"active_material.roughness": .2}, shared="ALL_USERS"))
        self.assertEqual(canonical["parameters"]["shared"], "ALL_USERS")
        self.assertNotIn("shared", validate_operation(op("property_set", values={"hide_render": True}))["parameters"],
                         "Left out, nothing is added to the canonical operation")
        with self.assertRaisesRegex(ValueError, "ALL_USERS_MATERIAL_ONLY"):
            validate_operation(op("property_set", values={"hide_render": True}, shared="ALL_USERS"))
        with self.assertRaises(ValueError, msg="Only ALL_USERS is spelled"):
            validate_operation(op("property_set", values={"active_material.roughness": .2}, shared="REFUSE"))
        self.assertTrue(needs_material_binding({"operations": [op("property_set", values={"active_material.roughness": .2})]}),
                        "Its users are bound natively, like the material kinds")
        self.assertFalse(needs_material_binding({"operations": [op("property_set", values={"hide_render": True})]}))
        self.assertFalse(needs_material_binding({"operations": [op("property_set", "$new", values={
            "active_material.roughness": .2})]}), "A new object's material has no live users")

    def test_a_changed_user_set_is_named_in_the_stale_message(self):
        from types import SimpleNamespace
        from angelo_agent.material_sharing import PROPERTY_FACTS
        from angelo_agent.program_material_facts import binding_change_message
        row = {"target": OBJ, "uid": 41, "material": "Paint", "others": 1, "users": ["A", "B"], "users_digest": "a",
               "outside": None}
        before = SimpleNamespace(facts=lambda: {PROPERTY_FACTS: [row]})
        after = SimpleNamespace(facts=lambda: {PROPERTY_FACTS: [dict(row, others=2, users=["A", "B", "C"],
                                                                       users_digest="b")]})
        self.assertIn("users of material 'Paint' changed", binding_change_message(before, after))
        self.assertNotIn("Paint", binding_change_message(before, before))


    def test_a_linked_material_is_refused_with_the_local_route(self):
        """Regression: an edit of a library material was verified and lost on save and reopen."""
        from angelo_agent.node_edit_spec import SOURCE
        for shared in (None, "ALL_USERS"):
            code, policy, text = self.code(self.context(linked=True, others=0), _Clone("LibMat", **{SOURCE: 41}), shared)
            self.assertEqual((code, policy), ("LINKED_MATERIAL", "UNSUPPORTED"), "A coded, correctable refusal")
            self.assertIn("copy_as", text)
            self.assertIn("material_principled", text)

    def test_a_library_override_copy_is_refused_before_the_private_copy_rule(self):
        """Regression: an override failed as INTERNAL_ERROR with partial changes possible."""
        from angelo_agent.node_edit_spec import SOURCE
        override = _Clone("LibMat.001")
        override.override_library = object()
        code, policy, text = self.code(self.context(), override)
        self.assertEqual((code, policy), ("LINKED_MATERIAL", "UNSUPPORTED"))
        self.assertIn("cannot edit or copy", text, "An override's advice never suggests the copy that fails for it")
        self.assertNotIn("copy_as", text)
        code, _policy, text = self.code(self.context(linked=True, others=0), _Clone("LibMat", **{SOURCE: 41}))
        self.assertIn("into its active slot (slot: that slot's index)", text)

    def test_a_slot_change_beside_material_paths_is_refused_before_staging(self):
        """Regression: the paths followed the slot change and failed as VALUE_NOT_RETAINED."""
        with self.assertRaisesRegex(ValueError, "ACTIVE_SLOT_CONFLICT"):
            validate_operation(op("property_set", values={"active_material_index": 1, "active_material.roughness": .9}))
        from angelo_agent.rna_rules import PropertyPathError
        try:
            validate_operation(op("property_set", values={"active_material_index": 1, "active_material.metallic": .5}))
        except PropertyPathError as error:
            self.assertEqual(error.refusal_code, "VALIDATION")
        validate_operation(op("property_set", values={"active_material_index": 1}))  # Alone, as before.

    def test_a_slot_change_in_another_operation_is_refused_with_accurate_guidance(self):
        """Regression: separate operations failed as PROGRAM_CHECK_BUDGET telling them to separate."""
        from angelo_agent.program_spec import validate_program
        from angelo_agent.rna_rules import PropertyPathError
        index, roughness = (op("property_set", values={"active_material_index": 1}),
                            op("property_set", values={"active_material.roughness": .9}))
        for operations in ([index, roughness], [roughness, index],
                           [index, dict(roughness, parameters=dict(roughness["parameters"], shared="ALL_USERS"))]):
            with self.subTest(order=[operation["parameters"]["values"] for operation in operations]):
                with self.assertRaises(PropertyPathError) as raised:
                    validate_program({"mode": "APPLY", "operations": operations,
                                      "write_set": [{"handle": OBJ, "revision": "r" * 64}]})
                self.assertEqual((raised.exception.code, raised.exception.refusal_code),
                                 ("ACTIVE_SLOT_CONFLICT", "VALIDATION"))
                self.assertIn("separate program", str(raised.exception))
                self.assertNotIn("PROGRAM_CHECK_BUDGET", str(raised.exception))
        other = dict(roughness, target="scn:" + "c" * 32 + ":3")
        validate_program({"mode": "APPLY", "operations": [index, other], "write_set": [
            {"handle": OBJ, "revision": "r" * 64}, {"handle": other["target"], "revision": "r" * 64}]})

    def test_a_linked_material_is_refused_at_binding_before_approval(self):
        """Regression: LINKED_MATERIAL came only at staging, after an approval naming its users."""
        from angelo_agent.material_sharing import refuse_linked
        from angelo_agent.rna_rules import PropertyPathError
        program = {"operations": [op("property_set", values={"hide_render": False}),
                                  op("property_set", values={"active_material.roughness": .2})]}
        refuse_linked(program, [{"target": OBJ, "material": "Paint"}])  # A local material: nothing to refuse.
        with self.assertRaises(PropertyPathError) as raised:
            refuse_linked(program, [{"target": OBJ, "material": "LibMat", "linked": True}])
        self.assertEqual((raised.exception.code, raised.exception.refusal_code, raised.exception.path),
                         ("LINKED_MATERIAL", "UNSUPPORTED", "active_material.roughness"))


class SharedApprovalTests(unittest.TestCase):
    """An approval names who an edit for all users changes before anything is staged."""

    def facts(self):
        from angelo_agent.material_sharing import PROPERTY_FACTS
        return {"owners": [{"target": OBJ, "slot_count": 1, "slots": [{"slot": 0, "resource": "live:41", "name": "Iron",
                                                                         "link": "DATA"}]}],
                "resources": {"live:41": {"uid": 41, "name": "Iron", "sharing": {OBJ: {
                    "others": 3, "outside": False, "users_digest": "d", "data_shared": False,
                    "other_users": ["Cap", "Lamp", "Post"], "other_count": 3}}}},
                PROPERTY_FACTS: [{"target": SOURCE, "uid": 7, "material": "Paint", "others": 1, "who": "Gate",
                                  "outside": None, "users": ["Fence", "Gate"], "count": 2, "other_users": ["Gate"],
                                  "other_count": 1}]}

    def test_review_rows_and_warning(self):
        from angelo_agent.approvals import _shared_review, approval_message
        operations = [op("material_principled", name="Iron", shared="ALL_USERS"),
                      op("property_set", SOURCE, values={"active_material.roughness": .2}, shared="ALL_USERS"),
                      op("node_edit", material="Iron", shared="ISOLATE"),
                      op("material_principled", OBJ, name="Created Here", shared="ALL_USERS"),
                      op("property_set", "$new", values={"active_material.roughness": .2}, shared="ALL_USERS")]
        rows = _shared_review(self.facts(), operations)
        self.assertEqual(rows, [{"step": 1, "material": "Iron", "other_users": ["Cap", "Lamp", "Post"], "other_count": 3},
                                {"step": 2, "material": "Paint", "other_users": ["Gate"], "other_count": 1},
                                {"step": 4, "material": "Created Here", "other_users": None, "other_count": None}],
                         "Only edits for all users are listed; unknown users are still disclosed")
        message = approval_message("angelo_scene_program", {"mode": "APPLY", "operations": operations,
                                                            "shared_edits": rows})
        self.assertIn("Step 1 also changes material “Iron” on 3 other objects: “Cap”, “Lamp”, “Post”.", message["warning"])
        self.assertIn("Step 2 also changes material “Paint” on 1 other object: “Gate”.", message["warning"])
        self.assertIn("Step 4 changes material “Created Here” for every object that uses it.", message["warning"])

    def test_data_no_object_uses_is_named_too(self):
        """Regression: an orphan mesh with a fake user changed unnamed under an edit for all users."""
        from angelo_agent.approvals import _shared_lines, _shared_review
        from angelo_agent.claude_outcomes import compact_receipt, terse_receipt
        facts = self.facts()
        facts["resources"]["live:41"]["sharing"][OBJ].update(other_users=[], other_count=0, holders=["Orphan Mesh"])
        rows = _shared_review(facts, [op("material_principled", name="Iron", shared="ALL_USERS")])
        self.assertEqual(rows, [{"step": 1, "material": "Iron", "other_users": [], "other_count": 0,
                                 "holders": ["Orphan Mesh"]}])
        self.assertEqual(_shared_lines(rows), ["Step 1 also changes it in data no object uses: “Orphan Mesh”."])
        result = shared_result([{"material": "Iron", "users": ["Cube"], "count": 1, "truncated": False,
                                 "holders": ["Orphan Mesh"]}])
        self.assertIn("in data no object uses ('Orphan Mesh')", terse_receipt("angelo_scene_program", result)["verified"])
        self.assertEqual(compact_receipt("angelo_scene_program", result)["shared_edits"][0]["holders"], ["Orphan Mesh"])

    def test_long_lists_are_bounded_with_a_count(self):
        from angelo_agent.approvals import _shared_lines
        names = ["Post %02d" % index for index in range(32)]
        line, = _shared_lines([{"step": 2, "material": "Iron", "other_users": names, "other_count": 40}])
        self.assertIn("“Post 07” and 32 more", line)
        self.assertNotIn("Post 08", line)
        lines = _shared_lines([{"step": index, "material": "M", "other_users": ["A"], "other_count": 1}
                               for index in range(1, 7)])
        self.assertEqual(len(lines), 5)
        self.assertIn("2 more shared material edits", lines[-1])


class TopologyVerificationTests(unittest.TestCase):
    """A topology edit no check measures is an unverified effect."""

    def program(self, checks=()):
        return validate_program("angelo_scene_program", {"mode": "APPLY", "read_set": [],
            "write_set": [{"handle": OBJ, "revision": REV}], "expected_checks": list(checks), "operations": [
                op("create_primitive", None, primitive="CUBE", name="Marker", alias="m", size=1, location=[4, 0, 0]),
                op("mesh_extrude", face_selector={"normal": [0, 0, 1]}, translation=[0, 0, 5.0])]})

    def test_unmeasured_extrude_is_partial_and_never_certified(self):
        from angelo_agent.program_checks import resolve_obligations
        from angelo_agent.program_completion import pure_program_checks
        bundle = resolve_obligations(self.program())
        self.assertEqual([(row["operation"], row["reason"], row["root"]) for row in bundle.unknowns()],
                         [(1, "unchecked_geometry_edit", {"target": OBJ})])
        self.assertIsNone(pure_program_checks(self.program()), "Partial obligations certify nothing")

    def test_a_geometry_check_of_the_object_completes_it(self):
        from angelo_agent.program_checks import resolve_obligations
        counts = {key: {"minimum": 0, "maximum": 100} for key in ("vertices", "edges", "faces")}
        dimensions = {"kind": "dimensions", "target": OBJ, "expected": [2, 2, 7], "space": "WORLD", "tolerance": .001}
        self.assertEqual(resolve_obligations(self.program([dimensions])).unknowns(), [],
                         "Its new faces are derived from its staged result; dimensions see its displacement")
        self.assertTrue(resolve_obligations(self.program([{"kind": "mesh_counts", "target": OBJ, **counts}])).unknowns(),
                        "Counts alone cannot see the displacement")
        other = {"kind": "dimensions", "target": "$m", "expected": [1, 1, 1], "space": "WORLD", "tolerance": .001}
        self.assertTrue(resolve_obligations(self.program([other])).unknowns(), "Another object's check measures nothing here")


if __name__ == "__main__":
    unittest.main()
