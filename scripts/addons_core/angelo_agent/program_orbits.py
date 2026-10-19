# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Camera-relative placements (``orbit``) resolved into exact locations before approval.

``camera_create`` and ``light_create`` may say where the new object stands
around its ``look_at`` point as the scene camera sees it, instead of a world
location (``action_camera_light_spec.ORBIT``): a rim light is ``azimuth`` pi,
behind the subject, wherever the camera is. The controller resolves such a
program once, on the main thread, at its first precondition
(``program_camera.staged_program``, which reads the camera through
``BlenderExecutor.host_program``), as it expands a recipe: each orbit becomes
the location it names (``orbit_location``) and the resolved program is the
call's ``program_arguments``. The approval shows that exact location, the
derived transform and aim checks certify it at commit, and the kernel creates
an ordinary camera or light; nothing after this point knows about orbits.

Which camera: the one the scene looks through when the program has run, the
camera the person will see the light from. That is the scene's camera before
the program (its handle and world position, which the turn context also
states, and the handles of the objects that move it: its parent chain, every
Object its constraints point at, and what those read, ``follows``, from
``object_dependencies.walk``) unless a ``property_set`` of the
scene's ``camera`` makes another one the scene's, wherever in the program it
stands: a camera the program creates with ``camera_create`` (at its stated or
resolved location) or an existing camera the host reads (``cameras``). An orbit
that places the very camera the program ends with is measured from the scene
camera at its own point in the program, and so is every orbit of a program that
leaves the scene without a camera. Four recorded GPT-6.1 Sol tree one-shots
each created a camera, made it the scene's and placed a sun by
orbit in one program, two with the property_set after the orbit, and each was
refused whole while orbits were measured only from the camera before the
program. The frame is built from the camera's position alone, never its
rotation, so a rolled camera's "right" is the world-level right of an unrolled
camera at that position. The camera is not bound as an input: binding it would
also bind its parents and constraint targets. The location is fixed when it is
resolved, before approval, and nothing re-places it: the resolved program
records what each orbit was measured from (``camera_guards.FIELD``), and commit
reads it again through Blender after every write and refuses when the camera
stands elsewhere (``camera_guards``, the guarantee). What follows here is the
prediction that refuses early, with the remedy.

Refused before anything is staged, naming the orbit, the operation and what to
write instead (VALIDATION, which claims no change and leaves the request free
to correct it), only where the camera's position cannot be known before the
program runs or names no side:

* no camera to measure from (the scene has none and the program makes none);
* an operation of the program that edits that camera or writes an object it
  follows, anywhere in the program (commit reads the camera after it; a camera
  measured at the orbit's own point may still be deleted after it, and commit
  reads it before it goes), or a
  camera the program makes in a way that states no position (a batch copy); a
  camera whose dependencies cannot all be read (more than ``MAX_FOLLOWED``
  objects, or past the node or user-map budget) cannot be checked and
  refuses every orbit measured from it; orbits that place each other's camera;
* a program that moves the current frame, or writes another setting after
  which Blender runs its frame update (``program_frames.FRAME_UPDATE_PATHS``),
  while the camera moves with time (animated, parented or constrained:
  ``moves_with_time``, which the host reports): the scene is evaluated at its
  time again, not where it was measured; a frame-only rebind after approval is
  refused for the same camera (``program_camera.staged_program`` marks the call);
* a camera whose position is not finite, one standing at the point, or one
  straight above or below it (no side);
