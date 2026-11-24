# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scripts the person approved once for a project, by their exact bytes (2.1.0). **Off in 2.1.0** (``OFFERED``):
nothing below is offered or honoured until a run-time check keeps a remembered script to its own bytes.

A build script the agent runs 40 times used to need 40 approvals. On a script's card, once the person has opened its
exact text, **Always run this exact script in this project** records it here; the next run of the very same bytes in
the same project runs without asking (``TrustedScripts.reviewed_script_allowed``), and any change to a single byte
asks again. The person sees and removes the project's list on the Rules page (``rules_sheet``, Scripts).

* **Bound to the bytes.** An entry is the SHA-256 of the script's UTF-8 text, never its name, description or a path.
* **Per user and project.** The file is in the person's state folder (``reviewed-scripts.json``, owner-only),
  never in a .blend, so a downloaded project can't bring approvals with it. A project is its document id and its
  real path together (``project_key``): a copy of a file elsewhere, or another file with a borrowed id, is another
  project. An unsaved document has no project, so nothing is remembered for it.
* **Bounded.** ``MAX_PER_PROJECT`` scripts per project and ``MAX_PROJECTS`` projects, the oldest dropped first.
* **Held in C** (2.0.0's rule for every standing consent, grant_operators.rst "Authority in C"): each entry is also
  ``grant_authority.REVIEWED_SCRIPTS`` in C's ledger (``ledger_key``), a namespace C defines as it starts. Remembering
  writes C first, which only the person's own grant scope (their aimed press on the card's button) may widen; reading
  the file mirrors it into C as Angelo's own decision, never while an agent script runs; a script runs unasked only
  while the file and C both hold it, so nothing a script changes in Python or on disk is a consent.

Main thread; small file. Standard library only; no bpy.
"""

import hashlib
import json
import os
import stat
import time
import uuid
from pathlib import Path

from . import grant_authority, owned_paths, script_reach, shared_replace

NAME = "reviewed-scripts.json"
# Whether Always run this exact script is offered and honoured at all. Off in 2.1.0 (break-it re-read of 23413c6bae1,
# M-1): a check over a script's text can't promise "only its own bytes", since a remembered script can run Python kept
# elsewhere (a text block through wm.context_set_value, a logging factory). It turns on only once a run-time check in
# C refuses every compile, exec or import outside the approved bytes and is re-reviewed. Off: nothing is offered,
# nothing remembered runs unasked, and the Rules page has no Scripts tab.
OFFERED = False
# The file's version. Bump it (to 2) in the same change that turns OFFERED on: approvals the 2.1.0 dev builds remembered
# under the unsound text check are then dropped, never honoured (re-read of 1c2c4edaf48, S-3).
VERSION = 1
MAX_PER_PROJECT = 200
MAX_PROJECTS = 100
MAX_FILE = 2 * 1024 * 1024
LABEL = 120


def digest(code):
    """The SHA-256 a remembered script is kept by (hashed once while it is pending: ``script_reach.text_key``), or
    None for text that can't be UTF-8, which is never remembered (A-7)."""
    key, strict = script_reach.text_key(code)
    return key if strict else None


def runs_other_code(code):
    """Whether ``code`` may run code that isn't its own text, so Always run this exact script never covers it
    (``script_reach``: judged once per script, by its digest)."""
    return script_reach.reaches(code)


def project_key(document_id, path):
    """The project an approval belongs to, or "" for an unsaved document."""
    if not document_id or not path:
        return ""
    return hashlib.sha256((str(document_id) + "\0" + os.path.realpath(path)).encode("utf-8")).hexdigest()


def ledger_key(project, code_digest):
    """C's key for a remembered script: its project, then its digest (both hex, so never a "/")."""
    return "%s/%s" % (project, code_digest)


def _held(project, code_digest):
    return grant_authority.holds(grant_authority.REVIEWED_SCRIPTS, ledger_key(project, code_digest))


def _hex(value):
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


