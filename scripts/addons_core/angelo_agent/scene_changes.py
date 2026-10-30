# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""When a followed scene changed in a way an export can see, and when re-exporting it is allowed. Main thread.

Shared by the live previews that re-export a scene after each change (``web_preview``, ``engine_link``): a
``depsgraph_update_post`` handler asks ``matters``; a timer re-exports once the scene has been still for
``quiet_seconds`` and nothing is ``busy``.
"""

import bpy

SCENE_FIELDS = ("frame_current", "frame_start", "frame_end", "world", "camera")


def scene_key(scene, view_layer):
    """What a flagless update can change that an export reads: scene settings and which objects show, by name."""
    shown = tuple(obj.name for obj in view_layer.objects if obj.visible_get(view_layer=view_layer))
    return tuple(getattr(scene, name, None) for name in SCENE_FIELDS) + (scene.session_uid, hash(shown))


def content(update):
    """An update an export can see: any data but the scene itself, or an object's geometry, transform or shading.
    A selection click updates the scene and its objects with none of those flags."""
    if isinstance(update.id, bpy.types.Scene):
        return False
    if isinstance(update.id, bpy.types.Object):
        return update.is_updated_geometry or update.is_updated_transform or update.is_updated_shading
    return True


def matters(depsgraph, scene, view_layer, last_key):
    """(changed, key): whether ``depsgraph``'s updates change what an export of ``scene`` writes, and the scene key
    to remember (hiding, renaming or a setting moved count; a selection does not)."""
    if any(content(update) for update in depsgraph.updates):
        return True, last_key
    key = scene_key(scene, view_layer)
    return key != last_key, key


def busy(window):
    """True while re-exporting would intrude: a render or bake job, playback, a modal interaction (a drag) or Edit
    Mode."""
    from . import input_policy, job_host
    manager = job_host._manager()
    if manager is not None and manager.active() is not None:
        return True     # A render or bake job reads the scene on its own thread.
    screen = window.screen
    if screen is not None and screen.is_animation_playing or input_policy.interaction_active(window):
        return True
    return editing(window)


def editing(window):
    """True while an object is in Edit Mode: its changes aren't in its mesh until the person leaves Edit Mode, and
    exporting then would switch modes (``web_export.run_exporter``). The export follows on leaving it."""
    with bpy.context.temp_override(window=window):
        return any(obj.mode == "EDIT" for obj in bpy.context.objects_in_mode or ())


def quiet_seconds(last_ms, minimum, share, maximum):
    """Stillness before a re-export: at least ``minimum`` and ``share`` times the last export, at most ``maximum``,
    so a large scene's exports never take more than about 1/(share+1) of the main thread."""
    return min(maximum, max(minimum, share * last_ms / 1000.0))
