# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""When to suggest an engine's live link, and what the agent is told about engines. No Blender here.

``engine_detect`` answers which engines run; this module decides:

* **The suggestion** (``Memory.next_suggestion``): an engine whose editor runs while the live link is off, and that
  Angelo has never suggested before, gets one card (``engine_suggest_ui``). Each engine is suggested at most once per
  person, whatever they answer, and never while the link is on or after they turned it on once (they know it). The
  memory is one small JSON file beside the chat's other local state; a damaged file suggests nothing, so it can never
  nag.
* **The agent's note** (``note``, ``Reported``): which engine editors run, and whether the live link is on, are added to a request's
  observation only when that changed since the agent was last told in this conversation, so an unchanged computer
  adds no bytes to any request. Only engine names ever reach it, never a process name or a path.
"""

import json
import os
import uuid

from . import shared_replace
from .engine_detect import DISPLAY, ENGINES

FILE_NAME = "engine-suggestions.json"


class Memory:
    """The engines already suggested, kept in ``path`` (None: this session only)."""

    def __init__(self, path=None):
        self.path, self.suggested, self.readable = path, set(), True
        if path and os.path.exists(path):
            try:
                value = json.loads(shared_replace.read_text(path))   # Sharing delete: never refuses a save.
                self.suggested = {item for item in value.get("suggested", []) if item in ENGINES}
            except (OSError, ValueError, AttributeError):
                self.readable = False   # Unknown history: suggest nothing rather than repeat a card.

    def done(self):
        """Nothing is left to suggest: every engine was suggested once (or the history can't be read)."""
        return not self.readable or self.suggested >= set(ENGINES)

    def next_suggestion(self, running, link_on, link_used):
        """The engine to suggest now, or None. ``link_used``: the person turned the link on before (Angelo
        remembers it), so they know it."""
        if link_on or link_used or not self.readable:
            return None
        for engine in ENGINES:   # A fixed order, so two engines at once give one card and the next one later.
            if engine in running and engine not in self.suggested:
                return engine
        return None

    def mark(self, engine):
        self.suggested.add(engine)
        if not self.path:
            return
        temporary = "%s.%s.tmp" % (self.path, uuid.uuid4().hex)   # Its own: another window may save at once.
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(temporary, "x", encoding="utf-8") as handle:
                json.dump({"suggested": sorted(self.suggested)}, handle)
            shared_replace.replace(temporary, self.path)
        except OSError as error:   # Remembered for this session only, and said.
            print("Angelo: the engine suggestions shown could not be saved:", error)
            if os.path.exists(temporary):
                os.remove(temporary)


def note(running, link_on):
    """The observation's ``engines`` value: engine names only, in English (model-visible text is never translated).
    Installed engines are not in it (they would add bytes to every first request on a game developer's computer);
    the ``FEATURES`` lookup lists them."""
    value = {"running": [DISPLAY[engine] for engine in ENGINES if engine in running], "live_link": bool(link_on)}
    if running and not link_on:
        value["hint"] = ("Angelo's engine live link edits this engine's models here and updates them there; "
                         "angelo_agent_capabilities family FEATURES, names [\"engine_link\"], says how to turn it on")
    return value


class Reported:
    """What one conversation's agent was last told, so a request carries the note only when it changed."""

    def __init__(self):
        self.last = None

    def take(self, value):
        """``value`` if the agent hasn't been told it yet (then it has), else None. Nothing detected and never
        reported adds nothing."""
        if value == self.last or (self.last is None and not value["running"] and not value["live_link"]):
            return None
        self.last = value
        return value
