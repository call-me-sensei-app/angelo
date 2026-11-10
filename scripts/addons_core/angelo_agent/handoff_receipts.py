# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Budgeted historical receipt inspection; saved JSON supplies no completion authority.

The host-summary cursor must independently establish historical certification.
This owner only checks presentation predicates, with no partial positive result.
"""

from dataclasses import dataclass

from .handoff_json import UNSUPPORTED, WORK_LIMIT, admit_mapping


@dataclass(frozen=True)
class Checks:
    positive: bool = False
    reason: str | None = None


class Refused(Exception):
    pass


class Reader:
    """Exact builtin fields and positive-credit iteration on one shared budget."""

    def __init__(self, budget):
        self.budget = budget

    def visit(self):
        if not self.budget.visit():
            raise Refused(WORK_LIMIT)

    def read(self, mapping, key, default=None):
        self.visit()
        if type(key) is not str:
            raise Refused(UNSUPPORTED)
        if (reason := admit_mapping(mapping, self.budget)) is not None:
            raise Refused(reason)
        value = mapping.get(key, default)
        kind = type(value)
        if value is not None and not (kind is bool or kind is int or kind is float or kind is str
                                      or kind is list or kind is dict):
            raise Refused(UNSUPPORTED)
        if kind is str and value and not self.budget.scan(len(value)):
            raise Refused(WORK_LIMIT)
        if kind is int and not self.budget.scan(max(1, (value.bit_length() + 3) // 4)):
            raise Refused(WORK_LIMIT)
        return value

    def rows(self, values):
        if type(values) is not list:
            raise Refused(UNSUPPORTED)
        for index in range(len(values)):
            self.visit()
            value = values[index]
            kind = type(value)
            if value is not None and not (kind is bool or kind is int or kind is float or kind is str
                                          or kind is list or kind is dict):
                raise Refused(UNSUPPORTED)
            yield value

    def present(self, mapping, key):
        self.visit()
        if type(key) is not str:
            raise Refused(UNSUPPORTED)
        if (reason := admit_mapping(mapping, self.budget)) is not None:
            raise Refused(reason)
        return key in mapping

    def items(self, mapping):
        if type(mapping) is not dict:
            raise Refused(UNSUPPORTED)
        iterator = iter(mapping.items())
        for _ in range(len(mapping)):
            self.visit()
            key, value = next(iterator)
            if type(key) is not str:
                raise Refused(UNSUPPORTED)
            if key and not self.budget.scan(len(key)):
                raise Refused(WORK_LIMIT)
            yield key, value

    def positive_rows(self, rows, *, require_children=False):
        """Same pass predicate as the existing owner, without copying ancestor sets.

        ``require_children`` is the outer program-check rule: each check must
        contain nonempty positive predicate rows. Nested predicate rows need
        children only when their own ``checks`` field is present.
        """
        active, stack = set(), []

        def enter(values, required):
            if type(values) is not list or not values:
                return False
            if id(values) in active:
                return False
            active.add(id(values))
            stack.append(("list", values, 0, required))
            return True

        try:
            self.visit()
            if not enter(rows, require_children):
                return Checks()
            while stack:
                action, values, index, required = stack.pop()
                if action == "leave":
                    active.remove(id(values))
                    continue
                if index == len(values):
                    active.remove(id(values))
                    continue
                self.visit()
                row = values[index]
                if type(row) is not dict or id(row) in active:
                    return Checks()
                active.add(id(row))
                if self.read(row, "passed") is not True:
                    return Checks()
                status = self.read(row, "status")
                if type(status) is not str or status != "pass":
                    return Checks()
                stack.append(("list", values, index + 1, required))
                stack.append(("leave", row, 0, False))
                if required or self.present(row, "checks"):
                    if not enter(self.read(row, "checks"), False):
                        return Checks()
        except Refused as error:
            return Checks(reason=error.args[0])
        except (IndexError, RuntimeError):
            return Checks(reason=UNSUPPORTED)
        return Checks(positive=True)


def _receipt_rows(reader, values, fields):
    from .claude_outcomes import _MAX_RECEIPT_ROWS
    output, count = [], 0
    if type(values) is not list:
        return output, False
    for row in reader.rows(values):
        if type(row) is not dict:
            continue
        count += 1
        if count <= _MAX_RECEIPT_ROWS:
            output.append({key: reader.read(row, key) for key in fields if reader.present(row, key)})
    return output, count > _MAX_RECEIPT_ROWS


def _shared_edits(reader, result):
    values, output = reader.read(result, "shared_edits"), []
    if type(values) is not list:
        return output
    for row in reader.rows(values):
        if type(row) is not dict:
            continue
        material, users, count = (reader.read(row, key) for key in ("material", "users", "count"))
        if type(material) is not str or type(users) is not list or type(count) is not int:
            continue
        names = []
        for name in reader.rows(users):
            if type(name) is str:
                names.append(name)
                if len(names) == 8:  # Same compact receipt's existing user excerpt.
                    break
        entry = {"material": material, "users": names, "count": count}
        holders = reader.read(row, "holders")
        kept = [name for name in reader.rows(holders) if type(name) is str] if type(holders) is list else []
        if kept:
            entry["holders"] = kept
        output.append(entry)
        if len(output) == 4:  # Same compact receipt's existing shared-edit excerpt.
            break
    return output


def _operation_facts(reader, result):
    from .result_facts import FACT_ROWS, OPERATION_FACTS
    values, lifted, notes, count = reader.read(result, "operation_results"), [], {}, 0
    if type(values) is not list:
        return {}
    for index, row in enumerate(reader.rows(values)):
        if type(row) is not dict:
            continue
        entry = {"operation": index}
        for key in OPERATION_FACTS:
            if not reader.present(row, key):
                continue
            value = reader.read(row, key)
            note = reader.read(value, "note") if type(value) is dict else None
            if type(note) is str:
                if note and not reader.budget.scan(len(note)):
                    raise Refused(WORK_LIMIT)
                if notes.setdefault(key, note) == note:
                    value = {name: item for name, item in reader.items(value) if name != "note"}
            entry[key] = value
        if len(entry) == 1:
            continue
        count += 1
        if count <= FACT_ROWS:
            lifted.append(entry)
    if not count:
        return {}
    output = {"operation_facts": lifted}
    if count > FACT_ROWS:
        output["operation_facts_count"] = count
    if notes:
        output["operation_fact_notes"] = notes
    return output


def commit_facts(reader, result, without=()):
    """The same fact inventory/excerpts, charging even operation rows not shown."""
    from .result_facts import COMMIT_FACTS, FACT_ROWS
    output = {}
    for key in COMMIT_FACTS:
        if key in without:
            continue
        value = reader.read(result, key)
        if value is None or (type(value) is str or type(value) is list or type(value) is dict) and not value:
            continue
        if type(value) is list and len(value) > FACT_ROWS:
            rows = []
            for index in range(FACT_ROWS):
                reader.visit()
                rows.append(value[index])
            output[key], output[key + "_count"] = rows, len(value)
        else:
            output[key] = value
    output.update(_operation_facts(reader, result))
    return output


def compact_receipt(tool, result, *, budget):
    """Bounded counterpart of the existing historical compact receipt, not authority.

    A nonqualifying result returns no value without a refusal reason. A work or
    unsupported-data refusal requires omitting this candidate, never fallback
    to an unbounded serializer or a partial proof.
    """
    from .claude_outcomes import _PROGRAM_TOOLS, _RECEIPT_REVISION_FIELDS
    from .handoff_projection import Projection, compact
    reader = Reader(budget)
    try:
        if type(tool) is not str:
            raise Refused(UNSUPPORTED)
        if tool and not budget.scan(len(tool)):
            raise Refused(WORK_LIMIT)
        if tool not in _PROGRAM_TOOLS or type(result) is not dict:
            return Projection()
        if reader.present(result, "verification"):
            verification = reader.read(result, "verification")
            if (type(verification) is not dict or reader.read(verification, "status") != "complete"
                    or reader.read(verification, "unknowns")):
                return Projection()
        status = reader.read(result, "status")
        if (reader.read(result, "success") is not True or reader.read(result, "final") is not True
                or status not in ("committed", "preview") or reader.read(result, "journal_durable") is False):
            return Projection()
        for key in ("error", "replayed", "partial_changes_possible", "cleanup_warning", "cleanup_pending"):
            if reader.read(result, key):
                return Projection()
        checked = reader.positive_rows(reader.read(result, "checks"), require_children=True)
        if checked.reason:
            raise Refused(checked.reason)
        if not checked.positive:
            return Projection()
        receipt = {"status": status}
        preview = reader.read(result, "preview_id")
        if preview:
            receipt["preview_id"] = preview
        live = reader.read(result, "live_revisions")
        if type(live) is dict:
            objects, truncated = _receipt_rows(reader, reader.read(live, "objects"), _RECEIPT_REVISION_FIELDS)
            receipt["live_revisions"] = {"status": reader.read(live, "status"), "objects": objects}
            if truncated or reader.read(live, "truncated"):
                receipt["live_revisions"]["truncated"] = True
        changes = reader.read(result, "changes" if status == "committed" else "delta")
        if type(changes) is dict:
            summary = {}
            for key, fields in (("added", ("name", "type")), ("modified", ("name", "fields")),
                                ("removed", ("name", "type"))):
                rows, truncated = _receipt_rows(reader, reader.read(changes, key), fields)
                if rows:
                    summary[key] = rows
                if truncated:
                    summary["truncated"] = True
            if reader.read(changes, "truncated"):
                summary["truncated"] = True
            receipt["changes"] = summary
        views, _ = _receipt_rows(reader, reader.read(result, "views"), ("id", "view_id", "name"))
        if views:
            receipt["views"] = views
        shared = _shared_edits(reader, result)
        if shared:
            receipt["shared_edits"] = shared
        receipt.update(commit_facts(reader, result))
        return compact(receipt, budget=budget)
    except Refused as error:
        return Projection(reason=error.args[0])


def _live_rows(reader, entry, rows, currency):
    from .handoff_currency import CHANGED, LIVE_FIELDS
    kept, changed = [], []
    for row in reader.rows(rows):
        handle = reader.read(row, "handle")
        if handle is not None and type(handle) is not str:
            raise Refused(UNSUPPORTED)
        revision = reader.read(row, "object_revision")
        current = currency.current(handle) if currency is not None and type(revision) is str else None
        if current is not None and current != revision:
            changed.append(handle)
        elif (not reader.present(row, "object_revision") or currency is None
              or revision != currency.earlier(handle)):
            kept.append({key: reader.read(row, key) for key in LIVE_FIELDS if reader.present(row, key)})
    if kept:
        entry["live"] = kept
    if changed:
        entry[CHANGED] = changed


def terse_receipt(tool, result, *, budget, accepted=False, currency=None, identifiers=None):
    """The historical verified line and current identities, completely inspected or refused."""
    from .claude_outcomes import _MAX_CHECK_LINES
    from .handoff_currency import Currency
    from .handoff_labels import changes, display, labels, terse_values
    from .handoff_projection import Projection, compact
    receipt = compact_receipt(tool, result, budget=budget)
    if receipt.reason or receipt.value is None:
        return receipt
    if currency is not None and type(currency) is not Currency:
        return Projection(reason=UNSUPPORTED)
    reader = Reader(budget)
    try:
        names, targets, shown, count = labels(result, reader), set(), [], 0
        for check in reader.rows(reader.read(result, "checks")):
            for row in reader.rows(reader.read(check, "checks")):
                target = reader.read(row, "target")
                if target is not None and type(target) is not str:
                    raise Refused(UNSUPPORTED)
                name = names.get(target)
                if name and not budget.scan(len(name)):
                    raise Refused(WORK_LIMIT)
                targets.add(name)
                count += 1
                if len(shown) < _MAX_CHECK_LINES:
                    shown.append((row, name))
        lines = []
        for row, target in shown:
            reader.visit()
            kind = reader.read(row, "kind")
            values = terse_values(kind, reader.read(row, "observed"), reader)
            lines.append(display(reader.read(row, "kind", "check"), reader, 32)
                         + (" '" + display(target, reader) + "'" if target and len(targets) > 1 else "")
                         + (" " + ", ".join(values) if values else ""))
        if count > _MAX_CHECK_LINES:
            lines.append("and %d more" % (count - _MAX_CHECK_LINES))
        if receipt.value["status"] == "preview":
            head = "PREVIEW staged; live scene unchanged until Accept; staged " + changes(result, reader)
            where = "on the staged copy"
        else:
            head = (("Accepted preview, committed" if accepted or tool == "angelo_accept_preview" else "Committed")
                    + " (one Undo step): " + changes(result, reader))
            where = "live"
        entry = {"verified": head + ". Checks passed %s (%d/%d): " % (where, count, count) + "; ".join(lines)}
        for key in ("preview_id", "views"):
            if key in receipt.value:  # This complete primitive copy has admitted string keys.
                entry[key] = receipt.value[key]
        entry.update(commit_facts(reader, result))
        live = receipt.value.get("live_revisions") or {}
        _live_rows(reader, entry, live.get("objects", []), currency)
        if live.get("truncated"):
            entry["live_truncated"] = True
        return compact(entry, budget=budget, identifiers=identifiers)
    except Refused as error:
        return Projection(reason=error.args[0])
