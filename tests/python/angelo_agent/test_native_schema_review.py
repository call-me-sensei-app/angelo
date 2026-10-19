# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Regression tests for schema-consistency edge cases: lookups, open values, tolerances and bounds.

Each FINDING failed before its fix and asserts the fixed behaviour; a
``..._pin`` keeps behaviour verified sound. Pure host tests: no Blender, Claude
or inference.
"""

import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent.program_spec import validate_program  # noqa: E402
from angelo_agent.validation_properties import evaluate_property  # noqa: E402
from angelo_agent.validation_spec import check_references, validate_check  # noqa: E402
from test_native_property_set import Holder, Prop  # noqa: E402
from angelo_agent.turn_context import TurnContext  # noqa: E402
from test_native_agent_stack import Superseded, exact  # noqa: E402
from test_native_round_savings import committed, passed  # noqa: E402
from test_native_turn_diet import HANDLE, NEWER, REVISION, entry, observation, transform  # noqa: E402

LOOKUP = {"tool": "angelo_scene_object", "arguments": {"object": "o1", "sections": ["TRANSFORM"], "limit": 10}}


def edit(location):
    return {"tool": "angelo_scene_program", "arguments": {"mode": "APPLY", "operations": [
        transform(location=location)]}}


def committed_at(revision):
    """A committed program whose post-commit live revision of HANDLE is ``revision``."""
    result = committed(checks=[passed("transform", target=HANDLE,
                                      observed={"location": [0.0, 0.0, 1.0], "scope": "local_RNA"})])
    result["live_revisions"]["objects"][0].update(role="MODIFIED", object_revision=revision)
    return result


CAMERA = "scn:" + "a" * 32 + ":9"


class ClaudeLookupBindingOrderTests(Superseded):
    """query_observations.observe_lookups: each object's latest view binds the next round (providers.py)."""

    def run_turn(self, later):
        built = observation(entry(HANDLE, REVISION, mentioned=True, active=True))
        with patch("angelo_agent.turn_context.prepare", return_value=TurnContext(copy.deepcopy(later), stats={})):
            return self.turn("Move the cube up, then a little more.", observation=built)

    def test_a_lookup_before_an_edit_in_one_plan_does_not_rebind_the_pre_edit_revision(self):
        """FINDING (fixed): a plan [scene_object o1, program editing o1] then a next-round program
        on o1 without a write_set. The program committed NEWER and the continuation observation shows
        NEWER, but observe_lookups ran last and re-recorded the lookup's pre-edit REVISION, so the next
        round bound a stale revision and was refused as changed (an extra round)."""
        self.executor.results = [exact(HANDLE, REVISION, name="Eval Cube"), committed_at(NEWER),
                                 committed_at("4" * 8 + "e" * 56)]
        self.plans = [{"operations": [LOOKUP, edit([0, 0, 1])], "verify": True},
                      {"operations": [edit([0, 0, 2])]}]
        seen, execute = [], self.executor.execute
        self.executor.execute = lambda name, arguments: seen.append((name, arguments)) or execute(name, arguments)
        self.run_turn(observation(entry(HANDLE, NEWER, mentioned=True, active=True)))
        programs = [arguments for name, arguments in seen if name == "angelo_scene_program"]
        self.assertEqual(len(programs), 2, self.prompts[-1])
        self.assertEqual(programs[1]["write_set"], [{"handle": HANDLE, "revision": NEWER}],
                         "The next round binds the revision the commit and the fresh observation showed")

    def test_a_budget_truncated_search_row_does_not_revoke_the_fresh_observation(self):
        """FINDING (fixed): a search whose shared work budget ran out before o1's row returns it
        inexact (scene_fingerprints: object_revision None, uncertain_reason original_state_work_budget).
        observe_lookups applied that row after the continuation observation recorded o1's exact revision
        and popped it, so a next-round edit that omits the revision was refused with "no revision in your
        latest observation" although the latest observation showed one."""
        row = {"object": HANDLE, "name": "Eval Cube", "type": "MESH", "object_revision": None,
               "revision_exact": False, "program_revision_supported": False, "revision_limitations": [],
               "uncertain_reason": "original_state_work_budget"}
        search = {"tool": "angelo_scene_search", "arguments": {"query": "Cube"}}
        self.executor.results = [{"objects": [row], "total": 1}, committed_at(NEWER)]
        self.plans = [{"operations": [search], "verify": True}, {"operations": [edit([0, 0, 1])]},
                      {"operations": []}]
        self.run_turn(observation(entry(HANDLE, REVISION, mentioned=True, active=True)))
        self.assertEqual(self.executor.calls, ["angelo_scene_search", "angelo_scene_program"], self.prompts[2:])


