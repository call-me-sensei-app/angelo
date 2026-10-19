# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Value encoding, safe errors and per-read budgets for scene observations.

No Blender IDs, document epochs or persistent caches are owned here."""

import hashlib
import json
import math
import time


class SceneQueryError(ValueError):
    """Safe, bounded validation/preflight error; no scene mutation began."""
    partial_changes_possible = False


class BudgetExceeded(Exception):
    pass


class ReadBudget:
    """Per-pass work bounds. ``elements`` charges Python- or getter-per-element
    reads; ``packed`` charges bytes copied in one C call (attribute arrays and
    native buffers), which cost orders of magnitude less per element. The
    deadline bounds both. ``shared`` holds this pass's reads of datablocks that
    several objects share (scene_shared_reads); it lives and dies with the budget."""

    def __init__(self, elements, items, seconds, deadline=None, packed=0):
        self.elements = elements
        self.items = items
        self.packed = packed
        self.deadline = min(time.monotonic() + seconds, deadline) if deadline is not None else time.monotonic() + seconds
        self.shared = {}

    def take(self, elements=0, items=0, packed=0):
        if (elements > self.elements or items > self.items or packed > self.packed
                or time.monotonic() > self.deadline):
            raise BudgetExceeded("Original-state observation exceeded its bounded work budget")
        self.elements -= elements
        self.items -= items
        self.packed -= packed


class ReadOutput:
    def __init__(self, limit, item_limit):
        self.limit = limit
        self.remaining = item_limit
        self.omitted = []

    def listing(self, values, field):
        total = len(values)
        count = min(total, self.limit, self.remaining)
        self.remaining -= count
        if count < total:
            self.omitted.append({"field": field, "total": total, "returned": count})
        return values[:count]


# json.dumps with these options builds a new JSONEncoder on every call; field
# hashes make several calls per object per pass. One stateless encoder writes
# the same bytes (json.dumps itself is JSONEncoder(**options).encode(value)).
_ENCODER = json.JSONEncoder(sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def dump(value):
    return _ENCODER.encode(value)


def hash_value(value):
    # BLAKE2b-256 rather than SHA-256: field hashes run per object on every
    # whole-scene pass, and the bundled Python's SHA-256 measured roughly an
    # order of magnitude slower (see scene_intelligence.rst). Tokens are opaque.
    return hashlib.blake2b(dump(value).encode("utf-8"), digest_size=32).hexdigest()


def all_finite(values):
    """True when every value of a packed float32 array is finite.

    Equivalent to ``all(math.isfinite(v) for v in values)``, but the loop runs
    in C. Any NaN or infinity makes the float64 sum non-finite, while float32
    magnitudes (at most about 3.4e38) cannot overflow a float64 sum. Only use
    this for float32 data; a float64 array could overflow into a false failure.
    """
    return math.isfinite(sum(values))


def count_nonzero(values):
    """Number of nonzero entries in a packed flag array or list, counted in C."""
    return len(values) - values.count(0)


def label(value, limit=128):
    return str(value)[:limit]


def unreadable_revision(codes):
    """The refusal of a binding whose object has no exact revision (``codes``: its closed limitation codes).

    Nothing ran, and the cause is the object's data, not the call: UNSUPPORTED (LIMIT_EXCEEDED when a read
    budget ran out: any ``*_budget`` code, such as original_state_work_budget or curve_data_budget), so the model edits it another way (a script)
    and the request's other edits go on. Unclassified, it read as a failure that may have changed
    the scene and held them: a face one-shot (GPT 6.1 Sol) whose program set a sculpted Multires
    head's material ended there. ``revision_omission`` reads the codes back from the text.
    """
    error = SceneQueryError("Exact program revision unavailable: " + ", ".join(codes))
    error.refusal_code = "LIMIT_EXCEEDED" if any(code.endswith("_budget") for code in codes) else "UNSUPPORTED"
    return error


def revision_omission(error, *, closed_codes=None):
    """Why an exact ``object_revision`` failed closed, as a closed reason.

    ``object_revision`` lists its closed limitation codes after the colon of a
    SceneQueryError; callers that caught their store's own SceneQueryError pass
    ``closed_codes=True``. Any other reader error is named only by its type,
    never by its message. Shared by the turn context and post-commit read-backs.
    """
    if closed_codes is None:
        closed_codes = isinstance(error, SceneQueryError)
    if closed_codes:
        message = str(error)
        codes = [label(code.strip(), 64) for code in message.partition(":")[2].split(",") if code.strip()][:8]
        reason = "BUDGET" if "original_state_work_budget" in codes else "UNSUPPORTED"
        return {"reason": reason, "detail": ", ".join(codes) or label(message, 200)}
    return {"reason": "UNSUPPORTED", "detail": "revision_error:" + type(error).__name__}


def finite_number(value):
    value = float(value)
    if not math.isfinite(value):
        raise SceneQueryError("Non-finite scene values cannot provide a trustworthy observation")
    return value


def vector(value):
    return [finite_number(v) for v in value]


def session_uid(value):
    return int(value.session_uid)


def pointer(value):
    return int(value.as_pointer())
