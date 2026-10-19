# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Short session-scoped identifiers and the compact per-message suffix (toolset 19).

Pure tables and schema walks, the real scene-query staleness rules over the
host fake scene, and the real Controller and ClaudeProvider over the unbilled
stdio fixture: no Blender, Claude process, credentials or inference.
"""

import copy
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import context_ledger, identifiers  # noqa: E402
from angelo_agent.identifiers import IdentifierError, IdentifierTable, expand_call  # noqa: E402
from angelo_agent.plan_schema import plan_arguments  # noqa: E402
from angelo_agent.program_spec import validate_program  # noqa: E402
from angelo_agent.routing import TOOLSET_VERSION, handoff  # noqa: E402
from angelo_agent.scene_validation import execute as verify_execute  # noqa: E402
from angelo_agent.sessions import SessionStore  # noqa: E402
from angelo_agent.tool_specs import TOOL_MAP, native_plan_schema, validate_call  # noqa: E402
from test_native_cache_diet import ControllerFixture, activity  # noqa: E402
from test_native_round_savings import committed, staged_preview, program  # noqa: E402
from test_native_scene_queries import FakeObject, queries, window  # noqa: E402
from test_native_scene_validation import cube  # noqa: E402

EPOCH, LATER_EPOCH = "a" * 32, "c" * 32
HANDLE, OTHER = "scn:%s:159" % EPOCH, "scn:%s:160" % EPOCH
REVISION, NEWER = "1" * 8 + "b" * 56, "2" * 8 + "d" * 56
SUMMARY = {"role": "system", "text": "Host-verified result (no further model round was needed):\nCommitted.",
           "host_summary": True}


def shown(table, handle=HANDLE, revision=REVISION):
    """What one observation entry looks like to the model."""
    return table.compact({"handle": handle, "object_revision": revision})


class TableTests(unittest.TestCase):
    def test_handles_are_numbered_once_and_revisions_shortened_beside_them(self):
        table = IdentifierTable()
        entry = shown(table)
        self.assertEqual(entry, {"handle": "o1", "object_revision": REVISION[:8]})
        self.assertEqual(shown(table), entry, "Stable: an unchanged object renders identically")
        self.assertEqual(table.compact({"objects": [OTHER, HANDLE]}), {"objects": ["o2", "o1"]})
        self.assertEqual(table.handle("o1"), HANDLE)
        self.assertEqual(table.revision(HANDLE, REVISION[:8]), REVISION)
        self.assertEqual(table.revision(HANDLE, REVISION[:12]), REVISION, "Longer prefixes resolve too")

    def test_long_forms_aliases_names_and_free_text_pass_through(self):
        table = IdentifierTable()
        shown(table)
        for value in (HANDLE, "$cube", "Cube", "o0", "o01"):
            self.assertEqual(table.handle(value), value)
        self.assertEqual(table.revision(HANDLE, REVISION), REVISION)
        text = "Moved %s to the origin" % HANDLE
        self.assertEqual(table.compact({"text": text}), {"text": text}, "Free text is never rewritten")
        alone = table.compact({"revision": NEWER})
        self.assertEqual(alone, {"revision": NEWER}, "A revision without its handle stays whole")

    def test_unknown_or_unshown_identifiers_are_rejected_not_guessed(self):
        table = IdentifierTable()
        shown(table)
        with self.assertRaisesRegex(IdentifierError, "Unknown object handle o2"):
            table.handle("o2")
        with self.assertRaisesRegex(IdentifierError, "was not shown"):
            table.revision(HANDLE, NEWER[:8])
        with self.assertRaisesRegex(IdentifierError, "handle beside it"):
            table.revision("o1", REVISION[:8])

    def test_colliding_revision_prefixes_are_shown_whole_and_never_resolved_by_prefix(self):
        table = IdentifierTable()
        first = shown(table)["object_revision"]
        colliding = REVISION[:8] + "e" * 56
        second = shown(table, revision=colliding)["object_revision"]
        self.assertEqual(second, colliding, "A prefix shared with an earlier revision of the object is not shown")
        with self.assertRaisesRegex(IdentifierError, "ambiguous"):
            table.revision(HANDLE, first)
        self.assertEqual(table.revision(HANDLE, colliding), colliding)
        self.assertEqual(table.revision(HANDLE, REVISION), REVISION)

    def test_forgotten_numbers_are_never_reused(self):
        table = IdentifierTable()
        with patch.object(identifiers, "MAX_HANDLES", 2):
            for uid in range(1, 4):
                shown(table, handle="scn:%s:%d" % (EPOCH, uid))
            with self.assertRaisesRegex(IdentifierError, "Unknown object handle o1"):
                table.handle("o1")
            self.assertEqual(shown(table, handle="scn:%s:1" % EPOCH)["handle"], "o4")

    def test_tables_persist_only_whole_and_only_for_their_native_context(self):
        table = IdentifierTable()
        shown(table)
        shown(table, handle=OTHER, revision=NEWER)
        saved = table.to_dict("native-a")
        loaded = IdentifierTable.load(saved, "native-a")
        self.assertEqual(loaded.handle("o2"), OTHER)
        self.assertEqual(loaded.revision(OTHER, NEWER[:8]), NEWER)
        self.assertEqual(shown(loaded, handle="scn:%s:7" % EPOCH)["handle"], "o3", "Numbering continues")
        self.assertIsNone(IdentifierTable.load(saved, "native-b"))
        for broken in (dict(saved, next=1), dict(saved, handles=saved["handles"] + [["o1", OTHER]]),
                       dict(saved, revisions=[[HANDLE, ["not-hex"]]]), dict(saved, version=2)):
            self.assertIsNone(identifiers.clean(broken), broken)
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory, str(uuid.uuid4()))
            state = store.new("claude_subscription")
            state["runtime_sessions"]["claude_subscription"] = {"native_id": "native-a", "synced_length": 2,
                                                                "toolset_version": TOOLSET_VERSION, "identifiers": saved}
            store.save(state)
            self.assertEqual(store.load()["runtime_sessions"]["claude_subscription"]["identifiers"], saved)
            state["runtime_sessions"]["claude_subscription"]["identifiers"] = dict(saved, native_id="native-b")
            store.save(state)
            self.assertNotIn("identifiers", store.load()["runtime_sessions"]["claude_subscription"])


class StalenessTests(unittest.TestCase):
    """The owners' existing stale rules apply to every expanded short form."""

    def setUp(self):
        queries.invalidate()
        self.obj = FakeObject(101, "Artist mesh", mesh=True)
        self.window = window([self.obj])

    def test_a_short_handle_from_before_an_epoch_change_is_stale_never_rebound(self):
        table = IdentifierTable()
        before = table.compact({"handle": queries.object_handle(self.obj)})["handle"]
        arguments = expand_call("angelo_scene_object", {"object": before}, table)
        self.assertIs(queries.resolve_object(self.window, arguments["object"]), self.obj)
        queries.invalidate()  # Undo, Redo or a reload: a new document epoch.
        stale = expand_call("angelo_scene_object", {"object": before}, table)
        with self.assertRaisesRegex(ValueError, "expired"):
            queries.resolve_object(self.window, stale["object"])
        after = table.compact({"handle": queries.object_handle(self.obj)})["handle"]
        self.assertNotEqual(after, before, "The same object gets a new number in the new epoch")
        self.assertIs(queries.resolve_object(self.window, expand_call(
            "angelo_scene_object", {"object": after}, table)["object"]), self.obj)
        with self.assertRaisesRegex(ValueError, "expired"):
            queries.resolve_object(self.window, table.handle(before))

    def test_a_short_revision_of_an_older_state_is_rejected_exactly_as_before(self):
        table = IdentifierTable()
        handle = queries.object_handle(self.obj)
        old = queries.object_revision(self.window, self.obj)
        short = table.compact({"handle": handle, "object_revision": old})
        self.obj.location = [1.0, 0.0, 0.0]  # An intervening edit.
        current = queries.object_revision(self.window, self.obj)
        self.assertNotEqual(current, old)
        table.compact({"handle": handle, "object_revision": current})  # Shown again later.
        arguments = expand_call("angelo_scene_verify", {
            "read_set": [{"handle": short["handle"], "revision": short["object_revision"]}],
            "checks": [{"kind": "object_type", "target": short["handle"], "expected": "MESH"}]}, table)
        self.assertEqual(arguments["read_set"][0], {"handle": handle, "revision": old})
        self.assertEqual(arguments["checks"][0]["target"], handle)
        bound = cube()
        scene = SimpleNamespace(scene=SimpleNamespace(objects=[bound]))
        fake = ModuleType("angelo_agent.scene_queries")
        fake.SceneQueryError = ValueError
        fake.resolve_object = lambda window, value: bound
        fake.object_revision = lambda window, obj: current
        with patch.dict(sys.modules, {"angelo_agent.scene_queries": fake}):
            with self.assertRaisesRegex(ValueError, "changed since observation"):
                verify_execute(scene, "angelo_scene_verify", arguments)
            fresh = dict(arguments, read_set=[{"handle": handle, "revision": table.revision(handle, current[:8])}])
            self.assertTrue(verify_execute(scene, "angelo_scene_verify", fresh)["passed"])


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.table = IdentifierTable()
        shown(self.table)
        shown(self.table, handle=OTHER, revision=NEWER)

    def test_every_reference_position_of_a_program_resolves(self):
        program = {"mode": "APPLY", "write_set": [{"handle": "o1", "revision": REVISION[:8]}],
                   "read_set": [{"handle": "o2", "revision": NEWER[:8]}],
                   "operations": [
                       {"kind": "transform", "target": "o1", "parameters": {"scale": [2, 1, 1]}},
                       {"kind": "parent_set", "target": "o1", "parameters": {"parent": "o2"}},
                       {"kind": "modifier_add", "target": "o1", "parameters": {
                           "type": "BOOLEAN", "name": "o2", "settings": {"operand": "o2"}}},
                       {"kind": "constraint_add", "target": "o1", "parameters": {"type": "COPY_LOCATION", "target": "o2"}},
                       {"kind": "create_primitive", "target": None, "parameters": {
                           "primitive": "CUBE", "name": "o1", "alias": "o9"}}],
                   "expected_checks": [{"kind": "parent", "target": "o1", "expected": "o2"},
                                       {"kind": "object_name", "target": "$o9", "expected": "o1"}]}
        resolved = expand_call("angelo_scene_program", program, self.table)
        operations, checks = resolved["operations"], resolved["expected_checks"]
        self.assertEqual(resolved["write_set"], [{"handle": HANDLE, "revision": REVISION}])
        self.assertEqual(resolved["read_set"], [{"handle": OTHER, "revision": NEWER}])
        self.assertEqual([operation["target"] for operation in operations], [HANDLE] * 4 + [None])
        self.assertEqual(operations[1]["parameters"]["parent"], OTHER)
        self.assertEqual(operations[2]["parameters"]["settings"]["operand"], OTHER)
        self.assertEqual(operations[2]["parameters"]["name"], "o2", "A modifier name is not a handle")
        self.assertEqual(operations[3]["parameters"]["target"], OTHER)
        self.assertEqual(operations[4]["parameters"]["name"], "o1", "An object name is not a handle")
        self.assertEqual(operations[4]["parameters"]["alias"], "o9")
        self.assertEqual(checks[0], {"kind": "parent", "target": HANDLE, "expected": OTHER})
        self.assertEqual(checks[1]["expected"], "o1", "An expected name is not a handle")
        self.assertEqual(program["write_set"][0]["handle"], "o1", "The model's arguments are not mutated")
        validated = validate_program("angelo_scene_program", resolved, derive_checks=True)
        self.assertEqual(validated["write_set"][0]["handle"], HANDLE)

    def test_recipes_verification_queries_and_views_resolve(self):
        recipe = {"recipe": "bevel-and-shade", "target": "o1", "revision": REVISION[:8],
                  "parameters": {"width": 0.1, "segments": 2}, "mode": "APPLY"}
        self.assertEqual(validate_call("angelo_apply_recipe", expand_call("angelo_apply_recipe", recipe, self.table))
                         ["target"], HANDLE)
        verify = {"read_set": [{"handle": "o1", "revision": REVISION[:8]}],
                  "checks": [{"kind": "armature_binding", "target": "o1", "armature": "o2", "modifier": "o2"}]}
        self.assertEqual(expand_call("angelo_scene_verify", verify, self.table)["checks"][0],
                         {"kind": "armature_binding", "target": HANDLE, "armature": OTHER, "modifier": "o2"})
        self.assertEqual(expand_call("angelo_scene_relationships", {"object": "o2"}, self.table)["object"], OTHER)
        self.assertEqual(expand_call("angelo_view", {"targets": ["o1", "o2"]}, self.table)["targets"], [HANDLE, OTHER])

    def test_names_elsewhere_are_never_treated_as_handles(self):
        for name, arguments in (("angelo_scene_search", {"collection": "o1", "material": "o2", "query": "o1"}),
                                ("angelo_delegate", {"connection_id": str(uuid.UUID(int=1)), "model": "o1",
                                                     "task": "o2"}),
                                ("angelo_memory_update", {"expected_revision": 1,
                                                          "patch": {"constraints": ["o1"]}})):
            self.assertEqual(expand_call(name, arguments, self.table), arguments, name)
            self.assertEqual(expand_call(name, arguments, None), arguments, name)

    def test_without_a_table_only_long_forms_are_accepted(self):
        with self.assertRaisesRegex(IdentifierError, "not available in this conversation"):
            expand_call("angelo_scene_object", {"object": "o1"}, None)
        with self.assertRaisesRegex(IdentifierError, "full 64-digit"):
            expand_call("angelo_apply_recipe", {"recipe": "uv-prep", "target": HANDLE, "revision": REVISION[:8],
                                                "parameters": {"method": "CUBE"}, "mode": "APPLY"}, None)
        long_form = {"object": HANDLE}
        self.assertEqual(expand_call("angelo_scene_object", long_form, None), long_form)

    def test_new_handle_typed_parameters_are_covered_without_resolver_changes(self):
        """Every operation parameter typed with the shared HANDLE type resolves (native-coverage contract)."""
        from angelo_agent.action_schema_types import HANDLE as HANDLE_TYPE
        from angelo_agent.action_schemas import OPERATION_SCHEMAS
        schema = {"oneOf": [copy.deepcopy(variant) for variant in OPERATION_SCHEMAS.values()]
                  + [{"type": "object", "properties": {"kind": {"const": "future_kind"}, "target": HANDLE_TYPE,
                                                        "parameters": {"type": "object",
                                                                       "properties": {"follow": HANDLE_TYPE}}}}]}
        value = {"kind": "future_kind", "target": "o1", "parameters": {"follow": "o2"}}
        handles, check_types = identifiers._reference_schemas()
        resolved = identifiers._expand(value, schema, self.table, handles, check_types)
        self.assertEqual(resolved, {"kind": "future_kind", "target": HANDLE, "parameters": {"follow": OTHER}})

    def test_reference_typed_fields_match_the_owners_reference_lists(self):
        """Positions the resolver expands are exactly what program/check validation binds."""
        from angelo_agent.action_catalog import affected_references
        from angelo_agent.validation_spec import check_references
        program = {"mode": "APPLY", "write_set": [{"handle": "o1", "revision": REVISION[:8]}],
                   "read_set": [{"handle": "o2", "revision": NEWER[:8]}], "operations": [
                       {"kind": "armature_bind", "target": "o1", "parameters": {
                           "armature": "o2", "groups": [{"bone": "o1", "weights": [{"vertex": 0, "weight": 1}]}]}}],
                   "expected_checks": [{"kind": "armature_binding", "target": "o1", "armature": "o2",
                                        "modifier": "o1", "every_vertex_weighted": True, "tolerance": 0}]}
        resolved = expand_call("angelo_scene_program", program, self.table)
        self.assertEqual(set(affected_references(resolved["operations"][0])), {HANDLE, OTHER})
        self.assertEqual(resolved["operations"][0]["parameters"]["groups"][0]["bone"], "o1", "A bone name")
        self.assertEqual(set(check_references(resolved["expected_checks"][0])), {HANDLE, OTHER})


