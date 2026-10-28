# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Application-owned transcripts. Provider secrets never enter this schema."""

import json
import os
import sys
import time
import uuid
from pathlib import Path
from .chat_settings import APPROVAL_VERSION, migrate_saved_approval, migrated, stored_options
from . import state_paths
from .routing import CONTEXT_SEPARATOR, MODES, RENAMED_TOOLSET, TOOLSET_VERSION, valid_slot
from .task_memory import new_memory, clean_memory
from .invocations import new_invocations, clean_invocations, migrate_invocations
from . import context_ledger, identifiers, kept_state, session_journal, session_kept, session_validation
from .session_guard import GuardedWriter
from .runtime_environment import PROMPT_CACHE_TTLS
from .ui_text import n_, notice

# The title of a conversation with no request yet; stored in English and translated where it is shown.
UNTITLED = n_("New chat")
# Host tests (run_host_tests.py) and native suites set this: every save then reads its file back and requires it to
# reproduce the state a whole-file save would have written (session_journal, session_writer). Off in the product.
VERIFY_JOURNAL = os.environ.get("ANGELO_JOURNAL_VERIFY") == "1"
# A chat this build cannot fully read is kept, unchanged, before anything rewrites it (session_kept.preserve).
KEPT_NOTICE = n_("This build could not read all of this chat's saved history, so it kept the file unchanged as "
                 "{file} in the same folder before saving anything. A later Angelo may read it.")
UNKEPT_NOTICE = n_("This build could not read all of this chat's saved history and could not keep a copy of the file, "
                   "so it will not save this chat over it. Start a new chat to continue.")
# Top-level fields of the saved document this build understands; a later build's other fields mean "keep the file".
KNOWN_FIELDS = frozenset({"version", "id", "provider", "native_id", "connection_id", "model", "messages", "results",
                          "updated", "options", "approval_version", "toolset_version", "runtime_sessions",
                          "context_slot", "next_route", "task_memory", "invocations"})
# Chats whose journal a worker Blender writes (worker_journal_owner, design A'): session id -> (hosted now, hand-offs so
# far). This process never writes one while hosted, nor from a store loaded before its latest hand-off.
HOSTED = {}
HISTORY_WARNING = n_("This conversation's stored history could not be verified. Scene/file changes are disabled to preserve "
                     "duplicate protection. Readable valid messages and safe observations remain available; start New Chat before making changes.")


def clean_runtime_sessions(value):
    output = {}
    if not isinstance(value, dict):
        return output
    for identity, raw in list(value.items())[:100]:
        try:
            base, separator, slot = identity.partition(CONTEXT_SEPARATOR)
            if separator and not valid_slot(slot):
                continue
            if base not in MODES:
                uuid.UUID(base)
            if not isinstance(raw, dict):
                continue
            native = raw.get("native_id")
            toolset = int(raw.get("toolset_version", 1))
            # A context from before the tool rename is never resumed (routing.RENAMED_TOOLSET).
            resumable = isinstance(native, str) and toolset >= RENAMED_TOOLSET
            output[identity] = {"native_id": native[:256] if resumable else None,
                                "synced_length": max(0, int(raw.get("synced_length", 0))) if resumable else 0,
                                "toolset_version": toolset}
            if resumable:
                # What this native context already received, and its last
                # measured prompt size (context_ledger, context_rebase).
                ledger = context_ledger.usable(raw.get("received"), output[identity]["native_id"])
                if ledger is not None:
                    output[identity]["received"] = ledger
                tokens = raw.get("context_tokens")
                if type(tokens) is int and 0 < tokens <= 10 ** 9:
                    output[identity]["context_tokens"] = tokens
                # The prompt-cache TTL this session was started with (Controller.session_cache_ttl).
                if raw.get("cache_ttl") in PROMPT_CACHE_TTLS:
                    output[identity]["cache_ttl"] = raw["cache_ttl"]
                # The model that owns this native context (Controller._select_model_context).
                if valid_slot(raw.get("model")):
                    output[identity]["model"] = raw["model"]
                # Its short-identifier table (identifiers), only for this native ID.
                table = identifiers.clean(raw.get("identifiers"))
                if table is not None and table["native_id"] == output[identity]["native_id"]:
                    output[identity]["identifiers"] = table
        except (TypeError, ValueError, AttributeError):
            continue
    return output


