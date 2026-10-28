# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private Blender snapshots, durable file journals and retention.

Native UI captures/restores the document on the main thread. No provider thread
serializes bpy here. A READY snapshot is either a full ``scene.blend`` copy or,
after the snapshot worker verified it, a manifest in the deduplicated store
(``storage == "store"``, snapshot_store.py); restore yields the exact captured
bytes either way. Retention operates only on validated, application-owned
files; it never recursively removes folders or deletes a user's project.
"""

from contextlib import ExitStack
import os
import stat
import time
import uuid
from pathlib import Path

from .checkpoint_policy import (CAPTURE_MODES, EVERY_REQUEST, _digest, _file_info,
    _identity, _open_file, _private_directory, _protected_refs, _read_json,
    _replace_file, _unlink_file, _write_json,
    checkpoint_ref, clean_journal, journal_file_limit, plan_retention, restore_evidence)
from .files import BLOCKED, FileWorkspace, relative_path
from .checkpoint_transactions import _transaction, holds_transaction
from .checkpoint_records import _number, _record
from .checkpoint_capture_state import (decode_record, is_bundle, require_capture_idle,
    require_legacy_scene, V4_SCENE_UNAVAILABLE)
from .checkpoint_inventory import inventory
from .checkpoint_seal import copy_sealed, precheck as precheck_seal
from .checkpoint_restore import apply_file_restore, _JournalOwner, _RestoreParticipants
from .checkpoint_timeline import allocate_event, current_kinds, planned_entry, read_timeline, version_key
from .checkpoint_filesystem import (copy_stable_file as _copy_from, held_for_removal, list_names,
    assert_directory_identity, require_safe_deletion, same_file, descriptor_witness, file_witness)
from .snapshot_accounting import store_accounting
from .snapshot_store import SnapshotStore
from .checkpoint_retention import CheckpointRetentionMixin
from .checkpoint_restore_plan import CheckpointRestorePlanMixin

# CheckpointRetentionMixin and CheckpointRestorePlanMixin resolve these names from this module at call time (tests
# patch them here), so an import that looks unused in this file is still in use (test_native_checkpoint_mixins).
_MIXIN_NAMES = (current_kinds, planned_entry, version_key, _JournalOwner, _RestoreParticipants, require_legacy_scene,
                store_accounting, plan_retention, _protected_refs, inventory, held_for_removal, require_safe_deletion)

_CAPTURE_STARTS = {}
_LAST_CREATED = 0

_PRIVATE_NAMES = {"checkpoints", "checkpoint-captures", "checkpoint-scenes", "sessions", "references", "codex-profile", "claude-profile", "snapshot-store",
                  "connections.json", "connections.lock", "checkpoint.lock", "session.json", "checkpoint-policy.json", "performance.json", "checkpoint-timeline.json", "checkpoint-seal"}


def _require_safe_deletion():
    require_safe_deletion()


def _journal_before(record):
    # Legacy writers retain their existing path and cost. V4 changes need an
    # independent expected-before value, never a shallow copy of mutable files.
    return decode_record(record, record["id"]) if is_bundle(record) else None


class CheckpointStore(CheckpointRetentionMixin, CheckpointRestorePlanMixin):
    def __init__(self, root, session_id):
        supplied = Path(root).absolute()
        if supplied.is_symlink():
            raise ValueError("Snapshot storage cannot be a directory link")
        self.root = supplied.resolve()
        self.session_id = _identity(session_id)
        self.directory = self.root / "checkpoints" / self.session_id

    def reserve(self, original_path="", *, capture_mode=EVERY_REQUEST, purpose="request"):
        with _transaction(self.root):
            record = self._reservation_record(original_path, capture_mode, purpose)
            identity = record["id"]
            with _private_directory(self.folder(identity), create=True) as folder:
                _write_json(folder, "checkpoint.json", record)
            _CAPTURE_STARTS[identity] = time.monotonic()
            if len(_CAPTURE_STARTS) > 512:
                _CAPTURE_STARTS.pop(next(iter(_CAPTURE_STARTS)))
            return record, self.folder(identity) / "scene.blend"

    def _reservation_record(self, original_path, capture_mode, purpose):
        """Shared ordered bookkeeping only; callers own publication and resources."""
        global _LAST_CREATED
        if not holds_transaction(self.root):
            raise RuntimeError("Reservation allocation requires the profile transaction")
        sequence, _ = allocate_event(self, capture=True)
        identity = str(uuid.uuid4())
        _LAST_CREATED = max(time.time(), _LAST_CREATED + 0.000001)
        return _record({"version": 3, "boundary": sequence, "id": identity, "original_path": original_path,
            "created": _LAST_CREATED, "files": [], "state": "RESERVED", "purpose": purpose,
            "capture_mode": capture_mode, "protected": purpose == "safety"}, identity)

    def _reserve_capture_record(self, original_path, capture_mode, purpose):
        """Private destination-owner input, not a live native capture entry point."""
        record = self._reservation_record(original_path, capture_mode, purpose)
        return decode_record(dict(record, version=4, capture_validation="PENDING"), record["id"])

    def folder(self, identity):
        return self.directory / _identity(identity)

    def load(self, identity):
        with _transaction(self.root), _private_directory(self.folder(identity)) as folder:
            return decode_record(_read_json(folder, "checkpoint.json"), identity)

    def save(self, record):
        with _transaction(self.root), _private_directory(self.folder(record["id"])) as folder:
            try:
                current = decode_record(_read_json(folder, "checkpoint.json"), record["id"])
            except FileNotFoundError:
                current = None
            if is_bundle(record) or (current is not None and is_bundle(current)):
                raise ValueError("A structural v4 record cannot be changed by generic snapshot save")
            clean = _record(record, record["id"])
            # Existing capture callers save scene_bytes after native save. That
            # remains compatible, but bytes come from the actual regular file.
            # A deduplicated record has no full copy; its size is the verified
            # manifest size recorded when the full copy was adopted.
            if "scene_bytes" in clean and clean["state"] != "PRUNED" and clean.get("storage", "full") == "full":
                clean["scene_bytes"] = _file_info(folder, "scene.blend").st_size
                if not clean["scene_bytes"]:
                    raise ValueError("The Blender snapshot is empty; capture did not complete")
                clean["state"] = "READY"
                started = _CAPTURE_STARTS.pop(clean["id"], None)
                if started is not None and "capture_ms" not in clean:
                    clean["capture_ms"] = round((time.monotonic() - started) * 1000, 3)
            _write_json(folder, "checkpoint.json", clean)
            record.update(clean)

    def _save_journal(self, expected, record):
        """Fresh v4 file-journal merge only; no capture fields can be replaced."""
        if expected is None:
            self.save(record)
            return
        with _transaction(self.root), _private_directory(self.folder(record["id"])) as folder:
            before = decode_record(expected, record["id"])
            clean = decode_record(record, record["id"])
            current = decode_record(_read_json(folder, "checkpoint.json"), record["id"])
            if not is_bundle(before) or not is_bundle(clean) or current != before:
                raise ValueError("The journal owner changed; fresh metadata is required")
            if ({key: value for key, value in clean.items() if key != "files"}
                    != {key: value for key, value in current.items() if key != "files"}):
                raise ValueError("A journal change cannot replace capture or protection fields")
            merged = dict(current, files=clean["files"])
            _write_json(folder, "checkpoint.json", merged)
            record.update(merged)

    def complete(self, identity, *, capture_ms=None):
        """Call after the successful main-thread native save, never before it."""
        with _transaction(self.root):
            record = self.load(identity)
            require_legacy_scene(record)
            with _private_directory(self.folder(identity)) as folder:
                record["scene_bytes"] = _file_info(folder, "scene.blend").st_size
            if capture_ms is not None:
                record["capture_ms"] = capture_ms
            self.save(record)
            return record

    def mark_protected(self, identity, reason="safety"):
        if reason != "safety":
            raise ValueError("Only safety snapshots have durable protection; pass active references to a plan")
        with _transaction(self.root):
            record = self.load(identity)
            record.update(purpose="safety", protected=True)
            if is_bundle(record):
                clean = decode_record(record, identity)
                with _private_directory(self.folder(identity)) as folder:
                    _write_json(folder, "checkpoint.json", clean)
                record = clean
            else:
                self.save(record)
            return record

    def _target(self, item):
        root = Path(item["root"])
        if root.resolve() != root or any(part in BLOCKED for part in root.parts):
            raise ValueError("Journal workspace links are not restorable")
        lexical = root / item["path"]
        if any(path.is_symlink() or path.is_junction() for path in (lexical, *lexical.parents) if path.is_relative_to(root)):
            raise ValueError("Journal targets cannot follow links")
        target = FileWorkspace(root, own=self.root / "workspace").path(item["path"])
        if target == root or (target.is_relative_to(self.root) and target.relative_to(self.root).parts[0] in _PRIVATE_NAMES):
            raise ValueError("Private application state is not a file journal target")
        if target.exists() and (not target.is_file() or target.stat().st_size > journal_file_limit(item)):
            raise ValueError("A journaled target is no longer a regular file within its bounded size limit; restore was not applied")
        return target

    def journal(self, identity, root, relative, *, kind="file_tool", restore_entry=None, pending=False):
        """Durably prepare a before-image for the latest global capture window.

        File-effect callers use pending=True and commit_journal after publication;
        direct safety captures are ready immediately. A newer capture in ANY chat
        forces a fresh version even for a continuing older request.
        """
        with _transaction(self.root):
            record = self.load(identity)
            expected = _journal_before(record)
            if record["state"] == "PRUNED" or record.get("version", 1) < 3:
                raise ValueError("File effects require a new ordered request snapshot; legacy timing cannot be proved")
            if type(pending) is not bool or kind not in {"file_tool", "studio_export"}:
                raise ValueError("Invalid host file-journal preparation")
            root = Path(root).resolve()
            if restore_entry is not None:
                if record["purpose"] != "safety" or not record["protected"] or kind != "file_tool":
                    raise ValueError("Restore evidence only authorizes a protected safety journal")
                evidence = restore_evidence(restore_entry)
                # Journals store the path in the platform's own form ("exports\\model.glb" on Windows), so compare
                # that form: a caller may name the same file with "/".
                if evidence["root"] != str(root) or relative_path(evidence["path"]) != relative_path(relative):
                    raise ValueError("Restore evidence does not match the safety target")
                if evidence["current_kind"] == "studio_export":
                    kind = "studio_export_safety"
            self._target({"root": str(root), "path": relative, "kind": kind})
            path = FileWorkspace(root, own=self.root / "workspace").path(relative)
            relative = str(path.relative_to(root))
            if kind == "studio_export" and (path.exists() or path.is_symlink()):
                raise ValueError("Studio export journaling requires an absent target; no overwrites")
            window = read_timeline(self)["boundary"]
            for previous in record["files"]:
                if (previous["root"], previous["path"], previous.get("window")) == (str(root), relative, window):
                    if previous["phase"] != "ready":
                        raise ValueError("A pending/discarded file version needs reconciliation before another effect")
                    effect, _ = allocate_event(self)
                    previous.update(effect_sequence=effect, effect_kind="studio_export" if kind != "file_tool" else "file_tool",
                                    phase="pending" if pending else "ready")
                    self._save_journal(expected, record)
                    return dict(previous)
            if len(record["files"]) == 2048:
                raise ValueError("This request reached its bounded journal-version limit; start a new request")
            sequence, window = allocate_event(self)
            item = {"root": str(root), "path": relative, "existed": path.is_file(),
                    "backup": str(uuid.uuid4()) + ".bin", "mode": 0o600,
                    "sequence": sequence, "window": window, "phase": "pending" if pending else "ready",
                    "effect_sequence": sequence, "effect_kind": "studio_export" if kind != "file_tool" else "file_tool"}
            if kind != "file_tool":
                item["kind"] = kind
            self._target(item)
            backup_created = False
            try:
                if item["existed"]:
                    with (_private_directory(path.parent, within=root) as source_folder,
                          _private_directory(self.folder(identity)) as folder):
                        info = _file_info(source_folder, path.name)
                        limit = journal_file_limit(item, backup=True)
                        if info.st_size > limit:
                            raise ValueError("File journal exceeds its bounded pre-write size limit")
                        item["mode"] = stat.S_IMODE(info.st_mode) & 0o777
                        fd = _open_file(folder, item["backup"], os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                        backup_created = True
                        with os.fdopen(fd, "wb") as backup:
                            _copy_from(source_folder, path.name, backup, limit)
                            backup.flush()
                            os.fsync(backup.fileno())
                record["files"].append(item)
                self._save_journal(expected, record)
                return dict(item)
            except (OSError, ValueError):
                current = self.load(identity)
                persisted = next((entry for entry in current["files"] if version_key(entry) == version_key(item)), None)
                if persisted is not None:
                    self.rollback_journal(identity, persisted)
                elif backup_created:
                    with _private_directory(self.folder(identity)) as folder:
                        try:
                            _unlink_file(folder, item["backup"])
                        except OSError:
                            # Known inert ownership survives an unavailable
                            # unlink; it must never become a restore event.
                            current_before = _journal_before(current)
                            current["files"].append(dict(item, phase="discarded"))
                            self._save_journal(current_before, current)
                            raise
                raise

    def commit_journal(self, identity, entry):
        """Confirm a prepared file effect; failures retain explicit pending state."""
        with _transaction(self.root):
            record = self.load(identity)
            expected = _journal_before(record)
            current = next((item for item in record["files"] if version_key(item) == version_key(entry)), None)
            if current is None or current["phase"] == "discarded" or current["effect_sequence"] != entry["effect_sequence"]:
                raise ValueError("The prepared file journal changed before confirmation")
            if current["phase"] == "pending":
                current["phase"] = "ready"
                self._save_journal(expected, record)
            return dict(current)

    def rollback_journal(self, identity, entry, previous=None):
        """Release an unused version without ever replaying a foreign collision.

        Persist discarded ownership BEFORE backup deletion. A failed unlink or
        metadata acknowledgement leaves known inert cleanup, not an active event.
        """
        with _transaction(self.root):
            record = self.load(identity)
            expected = _journal_before(record)
            current = next((item for item in record["files"] if version_key(item) == version_key(entry)), None)
            if current is None:
                return
            if current != entry and current.get("phase") != "discarded":
                raise ValueError("The unused file journal changed during cleanup")
            if previous is not None:
                record["files"][record["files"].index(current)] = dict(previous)
                self._save_journal(expected, record)
                return
            current["phase"] = "discarded"
            self._save_journal(expected, record)
            expected = _journal_before(record)
            if current["existed"]:
                with _private_directory(self.folder(identity)) as folder:
                    try:
                        _file_info(folder, current["backup"])
                        _unlink_file(folder, current["backup"])
                    except FileNotFoundError:
                        pass
            record["files"].remove(current)
            self._save_journal(expected, record)

    def _session_store(self, session_id):
        return CheckpointStore(self.root, session_id)

    def availability(self, identity):
        try:
            with _transaction(self.root):
                record = self.load(identity)
                require_capture_idle(self, identity)
                precheck_seal(self.root, record)  # A missing required seal or key; Restore verifies the bytes.
                if is_bundle(record):
                    from .checkpoint_policy import scene_usable
                    if not scene_usable(record) or record.get("storage", "full") != "full":
                        return {"available": False, "state": record["state"],
                                "reason": "The complete Sculpt checkpoint did not finish verification",
                                "capture_validation": record["capture_validation"]}
                    try:
                        import _bpy
                    except ImportError:
                        return {"available": False, "state": record["state"], "reason": V4_SCENE_UNAVAILABLE}
                    native = getattr(_bpy, "_angelo_checkpoint", None)
                    if not all(callable(getattr(native, method, None)) for method in
                               ("prepare_restore", "restore", "close_restore")):
                        return {"available": False, "state": record["state"], "reason": V4_SCENE_UNAVAILABLE}
                    with _private_directory(self.folder(identity)) as folder:
                        if (_file_info(folder, "scene.blend").st_size != record["scene_bytes"]
                                or _file_info(folder, "sculpt.runtime").st_size != record["sculpt"]["bytes"]
                                or {"scene.blend@", "sculpt.runtime@"}.intersection(list_names(folder))):
                            raise ValueError("The complete Sculpt checkpoint pair is missing or incomplete")
                    return {"available": True, "state": "READY", "reason": ""}
                if record["state"] == "PRUNED":
                    return {"available": False, "state": "PRUNED", "reason": "Removed by snapshot retention (Snapshot Settings limits or a confirmed review)"}
                if record["state"] != "READY":
                    return {"available": False, "state": "RESERVED", "reason": "Snapshot capture did not finish"}
                if record.get("storage") == "store":
                    # Presence only; restore reassembles and verifies every byte.
                    if not SnapshotStore(self.root).has_manifest(checkpoint_ref(self.session_id, identity),
                                                                 bytes.fromhex(record["scene_hash"])):
                        raise ValueError("The stored snapshot data is missing")
                    return {"available": True, "state": "READY", "reason": ""}
                with _private_directory(self.folder(identity)) as folder:
                    if not _file_info(folder, "scene.blend").st_size:
                        raise ValueError("The Blender snapshot is empty")
                return {"available": True, "state": "READY", "reason": ""}
        except (OSError, ValueError, TypeError, RecursionError) as error:
            return {"available": False, "state": "UNAVAILABLE", "reason": str(error)}

    def recovery_copy(self, identity):
        with _transaction(self.root):
            record = self.load(identity)
            require_legacy_scene(record)
            require_capture_idle(self, identity)
            if record["state"] == "PRUNED":
                raise ValueError("This snapshot was removed by snapshot retention")
            target = self.directory / "recoveries" / ("recovered-" + str(uuid.uuid4()) + ".blend")
            with _private_directory(self.folder(identity)) as folder, _private_directory(target.parent, create=True) as recoveries:
                fd = _open_file(recoveries, target.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                try:
                    with os.fdopen(fd, "wb", buffering=8 * 1024 * 1024) as handle:
                        copy = lambda out: _copy_from(folder, "scene.blend", out)
                        if record.get("storage") == "store":  # Exact original bytes or an error; never a partial scene.
                            copy = lambda out: SnapshotStore(self.root).reassemble(checkpoint_ref(self.session_id, identity), out,
                                expected_digest=bytes.fromhex(record["scene_hash"]), expected_size=record["scene_bytes"])
                        copy_sealed(self.root, record, copy, handle)  # Verified against its seal (checkpoint_seal.py).
                        handle.flush()
                        os.fsync(handle.fileno())
                except (OSError, ValueError):
                    _unlink_file(recoveries, target.name)
                    raise
            return record, target

    def _restore(self, plan, *, participants=None):
        return apply_file_restore(self, plan, participants=participants,
                                  copy_file=_copy_from, replace_file=_replace_file, unlink_file=_unlink_file)