class PlanContractTests(unittest.TestCase):
    def test_the_plan_schema_teaches_short_identifiers_and_optional_defaults(self):
        schema = native_plan_schema()
        self.assertEqual(schema["required"], ["operations"], "verify defaults to false; an edit's reply is optional")
        encoded = json.dumps(schema)
        self.assertNotIn("scn:", encoded, "The model is shown only the short handle form")
        self.assertNotIn("{64}", encoded)
        recipe = plan_arguments(TOOL_MAP["angelo_apply_recipe"])
        self.assertEqual(recipe["properties"]["target"], identifiers.SHORT_HANDLE_SCHEMA)
        self.assertEqual(recipe["properties"]["revision"], identifiers.SHORT_REVISION_SCHEMA)
        program = TOOL_MAP["angelo_scene_program"]["inputSchema"]
        self.assertEqual(program["required"], ["mode", "operations"])

    def test_write_set_alone_binds_and_validation_stays_strict(self):
        transform = {"kind": "transform", "target": HANDLE, "parameters": {"scale": [2, 1, 1]}}
        program = {"mode": "APPLY", "write_set": [{"handle": HANDLE, "revision": REVISION}], "operations": [transform]}
        canonical = validate_call("angelo_scene_program", program)
        self.assertEqual((canonical["read_set"], canonical["write_set"]),
                         ([], [{"handle": HANDLE, "revision": REVISION}]))
        create = {"mode": "APPLY", "operations": [{"kind": "create_primitive", "target": None,
                                                   "parameters": {"primitive": "CUBE"}}]}
        self.assertEqual(validate_call("angelo_scene_program", create)["write_set"], [])
        for broken, message in (
                (dict(program, write_set=[]), "write set|read or write set"),
                (dict(program, write_set=[], read_set=[{"handle": HANDLE, "revision": REVISION}]), "write set"),
                (dict(program, read_set=[{"handle": HANDLE, "revision": NEWER}]), "identical revisions"),
                ({"operations": [transform]}, "Missing tool argument")):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                validate_call("angelo_scene_program", broken)


