# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Off-main-thread persistence of BYOK API keys in the OS key store.

The store is the macOS login Keychain, Windows Credential Manager or the Linux
Secret Service keyring; ``key_store`` selects it and documents the backend
contract. One item per connection: service ``key_store.SERVICE``, account
``<connection UUID>@<profile>``. The profile is a digest of the resolved
connections directory, so profiles selected with ANGELO_CONNECTIONS_DIR (tests,
isolated accounts) never read each other's keys, even when metadata recovery
copied a connection UUID between them. Moving or migrating the connections
folder therefore starts a new, empty namespace. Connection metadata stays
secret-free.

``SavedKeys`` runs every store call on ONE worker thread, holding
``key_store.STORE_CALLS`` (the macOS interaction switch is process-wide, and
the team sign-in's refresh token uses the same stores; libsecret and Credential
Manager calls are serialized the same way), and never on Blender's main thread. The main thread enqueues and
later pumps results. Before the first call the worker runs the backend's
optional ``probe``; when it reports the store ``unavailable``, every operation
answers ``unavailable`` without touching the store and ``unavailable`` holds
the reason, so the UI can switch the session to memory-only. Each operation
has a deadline: a call still waiting for a prompt (or a store that does not
answer) when it expires is reported as ``timeout`` (fail closed), its late
result is ignored, and an expired load/save that has not started is skipped. A
removal always runs, however late, so a removed connection cannot leave its key
behind once the store responds.

