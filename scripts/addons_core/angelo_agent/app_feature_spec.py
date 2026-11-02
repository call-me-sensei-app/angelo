# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_app_feature``: the agent turns an Angelo feature on or off for the person, as the + menu does. No Blender.

One deferred tool (``plan_kinds.DEFERRED_TOOLS``), so a turn that never uses it sends only its name; its exact
schema arrives with the ``FEATURES`` lookup that says how to use a feature (``feature_guide``). Every call is
approved on a card titled with what it does ("Turn on the engine live link"): it is never a bounded edit that Auto
runs alone, because turning a link on lets an engine's editor connect (doc/angelo/engine_link.rst).

Features and their actions (``ACTIONS``):

* ``ENGINE_LINK`` (the ``engine_link`` feature): ``ON`` starts the loopback listener every engine's plugin
  connects to, ``OFF`` stops it, ``ADD_TO_PROJECT`` opens a folder picker in which the person chooses the
  ``engine``'s project; Angelo copies its plugin there and turns the link on (``engine_link_ui``).
"""

TOOL = "angelo_app_feature"
ACTIONS = {"ENGINE_LINK": ("ON", "OFF", "ADD_TO_PROJECT")}   # Enum values in Blender's identifier style.
ENGINES = ("GODOT", "UNITY", "UNREAL")

APP_FEATURE_TOOLS = [{
    "name": TOOL,
    "description": (
        "Turn an Angelo feature on or off as its + menu row does; approved on a card. ENGINE_LINK: ON listens on this "
        "computer for the Angelo Link plugin of Godot, Unity and Unreal; OFF stops it; ADD_TO_PROJECT opens a folder "
        "picker for the engine's project, copies the plugin there and turns the link on."),
    "inputSchema": {"type": "object", "properties": {
        "feature": {"type": "string", "enum": sorted(ACTIONS)},
        "action": {"type": "string", "enum": sorted({action for actions in ACTIONS.values() for action in actions})},
        "engine": {"type": "string", "enum": list(ENGINES)}},
        "required": ["feature", "action"], "additionalProperties": False},
    "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
}]
APP_FEATURE_NAMES = frozenset(tool["name"] for tool in APP_FEATURE_TOOLS)


def validate_app_feature(name, args):
    """The canonical arguments; ValueError for any that is not valid."""
    if name != TOOL or not isinstance(args, dict) or set(args) - {"feature", "action", "engine"}:
        raise ValueError("Unknown app feature tool or argument")
    feature, action = args.get("feature"), args.get("action")
    if feature not in ACTIONS:
        raise ValueError("feature must be one of " + ", ".join(sorted(ACTIONS)))
    if action not in ACTIONS[feature]:
        raise ValueError("%s takes action %s" % (feature, ", ".join(ACTIONS[feature])))
    request = {"feature": feature, "action": action}
    if action == "ADD_TO_PROJECT":
        if args.get("engine") not in ENGINES:
            raise ValueError("ADD_TO_PROJECT needs engine " + ", ".join(ENGINES))
        request["engine"] = args["engine"]
    elif "engine" in args:
        raise ValueError("engine goes with ADD_TO_PROJECT only (one link serves every engine)")
    return request
