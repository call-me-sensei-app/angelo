# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Claude typed-plan schema: an exact core plus schemas read on demand.

The plan schema Claude Code receives in ``initialize.jsonSchema`` was most of
every model call's input (measured in doc/angelo/performance.rst, "Token
diet"). It now keeps exact JSON Schemas only for the tools, program action
kinds and check kinds most turns use (``plan_kinds`` lists them and says
why). Every other allowed
tool, action kind and check kind is still listed by name, in one generic variant
per position whose arguments the plan schema does not constrain. The model reads
an exact deferred schema with ``angelo_agent_capabilities`` (family ``TOOLS``,
``ACTIONS`` or ``CHECKS`` with ``names``), and a plan the host rejects comes back
with the exact schemas of deferred items and action parameters it submitted incorrectly.

Deferral moves only where the model reads a schema. ``tool_specs.validate_call``
validates every operation exactly before anything runs, as before, and the
generic variants list only the request's allowed names, so no tool, kind or
check becomes usable that was not before. Codex chat threads read the core
kinds from ``codex_reference`` and get ``deferred_schemas`` back with a rejected
call; ``tool_specs.dynamic_tools`` (voice threads) and external Codex
connections defer the same kinds and checks (``codex_arguments``). A Codex
chat thread's final message is a strict variant of this plan
(``codex_plan.output_schema`` from ``plan_document``, toolset 30).

The exact parts use a model-facing projection that drops only annotations and
redundancy that do not change which plans validate (empty ``required`` lists,
``type: string`` beside an all-string ``enum``, and ``default: null``), then
shares repeated fragments through local ``$defs``. Runtime validators keep using
the original schemas, whose defaults they apply.

