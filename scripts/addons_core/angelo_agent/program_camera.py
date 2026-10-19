# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a scene program needs from the live scene camera, fixed once before it is bound.

Two things a program says depend on the bound scene's camera as it is when the
program is admitted, and the controller fixes both at the call's first
precondition, on the main thread (``staged_program``, which reads the scene
through ``BlenderExecutor.host_program``), as it expands a recipe:

* orbit placements become the locations they name (``program_orbits.resolve``);
* a program that deletes the scene's active camera and sets no scene camera of
  its own also clears it (``program_refusals.release_camera``), as Blender's
  own delete does. Commit refuses to remove an object a scene still looks
  through, so such a program used to be refused when bound: nine of the ten
  recorded GPT-6.1 Sol bridge and tree one-shots that cleared the default scene
  with a typed program lost a round that way.

The result is the call's ``program_arguments``: the approval shows it (the
added ``property_set`` that clears the camera included), staging runs it, the
transcript records it beside what the model wrote and the committed result
reports the scene's camera among its changes. Later preconditions bind that
program again and never read anew, so the approval and the stage agree even
when the camera changes after it, and commit refuses a program whose measured
camera then stands elsewhere (``camera_guards``); a program that needed no change is bound as
it was written, and binding still refuses deleting a camera the scene then
looks through (``program_refusals.check_deletions``).
"""

from .program_orbits import has_orbit
from .refusal_codes import RefusalError


def needs_camera(program):
    """Whether a program's text depends on the live scene camera: an orbit, or a deletion that may be it."""
    operations = program.get("operations") if isinstance(program, dict) else None
    return has_orbit(program) or any(isinstance(operation, dict) and operation.get("kind") == "object_delete"
                                     for operation in operations or ())


def staged_program(call, executor, emit):
    """The arguments a call's precondition binds: its own, or the scene program the host fixed for it.

    A frame rebind (``controller._rebind_once``) and a recipe expansion record
    that program as ``program_arguments``; a scene program that reads the
    camera gets it here, at its first precondition: the approval shows it,
    staging runs it, and the transcript records it beside what the model wrote.
    """
    if (call.name == "angelo_scene_program" and call.program_arguments is None
            and getattr(call, "precondition", None) is None and needs_camera(call.arguments)):
        read = getattr(executor, "host_program", None)
        if not callable(read):
            if has_orbit(call.arguments):
                raise RefusalError("This host cannot resolve orbit placements; give location", code="UNSUPPORTED")
            return call.arguments  # Binding refuses deleting the scene's camera with its remedy.
        used = []
        program = read(call.arguments, used)
        # An orbit measured from a camera that moves with time holds only at this frame (controller._frame_rebind).
        call.orbit_moves_with_time = any(seen.get("moves_with_time") for seen in used)
        if program != call.arguments:
            call.program_arguments = program
            emit("activity", {"id": call.id, "arguments": dict(call.arguments, program=call.program_arguments)})
    return call.program_arguments if call.program_arguments is not None else call.arguments


def host_program(window, arguments, used=None):
    """``arguments`` (a canonical scene program) as the host fixes it from ``window``'s scene camera.

    Main thread; brings the view layer's evaluation up to date (an orbit is measured from what the scene shows, as
    commit reads it again), then reads the camera's handle, world position, what moves it and
    whether its placement moves with time (``program_frames.placement_moves_with_time``), and,
    only when the program deletes it, the scene's handle and settings
    revision. Changes nothing. ``used`` receives the cameras orbits were
    measured from (``program_orbits.resolve``).
    """
    from . import camera_guards, program_orbits, program_refusals
    from .scene_queries import object_handle
    from .scene_settings import scene_handle
    scene = window.scene
    camera = scene.camera
    handle = None if camera is None else object_handle(camera)

    view_layer = getattr(window, "view_layer", None)
    if has_orbit(arguments) and callable(getattr(view_layer, "update", None)):
        view_layer.update()  # Measure what the scene shows now, never a stale evaluation.
    depsgraph = getattr(view_layer, "depsgraph", None)

    def seen(obj, reference):
        follows, moves = program_orbits.camera_reads(obj, object_handle, scene_handle_of=scene_handle)
        # Read as commit reads it again (camera_guards): evaluated in the bound view layer's depsgraph.
        return {"handle": reference, "location": camera_guards.location(obj, depsgraph),
                "follows": follows, "moves_with_time": moves}  # One walk for both (object_dependencies).

    program = arguments
    if has_orbit(program):
        program = program_orbits.resolve(program, None if camera is None else seen(camera, handle),
                                         _existing_cameras(window, program, handle, seen), used)
    if handle is not None and program_refusals.releases(program, handle):
        from .scene_settings import settings_revision
        program = program_refusals.release_camera(program, scene_handle(scene), settings_revision(scene), handle)
    return program


def _existing_cameras(window, program, handle, seen):
    """What ``program_orbits.resolve`` reads of the existing objects a program makes the scene camera, by handle.

    One a handle no longer resolves to is left out: an orbit measured from it is refused with its reason.
    """
    from .scene_queries import resolve_object
    found = {}
    for operation in program["operations"]:
        values = operation["parameters"].get("values") if operation["kind"] == "property_set" else None
        reference = values.get("camera") if isinstance(values, dict) else None
        if isinstance(reference, str) and not reference.startswith("$") and reference not in (handle, *found):
            try:
                found[reference] = seen(resolve_object(window, reference), reference)
            except ValueError:
                continue
    return found
