# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The composer's + menu as one registry: every place Angelo's chat bar leads to (1.0.5). No Blender here.

The chat input bar is Angelo's main navigation, in Director and in Manual (maintainer, 1.0.5): from its + button a
person reaches attachments, Media Studio, Generation Flows, the base meshes and every later feature. A feature adds a
``Feature`` with ``register`` instead of a button somewhere else; ``chat_add_menu`` draws whatever is registered.

A feature is either one row that runs an operator (Media Studio), or a **group**: a header row that opens and closes,
and the rows ``children`` returns while it is open (Generation Flows, the base meshes). Every group is closed until
the person opens it, and the menu remembers their choice (``GroupMemory``, beside the chat's other local state), so
a long list (21 base meshes, every saved flow) never buries the rest. ``TOP_LEVEL`` names the features Angelo ships;
a host test checks each is registered by its module and appears in the menu.

Row dicts (drawn by ``chat_add_menu``): ``kind`` ``section`` / ``note`` / ``toggle`` / ``row`` / ``group``;
``action`` ``popup_add_open`` (run ``operator`` with ``properties``), ``popup_add_group`` (open or close ``value``),
or a feature's own (``popup_add_attach``, ``popup_add_pick``, ``popup_add_fit``, ``popup_add_choose``: a tool
with one option, chosen in one click); ``label`` / ``subtitle`` are ``n_``
constants or (template, fields) pairs, translated where drawn, except a label whose ``translate`` is False (a
flow's or an asset's name); ``glyph`` names the tile; ``depth`` 1 for a group's rows.
"""

import json
import os
from dataclasses import dataclass, field

from .ui_text import n_

# The features Angelo ships, in menu order. A module that adds one registers it at its own register(); a feature
# from another branch adds its id here with its registration (test_native_chat_destinations checks both).
TOP_LEVEL = ("attach", "media_studio", "flows", "workflows", "base_mesh", "hands", "web_export", "web_preview",
             "godot_link")

ATTACH = n_("Attach")
CREATE = n_("Create")
START_FROM = n_("Start from")


@dataclass(frozen=True)
class Feature:
    id: str
    title: str                 # n_: what the row says.
    why: str = ""              # n_: what the person gets, one line.
    glyph: str = "dot"         # The tile drawn beside it (chat_add_menu.draw_glyph).
    order: int = 100           # Menu position; built-ins use 0-40.
    section: str = ""          # n_: a heading drawn above it when it differs from the previous feature's.
    operator: str = ""         # A single row: the operator its click runs (INVOKE_DEFAULT) ...
    properties: tuple = ()     # ... with these ((name, value), ...).
    action: str = ""           # Or a feature-owned action instead of an operator (attach).
    children: object = None    # A group: callable(chosen, fit_target) -> its rows while open.
    lead_only: bool = False    # A tool performed at Send on Angelo's own scene: never offered in a worker's chat.
    owner: str = field(default="", compare=False)   # The registering module, for the coverage test.


_features = {}


def register(feature):
    """Add or replace ``feature`` (by id)."""
    if not feature.id or (feature.children is None) == (not (feature.operator or feature.action)):
        raise ValueError("A feature is one row with an operator or action, or a group with children")
    _features[feature.id] = feature


def unregister(feature_id):
    _features.pop(feature_id, None)


def features():
    return sorted(_features.values(), key=lambda item: (item.order, item.id))


def feature(feature_id):
    return _features.get(feature_id)


class GroupMemory:
    """Which groups the person opened, kept in one small JSON file; every group starts closed."""

    def __init__(self, path=None):
        self.path, self.open = path, set()
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as handle:
                    value = json.load(handle)
                self.open = {item for item in value.get("open", []) if isinstance(item, str)}
            except (OSError, ValueError, AttributeError):
                self.open = set()   # A damaged file: every group closed, as on a first launch.

    def is_open(self, group_id):
        return group_id in self.open

    def toggle(self, group_id):
        self.open ^= {group_id}
        if self.path:
            temporary = self.path + ".tmp"
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                with open(temporary, "w", encoding="utf-8") as handle:
                    json.dump({"open": sorted(self.open)}, handle)
                os.replace(temporary, self.path)
            except OSError:
                pass   # Remembered for this session only; nothing else depends on it.
        return group_id in self.open


def menu_rows(chosen=(), fit_target="", open_groups=(), worker=False):
    """Every registered feature's rows, top to bottom; a group's rows only while it is open. A worker's chat
    (``worker``) works on its worker's document, so it is offered no ``lead_only`` tool: Send would not run it."""
    rows, section = [], None
    for item in features():
        if worker and item.lead_only:
            continue
        if item.section and item.section != section:
            rows.append({"kind": "section", "label": item.section})
        section = item.section
        if item.children is None:
            rows.append({"kind": "row", "action": item.action or "popup_add_open", "value": item.id,
                         "label": item.title, "subtitle": item.why, "glyph": item.glyph, "feature": item.id,
                         "operator": item.operator, "properties": item.properties})
            continue
        opened = item.id in open_groups
        rows.append({"kind": "group", "action": "popup_add_group", "value": item.id, "label": item.title,
                     "subtitle": item.why, "glyph": item.glyph, "open": opened, "feature": item.id})
        if opened:
            rows += [dict(row, depth=1, feature=item.id) for row in item.children(chosen, fit_target)]
    return rows


def leaf(feature_id, row_id, title, subtitle="", glyph="dot", operator="", properties=(), translate=True):
    """One row inside a group that runs an operator (a flow, a template); ``row_id`` is unique in the menu."""
    return {"kind": "row", "action": "popup_add_open", "value": row_id, "label": title, "subtitle": subtitle,
            "glyph": glyph, "operator": operator, "properties": tuple(properties), "translate": translate,
            "feature": feature_id}


def _attach_feature():
    return Feature("attach", n_("Image or file…"), n_("Show the agent a reference picture or model"), glyph="attach",
                   order=0, section=ATTACH, action="popup_add_attach", owner="chat_destinations")


def _base_mesh_feature():
    from . import chosen_tools
    item = chosen_tools.tool("base_mesh")
    return Feature("base_mesh", n_("Base meshes"), item.why, glyph="base_mesh", order=40, section=START_FROM,
                   children=chosen_tools.base_mesh_rows, lead_only=True, owner="chosen_tools")


def _hands_feature():
    from . import chosen_tools
    item = chosen_tools.tool("hands")
    return Feature("hands", item.title, item.why, glyph="base_mesh", order=45, action="popup_add_choose",
                   lead_only=True, owner="chosen_tools")


def register_builtins():
    """The features this module and ``chosen_tools`` own (Blender-free); the rest register from their modules."""
    register(_attach_feature())
    register(_base_mesh_feature())
    register(_hands_feature())


register_builtins()