Identifier fields show only the short forms a Claude context reads
(``identifiers.model_facing``: ``o<n>`` handles and revision prefixes); the host
resolves them to the long forms the runtime validators check before anything
runs, and still accepts long forms where the runtime schema does. Bindings the
host infers (``identifiers.INFERRED_FIELDS``) are optional. ``verify`` is
optional and false when omitted; ``reply`` is optional for an edit.
"""

import copy
import json

from .generic_settings import TYPE_PATTERN as GENERIC_TYPE_PATTERN
from .action_schemas import ACTION_SCHEMAS, BEND_NOTE, MODIFIER_SETTINGS, OPERATION_SCHEMAS
from .action_schema_types import HANDLE, MAX_OPERATIONS, _validate
from .identifiers import INFERRED_FIELDS, model_facing
from .scene_binding import SCENE_KINDS, SCENE_OR_OBJECT_KINDS, TARGETLESS_KINDS
from .schema_compaction import compact_schema
from .tagged_unions import discriminated
from .plan_patch import SCHEMA as PATCH_SCHEMA
from .validation_spec import CHECK_VARIANTS
# Which kinds, modifier types, checks and tools are exact in each plan form (plan_kinds). Re-exported: providers,
# codex_reference, codex_plan and the tests read these names from this module.
from .plan_kinds import (CLAUDE_CORE_ACTIONS, CLAUDE_DEFERRED_ACTIONS, CLAUDE_EVERYDAY_ACTIONS,  # noqa: F401
                         CLAUDE_REPEAT_ACTIONS, CODEX_REFERENCE_ACTIONS, CODEX_REFERENCE_MODIFIERS, DEFERRED_ACTIONS,
                         DEFERRED_CHECKS, DEFERRED_MODIFIER_CHECKS, DEFERRED_MODIFIERS, DEFERRED_TOOLS, MODIFIER_KINDS,
                         PLAN_CORE_ACTIONS, PLAN_CORE_CHECKS, PLAN_CORE_MODIFIER_CHECKS, PLAN_CORE_MODIFIERS,
                         core_check as _core_check)


# Fragments at least this large are shared once in $defs. 200 bytes (the
# generic compaction default) left most repeated number/vector/handle
# fragments inline; below about 100 bytes a reference saves little.
PLAN_MIN_SHARED_BYTES = 40
MAX_LOOKUP_NAMES = 32
_GENERIC_TARGET = {"oneOf": [HANDLE, {"type": "null"}]}


def _check_kind(variant):
    properties = variant["properties"]
    kind = properties["kind"]["const"]
    return kind + ":" + properties["type"]["const"] if kind == "modifier" else kind


def model_schema(node):
    """Return a constraint-equivalent copy without redundant keywords.

    Only schema-valued positions are rewritten; enum, const and default values
    are data. Dropped: ``required: []``, ``type: "string"`` next to an enum of
    strings, and ``default: null`` (an omitted nullable field is null).
    """
    if isinstance(node, list):
        return [model_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    result = {}
    for key, value in node.items():
        if key in ("enum", "const", "default", "examples"):
            result[key] = copy.deepcopy(value)
        elif key == "properties" and isinstance(value, dict):
            result[key] = {name: model_schema(child) for name, child in value.items()}
        else:
            result[key] = model_schema(value)
    if result.get("required") == []:
        del result["required"]
    if (result.get("type") == "string" and isinstance(result.get("enum"), list)
            and all(isinstance(item, str) for item in result["enum"])):
        del result["type"]
    if "default" in result and result["default"] is None:
        del result["default"]
    return result


def _checks_items():
    variants = [copy.deepcopy(variant) for variant in CHECK_VARIANTS if _core_check(variant)]
    if DEFERRED_CHECKS:
        variants.append({"type": "object", "properties": {"kind": {"enum": list(DEFERRED_CHECKS)}},
                         "required": ["kind"]})
    if DEFERRED_MODIFIER_CHECKS:
        variants.append({"type": "object", "properties": {"kind": {"const": "modifier"},
                                                          "type": {"enum": list(DEFERRED_MODIFIER_CHECKS)}},
                         "required": ["kind", "type"]})
    return {"oneOf": variants}


def core_operation(kind, modifiers=PLAN_CORE_MODIFIERS):
    """A core kind's exact operation schema, with the modifier types outside ``modifiers`` named only."""
    schema = copy.deepcopy(OPERATION_SCHEMAS[kind])
    if kind == "batch":  # What its schema cannot say, as an exact lookup's note says it (action_catalog.catalog).
        from .batch_spec import PLACEMENT_NOTE
        schema["properties"]["parameters"]["description"] = PLACEMENT_NOTE
    elif kind == "mesh_bend":  # Its note as well: what its numbers mean (action_schemas.BEND_NOTE).
        schema["properties"]["parameters"]["description"] = BEND_NOTE
    deferred = [name for name in MODIFIER_SETTINGS if name not in modifiers]
    if kind in SCENE_KINDS | SCENE_OR_OBJECT_KINDS:  # No target is the bound scene (scene_binding.implicit_targets).
        schema["properties"]["target"] = {"oneOf": [schema["properties"]["target"], {"type": "null"}]}
        schema["required"] = [name for name in schema["required"] if name != "target"]
    elif kind in TARGETLESS_KINDS:  # Only null is possible: a plan need not write it (validate_operation).
        schema["required"] = [name for name in schema["required"] if name != "target"]
    if kind in MODIFIER_KINDS and deferred:
        parameters = schema["properties"]["parameters"]
        parameters["oneOf"] = [variant for variant in parameters["oneOf"]
                               if variant["properties"]["type"].get("const") in modifiers]
        # One identical generic variant for both kinds, so compaction shares it: the
        # deferred typed types by name, or any other modifier type of the build.
        # The pattern branch excludes the exact core types, as the host's own
        # generic variant does: inside a oneOf a typed BEVEL configure matching
        # both variants is rejected (Claude Code validates the plan's schema).
        parameters["oneOf"].append({"type": "object", "properties": {"type": {"anyOf": [
            {"enum": deferred}, {"type": "string", "pattern": GENERIC_TYPE_PATTERN,
                                 "not": {"enum": list(modifiers)}}]}},
            "required": ["type"]})
    return schema


