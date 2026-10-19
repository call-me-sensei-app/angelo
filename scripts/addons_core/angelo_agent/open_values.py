# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Objects in open settings maps: a handle written alone, stored as ``{"object": handle}`` (pure Python).

An open map's values are typed by RNA when the program is staged (property_set
values; generic modifier, constraint, particle and pose-bone settings; the
checks of them), so its schema cannot tell text from an object: a pointer there
is stored as ``{"object": handle}``, which is also how read-backs show it. Every
typed pointer field takes the handle or ``$alias`` alone as well
(``action_schema_types.POINTER``), and models write a map's pointers that way
too, so a string in an open map is stored as ``{"object": ...}`` where it can
only name an object:

* a long handle (``scn:<epoch>:<uid>``): ``validate_operation`` and ``validate_checks``;
* a short handle ``o<n>`` the conversation was shown: ``identifiers.expand_call``,
  which stores its long form;
* the ``$alias`` of an object an earlier operation of the program created:
  ``program_spec.validate_program``.

Any other string stays text, so text such as ``"$5 off"`` or a name is never
taken for an object. A text setting given one of these forms is refused by its
RNA type (or a common path's schema) rather than written as an object. Maps
whose values are never objects (node sockets and settings, F-Curve modifier
settings, operator properties) take ``SCALAR_VALUE``, whose schema offers no
``{"object": ...}`` (``action_properties``).
"""

import re

from .action_schema_types import HANDLE, OBJECT_REFERENCE

LONG_HANDLE = re.compile(r"scn:[a-f0-9]{32}:[1-9][0-9]{0,19}\Z")  # identifiers.LONG_HANDLE
SHORT_HANDLE = re.compile(r"o[1-9][0-9]{0,8}\Z")  # identifiers.SHORT_HANDLE


def open_position(schema):
    """Whether ``schema`` types a value RNA types at run time that may name an object (a map's value)."""
    if schema is True:
        return True
    union = schema.get("oneOf") if isinstance(schema, dict) else None
    return isinstance(union, list) and OBJECT_REFERENCE in union and HANDLE not in union


def long_handle(text):
    return text if LONG_HANDLE.fullmatch(text) else None


def _selects(value, branch):
    properties = branch.get("properties") if isinstance(branch, dict) else None
    return isinstance(properties, dict) and branch.get("type", "object") == "object" and all(
        value.get(key) == child["const"] for key, child in properties.items() if isinstance(child, dict) and "const" in child)


def wrapped(value, schema, reference):
    """``value`` with each open-map string ``reference(string)`` names an object for as ``{"object": ...}``.

    ``reference`` returns the reference to store, or None for text. Unions of
    objects are followed by their const fields, as ``identifiers`` does.
    """
    if isinstance(value, str):
        named = reference(value) if open_position(schema) else None
        return {"object": named} if named else value
    if not isinstance(schema, dict):
        return value
    if isinstance(value, list):
        items = schema.get("items")
        return [wrapped(item, items, reference) for item in value] if isinstance(items, dict) else value
    if not isinstance(value, dict):
        return value
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        branches = [branch for branch in schema.get("oneOf") or schema.get("anyOf") or () if _selects(value, branch)]
        if len(branches) > 1:  # A typed variant whose const matched wins over a generic one.
            typed = [branch for branch in branches if any(isinstance(child, dict) and "const" in child
                                                          for child in branch["properties"].values())]
            branches = typed if len(typed) == 1 else branches
        return wrapped(value, branches[0], reference) if len(branches) == 1 else value
    extra = schema.get("additionalProperties")
    return {key: wrapped(item, properties[key] if key in properties else extra, reference) for key, item in value.items()}


def open_reference(value, schema, table):
    """A short handle ``table`` numbered, in an open map's value position, as ``{"object": long handle}``."""
    known = getattr(table, "known_handle", None)
    if callable(known) and isinstance(value, str) and SHORT_HANDLE.fullmatch(value) and open_position(schema):
        handle = known(value)
        return {"object": handle} if handle else value
    return value
