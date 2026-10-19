# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``node_edit`` and the ``node_graph`` check: schemas, cross-field rules and derived checks (pure Python, no bpy).

``node_edit`` makes targeted edits to one EXISTING node tree of its target:
the tree of a material on an object's slots (``material``, by its live name),
the node group of a Geometry Nodes modifier (``modifier``) or the bound
scene's World (``world``, with the scene handle as target); ``group`` edits
inside a group node's group of a material tree instead. ``edits`` is an ordered
list of at most 32 steps, applied to a private staged copy and validated as a
whole: nodes are named by their exact names in the tree, never by position.

Steps (``op``): ``add`` a node by ``bl_idname`` (settings by RNA path, input
defaults by socket name or identifier, an image by resource ID, a location and
a frame), ``remove`` (optionally reconnecting through the node like Blender's
Delete with Reconnect), ``link``, ``unlink`` the links into one input,
``detach`` every link of a node, ``set`` settings (label, colour, mute,
collapse...) and input or Value/RGB output defaults, ``parent`` nodes to a
frame or detach them, ``duplicate`` exactly, ``swap`` a node's type keeping
compatible links and values, ``insert`` a node on a link, ``group`` nodes into
a new node group, ``ungroup`` a group node, and ``interface`` sockets of the
edited group.

