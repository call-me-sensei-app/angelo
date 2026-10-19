# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Frozen bound verification stays complete, detached and readable in manual review.

Host-only preparation and font stand-ins; this does not claim native GUI coverage.
"""

import copy
from dataclasses import replace
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import approval_review as review, approvals, program_checks  # noqa: E402
from angelo_agent.program_context import ProgramBinding  # noqa: E402
from angelo_agent.program_material_obligations import GUARD, NeedsMaterialBinding  # noqa: E402
from test_native_material_obligations import A, B, BLUE, cube, facts, material, node, op, program  # noqa: E402

DRAIN_BUDGET = 0.5  # seconds per layout call while a test drains a review


def binding(bundle):
    return ProgramBinding(1, 2, (1, 0.), 3, (), None, bundle.checks_digest, bundle)


def call_for(request, bundle=None, *, recipe=False):
    return SimpleNamespace(id="bound-review", name="angelo_apply_recipe" if recipe else "angelo_scene_program",
                           arguments={"recipe": "material-setup", "parameters": {}} if recipe else request,
                           program_arguments=request if recipe else None, document=("document", 1),
                           precondition=binding(bundle) if bundle is not None else None)


def rows(call):
    def wrap(value, *args):
        yield from ((line, 0) for line in value.split("\n"))
    # A drain, not a pacing test: a budget long enough for one step on any host (the per-call work before the
    # first step took longer than 4 ms on Windows, so a 4 ms budget never advanced).
    while review.advance_review_layout(call, 1, 80, 14, 0, 1, wrap, time.monotonic() + DRAIN_BUDGET):
        pass
    return review.review_rows(call, 1, 80, 14, 0, 1, None, True)


class BoundApprovalReviewTests(unittest.TestCase):
    def shared_program(self):
        request = program([node(A, {"Roughness": .1}, shared="ALL_USERS"),
                           node(B, {"Metallic": .8}, shared="ALL_USERS")], [A, B])
        bundle = program_checks.resolve_obligations(request, facts([A, B], {
            A: [("First", 11), ("Paint", 10)], B: [("Other", 12), ("Paint", 10)]}))
        return request, bundle

    def test_bound_program_and_recipe_use_exact_bundle_without_pure_derivation(self):
        request, bundle = self.shared_program()
        original, frozen = copy.deepcopy(request), bundle.encoded
        for recipe in (False, True):
            call = call_for(request, bundle, recipe=recipe)
            with patch.object(program_checks, "required_checks", side_effect=AssertionError("pure derivation")), \
                    patch.object(program_checks, "resolve_obligations", side_effect=AssertionError("new resolution")):
                call.precondition = review.prepare_review(call, call.precondition)
                displayed = review.review_arguments(call)
                summary, details = rows(call)
            shown = displayed["program"] if recipe else displayed
            self.assertEqual(len(shown["expected_checks"]), len(bundle.checks()))
            self.assertEqual(call._approval_review.checks_digest, bundle.checks_digest)
            identities = [check["material_identity"] for check in shown["expected_checks"] if "material_identity" in check]
            self.assertTrue(any(identity.get("same_material_as") == {"target": B, "slot": 1} for identity in identities))
            self.assertTrue(all(identity["selected_slot"] == 1 for identity in identities))
            self.assertIn("Same material as", str(details))
            self.assertIn("Selected slot", str(details))
            self.assertIn("Material effects", str(details))
            self.assertNotIn("live:10", str((summary, details, displayed)))
            self.assertNotIn(GUARD, str(displayed))
        self.assertEqual(request, original)
        self.assertEqual(bundle.encoded, frozen)
        self.assertTrue(any(GUARD in check for check in bundle.checks()))
        bundle.assert_integrity(request)

    def test_explicit_admission_binding_replaces_old_call_binding(self):
        request, bundle = self.shared_program()
        call = call_for(request, replace(bundle, checks_digest="old"))
        admitted = binding(bundle)
        self.assertIs(review.prepare_review(call, admitted), admitted)
        self.assertEqual(call._approval_review.checks_digest, bundle.checks_digest)
        self.assertIs(call._approval_review.sources[2], admitted)
        self.assertIsNone(review._current_review(call))
        call.precondition = admitted
        self.assertIsNotNone(review._current_review(call))

    def test_invalid_or_mismatched_bundle_clears_the_previous_display(self):
        request, bundle = self.shared_program()
        call = call_for(request, bundle)
        for invalid in (binding(replace(bundle, encoded=b"[]")),
                        binding(bundle)._replace(checks_digest="0" * 64),
                        binding(bundle)._replace(obligations=SimpleNamespace(checks=bundle.checks))):
            call.precondition = review.prepare_review(call, binding(bundle))
            with self.assertRaises(ValueError):
                review.prepare_review(call, invalid)
            self.assertIsNone(call._approval_review)
        call.arguments = copy.deepcopy(request)
        call.arguments["operations"][0]["parameters"]["edits"][0]["inputs"]["Roughness"] = .3
        with self.assertRaisesRegex(ValueError, "obligations changed"):
            review.prepare_review(call, binding(bundle))

    def test_missing_bound_obligations_never_fall_back_to_incomplete_display(self):
        request, bundle = self.shared_program()
        call = call_for(request)
        with self.assertRaises(NeedsMaterialBinding):
            review.prepare_review(call, binding(bundle)._replace(obligations=None))
        self.assertIsNone(call._approval_review)

    def test_returned_controls_and_immutable_snapshot_cannot_poison_authority(self):
        request, bundle = self.shared_program()
        call = call_for(request, bundle)
        original = (copy.deepcopy(request), bundle.encoded, bundle.effects_encoded)
        call.precondition = review.prepare_review(call, call.precondition)
        display = review.review_arguments(call)
        display["expected_checks"][0]["material_identity"].clear()
        display["material_effects"][0]["material_identity"].clear()
        display["operations"].clear()
        next_display = review.review_arguments(call)
        self.assertTrue(next_display["expected_checks"][0]["material_identity"])
        self.assertTrue(next_display["material_effects"][0]["material_identity"])
        with self.assertRaises(TypeError):
            call._approval_review.arguments["expected_checks"][0]["material_identity"]["selected_slot"] = 9
        self.assertEqual((request, bundle.encoded, bundle.effects_encoded), original)

    def test_prepared_draw_and_stale_manual_hit_keep_existing_boundaries(self):
        request, bundle = self.shared_program()
        call = call_for(request, bundle)
        call.precondition = review.prepare_review(call, call.precondition)
        expected = rows(call)
        forbidden = AssertionError("preparation work ran in draw")
        with patch.object(program_checks.FrozenObligations, "checks", side_effect=forbidden), \
                patch.object(program_checks.FrozenObligations, "facts", side_effect=forbidden), \
                patch.object(program_checks.FrozenObligations, "effects", side_effect=forbidden), \
                patch.object(review, "_review_obligations", side_effect=forbidden), \
                patch.object(review.hashlib, "sha256", side_effect=forbidden), \
                patch.object(review, "approval_message", side_effect=forbidden):
            for _ in range(100):
                self.assertEqual(review.review_rows(call, 1, 80, 14, 0, 1, None, True), expected)
        controller = SimpleNamespace(pending={call.id: call}, document=call.document)
        state = {"approval_review": review.review_token(call)}
        self.assertTrue(review.approve_current_review(controller, call.id, state.copy()))
        call.precondition = review.prepare_review(call, call.precondition)
        self.assertFalse(review.approve_current_review(controller, call.id, state.copy()))
        state = {"approval_review": review.review_token(call)}
        call.arguments["operations"][0]["parameters"]["edits"][0]["inputs"]["Roughness"] = .3
        self.assertFalse(review.approve_current_review(controller, call.id, state))
        self.assertIsNone(call._approval_review)

    def test_created_copied_and_isolated_origins_remain_visible_without_tokens(self):
        cases = [
            (program([cube("a"), cube("b"), material("$a"),
                      op("material_assign", "$b", source="$a", material="Paint", copy_as="Other")]), None, "copied_by"),
            (program([material(A, also=[B]), node(B, {"Roughness": .9}, shared="ISOLATE")], [A, B]),
             facts([A, B]), "isolated_by")]
        for request, bound_facts, label in cases:
            bundle = program_checks.resolve_obligations(request, bound_facts)
            call = call_for(request, bundle)
            call.precondition = review.prepare_review(call, call.precondition)
            display = review.review_arguments(call)
            self.assertIn("created_by", str(display))
            self.assertIn(label, str(display))
            for effect in bundle.effects():
                self.assertNotIn(effect["resource"], str(display))

    def test_nested_group_origin_and_isolation_are_visible(self):
        request = program([node(A, {"Roughness": .7}, group="Group", shared="ISOLATE")], [A])
        bound_facts = facts([A], {A: [("Paint", 10)]})
        bound_facts["resources"]["live:10"]["groups"] = {"Group": "tree:20"}
        bound_facts["resources"]["tree:20"] = {"uid": 20, "name": "Nested", "groups": {},
            "sharing": {A: {"others": 1, "data_shared": False, "outside": False, "users_digest": "fixture"}}}
        bundle = program_checks.resolve_obligations(request, bound_facts)
        call = call_for(request, bundle)
        call.precondition = review.prepare_review(call, call.precondition)
        display = review.review_arguments(call)
        self.assertIn("isolated_node_group_by", str(display))
        self.assertIn("original_node_group", str(display))
        self.assertIn("Group", str(display))
        self.assertNotIn("tree:20", str(display))
        self.assertNotIn("group-isolate:", str(display))

    def recovery_call(self, *, recipe=False):
        request = program([cube("a")])
        observation = "recovery:" + "c" * 32
        request["recovery_observation"] = observation
        bundle = program_checks.resolve_obligations(request)
        call = call_for(request, bundle, recipe=recipe)
        call.arguments["recovery_observation"] = observation
        call.recovery_review = True
        call.recovery_evidence = {"available": True, "reason": "Host routing advice is not an observed fact.",
                                  "observation": {"id": observation, "changes": [{
                                      "handle": A, "field": "transform",
                                      "before": {"location": [0, 0, 0], "unchanged": "x" * 300},
                                      "after": {"location": [563.125, 0, 0], "unchanged": "x" * 300}}]}}
        call.precondition = review.prepare_review(call, call.precondition)
        return call, bundle

    def test_recovery_facts_survive_immutable_program_and_recipe_layouts(self):
        for recipe in (False, True):
            with self.subTest(recipe=recipe):
                call, bundle = self.recovery_call(recipe=recipe)
                original = copy.deepcopy((call.arguments, call.program_arguments, call.recovery_evidence))
                displayed = review.review_arguments(call)
                controls = displayed["program"] if recipe else displayed
                self.assertEqual(controls["expected_checks"], bundle.checks())
                summary, details = rows(call)
                for rendered in (str(summary), str(details)):
                    self.assertIn("Observed scene changes (before → after):", rendered)
                    self.assertIn("563.125", rendered)
                    self.assertNotIn("_recovery", rendered)
                    self.assertNotIn(call.recovery_evidence["reason"], rendered)
                self.assertNotIn("x" * 300, str(summary))
                self.assertIn("x" * 300, str(details))
                snapshot = call._approval_review.arguments["_recovery"]["observation"]["changes"]
                self.assertIsInstance(snapshot, tuple)
                with self.assertRaises(TypeError):
                    snapshot[0]["after"]["location"][0] = 99
                displayed["_recovery"]["observation"]["changes"][0]["after"]["location"][0] = 99
                self.assertEqual((call.arguments, call.program_arguments, call.recovery_evidence), original)
                self.assertEqual(call._approval_review.checks_digest, bundle.checks_digest)

    def test_recovery_evidence_changes_reject_the_prior_manual_review(self):
        for change in ("replace", "in_place", "revoke"):
            with self.subTest(change=change):
                call, _bundle = self.recovery_call()
                controller = SimpleNamespace(pending={call.id: call}, document=call.document)
                state = {"approval_review": review.review_token(call)}
                self.assertTrue(review.approve_current_review(controller, call.id, state.copy()))
                if change == "replace":
                    call.recovery_evidence = copy.deepcopy(call.recovery_evidence)
                elif change == "in_place":
                    call.recovery_evidence["observation"]["changes"][0]["after"]["location"][0] = 12
                else:
                    call.recovery_review = False
                self.assertFalse(review.approve_current_review(controller, call.id, state))

    def test_pure_program_without_materials_preserves_exact_display_and_public_json(self):
        request = program([cube("a")])
        before = dict(request, expected_checks=program_checks.required_checks(request))
        public_json = json.dumps(request, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        expected = json.dumps(before, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        for bundle in (None, program_checks.resolve_obligations(request)):
            call = call_for(request, bundle)
            call.precondition = review.prepare_review(call, call.precondition)
            self.assertEqual(call._approval_review.encoded, expected)
            self.assertEqual(json.dumps(request, ensure_ascii=False, allow_nan=False, separators=(",", ":")), public_json)


if __name__ == "__main__":
    unittest.main()
