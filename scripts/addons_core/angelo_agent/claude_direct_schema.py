# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The tools a direct-tools Claude session is offered (claude_direct): names and argument schemas, byte-stable.

Every allowed tool, in ``tool_specs.TOOLS`` order, by name and argument schema only, as the typed plan's variants
show them (the instructions carry each tool's guidance): core tools with the plan's exact argument schemas
(``plan_schema.plan_arguments`` with ``CLAUDE_CORE_ACTIONS``: short identifiers, core kinds exact and the rest named,
tagged unions as if/then, no maxLength, the script's length targets); deferred tools named with a generic object
schema and a pointer to ``angelo_agent_capabilities``, as the plan names them. Every schema has an object at its root
(``object_root``), as the Messages API takes a tool's ``input_schema``.
"""

import functools
import json

from .plan_kinds import CLAUDE_CORE_ACTIONS, DEFERRED_TOOLS
from .plan_schema import PLAN_MIN_SHARED_BYTES, SCRIPT_TARGETS, plan_arguments, without_lengths
from .schema_compaction import compact_schema
from .tagged_unions import discriminated
from .tool_specs import TOOLS, TOOL_MAP, declared

DEFERRED_DESCRIPTION = ("Deferred: read its exact schema and guidance with angelo_agent_capabilities family TOOLS "
                        "and this name before calling it.")


@functools.lru_cache(maxsize=16)
def _tools(allowed):
    tools = []
    for tool in declared(allowed):   # A lead's "every tool" never lists a worker's own progress tool (P1-6 L).
        name = tool["name"]
        if name in DEFERRED_TOOLS:
            tools.append({"name": name, "description": DEFERRED_DESCRIPTION, "inputSchema": {"type": "object"}})
            continue
        schema = without_lengths(plan_arguments(tool, CLAUDE_CORE_ACTIONS))
        if name == "angelo_execute_python":
            for field, target in SCRIPT_TARGETS.items():
                schema["properties"][field] = dict(schema["properties"][field], description=target)
        schema = object_root(compact_schema(discriminated(schema), min_shared_bytes=PLAN_MIN_SHARED_BYTES))
        # No description, as the plan's variants have none: the instructions carry each tool's guidance.
        tools.append({"name": name, "inputSchema": schema})
    return json.dumps(tools, ensure_ascii=False, sort_keys=False, separators=(",", ":"))


def object_root(schema):
    """``schema`` with an object at its root, as the Messages API takes a tool's ``input_schema``.

    Claude Code leaves out a tool whose schema has ``oneOf``/``anyOf``/``allOf`` at its root (2.1.301: the recipe
    tool, whose arguments are one variant per recipe). Such a union becomes one object: the tag that names each
    variant (a property that is a ``const`` in every one) lists its values, a field the variants share is kept,
    and a field that differs offers each variant's form, titled with the tag values it goes with. Fields every
    variant requires stay required. ``tool_specs.validate_call`` still checks every call exactly, and a rejected
    call returns the exact schemas, so nothing becomes valid that was not. Fields the root states beside its union
    belong to every variant. A root ``allOf`` is left as it is (no tool has one); the per-tool test of
    ``test_native_claude_direct_review`` fails on it, so it cannot ship unseen.
    """
    union = schema.get("oneOf") or schema.get("anyOf")
    if not union or "allOf" in schema:
        return schema
    definitions = schema.get("$defs", {})

    def resolve(node):
        while isinstance(node, dict) and set(node) == {"$ref"} and node["$ref"].startswith("#/$defs/"):
            node = definitions[node["$ref"][len("#/$defs/"):]]
        return node
    variants = [resolve(variant) for variant in union]
    own = schema.get("properties", {})   # Fields the root states beside its union hold for every variant.
    variants = [dict(variant, properties={**own, **variant.get("properties", {})},
                     required=list(dict.fromkeys(list(schema.get("required", ())) + list(variant.get("required", ())))))
                for variant in variants]
    names = list(dict.fromkeys(name for variant in variants for name in variant.get("properties", {})))
    tag = next((name for name in names if all("const" in resolve(variant.get("properties", {}).get(name, {}))
                                               for variant in variants)), None)
    labels = [str(resolve(variant["properties"][tag])["const"]) if tag else "variant %d" % (index + 1)
              for index, variant in enumerate(variants)]
    properties = {}
    for name in names:
        if name == tag:
            properties[name] = {"enum": [resolve(variant["properties"][tag])["const"] for variant in variants]}
            continue
        forms = {}
        for label, variant in zip(labels, variants):
            if name in variant.get("properties", {}):
                forms.setdefault(json.dumps(variant["properties"][name], sort_keys=True), []).append(label)
        if len(forms) == 1:
            properties[name] = json.loads(next(iter(forms)))
        else:
            properties[name] = {"anyOf": [{"title": "%s: %s" % (tag or "for", ", ".join(owners)), "allOf": [json.loads(form)]}
                                          for form, owners in forms.items()]}
    required = [name for name in names if all(name in variant.get("required", ()) for variant in variants)]
    root = {"type": "object", "properties": properties, "required": required}
    if all(variant.get("additionalProperties") is False for variant in variants):
        root["additionalProperties"] = False
    if definitions:
        root["$defs"] = definitions
    return root


def tool_list(allowed=None):
    """The tools/list answer's tools for ``allowed`` (None: every tool): byte-identical for the same set."""
    return json.loads(_tools(None if allowed is None else frozenset(allowed)))


_JSON_TYPES = {"integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
               "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
               "boolean": lambda v: isinstance(v, bool), "array": lambda v: isinstance(v, list),
               "object": lambda v: isinstance(v, dict), "null": lambda v: v is None}


def typed_arguments(name, arguments):
    """A deferred tool's arguments with the values the transport turned into strings decoded again.

    A deferred tool is declared with no properties (``DEFERRED_DESCRIPTION``), and Claude's tool calls carry the
    value of a property no schema types as a string: Opus wrote ``"frame_start": "60"`` to angelo_job_start on this
    route where the plan route's JSON carried ``60`` (Windows A/B round 1, 3 of 3 bridge trials, each refused). A
    top-level string is decoded only where the tool's exact schema gives that property non-string types and the
    string is JSON of one of them; anything else stays as sent, and ``validate_call`` still checks every call
    exactly. Core tools declare their schemas, so their values arrive typed and are left alone.
    """
    if name not in DEFERRED_TOOLS or not isinstance(arguments, dict):
        return arguments
    properties = TOOL_MAP[name]["inputSchema"].get("properties", {})
    typed = dict(arguments)
    for key, value in arguments.items():
        schema = properties.get(key)
        kinds = schema.get("type") if isinstance(schema, dict) else None
        kinds = kinds if isinstance(kinds, list) else [kinds]
        if not isinstance(value, str) or not kinds or "string" in kinds or not all(k in _JSON_TYPES for k in kinds):
            continue
        try:
            decoded = json.loads(value)
        except ValueError:
            continue
        if any(_JSON_TYPES[kind](decoded) for kind in kinds):
            typed[key] = decoded
    return typed
