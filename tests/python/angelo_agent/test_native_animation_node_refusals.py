# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Animation/node recovery authority is explicit, including through staged wrappers."""

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import anim_nla, node_copy, node_edit_geometry, node_generic  # noqa: E402
from angelo_agent.action_catalog import validate_operation  # noqa: E402
from angelo_agent.anim_channels import AnimationError  # noqa: E402
from angelo_agent.node_edit_spec import NodeEditError  # noqa: E402
from angelo_agent.refusal_codes import RefusalError, error_result, refusal_code  # noqa: E402

OBJ = "scn:" + "a" * 32 + ":7"


class ClassificationTests(unittest.TestCase):
    def test_existing_domain_codes_keep_their_identity_and_closed_policy(self):
        cases = (
            (AnimationError, "KEY_NOT_FOUND", "VALIDATION"),
            (AnimationError, "DRIVEN", "UNSUPPORTED"),
            (AnimationError, "TOO_MANY_KEYS", "LIMIT_EXCEEDED"),
            (AnimationError, "ACTION_EXPIRED", "STALE_DEPENDENCY"),
            (AnimationError, "VALUE_NOT_RETAINED", "INTERNAL_ERROR"),
            (NodeEditError, "NODE_NOT_FOUND", "VALIDATION"),
            (NodeEditError, "WORLD_NESTED_GROUP", "UNSUPPORTED"),
            (NodeEditError, "TREE_SIZE", "LIMIT_EXCEEDED"),
            (NodeEditError, "NOT_STAGED", "INTERNAL_ERROR"),
            (NodeEditError, "SHARING_UNKNOWN", "INTERNAL_ERROR"),
            (NodeEditError, "NODE_COPY_INEXACT", "INTERNAL_ERROR"),
        )
        for error_type, domain, policy in cases:
            with self.subTest(domain=domain):
                error = error_type(domain, "arbitrary display text")
                self.assertEqual(error.code, domain)
                self.assertEqual(refusal_code(error), policy)
                self.assertEqual(error_result(error)["error_code"], policy)
                self.assertEqual(refusal_code(ValueError(str(error))), "UNKNOWN")

    def test_unrecognized_and_wrapper_only_domains_fail_closed(self):
        for error_type, domain in ((AnimationError, "NEW_ERROR"), (AnimationError, "STRIP_OVERLAP"),
                                   (NodeEditError, "REFUSED"), (NodeEditError, "BUDGET"),
                                   (NodeEditError, "GN_CONTRACT"), (NodeEditError, "NODE_TYPE_UNAVAILABLE")):
            with self.subTest(domain=domain):
                self.assertEqual(refusal_code(error_type(domain, "Invalid value; please try again")), "UNKNOWN")
        self.assertEqual(refusal_code(node_generic.NodeRuleError("NODE_TYPE_DENIED: familiar text")), "UNKNOWN")

    def test_normal_animation_node_and_particle_corrections_are_typed(self):
        operations = (
            {"kind": "keyframe_edit", "target": OBJ, "parameters": {"operation": "SCALE", "factor": 0}},
            {"kind": "nla_edit", "target": OBJ, "parameters": {"operation": "TRACK_SET", "track": "Base"}},
            {"kind": "node_edit", "target": OBJ, "parameters": {"material": "Wood", "edits": [
                {"op": "set", "node": "Value"}]}},
            {"kind": "particle_set", "target": OBJ, "parameters": {"modifier": "Particles", "settings": {
                "instance_object": {"object": OBJ}}}},
        )
        for operation in operations:
            with self.subTest(kind=operation["kind"]), self.assertRaises(ValueError) as raised:
                validate_operation(operation)
            self.assertEqual(refusal_code(raised.exception), "VALIDATION")
            self.assertEqual(refusal_code(ValueError(str(raised.exception))), "UNKNOWN")

    def test_generic_node_restriction_is_typed_at_its_actual_raise_site(self):
        with self.assertRaises(node_generic.NodeRuleError) as raised:
            node_generic.check_type("ShaderNodeScript", "SHADER")
        self.assertEqual(refusal_code(raised.exception), "UNSUPPORTED")
        self.assertEqual(refusal_code(ValueError(str(raised.exception))), "UNKNOWN")


