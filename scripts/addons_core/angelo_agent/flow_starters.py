# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Ready-made Generation Flows a person starts from (1.0.5): image to 3D, and image to reference to 3D. No Blender.

Each starter is an ordinary ``angelo.flow/1`` definition built only from today's node types, so the flow it creates
is the person's to edit, run and save like any other (``flow_open.from_starter`` writes it with
``flow_tree.write_flow``). It names no account: a generator step without one asks the person to choose or connect
one, as every step does. Prompts are model-visible text and stay in English; titles and summaries are translated
where they are drawn.

The entry points (Director's Flows menu, the Angelo menu, the composer's + menu) list ``STARTERS`` by ``id`` through
``flow_open``. Media Studio's Workflows tab (feature/v1-flow-templates, a later release) can list the same ids, or
replace this table with its ``angelo.flow-template/1`` templates, without changing those entry points.
"""

import copy

from .flow_spec import FORMAT, normalize
from .ui_text import n_

REFERENCE_PROMPT = ("Turn the image into a clean reference image of the same subject for 3D modelling: the whole "
                    "subject centred and fully visible, a three-quarter front view, a plain light-grey background, "
                    "even soft lighting, no shadow on the ground, no text. Keep its shapes, colours and materials.")

STARTERS = (
    {"id": "image_to_3d", "title": n_("Image to 3D"), "name": "Image to 3D",
     "summary": n_("Pick an image, make a 3D model of it and place it in the scene"),
     "flow": {"format": FORMAT,
              "nodes": [{"id": n_("Image", "Angelo"), "type": "IMAGE", "location": [-420, 0]},
                        {"id": n_("3D model", "Angelo"), "type": "GENERATE_3D", "location": [-120, 0]},
                        {"id": n_("Place in scene", "Angelo"), "type": "PLACE_IN_SCENE", "location": [180, 0]}],
              "links": [{"from": "Image", "to": "3D model", "input": "reference"},
                        {"from": "3D model", "to": "Place in scene", "input": "model"}]}},
    {"id": "image_reference_3d", "title": n_("Image to reference to 3D"), "name": "Image to reference to 3D",
     "summary": n_("Turn a sketch or photo into a clean reference image first, then a 3D model"),
     "flow": {"format": FORMAT,
              "nodes": [{"id": n_("Image", "Angelo"), "type": "IMAGE", "location": [-720, 0]},
                        {"id": n_("Reference", "Angelo"), "type": "GENERATE_IMAGE", "location": [-420, 0],
                         "fields": {"prompt": REFERENCE_PROMPT, "variants": 2, "review": True}},
                        {"id": n_("3D model", "Angelo"), "type": "GENERATE_3D", "location": [-120, 0]},
                        {"id": n_("Place in scene", "Angelo"), "type": "PLACE_IN_SCENE", "location": [180, 0]}],
              "links": [{"from": "Image", "to": "Reference", "input": "references"},
                        {"from": "Reference", "to": "3D model", "input": "reference"},
                        {"from": "3D model", "to": "Place in scene", "input": "model"}]}},
)


def starter(starter_id):
    return next((entry for entry in STARTERS if entry["id"] == starter_id), None)


def instantiate(starter_id):
    """The starter's canonical flow (a copy: the table is never edited), or KeyError."""
    entry = starter(starter_id)
    if entry is None:
        raise KeyError(starter_id)
    return normalize(copy.deepcopy(entry["flow"]))


# The flow names Angelo gives: New Flow's, Media Studio's Turn into a flow's and each template's title. Stored as
# they are (a flow's name is its identity for the agent and Subflow steps) and shown translated by ``flow_label``.
NEW_FLOW = n_("Generation Flow")
STUDIO_FLOW = n_("Media Studio flow")


def flow_label(name):
    """A flow's name as shown: a name Angelo gave is translated (Blender's ``.001`` suffix stays), a typed one stays
    as typed. Never stored."""
    from .ui_text import iface_
    base, dot, number = name.rpartition(".")
    base, suffix = (base, "." + number) if dot and number.isdigit() and len(number) == 3 else (name, "")
    defaults = {NEW_FLOW, STUDIO_FLOW} | {entry["title"] for entry in STARTERS}
    return (iface_(base) if base in defaults else base) + suffix

