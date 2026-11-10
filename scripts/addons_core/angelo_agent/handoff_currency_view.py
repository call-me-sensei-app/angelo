# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Final currency filtering on an owned neutral receipt, never raw history.

neutral_graph created every container and validated every exact string key.
The compose owner retains that private snapshot unchanged between admission
and final projection. This boundary, not a saved JSON flag, permits lookups.
"""

from dataclasses import dataclass, replace

from .handoff_bound import FinalWork, NeutralShape, identifier_json_bound
from .handoff_currency import CHANGED, Currency
from .handoff_json import UNSUPPORTED, WORK_LIMIT
from .handoff_projection import Projection
from .handoff_receipts import Refused
from .identifiers import IdentifierTable

_ROW_PROBES = ("handle", "object_revision", "object_revision_present")
_OUTPUT_SLOTS = ("live", CHANGED, "live_truncated")
INCOMPLETE_CURRENCY = "incomplete_currency"


@dataclass(frozen=True)
class ReceiptPlan:
    neutral: Projection
    final_work: FinalWork
    currency_nodes: int
    currency_characters: int


def _owned(neutral):
    if (type(neutral) is not Projection or neutral.reason is not None or type(neutral.shape) is not NeutralShape
            or type(neutral.value) is not dict):
        raise ValueError("A currency view requires the owner's complete neutral primitive snapshot")
    return neutral.value


def plan_receipt(neutral, *, budget):
    """Reserveable upper bound for currency view, identifier copy and JSON only.

    Its shape superset retains every live row and adds every potentially stale
    handle. Actual final filtering can only remove parts of that superset.
    No identifier allocation or actual current-revision decision occurs here.
    """
    root = _owned(neutral)
    if CHANGED in root:
        raise ValueError("Neutral receipt currency has not yet been projected")
    values = root.get("live", [])
    if type(values) is not list:
        raise Refused(UNSUPPORTED)
    characters, potential, potential_chars, potential_json = 0, 0, 0, 0
    for row in values:
        if not budget.visit():
            raise Refused(WORK_LIMIT)
        if type(row) is not dict:
            raise Refused(UNSUPPORTED)
        handle, revision = row.get("handle"), row.get("object_revision")
        if handle is not None and type(handle) is not str:
            raise Refused(UNSUPPORTED)
        if type(handle) is str:
            characters += len(handle)
        if type(revision) is str:
            characters += len(revision)
            potential += 1
            if handle is None:
                potential_json += len("null")
            else:
                potential_chars += len(handle)
                # Protocol upper bound for a string: quotes and at most six chars per code unit.
                potential_json += 2 + 6 * len(handle)
    nodes = 1 + len(root) + len(_OUTPUT_SLOTS) + len(values) * len(_ROW_PROBES)
    shape = neutral.shape
    if potential:
        # Normal spacing is the conservative bound even for a compact snapshot.
        key_json = len(CHANGED) + 2
        added_json = (2 if root else 0) + key_json + 2 + 2 + 2 * max(0, potential - 1) + potential_json
        shape = replace(shape, values=shape.values + 1 + potential, keys=shape.keys + 1,
                        string_chars=shape.string_chars + len(CHANGED) + potential_chars,
                        json_chars=shape.json_chars + added_json)
    primitive = identifier_json_bound(shape)
    bound = FinalWork(nodes + primitive.nodes, characters + primitive.characters, primitive.json_chars)
    return ReceiptPlan(neutral, bound, nodes, characters)


def project_receipt(plan, currency, *, budget, history_complete=True):
    """The same receipt currency decisions, with all final work still debited centrally."""
    if (type(plan) is not ReceiptPlan or type(currency) is not Currency or type(history_complete) is not bool
            or currency.table is not None and type(currency.table) is not IdentifierTable):
        return Projection(reason=UNSUPPORTED)
    root = _owned(plan.neutral)
    try:
        if not budget.visit():
            raise Refused(WORK_LIMIT)
        kept, changed = [], []
        for row in root.get("live", []):
            fields = {}
            for key in _ROW_PROBES:
                if not budget.visit():
                    raise Refused(WORK_LIMIT)
                fields[key] = "object_revision" in row if key == "object_revision_present" else row.get(key)
            handle, revision = fields["handle"], fields["object_revision"]
            for value in (handle, revision):
                if type(value) is str and value and not budget.scan(len(value)):
                    raise Refused(WORK_LIMIT)
            current = currency.current(handle) if type(revision) is str else None
            if not history_complete and type(revision) is str and current is None:
                raise Refused(INCOMPLETE_CURRENCY)
            if current is not None and type(current) is not str:
                raise Refused(UNSUPPORTED)
            if current is not None and current != revision:
                changed.append(handle)
            else:
                earlier = currency.earlier(handle) if fields["object_revision_present"] else None
                if earlier is not None and type(earlier) is not str:
                    raise Refused(UNSUPPORTED)
                if not fields["object_revision_present"] or revision != earlier:
                    kept.append(row)
        output = {}
        for key, value in root.items():
            if not budget.visit():
                raise Refused(WORK_LIMIT)
            if key not in _OUTPUT_SLOTS:
                output[key] = value
        for key, value in (("live", kept), (CHANGED, changed), ("live_truncated", root.get("live_truncated"))):
            if not budget.visit():
                raise Refused(WORK_LIMIT)
            if value:
                output[key] = value
        return Projection(value=output)
    except Refused as error:
        return Projection(reason=error.args[0])
