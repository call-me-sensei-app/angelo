# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The camera a program's orbit placements were measured from, read again through Blender when it commits.

An ``orbit`` (``program_orbits``) becomes a location before approval, measured
from the scene camera's position. Whether the program, or the frame update it
causes, would move that camera is first predicted by a static walk of what
places it (``object_dependencies.walk``), so that such a program is refused
early with its remedy. The prediction is not the guarantee; this check is.

The resolved program records what its orbits assumed (``FIELD``, written by
the host as it resolves them; the published schema has no such field, so a
model cannot send one): the camera the scene has after the program
(``scene_camera``, an object handle, a ``$alias`` or None) and, for each camera
an orbit was measured from, its reference and the position it was measured at.
The approval shows it with the program. Commit reads them again after every
write, in the same rollback scope as every other commit check
(``program_commit``). A program that writes a setting after which Blender runs
its frame update (``program_frames.FRAME_UPDATE_PATHS``: the frame, the preview
range, the time map...) first gets that update (``frame_update``: a
frame-change evaluation at its frame, without frame-change handlers), and up to
``SETTLE`` more while the measured cameras or what places them still move (a
dependency cycle Blender evaluates one step per update); one that has not
settled then is refused. Then:

* the camera the scene shows: its ``camera``, or, after such a program, the
  camera Blender's camera markers make the scene's at its time
  (``BKE_scene_camera_switch_find``, applied by that frame update), must be
  ``scene_camera``;
* each measured camera's evaluated world position in the bound view layer's
  depsgraph must be finite and the measured one within float32 precision
  (``ULPS`` = 4 * 2^-23 of each coordinate's magnitude, four to eight float32
  steps, at least ``ABSOLUTE``): a larger move refuses, near the origin or far
  from it (about 0.05 at a coordinate of 100000, the bound of a placement). A
  camera the program deletes
  is read before it is unlinked, with everything else the program wrote; a
  camera the program made and deleted again stood where it was stated.

