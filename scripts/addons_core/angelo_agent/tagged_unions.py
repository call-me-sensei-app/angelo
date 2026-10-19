# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tagged unions of Claude's plan schema written so a mismatch reports only its own branch.

Claude Code checks every typed plan against its schema with Ajv (``allErrors``)
and answers a mismatch with every error it found. A ``oneOf`` reports the errors
of each of its members, so a plan with one bad field in a program came back with
the errors of some forty tools and of every action kind: about 26,000
characters, of which Claude Code keeps the first and last 5,000. In each of the
six recorded Claude schema refusals the line naming the cause was in
the part cut out, and the model rewrote the plan blind, at 3.7-4.4k written
tokens a round.

``discriminated`` rewrites each union whose members are objects told apart by a
constant field (a plan operation's ``tool``, a program operation's or check's
``kind``, a modifier's ``type``) as one object whose tag lists every value, plus
an ``allOf`` of ``if`` (the tag's value) / ``then`` (that member). Ajv evaluates a
``then`` only where its ``if`` holds, so an error names the field of the member
the model chose: 150-500 characters for the mistakes measured
(doc/angelo/agent_execution.rst, "Referring to objects, data and values"). A
member whose tag is an ``enum`` or a pattern (the generic variants of deferred
tools, kinds and types) has no ``then``: the host checks it, as before.
The plans each form accepts are the same (``test_native_schema_consistency``).
An ``if`` states no ``required``: a member without its tag key satisfies every
``if`` and is checked against every ``then`` (7-10 KB, the missing tag listed
last); naming the tag would cost every plan 1,380 B and no audited refusal
needed it (``test_native_schema_review``).
Codex's response format is a strict grammar, which has no ``if``, and keeps its
``anyOf``.
"""

import copy

TAGS = ("tool", "kind", "type")


def _tag_values(schema):
    """The values a member's tag schema names: a const or an enum, else None (a pattern: any other value)."""
    if not isinstance(schema, dict):
        return None
    if "const" in schema:
        return [schema["const"]]
    return list(schema["enum"]) if isinstance(schema.get("enum"), list) else None


def _tag(members, tags):
    for name in tags:
        if all(isinstance(member, dict) and member.get("type") == "object"
               and isinstance((member.get("properties") or {}).get(name), dict) for member in members) and any(
                _tag_values(member["properties"][name]) for member in members):
            return name
    return None


def _without_tag(member, tag, closed_base):
    """A member as a ``then``: its fields but the tag (which the ``if`` states)."""
    then = {key: value for key, value in member.items() if key != "type"}
    properties = dict(member["properties"])
    if closed_base:
        del properties[tag]
        then.pop("additionalProperties", None)
        then["required"] = [name for name in member.get("required", ()) if name not in closed_base]
        if not then["required"]:
            del then["required"]
    else:
        properties[tag] = {}  # The member stays closed; the ``if`` checked the tag.
    then["properties"] = properties
    return then


def discriminate(union, tags=TAGS, smallest=3):
    """``{"oneOf": members}`` as a tag and if/then branches, or the union unchanged.

    ``smallest``: a union of fewer members is left as it is (two members report little).
    """
    members = union.get("oneOf") if isinstance(union, dict) and set(union) - {"description"} == {"oneOf"} else None
    tag = _tag(members, tags) if isinstance(members, list) and len(members) >= smallest else None
    if tag is None:
        return union
    groups, generic, values = {}, [], []
    for member in members:
        named = _tag_values(member["properties"][tag])
        if named is None or len(named) > 1:
            generic.append(member)  # Deferred names or any other value: the host checks it.
            values.append(member["properties"][tag])
            continue
        groups.setdefault(named[0], []).append(member)
    fields = {name for member in members for name in member["properties"]}
    closed = all(member.get("additionalProperties") is False and set(member["properties"]) == fields
                 for member in members)
    shared = set.intersection(*(set(member.get("required", ())) for member in members))
    base_required = [name for name in members[0].get("required", ()) if name in shared]  # In the members' order.
    if closed and len(generic) <= 1:
        base_fields = {name: copy.deepcopy(schema) for name, schema in (generic[0]["properties"] if generic else {}).items()
                       if name != tag}
        base_fields.update({name: {} for name in fields if name not in base_fields and name != tag})
        closed_base = set(base_required)
    else:
        base_fields, closed_base = {}, None
    branches = []
    for value, group in groups.items():
        rest = [name for name in tags if name != tag]
        then = (_without_tag(group[0], tag, closed_base) if len(group) == 1 else
                discriminate({"oneOf": [dict(member, properties=dict(member["properties"], **{tag: {}}))
                                        for member in group]}, rest, smallest=2))
        if then.get("oneOf"):
            return union  # Members that only a tag this module does not know tells apart.
        if len(group) > 1:
            then.pop("type", None)
        branches.append({"if": {"properties": {tag: {"const": value}}}, "then": then})
    names = [value for value in groups] + [value for schema in values for value in (_tag_values(schema) or ())]
    others = [schema for schema in values if _tag_values(schema) is None]
    tag_schema = {"enum": names} if not others else {"anyOf": [{"enum": names}, *others]}
    result = {"type": "object", "properties": {tag: tag_schema, **base_fields}, "required": base_required or [tag]}
    if closed_base is not None:
        result["additionalProperties"] = False
    result["allOf"] = branches
    if "description" in union:  # What the members' schemas cannot say (action_schemas.SETTINGS_NOTE).
        result["description"] = union["description"]
    return result


def discriminated(node):
    """A copy of a schema with every tagged ``oneOf`` discriminated, innermost first."""
    if isinstance(node, list):
        return [discriminated(item) for item in node]
    if not isinstance(node, dict):
        return node
    result = {key: (copy.deepcopy(value) if key in ("enum", "const", "default", "examples") else
                    {name: discriminated(child) for name, child in value.items()} if key == "properties"
                    else discriminated(value)) for key, value in node.items()}
    if set(result) - {"description"} == {"oneOf"}:
        return discriminate(result)
    return result


def members(union):
    """``(tag, {value: member}, other)`` of a tagged union, discriminated or a plain ``oneOf`` (fewer than three
    members): each tag value's member (as its ``then``), in order, and the tag's other values (generic names
    or patterns), for readers of the plan's shape."""
    if "oneOf" in union:
        tag = _tag(union["oneOf"], TAGS)
        exact = {member["properties"][tag]["const"]: member for member in union["oneOf"]
                 if "const" in member["properties"][tag]}
        other = [value for member in union["oneOf"] if "const" not in member["properties"][tag]
                 for value in (_tag_values(member["properties"][tag]) or [member["properties"][tag]])]
        return tag, exact, other
    properties = union.get("properties") or {}
    tag = next(name for name in TAGS if name in properties and "allOf" in union)
    exact = {branch["if"]["properties"][tag]["const"]: branch["then"] for branch in union["allOf"]}
    schema = properties[tag]
    named = schema["enum"] if "enum" in schema else schema["anyOf"][0]["enum"]
    other = [value for value in named if value not in exact] + list(schema.get("anyOf", [None])[1:])
    return tag, exact, other
