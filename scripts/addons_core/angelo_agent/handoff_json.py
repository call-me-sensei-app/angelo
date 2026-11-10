# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded primitive JSON for a handoff; no serializer callbacks or authority.

One budget belongs to one handoff. Failed encodings keep their inspection cost.
The caller owns a stable input snapshot and one output buffer, which also counts
headers and omission markers. Routing uses that same shared owner throughout.
"""

import json
import math
from dataclasses import dataclass

WORK_LIMIT = "work_limit"
OUTPUT_LIMIT = "output_limit"
UNSUPPORTED = "unsupported_type"
INVALID = "invalid_json"
CYCLE = "cycle"
_ESCAPES = {'"': '\\"', '\\': '\\\\', '\b': '\\b', '\f': '\\f', '\n': '\\n', '\r': '\\r', '\t': '\\t'}


class HandoffBudget:
    """Node and input-character credit, both derived from the caller's existing limit."""

    def __init__(self, limit):
        if type(limit) is not int or limit < 1:
            raise ValueError("A handoff requires a positive integer character budget")
        self._limit = limit
        self._nodes_left = self._chars_left = limit
        self._output_claimed = False
        self._final_nodes_bound = self._final_chars_bound = 0
        self._projecting = False

    @property
    def limit(self):
        return self._limit

    @property
    def nodes_left(self):
        return self._nodes_left - (0 if self._projecting else self._final_nodes_bound)

    @property
    def chars_left(self):
        return self._chars_left - (0 if self._projecting else self._final_chars_bound)

    @property
    def remaining_nodes(self):
        return self._nodes_left

    @property
    def remaining_chars(self):
        return self._chars_left

    @property
    def final_bound(self):
        return self._final_nodes_bound, self._final_chars_bound

    def reserve_final(self, nodes, characters):
        """Raise a proven final-work bound, without transferring or refunding credit.

        Admission work sees only credit above this fence. Failure leaves both
        the fence and remaining credit unchanged; any preceding inspection is
        still spent. Node work needs positive character headroom (visit's rule).
        """
        if self._projecting:
            raise ValueError("Final projection cannot admit more work")
        if (type(nodes) is not int or type(characters) is not int or nodes < 0 or characters < 0
                or nodes + characters == 0 or nodes > 0 and characters == 0):
            raise ValueError("A final work bound uses nonnegative integer credit with positive headroom")
        if nodes > self.nodes_left or characters > self.chars_left:
            return False
        self._final_nodes_bound += nodes
        self._final_chars_bound += characters
        return True

    def begin_final_projection(self):
        """One-way phase change; remaining counters never increase or reset."""
        if self._projecting:
            raise ValueError("Final projection already began")
        self._projecting = True

    def visit(self):
        if self.nodes_left < 1 or self.chars_left < 1:
            return False
        self._nodes_left -= 1
        return True

    def scan(self, count=1):
        if type(count) is not int or count < 1:
            raise ValueError("Character inspection consumes positive integer credit")
        if count > self.chars_left:
            self._chars_left = 0 if self._projecting else self._final_chars_bound
            return False
        self._chars_left -= count
        return True

    @property
    def exhausted(self):
        return self.nodes_left == 0 or self.chars_left == 0


class HandoffOutput:
    """All final text, including headers/stubs. Removing output never refunds work."""

    def __init__(self, budget):
        if budget._output_claimed:
            raise ValueError("A handoff budget already has its output owner")
        budget._output_claimed = True
        self._budget, self._parts, self._used = budget, [], 0

    @property
    def used(self):
        return self._used

    @property
    def remaining(self):
        return self._budget.limit - self.used

    def append(self, text):
        if type(text) is not str:
            raise TypeError("Handoff output is text")
        if len(text) > self.remaining:
            return False
        if text:
            self._parts.append(text)
            self._used += len(text)
        return True

    def pop(self):
        text = self._parts.pop()
        self._used -= len(text)
        return text

    def render(self):
        return "".join(self._parts)


@dataclass(frozen=True)
class JsonResult:
    text: str | None = None
    reason: str | None = None
    member_ends: dict | None = None


class _Refused(Exception):
    pass


