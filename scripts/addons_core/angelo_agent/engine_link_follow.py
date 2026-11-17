# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The scene the engine live link follows, and when it changed (``engine_link``; doc/angelo/engine_link.rst). Main
thread.

``engine_link`` re-exports every name here, keeps the state and binds itself as ``link`` when it loads: each function
reads the link's ``_state`` and its own helpers through it, so a change made there (a test's stand-in ``_window``) holds
here too. A change reaches the link in each way ``scene_changes`` names (``listen``): ``_on_depsgraph`` is watched for an
edit's evaluation and a frame change's, ``_on_edit`` hears the executor's ``scene_changes.edited``.
"""

import threading
import time

import bpy

from . import engine_link_assets as assets, gltf_scene_reads, live_windows, scene_changes, web_link_live


link = None   # The engine_link module (it binds itself).


def listen():
    """Hear every way a change of the followed scene arrives (while the link is on: ``engine_link._listen``)."""
    scene_changes.watch(_on_depsgraph)
    scene_changes.on_edit(_on_edit)


def unlisten():
    scene_changes.unwatch(_on_depsgraph)
    scene_changes.off_edit(_on_edit)


def _window():
    wm = bpy.context.window_manager
    return live_windows.find(wm, link._state["window"], live=False) or live_windows.first(wm, temporary=False)


def _followed(window):
    """(scene, view layer) the link exports: the window's, or the context's in a session without windows."""
    if window is not None:
        return window.scene, window.view_layer
    if bpy.app.background:
        return bpy.context.scene, bpy.context.view_layer
    return None, None


def scene_key(scene, view_layer):
    """What the live export reads that an update of the scene alone can change: the shared key
    (``scene_changes.scene_key``) and what the glTF exporter reads from the scene with the linked engine's live
    options (``gltf_scene_reads``: its name, the camera aspect, the engine for lights, the extras Godot asks for)."""
    key = scene_changes.scene_key(scene, view_layer)
    found = link._state["session"]
    if found is None or scene is None:
        return key
    options = assets.adapter(found["asset"].engine).live_options(found["params"])
    return (key, gltf_scene_reads.settings(scene, view_layer, options))


def _on_edit(scene):
    """An edit the executor reports (``scene_changes.edited``), which no depsgraph update may show."""
    state = link._state
    followed, _view_layer = link._followed(link._window())
    if state["session"] is None or state["exporting"] or followed is None \
            or scene.session_uid != followed.session_uid:
        return
    if state["dirty_at"] is None:
        state["dirty_at"] = time.monotonic()


@bpy.app.handlers.persistent
def _on_depsgraph(scene, depsgraph):   # depsgraph_update_post and frame_change_post (scene_changes.watch).
    state = link._state
    if state["session"] is None or state["exporting"] or threading.current_thread() is not threading.main_thread():
        return
    followed, view_layer = link._followed(link._window())
    if followed is None or scene.session_uid != followed.session_uid:
        return
    changed, state["scene"] = scene_changes.matters(depsgraph, scene, view_layer, state["scene"], key=link.scene_key)
    if changed:
        if state["dirty_at"] is None:   # The oldest unsent change: later ones go in the same frame.
            state["dirty_at"] = time.monotonic()
        if state["session"]["asset"].engine == "web":   # The web's frames hold only what changed.
            web_link_live.note(depsgraph)
