# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Generation Flow definitions (``angelo.flow/1``) and tool schemas. No Blender dependency.

A flow is a small directed graph of media steps. Source nodes supply images or
text; generator nodes call one Media Studio model; Place in Scene marks a 3D
result for import. The Blender node tree (``flow_nodes``) is the saved source
of truth and serializes to this format; the agent tools and the Node Editor
both edit flows through it, and every definition is checked here before use.

Node and input names are contracts: saved .blend files and agent plans use them.
"""

import hashlib
import json
import re
import uuid

from .studio_spec import OPTIONS, _validate

FORMAT = "angelo.flow/1"
MAX_NODES = 64
MAX_LINKS = 128
MAX_VARIANTS = 4
MAX_TAKE = 999  # Redo: a fresh take of an unchanged request is a new key; 0 (the first) is never written.
MAX_TEXT = 4000
MAX_PROMPT = 16000
REVISION_LENGTH = 12

IMAGE, TEXT, MODEL, PARTS, MODELS = "IMAGE", "TEXT", "MODEL", "PARTS", "MODELS"
SOURCE_TYPES = ("IMAGE", "SCENE_CAPTURE", "TEXT")
GENERATOR_TYPES = ("GENERATE_IMAGE", "GENERATE_3D")
# Character parts: a local Split Sheet step, a per-part 3D step (expanded into one Generate 3D step per
# part once the sheet is split, see flow_fanout) and Place Character (compose and place, undoable).
PART_TYPES = ("SPLIT_SHEET", "GENERATE_3D_EACH", "PLACE_CHARACTER")
# Every step that runs on the person's account, as the generator it runs as. What it needs (connection_needs) and
# whether its model fits (_check_model) are decided per generator: a step type missing here is drawn as a failure.
RUNS_AS = {"GENERATE_IMAGE": "GENERATE_IMAGE", "GENERATE_3D": "GENERATE_3D", "GENERATE_3D_EACH": "GENERATE_3D"}
# Each node type's drawn width (Node Editor units): its node is made this wide and templates lay steps out by it.
NODE_WIDTHS = {"IMAGE": 200, "SCENE_CAPTURE": 200, "TEXT": 200, "GENERATE_IMAGE": 260, "GENERATE_3D": 260,
               "PLACE_IN_SCENE": 200, "SUBFLOW": 240, "SPLIT_SHEET": 300, "GENERATE_3D_EACH": 260,
               "PLACE_CHARACTER": 220}
BASE_NODE_TYPES = SOURCE_TYPES + GENERATOR_TYPES + ("PLACE_IN_SCENE", "SUBFLOW")
NODE_TYPES = BASE_NODE_TYPES + PART_TYPES
SLOT_KINDS = ("BODY", "HAIR", "HEADWEAR", "INNER", "OUTER", "LOWER", "FOOTWEAR", "ACCESSORY")
MAX_SLOTS = 16

# What each node type emits, and the inputs it accepts: (socket kind, most links).
# A model can lower a reference limit (validated against the catalog at run time).
# A SUBFLOW node's inputs and output come from its recipe's interface instead.
OUTPUTS = {"IMAGE": IMAGE, "SCENE_CAPTURE": IMAGE, "TEXT": TEXT,
           "GENERATE_IMAGE": IMAGE, "GENERATE_3D": MODEL, "PLACE_IN_SCENE": None, "SUBFLOW": None,
           "SPLIT_SHEET": PARTS, "GENERATE_3D_EACH": MODELS, "PLACE_CHARACTER": None}
INPUTS = {
    "GENERATE_IMAGE": {"prompt": (TEXT, 8), "references": (IMAGE, 4)},
    "GENERATE_3D": {"prompt": (TEXT, 8), "reference": (IMAGE, 1)},
    "PLACE_IN_SCENE": {"model": (MODEL, 1)},
    "SPLIT_SHEET": {"sheet": (IMAGE, 1)},
    "GENERATE_3D_EACH": {"prompt": (TEXT, 8), "parts": (PARTS, 1)},
    "PLACE_CHARACTER": {"parts": (PARTS, 1), "models": (MODELS, 1)},
}
REFERENCE_INPUT = {"GENERATE_IMAGE": "references", "GENERATE_3D": "reference"}
FRAMINGS = ("CAMERA", "VIEWPORT")
SHADINGS = ("CLAY", "MATERIAL")
SUBJECTS = ("SCENE", "SELECTION")
RESOLUTIONS = (512, 768, 1024)
SCOPES = ("ALL", "NODE", "DOWNSTREAM", "UPSTREAM")

# Node names are Blender node names: any printable text without surrounding spaces.
_NAME = re.compile(r"[^\x00-\x1f\x7f\s](?:[^\x00-\x1f\x7f]{0,61}[^\x00-\x1f\x7f\s])?")
_FLOW_NAME = re.compile(r"[^\x00-\x1f\x7f]{1,63}")


class FlowError(ValueError):
    """An invalid flow or edit. Nothing was changed."""


def _name(value, what="node"):
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise FlowError(f"Use a {what} name of 1-63 printable characters without leading or trailing spaces")
    return value


def flow_name(value):
    if not isinstance(value, str) or not _FLOW_NAME.fullmatch(value) or not value.strip():
        raise FlowError("Use a flow name of 1-63 printable characters")
    return value


def _uuid(value, what):
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError):
        raise FlowError(f"Use the exact {what} UUID") from None


def _text(value, limit, what, required=False):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise FlowError(f"{what} must be text of at most {limit:,} characters")
    if required and not value.strip():
        raise FlowError(f"{what} can't be empty")
    return value


def _choice(value, choices, what):
    if value not in choices:
        raise FlowError(f"{what} must be one of: " + ", ".join(map(str, choices)))
    return value


def _source(value):
    if not isinstance(value, dict) or set(value) != {"kind", "id"}:
        raise FlowError("An Image node's source is {kind: ASSET, id: <library image UUID>} or {kind: URL, id: <public HTTPS URL>}")
    kind = _choice(value["kind"], ("ASSET", "URL"), "Image source kind")
    if kind == "ASSET":
        return {"kind": kind, "id": _uuid(value["id"], "library image")}
    url = _text(value["id"], 2048, "Image URL", required=True)
    if not url.startswith("https://"):
        raise FlowError("Image URLs must be public HTTPS addresses")
    return {"kind": kind, "id": url}


def node_fields(kind, value, previous=None):
    """The checked, defaulted fields of one node type. ``previous`` supplies omitted fields."""
    base = dict(previous or {})
    base.update(value)
    if kind == "IMAGE":
        # An unpicked image is allowed while editing; the step that reads it is blocked.
        return {"source": _source(base["source"]) if base.get("source") is not None else None}
    if kind == "SCENE_CAPTURE":
        return {"framing": _choice(base.get("framing", "CAMERA"), FRAMINGS, "framing"),
                "shading": _choice(base.get("shading", "CLAY"), SHADINGS, "shading"),
                "subject": _choice(base.get("subject", "SCENE"), SUBJECTS, "subject"),
                "resolution": _choice(base.get("resolution", 1024), RESOLUTIONS, "resolution")}
    if kind == "TEXT":
        return {"text": _text(base.get("text", ""), MAX_TEXT, "Text")}
    if kind in GENERATOR_TYPES:
        variants = base.get("variants", 1)
        if type(variants) is not int or not 1 <= variants <= MAX_VARIANTS:
            raise FlowError(f"variants must be a whole number from 1 to {MAX_VARIANTS}")
        review, take = base.get("review", variants > 1), base.get("take", 0)
        if type(review) is not bool:
            raise FlowError("review must be true or false")
        if type(take) is not int or not 0 <= take <= MAX_TAKE:
            raise FlowError(f"take is a whole number from 0 to {MAX_TAKE}")
        try:
            options = _validate(base.get("options", {}), OPTIONS)
        except ValueError as error:
            raise FlowError(str(error)) from None
        # An unset account/model is allowed while editing; evaluation blocks the step.
        model, connection = base.get("model", ""), base.get("connection_id", "")
        if not isinstance(model, str) or len(model) > 128:
            raise FlowError("Use the exact catalog model ID")
        return {"connection_id": _uuid(connection, "generation account") if connection else "", "model": model,
                "prompt": _text(base.get("prompt", ""), MAX_PROMPT, "Prompt"),
                "options": options, "variants": variants, "review": review, **({"take": take} if take else {})}
    if kind == "PLACE_IN_SCENE":
        return {}
    if kind == "SPLIT_SHEET":
        rows = base.get("rows", 2)
        if type(rows) is not int or rows not in (1, 2):
            raise FlowError("rows is 1 (front views) or 2 (front and back views)")
        return {"rows": rows, "slots": _slots(base.get("slots"))}
    if kind == "GENERATE_3D_EACH":
        fields = node_fields("GENERATE_3D", dict(base, variants=1, review=bool(base.get("review", False))))
        if base.get("variants", 1) != 1:
            raise FlowError("A per-part 3D step makes one model per part; variants must be 1")
        return fields
    if kind == "PLACE_CHARACTER":
        height = base.get("height", 1.7)
        if not isinstance(height, (int, float)) or isinstance(height, bool) or not 0.1 <= height <= 10:
            raise FlowError("height is the character's height in metres, 0.1 to 10")
        return {"height": round(float(height), 3)}
    if kind == "SUBFLOW":
        # An unset recipe is allowed while editing; evaluation blocks the node.
        recipe = base.get("recipe", "")
        return {"recipe": flow_name(recipe) if recipe else ""}
    raise FlowError("Unknown node type; use one of: " + ", ".join(NODE_TYPES))


def _slots(value):
    """A Split Sheet's columns, left to right: [{id, kind, items}] with exactly one BODY slot."""
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_SLOTS:
        raise FlowError(f"slots lists the sheet's columns, 1 to {MAX_SLOTS}: [{{id, kind, items}}]")
    slots, seen = [], set()
    for slot in value:
        if not isinstance(slot, dict) or set(slot) - {"id", "kind", "items"}:
            raise FlowError("Each slot is {id, kind, items}")
        identity = _name(slot.get("id"), "slot")
        if identity in seen or "/" in identity:
            raise FlowError(f"Slot names are unique and contain no '/'; '{identity}' isn't")
        seen.add(identity)
        items = _choice(slot.get("items", "whole"), ("whole", "split"), "items")
        slots.append({"id": identity, "kind": _choice(slot.get("kind"), SLOT_KINDS, "Slot kind"), "items": items})
    if [slot["kind"] for slot in slots].count("BODY") != 1:
        raise FlowError("A parts sheet has exactly one BODY slot, the figure the other parts are placed on")
    return slots


