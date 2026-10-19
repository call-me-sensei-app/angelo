# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private capture DTO checks; synthetic declarations never verify Sculpt bytes.

Only legacy storage controls write synthetic scene bytes. No native facade or
runtime companion is fabricated, and no scene consumer gains v4 support.
"""

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.checkpoint_capture_records import (
    validate_receipt, validate_record, validate_transition, file_identity, output_names,
)
from angelo_agent.checkpoint_filesystem import MAX_METADATA_BYTES
from angelo_agent.checkpoint_records import _record
from angelo_agent.checkpoints import CheckpointStore

# An absolute workspace on this host (a journal refuses a relative one; "/x" has no drive on Windows).
WORKSPACE = os.path.abspath("/synthetic/workspace")


CAPTURE = "12345678-1234-4234-8234-123456789abc"
OTHER = "12345678-1234-4234-8234-123456789abd"
SESSION = "23456789-2345-4345-8345-23456789abcd"
BACKUP = "34567890-3456-4456-8456-34567890abcd"
SEAL = "b" * 32 + "." + "c" * 64  # Structure only; checkpoint_seal verifies a real one.


def reservation():
    return {"version": 4, "id": CAPTURE, "created": 1.5,
            "original_path": "/synthetic/project.blend", "boundary": 10,
            "files": [], "state": "RESERVED", "purpose": "request",
            "capture_mode": "EVERY_REQUEST", "protected": False,
            "capture_validation": "PENDING"}


def declaration():
    return {"capture_id": CAPTURE, "scene_bytes": 64, "scene_hash": "a" * 64,
            "sculpt": {"filename": "sculpt.runtime", "format_version": 1,
                       "bytes": 128, "hash": "b" * 64, "owner_count": 2}}


def ready(validation="PENDING", storage="full"):
    value = reservation()
    value.update({key: item for key, item in declaration().items() if key != "capture_id"})
    value.update(state="READY", storage=storage, capture_validation=validation)
    return value


def journal():
    return {"root": WORKSPACE, "path": "asset.txt", "existed": True,
            "backup": BACKUP + ".bin", "mode": 0o600, "sequence": 11,
            "window": 10, "phase": "ready", "effect_sequence": 11,
            "effect_kind": "file_tool"}


def tombstone(validation="VALIDATED"):
    value = reservation()
    value.update(state="PRUNED", capture_validation=validation, pruned_at=2,
                 prune_reason="retention", bytes_freed=192)
    return value


class CaptureRecordStructureTests(unittest.TestCase):
    def test_reservation_has_no_manufactured_component_facts(self):
        value = validate_record(reservation(), CAPTURE)
        self.assertEqual(value, reservation())
        self.assertFalse({"scene_bytes", "scene_hash", "sculpt", "storage"} & value.keys())

    def test_complete_declared_states_and_storage_are_structurally_distinct(self):
        for state in ("PENDING", "VALIDATED", "INTERRUPTED_VALID", "INVALID"):
            for storage in ("full", "store"):
                with self.subTest(state=state, storage=storage):
                    value = ready(state, storage)
                    if state == "PENDING" and storage == "store":
                        with self.assertRaises(ValueError):
                            validate_record(value, CAPTURE)
                    else:
                        self.assertEqual(validate_record(value, CAPTURE), value)

    def test_absent_ready_storage_defaults_to_full(self):
        value = ready()
        del value["storage"]
        self.assertEqual(validate_record(value, CAPTURE)["storage"], "full")

    def test_tombstone_has_no_live_payload_or_journal(self):
        self.assertEqual(validate_record(tombstone(), CAPTURE), tombstone())
        for key, item in (("files", [journal()]), ("protected", True),
                          ("scene_bytes", 64), ("sculpt", declaration()["sculpt"]),
                          ("storage", "full")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_record(dict(tombstone(), **{key: item}), CAPTURE)
        for key in ("pruned_at", "prune_reason", "bytes_freed"):
            value = tombstone()
            del value[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                validate_record(value, CAPTURE)

    def test_receipt_is_an_independent_plain_declaration(self):
        source = declaration()
        result = validate_receipt(source, CAPTURE)
        self.assertIs(type(result), dict)
        self.assertEqual(result, source)
        self.assertIsNot(result["sculpt"], source["sculpt"])
        source["sculpt"]["hash"] = "c" * 64
        self.assertEqual(result["sculpt"]["hash"], "b" * 64)

    def test_record_deep_copies_journal_and_component_declarations(self):
        source = ready("VALIDATED")
        source["files"] = [journal()]
        result = validate_record(source, CAPTURE)
        source["files"][0]["path"] = "different.txt"
        source["sculpt"]["hash"] = "c" * 64
        self.assertEqual(result["files"][0]["path"], "asset.txt")
        self.assertEqual(result["sculpt"]["hash"], "b" * 64)

    def test_identity_is_canonical_and_bound_to_requested_capture(self):
        for identity in (OTHER, CAPTURE.upper(), "../capture", "", True):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                validate_record(reservation(), identity)
            with self.subTest(receipt_identity=identity), self.assertRaises(ValueError):
                validate_receipt(declaration(), identity)
        value = declaration()
        value["capture_id"] = OTHER
        with self.assertRaises(ValueError):
            validate_receipt(value, CAPTURE)

    def test_future_version_unknown_keys_and_fixed_runtime_name_are_rejected(self):
        for update in ({"version": 3}, {"version": 5}, {"extra": 1},
                       {"capture_validation": "COMPLETE"}, {"state": "MISSING"}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                validate_record(dict(reservation(), **update), CAPTURE)
        for field, item in (("filename", "../sculpt.runtime"), ("filename", "other.bin"),
                            ("format_version", 2), ("format_version", True), ("extra", 1)):
            value = declaration()
            value["sculpt"][field] = item
            with self.subTest(field=field, item=item), self.assertRaises(ValueError):
                validate_receipt(value, CAPTURE)
        value = declaration()
        value["extra"] = 1
        with self.assertRaises(ValueError):
            validate_receipt(value, CAPTURE)

    def test_receipt_and_ready_require_the_complete_component_set(self):
        for field in ("scene_bytes", "scene_hash", "sculpt"):
            value = ready()
            del value[field]
            with self.subTest(record_missing=field), self.assertRaises(ValueError):
                validate_record(value, CAPTURE)
        for field in declaration():
            value = declaration()
            del value[field]
            with self.subTest(receipt_missing=field), self.assertRaises(ValueError):
                validate_receipt(value, CAPTURE)
        for field in declaration()["sculpt"]:
            value = declaration()
            del value["sculpt"][field]
            with self.subTest(runtime_missing=field), self.assertRaises(ValueError):
                validate_receipt(value, CAPTURE)

    def test_reserved_cannot_claim_even_partial_component_output(self):
        for field, item in (("scene_bytes", 0), ("scene_hash", "a" * 64),
                            ("sculpt", declaration()["sculpt"]), ("storage", "full")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_record(dict(reservation(), **{field: item}), CAPTURE)
        with self.assertRaises(ValueError):
            validate_record(dict(reservation(), capture_validation="VALIDATED"), CAPTURE)

    def test_declared_counts_are_positive_exact_signed_63_bit_integers(self):
        for field in ("scene_bytes", "bytes", "owner_count"):
            for count in (0, -1, True, 1.0, 2**63):
                value = declaration()
                owner = value if field == "scene_bytes" else value["sculpt"]
                owner[field] = count
                with self.subTest(field=field, count=count), self.assertRaises(ValueError):
                    validate_receipt(value, CAPTURE)
            value = declaration()
            owner = value if field == "scene_bytes" else value["sculpt"]
            owner[field] = 2**63 - 1
            self.assertEqual(validate_receipt(value, CAPTURE), value)
        value = ready()
        value["scene_bytes"] = 2**63 - 1
        value["sculpt"]["bytes"] = 2**63 - 1
        self.assertEqual(validate_record(value, CAPTURE)["scene_bytes"], 2**63 - 1)

    def test_digest_shape_is_exact_without_digest_truth_claim(self):
        for invalid in ("A" * 64, "g" * 64, "a" * 63, "a" * 65, bytes(64), None):
            for field in ("scene_hash", "hash"):
                value = declaration()
                (value if field == "scene_hash" else value["sculpt"])[field] = invalid
                with self.subTest(field=field, invalid=invalid), self.assertRaises(ValueError):
                    validate_receipt(value, CAPTURE)
        # A syntactically valid digest remains a declaration with no file I/O.
        self.assertEqual(validate_receipt(declaration(), CAPTURE), declaration())

    def test_common_measurement_validation_remains_finite(self):
        for field in ("created", "capture_ms"):
            for invalid in (True, float("nan"), float("inf"), -1, 2**53 + 1):
                with self.subTest(field=field, invalid=invalid), self.assertRaises(ValueError):
                    validate_record(dict(reservation(), **{field: invalid}), CAPTURE)

    def test_ordered_journals_obey_existing_boundary_and_duplicate_rules(self):
        value = reservation()
        value["files"] = [journal()]
        self.assertEqual(validate_record(value, CAPTURE)["files"], value["files"])
        invalid_rows = []
        without_order = journal()
        for key in ("sequence", "window", "phase", "effect_sequence", "effect_kind"):
            del without_order[key]
        invalid_rows.append([without_order])
        invalid_rows.append([dict(journal(), window=9)])
        invalid_rows.append([journal(), journal()])
        invalid_rows.append([dict(journal(), mode=True)])
        invalid_rows.append([dict(journal(), path="../asset.txt")])
        invalid_rows.append([dict(journal(), sequence=10)])
        for rows in invalid_rows:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                validate_record(dict(value, files=rows), CAPTURE)

    def test_oversized_metadata_and_cycles_fail_explicitly(self):
        value = reservation()
        value["original_path"] = "x" * (MAX_METADATA_BYTES + 1)
        with self.assertRaises(ValueError):
            validate_record(value, CAPTURE)
        value = reservation()
        value["files"].append(value)
        with self.assertRaises(ValueError):
            validate_record(value, CAPTURE)

    def test_input_subclasses_cannot_run_inspection_hooks(self):
        calls = []

        class HookMeta(type):
            def __hash__(cls):
                calls.append("metaclass_hash")
                raise AssertionError("must inspect exact types without hashing them")

        class HookDict(dict, metaclass=HookMeta):
            def items(self):
                calls.append("items")
                raise AssertionError("must reject mapping subclass")

        class HookList(list):
            def __iter__(self):
                calls.append("iter")
                raise AssertionError("must reject sequence subclass")

        class HookString(str):
            def __hash__(self):
                calls.append("hash")
                return str.__hash__(self)

        candidates = [HookDict(reservation()), dict(reservation(), files=HookList()),
                      dict(reservation(), original_path=HookString("x"))]
        odd_key = reservation()
        odd_key[HookString("extra")] = 1
        candidates.append(odd_key)
        calls.clear()
        for value in candidates:
            with self.subTest(kind=type(value).__name__), self.assertRaises(ValueError):
                validate_record(value, CAPTURE)
        self.assertEqual(calls, [])


class CaptureTransitionStructureTests(unittest.TestCase):
    def test_publish_preserves_current_journal_and_protection(self):
        current = reservation()
        current.update(files=[journal()], protected=True)
        candidate = ready()
        candidate.update(files=copy.deepcopy(current["files"]), protected=True)
        result = validate_transition(current, candidate, kind="publish_bundle")
        self.assertEqual(result, candidate)
        self.assertIsNot(result["files"], candidate["files"])

    def test_stale_candidates_cannot_drop_callback_journals_or_protection(self):
        pairs = [(reservation(), ready(), "publish_bundle"),
                 (ready(), ready("VALIDATED"), "finish"),
                 (ready("VALIDATED"), ready("INVALID"), "invalidate"),
                 (ready("VALIDATED"), ready("VALIDATED", "store"), "adopt_base")]
        for before, after, kind in pairs:
            before.update(files=[journal()], protected=True)
            with self.subTest(kind=kind, stale="both"), self.assertRaises(ValueError):
                validate_transition(before, after, kind=kind)
            after["files"] = copy.deepcopy(before["files"])
            with self.subTest(kind=kind, stale="protection"), self.assertRaises(ValueError):
                validate_transition(before, after, kind=kind)

    def test_publication_and_terminal_validation_are_separate_transitions(self):
        for terminal in ("VALIDATED", "INTERRUPTED_VALID", "INVALID"):
            self.assertEqual(validate_transition(ready(), ready(terminal), kind="finish"), ready(terminal))
            with self.subTest(terminal=terminal), self.assertRaises(ValueError):
                validate_transition(reservation(), ready(terminal), kind="publish_bundle")

    def test_invalidation_preserves_published_component_facts(self):
        for terminal in ("VALIDATED", "INTERRUPTED_VALID"):
            self.assertEqual(validate_transition(ready(terminal), ready("INVALID"), kind="invalidate"),
                             ready("INVALID"))
        with self.assertRaises(ValueError):
            validate_transition(ready("INVALID"), ready("VALIDATED"), kind="finish")

    def test_sealed_components_cannot_be_refreshed_during_finish_or_adoption(self):
        for kind, before, after in (("finish", ready(), ready("VALIDATED")),
                                   ("adopt_base", ready("VALIDATED"), ready("VALIDATED", "store"))):
            for field in ("scene_hash", "scene_bytes", "sculpt"):
                candidate = copy.deepcopy(after)
                if field == "sculpt":
                    candidate[field]["hash"] = "c" * 64
                elif field == "scene_hash":
                    candidate[field] = "c" * 64
                else:
                    candidate[field] += 1
                with self.subTest(kind=kind, field=field), self.assertRaises(ValueError):
                    validate_transition(before, candidate, kind=kind)

    def test_component_seal_is_a_published_component_fact(self):
        seal = "a" * 32 + "." + "d" * 64  # <key id>.<HMAC>: a content seal (":" is an identity seal)
        sealed = dict(ready(), component_seal=seal)
        self.assertEqual(validate_record(sealed, CAPTURE)["component_seal"], seal)
        identity = "a" * 32 + ":" + "d" * 64
        self.assertEqual(validate_record(dict(ready(), component_seal=identity), CAPTURE)["component_seal"], identity)
        for bad in ("d" * 64, "A" * 32 + "." + "d" * 64, "a" * 32 + "-" + "d" * 64, seal[:-1], 7):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_record(dict(ready(), component_seal=bad), CAPTURE)
        for value in (dict(reservation(), component_seal=seal), dict(tombstone(), component_seal=seal)):
            with self.subTest(state=value["state"]), self.assertRaises(ValueError):
                validate_record(value, CAPTURE)
        # Publication may add it; finish and adoption may not change it.
        self.assertEqual(validate_transition(reservation(), sealed, kind="publish_bundle")["component_seal"], seal)
        other = "a" * 32 + "." + "e" * 64
        for kind, after in (("finish", dict(ready("VALIDATED"), component_seal=other)),
                            ("adopt_base", dict(ready("VALIDATED", "store"), component_seal=other))):
            before = sealed if kind == "finish" else dict(ready("VALIDATED"), component_seal=seal)
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                validate_transition(before, after, kind=kind)

    def test_identity_and_source_binding_cannot_change(self):
        for field, item in (("id", OTHER), ("created", 2), ("boundary", 11),
                            ("original_path", "/synthetic/other.blend"),
                            ("capture_mode", "BEFORE_FIRST_EDIT"), ("purpose", "safety")):
            candidate = dict(ready(), **{field: item})
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_transition(reservation(), candidate, kind="publish_bundle")

    def test_base_adoption_is_only_a_declared_full_to_store_change(self):
        for terminal in ("VALIDATED", "INTERRUPTED_VALID"):
            self.assertEqual(validate_transition(ready(terminal), ready(terminal, "store"), kind="adopt_base"),
                             ready(terminal, "store"))
        for terminal in ("PENDING", "INVALID"):
            with self.subTest(terminal=terminal), self.assertRaises(ValueError):
                validate_transition(ready(terminal), ready(terminal, "store"), kind="adopt_base")
        with self.assertRaises(ValueError):
            validate_transition(ready("VALIDATED", "store"), ready("VALIDATED"), kind="adopt_base")

    def test_no_runtime_restores_only_the_legacy_shape_with_fresh_journal(self):
        before = reservation()
        before.update(files=[journal()], protected=True)
        candidate = copy.deepcopy(before)
        del candidate["capture_validation"]
        candidate.update(version=3, state="READY", scene_bytes=64, component_seal=SEAL)
        self.assertEqual(validate_transition(before, candidate, kind="no_runtime"), _record(candidate, CAPTURE))
        with self.assertRaisesRegex(ValueError, "needs its seal"):  # Every READY record binds its BASE.
            validate_transition(before, {k: v for k, v in candidate.items() if k != "component_seal"}, kind="no_runtime")
        for field, item in (("capture_validation", "VALIDATED"),
                            ("sculpt", declaration()["sculpt"]), ("scene_hash", "a" * 64),
                            ("files", []), ("protected", False)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_transition(before, dict(candidate, **{field: item}), kind="no_runtime")
        with self.assertRaises(ValueError):
            validate_transition(ready(), candidate, kind="no_runtime")

    def test_no_runtime_keeps_the_existing_legacy_size_limit(self):
        candidate = reservation()
        del candidate["capture_validation"]
        candidate.update(version=3, state="READY", scene_bytes=2**53, component_seal=SEAL)
        self.assertEqual(validate_transition(reservation(), candidate, kind="no_runtime")["scene_bytes"], 2**53)
        for count in (0, True, 2**53 + 1):
            with self.subTest(count=count), self.assertRaises(ValueError):
                validate_transition(reservation(), dict(candidate, scene_bytes=count), kind="no_runtime")

    def test_prune_validates_only_a_payload_free_tombstone(self):
        before = ready("VALIDATED")
        before.update(files=[journal()], protected=True)
        self.assertEqual(validate_transition(before, tombstone(), kind="prune"), tombstone())
        with self.assertRaises(ValueError):
            validate_transition(before, ready("INVALID"), kind="prune")

    def test_prune_can_clear_legacy_diagnostics_but_cannot_replace_them(self):
        before = dict(ready("VALIDATED"), capture_ms=1.0)
        cleared = dict(tombstone(), original_path="")
        self.assertEqual(validate_transition(before, cleared, kind="prune"), cleared)
        retained = dict(tombstone(), capture_ms=1.0)
        self.assertEqual(validate_transition(before, retained, kind="prune"), retained)
        for update in ({"original_path": "/synthetic/other.blend"},
                       {"capture_ms": 2.0}, {"capture_ms": 1}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                validate_transition(before, dict(cleared, **update), kind="prune")
        with self.assertRaises(ValueError):
            validate_transition(ready("VALIDATED"), retained, kind="prune")
        with self.assertRaises(ValueError):
            validate_transition(dict(ready(), capture_ms=1.0), ready("VALIDATED"), kind="finish")

    def test_transition_names_and_repeated_execution_are_closed(self):
        for kind in ("retry", "", None, True):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                validate_transition(ready(), ready("VALIDATED"), kind=kind)
        with self.assertRaises(ValueError):
            validate_transition(ready("VALIDATED"), ready("VALIDATED"), kind="finish")


class ExistingCaptureConsumerIsolationTests(unittest.TestCase):
    def test_default_reserve_and_complete_still_produce_only_version_three(self):
        with tempfile.TemporaryDirectory(prefix="angelo-capture-records-") as temporary:
            store = CheckpointStore(Path(temporary).resolve(), SESSION)
            record, path = store.reserve()
            self.assertEqual(record["version"], 3)
            self.assertEqual(record["state"], "RESERVED")
            self.assertNotIn("capture_validation", record)
            path.write_bytes(b"synthetic ordinary capture")
            completed = store.complete(record["id"])
            self.assertEqual(completed["version"], 3)
            self.assertEqual(completed["state"], "READY")
            self.assertNotIn("capture_validation", completed)
            self.assertFalse((store.root / "checkpoint-captures").exists())

    def test_legacy_validator_still_accepts_versions_one_two_and_three(self):
        for version in (1, 2, 3):
            value = reservation()
            value.pop("capture_validation")
            value.update(version=version, state="READY", scene_bytes=32)
            if version < 3:
                value.pop("boundary")
            self.assertEqual(_record(value, CAPTURE), value)
        stored = dict(value, storage="store", scene_hash="a" * 64)
        self.assertEqual(_record(stored, CAPTURE), stored)
        with self.assertRaises(ValueError):
            _record(dict(value, scene_hash="a" * 64), CAPTURE)

    def test_private_import_does_not_enable_v4_in_existing_validator(self):
        for value in (reservation(), ready(), ready("VALIDATED"), tombstone()):
            validate_record(value, CAPTURE)
            with self.subTest(state=value["state"]), self.assertRaises(ValueError):
                _record(value, CAPTURE)

    def test_actual_legacy_store_load_and_recovery_remain_byte_preserving(self):
        with tempfile.TemporaryDirectory(prefix="angelo-capture-records-") as temporary:
            store = CheckpointStore(Path(temporary).resolve(), SESSION)
            content = b"synthetic legacy storage bytes, not a Blender file"
            for version in (1, 2, 3):
                record = reservation()
                record.pop("capture_validation")
                record.update(version=version, state="READY", scene_bytes=len(content))
                if version < 3:
                    record.pop("boundary")
                folder = store.folder(CAPTURE)
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "checkpoint.json").write_text(json.dumps(record), encoding="utf-8")
                (folder / "scene.blend").write_bytes(content)
                loaded = store.load(CAPTURE)
                self.assertEqual(loaded["version"], version)
                recovered, path = store.recovery_copy(CAPTURE)
                self.assertEqual(recovered, loaded)
                self.assertEqual(path.read_bytes(), content)
                path.unlink()

    def test_actual_load_preserves_v4_but_scene_consumers_refuse(self):
        with tempfile.TemporaryDirectory(prefix="angelo-capture-records-") as temporary:
            store = CheckpointStore(Path(temporary).resolve(), SESSION)
            folder = store.folder(CAPTURE)
            folder.mkdir(parents=True)
            (folder / "scene.blend").write_bytes(b"unverified synthetic base")
            for record in (reservation(), ready(), ready("VALIDATED"), tombstone()):
                (folder / "checkpoint.json").write_text(json.dumps(record), encoding="utf-8")
                with self.subTest(state=record["state"]):
                    self.assertEqual(store.load(CAPTURE), validate_record(record, CAPTURE))
                    with self.assertRaises(ValueError):
                        store.recovery_copy(CAPTURE)
                    self.assertFalse(store.availability(CAPTURE)["available"])
                    self.assertFalse((store.directory / "recoveries").exists())
            self.assertFalse((folder / "sculpt.runtime").exists())



class CaptureOutputLedgerTests(unittest.TestCase):
    def ledger(self):
        return {"version": 1, "directory": file_identity(-7, 2 ** 100),
                "roles": {"BASE": {"created": None}}}

    def record(self):
        return dict(reservation(), capture_outputs=self.ledger())

    def test_projection_roundtrip_and_deep_copy_preserve_full_identity_bits(self):
        source = self.record()
        result = validate_record(source, CAPTURE)
        self.assertEqual(result, source)
        self.assertEqual(result["capture_outputs"]["directory"]["inode"], str(2 ** 100))
        source["capture_outputs"]["roles"]["BASE"]["created"] = file_identity(2, 3)
        self.assertIsNone(result["capture_outputs"]["roles"]["BASE"]["created"])

    def test_canonical_identity_rejects_lossy_or_alternate_spellings(self):
        for value in (True, 3.0, "+1", "01", "-0", "", " 1", "1 ", "1.0", "1e3", "--1", "1" * 129):
            record = self.record()
            record["capture_outputs"]["directory"]["inode"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_record(record, CAPTURE)
        for value in (True, 3.0, "3"):
            with self.assertRaises(ValueError):
                file_identity(value, 1)
        self.assertEqual(file_identity(-(2 ** 100), 0), {"device": str(-(2 ** 100)), "inode": "0"})

    def test_exact_roles_and_ledger_shape(self):
        self.assertEqual(output_names("BASE"), ("scene.blend@", "scene.blend"))
        self.assertEqual(output_names("RUNTIME"), ("sculpt.runtime@", "sculpt.runtime"))
        for role in ("base", "../BASE", "THIRD", True, None):
            with self.assertRaises(ValueError):
                output_names(role)
        for mutate in (lambda x: x.update(extra=1), lambda x: x.update(version=True),
                       lambda x: x.update(roles={}), lambda x: x["roles"].update(THIRD={"created": None}),
                       lambda x: x["roles"]["BASE"].update(filename="outside"),
                       lambda x: x["roles"]["BASE"].update(created={"device": "1"})):
            record = self.record()
            mutate(record["capture_outputs"])
            with self.assertRaises(ValueError):
                validate_record(record, CAPTURE)

    def test_first_and_second_declaration_append_one_empty_slot_only(self):
        first = self.record()
        self.assertEqual(validate_transition(reservation(), first, kind="declare_output"), first)
        second = copy.deepcopy(first)
        second["capture_outputs"]["roles"]["RUNTIME"] = {"created": None}
        self.assertEqual(validate_transition(first, second, kind="declare_output"), second)
        with self.assertRaises(ValueError):
            validate_transition(first, first, kind="declare_output")
        with self.assertRaises(ValueError):
            validate_transition(reservation(), second, kind="declare_output")
        altered = copy.deepcopy(second)
        altered["capture_outputs"]["directory"]["inode"] = "9"
        with self.assertRaises(ValueError):
            validate_transition(first, altered, kind="declare_output")

    def test_record_observation_fills_exactly_one_existing_slot_once(self):
        before = self.record()
        after = copy.deepcopy(before)
        after["capture_outputs"]["roles"]["BASE"]["created"] = file_identity(7, 2 ** 100 + 1)
        self.assertEqual(validate_transition(before, after, kind="record_output"), after)
        with self.assertRaises(ValueError):
            validate_transition(after, after, kind="record_output")
        changed = copy.deepcopy(after)
        changed["capture_outputs"]["roles"]["BASE"]["created"] = file_identity(7, 2 ** 100 + 2)
        with self.assertRaises(ValueError):
            validate_transition(after, changed, kind="record_output")
        with self.assertRaises(ValueError):
            validate_transition(after, before, kind="record_output")
        with self.assertRaises(ValueError):
            validate_transition(reservation(), after, kind="record_output")

    def test_ledger_transition_preserves_journals_protection_and_boundary(self):
        before = reservation()
        before["files"] = [journal()]
        before["protected"] = True
        candidate = dict(before, capture_outputs=self.ledger())
        self.assertEqual(validate_transition(before, candidate, kind="declare_output"), candidate)
        for key, value in (("files", []), ("protected", False), ("boundary", 20), ("original_path", "changed")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_transition(before, dict(candidate, **{key: value}), kind="declare_output")

    def test_no_runtime_and_tombstone_cannot_discard_outstanding_ledger(self):
        after = dict(reservation(), version=3, state="READY", scene_bytes=8)
        del after["capture_validation"]
        with self.assertRaises(ValueError):
            validate_transition(self.record(), after, kind="no_runtime")
        with self.assertRaises(ValueError):
            validate_record(dict(tombstone(), capture_outputs=self.ledger()), CAPTURE)

    def test_old_transitions_cannot_change_ledger_or_grant_creation(self):
        before = self.record()
        after = dict(ready(), capture_outputs=self.ledger())
        self.assertEqual(validate_transition(before, after, kind="publish_bundle"), after)
        after["capture_outputs"]["roles"]["BASE"]["created"] = file_identity(1, 2)
        with self.assertRaises(ValueError):
            validate_transition(before, after, kind="publish_bundle")

if __name__ == "__main__":
    unittest.main()
