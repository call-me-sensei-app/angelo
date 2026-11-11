# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How long a RENDERED look took, said so the model can choose: a one-time kernel compile, the scene's preparation,
the look's sampling (view_render). Model-facing English, never translated.

The first render after an update compiles Cycles' render kernels (angelo_render_device.kernels): minutes, once. Read
as preparation it told the model every look of the scene costs that much ("iterate with LIT"), and it spent the
request's look budget. It is said apart, and only the look's own time counts against that budget, with two bounds
(break-it review 2026-11-11, F3): a look's compile counts at most ``kernels.CAP_S``, and a request is exempted at most
``kernels.CAP_S`` of compile over all its looks, so a machine whose kernels never stay cached still runs out of looks.
"""

HEAVY_PREPARATION = 10  # Seconds of preparation from which a result says that LIT is the way to iterate.


def _kernels():
    try:
        from angelo_render_device import kernels
    except ImportError:
        return None
    return kernels


def compiled_s(until=None):
    """The counted kernel compile of the render that just ended (``kernels.WATCH``, reset at each render's and job's
    start), all of it or only what came before monotonic time ``until``; 0 without the module."""
    kernels = _kernels()
    return kernels.WATCH.compile_s(until=until) if kernels is not None else 0.0


def capped(seconds):
    kernels = _kernels()
    return kernels.credited(seconds) if kernels is not None else 0.0


def exempt(compiled, exempted):
    """Seconds of this look's ``compiled`` kernel compile the request's look budget leaves out, given the
    ``exempted`` seconds its earlier looks were: at most one cap over the whole request."""
    kernels = _kernels()
    room = max(0.0, kernels.CAP_S - float(exempted or 0.0)) if kernels is not None else 0.0
    return min(capped(compiled), room)


def look_timeout(minutes, grace, cycles=True):
    """The look's tool call outlives the longest budget its job can be given: its minutes plus, for a Cycles look,
    the credited compile (``job_deadline.budget_s``), plus ``grace`` for the stop and the finish. The job's budget
    ends it first; an EEVEE or Workbench look is never credited, so its call keeps its own end (r2 re-review R2)."""
    kernels = _kernels()
    return float(minutes) * 60 + (kernels.CAP_S if cycles and kernels is not None else 0.0) + grace


def look_timing(seconds, first_sample, compiled, compiled_setup):
    """``{"took", "spent_s", ...}``: the result's sentence and fields, and the seconds the request's budget spends.
    ``compiled``: the look's kernel compile; ``compiled_setup``: the part of it before its first sample."""
    compiled = round(min(capped(compiled), max(0.0, float(seconds))), 1)
    found = {"spent_s": round(max(0.0, seconds - compiled), 1)}
    took = "It took %s s" % round(seconds, 1)
    if compiled > 0:
        found["kernel_compile_s"] = compiled
    if first_sample is not None:
        before = min(capped(compiled_setup), compiled, max(0.0, float(first_sample)))
        prepare = round(max(0.0, first_sample - before), 1)
        sampling = round(max(0.0, seconds - first_sample - (compiled - before)), 1)
        found.update(prepare_seconds=prepare, sampling_seconds=sampling)
        parts = []
        if compiled > 0:
            parts.append("%s s loading or compiling Cycles' render kernels (once after an update or on a new GPU; "
                         "later renders skip it)" % compiled)
        parts.append("%s s preparing the scene for rendering and %s s sampling" % (prepare, sampling))
        took += ": " + ", ".join(parts)
        if prepare >= HEAVY_PREPARATION:
            took += (". Preparation does not depend on the picture's size or samples, so every look of this scene "
                     "costs about as much: iterate with LIT (seconds) and render at checkpoints")
    elif compiled > 0:
        took += (", %s s of it loading or compiling Cycles' render kernels (once after an update or on a new GPU)"
                 % compiled)
    found["took"] = took
    return found
