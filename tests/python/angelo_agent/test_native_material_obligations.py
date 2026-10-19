# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private resource replay tests. Plain binding facts do not prove native RNA behavior."""

import copy
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.program_checks import (NeedsMaterialBinding, needs_material_binding, required_checks,
                                        resolve_obligations)
from angelo_agent.program_material_obligations import GUARD
from angelo_agent.node_link_obligations import LINK_GUARD
from angelo_agent.program_spec import validate_program
from angelo_agent.validation_spec import validate_check
from angelo_agent import material_rules, program_checks

A, B, C = ["scn:" + "a" * 32 + ":%d" % index for index in range(1, 4)]
RED, BLUE = [.8, .05, .05, 1], [.05, .1, .8, 1]


def op(kind, target, **parameters):
    return {"kind": kind, "target": target, "parameters": parameters}


def cube(alias):
    return op("create_primitive", None, primitive="CUBE", alias=alias, name=alias.upper())


def material(target, name="Paint", color=RED, **parameters):
    return op("material_principled", target, name=name, inputs={"Base Color": color, "Roughness": .2}, **parameters)


def node(target, inputs, **parameters):
    return op("node_edit", target, material="Paint", edits=[
        {"op": "set", "node": "Principled BSDF", "inputs": inputs}], **parameters)


def program(operations, owners=(), checks=()):
    return validate_program({"mode": "APPLY", "operations": operations, "expected_checks": list(checks),
                             "write_set": [{"handle": target, "revision": "b" * 64} for target in owners]})


def facts(owners, slots=None):
    slots = slots or {}
    resources = {}
    rows = []
    for target in owners:
        assigned = []
        for index, (name, uid) in enumerate(slots.get(target, [])):
            token = "live:%d" % uid
            resources.setdefault(token, {"uid": uid, "name": name, "principled": "Principled BSDF",
                                          "bsdf": {"count": 1, "name": "Principled BSDF", "linked": []},
                                          "groups": {}, "sharing": {}})
            assigned.append({"slot": index, "resource": token, "name": name, "link": "OBJECT"})
        rows.append({"target": target, "slot_count": len(assigned), "slots": assigned})
    for token, resource in resources.items():
        holders = [row["target"] for row in rows if any(entry["resource"] == token for entry in row["slots"])]
        resource["sharing"] = {target: {"others": len(holders) - 1, "data_shared": False,
                                        "outside": False, "users_digest": "test"} for target in holders}
    return {"owners": rows, "resources": resources}


def checks_of(checks, kind):
    return [check for check in checks if check["kind"] == kind]


