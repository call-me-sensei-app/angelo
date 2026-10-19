# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The text Claude Code answers a StructuredOutput schema mismatch with, approximated without Node.

Claude Code 2.1.293 validates structured output with Ajv
(``allErrors: true, validateFormats: false``) and writes each error as
``<instancePath or "root">: <message>``, joined by ", " after "Output does not
match required schema: ". ``additionalProperties`` adds ``('x' is not
allowed)``, length, item and property bounds add ``(got N)``, ``enum`` and
``const`` add their value as JSON cut at 300 characters with "…". A result
longer than 11,024 characters (10,000 and a grace of 1,024) keeps its first
and last 5,000.

This follows Ajv's keyword semantics for the subset Angelo's plan schema uses
(``$ref``, type, bounds, pattern, properties, required, additionalProperties,
propertyNames, items, enum, const, not, anyOf, oneOf, allOf, if/then/else), in
Ajv's order: the keywords of any type (const, enum, not, anyOf, oneOf, allOf,
if) before those of the value's type, so an object's "required" errors follow
its unions'. Calibration: for a recorded Opus plan of the camera mini one-shot against the
plan schema of that build it writes 26,337 characters, and what it shows of
them is the text Claude Code recorded, character for character (the first and
last 5,000).
"""

import json
import re

KEEP = 5000
GRACE = 1024  # Claude Code cuts only a text longer than 2 * KEEP + GRACE (11,024 characters).
_VALUE_LIMIT = 300


def _type_of(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return {str: "string", list: "array", dict: "object"}.get(type(value))


def _is(value, kind):
    actual = _type_of(value)
    if kind == "number":
        return actual in ("number", "integer")
    if kind == "integer":
        return actual == "integer" or (actual == "number" and float(value).is_integer())
    return actual == kind


def _shown(value):
    text = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    return text if len(text) <= _VALUE_LIMIT else text[:_VALUE_LIMIT] + "…"


def _equal(a, b):
    return a == b and (_type_of(a) == _type_of(b) or (_is(a, "number") and _is(b, "number")))


# Ajv's keyword order (v8, draft-07 vocabularies): the keywords of any type first, then those of the value's type,
# so an object's "required" and property errors follow its anyOf, oneOf, allOf and if/then ones.
_GROUPS = {"number": ("maximum", "minimum"), "string": ("maxLength", "minLength", "pattern"),
           "array": ("maxItems", "minItems", "items"),
           "object": ("maxProperties", "minProperties", "required", "propertyNames", "additionalProperties",
                      "properties")}


class _Validator:
    def __init__(self, root):
        self.root = root

    def errors(self, value, schema, path=""):
        if schema is True or schema == {}:
            return []
        if schema is False:
            return [(path, "boolean schema is false")]
        found = []
        if "$ref" in schema:
            name = schema["$ref"].rsplit("/", 1)[1]
            found += self.errors(value, self.root["$defs"][name], path)
        kinds = schema.get("type")
        kinds = [kinds] if isinstance(kinds, str) else kinds
        typed = kinds is None or any(_is(value, kind) for kind in kinds)
        # A schema of one type with keywords of that type reports a mismatch where they run, else first.
        late = kinds is not None and len(kinds) == 1 and any(key in schema for key in _GROUPS.get(kinds[0], ()))
        if not typed and not late:
            found.append((path, "must be " + ",".join(kinds)))
        found += self._any_type(value, schema, path)
        if not typed and late:
            found.append((path, "must be " + ",".join(kinds)))
        else:
            found += self._of_type(value, schema, path)
        return found

    def _any_type(self, value, schema, path):
        found = []
        if "const" in schema and not _equal(value, schema["const"]):
            found.append((path, "must be equal to constant: " + _shown(schema["const"])))
        if "enum" in schema and not any(_equal(value, item) for item in schema["enum"]):
            found.append((path, "must be equal to one of the allowed values: " + _shown(schema["enum"])))
        if "not" in schema and not self.errors(value, schema["not"], path):
            found.append((path, "must NOT be valid"))
        if "anyOf" in schema:
            failed = []
            for member in schema["anyOf"]:
                member_errors = self.errors(value, member, path)
                if not member_errors:
                    failed = None
                    break
                failed += member_errors
            if failed is not None:
                found += failed + [(path, "must match a schema in anyOf")]
        if "oneOf" in schema:
            failed, passing = [], 0
            for member in schema["oneOf"]:
                member_errors = self.errors(value, member, path)
                passing += not member_errors
                failed += member_errors
            if passing != 1:
                found += (failed if passing == 0 else []) + [(path, "must match exactly one schema in oneOf")]
        for member in schema.get("allOf", ()):
            found += self.errors(value, member, path)
        if "if" in schema:
            branch, label = (schema.get("then"), "then") if not self.errors(value, schema["if"], path) else (
                schema.get("else"), "else")
            branch_errors = self.errors(value, branch, path) if branch is not None else []
            found += branch_errors + ([(path, 'must match "%s" schema' % label)] if branch_errors else [])
        return found

    def _of_type(self, value, schema, path):
        found, actual = [], _type_of(value)
        if actual in ("number", "integer"):
            if "maximum" in schema and value > schema["maximum"]:
                found.append((path, "must be <= %s" % schema["maximum"]))
            if "minimum" in schema and value < schema["minimum"]:
                found.append((path, "must be >= %s" % schema["minimum"]))
        if actual == "string":
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                found.append((path, "must NOT have more than %d characters (got %d)" % (schema["maxLength"], len(value))))
            if "minLength" in schema and len(value) < schema["minLength"]:
                found.append((path, "must NOT have fewer than %d characters (got %d)" % (schema["minLength"], len(value))))
            if "pattern" in schema and not re.search(schema["pattern"], value):
                found.append((path, 'must match pattern "%s"' % schema["pattern"]))
        if actual == "array":
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                found.append((path, "must NOT have more than %d items (got %d)" % (schema["maxItems"], len(value))))
            if "minItems" in schema and len(value) < schema["minItems"]:
                found.append((path, "must NOT have fewer than %d items (got %d)" % (schema["minItems"], len(value))))
            if isinstance(schema.get("items"), dict):
                for index, item in enumerate(value):
                    found += self.errors(item, schema["items"], "%s/%d" % (path, index))
        if actual == "object":
            count = len(value)
            if "maxProperties" in schema and count > schema["maxProperties"]:
                found.append((path, "must NOT have more than %d properties (got %d)" % (schema["maxProperties"], count)))
            if "minProperties" in schema and count < schema["minProperties"]:
                found.append((path, "must NOT have fewer than %d properties (got %d)" % (schema["minProperties"], count)))
            found += [(path, "must have required property '%s'" % key) for key in schema.get("required", ())
                      if key not in value]
            for key in value if "propertyNames" in schema else ():
                names = self.errors(key, schema["propertyNames"], path)
                found += names + ([(path, "property name must be valid")] if names else [])
            properties, extra = schema.get("properties", {}), schema.get("additionalProperties")
            for key in value:
                if key not in properties and extra is False:
                    found.append((path, "must NOT have additional properties ('%s' is not allowed)" % key))
                elif key not in properties and isinstance(extra, dict):
                    found += self.errors(value[key], extra, path + "/" + key)
            for key, child in properties.items():
                if key in value:
                    found += self.errors(value[key], child, path + "/" + key)
        return found


def mismatch(value, schema):
    """Claude Code's whole error text for ``value``, or None when the schema accepts it."""
    errors = _Validator(schema).errors(value, schema)
    if not errors:
        return None
    return "Output does not match required schema: " + ", ".join(
        "%s: %s" % (path or "root", message) for path, message in errors)


def shown(text):
    """What the model reads of a mismatch: the first and last 5,000 characters of one over 11,024."""
    if text is None or len(text) <= 2 * KEEP + GRACE:
        return text
    return text[:KEEP] + "\n\n... [%d characters truncated] ...\n\n" % (len(text) - 2 * KEEP) + text[-KEEP:]
