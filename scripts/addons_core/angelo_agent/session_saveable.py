# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A chat's state made of values its file can hold: ``make_saveable``.

The journal is finite JSON in UTF-8 (``session_journal.encode``). Its encoder refuses, before it writes anything:

* with ``ValueError``: a number that is not finite (as a value or as a key), text that is not valid UTF-8, a
  container that holds itself, an integer too long to write in decimal (Python's digit limit);
* with ``TypeError``: a value JSON has no form for (bytes, a set, a number or an array of numpy's, a Blender
  vector, a path, any other object) and a key that is not text, a number, a boolean or None;
* with ``RecursionError``: containers nested deeper than its stack.

Such a value reaches a chat's state from outside the store: a JSON escape for half of a character (JSON allows
``"\\ud83d"`` alone, and a model or a connected app can send one in a tool's arguments, an ID or its text), bytes
Python read as lone surrogates (a file name that is not UTF-8, a Blender string cut inside a character), a
measurement (a division by a zero duration, an unknown remaining time), a row or a field a part of Angelo wrote
from Python values it never passed through JSON. The value then stays in the state, so every later save fails on
it too.

``SessionStore.save`` and ``compact`` (``held``) call this only after a write raised one of those errors: a save
that succeeds, every ordinary one, runs nothing of it. It changes what the file holds of the state (``SAVED``; the
state's other keys are runtime values no save writes, and are never touched) in place, because the controller
holds its rows by index and by object:

* text: a surrogate pair held as two code units becomes its character; a lone surrogate becomes U+FFFD;
* a number that is not finite: null (JSON has no other value for it);
* an integer too long to write in decimal: its hexadecimal text, which is exact;
* a container reached again inside itself: null at that reference;
* a tuple holding one of these: a list, as the file would hold it anyway;
* bytes: text, read as UTF-8 with U+FFFD for what is not; a path: its text;
* a set: a sorted list; a mapping that is not a dict: a dict; a sized iterable (a vector, an array): a list;
* a number that is not Python's own (numpy's, a fraction, a decimal): Python's;
* an object that converts itself (``tolist``, ``to_list``, ``to_dict``): what that gives;
* any other object: null;
* a key: text as text; a number that is not finite, an integer too long and bytes as their text; a tuple, a set or
  another container as the JSON text of its saveable form; any other object as ``<its type's name>``. When two
  keys become the same key, the later one's value stays;
* only when the encoder, or this walk, ran out of stack: null for what is nested deeper than ``DEEPEST`` levels.

Every change is counted by kind (``make_saveable`` returns the counts, ``note`` prints them and keeps them on the
store as ``made_saveable``): the kind and, for an object, its type's name, never a value.

Standard library only; no bpy.
"""

import collections
import collections.abc
import functools
import json
import math
import numbers
import os

# What the journal's encoder raises for a value of the state (``tool_finalization.PERSISTENCE_ERRORS`` names them too).
UNSAVEABLE = (ValueError, TypeError, RecursionError, OverflowError)
# The state's keys a save writes (``SessionStore._fields`` and ``session_journal.STRUCTURED``); pinned by
# ``test_native_unsaveable_state_recovers``, so a field saved from now on is cleaned too.
SAVED = ("version", "id", "provider", "native_id", "connection_id", "model", "updated", "options", "approval_version",
         "toolset_version", "runtime_sessions", "context_slot", "next_route", "task_memory", "document_file",
         "messages", "results", "invocations")
DEEPEST = 200        # Levels of containers kept when the stack ran out: far past any value a tool or a model sends.
SHORT_BITS = 2000    # An integer of this many bits is under every digit limit Python allows (640 digits at least).
BYTES = (bytes, bytearray, memoryview)
PLAIN = (str, int, float, bool, list, dict, tuple, bytes, type(None))   # What a foreign object may stand for.
CONVERTERS = ("tolist", "to_list", "to_dict")   # numpy's scalars and arrays; Blender's ID property arrays and groups.
_NONE = object()


def held(write):
    """A ``SessionStore`` write of ``(store, state, ...)`` that does not fail for good on a value of ``state``.

    When ``write`` raises for a value (``UNSAVEABLE``) and ``state`` held one the file cannot hold, every such value
    is made saveable in place, what was changed is noted (``note``) and ``write`` runs once more, rewriting the file
    whole (rows changed that no save was told about). Before, the value stayed in the state, every later save
    failed on it too, and the chat admitted no work until Angelo was started again (the showcase pilot,
    2026-11-14). An error that no value of ``state`` caused is raised as it was; so is whatever the second write
    raises: one more attempt, never a loop.
    """
    @functools.wraps(write)
    def saving(store, state, *args, **kwargs):
        with store._writer.lock:   # Re-entrant: ``write`` takes it too, and the two attempts are one save.
            try:
                return write(store, state, *args, **kwargs)
            except UNSAVEABLE as error:
                kinds = make_saveable(state, deep=isinstance(error, RecursionError))
                if not kinds:
                    raise
                note(store, kinds)
                store._memory = (None, None, None)   # The cleaned task memory was made from the memory as it was.
                store._writer.reset()
                return write(store, state, *args, **kwargs)
    return saving


def note(store, kinds):
    """Record what a save changed: how many values of which kinds, never a value (as ``journal_refusal`` does)."""
    total = getattr(store, "made_saveable", None) or collections.Counter()
    total.update(kinds)
    store.made_saveable = total
    print("Angelo: a chat's state held %d value(s) its file cannot hold; each was made saveable: %s"
          % (sum(kinds.values()), ", ".join("%s (%d)" % item for item in sorted(kinds.items()))))


def text(value):
    """``value`` as text UTF-8 can encode (the same object when it already is)."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        # UTF-16 pairs the halves of a character and has no form for a lone one.
        return value.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")
    return value


def make_saveable(state, deep=False):
    """Change what the file holds of ``state`` (``SAVED``) in place so the journal can encode it.

    Returns a ``Counter`` of the kinds of value changed, empty when nothing in ``state`` was what the encoder
    refuses: ``text``, ``nonfinite``, ``integer``, ``cycle``, ``key``, ``depth``, ``converted:<type>`` and
    ``dropped:<type>``. ``deep``: the encoder ran out of stack, so nothing deeper than ``DEEPEST`` is kept; the same
    is done when this walk runs out of stack itself.
    """
    kinds = collections.Counter()
    for deepest in ((DEEPEST,) if deep else (None, DEEPEST)):
        walk = _Walk(kinds, deepest)
        try:
            for key in SAVED:
                if key in state:
                    new = walk.value(state[key], 0)
                    if new is not state[key]:
                        state[key] = new
        except RecursionError:
            if deepest is not None:
                raise
            continue   # What the first pass changed stays changed; the bounded pass finishes it.
        break
    return kinds


def _short(value):
    """True when the encoder can write the integer ``value`` in decimal."""
    if value.bit_length() <= SHORT_BITS:
        return True
    try:
        int.__repr__(value)   # What the encoder calls, whatever a subclass prints.
    except ValueError:
        return False
    return True


def _ordered(items):
    """``items`` as a list in an order of its own: sorted, or by type and form when its values have no order."""
    items = list(items)
    try:
        return sorted(items)
    except TypeError:
        return sorted(items, key=lambda item: (type(item).__name__, repr(item)))


def _plain(value):
    """The plain value an object JSON has no form for stands for, or ``_NONE`` when it stands for none."""
    if isinstance(value, BYTES):
        return bytes(value).decode("utf-8", "replace")
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Number):
        return float(value)
    if isinstance(value, os.PathLike):
        return os.fspath(value)
    if isinstance(value, collections.abc.Mapping):
        return dict(value.items())
    if isinstance(value, collections.abc.Set):
        return _ordered(value)
    for name in CONVERTERS:
        convert = getattr(value, name, None)
        if callable(convert):
            return convert()
    kind = type(value)
    if hasattr(kind, "__len__") and hasattr(kind, "__iter__"):
        return list(value)
    return _NONE


