# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Lossless, locally scoped sharing of repeated static JSON Schema fragments.

Only standard schema-valued keyword positions are traversed. Values of enum,
const, default, examples and unknown/annotation keywords are data, never schemas.
Every emitted reference resolves in this document's top-level ``$defs``. Call
this on a complete inputSchema or on the complete wrapped plan schema, NOT on
subschemas which will later be embedded under a different document root.

Existing references, definitions, identifiers and anchors are deliberately a
deep-copy no-op: relocating them would require dialect/scope-aware resolution.
Compaction is cached by immutable schema content, with fresh independent copies
returned to callers. This belongs in static tool assembly, not scene execution.
"""

from collections import Counter
import copy
from functools import lru_cache
import json


MIN_SHARED_BYTES = 100
_SCHEMA_MAPS = frozenset({"properties", "patternProperties", "dependentSchemas"})
_SCHEMA_LISTS = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})
_SCHEMA_VALUES = frozenset({"additionalProperties", "additionalItems", "contains",
                          "unevaluatedProperties", "unevaluatedItems", "propertyNames",
                          "contentSchema", "not", "if", "then", "else"})
_SCOPE_KEYWORDS = frozenset({"$ref", "$defs", "definitions", "$id", "id", "$anchor",
                           "$dynamicRef", "$dynamicAnchor", "$recursiveRef", "$recursiveAnchor",
                           "$vocabulary"})
_REFERENCE_PREFIX = "#/$defs/"


def _is_schema(value):
    return isinstance(value, dict) or type(value) is bool


def _encode(value, preserve_order=False):
    return json.dumps(value, ensure_ascii=False, sort_keys=not preserve_order, allow_nan=False, separators=(",", ":"))


def _children(node):
    """Yield schema nodes, not property maps, instance data or dependency lists."""
    if not isinstance(node, dict):
        return
    for keyword, value in node.items():
        if keyword in _SCHEMA_MAPS or keyword == "dependencies":
            if isinstance(value, dict):
                yield from (child for child in value.values() if _is_schema(child))
        elif keyword in _SCHEMA_LISTS or (keyword == "items" and isinstance(value, list)):
            if isinstance(value, list):
                yield from (child for child in value if _is_schema(child))
        elif (keyword in _SCHEMA_VALUES or keyword == "items") and _is_schema(value):
            yield value


def _walk(node, active=None):
    # The same Python dict may legitimately occur at several schema positions.
    # Count those occurrences separately, but reject actual non-JSON cycles.
    active = set() if active is None else active
    if isinstance(node, dict):
        pointer = id(node)
        if pointer in active:
            raise ValueError("JSON Schema must not contain cyclic Python containers")
        active.add(pointer)
    yield node
    for child in _children(node):
        yield from _walk(child, active)
    if isinstance(node, dict):
        active.remove(pointer)


def _rewrite(node, replacement):
    """Deep-copy while applying replacement ONLY at schema node positions."""
    replaced = replacement(node)
    if replaced is not None:
        return replaced
    if not isinstance(node, dict):
        return copy.deepcopy(node)
    result = {}
    for keyword, value in node.items():
        if (keyword in _SCHEMA_MAPS or keyword == "dependencies") and isinstance(value, dict):
            result[keyword] = {name: _rewrite(child, replacement) if _is_schema(child) else copy.deepcopy(child)
                               for name, child in value.items()}
        elif (keyword in _SCHEMA_LISTS or keyword == "items") and isinstance(value, list):
            result[keyword] = [_rewrite(child, replacement) if _is_schema(child) else copy.deepcopy(child)
                               for child in value]
        elif (keyword in _SCHEMA_VALUES or keyword == "items") and _is_schema(value):
            result[keyword] = _rewrite(value, replacement)
        else:
            result[keyword] = copy.deepcopy(value)
    return result


def _reference_name(node):
    if isinstance(node, dict) and set(node) == {"$ref"}:
        value = node["$ref"]
        if isinstance(value, str) and value.startswith(_REFERENCE_PREFIX):
            return value[len(_REFERENCE_PREFIX):]
    return None


def _prune_definitions(root, definitions):
    """Do not pay for a child shared only because its parent was duplicated.

    Counts are physical references in the compact document, not in its expanded
    projection. Inlining a single-use definition preserves its child's counts.
    Removing unused definitions can expose further unused ones, hence the loop.
    """
    while definitions:
        counts = Counter(_reference_name(node) for node in _walk(root))
        for body in definitions.values():
            counts.update(_reference_name(node) for node in _walk(body))
        removable = {name for name in definitions if counts[name] < 2}
        if not removable:
            break

        def inline(node):
            name = _reference_name(node)
            if name in removable:
                return _rewrite(definitions[name], inline)
            return None

        root = _rewrite(root, inline)
        definitions = {name: _rewrite(body, inline) for name, body in definitions.items()
                       if name not in removable}
    return root, definitions


def _definition_name(index):
    """Dense identifiers for surviving generated definitions: a..Z, aa..aZ..."""
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    name = ""
    while True:
        name = alphabet[index % len(alphabet)] + name
        index = index // len(alphabet) - 1
        if index < 0:
            return name


def _rename_definitions(root, definitions):
    # Only our generated, unscoped references reach here. Literal dictionaries
    # are not visited, and the public entry point preserves existing scopes.
    names = {old: _definition_name(index) for index, old in enumerate(definitions)}

    def rename(node):
        old = _reference_name(node)
        return {"$ref": _REFERENCE_PREFIX + names[old]} if old is not None else None

    return (_rewrite(root, rename),
            {names[old]: _rewrite(body, rename) for old, body in definitions.items()})


@lru_cache(maxsize=64)
def _compact_serialized(serialized, min_shared_bytes, preserve_order=False):
    schema = json.loads(serialized)
    counts, fragments = Counter(), {}
    for node in _walk(schema):
        if not isinstance(node, dict):
            continue
        encoded = _encode(node, preserve_order)
        if len(encoded.encode("utf-8")) >= min_shared_bytes:
            counts[encoded] += 1
            fragments.setdefault(encoded, node)
    candidates = sorted(encoded for encoded, count in counts.items() if count > 1)
    if not candidates:
        return schema
    names = {encoded: "s" + str(index) for index, encoded in enumerate(candidates)}

    def reference(node):
        if isinstance(node, dict):
            name = names.get(_encode(node, preserve_order))
            if name is not None:
                return {"$ref": _REFERENCE_PREFIX + name}
        return None

    root = _rewrite(schema, reference)
    # Rewrite each definition's CHILDREN, not its root. Proper descendants have
    # strictly shorter serialized content, so reference edges cannot form cycles.
    definitions = {names[encoded]: _rewrite(fragments[encoded], lambda node, body=fragments[encoded]:
                                            None if node is body else reference(node))
                   for encoded in candidates}
    root, definitions = _prune_definitions(root, definitions)
    if definitions:
        root, definitions = _rename_definitions(root, definitions)
        root["$defs"] = definitions
    return root if len(_encode(root).encode("utf-8")) < len(serialized.encode("utf-8")) else schema


def compact_schema(schema, min_shared_bytes=MIN_SHARED_BYTES, *, preserve_order=False):
    """Return an independent, constraint-equivalent compact JSON Schema.

    Inputs are JSON dictionaries or boolean schemas. No literals or constraints
    are removed or broadened. Schema-scoped existing references/definitions are
    a conservative no-op, including recursive references. Literal keys named
    ``$ref``/``$defs`` inside defaults or enums do not trigger that no-op.
    ``min_shared_bytes`` is the smallest repeated fragment worth a reference;
    a smaller value trades readability for size.

    The result's keys are sorted unless ``preserve_order``: a strict
    structured-output schema must keep its property order, because the model
    writes properties in schema order (codex_plan, toolset 30).
    """
    if type(min_shared_bytes) is not int or min_shared_bytes < 32:
        raise ValueError("Shared schema fragments must be at least 32 bytes")
    if not _is_schema(schema):
        raise TypeError("compact_schema expects an object or boolean JSON Schema")
    for node in _walk(schema):
        if isinstance(node, dict) and _SCOPE_KEYWORDS.intersection(node):
            return copy.deepcopy(schema)
    # Ordered consumers must not share otherwise equal fragments whose property
    # order differs: structured-output generation follows that property order.
    serialized = (json.dumps(schema, ensure_ascii=False, allow_nan=False, separators=(",", ":")) if preserve_order
                  else _encode(schema))
    return copy.deepcopy(_compact_serialized(serialized, min_shared_bytes, preserve_order))


def compact_nullable_scalars(schema):
    """Shorten generated scalar-or-null forms after definition sharing.

    Keeping shared definitions first matters: inlining a small nullable type
    before sharing can cost more than its references. Only a scalar and an
    unconstrained null branch are combined; references are never resolved and
    property order, other unions, containers and literal data are untouched.
    """
    def nullable(node):
        members = node.get("anyOf") if isinstance(node, dict) and set(node) == {"anyOf"} else None
        if not isinstance(members, list) or len(members) != 2 or members[1] != {"type": "null"}:
            return None
        scalar = members[0]
        allowed = {"type", "enum", "const", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "description"}
        if (not isinstance(scalar, dict) or scalar.get("type") not in ("string", "boolean", "integer", "number")
                or set(scalar) - allowed or {"const", "enum"} <= set(scalar)):
            return None
        result = copy.deepcopy(scalar)
        result["type"] = [scalar["type"], "null"]
        if "enum" in result:
            result["enum"] = result["enum"] + [None]
        if "const" in result:
            result["enum"] = [result.pop("const"), None]
        return result if len(_encode(result, True)) < len(_encode(node, True)) else None

    result = _rewrite(schema, nullable)
    if isinstance(schema.get("$defs"), dict):
        result["$defs"] = {name: _rewrite(body, nullable) for name, body in schema["$defs"].items()}
    return result
