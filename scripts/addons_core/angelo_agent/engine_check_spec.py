# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine preflight tools: what a game engine's importer would do with this scene, and its typed fixes. No
Blender dependency.

Two deferred tools (``plan_kinds.DEFERRED_TOOLS``), so a turn that never prepares an engine export sends only their
names. ``angelo_engine_check`` is read-only and runs without a card; ``angelo_engine_fix`` changes the scene (one
Undo step) and is approved. The behaviour is in doc/angelo/engine_preflight.rst; the host side is
``engine_check_host``.
"""

from .engine_rules import FIXES, TARGETS

CHECK, FIX = "angelo_engine_check", "angelo_engine_fix"
FORMAT_CHOICES = ("AUTO", "GLB", "FBX")
MAX_FIXES = 32
MAX_SUBJECT = 256
MAX_VERSION = 24
MAX_CODE = 64

_COMMON = {
    "target": {"type": "string", "enum": list(TARGETS)},
    "format": {"type": "string", "enum": list(FORMAT_CHOICES)},
    "engine_version": {"type": "string", "minLength": 1, "maxLength": MAX_VERSION},
}
ENGINE_TOOLS = [
    {"name": CHECK,
     "description": (
         "Read-only: what a game engine's importer (GODOT, UNITY, UNREAL; WEB is Three.js) does with this scene "
         "exported its usual way (format AUTO). Per rule: what it looked for, examples found, engine source, "
         "versions and fixable: the typed fix angelo_engine_fix applies. code: one rule's findings (100 at most)."),
     "inputSchema": {"type": "object", "properties": dict(_COMMON, selection_only={"type": "boolean"},
                                                          code={"type": "string"}),
                     "required": ["target"], "additionalProperties": False},
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}},
    {"name": FIX,
     "description": (
         "Apply fixable findings of angelo_engine_check (each by code and subject) in one Undo step. The scene is "
         "checked again first and a finding gone is refused. Returns each change and the findings left."),
     "inputSchema": {"type": "object", "properties": dict(_COMMON, fixes={
         "type": "array", "minItems": 1, "maxItems": MAX_FIXES,
         "items": {"type": "object", "properties": {"code": {"type": "string", "enum": sorted(FIXES)},
                                                    "subject": {"type": "string", "maxLength": MAX_SUBJECT}},
                   "required": ["code", "subject"], "additionalProperties": False}}),
                     "required": ["target", "fixes"], "additionalProperties": False},
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}},
]
ENGINE_NAMES = frozenset(tool["name"] for tool in ENGINE_TOOLS)


def _common(args):
    target = args.get("target")
    if target not in TARGETS:
        raise ValueError("target must be one of " + ", ".join(TARGETS))
    fmt = args.get("format", "AUTO")
    if fmt not in FORMAT_CHOICES:
        raise ValueError("format must be AUTO, GLB or FBX")
    from .engine_rules import TARGET_FORMATS
    if fmt != "AUTO" and fmt not in TARGET_FORMATS[target]:
        raise ValueError("%s takes %s" % (target, " or ".join(TARGET_FORMATS[target])))
    version = args.get("engine_version")
    if version is not None and (not isinstance(version, str) or not version.strip() or len(version) > MAX_VERSION
                                or any(ord(c) < 32 for c in version)):
        raise ValueError("engine_version must be a short version such as 4.7 or 6000.5")
    request = {"target": target, "format": fmt}
    if version is not None:
        request["engine_version"] = version.strip()
    return request


def validate_engine(name, args):
    """The canonical arguments of an engine preflight call; ValueError for any that is not valid."""
    if name == CHECK:
        request = _common(args)
        selection = args.get("selection_only", False)
        if not isinstance(selection, bool):
            raise ValueError("selection_only must be true or false")
        request = dict(request, selection_only=selection)
        code = args.get("code")
        if code is not None:
            from .engine_rules import RULES
            if not isinstance(code, str) or len(code) > MAX_CODE or code not in RULES:
                raise ValueError("code must be a rule code from a finding")
            request["code"] = code
        return request
    if name == FIX:
        request = _common(args)
        fixes = args.get("fixes")
        if not isinstance(fixes, list) or not 1 <= len(fixes) <= MAX_FIXES:
            raise ValueError("fixes must list 1 to %d findings" % MAX_FIXES)
        chosen = []
        for item in fixes:
            if not isinstance(item, dict) or set(item) - {"code", "subject"}:
                raise ValueError("each fix is {code, subject}")
            code, subject = item.get("code"), item.get("subject")
            if code not in FIXES:
                raise ValueError("%s has no typed fix" % code)
            if not isinstance(subject, str) or len(subject) > MAX_SUBJECT:
                raise ValueError("subject must be the finding's subject")
            if (code, subject) not in chosen:
                chosen.append((code, subject))
        return dict(request, fixes=[{"code": code, "subject": subject} for code, subject in chosen])
    raise ValueError("Unknown engine preflight tool")
