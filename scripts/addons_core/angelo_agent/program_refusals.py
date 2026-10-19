# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Refusals a program gets when it is bound, before anything is staged or approved.

Some edits can only fail at commit, after the person approved them and the
whole program was staged: the live scene, not the private stage, holds the
fact that forbids them. Where the program's own text and the live scene decide
the outcome, ``native_programs.bind`` asks here first and the refusal says what
to submit instead. Commit keeps its own check as the last line.

* Deleting a scene's active camera (``object_delete``): commit refuses to
  remove an object a scene still looks through, so that no scene's camera
  changes outside what was approved. It is allowed when the same program
  gives the bound scene another camera, or none (``property_set`` on the
  scene with ``camera``), wherever in the program that is: commit writes
  scene settings before it removes objects. A program that sets no camera of
  its own gets that ``property_set`` from the host before it is bound
  (``release_camera``, through ``program_camera``), so the approval shows it
  and Undo restores it; this check refuses only what that leaves: a camera
  another scene uses (this program cannot release it), a program that makes
  the camera it deletes the scene's, and a program bound without the host's
  completion (an executor that cannot read the camera, or no room for it).

* Moving the current frame (``property_set`` of ``frame_current``,
  ``check_frame_move``): every check is measured at the frame the program
  leaves, and Blender's frame change runs after commit. A program that moves
  the frame is refused when the move would change what was approved there:
  timeline markers that bind cameras (the frame change makes their camera the
  scene's, after the checks and outside the program), and an existing
  animated object the program also writes without keys (its animation decides
  those values at the new frame). Either would otherwise change an approved
  result after its checks. Objects the program creates and keys stay admitted:
  their checks sample the keys' own frames, and GPT-6.1 Sol's bridge programs
  key a car and move to a frame of its drive in one program. The fix named is
  the same: write the frame or that frame setting in a program of its own.

``check_deletions`` and ``check_frame_move`` are main thread only; they read
live data and change nothing. ``releases`` and ``release_camera`` are pure.
"""

import copy

from .program_jobs import ProgramError


def _last_camera(program, scene_handle):
    """``(set, value)``: whether the program sets the bound scene's camera, and to what (the last write wins)."""
    found = (False, None)
    for operation in program["operations"]:
        values = operation["parameters"].get("values") if operation["kind"] == "property_set" else None
        if operation["target"] == scene_handle and isinstance(values, dict) and "camera" in values:
            found = (True, values["camera"])
    return found


def check_deletions(scene, scene_handle, originals, program):
    """Raise ProgramError for an ``object_delete`` of a camera a scene would still look through after commit, or of an
    object a fake user's datablock points at (commit refuses that, ``_unlink_deleted``, and no kind can change it)."""
    import bpy
    _check_fake_holders(bpy, [originals.get(operation["target"]) for operation in program["operations"]
                              if operation["kind"] == "object_delete"])
    for operation in program["operations"]:
        obj = originals.get(operation["target"]) if operation["kind"] == "object_delete" else None
        if obj is None:
            continue
        others = [item.name for item in bpy.data.scenes if item.camera == obj and item != scene]
        if others:
            raise ProgramError("object_delete ACTIVE_CAMERA: %r is the active camera of the scene %r, which this "
                               "program cannot change; give that scene another camera first"
                               % (obj.name[:64], others[0][:64]), code="UNSUPPORTED")
        if scene.camera != obj:
            continue
        replaced, camera = _last_camera(program, scene_handle)
        if not replaced or camera == operation["target"]:
            raise ProgramError("object_delete ACTIVE_CAMERA: %r is the scene's active camera. In the same program "
                               "make another camera active (property_set on the scene handle with values "
                               "{\"camera\": a camera's handle or $alias}) or clear it ({\"camera\": null}), then "
                               "delete it" % obj.name[:64], code="VALIDATION")


def _check_fake_holders(bpy, doomed):
    """Before approval, what commit refused after it (earlier, never more): a
    datablock kept by its fake user that points at a deleted object. Every kind that edits such data (a material,
    World, node group, particle settings or camera data) gives its users an edited copy and the kept original still
    points at the object, so commit's in-use check always fails; an object or other used data the program may still
    change is left to commit."""
    doomed = [obj for obj in doomed if obj is not None]
    users = bpy.data.user_map(subset=doomed) if doomed else {}
    for obj in doomed:
        keeper = next((user for user in users.get(obj, ()) if getattr(user, "use_fake_user", False)
                       and not isinstance(user, (bpy.types.Object, bpy.types.Collection, bpy.types.Scene))), None)
        if keeper is not None:
            raise ProgramError("object_delete OBJECT_IN_USE: %s %r, kept by its fake user, points at %r, and no "
                               "program kind can change that; remove the reference or the fake user first (a "
                               "reviewed script), or keep the object" % (keeper.bl_rna.name, keeper.name[:48],
                                                                        obj.name[:64]), code="UNSUPPORTED")


def releases(program, camera_handle):
    """Whether ``program`` deletes the scene's camera ``camera_handle`` and sets no scene camera itself."""
    operations = program["operations"]
    return (any(operation["kind"] == "object_delete" and operation["target"] == camera_handle for operation in operations)
            and not any(operation["kind"] == "property_set" and "camera" in (operation["parameters"].get("values") or {})
                        for operation in operations))


def release_camera(program, scene_handle, revision, camera_handle):
    """``program`` (canonical) with the bound scene's camera cleared after it when it deletes that camera.

    Added as the last operation, ``property_set`` of ``camera`` null on the
    scene, with the scene in the write set: bound to the revision the program
    already declares for it, else to ``revision`` (the live one). Unchanged
    when ``releases`` is false or there is no room (32 operations, 64 inputs):
    binding then refuses with the remedy. A new dictionary.
    """
    from .program_spec import MAX_INPUT_OBJECTS, MAX_OPERATIONS, validate_program
    if not releases(program, camera_handle) or len(program["operations"]) >= MAX_OPERATIONS:
        return program
    released = copy.deepcopy(program)
    declared = {entry["handle"]: entry["revision"] for key in ("read_set", "write_set") for entry in released[key]}
    if scene_handle not in declared and len(declared) >= MAX_INPUT_OBJECTS:
        return program
    released["read_set"] = [entry for entry in released["read_set"] if entry["handle"] != scene_handle]
    if not any(entry["handle"] == scene_handle for entry in released["write_set"]):
        released["write_set"].append({"handle": scene_handle, "revision": declared.get(scene_handle, revision)})
    released["operations"].append({"kind": "property_set", "target": scene_handle,
                                   "parameters": {"values": {"camera": None}}})
    try:
        return validate_program(released)
    except ValueError:
        return program  # Binding refuses with the remedy, as for a program without the host's completion.


FRAME_FIX = ("write the frame or that frame setting in a program of its own (a render job or a view takes its own "
             "frame)")


def check_frame_move(scene, originals, program):
    """Raise ProgramError (VALIDATION) for a frame move that would change what the program approved."""
    from .program_frames import FRAME_PATHS, animated_path, frame_updates, unkeyed_writes
    from .scene_binding import scene_only
    written = frame_updates(program)
    if not written:
        return
    operations = program["operations"]
    bound = [marker.name for marker in scene.timeline_markers if marker.camera is not None]
    if bound or any(operation["kind"] == "marker_edit" and operation["parameters"].get("camera") is not None
                    for operation in operations):
        raise ProgramError("property_set FRAME_CAMERA_MARKERS: %s bind cameras to the timeline, and this program "
                           "writes %s, after which Blender's frame update can make one of them the scene camera after "
                           "this program's checks; %s"
                           % (("the markers " + ", ".join(repr(name[:64]) for name in bound[:3])) if bound
                              else "this program's markers", written[0], FRAME_FIX), code="VALIDATION")
    # Only what a key could hold: a rename, a material or a key deletion of an animated object stays admitted;
    # objects the program creates and keys are checked at their keys' frames.
    modes = {}  # (reference, bone) -> the rotation mode an earlier bone_set of this program left.

    def current_mode(reference, bone):  # A bone's rotation mode at that operation (action_bones_spec.pose_paths).
        if (reference, bone) in modes:
            return modes[reference, bone]
        pose = getattr(originals.get(reference), "pose", None)
        return getattr(pose.bones.get(bone), "rotation_mode", None) if pose is not None else None

    for index, operation in enumerate(operations):
        if operation["kind"] == "bone_set" and operation["parameters"]["rotation_mode"] is not None:
            after = operation["parameters"]["rotation_mode"]
        else:
            after = None
        for reference, path in unkeyed_writes(operation, current_mode):
            block = scene if scene_only(operation) else originals.get(reference)
            if block is not None and path not in FRAME_PATHS and animated_path(block, path):
                raise ProgramError("property_set FRAME_ANIMATED: operations[%d] (%s) sets %s of %r without a key, and "
                                   "an F-curve or driver decides that value when Blender evaluates the frame after "
                                   "this program's %s, so the commit would not "
                                   "keep what was checked; key it (property_keyframe or keyframe_insert), or %s"
                                   % (index, operation["kind"], path[:96], block.name[:64], written[0], FRAME_FIX),
                                   code="VALIDATION")
        if after is not None:
            modes[operation["target"], operation["parameters"]["bone"]] = after