def camera_without_focus():
    dof = Holder({"focus_object": Prop("POINTER")}, {"focus_object": None})
    data = Holder({"dof": Prop("POINTER", fixed="CameraDOFSettings")}, {"dof": dof, "animation_data": None})
    return Holder({"data": Prop("POINTER", fixed="Camera")},
                  {"data": data, "type": "CAMERA", "name": "Camera", "animation_data": None})


class OpenMapCheckPointerTests(unittest.TestCase):
    """A check's open settings map: a string open_values leaves as text, at an RNA object pointer."""

    def reader(self, targets):
        return SimpleNamespace(context=SimpleNamespace(targets=targets, resolve_target=targets.get),
                               spend=lambda amount: None)

    def test_an_undeclared_alias_text_in_a_pointer_check_does_not_pass_on_an_empty_pointer(self):
        """FINDING (fixed): a property check expecting data.dof.focus_object to be "$focus" (an alias no
        operation declares, or one a later program creates) keeps the text (open_values wraps only declared
        aliases), is admitted (check_references lists only {"object": ...} forms), and validation_properties
        resolved the text to None and compared it with an empty pointer: the check PASSED although no focus
        object was set. A name ("Lamp") passed the same way. Now it fails, and says the value names nothing."""
        camera = camera_without_focus()
        for text in ("$focus", "Lamp"):
            with self.subTest(text=text):
                program = validate_program("angelo_scene_program", {"mode": "APPLY", "write_set": [
                    {"handle": CAMERA, "revision": "b" * 64}], "operations": [
                    {"kind": "transform", "target": CAMERA, "parameters": {"location": [0, 0, 1]}}],
                    "expected_checks": [{"kind": "property", "target": CAMERA,
                                         "values": {"data.dof.focus_object": text}}]})
                check = program["expected_checks"][0]  # Admitted: the program binds nothing for the text.
                self.assertEqual(check["values"], {"data.dof.focus_object": text})
                self.assertEqual(check_references(check), (CAMERA,), "The text is no dependency")
                passed, observed = evaluate_property(self.reader({CAMERA: camera}), camera, check)
                self.assertFalse(passed, "An unset focus object passed a check naming one: %s" % observed)
                self.assertEqual(observed["unresolved"], ["data.dof.focus_object"])

    def test_a_reference_that_resolves_passes_only_on_its_object(self):
        camera, focus = camera_without_focus(), SimpleNamespace(name="Focus")
        reader = self.reader({CAMERA: camera, "$focus": focus})
        for expected in ({"object": "$focus"}, "$focus"):  # The stored form, and text naming a declared alias.
            check = {"kind": "property", "target": CAMERA, "values": {"data.dof.focus_object": expected}}
            with self.subTest(expected=expected):
                self.assertFalse(evaluate_property(reader, camera, check)[0], "An empty pointer is not the object")
                camera.data.dof.focus_object = focus
                self.assertEqual(evaluate_property(reader, camera, check), (True, {
                    "values": {"data.dof.focus_object": "Focus"}, "scope": "STORED_SETTINGS"}))
                camera.data.dof.focus_object = None
        check = {"kind": "property", "target": CAMERA, "values": {"data.dof.focus_object": None}}
        self.assertTrue(evaluate_property(reader, camera, check)[0], "null expects an empty pointer")

    def test_a_declared_alias_in_a_check_map_becomes_an_object(self):
        """Sound: the alias of an object the program creates is wrapped and counted as a dependency."""
        program = validate_program("angelo_scene_program", {"mode": "APPLY", "write_set": [
            {"handle": CAMERA, "revision": "b" * 64}], "operations": [
            {"kind": "create_object", "parameters": {"type": "EMPTY", "alias": "focus"}}],
            "expected_checks": [{"kind": "property", "target": CAMERA, "values": {"data.dof.focus_object": "$focus"}}]})
        self.assertEqual(program["expected_checks"][0]["values"], {"data.dof.focus_object": {"object": "$focus"}})