def integer_decimal_upper(value):
    """A room-independent decimal upper bound for an exact builtin integer."""
    if type(value) is not int:
        raise ValueError("Decimal size requires an exact builtin integer")
    return max(1, (value.bit_length() * 30103 + 99999) // 100000) + int(value < 0)


def integer_decimal_bound(value, room, budget):
    """Prepay a decimal allocation upper bound before str(), or refuse.

    30102/100000 < log10(2) < 30103/100000. When the upper
    estimate crosses room, a bounded power-of-ten comparison can prove a
    tighter upper bound without constructing the decimal string. The lower
    estimate rejects huge inputs before negation or power allocation; any
    remaining temporary integer is proportional to the caller's own room.
    """
    if type(value) is not int or type(room) is not int or room < 1:
        return None
    sign, bits = int(value < 0), value.bit_length()
    upper = integer_decimal_upper(value)
    if upper <= room:
        return upper if budget.scan(upper) else None
    lower = max(1, ((max(0, bits - 1) * 30102) // 100000) + 1) + sign
    if lower > room or room <= sign:
        return None
    # The threshold integer is itself proportional to room. Pay before that
    # allocation too; rejected near-threshold attempts cannot repeat for free.
    if not budget.scan(room):
        return None
    magnitude = -value if sign else value
    return room if magnitude < 10 ** (room - sign) else None


def admit_mapping(value, budget):
    """Prove exact string keys before any keyed lookup into untrusted history.

    Even an exact dict can contain a custom key with a colliding hash; get or
    membership can then call its equality method. Iterating existing keys does
    not hash or compare them. No admission is cached across mutable records.
    """
    if type(value) is not dict:
        return UNSUPPORTED
    iterator = iter(value)
    try:
        for _ in range(len(value)):
            if not budget.visit():
                return WORK_LIMIT
            key = next(iterator)
            if type(key) is not str:
                return UNSUPPORTED
            if key and not budget.scan(len(key)):
                return WORK_LIMIT
    except (RuntimeError, StopIteration):
        return INVALID
    return None


class _Writer:
    def __init__(self, limit, budget):
        self.limit, self.budget, self.parts, self.used = limit, budget, [], 0

    @property
    def remaining(self):
        return self.limit - self.used

    def put(self, text):
        if len(text) > self.remaining:
            raise _Refused(OUTPUT_LIMIT)
        self.parts.append(text)
        self.used += len(text)

    def string(self, value):
        if len(value) + 2 > self.remaining:
            raise _Refused(OUTPUT_LIMIT)  # Lower bound: do not scan a huge rejected string.
        self.put('"')
        for character in value:
            if not self.budget.scan():
                raise _Refused(WORK_LIMIT)
            code = ord(character)
            escaped = _ESCAPES.get(character)
            if escaped is None:
                escaped = "\\u%04x" % code if code < 32 or 0xD800 <= code <= 0xDFFF else character
            self.put(escaped)
        self.put('"')


def encode_json(value, *, budget, compact=False, max_chars=None, track_members=False):
    """Complete JSON or a refusal, using exact builtins only; never partial receipt JSON.

    Valid Unicode JSON matches ensure_ascii=False. Surrogate code units are
    escaped explicitly so the result can be transported as UTF-8. Cycles and
    non-JSON/custom values are refused without repr or serializer callbacks.
    """
    if type(compact) is not bool or type(track_members) is not bool:
        return JsonResult(reason=UNSUPPORTED)
    limit = budget.limit if max_chars is None else max_chars
    if type(limit) is not int or limit < 1:
        return JsonResult(reason=OUTPUT_LIMIT)
    writer = _Writer(min(limit, budget.limit), budget)
    comma, colon = (",", ":") if compact else (", ", ": ")
    stack, active, member_ends = [("value", value)], set(), {}
    try:
        while stack:
            action, current = stack.pop()
            if action == "member_end":
                if not budget.visit():
                    raise _Refused(WORK_LIMIT)
                member_ends[current] = writer.used
                continue
            if action == "array":
                container, index, length = current
                if index == length:
                    if len(container) != length:
                        raise _Refused(INVALID)
                    active.remove(id(container))
                    writer.put("]")
                    continue
                if budget.exhausted:
                    raise _Refused(WORK_LIMIT)
                if index:
                    writer.put(comma)
                stack.extend((("array", (container, index + 1, length)), ("value", container[index])))
                continue
            if action == "object":
                container, iterator, index, length = current
                if index == length:
                    if len(container) != length:
                        raise _Refused(INVALID)
                    active.remove(id(container))
                    writer.put("}")
                    continue
                if not budget.visit():
                    raise _Refused(WORK_LIMIT)
                key, child = next(iterator)
                if type(key) is not str:
                    raise _Refused(UNSUPPORTED)
                if index:
                    writer.put(comma)
                writer.string(key)
                writer.put(colon)
                stack.append(("object", (container, iterator, index + 1, length)))
                if track_members and container is value:
                    stack.append(("member_end", key))
                stack.append(("value", child))
                continue
            if not budget.visit():
                raise _Refused(WORK_LIMIT)
            kind = type(current)
            if current is None:
                writer.put("null")
            elif kind is bool:
                writer.put("true" if current else "false")
            elif kind is str:
                writer.string(current)
            elif kind is int:
                room = min(writer.remaining, budget.chars_left)
                work_limited = writer.remaining > budget.chars_left
                upper = integer_decimal_bound(current, room, budget)
                if upper is None:
                    raise _Refused(WORK_LIMIT if work_limited else OUTPUT_LIMIT)
                encoded = str(current)  # Exact builtin int only; never a custom conversion.
                writer.put(encoded)
            elif kind is float:
                if not math.isfinite(current):
                    raise _Refused(INVALID)
                encoded = json.dumps(current, allow_nan=False)  # Exact finite IEEE float scalar, not a container.
                if not budget.scan(len(encoded)):
                    raise _Refused(WORK_LIMIT)
                writer.put(encoded)
            elif kind is list or kind is dict:
                identity = id(current)
                if identity in active:
                    raise _Refused(CYCLE)
                active.add(identity)
                if kind is list:
                    writer.put("[")
                    stack.append(("array", (current, 0, len(current))))
                else:
                    writer.put("{")
                    stack.append(("object", (current, iter(current.items()), 0, len(current))))
            else:
                raise _Refused(UNSUPPORTED)
    except _Refused as error:
        return JsonResult(reason=error.args[0])
    except (ValueError, OverflowError, RuntimeError, IndexError, StopIteration):
        return JsonResult(reason=INVALID)
    return JsonResult(text="".join(writer.parts), member_ends=member_ends if track_members else None)