def _operation_items(core=PLAN_CORE_ACTIONS, modifiers=PLAN_CORE_MODIFIERS):
    variants = [core_operation(kind, modifiers) for kind in OPERATION_SCHEMAS if kind in core]
    deferred = [kind for kind in OPERATION_SCHEMAS if kind not in core]
    if deferred:
        # target is optional, as in the exact variants of the kinds that may leave it out: a creating kind
        # (null only) and a scene edit (the observed scene), which validate_operation and
        # scene_binding.implicit_targets fill in. The host still refuses a missing object target.
        variants.append({"type": "object", "properties": {
            "kind": {"enum": deferred}, "target": copy.deepcopy(_GENERIC_TARGET),
            "parameters": {"type": "object"}}, "required": ["kind", "parameters"],
            "additionalProperties": False})
    return {"oneOf": variants}


def plan_arguments(tool, core=PLAN_CORE_ACTIONS):
    """The exact core argument schema of one tool, with deferred kinds named only."""
    schema = copy.deepcopy(tool["inputSchema"])
    properties = schema.get("properties", {})
    if tool["name"] == "angelo_scene_program":
        properties["operations"]["items"] = _operation_items(core)
        properties["expected_checks"]["items"] = _checks_items()
    elif tool["name"] == "angelo_scene_verify":
        properties["checks"]["items"] = _checks_items()
    return model_schema(model_facing(schema, INFERRED_FIELDS.get(tool["name"], ())))


def codex_arguments(tool):
    """A Codex dynamic tool's argument schema: the same deferred kinds and checks as a
    Claude plan, named only, but long identifiers (Codex has no identifier table).

    Codex sends one tool call at a time and reads a deferred kind's exact schema
    with ``angelo_agent_capabilities`` (``CODEX_DEFERRED_GUIDANCE``); a call the
    host rejects returns the exact schemas of the deferred items it used.
    """
    schema = copy.deepcopy(tool["inputSchema"])
    properties = schema.get("properties", {})
    if tool["name"] == "angelo_scene_program":
        properties["operations"]["items"] = _operation_items()
        properties["expected_checks"]["items"] = _checks_items()
    elif tool["name"] == "angelo_scene_verify":
        properties["checks"]["items"] = _checks_items()
    else:
        return schema
    return model_schema(schema)


# What the plan says about a script's two free texts instead of their limits: targets a fifth
# under what validate_call enforces, because a model that aims at a stated length overshoots it. ``base`` says
# what Claude's plan carries of the tool's patch sentence (script_store), which only Codex declarations hold.
SCRIPT_TARGETS = {"description": "Under 400 characters", "code": "Under 27000 characters",
                  "base": "Instead of code: a result's script id; edits apply in order, each find exactly once"}


def without_lengths(node):
    """A copy of a schema without ``maxLength``.

    Claude Code validates each plan against the schema it was given and
    answers a mismatch with the errors of every ``oneOf`` member, the cause
    among some eight thousand characters; the whole plan, scripts included, is
    then written again. ``tool_specs.validate_call`` enforces every length
    before anything runs and answers in one line, so the limits stay the
    host's. (Codex's strict variant never carried them: ``strict_schema``.)
    """
    if isinstance(node, list):
        return [without_lengths(item) for item in node]
    if not isinstance(node, dict):
        return node
    return {key: (copy.deepcopy(value) if key in ("enum", "const", "default", "examples") else
                  {name: without_lengths(child) for name, child in value.items()}
                  if key == "properties" and isinstance(value, dict) else without_lengths(value))
            for key, value in node.items() if key != "maxLength" or not isinstance(value, int)}


def plan_schema(tools, allowed=None):
    """The complete typed-plan schema for ``tools`` filtered by ``allowed``."""
    document = without_lengths(plan_document(tools, allowed, CLAUDE_CORE_ACTIONS))
    document["properties"]["patch"] = copy.deepcopy(PATCH_SCHEMA)  # A correction of a held plan (plan_patch).
    for variant in document["properties"]["operations"]["items"].get("oneOf", ()):
        if variant["properties"]["tool"].get("const") == "angelo_execute_python":
            for field, target in SCRIPT_TARGETS.items():
                variant["properties"]["arguments"]["properties"][field]["description"] = target
    # Tagged unions as if/then, so a mismatch reports only its own member (tagged_unions). Compact the
    # complete plan, not each nested tool: local references resolve against this schema's root.
    return compact_schema(discriminated(document), min_shared_bytes=PLAN_MIN_SHARED_BYTES)


