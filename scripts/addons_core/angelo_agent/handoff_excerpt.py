# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded historical result text, preserving the existing fact-aware excerpt layout."""

from .handoff_json import OUTPUT_LIMIT, UNSUPPORTED, WORK_LIMIT, encode_json
from .handoff_projection import Projection, compact as primitive_copy
from .handoff_receipts import Reader, Refused, _operation_facts
from .handoff_program_copy import copied
from .model_results import WORKER_FINISHES
from .result_facts import COMMIT_FACTS, EXCERPT, EXCERPT_FACTS


def _joined(parts, reader):
    size = 0
    for part in parts:
        reader.visit()
        size += len(part)
    if size and not reader.budget.scan(size):
        raise Refused(WORK_LIMIT)
    return "".join(parts)


def _full_result(result, currency, reader):
    if currency is None or type(result) is not dict:
        return result
    from .handoff_currency import CHANGED
    output, changed, seen = copied(result, reader), [], set()
    for key in ("live_revisions", "after"):
        section = reader.read(result, key)
        objects = reader.read(section, "objects") if type(section) is dict else None
        if type(objects) is not list:
            continue
        kept, stale = [], False
        for row in reader.rows(objects):
            revision = reader.read(row, "object_revision") if type(row) is dict else None
            handle = reader.read(row, "handle") if type(row) is dict else None
            if handle is not None and type(handle) is not str:
                raise Refused(UNSUPPORTED)
            current = currency.current(handle) if type(revision) is str else None
            if current is not None and current != revision:
                stale = True
                if handle not in seen:
                    changed.append(handle)
                    seen.add(handle)
            else:
                kept.append(row)
        if stale:
            output[key] = dict(copied(section, reader), objects=kept)
    return dict({CHANGED: changed}, **output) if changed else output


def _partial(result, reader):
    if type(result) is not dict or not reader.present(result, "verification"):
        return False
    verification = reader.read(result, "verification")
    return (type(verification) is not dict or reader.read(verification, "status") != "complete"
            or bool(reader.read(verification, "unknowns")))


def _ordered_partial(result, reader):
    verification = reader.read(result, "verification")
    if type(verification) is not dict:
        return result
    unknowns = reader.read(verification, "unknowns")
    if type(unknowns) is list:
        ordered = []
        for row in reader.rows(unknowns):
            if type(row) is dict:
                first = {key: reader.read(row, key) for key in ("operation", "root", "reason", "affected")
                         if reader.present(row, key)}
                first.update(copied(row, reader))
                row = first
            ordered.append(row)
        verification = dict(copied(verification, reader), unknowns=ordered)
    verification = {"status": reader.read(verification, "status"),
                    "unknown_count": len(unknowns) if type(unknowns) is list else None,
                    **copied(verification, reader, ("status", "unknown_count"))}
    return {"verification": verification, **copied(result, reader, ("verification",))}


def fact_candidates(shown, reader):
    """Fact values before identifier projection; none of these fields is a currency row."""
    candidates = []
    if type(shown) is not dict:
        return candidates
    for key in COMMIT_FACTS:
        value = reader.read(shown, key)
        if value is not None and not ((type(value) is str or type(value) is list or type(value) is dict) and not value):
            candidates.append((key, value, key))
    for key, value in _operation_facts(reader, shown).items():
        reader.visit()
        candidates.append((key, value, "operation_results"))
    return candidates


