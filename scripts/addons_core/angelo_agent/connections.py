# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Named connection metadata. Credentials are never serialized here.

Only connections a person added are stored. Team connections (method TEAM) are
derived from the signed-in Angelo account at run time (``team_connections``)
and never written here: an older build that saves this file keeps only the
records whose mode it knows, so a stored team record would silently vanish,
and a team record outlives neither sign-out nor a change of organization.
"""

import json
import os
import uuid
import copy
import tempfile
from contextlib import contextmanager
from pathlib import Path
from . import managed_policy
from .provider_registry import CODING, MODE_BY_CONNECTION, TEAM, spec_for
from .storage import valid_profile_directory

MODES = {("OPENAI", "SUBSCRIPTION"): "codex_subscription", ("OPENAI", "API_KEY"): "codex_api",
         ("ANTHROPIC", "SUBSCRIPTION"): "claude_subscription", ("ANTHROPIC", "API_KEY"): "claude_api"}
FIELDS = ("id", "name", "provider", "method", "mode", "model", "reuse_native")
FIELDS += ("group", "native_profile")


@contextmanager
def metadata_lock(directory, name="connections.lock"):
    # Account edits are tiny, infrequent transactions. Serialize independent
    # app instances so stale stores cannot erase each other's connections.
    # ``team-account.lock`` serializes team sign-in renewals the same way.
    with open(Path(directory) / name, "a+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            if handle.read(1) == b"":
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


class ConnectionStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "connections.json"
        self.records = {}
        self.active = None
        # Modes whose detected default native sign-in is not offered: the person
        # dismissed its suggestion or removed the connection that reused it.
        # Isolated test/eval stores list every mode. The saved key keeps its
        # original name; it never meant "create" and needs no migration.
        self.ignored_native_modes = set()
        self._baseline = {}
        self._baseline_ignored = set()
        self._baseline_active = None
        self.reload()

    def reload(self):
        self.records, self.active, self.ignored_native_modes = {}, None, set()
        try:
            raw = json.loads(self.path.read_text())
            self.ignored_native_modes = set(raw.get("ignored_native_modes", [])) & set(MODES.values())
            for record in raw.get("connections", []):
                try:
                    identity = str(uuid.UUID(record["id"]))
                    group = record.get("group", CODING)
                    if record["method"] == TEAM:
                        continue  # Derived from the account, never stored; ignore a hand-written one.
                    if record["mode"] != MODE_BY_CONNECTION[(group, record["provider"], record["method"])]:
                        continue
                    entry = {field: record.get(field) for field in FIELDS}
                    entry.update(group=group, id=identity, reuse_native=bool(record.get("reuse_native")) and record["mode"] in MODES.values())
                    entry["native_profile"] = valid_profile_directory(record.get("native_profile"), identity, record["mode"])
                    self.records[identity] = entry
                except (ValueError, TypeError, KeyError, AttributeError):
                    continue  # One broken entry must not hide every later account.
            self.active = raw.get("active") if raw.get("active") in self.records else None
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            pass
        self._baseline = copy.deepcopy(self.records)
        self._baseline_ignored = set(self.ignored_native_modes)
        self._baseline_active = self.active
        self._signature = self.signature()

    def signature(self):
        try:
            stat = self.path.stat()
            return stat.st_ino, stat.st_mtime_ns, stat.st_size
        except OSError:
            return None

    def reload_if_changed(self):
        if self.signature() == self._signature:
            return False
        self.reload()
        return True

    def add(self, provider, method, name="", model="", reuse_native=False, group=CODING):
        if method == TEAM:
            raise ValueError("Team connections come from signing in to an Angelo team; they are never stored")
        refusal = managed_policy.personal_refusal(managed_policy.current(), True)
        if refusal:  # AllowPersonalConnections off: no subscription or API key can be added.
            raise ValueError(refusal)
        try:
            mode = MODE_BY_CONNECTION[(group, provider, method)]
        except KeyError:
            raise ValueError("This provider/connection group doesn't support that authentication method") from None
        default = spec_for(mode)["name"]
        record = {"id": str(uuid.uuid4()), "provider": provider, "method": method, "mode": mode,
                  "name": name.strip()[:64] or default, "model": model.strip(), "group": group,
                  "reuse_native": bool(reuse_native) and mode in MODES.values()}
        record["native_profile"] = None
        self.records[record["id"]] = record
        if reuse_native:
            self.ignored_native_modes.discard(mode)
        if self.active is None and group == CODING:
            self.active = record["id"]
        self.save()
        return self.records[record["id"]]

    def native_connection(self, mode):
        """The connection the person added to reuse ``mode``'s default native sign-in, if any."""
        return next((r for r in self.records.values() if r["mode"] == mode and r["reuse_native"]), None)

    def suggests_native(self, mode):
        """Whether a detected default sign-in for ``mode`` may be offered as a suggestion.

        Discovery never creates a connection;
        the person adds one through the Add-connection form. Nothing is offered
        for a dismissed mode or one that a connection already reuses.
        """
        return (mode in MODES.values() and mode not in self.ignored_native_modes
                and self.native_connection(mode) is None)

    def dismiss_native(self, mode):
        """Stop offering ``mode``'s detected sign-in. Persisted; removal never undoes it."""
        if mode not in MODES.values():
            raise ValueError("Only a native sign-in can be dismissed")
        self.ignored_native_modes.add(mode)
        self.save()

    def activate(self, identity):
        if identity not in self.records:
            raise ValueError("Connection is not available")
        if self.records[identity]["group"] != CODING:
            raise ValueError("Generative AI cannot become a coding-agent default")
        self.active = identity
        self.save()

    def remove(self, identity):
        removed = self.records.pop(identity, None)
        if removed and removed["reuse_native"]:
            # A removed reuse is not offered back; a dismissal is never undone here.
            self.ignored_native_modes.add(removed["mode"])
        if self.active == identity:
            self.active = next((r["id"] for r in self.records.values() if r["group"] == CODING), None)
        self.save()

    def save(self):
        with metadata_lock(self.directory):
            latest = ConnectionStore(self.directory)
            for identity in self._baseline.keys() - self.records.keys():
                latest.records.pop(identity, None)
            for identity, record in self.records.items():
                if record != self._baseline.get(identity):
                    # A stale account rename must not resurrect an account
                    # explicitly removed by another window.
                    if identity in self._baseline and identity not in latest.records:
                        continue
                    latest.records[identity] = {field: record.get(field) for field in FIELDS}
            latest.ignored_native_modes -= self._baseline_ignored - self.ignored_native_modes
            latest.ignored_native_modes |= self.ignored_native_modes - self._baseline_ignored
            active = self.active if self.active != self._baseline_active else latest.active
            if active not in latest.records:
                active = next((r["id"] for r in latest.records.values() if r["group"] == CODING), None)
            fd, temporary = tempfile.mkstemp(prefix=".connections-", suffix=".tmp", dir=self.directory)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump({"version": 3, "active": active, "connections": list(latest.records.values()),
                               "ignored_native_modes": sorted(latest.ignored_native_modes)}, handle, allow_nan=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            self.reload()

    def import_metadata(self, source_directory):
        """Recover named accounts only. Native credentials stay with their CLI."""
        source = ConnectionStore(source_directory)
        imported = []
        for identity, value in source.records.items():
            if identity in self.records or (value["reuse_native"] and any(
                    r["reuse_native"] and r["mode"] == value["mode"] for r in self.records.values())):
                continue
            record = dict(value)
            if not record["reuse_native"] and spec_for(record["mode"])["runtime"]:
                family = "claude" if record["mode"].startswith("claude_") else "codex"
                profile = record.get("native_profile") or str(Path(source_directory).absolute() / (family + "-profile") / identity)
                record["native_profile"] = valid_profile_directory(profile, identity, record["mode"])
            self.records[identity] = record
            imported.append(identity)
        self.ignored_native_modes |= source.ignored_native_modes
        self.save()
        return imported
