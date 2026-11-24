# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Isolated units in right-to-left text: the isolate marks, and the lines laid out with them (``rtl_text``).

A filled-in value (a share, a path, a file or model name) is wrapped in FIRST STRONG ISOLATE ... POP DIRECTIONAL
ISOLATE (``isolate``) so ``rtl_text.to_visual`` lays it out as one unit in its own first strong direction.
``_explicit`` turns every isolate in a line into balanced LEFT-TO-RIGHT or RIGHT-TO-LEFT ISOLATE (or unwraps one with
no strong letter), and wrapping reopens a unit cut between two rows (``open_isolates``, ``carry_isolates``,
``isolated_tail``, ``marked_index``).

Visual text cannot say where a unit was, so the lines ``to_visual`` laid out with units are remembered with their
marked reading order (``_remember``, ``_recall``): the 2048 most recently laid out or read back, never a form only
measured (``probing``). No ``bpy``; no import of ``rtl_text`` (it imports this module).
"""

import threading
import unicodedata
from contextlib import contextmanager

_LRI, _RLI, _FSI, _PDI = "\u2066", "\u2067", "\u2068", "\u2069"
_MARKS = _LRI + _RLI + _FSI + _PDI
_NO_MARKS = {ord(mark): None for mark in _MARKS}
_ISOLATED = {}  # Visual line -> its reading order with explicit isolates, for lines ``to_visual`` laid out with units.
_ISOLATED_LIMIT = 2048   # Lines, least recently used first out: a line drawn and read back each frame stays.
_PROBING = threading.local()   # ``probing`` depth on this thread.


def strip_isolates(text):
    """``text`` without isolate marks (U+2066 to U+2069)."""
    return text.translate(_NO_MARKS) if any(mark in text for mark in _MARKS) else text


def isolate(value):
    """``value`` marked to be laid out as one unit in its own first strong direction (each line on its own).

    Marks already in ``value`` are removed first, so text from a person, a model or a file system can never
    unbalance the units around it.
    """
    return "\n".join(_FSI + line + _PDI for line in strip_isolates(str(value)).split("\n"))


def _first_strong(text):
    """``"L"``, ``"R"`` or ``None``: the direction of the first strong character outside nested isolates."""
    depth = 0
    for ch in text:
        if ch in (_LRI, _RLI, _FSI):
            depth += 1
        elif ch == _PDI:
            depth = max(0, depth - 1)
        elif not depth:
            bidi = unicodedata.bidirectional(ch)
            if bidi == "L":
                return "L"
            if bidi in ("R", "AL"):
                return "R"
    return None


def _explicit(line):
    """``line`` with balanced, explicit isolates: each FIRST STRONG ISOLATE becomes LEFT-TO-RIGHT or RIGHT-TO-LEFT
    ISOLATE by its content, one with no strong character is unwrapped (laid out as without it), an unmatched POP is
    dropped and an isolate left open is closed at the end of the line."""
    if not any(mark in line for mark in _MARKS):
        return line
    out, opened = list(line), []
    for position, ch in enumerate(line):
        if ch in (_LRI, _RLI, _FSI):
            opened.append(position)
        elif ch == _PDI:
            if opened:
                _settle(line, out, opened.pop(), position)
            else:
                out[position] = ""
    tail = []
    for start in reversed(opened):
        if _settle(line, out, start, len(line)):
            tail.append(_PDI)
    return "".join(out) + "".join(tail)


def _settle(line, out, start, end):
    """Resolve the isolate opened at ``start`` and closed at ``end``; False when it is unwrapped."""
    mark = line[start]
    if mark == _FSI:
        direction = _first_strong(line[start + 1:end])
        mark = None if direction is None else _LRI if direction == "L" else _RLI
    out[start] = mark or ""
    if end < len(line):
        out[end] = _PDI if mark else ""
    return mark is not None


def open_isolates(logical):
    """The explicit isolate marks still open at the end of ``logical`` (to start the next wrapped row with)."""
    opened = []
    for ch in logical:
        if ch in (_LRI, _RLI):
            opened.append(ch)
        elif ch == _PDI and opened:
            opened.pop()
    return "".join(opened)


def carry_isolates(rows):
    """Reading-order ``rows`` cut from one marked paragraph, each after the first opening the isolates the row
    before it left open, so a unit cut over two rows is a unit on each."""
    carried, out = "", []
    for row in rows:
        row = carried + row
        out.append(row)
        carried = open_isolates(row)
    return out


def isolated_tail(marked, length):
    """The last ``length`` characters (isolate marks not counted) of the marked reading-order ``marked``, opening
    first the isolates still open where it starts, so a tail cut from a unit is laid out as that unit."""
    index, kept = len(marked), 0
    while index > 0 and kept < length:
        index -= 1
        kept += marked[index] not in _MARKS
    return open_isolates(marked[:index]) + marked[index:]


def marked_index(marked, count):
    """The index in ``marked`` after its first ``count`` characters that are not isolate marks (``count`` itself when
    ``marked`` has no mark), to place a cut counted in drawn characters on marked reading-order text."""
    if count <= 0 or not any(mark in marked for mark in _MARKS):
        return count
    seen = 0
    for index, ch in enumerate(marked):
        if seen == count:
            return index
        seen += ch not in _MARKS
    return len(marked) + count - seen


@contextmanager
def probing():
    """Within it, ``to_visual`` remembers nothing: for forms only measured (a fit search), never drawn or read back."""
    _PROBING.depth = getattr(_PROBING, "depth", 0) + 1
    try:
        yield
    finally:
        _PROBING.depth -= 1


def _recall(visual):
    """The marked reading order remembered for ``visual``, else ``None``; a hit becomes the most recently used."""
    marked = _ISOLATED.pop(visual, None)
    if marked is not None:
        _ISOLATED[visual] = marked
    return marked


def _remember(visual, logical):
    if getattr(_PROBING, "depth", 0):
        return
    _ISOLATED.pop(visual, None)
    _ISOLATED[visual] = logical
    while len(_ISOLATED) > _ISOLATED_LIMIT:
        try:
            del _ISOLATED[next(iter(_ISOLATED))]
        except (StopIteration, RuntimeError, KeyError):   # Changed by another thread meanwhile; bounded next time.
            break