def clean_next_route(value):
    if not isinstance(value, dict) or value.get("provider") not in MODES:
        return None
    try:
        identity = str(uuid.UUID(value["connection_id"]))
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    return {"connection_id": identity, "provider": value["provider"]}


def _clean_result_payloads(value):
    """Drop invalid/aged payloads only; permanent invocation IDs live elsewhere."""
    output = {}
    if type(value) is not dict:
        return output
    for identity, record in value.items():
        if (type(identity) is not str or type(record) is not dict
                or type(record.get("document")) is not str or type(record.get("result")) is not dict):
            continue
        try:
            json.dumps(record, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            continue
        output[identity] = record
        if len(output) > 256:
            output.pop(next(iter(output)))
    return output


def read_transcript(path):
    """A saved conversation's raw document (its base with every complete record applied), for tools and tests.

    Unvalidated: ``SessionStore.load`` is the product's reader. Raises ``OSError`` or ``ValueError``.
    """
    return session_journal.read(path)[0]


class SessionStore:
    """One chat's private transcript and mutation-ID ledger: ``sessions/<id>.json``.

    A save appends what changed (``session_writer``); the file format and its
    durability are in ``session_journal``. ``load`` reads both the whole-file
    format of earlier builds and the journal.
    """

    def __init__(self, directory, session_id=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.session_id = str(uuid.UUID(session_id)) if session_id else None
        if self.session_id:
            folder = self.directory / "sessions"
            folder.mkdir(exist_ok=True, mode=0o700)
            self.path = folder / (self.session_id + ".json")
        else:
            self.path = self.directory / "session.json"
        self._writer = GuardedWriter(self.path)   # Never writes over another writer's content.
        self._writer.owned_fields = frozenset({"task_memory"})  # _cleaned_memory's value is this store's own.
        self._request_start = 0
        self._unkept = False  # A file this build could not read or keep: never written over (load).
        self._hand_offs = HOSTED.get(self.session_id, (False, 0))[1]   # HOSTED: renewed by every load.
        # The last task memory cleaned for a save, and the object it was cleaned from (held, so its identity is
        # not reused): task_memory's helpers are pure and install a new object for every change.
        self._memory = (None, None, None)

    def _pre_rename_profile(self):
        """This profile's identity in the pre-rename state folder, whose chats state_migration moved here."""
        legacy = state_paths.legacy_sibling(self.directory)
        return str(legacy) if legacy is not None else None

    def new(self, provider="codex_subscription"):
        return {"version": 1, "id": self.session_id or str(uuid.uuid4()), "provider": provider,
                "native_id": None, "context_slot": None, "connection_id": None, "model": "", "options": stored_options({}),
                "approval_version": APPROVAL_VERSION,
                "toolset_version": TOOLSET_VERSION, "runtime_sessions": {}, "next_route": None,
                "messages": [], "results": {}, "invocations": new_invocations(),
                "task_memory": new_memory(str(self.directory.resolve())), "updated": time.time()}

    def load(self):
        """The chat as saved, verified; or an unverified history whose mutation IDs are closed.

        The next save rewrites the file whole from this state, only over the file as read (``session_guard``).
        A file this build cannot fully read (unreadable, failing validation, a damaged ledger, an unknown field)
        is first kept beside it (``session_kept.preserve``) and the chat says so; if it cannot be kept, ``save`` raises.
        """
        self._writer.loading()
        self._memory = (None, None, None)
        self._unkept, self._hand_offs = False, HOSTED.get(self.session_id, (False, 0))[1]
        session_journal.discard_stale(self.path)
        state, partial = self._read()
        if partial:
            try:
                kept = session_kept.preserve(self.path)
            except OSError:
                self._unkept, kept = True, None
                state["invocations"]["closed"] = True
            if kept != "":
                # Before the history warning, which stays the chat's last word on what it can still do.
                rows = state["messages"]
                at = len(rows) - 1 if rows and rows[-1].get("text") == HISTORY_WARNING else len(rows)
                rows.insert(at, {"role": "system", **(notice(KEPT_NOTICE, file=kept) if kept else notice(UNKEPT_NOTICE))})
        self._writer.adopt(partial)
        return state

    _journal = staticmethod(lambda path: session_journal.read(path)[0])   # ``_read``'s parts; MirrorStore overrides them
    _rows, _results, _ledger = map(staticmethod, (session_validation.messages, _clean_result_payloads, clean_invocations))

    def _read(self):
        """``(state, partial)``: ``partial`` when the file holds anything this build did not fully understand."""
        existed = False
        try:
            existed = self.path.exists() or self.path.is_symlink()
            raw = self._journal(self.path)
            if (not isinstance(raw, dict) or type(raw.get("version")) is not int
                    or raw.get("version") != 1 or not isinstance(raw.get("messages"), list)):
                return self._unverified_history(), True
            state = self.new()
            verified_header = session_validation.header(raw, state, session_id=self.session_id)
            if state["toolset_version"] < RENAMED_TOOLSET:
                state["native_id"] = None  # Never resume a pre-rename native context (routing.RENAMED_TOOLSET).
            state["messages"], verified_messages = self._rows(raw["messages"])
            state["results"] = raw.get("results", {})
            external = any(isinstance(m, dict) and m.get("request_kind") == "external_session" for m in state["messages"][:2])
            options, state["approval_version"] = migrate_saved_approval(raw.get("options"), raw.get("approval_version"),
                                                                        external=external)
            state["options"] = stored_options(options)  # The person's choice; policy applies when it is read.
            state["runtime_sessions"] = clean_runtime_sessions(raw.get("runtime_sessions"))
            # The current native context; its native ID is state["native_id"].
            state["context_slot"] = raw.get("context_slot") if valid_slot(raw.get("context_slot")) else None
            state["next_route"] = clean_next_route(raw.get("next_route"))
            # Old transcripts simply start with an empty brief. A restart never
            # turns saved object IDs/provenance into live scene/policy authority.
            state["task_memory"] = clean_memory(raw.get("task_memory"), profile_id=str(self.directory.resolve()),
                                                rehydrating=True, moved_from_profile_id=self._pre_rename_profile())
            state["invocations"] = (self._ledger(raw["invocations"]) if "invocations" in raw
                                    else migrate_invocations(state.get("results"), bool(state["messages"])))
            state["results"] = self._results(state.get("results"))
            ledger = raw.get("invocations")
            damaged = ("invocations" in raw and state["invocations"]["closed"]
                       and not (type(ledger) is dict and ledger.get("closed") is True))
            partial = not verified_header or not verified_messages or damaged or bool(set(raw) - KNOWN_FIELDS)
            if not verified_header or not verified_messages:
                state["invocations"]["closed"] = True
                state["native_id"], state["runtime_sessions"] = None, {}
                state["messages"].append({"role": "system", **notice(HISTORY_WARNING)})
            elif session_validation.uncertain_recovery(state["messages"]):
                # The scene may have loaded even though final recovery storage
                # failed. Preserve safety links/ID protection, never resume the
                # old native context as if its document still existed.
                state["native_id"], state["runtime_sessions"] = None, {}
                state["messages"].append({"role": "system", **notice(
                    "A snapshot recovery has an incomplete stored outcome. Its safety link is preserved. Inspect the current scene/files; a fresh native context and observations are required before continuing. Do not automatically repeat recovery or earlier edits.")})
            return state, partial
        except FileNotFoundError:
            return (self._unverified_history() if existed or self.path.is_symlink() else self.new()), False
        except (OSError, ValueError, TypeError, AttributeError, RecursionError, OverflowError):
            return self._unverified_history(), True

    def _unverified_history(self):
        """Never silently reopen mutation IDs after losing an existing history."""
        state = self.new()
        state["invocations"]["closed"] = True
        state["messages"].append({"role": "system", **notice(HISTORY_WARNING)})
        return state

    def _fields(self, state):
        """Everything saved but the rows, results and ledger, cleaned as every save cleans it, in file order.

        Explicit whitelist: never serialize runtime objects, environment, auth events or keys.
        """
        fields = [(key, state[key]) for key in ("version", "id", "provider", "native_id", "connection_id", "model",
                                                "updated")]
        fields.append(("options", stored_options(state.get("options"))))  # Never a policy-forced Ask.
        if migrated(state.get("approval_version")):
            fields.append(("approval_version", state["approval_version"]))
        fields += [("toolset_version", state.get("toolset_version", TOOLSET_VERSION)),
                   ("runtime_sessions", clean_runtime_sessions(state.get("runtime_sessions"))),
                   ("context_slot", state.get("context_slot") if valid_slot(state.get("context_slot")) else None),
                   ("next_route", clean_next_route(state.get("next_route"))),
                   ("task_memory", self._cleaned_memory(state.get("task_memory")))]
        return fields

    def _cleaned_memory(self, memory):
        """``clean_memory`` of the state's task memory, again only when it is another object.

        Cleaning a full brief costs several milliseconds and the memory changes only through task_memory's
        pure helpers, which install a new object. The cleaned value is this store's own: nothing changes it.
        """
        source, _, cleaned = self._memory
        if memory is not source:
            # The profile folder is resolved when the memory is cleaned, not on every save: a resolve is a system
            # call per path component, each a hand-off of the GIL to the snapshot worker.
            profile = str(self.directory.resolve())
            cleaned = clean_memory(memory, profile_id=profile)
            self._memory = (memory, profile, cleaned)
        return cleaned

    def save(self, state):
        """Make ``state`` durable: append what changed, or rewrite the file when that cannot describe it.

        Raises on failure, leaving the file as it was (or with a torn last record, which a load
        ignores); the next save then rewrites it whole. A value that cannot be saved (not finite
        JSON, not UTF-8) raises before anything is written.
        """
        with self._writer.lock:
            self._refuse_unkept()
            fields = self._fields(state)
            messages, results = state["messages"], state["results"]
            if not self._writer.force_due():
                try:
                    if self._writer.append(fields, messages, results, state.get("invocations"), self._request_start):
                        return self._verified(state, fields)
                except session_journal.FileChanged:
                    pass  # Not the file as last written: never append to it; the rewrite checks it (session_guard).
            self._rewrite(state, fields)
            return self._verified(state, fields)

    def compact(self, state):
        """Rewrite the whole file from ``state`` now (chat close): one base line, readable by every build."""
        with self._writer.lock:
            self._refuse_unkept()
            fields = self._fields(state)
            self._rewrite(state, fields)
            return self._verified(state, fields)

    def leave(self, state):
        """A switch to another chat (``ui.show_session``): leave the file one base line, readable by every build.

        Nothing is written when it already is one holding ``state`` but the switch's time (``updated``, which
        reaches the file with the chat's next save or its close): the check costs an append's comparison, not
        a rewrite of the conversation. Otherwise the chat is saved, then compacted from what the file holds
        when that left records (as ``compact_if_due``).
        """
        with self._writer.lock:
            if self.current(state):
                return self._verified(state, self._writer.written_fields())
            self.save(state)
            if self._writer.records:
                self._writer.compact(state["messages"], state["results"], state["invocations"], self._request_start)
                self._verified(state, self._writer.written_fields())

    def current(self, state):
        """True when the file is one base line holding ``state`` but its time (``updated``): no record since the
        last rewrite, and an append would write nothing else (``JournalWriter.current``). False before the first
        save after a load. ``leave`` writes nothing then; ``activity_rows.bind_key`` does not rewrite."""
        with self._writer.lock:
            return self._writer.current(self._fields(state), state["messages"], state["results"],
                                        state.get("invocations"), self._request_start, but=("updated",))

    def compact_if_due(self, state):
        """At a turn's end: save, then compact once the records outgrow the base. True when it compacted."""
        with self._writer.lock:
            self.save(state)
            if not self._writer.compaction_due():
                return False
            self._writer.compact(state["messages"], state["results"], state["invocations"], self._request_start)
            self._verified(state, self._writer.written_fields())
            return True

    def _refuse_unkept(self):
        if self._unkept:
            raise OSError("This chat's saved file could not be read or kept; it is not saved over")
        hosted = HOSTED.get(self.session_id)
        if hosted is not None and (hosted[0] or hosted[1] != self._hand_offs):
            raise session_journal.JournalHosted("A worker Blender writes this chat's file; it is not saved over here")

    def seal(self, index):
        """Row ``index`` starts the current request (``Controller``): saves encode the rows from there.

        Older rows are written again only when ``touch``ed or found by the writer's scrub.
        """
        with self._writer.lock:
            self._request_start = index if type(index) is int and index >= 0 else 0

    def invalidate(self):
        """Rows changed that no save was told about (a restore's badges, a key's redaction): rewrite next."""
        with self._writer.lock:
            self._writer.reset()

    def redact_kept(self, secret):
        """Redact a key just bound in this chat's kept copies (``session_kept.redact_kept``): the names that may
        still hold it, each also reported on standard error by its name (never the key or the folder)."""
        held = session_kept.redact_kept(self.path, secret)
        for name in held:
            sys.stderr.write("Angelo: the connection's key could not be removed from this chat's kept copy %s\n" % name)
        return held

    def touch(self, index):
        """Row ``index`` changed in place (``Controller``): the next save writes it even if it is old."""
        with self._writer.lock:
            self._writer.touch(index)

    @property
    def last_write(self):
        """``"append"`` or ``"rewrite"``: what the last successful save wrote."""
        return self._writer.last_write

    def _rewrite(self, state, fields):
        # The whole ledger is validated only here (load and rewrite): entries added since are checked as they are.
        state["invocations"] = clean_invocations(state.get("invocations"))
        self._writer.rewrite(fields, state["messages"], state["results"], state["invocations"], self._request_start)

    def _verified(self, state, fields):
        # ``fields`` as this save cleaned them: some (the native ID, the model) change outside the controller lock,
        # which only orders row, result and ledger changes against saves.
        if VERIFY_JOURNAL:
            expected = json.loads(session_journal.encode(dict(
                fields, messages=state["messages"], results=state["results"],
                invocations=clean_invocations(state.get("invocations")))))
            saved = session_journal.read(self.path)[0]
            if saved != expected:
                raise AssertionError("The conversation journal does not reproduce the saved state (%s): %s"
                                     % (_first_difference(saved, expected), self.path))


def _first_difference(saved, expected, path="$"):
    """Where two JSON values first differ, for the journal's verification failures."""
    if type(saved) is not type(expected):
        return "%s: %s on disk, %s in memory" % (path, type(saved).__name__, type(expected).__name__)
    if isinstance(saved, dict):
        for key in list(expected) + [key for key in saved if key not in expected]:
            if key not in saved or key not in expected:
                return "%s.%s: %s" % (path, key, "missing on disk" if key not in saved else "not in memory")
            if saved[key] != expected[key]:
                return _first_difference(saved[key], expected[key], "%s.%s" % (path, key))
    elif isinstance(saved, list):
        for index, (left, right) in enumerate(zip(saved, expected)):
            if left != right:
                return _first_difference(left, right, "%s[%d]" % (path, index))
        return "%s: %d items on disk, %d in memory" % (path, len(saved), len(expected))
    return "%s: %r on disk, %r in memory" % (path, saved, expected)


class SessionCollection:
    """One private transcript/ledger per conversation and a small switcher index."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "conversations.json"
        self.records = {}
        self.active = None
        self._dirty, self._saved_at = False, 0.0  # A noted change not yet written, and when the index last was.
        # A list it can't use is never written over as if empty (kept_state); the chats on disk are listed again.
        raw, self._state = kept_state.read(self.path)
        self.records, self.active, self._state = session_kept.index_records(self.directory, raw, self._state)
        self._kept = session_kept.take_kept_index(self.directory, self.path, self.records)  # Removed once saved.
        self._recovered = set()   # Listed again from sessions/: the saved list's title wins once it can be read.
        if self._state in (kept_state.UNKNOWN, kept_state.DAMAGED):   # And every chat on disk it doesn't list yet.
            for identity, record in session_kept.recovered_index(self.directory / "sessions").items():
                if self.records.setdefault(identity, record) is record:
                    self._recovered.add(identity)
            self.active = max(self.records.values(), key=lambda r: r["updated"])["id"] if self.records else None
        if not self.records:
            # Preserve the prototype's previous single conversation on upgrade.
            legacy = SessionStore(directory).load()
            SessionStore(directory, legacy["id"]).save(legacy)
            self.update(legacy)
            self.active = legacy["id"]
            self.save()
        elif self.active is None:
            self.active = next(iter(self.records))

    def create(self, provider="codex_subscription"):
        state = SessionStore(self.directory).new(provider)
        SessionStore(self.directory, state["id"]).save(state)
        self.update(state)
        self.activate(state["id"])
        return state["id"]

    def update(self, state):
        first_user = next((m["text"] for m in state["messages"] if isinstance(m, dict)
                           and m.get("role") == "user" and isinstance(m.get("text"), str)), "")
        title = " ".join(first_user.split())[:64] or UNTITLED
        record = {"id": state["id"], "title": title, "provider": state["provider"], "updated": state["updated"]}
        changed = self.records.get(state["id"]) != record
        self.records[state["id"]] = record
        return changed

    def activate(self, session_id):
        if session_id not in self.records:
            raise ValueError("This conversation is not available")
        self.active = session_id
        self.save()

    def note(self, state):
        """A chat's state as the UI pump sees it: its record changes here, and ``save_if_due`` writes the index."""
        self._dirty = self.update(state) or self._dirty

    def save_if_due(self, interval=1.0):
        """Write the index when a noted change waits and it was last written over ``interval`` seconds ago."""
        if self._dirty and time.monotonic() - self._saved_at > interval:
            self.save()

    def ordered(self):
        return sorted(self.records.values(), key=lambda r: r.get("updated") or 0, reverse=True)

    def save(self):
        if self._state == kept_state.UNKNOWN:   # Read again: what it lists is taken in, never written over.
            raw, state = kept_state.read(self.path)
            listed, _active, self._state = session_kept.index_records(self.directory, raw, state)
            for identity, record in listed.items():
                if identity not in self.records or identity in self._recovered:
                    self.records[identity] = record
        if self._state in (kept_state.UNKNOWN, kept_state.DAMAGED) and not kept_state.keep_before_write(self.path):
            return   # Kept beside it first (every chat is listed again from sessions/), or not written at all.
        self._state, self._recovered = kept_state.OK, set()
        payload = {"version": 1, "active": self.active, "sessions": list(self.records.values())}
        temporary = self.path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, allow_nan=False)
        os.replace(temporary, self.path)
        kept_state.remove_copies(self._kept)   # What they listed is in the file now.
        self._kept = []
        self._dirty, self._saved_at = False, time.monotonic()
