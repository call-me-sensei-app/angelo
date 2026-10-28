# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The web export tools: web-ready glTF for Three.js, its checks and the live browser preview. No Blender dependency.

Three deferred tools (``plan_kinds.DEFERRED_TOOLS``), so a turn that never exports for the web sends only their
names. ``angelo_web_check`` is read-only and runs without a card; ``angelo_web_export`` writes a file into the
workspace and ``angelo_web_preview`` starts a local server and opens the person's browser, so each is approved.
The behaviour is in doc/angelo/web_export.rst; the host side is ``web_host``.
"""

MESHES = ("AUTO", "MESHOPT", "DRACO", "NONE")
TEXTURES = ("AUTO", "WEBP", "KTX2", "KEEP")
PREVIEW_ACTIONS = ("START", "STOP")
MAX_OUTPUT = 1024
DEFAULT_OUTPUT = "web/scene.glb"
CHECK, EXPORT, PREVIEW = "angelo_web_check", "angelo_web_export", "angelo_web_preview"

_OPTIONS = {
    "meshes": {"type": "string", "enum": list(MESHES)},
    "textures": {"type": "string", "enum": list(TEXTURES)},
    "merge": {"type": "boolean"},
    "selection_only": {"type": "boolean"},
}
_EFFECT = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
WEB_TOOLS = [
    {"name": CHECK,
     "description": (
         "Read-only: what a web export for Three.js would lose (shader nodes, lights, modifiers, particles, effects), "
         "each with its fix, and its estimated size budget (objects, triangles, textures, draw calls). The web never "
         "looks identical to Blender: say what the findings say differs."),
     "inputSchema": {"type": "object", "properties": dict(_OPTIONS), "additionalProperties": False},
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}},
    {"name": EXPORT,
     "description": (
         "Write one web-ready .glb for Three.js at output, a workspace-relative path (default " + DEFAULT_OUTPUT +
         "; a taken name gets the next free one): meshopt or Draco meshes, WebP or KTX2 textures where this build "
         "can, static objects that share materials merged into fewer draw calls (merge false keeps every object). "
         "Returns the measured budget (bytes, triangles, draw calls, textures) and the findings of angelo_web_check."),
     "inputSchema": {"type": "object", "properties": dict(_OPTIONS, output={"type": "string", "minLength": 1,
                                                                             "maxLength": MAX_OUTPUT}),
                     "additionalProperties": False},
     "annotations": dict(_EFFECT)},
    {"name": PREVIEW,
     "description": (
         "START opens the live web preview in the person's browser: a page on this computer only that shows the "
         "scene in Three.js beside what doesn't carry over, and re-exports after every scene change; it returns "
         "the URL, the budget and the findings. STOP ends it. A material's web shader (toon, rim glow, hologram or "
         "a GLSL file, set in its Web Shader panel) shows only in the browser, never in Blender's viewport: verify "
         "it with angelo_view image WEB (RENDERED, views [\"SCENE_CAMERA\"]), the page as the browser draws it, "
         "with each web shader's compile status."),
     "inputSchema": {"type": "object", "properties": {"action": {"type": "string", "enum": list(PREVIEW_ACTIONS)}},
                     "required": ["action"], "additionalProperties": False},
     "annotations": dict(_EFFECT)},
]
WEB_NAMES = frozenset(tool["name"] for tool in WEB_TOOLS)


def _options(args):
    request = {}
    for field, allowed in (("meshes", MESHES), ("textures", TEXTURES)):
        value = args.get(field, "AUTO")
        if value not in allowed:
            raise ValueError("%s must be one of %s" % (field, ", ".join(allowed)))
        request[field] = value
    for field, default in (("merge", True), ("selection_only", False)):
        value = args.get(field, default)
        if not isinstance(value, bool):
            raise ValueError(field + " must be true or false")
        request[field] = value
    return request


def _output(value):
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_OUTPUT or any(ord(c) < 32 for c in value):
        raise ValueError("output must be a printable workspace-relative path of at most %d characters" % MAX_OUTPUT)
    output = value.strip().replace("\\", "/").rstrip("/")
    if output.startswith("/") or ":" in output.split("/", 1)[0] or ".." in output.split("/"):
        raise ValueError("output must be relative to the workspace, without .. or a drive")
    return output if output.lower().endswith(".glb") else output + ".glb"


def validate_web(name, args):
    """The canonical arguments of a web tool call; ValueError for any that is not valid."""
    if name == PREVIEW:
        if args.get("action") not in PREVIEW_ACTIONS:
            raise ValueError("action must be START or STOP")
        return {"action": args["action"]}
    if name == CHECK:
        return _options(args)
    if name == EXPORT:
        return dict(_options(args), output=_output(args.get("output", DEFAULT_OUTPUT)))
    raise ValueError("Unknown web export tool")
