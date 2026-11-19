# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The launcher's visibility tools: views from any angle, the whole window, the window's state and the conversation's
usage (``angelo_look``, ``angelo_window``, ``angelo_state``, ``angelo_usage``). Each is one ``agent`` command to
Angelo's window (``external_visibility``); all read, cost no model call and leave nothing in the file. Pure standard
library; doc/angelo/external_mcp.rst, "Looks", "State" and "Usage".
"""

import json

_READ = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
_ANGLE = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}
_POINT = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
VIEW = {"description": (
    "FRONT, BACK, LEFT, RIGHT, TOP, BOTTOM, ISO (orthographic, fitted to the objects), perspective (a 3/4 view through "
    "a 50 mm lens), FIT (the person's viewport direction), SCENE_CAMERA (the scene's active camera); or "
    "{\"orbit\": [azimuth, elevation]} in degrees around the objects (azimuth 0 looks from the front, 90 from the "
    "right; elevation 90 straight down; optional distance in scene units); {\"camera\": name} for any scene camera; "
    "{\"location\": [x, y, z], \"look_at\": [x, y, z], \"lens\"?} for any other camera."),
    "anyOf": [{"type": "string"},
              {"type": "object", "properties": {"orbit": _ANGLE, "distance": {"type": "number"}},
               "required": ["orbit"], "additionalProperties": False},
              {"type": "object", "properties": {"camera": {"type": "string"}}, "required": ["camera"],
               "additionalProperties": False},
              {"type": "object", "properties": {"location": _POINT, "look_at": _POINT, "lens": {"type": "number"}},
               "required": ["location", "look_at"], "additionalProperties": False}]}

VISIBILITY_TOOLS = [
    {"name": "angelo_look", "title": "Look at the scene from any angle",
     "description": (
         "Pictures of the scene as it is now, from up to 3 cameras, framed on the objects you name (default: "
         "everything visible), as Angelo's agent's own views are drawn. Read-only: no camera is made and nothing in "
         "the file changes; no model call. SOLID (default), WIREFRAME, MATERIAL (a built-in light) or LIT (the "
         "scene's own lights and world). For a final render, ask Angelo's agent with angelo_send. Each picture's id "
         "works with angelo_image; this app's newest 8 pictures stay readable. busy, saying why, while playback "
         "runs, while the person uses a tool or navigates, or while a cold MATERIAL or LIT look's shaders compile "
         "(Angelo prepares them in the background at most once a minute per scene): look again shortly."),
     "inputSchema": {"type": "object", "properties": {
         "views": {"type": "array", "minItems": 1, "maxItems": 3, "items": VIEW},
         "objects": {"type": "array", "maxItems": 16, "items": {"type": "string"},
                     "description": "Object names as the person sees them in Angelo, to frame on."},
         "resolution": {"type": "integer", "enum": [256, 384, 512, 768, 1024], "default": 512},
         "shading": {"type": "string", "enum": ["SOLID", "WIREFRAME", "MATERIAL", "LIT"], "default": "SOLID"}},
         "additionalProperties": False},
     "annotations": _READ},
    {"name": "angelo_window", "title": "Angelo's window layout",
     "description": ("A window-sized picture of Angelo's layout, every editor a flat grey outlined box (no interface "
                     "pixels), and layout: each editor's type and box. In this version the 3D viewports are grey too: "
                     "get their scene with angelo_viewport (the person's viewport direction) or angelo_look (any "
                     "angle). Read-only, no model call."),
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "annotations": _READ},
    {"name": "angelo_state", "title": "What Angelo's window is doing",
     "description": ("The window's mode, active tool and its key settings, selection (with edit-mode counts and "
                     "bounds), workspace and editors, viewport shading, overlays and view, scene, view layer, frame, "
                     "range and playback, active camera, render engine and output, running jobs with progress, recent "
                     "Undo steps and unsaved changes; and this conversation's model, effort, approval, consents, goal, "
                     "pending approval (information only), connection type and Angelo's version. Never a file path."),
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "annotations": _READ},
    {"name": "angelo_usage", "title": "What this conversation has spent",
     "description": ("Tokens (new input, cache read, cache write, output) and the estimate at list price for each of "
                     "the conversation's turns, each of your tasks and the conversation; the goal's budget, spend and "
                     "time; the subscription's usage windows where its runtime reported them. billing says how to read "
                     "the estimate (on a subscription it is not a charge)."),
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "annotations": _READ},
]
VISIBILITY_NAMES = frozenset(tool["name"] for tool in VISIBILITY_TOOLS)
ACTIONS = {"angelo_look": "look", "angelo_window": "window", "angelo_state": "state", "angelo_usage": "usage"}


def _failure(envelope):
    result = envelope.get("result") if isinstance(envelope.get("result"), dict) else {}
    value = dict(result, code=str(result.get("reason") or envelope.get("code") or "unavailable"),
                 message=str(envelope.get("message") or envelope.get("code") or "unavailable"))
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, separators=(",", ":"))}],
            "structuredContent": value, "isError": True}


def result(envelope):
    """A visibility command's MCP result: pictures as image content beside their ids, other answers as JSON."""
    if envelope.get("code") != "ok":
        return _failure(envelope)
    value = dict(envelope.get("result") or {})
    pictures = value.pop("images", None)
    content = [{"type": "image", "data": p.get("data", ""), "mimeType": p.get("mimeType", "image/png")}
               for p in pictures or ()]
    if pictures is not None:
        value["sent"] = [p.get("id") for p in pictures]   # The rest by id, with angelo_image.
    content.append({"type": "text", "text": json.dumps(value, ensure_ascii=False, separators=(",", ":"))})
    return {"content": content, "structuredContent": value, "isError": False}


def call(tools, name, arguments):
    """One visibility tool as an ``agent`` command (``tools``: ``external_mcp_agent.AgentTools``)."""
    args = {"action": ACTIONS[name]}
    if name == "angelo_look":
        args["look"] = {key: arguments[key] for key in ("views", "objects", "resolution", "shading")
                        if key in arguments}
    return result(tools.agent_request(args))
