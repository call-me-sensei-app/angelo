# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One material on several objects, stated once (pure Python, no bpy).

Two ways a program gives objects a material it already stated, besides
``material_assign``:

* ``also``: on ``material_principled``, ``shader_nodes`` and
  ``material_assign``, the objects that get the operation's material as well
  as the target. Slot and face fields apply to the target only; each ``also``
  object keeps the slot that already has the material, else gets a new last
  slot. A material kind that edits a material its target already has takes no
  ``also`` (``material_assign`` gives an existing material to more objects).
* a ``material_principled`` that states only a name (no input in the call;
  canonical programs keep its ``inputs`` empty, so stating a default value is
  stating it) and names a material an earlier
  operation of the program stated,
  or one its target holds, assigns that material instead of redefining it: a
  held one is kept as it is (``action_nodes._keep``, and the bound material
  obligations agree; it may take ``also``), never rebuilt at default values
  (otherwise a held red material, live ones too,
  committed grey). A name alone that makes a new material makes Blender's
  default one.

Both are plain assignments of one staged datablock: the objects share it after
commit, each is bound and verified like a ``material_assign`` target, and one
derived ``material_assignment`` check per object states the result
(``derived_everyday``).

A ``material_principled`` that states inputs writes those and no other
(``action_schemas.SETTINGS_NOTE``): on a material its target holds, in place on
its one Principled BSDF, every other input, node and link kept (refused when it
has none or several, or a link drives a stated input: ``in_place_refusal``); a
new material keeps Blender's defaults for the rest. Rebuilding the graph at
default values instead committed a red metal that set only Roughness grey.
"""

import copy
import json

from .action_schema_types import POINTER, array
from .refusal_codes import RefusalError

MAX_ALSO = 31  # With the target: 32 objects, a program's operation count.
ALSO = dict(array(POINTER, 0, MAX_ALSO), uniqueItems=True, default=[])
KINDS = frozenset({"material_principled", "shader_nodes", "material_assign"})
STATING_KINDS = frozenset({"material_principled", "shader_nodes"})


def not_in_program(name):
    """``material_assign`` without ``source`` named a material no object of the program holds.

    The assignment never looks a material up in the file, so one a script makes
    is not found either, even in an earlier step of the same plan: the host
    validates every step before the first runs. Recorded arched-footbridge
    one-shots lost a round to that plan shape (a script made the material, a
    program assigned it by name); the text names the plan that works.

    Deliberate (agent_execution.rst, "Referring to objects, data and values"): a
    program's inputs are bound to declared objects' revisions, which include
    their materials, and a name never stands in for that evidence; a material no
    declared object holds has no revision a commit could recheck, and staging
    has no private copy of it to swap back. So a file material no object uses is
    assigned with a reviewed script, which the text also says.
    """
    return RefusalError("material_assign MATERIAL_NOT_FOUND: no object of this program has a material named %r, and "
                        "material_assign never looks one up in the file, so one a script makes is not found: state it "
                        "in this program (material_principled; a script after this program can reuse it by name) or "
                        "name an object that has it in source. A file material no object uses is assigned with a "
                        "reviewed script" % name[:64], code="VALIDATION")


def also(operation):
    """The ``also`` references of a canonical operation (none for other kinds)."""
    return list(operation["parameters"]["also"]) if operation["kind"] in KINDS else []


def canonical(result, raw):
    """Cross-field rules: ``also`` never repeats the target; a ``material_principled`` that states no input keeps
    ``inputs`` empty, so canonical programs say which statements gave values (``names_only``)."""
    if result["target"] in result["parameters"]["also"]:
        raise ValueError("%s also lists objects other than its target" % result["kind"])
    if result["kind"] == "material_principled" and not raw["parameters"].get("inputs"):
        result["parameters"]["inputs"] = {}
    return result


# A new Principled material's values for the inputs material_principled types: Blender 5.2's own defaults, which a
# new material keeps for every input its statement leaves out (SETTINGS_NOTE; test_blender_kept_settings measures
# them). Only definition() reads them, to compare two statements of one material.
NEW_INPUTS = {"Base Color": [0.8, 0.8, 0.8, 1.0], "Metallic": 0.0, "Roughness": 0.5, "IOR": 1.5, "Alpha": 1.0,
              "Emission Color": [1.0, 1.0, 1.0, 1.0], "Emission Strength": 0.0}


def principled_inputs(params):
    """The inputs a canonical ``material_principled`` writes: those it states, and no other (SETTINGS_NOTE).

    A new material keeps Blender's defaults for the rest; a material its target holds keeps its own values,
    since the kernel sets these on its one Principled BSDF in place (``action_nodes``)."""
    return copy.deepcopy(params["inputs"])


def reaches_surface(start, links, passes, outputs):
    """Whether node ``start`` feeds the Surface of one of ``outputs`` (the Material Outputs an engine renders).

    ``links``: ``(from node, from socket, to node, to socket)`` of the enabled links, sockets by identifier;
    ``passes``: of each muted node, the ``(input, output)`` sockets it passes through (its internal links; a muted
    node passes nothing else). Connectivity, not what a mix or a group makes of it."""
    frontier, seen = [(start, None)], set()
    while frontier:
        node, socket = frontier.pop()
        leaving = {output for source, output in passes[node] if source == socket} if node in passes else None
        for source, out, target, into in links:
            if source != node or leaving is not None and out not in leaving:
                continue
            if target in outputs:
                if into == "Surface":
                    return True
                continue
            if (target, into) not in seen:
                seen.add((target, into))
                frontier.append((target, into))
    return False


def in_place_refusal(operation, count, linked, shown=True):
    """None, or why a ``material_principled`` cannot set its inputs on the material it names in place.

    ``count``: the material's Principled BSDF nodes; of the one there is, ``linked``: the stated inputs a link
    drives, and ``shown``: whether it reaches the Surface of an output an engine renders (``tree_reaches``; None:
    unknown). Shown in one engine is shown: refused only where no engine would show it.
    A value set where it would not show is refused (otherwise one set on a BSDF a muted node or an inactive output
    kept from the surface reported success). The kernel and the bound obligations share
    this text."""
    name = operation["parameters"]["name"][:64]
    if count != 1:
        return RefusalError("material_principled PRINCIPLED_NOT_SINGLE: %r has %s Principled BSDF nodes, and "
                            "material_principled sets inputs of exactly one, keeping the rest of the graph; edit its "
                            "nodes with node_edit or a reviewed script, state a whole new graph with shader_nodes, or "
                            "choose a new name" % (name, count or "no"), code="VALIDATION")
    if shown is False:
        return RefusalError("material_principled PRINCIPLED_NOT_SHOWN: %r's Principled BSDF does not reach the Surface of "
                            "the Material Output any render engine uses, so values set on it would not show; "
                            "connect it with node_edit or a reviewed script, state a whole new graph with shader_nodes, "
                            "or choose a new name" % name, code="VALIDATION")
    if linked:
        return RefusalError("material_principled INPUT_LINKED: %s of %r's Principled BSDF %s driven by a link, so a "
                            "value set there would not show; leave %s out, or unlink it first with node_edit (or a "
                            "reviewed script)"
                            % (", ".join(linked), name, "is" if len(linked) == 1 else "are",
                               "it" if len(linked) == 1 else "them"), code="VALIDATION")
    return None


def shared_in_program(name, arrived=False, created=False, assigned=False, edited=False, isolated=False):
    """``(code, detail)`` of the kernel's refusal of an in-place edit of a material shared within the program
    (node_edit._material and _facts), which the bound obligations raise before approval; None when none applies.
    Without the obligations, these would be refused only after approval."""
    if arrived:
        return "ARRIVED_IN_PROGRAM", ("%s came to this object in this program (assigned, copied or joined), so its "
                                      "users were not bound for it; edit it on the object it came from, which had it "
                                      "before the program, or in a later program" % name)
    if created:
        return "NODE_TREE_SHARED", ("%s was created and shared earlier in this program; edit it with shared "
                                    "ALL_USERS, or use material_assign with copy_as and a new name before editing "
                                    "one object's copy" % name)
    if assigned:
        return "NODE_TREE_SHARED", ("%s was assigned to another object earlier in this program; edit it with shared "
                                    "ALL_USERS, or before assigning it" % name)
    if edited:  # Its edited copy is another object's too now.
        return "NODE_TREE_SHARED", ("%s was edited earlier in this program and another object holds that edited "
                                    "material now (assigned, copied or edited for all users), so an edit in place "
                                    "would change it too; edit it with shared ALL_USERS, or make every edit of it "
                                    "before assigning or copying it" % name)
    if isolated:
        return "NODE_TREE_SHARED", ("%s is held by another object since an earlier step of this program (assigned, "
                                    "copied or edited for all users), so it cannot be isolated for this one; edit it "
                                    "with shared ALL_USERS, or isolate it before assigning or copying it" % name)
    return None


def animated(*blocks):
    """Whether an Action, driver or NLA animates any of ``blocks``: node_edit refuses editing such a tree in place
    (ANIMATED_TREE), and the bound obligations refuse it before approval from this fact."""
    for block in blocks:
        animation = getattr(block, "animation_data", None)
        if animation is not None and (animation.action is not None or len(animation.drivers) or len(animation.nla_tracks)):
            return True
    return False


def driven(socket):
    """Whether a link drives ``socket`` in Blender's evaluation: an enabled (unmuted), valid one. A muted or invalid
    link leaves the input's own value in use, so it is no reason to refuse a value set there."""
    return any(not link.is_muted and link.is_valid for link in socket.links)


