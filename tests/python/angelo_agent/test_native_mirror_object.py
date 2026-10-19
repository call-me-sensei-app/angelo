# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Mirror origin admission, transport and mandatory identity obligations, without Blender.

Actual RNA assignment, private/live remapping, rollback, frames and Undo are
covered separately by test_blender_mirror_object.py; these tests do not prove them.
"""

import copy
import json
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent.action_catalog import affected_references, validate_operation, written_references
from angelo_agent.action_schemas import MODIFIER_SETTINGS
from angelo_agent.codex_plan import ordinary_schema, parse_plan
from angelo_agent.derived_checks import derive_checks
from angelo_agent.generic_settings import evaluate
from angelo_agent.identifiers import IdentifierError, expand_call
from angelo_agent.program_checks import required_checks, resolve_obligations
from angelo_agent.program_spec import validate_program
from angelo_agent.strict_schema import decode, encode
from angelo_agent.validation_spec import validate_check
from test_native_coverage_identifiers import OBJ, OTHER, OTHER_REV, REV, table
from test_native_generic_settings import prop
import test_native_partial_completion as partial


def mirror(kind="modifier_configure", name="Mirror", target=OBJ, **settings):
    field = "modifier" if kind == "modifier_configure" else "name"
    return {"kind": kind, "target": target, "parameters": {"type": "MIRROR", field: name, "settings": settings}}


def program(*operations, checks=()):
    return validate_program("angelo_scene_program", {"mode": "APPLY", "operations": list(operations),
        "write_set": [{"handle": OBJ, "revision": REV}],
        "read_set": [{"handle": OTHER, "revision": OTHER_REV}], "expected_checks": list(checks)}, derive_checks=True)


def pointers(*operations):
    return [check for check in derive_checks([validate_operation(op) for op in operations])
            if check["kind"] == "modifier_values"]


class MirrorAdmissionTests(unittest.TestCase):
    def test_preserve_set_clear_and_conflict_for_add_and_configure(self):
        # {"object": handle} is every pointer's other form (action_schema_types.POINTER), stored as the handle.
        cases = [({}, None, False), ({"mirror_object": None}, None, False), ({"clear": False}, None, False),
                 ({"mirror_object": OTHER}, OTHER, False), ({"mirror_object": {"object": OTHER}}, OTHER, False),
                 ({"clear": True}, None, True), ({"clear": True, "mirror_object": None}, None, True)]
        for kind in ("modifier_add", "modifier_configure"):
            for fields, origin, clear in cases:
                with self.subTest(kind=kind, fields=fields):
                    settings = validate_operation(mirror(kind, **fields))["parameters"]["settings"]
                    self.assertEqual((settings.get("mirror_object"), settings.get("clear", False)), (origin, clear))
                    # Only what the call states: the modifier keeps the rest (action_schemas.SETTINGS_NOTE).
                    self.assertEqual(set(settings), {key for key, value in fields.items() if value is not None})
            for fields in ({"mirror_object": OTHER, "clear": True}, {"mirror_object": OBJ},
                           {"mirror_object": False}, {"mirror_object": 1}, {"mirror_object": {"object": OBJ}},
                           {"mirror_object": {"object": OTHER, "extra": 1}}, {"mirror_object": {"object": None}},
                           {"mirror_object": "Artist Origin"}, {"clear": None}, {"clear": 0}, {"clear": 1}):
                with self.subTest(kind=kind, fields=fields), self.assertRaises(ValueError):
                    validate_operation(mirror(kind, **fields))
        self.assertEqual([kind for kind, schema in MODIFIER_SETTINGS.items()
                          if "clear" in schema.get("properties", {})], ["MIRROR"])

    def test_strict_padding_preserves_while_explicit_clear_survives(self):
        for kind in ("modifier_add", "modifier_configure"):
            for settings in ({}, {"mirror_object": None}, {"clear": False}, {"mirror_object": OTHER}, {"clear": True}):
                operation = mirror(kind, **settings)
                ordinary = {"reply": "", "operations": [{"tool": "angelo_scene_program",
                    "arguments": {"mode": "APPLY", "operations": [operation]}}]}
                written = encode(ordinary, ordinary_schema())
                parsed, problem = parse_plan(json.dumps(written))
                self.assertIsNone(problem)
                decoded = parsed["operations"][0]["arguments"]["operations"][0]
                self.assertEqual(validate_operation(decoded), validate_operation(operation))
        # JSON-text settings are parsed as written; optional strict padding is a different boundary.
        schema = MODIFIER_SETTINGS["MIRROR"]
        padded = encode({}, schema)
        self.assertIsNone(padded["clear"])
        self.assertNotIn("clear", decode(padded, schema))
        for fields in ({"clear": True}, {"mirror_object": OTHER}):
            self.assertEqual(decode(json.dumps(fields), schema), fields)
        with self.assertRaises(ValueError):
            validate_operation(mirror(**decode('{"clear":null}', schema)))

    def test_flat_reference_is_read_only_and_short_ids_bind_it(self):
        for kind in ("modifier_add", "modifier_configure"):
            operation = mirror(kind, mirror_object=OTHER)
            self.assertEqual(affected_references(operation), (OBJ, OTHER))
            self.assertEqual(written_references(operation), (OBJ,))
            for settings in ({}, {"clear": True}, {"mirror_object": None}):
                self.assertEqual(affected_references(mirror(kind, **settings)), (OBJ,))
        resolved = expand_call("angelo_scene_program", {"mode": "APPLY",
            "operations": [mirror(target="o1", mirror_object="o2")]}, table())
        self.assertEqual(resolved["write_set"], [{"handle": OBJ, "revision": REV}])
        self.assertEqual(resolved["read_set"], [{"handle": OTHER, "revision": OTHER_REV}])
        self.assertEqual(resolved["operations"][0]["parameters"]["settings"]["mirror_object"], OTHER)
        for reference in ("o99", "scn:" + "b" * 32 + ":12"):
            with self.subTest(reference=reference), self.assertRaises(IdentifierError):
                expand_call("angelo_scene_program", {"mode": "APPLY",
                    "operations": [mirror(target="o1", mirror_object=reference)]}, table())
        stale = {"mode": "APPLY", "operations": [mirror(target="o1", mirror_object="o2")],
                 "read_set": [{"handle": "o2", "revision": "f" * 64}]}
        # An explicit revision is preserved for live admission to judge, never silently refreshed.
        self.assertEqual(expand_call("angelo_scene_program", stale, table())["read_set"],
                         [{"handle": OTHER, "revision": "f" * 64}])

    def test_undeclared_and_forward_alias_refused_but_earlier_alias_works(self):
        missing = {"mode": "APPLY", "operations": [mirror(mirror_object=OTHER)],
                   "write_set": [{"handle": OBJ, "revision": REV}]}
        with self.assertRaises(ValueError):
            validate_program("angelo_scene_program", missing)
        create = {"kind": "create_primitive", "target": None, "parameters": {"primitive": "CUBE", "alias": "origin"}}
        self.assertEqual(program(create, mirror(mirror_object="$origin"))["operations"][1]
                         ["parameters"]["settings"]["mirror_object"], "$origin")
        with self.assertRaises(ValueError):
            program(mirror(mirror_object="$origin"), create)


class MirrorObligationTests(unittest.TestCase):
    def test_set_and_clear_keep_scalar_checks_and_resolve_canonical_name(self):
        for fields, expected in (({"mirror_object": OTHER}, {"object": OTHER}), ({"clear": True}, None)):
            operation = mirror("modifier_add", **fields)
            del operation["parameters"]["name"]
            checks = required_checks(program(operation))
            self.assertEqual([row["kind"] for row in checks], ["modifier", "modifier_values"])
            self.assertEqual(checks[0]["values"], {})  # No scalar setting stated: the modifier, of its type.
            self.assertEqual(checks[1], {"kind": "modifier_values", "target": OBJ, "name": "Angelo MIRROR",
                "type": "MIRROR", "values": {"mirror_object": expected}, "tolerance": 0.000001})
            self.assertEqual(validate_check(checks[1]), checks[1])
        self.assertEqual(pointers(mirror()), [])

    def test_pointer_survives_only_operations_that_preserve_its_exact_identity(self):
        set_origin = mirror(mirror_object=OTHER)
        expected = pointers(set_origin)
        for follow in (mirror(), mirror(mirror_object=None), mirror(clear=False, use_clip=False),
                       mirror(name="Other", clear=True),
                       {"kind": "modifier_remove", "target": OBJ, "parameters": {"modifier": "Other"}},
                       {"kind": "modifier_apply", "target": OBJ, "parameters": {"modifier": "Other"}},
                       {"kind": "modifier_move", "target": OBJ, "parameters": {"modifier": "Mirror", "index": 0}}):
            with self.subTest(follow=follow):
                self.assertEqual([row for row in pointers(set_origin, follow) if row["name"] == "Mirror"], expected)
        for kind in ("modifier_remove", "modifier_apply"):
            self.assertEqual(pointers(set_origin, {"kind": kind, "target": OBJ,
                                                  "parameters": {"modifier": "Mirror"}}), [])
        self.assertEqual(pointers(set_origin, mirror(clear=True))[0]["values"], {"mirror_object": None})
        self.assertEqual(pointers(mirror(clear=True), set_origin), expected)
        self.assertEqual(pointers(set_origin, mirror(mirror_object="$other"))[0]["values"],
                         {"mirror_object": {"object": "$other"}})
        # Unknown/operator effects retain the existing conservative invalidation.
        from angelo_agent.derived_checks import _keeps, MODIFIER_KEEPS
        self.assertFalse(_keeps(expected[0], MODIFIER_KEEPS,
                                {"kind": "operator_run", "target": OBJ, "parameters": {}}))
        generic = dict(expected[0], values={"mirror_object": {"object": OTHER}, "show_viewport": True})
        self.assertFalse(_keeps(generic, MODIFIER_KEEPS, validate_operation(mirror())))

    def test_authored_checks_cannot_replace_mandatory_pointer_or_scalar_checks(self):
        required = pointers(mirror(mirror_object=OTHER))[0]
        authored = dict(copy.deepcopy(required), tolerance=100)
        authored["values"]["mirror_object"] = None
        checks = required_checks(program(mirror(mirror_object=OTHER), checks=[authored]))
        self.assertIn(required, checks)
        self.assertIn(authored, checks)
        self.assertEqual(len(checks), 3)

    def test_frozen_obligations_bind_both_pointer_intent_and_check(self):
        programs = [program(mirror(**settings)) for settings in ({}, {"mirror_object": OTHER}, {"clear": True})]
        obligations = [resolve_obligations(request) for request in programs]
        self.assertEqual(len({bundle.identity for bundle in obligations}), 3)
        self.assertEqual(len({bundle.program_digest for bundle in obligations}), 3)
        for request, bundle in zip(programs, obligations):
            bundle.assert_integrity(request)
            changed = copy.deepcopy(request)
            settings = changed["operations"][0]["parameters"]["settings"]
            settings["clear"] = not settings.get("clear", False)
            with self.assertRaises(ValueError):
                bundle.assert_integrity(changed)

    def test_existing_pointer_evaluator_uses_identity_not_shared_display_name(self):
        class Object:
            name = "Identical display name"
        origin, substitute = Object(), Object()
        modifier = SimpleNamespace(mirror_object=origin,
            bl_rna=SimpleNamespace(properties={"mirror_object": prop("POINTER", "mirror_object")}))
        wanted = {"mirror_object": {"object": OTHER}}
        self.assertTrue(evaluate(modifier, wanted, 1000, lambda _: origin)[0])
        self.assertFalse(evaluate(modifier, wanted, 1000, lambda _: substitute)[0])
        self.assertFalse(evaluate(modifier, {"mirror_object": None}, 1000, lambda _: origin)[0])
        modifier.mirror_object = None
        self.assertTrue(evaluate(modifier, {"mirror_object": None}, 0, lambda _: origin)[0])


class MirrorPartialTests(unittest.TestCase):
    # Reuse the actual host completion harness, without rerunning its unrelated tests.
    setUp = partial.PartialCompletionTests.setUp
    tearDown = partial.PartialCompletionTests.tearDown
    stage = partial.PartialCompletionTests.stage
    deliver = partial.PartialCompletionTests.deliver
    assert_uncertified = partial.PartialCompletionTests.assert_uncertified

    def test_passing_mirror_does_not_certify_partial_node_edit(self):
        request = partial.partial_request(large=False)
        target = request["operations"][0]["target"]
        request["operations"].insert(0, validate_operation(mirror(target=target, clear=True)))
        before = resolve_obligations(partial.partial_request(large=False)).unknowns()
        before[0]["operation"] += 1
        self.assertEqual(resolve_obligations(request).unknowns(), before)
        job, result = self.stage(request)
        self.assertTrue(any(row["kind"] == "modifier_values" for row in job.checks))
        self.assertEqual(result["verification"]["status"], "partial")
        operation, result, completion = self.deliver(job, result)
        self.assert_uncertified(operation, result, completion)


if __name__ == "__main__":
    unittest.main()