class GenericSettingsPointerTests(unittest.TestCase):
    def test_text_at_a_constraint_pointer_does_not_pass_on_an_empty_target(self):
        """FINDING (fixed, same class): a constraint_values check {"target": "Empty"} (a name, or an
        undeclared $alias, which open_values leaves as text) is admitted by validate_checks (json_value
        takes text) and generic_settings.evaluate compared an empty constraint target with
        resolve(None): the check passed although the constraint had no target. Modifier, particle, bone,
        node and animation-modifier settings share that evaluator."""
        from angelo_agent.generic_settings import evaluate
        constraint = Holder({"target": Prop("POINTER")}, {"target": None})
        check = validate_check({"kind": "constraint_values", "target": CAMERA, "name": "Track To",
                                "values": {"target": "Empty"}})
        passed, observed = evaluate(constraint, check["values"], check.get("tolerance"), {}.get)
        self.assertFalse(passed, observed)
        self.assertEqual(observed["unresolved"], ["target"])
        empty = SimpleNamespace(name="Empty")
        resolve = {"$empty": empty}.get
        self.assertFalse(evaluate(constraint, {"target": {"object": "$empty"}}, None, resolve)[0])
        constraint.target = empty
        self.assertTrue(evaluate(constraint, {"target": {"object": "$empty"}}, None, resolve)[0])
        self.assertFalse(evaluate(constraint, {"target": None}, None, resolve)[0])


class OmittedToleranceTests(unittest.TestCase):
    """check_defaults: an omitted tolerance compares each number at its own float32 precision."""

    def light(self, energy, spot_size):
        return SimpleNamespace(type="LIGHT", data=SimpleNamespace(
            type="SPOT", energy=energy, spot_size=spot_size, animation_data=None, node_tree=None))

    def test_a_large_value_does_not_loosen_the_small_ones(self):
        """FINDING (fixed): one tolerance for the whole check, 4e-7 x its largest expected number, so
        a light_settings check stating energy 1,000,000 W compared spot_size, spot_blend and colour within
        0.4: a spot 23 degrees wider than asked passed. Float32 read-back error is relative to each value,
        so the default is per value (as rna_rules.same does). Derived checks shared one tolerance too."""
        from angelo_agent.validation_camera_light import evaluate_camera_light
        check = validate_check({"kind": "light_settings", "target": CAMERA, "values": {
            "energy": 1000000, "spot_size": 0.785398}})
        self.assertNotIn("tolerance", check, "Left out, each number compares at its own precision")
        reader = SimpleNamespace(spend=lambda amount: None)
        passed, observed = evaluate_camera_light(reader, self.light(1000000.0, 0.785398 + 0.39), check)
        self.assertFalse(passed, "A spot 0.39 rad (22 degrees) too wide passed: %s" % observed)
        passed, observed = evaluate_camera_light(reader, self.light(1000000.0625, 0.7853979), check)
        self.assertTrue(passed, "One float32 ULP of each value still passes: %s" % observed)

    def test_derived_checks_state_no_shared_tolerance(self):
        from angelo_agent.program_checks import required_checks
        light = {"kind": "light_create", "target": None, "parameters": {
            "name": "Flood", "alias": "flood", "location": [0, 0, 5],
            "settings": {"light_type": "SPOT", "energy": 1000000, "spot_size": 0.785398}}}
        program = validate_program("angelo_scene_program", {"mode": "APPLY", "operations": [light]})
        settings = [check for check in required_checks(program) if check["kind"] == "light_settings"]
        self.assertEqual(len(settings), 1)
        self.assertNotIn("tolerance", settings[0])

    def test_an_authored_twin_replaces_a_derived_check_only_when_no_looser(self):
        """A stated tolerance within every number's own (at most 0.00001) proves the derived twin that
        states none, so that one runs once and is reported as implied; a looser one never replaces it."""
        from angelo_agent.program_checks import required_checks, resolve_obligations
        move = {"kind": "transform", "target": CAMERA, "parameters": {"location": [2, 3, 4]}}
        for tolerance, count in ((0.00001, 1), (0, 1), (0.000011, 2), (0.001, 2)):
            authored = {"kind": "transform", "target": CAMERA, "property": "location", "expected": [2, 3, 4],
                        "tolerance": tolerance}
            program = validate_program("angelo_scene_program", {"mode": "APPLY", "write_set": [
                {"handle": CAMERA, "revision": "b" * 64}], "operations": [move], "expected_checks": [authored]})
            with self.subTest(tolerance=tolerance):
                self.assertEqual(len(required_checks(program)), count)
                self.assertEqual(resolve_obligations(program).derived_flags, (True,) * count if count == 1
                                 else (False, True))