def tree_reaches(tree, node):
    """Whether a Blender material tree shows ``node`` in some engine: ``reaches_surface`` of the output Blender
    renders for EEVEE or for Cycles (``get_output_node``: an engine's own target before the active ALL one), through
    muted nodes' internal links (the kernel's and the binding's reading)."""
    links = [(link.from_node.name, link.from_socket.identifier, link.to_node.name, link.to_socket.identifier)
             for link in tree.links if not link.is_muted and link.is_valid]
    passes = {item.name: {(link.from_socket.identifier, link.to_socket.identifier) for link in item.internal_links}
              for item in tree.nodes if item.mute}
    rendered = set()
    for engine in ("EEVEE", "CYCLES"):
        try:
            output = tree.get_output_node(engine)
        except (TypeError, ValueError):
            output = tree.get_output_node("ALL")
        if output is not None:
            rendered.add(output.name)
    return any(reaches_surface(node.name, links, passes, {output}) for output in rendered)


# The bound obligations' model of a Principled material graph (program_material_obligations).
_PRINCIPLED = "ShaderNodeBsdfPrincipled"


def principled_history(params):
    """A new material's graph (action_nodes._new_principled) with the inputs it states; Blender's defaults elsewhere."""
    return [
        {"op": "add", "node": "Principled BSDF", "type": _PRINCIPLED,
         "settings": {}, "inputs": principled_inputs(params), "parent": None},
        {"op": "add", "node": "Material Output", "type": "ShaderNodeOutputMaterial",
         "settings": {}, "inputs": {}, "parent": None},
        {"op": "link", "from_node": "Principled BSDF", "from_socket": "BSDF",
         "to_node": "Material Output", "to_socket": "Surface"},
    ]


