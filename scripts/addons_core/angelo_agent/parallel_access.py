# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether this Angelo offers parallel agents (the ``angelo_workers_*`` family) to its chats.

On by default on a capable machine (1.0.6, the maintainer's decisions: parallel edit ships reachable, and only where
the machine can run it, ``parallel_capability``): the lead can start workers, which the session rail shows live and
whose parts it merges back. Below the bar it does not exist there, and Settings says what it needs.
``ANGELO_ENABLE_PARALLEL_AGENTS=0`` at launch turns it off; ``=1`` turns it on whatever the machine, for test fixtures
and headless suites only (a background Angelo never passes the check). Off:
the family is then not in the build's tool list at all (``tool_specs`` adds it only when this is on, as it adds
background-job tools only on a build that has jobs), so every request, plan schema, Codex declaration and discovery
listing is byte-identical to a build without it. Decided once per launch: the tool list is built when the add-on is
imported. A worker's own chat never gets the family (``for_chat``): workers do not start workers. No bpy.

A worker Blender that hosts its chats (design A′) runs in background, where the machine check never passes, so it
cannot decide this for itself: the lead that started it did. ``in_worker`` (``ANGELO_PARALLEL_WORKER=1``, set only by
``worker_process.worker_environment`` for a hosting worker) gives its chats what a lanes worker chat gets in Angelo:
its own progress tool (``tool_specs``), the worker's tool set (``restrict``) and its briefing
(``agent_guidance.lead_guidance``), never the lead's family. Found by R7: without it a hosted worker's
``angelo_workers_progress`` was refused ("not available in this request").
"""

from .worker_spec import FAMILY_NAMES, PART_NAMES, WORKER_NAMES


def _forced():
    from . import product_env
    return product_env.get("ENABLE_PARALLEL_AGENTS")


WORKER_ENV = "PARALLEL_WORKER"   # ANGELO_PARALLEL_WORKER: "1" in a worker Blender that hosts its chats


def in_worker():
    """Whether this Angelo is a headless worker Blender a lead started to host its chats: every chat here is a
    worker's own (``for_chat``)."""
    from . import product_env
    return product_env.get(WORKER_ENV) == "1" and product_env.get("HEADLESS") == "1"


def enabled():
    value = _forced()
    if value in {"0", "1"}:
        return value == "1"
    from . import parallel_capability
    return parallel_capability.current().capable


def worker_limit():
    """How many workers may run at once here: the person's Workers at once (``chosen``), else the machine's own count
    (``parallel_capability``). Forced on (a live trial, a benchmark arm, a suite) skips only the GPU and window check
    and never reads the person's choice: the count still follows the machine's memory and cores, at least
    ``MIN_WORKERS`` so a fixture runs on a small one (break-it P1-20 H2), and a forced run may cap it lower with
    ``ANGELO_PARALLEL_WORKER_CAP`` (a benchmark arm); a person's Angelo, never forced, ignores the cap."""
    from . import parallel_capability, product_env
    if _forced() == "1":
        machine = max(parallel_capability.MIN_WORKERS, parallel_capability.measured_fit())
        cap = forced_cap(product_env.get("PARALLEL_WORKER_CAP"))
        return min(machine, cap) if cap else machine
    return (chosen() or parallel_capability.current().workers) if enabled() else 0


def chosen():
    """The person's Workers at once (``parallel_setting``): 1-16, or 0 for Suggested (follow the machine), also before
    the preferences are registered and without Blender. Read from Blender's own preferences, never through ``ui``:
    the tool list asks for the count while ``ui`` is still importing (its import chain reaches ``tool_specs``), and a
    half-imported ``ui`` stopped the add-on loading (Mac 1 GUI suite, cf141994dd4)."""
    from .parallel_capability import MAX_WORKERS
    try:
        import bpy
    except ImportError:   # No Blender (host tests).
        return 0
    addon = bpy.context.preferences.addons.get(__package__)
    value = getattr(getattr(addon, "preferences", None), "parallel_workers", 0)   # None until registered.
    return value if type(value) is int and 1 <= value <= MAX_WORKERS else 0