Outcomes: load ``loaded``/``absent``; save ``saved`` (read back by this build)
or ``saved_needs_approval`` (saved, but only another build may read it); forget
``forgotten``, or ``erased`` when this build may not delete another build's
item and overwrote its secret instead (macOS only); failures are kinds in
``FAILURES``.
"""

import hashlib
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .key_store import KINDS, STORE_CALLS

AUTOMATIC_TIMEOUT = 10.0     # seconds; automatic calls never wait for a prompt
INTERACTIVE_TIMEOUT = 120.0  # seconds for a person to answer an explicit OS prompt
FAILURES = KINDS


def profile_id(directory):
    """Stable discriminator for one connections directory (the path itself is not stored)."""
    resolved = str(Path(directory).expanduser().resolve())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]


def account_name(identity, profile):
    return "%s@%s" % (uuid.UUID(str(identity)), profile)


@dataclass(eq=False)
class _Operation:
    kind: str            # "load", "save" or "forget"
    identity: str
    generation: int
    deadline: float
    interactive: bool = False
    label: str = ""
    secret: str = ""
    expired: bool = False
    started: bool = False

    def __repr__(self):  # Never show a secret in a traceback or debugger listing.
        return "<Saved key %s %s>" % (self.kind, self.identity)


class SavedKeys:
    """Saved-key operations for one connections profile.

    ``backend`` follows the ``key_store`` contract: ``read(account,
    interactive)``, ``write(account, secret, label, interactive)``,
    ``delete(account, interactive)``, optionally ``erase`` and ``probe``, and
    raises an exception with a ``kind`` attribute on failure. On the main
    thread, ``state[identity]`` is ``(operation, outcome)`` and
    ``stored[identity]`` is True/False when known, None when unknown.
    ``unavailable`` is the probe's reason once the store proved unusable.
    """

    def __init__(self, backend, profile, *, clock=time.monotonic, automatic_timeout=AUTOMATIC_TIMEOUT,
                 interactive_timeout=INTERACTIVE_TIMEOUT):
        self.backend, self.profile, self.clock = backend, profile, clock
        self.automatic_timeout, self.interactive_timeout = automatic_timeout, interactive_timeout
        self.state, self.stored = {}, {}
        self._generations, self._pending = {}, []
        self._requests, self._results = queue.Queue(), queue.Queue()
        self._lock = threading.Lock()
        self.closed = False
        self.unavailable = ""
        self._thread = threading.Thread(target=self._run, daemon=True, name="AngeloSavedKeys")
        self._thread.start()

    # Main thread -----------------------------------------------------------

    def _enqueue(self, kind, identity, *, interactive=False, label="", secret=""):
        if self.closed:
            raise RuntimeError("Saved-key storage is closed")
        identity = str(uuid.UUID(str(identity)))
        generation = self._generations.get(identity, 0) + 1
        self._generations[identity] = generation
        timeout = self.interactive_timeout if interactive else self.automatic_timeout
        operation = _Operation(kind, identity, generation, self.clock() + timeout, interactive, label, secret)
        self._pending.append(operation)
        self.state[identity] = (kind, "waiting" if interactive else "running")
        self._requests.put(operation)
        return operation

    def load(self, identity, interactive=False):
        """Read a saved key. Only ``interactive=True`` may show an OS prompt."""
        return self._enqueue("load", identity, interactive=interactive)

    def save(self, identity, secret, label):
        return self._enqueue("save", identity, label=label, secret=secret)

    def forget(self, identity):
        return self._enqueue("forget", identity)

    def pump(self):
        """Apply finished or expired work: [(identity, operation, outcome, secret or None)]."""
        changes = []
        while True:
            try:
                operation, outcome, secret = self._results.get_nowait()
            except queue.Empty:
                break
            if operation in self._pending:
                self._pending.remove(operation)
            if operation.expired:
                # Only a removal is still meaningful after its deadline.
                if operation.kind == "forget" and outcome in {"forgotten", "erased"}:
                    if operation.generation == self._generations.get(operation.identity):
                        self._apply(operation, outcome)
                    changes.append((operation.identity, operation.kind, outcome + "_late", None))
                continue
            if operation.generation != self._generations.get(operation.identity):
                continue  # A newer action for this connection supersedes it.
            self._apply(operation, outcome)
            changes.append((operation.identity, operation.kind, outcome, secret))
        now = self.clock()
        for operation in list(self._pending):
            if now < operation.deadline:
                continue
            with self._lock:
                operation.expired = True
                if not operation.started:
                    operation.secret = ""
            self._pending.remove(operation)
            if operation.generation == self._generations.get(operation.identity):
                self._apply(operation, "timeout")
                changes.append((operation.identity, operation.kind, "timeout", None))
        return changes

    def _apply(self, operation, outcome):
        identity = operation.identity
        self.state[identity] = (operation.kind, outcome)
        if outcome in {"loaded", "saved"}:
            self.stored[identity] = True
        elif outcome in {"absent", "forgotten", "erased"}:
            self.stored[identity] = False
        elif operation.kind != "save" or outcome == "saved_needs_approval":
            self.stored[identity] = None  # The item may exist; this build just can't read or remove it.

    def busy(self, identity):
        return any(o.identity == identity for o in self._pending)

    def discard(self, identity):
        """Drop main-thread status for a connection that no longer exists."""
        self.state.pop(identity, None)
        self.stored.pop(identity, None)

    def close(self):
        """Stop accepting work. A call blocked on a prompt is abandoned, never joined."""
        with self._lock:
            self.closed = True
            for operation in self._pending:
                if not operation.started:
                    operation.secret = ""
        self._pending.clear()
        self._requests.put(None)

    # Worker thread ---------------------------------------------------------

    def _probe(self):
        probe = getattr(self.backend, "probe", None)
        if probe is None:
            return
        try:
            with STORE_CALLS:
                probe()
        except Exception as error:  # noqa: BLE001 - only a store-wide "unavailable" changes the mode.
            if getattr(error, "kind", "") == "unavailable":
                with self._lock:
                    self.unavailable = getattr(error, "message", "") or "The key store is unavailable"

    def _run(self):
        self._probe()
        while True:
            operation = self._requests.get()
            if operation is None:
                return
            with self._lock:
                skip = (operation.expired or self.closed) and operation.kind != "forget"
                operation.started = not skip
                secret, operation.secret = operation.secret, ""
            if skip:
                continue
            account = account_name(operation.identity, self.profile)
            loaded = None
            try:
                if self.unavailable:
                    outcome = "unavailable"  # The probe found no usable store; nothing is called.
                else:
                    with STORE_CALLS:  # Shared with the team sign-in's store calls (key_store).
                        outcome, loaded = self._call(operation, account, secret)
            except Exception as error:  # noqa: BLE001 - every failure must reach the UI as a kind.
                kind = getattr(error, "kind", "failed")
                outcome = kind if kind in FAILURES else "failed"
                loaded = None
            finally:
                secret = ""
            self._results.put((operation, outcome, loaded))

    def _call(self, operation, account, secret):
        """``(outcome, loaded secret or None)`` of one backend call."""
        if operation.kind == "load":
            loaded = self.backend.read(account, interactive=operation.interactive) or None
            return ("absent" if loaded is None else "loaded"), loaded  # An erased item is absent.
        if operation.kind == "save":
            verified = self.backend.write(account, secret, operation.label, interactive=False)
            return ("saved" if verified else "saved_needs_approval"), None
        return self._forget(account), None

    def _forget(self, account):
        erase = getattr(self.backend, "erase", None)  # Only the macOS Keychain has one.
        try:
            self.backend.delete(account, interactive=False)
            return "forgotten"
        except Exception as error:  # noqa: BLE001 - only a missing approval falls back.
            if getattr(error, "kind", "") != "approval" or erase is None:
                raise
        # Another build owns the item: remove the key material, report the shell.
        return "erased" if erase(account, interactive=False) else "forgotten"
