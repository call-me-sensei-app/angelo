# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The program reference a Codex thread reads in its instructions, as TypeScript types.

Codex 0.160 runs the GPT-6 and GPT-5.6 models in *code mode* (the model
catalog's ``tool_mode: code_mode_only``): the model sees one ``exec`` tool whose
description declares every dynamic tool as a TypeScript signature, and calls
Angelo's tools from JavaScript (``await tools.angelo_scene_program({...})``).
Codex renders each declaration within a budget of about 2,000 characters; past
it, every union in the declaration becomes ``unknown`` (measured with
``tests/python/angelo_agent/codex_prompt_probe.py``, doc/angelo/gpt_launch.rst).
``angelo_scene_program`` and ``angelo_scene_verify`` exceed it, so without this
reference the model saw ``operations: Array<unknown | ...>`` and no check
shapes at all.

This module renders the partition a Claude typed plan uses (``plan_schema``:
the core and everyday action kinds and the core checks exactly, the others by
name, read on demand with ``angelo_agent_capabilities``) as compact TypeScript,
once, in the thread's byte-stable instructions. TypeScript is about a fifth of
the JSON Schema, so the reference also states ``create_object``, the ARRAY
modifier, the scene settings requests name most and the job a render starts:
each cost a lookup round in recorded sessions. It moves only where the model reads
the shapes: ``tool_specs.validate_call`` still validates every call exactly
against the original schemas, so nothing becomes usable that was not before.
The renderer is deterministic (same schemas, same bytes), so the instructions
stay a cache hit across turns and threads.
"""

import json
import re

from .identifiers import model_facing
from .action_camera_light_spec import CAMERA_SETTINGS, LIGHT_SETTINGS, ORBIT_REFERENCE_NOTE
from .action_schema_types import POINTER
from .action_schemas import BEND_NOTE, FACE_SELECTOR, MODIFIER_SETTINGS, OPERATION_SCHEMAS
from .batch_spec import PLACEMENT_NOTE
from .plan_schema import (CODEX_REFERENCE_ACTIONS, CODEX_REFERENCE_MODIFIERS, DEFERRED_CHECKS,
                          DEFERRED_MODIFIER_CHECKS, MODIFIER_KINDS, PLAN_CORE_ACTIONS, PLAN_CORE_MODIFIERS,
                          _checks_items, _operation_items, model_schema)

_MAX_TUPLE = 4
_IDENTIFIER = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*\Z")


def _literal(value):
    return json.dumps(value, ensure_ascii=False)


def _object_parts(schema, definitions, aliases):
    """``[(name, rendered, optional)]`` of an object schema's properties, in order."""
    required = set(schema.get("required", ()))
    return [(name, _typescript(child, definitions, aliases), name not in required)
            for name, child in (schema.get("properties") or {}).items()]


def _key(name):
    return name if _IDENTIFIER.match(name) else _literal(name)


def _render_object(parts, open_ended=False):
    fields = "".join("%s%s:%s;" % (_key(name), "?" if optional else "", rendered) for name, rendered, optional in parts)
    if open_ended:
        fields += "[k:string]:%s;" % (open_ended if isinstance(open_ended, str) else "unknown")
    return "{" + fields + "}"


def _is_object(schema):
    return isinstance(schema, dict) and (schema.get("type") == "object" or "properties" in schema)


def _union(members, definitions, aliases):
    members = [_resolve(member, definitions) for member in members]
    if len(members) > 2 and members[-1] == {"type": "null"}:
        # A nullable union reads as its non-null part (which may have an alias, such as Obj) or null.
        return _typescript({"oneOf": members[:-1]}, definitions, aliases) + "|null"
    if len(members) > 1 and all(_is_object(member) for member in members):
        # Object variants sharing fields (a primitive's location, rotation...) are
        # written as Common & (A | B), so each shared field appears once.
        parts = [_object_parts(member, definitions, aliases) for member in members]
        common = [part for part in parts[0] if all(part in other for other in parts[1:])]
        if common:
            rest = [_render_object([part for part in member if part not in common]) for member in parts]
            return _render_object(common) + "&(" + "|".join(dict.fromkeys(rest)) + ")"
    rendered = [_typescript(member, definitions, aliases) for member in members]
    return "|".join(dict.fromkeys(rendered))


def _resolve(schema, definitions):
    reference = schema.get("$ref") if isinstance(schema, dict) else None
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        return definitions.get(reference[len("#/$defs/"):], {})
    return schema


def typescript(schema, definitions=None):
    """A compact TypeScript type for a JSON Schema (the subset Angelo's tool schemas use).

    Unions, enums, consts, nullable types, objects (optional fields marked
    ``?``), arrays and short fixed-length arrays as tuples. Bounds, patterns and
    defaults are left to the host's validator, as in Codex's own declarations.
    """
    if definitions is None:
        definitions = schema.get("$defs", {}) if isinstance(schema, dict) else {}
    return _typescript(schema, definitions, None)


def _typescript(schema, definitions, aliases):
    rendered = _render_type(_resolve(schema, definitions), definitions, aliases)
    # Compare a whole rendered type node, never arbitrary text or property names.
    # These aliases belong only to the program reference; the public renderer
    # retains its ordinary output when no local declarations accompany it.
    return aliases.get(rendered, rendered) if aliases else rendered


def _render_type(schema, definitions, aliases):
    if schema is True or schema == {}:
        return "unknown"
    if not isinstance(schema, dict):
        return "unknown"
    if "const" in schema:
        return _literal(schema["const"])
    if "enum" in schema:
        return "|".join(_literal(value) for value in schema["enum"])
    for key in ("oneOf", "anyOf"):
        if key in schema:
            return _union(schema[key], definitions, aliases)
    kind = schema.get("type")
    if isinstance(kind, list):
        return "|".join(_typescript(dict(schema, type=item), definitions, aliases) for item in kind)
    if kind == "object" or "properties" in schema:
        extra = schema.get("additionalProperties", True)
        open_ended = extra is not False and not schema.get("properties")
        if isinstance(extra, dict) and extra and schema.get("properties"):
            # A map with a few typed keys (property_set's values): the other keys take the map's value type.
            open_ended = _typescript(extra, definitions, aliases)
        return _render_object(_object_parts(schema, definitions, aliases), open_ended)
    if kind == "array":
        item = _typescript(schema.get("items", True), definitions, aliases)
        count = schema.get("minItems")
        if count is not None and count == schema.get("maxItems") and 1 <= count <= _MAX_TUPLE:
            return "[" + ",".join([item] * count) + "]"
        return ("(%s)[]" if "|" in item or "&" in item else "%s[]") % item
    if kind == "integer":
        return "number"
    if kind in ("string", "number", "boolean", "null"):
        return kind
    return "unknown"


def _reference_aliases():
    """Declare exact repeated types locally, in dependency order.

    Bounds/defaults stay in their schema owners and the host validator. The
    registry matches the same complete type nodes that ``typescript`` renders.
    """
    schemas = [("Obj", POINTER),  # An object pointer: handle, $alias or {object: handle} (action_schema_types).
               ("Vec3", {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}),
               ("RGBA", {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4}),
               ("FaceSelector", FACE_SELECTOR["oneOf"][0]),
               ("SubdivisionSettings", MODIFIER_SETTINGS["SUBSURF"]),
               ("MirrorSettings", MODIFIER_SETTINGS["MIRROR"]),
               ("CameraSettings", CAMERA_SETTINGS), ("LightSettings", LIGHT_SETTINGS)]
    aliases, lines = {}, []
    for name, schema in schemas:
        body = _typescript(model_schema(schema), {}, aliases)
        aliases[body] = name
        lines.append("type " + name + "=" + body + ";")
    return aliases, lines


# The scene settings requests name (render, sampling, colour, frames, camera); validated against the allow-list.
SCENE_PATHS = ("render.engine", "render.resolution_x", "render.resolution_y", "render.resolution_percentage",
               "render.fps", "render.film_transparent", "render.use_motion_blur", "eevee.taa_render_samples",
               "eevee.use_raytracing", "cycles.samples", "cycles.use_denoising", "view_settings.view_transform",
               "frame_start", "frame_end", "camera")
JOB_TOOL = "angelo_job_start"


def _deferred_lines():
    checks = list(DEFERRED_CHECKS) + ["modifier:" + kind for kind in DEFERRED_MODIFIER_CHECKS]
    actions = [kind for kind in OPERATION_SCHEMAS if kind not in CODEX_REFERENCE_ACTIONS]
    return ("Other action kinds (exact schemas from angelo_agent_capabilities {family:\"ACTIONS\",names:[...]}): "
            + ", ".join(actions) + ".\nOther check kinds ({family:\"CHECKS\"}): " + ", ".join(checks) + ".")


def _scene_lines(aliases=None):
    """Scene settings by path, and the job tool's arguments when this build has jobs (job_spec)."""
    from .tool_specs import TOOL_MAP
    lines = ["Scene settings are property_set values with no target: " + ", ".join(SCENE_PATHS).replace(
        "render.engine", 'render.engine ("BLENDER_EEVEE", "CYCLES")', 1)
             + " (a camera handle or $alias); others: angelo_scene_settings."]
    if JOB_TOOL in TOOL_MAP:
        schema = model_schema(TOOL_MAP[JOB_TOOL]["inputSchema"])
        # The job family's output rule (job_spec, JOB_GUIDANCE): never overwritten, a taken name gets the next free one.
        lines.append("%s arguments (output: a workspace-relative path; a taken name gets the next free one, which the "
                     "result names; it runs after your turn): %s."
                     % (JOB_TOOL, _typescript(schema, schema.get("$defs", {}), aliases)))
    return lines


def _used_declarations(declarations, body):
    """The alias declarations ``body`` uses, directly or through a later kept alias; order kept."""
    text, kept = "\n".join(body), []
    for line in reversed(declarations):
        name = line[len("type "):line.index("=")]
        if re.search(r"\b%s\b" % re.escape(name), text):
            kept.insert(0, line)
            text += "\n" + line
    return kept


def response_format_kinds():
    """The reference's action kinds a plan thread's response format states exactly, as completely as the reference.

    The modifier kinds are left out while the reference names more modifier
    types exactly (ARRAY) than the response format does.
    """
    wider = set(CODEX_REFERENCE_MODIFIERS) != set(PLAN_CORE_MODIFIERS)
    return tuple(kind for kind in CODEX_REFERENCE_ACTIONS
                 if kind in PLAN_CORE_ACTIONS and not (wider and kind in MODIFIER_KINDS))


def program_reference(short_identifiers=True, plan=False):
    """The instructions block: core operations and checks as TypeScript, the rest by name.

    ``short_identifiers``: the model-facing projection a short-identifier
    context reads (inferred bindings optional), as in Claude's plan schema.
    ``plan``: a typed-plan thread, whose strict response format
    (``codex_plan.output_schema``) already states the plan's core action kinds
    and every core check exactly. The reference then names those instead of
    stating them a second time in every request (doc/angelo/performance.rst,
    "Fixed opening of a new chat"), and declares only the aliases it uses.
    """
    project = (lambda schema: model_schema(model_facing(schema))) if short_identifiers else model_schema
    stated = response_format_kinds() if plan else ()
    items = _operation_items(CODEX_REFERENCE_ACTIONS, CODEX_REFERENCE_MODIFIERS)
    operations = [variant for variant in project(items)["oneOf"]
                  if variant["properties"]["kind"].get("const") in CODEX_REFERENCE_ACTIONS
                  and variant["properties"]["kind"].get("const") not in stated]
    for variant in operations:  # A target that can only be null is not written (scene_binding.TARGETLESS_KINDS).
        if variant["properties"].get("target") == {"type": "null"}:
            del variant["properties"]["target"]
    checks = [variant for variant in project(_checks_items())["oneOf"] if "const" in variant["properties"]["kind"]
              and "enum" not in variant["properties"].get("type", {})]
    lines = ["Program reference (angelo_scene_program operations and expected_checks, angelo_scene_verify checks; "
             "Target = o<n> handle or $alias):" if short_identifiers else
             "Program reference (angelo_scene_program operations and expected_checks, angelo_scene_verify checks; "
             "Target = object handle or $alias):"]
    if plan:
        lines[0] = lines[0][:-1] + (". Your response format states exactly the action kinds %s and the checks of "
                                    "its expected_checks: write those as it does, in a plan or an exec call. Op is "
                                    "every other exact kind:" % ", ".join(stated))
    aliases, declarations = _reference_aliases()
    body = ["type Op=" + _typescript({"oneOf": operations}, {}, aliases).replace('target:string', 'target:Target')
            .replace('target?:string', 'target?:Target') + ";"]
    if not plan:
        body += ["type Check=" + _typescript({"oneOf": checks}, {}, aliases).replace('target:string', 'target:Target') + ";"]
    if "batch" in CODEX_REFERENCE_ACTIONS:  # What batch's TypeScript cannot say (batch_spec).
        body.append(PLACEMENT_NOTE)
    if "light_create" in CODEX_REFERENCE_ACTIONS:  # When to use orbit, and what it means: TypeScript drops descriptions.
        body.append(ORBIT_REFERENCE_NOTE)
    if "mesh_bend" in CODEX_REFERENCE_ACTIONS:  # What its numbers mean: the bow, its ends and the object's scale.
        body.append(BEND_NOTE)
    body.append(_deferred_lines())
    body += _scene_lines(aliases)
    return "\n".join(lines + (_used_declarations(declarations, body) if plan else declarations) + body)
