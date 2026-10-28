# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The combined ordered file-restore plan across all chats, under the store transaction.

Owns what a Restore of one capture plans and proves before any file changes: its Sculpt working pair's binding to
the selected record, the earliest ordered before-image of each target after the capture (in any chat), the exact
backup and last-effect owners of each target and their re-validation, and the last-effect evidence a restored file
leaves. Applying the plan stays with the store (``CheckpointStore._restore``), which binds the file primitives.
As in ``checkpoint_retention``, the original module retains shared state and patchable dependencies: methods resolve
them at call time; this base owns no resources.
"""


class CheckpointRestorePlanMixin:
    __slots__ = ()

    def _require_recovery(self, identity, selected, recovery):
        """A real held working pair admits file planning; DTOs never do."""
        from .checkpoint_recovery import CheckpointRecovery
        if (type(recovery) is not CheckpointRecovery
                or recovery.path.parent != self.root / "checkpoints" / self.session_id / "recoveries"
                or recovery.path.parent != self.directory / "recoveries"):
            raise ValueError("Sculpt recovery requires its exact private working pair")
        recovery._validate()
        captured = recovery.record
        if (captured["id"] != identity or selected.get("version") != 4
                or selected["state"] != "READY"
                or selected["capture_validation"] not in {"VALIDATED", "INTERRUPTED_VALID"}
                or any(selected.get(key) != captured.get(key) for key in
                       ("id", "boundary", "created", "original_path", "scene_bytes", "scene_hash", "sculpt", "storage",
                        "component_seal"))):
            raise ValueError("The selected Sculpt checkpoint changed after recovery preparation")

    def restore_plan(self, identity, *, recovery=None):
        """Earliest ordered before-images after this capture, across all chats."""
        return self._prepare_file_restore(identity, recovery=recovery)[0]

    def _prepare_file_restore(self, identity, *, recovery=None):
        """Bind all participating owners separately from the public journal triples."""
        from . import checkpoints
        with checkpoints._transaction(self.root):
            selected = self.load(identity)
            if checkpoints.is_bundle(selected):
                self._require_recovery(identity, selected, recovery)
            else:
                checkpoints.require_legacy_scene(selected)
            checkpoints.require_capture_idle(self, identity)
            if selected["state"] == "PRUNED":
                raise ValueError("This snapshot was removed by snapshot retention")
            inventory = self._inventory()
            if inventory["issues"]:
                raise ValueError("File restore is unavailable: " + inventory["issues"][0]["error"])
            records = inventory["checkpoints"]
            if not selected.get("boundary") and any("sequence" not in entry for record in records for entry in record["files"]):
                raise ValueError("Legacy file-write timing cannot be proved; its scene/history are preserved, but this combined restore is unavailable")
            kinds = checkpoints.current_kinds(records)
            versions = [(entry["sequence"], record, entry) for record in records for entry in record["files"]
                        if entry.get("phase", "ready") == "ready" and
                        entry.get("sequence", 0) > selected.get("boundary", 0)]
            latest = {}
            for record in records:
                for entry in record["files"]:
                    if entry.get("phase") == "ready":
                        key = entry["root"], entry["path"]
                        if key not in latest or entry["effect_sequence"] > latest[key][1]["effect_sequence"]:
                            latest[key] = record, entry
            targets, backup_owners, effect_owners = {}, {}, {}
            for _, record, entry in sorted(versions, key=lambda value: value[0]):
                store = self._session_store(record["session_id"])
                evidence = checkpoints.planned_entry(entry, kinds)
                target = store._target(evidence)
                key = str(target)
                if key not in targets:
                    targets[key] = (target, store.folder(record["id"]) / entry["backup"], evidence)
                    backup_owners[key] = checkpoints._JournalOwner(record["session_id"], record["id"],
                                                                   checkpoints.version_key(entry))
                    source, effect = latest[(entry["root"], entry["path"])]
                    effect_owners[key] = checkpoints._JournalOwner(source["session_id"], source["id"],
                                                                   checkpoints.version_key(effect),
                                                                   effect["effect_sequence"])
            participants = checkpoints._RestoreParticipants((self.session_id, identity), tuple(backup_owners.values()),
                                                            tuple(effect_owners.values()), recovery)
            refs = {(owner.session_id, owner.checkpoint_id)
                    for owner in (*participants.backups, *participants.effects)}
            for session_id, checkpoint_id in sorted(refs):
                checkpoints.require_capture_idle(self._session_store(session_id), checkpoint_id)
            return list(targets.values()), participants

    def _validate_file_restore(self, plan, participants):
        from . import checkpoints
        if not checkpoints.holds_transaction(self.root):
            raise ValueError("File restore requires its existing profile transaction")
        if type(participants) is not checkpoints._RestoreParticipants or participants.selected[0] != self.session_id:
            raise ValueError("File restore requires its exact selected and journal owners")
        fresh, owners = self._prepare_file_restore(participants.selected[1], recovery=participants.recovery)
        if plan != fresh or participants != owners:
            raise ValueError("The file restore participants changed; inspect a fresh plan")

    def restore_files(self, identity):
        # The combined ordered plan is the safe public restore boundary; do not
        # replay an older request's entire history across a later capture.
        return self.restore_since(identity)

    def restore_since(self, identity, plan=None, *, recovery=None):
        from . import checkpoints
        with checkpoints._transaction(self.root):
            fresh, participants = self._prepare_file_restore(identity, recovery=recovery)
            if plan is not None and plan != fresh:
                raise ValueError("The file restore plan changed; inspect it again before restoring")
            return self._restore(fresh, participants=participants)

    def _record_restored_effect(self, item, *, owner, absent=False):
        # Update only last-effect evidence, never the before-image sequence/kind.
        # UI safety versions consequently remain the earliest Undo Restore image.
        from . import checkpoints
        if not checkpoints.holds_transaction(self.root) or type(owner) is not checkpoints._JournalOwner:
            raise ValueError("Restored effects require their exact preflight owner")
        records = self._inventory(validate_targets=False)["checkpoints"]
        versions = [(entry["effect_sequence"], record, entry) for record in records for entry in record["files"]
                    if entry.get("phase") == "ready" and (entry["root"], entry["path"]) == (item["root"], item["path"])]
        if not versions:
            raise ValueError("A restored file has no ordered recovery owner")
        _, source, entry = max(versions, key=lambda value: value[0])
        current_owner = checkpoints._JournalOwner(source["session_id"], source["id"], checkpoints.version_key(entry),
                                                  entry["effect_sequence"])
        if current_owner != owner:
            raise ValueError("The restored file's effect owner changed")
        store = self._session_store(source["session_id"])
        checkpoints.require_capture_idle(store, source["id"])
        record = store.load(source["id"])
        expected = checkpoints._journal_before(record)
        version = checkpoints.version_key(entry)
        current = next(value for value in record["files"] if checkpoints.version_key(value) == version)
        effect, _ = checkpoints.allocate_event(self)
        kind = "studio_export" if not absent and item.get("kind") == "studio_export_safety" else "file_tool"
        current.update(effect_sequence=effect, effect_kind=kind)
        store._save_journal(expected, record)
