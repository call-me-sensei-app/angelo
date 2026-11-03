# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The agent's tools for an engine live link the person has turned on. No Blender dependency.

Two deferred tools (``plan_kinds.DEFERRED_TOOLS``), so a turn that never touches an engine sends only their names:

* ``angelo_engine_link_status`` (read-only, no card): whether the link is on, which engines run, which editors are
  connected, the linked asset, what was last pushed live and saved, and what each editor acknowledged.
* ``angelo_engine_link`` (approved on a card, never in Auto): ``PUSH`` sends the live update now, ``SAVE`` saves the
  linked asset as the person's File > Save does (the durable write-back and the editors' re-import), ``CLOSE``
  ends the link and keeps the file open. ``OPEN`` raises the request card an engine's Edit in Angelo raises, for an
  asset named the way its engine names it; the person answers it there (``asks_only``: that card is the approval,
  so there is no second one) and nothing opens inside the tool.

Neither ever turns the link on: that stays the person's (``angelo_app_feature`` ENGINE_LINK, on its own card).
Results are compact English facts, never a path (doc/angelo/engine_link.rst, "The agent").
"""

STATUS, EDIT = "angelo_engine_link_status", "angelo_engine_link"
ACTIONS = ("OPEN", "PUSH", "SAVE", "CLOSE")   # Enum values in Blender's identifier style.
ENGINES = ("GODOT", "UNITY", "UNREAL")
MAX_ASSET, MAX_PROJECT = 1024, 200

ENGINE_LINK_TOOLS = [
    {"name": STATUS,
     "description": (
         "Read-only: the game engine live link the person turned on: engines running, connected editors, the linked "
         "asset, what was last pushed live and saved (and when), and each editor's acknowledgement (previewed, "
         "reimported). Never turns the link on."),
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}},
    {"name": EDIT,
     "description": (
         "Act on the engine link the person turned on; never turns it on. OPEN asks the person, on the card an "
         "engine's Edit in Angelo shows, to open asset (as the engine names it, res://... or Assets/..., or its file "
         "name) from a connected editor; engine and project pick one when several match. PUSH, SAVE and CLOSE act "
         "on the linked asset, approved on a card: PUSH sends the live update now; SAVE saves it for the engine to "
         "re-import; CLOSE ends the link, the file stays open. PUSH and SAVE wait briefly for the editors' acknowledgement."),
     "inputSchema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": list(ACTIONS)},
         "asset": {"type": "string", "minLength": 1, "maxLength": MAX_ASSET},
         "engine": {"type": "string", "enum": list(ENGINES)},
         "project": {"type": "string", "minLength": 1, "maxLength": MAX_PROJECT}},
         "required": ["action"], "additionalProperties": False},
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}},
]
ENGINE_LINK_NAMES = frozenset(tool["name"] for tool in ENGINE_LINK_TOOLS)


def validate_engine_link(name, args):
    """The canonical arguments; ValueError for any that is not valid."""
    if not isinstance(args, dict):
        raise ValueError("Tool arguments must be an object")
    if name == STATUS:
        if args:
            raise ValueError("angelo_engine_link_status takes no arguments")
        return {}
    if name != EDIT or set(args) - {"action", "asset", "engine", "project"}:
        raise ValueError("Unknown engine link tool or argument")
    action = args.get("action")
    if action not in ACTIONS:
        raise ValueError("action must be one of " + ", ".join(ACTIONS))
    if action != "OPEN":
        if set(args) - {"action"}:
            raise ValueError("asset, engine and project go with OPEN only; %s acts on the linked asset" % action)
        return {"action": action}
    request = {"action": action, "asset": _text(args.get("asset"), "asset", MAX_ASSET)}
    if "engine" in args:
        if args["engine"] not in ENGINES:
            raise ValueError("engine must be one of " + ", ".join(ENGINES))
        request["engine"] = args["engine"]
    if "project" in args:
        request["project"] = _text(args["project"], "project", MAX_PROJECT)
    return request


def _text(value, field, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError("%s must be 1-%d printable characters" % (field, limit))
    return value.strip()


def asks_only(name, args):
    """Whether this call only raises a card the person answers (OPEN): that card is its approval, so it runs without
    a second one. Read-only chats still refuse it, as every other edit."""
    return name == EDIT and isinstance(args, dict) and args.get("action") == "OPEN"
