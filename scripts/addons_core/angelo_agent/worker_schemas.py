# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The schemas a lead's chat already looked up in this job, sent with a worker's brief. No Blender of its own.

In the 2026-10-25 dev check each worker spent its first request (2.3 s and 2.9 s) on its progress plan and a schema
lookup; worker 1 looked up ``shader_nodes``, which its lead had looked up in the same job. A worker chat runs its lead's
runtime and model with the same declarations of kinds and deferred tools, so what its lead had to look up it lacks too.
``block`` gives the worker those answers as facts at the end of its brief, after the opening every worker shares (so
the prompt cache still serves that opening), labelled as already looked up and never as something to use.

What goes:

* the lead chat's own ``angelo_agent_capabilities`` lookups by name that completed in the job that started the part:
  the turn of the ``angelo_workers_start`` call that named it (its activity row), from the person's message that began
  it to their next one, lookups made after the start included (the dev check's lead looked ``shader_nodes`` up just
  after starting worker 1, before that worker was ready). A start not yet in the rows (a background Blender adopts
  inside its own call) reads the lead's current request. Never a lookup of the person's earlier or later requests, a
  failed one, or a collaborator's.
* only what the worker chat is offered (``parallel_access.restrict``): a tool it is offered whose exact schema its
  declarations defer (``DEFERRED_TOOLS``; a core tool's is declared to it already), and action kinds, checks and
  recipes while it is offered a tool that carries them (``CARRIERS``). The worker family, Python, Studio and file
  tools are never offered to a worker, so never sent.
* one lookup per family, its names in the order the lead first resolved them, answered by the function the worker
  Blender runs for its own lookup (``blender_tools.agent_capabilities``) in the copy a model reads
  (``model_results.model_result``), serialized as the worker's runtime returns a tool result: compact JSON, escaped to
  ASCII on Codex and Claude's typed plan, unescaped on Claude's direct-tools route. The worker's model reads each
  answer exactly as its own lookup would return it.
* at most ``MAX_BYTES``: names are taken in the order the lead resolved them while the block still fits, so one too
  large on its own (``angelo_scene_program``'s whole schema is about 122 KB) never crowds out the rest.
"""

import json

CAPABILITIES = "angelo_agent_capabilities"
START = "angelo_workers_start"
# About two of the largest action schemas (shader_nodes 10.4 KB, modifier_add 9.8 KB): the dev check's lead set
# makes 10.8 KB. Every worker's first request carries the block uncached, and its later requests read it cached.
MAX_BYTES = 24000
MAX_NAMES = 32   # One lookup's names (execution_spec.EXECUTION_TOOLS).
# Model-visible (never translated): a fact about this job, never an instruction.
HEADER = "\n\nThe lookups already made in this job (each angelo_agent_capabilities call and its answer):"
# The worker's tools whose arguments carry a family's items: offered while one of them is.
CARRIERS = {"ACTIONS": frozenset({"angelo_scene_program"}),
            "CHECKS": frozenset({"angelo_scene_program", "angelo_scene_verify"}),
            "RECIPES": frozenset({"angelo_apply_recipe", "angelo_expand_recipe"})}


def block(worker, lead, part):
    """The text ``worker``'s brief for ``part`` (one of the lead's start ``parts``) ends with; "" when none applies.
    Only ever a saving: anything unexpected leaves the block out and the worker looks up for itself."""
    try:
        order = resolved(job_rows(lead, part), offered())
        return render(order, ascii_only(worker)) if order else ""
    except Exception as error:   # noqa: BLE001 - the brief always goes.
        print("Angelo: worker schemas:", error)
        return ""


def job_rows(lead, part):
    """The lead's rows of the job that started ``part``: from the person's message before its start row (else before
    the newest row) to their next message."""
    rows = list(lead.state.get("messages") or ())
    anchor = next((index for index in range(len(rows) - 1, -1, -1) if _starts(rows[index], part)), len(rows) - 1)
    begin = next((index for index in range(anchor, -1, -1) if rows[index].get("role") == "user"), None)
    if begin is None:
        return []
    end = next((index for index in range(anchor + 1, len(rows)) if rows[index].get("role") == "user"), len(rows))
    return rows[begin + 1:end]


def _starts(row, part):
    arguments = row.get("arguments") if row.get("role") == "activity" and row.get("tool") == START else None
    return isinstance(arguments, dict) and isinstance(arguments.get("parts"), list) and part in arguments["parts"]


def offered():
    """The tool names a worker chat is offered (none while parallel agents are off)."""
    from . import parallel_access
    from .tool_specs import TOOL_MAP
    return frozenset(parallel_access.restrict(None, frozenset(TOOL_MAP), worker=True) or ())


def wanted(family, name, tools):
    """Whether a worker offered ``tools`` gets ``name``'s answer in ``family``."""
    from .plan_schema import DEFERRED_TOOLS
    from .tool_specs import TOOL_MAP
    if family in ("TOOLS", "QUERIES") or name in TOOL_MAP:
        return name in tools and name in DEFERRED_TOOLS
    carriers = CARRIERS.get(family) or frozenset().union(*CARRIERS.values())   # SUMMARY: a name of any family.
    return bool(carriers & tools)


def resolved(rows, tools):
    """[(family, name)] the lead's completed lookups in ``rows`` resolved for a worker offered ``tools``, in the order
    the lead first resolved them."""
    from .execution_spec import CAPABILITY_FAMILIES
    order = []
    for row in rows:
        if (row.get("role") != "activity" or row.get("tool") != CAPABILITIES or row.get("status") != "completed"
                or row.get("parent_call") or row.get("collaborator")):
            continue
        arguments, result = row.get("arguments"), row.get("result")
        if (not isinstance(arguments, dict) or not isinstance(result, dict) or result.get("error")
                or result.get("success") is False or arguments.get("family") not in CAPABILITY_FAMILIES
                or not isinstance(arguments.get("names"), list)):
            continue
        unknown = result.get("unknown_names") if isinstance(result.get("unknown_names"), list) else ()
        for name in arguments["names"]:
            item = (arguments["family"], name)
            if (isinstance(name, str) and name not in unknown and item not in order
                    and wanted(arguments["family"], name, tools)):
                order.append(item)
    return order


def ascii_only(worker):
    """Whether the worker's runtime escapes a tool result to ASCII: Codex and Claude's typed plan do (``json.dumps``'
    default), Claude's direct-tools route does not (``claude_direct.DirectRequest._packed``)."""
    from . import claude_direct
    from .provider_registry import runtime_kind
    try:
        runtime = runtime_kind(worker.state.get("provider") or "")
    except ValueError:
        return True
    return not (runtime == "claude" and claude_direct.requested())


def answer(family, names):
    """What the worker Blender returns for its own lookup of ``names`` (``BlenderExecutor.execute``)."""
    from .blender_tools import agent_capabilities
    return agent_capabilities(family, list(names))


def render(order, ascii_text, limit=MAX_BYTES):
    """The block for ``order`` ([(family, name)]): every name when it fits in ``limit`` bytes, else each in order
    while the block still fits; "" when none does."""
    from .model_results import model_result
    cache = {}

    def text(items):
        families = {}
        for family, name in items:
            families.setdefault(family, []).append(name)
        lines = [HEADER]
        for family, names in families.items():
            key = (family, tuple(names))
            if key not in cache:
                cache[key] = json.dumps(model_result(CAPABILITIES, answer(family, names)), ensure_ascii=ascii_text,
                                        allow_nan=False, separators=(",", ":"))
            lines += [CAPABILITIES + " " + json.dumps({"family": family, "names": names}, separators=(",", ":")),
                      cache[key]]
        return "\n".join(lines)

    def fits(items):
        return len(text(items).encode("utf-8")) <= limit

    order = [item for index, item in enumerate(order)
             if sum(1 for family, _name in order[:index] if family == item[0]) < MAX_NAMES]
    if fits(order):
        return text(order)
    kept = []
    for item in order:
        if fits(kept + [item]):
            kept.append(item)
    return text(kept) if kept else ""
