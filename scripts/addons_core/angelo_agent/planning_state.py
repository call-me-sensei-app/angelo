# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Plan mode read from the chat's history (doc/angelo/plan_mode.rst, "Contract"). No bpy.

A plan is its ``angelo_plan`` ``PROPOSE`` call's activity row (the validated plan is in its result); the person's
choice is their request row's ``plan_choice``. Everything the card, the chips and Go need is derived from those rows,
so it is saved with the chat and reads the same after a restart: nothing here is held anywhere else.

* ``plans`` / ``card_state`` / ``live_plan``: which plan a card draws and whether its buttons are live.
* ``go_text`` / ``choice_fields`` / ``kept_choice``: Go's request (model-facing, English, facts only) and its row.
* ``chosen``: the newest plan the person went with, for the composer's plan chip.
"""

from .planning_spec import TOOL_NAME

SHOWN = "SHOWN"
LIVE, CHOSEN, SUPERSEDED, ENDED = "live", "chosen", "superseded", "ended"
APPROVAL_NAMES = {"ASK": "Ask before changes", "AUTO_BOUNDED": "Auto · bounded edits"}   # Model-facing (English).
MAX_NOTES = 8000   # The person's edits and notes sent with Go (the message box holds at most 32000 characters).


def plan_of(message):
    """The validated plan a row holds, or None: an ``angelo_plan`` activity row whose result was shown."""
    if not isinstance(message, dict) or message.get("role") != "activity" or message.get("tool") != TOOL_NAME:
        return None
    result = message.get("result")
    plan = result.get("plan") if isinstance(result, dict) and result.get("status") == SHOWN else None
    return plan if isinstance(plan, dict) and isinstance(plan.get("approaches"), list) and plan["approaches"] else None


def plans(messages):
    """``[(index, call_id, plan)]`` of every plan in the chat, oldest first."""
    return [(index, message.get("call_id"), plan) for index, message in enumerate(messages or ())
            if (plan := plan_of(message)) is not None]


def choice_of(message):
    """A request row's ``plan_choice``, or None: only the person's own row carries one (``kept_choice``)."""
    if not isinstance(message, dict) or message.get("role") != "user" or message.get("request_kind"):
        return None
    choice = message.get("plan_choice")
    return choice if isinstance(choice, dict) and isinstance(choice.get("call_id"), str) else None


def card_state(messages, call_id, planning):
    """``(state, approach)`` of the card under plan ``call_id``: ``CHOSEN`` with the approach the person went with,
    ``SUPERSEDED`` when a newer plan exists, ``LIVE`` for the newest plan while the chat plans, else ``ENDED``."""
    found, newer = False, False
    for message in messages or ():
        choice = choice_of(message)
        if choice is not None and choice["call_id"] == call_id:
            return CHOSEN, choice.get("approach")
        if plan_of(message) is not None:
            if message.get("call_id") == call_id:
                found = True
            elif found:
                newer = True
    if not found:
        return ENDED, None
    if newer:
        return SUPERSEDED, None
    return (LIVE if planning else ENDED), None


def live_plan(messages, planning):
    """``(call_id, plan)`` of the plan whose Go is live, or ``(None, None)``."""
    found = plans(messages)
    if not found:
        return None, None
    _index, call_id, plan = found[-1]
    return (call_id, plan) if card_state(messages, call_id, planning)[0] == LIVE else (None, None)


def chosen(messages):
    """``(call_id, title)`` of the newest plan the person went with, while no later plan was proposed, else None."""
    last = None
    for message in messages or ():
        choice = choice_of(message)
        if choice is not None:
            last = (choice["call_id"], str(choice.get("title") or ""))
        elif plan_of(message) is not None:
            last = None
    return last


def _quoted(text):
    """Data quoted for the model: the plan's text is the model's own, the notes the person's; neither is a
    host instruction, so both go inside one JSON string each."""
    import json
    return json.dumps(text, ensure_ascii=False)


def cancelled_sentence(jobs):
    """What the agent reads of the jobs planning cancelled (re-read NIT 2): their IDs, to queue again if still wanted."""
    return ("Planning cancelled this chat's queued background jobs %s; queue them again after the person's Go if the "
            "chosen approach still needs them." % ", ".join(jobs))


def go_text(call_id, index, plan, approval, notes="", edited=False, cancelled=()):
    """Go's request, as the model reads it (English, never translated: localization.rst)."""
    approach = plan["approaches"][index]
    lines = ["The person chose approach %d of the plan (angelo_plan call %s) and pressed Go; approvals are now %s. "
             "Build it." % (index + 1, call_id, APPROVAL_NAMES[approval]),
             "Approach: " + _quoted(approach["title"]), "Steps:"]
    lines += ["%d. %s" % (number, _quoted(step)) for number, step in enumerate(approach["steps"], 1)]
    if approach.get("risks"):
        lines.append("Risks: " + "; ".join(_quoted(risk) for risk in approach["risks"]))
    lines.append("Estimate: " + _quoted(approach["estimate"]))
    notes = (notes or "").strip()[:MAX_NOTES]
    label = ("The person's edits (an approach of this plan copied into their message box with Edit, then changed) and "
             "notes: " if edited and notes else "The person's edits and notes (their words): ")
    lines.append(label + (_quoted(notes) if notes else "none"))
    if cancelled:
        lines.append(cancelled_sentence(cancelled))
    return "\n".join(lines)


