# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The lead's tools for parallel agents: start worker agents on parts, and read how they are doing. No Blender.

``angelo_workers_start`` gives each part its own worker: a background Blender with a file of its own, and a chat in
Angelo bound to it that builds the part with the typed scene tools and Python (``worker_sessions``; the worker's own
base instructions are ``WORKER_BRIEFING``, its first message the part's brief). In an Ask chat it waits for the
person's approval (its card names every part); in Auto it starts at once, as Auto applies the typed edits: a timed run
has no click in it, and the person sees each worker on the rail. Parts past the worker limit wait their turn, each
starting once a running worker is done (``worker_queue``).
``angelo_workers_status`` reads every worker's state, its newest reply and what its part holds; it can wait for a
worker to be done, and take back queued parts (``worker_status``).
``angelo_workers_merge`` brings finished parts into this scene, each placed once or many times (``worker_merge``): a
bounded, undoable edit like the typed scene tools.

The family exists in a request only while parallel agents are enabled (``parallel_access``); otherwise no byte of
it reaches a model. Split by unique design, never by copy: a set of identical things is one part, copied once it is
built (the parallel-agents design, "Splitting work").
"""

from .parallel_capability import MAX_WORKERS   # The most at once on any machine; each machine's own count may be lower.
from .worker_sessions import MAX_AUTO_STARTS   # The most parts one start takes (and Auto starts per request).

PREFIX = "angelo_workers_"
NAME_LIMIT = 40
BRIEF_LIMIT = 4000
WAIT_DEFAULT, WAIT_LIMIT = 900, 1800   # Seconds a status call may wait for the workers to finish.

PART = {"type": "object", "properties": {
    "name": {"type": "string", "minLength": 1, "maxLength": NAME_LIMIT,
             "description": "Short name of the part, shown on its tile (e.g. a piece of a set)."},
    "brief": {"type": "string", "minLength": 1, "maxLength": BRIEF_LIMIT,
              "description": "Everything the worker needs to build this part alone: what it is, its size in metres, "
                             "its style and materials, where it sits."}},
    "required": ["name", "brief"], "additionalProperties": False}

from .worker_merge import PART as MERGE_PART  # noqa: E402 - the merge's own schema, beside its checks.

def start_description(limit, where="on this computer"):
    """The start tool's text for a launch that runs ``limit`` workers at once: the lead splits for it before its first
    start. The count is ``parallel_access.worker_limit`` (the person's Workers at once, else what the machine fits), or
    a forced run's cap, ``where`` then "in this run" (break-it E2E-16 M-4: a capped lead was told 4); parts past it wait
    their turn (``worker_queue``: the dev check's capped lead lost 5.5 s to a refused start and its re-plan,
    B3-SPEED-ANALYSIS)."""
    return ("This tool starts one worker agent per part, in parallel (in an Ask chat once the person approves). Each "
            "builds its part in a file of its own with the typed scene tools and Python, never in this scene; the "
            "person watches each one live. A repeated thing is one part: its worker builds it once, and the merge "
            "places it many times. At most %s at once %s; the parts past that wait their turn, each starting once a "
            "running worker is done. A call runs once the whole request that makes it is written: a script written in "
            "the same request delays every worker's start by its writing time. A worker that has started runs beside "
            "your requests; your own share runs in turn with your other requests." % (at_once(limit), where))


def at_once(limit):
    """``limit`` workers running, said right for one too (Workers at once offers 1; model-facing, never translated)."""
    return "1 worker runs" if limit == 1 else "%d workers run" % limit


WORKER_TOOLS = [
    {"name": PREFIX + "start",
     "description": start_description(MAX_WORKERS),
     "inputSchema": {"type": "object", "properties": {"parts": {"type": "array", "minItems": 1,
                                                              "maxItems": MAX_AUTO_STARTS, "items": PART}},
                     "required": ["parts"], "additionalProperties": False},
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}},
    {"name": PREFIX + "merge",
     "description": ("Bring finished parts into this scene as ordinary objects, one Undo step: each part's worker "
                     "file is appended into a collection named after the part, then placed once per placement. "
                     "Give a repeated design several placements instead of building it again: every copy shares "
                     "its mesh; a placement can swap the part's materials for another of its materials. Merge a "
                     "part once its worker is done (a status wait naming it answers when it ends); merged workers stop."),
     "inputSchema": {"type": "object", "properties": {"parts": {"type": "array", "minItems": 1, "maxItems": 16,
                                                              "items": MERGE_PART}},
                     "required": ["parts"], "additionalProperties": False},
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}},
    {"name": PREFIX + "status",
     "description": ("Every worker this conversation started: its state (working, approval, done: it reported every "
                     "step of its plan done, replied: its turn ended otherwise, merging, merged, failed, ended, idle, "
                     "queued: waiting for a free place, starting, failed_to_start); how its newest turn ended (turn: "
                     "completed, failed, stopped, not_sent; empty while it works); its newest reply; the objects its "
                     "part holds, its size and world bounds in metres (size_m, bounds_m: where it stands, to place parts "
                     "beside each other; helper geometry is left out unless a part has nothing else: Boolean operands, "
                     "wire or bounds display, hidden from renders). With wait, answers once a worker is done that no "
                     "workers_finished_since_status listed, or every part is done (or after timeout_seconds), costing "
                     "nothing meanwhile. A worker's approval card is the person's to answer on its tile; while no "
                     "window shows the tiles, a card not yet named in an approval_no_window_shows that reached you "
                     "ends a wait. With workers, once "
                     "none of those runs or waits to start (not_built: any that cannot be), so a merge after it in the "
                     "same request takes them as they finish. Your tool results and messages list finished, unmerged "
                     "workers not yet listed in one that reached you (workers_finished_since_status)."),
     "inputSchema": {"type": "object", "properties": {
         "wait": {"type": "boolean", "description": "Answer once a worker is done, or all are."},
         "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": WAIT_LIMIT,
                             "description": "Longest wait (default %d)." % WAIT_DEFAULT},
         "take_back": {"type": "array", "maxItems": MAX_AUTO_STARTS, "items": {"type": "integer", "minimum": 1},
                       "description": "Queued parts' worker numbers to take back before they start."},
         "workers": {"type": "array", "minItems": 1, "maxItems": MAX_AUTO_STARTS,
                     "items": {"type": "integer", "minimum": 1}, "description": "With wait: only these workers."}},
                     "additionalProperties": False},
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}},
]
WORKER_NAMES = frozenset(tool["name"] for tool in WORKER_TOOLS)

# A worker's own tool (never the lead's, never an app's): its plan and how far it is, for its tile on the person's rail.
PROGRESS = PREFIX + "progress"
STEP_LIMIT, STEPS_LIMIT = 80, 40
PART_TOOLS = [
    {"name": PROGRESS,
     "description": ("This part's plan and progress, as its tile in the person's view shows them: its steps "
                     "(short titles, in order) and done (how many are finished). It changes nothing in the scene, and "
                     "it runs in the same reply as edits."),
     "inputSchema": {"type": "object", "properties": {
         "steps": {"type": "array", "minItems": 1, "maxItems": STEPS_LIMIT,
                   "items": {"type": "string", "minLength": 1, "maxLength": STEP_LIMIT}},
         "done": {"type": "integer", "minimum": 0, "maximum": STEPS_LIMIT}},
         "required": ["done"], "additionalProperties": False},
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}},
]
PART_NAMES = frozenset(tool["name"] for tool in PART_TOOLS)
FAMILY_NAMES = WORKER_NAMES | PART_NAMES


def for_launch(limit, where="on this computer"):
    """The lead's tools as this launch declares them: the start tool says this launch's own count and where it comes
    from (``parallel_access.limit_where``)."""
    return [dict(tool, description=start_description(limit, where)) if tool["name"] == PREFIX + "start" else tool
            for tool in WORKER_TOOLS]
# Sent with the family's schemas when a model looks them up (agent_guidance.FAMILY_GUIDANCE).
WORKER_GUIDANCE = ("About parallel workers: a worker sees only its own file and its brief, so a brief complete on its own "
                   "(size, style, materials, where it is built) is all its part is built from; angelo_workers_start "
                   "takes one part per unique design. A worker's part stays in its own file until angelo_workers_merge "
                   "brings it into this scene; angelo_workers_status says each worker's state, and its wait answers "
                   "once a worker is done. Your own edits run while they build. This lookup's "
                   "workers_launched_ahead counts workers launching or ready for you. Each copy of a "
                   "design is a placement of one merged part; the finishes that differ between copies are materials "
                   "the part's worker builds, swapped per placement.")


# Appended to a lead chat's base instructions while parallel agents are on (agent_guidance.lead_guidance): what the
# family is for, generic to every job. Model-visible, never translated.
WORKER_ANNOUNCEMENT = ("\nAbout parallel workers: a job made of several independent parts, each its own design, can be split "
                       "across worker agents that build their parts at the same time, each in a file of its own, while "
                       "you do your own share; a finished part comes into this scene only through the merge. A design "
                       "used more than once is one part, built once and placed many times by the merge "
                       "(angelo_workers_start, angelo_workers_status and angelo_workers_merge are deferred: "
                       "angelo_agent_capabilities family TOOLS gives their schemas and guidance).")

# A worker chat's own base instructions (agent_guidance.lead_guidance), in place of the lead's announcement: the same
# text for every worker of every job, so it is part of the cached prefix all workers share; each worker's first message
# carries only its part (``worker_sessions.BRIEF``). Model-visible, never translated. Facts only: the model decides how
# it builds (the maintainer's rule); never a workflow such as which tool to prefer.
WORKER_BRIEFING = ("\nThis chat is a parallel worker: you build one part of a larger job, in a file of your own; "
                   "another agent leads the job and gave you this part (your first message names it and gives its "
                   "brief). You have the typed scene tools and Python (angelo_execute_python) in your part's own "
                   "Blender, approved under the lead's approval rules; angelo_view shows your part. The steps and done "
                   "count given to angelo_workers_progress show on your tile in the person's view; that tool changes "
                   "nothing in the scene and runs in the same reply as edits. The lead reads your newest reply and "
                   "your part's measured size, and its merge brings in every object of your file, into the lead's "
                   "scene, where the lead sees it with the rest of that scene. The lead's scene "
                   "has its own lights, camera and world; a light the brief asks for is part of the part.")


def validate_worker(name, args):
    """The canonical arguments; ValueError for anything else (``tool_specs.validate_call``)."""
    if name == PREFIX + "status":
        wait, timeout, back = args.get("wait", False), args.get("timeout_seconds", WAIT_DEFAULT), args.get("take_back", [])
        only = args.get("workers")
        if set(args) - {"wait", "timeout_seconds", "take_back", "workers"} or not isinstance(wait, bool) \
                or type(timeout) is not int or not 1 <= timeout <= WAIT_LIMIT or any(
                    not isinstance(numbers, list) or len(numbers) > MAX_AUTO_STARTS
                    or any(type(number) is not int or number < 1 for number in numbers) for numbers in (back, only or []))\
                or only is not None and (not only or not wait):
            raise ValueError("status takes wait (true or false), timeout_seconds (1-%d), take_back and, with wait, "
                             "workers (each at most %d worker numbers, each 1 or more)" % (WAIT_LIMIT, MAX_AUTO_STARTS))
        clean = {"wait": True, "timeout_seconds": timeout} if wait else {}
        clean.update({"workers": sorted(set(only))} if only else {})
        return dict(clean, take_back=sorted(set(back))) if back else clean
    if name == PREFIX + "merge":
        from .worker_merge import validate
        return validate(args)
    if name == PROGRESS:
        return _progress(args)
    parts = args.get("parts")
    if not isinstance(parts, list) or not 1 <= len(parts) <= MAX_AUTO_STARTS:
        raise ValueError("Give 1-%d parts (%s given)" % (MAX_AUTO_STARTS, len(parts) if isinstance(parts, list)
                                                         else "none"))
    clean, names = [], set()
    for part in parts:
        if not isinstance(part, dict) or set(part) != {"name", "brief"}:
            raise ValueError("Each part is {name, brief}")
        label, brief = part["name"], part["brief"]
        for value, limit, field, breaks in ((label, NAME_LIMIT, "name", ""), (brief, BRIEF_LIMIT, "brief", "\n\t")):
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError("A part's %s needs 1-%d characters (this one has %s)"
                                 % (field, limit, len(value) if isinstance(value, str) else "none"))
            bad = next((ch for ch in value if not ch.isprintable() and ch not in breaks), None)
            if bad is not None:
                raise ValueError("A part's %s may not contain the control character %r%s" % (
                    field, bad, " (line breaks and tabs are fine)" if breaks else ""))
        label = " ".join(label.split())
        if label.casefold() in names:
            raise ValueError("Part names must differ: %s is given twice" % label)
        names.add(label.casefold())
        clean.append({"name": label, "brief": brief.strip()})
    return {"parts": clean}


def _progress(args):
    done, steps = args.get("done"), args.get("steps")
    if type(done) is not int or done < 0:
        raise ValueError("done is how many steps are finished: a whole number from 0")
    clean = {"done": min(done, STEPS_LIMIT)}
    titles = clean_steps(steps) if steps is not None else []
    if titles:   # Steps with no usable title are left out: the count still shows, the plan still runs (P1-11 S8).
        clean["steps"] = titles
    return clean


def clean_steps(steps):
    """A worker's step titles as its tile shows them: at most ``STEPS_LIMIT``, each one line of text cut at a word to
    ``STEP_LIMIT`` characters, a blank or odd one kept as "…" so ``done`` counts the same steps; [] when none has text.
    A report is display only, so a long or odd title is cut, never a reason to refuse the plan it rides in (live trial
    2026-10-24: a worker's plan with every edit of its part was refused for one long title, and its corrected plan
    never built the part)."""
    import unicodedata
    titles, usable = [], False

    def plain(ch):   # Control characters and line breaks become spaces, a lone surrogate (it cannot be drawn) a
        kind = unicodedata.category(ch)   # replacement mark; joiners and other format characters stay (Persian).
        return " " if kind in {"Cc", "Zl", "Zp"} else "\ufffd" if kind == "Cs" else ch
    for step in (steps[:STEPS_LIMIT] if isinstance(steps, list) else ()):
        text = " ".join("".join(plain(ch) for ch in step).split()) if isinstance(step, str) else ""
        if len(text) > STEP_LIMIT:
            cut = text[:STEP_LIMIT - 1]
            text = (cut.rsplit(" ", 1)[0] if " " in cut else cut) + "…"
        usable = usable or bool(text)
        titles.append(text or "…")   # A blank or odd entry keeps its place, so done still counts the right steps.
    return titles if usable else []


CUBE_TOOLS = frozenset({"angelo_create_cube", "angelo_set_transform"})


def cube_batch(operations):
    """A plan's edits as one native batch when every edit is a cube or a move, else None. A worker's own progress
    reports in it are checked and left out of the batch, so they never cost one (break-it P1-7 N2); the caller puts
    them on the tile once the batch has committed (``note_reports``: never ahead of the work, P1-8 P5). In a plan that
    runs item by item they are items like any other. Without reports this is the plain cube rule."""
    edits = [operation for operation in operations if operation["tool"] not in PART_NAMES]
    if not edits or not all(operation["tool"] in CUBE_TOOLS for operation in edits):
        return None
    for operation in operations:
        if operation["tool"] in PART_NAMES:
            validate_worker(operation["tool"], operation.get("arguments") or {})
    return edits


def note_reports(operations, controller):
    """A committed cube batch's progress reports on the worker's tile, in plan order. ``controller`` is what a
    provider holds: a request's ``RuntimeContext`` (its chat is ``host``) or the chat itself."""
    chat_id = _chat_id(controller)
    if chat_id is None:
        return
    from . import parallel_sessions
    for operation in operations:
        if operation["tool"] in PART_NAMES:
            report = validate_worker(operation["tool"], operation.get("arguments") or {})
            parallel_sessions.note_progress(chat_id, report["done"], report.get("steps"))


def _chat_id(controller):
    """The id of the chat ``controller`` stands for: a provider's context carries only the request's state, which
    names no chat, so the id is its host's (``RuntimeContext.state`` has no "id": the A′ boundary map, 2026-10-27)."""
    chat = getattr(controller, "host", None) or controller
    state = getattr(chat, "state", None)
    chat_id = state.get("id") if isinstance(state, dict) else None
    return chat_id if isinstance(chat_id, str) and chat_id else None
