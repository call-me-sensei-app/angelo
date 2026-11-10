# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Lazy, shared-budget handoff composition; entries carry history, never mutation authority.

Candidates encode once. Selection reads only their bounded metadata. Its
control passes are reserved before candidate construction, so an exhausted raw
inspection can still publish an honest omission record and complete JSON.

Admission reserves final work, then projects chronologically before selecting
recent output. Routing supplies the header counted by the same output owner.
"""

from dataclasses import dataclass

from . import document_offer
from .claude_outcomes import _PROGRAM_TOOLS
from .handoff_currency import Currency
from .handoff_candidates import Activity, prepare_activity, render_activity
from .handoff_history import History, HostSummaries
from .handoff_json import HandoffBudget, HandoffOutput, WORK_LIMIT, encode_json
from .handoff_receipts import Reader, Refused
from .handoff_text import fit_text_entry
from .identifiers import IdentifierTable
from .legacy_names import current_tool_name


@dataclass
class Candidate:
    index: int
    encoded: str
    call_id: str | None = None
    activity: bool = False
    summarized: bool = False
    merged: bool = False
    truncated: bool = False


# At most one pass per admitted record for each named control operation.
# Summary groups are disjoint; even rollback/fallback visits no activity twice
# within a pass. These are work components, never transcript/output limits.
_CONTROL_PASSES = ("chronological projection", "index", "selection", "member lookup",
                   "summarized predicate", "merged predicate", "member lengths", "group inclusion",
                   "rollback", "retirement predicate", "fallback", "omission count",
                   "truncation count", "render", "included call IDs", "group closure")


def _person_only(message, reader):
    # HostSummaries has admitted this exact record's keys; no callback runs between.
    recipe = message.get("i18n")
    if (type(recipe) is list or type(recipe) is tuple) and recipe:
        reader.visit()
        first = recipe[0]
        if type(first) is str:
            if first and not reader.budget.scan(len(first)):
                raise Refused(WORK_LIMIT)
            return first in (document_offer.OFFER, document_offer.CHANGED, document_offer.STALE)
    return False


def _candidate(index, message, certification, reader, compact, max_chars, marker_chars):
    if (reader.read(message, "notice") is True or _person_only(message, reader)
            or reader.read(message, "withdrawn") is True):
        return None, False
    role = reader.read(message, "role")
    activity = role == "activity" and reader.read(message, "status") in ("completed", "failed")
    if role not in ("user", "assistant", "system", "error") and not activity:
        return None, False
    if role == "assistant":
        value = message.get("text")
        if value is not None and type(value) is not str:
            from .handoff_json import UNSUPPORTED
            raise Refused(UNSUPPORTED)
        if not value:
            return None, False
    if not activity:
        raw = message.get("model_text")
        raw = raw if type(raw) is str else message.get("text", "")
        raw = "" if raw is None else raw
        entry = {"role": role, "text": ""}
        if role == "user" and reader.read(message, "request_kind") == "native_preview_accept":
            entry["request_kind"] = "human UI preview acceptance; no model request"
        elif role == "user" and reader.read(message, "steer") is True:
            entry["note"] = "sent by the person while the previous request ran; read during it"
        result = fit_text_entry(entry, raw, budget=reader.budget, compact=compact, max_chars=max_chars,
                                truncated_max_chars=max_chars - marker_chars - 1)
        if result.reason:
            raise Refused(result.reason)
        return Candidate(index, result.text, truncated=result.truncated), result.work_exhausted
    return prepare_activity(index, message, certification, reader, compact_json=compact), False


def _marker(omitted, truncated, unexamined, exhausted, compact):
    # Trusted counters bounded by len(the exact transcript list); formatting
    # credit for their maximum digit lengths was prepaid by compose.
    comma, pair = (",", ":") if compact else (", ", ": ")
    values = [('"role"', '"handoff_omission"'), ('"omitted_candidates"', str(omitted)),
              ('"truncated_text_candidates"', str(truncated)), ('"unexamined_transcript_records"', str(unexamined)),
              ('"work_exhausted"', "true" if exhausted else "false")]
    return "{" + comma.join(key + pair + value for key, value in values) + "}"


def compose(messages, start, limit, *, header, identifiers=None):
    """The routing facade's text/actually-included IDs; all final characters count."""
    if identifiers is not None and type(identifiers) is not IdentifierTable:
        raise ValueError("A compact handoff requires its actual identifier table")
    budget = HandoffBudget(limit)
    history = History(messages, start, budget)
    if not history.unexamined:
        return "", frozenset()
    if type(header) is not str:
        raise ValueError("A handoff header is text")
    compact = identifiers is not None
    maximum = history.unexamined
    reserved = encode_json({"role": "handoff_omission", "omitted_candidates": maximum,
        "truncated_text_candidates": maximum, "unexamined_transcript_records": maximum, "work_exhausted": False},
        budget=budget, compact=compact)
    if (reserved.reason or len(header) + len(reserved.text) > limit
            or header and not budget.scan(len(header))):
        raise ValueError("Handoff budget cannot hold its header and a complete omission record")
    output, reader, adjacency = HandoffOutput(budget), Reader(budget), HostSummaries()
    currency = Currency(identifiers) if compact else None
    candidates, groups, failed, unexamined, exhausted = [], {}, set(), 0, False
    ineligible, required_summary_rows = set(), set()
    admitted_records = 0
    while (taken := history.take()) is not None:
        index, message = taken
        if message is None:
            unexamined = history.unexamined + 1
            break
        if not budget.reserve_final(len(_CONTROL_PASSES), 1):
            unexamined, exhausted = history.unexamined + 1, True
            break
        admitted_records += 1
        certification = adjacency.observe(index, message, budget)
        if certification.reason:
            unexamined, exhausted = history.unexamined + 1, budget.exhausted
            break
        if certification.closed is not None:
            groups[certification.closed.summary] = certification.closed
        try:
            if certification.certified and message["role"] == "activity":
                tool = reader.read(message, "tool")
                if current_tool_name(tool) in _PROGRAM_TOOLS:
                    required_summary_rows.add(index)
            candidate, cut = _candidate(index, message, certification, reader, compact,
                                         limit - len(header), len(reserved.text))
            if type(candidate) is Activity and not budget.reserve_final(
                    candidate.final_work.nodes, candidate.final_work.characters):
                unexamined, exhausted = history.unexamined + 1, True
                break
            if (currency is not None and message["role"] == "activity"
                    and currency.observe_latest(message, budget) is not None):
                unexamined, exhausted = history.unexamined + 1, True
                break
            if candidate is not None:
                candidates.append(candidate)
            elif message["role"] == "activity":
                ineligible.add(index)
            if cut:
                unexamined, exhausted = history.unexamined, True
                break
        except Refused as error:
            failed.add(index)
            # No earlier candidate may be projected past an uninspected later
            # currency record, even when its refusal was an unsupported type.
            unexamined, exhausted = history.unexamined, error.args[0] == WORK_LIMIT or budget.exhausted
            break
    if history.reason is not None:
        unexamined, exhausted = max(unexamined, history.unexamined), budget.exhausted
    last = adjacency.end(reached_origin=history.start == 0 and history.cursor < 0)
    if last is not None:
        groups[last.summary] = last
    budget.begin_final_projection()
    for _record in range(admitted_records):
        for _pass in _CONTROL_PASSES:
            reader.visit()
    finalized = []
    history_complete = history.cursor < history.start and not (history.reason or failed or unexamined or exhausted)
    # Admission is newest first; table IDs and revision prefixes are allocated
    # only now, oldest first, against the complete admitted currency map.
    for item in reversed(candidates):
        if type(item) is Activity:
            projected = render_activity(item, budget=budget, identifiers=identifiers,
                                        currency=currency, max_chars=limit - len(header),
                                        history_complete=history_complete)
            if projected.reason:
                if projected.reason == WORK_LIMIT:
                    raise ValueError("An admitted handoff final-work bound was insufficient")
                failed.add(item.index)
                continue
            item = Candidate(item.index, projected.value, item.call_id, True,
                             item.tool in _PROGRAM_TOOLS, item.merged)
        finalized.append(item)
    candidates = list(reversed(finalized))
    by_index = {item.index: item for item in candidates}
    selected, chosen, retired, payload = [], set(), set(), 0
    reporting = bool(failed or unexamined or exhausted)
    capacity = limit - len(header) - (len(reserved.text) + 1 if reporting else 0)

    def fits(size, count=1):
        return payload + size + max(0, len(selected) + count - 1) <= capacity

    def include(item):
        nonlocal payload, reporting, capacity
        if item.truncated:
            reporting = True
            capacity = limit - len(header) - len(reserved.text) - 1
        selected.append(item)
        chosen.add(item.index)
        payload += len(item.encoded)

    def eligible_rows(group):
        return (index for index in group.rows if index not in ineligible or index in required_summary_rows)

    for item in candidates:
        if item.index in chosen:
            continue
        group = groups.get(item.index) if compact else None
        members = [by_index.get(index) for index in eligible_rows(group)] if group is not None else []
        redundant = bool(members) and any(member is not None and member.summarized for member in members)
        redundant = redundant and all(member is not None and (not member.summarized or member.merged) for member in members)
        if redundant and fits(sum(len(member.encoded) for member in members), len(members)):
            retired.add(item.index)
            for member in reversed(members):
                include(member)
            continue
        if item.truncated:
            capacity = limit - len(header) - len(reserved.text) - 1
            reporting = True
        if fits(len(item.encoded)):
            include(item)
        else:
            reporting = True
            capacity = limit - len(header) - len(reserved.text) - 1
    # Reserve reporting space after a late output omission. Keep summary groups
    # atomic: if their activities cannot all remain, restore their newer summary.
    while selected and payload + max(0, len(selected) - 1) > capacity:
        removed = selected.pop()
        chosen.remove(removed.index)
        payload -= len(removed.encoded)
    if retired and any(not all(index in chosen for index in eligible_rows(groups[summary])) for summary in retired):
        # Conservative final fallback: no summary is retired after an output rollback.
        # One additional bounded metadata pass, prepaid with each candidate.
        selected, chosen, payload, retired = [], set(), 0, set()
        for item in candidates:
            if fits(len(item.encoded)):
                include(item)
    omitted = len(failed) + sum(item.index not in chosen and item.index not in retired for item in candidates)
    truncated = sum(item.truncated for item in selected)
    reporting = bool(omitted or truncated or unexamined or exhausted)
    if not selected and not reporting:
        return "", frozenset()
    if not output.append(header):
        raise ValueError("Handoff header exceeds its output budget")
    if reporting:
        if not output.append(_marker(omitted, truncated, unexamined, exhausted, compact)):
            raise ValueError("Handoff omission record exceeds its reserved output budget")
    for item in reversed(selected):
        if reporting or output.used > len(header):
            if not output.append("\n"):
                raise ValueError("Handoff separator exceeds its output budget")
        if not output.append(item.encoded):
            raise ValueError("Handoff entry exceeds its output budget")
    return output.render(), frozenset(item.call_id for item in selected if item.call_id is not None)
