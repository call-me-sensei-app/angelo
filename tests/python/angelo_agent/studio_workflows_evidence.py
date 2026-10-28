# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The swap-and-hide evidence step of test_blender_studio_workflows (its held-out evidence runs). Runs in Blender."""

import json
import os
from pathlib import Path

import bpy
from mathutils import Vector


def store_fit(tree, shown, checks):
    """Store ANGELO_QA_GROUNDING with the run's split as Fit to My Image stores its result; Place then uses it."""
    from angelo_agent import flow_host, flow_templates
    from angelo_agent.flow_split_host import store_grounding
    fit = json.loads(Path(os.environ["ANGELO_QA_GROUNDING"]).read_text())
    stored = store_grounding(tree, flow_templates.SPLIT_STEP, shown["split"]["key"], fit)
    entry = flow_host.refresh(tree)["states"][flow_templates.CHARACTER_STEP]
    checks["a_fit_reaches_place"] = stored and entry["value"].get("grounding") == fit
    return fit


def swap_and_hide(meshes, window):
    """Hide one placed part and swap another's model in place (a stand-in of the same size), and show both in the
    Outliner. True when the other parts are untouched."""
    by_part = {o.get("angelo_character_part"): o for o in meshes}
    hidden = next((by_part[p] for p in ("outer", "inner", "hair") if p in by_part), None)
    swapped = next((by_part[p] for p in ("footwear", "lower", "hair") if p in by_part and by_part[p] is not hidden), None)
    if hidden is None or swapped is None:
        return False
    others = {o.name: (o.data.name, o.hide_get()) for o in meshes if o not in (hidden, swapped)}
    hidden.hide_set(True)
    corners = [swapped.matrix_world @ v.co for v in swapped.data.vertices]
    lo = [min(c[i] for c in corners) for i in range(3)]
    hi = [max(c[i] for c in corners) for i in range(3)]
    stand_in = bpy.data.meshes.new("QA swapped model")
    inverse = swapped.matrix_world.inverted()
    box = [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    stand_in.from_pydata([inverse @ Vector(c) for c in box], [],
                         [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)])
    old = swapped.data
    for modifier in list(swapped.modifiers):   # The old model's masks name its own vertices.
        swapped.modifiers.remove(modifier)
    swapped.data = stand_in
    for area in window.screen.areas:
        if area.type == 'OUTLINER':
            for obj in bpy.context.view_layer.objects:
                obj.select_set(obj in (hidden, swapped))
            bpy.context.view_layer.objects.active = swapped
            region = next(r for r in area.regions if r.type == 'WINDOW')
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.outliner.show_active()
    return (hidden.hide_get() and swapped.data is stand_in and old.users == 0 and
            all((o.data.name, o.hide_get()) == others[o.name] for o in meshes if o.name in others))
