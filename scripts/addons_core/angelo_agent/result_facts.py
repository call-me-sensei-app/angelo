# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The facts a tool result states beyond its checks and changed rows, and where the bounded forms keep them.

Every compact form of a result (``claude_outcomes.compact_receipt`` and ``terse_receipt``, the unchecked receipt and
``program_completion.continuation_receipt``) keeps ``commit_facts``; the 5,000-character excerpt
(``program_completion.history_result_text``) keeps the facts its cut would lose (``bounded_excerpt``). Route parity
registers these keys as result facts (``scripts/angelo/prompt_registry.json`` ``result_facts``).
"""
import json

# What a commit result states about its outcome beyond its checks and changed rows: every compact form keeps these
# (commit_facts) and the excerpt keeps them past its cut (bounded_excerpt). Objects that stopped sharing their data, the
# World and Sun lights after a commit that changed them, where what it edited that moves is at a few frames, objects
# outside it whose shape follows one it reshaped, checks no evaluation could measure, and effects a kernel could not
# measure. Left out: a moved frame (``frame_moved``) and the unkeyed animated values a frame update replaced or will
# (``unkeyed_*``) arrive as host sentences that tell the model what to do; facts reach every form, instructions do not
# (prompt_audit.rst, the open gaps).
COMMIT_FACTS = ("isolated_shared_data", "world", "motion", "unchecked_dependents", "unobservable_checks",
                "unverified_effects")
# Facts an operation's own result states, lifted into ``operation_facts`` (the receipts keep no operation_results).
OPERATION_FACTS = ("also_slots_only",)
FACT_ROWS = 32   # Rows a compact form keeps of a listed fact; the full count is stated beside them.
EXCERPT = 5000   # Characters of a result a later turn reads (program_completion.history_result_text).
EXCERPT_FACTS = 2500   # Most of the excerpt the facts it would cut may take back; past it they are named.


def commit_facts(result, without=()):
    """The result's facts (``COMMIT_FACTS``, ``OPERATION_FACTS``) as a compact entry keeps them: a list longer than
    ``FACT_ROWS`` keeps its first rows and states its length (``<key>_count``). ``without``: facts the entry already
    states in its own words (an unchecked receipt's dependents line)."""
    if not isinstance(result, dict):
        return {}
    facts = {}
    for key in COMMIT_FACTS:
        value = result.get(key)
        if key in without or value in (None, "", [], {}):
            continue
        if isinstance(value, list) and len(value) > FACT_ROWS:
            facts[key], facts[key + "_count"] = value[:FACT_ROWS], len(value)
        else:
            facts[key] = value
    facts.update(operation_facts(result))
    return facts


def operation_facts(result):
    """``OPERATION_FACTS`` stated inside ``operation_results``, each row with its operation's index, as
    ``operation_facts``; a ``note`` every row of a fact repeats is stated once, in ``operation_fact_notes``."""
    rows = result.get("operation_results") if isinstance(result, dict) else None
    lifted, notes = [], {}
    for index, row in enumerate(rows if isinstance(rows, list) else ()):
        if not isinstance(row, dict) or not any(key in row for key in OPERATION_FACTS):
            continue
        entry = {"operation": index}
        for key in OPERATION_FACTS:
            if key not in row:
                continue
            value = row[key]
            note = value.get("note") if isinstance(value, dict) else None
            if isinstance(note, str) and notes.setdefault(key, note) == note:
                value = {name: item for name, item in value.items() if name != "note"}
            entry[key] = value
        lifted.append(entry)
    if not lifted:
        return {}
    out = {"operation_facts": lifted[:FACT_ROWS]}
    if len(lifted) > FACT_ROWS:
        out["operation_facts_count"] = len(lifted)
    if notes:
        out["operation_fact_notes"] = notes
    return out


def bounded_excerpt(shown, encoded, separators=None, limit=EXCERPT):
    """``encoded`` (``json.dumps(shown, separators=separators)``) cut to ``limit`` characters, as a later turn reads a
    result, keeping its facts: when the cut would lose a fact (``commit_facts``' keys at the top level, or an
    operation's own fact inside ``operation_results``), the prefix gives back the end it needs and the facts follow it
    after ``…``, whole and in ``COMMIT_FACTS`` order, within ``EXCERPT_FACTS`` characters (any that does not fit is
    named in ``facts_omitted``). A result the cut keeps whole, or whose cut loses no fact, reads exactly as before; when
    the facts that would follow are all whole in the cut as before (the larger ones named only), that cut stays, less a
    short marker naming the facts it loses, or exactly as before when that marker would cut a fact it kept whole. It
    never shows less of a fact than the cut before."""
    if len(encoded) <= limit or not isinstance(shown, dict):
        return encoded[:limit]
    item, pair = separators or (", ", ": ")
    ends, position = {}, 1
    for key, value in shown.items():
        position += len(json.dumps(key, ensure_ascii=False)) + len(pair) + len(json.dumps(value, ensure_ascii=False,
                                                                                          separators=separators))
        ends[key] = position
        position += len(item)
    if position - len(item) + 1 != len(encoded):   # Not this encoding's layout: cut as before.
        return encoded[:limit]
    lifted = operation_facts(shown)
    candidates = [(key, shown[key]) for key in COMMIT_FACTS if shown.get(key) not in (None, "", [], {})]
    candidates += [(key, value) for key, value in lifted.items()]
    owner = {key: key for key, _ in candidates}
    owner.update({key: "operation_results" for key in lifted})
    # Settle: the cut only ever moves back and a fact lost once stays lost, so this ends; the tail is built for the
    # final cut, and every fact ending past it must be in the tail or named, else the cut stays as before.
    cut, lost, tail = limit, [], ""
    for _ in range(len(candidates) + 2):
        grown = [(key, value) for key, value in candidates if (key, value) not in lost and ends.get(owner[key], 0) > cut]
        if not grown and tail:
            break
        lost += grown
        if not lost:
            return encoded[:limit]
        kept, omitted, used = {}, [], 0
        for key, value in [(key, value) for key, value in candidates if (key, value) in lost]:
            size = len(json.dumps(value, ensure_ascii=False, separators=separators)) + len(key) + 4
            if used + size <= EXCERPT_FACTS:
                kept[key], used = value, used + size
            else:
                omitted.append(key)
        tail = "…" + json.dumps(dict(kept, **({"facts_omitted": omitted} if omitted else {})), ensure_ascii=False,
                                separators=separators)
        cut = min(cut, limit - len(tail))
    stated = set(kept) | set(omitted)
    # A fact the cut moved back for may have ended up named instead of stated: give the prefix back what the final tail
    # does not need, while every fact ending past the cut stays stated or named.
    wider = limit - len(tail)
    if wider > cut and all(ends.get(owner[key], 0) <= wider or key in stated for key, _ in candidates):
        cut = wider
    if any(ends.get(owner[key], 0) > cut and key not in stated for key, _ in candidates):
        return encoded[:limit]
    if any(ends.get(owner[key], 0) > limit for key in kept):
        return encoded[:cut] + tail
    # The tail would state no fact the cut as before lacks: that cut, naming the facts it loses (a short marker).
    names = [key for key, _ in candidates if ends.get(owner[key], 0) > limit]
    if not names:
        return encoded[:limit]
    marker = "…" + json.dumps({"facts_omitted": names}, ensure_ascii=False, separators=separators)
    if any(limit - len(marker) < ends.get(owner[key], 0) <= limit for key, _ in candidates):
        return encoded[:limit]   # The marker would cut a fact the cut before kept whole: keep that cut.
    return encoded[:limit - len(marker)] + marker
