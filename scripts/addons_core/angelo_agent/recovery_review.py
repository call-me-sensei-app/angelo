# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded, escaped recovery evidence for approval cards and full Details.

Input is the existing host-owned detached review snapshot. Formatting never
supplies execution or correction authority.
"""

from collections.abc import Mapping
import json
import unicodedata

from .ui_text import iface_

def _recovery_text(args, *, complete=False):
    """Show only host evidence for this citation; never present a reason as fact.

    The barrier admits at most 64 field changes and 16 KiB of their complete
    values. Details keeps every admitted field. The card shows three changed
    values, with each value capped at 240 characters and an explicit Details
    link instruction; unchanged dictionary members cannot hide the difference.
    """
    evidence = args.get("_recovery")
    observation = evidence.get("observation") if isinstance(evidence, Mapping) and evidence.get("available") else None
    if (not isinstance(observation, Mapping) or not isinstance(observation.get("id"), str) or
            observation["id"] != args.get("recovery_observation")):
        return ""
    changes = observation.get("changes")
    if (not isinstance(changes, (list, tuple)) or not 0 < len(changes) <= 64 or
            any(not isinstance(row, Mapping) or not isinstance(row.get("handle"), str) or
                not isinstance(row.get("field"), str) or "before" not in row or "after" not in row for row in changes)):
        return ""
    try:
        if len(json.dumps(changes, ensure_ascii=False, allow_nan=False, default=_review_json_default).encode("utf-8")) > 16384:
            return ""
    except (ValueError, TypeError, OverflowError, RecursionError):
        return ""
    lines = [iface_("Observed scene changes (before → after):")]
    for change in changes:
        pairs = [((), change["before"], change["after"])] if complete else _changed_values(change["before"], change["after"])
        for path, before, after in pairs:
            label = change["handle"] + " · " + change["field"]
            if path:
                label += " · " + ".".join(path)
            limit = None if complete else 240
            values = _evidence_value(before, limit) + " → " + _evidence_value(after, limit)
            lines.append(iface_("{label}: {value}", label=_evidence_value(label, limit), value=values))
            if not complete and len(lines) == 4:
                break
        if not complete and len(lines) == 4:
            break
    if not complete:
        lines.append(iface_("Open Details for every observed change and its complete values."))
    return "\n".join(lines)

def _changed_values(before, after, path=()):
    if isinstance(before, Mapping) and isinstance(after, Mapping) and before.keys() == after.keys():
        for key in before:
            if before[key] != after[key] or type(before[key]) is not type(after[key]):
                yield from _changed_values(before[key], after[key], (*path, str(key)))
    else:
        yield path, before, after

def _review_json_default(value):
    # Prepared reviews freeze dictionaries as MappingProxyType; serialize only
    # their detached values, without weakening the immutable snapshot itself.
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError("Unsupported value in recovery evidence")

def _evidence_value(value, limit=None):
    # JSON preserves scalar types, float precision and visible newline escapes.
    # Escape bidi/format controls too, so scene data cannot impersonate UI text.
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
                         default=_review_json_default)
    text = "".join("\\u%04x" % ord(char) if unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} else char
                   for char in encoded)
    return text if limit is None or len(text) <= limit else text[:limit] + "…"

def _with_recovery(message, args):
    explanation = _recovery_text(args)
    return dict(message, body=explanation + "\n\n" + message["body"]) if explanation else message
