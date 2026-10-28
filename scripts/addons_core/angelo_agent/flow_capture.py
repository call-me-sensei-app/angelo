# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scene Capture steps: free, local renders that become library images generators reference. Main thread.

An unchanged scene keeps its previous capture (same asset) for ``CAPTURE_REUSE_SECONDS``, so downstream
steps stay reusable; any edit outside the flow's own node trees invalidates it (``_scene_updated``).
"""

import hashlib
import time

import bpy

from . import flow_nodes, flow_tree
from .flow_spec import GENERATOR_TYPES, FlowError
from .flow_steps import slot_get, slot_set, step_slot

CAPTURE_REUSE_SECONDS = 900
_scene_changes = 0
_captures = {}     # (tree name, node name) -> (scene change count, monotonic time)


def _scene_updated(scene, depsgraph):
    """Count edits that can change a capture; this flow's own node edits don't."""
    global _scene_changes
    for update in depsgraph.updates:
        if not (isinstance(update.id, bpy.types.NodeTree) and update.id.bl_idname == flow_nodes.TREE):
            _scene_changes += 1
            return


def capture_inputs(tree, window, names):
    """Capture the named Scene Capture nodes into the library, reusing unchanged captures.

    A capture is a free, local render; it becomes an imported Studio image so
    its pixels are what generators reference. An unchanged scene keeps the
    previous capture (same asset), so downstream steps stay reusable.
    """
    from . import scene_queries, studio, view_capture
    from .flow_host import evaluate_tree, library
    store = library()
    if names:
        # Evaluate pending edits now so their update handlers count them before
        # a capture is reused; an edit made in this same tick must invalidate it.
        window.view_layer.update()
    flow = evaluate_tree(tree)[0] if names else None
    for name in names:
        slot = step_slot(tree, flow, name)
        node = slot[0] if slot else None
        if node is None or node.bl_idname != flow_nodes.ANGELO_FlowSceneCaptureNode.bl_idname:
            continue
        stamp, previous = _captures.get((tree.name, name)), slot_get(slot, "capture_asset")
        if (previous and previous in store.assets and stamp
                and stamp[0] == _scene_changes and time.monotonic() - stamp[1] < CAPTURE_REUSE_SECONDS):
            continue
        targets = []
        if node.subject == "SELECTION":
            targets = [scene_queries.object_handle(o) for o in window.view_layer.objects.selected][:16]
            if not targets:
                raise FlowError(f"'{name}' captures the selection; select the objects to capture first")
        if node.framing == "CAMERA" and window.scene.camera is None:
            raise FlowError(f"'{name}' uses the scene camera, and this scene has none; add one or use Current view")
        result = view_capture.execute(window, {"targets": targets, "resolution": int(node.resolution),
                                               "views": ["SCENE_CAMERA" if node.framing == "CAMERA" else "FIT"],
                                               "shading": "SOLID" if node.shading == "CLAY" else "MATERIAL"},
                                      studio._references)
        view = result["views"][0]
        try:
            with open(view["path"], "rb") as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()
            if digest != slot_get(slot, "capture_hash") or previous not in store.assets:
                asset = store.import_image(view["path"])
                asset.update(prompt=f"Scene capture · {tree.name} · {name}",
                             lineage={"source": "capture", "flow_id": flow_tree.flow_id(tree), "node": name})
                store.update(asset)
                slot_set(slot, "capture_asset", asset["id"])
                slot_set(slot, "capture_hash", digest)
        finally:
            studio._references.discard_observation(view)
        _captures[(tree.name, name)] = (_scene_changes, time.monotonic())


def needed_captures(flow, scope_nodes):
    """Scene Capture nodes feeding any generator in scope."""
    from .flow_spec import ancestors
    nodes = {n["id"]: n for n in flow["nodes"]}
    needed = set()
    for identity in scope_nodes:
        if nodes[identity]["type"] in GENERATOR_TYPES:
            needed |= {a for a in ancestors(flow, identity) | {identity} if nodes[a]["type"] == "SCENE_CAPTURE"}
    return sorted(needed)


def clear():
    _captures.clear()


def register():
    bpy.app.handlers.depsgraph_update_post.append(_scene_updated)


def unregister():
    if _scene_updated in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_scene_updated)