def _location(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) <= 100000 for v in value)):
        raise FlowError("location is [x, y] in Node Editor units")
    return [round(float(v), 1) for v in value]


def interface_of(flow):
    """A recipe's interface for the flows that use it: {inputs: {name: kind}, output: kind}, or None."""
    recipe = flow.get("recipe")
    if not recipe:
        return None
    kinds = {node["id"]: OUTPUTS[node["type"]] for node in flow["nodes"]}
    return {"inputs": {name: kinds[name] for name in recipe["inputs"]}, "output": kinds[recipe["output"]]}


def inputs_for(node, interfaces):
    """{input: (socket kind, most links)} of one canonical node."""
    if node["type"] != "SUBFLOW":
        return INPUTS.get(node["type"], {})
    interface = interfaces.get(node["recipe"]) if node["recipe"] else None
    return {name: (kind, 1) for name, kind in interface["inputs"].items()} if interface else {}


def output_for(node, interfaces):
    if node["type"] != "SUBFLOW":
        return OUTPUTS[node["type"]]
    interface = interfaces.get(node["recipe"]) if node["recipe"] else None
    return interface["output"] if interface else None


def _recipe(value, nodes):
    """Check a recipe interface: exposed source inputs and one output node."""
    if not isinstance(value, dict) or set(value) != {"inputs", "output"} or not isinstance(value["inputs"], list):
        raise FlowError("A recipe is {inputs: [Text, Image or Scene Capture node ids], output: node id}")
    inputs = list(dict.fromkeys(value["inputs"]))
    for name in inputs:
        if name not in nodes or nodes[name]["type"] not in SOURCE_TYPES:
            raise FlowError(f"Recipe input '{name}' must be a Text, Image or Scene Capture node in this flow")
    output = value["output"]
    if output not in nodes or nodes[output]["type"] not in GENERATOR_TYPES:
        raise FlowError("A recipe's output must be one of its Generate Image or Generate 3D nodes")
    if any(node["type"] == "SUBFLOW" for node in nodes.values()):
        raise FlowError("A recipe can't contain a Subflow node yet")
    if any(node["type"] in PART_TYPES for node in nodes.values()):
        raise FlowError("A recipe can't contain character-part steps (Split Sheet, per-part 3D, Place Character) yet")
    return {"inputs": inputs, "output": output}