A tree another user shares (other objects, scenes, modifiers or a fake user)
is refused unless ``shared`` is ``ISOLATE``: then only this target's reference
receives the edited copy and every other user keeps the original. A material
(never a modifier's group, the World or a nested group) may instead be edited
for ``ALL_USERS``: every object of the bound scene that uses it shows the edit
and the result names them (``material_sharing``). The kernel is ``node_edit``;
commit, revision and the check predicate are ``node_edit_commit`` and
``node_graph_check``.
"""

import copy
import re

from .action_schema_types import array, boolean, enum, nullable, number, obj, text
from .action_world_spec import IMAGE_ID
from .node_generic import SOCKET_PATTERN
from .refusal_codes import RefusalError

MAX_EDITS = 32
MAX_CHECK_ITEMS = 32
# Generic node types (node_generic.TYPE_PATTERN) plus frames, which carry layout only.
TYPE_PATTERN = (r"^(?:ShaderNode|GeometryNode|FunctionNode|NodeReroute|NodeGroupInput|NodeGroupOutput|NodeFrame)"
                r"[A-Za-z0-9]*$")
# Settings a step never writes: selection is editor state; existing nodes keep their place (rna_rules.NODE_PLACE).
DENIED_SETTINGS = frozenset({"select", "location", "location_absolute"})
INTERFACE_TYPES = ("FLOAT", "INT", "BOOLEAN", "VECTOR", "COLOR", "SHADER")
# Staging markers (ID properties on staged copies) and the kernel's tree rules, shared with commit and tests.
EDITED = "_angelo_node_edit_source_uid"  # The live session UID an edited staged tree copies.
MODE = "_angelo_node_edit_mode"  # REMAP (every use of the live tree) or ASSIGN (only the target's references).
SOURCE = "_angelo_stage_source_uid"  # program_objects.MATERIAL_SOURCE: an unedited staged clone.
SHARING = "node_edit_sharing"  # stage.extras: handle -> {live uid: {others, name, who, data_shared}}
MAX_TREE_NODES, MAX_TREE_LINKS = 256, 512
OUTPUT_TYPES = frozenset({"ShaderNodeOutputMaterial", "ShaderNodeOutputWorld", "ShaderNodeOutputLight",
                          "ShaderNodeOutputAOV", "ShaderNodeOutputLineStyle", "NodeGroupOutput"})
SURFACE_OUTPUTS = {"ShaderNodeOutputMaterial": "Surface", "ShaderNodeOutputWorld": "Surface"}


def is_edited(block):
    """A staged copy a step of this program edited (a marker a fresh clone copied from a live tree does not count)."""
    keys = block.keys()
    return EDITED in keys and SOURCE not in keys
NODE_NAME = text(63)
# Patterns are checked by canonical(), not repeated in the exact schema a lookup returns.
TYPE = text(64)
SOCKET = text(64)
LOCATION = array(number(-100000, 100000), 2, 2)


def _open_map(pattern):
    from .action_properties import SCALAR_VALUE  # Never an object: _screen_values refuses one.
    return {"type": "object", "properties": {}, "additionalProperties": SCALAR_VALUE, "propertyNames": {"pattern": pattern},
            "maxProperties": 32, "default": {}}


def _op(name, fields, required=()):
    return obj(dict({"op": {"const": name}}, **fields), ["op", *required])


def _variants():
    """Each step's exact closed schema by op; canonical() validates every step against its op's variant."""
    from .rna_rules import PATH_PATTERN
    settings, sockets = _open_map(PATH_PATTERN), _open_map(SOCKET_PATTERN)
    ends = {"from_node": NODE_NAME, "from_socket": SOCKET, "to_node": NODE_NAME, "to_socket": SOCKET}
    edits = {"oneOf": [
        _op("add", {"node": NODE_NAME, "type": TYPE, "settings": settings, "inputs": sockets, "image": nullable(IMAGE_ID),
                    "location": nullable(LOCATION), "parent": nullable(NODE_NAME)}, ["node", "type"]),
        _op("remove", {"node": NODE_NAME, "reconnect": boolean(False)}, ["node"]),
        _op("link", ends, list(ends)),
        _op("unlink", {"to_node": NODE_NAME, "to_socket": SOCKET, "from_node": nullable(NODE_NAME),
                       "from_socket": nullable(SOCKET)}, ["to_node", "to_socket"]),
        _op("detach", {"node": NODE_NAME, "reconnect": boolean(False)}, ["node"]),
        _op("set", {"node": NODE_NAME, "settings": settings, "inputs": sockets, "outputs": sockets,
                    "image": nullable(IMAGE_ID)}, ["node"]),
        _op("parent", {"nodes": array(NODE_NAME, 1, 32), "frame": nullable(NODE_NAME)}, ["nodes"]),
        _op("duplicate", {"node": NODE_NAME, "as": NODE_NAME, "keep_inputs": boolean(False),
                          "offset": array(number(-100000, 100000), 2, 2, [40, -40])}, ["node", "as"]),
        _op("swap", {"node": NODE_NAME, "type": TYPE}, ["node", "type"]),
        _op("insert", dict(ends, node=NODE_NAME, input=nullable(SOCKET), output=nullable(SOCKET)), ["node", *ends]),
        _op("group", {"nodes": array(NODE_NAME, 1, 32), "node": NODE_NAME, "name": text(63)}, ["nodes", "node", "name"]),
        _op("ungroup", {"node": NODE_NAME}, ["node"]),
        _op("interface", {"action": enum(["ADD", "REMOVE", "SET"]), "name": text(63), "in_out": enum(["INPUT", "OUTPUT"]),
                          "socket_type": nullable(enum(INTERFACE_TYPES)), "new_name": nullable(text(63)),
                          "min": nullable(number(-1e30, 1e30)), "max": nullable(number(-1e30, 1e30))},
            ["action", "name", "in_out"])]}
    # "default" (an interface input's value) is added to the interface step below: it is any JSON value.
    edits["oneOf"][-1]["properties"]["default"] = _open_map(SOCKET_PATTERN)["additionalProperties"]
    return {variant["properties"]["op"]["const"]: variant for variant in edits["oneOf"]}


def _wider(known, spec):
    """One field's schema across steps: the same, or one step's nullable form of another's."""
    if known is None or known == spec:
        return spec
    for plain, loose in ((known, spec), (spec, known)):
        if loose.get("oneOf") == [plain, {"type": "null"}]:
            return loose
    raise AssertionError("node_edit steps disagree on a field's schema")


def _step_schema(variants):
    """The advertised step: one closed object with each field once and an op enum.

    A oneOf of the variants repeated the open value maps in every step that
    takes them and took the complete internal catalog past its 150 KB bound
    once the phase-5 animation kinds were merged; canonical() narrows each step
    to its op's variant, so the accepted steps and their canonical form are
    unchanged and a field another op owns is refused by name.
    """
    fields = {}
    for variant in variants.values():
        for key, spec in variant["properties"].items():
            if key != "op":
                fields[key] = _wider(fields.get(key), spec)
    ops = ["%s %s" % (op, " ".join(key for key in variant["required"] if key != "op"))
           + (" [%s]" % " ".join(key for key in variant["properties"] if key not in variant["required"])
              if set(variant["properties"]) - set(variant["required"]) else "")
           for op, variant in variants.items()]
    step = obj(dict({"op": enum(list(variants))}, **fields), ["op"])
    step["description"] = "Fields by op, required then [optional]; other fields are refused: " + "; ".join(ops)
    return step


_VARIANTS = _variants()


def _schema():
    # material: a name as every other material field takes it (material_assign, the material checks).
    return obj({"material": nullable(text(64)), "modifier": nullable(text(63)), "world": boolean(False),
                "group": nullable(NODE_NAME), "shared": enum(["REFUSE", "ISOLATE", "ALL_USERS"], "REFUSE"),
                "edits": array(_step_schema(_VARIANTS), 1, MAX_EDITS)}, ["edits"])


NODE_EDIT_SCHEMA = _schema()


# REFUSED, BUDGET, GN_CONTRACT and NODE_TYPE_UNAVAILABLE also wrap arbitrary
# exceptions. Their call sites must carry the original policy explicitly.
_REFUSAL_CODES = {
    **dict.fromkeys((
        "CYCLE", "DUPLICATE_NAMES", "EMPTY", "FIELDS", "GROUP_NOT_FOUND", "INCOMPATIBLE_SOCKETS", "INTERFACE",
        "INTERFACE_NEEDS_GROUP", "INVALID_LINK", "LINK_NOT_FOUND", "MATERIAL_NOT_FOUND", "MODIFIER_NOT_FOUND",
        "NAME_TAKEN", "NODE_NOT_FOUND", "NODE_TREE_SHARED", "NODE_TYPE", "NOT_AN_IMAGE_NODE", "NOT_A_FRAME",
        "NOT_A_GROUP", "NO_NODE_TREE", "NO_WORLD", "OUTPUT_DISCONNECTED", "OUTPUT_NODES", "OWNER_TYPE_MISMATCH",
        "PARENT", "SOCKET_NAME", "SOCKET_NOT_FOUND", "SOCKET_TYPE", "TREE", "ALL_USERS_MATERIAL_ONLY",
        "SHARED_CONFLICT", "ARRIVED_IN_PROGRAM",
    ), "VALIDATION"),
    **dict.fromkeys((
        "ANIMATED_TREE", "DATA_SHARED", "GROUP_SOCKET_TYPE", "NEW_OBJECT", "NODE_TYPE_DENIED", "NOT_COPYABLE",
        "NOT_GROUPABLE", "NOT_INSERTABLE", "NOT_SUPPORTED", "NOT_SWAPPABLE", "NO_POINTERS", "SETTING_DENIED",
        "UNGROUP_CONVERSION", "WORLD_NESTED_GROUP", "SHARED_OUTSIDE_SCENE",
    ), "UNSUPPORTED"),
    "TREE_SIZE": "LIMIT_EXCEEDED",
    **dict.fromkeys(("NODE_COPY_INEXACT", "NOT_STAGED", "SHARING_UNKNOWN", "VALUE_NOT_RETAINED"), "INTERNAL_ERROR"),
}


class NodeEditError(RefusalError):
    """A node_edit refusal; domain ``code`` stays stable for callers and tests."""

    def __init__(self, code, detail, *, refusal_code=None, details=None):
        policy = _REFUSAL_CODES.get(code, "UNKNOWN") if refusal_code is None else refusal_code
        super().__init__("node_edit %s: %s" % (code, detail), code=policy, details=details)
        self.refusal_code = policy
        self.code = code


def _screen_values(values, sockets):
    from .rna_rules import json_value, object_reference, screen_path
    for key, value in values.items():
        if not sockets:
            screen_path(key, struct=True)
            if key in DENIED_SETTINGS:
                raise NodeEditError("SETTING_DENIED", "%s is editor state; nodes keep their selection and place" % key)
        json_value(value, key)
        if object_reference(value) is not None:
            raise NodeEditError("NO_POINTERS", "node settings and sockets take no object or datablock pointers: " + key)


def _step(given, index):
    """One step exactly as its op's closed variant validates it (fields, required fields and defaults)."""
    from .action_schema_types import _validate
    variant = _VARIANTS[given["op"]]
    foreign = sorted(set(given) - set(variant["properties"]))
    if foreign:
        raise NodeEditError("FIELDS", "%s takes no %s" % (given["op"], ", ".join(foreign)))
    missing = [key for key in variant["required"] if key not in given]
    if missing:
        raise NodeEditError("FIELDS", "%s needs %s" % (given["op"], ", ".join(missing)))
    return _validate(given, variant, "operation.parameters.edits[%d]" % index)


def canonical(result, raw):
    """Cross-field rules of a schema-validated node_edit; generic values keep their JSON types."""
    params = result["parameters"]
    params["edits"] = [_step(given, index) for index, given in enumerate(raw["parameters"]["edits"])]
    if sum((params["material"] is not None, params["modifier"] is not None, params["world"])) != 1:
        raise NodeEditError("TREE", "name exactly one tree: material (a name on the target's slots), modifier "
                                    "(a Geometry Nodes modifier) or world true (scene handle target)")
    if params["shared"] == "ALL_USERS" and (params["material"] is None or params["group"] is not None):
        # A shared group or World changes geometry or scenes this program neither stages nor bounds.
        raise NodeEditError("ALL_USERS_MATERIAL_ONLY", "shared ALL_USERS edits a material's own tree for every "
                                                       "object that uses it; a modifier's group, the World or a "
                                                       "nested group takes REFUSE or ISOLATE")
    created = []
    for edit, given in zip(params["edits"], raw["parameters"]["edits"]):
        if "type" in edit and not re.fullmatch(TYPE_PATTERN, edit["type"]):
            raise NodeEditError("NODE_TYPE", "name a node type by its bl_idname (ShaderNode..., GeometryNode..., "
                                             "FunctionNode..., NodeReroute, NodeFrame)")
        for key in ("from_socket", "to_socket", "input", "output"):
            if edit.get(key) is not None and not re.fullmatch(SOCKET_PATTERN, edit[key]):
                raise NodeEditError("SOCKET_NAME", "name a socket by its name or identifier: " + repr(edit[key][:64]))
        for key in ("settings", "inputs", "outputs"):
            if key in edit:
                edit[key] = copy.deepcopy(given.get(key, {}))
                _screen_values(edit[key], key != "settings")
        op = edit["op"]
        if op == "interface":
            if "default" in given:
                edit["default"] = copy.deepcopy(given["default"])
                _screen_values({"default": edit["default"]}, True)
            if params["group"] is None:
                raise NodeEditError("INTERFACE_NEEDS_GROUP", "interface sockets belong to a node group: name its group "
                                                             "node in group")
            if edit["action"] == "ADD" and edit["socket_type"] is None:
                raise NodeEditError("INTERFACE", "ADD needs a socket_type")
            if edit["action"] == "SET" and edit["new_name"] is None and edit["min"] is None and edit["max"] is None \
                    and "default" not in edit:
                raise NodeEditError("INTERFACE", "SET needs new_name, default, min or max")
            if edit["action"] == "REMOVE" and (edit["socket_type"] is not None or "default" in edit):
                raise NodeEditError("INTERFACE", "REMOVE takes only name and in_out")
        if op == "set" and not (edit["settings"] or edit["inputs"] or edit["outputs"] or edit["image"]):
            raise NodeEditError("EMPTY", "set needs settings, inputs, outputs or an image")
        if op == "parent" and edit["frame"] in edit["nodes"]:
            raise NodeEditError("PARENT", "a frame cannot contain itself")
        if op in {"parent", "group"} and len(set(edit["nodes"])) != len(edit["nodes"]):
            raise NodeEditError("DUPLICATE_NAMES", "list each node once")
        if op == "duplicate" and edit["as"] == edit["node"]:
            raise NodeEditError("NAME_TAKEN", "a duplicate needs a new name")
        created.extend({"add": [edit.get("node")], "duplicate": [edit.get("as")], "group": [edit.get("node")]}.get(op, []))
    if len(set(created)) != len(created):
        raise NodeEditError("NAME_TAKEN", "each new node needs its own name")
    return params


def families(params):
    """The families a canonical node_edit writes: the World of the bound scene, or an object's trees."""
    return ["world_nodes"] if params["world"] else ["object", "node_trees"]


def tree_key(params):
    """Which tree (and nested group) a node_edit or node_graph check addresses."""
    return (params.get("material"), params.get("modifier"), bool(params.get("world")), params.get("group"))


def keeps(check, operation):
    """Whether a later node_edit leaves a node_graph check true: it edits another root tree."""
    return tree_key(check)[:3] != tree_key(operation["parameters"])[:3]


def derived_check(target, params, *, input_roles=None, strict_roles=False, unknowns=None,
                  input_identifiers=None, output_identifiers=None, output_names=None):
    """The node_graph check a canonical node_edit states exactly, or None.

    Facts are kept only while no later step of the same operation may change
    them: removing, swapping, grouping or ungrouping a node drops what was
    stated about it and its links; a node whose type is stated keeps it.
    """
    from .node_link_obligations import LinkFacts, LINK_GUARD
    nodes, absent = {}, {}

    def unknown(edit, reason, affected):
        if unknowns is not None and affected:
            root = {"target": edit.get("_target", target), **{key: params[key] for key in ("material", "modifier", "group")
                                                            if params[key] is not None}}
            if params["world"]:
                root["world"] = True
            unknowns.append({"operation": edit.get("_operation", 0), "root": root, "reason": reason,
                             "affected": copy.deepcopy(affected)})

    link_facts = LinkFacts(input_roles, unknown, strict=strict_roles, private=unknowns is not None,
                           input_ids=input_identifiers, output_ids=output_identifiers, output_names=output_names)
    links, unlinked = link_facts.links, link_facts.unlinked

    def previous_facts(name):
        facts = {"nodes": [nodes[name]] if name in nodes else [], **link_facts.related(name)}
        return [facts] if facts["nodes"] or facts["links"] or facts.get("unlinked") or LINK_GUARD in facts else []

    def transition_facts(name):
        facts = copy.deepcopy(previous_facts(name))
        for entry in facts:
            entry.pop(LINK_GUARD, None)  # Absence on a retired endpoint is vacuous.
            entry.pop("unlinked", None)
            for node in entry["nodes"]:
                node.pop("type", None)  # Replacement explicitly states its new type.
            entry["nodes"] = [node for node in entry["nodes"] if set(node) != {"name"}]
        return [entry for entry in facts if entry["nodes"] or entry["links"]]

    def forget(name):
        nodes.pop(name, None)
        link_facts.forget(name)

    for edit in params["edits"]:
        op, name = edit["op"], edit.get("node")
        if op == "add":
            if name in nodes:
                link_facts.roles.pop(name, None)
            absent.pop(name, None)
            entry = {"name": name, "type": edit["type"]}
            entry.update({key: copy.deepcopy(edit[key]) for key in ("settings", "inputs") if edit[key]})
            if edit["parent"] is not None:
                entry["parent"] = edit["parent"]
            nodes[name] = entry
        elif op in {"remove", "ungroup"}:
            if op == "ungroup":
                unknown(edit, "node_generation_transition", transition_facts(name))
            forget(name)
            absent[name] = True
        elif op == "set":
            # Generic settings can rebuild this node's sockets or coerce other
            # settings. Preserve the earlier facts explicitly as unproved;
            # the new literal writes and every other node stay mandatory.
            safe = {"label", "mute", "hide", "width", "height", "show_options", "show_preview",
                    "show_texture", "color", "use_custom_color"}
            if unknowns is not None and set(edit["settings"]) - safe:
                affected = copy.deepcopy(previous_facts(name))
                for facts in affected:
                    retained = []
                    for entry in facts["nodes"]:
                        for field in ("settings", "inputs", "outputs"):
                            for written in edit[field]:
                                entry.get(field, {}).pop(written, None)
                            if not entry.get(field):
                                entry.pop(field, None)
                        entry.pop("type", None)  # SET does not replace the node's type.
                        if set(entry) != {"name"}:
                            retained.append(entry)
                    facts["nodes"] = retained
                affected = [facts for facts in affected
                            if facts["nodes"] or facts["links"] or facts.get("unlinked") or LINK_GUARD in facts]
                link_facts.invalidate_bindings(name)
                if affected:
                    unknown(edit, "opaque_node_settings", affected)
                    kind = nodes.get(name, {}).get("type")
                    invalidated = name in link_facts.invalidated
                    forget(name)
                    if invalidated:
                        link_facts.invalidated.add(name)
                    if kind is not None:
                        nodes[name] = {"name": name, "type": kind}
            entry = nodes.setdefault(name, {"name": name})
            for key in ("settings", "inputs", "outputs"):
                if edit[key]:
                    entry[key] = dict(entry.get(key, {}), **copy.deepcopy(edit[key]))
        elif op in {"link", "unlink", "insert", "detach"}:
            link_facts.apply(edit)
        elif op == "parent":
            for member in edit["nodes"]:
                nodes.setdefault(member, {"name": member})["parent"] = edit["frame"]
        elif op == "duplicate":
            absent.pop(edit["as"], None)
            link_facts.forget(edit["as"])
            nodes[edit["as"]] = {"name": edit["as"], **({"type": nodes[name]["type"]} if "type" in nodes.get(name, {}) else {})}
        elif op == "swap":
            unknown(edit, "node_generation_transition", transition_facts(name))
            forget(name)
            nodes[name] = {"name": name, "type": edit["type"]}
        elif op == "group":
            for member in edit["nodes"]:
                unknown(edit, "node_generation_transition", transition_facts(member))
                forget(member)
                absent[member] = True
            absent.pop(name, None)
            nodes[name] = {"name": name, "type": "ShaderNodeGroup"}
    check = {"kind": "node_graph", "target": target, **{key: params[key] for key in ("material", "modifier", "group")
                                                        if params[key] is not None}}
    if params["world"]:
        check["world"] = True
    # The host packs these complete conjunctions into bounded check rows only
    # after derivation. Truncating here would permanently lose obligations.
    if link_facts.absent:
        for entry in link_facts.absent:
            nodes.setdefault(entry["to_node"], {"name": entry["to_node"]})
        check[LINK_GUARD] = {"absent": link_facts.absent}
    check["nodes"] = list(nodes.values())
    check["absent"] = [{"name": name} for name in absent]
    check["links"] = links
    check["unlinked"] = list(unlinked.values())
    if not (check["nodes"] or check["absent"] or check["links"] or check["unlinked"]):
        return None
    return check  # No tolerance: each value compares at its own float32 precision (rna_rules.same).


_LABEL = {"type": "string", "minLength": 1, "maxLength": 64}
_TYPE = {"type": "string", "minLength": 1, "maxLength": 64, "pattern": TYPE_PATTERN}
_OPEN = {"type": "object", "properties": {}, "additionalProperties": True, "maxProperties": 32}


def _items(item):
    return {"type": "array", "items": item, "maxItems": MAX_CHECK_ITEMS}


# Nodes by exact name and/or bl_idname: each expected entry must match a node, no absent one may.
NODE_GRAPH_CHECK = {"type": "object", "additionalProperties": False, "required": ["kind", "target"], "properties": {
    "kind": {"const": "node_graph"}, "target": {"type": "string", "minLength": 1, "maxLength": 128},
    "material": _LABEL, "modifier": _LABEL, "world": {"type": "boolean"}, "group": _LABEL,
    "nodes": _items({"type": "object", "minProperties": 1, "additionalProperties": False, "properties": {
        "name": _LABEL, "type": _TYPE, "parent": {"anyOf": [_LABEL, {"type": "null"}]}, "inputs": _OPEN,
        "outputs": _OPEN, "settings": _OPEN}}),
    "absent": _items({"type": "object", "minProperties": 1, "additionalProperties": False,
                      "properties": {"name": _LABEL, "type": _TYPE}}),
    "links": _items({"type": "object", "required": ["from_socket", "to_socket"], "additionalProperties": False,
                     "properties": {"from_node": _LABEL, "from_type": _TYPE, "from_socket": _LABEL, "to_node": _LABEL,
                                    "to_type": _TYPE, "to_socket": _LABEL}}),
    "unlinked": _items({"type": "object", "required": ["socket"], "additionalProperties": False,
                        "properties": {"node": _LABEL, "type": _TYPE, "socket": _LABEL}}),
    "tolerance": {"type": "number", "minimum": 0, "maximum": 100}}}


def screen_check(check):
    """Cross-field rules of a canonical node_graph check (pure)."""
    from .rna_rules import json_value, screen_path
    if sum(("material" in check, "modifier" in check, bool(check.get("world")))) != 1 or check.get("world") is False:
        raise RefusalError("A node_graph check names exactly one tree: material, modifier or world true", code="VALIDATION")
    for key in ("nodes", "absent", "links", "unlinked"):
        check.setdefault(key, [])
    if not (check["nodes"] or check["absent"] or check["links"] or check["unlinked"]):
        raise RefusalError("A node_graph check needs nodes, absent nodes, links or unlinked sockets", code="VALIDATION")
    for entry in check["nodes"]:
        if "name" not in entry and "type" not in entry:
            raise RefusalError("A node_graph node entry needs a name or a type", code="VALIDATION")
        for path, value in entry.get("settings", {}).items():
            screen_path(path, struct="NODE")
            json_value(value, path)
        for name, value in list(entry.get("inputs", {}).items()) + list(entry.get("outputs", {}).items()):
            if not re.fullmatch(SOCKET_PATTERN, name):
                raise RefusalError("Name a socket by its name or identifier", code="VALIDATION")
            json_value(value, name)
    for link in check["links"]:
        if not ({"from_node", "from_type"} & set(link) and {"to_node", "to_type"} & set(link)):
            raise RefusalError("A node_graph link names each end node by name or type", code="VALIDATION")
    for socket in check["unlinked"]:
        if not {"node", "type"} & set(socket):
            raise RefusalError("A node_graph unlinked socket names its node by name or type", code="VALIDATION")
    return check
