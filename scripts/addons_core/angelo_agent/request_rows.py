# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whose words a chat row is. A request is a ``user`` row so the model reads it as one, but Angelo makes some itself:
an accepted staged preview (``native_preview_accept``), the worker-done event (``angelo_event``, ``worker_resume``)
and a worker's brief (``worker_brief``, ``worker_sessions``: written by its lead's model, with the person's words
quoted in it). Readers that mean the person's own words (their newest request, URLs they gave, the images of their
request, the person's input style) read ``person_request`` (break-it EVENT M-3; R2-L-6: a URL in a brief was taken as
the person's evidence); a rebased context's requests are the chat's task, the brief included (``task_request``).
Whose send it was is kept when it is sent (``persons_send``): only the person's goes back to their composer when it is
never sent, and only theirs takes their staged files, never a connected app's task (R2-L-5). No bpy.
"""

WORKER_BRIEF = "worker_brief"
EVENT, GOAL = "angelo_event", "angelo_goal"
CONTINUE = "angelo_continue"   # Continue a request cut off when Angelo closed (crash_resume).
HOST_REQUESTS = frozenset({"native_preview_accept", EVENT, GOAL, CONTINUE, WORKER_BRIEF})
NOTICE_REQUESTS = frozenset({EVENT, GOAL, CONTINUE})   # Angelo's own requests the chat draws as a notice (``chat_blocks``).
GOAL_FACT_KEYS = ("round", "score", "pass_at", "check", "minutes", "limit_minutes", "last_seconds", "last_tokens",
                  "last_usd")
IMAGE_ONLY_TEXT = "Use these reference files."   # A send with images and no words: its row's text, never theirs.


def request_fields(controller):
    """A request row's own kind as ``Controller._start_turn`` emits it: a connected app's task, or Angelo's own request
    (``Controller.send``'s ``host_request``, the worker-done event's parts, a cut-off request's continuation). Its
    open-turn mark is ``crash_resume.opened``'s."""
    if getattr(controller, "_external_task", False):
        return {"request_kind": "external_task", "client": controller.external_client}
    if getattr(controller, "_request_kind", None) == WORKER_BRIEF:
        return {"request_kind": WORKER_BRIEF}
    if getattr(controller, "_request_kind", None) == CONTINUE:
        return {"request_kind": CONTINUE, "parts": [int(part) for part in getattr(controller, "_host_request", None) or ()]}
    host = getattr(controller, "_host_request", None)   # Any host request is Angelo's, even with no parts (R2-L-8).
    if host is None:   # The person's send: where its words came from travels with them (plan_mode.rst, N2, N2b).
        from . import planning_state
        text = getattr(controller, "_request_text", None)
        state = getattr(controller, "state", None) if isinstance(getattr(controller, "state", None), dict) else {}
        choice = getattr(controller, "_plan_choice", None)   # The person's Go on a plan card (planning_controls.go),
        if not isinstance(choice, dict):   # or a Go sent again (Resend): its row's provenance, read from history.
            choice = planning_state.choice_for_text(state.get("messages"), text)
        mark = state.get("options", {}).get("plan_draft") if isinstance(state.get("options"), dict) else None
        if mark is not None and hasattr(controller, "set_options"):
            controller.set_options(plan_draft=None)   # Edit's mark goes with the send that carries its draft.
        if isinstance(choice, dict):
            return {"plan_choice": dict(choice)}
        return {"from_plan": True} if planning_state.marked(mark, text) else {}
    from .goal_loop import goal_round
    if goal_round(controller):   # A goal's round: its numbers for the row.
        return {"request_kind": GOAL, "parts": list(host), "goal": dict(getattr(controller, "_host_facts", {}) or {})}
    return {"request_kind": EVENT, "parts": list(host)}


def kept_fields(data):
    """What a stored request row keeps of its kind (``Controller``'s row pump), and its open-turn mark."""
    from .crash_resume import OPEN
    kept = _kept_kind(data)
    if isinstance(data.get(OPEN), dict):
        kept[OPEN] = {"pid": data[OPEN].get("pid"), "run": str(data[OPEN].get("run") or "")}
    return kept


def _kept_kind(data):
    if data.get("request_kind") == CONTINUE:
        return {"request_kind": CONTINUE, "parts": [int(part) for part in data.get("parts") or () if type(part) is int]}
    if data.get("request_kind") in {"external_request", "external_task"}:
        return {"request_kind": data["request_kind"], "client": str(data.get("client") or "")}
    if data.get("request_kind") == "angelo_event":
        return {"request_kind": "angelo_event", "parts": [str(part) for part in data.get("parts") or ()]}
    if data.get("request_kind") == WORKER_BRIEF:
        return {"request_kind": WORKER_BRIEF}
    if data.get("from_plan") is True and not data.get("request_kind"):   # A draft Edit filled from a plan.
        return {"from_plan": True}
    if data.get("plan_choice") is not None and not data.get("request_kind"):   # The person's Go (plan_mode.rst).
        from .planning_state import kept_choice
        choice = kept_choice(data["plan_choice"])
        return {"plan_choice": choice} if choice is not None else {}
    if data.get("request_kind") == GOAL:
        facts = data.get("goal") if isinstance(data.get("goal"), dict) else {}
        return {"request_kind": GOAL, "parts": [], "goal": {key: facts[key] for key in GOAL_FACT_KEYS
                                                           if type(facts.get(key)) in (int, float, str)}}
    return {}


def request_kind(value, *, lead=False):
    """A send's ``request_kind`` as it crosses to a worker's chat: None (the person's), ``WORKER_BRIEF``, a guarded goal round, or a worker-done event.
    ``lead``: the lead's own send (``sender``), which may also continue a request cut off when Angelo closed
    (``CONTINUE``); that never crosses to a worker, whose cut-off turns its own journal recovers."""
    if value not in (None, WORKER_BRIEF, GOAL, EVENT) + ((CONTINUE,) if lead else ()):
        raise ValueError("A send's request kind is the person's (none), a worker's brief, a held goal round, or a worker-done event")
    return value


def sender(controller, host_request, kind, references=None):
    """``Controller.send``'s answer to whose send it is, before anything is taken or stored: ``(files, kind,
    persons)``: the files it takes (the person's staged ones, or for a brief those Angelo staged for it; none for
    Angelo's own request or a connected app's task, R2-L-5), its checked ``request_kind``, and whether it is the
    person's own. The controller stores them only once the send is admitted, so a refused send leaves nothing."""
    kind = request_kind(kind, lead=getattr(controller, "remote_executor", None) is None)
    external = getattr(controller, "_external_task", False)
    if kind in (EVENT, CONTINUE) and host_request is None:
        raise ValueError("A worker-done event or a continuation needs a host request")
    if kind == GOAL:
        from .goal_controls import admits_round
        if external or not admits_round(controller, host_request):
            raise ValueError("Only the owned continuation of an active held goal may send a goal round")
    # A connected app's message carries only what the app attached itself (``references``), never the person's staged
    # files (R2-L-5).
    files = (() if host_request is not None else references if references is not None else
             () if external else controller.attachments)
    return files, kind, host_request is None and not external and kind is None


def persons_send(sender):
    """Whether ``sender``'s newest send was the person's own (``Controller.send``, a worker chat's mirror, as each
    records it): never Angelo's request, a connected app's task or a worker's brief."""
    found = getattr(sender, "_persons_send", None)
    return getattr(sender, "_host_request", None) is None if found is None else bool(found)


def apps_send(sender):
    """Whether ``sender``'s newest send was its connected app's own message in its External conversation, where the
    app is the requester (doc/angelo/external_mcp.rst, "Working through Angelo's agent"): never Angelo's request."""
    return (bool(getattr(sender, "external_client", "")) and getattr(sender, "_host_request", None) is None
            and getattr(sender, "_request_kind", None) is None)


def requesters_send(sender):
    """Whether ``sender``'s newest send was its requester's: the person's own, or in an External conversation its
    app's. What a request asks for (a goal held from its words) is the requester's to start."""
    return persons_send(sender) or apps_send(sender)


def drawn_as_notice(row):
    """A request row Angelo made that the chat draws as a notice, never a bubble (``chat_blocks``). Its snapshot's
    Restore goes on that notice (``activity_text.notice_view``, R2-L-1), as a bubble's goes under it."""
    return isinstance(row, dict) and row.get("role") == "user" and row.get("request_kind") in NOTICE_REQUESTS


def starts_request(row):
    """A user row that starts a request: never a steer (``steering``), which the person sent into a running request
    and which belongs to that request. Readers that look for "the current request" use this."""
    return isinstance(row, dict) and row.get("role") == "user" and not row.get("steer")


def own_words(row):
    """A user row of the person's own typing: no request kind at all (never Angelo's, a connected app's task or a
    memory rebind): the rows a worker's brief quotes and takes files from (``worker_request``,
    ``worker_references``)."""
    return isinstance(row, dict) and row.get("role") == "user" and not row.get("request_kind")


def persons_text(row):
    """What of a request row is the person's own words: its text, except for a Go on a plan card (``plan_choice``),
    whose text is the model's plan quoted for the model: only the notes the person typed with it, and none when they
    began as an approach copied in with Edit (plan_mode.rst, "Safety"). Every reader of the person's words (memory
    evidence, a worker's brief, search, a goal's objective) reads this, never ``row["text"]`` (break-it review S2)."""
    if not isinstance(row, dict):
        return None
    if row.get("from_plan") is True:   # A draft that still held what Edit copied from the plan: the model's words.
        return ""
    choice = row.get("plan_choice")
    if isinstance(choice, dict):
        return "" if choice.get("edited") else str(choice.get("notes") or "")
    return row.get("text") if isinstance(row.get("text"), str) else None


def person_request(row):
    """A row of the person's own words: a user row Angelo did not make itself (a steer included)."""
    return isinstance(row, dict) and row.get("role") == "user" and row.get("request_kind") not in HOST_REQUESTS


def task_request(row):
    """A request the chat works on: the person's own, or a worker's brief (its chat's task, from its lead)."""
    return person_request(row) or (isinstance(row, dict) and row.get("role") == "user"
                                   and row.get("request_kind") == WORKER_BRIEF)


def person_request_start(row):
    """A request of the person's own: ``person_request`` that starts a request (never a steer)."""
    return person_request(row) and not row.get("steer")


def task_request_start(row):
    """A task request starts its own handoff; steering stays with the task it changed."""
    return task_request(row) and not row.get("steer")
