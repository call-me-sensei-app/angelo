# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Timeline markers of the bound scene: stage copy, ``marker_edit`` kernel, revision rows and commit.

Markers are scene state with no object revision, so they join the scene's
settings revision (``revision_rows``: name, frame, selection and bound camera
of every marker, in order); a manual marker edit after observation or during
a preview fails the program closed. Staging copies the live markers onto the
private stage scene; a marker's camera becomes the camera's staged copy, which
requires the camera to be declared: a live marker bound to an undeclared
camera refuses every ``marker_edit`` (``MARKER_CAMERA_UNDECLARED``) instead
of dropping that binding. Markers are addressed by exact name; a name used
twice is ambiguous and refused. New markers are created unselected.

Commit (``transfer``) replaces the live marker list with the staged one, each
camera mapped back to its live object, and keeps the previous list to restore
on rollback. A marker has no identity anything else points at, so rebuilding
the list changes nothing but its contents.
"""

from .anim_channels import AnimationError

MAX_MARKERS = 1024
UNDECLARED = "marker_undeclared_cameras"


def revision_rows(scene):
    """Every marker of ``scene`` as [name, frame, selected, camera session UID or None], in order."""
    return [[marker.name, int(marker.frame), bool(marker.select),
             int(marker.camera.session_uid) if marker.camera is not None else None]
            for marker in scene.timeline_markers]


def summary(scene, limit=64):
    """Read-only rows for ``angelo_scene_settings``: names, frames and bound camera handles."""
    from . import scene_queries
    from .scene_read_values import label
    return [{"name": label(marker.name), "frame": int(marker.frame),
             "camera": scene_queries.object_handle(marker.camera) if marker.camera is not None else None}
            for marker in list(scene.timeline_markers)[:limit]]


def stage_markers(stage, scene):
    """Copy the live markers onto the stage scene, cameras mapped to their staged copies."""
    staged = {original.as_pointer(): stage.objects[handle] for handle, original in stage.originals.items()
              if handle in stage.objects}
    undeclared = 0
    if len(scene.timeline_markers) > MAX_MARKERS:
        raise AnimationError("TOO_MANY_MARKERS", "at most %d timeline markers" % MAX_MARKERS)
    for marker in scene.timeline_markers:
        copy = stage.scene.timeline_markers.new(marker.name, frame=marker.frame)
        copy.select = marker.select
        if marker.camera is not None:
            copy.camera = staged.get(marker.camera.as_pointer())
            undeclared += copy.camera is None
    stage.extras[UNDECLARED] = undeclared


def _marker(scene, name):
    found = [marker for marker in scene.timeline_markers if marker.name == name]
    if not found:
        names = ", ".join(repr(marker.name[:64]) for marker in list(scene.timeline_markers)[:16])
        raise AnimationError("MARKER_NOT_FOUND", "no timeline marker is named %r; markers: %s" % (name[:64], names or "none"))
    if len(found) > 1:
        raise AnimationError("MARKER_AMBIGUOUS", "%d markers are named %r; rename one first" % (len(found), name[:64]))
    return found[0]


def _camera(context, reference):
    if reference is None:
        return None
    camera = context.resolve(reference)
    if camera.type != "CAMERA":
        raise AnimationError("NOT_A_CAMERA", "a marker binds a camera object")
    return camera


def _free_name(scene, name):
    if any(marker.name == name for marker in scene.timeline_markers):
        raise AnimationError("NAME_TAKEN", "a timeline marker is already named %r" % name[:64])


def apply(context, operation):
    """marker_edit on the stage scene (the bound scene's private stand-in)."""
    if operation["target"] not in getattr(context, "scene_handles", ()):
        raise AnimationError("OWNER_TYPE_MISMATCH", "marker_edit needs the bound scene handle as target")
    if context.extras.get(UNDECLARED):
        raise AnimationError("MARKER_CAMERA_UNDECLARED", "%d marker(s) bind a camera this program did not declare; add "
                             "those cameras to the read set" % context.extras[UNDECLARED])
    params, scene = operation["parameters"], context.scene
    kind = params["operation"]
    if kind == "ADD":
        _free_name(scene, params["name"])
        if len(scene.timeline_markers) >= MAX_MARKERS:
            raise AnimationError("TOO_MANY_MARKERS", "at most %d timeline markers" % MAX_MARKERS)
        marker = scene.timeline_markers.new(params["name"], frame=params["frame"])
        marker.select = False
        marker.camera = _camera(context, params["camera"])
    elif kind == "REMOVE":
        scene.timeline_markers.remove(_marker(scene, params["marker"]))
    elif kind == "SET":
        marker = _marker(scene, params["marker"])
        if params["name"] is not None and params["name"] != marker.name:
            _free_name(scene, params["name"])
            marker.name = params["name"]
        if params["frame"] is not None:
            marker.frame = params["frame"]
    else:
        _marker(scene, params["marker"]).camera = _camera(context, params["camera"])
    return {"kind": operation["kind"], "target": operation["target"], "operation": kind,
            "markers": [[marker.name[:64], int(marker.frame)] for marker in list(scene.timeline_markers)[:32]],
            "marker_count": len(scene.timeline_markers)}


def _rebuild(scene, rows):
    scene.timeline_markers.clear()
    for name, frame, select, camera in rows:
        marker = scene.timeline_markers.new(name, frame=frame)
        marker.select, marker.camera = select, camera


def transfer(stage, scene, source_map, undo_journal):
    """Replace the live markers with the staged ones (cameras mapped live), restorable on rollback."""
    from .program_jobs import ProgramError
    previous = [(marker.name, marker.frame, marker.select, marker.camera) for marker in scene.timeline_markers]
    wanted = []
    for marker in stage.scene.timeline_markers:
        camera = marker.camera
        live = source_map.get(camera.as_pointer()) if camera is not None else None
        if camera is not None and live is None:
            raise ProgramError("A staged marker binds a camera outside the program", code='INTERNAL_ERROR')
        wanted.append((marker.name, marker.frame, marker.select, live))
    undo_journal.append(lambda: _rebuild(scene, previous))
    _rebuild(scene, wanted)
    actual = [(marker.name, marker.frame, marker.select, marker.camera) for marker in scene.timeline_markers]
    if actual != wanted:
        raise ProgramError("The committed timeline markers do not match their staged result", code='INTERNAL_ERROR')
