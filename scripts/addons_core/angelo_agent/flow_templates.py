# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Generation Flow templates (``angelo.flow-template/1``): bundled, read-only, versioned flows. No Blender.

A template is a flow that ships with Angelo, and this table is the only list of them (1.0.5 folded the earlier
"starter" flows in). It is used two ways from one definition:

* **Generation Flow** creates it as ordinary, editable nodes (``instantiate`` returns an ``angelo.flow/1``
  definition that ``flow_tree.write_flow`` writes; ``flow_open`` offers every template from Director's Flows
  menu, the Angelo menu, the Node Editor's Add menu and the chat bar's + menu), so every prompt, model and slot
  can be changed and saved as the person's own flow.
* **Media Studio's Workflows tab** (``studio_workflows``) runs a template marked ``studio`` as plain input →
  output: it shows only its ``inputs`` and review stops and runs the same instantiated flow through the same
  planner, approval card and runner, so a step finished in one surface is reused for free in the other.

A template never names an account. Its generator nodes name a **role** (the
image or 3D model it needs); ``instantiate`` resolves each role to one of the
user's connected catalog entries, preferring the template's listed models, and
applies that provider's options. An unresolved role leaves the step without an
account, and the node asks the user to choose or connect one, as any step does.

Titles and summaries are translated at display time (``n_``); prompts and step names are model-visible text and
stay in English.
"""

import copy

from .flow_spec import FORMAT as FLOW_FORMAT, NODE_WIDTHS, FlowError, normalize
from .ui_text import n_

FORMAT = "angelo.flow-template/1"

# Model-visible prompts (never translated). The parts-sheet wording is the research's final prompt:
# a modest mannequin body (undressing wording is refused by image moderation), one column per slot,
# body parts only in the body column, and one shared full-height frame so parts keep their placement.
REFERENCE_PROMPT = ("Turn the image into a clean reference image of the same subject for 3D modelling: the whole "
                    "subject centred and fully visible, a three-quarter front view, a plain light-grey background, "
                    "even soft lighting, no shadow on the ground, no text. Keep its shapes, colours and materials.")
CHARACTER_PROMPT = ("Full-body character design. Standing straight in a relaxed A-pose, arms held slightly away from the "
                    "body, feet slightly apart, facing the camera, orthographic front view. The entire figure is visible "
                    "from head to toe with a margin. Plain flat light-grey background, even soft lighting, no ground "
                    "shadow, no text. The character:")
PARTS_SHEET_PROMPT = (
    "Using the character in the reference image, create a technical parts sheet on a plain flat white background, "
    "laid out as a grid of 8 equal columns and 2 rows separated by thin light-grey lines. Top row: front views. "
    "Bottom row: back views of the same parts. Columns, left to right: 1 Base body: the same character's body as a "
    "smooth, simplified mannequin with no anatomical detail, with the same height, build, proportions, face, skin "
    "colour, hands and feet, and any tail, ears, horns or wings, dressed in a plain modest matte light-grey "
    "long-sleeved base layer and full-length leggings; the head has no hair. 2 Hair: all hair, including facial "
    "hair, without the head. 3 Headwear. 4 Inner upper layer: shirts, tops, sweaters and anything worn under an "
    "outer layer. 5 Outer layer: jackets, coats, robes, capes, armour, and any one-piece garment such as a dress. "
    "6 Lower garments: trousers, shorts, skirts. 7 Footwear. 8 Accessories: glasses, jewellery, scarves, belts, "
    "bags, weapons and held items. Every item appears in exactly one cell and is never repeated in another cell. "
    "Body parts appear only in column 1: no other cell shows any head, face, ears, hands, arms, legs or feet, and "
    "the base body wears nothing but its base layer. Draw each part in its cell exactly where and exactly as large "
    "as it sits on the full character in the same pose, as if everything else were invisible: all cells share one "
    "full-height frame, with the top of the head near the top of the cell and the soles of the feet near the "
    "bottom, so stacking the cells would rebuild the character. Draw garments as they are worn, including the areas "
    "normally hidden by other layers. If the character has no part for a column, leave both of its cells empty. "
    "Keep each part's colours, materials and details identical to the reference. Orthographic views, even soft "
    "lighting, no shadows, no labels, no text.")
CHARACTER_SLOTS = [
    {"id": "body", "kind": "BODY"}, {"id": "hair", "kind": "HAIR"}, {"id": "headwear", "kind": "HEADWEAR"},
    {"id": "inner", "kind": "INNER"}, {"id": "outer", "kind": "OUTER"}, {"id": "lower", "kind": "LOWER"},
    {"id": "footwear", "kind": "FOOTWEAR"}, {"id": "accessories", "kind": "ACCESSORY", "items": "split"}]

# Roles: what a generator step needs, the models to prefer in order, and options per provider mode.
# Tripo isn't preferred (Angelo doesn't lean on it), but a user whose only 3D account is Tripo can still run.
IMAGE_ROLE = {"kind": "IMAGE", "prefer": ["gpt-image-2.5-sunburst", "gpt-image-2.5-flare"],
              "options": {"openai_images_api": {"quality": "high"}, "*": {}}}
SHEET_ROLE = {"kind": "IMAGE", "prefer": ["gpt-image-2.5-sunburst", "gpt-image-2.5-flare"],
              "options": {"openai_images_api": {"size": "3840x1920", "quality": "high", "background": "opaque"},
                          "*": {"aspect": "16:9", "resolution": "4K"}}}
MESH_ROLE = {"kind": "3D", "prefer": ["meshy-7", "meshy-7.1"], "options": {"*": {"texture": True}}}
REFERENCE_ROLE = {"kind": "IMAGE", "prefer": ["gpt-image-2.5-sunburst", "gpt-image-2.5-flare"], "options": {"*": {}}}


# Step names: stored in English in files (the agent and Media Studio's Workflows tab find steps by them) and shown
# translated, as every name Angelo gives (flow_setup.default_names).
SHEET_STEP, SPLIT_STEP, PARTS_STEP, CHARACTER_STEP = (n_("Parts sheet", "Angelo"), n_("Split", "Angelo"),
                                                      n_("Parts 3D", "Angelo"), n_("Character", "Angelo"))
CHARACTER_IMAGE, DESCRIPTION = n_("Character image", "Angelo"), n_("Description", "Angelo")


GAP = 80   # Node Editor units between steps laid out side by side.


def _in_a_row(nodes, x):
    """``nodes`` placed left to right from ``x``, each after the one before by that one's drawn width
    (flow_spec.NODE_WIDTHS) and GAP: a step never covers the next, whatever their widths."""
    for node in nodes:
        node["location"] = [x, 0]
        x += NODE_WIDTHS[node["type"]] + GAP
    return nodes


def _sheet_steps():
    return [
        {"id": SHEET_STEP, "type": "GENERATE_IMAGE", "role": "sheet",
         "fields": {"prompt": PARTS_SHEET_PROMPT, "review": True}},
        {"id": SPLIT_STEP, "type": "SPLIT_SHEET", "fields": {"slots": CHARACTER_SLOTS}},
        {"id": PARTS_STEP, "type": "GENERATE_3D_EACH", "role": "mesh", "fields": {}},
        {"id": CHARACTER_STEP, "type": "PLACE_CHARACTER", "fields": {"height": 1.7}},
    ]


_SHEET_LINKS = [
    {"from": SHEET_STEP, "to": SPLIT_STEP, "input": "sheet"},
    {"from": SPLIT_STEP, "to": PARTS_STEP, "input": "parts"},
    {"from": SPLIT_STEP, "to": CHARACTER_STEP, "input": "parts"},
    {"from": PARTS_STEP, "to": CHARACTER_STEP, "input": "models"},
]

TEMPLATES = {
    "image_to_3d": {
        "format": FORMAT, "id": "image_to_3d", "version": 1,
        "title": n_("Image to 3D"),
        "summary": n_("Pick an image, make a 3D model of it and place it in the scene"),
        "inputs": [{"node": "Image", "kind": "IMAGE", "label": n_("Image")}],
        "review": [],
        "roles": {"mesh": MESH_ROLE},
        "flow": {"nodes": _in_a_row([{"id": n_("Image", "Angelo"), "type": "IMAGE"},
                                     {"id": n_("3D model", "Angelo"), "type": "GENERATE_3D", "role": "mesh"},
                                     {"id": n_("Place in scene", "Angelo"), "type": "PLACE_IN_SCENE"}], -420),
                 "links": [{"from": "Image", "to": "3D model", "input": "reference"},
                           {"from": "3D model", "to": "Place in scene", "input": "model"}]},
    },
    "image_reference_3d": {
        "format": FORMAT, "id": "image_reference_3d", "version": 1,
        "title": n_("Image to reference to 3D"),
        "summary": n_("Turn a sketch or photo into a clean reference image first, then a 3D model"),
        "inputs": [{"node": "Image", "kind": "IMAGE", "label": n_("Image")}],
        "review": ["Reference"],
        "roles": {"reference": REFERENCE_ROLE, "mesh": MESH_ROLE},
        "flow": {"nodes": _in_a_row([{"id": n_("Image", "Angelo"), "type": "IMAGE"},
                                     {"id": n_("Reference", "Angelo"), "type": "GENERATE_IMAGE", "role": "reference",
                                      "fields": {"prompt": REFERENCE_PROMPT, "variants": 2, "review": True}},
                                     {"id": n_("3D model", "Angelo"), "type": "GENERATE_3D", "role": "mesh"},
                                     {"id": n_("Place in scene", "Angelo"), "type": "PLACE_IN_SCENE"}], -720),
                 "links": [{"from": "Image", "to": "Reference", "input": "references"},
                           {"from": "Reference", "to": "3D model", "input": "reference"},
                           {"from": "3D model", "to": "Place in scene", "input": "model"}]},
    },
    "character-in-parts": {
        "format": FORMAT, "id": "character-in-parts", "version": 1, "studio": True,
        "title": n_("Character in parts"),
        "summary": n_("A character image becomes separate 3D parts (body, hair, clothing, accessories) placed together"),
        "inputs": [{"node": "Character image", "kind": "IMAGE", "label": n_("Character image")}],
        "review": [SHEET_STEP],
        "roles": {"sheet": SHEET_ROLE, "mesh": MESH_ROLE},
        "flow": {"nodes": _in_a_row([{"id": CHARACTER_IMAGE, "type": "IMAGE", "fields": {"source": None}}]
                                    + _sheet_steps(), -340),
                 "links": [{"from": "Character image", "to": SHEET_STEP, "input": "references"}] + _SHEET_LINKS},
    },
    "character-in-parts-from-text": {
        "format": FORMAT, "id": "character-in-parts-from-text", "version": 1, "studio": True,
        "title": n_("Character in parts from a description"),
        "summary": n_("A description becomes a character image, then separate 3D parts placed together"),
        "inputs": [{"node": "Description", "kind": "TEXT", "label": n_("Description")}],
        "review": ["Character image", SHEET_STEP],
        "roles": {"image": IMAGE_ROLE, "sheet": SHEET_ROLE, "mesh": MESH_ROLE},
        "flow": {"nodes": _in_a_row([{"id": DESCRIPTION, "type": "TEXT", "fields": {"text": ""}},
                                     {"id": CHARACTER_IMAGE, "type": "GENERATE_IMAGE", "role": "image",
                                      "fields": {"prompt": CHARACTER_PROMPT, "options": {"aspect": "3:4"},
                                                 "review": True}}]
                                    + _sheet_steps(), -680),
                 "links": [{"from": "Description", "to": "Character image", "input": "prompt"},
                           {"from": "Character image", "to": SHEET_STEP, "input": "references"}] + _SHEET_LINKS},
    },
}


def listing():
    """[{id, version, title, summary, inputs, studio}] for menus and Media Studio, in a stable order."""
    return [dict({key: template[key] for key in ("id", "version", "title", "summary", "inputs")},
                 studio=template.get("studio", False)) for template in TEMPLATES.values()]


def template(template_id):
    """The template named ``template_id``, or None."""
    return TEMPLATES.get(template_id)


def studio_ids():
    """The templates Media Studio's Workflows tab runs (marked ``studio``), in order."""
    return [key for key, value in TEMPLATES.items() if value.get("studio")]