* an orbit that lands past the location bound (a far point with the default
  distance, which is the camera's): it names the orbit and asks for a shorter
  distance. Any other way a resolved program could fail validation is refused
  the same way rather than reaching the precondition as an unclassified error.

Pure Python; no Blender access. The host reads the camera.
"""

import copy

from .action_camera_light_spec import CAMERA_LIGHT_CREATE_KINDS, CAMERA_LIGHT_SCHEMAS, orbit_location
from .refusal_codes import RefusalError

ORBIT_HEAD = "orbit of %r (operations[%d]) is measured from %s"
AT_POINT = "the scene camera at that point of the program"
# The objects the host reports as moving the camera (BlenderExecutor.host_program); past it, nothing is checked.
MAX_FOLLOWED = 256
# A resolved location must pass the bound the create schema gives location (+-100,000 scene units).
LOCATION_BOUND = CAMERA_LIGHT_SCHEMAS["light_create"]["properties"]["location"]["items"]["maximum"]


def has_orbit(program):
    """Whether a (canonical) program places a new camera or light by ``orbit``."""
    operations = program.get("operations") if isinstance(program, dict) else None
    return any(isinstance(operation, dict) and operation.get("kind") in CAMERA_LIGHT_CREATE_KINDS
               and isinstance(operation.get("parameters"), dict) and "orbit" in operation["parameters"]
               for operation in operations or ())


def _sets_camera(operation):
    """``(True, value)`` when ``operation`` sets the scene camera (a reference, or None to clear it), else (False, None)."""
    values = operation["parameters"].get("values") if operation["kind"] == "property_set" else None
    return (True, values["camera"]) if isinstance(values, dict) and "camera" in values else (False, None)


def _label(index, operations):
    return "operations[%d] (%s)" % (index, operations[index]["kind"])


class _Cameras:
    """The scene camera of each orbit in one program (module docstring), placing orbits in dependency order."""

    def __init__(self, operations, camera, cameras, used):
        from .action_catalog import deleted_references, written_references
        from .program_frames import frame_updates
        self.operations, self.camera, self.cameras, self.used = operations, camera, cameras, used
        self.frame_moves = frame_updates({"operations": operations})
        self.effects = [(frozenset(written_references(operation)), frozenset(deleted_references(operation)))
                        for operation in operations]
        self.created = {"$" + operation["parameters"]["alias"].lstrip("$"): index
                        for index, operation in enumerate(operations)
                        if operation["kind"] == "camera_create" and operation["parameters"].get("alias")}
        self.final, self.placing, self.measured = self.at(len(operations)), set(), []

    def at(self, index):
        """The scene camera's reference before ``operations[index]`` runs (None: no camera)."""
        reference = self.camera["handle"] if self.camera is not None else None
        for position, operation in enumerate(self.operations[:index]):
            sets, value = _sets_camera(operation)
            if sets:
                reference = value
            elif reference is not None and reference in self.effects[position][1]:
                reference = None
        return reference

    def refuse(self, index, whom, reason, fix):
        name = str(self.operations[index]["parameters"].get("name", ""))[:64]
        raise RefusalError((ORBIT_HEAD + ", and %s: %s") % (name, index, whom, reason, fix), code="VALIDATION")

    def place(self, index):
        """Replace the orbit of ``operations[index]`` with the location it names (once)."""
        parameters = self.operations[index]["parameters"]
        if "orbit" not in parameters:
            return
        if index in self.placing:
            self.refuse(index, "a camera another orbit of this program places", "the two depend on each other",
                        "give one of them a location")
        self.placing.add(index)
        alias = parameters.get("alias")
        own = "$" + alias.lstrip("$") if self.operations[index]["kind"] == "camera_create" and alias else None
        if self.final is not None and self.final != own:
            reference, whom = self.final, "the camera the scene has after this program"
        else:  # It places that camera, or the program leaves the scene without one.
            reference, whom = self.at(index), AT_POINT
        span = range(len(self.operations))  # Commit reads the camera after the whole program (camera_guards).
        if reference is None:
            self.refuse(index, "the scene camera", "the scene has none",
                        "make one the scene's (property_set camera) in this program or an earlier one, or give location")
        seen, follows, made = self.position(index, reference, whom)
        if self.frame_moves and seen.get("moves_with_time"):
            self.refuse(index, whom, "this program writes %s, after which Blender evaluates the scene at its frame again, "
                        "and that camera moves with time (animation, a parent or a constraint)" % self.frame_moves[0],
                        "write it in a program of its own, or give location")
        self.used.append(seen)
        self.measured.append((reference, seen["location"]))  # Read again when the program commits (camera_guards).
        for position in span:
            written = self.effects[position][0]
            if position in (index, made):
                continue
            if reference in written:
                if position > index and reference in self.effects[position][1] and whom is AT_POINT:
                    continue  # Deleted after the orbit: commit reads it before it goes (camera_guards).
                self.refuse(index, whom, "%s %s the scene camera" % (
                    _label(position, self.operations), "deletes" if reference in self.effects[position][1] else "edits"),
                            "change it in an earlier program or a later one, or give location")
            if {item for item in follows if "#" not in item} & written:
                self.refuse(index, whom, "%s moves an object the scene camera follows (its parent, an object a "
                            "constraint points at, or one they read)" % _label(position, self.operations),
                            "move it in an earlier program or a later one, or give location")
            for handle, root in (item.split("#", 1) for item in follows if "#" in item):
                roots = _scene_roots(self.operations[position]) if handle in written else set()
                if roots and ("*" in roots or root == "*" or root in roots):
                    self.refuse(index, whom, "%s edits scene settings a driver of the scene camera reads (%s)"
                                % (_label(position, self.operations), "any" if root == "*" else root),
                                "edit them in an earlier program or a later one, or give location")
        parameters["location"] = _located(parameters, seen)
        del parameters["orbit"]

    def position(self, index, reference, whom):
        """``(camera, follows, creating operation or None)`` of ``reference``, placing a created camera first."""
        if reference in self.created:
            made = self.created[reference]
            self.place(made)
            return {"location": self.operations[made]["parameters"]["location"]}, frozenset(), made
        seen = self.camera if self.camera is not None and reference == self.camera["handle"] else self.cameras.get(reference)
        if seen is None:
            self.refuse(index, whom, "the program makes it the scene camera in a way that states no position (%s)"
                        % str(reference)[:64], "give location, or make it the scene camera in an earlier program")
        if seen.get("follows", ()) is None:
            self.refuse(index, whom, "what places that camera cannot all be read (more than %d objects, or past the "
                        "node or user-map budget)" % MAX_FOLLOWED, "give location")
        return seen, frozenset(seen.get("follows") or ()), None


def _located(parameters, camera):
    """The location an orbit names, refused (VALIDATION) where it cannot be one."""
    try:
        location = orbit_location(camera["location"], parameters["look_at"], parameters["orbit"])
    except ValueError as error:
        raise RefusalError(str(error), code="VALIDATION") from error
    if any(abs(value) > LOCATION_BOUND for value in location):
        raise RefusalError("orbit of %r lands past the location bound of %g; give it a shorter distance"
                           % (parameters["name"][:64], LOCATION_BOUND), code="VALIDATION")
    return location


def resolve(program, camera, cameras=None, used=None):
    """``program`` (canonical) with every orbit replaced by its location; a new dictionary.

    ``camera``: ``{"handle": long handle, "location": [x, y, z], "follows":
    [handles]}`` of the bound scene's camera before the program (``follows``:
    what moves it, None when there were more than MAX_FOLLOWED), or None when
    the scene has none; ``cameras``: the same for the existing objects the
    program makes the scene camera, by handle. Either may say
    ``moves_with_time``. ``used``: a list that receives each camera an orbit
    was measured from. Raises RefusalError (VALIDATION) for the cases in the
    module docstring.
    """
    from .program_spec import validate_program
    if not has_orbit(program):
        return program
    resolved = copy.deepcopy(program)
    placing = _Cameras(resolved["operations"], camera, cameras or {}, used if used is not None else [])
    for index, operation in enumerate(resolved["operations"]):
        if operation["kind"] in CAMERA_LIGHT_CREATE_KINDS:
            placing.place(index)
    from .camera_guards import record
    resolved = record(resolved, placing.final, placing.measured)
    try:
        return validate_program(resolved)  # Canonical (field order included), and refused as VALIDATION if not.
    except RefusalError:
        raise
    except ValueError as error:
        raise RefusalError(str(error), code="VALIDATION") from error


def followed_objects(camera, handle_of, limit=MAX_FOLLOWED, scene_handle_of=None):
    """Handles of the objects whose transform or shape can move ``camera``, each once; None past ``limit``.

    The walk ``program_frames.placement_moves_with_time`` uses
    (``object_dependencies.walk``): its parent chain, every Object its
    constraints point at (a custom space included), and for a vertex, bone or
    path parent or a subtarget that object's modifiers' Objects too. A program
    that moved one of them used to leave the orbit measured from the camera's
    old place (for example a Limit Location's space
    object, a vertex parent's Mirror object), and so do the objects and
    scenes its drivers read and the Objects and Collections in Geometry Nodes
    groups. An orbit reads the camera's position only, so the
    targets of a camera's rotation-only constraints are not followed.
    """
    return camera_reads(camera, handle_of, limit, scene_handle_of)[0]


def camera_reads(camera, handle_of, limit=MAX_FOLLOWED, scene_handle_of=None):
    """``(follows, moves_with_time)`` of a measured camera from one walk (``object_dependencies.walk``).

    ``follows``: object handles, and ``"<scene handle>#<path root>"`` for the
    scene settings its drivers read; None when the walk is incomplete, or a
    scene is read and no ``scene_handle_of`` names it."""
    from .object_dependencies import walk
    found = walk(camera, limit, position_only=True)
    if not found.complete or found.scenes and scene_handle_of is None:
        return None, True  # A scene a driver reads is followed by its handle; without one, nothing is proven.
    return ([handle_of(other) for other in found.objects if hasattr(other, "matrix_world")]
            + ["%s#%s" % (scene_handle_of(scene), root) for scene, root in found.scenes]), found.time


def _scene_roots(operation):
    """The first steps of the scene settings an operation writes (``*``: any)."""
    from .object_dependencies import path_root
    kind, params = operation["kind"], operation["parameters"]
    if kind == "property_set":
        return {path_root(path) for path in params["values"]}
    if kind == "world_configure" or kind == "node_edit" and params.get("world"):
        return {"world"}
    if kind == "marker_edit":
        return {"timeline_markers"}
    return {"*"}