def plan_document(tools, allowed=None, core=PLAN_CORE_ACTIONS):
    """``plan_schema`` before compaction (codex_plan derives its strict variant from it).

    ``core``: the action kinds with exact schemas; Claude's plan passes ``CLAUDE_CORE_ACTIONS``.
    """
    usable = [tool for tool in tools if allowed is None or tool["name"] in allowed]
    variants = [{"type": "object", "properties": {"tool": {"const": tool["name"]},
                                                  "arguments": plan_arguments(tool, core)},
                 "required": ["tool", "arguments"], "additionalProperties": False}
                for tool in usable if tool["name"] not in DEFERRED_TOOLS]
    deferred = [tool["name"] for tool in usable if tool["name"] in DEFERRED_TOOLS]
    if deferred:
        variants.append({"type": "object", "properties": {"tool": {"enum": deferred}, "arguments": {"type": "object"}},
                         "required": ["tool", "arguments"], "additionalProperties": False})
    schema = {"type": "object", "properties": {
        "reply": {"type": "string"},
        "operations": {"type": "array", "minItems": 0, "maxItems": 64, "items": {"oneOf": variants}},
        "verify": {"type": "boolean"},
        # Optional and omitted in the normal case, so it costs no output tokens;
        # Auto routing escalates a "low" plan before anything runs (toolset 20).
        "conf": {"enum": ["low"]},
    }, "required": ["operations"], "additionalProperties": False}
    if not usable:
        schema["properties"]["operations"] = {"type": "array", "maxItems": 0, "items": {"type": "object"}}
    return schema


def _exact(schema):
    return compact_schema(model_schema(schema))


def lookup(family, names, hidden=frozenset()):
    """Exact schemas of named tools, action kinds, check kinds, recipes or queries. No Blender access.

    Returns the documents ``angelo_agent_capabilities`` adds for ``names``, which it
    takes with every family: QUERIES names scene query tools, SUMMARY a name of any
    family. Unknown names are listed rather than guessed. Tool lookups also return the
    workflow guidance of the sculpt and Media Studio families. ``hidden`` tools
    (the team family while signed out) read as unknown.
    """
    from .agent_guidance import FAMILY_GUIDANCE
    from .tool_specs import TOOL_MAP
    names = list(dict.fromkeys(names))[:MAX_LOOKUP_NAMES]
    if family == "TOOLS":
        found = [name for name in names if name in TOOL_MAP and name not in hidden]
        result = {"tools": [{"name": name, "description": TOOL_MAP[name]["description"],
                             "read_only": TOOL_MAP[name]["annotations"]["readOnlyHint"],
                             "input_schema": _exact(TOOL_MAP[name]["inputSchema"])} for name in found]}
        guidance = {key: text for key, (members, text) in FAMILY_GUIDANCE.items() if members & set(found)}
        if guidance:
            result["guidance"] = guidance
    elif family == "ACTIONS":
        from .action_catalog import catalog
        found = [name for name in names if name in ACTION_SCHEMAS]
        result = {"actions": [dict(catalog(kind), parameters=_exact(ACTION_SCHEMAS[kind])) for kind in found]}
    elif family == "CHECKS":
        # "modifier" names every modifier check variant; "modifier:BEVEL" one of them.
        variants = {_check_kind(variant): variant for variant in CHECK_VARIANTS}
        selected = {key: variant for name in names for key, variant in variants.items()
                    if key == name or key.startswith(name + ":")}
        found = [name for name in names if any(key == name or key.startswith(name + ":") for key in selected)]
        result = {"checks": [_exact(variant) for variant in selected.values()]}
    elif family == "RECIPES":
        from .action_recipes import recipe_capabilities
        recipes = {recipe["name"]: recipe for recipe in recipe_capabilities()["recipes"]}
        # bevel_and_shade names bevel-and-shade: the recipe ids are the only names with hyphens.
        wanted = {name: name.replace("_", "-") for name in names}
        found = [name for name in names if wanted[name] in recipes]
        result = {"recipes": [dict(recipes[recipe], parameters=_exact(recipes[recipe]["parameters"]))
                              for recipe in dict.fromkeys(wanted[name] for name in found)]}
    elif family in ("QUERIES", "SUMMARY"):
        from .scene_spec import SCENE_NAMES
        parts = ("TOOLS",) if family == "QUERIES" else ("TOOLS", "ACTIONS", "CHECKS", "RECIPES")
        result, found = {}, []
        for part in parts:
            asked = [name for name in names if family != "QUERIES" or name in SCENE_NAMES]
            answer = lookup(part, asked, hidden) if asked else {}
            known = [name for name in asked if name not in answer.get("unknown_names", ())]
            result.update({key: value for key, value in answer.items() if key != "unknown_names" and known})
            found += [name for name in known if name not in found]
    else:
        raise ValueError("Exact schema lookups use the SUMMARY, ACTIONS, QUERIES, CHECKS, RECIPES or TOOLS family")
    unknown = [name for name in names if name not in found]
    if unknown:
        result["unknown_names"] = unknown
    return result