class CompactSuffixTests(unittest.TestCase):
    OBSERVATION = {"context_version": 2, "scene": "Scene", "object_count": 3, "objects": [
        {"name": "Eval Cube", "type": "MESH", "handle": HANDLE, "location": [0.0, 0.0, 0.0], "selected": False,
         "active": False, "mentioned": True, "recently_changed": True, "object_revision": REVISION},
        {"name": "Cube", "type": "MESH", "handle": OTHER, "location": [1.5, 0.0, 0.0], "selected": True,
         "active": True, "object_revision": NEWER}], "truncated": False, "order": "ACTIVE_FIRST",
        "active_object": OTHER, "mode": "OBJECT", "frame_current": 1, "selected_count": 1,
        "workspace_mode": "MANUAL", "rich_queries": ["angelo_scene_search"], "revision_note": "Bind directly.",
        "mentions": {"matched": 1, "included": 1},
        "changes_since_last_turn": {"status": "TRACKED", "hint": "Hints only.", "objects": [
            {"handle": HANDLE, "name": "Eval Cube", "type": "MESH", "changes": ["transform"]}],
            "objects_overflow": False, "datablocks": [], "selection_changed": False}}

    def test_a_claude_observation_is_short_and_keeps_its_untrusted_label(self):
        table = IdentifierTable()
        text = context_ledger.TurnBlocks(identifiers=table).observation(self.OBSERVATION)
        self.assertTrue(text.startswith(context_ledger.OBSERVATION_HEADER))
        sent = json.loads(text[len(context_ledger.OBSERVATION_HEADER):])
        self.assertEqual(sent["objects"][0], {"name": "Eval Cube", "type": "MESH", "handle": "o1",
                                              "location": [0, 0, 0], "mentioned": True, "recently_changed": True,
                                              "object_revision": REVISION[:8]})
        self.assertEqual(sent["changes_since_last_turn"], {"status": "TRACKED",
                                                           "objects": [{"handle": "o1", "changes": ["transform"]}]})
        for absent in ("revision_note", "rich_queries", "order", "truncated", "Hints only.", "scn:", REVISION):
            self.assertNotIn(absent, text)
        self.assertLess(len(text), len(context_ledger.TurnBlocks().observation(self.OBSERVATION)) * 0.6)

    def test_a_delta_sends_changed_state_and_entries_and_an_epoch_change_is_never_unchanged(self):
        table = IdentifierTable()
        first = context_ledger.TurnBlocks(identifiers=table)
        first.observation(self.OBSERVATION)
        ledger = context_ledger.usable(first.ledger("native"), "native")
        same = context_ledger.TurnBlocks(ledger, identifiers=table)
        repeated = json.loads(same.observation(copy.deepcopy(self.OBSERVATION))[len(context_ledger.OBSERVATION_DELTA_HEADER):])
        self.assertEqual([entry["handle"] for entry in repeated["objects"]], ["o1"], "The mentioned object is re-listed")
        self.assertEqual(repeated["unchanged_objects"], 1)
        unmentioned = copy.deepcopy(self.OBSERVATION)
        del unmentioned["objects"][0]["mentioned"]
        quiet = context_ledger.TurnBlocks(identifiers=table)
        quiet.observation(unmentioned)
        quiet_ledger = context_ledger.usable(quiet.ledger("native"), "native")
        self.assertEqual(context_ledger.TurnBlocks(quiet_ledger, identifiers=table).observation(copy.deepcopy(unmentioned)),
                         context_ledger.OBSERVATION_DELTA_UNCHANGED)
        moved = copy.deepcopy(self.OBSERVATION)
        moved["objects"][0]["location"] = [3.0, 0.0, 0.0]
        moved["frame_current"], moved["truncated"] = 5, True
        text = context_ledger.TurnBlocks(ledger, identifiers=table).observation(moved)
        self.assertTrue(text.startswith(context_ledger.OBSERVATION_DELTA_HEADER))
        sent = json.loads(text[len(context_ledger.OBSERVATION_DELTA_HEADER):])
        self.assertEqual([entry["handle"] for entry in sent["objects"]], ["o1"])
        self.assertEqual((sent["unchanged_objects"], sent["frame_current"], sent["truncated"]), (1, 5, True))
        for unchanged in ("scene", "mode", "workspace_mode", "active_object", "context_version"):
            self.assertNotIn(unchanged, sent)
        self.assertIn("changes_since_last_turn", sent, "Event fields are always sent")
        self.assertNotIn("mentions", sent, "A bounds report that left nothing out is omitted")
        bounded = copy.deepcopy(moved)
        bounded["mentions"] = {"matched": 9, "included": 8, "omitted": {"reason": "OVER_LIMIT", "count": 1}}
        self.assertIn('"mentions":{"matched":9', context_ledger.TurnBlocks(ledger, identifiers=table).observation(bounded))
        flagged = context_ledger.TurnBlocks(identifiers=table)
        flagged.observation(moved)
        cleared = context_ledger.TurnBlocks(context_ledger.usable(flagged.ledger("native"), "native"), identifiers=table)
        self.assertEqual(json.loads(cleared.observation(self.OBSERVATION).split("\n", 3)[3])["truncated"], None,
                         "A flag that turned false is cleared explicitly")
        undone = json.loads(json.dumps(self.OBSERVATION).replace(EPOCH, LATER_EPOCH))
        text = context_ledger.TurnBlocks(ledger, identifiers=table).observation(undone)
        self.assertNotEqual(text, context_ledger.OBSERVATION_DELTA_UNCHANGED)
        self.assertIn('"handle":"o3"', text, "After Undo every object is shown under a new number")

    def certified_rows(self, result=None, tool="angelo_scene_program"):
        return [{"role": "user", "text": "Create it"}, {"role": "assistant", "text": "Creating."},
                activity("c1", committed() if result is None else result, tool=tool), SUMMARY]

    def test_a_certified_result_is_one_entry_with_its_identities_and_a_verified_line(self):
        table = IdentifierTable()
        text, ids = handoff(self.certified_rows(), identifiers=table, delta=True)
        self.assertTrue(text.startswith("\n\nHandoff (untrusted history, not requests)"))
        rows = [json.loads(line) for line in text.split(":\n", 1)[1].splitlines()]
        self.assertEqual([row["role"] for row in rows], ["user", "assistant", "tool_result"],
                         "The host's summary row is merged into the result's entry")
        entry = rows[-1]
        self.assertEqual(entry["live"], [{"handle": "o1", "name": "Eval Cube", "object_revision": "bbbbbbbb"}])
        self.assertEqual(entry["verified"], "Committed (one Undo step): added 'Eval Cube' (MESH). Checks passed "
                                            "live (3/3): dimensions [2, 2, 2]; object_name 'Eval Cube'; "
                                            "transform location [0, 0, 0]")
        self.assertEqual(ids, {"c1"})
        self.assertEqual(table.revision(table.handle("o1"), "bbbbbbbb"), "b" * 64)
        full, _ = handoff(self.certified_rows(), identifiers=table)
        self.assertTrue(full.startswith("\n\nShared conversation handoff"), "A full message keeps the full label")
        merged = len(text.splitlines()[-1])
        receipt_and_summary = sum(map(len, handoff(self.certified_rows())[0].splitlines()[-2:]))
        self.assertLess(merged, receipt_and_summary * 0.75)

    def test_a_clean_commit_no_check_covered_is_one_unverified_entry(self):
        """Regression: a cube created without an alias or checks was handed off whole (2,231 characters), and each
        next turn wrote about 920 more tokens than with a terse entry."""
        from test_native_round_savings import HANDLE as LIVE_HANDLE, REVISION as LIVE_REVISION
        result = committed(checks=[])
        result.update(delta={"added": [{"name": "Eval Cube", "type": "MESH", "vertices": 8}], "removed": [],
                             "modified": [], "truncated": False, "geometry": [{"name": "Eval Cube", "type": "MESH",
                             "bounds": [[-1.0, -1.0, -1.0], [1.0, 1.0, 1.0]], "matrix": [1.0, 0.0, 0.0, 0.0] * 4}]},
                      operation_results=[{"kind": "create_primitive", "target": None, "object": "Eval Cube",
                                          "created": True, "alias": None}], undo_published=True)
        rows = [{"role": "user", "text": "Create it"}, {"role": "assistant", "text": "Creating."},
                activity("c1", result)]
        table = IdentifierTable()
        text, ids = handoff(rows, identifiers=table, delta=True)
        entry = json.loads(text.splitlines()[-1])
        self.assertEqual(entry, {"role": "tool_result", "tool": "angelo_scene_program",
                                 "unverified": "Committed (one Undo step): added 'Eval Cube' (MESH). No host check "
                                               "covered it, so nothing was verified",
                                 "live": [{"handle": "o1", "name": "Eval Cube", "object_revision": "bbbbbbbb"}]})
        self.assertEqual(ids, {"c1"}, "Its receipt is not repeated in task memory")
        raw = len(json.dumps({"role": "tool_result", "tool": "angelo_scene_program",
                              "result": json.dumps(result, separators=(",", ":"))}, separators=(",", ":")))
        self.assertLess(len(text.splitlines()[-1]), raw / 3)
        # A live row the same message's observation already shows with that revision is left out.
        observed = IdentifierTable()
        observation = {"objects": [{"handle": LIVE_HANDLE, "object_revision": LIVE_REVISION}]}
        observed.compact(observation)
        observed.observe(observation)
        self.assertNotIn("live", json.loads(handoff(rows, identifiers=observed, delta=True)[0].splitlines()[-1]))
        # Anything unfinished, uncertain or checked keeps its whole result; so does a full (long-form) handoff.
        for changed in (dict(result, partial_changes_possible=True), dict(result, undoable=False),
                        dict(result, status="preview"), dict(result, cleanup_pending=True),
                        dict(result, replayed=True), dict(result, journal_durable=False),
                        dict(result, verification={"status": "partial", "unknowns": [{"reason": "GEOMETRY"}]}),
                        dict(result, changes=dict(result["changes"], truncated=True)),
                        dict(result, checks=committed()["checks"])):
            with self.subTest(changed={key: value for key, value in changed.items() if result.get(key) != value}):
                text, _ = handoff(rows[:2] + [activity("c1", changed)], identifiers=IdentifierTable(), delta=True)
                self.assertIn('"result":"', text)
        for tool, status in (("angelo_apply_recipe", "completed"), ("angelo_scene_program", "failed")):
            text, _ = handoff(rows[:2] + [activity("c1", result, tool=tool, status=status)],
                              identifiers=IdentifierTable(), delta=True)
            self.assertNotIn('"unverified"', text)
        self.assertIn('"result":', handoff(rows)[0], "A context without short identifiers reads the whole result")

    def test_failures_and_uncertain_results_keep_their_full_result(self):
        table = IdentifierTable()
        for result in (dict(committed(), success=False, status="failed", error="Expected check failed"),
                       dict(committed(), partial_changes_possible=True)):
            text, _ = handoff(self.certified_rows(result), identifiers=table, delta=True)
            self.assertIn('"result":"', text)
            self.assertIn("Host-verified result", text, "No merged line, so the summary row stays")
            self.assertNotIn("scn:", text, "Identifiers in a full result are short too")

    def test_previews_and_human_accepts_keep_what_the_next_plan_needs(self):
        table = IdentifierTable()
        preview = {key: value for key, value in staged_preview().items() if key != "views"}
        text, _ = handoff(self.certified_rows(preview), identifiers=table, delta=True)
        entry = json.loads(text.splitlines()[-1])
        self.assertEqual(entry["preview_id"], "5ff850ee-6313-4fd5-94fd-c62f452c26d9")
        self.assertTrue(entry["verified"].startswith("PREVIEW staged; live scene unchanged until Accept"))
        from contextlib import ExitStack
        from angelo_agent.controller import Controller
        from program_completion_fixtures import completed_job
        with tempfile.TemporaryDirectory() as directory, ExitStack() as cleanup:
            controller = Controller(directory)
            cleanup.callback(controller.close)
            controller.set_document(("identifier-accept-fixture", 1))
            controller.busy = True
            arguments = {"preview_id": preview["preview_id"]}
            controller.request_tool("ui/1", "angelo_accept_preview", arguments, lambda _: None)
            call = next(iter(controller.pending.values()))
            call.local = True
            controller.emit("message", {"role": "user", "text": "Accept (human UI action; no model request).",
                "request_kind": "native_preview_accept", "preview_id": preview["preview_id"], "call_id": call.id})
            controller.native_jobs[call.id] = completed_job(program("PREVIEW")["arguments"], committed())
            controller.finish_tool(call, committed())
            controller.pump_events()
            accept = copy.deepcopy(controller.state["messages"])
        entry = json.loads(handoff(accept, identifiers=table)[0].splitlines()[-1])
        self.assertTrue(entry["verified"].startswith("Accepted preview, committed (one Undo step)"))
        self.assertEqual(entry["live"][0]["handle"], "o1")
        raw_history = [row for row in accept if not row.get("host_summary")]
        raw = json.loads(handoff(raw_history, identifiers=table)[0].splitlines()[-1])
        self.assertNotIn("verified", raw, "Human intent and saved result JSON do not recreate private proof")



