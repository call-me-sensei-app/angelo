# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Strict structured-output variants of Angelo's JSON Schemas, and the way back.

OpenAI's strict ``json_schema`` response format (what Codex's ``turn/start``
``outputSchema`` becomes) accepts a subset of JSON Schema
(https://developers.openai.com/api/docs/guides/structured-outputs, "Supported
schemas", read 2026-10-09): every object lists all its properties as
``required`` and sets ``additionalProperties: false``; unions are ``anyOf``
(no ``oneOf``, ``allOf`` or ``not``); the root is an object; at most 5,000
object properties, 10 levels of nesting, 1,000 enum values and 120,000
characters of property names, definition names, enum and const values.

``strict_schema`` derives that variant from an ordinary schema, mechanically:

* an optional property becomes required and nullable (``null`` = omitted);
* ``oneOf`` becomes ``anyOf``; ``not`` is dropped;
* an object the subset cannot express (open, a map, or deeper than
  ``max_depth``) becomes a string holding the object as JSON text
  (``JSON_TEXT``);
* properties keep the document's order, with each object's ``const``
  discriminators first: the model writes them in schema order, so the
  member it means is fixed before its other fields (compact the result with
  ``preserve_order``);
* a property that only ``null`` fits (a creating operation's ``target``) is
  left out: the host knows it;
* an object with a shape (``strict_shapes``) is a union of prefixes of one
  property order, so its trailing fields may be left out instead of written
  as ``null``;
* only ``STRICT_KEYWORDS`` survive: bounds on strings (pattern, length),
  ``format``, ``uniqueItems``, array bounds above ``MAX_ITEMS_BOUND`` and
  defaults are left to the host's validator.

The variant only shapes what the model writes. ``decode`` turns a strict
value back into the ordinary form (drops the ``null`` of an omitted optional
property, restores a null-only one, parses JSON text), guided by the ORIGINAL
schema, and the host then validates the result exactly as before. Dropped keywords and anyOf overlaps
can therefore never let anything through that the original schema's validator
rejects. ``strict_problems`` checks a variant against the subset and limits.
"""

import copy
import json

from .strict_shapes import SHAPE, forms

STRICT_KEYWORDS = frozenset({"type", "properties", "required", "additionalProperties", "items", "anyOf", "enum",
                             "const", "$ref", "$defs", "description", "minimum", "maximum", "exclusiveMinimum",
                             "exclusiveMaximum", "minItems", "maxItems"})
# OpenAI's documented limits for one strict schema.
MAX_DEPTH, MAX_PROPERTIES, MAX_STRING_CHARS, MAX_ENUM_VALUES = 10, 5000, 120000, 1000
LARGE_ENUM, LARGE_ENUM_CHARS = 250, 15000
MAX_ITEMS_BOUND = 16
JSON_TEXT = {"type": "string", "description": "JSON text of an object"}
_NULL = {"type": "null"}
_NUMERIC_BOUNDS = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum")
_REFERENCE = "#/$defs/"


def inline_references(schema):
    """A copy of ``schema`` with its local ``#/$defs/`` references expanded (no recursion allowed)."""
    definitions = schema.get("$defs", {}) if isinstance(schema, dict) else {}

    def expand(node, active):
        if isinstance(node, list):
            return [expand(item, active) for item in node]
        if not isinstance(node, dict):
            return node
        reference = node.get("$ref")
        if isinstance(reference, str) and reference.startswith(_REFERENCE):
            name = reference[len(_REFERENCE):]
            if name in active or name not in definitions:
                raise ValueError("Unresolvable or recursive schema reference: " + reference)
            rest = {key: value for key, value in node.items() if key != "$ref"}
            return expand(dict(definitions[name], **rest), active | {name})
        return {key: (copy.deepcopy(value) if key in ("enum", "const", "default", "examples")
                      else expand(value, active)) for key, value in node.items() if key != "$defs"}
    return expand(schema, frozenset())


def _union(node):
    """The members of a union node, each merged with the node's own keywords."""
    members = node.get("oneOf") or node.get("anyOf")
    if not isinstance(members, list):
        return None
    base = {key: value for key, value in node.items() if key not in ("oneOf", "anyOf")}
    merged = []
    for member in members:
        if not isinstance(member, dict):
            raise ValueError("Boolean union members are not supported")
        combined = dict(base, **member)
        if "properties" in base and "properties" in member:
            combined["properties"] = dict(base["properties"], **member["properties"])
        if "required" in base and "required" in member:
            combined["required"] = list(dict.fromkeys(list(base["required"]) + list(member["required"])))
        merged.append(combined)
    return merged


def _is_object(node):
    return node.get("type") == "object" or "properties" in node


def _closed(node):
    """An object whose properties are all listed: the subset can spell it out."""
    return bool(node.get("properties")) and node.get("additionalProperties") is False


def _admits_null(node):
    kind = node.get("type")
    return (kind == "null" or (isinstance(kind, list) and "null" in kind)
            or ("enum" in node and None in node["enum"]) or ("const" in node and node["const"] is None)
            or any(_admits_null(member) for member in node.get("anyOf", ())))


def _scalar(node):
    kind = node["type"]
    result = {"type": list(kind) if isinstance(kind, list) else kind}
    if set(result["type"] if isinstance(kind, list) else [kind]) & {"number", "integer"}:
        result.update({key: node[key] for key in _NUMERIC_BOUNDS if key in node})
    return result


def _json_type(value):
    """The JSON type a ``const``/``enum`` value needs beside it (OpenAI's platform API requires ``type``)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number" if isinstance(value, float) else "integer"
    if isinstance(value, str):
        return "string"
    raise ValueError("A const or enum value must be a JSON scalar")


def _typed_enum(values):
    """An enum with its ``type``; values of several types become an ``anyOf`` of one enum per type."""
    groups = {}
    for value in values:
        kind = _json_type(value)
        if kind == "integer" and any(isinstance(other, float) for other in values):
            kind = "number"
        groups.setdefault(kind, []).append(copy.deepcopy(value))
    members = [{"type": "null"} if kind == "null" else {"type": kind, "enum": items} for kind, items in groups.items()]
    if not members:
        raise ValueError("An empty enum has no strict form")
    return members[0] if len(members) == 1 else {"anyOf": members}


def _strict(node, depth, max_depth):
    if node is True or node == {}:
        raise ValueError("An unconstrained schema position has no strict form")
    if not isinstance(node, dict):
        raise ValueError("Unsupported schema node")
    members = _union(node)
    if members is not None:
        variants = []
        for member in members:
            variant = _strict(member, depth, max_depth)
            for item in variant.get("anyOf", [variant]):
                if item not in variants:
                    variants.append(item)
        return variants[0] if len(variants) == 1 else {"anyOf": variants}
    if "const" in node:
        return {"type": _json_type(node["const"]), "const": copy.deepcopy(node["const"])}
    if "enum" in node:
        return _typed_enum(node["enum"])
    if _is_object(node):
        if not _closed(node) or depth > max_depth:
            return dict(JSON_TEXT)
        required = set(node.get("required", ()))
        properties = {}
        for name, child in node["properties"].items():
            strict = _strict(child, depth + 1, max_depth)
            if strict == _NULL:
                continue  # Nothing to choose: decode restores it.
            if name not in required and not _admits_null(strict):
                strict = {"anyOf": strict["anyOf"] + [_NULL] if set(strict) == {"anyOf"} else [strict, _NULL]}
            properties[name] = strict
        # Discriminators first, then the shape's order; a shape adds the shorter forms it allows.
        variants = [{"type": "object", "properties": {name: properties[name] for name in names}, "required": names,
                     "additionalProperties": False}
                    for names in forms(properties, _discriminators_first(properties), required & set(properties),
                                       node.get(SHAPE))]
        return variants[0] if len(variants) == 1 else {"anyOf": variants}
    kind = node.get("type")
    if kind == "array":
        result = {"type": "array", "items": _strict(node.get("items", True), depth, max_depth)}
        # Small bounds (vectors, colors) shape the output; a large one would only
        # expand the decoding grammar, and the host checks lengths anyway.
        result.update({key: node[key] for key in ("minItems", "maxItems") if node.get(key, MAX_ITEMS_BOUND + 1)
                       <= MAX_ITEMS_BOUND})
        return result
    if kind in ("string", "number", "integer", "boolean", "null") or isinstance(kind, list):
        return _scalar(node)
    raise ValueError("Schema node has no strict form: %s" % sorted(node))


def _discriminators_first(properties):
    """The ``const`` properties (a union member's tool, kind, recipe, primitive or type), which are written first.

    The model writes a strict output's properties in schema order and a union
    member is fixed by the first properties it writes. With the discriminator
    behind other fields, those fields chose the member and decoding then
    forced its name: with properties in alphabetical order (
    ``arguments`` before ``tool``, ``parameters`` before ``recipe``) a program
    the model began with ``"mode"`` could only continue as a recipe, and
    "create a cube" ran fit-dimensions on the default cube.
    """
    return [name for name, child in properties.items() if "const" in child]


def strict_schema(schema, *, max_depth=MAX_DEPTH):
    """The strict variant of ``schema`` (references inlined; the caller may compact it again).

    ``max_depth`` counts object levels from the root (1); an object at a deeper
    level is carried as JSON text. Raises ValueError for a position the subset
    cannot express at all (an unconstrained ``{}``), so a schema change that
    would need one fails in tests, not in a model request.
    """
    root = _strict(inline_references(schema), 1, max_depth)
    if root.get("type") != "object":
        raise ValueError("A strict schema's root must be an object")
    return root


def _matches(value, node):
    """Whether ``value`` (a strict output) belongs to the original schema ``node``: picks a union member."""
    members = _union(node)
    if members is not None:
        return any(_matches(value, member) for member in members)
    if "const" in node:
        return value == node["const"] and type(value) is type(node["const"])
    if "enum" in node:
        return any(value == item and type(value) is type(item) for item in node["enum"])
    if _is_object(node):
        if isinstance(value, str):
            return True  # JSON text: the host's validator checks what it holds.
        if not isinstance(value, dict):
            return False
        properties = node.get("properties") or {}
        required = set(node.get("required", ()))
        if node.get("additionalProperties") is False and set(value) - set(properties):
            return False
        for name, child in properties.items():
            present = name in value and value[name] is not None
            if name in required and name not in value and child != _NULL:
                return False
            if present and ("const" in child or "enum" in child) and not _matches(value[name], child):
                return False
        return True
    kind = node.get("type")
    kinds = kind if isinstance(kind, list) else [kind] if kind else []
    return not kinds or any(_type_matches(value, item) for item in kinds)


def _type_matches(value, kind):
    if kind == "null":
        return value is None
    if kind == "boolean":
        return type(value) is bool
    if kind == "integer":
        return type(value) is int
    if kind == "number":
        return type(value) in (int, float)
    if kind == "string":
        return isinstance(value, str)
    if kind == "array":
        return isinstance(value, list)
    if kind == "object":
        return isinstance(value, dict)
    return False


class StrictDecodeError(ValueError):
    """A strict output whose JSON text does not hold an object; ``path`` names where."""

    def __init__(self, path, message):
        super().__init__("%s: %s" % (path or "output", message))
        self.path = path


def decode(value, schema, path=""):
    """The ordinary form of a strict output ``value`` of the ORIGINAL ``schema``.

    Inline references first (``inline_references``). A ``null`` optional
    property is dropped; JSON text where the original has an object is parsed
    and returned as written (the model read that object's exact schema). Raises
    StrictDecodeError for JSON text that is not an object. Anything else passes
    through for the host's exact validation, which rejects what does not fit.
    """
    members = _union(schema)
    if members is not None:
        for member in members:
            if _matches(value, member):
                return decode(value, member, path)
        return value
    if _is_object(schema):
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except ValueError as error:
                raise StrictDecodeError(path, "invalid JSON text (%s)" % error.msg) from None
            if not isinstance(parsed, dict):
                raise StrictDecodeError(path, "the JSON text must hold an object")
            return parsed
        if not isinstance(value, dict):
            return value
        properties, required = schema.get("properties") or {}, set(schema.get("required", ()))
        result = {}
        for name, item in value.items():
            if name in properties and item is None and name not in required:
                continue
            child = properties.get(name)
            result[name] = decode(item, child, "%s.%s" % (path, name) if path else name) if child else item
        result.update({name: None for name, child in properties.items() if child == _NULL and name not in result})
        return result
    if schema.get("type") == "array" and isinstance(value, list) and isinstance(schema.get("items"), dict):
        return [decode(item, schema["items"], "%s[%d]" % (path, index)) for index, item in enumerate(value)]
    return value


def encode(value, schema):
    """The strict output a model would write for an ordinary ``value`` of ``schema`` (tests and measurements).

    The inverse of ``decode`` for values the original schema accepts: omitted
    optional properties become ``null`` (or are left out with the rest of a
    shaped object's tail), and objects the strict variant carries as JSON text
    are serialized. ``max_depth`` must match the variant's.
    """
    return _encode(value, inline_references(schema), 1, MAX_DEPTH)


def _encode(value, node, depth, max_depth):
    members = _union(node)
    if members is not None:
        for member in members:
            if _accepts(value, member):
                return _encode(value, member, depth, max_depth)
        raise ValueError("Value matches no union member")
    if _is_object(node):
        if not _closed(node) or depth > max_depth:
            return json.dumps(value, separators=(",", ":"))
        written = {name: (None if name not in value else _encode(value[name], child, depth + 1, max_depth))
                   for name, child in node["properties"].items() if child != _NULL}
        first = [name for name in written if "const" in node["properties"][name]]
        required = [name for name in node.get("required", ()) if name in written]
        names = next(names for names in forms(written, first, required, node.get(SHAPE))
                     if all(written[name] is None for name in written if name not in names))
        return {name: written[name] for name in names}
    if node.get("type") == "array":
        return [_encode(item, node.get("items", True), depth, max_depth) for item in value]
    return copy.deepcopy(value)


def _accepts(value, node):
    """A structural acceptance test for ``encode`` (tests compare against jsonschema)."""
    members = _union(node)
    if members is not None:
        return any(_accepts(value, member) for member in members)
    if "const" in node or "enum" in node:
        return _matches(value, node)
    if _is_object(node):
        if not isinstance(value, dict):
            return False
        properties = node.get("properties") or {}
        if node.get("additionalProperties") is False and set(value) - set(properties):
            return False
        if set(node.get("required", ())) - set(value):
            return False
        return all(_accepts(item, properties[name]) for name, item in value.items() if name in properties)
    if node.get("type") == "array":
        return isinstance(value, list) and all(_accepts(item, node.get("items", {})) for item in value)
    return _matches(value, node)


_TYPES = frozenset({"string", "number", "integer", "boolean", "object", "array", "null"})
_KEYWORDS_FOR = {"object": {"properties", "required", "additionalProperties"},
                 "array": {"items", "minItems", "maxItems"},
                 "number": set(_NUMERIC_BOUNDS), "integer": set(_NUMERIC_BOUNDS)}
_ANY_TYPE = {"type", "description", "const", "enum"}


def _node_problems(node, path, definitions):
    """Where one node leaves the platform API's strict shape (a bare $ref, an anyOf, or a typed node)."""
    problems = []
    if "$ref" in node:
        if set(node) != {"$ref"}:
            problems.append(path + ": a $ref has no sibling keywords")
        return problems
    if "anyOf" in node:
        if set(node) - {"anyOf", "description"} or not isinstance(node["anyOf"], list) or not node["anyOf"]:
            problems.append(path + ": an anyOf is a non-empty list with nothing beside it but a description")
        return problems
    kind = node.get("type")
    kinds = kind if isinstance(kind, list) else [kind]
    if not kinds or any(item not in _TYPES for item in kinds):
        return [path + ": schema must have a 'type' key"]  # The platform API's own words (2026-10-09).
    allowed = set(_ANY_TYPE).union(*(_KEYWORDS_FOR.get(item, set()) for item in kinds))
    if set(node) - allowed - {"$defs"}:
        problems.append("%s: keywords %s do not apply to type %s" % (path, sorted(set(node) - allowed - {"$defs"}), kind))
    if "$defs" in node and path != "root":
        problems.append(path + ": $defs only at the root")
    values = ([node["const"]] if "const" in node else []) + (list(node["enum"]) if isinstance(node.get("enum"), list) else [])
    if "enum" in node and not node["enum"]:
        problems.append(path + ": an empty enum")
    for value in values:
        try:
            value_kind = _json_type(value)
        except ValueError:
            value_kind = None
        if value_kind not in kinds and not (value_kind == "integer" and "number" in kinds):
            problems.append("%s: value %r is not of type %s" % (path, value, kind))
    if "array" in kinds and "items" not in node:
        problems.append(path + ": an array needs items")
    if "description" in node and not isinstance(node["description"], str):
        problems.append(path + ": a description is a string")
    return problems


def strict_problems(schema):
    """Where ``schema`` leaves OpenAI's strict subset or its documented limits (an empty list when it fits).

    The rules are those of the platform API's validator, the stricter of the two
    OpenAI endpoints Codex reaches (ChatGPT sign-in accepted a ``const`` without
    ``type``; an API key did not, 2026-10-09): every node is a bare ``$ref``, an
    ``anyOf`` or a typed node whose keywords fit its type; every object closed
    with every property required; the root an object; ``$defs`` only at the
    root; at most 10 levels (objects and arrays both counted, the stricter
    reading), 5,000 properties, 1,000 enum values and 120,000 characters of
    names and values.
    """
    problems, totals = [], {"properties": 0, "chars": 0, "enum": 0}
    definitions = schema.get("$defs", {})
    totals["chars"] += sum(len(name) for name in definitions)

    def visit(node, path, depth, active):
        if not isinstance(node, dict):
            problems.append(path + ": not an object schema")
            return
        unknown = set(node) - STRICT_KEYWORDS
        if unknown:
            problems.append("%s: unsupported keywords %s" % (path, sorted(unknown)))
        problems.extend(_node_problems(node, path, definitions))
        reference = node.get("$ref")
        if isinstance(reference, str):
            name = reference[len(_REFERENCE):] if reference.startswith(_REFERENCE) else None
            if name not in definitions or name in active:
                problems.append(path + ": unresolvable or recursive reference")
                return
            visit(definitions[name], path, depth, active | {name})
            return
        if "enum" in node:
            totals["enum"] += len(node["enum"])
            texts = [str(item) for item in node["enum"]]
            totals["chars"] += sum(len(text) for text in texts)
            if len(texts) > LARGE_ENUM and sum(map(len, texts)) > LARGE_ENUM_CHARS:
                problems.append(path + ": enum too large")
        if "const" in node:
            totals["chars"] += len(str(node["const"]))
        for member in node.get("anyOf", ()):
            visit(member, path + "|", depth, active)
        if node.get("type") in ("object", "array") and depth > MAX_DEPTH:
            problems.append("%s: nesting depth %d exceeds %d" % (path, depth, MAX_DEPTH))
        if node.get("type") == "object":
            properties = node.get("properties", {})
            if node.get("additionalProperties") is not False or list(node.get("required", ())) != list(properties):
                problems.append(path + ": objects need additionalProperties false and every property required")
            totals["properties"] += len(properties)
            totals["chars"] += sum(len(name) for name in properties)
            for name, child in properties.items():
                visit(child, path + "." + name, depth + 1, active)
        if node.get("type") == "array" and "items" in node:
            visit(node["items"], path + "[]", depth + 1, active)

    if schema.get("type") != "object" or "anyOf" in schema:
        problems.append("root: must be an object, not a union")
    visit(schema, "root", 1, frozenset())
    if totals["properties"] > MAX_PROPERTIES:
        problems.append("%d object properties exceed %d" % (totals["properties"], MAX_PROPERTIES))
    if totals["chars"] > MAX_STRING_CHARS:
        problems.append("%d characters of names and values exceed %d" % (totals["chars"], MAX_STRING_CHARS))
    if totals["enum"] > MAX_ENUM_VALUES:
        problems.append("%d enum values exceed %d" % (totals["enum"], MAX_ENUM_VALUES))
    return problems
