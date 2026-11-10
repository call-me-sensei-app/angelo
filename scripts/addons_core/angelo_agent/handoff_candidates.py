# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Complete neutral activity admission and chronological final presentation."""

from dataclasses import dataclass

from .handoff_bound import FinalWork, identifier_json_bound, neutral_graph
from .handoff_currency_view import plan_receipt, project_receipt
from .handoff_full import FullPlan, prepare_full, render_full
from .handoff_json import UNSUPPORTED, encode_json
from .handoff_model import model_result
from .handoff_projection import Projection, compact
from .handoff_receipts import Refused, compact_receipt, terse_receipt
from .handoff_unchecked import unchecked_receipt
from .legacy_names import current_tool_name


@dataclass(frozen=True)
class Activity:
    index: int
    tool: str
    call_id: str | None
    plan: object
    kind: str
    reverted: bool
    merged: bool
    final_work: FinalWork


def prepare_activity(index, message, certification, reader, *, compact_json):
    """No identifier table or unfinished currency map enters admission."""
    tool = reader.read(message, "tool")
    if type(tool) is not str:
        raise Refused(UNSUPPORTED)
    tool = current_tool_name(tool)
    result = reader.read(message, "result", {})
    reverted = bool(reader.read(message, "reverted"))
    clean = reader.read(message, "status") == "completed" and not reverted
    plan, kind, merged = None, None, False
    if compact_json and clean:
        receipt = (terse_receipt(tool, result, budget=reader.budget, accepted=certification.accepted)
                   if certification.certified else unchecked_receipt(tool, result, budget=reader.budget))
        if receipt.reason:
            raise Refused(receipt.reason)
        if receipt.value is not None:
            neutral = neutral_graph(receipt.value, budget=reader.budget, compact_json=compact_json)
            if neutral.reason:
                raise Refused(neutral.reason)
            plan, kind, merged = plan_receipt(neutral, budget=reader.budget), "currency_receipt", certification.certified
    if plan is None:
        receipt = compact_receipt(tool, result, budget=reader.budget) if clean and certification.certified else None
        if receipt is not None and receipt.reason:
            raise Refused(receipt.reason)
        if receipt is not None and receipt.value is not None:
            plan = neutral_graph({"role": "tool_result", "tool": tool, "receipt": receipt.value},
                                 budget=reader.budget, compact_json=compact_json)
            if plan.reason:
                raise Refused(plan.reason)
            kind = "plain_receipt"
        else:
            if compact_json:
                modeled = model_result(tool, result, budget=reader.budget)
                if modeled.reason:
                    raise Refused(modeled.reason)
                result = modeled.value
            plan = prepare_full(result, budget=reader.budget, compact_json=compact_json)
            if type(plan) is not FullPlan:
                raise Refused(plan.reason)
            kind = "full"
    if kind == "plain_receipt":
        if compact_json:
            bound = identifier_json_bound(plan.shape)
        else:
            # The complete neutral outer entry is already private/validated.
            # With no identifier projection, only untracked JSON remains:
            # one visit per value/key; strings and numeric decimal upper bounds.
            shape = plan.shape
            bound = FinalWork(shape.values + shape.keys,
                              shape.string_chars + shape.number_chars + 1, shape.json_chars)
    else:
        bound = plan.final_work
    if kind == "currency_receipt":
        # Receipt projection precedes the two added literal fields. Those
        # fields contain no identifier/revision strings and cannot allocate IDs.
        nodes = 4  # two scalar values and two dictionary keys in outer encoding
        characters = len("role") + len("tool_result") + len("tool") + len(tool)
    elif kind == "full":
        nodes = 9  # dictionary + four values + four keys
        characters = sum(map(len, ("role", "tool_result", "tool", tool, "result", "reverted"))) + plan.text_characters
    else:
        nodes = characters = 0
    bound = FinalWork(bound.nodes + nodes, bound.characters + characters + 1, bound.json_chars)
    call_id = reader.read(message, "call_id")
    return Activity(index, tool, call_id if type(call_id) is str else None,
                    plan, kind, reverted, merged, bound)


def render_activity(activity, *, budget, identifiers, currency, max_chars, history_complete=True):
    """All admitted activity projections run in chronological transcript order."""
    if activity.kind == "currency_receipt":
        projected = project_receipt(activity.plan, currency, budget=budget, history_complete=history_complete)
        if projected.reason:
            return projected
        projected = compact(projected.value, budget=budget, identifiers=identifiers)
        if projected.reason:
            return projected
        entry = {"role": "tool_result", "tool": activity.tool, **projected.value}
    elif activity.kind == "plain_receipt":
        entry = activity.plan.value
        if identifiers is not None:
            projected = compact(entry, budget=budget, identifiers=identifiers)
            if projected.reason:
                return projected
            entry = projected.value
    else:
        text = render_full(activity.plan, budget=budget, identifiers=identifiers,
                           currency=currency, history_complete=history_complete)
        if text.reason:
            return text
        entry = {"role": "tool_result", "tool": activity.tool, "result": text.value, "reverted": activity.reverted}
    encoded = encode_json(entry, budget=budget, compact=identifiers is not None, max_chars=max_chars)
    return Projection(value=encoded.text, reason=encoded.reason)
