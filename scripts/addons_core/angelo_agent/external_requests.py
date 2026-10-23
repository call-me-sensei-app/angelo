# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""CLI commands on the service thread: allowlist, bounds, envelopes and projection.

Nothing here touches ``bpy``. Work that needs Blender or controller state is a
``Work`` item on a bounded queue that ``external_host`` drains from ``ui.pump``
on the main thread; the connection thread waits for it with a timeout.

Phase 1 is read-only. Every tool outside ``PHASE1_READ_TOOLS`` is refused here,
before anything reaches a controller, so no CLI path can admit a mutation,
file access, memory update, Studio usage, delegation or reviewed Python.
"""

import hashlib
import json
import os
import queue
import re
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from .routing import TOOLSET_VERSION
from .studio_spec import STUDIO_NAMES
from .tool_specs import BOUNDED_MUTATIONS, FILE_TOOLS, TOOL_MAP, dynamic_tools

PHASE1_READ_TOOLS = frozenset({
    "angelo_inspect_scene", "angelo_inspect_sculpt", "angelo_scene_search", "angelo_scene_object",
    "angelo_scene_relationships", "angelo_scene_delta", "angelo_scene_verify", "angelo_inspect_preview",
    "angelo_expand_recipe", "angelo_blender_api", "angelo_agent_capabilities", "angelo_agent_metrics",
    "angelo_memory_read",
})
# A tool annotation change must not silently widen what an external client can do.
if any(not TOOL_MAP[name]["annotations"]["readOnlyHint"] for name in PHASE1_READ_TOOLS):
    raise RuntimeError("The phase-1 external CLI allowlist may contain only read-only tools")

IMAGE_TOOLS = frozenset({"angelo_sculpt_view", "angelo_sculpt_review", "angelo_sculpt_target", "angelo_view"})
DEFERRED_COMMANDS = {
    "watch": "`angelo watch` is not available yet; poll with `angelo status --call ID`.",
    "stop": "`angelo stop` arrives with external mutation requests (phase 2); phase-1 reads finish on their own.",
    "observe": "`angelo observe` is not available yet; use `angelo call angelo_scene_search` or `angelo_scene_delta`.",
    "view": "`angelo view` is not available yet (visual observation is a later phase).",
}
WORK_QUEUE_SIZE = 16
MAIN_THREAD_TIMEOUT = 10.0
RATE_PER_SECOND = 20
AUDIT_BYTES = 256 * 1024
PATH_KEYS = frozenset({"path", "filepath", "file_path", "directory", "root"})
_ABSOLUTE = re.compile(r"(?:/|~|[A-Za-z]:[\\/]|\\\\)")


def exclusion_reason(name):
    """Why a tool is outside the phase-1 allowlist; shown by ``angelo tools``."""
    if name == "angelo_execute_python":
        return "Privileged, non-transactional Blender Python; never available without per-call exact-code review."
    if name in STUDIO_NAMES:
        return "Media Studio spends generation accounts or changes the library and needs individual usage approval."
    if name == "angelo_delegate":
        return "Consults another connected account (billing); an external client has no lead turn."
    if name in {"angelo_write_file", "angelo_delete_file"} or name == "angelo_memory_update":
        return "Individually approved file or project-note effect; deferred."
    if name in FILE_TOOLS:
        return "An unapproved read of the Blender project directory could escape the caller's own sandbox."
    if name == "angelo_read_reference":
        return "Nothing is attached to an External conversation."
    if name == "angelo_set_workspace_mode":
        return "Changes the artist's layout."
    if name in IMAGE_TOOLS:
        return "Image-bearing observation; planned for `angelo view` in a later phase."
    if not TOOL_MAP[name]["annotations"]["readOnlyHint"]:
        return "Scene mutation; external mutation requests under Ask approval are phase 2."
    return "Not in the phase-1 read-only allowlist."


_catalog_cache = {}


def _allowed_schemas():
    if TOOLSET_VERSION not in _catalog_cache:
        _catalog_cache.clear()
        _catalog_cache[TOOLSET_VERSION] = {tool["name"]: tool for tool in dynamic_tools(PHASE1_READ_TOOLS)}
    return _catalog_cache[TOOLSET_VERSION]


def tools_catalog(name=None, include_schema=False):
    """Discovery only: it grants nothing and host validation stays authoritative."""
    schemas = _allowed_schemas()
    allowed, excluded = [], []
    for tool_name in sorted(TOOL_MAP):
        if name is not None and tool_name != name:
            continue
        spec = TOOL_MAP[tool_name]
        if tool_name in PHASE1_READ_TOOLS:
            schema = schemas[tool_name]["inputSchema"]
            entry = {"name": tool_name, "description": spec["description"], "read_only": True,
                     "bounded": tool_name in BOUNDED_MUTATIONS, "approval": "none",
                     "schema_sha256": hashlib.sha256(json.dumps(schema, sort_keys=True, ensure_ascii=False,
                                                                separators=(",", ":")).encode("utf-8")).hexdigest()}
            if include_schema:
                entry["inputSchema"] = schema
            allowed.append(entry)
        else:
            excluded.append({"name": tool_name, "read_only": spec["annotations"]["readOnlyHint"],
                             "reason": exclusion_reason(tool_name)})
    return {"toolset_version": TOOLSET_VERSION, "phase": 1, "allowed": allowed, "excluded": excluded,
            "note": "Discovery grants nothing; Angelo validates and bounds every call."}


def result_code(result, *, document_changed=False, cancelled=False):
    """Map an actual host result to a CLI code from host state, never from message text."""
    if not isinstance(result, dict):
        return "uncertain"
    changes = result.get("changes")
    if (result.get("partial_changes_possible") or result.get("serialization_failed")
            or result.get("journal_durable") is False or isinstance(changes, dict) and changes.get("uncertain")):
        return "uncertain"
    if result.get("expired") or result.get("cancelled"):
        return "not_applied"
    if result.get("error") or result.get("success") is False:
        if document_changed:
            return "stale"
        return "not_applied" if cancelled else "tool_failed"
    return "ok"


def default_roots():
    """Private roots knowable without Blender; the host adds state/profile/project roots."""
    import tempfile
    return ((str(Path.home()), "<home>"), (tempfile.gettempdir(), "<temp>"))


def _root_pattern(roots):
    usable = []
    for path, token in roots:
        for variant in {str(path), os.path.realpath(str(path))}:
            variant = variant.rstrip("/\\")
            if len(variant) >= 4 and _ABSOLUTE.match(variant):
                usable.append((variant, token))
    usable.sort(key=lambda item: len(item[0]), reverse=True)
    if not usable:
        return None, {}
    tokens = dict(usable)
    pattern = re.compile("|".join(re.escape(path) for path, _ in usable) + r"(?=[/\\]|$|[^\w.\-])")
    return pattern, tokens


def project(value, roots):
    """Remove private absolute paths from a host result before it leaves Angelo.

    ``path``-like keys holding absolute paths become ``"<withheld>"``; a
    ``path`` also gains an opaque ``view_id`` digest for a future ``view``
    command. Private roots inside other strings become placeholder tokens.
    """
    pattern, tokens = _root_pattern(roots)

    def strip(text):
        return pattern.sub(lambda match: tokens[match.group(0)], text) if pattern else text

    def walk(item, depth):
        if depth > 96:
            return None
        if isinstance(item, str):
            return strip(item)
        if isinstance(item, list):
            return [walk(entry, depth + 1) for entry in item]
        if isinstance(item, dict):
            output = {}
            for key, entry in item.items():
                if key in PATH_KEYS and isinstance(entry, str) and _ABSOLUTE.match(entry):
                    output[key] = "<withheld>"
                    if key == "path" and "view_id" not in item:
                        output["view_id"] = hashlib.sha256(entry.encode("utf-8", "replace")).hexdigest()[:32]
                else:
                    output[strip(key) if isinstance(key, str) else key] = walk(entry, depth + 1)
            return output
        return item

    return walk(value, 0)


class Work:
    """One main-thread request. The host claims it once; a timed-out waiter abandons it."""

    def __init__(self, kind, client, payload):
        self.kind, self.client, self.payload = kind, client, payload
        self.enqueued = time.monotonic()
        self.claimed_at = self.completed_at = None
        self.admitted = False
        self.response = None
        self._event = threading.Event()
        self._claimed = threading.Event()
        self._lock = threading.Lock()
        self._state = "queued"

    def claim(self):
        with self._lock:
            if self._state != "queued":
                return False
            self._state, self.claimed_at = "claimed", time.monotonic()
            self._claimed.set()
            return True

    def wait_claimed(self, timeout):
        return self._claimed.wait(timeout)

    def abandon(self):
        """True when the host never claimed this work; it will now never run."""
        with self._lock:
            if self._state == "queued":
                self._state = "abandoned"
                return True
            return False

    def complete(self, response):
        with self._lock:
            if self._event.is_set():
                return
            self.response, self.completed_at = response, time.monotonic()
            self._event.set()

    def wait(self, timeout):
        return self._event.wait(timeout)


class WorkQueue:
    def __init__(self, size=WORK_QUEUE_SIZE):
        self._queue = queue.Queue(maxsize=size)

    def submit(self, work):
        try:
            self._queue.put_nowait(work)
            return True
        except queue.Full:
            return False

    def take(self):
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    def fail_all(self, response):
        while (work := self.take()) is not None:
            if work.claim():
                work.complete(response)

    def __len__(self):
        return self._queue.qsize()


class RateLimiter:
    """At most ``limit`` admitted calls per label in any one-second window."""

    def __init__(self, limit=RATE_PER_SECOND, clock=time.monotonic):
        self.limit, self.clock = limit, clock
        self._seen = {}
        self._lock = threading.Lock()

    def allow(self, label):
        now = self.clock()
        with self._lock:
            window = self._seen.setdefault(label, deque())
            while window and now - window[0] >= 1.0:
                window.popleft()
            if len(window) >= self.limit:
                return False
            window.append(now)
            if len(self._seen) > 256:
                self._seen.pop(next(iter(self._seen)))
            return True


class AuditLog:
    """Bounded owner-only ``cli/audit.jsonl``: metadata and argument digests, never arguments or results."""

    def __init__(self, path, limit=AUDIT_BYTES):
        self.path, self.limit = Path(path), limit
        self._lock = threading.Lock()

    def record(self, **fields):
        line = (json.dumps(dict(fields, time=round(time.time(), 3)), sort_keys=True) + "\n").encode("utf-8")
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        with self._lock:
            try:
                descriptor = os.open(self.path, flags, 0o600)
                try:
                    os.write(descriptor, line)
                    size = os.fstat(descriptor).st_size
                finally:
                    os.close(descriptor)
                if size > self.limit:
                    self._trim()
            except OSError:
                pass  # Audit is best-effort; transcripts and the invocation journal stay authoritative.

    def _trim(self):
        data = self.path.read_bytes()
        kept = data[-(self.limit // 2):]
        kept = kept[kept.find(b"\n") + 1:]
        temporary = self.path.with_name(self.path.name + "." + uuid.uuid4().hex + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(kept)
        os.replace(temporary, self.path)


def _digest(value):
    try:
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                         separators=(",", ":")).encode("utf-8")).hexdigest()
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return None


class ClientRegistry:
    """label -> conversation ID in ``<state>/cli/clients.json`` (owner-only, atomic replace).

    A label is routing and display data, not a security principal.
    """

    def __init__(self, state_directory):
        self.path = Path(state_directory) / "cli" / "clients.json"

    def load(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        clients = raw.get("clients") if isinstance(raw, dict) else None
        output = {}
        for label, record in (clients or {}).items() if isinstance(clients, dict) else ():
            try:
                output[label] = str(uuid.UUID(record["session_id"]))
            except (TypeError, ValueError, KeyError, AttributeError):
                continue
        return output

    def save(self, mapping):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_name("clients." + uuid.uuid4().hex + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "clients": {label: {"session_id": identity}
                                                 for label, identity in mapping.items()}}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)


def external_label(state_directory, session_id):
    """The client label owning a conversation, or "" (used when ui builds a controller)."""
    for label, identity in ClientRegistry(state_directory).load().items():
        if identity == session_id:
            return label
    return ""