class ReviewedScripts:
    def __init__(self, directory):
        self.path = Path(directory) / NAME
        self._projects = self._load()
        grant_authority.mirror(grant_authority.REVIEWED_SCRIPTS, [   # Angelo's own file, held in C (refused in a script).
            (ledger_key(project, key), True) for project, entries in self._projects.items() for key in entries])

    def _load(self):
        try:
            status = os.lstat(self.path)
            if not stat.S_ISREG(status.st_mode) or status.st_size > MAX_FILE:
                return {}
            raw = json.loads(shared_replace.read_bytes(self.path).decode("utf-8"))
        except (OSError, ValueError, UnicodeError):
            return {}
        projects = raw.get("projects") if isinstance(raw, dict) and raw.get("version") == VERSION else None
        if not isinstance(projects, dict):
            return {}
        clean = {}
        for key, entries in projects.items():
            if _hex(key) and isinstance(entries, dict):
                clean[key] = {code: {"label": str(entry.get("label") or "")[:LABEL], "at": float(entry.get("at") or 0),
                                     "bytes": int(entry.get("bytes") or 0)}
                              for code, entry in entries.items() if _hex(code) and isinstance(entry, dict)
                              and type(entry.get("at")) in (int, float) and type(entry.get("bytes")) is int}
        return clean

    def allowed(self, project, code):
        if not project or not isinstance(code, str):
            return False
        code_digest = digest(code)
        return (code_digest is not None and code_digest in self._projects.get(project, {})
                and _held(project, code_digest))

    def add(self, project, code, label):
        if not project or not isinstance(code, str):
            raise ValueError("Scripts are remembered only for a saved project")
        if digest(code) is None:
            raise ValueError("This script's text can't be remembered")
        projects = {key: dict(value) for key, value in self._projects.items()}
        entries = projects.pop(project, {})
        projects[project] = entries   # Newest project last: the oldest goes first.
        entries.pop(digest(code), None)
        entries[digest(code)] = {"label": " ".join(str(label or "").split())[:LABEL], "at": time.time(),
                                 "bytes": len(code.encode("utf-8"))}
        while len(entries) > MAX_PER_PROJECT:
            del entries[next(iter(entries))]
        while len(projects) > MAX_PROJECTS:
            del projects[next(iter(projects))]
        key = ledger_key(project, digest(code))
        grant_authority.record(grant_authority.REVIEWED_SCRIPTS, key, True)   # C first: only the person's own scope.
        try:
            self._save(projects)   # Then the file: a failed write approves nothing, in memory or C (r2 S-c).
        except BaseException:
            grant_authority.forget(grant_authority.REVIEWED_SCRIPTS, key)
            raise
        self._projects = projects

    def revoke(self, project, code_digest):
        projects = {key: dict(value) for key, value in self._projects.items()}
        entries = projects.get(project, {})
        if entries.pop(code_digest, None) is None:
            return False
        if not entries:
            projects.pop(project, None)
        self._projects = projects   # Narrows at once, in memory first (r3 S-4): a failed write is raised, never undone.
        grant_authority.forget(grant_authority.REVIEWED_SCRIPTS, ledger_key(project, code_digest))
        self._save(projects)
        return True

    def entries(self, project):
        """``[(digest, label, at, bytes)]`` of ``project``, newest first."""
        return [(key, entry["label"], entry["at"], entry["bytes"])
                for key, entry in reversed(list(self._projects.get(project, {}).items()))]

    def _save(self, projects):
        owned_paths.require_writable(self.path)   # C first: nothing is made beside a target it refuses.
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_name("." + NAME + "." + uuid.uuid4().hex[:12] + ".tmp")
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"version": VERSION, "projects": projects}, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            shared_replace.replace(temporary, self.path)
        finally:
            shared_replace.discard(temporary)


_stores = {}


def store(directory):
    key = str(directory)
    found = _stores.get(key)
    if found is None:
        found = _stores[key] = ReviewedScripts(directory)
    return found


# Remembering a script is the person's approval (the card's button); forgetting one narrows (grant_guard.entry).
from . import grant_guard as _grant_guard  # noqa: E402  (the entry behind a grant: grant_guard.entry)
_grant_guard.entry_methods(ReviewedScripts, "add")