class CameraPointerTests(unittest.TestCase):
    def test_both_camera_forms_fail_closed_alike_and_bind_alike_pin(self):
        """Verified sound: a later alias, a name or a null
        pointer are refused in either form, and the handle and {"object": handle} forms give the same
        canonical program, dependencies and checks."""
        from angelo_agent.action_catalog import affected_references
        from angelo_agent.identifiers import IdentifierError, expand_call
        from angelo_agent.program_checks import required_checks
        from test_native_coverage_identifiers import SCENE, SCENE_REV, table as coverage_table
        write = [{"handle": SCENE, "revision": SCENE_REV}]
        for form in ("$cam", {"object": "$cam"}):
            with self.subTest(later_alias=form), self.assertRaises(ValueError):
                validate_program("angelo_scene_program", {"mode": "APPLY", "write_set": write, "operations": [
                    {"kind": "property_set", "target": SCENE, "parameters": {"values": {"camera": form}}},
                    {"kind": "camera_create", "target": None, "parameters": {"alias": "cam", "name": "Shot"}}]})
        short = [{"handle": "o3", "revision": SCENE_REV[:8]}]
        for form in ("Camera", {"object": "Camera"}):
            with self.subTest(name=form), self.assertRaises(IdentifierError):
                expand_call("angelo_scene_program", {"mode": "APPLY", "write_set": short, "operations": [
                    {"kind": "property_set", "target": "o3", "parameters": {"values": {"camera": form}}}]},
                    coverage_table())
        for form in ({"object": None}, {}, {"object": ""}, {"object": "o2", "x": 1}):
            with self.subTest(malformed=form), self.assertRaises(ValueError):
                validate_program("angelo_scene_program", {"mode": "APPLY", "write_set": write, "operations": [
                    {"kind": "property_set", "target": SCENE, "parameters": {"values": {"camera": form}}}]})
        outcomes = []
        for form in ("o2", {"object": "o2"}):
            arguments = expand_call("angelo_scene_program", {"mode": "APPLY", "write_set": short, "operations": [
                {"kind": "property_set", "target": "o3", "parameters": {"values": {"camera": form}}}],
                "expected_checks": [{"kind": "property", "target": "o3", "values": {"camera": form}}]},
                coverage_table())
            program = validate_program("angelo_scene_program", arguments)
            outcomes.append((program, affected_references(program["operations"][0]), required_checks(program)))
        self.assertEqual(outcomes[0], outcomes[1])


class ChannelBoundTests(unittest.TestCase):
    """A key's or an animation recipe's value is bounded as its channel is written by a transform."""

    def test_a_scale_key_takes_a_transforms_scale_bound_and_says_so(self):
        """FINDING (fixed): keyframe_insert's value took 100,000 for every channel, so a scale key accepted
        what a transform's scale (1,000, also each transform's result in action_object) refused. Its schema is
        a union by channel; a mistake names its own field. simple-animation, which expands to it, keys location
        and rotation: a scale variant of it would cost every plan and declaration 0.4-1.4 KB."""
        from angelo_agent.action_catalog import validate_operation
        from angelo_agent.action_recipes import validate_parameters
        key = {"kind": "keyframe_insert", "target": CAMERA, "parameters": {"property": "scale", "value": [1000, 1, 1]}}
        self.assertEqual(validate_operation(key)["parameters"]["value"], [1000.0, 1.0, 1.0])
        with self.assertRaisesRegex(ValueError, r"parameters\.value\[0\] requires a finite bounded number"):
            validate_operation(dict(key, parameters={"property": "scale", "value": [1000.5, 1, 1]}))
        with self.assertRaisesRegex(ValueError, r"parameters\.frame requires a finite bounded number"):
            validate_operation(dict(key, parameters={"property": "location", "value": [0, 0, 1], "frame": 200000}))
        for channel, value in (("location", [100000, 0, 0]), ("rotation_euler", [0, 0, 6 * 3.14159])):
            self.assertEqual(validate_operation(dict(key, parameters={"property": channel, "value": value}))[
                "parameters"]["value"][0], value[0])
        animation = {"property": "location", "start_frame": 1, "end_frame": 24, "start_value": [0, 0, 0],
                     "end_value": [100000, 0, 0]}
        self.assertEqual(validate_parameters("simple-animation", animation)["end_value"][0], 100000)
        with self.assertRaisesRegex(ValueError, r"property has an unsupported value"):
            validate_parameters("simple-animation", dict(animation, property="scale"))