def _excerpt(shown, encoded, ends, compact, reader, prepared=None):
    if len(encoded) <= EXCERPT or type(shown) is not dict:
        return _joined([encoded[:EXCERPT]], reader)
    candidates = fact_candidates(shown, reader) if prepared is None else prepared
    comma, pair = (",", ":") if compact else (", ", ": ")
    cache, keys, lost, cut, tail_size, kept, omitted = {}, {}, set(), EXCERPT, 0, [], []

    def encoded_json(value, max_chars=None):
        result = encode_json(value, budget=reader.budget, compact=compact, max_chars=max_chars)
        if result.reason:
            if result.reason == OUTPUT_LIMIT and max_chars is not None:
                return None
            raise Refused(result.reason)
        return result.text

    for _ in range(len(candidates) + 2):
        grown = []
        for key, _value, owner in candidates:
            reader.visit()
            if key not in lost and ends.get(owner, 0) > cut:
                grown.append(key)
        if not grown and tail_size:
            break
        lost.update(grown)
        if not lost:
            return _joined([encoded[:EXCERPT]], reader)
        kept, omitted, used = [], [], 0
        for key, value, _owner in candidates:
            reader.visit()
            if key not in lost:
                continue
            if key not in cache:
                cache[key] = encoded_json(value, EXCERPT_FACTS)
                keys[key] = encoded_json(key)
            text = cache[key]
            size = len(text) + len(key) + 4 if text is not None else EXCERPT_FACTS + 1
            if used + size <= EXCERPT_FACTS:
                kept.append(key)
                used += size
            else:
                omitted.append(key)
        # Settle only lengths. Cached tokens form the final tail once below.
        tail_size, member_count = 3, 0  # ellipsis and braces
        for key in kept:
            reader.visit()
            tail_size += (len(comma) if member_count else 0) + len(keys[key]) + len(pair) + len(cache[key])
            member_count += 1
        if omitted:
            if "facts_omitted" not in keys:
                keys["facts_omitted"] = encoded_json("facts_omitted")
            tail_size += (len(comma) if member_count else 0) + len(keys["facts_omitted"]) + len(pair) + 2
            for index, key in enumerate(omitted):
                reader.visit()
                tail_size += (len(comma) if index else 0) + len(keys[key])
        cut = min(cut, EXCERPT - tail_size)
    stated = set(kept) | set(omitted)
    wider = EXCERPT - tail_size
    beyond_cut, wider_safe, kept_beyond, names, marker_safe = False, True, False, [], True
    for key, _value, owner in candidates:
        reader.visit()
        end = ends.get(owner, 0)
        wider_safe = wider_safe and (end <= wider or key in stated)
        if end > EXCERPT:
            names.append(key)
    if wider > cut and wider_safe:
        cut = wider
    for key, _value, owner in candidates:
        reader.visit()
        end = ends.get(owner, 0)
        beyond_cut = beyond_cut or end > cut and key not in stated
        kept_beyond = kept_beyond or key in kept and end > EXCERPT
    if beyond_cut:
        return _joined([encoded[:EXCERPT]], reader)
    if kept_beyond:
        parts = [encoded[:cut], "…{"]
        for index, key in enumerate(kept):
            reader.visit()
            if index:
                parts.append(comma)
            parts.extend((keys[key], pair, cache[key]))
        if omitted:
            if kept:
                parts.append(comma)
            parts.extend((keys["facts_omitted"], pair, "["))
            for index, key in enumerate(omitted):
                reader.visit()
                if index:
                    parts.append(comma)
                parts.append(keys[key])
            parts.append("]")
        parts.append("}")
        return _joined(parts, reader)
    if not names:
        return _joined([encoded[:EXCERPT]], reader)
    marker = _joined(["…", encoded_json({"facts_omitted": names})], reader)
    for _key, _value, owner in candidates:
        reader.visit()
        if EXCERPT - len(marker) < ends.get(owner, 0) <= EXCERPT:
            marker_safe = False
    return _joined([encoded[:EXCERPT - len(marker)], marker] if marker_safe else [encoded[:EXCERPT]], reader)


def history_result_text(result, *, budget, identifiers=None, currency=None):
    """Exact old supported formatting when construction/encoding fits shared credit."""
    from .handoff_currency import Currency
    if currency is not None and type(currency) is not Currency:
        return Projection(reason=UNSUPPORTED)
    reader = Reader(budget)
    try:
        if type(result) is dict:
            result = copied(result, reader, (WORKER_FINISHES,))
        result = _full_result(result, currency, reader)
        partial = _partial(result, reader)
        shown = _ordered_partial(result, reader) if partial and type(result) is dict else result
        projected = primitive_copy(shown, budget=budget, identifiers=identifiers)
        if projected.reason:
            return projected
        shown = projected.value
        encoded = encode_json(shown, budget=budget, compact=identifiers is not None, track_members=True)
        if encoded.reason:
            return Projection(reason=encoded.reason)
        if partial and len(encoded.text) > EXCERPT and type(shown.get("verification")) is dict:
            verification = shown["verification"]
            shown["verification"] = {"status": reader.read(verification, "status"),
                "unknown_count": reader.read(verification, "unknown_count"), "excerpt": True,
                **copied(verification, reader, ("status", "unknown_count", "excerpt"))}
            encoded = encode_json(shown, budget=budget, compact=identifiers is not None, track_members=True)
            if encoded.reason:
                return Projection(reason=encoded.reason)
        return Projection(value=_excerpt(shown, encoded.text, encoded.member_ends, identifiers is not None, reader))
    except Refused as error:
        return Projection(reason=error.args[0])
    except (IndexError, RuntimeError):
        return Projection(reason=UNSUPPORTED)