def normalize(definition, interfaces=None):
    """Check a whole definition and return its canonical form.

    Canonical means: known fields only, defaults applied, links in a stable
    order. Cycles, unknown inputs, wrong socket kinds and over-limit inputs
    are refused here, so every later step can trust the graph's shape.
    ``interfaces`` maps recipe names to ``interface_of`` results; a Subflow
    node whose recipe is missing accepts and emits nothing.
    """
    interfaces = interfaces or {}
    if not isinstance(definition, dict) or definition.get("format", FORMAT) != FORMAT:
        raise FlowError("Unsupported flow format; expected " + FORMAT)
    nodes, links = definition.get("nodes", []), definition.get("links", [])
    if not isinstance(nodes, list) or not isinstance(links, list):
        raise FlowError("A flow has a nodes list and a links list")
    if len(nodes) > MAX_NODES or len(links) > MAX_LINKS:
        raise FlowError(f"A flow is limited to {MAX_NODES} nodes and {MAX_LINKS} links")
    result, seen = [], {}
    for node in nodes:
        if not isinstance(node, dict):
            raise FlowError("Each node is an object")
        identity = _name(node.get("id"))
        if identity in seen:
            raise FlowError(f"Node name '{identity}' is used twice")
        kind = _choice(node.get("type"), NODE_TYPES, "Node type")
        entry = {"id": identity, "type": kind}
        if node.get("label"):
            entry["label"] = _text(node["label"], 64, "Label")
        if node.get("location") is not None:
            entry["location"] = _location(node["location"])
        entry.update(node_fields(kind, node.get("fields", {})))
        if kind in GENERATOR_TYPES and node.get("chosen"):
            entry["chosen"] = _uuid(node["chosen"], "chosen output")
        if kind in ("SUBFLOW", "GENERATE_3D_EACH") and node.get("chosen"):
            entry["chosen"] = _inner_choices(node["chosen"])
        seen[identity] = entry
        result.append(entry)
    checked, counts = [], {}
    for link in links:
        if not isinstance(link, dict) or set(link) != {"from", "to", "input"}:
            raise FlowError("Each link is {from, to, input}")
        source, target = seen.get(link["from"]), seen.get(link["to"])
        if source is None or target is None:
            raise FlowError("A link names a node that isn't in the flow")
        inputs = inputs_for(target, interfaces)
        if link["input"] not in inputs:
            raise FlowError(f"'{target['id']}' has no input '{link['input']}'; inputs: " + (", ".join(inputs) or "none"))
        kind, limit = inputs[link["input"]]
        produced = output_for(source, interfaces)
        if produced != kind:
            raise FlowError(f"'{source['id']}' produces {produced or 'nothing'}, but '{link['input']}' takes {kind}")
        slot = (target["id"], link["input"])
        counts[slot] = counts.get(slot, 0) + 1
        if counts[slot] > limit:
            raise FlowError(f"'{target['id']}' accepts at most {limit} link(s) into '{link['input']}'")
        if (link["from"], link["to"], link["input"]) in {(l["from"], l["to"], l["input"]) for l in checked}:
            raise FlowError("The same link is listed twice")
        checked.append({"from": link["from"], "to": link["to"], "input": link["input"]})
    flow = {"format": FORMAT, "nodes": result, "links": checked}
    if definition.get("recipe"):
        flow["recipe"] = _recipe(definition["recipe"], seen)
    topological_order(flow)  # Refuses cycles.
    return flow