def deferred_index(hidden=frozenset()):
    """What a Claude plan names without an exact schema, for discovery (without ``hidden`` tools)."""
    from .tool_specs import TOOLS
    return {"deferred_tools": [{"name": tool["name"], "description": tool["description"]}
                               for tool in TOOLS if tool["name"] in DEFERRED_TOOLS and tool["name"] not in hidden],
            "deferred_action_kinds": list(DEFERRED_ACTIONS),
            "deferred_modifier_types": {"kinds": list(MODIFIER_KINDS), "types": list(DEFERRED_MODIFIERS)},
            "deferred_check_kinds": list(DEFERRED_CHECKS) + ["modifier:" + kind for kind in DEFERRED_MODIFIER_CHECKS],
            "lookup": "Read exact schemas with family TOOLS, ACTIONS or CHECKS and names."}


def deferred_references(operations, core=PLAN_CORE_ACTIONS, modifiers=PLAN_CORE_MODIFIERS):
    """Deferred tools, action kinds and check kinds a (possibly invalid) plan names.

    Plan contents are untrusted model output: anything malformed is skipped,
    and only catalog names are returned. ``core`` and ``modifiers``: the action
    kinds and modifier types the model reads exactly (``CLAUDE_CORE_ACTIONS``
    for Claude's plan, the ``CODEX_REFERENCE_*`` sets for a Codex chat thread).
    """
    tools, actions, checks = set(), set(), set()
    deferred_actions = tuple(kind for kind in OPERATION_SCHEMAS if kind not in core)
    deferred_modifiers = tuple(kind for kind in MODIFIER_SETTINGS if kind not in modifiers)
    deferred_checks = set(DEFERRED_CHECKS) | {"modifier:" + kind for kind in DEFERRED_MODIFIER_CHECKS}
    for operation in operations if isinstance(operations, list) else ():
        if not isinstance(operation, dict) or not isinstance(operation.get("tool"), str):
            continue
        if operation["tool"] in DEFERRED_TOOLS:
            tools.add(operation["tool"])
        arguments = operation.get("arguments")
        if not isinstance(arguments, dict):
            continue
        for item in arguments.get("operations") if isinstance(arguments.get("operations"), list) else ():
            if isinstance(item, dict) and item.get("kind") in deferred_actions:
                actions.add(item["kind"])
            elif (isinstance(item, dict) and item.get("kind") in MODIFIER_KINDS
                  and isinstance(item.get("parameters"), dict) and item["parameters"].get("type") in deferred_modifiers):
                actions.add(item["kind"])
        for field in ("expected_checks", "checks"):
            for item in arguments.get(field) if isinstance(arguments.get(field), list) else ():
                if not isinstance(item, dict) or not isinstance(item.get("kind"), str):
                    continue
                key = item["kind"] + ":" + str(item.get("type")) if item["kind"] == "modifier" else item["kind"]
                if key in deferred_checks:
                    checks.add(key)
    return {family: sorted(values) for family, values in
            (("TOOLS", tools), ("ACTIONS", actions), ("CHECKS", checks)) if values}


