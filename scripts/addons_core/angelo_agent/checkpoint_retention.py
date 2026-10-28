# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Checkpoint inventory and conservative removal under the store transaction.

The original module retains shared state and patchable dependencies.
Methods resolve those named dependencies at call time; this base owns no resources.
"""


class CheckpointRetentionMixin:
    __slots__ = ()

    def _inventory(self, *, validate_targets=True, probe=False):
        from . import checkpoints as checkpoints
        return checkpoints.inventory(self, validate_targets=validate_targets, probe=probe)

    def inspect(self):
        """Measured private inventory; malformed storage blocks deletion plans."""
        from . import checkpoints as checkpoints
        with checkpoints._transaction(self.root):
            return self._inventory()

    def prune_plan(self, policy=None, *, protected_refs=(), now=None, preferred_refs=()):
        from . import checkpoints as checkpoints
        with checkpoints._transaction(self.root):
            inventory = self._inventory()
            accounting = checkpoints.store_accounting(self, inventory)
            return checkpoints.plan_retention(inventory, policy, protected_refs=protected_refs, now=now,
                                  accounting=accounting, preferred_refs=preferred_refs)

    def incomplete_plan(self, *, protected_refs=(), now=None, older_than_seconds=86400, max_count=16):
        """Bounded cleanup proposal: only empty reservations, never usable scenes."""
        from . import checkpoints as checkpoints
        refs = checkpoints._protected_refs(protected_refs)
        now = checkpoints.time.time() if now is None else now
        if not checkpoints._number(now) or type(older_than_seconds) is not int or older_than_seconds < 300 or type(max_count) is not int or not 1 <= max_count <= 64:
            raise ValueError("Reservation cleanup needs a finite time, age >=300 seconds and limit 1..64")
        with checkpoints._transaction(self.root):
            inventory = self._inventory()
            eligible = [item for item in inventory["checkpoints"] if item.get("version", 1) != 4 and
                        item.get("capture_active") is not True and item["state"] == "RESERVED" and
                        not item["files"] and not item["scene_bytes"] and not item["protected"] and
                        item["purpose"] != "safety" and item["ref"] not in refs and item["created"] <= now - older_than_seconds]
            plan = {"version": 1, "kind": "incomplete", "planned_at": now, "older_than_seconds": older_than_seconds,
                    "max_count": max_count, "protected_refs": refs, "inventory_token": inventory["token"],
                    "blocked": bool(inventory["issues"]), "candidates": []}
            if not plan["blocked"]:
                plan["candidates"] = [{key: item[key] for key in ("ref", "session_id", "id", "bytes", "fingerprint")}
                                      for item in sorted(eligible, key=lambda item: item["created"])[:max_count]]
            plan["candidate_bytes"] = sum(item["bytes"] for item in plan["candidates"])
            plan["token"] = checkpoints._digest(plan)
            return plan

    def prune(self, plan, *, user_confirmed=False, protected_refs=()):
        """Apply only an unchanged exact plan after explicit human confirmation."""
        from . import checkpoints as checkpoints
        if user_confirmed is not True:
            raise ValueError("A human must confirm the exact snapshot removal plan")
        if not isinstance(plan, dict) or plan.get("kind") not in {"retention", "incomplete"}:
            raise ValueError("Invalid snapshot removal plan")
        checkpoints._require_safe_deletion()
        with checkpoints._transaction(self.root):
            if plan["kind"] == "retention":
                fresh = self.prune_plan(plan.get("policy"), protected_refs=protected_refs, now=plan.get("planned_at"),
                                        preferred_refs=plan.get("preferred_refs", ()))
            else:
                fresh = self.incomplete_plan(protected_refs=protected_refs, now=plan.get("planned_at"),
                    older_than_seconds=plan.get("older_than_seconds"), max_count=plan.get("max_count"))
            if plan != fresh or fresh["blocked"]:
                raise ValueError("Snapshots/protection changed; inspect and confirm a fresh removal plan")
            return self._remove(fresh)

    def _verify_bundle_removal(self, record):
        """Verify the exact finite published pair before any batch tombstone."""
        from . import checkpoints as checkpoints
        from .checkpoint_capture import _hash_descriptor, _stable_file
        from .checkpoint_capture_records import file_identity
        from .checkpoint_policy import scene_usable
        if (not checkpoints.holds_transaction(self.root) or not scene_usable(record)
                or record.get("storage", "full") != "full"):
            raise ValueError("Sculpt removal requires its complete verified full pair")
        ledger = record.get("capture_outputs", {})
        if set(ledger.get("roles", {})) != {"BASE", "RUNTIME"}:
            raise ValueError("Sculpt removal requires its exact published output ledger")
        admitted = {}
        with checkpoints._private_directory(self.folder(record["id"])) as folder:
            from .checkpoint_filesystem import directory_info
            directory = directory_info(folder)
            if ledger.get("directory") != file_identity(directory.st_dev, directory.st_ino):
                raise ValueError("Sculpt removal directory no longer matches its capture")
            allowed = {"checkpoint.json", "scene.blend", "sculpt.runtime"} | {
                item["backup"] for item in record["files"] if item["existed"]}
            if set(checkpoints.list_names(folder)) - allowed:
                raise ValueError("Sculpt removal cannot discard incomplete or unknown output names")
            for role, name, size, digest in (
                ("BASE", "scene.blend", record["scene_bytes"], record["scene_hash"]),
                ("RUNTIME", "sculpt.runtime", record["sculpt"]["bytes"], record["sculpt"]["hash"]),
            ):
                fd = checkpoints._open_file(folder, name, checkpoints.os.O_RDONLY)
                try:
                    opened = checkpoints.descriptor_witness(fd)
                    if (not checkpoints.same_file(opened, checkpoints._file_info(folder, name))
                            or ledger["roles"][role]["created"] != file_identity(opened.st_dev, opened.st_ino)
                            or _hash_descriptor(fd, size) != digest
                            or not _stable_file(opened, checkpoints.descriptor_witness(fd))
                            or not _stable_file(opened, checkpoints.file_witness(folder, name))):
                        raise ValueError("Sculpt removal component identity or bytes changed")
                    admitted[name] = opened
                finally:
                    checkpoints.os.close(fd)
        return admitted

    def _remove(self, fresh):
        """Tombstone, then delete payload files of an admitted plan (transaction held).

        Deduplicated chunks are released by the snapshot worker's garbage
        collection once no remaining manifest references them, so the result
        separates immediately freed file bytes from pending store bytes.
        """
        from . import checkpoints as checkpoints
        checkpoints._require_safe_deletion()
        with checkpoints._transaction(self.root):
            from .checkpoint_capture import _stable_file
            from .checkpoint_capture_records import file_identity
            from .checkpoint_filesystem import directory_info
            admitted = {}
            # Admission covers the entire batch before its first tombstone.
            # A direct caller cannot hide a later pinned/v4 candidate behind an
            # otherwise removable earlier record.
            for candidate in fresh["candidates"]:
                owner = self._session_store(candidate["session_id"])
                record = owner.load(candidate["id"])
                checkpoints.require_capture_idle(owner, record["id"])
                if checkpoints.is_bundle(record):
                    admitted[candidate["ref"]] = owner._verify_bundle_removal(record)
                else:
                    checkpoints.require_legacy_scene(record)
            removed, freed, mutating = [], 0, set()
            try:
                for candidate in fresh["candidates"]:
                    store = checkpoints.CheckpointStore(self.root, candidate["session_id"])
                    record = store.load(candidate["id"])
                    with checkpoints._private_directory(store.folder(record["id"])) as folder:
                        names = sorted(checkpoints.list_names(folder))
                        allowed = {"checkpoint.json", "scene.blend"} | {item["backup"] for item in record["files"] if item["existed"]}
                        if checkpoints.is_bundle(record):
                            allowed.add("sculpt.runtime")
                        if set(names) - allowed:
                            raise ValueError("Snapshot storage changed; no unrecognized files were removed")
                        infos = [(name, checkpoints.file_witness(folder, name)) for name in names]
                        fingerprint = checkpoints._digest({"record": record, "files": [(name, info.st_size, info.st_mtime_ns,
                                               info.st_dev, info.st_ino) for name, info in infos]})
                        if fingerprint != candidate["fingerprint"]:
                            raise ValueError("Snapshot storage changed; inspect a fresh removal plan")
                        sizes = {name: checkpoints._file_info(folder, name).st_size for name in names}
                        tombstone = {"version": 2, "id": record["id"], "created": record["created"], "original_path": "",
                            "files": [], "state": "PRUNED", "purpose": record["purpose"], "capture_mode": record["capture_mode"],
                            "protected": False, "pruned_at": checkpoints.time.time(), "prune_reason": fresh["kind"], "bytes_freed": 0}
                        if record.get("boundary"):
                            tombstone.update(version=3, boundary=record["boundary"])
                        if checkpoints.is_bundle(record):
                            from .checkpoint_capture_records import validate_transition
                            tombstone.update(version=4, capture_validation=record["capture_validation"])
                            tombstone = validate_transition(record, tombstone, kind="prune")
                        payload = [name for name in names if name != "checkpoint.json"]
                        # Keep one record's actual payload descriptors through all
                        # metadata I/O. Batch stat witnesses retain the earlier
                        # full-pair hash admission without O(history) open fds.
                        expected = {name: info for name, info in infos if name in payload}
                        for name, info in admitted.get(candidate["ref"], {}).items():
                            if name not in expected or not _stable_file(info, expected[name]):
                                raise ValueError("A verified removal component changed after batch admission")
                        # Windows: a payload file another program holds open without delete sharing refuses the whole
                        # removal here, before the tombstone, and the next retention run tries again.
                        with checkpoints.ExitStack() as handles:
                            held = {}
                            for name in payload:
                                fd = checkpoints._open_file(folder, name, checkpoints.os.O_RDONLY)
                                handles.callback(checkpoints.os.close, fd)
                                handle = handles.enter_context(checkpoints.os.fdopen(fd, "rb", buffering=0, closefd=False))
                                held[name] = handle
                            def unchanged(name):
                                checkpoints.assert_directory_identity(store.folder(record["id"]), folder)
                                if checkpoints.is_bundle(record):
                                    directory = directory_info(folder)
                                    if record["capture_outputs"]["directory"] != file_identity(directory.st_dev, directory.st_ino):
                                        raise ValueError("The admitted removal directory changed")
                                if (not _stable_file(expected[name], checkpoints.descriptor_witness(held[name].fileno()))
                                        or not _stable_file(expected[name], checkpoints.file_witness(folder, name))):
                                    raise ValueError("A removal payload changed; its replacement was preserved")
                            for name in payload:
                                unchanged(name)
                            remove = handles.enter_context(checkpoints.held_for_removal(folder, payload))
                            # Invalidate restore links durably before removing backing data. Every payload file is
                            # already held for deletion, so the tombstone never outlives a file it cannot remove.
                            mutating.add(candidate["ref"])
                            checkpoints._write_json(folder, "checkpoint.json", tombstone)
                            # Metadata replacement/sync is an observable I/O barrier.
                            # A new name at that barrier must not become deletion authority.
                            for name in payload:
                                unchanged(name)
                            for name in payload:
                                unchanged(name)
                                remove(name)  # Anchored: never follows a link or recurses.
                                freed += sizes[name]
                                tombstone["bytes_freed"] += sizes[name]
                        checkpoints._write_json(folder, "checkpoint.json", tombstone)
                    checkpoints._CAPTURE_STARTS.pop(record["id"], None)
                    removed.append(candidate["ref"])
                    try:   # Its scene map goes with it; one left behind is the snapshot worker's orphan.
                        from .checkpoint_scene_map import remove as remove_scene_map
                        remove_scene_map(self.root, candidate["session_id"], record["id"])
                    except (OSError, ValueError):
                        pass
            except (OSError, ValueError) as error:
                # These are confirmed lower bounds, never foreign replacement
                # sizes or a claim that the current tombstone finished removal.
                error.removed_checkpoints = tuple(removed)
                error.known_bytes_freed = freed
                error.possibly_changed_checkpoints = tuple(sorted(mutating))
                error.partial_removal = bool(mutating)
                if mutating:
                    error.add_note("Snapshot removal may be partial; inspect retained tombstones and payloads before retrying")
                raise
            return {"removed": removed, "bytes_freed": freed, "tombstones_retained": True,
                    "store_bytes_pending": sum(item.get("store_bytes", 0) for item in fresh["candidates"])}