Otherwise the commit raises ``CAMERA_MOVED`` (``CHECK_FAILED``) naming the
camera, where it was measured and where it stands, what moved it where the host
can tell (an edit after approval, a written parent or constraint target, the
frame update, a camera the markers switch to) and the fix; the journal rolls
the program back, and nothing re-places the light after approval. Blender's
frame update replaces unkeyed values of animated channels as any frame change
does, refused or not (``animated_values``): the result names them rather than
claiming nothing changed. Frame-change handlers (Python of an add-on or a
trusted file) run when Blender processes the frame change after commit, as for
any frame change, and are outside this check.
"""

import math

FIELD = "measured_from"
ULPS = 4 * 2.0 ** -23  # Four float32 steps of a coordinate's magnitude: what re-evaluating the same state may differ by.
ABSOLUTE = 1e-6
MAX_CAMERAS = 32
SETTLE = 3  # Further frame updates while a dependency cycle still moves the cameras (frame_update).
AGAIN = "Submit the program again so the host measures the camera where it stands now"
FRAME = "Write the frame or frame setting in a program of its own"
_BOUND = 1e9


def validate(value, aliases):
    """The canonical ``FIELD`` of a program, or ValueError (host-written: any other shape is a host error)."""
    if not isinstance(value, dict) or set(value) != {"scene_camera", "cameras"}:
        raise ValueError("measured_from requires exactly scene_camera and cameras")
    cameras = value["cameras"]
    if not isinstance(cameras, list) or not 1 <= len(cameras) <= MAX_CAMERAS:
        raise ValueError("measured_from lists 1-32 measured cameras")
    result, seen = [], set()
    for entry in cameras:
        if not isinstance(entry, dict) or set(entry) != {"camera", "location"}:
            raise ValueError("A measured camera requires exactly camera and location")
        reference = _reference(entry["camera"], aliases)
        location = entry["location"]
        if (not isinstance(location, list) or len(location) != 3 or any(
                isinstance(item, bool) or not isinstance(item, (int, float)) or not abs(item) <= _BOUND
                for item in location)):
            raise ValueError("A measured camera's location is three finite numbers")
        if reference in seen:
            raise ValueError("A camera is measured once per program")
        seen.add(reference)
        result.append({"camera": reference, "location": [float(item) for item in location]})
    scene_camera = value["scene_camera"]
    return {"scene_camera": None if scene_camera is None else _reference(scene_camera, aliases), "cameras": result}


def _reference(value, aliases):
    if not isinstance(value, str) or not 1 <= len(value) <= 160:
        raise ValueError("A measured camera is an object handle or a created $alias")
    if value.startswith("$") and value not in aliases:
        raise ValueError("A measured camera's $alias names a camera this program creates")
    return value


def record(program, scene_camera, measured):
    """``program`` with ``FIELD`` stating ``scene_camera`` and each ``(reference, location)`` once, in order."""
    cameras, seen = [], set()
    for reference, location in measured:
        if reference not in seen:
            seen.add(reference)
            cameras.append({"camera": reference, "location": [float(value) for value in location]})
    return dict(program, **{FIELD: {"scene_camera": scene_camera, "cameras": cameras}}) if cameras else program


def for_program(program):
    """What commit reads again for a canonical program: its ``FIELD`` (if any) and the frame-update settings it
    writes (``program_frames.frame_updates``)."""
    from .program_frames import frame_updates
    measured = program.get(FIELD) if isinstance(program, dict) else None
    return dict(measured or {}, frame_written=frame_updates(program))


def location(obj, depsgraph):
    """The world position ``obj`` has in ``depsgraph`` (its original's where none is given or it is not evaluated)."""
    evaluated = obj
    if depsgraph is not None and callable(getattr(obj, "evaluated_get", None)):
        evaluated = obj.evaluated_get(depsgraph)
    return [float(value) for value in evaluated.matrix_world.translation]


def _key(value):
    pointer = getattr(value, "as_pointer", None)
    return pointer() if callable(pointer) else id(value)


def _near(first, second):
    return all(abs(a - b) <= max(ABSOLUTE, ULPS * max(abs(a), abs(b))) for a, b in zip(first, second))


def _f32(value):
    import struct
    return struct.unpack("f", struct.pack("f", value))[0]


def scene_time(scene):
    """``BKE_scene_ctime_get``, in float32 as Blender computes it: the frame with its subframe, times the time
    map's ``framelen`` (``frame_map_old / frame_map_new``)."""
    render = scene.render
    length = _f32(_f32(render.frame_map_old) / _f32(render.frame_map_new)) if render.frame_map_new else 1.0
    return _f32(_f32(scene.frame_current + _f32(scene.frame_subframe)) * length)


def marker_camera(scene):
    """The camera Blender's camera markers make ``scene``'s at its frame, or None (``BKE_scene_camera_switch_find``)."""
    time, frame, first, first_frame, camera = int(scene_time(scene)), None, None, None, None
    for marker in scene.timeline_markers:
        bound = marker.camera
        if bound is None or bound.hide_render:
            continue
        if marker.frame <= time and (frame is None or marker.frame > frame):
            camera, frame = bound, marker.frame
            if frame == time:
                break
        if first_frame is None or marker.frame < first_frame:
            first, first_frame = bound, marker.frame
    return camera if camera is not None else first


class _Check:
    def __init__(self, backend, stage, measured):
        self.backend, self.stage, self.measured = backend, stage, measured
        self.cameras = measured.get("cameras") or []
        self.created = {"$" + alias.lstrip("$"): obj for alias, obj in stage.context.aliases.items()}
        self.frame_written, self.done, self.updated = list(measured.get("frame_written") or ()), set(), 0
        depsgraph = backend.view_layer.depsgraph
        self.stood = {}  # Where each existing measured camera stands before any write: an edit after it was measured.
        for entry in self.cameras:
            obj = None if entry["camera"].startswith("$") else self.resolve(entry["camera"])
            if obj is not None:
                self.stood[entry["camera"]] = location(obj, depsgraph)
        from .animated_values import Channels, animated_ids, at_frame, _key
        owners = list(self.program_owners())
        self.channels = Channels((animated_ids(depsgraph) if self.frame_written else []) + owners)  # Before any write.
        # Written IDs the channel list had to leave out: what an animation replaces of their writes is not measured.
        self.unlisted = any(owner is not None and _key(owner) in self.channels.skipped for owner in owners)
        self.after_writes = self.frame_report = self.writes_report = self.rolled_report = None
        self.pending = []  # Stored values their animation does not give now: an artist's unkeyed edits.
        if self.frame_written and self.channels.entries and not self.channels.partial:
            # Blender's frame update replaces them, kept or refused: the update a frame change runs, at this frame.
            values = self.channels.values
            self.pending = self.channels.changes(values, at_frame(self.channels, lambda: _tag(backend),
                                                                  backend.view_layer))

    def program_owners(self):
        """The IDs this program writes and those whose animation can hold what it writes: each written or created
        object, its data and shape keys, its materials and their trees, and a written scene with its World."""
        writes = set(self.stage.binding.get("writes", ()))
        blocks = [self.stage.originals[handle] for handle in writes if handle in self.stage.originals]
        for block in blocks + list(self.created.values()):
            data = getattr(block, "data", None)
            yield from (block, data, getattr(data, "shape_keys", None))
            for slot in getattr(block, "material_slots", ()):
                yield from (slot.material, getattr(slot.material, "node_tree", None))
        if writes & frozenset(getattr(self.stage.context, "scene_handles", ()) or ()):
            world = self.backend.scene.world
            yield from (self.backend.scene, world, getattr(world, "node_tree", None))

    @property
    def frame_moved(self):
        """Whether Blender runs its frame update after this commit (the program writes a frame-update setting)."""
        scene = self.backend.scene
        return bool(self.frame_written) or (scene.frame_current, scene.frame_subframe) != tuple(self.backend.frame)

    def resolve(self, reference):
        if reference.startswith("$"):
            return self.created.get(reference)
        try:
            return self.backend._resolve(reference)
        except Exception:  # A handle that no longer resolves names a camera the scene no longer has.
            return None

    def refuse(self, text, fix=AGAIN):
        from .program_jobs import ProgramError
        raise ProgramError("CAMERA_MOVED: %s; nothing this program wrote remains. %s, or give the light or camera a "
                           "location." % (text, fix), code="CHECK_FAILED")

    def camera(self, entry, depsgraph):
        reference = entry["camera"]
        if reference in self.done:
            return
        self.done.add(reference)
        obj = self.resolve(reference)
        if obj is None:
            if reference.startswith("$"):
                return  # Made and deleted by this program: it stood where the program stated, nothing moved it.
            self.refuse("the camera an orbit was measured from is no longer in the scene")  # Gone after approval.
        now = location(obj, depsgraph)
        if not all(math.isfinite(value) for value in now):
            self.refuse("camera %r evaluates to a position that is not finite %s" % (str(obj.name)[:64], _shown(now)))
        if not _near(now, entry["location"]):
            moved = sum((a - b) ** 2 for a, b in zip(now, entry["location"])) ** .5
            why, fix = self.cause(obj, entry)
            self.refuse("orbit placements were measured from camera %r at %s, and after this program's writes it "
                        "stands at %s (%.6g away): %s" % (str(obj.name)[:64], _shown(entry["location"]), _shown(now),
                                                          moved, why), fix)

    def cause(self, obj, entry):
        """What moved ``obj`` where the host can tell, and the fix that names: ``(why, fix)``."""
        before = self.stood.get(entry["camera"])
        if before is not None and not _near(before, entry["location"]):
            return ("it was moved after it was measured, outside this program (it stood at %s before this program's "
                    "writes)" % _shown(before), AGAIN)
        written = {_key(original) for handle, original in self.stage.originals.items()
                   if handle in self.stage.binding.get("writes", ())}
        movers, item, seen = [], obj, set()
        while item is not None and _key(item) not in seen:  # Its parent chain and their constraints' Objects.
            seen.add(_key(item))
            parent = getattr(item, "parent", None)
            for other in [parent] + [getattr(c, "target", None) for c in getattr(item, "constraints", ())]:
                if other is not None and _key(other) in written:
                    movers.append(str(other.name)[:64])
            item = parent
        if movers:
            return ("the program writes %s, which places it" % ", ".join(sorted(set(movers))[:4]),
                    "Change that in an earlier program or a later one")
        if self.frame_moved:
            return ("the program writes %s, after which Blender evaluates the scene at its time again, and the camera "
                    "moves with time" % (self.frame_written or ["the frame"])[0], FRAME)
        return "something that places it changed (a driver, a node or a scene setting)", AGAIN

    def placing(self):
        """The measured cameras and what the walk finds places them (cameras alone past its limit)."""
        from .object_dependencies import walk
        from .program_orbits import MAX_FOLLOWED
        found = []
        for entry in self.cameras:
            obj = self.resolve(entry["camera"])
            if obj is None:
                continue
            found.append(obj)
            reached = walk(obj, MAX_FOLLOWED, position_only=True)
            found.extend(item for item in reached.objects if reached.complete and hasattr(item, "matrix_world"))
        return found


def _shown(values):
    return "(%s)" % ", ".join("%.6g" % value for value in values)


def prepare(backend, stage):
    """The commit's check of ``stage`` (None for a backend ``native_programs.begin`` did not make), before writes."""
    measured = getattr(backend, "measured_cameras", None)
    return None if measured is None else _Check(backend, stage, measured)


def before_deletions(check, deleted):
    """Read the measured cameras the program deletes, after every other write and the frame update, before
    commit unlinks them."""
    doomed = [entry for entry in check.cameras if entry["camera"] in set(deleted)] if check is not None else ()
    if doomed:
        check.backend.view_layer.update()  # Everything written, at the end frame, before the camera leaves.
        frame_update(check)
        for entry in doomed:
            check.camera(entry, check.backend.view_layer.depsgraph)


def _matrices(objects, depsgraph):
    return [tuple(value for row in location_matrix(obj, depsgraph) for value in row) for obj in objects]


def location_matrix(obj, depsgraph):
    evaluated = obj.evaluated_get(depsgraph) if callable(getattr(obj, "evaluated_get", None)) else obj
    return evaluated.matrix_world


def _tag(backend):
    backend.scene.frame_subframe = backend.scene.frame_subframe  # A frame-change tag at the same frame.
    backend.view_layer.update()


def frame_update(check):
    """After a program with orbits that writes a frame-update setting: the update Blender runs for it after commit.

    A time-source evaluation at the scene's time (``DEG_evaluate_on_refresh`` of a frame-change tag; frame-change
    handlers do not run), so that the cameras are read as the scene shows them. A dependency cycle, which Blender
    evaluates one step per update, can still move them then: up to ``SETTLE`` more updates run while the measured
    cameras or what places them still change, and a placement that has not settled is refused. Returns the
    updates run (none for a program without orbits, which Blender's own update after commit serves)."""
    if check is None or not check.cameras or not check.frame_moved:
        return 0
    check.updated += 1
    backend, objects = check.backend, check.placing()
    previous = _matrices(objects, backend.view_layer.depsgraph)
    for passes in range(1, SETTLE + 2):
        _tag(backend)
        current = _matrices(objects, backend.view_layer.depsgraph)
        if current == previous:
            return passes
        previous = current
    check.refuse("what places the measured cameras still changes after %d of Blender's updates at frame %d (a "
                 "dependency cycle that does not settle, whose driven values Blender moves on every update)"
                 % (SETTLE + 1, backend.scene.frame_current), FRAME)


def written(check, replaced=()):
    """After every transfer, before any evaluation: the stored values the program left (``animated_values``), read
    on the IDs the live objects now show (``replaced``: the data, keys, Actions and trees commit swapped in)."""
    if check is not None:
        check.channels.rebind(replaced)
        check.after_writes = check.channels.read()


def measure(check):
    """Inside the commit, after its checks: what an animation replaces of what is stored now, by Blender's evaluator.

    After a frame-update setting: the update Blender runs for it (once), and every stored value it replaced. Else:
    each channel the program changed that its own animation replaces at the next frame change (the IDs' animation
    evaluated alone, then the committed values written back)."""
    from .animated_values import WRITE_FACT, evaluate, held_by, unmeasured
    channels = check.channels if check is not None else None
    if channels is None:
        return
    if check.frame_written and channels.partial:
        check.frame_report = unmeasured()  # Not every animated ID could be listed, so none was evaluated before.
        return
    if not channels.entries:
        check.writes_report = unmeasured() if check.unlisted else None
        return
    stored = check.after_writes if check.after_writes is not None else channels.values
    backend = check.backend
    if check.frame_written:
        if check.pending and not check.updated:
            check.updated += 1
            _tag(backend)  # The update Blender runs for the frame setting, once.
        check.frame_report = _lost(check)
        return
    created = {_key(obj) for obj in check.created.values()}
    changed = set(channels.changes(channels.values, stored)) | {
        (slot, index) for slot, (owner, _path) in enumerate(channels.entries) if _key(owner) in created
        for index in range(len((stored[slot] or (False, ()))[1]))}
    if not changed:
        check.writes_report = unmeasured() if check.unlisted else None
        return
    now = evaluate(channels, {slot for slot, _ in changed}, backend.view_layer)
    pairs = channels.changes(stored, now, only=changed)
    check.writes_report = (channels.stated(pairs, stored, now, WRITE_FACT, extra=held_by) if pairs
                           else unmeasured() if check.unlisted else None)


def rolled_back(check, journal):
    """After a rollback: the one update Blender runs when the program had written a frame-update setting (the
    rollback restores it through the same setting), and the stored values that update replaced."""
    from .program_frames import FRAME_UPDATE_PATHS
    if check is None:
        return
    names = {path.rsplit(".", 1)[-1] for path in FRAME_UPDATE_PATHS}
    if check.updated or check.frame_written and any(name in names for _holder, name, _value in journal):
        _tag(check.backend)
    if check.frame_written and not check.channels.partial:
        check.rolled_report = _lost(check)


def _lost(check):
    """Of the unkeyed edits found before the writes, those a frame update replaced (stated, or None)."""
    from .animated_values import FRAME_FACT
    if not check.pending:
        return None
    channels, before = check.channels, check.channels.values
    now = channels.read()
    pairs = channels.changes(before, now, only=set(check.pending))
    return channels.stated(pairs, before, now, FRAME_FACT) if pairs else None


def replaced(check):
    """The sentence a commit failure adds for stored values a frame update replaced (empty if none)."""
    from .animated_values import note
    return note(check.rolled_report) if check is not None else ""


def annotate(check, result):
    """What the commit's frame update replaced, and what the next frame change will, stated in the result."""
    if check is None:
        return
    if check.frame_report:
        result["unkeyed_values_replaced"] = check.frame_report
    if check.writes_report:
        result["unkeyed_writes"] = check.writes_report


def verify(check):
    """Raise ``CAMERA_MOVED`` unless the scene camera and every measured camera are as the orbits assumed.

    Called after commit's view-layer update, its frame update and live checks, inside the rollback scope."""
    if check is None or not check.cameras:
        return
    backend, depsgraph = check.backend, check.backend.view_layer.depsgraph
    expected = check.measured["scene_camera"]
    shown = backend.scene.camera
    switched = marker_camera(backend.scene) if check.frame_moved else None
    if switched is not None:
        shown = switched
    wanted = None if expected is None else check.resolve(expected)
    if (shown is None) != (expected is None) or shown is not None and shown != wanted:
        marker = switched is not None and switched != backend.scene.camera
        check.refuse("the orbits assumed the scene would look through %s after this program, and it looks through "
                     "%s%s" % ("no camera" if expected is None else repr(str(getattr(wanted, "name", expected))[:64]),
                               "no camera" if shown is None else repr(str(shown.name)[:64]),
                               " (a camera marker switches to it at frame %d)" % backend.scene.frame_current
                               if marker else ""), FRAME if marker else AGAIN)
    for entry in check.cameras:
        check.camera(entry, depsgraph)