class _Walk:
    """One pass over a state: the kinds it changed, the containers it is inside and how deep it may go."""

    def __init__(self, kinds, deepest):
        self.kinds, self.deepest, self.open, self.count = kinds, deepest, set(), 0

    def changed(self, kind, new):
        self.kinds[kind] += 1
        self.count += 1
        return new

    def value(self, value, depth):
        """``value`` as the file can hold it (the same object when it can); a dict or a list is cleaned in place."""
        if isinstance(value, str):
            new = text(value)
            return value if new is value else self.changed("text", new)
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value if _short(value) else self.changed("integer", hex(value))
        if isinstance(value, float):
            return value if math.isfinite(value) else self.changed("nonfinite", None)
        if isinstance(value, (dict, list, tuple)):
            if id(value) in self.open:
                return self.changed("cycle", None)
            if self.deepest is not None and depth >= self.deepest:
                return self.changed("depth", None)
            if isinstance(value, tuple):
                items, before = list(value), self.count
                self.container(items, depth + 1)
                return value if self.count == before else items
            self.container(value, depth + 1)
            return value
        return self.foreign(value, depth)

    def foreign(self, value, depth):
        """An object JSON has no form for: the plain value it stands for, cleaned in its turn, or None."""
        name = type(value).__name__
        try:
            plain = _plain(value)
        except Exception:   # Whatever a foreign object's own conversion raises: it stands for no plain value.
            plain = _NONE
        if type(plain) not in PLAIN:
            return self.changed("dropped:" + name, None)
        self.changed("converted:" + name, None)
        return self.value(plain, depth)

    def key(self, key):
        """``key`` as a key the file can hold (the same object when it is one)."""
        if isinstance(key, str):
            return text(key)
        if key is None or isinstance(key, bool):
            return key
        if isinstance(key, int) and _short(key) or isinstance(key, float) and math.isfinite(key):
            return key
        plain = _Walk(collections.Counter(), DEEPEST).value(key, 0)
        if plain is None:
            return repr(float(key)) if isinstance(key, float) else "<%s>" % type(key).__name__
        if isinstance(plain, (str, int, float)):
            return plain
        return json.dumps(plain, ensure_ascii=False, allow_nan=False)

    def container(self, value, depth):
        """Clean the dict or list ``value`` in place."""
        self.open.add(id(value))
        try:
            if isinstance(value, dict):
                kept, renamed = [], False
                for key, item in list(value.items()):
                    new_key, new_item = self.key(key), self.value(item, depth)
                    if new_key is not key:
                        renamed = self.changed("key", True)
                    kept.append((new_key, new_item))
                    if new_item is not item:
                        value[key] = new_item
                if renamed:   # In place and in order: every entry again, each under the key the file can hold.
                    value.clear()
                    value.update(kept)
            else:
                for index, item in enumerate(value):
                    new_item = self.value(item, depth)
                    if new_item is not item:
                        value[index] = new_item
        finally:
            self.open.discard(id(value))
