# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared by the GUI suites of angelo_view's reference comparisons (test_blender_view_outline*.py), in the app.

``decode`` reads the encoders' own PNGs without a Blender Image; ``Wrapped`` makes every angelo_view call inside
a full snapshot of the document, the window's viewports and the data-block inventory, a depsgraph-update counter
and the project's own SceneCheck, recording any change; ``attach`` adds an image to the conversation.
``Wrapped.view`` is a generator (a pending look is polled through the event loop): a suite's step runs it with
``yield from`` and its timer advances the step a tick at a time.
"""

from pathlib import Path
import struct
import zlib

import bpy
import numpy as np

import rendered_view_fixtures as fx


def decode(path):
    """RGB rows of the encoders' own filter-0 PNGs (no Blender Image needed)."""
    data, offset, idat, size, channels = Path(path).read_bytes(), 8, b"", None, 3
    while offset < len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind, body = data[offset + 4:offset + 8], data[offset + 8:offset + 8 + length]
        if kind == b"IHDR":
            size, channels = struct.unpack(">II", body[:8]), {2: 3, 6: 4}[body[9]]
        elif kind == b"IDAT":
            idat += body
        offset += 12 + length
    width, height = size
    raw = np.frombuffer(zlib.decompress(idat), dtype=np.uint8).reshape(height, width * channels + 1)
    assert not raw[:, 0].any(), "unexpected PNG filter"
    return raw[:, 1:].reshape(height, width, channels)[:, :, :3].astype(np.int16)


def _plain(value):
    if isinstance(value, (str, bool, int, float)) or value is None:
        return value
    if isinstance(value, bpy.types.ID):
        return "ID:" + value.name
    try:
        return [_plain(item) for item in value]
    except TypeError:
        return repr(value)


def _rna(owner):
    values = {}
    for prop in owner.bl_rna.properties:
        if prop.identifier != "rna_type" and prop.type not in ("POINTER", "COLLECTION"):
            try:
                values[prop.identifier] = _plain(getattr(owner, prop.identifier))
            except Exception as error:  # Context-only properties: recorded as unreadable.
                values[prop.identifier] = "unreadable: " + type(error).__name__
    return values


def snapshot(window):
    scene, layer = window.scene, window.view_layer
    data = {prop.identifier: sorted((item.name, item.session_uid) for item in getattr(bpy.data, prop.identifier))
            for prop in bpy.data.bl_rna.properties
            if prop.type == "COLLECTION" and prop.identifier not in ("window_managers", "workspaces", "screens")}
    objects = {obj.name: (_plain(obj.matrix_world), obj.hide_viewport, obj.hide_get(view_layer=layer),
                          obj.select_get(view_layer=layer), obj.mode) for obj in scene.objects}
    views = []
    for area in window.screen.areas:
        if area.type == "VIEW_3D":
            space = area.spaces.active
            views.append((_rna(space), _rna(space.shading), _rna(space.overlay), _rna(space.region_3d)))
    return {"data": data, "objects": objects, "views": views, "scene": _rna(scene), "display": _rna(scene.display),
            "render": _rna(scene.render), "frame": scene.frame_current, "active": _plain(layer.objects.active),
            "mode": bpy.context.mode, "dirty": bpy.data.is_dirty}


class Wrapped:
    """angelo_view calls, each checked to leave the document, the viewports and the data unchanged."""

    def __init__(self, window, executor):
        from angelo_agent import view_scene_check
        self.window, self.executor, self.check = window, executor, view_scene_check
        self.calls, self.updates = [], 0
        bpy.app.handlers.depsgraph_update_post.append(self._count)

    def _count(self, _scene, _depsgraph):
        self.updates += 1

    def close(self):
        if self._count in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.remove(self._count)

    def view(self, args, *, label):
        """Generator: one call, wrapped. A look whose shaders are still compiling is pending (``view_warm``): it is
        polled through the event loop, as ``Controller._pump_events`` does, and the wrap covers the wait too."""
        before, updates = snapshot(self.window), self.updates
        scene_check = self.check.SceneCheck(self.window, deep=True)
        try:
            result = self.executor.execute("angelo_view", args)
            if not isinstance(result, dict):
                result = yield from fx.pending_result(result)
        finally:
            after, found = snapshot(self.window), scene_check.finish()
            self.calls.append({"call": label, "changed": sorted(k for k in before if before[k] != after[k]),
                               "scene_check": found["changes"], "depsgraph_updates": self.updates - updates})
        assert result.get("success"), result
        return result

    def changed(self):
        return [call for call in self.calls if call["changed"] or call["scene_check"] or call["depsgraph_updates"]]


def attach(references, folder, name, rows):
    """An RGBA picture (rows top first) attached to the conversation: its reference_id."""
    from angelo_agent.view_pixels import encode_rgba_png
    source = Path(folder) / "sources" / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(encode_rgba_png(np.ascontiguousarray(rows, dtype=np.uint8)))
    return references.import_images([str(source)])[0]["id"]
