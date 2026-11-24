# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_plan``: the model's way into plan mode and its plan (doc/angelo/plan_mode.rst, "The tool").

Schema and validation only; ``planning_controls`` executes it. One deferred tool on both runtimes, small enough for
Codex code mode's declaration budget (about 2,000 characters, gpt_launch.rst). Two actions: ``START`` (the chat
becomes read only and plans: it only narrows) and ``PROPOSE`` (the plan the person chooses from). Nothing ends planning
or presses Go: that is the person's press on the card (``planning_controls.go``). The description states what the
tool does and when the person has asked for it, never a workflow.
"""

from . import hidden_text, read_only

TOOL_NAME = "angelo_plan"
START, PROPOSE = "START", "PROPOSE"
ACTIONS = (START, PROPOSE)
FIELDS = {START: frozenset(), PROPOSE: frozenset({"summary", "approaches", "recommended", "questions"})}
APPROACH_FIELDS = frozenset({"title", "steps", "risks", "estimate"})
LIMITS = {"summary": 600, "title": 80, "step": 240, "risk": 240, "estimate": 80, "question": 240,
          "approaches": 3, "steps": 12, "risks": 6, "questions": 5}
# Text the person cannot see on the card never reaches the model through a plan: hidden_text decides by Unicode
# property, with the contexts real text needs (Thai word breaks, joiners, bidi marks, emoji selectors). A summary may
# keep its line breaks; every other text is one line.

REQUIRED = frozenset({"summary", "approaches", "recommended"})


def fields_by_action():
    """The declaration and validator share this action-specific field contract (test_native_tool_kind_fields)."""
    order = ("summary", "approaches", "recommended", "questions")
    entries = [action + ": " + (" ".join(field + ("*" if field in REQUIRED else "") for field in order
                                         if field in FIELDS[action]) or "no other field") for action in ACTIONS]
    return "Fields by action (* required; any other is refused): " + "; ".join(entries) + "."


_TEXT = {"type": "string"}
_LIST = {"type": "array", "items": _TEXT}

PLAN_TOOLS = [{
    "name": TOOL_NAME,
    "description": (
        "Plan mode, only when the person wants to plan, research or discuss approaches before building. START makes "
        "this chat read only now: research and inspect (web, scene, views), ask in chat if needed. PROPOSE ends the "
        "planning turn: 1-3 approaches with steps, risks and an estimate, the recommended one's index, open "
        "questions. Angelo shows it as a card; the person's Go on it switches to building and sends the chosen "
        "approach. PROPOSE needs plan mode. Limits: summary 600 chars, title 80, estimate 80, each step, risk and "
        "question 240; at most 12 steps, 6 risks, 5 questions. " + fields_by_action()),
    "inputSchema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "summary": _TEXT,
        "approaches": {"type": "array", "items": {"type": "object", "properties": {
            "title": _TEXT, "steps": _LIST, "risks": _LIST, "estimate": _TEXT},
            "required": ["title", "steps", "estimate"]}},
        "recommended": {"type": "integer"},
        "questions": _LIST},
        "required": ["action"], "additionalProperties": False},
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
}]
PLAN_NAMES = frozenset({TOOL_NAME})


def _text(value, field, limit, lines=False):
    if type(value) is not str or len(value) > limit or not value.strip():
        raise ValueError("%s is text of 1-%d characters" % (field, limit))
    found = hidden_text.find(value, newlines=lines)
    if found is not None:
        raise ValueError("%s holds a character a person cannot see (%s at %d)" % (field, hidden_text.name(value[found]),
                                                                                   found))
    return value


def _texts(value, field, item, most, least=0):
    if value is None and least == 0:
        return []
    if type(value) is not list or not least <= len(value) <= most:
        raise ValueError("%s is a list of %d-%d texts" % (field, least, most))
    return [_text(entry, "%s[%d]" % (field, index), LIMITS[item]) for index, entry in enumerate(value)]


def _approach(value, index):
    field = "approaches[%d]" % index
    if type(value) is not dict:
        raise ValueError("%s is {title, steps, risks, estimate}" % field)
    extra = sorted(set(value) - APPROACH_FIELDS, key=str)
    if extra:
        raise ValueError("%s takes no %s" % (field, str(extra[0])[:40]))
    return {"title": _text(value.get("title"), field + ".title", LIMITS["title"]),
            "steps": _texts(value.get("steps"), field + ".steps", "step", LIMITS["steps"], 1),
            "risks": _texts(value.get("risks"), field + ".risks", "risk", LIMITS["risks"]),
            "estimate": _text(value.get("estimate"), field + ".estimate", LIMITS["estimate"])}


def validate_plan(name, args):
    """The canonical arguments of an ``angelo_plan`` call; ValueError naming the field for anything else."""
    if name != TOOL_NAME or type(args) is not dict or args.get("action") not in ACTIONS:
        raise ValueError("angelo_plan takes an action: START or PROPOSE")
    action = args["action"]
    extra = sorted(set(args) - FIELDS[action] - {"action"}, key=str)
    if extra:
        raise ValueError("%s takes no %s" % (action, str(extra[0])[:40]))
    if action == START:
        return {"action": START}
    approaches = args.get("approaches")
    if type(approaches) is not list or not 1 <= len(approaches) <= LIMITS["approaches"]:
        raise ValueError("approaches is a list of 1-%d approaches" % LIMITS["approaches"])
    recommended = args.get("recommended")
    if type(recommended) is not int or not 0 <= recommended < len(approaches):
        raise ValueError("recommended is the index (from 0) of one of the approaches")
    return {"action": PROPOSE, "summary": _text(args.get("summary"), "summary", LIMITS["summary"], lines=True),
            "approaches": [_approach(item, index) for index, item in enumerate(approaches)],
            "recommended": recommended,
            "questions": _texts(args.get("questions"), "questions", "question", LIMITS["questions"])}


# The validator reads these as registered, read only (read_only.seal; the tool's schema keeps the originals).
read_only.seal(globals(), "FIELDS", "LIMITS", "APPROACH_FIELDS", "REQUIRED")