def forced_cap(value):
    """A forced run's cap: a whole number of at least 1 (spaces around it allowed), else None (no cap). A value set
    but not such a number is said once in the log (break-it E2E-16 L-2: "1.0" or "x" silently ran uncapped, and "²"
    passed ``isdigit`` and made every start raise)."""
    text = str(value or "").strip()
    try:
        cap = int(text) if text.isascii() else None
    except ValueError:
        cap = None
    if text and (cap is None or cap < 1) and text not in _said:
        _said.add(text)
        print("Angelo: ANGELO_PARALLEL_WORKER_CAP=%r is not a whole number of at least 1; no cap" % text)
    return cap if cap is not None and cap >= 1 else None


_said = set()


def limit_where():
    """Where ``worker_limit`` comes from, in the lead's words: this computer, or this run when forced (a benchmark
    arm, a test fixture: the 2026-10-25 dev check's capped lead was told "on this computer")."""
    return "in this run" if _forced() == "1" else "on this computer"


def limit_text(limit, busy):
    """The places line of the lead's status while a part of it waits (``worker_status``): where the limit comes from,
    this computer, or this run when forced (a benchmark arm, a test fixture: the 2026-10-25 dev check's capped lead was
    told "on this computer"), each count in its own number ("1 workers" was said there too)."""
    from .worker_spec import at_once
    return ("At most %s at once %s; %d already %s, so %d more can start; queued parts start in order, each once a "
            "running worker is done" % (at_once(limit), limit_where(), busy, "runs or is starting" if busy == 1 else
                                        "run or are starting", max(0, limit - busy)))


def restrict(allowed, tool_names, worker=False):
    """A request's allowed tool set (``None``: every tool) for its chat: the lead never gets a worker's own progress
    tool, a worker never gets the lead's start and status. Off, the family is not in the tool list at all, so the set is
    returned as it is (``None`` stays ``None``: byte-identical)."""
    if not enabled() and not in_worker():
        return allowed
    if not worker:   # None stays None: the declarations leave the worker's own tool out (tool_specs.declared).
        return allowed if allowed is None else frozenset(allowed) - PART_NAMES
    # A worker's chat is offered exactly what runs for it: its worker Blender's reads, typed edits and scripts (approved
    # in Angelo as any chat's, ``worker_script``), and the chat's own tools in Angelo. Anything else would only be
    # refused there (live trial 2026-10-24: a worker offered a tool it could not run planned with it, was refused and
    # ended its turn without building its part).
    from .remote_executor import local_tools, worker_tools
    reads, edits = worker_tools()
    return (frozenset(tool_names if allowed is None else allowed) & (reads | edits | local_tools())) - WORKER_NAMES


def for_chat(allowed, tool_names, controller):
    """``restrict`` for the chat a request belongs to (a worker's chat never gets the family)."""
    from . import worker_routing
    return restrict(allowed, tool_names, worker=worker_chat(controller))


def worker_chat(controller):
    """Whether ``controller`` is a worker's own chat: bound to, hosted in or recorded for a worker in Angelo, or any
    chat of a worker Blender that hosts its chats (``in_worker``)."""
    from . import worker_routing
    return in_worker() or worker_routing.worker_snapshots(controller)


def hidden_tools():
    """Tool names discovery must not list or describe in this session."""
    return PART_NAMES if enabled() else FAMILY_NAMES


def discovery_hidden(controller=None):
    """``hidden_tools`` for ``controller``'s discovery, and for a worker's own chat (``worker_chat``, or the lanes
    worker's shadow of it) every tool its worker set does not offer: hand rigging, sculpt, jobs and the rest would only
    be refused there (``worker_refusals``), so a worker chat is never told of them."""
    hidden = hidden_tools()
    if controller is None or not (getattr(controller, "worker_shadow", False) or worker_chat(controller)):
        return hidden
    from .remote_executor import local_tools, worker_tools
    from .tool_specs import TOOL_MAP
    reads, edits = worker_tools()
    return hidden | (frozenset(TOOL_MAP) - reads - edits - local_tools())