def _inner_choices(value):
    """Chosen outputs of a Subflow's inner steps or a per-part step's parts: {step or part name: asset ID}."""
    if not isinstance(value, dict) or len(value) > MAX_NODES:
        raise FlowError("A Subflow or per-part node's chosen outputs map step or part names to asset IDs")
    return {_name(name): _uuid(identity, "chosen output") for name, identity in sorted(value.items())}


def inputs_of(flow, node_id, name):
    """Upstream node IDs linked into one input, in link order (prompt fragments keep it)."""
    return [link["from"] for link in flow["links"] if link["to"] == node_id and link["input"] == name]


def topological_order(flow):
    """Node IDs, dependencies first; ties keep declaration order. Raises on a cycle."""
    order, remaining = [], [node["id"] for node in flow["nodes"]]
    needs = {identity: {l["from"] for l in flow["links"] if l["to"] == identity} for identity in remaining}
    done = set()
    while remaining:
        ready = [identity for identity in remaining if needs[identity] <= done]
        if not ready:
            raise FlowError("The flow has a cycle; a step can't depend on its own output")
        for identity in ready:
            order.append(identity)
            done.add(identity)
        remaining = [identity for identity in remaining if identity not in done]
    return order


def descendants(flow, node_id):
    found, frontier = set(), [node_id]
    while frontier:
        current = frontier.pop()
        for link in flow["links"]:
            if link["from"] == current and link["to"] not in found:
                found.add(link["to"])
                frontier.append(link["to"])
    return found


