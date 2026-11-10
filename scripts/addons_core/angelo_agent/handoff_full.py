# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Admission snapshots and final work bounds for a full historical result.

Only complete owned primitive graphs cross the phase boundary. Currency and
identifier projection happen later, chronologically, against the final map.
Only the final projected graph supplies fact excerpts; the table is not retouched.
"""

from dataclasses import dataclass, replace

from .handoff_bound import FinalWork, identifier_json_bound, neutral_graph
from .handoff_currency import CHANGED, Currency
from .handoff_currency_view import INCOMPLETE_CURRENCY
from .handoff_excerpt import _excerpt, _ordered_partial, _partial
from .handoff_json import UNSUPPORTED, WORK_LIMIT, encode_json
from .handoff_program_copy import copied
from .handoff_projection import Projection, compact
from .handoff_receipts import Reader, Refused
from .identifiers import IdentifierTable
from .model_results import WORKER_FINISHES
from .result_facts import COMMIT_FACTS, EXCERPT, FACT_ROWS, OPERATION_FACTS


@dataclass(frozen=True)
class FullPlan:
    neutral: Projection
    facts: tuple
    partial: bool
    final_work: FinalWork
    text_characters: int


def _visit(budget):
    if not budget.visit():
        raise Refused(WORK_LIMIT)


def _scan(budget, text):
    if text and not budget.scan(len(text)):
        raise Refused(WORK_LIMIT)


def _currency_shape(root, partial, budget):
    """A superset of every final currency/partial-marker outcome.

    Keep all original rows and add every possibly stale handle. The actual
    view only removes rows. Existing changed_since wins the old dict merge.
    Every copied slot/inspected row is charged before constructing the bound.
    """
    if type(root) is not dict:
        return root, 0, 0
    output, potential, seen = {}, [], set()
    nodes, characters = 0, 0
    for key, value in root.items():
        _visit(budget)
        output[key] = value
        nodes += 1
    # Final root/section lookups are explicit visits, separate from slot copies.
    nodes += 2 * 2
    for name in ("live_revisions", "after"):
        _visit(budget)
        section = root.get(name)
        rows = section.get("objects") if type(section) is dict else None
        if type(rows) is not list:
            continue
        nodes += len(section)
        for row in rows:
            _visit(budget)
            nodes += 3  # one row visit, handle and revision probes
            if type(row) is not dict:
                continue
            handle, revision = row.get("handle"), row.get("object_revision")
            if type(handle) is str and type(revision) is str:
                _scan(budget, handle)
                _scan(budget, revision)
                characters += len(handle) + len(revision)
                if handle not in seen:
                    seen.add(handle)
                    potential.append(handle)
    if potential and CHANGED not in output:
        output = {CHANGED: potential, **output}
    # Final verification ordering copies its fields even without a long cut.
    verification = root.get("verification")
    if partial and type(verification) is dict:
        nodes += len(root) + 2 * len(verification) + 3
    return output, nodes, characters


def _fact_superset(root, reader, compact_json):
    """Fund discovery on the *projected* graph, without touching its table twice.

    All possible lifted operation rows retain their note here. Actual final
    note deduplication can only remove fields, regardless of short-ID collisions
    or table eviction. The first note is also retained separately, as before.
    """
    if type(root) is not dict:
        return (), 0, 0
    facts, inspected = [], {}
    for key in COMMIT_FACTS:
        value = reader.read(root, key)
        if value is not None and not ((type(value) is str or type(value) is list or type(value) is dict) and not value):
            facts.append((key, value, key))
            inspected[key] = value
    operations = reader.read(root, "operation_results")
    lifted, notes, sources, count = [], {}, [], 0
    if type(operations) is list:
        for index, row in enumerate(reader.rows(operations)):
            if type(row) is not dict:
                sources.append(None)  # The final rows cursor still visits it.
                continue
            row_keys = []
            for key, _value in reader.items(row):
                row_keys.append(key)
            entry, values = {"operation": index}, {}
            for key in OPERATION_FACTS:
                if not reader.present(row, key):
                    continue
                value = reader.read(row, key)
                values[key] = entry[key] = value
                note = reader.read(value, "note") if type(value) is dict else None
                if type(note) is str:
                    notes.setdefault(key, note)
            sources.append({"keys": row_keys, "values": values})
            if len(entry) > 1:
                count += 1
                if count <= FACT_ROWS:
                    lifted.append(entry)
    inspected["operation_sources"] = sources
    if type(operations) is str or type(operations) is int:
        inspected["non_list_operations"] = operations  # Reader.read scans it before the list predicate.
    if count:
        facts.append(("operation_facts", lifted, "operation_results"))
        if count > FACT_ROWS:
            facts.append(("operation_facts_count", count, "operation_results"))
        if notes:
            facts.append(("operation_fact_notes", notes, "operation_results"))
    envelope = neutral_graph(inspected, budget=reader.budget, compact_json=compact_json)
    if envelope.reason:
        raise Refused(envelope.reason)
    key_characters = 0
    for key, _value in reader.items(root):
        key_characters += len(key)
    lookups = len(COMMIT_FACTS) + 1
    # Each root read visits itself and all admitted keys. For operation rows:
    # present+read per field, note read, and optional note-removal items walk.
    # 2P+3 covers those visits/key admissions and two note scans, even when
    # every selected value is a dictionary. Unselected children are not read.
    passes = 2 * len(OPERATION_FACTS) + 3
    nodes = lookups * (1 + len(root)) + passes * (envelope.shape.values + envelope.shape.keys) + 3
    characters = lookups * key_characters + passes * (envelope.shape.string_chars + envelope.shape.number_chars)
    return facts, nodes, characters


def prepare_full(result, *, budget, compact_json):
    """All raw/model copying and fact discovery occur in the admission phase."""
    reader = Reader(budget)
    try:
        if type(result) is dict:
            result = copied(result, reader, (WORKER_FINISHES,))
        partial = _partial(result, reader)
        shown = _ordered_partial(result, reader) if partial and type(result) is dict else result
        neutral = neutral_graph(shown, budget=budget, compact_json=compact_json)
        if neutral.reason:
            raise Refused(neutral.reason)
        supersets, fact_nodes, fact_characters = _fact_superset(neutral.value, reader, compact_json)
        facts = []
        for key, value, owner in supersets:
            reader.visit()
            projected = neutral_graph(value, budget=budget, compact_json=compact_json)
            if projected.reason:
                raise Refused(projected.reason)
            facts.append((key, projected, owner))
        superset, currency_nodes, currency_chars = _currency_shape(neutral.value, partial, budget)
        maximum = neutral_graph(superset, budget=budget, compact_json=compact_json)
        if maximum.reason:
            raise Refused(maximum.reason)
        shape = maximum.shape
        if partial and type(neutral.value) is dict and type(neutral.value.get("verification")) is dict:
            # Retain the original excerpt value in the first-encoding bound;
            # independently add the complete replacement member for the second.
            shape = replace(shape, values=shape.values + 1, keys=shape.keys + 1,
                            string_chars=shape.string_chars + len("excerpt"),
                            json_chars=shape.json_chars + len('"excerpt"') + 2 + 2 + len("true"))
        primitive = identifier_json_bound(shape)
        # Partial results can encode again after inserting excerpt=True.
        # Copy is not repeated; reserving its complete component is conservative.
        passes = 2 if partial else 1
        nodes = currency_nodes + fact_nodes + passes * primitive.nodes
        characters = currency_chars + fact_characters + passes * primitive.characters
        count, key_chars, key_json = len(facts), 0, 0
        for key, projected, _owner in facts:
            reader.visit()
            # Final fact values come from the already-projected graph. This
            # complete component conservatively includes an unused copy, but
            # render_full never re-enters the actual IdentifierTable for facts.
            bound = identifier_json_bound(projected.shape)
            nodes += bound.nodes + 1  # key encoding is a scalar visit
            characters += bound.characters + len(key)
            key_chars += len(key)
            key_json += len(key) + 2  # inventory keys are plain ASCII
        # At most count+2 settling passes: growth, selection, and one length
        # walk each. Then three decision walks and the single assembly walk.
        nodes += 3 * count * (count + 2) + 8 * count + 10
        marker_size = 1 + 2 + len('"facts_omitted"') + 2 + 2 + key_json + 2 * max(0, count - 1)
        text_size = min(EXCERPT, shape.json_chars)
        # Cached facts_omitted key; the optional standalone marker dictionary;
        # one marker join and one final excerpt join. Final visit needs credit.
        nodes += 1 + count + 3
        characters += len("facts_omitted") * 2 + key_chars + marker_size + text_size + 1
        return FullPlan(neutral, tuple(facts), partial,
                        FinalWork(nodes, characters, shape.json_chars), text_size)
    except Refused as error:
        return Projection(reason=error.args[0])


def _currency_view(root, currency, budget, history_complete):
    if type(root) is not dict or currency is None:
        return root
    output, changed, seen = {}, [], set()
    for key, value in root.items():
        _visit(budget)
        output[key] = value
    for name in ("live_revisions", "after"):
        _visit(budget)
        section = root.get(name)
        _visit(budget)
        rows = section.get("objects") if type(section) is dict else None
        if type(rows) is not list:
            continue
        kept, stale = [], False
        for row in rows:
            _visit(budget)
            if type(row) is not dict:
                kept.append(row)
                continue
            _visit(budget)
            handle = row.get("handle")
            _visit(budget)
            revision = row.get("object_revision")
            if type(handle) is str and type(revision) is str:
                _scan(budget, handle)
                _scan(budget, revision)
                current = currency.current(handle)
            else:
                current = None
            if not history_complete and type(revision) is str and current is None:
                raise Refused(INCOMPLETE_CURRENCY)
            if current is not None and current != revision:
                stale = True
                if handle not in seen:
                    seen.add(handle)
                    changed.append(handle)
            else:
                kept.append(row)
        if stale:
            replacement = {}
            for key, value in section.items():
                _visit(budget)
                replacement[key] = kept if key == "objects" else value
            output[name] = replacement
    return {CHANGED: changed, **output} if changed else output


def render_full(plan, *, budget, identifiers=None, currency=None, history_complete=True):
    """Final phase only: no raw result access and no early identifier allocation."""
    if (type(plan) is not FullPlan or type(history_complete) is not bool
            or currency is not None and type(currency) is not Currency
            or currency is not None and currency.table is not None and type(currency.table) is not IdentifierTable):
        return Projection(reason=UNSUPPORTED)
    try:
        shown = _currency_view(plan.neutral.value, currency, budget, history_complete)
        if plan.partial and type(shown) is dict and type(shown.get("verification")) is dict:
            ordered = {"verification": shown["verification"]}
            for key, value in shown.items():
                _visit(budget)
                if key != "verification":
                    ordered[key] = value
            shown = ordered
        projected = compact(shown, budget=budget, identifiers=identifiers)
        if projected.reason:
            return projected
        shown = projected.value
        encoded = encode_json(shown, budget=budget, compact=identifiers is not None, track_members=True)
        if encoded.reason:
            return Projection(reason=encoded.reason)
        if plan.partial and len(encoded.text) > EXCERPT and type(shown.get("verification")) is dict:
            verification, patched = shown["verification"], {}
            for key in ("status", "unknown_count", "excerpt"):
                _visit(budget)
                patched[key] = True if key == "excerpt" else verification.get(key)
            for key, value in verification.items():
                _visit(budget)
                if key not in patched:
                    patched[key] = value
            shown["verification"] = patched
            encoded = encode_json(shown, budget=budget, compact=identifiers is not None, track_members=True)
            if encoded.reason:
                return Projection(reason=encoded.reason)
        return Projection(value=_excerpt(shown, encoded.text, encoded.member_ends,
                                        identifiers is not None, Reader(budget)))
    except Refused as error:
        return Projection(reason=error.args[0])