def graph_bsdf(params):
    """A ``shader_nodes`` graph's Principled BSDF facts (``program_material_facts._bsdf``; several outputs: unknown)."""
    nodes = [node["id"] for node in params["nodes"] if node["type"] in ("BSDF_PRINCIPLED", _PRINCIPLED)]
    if len(nodes) != 1:
        return {"count": len(nodes)}
    from .action_node_types import _principled_inputs
    linked = sorted({link["to_socket"] for link in params["links"] if link["to_node"] == nodes[0]} & set(_principled_inputs))
    outputs = [node for node in params["nodes"] if node["type"] in ("OUTPUT_MATERIAL", "ShaderNodeOutputMaterial")]
    links = [(link["from_node"], link["from_socket"], link["to_node"], link["to_socket"]) for link in params["links"]]
    plain = len(outputs) == 1 and (outputs[0].get("settings") or {}).get("target", "ALL") == "ALL" and not any(
        (node.get("settings") or {}).get("mute") for node in params["nodes"])  # Else the kernel reads Blender's choice.
    shown = reaches_surface(nodes[0], links, {}, {outputs[0]["id"]}) if plain else None
    return {"count": 1, "name": nodes[0], "linked": linked, "shown": shown}


def keeps_bsdf(edit, name):
    """Whether a node_edit step keeps the bound BSDF the only Principled BSDF, named ``name`` (conservative)."""
    op = edit["op"]
    if op in {"group", "ungroup", "interface"}:
        return False  # Names or nested graphs change.
    another = edit.get("type") == _PRINCIPLED and op in {"add", "swap"}  # A second Principled BSDF.
    return not another and not (op in {"remove", "duplicate", "swap"} and edit["node"] == name)


