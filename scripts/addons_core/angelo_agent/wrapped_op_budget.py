# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The one in-turn time budget every typed wrapped operation shares, and the job past it (doc/angelo/wrapped_ops.rst).

A wrapped operation runs inside the agent's turn. All of them share one budget, ``TURN_BUDGET_S``, which is
``job_start_wait.MAX_HOLD``: how long a turn already waits on a job before handing it back. So a wrapped operation
never holds a turn longer than a job may.

Each declaration names ``input_bound``, the measure of its input that its time grows with (``{"triangles": N}``).
``bound_from_timings`` sets it from timed runs. The bound is the largest measured size whose nearest-rank p95 (stage
plus run) fits the budget on Mac 1 and on the Windows VM. A size above a failing one never counts, and nothing is
extrapolated. A constant-cost operation declares ``input_bound=None``; its measured p95 must still fit the budget.

``admit`` refuses an input over the bound before anything is staged. The refusal is the correctable
``LIMIT_EXCEEDED`` with ``limit: "turn_budget"``. It carries the budget, the bound and the measured input. It also
names ``job_kind``, the Background Job that does the same work (``angelo_job_start``), or, without one, the ``levers``:
the parameters that shrink the input.

``job_if = (parameter, {values})`` names values of one enum parameter whose time does not follow the input's size,
so no bound separates the calls that fit (a slow algorithm, not a large input). ``admit`` sends those calls to
``job_kind`` before reading the bound, an omitted parameter counting as its default. Other calls keep the bound.
"""

import math

from .job_start_wait import MAX_HOLD
from .refusal_codes import RefusalError

TURN_BUDGET_S = MAX_HOLD
PLATFORMS = ("mac1", "windows")   # Mac 1 and the Windows VM: both must fit (the latency-first rule).
MIN_TRIALS = 20                   # Timed trials per size, after one untimed warm-up; p95 of fewer is the maximum.


def p95(samples):
    """Nearest-rank 95th percentile."""
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def bound_from_timings(timings, budget_s=TURN_BUDGET_S):
    """``timings``: ``{platform: {size: [seconds, ...]}}`` (stage plus run per trial). Returns ``{"bound", "first_over",
    "budget_s", "p95": {size: {platform: seconds}}}``. ``bound`` is None when no measured size fits."""
    missing = [name for name in PLATFORMS if name not in timings]
    if missing:
        raise ValueError("timings need %s" % ", ".join(missing))
    sizes = {name: set(by_size) for name, by_size in timings.items()}
    if len({frozenset(found) for found in sizes.values()}) != 1:
        raise ValueError("every size must be measured on both machines")
    table = {}
    for name, by_size in timings.items():
        for size, samples in by_size.items():
            if len(samples) < MIN_TRIALS:
                raise ValueError("%s at %s: %d trials, %d needed" % (name, size, len(samples), MIN_TRIALS))
            if any(not math.isfinite(s) or s < 0 for s in samples):
                raise ValueError("%s at %s: a trial is not a time" % (name, size))
            table.setdefault(size, {})[name] = p95(samples)
    bound = first_over = None
    for size in sorted(table):
        if max(table[size].values()) > budget_s:
            first_over = size
            break
        bound = size
    return {"bound": bound, "first_over": first_over, "budget_s": budget_s, "p95": table}


def check_declaration(declaration, job_kinds):
    """Refuse a declaration that breaks the shared budget's contract (registration calls this)."""
    if "budget_s" in declaration:
        raise ValueError("%s: the budget is shared (wrapped_op_budget.TURN_BUDGET_S), never declared"
                         % declaration.get("kind"))
    bound = declaration.get("input_bound")
    if bound is not None:
        if not isinstance(bound, dict) or not bound:
            raise ValueError("input_bound is a non-empty {measure: count} or None")
        for measure, count in bound.items():
            if not isinstance(measure, str) or not measure or type(count) is not int or count < 1:
                raise ValueError("input_bound %r: a measure name and a positive whole count" % (measure,))
    job_kind = declaration.get("job_kind")
    if job_kind is not None and job_kind not in job_kinds:
        raise ValueError("job_kind %s is not a Background Job kind" % job_kind)
    properties = declaration.get("parameters", {}).get("properties", {})
    unknown = [lever for lever in declaration.get("levers", ()) if lever not in properties]
    if unknown:
        raise ValueError("levers that are not parameters: %s" % ", ".join(unknown))
    job_if = declaration.get("job_if")
    if job_if is not None:
        if job_kind is None:
            raise ValueError("job_if needs a job_kind to send its calls to")
        if not (isinstance(job_if, tuple) and len(job_if) == 2 and isinstance(job_if[1], (set, frozenset, tuple))):
            raise ValueError("job_if is (enum parameter, {values})")
        parameter, values = job_if
        allowed = properties.get(parameter, {}).get("enum") if isinstance(parameter, str) else None
        if not allowed or not values or not set(values) <= set(allowed):
            raise ValueError("job_if %r: values of an enum parameter" % (parameter,))
        if "default" not in properties[parameter] and parameter not in declaration["parameters"].get("required", ()):
            # Left out, the operator's own default would decide, and it may be one of the job's values.
            raise ValueError("job_if %r: the parameter needs a default or must be required" % (parameter,))


def _job_if(declaration, parameters):
    """``{parameter: value}`` when the call's value (its default when omitted) is one ``job_if`` sends to the job."""
    if not declaration.get("job_if"):
        return None
    name, values = declaration["job_if"]
    value = (parameters or {}).get(name, declaration["parameters"]["properties"][name].get("default"))
    return {name: value} if value in values else None


def admit(declaration, measured, parameters=None):
    """Before staging: None when the call runs in the turn, else raise. ``measured`` holds the input's measures (every
    bounded one); ``parameters`` the call's parameters (``job_if`` reads one of them)."""
    bound = declaration.get("input_bound") or None
    job_kind, levers = declaration.get("job_kind"), list(declaration.get("levers", ()))
    always = _job_if(declaration, parameters)
    if always:
        (name, value), = always.items()
        message = ("%s with %s %s always runs as a %s job, whatever the input's size: start the same work with "
                   "angelo_job_start, which runs in the background." % (declaration["kind"], name, value, job_kind))
        _refuse(message, bound, {m: measured.get(m) for m in bound or ()}, job_kind, levers, always)
    if not bound:
        return None
    unmeasured = [measure for measure in bound if measure not in measured]
    if unmeasured:
        raise ValueError("the input was not measured for %s" % ", ".join(unmeasured))
    if all(measured[measure] <= limit for measure, limit in bound.items()):
        return None
    sizes = ", ".join("%s %s (most %s)" % (m, measured[m], limit) for m, limit in bound.items())
    message = "%s on this input is more than one turn runs within %g s: %s." % (declaration["kind"], TURN_BUDGET_S,
                                                                               sizes)
    if job_kind:
        message += " Start the same work as a %s job (angelo_job_start), which runs in the background." % job_kind
    elif levers:
        message += " Make the input smaller with %s, or split the work into smaller calls." % ", ".join(levers)
    else:
        message += " Split the work into smaller calls."
    _refuse(message, bound, {measure: measured[measure] for measure in bound}, job_kind, levers, None)


def _refuse(message, bound, measured, job_kind, levers, job_if):
    error = RefusalError(message, code="LIMIT_EXCEEDED", details={
        "limit": "turn_budget", "budget_s": TURN_BUDGET_S, "bound": dict(bound) if bound else None,
        "measured": measured, "job_kind": job_kind, "levers": levers, "job_if": job_if})
    error.partial_changes_possible = False   # Refused before staging: nothing ran.
    raise error
