# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded unverified receipt policy; never label unchecked work as verified."""

from .handoff_excerpt import _joined, _partial
from .handoff_json import CYCLE, UNSUPPORTED, WORK_LIMIT
from .handoff_labels import changes, decimal
from .handoff_program_copy import copied
from .handoff_projection import Projection, compact
from .handoff_receipts import Reader, Refused, _live_rows, _receipt_rows, _shared_edits, commit_facts


def uncertain(value, reader):
    """Existing uncertainty inventory, iterative paths limited to the one revision-budget exception."""
    active, stack = set(), [("value", value, 0)]
    while stack:
        action, item, state = stack.pop()
        reader.visit()
        if action == "leave":
            active.remove(id(item))
            continue
        if action == "dict":
            mapping, iterator = item
            try:
                key, child = next(iterator)
            except StopIteration:
                continue
            stack.append(("dict", item, state))
            kind = type(child)
            if child is not None and not (kind is dict or kind is list or kind is str or kind is int
                                          or kind is float or kind is bool):
                raise Refused(UNSUPPORTED)
            if kind is str and child and not reader.budget.scan(len(child)):
                raise Refused(WORK_LIMIT)
            if key == "reason" and type(child) is str and child == "BUDGET" and state == 4:
                continue
            if key and not reader.budget.scan(len(key)):
                raise Refused(WORK_LIMIT)
            label = key.lower()
            warned = False
            for part in ("warning", "error", "uncertain", "partial", "cleanup", "truncated", "reason", "failure",
                         "inconclusive", "unverified", "replayed"):
                if label and not reader.budget.scan(len(label)):
                    raise Refused(WORK_LIMIT)
                if part in label:
                    warned = True
                    break
            if ((bool(child) and warned) or key in ("success", "passed", "verified", "journal_durable") and child is False
                    or key == "status" and type(child) is str and child in (
                        "fail", "failed", "error", "inconclusive", "uncertain", "unverified", "cancelled", "partial")):
                return True
            if key == "checks":
                checked = reader.positive_rows(child)
                if checked.reason:
                    raise Refused(checked.reason)
                if not checked.positive:
                    return True
            next_state = (1 if state == 0 and key == "live_revisions" else
                          2 if state == 1 and key == "objects" else
                          4 if state == 3 and key == "revision_omitted" else -1)
            stack.append(("value", child, next_state))
            continue
        if action == "list":
            values, index = item
            if index == len(values):
                continue
            stack.extend((("list", (values, index + 1), state),
                          ("value", values[index], 3 if state == 2 else -1)))
            continue
        kind = type(item)
        if kind is dict or kind is list:
            if id(item) in active:
                raise Refused(CYCLE)
            active.add(id(item))
            stack.append(("leave", item, state))
            stack.append(("dict", (item, iter(reader.items(item))), state) if kind is dict
                         else ("list", (item, 0), state))
        elif kind is str:
            if item and not reader.budget.scan(len(item)):
                raise Refused(WORK_LIMIT)
        elif kind is int:
            if not reader.budget.scan(max(1, (item.bit_length() + 3) // 4)):
                raise Refused(WORK_LIMIT)
        elif item is not None and kind is not float and kind is not bool:
            raise Refused(UNSUPPORTED)
    return False


def _dependents(result, reader):
    rows = reader.read(result, "unchecked_dependents")
    if type(rows) is not list or not rows:
        return None
    names, more, unknown = [], 0, False
    for row in reader.rows(rows):
        if type(row) is not dict:
            continue
        name, uses = reader.read(row, "name"), reader.read(row, "uses")
        if type(name) is str and type(uses) is str:
            names.append(_joined(["'", name[:64], "' (uses '", uses[:64], "')"], reader))
        extra = reader.read(row, "more", 0)
        if type(extra) is int:
            if not reader.budget.scan(max(1, (more.bit_length() + 3) // 4)):
                raise Refused(WORK_LIMIT)
            more += extra
        unknown = unknown or bool(reader.read(row, "unknown"))
    if unknown or not names:
        return "- Not checked: objects outside this program that may use an object it changed (not listed)."
    parts = ["- Not checked: the evaluated shape or placement of "]
    for name in names:
        reader.visit()
        if len(parts) > 1:
            parts.append(", ")
        parts.append(name)
    if more:
        parts.extend((" and ", decimal(more, reader), " more"))
    parts.append(", outside this program, which uses an object it changed.")
    return _joined(parts, reader)


def unchecked_receipt(tool, result, *, budget, currency=None, identifiers=None):
    from .handoff_currency import Currency, LIVE_FIELDS
    reader = Reader(budget)
    try:
        if type(tool) is not str:
            raise Refused(UNSUPPORTED)
        if tool and not budget.scan(len(tool)):
            raise Refused(WORK_LIMIT)
        if tool != "angelo_scene_program" or type(result) is not dict:
            return Projection()
        if currency is not None and type(currency) is not Currency:
            raise Refused(UNSUPPORTED)
        checks = reader.read(result, "checks")
        if (type(checks) is not list or checks or reader.read(result, "status") != "committed"
                or reader.read(result, "success") is not True or reader.read(result, "final") is not True
                or reader.read(result, "undoable") is not True or reader.read(result, "journal_durable") is False
                or _partial(result, reader)):
            return Projection()
        for key in ("error", "replayed", "partial_changes_possible", "cleanup_warning", "cleanup_pending"):
            if reader.read(result, key):
                return Projection()
        if uncertain(copied(result, reader, ("checks",)), reader):
            return Projection()
        lines = ["Committed (one Undo step): " + changes(result, reader)
                 + ". No host check covered it, so nothing was verified"]
        dependents = _dependents(result, reader)
        if dependents:
            lines.append(dependents[2:] if dependents.startswith("- ") else dependents)
        entry = {"unverified": _joined([lines[0], *([". ", lines[1]] if len(lines) > 1 else [])], reader)}
        views, _ = _receipt_rows(reader, reader.read(result, "views"), ("id", "view_id", "name"))
        if views:
            entry["views"] = views
        shared = _shared_edits(reader, result)
        if shared:
            entry["shared_edits"] = shared
        entry.update(commit_facts(reader, result, without=("unchecked_dependents",) if dependents else ()))
        live = reader.read(result, "live_revisions")
        live = live if type(live) is dict else {}
        rows, truncated = _receipt_rows(reader, reader.read(live, "objects"), LIVE_FIELDS)
        _live_rows(reader, entry, rows, currency)
        if truncated or reader.read(live, "truncated"):
            entry["live_truncated"] = True
        return compact(entry, budget=budget, identifiers=identifiers)
    except Refused as error:
        return Projection(reason=error.args[0])