class OfflinePlans(ControllerFixture):
    """The real Controller and provider: resolution before validation and dispatch."""

    def observation(self):
        return {"scene": "Scene", "object_count": 1, "objects": [
            {"name": "Eval Cube", "type": "MESH", "handle": HANDLE, "location": [0.0, 0.0, 0.0],
             "selected": True, "active": True, "object_revision": REVISION}]}

    def transform(self, handle="o1", revision=REVISION[:8]):
        return {"tool": "angelo_scene_program", "arguments": {
            "mode": "APPLY", "write_set": [{"handle": handle, "revision": revision}],
            "operations": [{"kind": "transform", "target": handle, "parameters": {"scale": [2, 1, 1]}}]}}

    def test_a_short_form_plan_runs_with_long_forms_and_omits_verify(self):
        self.executor.results = [committed()]
        self.plans = [{"reply": "Scaling it.", "operations": [self.transform()]}]
        prompt = self.turn("Scale it.", observation=self.observation())
        self.assertIn('"handle":"o1"', prompt)
        self.assertNotIn(HANDLE, prompt)
        call = next(m for m in self.c.state["messages"] if m.get("tool") == "angelo_scene_program")
        self.assertEqual(call["arguments"]["write_set"], [{"handle": HANDLE, "revision": REVISION}])
        self.assertEqual(call["arguments"]["operations"][0]["target"], HANDLE)
        self.assertEqual(self.executor.calls, ["angelo_scene_program"])
        saved = self.entry()["identifiers"]
        self.assertEqual((saved["native_id"], saved["handles"][0]), ("native-1", ["o1", HANDLE]))

    def test_an_unknown_short_handle_is_rejected_before_anything_runs(self):
        self.plans = [{"reply": "Scaling it.", "operations": [self.transform(handle="o7")], "verify": False},
                      {"reply": "Which object?", "operations": [], "verify": False}]
        self.turn("Scale it.", observation=self.observation())
        self.assertEqual(self.executor.calls, [])
        self.assertIn("Unknown object handle o7", self.prompts[1])
        self.assertFalse(any(m.get("tool") == "angelo_scene_program" for m in self.c.state["messages"]))

    def test_a_restart_keeps_the_numbering_and_a_lost_table_falls_back_to_long_forms(self):
        self.turn("Look.", observation=self.observation())
        self.c.save()
        self.c.close()
        self.c = self.controller(session_id=self.session)
        self.executor.results = [committed()]
        self.plans = [{"reply": "Scaling it.", "operations": [self.transform()], "verify": False}]
        self.turn("Scale it.", observation=self.observation())
        self.assertEqual(self.executor.calls, ["angelo_scene_program"], "o1 still means the same handle")
        self.entry().pop("identifiers")
        self.c.save()
        self.c.close()
        self.c = self.controller(session_id=self.session)
        self.plans = [{"reply": "Scaling it.", "operations": [self.transform()], "verify": False},
                      {"reply": "Use full handles.", "operations": [], "verify": False}]
        moved = self.observation()
        moved["objects"][0]["location"] = [1.0, 0.0, 0.0]
        self.turn("Scale it again.", observation=moved)
        self.assertIn(HANDLE, self.prompts[-2], "A resumed context without its table sees long forms")
        self.assertNotIn('"handle":"o1"', self.prompts[-2])
        self.assertIn("not available in this conversation", self.prompts[-1])
        self.assertEqual(self.executor.calls, ["angelo_scene_program"], "Nothing else ran")

    def test_an_observation_the_provider_inspects_itself_is_short_too(self):
        import time
        from angelo_agent.controller import ToolDispatchBudget
        self.executor.results = [dict(self.observation(), success=True)]
        self.c.send("Look.", None)  # No turn context: the provider inspects the scene.
        deadline = time.monotonic() + 5
        while (self.c.busy or (self.c._worker and self.c._worker.is_alive())) and time.monotonic() < deadline:
            self.c.pump_events()
            self.c.pump_tools(self.executor, self.c.document, budget=ToolDispatchBudget(seconds=60))
            time.sleep(.002)
        self.c.pump_events()
        self.assertEqual(self.executor.calls, ["angelo_inspect_scene"])
        self.assertIn('"handle":"o1"', self.prompts[-1])
        self.assertNotIn(HANDLE, self.prompts[-1])

    def test_an_older_toolset_context_starts_a_fresh_table(self):
        self.turn("Look.", observation=self.observation())
        self.entry().pop("identifiers")
        # A context recorded before short identifiers existed (not merely the previous toolset).
        self.entry()["toolset_version"] = self.c.state["toolset_version"] = identifiers.FIRST_TOOLSET - 1
        prompt = self.turn("Look again.", observation=self.observation())
        self.assertIn('"handle":"o1"', prompt)
        self.assertEqual(self.c.resolve_call("angelo_scene_object", {"object": "o1"})["object"], HANDLE)

    def _codex_turn(self, codex, provider):
        import time
        with patch("angelo_agent.controller.CodexProvider", provider):
            codex.send("Look.", self.observation())
            deadline = time.monotonic() + 5
            while (codex.busy or (codex._worker and codex._worker.is_alive())) and time.monotonic() < deadline:
                codex.pump_events()
                time.sleep(.002)
            codex.pump_events()
        return provider.instances[-1]

    def test_codex_chat_threads_get_short_identifiers(self):
        from angelo_agent.controller import Controller
        from test_native_routing import FakeProvider
        FakeProvider.instances = []
        codex = Controller(self.directory.name + "/codex")
        codex.set_document(("scratch", 1))
        codex.choose_connection("codex_subscription", {"model": ""}, "", None, [], "")
        provider = self._codex_turn(codex, FakeProvider)
        self.assertNotIn(HANDLE, provider.prompt)
        self.assertIn('"o1"', provider.prompt)
        self.assertIsNotNone(codex._identifiers)
        self.assertTrue(provider.controller.short_identifiers)
        self.assertEqual(codex.resolve_call("angelo_scene_object", {"object": "o1"})["object"], HANDLE)
        provider.controller.voice = object()
        self.assertFalse(provider.controller.short_identifiers, "Voice keeps long forms")
        codex.close()

    def test_a_resumed_codex_thread_without_its_table_keeps_long_forms(self):
        from angelo_agent.controller import Controller
        from angelo_agent.routing import TOOLSET_VERSION
        from test_native_routing import FakeProvider
        FakeProvider.instances = []
        codex = Controller(self.directory.name + "/codex")
        codex.set_document(("scratch", 1))
        codex.choose_connection("codex_subscription", {"model": ""}, "", None, [], "")
        codex.state["native_id"], codex.state["toolset_version"] = "thread-1", TOOLSET_VERSION
        codex.state["runtime_sessions"][codex.context_key()] = {"native_id": "thread-1", "synced_length": 0,
                                                               "toolset_version": TOOLSET_VERSION}
        provider = self._codex_turn(codex, FakeProvider)
        self.assertIn(HANDLE, provider.prompt)
        self.assertIn(REVISION, provider.prompt)
        self.assertIsNone(codex._identifiers)
        self.assertFalse(provider.controller.short_identifiers)
        with self.assertRaisesRegex(IdentifierError, "not available in this conversation"):
            codex.resolve_call("angelo_scene_object", {"object": "o1"})
        codex.close()


