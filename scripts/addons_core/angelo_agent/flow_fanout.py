# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Per-part 3D steps as ordinary Generate 3D steps, once their parts are known. No Blender dependency.

A GENERATE_3D_EACH node makes one model per part of a split parts sheet. The
parts only exist after the sheet is generated and split (a free, local step),
so the node can't be planned before that: until then it evaluates as waiting,
and the sheet's review stop is where the user sees the parts before paying.

Once its Split Sheet step is ready, ``expand_parts`` replaces the node with, for
each part, an internal Image node holding the part's crop (a library image) and
a GENERATE_3D step named ``<node>/<part>`` that copies the node's account,
model, prompt and options. Input keys, reuse, staleness, planning, the itemized
approval card and runs then work on those steps unchanged, exactly as for
Subflow expansion: there is one run path. Each part keeps its own chosen output
in the node's ``chosen`` map, by part name.

Links out of the node (into Place Character's ``models``) leave from every part
step, and a ``parts`` link from the Split Sheet step to each part step keeps
scope and ordering ("from the sheet downstream" includes the parts).
"""

from .flow_spec import _NAME, inputs_of

SEPARATOR = "/"
IMAGE_SUFFIX = " · image"
READY = "READY"


def part_step(node_id, part_id):
    return node_id + SEPARATOR + part_id


def part_label(part):
    """A part's display name: its slot, numbered when the slot was split into items."""
    return part["slot"] if not part.get("numbered") else f"{part['slot']} {part['item'] + 1}"


def expand_parts(flow, states):
    """The flow with every expandable per-part node replaced by its part steps.

    ``states`` are evaluation states of ``flow``; a node expands only when the
    Split Sheet step feeding its ``parts`` input is READY. Returns ``flow``
    itself when nothing expands. Expanded part steps carry ``fanout`` (the
    node's name) and ``part`` (the part's name); their images carry ``internal``.
    """
    fanouts = {node["id"]: node for node in flow["nodes"] if node["type"] == "GENERATE_3D_EACH"}
    expandable = {}
    for identity in fanouts:
        source = inputs_of(flow, identity, "parts")
        state = states.get(source[0]) if source else None
        if state and state.get("state") == READY and state.get("value"):
            expandable[identity] = (source[0], state["value"])
    if not expandable:
        return flow
    nodes, links, outputs = [], [], {}
    for node in flow["nodes"]:
        if node["id"] not in expandable:
            nodes.append(node)
            continue
        split, manifest = expandable[node["id"]]
        prompts = inputs_of(flow, node["id"], "prompt")
        choices = node.get("chosen") or {}
        too_long = [part_step(node["id"], p["id"]) + IMAGE_SUFFIX for p in manifest["parts"]
                    if not _NAME.fullmatch(part_step(node["id"], p["id"]) + IMAGE_SUFFIX)]
        if too_long:
            nodes.append(dict(node, problem=f"'{too_long[0]}' is longer than 63 characters; shorten the node's name"))
            continue
        members = []
        for part in manifest["parts"]:
            step = part_step(node["id"], part["id"])
            image = step + IMAGE_SUFFIX
            nodes.append({"id": image, "type": "IMAGE", "source": {"kind": "ASSET", "id": part["asset"]},
                          "fanout": node["id"], "part": part["id"], "internal": True})
            generator = {"id": step, "type": "GENERATE_3D",
                         **{key: node[key] for key in ("connection_id", "model", "prompt", "options")},
                         "variants": 1, "review": False, "fanout": node["id"], "part": part["id"],
                         "label": (node.get("label") or node["id"]) + " › " + part_label(part)}
            if choices.get(part["id"]):
                generator["chosen"] = choices[part["id"]]
            nodes.append(generator)
            links.append({"from": image, "to": step, "input": "reference"})
            links.append({"from": split, "to": step, "input": "parts"})
            links.extend({"from": source, "to": step, "input": "prompt"} for source in prompts)
            members.append(step)
        outputs[node["id"]] = members
    for link in flow["links"]:
        if link["to"] in outputs:
            continue  # The node's own inputs: consumed by its part steps above.
        if link["from"] in outputs:
            links.extend({"from": member, "to": link["to"], "input": link["input"]} for member in outputs[link["from"]])
            continue
        links.append(link)
    expanded = dict(flow, nodes=nodes, links=links)
    return expanded


def members(flow, name):
    """Part step names that belong to the per-part node ``name`` (its generators, not their images)."""
    return [node["id"] for node in flow["nodes"] if node.get("fanout") == name and node["type"] == "GENERATE_3D"]


def part_summaries(flow, states):
    """Per expanded per-part node: its parts (from the split) and each part's chosen model, for drawing."""
    summaries = {}
    for node in flow["nodes"]:
        if node.get("fanout") and node["type"] == "GENERATE_3D" and node["fanout"] not in summaries:
            split = next((l["from"] for l in flow["links"] if l["to"] == node["id"] and l["input"] == "parts"), None)
            value = (states.get(split) or {}).get("value") or {}
            names = members(flow, node["fanout"])
            summaries[node["fanout"]] = {
                "parts": value.get("parts", []),
                "chosen": {entry["part"]: (states.get(entry["id"]) or {}).get("chosen")
                           for entry in flow["nodes"] if entry["id"] in names}}
    return summaries