def _choose(role, entries):
    """The catalog entry a role resolves to: a ready preferred model first, then any ready entry of its kind."""
    ready = [entry for entry in entries if entry.get("kind") == role["kind"] and entry.get("ready")]
    for model in role["prefer"]:
        match = next((entry for entry in ready if entry["id"] == model), None)
        if match:
            return match
    return ready[0] if ready else None


def _options(role, entry, fields):
    by_mode = role["options"]
    options = dict(by_mode.get(entry["mode"], by_mode.get("*", {})))
    if entry["mode"] == "openai_images_api" and "size" in options:
        return dict(options)  # An explicit size replaces aspect/resolution for OpenAI images.
    options.update(fields.get("options", {}))
    return options


def instantiate(template_id, entries):
    """(canonical angelo.flow/1 flow, unresolved role names) for a template and the user's catalog entries.

    ``entries`` are catalog entries ``{connection, id, mode, kind, ready}``.
    """
    template = TEMPLATES.get(template_id)
    if template is None:
        raise FlowError(f"There's no template named '{template_id}'")
    nodes, unresolved = [], []
    for node in copy.deepcopy(template["flow"]["nodes"]):
        role_name = node.pop("role", None)
        if role_name:
            role = template["roles"][role_name]
            entry = _choose(role, entries)
            fields = node.setdefault("fields", {})
            if entry is None:
                unresolved.append(role_name)  # The step stays without an account; its node asks for one.
            else:
                fields.update(connection_id=entry["connection"], model=entry["id"], options=_options(role, entry, fields))
        nodes.append(node)
    flow = normalize({"format": FLOW_FORMAT, "nodes": nodes, "links": copy.deepcopy(template["flow"]["links"])})
    return flow, sorted(set(unresolved))


# The flow names Angelo gives: New Flow's, Media Studio's Turn into a flow's and each template's title. Stored as
# they are (a flow's name is its identity for the agent and Subflow steps) and shown translated by ``flow_label``.
NEW_FLOW = n_("Generation Flow")
STUDIO_FLOW = n_("Media Studio flow")


def flow_label(name):
    """A flow's name as shown: a name Angelo gave is translated (Blender's ``.001`` suffix stays), a typed one stays
    as typed. Never stored."""
    from .ui_text import iface_
    defaults = {NEW_FLOW, STUDIO_FLOW} | {template["title"] for template in TEMPLATES.values()}
    title, separator, rest = name.partition(" · ")   # A Workflows run: "<template title> · <its input>".
    if separator and title in defaults:
        return iface_(title) + separator + rest
    base, dot, number = name.rpartition(".")
    base, suffix = (base, "." + number) if dot and number.isdigit() and len(number) == 3 else (name, "")
    return (iface_(base) if base in defaults else base) + suffix


def step_names():
    """Every step name the templates give (shown translated, flow_setup.default_names)."""
    return {node["id"] for template in TEMPLATES.values() for node in template["flow"]["nodes"]}