class WrapperTests(unittest.TestCase):
    def test_nla_wrapper_preserves_policy_details_and_restores_staged_strip(self):
        cases = (RuntimeError("the track has no room"),
                 AnimationError("STRIP_NOT_FOUND", "correct this selection", details={"track": "Base"}),
                 AnimationError("STRIP_VALUE_NOT_RETAINED", "a failed readback"))
        for original in cases:
            with self.subTest(policy=refusal_code(original)):
                strip = SimpleNamespace(name="Walk")
                source = SimpleNamespace(strips=SimpleNamespace(remove=Mock()))
                target = SimpleNamespace(name="Other")
                with patch.object(anim_nla, "strip_state", return_value={}), patch.object(
                        anim_nla, "build_strip", side_effect=[original, SimpleNamespace()]) as build:
                    with self.assertRaises(AnimationError) as raised:
                        anim_nla.move_strip(strip, source, target, {})
                self.assertEqual(raised.exception.code, "STRIP_OVERLAP")
                self.assertEqual(refusal_code(raised.exception), refusal_code(original))
                self.assertEqual(raised.exception.details, getattr(original, "details", {}))
                source.strips.remove.assert_called_once_with(strip)
                self.assertEqual(build.call_count, 2)

    def test_node_creation_wrappers_do_not_reclassify_arbitrary_runtime_failures(self):
        for operation in (lambda tree: node_generic.build(tree, {"type": "ShaderNodeValue"}, "marker"),
                          lambda tree: node_copy.copy_node(SimpleNamespace(bl_idname="ShaderNodeValue"), tree)):
            with self.subTest(operation=operation):
                tree = SimpleNamespace(nodes=SimpleNamespace(new=Mock(side_effect=RuntimeError(
                    "NODE_TYPE_UNAVAILABLE: invalid input; lower the budget"))))
                with self.assertRaises(ValueError) as raised:
                    operation(tree)
                self.assertEqual(refusal_code(raised.exception), "UNKNOWN")

    def test_geometry_contract_and_budget_wrappers_preserve_original_authority(self):
        class Tree(dict):
            name = "Graph"
            __hash__ = object.__hash__

        for phase in ("contract", "budget"):
            for original in (ValueError("BUDGET: invalid graph"),
                             RefusalError("too much geometry", code="LIMIT_EXCEEDED", details={"limit": 4}),
                             RefusalError("private stage lost", code="INTERNAL_ERROR")):
                with self.subTest(phase=phase, policy=refusal_code(original)):
                    tree = Tree()
                    context = SimpleNamespace(node_graphs={})
                    audit = Mock(side_effect=original if phase == "contract" else None)
                    budget = Mock(side_effect=original if phase == "budget" else None)
                    modules = {"angelo_agent.action_node_graphs": SimpleNamespace(_audit_geometry_tree=audit),
                               "angelo_agent.action_budget": SimpleNamespace(_modifier_bound=budget)}
                    with patch.dict(sys.modules, modules), patch.object(node_edit_geometry, "provenance", return_value={}), \
                            patch("angelo_agent.action_catalog.validate_operation", return_value={"parameters": {}}):
                        with self.assertRaises(NodeEditError) as raised:
                            node_edit_geometry.refresh(context, object(), tree)
                    self.assertEqual(refusal_code(raised.exception), refusal_code(original))
                    self.assertEqual(raised.exception.details, getattr(original, "details", {}))

    def test_node_step_wrapper_preserves_domain_policy_and_details(self):
        # Load only this bpy-dependent module with a harmless import stand-in; its
        # actual stage/operation wrapper runs, while no Blender API is invoked.
        path = Path(node_edit_geometry.__file__).with_name("node_edit.py")
        spec = importlib.util.spec_from_file_location("angelo_agent._refusal_node_edit_test", path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"bpy": SimpleNamespace()}):
            spec.loader.exec_module(module)
        operation = {"target": OBJ, "parameters": {"world": True, "group": None, "edits": [{"op": "set"}]}}
        graph = SimpleNamespace(bl_idname="ShaderNodeTree")
        root = SimpleNamespace(node_tree=graph)
        state = SimpleNamespace(group_node=None)
        for original in (ValueError("NODE_NOT_FOUND: a familiar refusal"),
                         NodeEditError("NODE_NOT_FOUND", "missing", details={"node": "Value"}),
                         NodeEditError("NOT_STAGED", "private owner lost")):
            with self.subTest(policy=refusal_code(original)), patch.object(module, "_world", return_value=root), \
                    patch.object(module.ops, "EditState", return_value=state), \
                    patch.object(module.ops, "apply", side_effect=original):
                with self.assertRaises(NodeEditError) as raised:
                    module.apply(SimpleNamespace(), operation)
            self.assertEqual(refusal_code(raised.exception), refusal_code(original))
            self.assertEqual(raised.exception.code, getattr(original, "code", "REFUSED"))
            self.assertEqual(raised.exception.details, getattr(original, "details", {}))


if __name__ == "__main__":
    unittest.main()