class ClaudeCodeErrorTests(unittest.TestCase):
    """claude_code_errors follows what Claude Code shows of a StructuredOutput mismatch."""

    def test_a_text_is_cut_only_past_11024_characters(self):
        from claude_code_errors import shown
        self.assertEqual(shown("x" * 11024), "x" * 11024)
        cut = shown("a" * 5000 + "x" * 1025 + "b" * 5000)
        self.assertEqual((cut[:5000], cut[-5000:]), ("a" * 5000, "b" * 5000))
        self.assertIn("[1025 characters truncated]", cut)

    def test_an_objects_required_errors_follow_its_unions_as_in_ajv(self):
        from claude_code_errors import mismatch
        schema = {"type": "object", "properties": {"tool": {"type": "string"}}, "required": ["tool"],
                  "allOf": [{"if": {"properties": {"tool": {"const": "a"}}}, "then": {"required": ["x"]}}]}
        self.assertEqual(mismatch({}, schema), "Output does not match required schema: root: must have required "
                         "property 'x', root: must match \"then\" schema, root: must have required property 'tool'")


try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None


@unittest.skipIf(Draft202012Validator is None, "jsonschema is not installed")
class TaggedUnionTests(unittest.TestCase):
    def test_cross_kind_and_cross_tool_swaps_agree_pin(self):
        """Sound (pin): structurally different plans, each corpus operation under every other kind and each call
        under every other tool, and checks under every kind and modifier type, are accepted alike by the oneOf
        and the if/then forms (an exhaustive run covers all 10,721 swaps and 14,477 multi-mutation/combined plans;
        this keeps a deterministic sample)."""
        from angelo_agent.tool_specs import native_plan_schema
        from angelo_agent.validation_spec import CHECK_VARIANTS
        from test_native_tagged_unions import corpus, one_of_plan, program
        before, after = Draft202012Validator(one_of_plan()), Draft202012Validator(native_plan_schema())
        plans = corpus()
        ops = [o for p in plans for c in p["operations"] if c["tool"] == "angelo_scene_program"
               for o in c["arguments"]["operations"]]
        kinds = sorted({o["kind"] for o in ops})
        checks = [c for p in plans for o in p["operations"] for c in o.get("arguments", {}).get("expected_checks", [])]
        check_kinds = sorted({v["properties"]["kind"]["const"] for v in CHECK_VARIANTS}) + ["unknown_kind"]
        candidates = [program(dict(o, kind=k)) for o in ops for k in kinds if k != o["kind"]]
        candidates += [{"operations": [dict(c, tool=t)]} for p in plans for c in p["operations"]
                       for t in sorted({c["tool"] for p in plans for c in p["operations"]}) if t != c["tool"]]
        move = {"kind": "transform", "target": "o3", "parameters": {"scale": [2, 1, 1]}}
        candidates += [program(move, checks=[dict(c, kind=k, **extra)]) for c in checks for k in check_kinds
                       for extra in ({}, {"type": "ARRAY"}, {"type": "BEVEL"})]
        for plan in candidates[::9]:
            self.assertEqual(before.is_valid(plan), after.is_valid(plan), json.dumps(plan)[:400])

    def test_a_missing_tag_is_named_in_what_the_model_reads_pin(self):
        """Kept residual (note): an ``if`` has no ``required``, so an operation whose tag key is missing or
        misspelled ("type" for "kind", "name" for "tool") satisfies every ``if`` and is checked against every
        member: 7-10 KB, still smaller than the oneOf's 13-24 KB. Ajv lists the missing tag's "required" line
        after the members' errors, and Claude Code shows a text of up to 11,024 characters whole, so the
        model reads the cause. "required": [tag] in each ``if`` would answer with that one line, for 1,380 B
        on every plan; no recorded refusal was a missing tag. Past 11,024 the line stays in the
        last 5,000 shown; this pins both, so growth past the bound is a decision, not drift."""
        from angelo_agent.tool_specs import native_plan_schema
        from claude_code_errors import GRACE, KEEP, mismatch, shown
        from test_native_tagged_unions import program
        plan = native_plan_schema()
        for value, line in ((program({"type": "transform", "target": "o3", "parameters": {"location": [0, 0, 1]}}),
                             "/operations/0/arguments/operations/0: must have required property 'kind'"),
                            ({"operations": [{"name": "angelo_scene_program", "arguments": {"mode": "APPLY"}}]},
                             "/operations/0: must have required property 'tool'")):
            text = mismatch(value, plan)
            with self.subTest(line=line):
                self.assertIn(line, shown(text)[-KEEP:])
                self.assertLessEqual(len(text), 2 * KEEP + GRACE, "%d characters: no longer shown whole" % len(text))


if __name__ == "__main__":
    unittest.main()
