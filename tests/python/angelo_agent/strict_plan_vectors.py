# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Plan vectors for the Codex typed plan's strict schema (test_native_codex_plan.py).

``union_vectors`` walks the ordinary plan schema (``codex_plan.ordinary_schema``)
and returns one plan per member of every union it can reach (tools, action
kinds, primitive and modifier types, check kinds, the generic
deferred variants), each with only the required fields and with every field.
Values are the first the schema allows; identifier patterns get the forms a
short-identifier context reads (``PATTERN_EXAMPLES``), so a new pattern fails
loudly here instead of producing an invalid vector.

``NAMED`` are hand-written standards vectors for shapes that broke before
(a typed BEVEL matched the generic modifier variant inside a ``oneOf`` and a
gate failed at 98/120) or that the strict variant changes most (JSON text).
"""

import json
import uuid

from angelo_agent.strict_schema import _union

HANDLE_SHORT, REVISION_PREFIX = "o1", "bbbbbbbb"
PATTERN_EXAMPLES = {
    "^recovery:[a-f0-9]{32}$": "recovery:" + "a" * 32,
    "^[A-Za-z][A-Za-z0-9_]{0,31}$": "cube",
    "^(?:\\$[A-Za-z][A-Za-z0-9_]{0,31}|[A-Za-z0-9][A-Za-z0-9_.:\\-]{0,127})$": HANDLE_SHORT,
    "^[A-Z][A-Z_]{1,31}$": "WAVE",
    "^o[1-9][0-9]{0,8}$": HANDLE_SHORT,
    "^[a-f0-9]{8,64}$": REVISION_PREFIX,
}


class _Example:
    def __init__(self, choices, full):
        self.choices, self.full, self.unions = choices, full, {}

    def value(self, node, path):
        members = _union(node)
        if members is not None:
            self.unions[path] = len(members)
            index = self.choices.get(path, 0)
            return self.value(members[index], "%s|%d" % (path, index))
        if "const" in node:
            return node["const"]
        if "enum" in node:
            return next((item for item in node["enum"] if item is not None), None)
        kind = node.get("type")
        if isinstance(kind, list):
            kind = next(item for item in kind if item != "null")
        if kind == "object" or "properties" in node:
            properties = node.get("properties") or {}
            if not properties:
                return {"note": "open"} if self.full else {}
            names = list(properties) if self.full else list(node.get("required", ()))
            return {name: self.value(properties[name], path + "." + name) for name in names}
        if kind == "array":
            count = max(node.get("minItems", 0), 1 if self.full else 0)
            items = node.get("items", {})
            return [self._item(items, path + "[]", index) for index in range(count)]
        if kind == "string":
            if "pattern" in node:
                return PATTERN_EXAMPLES[node["pattern"]]
            if node.get("format") == "uuid":
                return str(uuid.UUID(int=1))
            return "x" * max(1, node.get("minLength", 1))
        if kind in ("integer", "number"):
            low = node.get("minimum", node.get("exclusiveMinimum", 0))
            value = low + (1 if "exclusiveMinimum" in node and "minimum" not in node else 0)
            value = min(value, node.get("maximum", value))
            return int(value) if kind == "integer" else float(value)
        if kind == "boolean":
            return True
        if kind == "null":
            return None
        raise AssertionError("No example for schema at %s: %s" % (path, sorted(node)))

    def _item(self, node, path, index):
        value = self.value(node, path)
        return value + index if type(value) is int else value  # Distinct for uniqueItems arrays.


def union_vectors(schema):
    """``[(label, plan)]``: one plan per reachable union member, minimal and full.

    Each union position is expanded once, under the choices that first reached
    it, so the count is the number of members, not their combinations.
    """
    vectors = []
    for full in (False, True):
        pending, expanded = [{}], set()
        while pending:
            choices = pending.pop(0)
            example = _Example(choices, full)
            plan = example.value(schema, "")
            vectors.append(("%s %s" % ("full" if full else "minimal", json.dumps(choices, sort_keys=True)), plan))
            for path, count in example.unions.items():
                if path in expanded:
                    continue
                expanded.add(path)
                pending.extend(dict(choices, **{path: index}) for index in range(1, count))
    return vectors


def _program(*operations, checks=()):
    return {"tool": "angelo_scene_program", "arguments": {
        "mode": "APPLY", "operations": list(operations), "expected_checks": list(checks)}}


# Ordinary plans, as a model reading the exact schemas would mean them.
NAMED = {
    "typed BEVEL configure (the 98/120 overlap)": _program(
        {"kind": "modifier_configure", "target": "o1", "parameters": {
            "modifier": "Bevel", "type": "BEVEL", "settings": {"width": 0.05, "segments": 3}}}),
    "generic REMESH add": _program(
        {"kind": "modifier_add", "target": "o1", "parameters": {"type": "REMESH"}}),
    "create a cube and check it": _program(
        {"kind": "create_primitive", "target": None, "parameters": {
            "primitive": "CUBE", "name": "Box", "alias": "box", "size": 2, "location": [0, 0, 1]}},
        checks=[{"kind": "dimensions", "target": "$box", "expected": [2, 2, 2], "tolerance": 0.0001}]),
    "material": _program(
        {"kind": "material_principled", "target": "o1", "parameters": {
            "name": "Red", "inputs": {"Base Color": [1, 0, 0, 1], "Roughness": 0.4, "Metallic": 0}}}),
    "deferred kind with parameters": _program(
        {"kind": "object_rename", "target": "o1", "parameters": {"name": "Box"}}),
    "deferred check": _program(
        {"kind": "transform", "target": "o1", "parameters": {"mode": "DELTA", "location": [0, 0, 1]}},
        checks=[{"kind": "object_exists", "target": "o1", "expected": True}]),
    "fit-dimensions recipe": {"tool": "angelo_apply_recipe", "arguments": {
        "mode": "APPLY", "recipe": "fit-dimensions", "target": "o1", "parameters": {
            "dimensions": [4, 1, 3], "minimum_scale": 0.001, "maximum_scale": 1000, "maximum_scale_ratio": 100}}},
    "scale to dimensions": _program(
        {"kind": "transform", "target": "o1", "parameters": {"mode": "SET", "scale": [2, 0.5, 1.5]}},
        checks=[{"kind": "dimensions", "target": "o1", "expected": [4, 1, 3], "tolerance": 0.0001}]),
    "deferred tool": {"tool": "angelo_memory_update", "arguments": {
        "expected_revision": 0, "patch": {"objective": "Keep the cube centred."}}},
    "cube batch": {"tool": "angelo_apply_batch", "arguments": {"operations": [
        {"tool": "angelo_create_cube", "arguments": {"name": "A", "size": 1, "location": [0, 0, 0]}}]}},
}


# Final messages from the first toolset-30 gates (codex-cli 0.160.0),
# verbatim from the bench profile's Codex rollouts: (model, request, raw text).
# The output schema reached the model with every object's keys sorted
# (compact_schema), so ``arguments`` came before ``tool`` and ``parameters``
# before ``recipe``; each began a program with "mode" and decoding could only
# continue it as a recipe, filled with the nearest values in the history.
GATE_OUTPUTS = (
    ("gpt-6.1-sol", "Create a cube named 'Eval Cube' that is exactly 2 m on each side, centred at the world origin.",
     '{"operations":[{"arguments":{"mode":"APPLY","parameters":{ "dimensions": [2,2,2], "maximum_scale": 1000, '
     '"maximum_scale_ratio": 100, "minimum_scale": 0.000001, "tolerance": 0.000001},"recipe":"fit-dimensions",'
     '"revision":null,"target":"o1"},"tool":"angelo_apply_recipe"}],"reply":""}'),
    ("gpt-6.1-sol", "Stage a PREVIEW only, do not apply it, that moves 'Eval Cube' to location (3, 0, 0).",
     '{"operations":[{"arguments":{"mode":"PREVIEW","parameters":{"end_frame":30,"end_value":[3,0,0],'
     '"interpolation":"LINEAR","property":"location","start_frame":1,"start_value":[3,0,0],"tolerance":0.000001},'
     '"recipe":"simple-animation","revision":null,"target":"o4"},"tool":"angelo_apply_recipe"}],"reply":""}'),
    ("gpt-6.1-sol", "Add a non-destructive bevel to 'Eval Cube' with a width of 0.05 m and 2 segments.",
     '{"operations":[{"arguments":{"mode":"APPLY","parameters":{ "dimensions":[1,2,1],"maximum_scale":1000,'
     '"maximum_scale_ratio":100,"minimum_scale":0.000001,"tolerance":0.000001},"recipe":"fit-dimensions",'
     '"revision":null,"target":"o4"},"tool":"angelo_apply_recipe"}],"reply":""}'),
    ("gpt-6-astra", "Change the bevel modifier on 'Eval Cube' to a width of 0.075 m and 3 segments.",
     '{"operations":[{"arguments":{"mode":"APPLY","parameters":{"base_color":[0.2,0.8,0.3,1],"metallic":0,'
     '"name":"Long Material 04","roughness":0.2,"tolerance":0.000001},"recipe":"material-setup","revision":null,'
     '"target":"o4"},"tool":"angelo_apply_recipe"}],"reply":""}'),
)

# The four failing bevel_and_shade final plans of the second toolset-30 gates
# (after the key-order fix), verbatim: a program applying the
# cube's scale, then bevel-and-shade on the same object, bindings left to the
# host. Resolved together, both took the turn's revision; the program's commit
# replaced it, and the recipe was refused as "changed since observation".
REGATE_OUTPUTS = (
    ("gpt-6.1-sol", "trial 2",
     '{"reply":"","operations":[{"tool":"angelo_scene_program","arguments":{"mode":"APPLY","read_set":null,'
     '"write_set":null,"operations":[{"kind":"transform_apply","target":"o4","parameters":"{\\"location\\":false,'
     '\\"rotation\\":false,\\"scale\\":true}"}],"expected_checks":[{"kind":"dimensions","target":"o4","expected":'
     '[4,1,3],"tolerance":0.000001,"space":"LOCAL"}]}},{"tool":"angelo_apply_recipe","arguments":{"recipe":'
     '"bevel-and-shade","target":"o4","revision":null,"parameters":{"width":0.125,"segments":3,"modifier_name":'
     '"Bevel","shade_modifier_name":"Smooth Shading","tolerance":0.000001},"mode":"APPLY"}}]}'),
    ("gpt-6.1-sol", "trial 4",
     '{"reply":"","operations":[{"tool":"angelo_scene_program","arguments":{"mode":"APPLY","read_set":null,'
     '"write_set":null,"operations":[{"kind":"transform_apply","target":"o4","parameters":"{\\"location\\":false,'
     '\\"rotation\\":false,\\"scale\\":true}"}],"expected_checks":[{"kind":"dimensions","target":"o4","expected":'
     '[4,1,3],"tolerance":0.000001,"space":"WORLD"}]}},{"tool":"angelo_apply_recipe","arguments":{"recipe":'
     '"bevel-and-shade","target":"o4","revision":null,"parameters":{"width":0.125,"segments":3,"modifier_name":'
     '"Bevel","shade_modifier_name":"Smooth Shading","tolerance":0.000001},"mode":"APPLY"}}]}'),
    ("gpt-6-astra", "trial 4",
     '{"reply":"","operations":[{"tool":"angelo_scene_program","arguments":{"mode":"APPLY","read_set":null,'
     '"write_set":null,"operations":[{"kind":"transform_apply","target":"o4","parameters":"{\\"location\\":false,'
     '\\"rotation\\":false,\\"scale\\":true}"}],"expected_checks":[{"kind":"dimensions","target":"o4","expected":'
     '[4,1,3],"tolerance":0.000001,"space":"WORLD"},{"kind":"transform","target":"o4","property":"scale","expected":'
     '[1,1,1],"tolerance":0.000001}]}},{"tool":"angelo_apply_recipe","arguments":{"recipe":"bevel-and-shade",'
     '"target":"o4","revision":null,"parameters":{"width":0.125,"segments":3,"modifier_name":"Bevel",'
     '"shade_modifier_name":"Smooth Shading","tolerance":0.000001},"mode":"APPLY"}}]}'),
    ("gpt-6-astra", "trial 5",
     '{"reply":"","operations":[{"tool":"angelo_scene_program","arguments":{"mode":"APPLY","read_set":null,'
     '"write_set":null,"operations":[{"kind":"transform_apply","target":"o4","parameters":"{\\"location\\":false,'
     '\\"rotation\\":false,\\"scale\\":true}"}],"expected_checks":[{"kind":"dimensions","target":"o4","expected":'
     '[4,1,3],"tolerance":0.000001,"space":"WORLD"}]}},{"tool":"angelo_apply_recipe","arguments":{"recipe":'
     '"bevel-and-shade","target":"o4","revision":null,"parameters":{"width":0.125,"segments":3,"modifier_name":'
     '"Bevel","shade_modifier_name":"Smooth Shading","tolerance":0.000001},"mode":"APPLY"}}]}'),
)
