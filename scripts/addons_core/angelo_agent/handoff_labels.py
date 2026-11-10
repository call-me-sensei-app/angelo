# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded historical receipt labels; the same wording as the existing owner."""

import math

from .handoff_json import INVALID, UNSUPPORTED, WORK_LIMIT, integer_decimal_bound
from .handoff_receipts import Refused, _shared_edits


def display(value, reader, limit=64):
    reader.visit()
    kind = type(value)
    if kind is str:
        text = value
    elif value is None:
        text = "None"
    elif kind is bool:
        text = "True" if value else "False"
    elif kind is int:
        upper = integer_decimal_bound(value, reader.budget.chars_left, reader.budget)
        if upper is None:
            raise Refused(WORK_LIMIT)
        try:
            text = str(value)
        except ValueError:
            raise Refused(INVALID) from None
    elif kind is float and math.isfinite(value):
        text = str(value)
    else:
        raise Refused(UNSUPPORTED)
    kept = min(len(text), limit if len(text) <= limit else limit - 1)
    output = []
    for index in range(kept):
        if not reader.budget.scan():
            raise Refused(WORK_LIMIT)
        character = text[index]
        output.append(" " if ord(character) < 32 or character == "\x7f" else character)
    return "".join(output) + ("…" if len(text) > limit else "")


def number(value, reader):
    reader.visit()
    kind = type(value)
    if kind is not int and kind is not float:
        return None
    if kind is int and max(1, (value.bit_length() + 3) // 4) > reader.budget.chars_left:
        raise Refused(WORK_LIMIT)
    if kind is float and not math.isfinite(value):
        raise Refused(INVALID)
    try:
        encoded = format(value, ".6g")
    except (ValueError, OverflowError):
        raise Refused(INVALID) from None
    if not reader.budget.scan(len(encoded)):
        raise Refused(WORK_LIMIT)
    return encoded


def value_text(value, reader):
    kind = type(value)
    if value is not None and not (kind is str or kind is int or kind is float or kind is bool
                                  or kind is list or kind is dict):
        raise Refused(UNSUPPORTED)
    if type(value) is str:
        return "'" + display(value, reader, 48) + "'"
    numerical = number(value, reader)
    if numerical is not None:
        return numerical
    if type(value) is bool or value is None:
        return "true" if value is True else "false" if value is False else "none"
    if type(value) is list and len(value) <= 4:
        values = []
        for item in reader.rows(value):
            encoded = number(item, reader)
            if encoded is None:
                return None
            values.append(encoded)
        return "[" + ", ".join(values) + "]"
    return None


def decimal(value, reader):
    reader.visit()
    if type(value) is not int:
        raise Refused(UNSUPPORTED)
    upper = integer_decimal_bound(value, reader.budget.chars_left, reader.budget)
    if upper is None:
        raise Refused(WORK_LIMIT)
    try:
        encoded = str(value)
    except ValueError:
        raise Refused(INVALID) from None
    return encoded


def labels(result, reader):
    output = {}
    for section_name, row_name in (("live_revisions", "objects"), ("delta", "modified")):
        section = reader.read(result, section_name)
        if section is None:
            continue
        if type(section) is not dict:
            raise Refused(UNSUPPORTED)
        rows = reader.read(section, row_name)
        if type(rows) is not list:
            continue
        for row in reader.rows(rows):
            if type(row) is not dict:
                continue
            name = reader.read(row, "name")
            if type(name) is not str:
                continue
            for key in (("handle", "alias") if section_name == "live_revisions" else ("handle",)):
                handle = reader.read(row, key)
                if type(handle) is str:
                    if section_name == "live_revisions":
                        output[handle] = name
                    else:
                        output.setdefault(handle, name)
    return output


def terse_values(kind, observed, reader):
    output = []
    if type(observed) is not dict:
        return output
    if kind is not None and type(kind) is not str:
        raise Refused(UNSUPPORTED)
    for key, value in reader.items(observed):
        if key in ("scope", "space") or value is True:
            continue
        text = value_text(value, reader)
        if text is not None:
            output.append(text if key == kind or type(kind) is str and kind.endswith("_" + key) else key + " " + text)
    return output


def changes(result, reader):
    status = reader.read(result, "status")
    changes = reader.read(result, "changes" if status == "committed" else "delta")
    if type(changes) is not dict:
        changes = {}
    parts = []
    for key in ("added", "modified"):
        rows, kept, count = reader.read(changes, key), [], 0
        if type(rows) is not list:
            continue
        for row in reader.rows(rows):
            if type(row) is dict:
                count += 1
                if len(kept) < 8:
                    name = display(reader.read(row, "name", ""), reader)
                    kept.append("'" + name + "'" + (" (" + display(reader.read(row, "type", ""), reader, 16) + ")"
                                                    if key == "added" else ""))
        if count:
            parts.append(key + " " + ", ".join(kept) + (" and %d more" % (count - 8) if count > 8 else ""))
    for row in _shared_edits(reader, result):
        users, count = row["users"], row["count"]
        names = []
        for name in reader.rows(users):
            names.append("'" + display(name, reader) + "'")
        part = "material '%s' edited for all %s users (%s%s)" % (
            display(row["material"], reader), decimal(count, reader), ", ".join(names),
            " and %s more" % decimal(count - min(len(users), 8), reader) if count > min(len(users), 8) else "")
        holders = row.get("holders")  # This row was built here from admitted fields, not untrusted keys.
        if holders:
            shown = []
            for index in range(min(len(holders), 8)):
                reader.visit()
                shown.append("'" + display(holders[index], reader) + "'")
            part += " and in data no object uses (%s)" % ", ".join(shown)
        parts.append(part)
    return "; ".join(parts) or "no object changes reported"
