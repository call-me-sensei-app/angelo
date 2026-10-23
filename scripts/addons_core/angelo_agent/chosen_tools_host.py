# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The person's chosen tools, performed at Send before the observation is built (main thread).

``chosen_tools`` holds the registry and the + menu; this module performs each
choice correct by construction, so the model starts from the result with no
lookup or script round:

* **base_mesh**: ``asset_start.place``, the same operation a model's typed
  create will use: the bundled asset at the 3D cursor at real size or, when
  the person asked, at the selected mesh's place and height, its objects
  marked with their origin; its main object selected and active.

A choice is all or nothing: a failure removes what it added and its outcome
says why, and the request still goes. One Undo step ("Start from a base mesh")
per request. Outcomes feed the chat row; the model sees the added objects in its
ordinary scene facts.
"""

import time

import bpy
from mathutils import Vector



def shown_name(obj):
    """How the person's UI names an object: a base mesh Angelo placed by its menu name ("Head – Stylized"), never
    its object name ("GEO-head_stylized"); any other object as the Outliner shows it."""
    from .chosen_tools import chip
    from .human_base_meshes import PROPERTY
    asset = obj.get(PROPERTY) if obj is not None else None
    if isinstance(asset, str) and asset:
        label = chip({"tool": "base_mesh", "option": asset})[0]
        if label:
            return label
    return obj.name if obj is not None else ""


def fit_target(window):
    """The selected mesh a base mesh can be fitted to, or None."""
    active = window.view_layer.objects.active
    if active is not None and active.type == "MESH" and active.select_get(view_layer=window.view_layer):
        return active
    return None


def perform(choices, window):
    """Perform every choice in order; returns (outcomes, elapsed ms per choice). Never raises for one choice."""
    outcomes, timings = [], []
    if not choices:
        return outcomes, timings
    area, region = _view3d(window)
    with bpy.context.temp_override(window=window, area=area, region=region):
        active = window.view_layer.objects.active
        if active is not None and active.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for choice in choices:
            started = time.perf_counter()
            before = _ids()
            try:
                if choice["tool"] != "base_mesh":
                    raise ValueError("Unknown tool")
                outcomes.append(_base_mesh(window, choice))
            except Exception as error:   # One choice failing never blocks the request.
                _remove_new(before)
                outcomes.append({"tool": choice["tool"], "choice": choice.get("option") or "",
                                 "done_by_angelo": False, "not_done": str(error)[:240]})
            timings.append(round((time.perf_counter() - started) * 1000, 2))
        window.view_layer.update()
        if any(outcome["done_by_angelo"] for outcome in outcomes):
            bpy.ops.ed.undo_push(message="Start from a base mesh")
    return outcomes, timings


def perform_for_request(controller, window):
    """The Send step: perform ``controller.chosen_tools`` and clear them; returns (outcomes, timings) or None.

    Call ``report`` after the message is sent, so its rows follow the person's message. It runs before the
    request checkpoint, so restoring that checkpoint keeps what was added; its own Undo step removes it.
    """
    choices = list(getattr(controller, "chosen_tools", ()) or ())
    if not choices or controller.busy:
        return None
    outcomes, timings = perform(choices, window)
    controller.chosen_tools = []
    controller.last_chosen_timings = timings
    return outcomes, timings


def report(controller, performed):
    """One chat row per choice: what was added, by the name the menu and chip show, or why it wasn't. Never an
    internal name (an asset's object or data-block name, "GEO-head_stylized") and never a timing: timings stay in
    ``controller.last_chosen_timings`` for fixtures (maintainer rule, 1.0.5)."""
    from .chosen_tools import chip
    from .ui_text import notice
    for outcome, _elapsed in zip(*(performed or ((), ()))):
        name = chip({"tool": outcome["tool"], "option": outcome["choice"]})[0]   # As the menu and chip name it.
        if outcome["done_by_angelo"]:
            row = notice("Added {choice} to start from", choice=name)
        else:
            row = notice("Couldn't add {choice}: {reason}", choice=name or outcome["tool"],
                         reason=outcome["not_done"])
        controller.emit("message", {"role": "system", **row})


def _base_mesh(window, choice):
    from . import asset_start
    scene, layer = window.scene, window.view_layer
    target = fit_target(window) if choice.get("fit_selected") else None
    fit_box = None
    if target is not None:
        corners = [target.matrix_world @ Vector(corner) for corner in target.bound_box]
        fit_box = ([min(c[i] for c in corners) for i in range(3)], [max(c[i] for c in corners) for i in range(3)])
    _, main, facts = asset_start.place(choice["option"], _SceneSink(scene), location=scene.cursor.location,
                                             fit_box=fit_box)
    layer.update()
    for obj in layer.objects.selected:
        obj.select_set(False)
    main.select_set(True)
    layer.objects.active = main
    facts["placed"]["at"] = ("the size and place of " + target.name) if target is not None else "3D cursor"
    return dict({"tool": "base_mesh", "choice": facts.pop("asset"), "done_by_angelo": True}, **facts)


class _SceneSink:
    """``asset_start`` sink for the host step: objects go to the scene collection; rollback is ``_remove_new``."""

    def __init__(self, scene):
        self.scene = scene

    def track(self, block):
        return block

    def link(self, obj):
        self.scene.collection.objects.link(obj)


def _view3d(window):
    area = next((a for a in window.screen.areas if a.type == 'VIEW_3D'), None)
    if area is None:
        raise RuntimeError("Keep a 3D Viewport open so Angelo can add what you chose")
    return area, next(r for r in area.regions if r.type == 'WINDOW')


_DATA = ("objects", "collections", "meshes", "materials", "node_groups", "images", "shape_keys")


def _ids():
    return {key: set(getattr(bpy.data, key)) for key in _DATA}


def _remove_new(before):
    """Remove what a failed choice added (objects first, then their data)."""
    for key in _DATA:
        collection = getattr(bpy.data, key)
        for item in [item for item in collection if item not in before[key]]:
            try:
                collection.remove(item)
            except (ReferenceError, RuntimeError):
                pass