class PlanRejected(ValueError):
    """The host rejected a typed plan before any operation ran, after its correction rounds."""


def _correction_schemas(operations, core=PLAN_CORE_ACTIONS, modifiers=PLAN_CORE_MODIFIERS):
    """Exact feedback for deferred references and invalid parameters, never execution authority."""
    from .action_catalog import catalog
    bounded = []
    # Match the existing plan (64), program (32) and authored-check (32) limits, even for malformed input.
    for operation in operations[:64] if isinstance(operations, list) else ():
        if not isinstance(operation, dict) or not isinstance(operation.get("tool"), str):
            continue
        arguments = operation.get("arguments")
        fields = {key: arguments[key][:MAX_OPERATIONS] for key in ("operations", "checks", "expected_checks")
                  if isinstance(arguments, dict) and isinstance(arguments.get(key), list)}
        bounded.append({"tool": operation["tool"], "arguments": fields})
    references = deferred_references(bounded, core, modifiers)
    actions, submitted_types = set(references.get("ACTIONS", ())), {}
    for operation in bounded:
        if operation["tool"] != "angelo_scene_program":
            continue
        for item in operation["arguments"].get("operations", ()):
            if not isinstance(item, dict) or not isinstance(item.get("kind"), str):
                continue
            kind, parameters = item["kind"], item.get("parameters")
            if kind not in ACTION_SCHEMAS:
                continue
            if kind in MODIFIER_KINDS:
                subtype = parameters.get("type") if isinstance(parameters, dict) else None
                if not isinstance(subtype, str) or subtype not in MODIFIER_SETTINGS:
                    submitted_types[kind] = None  # Unknown/missing type needs the complete fallback schema.
                elif submitted_types.setdefault(kind, set()) is not None:
                    submitted_types[kind].add(subtype)
            try:
                _validate(parameters, ACTION_SCHEMAS[kind], "operation.parameters")
            except (ValueError, TypeError, RecursionError):
                actions.add(kind)
    schemas = {family: lookup(family, names) for family, names in references.items() if family != "ACTIONS"}
    if actions:
        entries = []
        for kind in sorted(actions)[:MAX_LOOKUP_NAMES]:
            schema, selected = ACTION_SCHEMAS[kind], submitted_types.get(kind)
            if selected:
                variants = [variant for variant in schema["oneOf"]
                            if variant["properties"]["type"].get("const") in selected]
                schema = variants[0] if len(variants) == 1 else {"oneOf": variants}
            entries.append(dict(catalog(kind), parameters=_exact(schema)))
        schemas["ACTIONS"] = {"actions": entries}
    return schemas


def deferred_schemas(name, arguments, core=PLAN_CORE_ACTIONS):
    """Exact correction schemas for one rejected call ({} if none); ``core``: the kinds its model reads exactly."""
    return _correction_schemas([{"tool": name, "arguments": arguments}], core)


def rejection_prompt(problems, operations, core=PLAN_CORE_ACTIONS, modifiers=PLAN_CORE_MODIFIERS):
    """The next round's prompt after the host rejected a plan before running it."""
    schemas = _correction_schemas(operations, core, modifiers)
    text = ("The host rejected your last plan before running any of its operations; nothing changed. "
            "Fix these problems and submit a corrected plan:\n" + "\n".join(problems))
    if schemas:
        text += ("\nExact schemas of deferred references and invalid action parameters "
                 "(these, not the generic plan variants, are what the host validates):\n"
                 + json.dumps(schemas, separators=(",", ":")))
    return text


def staged_rejection_prompt(problem, operations, core=PLAN_CORE_ACTIONS, modifiers=PLAN_CORE_MODIFIERS):
    """The next round's prompt after a plan stopped before any live change (on its stage or at dispatch)."""
    text = ("The host stopped your last plan before it changed the scene; nothing changed. Fix this and "
            "submit a corrected plan (it is approved again):\n" + problem)
    schemas = {family: lookup(family, names)
               for family, names in deferred_references(operations, core, modifiers).items()}
    if schemas:
        text += ("\nExact schemas of the deferred tools, action kinds and checks it used:\n"
                 + json.dumps(schemas, separators=(",", ":")))
    return text
