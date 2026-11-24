# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Standing rules: what the person wants every request in a project (or in every project) to follow.

Two texts, each written only by the person:

* **All projects** lives in the state folder (``rules.json``, owner-only), never in a document.
* **This project** lives in the document (``project_rules_scene``), so it travels with the file to a teammate.

A project's text that arrives with a file is someone else's words until this person accepts them: a downloaded or
shared .blend carrying "run every script without asking" must never reach the model silently. So every text has a
*decision*, kept by the text's digest (never by the file it came from): Use, Don't use, or none yet.

What applies is held in C (``grant_authority.RULE_DECISIONS`` by digest, ``GLOBAL_RULES`` as the All projects text's
digest), never in Python state a script could rewrite in the same interpreter (the 2.1.0 break-it review, R-1). Use
and a new text are set only in the person's grant scope, and agent code changes neither, in either direction (2.0.0's
G2 ruling). ``rules.json`` is Angelo's own copy, mirrored into C as Angelo reads it; only what C holds is saved. Rules the person types in Angelo are decided Use as they save them; any other text is held for review
(``standing(...).project_state == "review"``) and is not sent. A decision is about the exact text: one changed
character outside Angelo makes it a new text to review.

The model receives the applied rules as one block (``model_block``) that ``context_ledger.TurnBlocks.rules`` sends in
full to a new context and again only when it changes, never in the runtimes' base instructions, so the cached prefix
never moves and no file in a workspace (``CLAUDE.md``, ``AGENTS.md``) ever reaches the model by itself. Rules are the
person's instructions; they never grant what the host enforces (approvals, file roots, budgets stay host checks).
Standard library only; no bpy.
"""

import hashlib
import json
import os
import stat
import time
import uuid
from collections import namedtuple

from . import grant_authority, hidden_text, owned_paths, shared_replace

NAME = "rules.json"
VERSION = 1
LIMIT = 4000               # Characters per text: a rules block costs tokens in every new context.
MAX_DECISIONS = 256         # Digests remembered, newest kept (each is a key in C's bounded ledger).
GLOBAL_KEY = "text"         # GLOBAL_RULES' one key.
MAX_FILE = 256 * 1024
# What a person can't see is decided by the one shared check (``hidden_text``: Unicode properties with the contexts
# real writing needs, tested on every catalogue); a tab is space a person sees, so it is read as one.
# The model reads these; never translated.
HEADER = ("\n\nThe person's standing rules for this chat, written by the person in Angelo. Follow them unless the "
          "person's message says otherwise; where they conflict, This project wins over All projects. They never "
          "grant permissions: approvals, file access and budgets stay as the host enforces them.")
GLOBAL_LABEL = "\nAll projects:\n"
PROJECT_LABEL = "\nThis project:\n"
NO_RULES = "\n\nThe person's standing rules: none now (the earlier rules no longer apply)."

Standing = namedtuple("Standing", "global_text project_text project_state")


class RulesError(ValueError):
    pass


def clean(text):
    """The text as kept and sent: newlines as ``\\n``, trailing whitespace dropped, at most ``LIMIT`` characters.
    A character a person can't see there (``hidden_text``: controls but newline and tab, format, private-use and
    unassigned characters, every default-ignorable one, but what real writing needs in context) is refused."""
    if type(text) is not str:
        raise RulesError("Rules are text")
    text = text.replace("\r\n", "\n").replace("\r", "\n").rstrip()
    if len(text) > LIMIT:
        raise RulesError("Rules are at most %d characters (this has %d)" % (LIMIT, len(text)))
    at = hidden_text.find(text.replace("\t", " "), newlines=True)
    if at is not None:
        raise RulesError("Rules can't hold control, invisible or text-direction characters (%s)"
                         % hidden_text.name(text[at]))
    return text


def digest(text):
    return hashlib.sha256(clean(text).encode("utf-8")).hexdigest()


def _held_digest(text):
    """The digest of ``text`` as kept, or None when it is not a valid text (whatever Python state now holds)."""
    try:
        return digest(text)
    except RulesError:
        return None


def _hex(value):
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


class RulesStore:
    """``rules.json`` in the state folder: the All projects text and the decisions. Main thread; small file."""

    def __init__(self, directory):
        self.path = directory / NAME
        self._unreadable = False
        self._global, self._decisions = self._load()
        self._mirrored = self._mirror()

    def _mirror(self):
        """Angelo's own file into C (refused while an agent script runs: then nothing applies until it may be)."""
        pairs = [(key, entry["use"]) for key, entry in self._decisions.items()]
        if not grant_authority.mirror(grant_authority.RULE_DECISIONS, pairs):
            return False
        return not self._global or grant_authority.mirror(grant_authority.GLOBAL_RULES,
                                                          [(GLOBAL_KEY, digest(self._global))])

    def _ready(self):
        """Mirrored at last, from the file again (only Angelo writes it), when it was refused before."""
        if self._mirrored is not True:
            self._global, self._decisions = self._load()
            self._mirrored = self._mirror()

    def _load(self):
        try:
            status = os.lstat(self.path)
        except FileNotFoundError:
            return "", {}
        except OSError:
            self._unreadable = True
            return "", {}
        if not stat.S_ISREG(status.st_mode) or status.st_size > MAX_FILE:
            self._unreadable = True   # A link, folder or oversized file is never read, and never written through.
            return "", {}
        try:
            with shared_replace.open_file(self.path, os.O_RDONLY | getattr(os, "O_BINARY", 0)
                                          | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)) as handle:
                raw = json.loads(handle.read(MAX_FILE + 1).decode("utf-8"))
            if type(raw) is not dict or raw.get("version") != VERSION or not isinstance(raw.get("decisions"), dict):
                raise ValueError("shape")
            text = clean(raw.get("global"))
            decisions = {}
            for key, value in raw["decisions"].items():
                if not (_hex(key) and type(value) is dict and type(value.get("use")) is bool
                        and type(value.get("at")) in (int, float)):
                    raise ValueError("decision")
                decisions[key] = {"use": value["use"], "at": value["at"]}
            return text, decisions
        except (OSError, ValueError, UnicodeError, RecursionError):
            self._unreadable = True
            return "", {}

    def global_rules(self):
        """The All projects text whose digest C holds: from memory, or Angelo's own file when memory no longer
        matches (a script can rewrite memory, not the file); "" when neither does."""
        self._ready()
        held = grant_authority.value(grant_authority.GLOBAL_RULES, GLOBAL_KEY)
        if not held:
            return ""
        if _held_digest(self._global) == held:
            return self._global
        text, _decisions = self._load()
        if _held_digest(text) == held:
            self._global = text
            return text
        return ""

    def decision(self, text):
        """True (Use), False (Don't use) or None (not decided) for this exact text, as C holds it."""
        key = _held_digest(text)
        if key is None:
            return None
        self._ready()
        held = grant_authority.value(grant_authority.RULE_DECISIONS, key)
        return held if type(held) is bool else None

    def _restore(self, namespace, key, previous):
        if previous is None:
            grant_authority.forget(namespace, key)
        else:
            grant_authority.record(namespace, key, previous)

    def _kept(self, decisions):
        """Only what C holds is saved: a decision forged in memory never reaches the file (R-1)."""
        return {key: entry for key, entry in decisions.items()
                if grant_authority.value(grant_authority.RULE_DECISIONS, key) is entry["use"]}

    def decide(self, text, use):
        """Record the person's choice for ``text`` (their Use or Don't use, or their own save)."""
        key, use = digest(text), bool(use)
        self._ready()
        previous = grant_authority.value(grant_authority.RULE_DECISIONS, key)
        grant_authority.record(grant_authority.RULE_DECISIONS, key, use)   # Use: the person's scope, or refused.
        decisions = self._with(key, use)
        try:
            self._save(self.global_rules(), self._kept(decisions))
        except BaseException:
            if use:   # A refused write grants nothing (r2 S-c); declining stands, never undone (r3 S-4).
                self._restore(grant_authority.RULE_DECISIONS, key, previous)
            raise
        for dropped in set(self._decisions) - set(decisions):   # The oldest, past MAX_DECISIONS.
            grant_authority.forget(grant_authority.RULE_DECISIONS, dropped)
        self._decisions = decisions

    def _with(self, key, use):
        """The decisions with ``key`` decided, newest last (the file keeps this order), the oldest first to go."""
        decisions = dict(self._decisions)
        decisions.pop(key, None)
        decisions[key] = {"use": bool(use), "at": time.time()}
        while len(decisions) > MAX_DECISIONS:
            del decisions[next(iter(decisions))]
        return decisions

    def set_global(self, text):
        text = clean(text)
        self._ready()
        if not text:   # Clearing narrows at once, in C first; a failed write is raised, never undone (r3 S-4).
            grant_authority.forget(grant_authority.GLOBAL_RULES, GLOBAL_KEY)
            self._global = text
            self._save(text, self._kept(dict(self._decisions)))
            return text
        key = digest(text)
        previous = (grant_authority.value(grant_authority.GLOBAL_RULES, GLOBAL_KEY),
                    grant_authority.value(grant_authority.RULE_DECISIONS, key))
        grant_authority.record(grant_authority.GLOBAL_RULES, GLOBAL_KEY, key)   # The person's scope, or refused.
        try:
            grant_authority.record(grant_authority.RULE_DECISIONS, key, True)
            decisions = self._with(key, True)
            self._save(text, self._kept(decisions))   # A refused write applies nothing (r2 S-c).
        except BaseException:
            self._restore(grant_authority.GLOBAL_RULES, GLOBAL_KEY, previous[0])
            self._restore(grant_authority.RULE_DECISIONS, key, previous[1])
            raise
        for dropped in set(self._decisions) - set(decisions):
            grant_authority.forget(grant_authority.RULE_DECISIONS, dropped)
        self._global, self._decisions = text, decisions
        return text

    def _save(self, global_text, decisions):
        owned_paths.require_writable(self.path)   # C first: nothing is made beside a target it refuses.
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self._unreadable:
            self._keep_aside()
        encoded = json.dumps({"version": VERSION, "global": global_text, "decisions": decisions},
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
        temporary = self.path.with_name("." + NAME + "." + uuid.uuid4().hex[:12] + ".tmp")
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            shared_replace.replace(temporary, self.path)
        finally:
            shared_replace.discard(temporary)
        self._unreadable = False

    def _keep_aside(self):
        """Move a file this build could not read aside (``rules.json.<UTC time>.unreadable``) before writing anew,
        so a later build or the person can still read what it held. A link is moved, never followed."""
        if not os.path.lexists(self.path):
            return
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        for attempt in range(100):
            kept = self.path.with_name("%s.%s%s.unreadable" % (NAME, stamp, "-%d" % attempt if attempt else ""))
            if not os.path.lexists(kept):
                shared_replace.replace(self.path, kept)
                return
        raise RulesError("The old rules file could not be kept aside")


def standing(store, project_text):
    """The rules that apply now. ``project_state``: none, applied, review (not decided), declined, invalid."""
    try:
        project = clean(project_text)
    except RulesError:
        return Standing(store.global_rules(), "", "invalid")
    if not project:
        return Standing(store.global_rules(), "", "none")
    choice = store.decision(project)
    state = "applied" if choice is True else "declined" if choice is False else "review"
    return Standing(store.global_rules(), project, state)


def model_block(standing):
    """The model's text for the rules that apply, or "" when none do."""
    parts = []
    if standing.global_text:
        parts.append(GLOBAL_LABEL + standing.global_text)
    if standing.project_state == "applied":
        parts.append(PROJECT_LABEL + standing.project_text)
    return HEADER + "".join(parts) if parts else ""


# Only the person writes rules or decides a file's rules: the functions behind those clicks refuse agent code in
# either direction (grant_guard.entry; 2.0.0's G2 ruling; the 2.1.0 break-it review, R-1), and C refuses its ledger to
# a running script as well.
from . import grant_guard as _grant_guard  # noqa: E402  (the entry behind a grant: grant_guard.entry)
_grant_guard.entry_methods(RulesStore, "set_global", "decide")