def keeps_wiring(edit):
    """Whether a node_edit step leaves the links, mutes and active output as bound (values only)."""
    return edit["op"] in {"add", "parent", "duplicate"} or edit["op"] == "set" and not edit["settings"]


# A material kind's fields that place its material rather than define it (action_materials.defined compares the rest).
PLACEMENT = ("slot", "face_indices", "face_selector", "index_scope", "topology_step", "shared", "also")


def definition(operation, earlier=None, held=False):
    """What a stating kind says its material is: kind and every value but placement and name, as canonical JSON.

    A ``material_principled``'s inputs are what the material has after it: those it states over the inputs
    already known, from ``earlier`` (the definition this program gave the material), else none for a material its
    target ``held`` before the program, else a new material's (Blender's defaults). A statement without inputs
    says the default material, as one stating every default does."""
    values = {key: value for key, value in operation["parameters"].items() if key not in PLACEMENT + ("name",)}
    if operation["kind"] == "material_principled":
        known = _inputs(earlier) if earlier is not None else {} if held else copy.deepcopy(NEW_INPUTS)
        values["inputs"] = dict(known, **principled_inputs(operation["parameters"]))
    return json.dumps([operation["kind"], values], sort_keys=True, separators=(",", ":"))


def _inputs(earlier):
    kind, values = json.loads(earlier)
    return values["inputs"] if kind == "material_principled" else {}


def agrees(earlier, operation):
    """Whether a statement of a material its target does not hold says what ``earlier`` defined, so the target is
    given that material: a ``material_principled`` states only inputs it has, at their values (one it leaves out
    stays as it is); ``shader_nodes`` the same graph. The kernel, program_rules and the bound obligations share it:
    a comparator of its own would refuse restatements the others accept."""
    if earlier == definition(operation) or names_only(operation):  # A name alone: that material, whatever it is.
        return True
    kind, values = json.loads(earlier)
    if operation["kind"] != "material_principled" or kind != "material_principled":
        return False
    return all(name in values["inputs"] and values["inputs"][name] == value
               for name, value in principled_inputs(operation["parameters"]).items())


def redefined(operation):
    """The MATERIAL_REDEFINED refusal of a stating kind that names an earlier statement with other values."""
    return RefusalError("%s MATERIAL_REDEFINED: %r was stated earlier in this program with other values; state a "
                        "material once and reuse it with material_assign, or choose another name"
                        % (operation["kind"], operation["parameters"]["name"][:64]), code="VALIDATION")


def names_only(operation):
    """Whether a canonical ``material_principled`` states a name and no input (never a comparison of values: one
    stating a default value states it)."""
    return operation["kind"] == "material_principled" and not operation["parameters"]["inputs"]


def checks(operations, index, derived, keeps):
    """The derived checks of ``operations[index]`` with the material rules applied (derived_everyday).

    A name-only ``material_principled`` that follows a statement of its
    material loses its ``material_values`` check (the values are the first
    statement's), and every ``also`` object gets a ``material_assignment`` check.
    """
    operation = operations[index]
    if operation["kind"] not in KINDS:
        return derived
    params = operation["parameters"]
    name = params.get("copy_as") or params.get("material") or params.get("name")
    if names_only(operation) and any(earlier["kind"] in STATING_KINDS and earlier["parameters"]["name"] == name
                                     for earlier in operations[:index]):
        derived = [(check, kept) for check, kept in derived if check["kind"] != "material_values"]
    if any(check["kind"] == "material_assignment" for check, _ in derived):
        derived = derived + [({"kind": "material_assignment", "target": reference, "material": name, "all_faces": False},
                              keeps) for reference in params["also"]]
    return derived