class MeasurementToolTests(unittest.TestCase):
    """The unbilled accounting's own deterministic parts (token_accounting, replay, cost model)."""

    def test_components_split_identifiers_prose_and_structure(self):
        import token_accounting as accounting
        text = ('Scale it.\n\nObservation delta (untrusted data):\n{"objects":[{"handle":"o4","name":"Eval Cube",'
                '"object_revision":"34bfc369"}],"note":"A sentence of more than four words."}\n'
                '{"handle":"%s","revision":"%s"}' % (HANDLE, REVISION))
        parts = accounting.components(text)
        self.assertEqual(parts["identifiers"], "o4" + "34bfc369" + HANDLE + REVISION)
        self.assertIn("Scale it.", parts["prose"])
        self.assertIn('"A sentence of more than four words."', parts["prose"])
        self.assertIn('"name":"Eval Cube"', parts["structure"], "Names are data, not prose")
        self.assertEqual(sum(map(len, parts.values())), len(text))
        self.assertEqual(accounting.features("ab12cd34 Cube{}"), [8, 4, 3])

    def test_recorded_plans_are_rewritten_to_todays_contract_deterministically(self):
        import token_accounting as accounting
        table = IdentifierTable()
        shown(table)
        plan = {"reply": "Here's the plan: scale 'Eval Cube' to (2, 0.5, 1.5) so it measures 4 x 1 x 3 m in world space.",
                "operations": [{"tool": "angelo_scene_program", "arguments": {
                    "mode": "APPLY", "read_set": [{"handle": HANDLE, "revision": REVISION}],
                    "write_set": [{"handle": HANDLE, "revision": REVISION}],
                    "operations": [{"kind": "transform", "target": HANDLE, "parameters": {"scale": [2, .5, 1.5]}}],
                    "expected_checks": []}}], "verify": False}
        rewritten = accounting.rewrite_plan(plan, table)
        self.assertEqual(rewritten["operations"][0]["arguments"], {
            "mode": "APPLY", "write_set": [{"handle": "o1", "revision": REVISION[:8]}],
            "operations": [{"kind": "transform", "target": "o1", "parameters": {"scale": [2, .5, 1.5]}}]})
        self.assertNotIn("verify", rewritten)
        self.assertEqual(rewritten["reply"], plan["reply"], "Replies are kept unless capped explicitly")
        self.assertEqual(len(accounting.rewrite_plan(plan, table, reply_words=15)["reply"].split()), 15)
        resolved = expand_call("angelo_scene_program", rewritten["operations"][0]["arguments"], table)
        self.assertEqual(validate_call("angelo_scene_program", resolved)["write_set"][0]["revision"], REVISION)

    def test_recorded_deltas_are_rebuilt_into_the_observations_they_came_from(self):
        import replay_turn_context as replay
        first = {"objects": [{"handle": "a"}, {"handle": "b"}, {"handle": "c"}], "order": "X", "mode": "OBJECT",
                 "changes_since_last_turn": {"status": "TRACKED", "hint": "Hints."}}
        delta = {"objects": [{"handle": "b", "moved": True}, {"handle": "d"}], "mode": "EDIT",
                 "changes_since_last_turn": {"status": "TRACKED"}, "static_notes": "unchanged",
                 "objects_scope": "CHANGED", "unchanged_objects": 1, "objects_no_longer_listed": ["c"]}
        self.assertEqual(replay.full_observation(first, delta), {
            "order": "X", "mode": "EDIT", "changes_since_last_turn": {"status": "TRACKED", "hint": "Hints."},
            "objects": [{"handle": "a"}, {"handle": "b", "moved": True}, {"handle": "d"}]})
        self.assertIs(replay.full_observation(first, None), first, "An 'unchanged' line is the previous one")

    def test_the_cost_model_uses_opus_prices(self):
        import long_session_cost_model as cost
        self.assertEqual((cost.READ, cost.WRITE, cost.OUTPUT, cost.INPUT), (0.20, 8.0, 20.0, 4.0))


if __name__ == "__main__":
    unittest.main()