def choice_fields(call_id, index, plan, approval, notes="", edited=False):
    """The ``plan_choice`` Go's request row carries (``request_rows.request_fields``): what the row draws in the
    person's language (``planning_text.row_display``), while its ``text`` is ``go_text``."""
    return {"call_id": call_id, "approach": index, "title": plan["approaches"][index]["title"],
            "approval": approval, "notes": (notes or "").strip()[:MAX_NOTES], "edited": bool(edited and notes)}


def choice_for_text(messages, text):
    """The ``plan_choice`` of the newest Go row whose text is exactly ``text``, or None: a Go sent again (Resend after a
    failed turn) carries its provenance with its text (break-it review N2)."""
    if not isinstance(text, str) or not text:
        return None
    for message in reversed(list(messages or ())):
        choice = choice_of(message)
        if choice is not None and message.get("text") == text:
            return choice
    return None


MAX_MARK_LINES = 13   # An approach's title and its 12 steps.


def edit_mark(call_id, plan, index):
    """The mark Edit leaves on the chat's draft (``options.plan_draft``, saved with the chat): which plan it copied,
    and the lines it copied, so a send or a Go can tell whether the draft still holds them (N2b)."""
    approach = plan["approaches"][index]
    return {"call_id": str(call_id)[:200], "lines": [approach["title"]] + list(approach["steps"])}


def clean_mark(value):
    """A stored ``plan_draft`` as it is kept, or None (``chat_settings.stored_options``)."""
    if not isinstance(value, dict) or not isinstance(value.get("call_id"), str) or not isinstance(value.get("lines"), list):
        return None
    lines = [line for line in value["lines"][:MAX_MARK_LINES] if isinstance(line, str) and line.strip()]
    return {"call_id": value["call_id"][:200], "lines": [line[:240] for line in lines]} if lines else None


def marked(mark, text):
    """Whether ``text`` still holds a line Edit copied (``mark``): an emptied and retyped draft does not."""
    mark = clean_mark(mark)
    return bool(mark) and isinstance(text, str) and any(line.strip() in text for line in mark["lines"])


def draft_for(messages, text):
    """``(text, mark)`` to put back where the person types for a request's ``text``: a Go's own text is the model's
    plan, so only the person's notes go back, marked when they began with Edit (N2: Put message back, an unsent Go)."""
    choice = choice_for_text(messages, text)
    if choice is None:
        return text, None
    notes = str(choice.get("notes") or "")
    if not choice.get("edited"):
        return notes, None
    found = next((plan for _index, call_id, plan in plans(messages) if call_id == choice["call_id"]), None)
    index = choice.get("approach")
    valid = found is not None and type(index) is int and 0 <= index < len(found["approaches"])
    return notes, (edit_mark(choice["call_id"], found, index) if valid else {"call_id": choice["call_id"],
                                                                                "lines": [notes[:240]]})


def goal_objective(choice, text):
    """A chat-bar goal's objective when its message is sent: the person's words. A Go on a plan card (``choice``,
    ``choice_fields``) sends the model's plan as its text, so the objective is the person's notes, or the approach
    they chose by its title (break-it review S2)."""
    if not isinstance(choice, dict):
        return text
    from .request_rows import persons_text
    notes = persons_text({"role": "user", "plan_choice": choice})
    return notes or "Go with approach %d: %s" % (int(choice.get("approach", 0)) + 1, choice.get("title", ""))


def sent_objective(controller, text):
    """``goal_objective`` of the send under way in ``controller``'s chat: its Go's choice, if a plan card sent it."""
    return goal_objective(getattr(controller, "_plan_choice", None), text)


def kept_choice(value):
    """A stored row's ``plan_choice`` as it is kept (``request_rows.kept_fields``), or None."""
    if not isinstance(value, dict) or not isinstance(value.get("call_id"), str):
        return None
    if type(value.get("approach")) is not int or value.get("approval") not in APPROVAL_NAMES:
        return None
    return {"call_id": value["call_id"][:200], "approach": value["approach"], "title": str(value.get("title") or "")[:80],
            "approval": value["approval"], "notes": str(value.get("notes") or "")[:MAX_NOTES],
            "edited": value.get("edited") is True}