def ancestors(flow, node_id):
    found, frontier = set(), [node_id]
    while frontier:
        current = frontier.pop()
        for link in flow["links"]:
            if link["to"] == current and link["from"] not in found:
                found.add(link["from"])
                frontier.append(link["from"])
    return found


def revision(flow):
    """A short content revision of a canonical flow, for stale-edit rejection."""
    body = json.dumps(flow, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:REVISION_LENGTH]


def apply_edit(flow, edit, interfaces=None):
    """Return a new canonical flow with an agent edit applied. ``flow`` may be empty.

    ``nodes`` creates or updates nodes by name (an update keeps omitted fields),
    ``remove`` deletes nodes and their links, ``unlink`` clears the links into
    named inputs, and ``links`` then adds links in order. ``recipe`` sets the
    flow's recipe interface (null clears it). A node's type can't change;
    remove and re-create it instead.
    """
    current = {node["id"]: node for node in flow.get("nodes", [])}
    order = [node["id"] for node in flow.get("nodes", [])]
    links = list(flow.get("links", []))
    for identity in edit.get("remove", []):
        if identity not in current:
            raise FlowError(f"There's no node named '{identity}' to remove")
        current.pop(identity)
        order.remove(identity)
        links = [l for l in links if identity not in (l["from"], l["to"])]
    for node in edit.get("nodes", []):
        identity, kind = _name(node.get("id")), node.get("type")
        existing = current.get(identity)
        if existing and kind not in (None, existing["type"]):
            raise FlowError(f"'{identity}' is a {existing['type']}; remove it and add a new node to change its type")
        kind = kind or (existing or {}).get("type")
        if kind is None:
            raise FlowError(f"Give the new node '{identity}' a type")
        previous = {k: v for k, v in (existing or {}).items() if k not in _NODE_KEYS}
        fields = node_fields(kind, node.get("fields", {}), previous)
        entry = {"id": identity, "type": kind, **fields}
        for key in ("label", "location"):
            if node.get(key) is not None:
                entry[key] = node[key]
            elif existing and key in existing:
                entry[key] = existing[key]
        if existing and existing.get("chosen") and existing["type"] == kind:
            entry["chosen"] = existing["chosen"]
        if identity not in current:
            order.append(identity)
        current[identity] = entry
    for target in edit.get("unlink", []):
        links = [l for l in links if (l["to"], l["input"]) != (target.get("to"), target.get("input"))]
    links.extend({"from": l.get("from"), "to": l.get("to"), "input": l.get("input")} for l in edit.get("links", []))
    recipe = edit["recipe"] if "recipe" in edit else flow.get("recipe")
    if recipe:
        removed = set(edit.get("remove", []))
        recipe = {"inputs": [n for n in recipe.get("inputs", []) if n not in removed], "output": recipe.get("output")}
    return normalize({"format": FORMAT, "nodes": [_definition(current[i]) for i in order], "links": links,
                      "recipe": recipe}, interfaces)


_NODE_KEYS = frozenset({"id", "type", "label", "location", "chosen"})


def _definition(entry):
    node = {"id": entry["id"], "type": entry["type"], "fields": {k: v for k, v in entry.items() if k not in _NODE_KEYS}}
    node.update({k: entry[k] for k in ("label", "location", "chosen") if k in entry})
    return node


def as_definition(flow):
    """The ``nodes[].fields`` shape ``normalize`` accepts, from a canonical flow."""
    definition = {"format": FORMAT, "nodes": [_definition(n) for n in flow["nodes"]], "links": list(flow["links"])}
    if flow.get("recipe"):
        definition["recipe"] = dict(flow["recipe"])
    return definition
