# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Structural bounds for a complete neutral primitive graph, independent of JSON writer accounting.

This proves only identifier-copy plus JSON encoding. Currency, fact excerpts,
headers and final composition require their own declared components before
the routing owner can reserve a whole candidate's final work.
"""

from collections import Counter
from dataclasses import dataclass
import math
import sys

from .handoff_json import INVALID, WORK_LIMIT, integer_decimal_bound, integer_decimal_upper
from .identifiers import _HANDLE_KEYS, _REVISION_KEYS


@dataclass(frozen=True)
class NeutralShape:
    values: int
    keys: int
    dictionaries: int
    string_chars: int
    number_chars: int
    json_chars: int


@dataclass(frozen=True)
class FinalWork:
    nodes: int
    characters: int
    json_chars: int


class ShapeBuilder:
    """Used only during the existing bounded primitive copy, before admission returns."""

    def __init__(self, compact_json):
        self.values = self.keys = self.dictionaries = self.string_chars = self.number_chars = self.json_chars = 0
        self.comma, self.colon = (1, 1) if compact_json else (2, 2)

    def string(self, value):
        # The copy owner already debited these character inspections before this walk.
        self.string_chars += len(value)
        encoded = 2
        for character in value:
            code = ord(character)
            if character == '"' or character == "\\" or code in (8, 9, 10, 12, 13):
                encoded += 2
            elif code < 32 or 0xD800 <= code <= 0xDFFF:
                encoded += 6
            else:
                encoded += 1
        self.json_chars += encoded

    def key(self, value):
        self.keys += 1
        self.string(value)

    def value(self, value, budget):
        self.values += 1
        kind = type(value)
        if kind is dict:
            self.dictionaries += 1
            count = len(value)
            self.json_chars += 2 + max(0, count - 1) * self.comma + count * self.colon
        elif kind is list:
            self.json_chars += 2 + max(0, len(value) - 1) * self.comma
        elif kind is str:
            self.string(value)
        elif value is None:
            self.json_chars += 4
        elif kind is bool:
            self.json_chars += 4 if value else 5
        elif kind is int:
            admitted = integer_decimal_bound(value, budget.chars_left, budget)
            if admitted is None:
                return WORK_LIMIT
            # Refinement may prepay less in this admission room. Future work
            # must retain the room-independent upper bound, not that quota.
            size = integer_decimal_upper(value)
            self.number_chars += size
            self.json_chars += size
        elif kind is float:
            if not math.isfinite(value):
                return INVALID
            # IEEE float round-trip digits + bounded exponent digits/sign/punctuation.
            # This is deliberately independent of json.dumps/encode_json output.
            size = sys.float_info.dig + 2 + len(str(sys.float_info.max_exp)) + len("-.e+")
            self.number_chars += size
            self.json_chars += size
        return None

    def finish(self):
        return NeutralShape(self.values, self.keys, self.dictionaries, self.string_chars,
                            self.number_chars, self.json_chars)


def identifier_json_bound(shape):
    """Upper bound for existing identifier copy followed by tracked JSON encoding.

    Copy: each value/key once; dict close plus all possible handle/revision
    field probes. String fields may be scanned again by those probes.
    Encoding: each value/key once, plus at most one root-member endpoint per
    key. A positive character remains for a final endpoint visit after scans.
    Short identifiers/revisions never grow strings; key collisions remove work.
    ``json_chars`` independently includes quotes, escapes and all separators.
    """
    if type(shape) is not NeutralShape:
        raise ValueError("A final bound requires an admitted neutral shape")
    probes = Counter((*_HANDLE_KEYS, *_REVISION_KEYS))
    scan_multiplicity = max(probes.values(), default=0)
    copy_nodes = shape.values + shape.keys + (1 + len(_HANDLE_KEYS) + len(_REVISION_KEYS)) * shape.dictionaries
    encode_nodes = shape.values + shape.keys + shape.keys
    copy_chars = (1 + scan_multiplicity) * shape.string_chars
    encode_chars = shape.string_chars + shape.number_chars
    return FinalWork(copy_nodes + encode_nodes, copy_chars + encode_chars + 1, shape.json_chars)


def neutral_graph(value, *, budget, compact_json=False):
    """Complete exact-primitive owned copy plus independently collected shape, or refusal."""
    from .handoff_projection import compact
    return compact(value, budget=budget, measure_shape=True, compact_json=compact_json)