class MaterialObligationsTests(unittest.TestCase):
    def test_early_material_refusals_preserve_existing_error_surfaces(self):
        for source in (None, A):
            request = program([op("material_assign", B, material="Missing", source=source)], [A, B])
            with self.assertRaisesRegex(ValueError, "^material_assign MATERIAL_NOT_FOUND:"):
                resolve_obligations(request, facts([A, B], {A: [("Paint", 1)]}))
    def test_created_shared_rewrite_has_one_complete_blue_state_and_identity_on_both_owners(self):
        request = program([cube("a"), cube("b"), material("$a", also=["$b"]),
                           material("$b", color=BLUE, shared="ALL_USERS")])
        bundle = resolve_obligations(request)
        checks = bundle.checks()
        values = checks_of(checks, "material_values")
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0]["base_color"], BLUE)
        assignments = checks_of(checks, "material_assignment")
        self.assertEqual({check["target"] for check in assignments}, {"$a", "$b"})
        self.assertEqual({check[GUARD]["resource"] for check in assignments}, {"created:2"})
        self.assertFalse(bundle.requires_binding)

    def test_unrelated_created_resource_never_coalesces_missing_live_uid(self):
        request = program([cube("a"), cube("b"), cube("c"), material("$a", also=["$b"]),
                           material("$c", "Green", [0, 1, 0, 1]),
                           material("$b", color=BLUE, shared="ALL_USERS")])
        checks = required_checks(request)
        green = next(check for check in checks_of(checks, "material_assignment") if check["target"] == "$c")
        self.assertEqual(green["material"], "Green")
        self.assertEqual(green[GUARD]["resource"], "created:4")
        self.assertEqual({check["material"]: check["base_color"] for check in checks_of(checks, "material_values")},
                         {"Paint": BLUE, "Green": [0, 1, 0, 1]})

    def test_authored_contradiction_is_never_rewritten_or_given_a_private_name_override(self):
        authored = {"kind": "material_values", "target": "$a", "material": "Paint",
                    "base_color": RED, "roughness": .2, "metallic": 0, "tolerance": .00001}
        request = program([cube("a"), cube("b"), material("$a", also=["$b"]),
                           material("$b", color=BLUE, shared="ALL_USERS")], checks=[authored])
        checks = required_checks(request)
        self.assertEqual(checks[0], authored)
        self.assertNotIn(GUARD, checks[0])
        self.assertEqual(checks_of(checks, "material_values")[-1]["base_color"], BLUE)

    def test_existing_material_requires_private_facts_and_identity_is_not_inferred_from_name(self):
        request = program([node(A, {"Roughness": .1}, shared="ALL_USERS"),
                           node(B, {"Metallic": .8}, shared="ALL_USERS")], [A, B])
        self.assertTrue(needs_material_binding(request))
        with self.assertRaises(NeedsMaterialBinding):
            required_checks(request)
        same = facts([A, B], {A: [("Paint", 10)], B: [("Paint", 10)]})
        checks = required_checks(request, material_facts=same)
        graphs = checks_of(checks, "node_graph")
        self.assertEqual(len(graphs), 1)
        self.assertEqual(graphs[0]["nodes"][0]["inputs"], {"Roughness": .1, "Metallic": .8})
        separate = facts([A, B], {A: [("Paint", 10)], B: [("Paint", 20)]})
        graphs = checks_of(required_checks(request, material_facts=separate), "node_graph")
        self.assertEqual(len(graphs), 2)
        self.assertEqual({check[GUARD]["resource"] for check in graphs}, {"live:10", "live:20"})

    def test_same_root_node_history_preserves_unaffected_fields(self):
        request = program([node(A, {"Roughness": .1, "Metallic": .7}),
                           node(A, {"Roughness": .8})], [A])
        checks = required_checks(request, material_facts=facts([A], {A: [("Paint", 10)]}))
        graph, = checks_of(checks, "node_graph")
        self.assertEqual(graph["nodes"][0]["inputs"], {"Roughness": .8, "Metallic": .7})

    def test_material_then_node_updates_only_the_proved_shader_fields(self):
        request = program([material(A, also=[B]), node(B, {"Roughness": .9}, shared="ALL_USERS")], [A, B])
        checks = required_checks(request, material_facts=facts([A, B]))
        values, = checks_of(checks, "material_values")
        self.assertEqual(values["base_color"], RED)
        self.assertEqual(values["roughness"], .9)
        self.assertNotIn("metallic", values)  # Neither step states it (action_schemas.SETTINGS_NOTE).

    def test_other_shader_node_same_socket_does_not_overwrite_material_role(self):
        request = program([material(A), op("node_edit", A, material="Paint", edits=[
            {"op": "add", "node": "Other", "type": "ShaderNodeBsdfPrincipled", "inputs": {"Roughness": .9}}])], [A])
        checks = required_checks(request, material_facts=facts([A]))
        self.assertEqual(checks_of(checks, "material_values"), [], "Two Principled nodes exceed material_values scope")
        graph, = checks_of(checks, "node_graph")
        self.assertEqual(next(row for row in graph["nodes"] if row["name"] == "Principled BSDF")["inputs"]["Roughness"], .2)
        self.assertEqual(next(row for row in graph["nodes"] if row["name"] == "Other")["inputs"]["Roughness"], .9)

    def test_node_then_material_edit_in_place_keeps_old_graph_facts(self):
        """material_principled sets the stated inputs of a held material's one Principled BSDF, after the node edit.

        Regression: it rebuilt the graph at default values, dropping the node edit's other facts."""
        request = program([node(A, {"Roughness": .1, "Alpha": .5}, shared="ALL_USERS"),
                           material(B, color=BLUE, shared="ALL_USERS")], [A, B])
        checks = required_checks(request, material_facts=facts([A, B], {A: [("Paint", 10)], B: [("Paint", 10)]}))
        self.assertEqual(checks_of(checks, "material_values"), [])  # A held graph: the node check states it.
        graph, = checks_of(checks, "node_graph")
        shader = next(row for row in graph["nodes"] if row["name"] == "Principled BSDF")
        self.assertEqual(shader["inputs"], {"Roughness": .2, "Alpha": .5, "Base Color": BLUE})
        self.assertNotIn("type", shader)  # Edited, never added again.

    def test_fresh_isolation_forks_identity_and_keeps_private_logical_lookup(self):
        request = program([material(A, also=[B]), node(B, {"Roughness": .9}, shared="ISOLATE"),
                           node(B, {"Metallic": .6}, shared="ISOLATE")], [A, B])
        bundle = resolve_obligations(request, facts([A, B]))
        assigned = {row["target"]: row[GUARD]["resource"] for row in bundle.checks() if GUARD in row}
        self.assertEqual(assigned, {A: "created:0", B: "isolate:1"})
        states = {row["target"]: row for row in checks_of(bundle.checks(), "material_values")}
        self.assertEqual(states[A]["roughness"], .2)
        self.assertEqual(states[B]["roughness"], .9)
        self.assertEqual(states[B]["metallic"], .6)
        self.assertEqual(bundle.effects()[2]["source"], "isolate:1")

    def test_copy_as_is_independent_and_retains_inherited_written_state(self):
        request = program([cube("a"), cube("b"), material("$a"),
                           op("material_assign", "$b", source="$a", material="Paint", copy_as="Other"),
                           material("$a", color=BLUE, shared="ALL_USERS")])
        checks = required_checks(request)
        states = {row["material"]: row["base_color"] for row in checks_of(checks, "material_values")}
        self.assertEqual(states, {"Paint": BLUE, "Other": RED})
        assigned = {row["target"]: row[GUARD]["resource"] for row in checks_of(checks, "material_assignment")}
        self.assertEqual(assigned, {"$a": "created:2", "$b": "copy:3"})

    def test_assignment_target_precedes_other_staged_objects(self):
        request = program([op("material_assign", B, material="Paint")], [A, B])
        checks = required_checks(request, material_facts=facts([A, B], {A: [("Paint", 10)], B: [("Paint", 20)]}))
        self.assertEqual(checks[0][GUARD]["resource"], "live:20")

    def test_implicit_assignment_uses_defined_resource_before_other_owners(self):
        request = program([material(A), op("material_assign", C, material="Paint")], [A, B, C])
        bound = facts([B, A, C], {B: [("Paint", 20)]})
        checks = required_checks(request, material_facts=bound)
        assigned = next(check for check in checks_of(checks, "material_assignment") if check["target"] == C)
        self.assertEqual(assigned[GUARD]["resource"], "created:0")

    def test_explicit_assignment_reuses_fresh_isolated_resource(self):
        request = program([material(A, also=[B]), node(B, {"Roughness": .9}, shared="ISOLATE"),
                           op("material_assign", C, source=B, material="Paint")], [A, B, C])
        bundle = resolve_obligations(request, facts([A, B, C]))
        assigned = next(check for check in checks_of(bundle.checks(), "material_assignment") if check["target"] == C)
        self.assertEqual(assigned[GUARD]["resource"], "isolate:1")
        self.assertEqual(bundle.effects()[2]["source"], "isolate:1")

    def test_overwriting_one_slot_keeps_shared_original_on_other_owner(self):
        request = program([cube("a"), cube("b"), material("$a", also=["$b"]),
                           material("$a", "New", BLUE, slot=0)])
        checks = required_checks(request)
        states = {(row["target"], row["material"]): row["base_color"] for row in checks_of(checks, "material_values")}
        self.assertEqual(states, {("$a", "New"): BLUE, ("$b", "Paint"): RED})

    def test_name_only_keeps_what_its_target_holds_and_a_new_target_reuses(self):
        """A name alone never redefines a material: the target that holds it keeps it (action_nodes._keep).

        Was pinned the other way, rebuilding the held material at default
        values, which committed a red material turned grey."""
        request = program([cube("a"), cube("b"), material("$a"),
                           op("material_principled", "$b", name="Paint")])
        values, = checks_of(required_checks(request), "material_values")
        self.assertEqual(values["base_color"], RED)
        request = program([cube("a"), material("$a"), op("material_principled", "$a", name="Paint")])
        values, = checks_of(required_checks(request), "material_values")
        self.assertEqual(values["base_color"], RED)
        alone = program([cube("a"), op("material_principled", "$a", name="Fresh")])  # A new material: Blender's.
        self.assertEqual(checks_of(required_checks(alone), "material_values"), [])  # It states no value.
        self.assertEqual(material_rules.principled_inputs(alone["operations"][1]["parameters"]), {})

    def test_nested_isolation_forks_group_and_material_identity(self):
        request = program([op("node_edit", B, material="Paint", group="Group", shared="ISOLATE", edits=[
            {"op": "set", "node": "Noise", "inputs": {"Scale": 2}}])], [A, B])
        bound = facts([A, B], {A: [("Paint", 10)], B: [("Paint", 10)]})
        bound["resources"]["live:10"]["groups"] = {"Group": "group:30"}
        bound["resources"]["group:30"] = {"uid": 30, "name": "Inner", "groups": {}, "principled": None,
                                           "sharing": {B: {"others": 1}}}
        bundle = resolve_obligations(request, bound)
        graph, = checks_of(bundle.checks(), "node_graph")
        self.assertEqual(graph[GUARD], {"resource": "isolate:0", "slot": 0, "tree_resource": "group-isolate:0"})
        self.assertEqual(bundle.effects()[0]["tree_source"], "group:30")

    def test_unlink_obligation_survives_history_and_later_link_supersedes_it(self):
        bound = facts([A], {A: [("Paint", 10)]})
        bound["resources"]["live:10"]["inputs"] = {"Shader": {"Color": False}}
        unlink = op("node_edit", A, material="Paint", edits=[{"op": "unlink", "to_node": "Shader", "to_socket": "Color"}])
        request = program([unlink, node(A, {"Roughness": .7})], [A])
        graph, = checks_of(required_checks(request, material_facts=bound), "node_graph")
        self.assertEqual(graph["unlinked"], [{"node": "Shader", "socket": "Color"}])
        link = op("node_edit", A, material="Paint", edits=[{"op": "link", "from_node": "Texture", "from_socket": "Color",
                                                           "to_node": "Shader", "to_socket": "Color"}])
        graph, = checks_of(required_checks(program([unlink, link], [A]), material_facts=bound), "node_graph")
        self.assertEqual(graph["unlinked"], [])
        self.assertEqual(len(graph["links"]), 1)

    def test_bound_multi_input_appends_and_selective_unlink_keeps_other_source(self):
        links = [{"op": "link", "from_node": source, "from_socket": "Geometry",
                  "to_node": "Join", "to_socket": "Geometry"} for source in ("A", "B")]
        operations = [op("node_edit", A, modifier="Geometry", edits=links),
                      op("node_edit", A, modifier="Geometry", edits=[{"op": "unlink", "from_node": "A",
                                                                       "to_node": "Join", "to_socket": "Geometry"}])]
        bound = facts([A])
        bound["node_roots"] = [{"target": A, "material": None, "modifier": "Geometry", "world": False,
                                "group": None, "inputs": {"Join": {"Geometry": True}}}]
        before, = checks_of(required_checks(program(operations[:1], [A]), material_facts=bound), "node_graph")
        self.assertEqual([link["from_node"] for link in before["links"]], ["A", "B"])
        after, = checks_of(required_checks(program(operations, [A]), material_facts=bound), "node_graph")
        self.assertEqual([link["from_node"] for link in after["links"]], ["B"])
        self.assertEqual(after["unlinked"], [])
        self.assertEqual(after[LINK_GUARD], {"absent": [{"from_node": "A", "to_node": "Join", "to_socket": "Geometry"}]})

    def test_qualified_unlink_uses_bound_identifier_aliases_without_confusing_same_named_inputs(self):
        operations = [op("node_edit", A, material="Paint", edits=[
            {"op": "link", "from_node": "Keep", "from_socket": "Color", "to_node": "Dest", "to_socket": "second"},
            {"op": "link", "from_node": "Remove", "from_socket": "color-id", "to_node": "Dest", "to_socket": "Value"},
            {"op": "unlink", "from_node": "Remove", "from_socket": "Color", "to_node": "Dest", "to_socket": "first"}])]
        bound = facts([A], {A: [("Paint", 10)]})
        resource = bound["resources"]["live:10"]
        resource.update(inputs={"Dest": {"Value": False, "first": False, "second": False}},
                        input_identifiers={"Dest": {"Value": "first", "first": "first", "second": "second"}},
                        output_identifiers={"Keep": {"Color": "color-id"}, "Remove": {"color-id": "color-id"}},
                        output_names={"Keep": {"color-id": "Color"}, "Remove": {"color-id": "Color"}})
        bundle = resolve_obligations(program(operations, [A]), bound)
        self.assertFalse(bundle.unknowns())
        graph, = checks_of(bundle.checks(), "node_graph")
        self.assertEqual(graph["links"], [{"from_node": "Keep", "from_socket": "color-id", "to_node": "Dest", "to_socket": "second"}])
        self.assertEqual(graph[LINK_GUARD]["absent"][0]["from_socket"], "Color", "Unlink matches name OR identifier")
        self.assertEqual(graph[LINK_GUARD]["absent"][0]["to_socket"], "first")

    def test_retiring_a_negative_edge_endpoint_needs_no_deleted_node_existence(self):
        for qualifier in ({}, {"from_node": "Source"}):
            unlink = {"op": "unlink", "to_node": "Dest", "to_socket": "Color", **qualifier}
            for change in ({"op": "remove", "node": "Dest"}, {"op": "swap", "node": "Dest", "type": "ShaderNodeMath"}):
                with self.subTest(qualifier=qualifier, change=change):
                    request = program([op("node_edit", A, material="Paint", edits=[unlink, change])], [A])
                    bundle = resolve_obligations(request, facts([A], {A: [("Paint", 10)]}))
                    self.assertFalse(bundle.unknowns())
                    self.assertFalse(any(LINK_GUARD in check or check.get("unlinked") for check in bundle.checks()))

    def test_opaque_settings_preserve_prior_negative_edge_as_unknown(self):
        request = program([op("node_edit", A, material="Paint", edits=[
            {"op": "unlink", "from_node": "Source", "to_node": "Dest", "to_socket": "Color"},
            {"op": "set", "node": "Dest", "settings": {"operation": "MULTIPLY"}}])], [A])
        bundle = resolve_obligations(request, facts([A], {A: [("Paint", 10)]}))
        self.assertEqual(bundle.unknowns()[0]["affected"][0][LINK_GUARD]["absent"][0]["from_node"], "Source")
        self.assertFalse(any(LINK_GUARD in check for check in bundle.checks()))

    def test_opaque_settings_preserve_all_unlink_as_unknown_and_keep_unaffected_and_authored_facts(self):
        authored = {"kind": "node_graph", "target": A, "material": "Paint",
                    "unlinked": [{"node": "Dest", "socket": "Color"}]}
        request = program([op("node_edit", A, material="Paint", edits=[
            {"op": "unlink", "to_node": "Dest", "to_socket": "Color"},
            {"op": "unlink", "to_node": "Other", "to_socket": "Value"},
            {"op": "set", "node": "Dest", "settings": {"operation": "MULTIPLY"}}])], [A], [authored])
        bundle = resolve_obligations(request, facts([A], {A: [("Paint", 10)]}))
        self.assertEqual(bundle.checks()[0], request["expected_checks"][0])
        graph, = checks_of(bundle.checks()[1:], "node_graph")
        self.assertEqual(graph["unlinked"], [{"node": "Other", "socket": "Value"}])
        self.assertIn({"name": "Dest", "settings": {"operation": "MULTIPLY"}}, graph["nodes"])
        unknown, = bundle.unknowns()
        self.assertEqual(unknown["reason"], "opaque_node_settings")
        self.assertEqual(unknown["affected"][0]["unlinked"], [{"node": "Dest", "socket": "Color"}])
        bundle.assert_integrity(request)

    def test_opaque_settings_invalidate_bound_socket_aliases_even_without_prior_requested_facts(self):
        authored = {"kind": "node_graph", "target": A, "material": "Paint",
                    "links": [{"from_node": "Source", "from_socket": "Color", "to_node": "Dest", "to_socket": "old-socket-id"}]}
        request = program([op("node_edit", A, material="Paint", edits=[
            {"op": "set", "node": "Dest", "settings": {"data_type": "VECTOR"}},
            {"op": "link", "from_node": "Source", "from_socket": "Color", "to_node": "Dest", "to_socket": "Value"},
            {"op": "link", "from_node": "Source", "from_socket": "Color", "to_node": "Other", "to_socket": "Value"}])], [A], [authored])
        bound = facts([A], {A: [("Paint", 10)]})
        bound["resources"]["live:10"].update(inputs={"Dest": {"Value": False}, "Other": {"Value": False}},
            input_identifiers={"Dest": {"Value": "old-socket-id"}, "Other": {"Value": "other-socket-id"}},
            output_identifiers={"Source": {"Color": "color-id"}})
        bundle = resolve_obligations(request, bound)
        self.assertEqual(bundle.checks()[0], request["expected_checks"][0])
        graph, = checks_of(bundle.checks()[1:], "node_graph")
        self.assertEqual([link["to_socket"] for link in graph["links"]], ["Value", "other-socket-id"])
        self.assertEqual([link["from_socket"] for link in graph["links"]], ["color-id", "color-id"])
        unknown, = bundle.unknowns()
        self.assertEqual(unknown["reason"], "socket_selector_alias")
        self.assertEqual(unknown["affected"][0]["links"], [graph["links"][0]])
        bundle.assert_integrity(request)

    def test_unlink_guard_is_split_and_cannot_be_supplied_as_an_authored_check(self):
        edits = [{"op": "unlink", "from_node": "Source %d" % i, "to_node": "Dest", "to_socket": "Color"} for i in range(64)]
        request = program([op("node_edit", A, material="Paint", edits=edits[:32]),
                           op("node_edit", A, material="Paint", edits=edits[32:])], [A])
        bundle = resolve_obligations(request, facts([A], {A: [("Paint", 10)]}))
        guards = [check[LINK_GUARD] for check in bundle.checks() if LINK_GUARD in check]
        self.assertEqual([len(guard["absent"]) for guard in guards], [32, 32])
        for check in bundle.checks():
            with self.assertRaises(ValueError):
                validate_check(check)

    def test_bound_single_input_replaces_previous_link(self):
        request = program([op("node_edit", A, material="Paint", edits=[
            {"op": "link", "from_node": source, "from_socket": "Color", "to_node": "Shader", "to_socket": "Color"}
            for source in ("A", "B")])], [A])
        bound = facts([A], {A: [("Paint", 10)]})
        bound["resources"]["live:10"]["inputs"] = {"Shader": {"Color": False}}
        graph, = checks_of(required_checks(request, material_facts=bound), "node_graph")
        self.assertEqual([link["from_node"] for link in graph["links"]], ["B"])

    def test_unknown_socket_role_is_not_silently_assumed_single(self):
        request = program([op("node_edit", A, modifier="Geometry", edits=[
            {"op": "link", "from_node": source, "from_socket": "Geometry", "to_node": "Join", "to_socket": "Geometry"}
            for source in ("A", "B")])], [A])
        bundle = resolve_obligations(request, facts([A]))
        self.assertEqual(bundle.unknowns()[0]["reason"], "unknown_input_cardinality")
        self.assertEqual(bundle.unknowns()[0]["affected"][0]["links"][0]["from_node"], "A")
        self.assertEqual(bundle.checks()[0]["links"][0]["from_node"], "B")
        with self.assertRaisesRegex(ValueError, "verification is partial"):
            required_checks(request, material_facts=facts([A]))

    def test_opaque_settings_preserve_unproved_facts_and_keep_other_nodes_and_authored_checks(self):
        authored = {"kind": "node_graph", "target": A, "modifier": "Geometry",
                    "nodes": [{"name": "Math", "inputs": {"Value": 1}}]}
        request = program([op("node_edit", A, modifier="Geometry", edits=[
            {"op": "set", "node": "Other", "inputs": {"Scale": 2}},
            {"op": "set", "node": "Math", "inputs": {"Value": 1}},
            {"op": "set", "node": "Math", "settings": {"operation": "MULTIPLY"}}])], [A], [authored])
        bundle = resolve_obligations(request)
        self.assertEqual(bundle.checks()[0], request["expected_checks"][0])
        known = bundle.checks()[1]["nodes"]
        self.assertIn({"name": "Other", "inputs": {"Scale": 2}}, known)
        self.assertIn({"name": "Math", "settings": {"operation": "MULTIPLY"}}, known)
        unknown, = bundle.unknowns()
        self.assertEqual(unknown["operation"], 0)
        self.assertEqual(unknown["root"], {"target": A, "modifier": "Geometry"})
        self.assertEqual(unknown["reason"], "opaque_node_settings")
        self.assertEqual(unknown["affected"][0]["nodes"], [{"name": "Math", "inputs": {"Value": 1}}])
        bundle.assert_integrity(request)
        altered = bundle.unknowns()
        altered.clear()
        self.assertTrue(bundle.unknowns())

    def test_same_literal_setting_overwrite_is_proved_without_spurious_partial(self):
        request = program([op("node_edit", A, modifier="Geometry", edits=[
            {"op": "set", "node": "Math", "settings": {"operation": "ADD"}},
            {"op": "set", "node": "Math", "settings": {"operation": "MULTIPLY"}}])], [A])
        bundle = resolve_obligations(request)
        self.assertFalse(bundle.unknowns())
        self.assertEqual(bundle.checks()[0]["nodes"], [{"name": "Math", "settings": {"operation": "MULTIPLY"}}])

    def test_bound_facts_and_provenance_are_part_of_existing_byte_budget(self):
        request = program([material(A)], [A])
        bound = facts([A])
        with patch.object(program_checks, "MAX_CHECK_BYTES", 300), self.assertRaisesRegex(ValueError, "PROGRAM_CHECK_BUDGET"):
            resolve_obligations(request, bound)

    def test_pure_cube_admission_does_not_request_or_collect_material_facts(self):
        request = program([cube("a")])
        self.assertFalse(needs_material_binding(request))
        with patch("angelo_agent.program_material_obligations.material_checks", side_effect=AssertionError("unexpected replay")):
            self.assertEqual(len(required_checks(request)), 3)

    def test_bundle_is_immutable_and_returned_copies_cannot_mutate_it(self):
        request = program([cube("a"), material("$a")])
        original = copy.deepcopy(request)
        bundle = resolve_obligations(request)
        first = bundle.checks()
        first[-1]["material"] = "mutated"
        bundle.effects()[0]["resource"] = "mutated"
        bundle.assert_integrity(request)
        self.assertEqual(request, original)
        self.assertEqual(bundle, resolve_obligations(request))
        self.assertEqual(len(bundle.derived_flags), len(bundle.checks()))
        for check in bundle.checks():
            if GUARD in check:
                with self.assertRaises(ValueError):
                    validate_check(check)

    def test_true_1088_outer_bound_still_admits_every_guard(self):
        handles = ["scn:" + "a" * 32 + ":%d" % index for index in range(1, 64)]
        operations = [material(target, name="Material %02d" % index, also=handles[32:])
                      for index, target in enumerate(handles[:32])]
        authored = [{"kind": "object_count", "minimum": 63, "maximum": 63, "types": []}] * 32
        request = program(operations, handles, authored)
        bundle = resolve_obligations(request, facts(handles))
        self.assertEqual(len(bundle.checks()), 1088)
        self.assertEqual(sum(GUARD in check for check in bundle.checks()), 1056)
        self.assertLess(len(bundle.encoded) + len(bundle.facts_encoded) + len(bundle.effects_encoded), 1048576)


if __name__ == "__main__":
    unittest.main()
