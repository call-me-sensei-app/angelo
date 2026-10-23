# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tools the person chooses for a request, and the composer's + menu that offers them. No Blender here.

Angelo never chooses for the model, and a model given a helpful aid as a fact
may still not reach for it (the bundled base meshes: 0 uses in 20 trials). So
the choice is the person's: the composer's + menu lists attachments and tools,
each tool with what it gives the person and what Angelo will do. A chosen tool
is a chip beside the attachments; at Send the host performs it before the
observation (``chosen_tools_host``), so the model starts from the result: the
added objects are in the scene facts it already receives. Nothing model-facing
changes.

The one tool so far is "Start from a base mesh" (``asset_start.place``, the
operation a model's typed create will share). A tool is one ``Tool`` record:
adding one is a record plus its step in the host module.
"""

from dataclasses import dataclass

from . import human_base_meshes
from .ui_text import n_

BASE_MESH_PROPERTY = human_base_meshes.PROPERTY
# Menu order of the base meshes: what a person starts a character from first.
KIND_ORDER = ("head", "body", "hand", "foot", "eye", "jaw", "skull", "skeleton")

OPTION_DETAIL = n_("{cm} cm · {verts} verts")
FIT = n_("Match the size of {name}")
FOOTER = n_("Angelo adds it when you send your message, before the agent starts.")
CHIP_KIND = n_("Start from")


@dataclass(frozen=True)
class Tool:
    id: str
    title: str        # n_: the action, as the person says it.
    why: str          # n_: what the person gets, one line.
    options: tuple    # ((value, label, height cm, vertices, kind)).


def _base_mesh_options():
    rows = [(a["name"], " ".join(a["name"].split()).replace(" - ", " – "), round(a["size"][2] * 100),
             a["total_vertices"], a["kind"]) for a in human_base_meshes.table()["assets"]]
    return tuple(sorted(rows, key=lambda row: (KIND_ORDER.index(row[4]) if row[4] in KIND_ORDER else 99, row[1])))


def tools():
    return (Tool("base_mesh", n_("Start from a base mesh"),
                 n_("Sculpt-ready, with eyes and clean topology, at real size"), _base_mesh_options()),)


def tool(tool_id):
    return next((item for item in tools() if item.id == tool_id), None)


def option(tool_id, value):
    item = tool(tool_id)
    return next((row for row in item.options if row[0] == value), None) if item else None


def option_detail(row):
    """An option's subtitle as (template, fields), translated where it is drawn."""
    return OPTION_DETAIL, {"cm": row[2], "verts": "{:,}".format(row[3])}


def choose(chosen, tool_id, value, fit_selected=False):
    """``chosen`` with this choice (one per tool, replacing an earlier one), or ValueError."""
    if option(tool_id, value) is None:
        raise ValueError("Choose one of this tool's options")
    rest = [choice for choice in chosen if choice["tool"] != tool_id]
    return rest + [{"tool": tool_id, "option": value, "fit_selected": bool(fit_selected)}]


def menu_rows(chosen=(), fit_target="", open_groups=()):
    """The + menu, top to bottom (``chat_destinations.menu_rows``: every registered feature; a group's rows only
    while ``open_groups`` has it). ``fit_target`` names the selected mesh a base mesh can be fitted to ('' with none,
    and the toggle is not offered)."""
    from . import chat_destinations
    return chat_destinations.menu_rows(chosen, fit_target, open_groups)


def base_mesh_rows(chosen=(), fit_target=""):
    """The base-mesh group's rows (``chat_destinations``): the fit toggle, then every bundled base mesh, heads first.
    Option labels are asset names, shown as they are."""
    picked = {(choice["tool"], choice["option"]): choice for choice in chosen}
    item, rows = tool("base_mesh"), []
    if fit_target:
        on = any(choice["fit_selected"] for choice in chosen if choice["tool"] == item.id)
        rows.append({"kind": "toggle", "action": "popup_add_fit", "value": item.id,
                     "label": (FIT, {"name": fit_target}), "on": on})
    for row in item.options:
        rows.append({"kind": "row", "action": "popup_add_pick", "value": (item.id, row[0]), "label": row[1],
                     "subtitle": option_detail(row), "glyph": item.id, "selected": (item.id, row[0]) in picked,
                     "translate": False, "thumbnail": row[0]})   # Its own picture (base_mesh_thumbs).
    return rows


def chip(choice):
    """(label, kind) for the composer chip of a chosen tool."""
    row = option(choice["tool"], choice["option"])
    return (row[1] if row else choice["option"]), CHIP_KIND
