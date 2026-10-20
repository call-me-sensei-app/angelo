# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Main-thread native program facade, exact bindings and lifecycle admission.

Public begin/precondition/job/preview APIs are stable. Private graph isolation,
prospective checks, bundle ownership and atomic live writes have separate owners
(program_objects, program_storage, program_commit, program_dependencies).
Timers poll stages but NEVER commit; the host invokes commit_job in one normal
UNDO-enabled operator. Durable pre-request checkpoints remain the baseline."""

import copy
import sys
import threading
import uuid
import weakref
import contextlib

from . import camera_guards, exact_fence
from .program_refusals import check_deletions, check_frame_move
from .program_jobs import ProgramError, ProgramJob, PREVIEWS, WRITER
from .program_job_state import bound_revision
from .refusal_codes import RefusalError, refusal_code
from .action_operator_spec import needs_window
from .input_policy import tool_running
from .program_context import ProgramBinding, ProgramFrameChanged, program_binding
from .program_spec import validate_program, PROGRAM_NAMES, PROGRAM_TOOLS, PROG_TOOLS
from .program_material_facts import resolve_binding, binding_change_message
from .program_obligation_lifecycle import assert_preview, bound_obligations
from .program_dependencies import (
    Stage, SuspendedStage, MAX_TOTAL_VERTICES, MAX_STAGE_IDS, MAX_FINGERPRINT_ITEMS, fingerprint_budget as new_fingerprint_budget,
    _bpy, _uid, _main_ids, _rna_values, _embedded_links, _mutable_dependencies, _fingerprint,
)
from .program_objects import ProgramObjectsMixin
from .legacy_names import PROGRAM_STAGE, read_property
from .program_admission import (admit_input, assert_supported_custom_links, observed_binding, read_revision,
                                undeclared_dependencies)
from .program_storage import ProgramStorageMixin, cleanup_pending, has_pending_cleanup, require_cleanup_complete
from .stage_borrowing import borrows_any
from .program_commit import ProgramCommitMixin, _set_settings
from .program_verification import ProgramVerificationMixin
from .program_frames import FRAME_INDEPENDENT_MODIFIERS, FRAME_INDEPENDENT_TYPES, TIME_DEPENDENT_NODES, MAX_FRAME_SCAN_NODES, _animated, _graph_frame_independent, frame_independent, writes_frame  # noqa: F401 (re-exported)

_jobs = weakref.WeakValueDictionary()
_generation = 0
_registered = False
NativeProgramJob = ProgramJob

class BlenderProgramBackend(ProgramObjectsMixin, ProgramVerificationMixin, ProgramStorageMixin, ProgramCommitMixin):
    prepared_stage_ready = True  # create_stage isolates/remaps the complete graph before returning.
    coalesce_operations = True  # One action-only quantum: <=8 operations or 4 ms, always >=1.
    fuse_phases = True  # One hydration per poll serves several units within 16 ms of work.
    def __init__(self, window, store=None, controller=None):
        self.window, self.store, self.controller = window, store, controller
        self.scene = window.scene
        self.view_layer = window.view_layer
        self.generation = _generation
        self.scene_uid, self.view_uid = _uid(self.scene), int(self.view_layer.as_pointer())
        self.frame = (self.scene.frame_current, self.scene.frame_subframe)
        self.filepath = _bpy().data.filepath
        self.mutation_started = False

    def ensure_main_thread(self):
        if threading.current_thread() is not threading.main_thread():
            raise ProgramError("Native program jobs must run on Blender's main thread", code="INTERNAL_ERROR")

    def operation_waits(self, operation):
        """Whether ``operation`` waits for an artist's modal tool in any window to end (ProgramJob polls again).

        A transform operator_run alone gets a window context: its call would take over that window's status
        bar and Blender's global transform state (G.moving) under the artist's tool.
        """
        return (operation["kind"] == "operator_run" and needs_window(operation["parameters"]["operator"])
                and interaction_running())

    @property
    def cleanup_pending(self):
        return has_pending_cleanup()

    def check_context(self, binding=None):
        self.ensure_main_thread()
        try:
            if self.generation != _generation or _uid(self.window.scene) != self.scene_uid or self.window.view_layer.as_pointer() != self.view_uid:
                raise ProgramError("The document, scene or view layer changed; discard and observe a fresh program", code="STALE_CONTEXT")
            if _bpy().data.filepath != self.filepath:
                raise ProgramError("The document path changed; the staged result no longer has its exact context", code="STALE_CONTEXT")
            frame = (self.scene.frame_current, self.scene.frame_subframe)
            if frame != self.frame:
                # Distinct from the failures above: the controller may rebind an
                # unchanged program once at the new frame (program_context).
                raise ProgramFrameChanged(self.frame, frame)
            if self.controller is not None and getattr(self.controller, "closed", False):
                raise ProgramError("The originating conversation is no longer active", code="OWNERSHIP")
        except ReferenceError as error:
            raise ProgramError("The original document was reloaded or replaced", code="STALE_CONTEXT") from error

    def _resolve(self, handle):
        if self.store is None:
            from . import scene_queries
            self.store = scene_queries
        resolver = getattr(self.store, "resolve_object", getattr(self.store, "resolve", None))
        if resolver is None:
            raise ProgramError("A document-scoped scene handle resolver is required for native programs", code="INTERNAL_ERROR")
        try:
            return resolver(self.window, handle)
        except TypeError:
            return resolver(handle)

    def _revision(self, obj, handle):
        """The exact current object revision; the scene reader's is read once per exact fence (exact_fence)."""
        revision = getattr(self.store, "object_revision", None)
        queries = sys.modules.get(__package__ + ".scene_queries")
        if exact_fence.current() is None or revision not in (None, getattr(queries, "object_revision", None)):
            return read_revision(self.window, obj, revision)  # Another store's reader is never shared.
        key = (self.window.as_pointer(), obj.as_pointer())
        return exact_fence.check("revision", key, lambda: read_revision(self.window, obj, revision))

    @exact_fence.reads_only
    def bind(self, program):
        self.check_context()
        require_cleanup_complete()
        from .action_catalog import MAX_VERTICES, MAX_EDGES, MAX_FACES
        entries = {entry["handle"]: entry["revision"] for key in ("read_set", "write_set") for entry in program[key]}
        originals, revisions, fingerprints = {}, {}, {}
        fingerprint_budget = new_fingerprint_budget()
        vertices = 0
        scene_binding = None
        current_scene = self._scene_handle()
        for handle, expected in entries.items():
            if handle == current_scene:
                # The bound scene's allow-listed settings, not an object.
                if self._scene_revision() != expected:
                    raise ProgramError("The scene settings changed; read angelo_scene_settings again before retrying", code="STALE_SETTING", details={"handles": [handle]})
                revisions[handle] = expected
                scene_binding = {"handle": handle, "revision": expected,
                                 "write": any(entry["handle"] == handle for entry in program["write_set"])}
                continue
            obj = self._resolve(handle)
            if obj is None or obj not in list(self.scene.objects):
                raise ProgramError("A declared input is no longer in the bound scene", code="STALE_REVISION", details={"handles": [handle]})
            vertices += admit_input(obj, (MAX_VERTICES, MAX_EDGES, MAX_FACES))
            actual = self._revision(obj, handle)
            if actual != expected:
                raise ProgramError("An exact object revision changed; inspect the target before retrying", code="STALE_REVISION", details={"handles": [handle]})
            originals[handle], revisions[handle], fingerprints[handle] = obj, actual, _fingerprint(obj, fingerprint_budget)
        # Undeclared dependencies (a parent the program clears, a modifier's object): bound like an inferred
        # binding to the revision the context last observed, never to the live one.
        dependencies, inferred = undeclared_dependencies(list(originals.values())), []
        while dependencies:
            obj = dependencies.pop(0)
            handle, expected = observed_binding(self.controller, obj, self.scene)
            vertices += admit_input(obj, (MAX_VERTICES, MAX_EDGES, MAX_FACES))
            if len(originals) >= 64 or self._revision(obj, handle) != expected:
                raise ProgramError("A dependency (%s) changed since your latest observation, or there are more than 64 "
                                   "inputs; observe it again and declare it in the read set" % handle, code="STALE_DEPENDENCY", details={"handles": [handle]})
            originals[handle], revisions[handle], fingerprints[handle] = obj, expected, _fingerprint(obj, fingerprint_budget)
            inferred.append(handle)
            dependencies = undeclared_dependencies(list(originals.values()))
        if vertices > MAX_TOTAL_VERTICES:
            raise ProgramError("The program's inputs have %s vertices in all; at most %s: split it into programs with "
                               "fewer inputs" % (format(vertices, ","), format(MAX_TOTAL_VERTICES, ",")),
                               code="LIMIT_EXCEEDED")
        check_deletions(self.scene, current_scene, originals, program)  # What only commit could refuse, now.
        check_frame_move(self.scene, originals, program)
        self._preflight_existing(originals)
        objects = tuple(handle for handle in entries if handle in originals) + tuple(inferred)
        binding = {"handles": objects, "revisions": revisions, "fingerprints": fingerprints, "scene": scene_binding,
                "originals": originals,
                "writes": tuple(entry["handle"] for entry in program["write_set"] if entry["handle"] in originals),
                "generation": self.generation, "scene_uid": self.scene_uid, "frame": self.frame,
                "view_uid": self.view_uid, "program": copy.deepcopy(program)}
        binding["obligations"] = resolve_binding(self, binding, fingerprint_budget)
        return binding

    def _scene_handle(self):
        from .scene_settings import scene_handle
        return scene_handle(self.scene)

    def _scene_revision(self):
        from .scene_settings import settings_revision
        return settings_revision(self.scene)

    @exact_fence.reads_only
    def verify(self, binding):
        self.check_context(binding)
        scene = binding.get("scene")
        if scene is not None and (self._scene_handle() != scene["handle"] or self._scene_revision() != scene["revision"]):
            raise ProgramError("The scene settings changed; the staged program was discarded, not applied", code="STALE_SETTING", details={"handles": [scene["handle"]]})
        for handle in binding["handles"]:
            obj = self._resolve(handle)
            if obj != binding["originals"][handle]:
                raise ProgramError("The exact target changed; the staged program was discarded, not applied", code="STALE_REVISION", details={"handles": [handle]})
            # A new unsupported link in a readonly serialized dependency must
            # also invalidate a preview, not escape the original fingerprint's
            # closed mutable families or be silently replaced by the bundle.
            assert_supported_custom_links(obj)
            if bound_revision(lambda: self._revision(obj, handle), handle) != binding["revisions"][handle]:
                raise ProgramError("The exact target or a mutable dependency changed; the staged program was discarded, not applied",
                                   code="STALE_REVISION", details={"handles": [handle]})
            if _fingerprint(obj) != binding["fingerprints"][handle]:
                raise ProgramError("A mutable dependency changed outside the available object observation; the staged program was discarded",
                                   code="STALE_DEPENDENCY", details={"handles": [handle], "opaque": True})
        try:
            current = resolve_binding(self, binding)
        except RefusalError as error:  # It resolved at binding: a refusal now (a renamed material) is a changed state.
            if isinstance(error, ProgramError) or refusal_code(error) == "INTERNAL_ERROR":
                raise  # Its own code; an internal fault keeps the recovery barrier.
            raise ProgramError("The bound material verification changed: " + str(error)[:300] + "; observe again",
                               code="STALE_DEPENDENCY", details={"handles": list(binding["handles"]), "opaque": True})
        if current.identity != binding["obligations"].identity:
            raise ProgramError(binding_change_message(binding["obligations"], current),
                               code="STALE_DEPENDENCY", details={"handles": list(binding["handles"]), "opaque": True})

    def _preflight_existing(self, objects):
        from .action_kernels import assert_evaluation_budget

        class ReadOnlyBounds:
            def __init__(self):
                self.node_bounds, self.node_graphs = {}, {}
                self.owned_ids = {block for obj in objects.values() for block in _mutable_dependencies(obj)}

            def assert_staged(self, obj):
                if obj not in objects.values():
                    raise ProgramError("Modifier dependencies must be explicitly declared", code="VALIDATION")
                return obj

            def require_private(self, block):
                # _modifier_bound is an exclusively read-only count/settings
                # function. Shared source data will be cloned before kernels.
                return block

            def resolve(self, reference):
                return self.assert_staged(objects[reference])

        bounds = ReadOnlyBounds()
        for obj in objects.values():
            assert_evaluation_budget(bounds, obj)  # No depsgraph/evaluated geometry.

    def _apply_settings(self, value, settings, mapping):
        """Explicit commit callback; the facade retains its diagnostic test hook."""
        return _set_settings(value, settings, mapping)

def _owner(controller, window):
    if controller is not None:
        identity = getattr(controller, "session_id", None)
        if identity is None:
            state = getattr(controller, "state", {})
            identity = state.get("id") if isinstance(state, dict) else None
        return ("chat", str(identity)) if identity else ("controller", id(controller))
    return ("window", int(window.as_pointer()))

def precondition(window, name, args, store=None, controller=None):
    args = validate_program(name, args)
    backend = BlenderProgramBackend(window, store, controller)
    backend.ensure_main_thread()
    if name in {"angelo_scene_program", "angelo_accept_preview"}:
        require_cleanup_complete()
    if name == "angelo_scene_program":
        binding = backend.bind(args)
        scene = binding.get("scene")
        obligations = bound_obligations(args, binding)
        return ProgramBinding(binding["generation"], binding["scene_uid"], binding["frame"], binding["view_uid"],
                              tuple((handle, binding["revisions"][handle], binding["fingerprints"][handle])
                                    for handle in binding["handles"]),
                              (scene["handle"], scene["revision"]) if scene else None,
                              obligations.checks_digest, obligations)
    record = PREVIEWS.get(args["preview_id"], _owner(controller, window))
    if name != "angelo_discard_preview":
        try:
            assert_preview(record)
            record["backend"].verify(record["binding"])
        except Exception:
            PREVIEWS.discard(args["preview_id"], _owner(controller, window))
            raise
    return (record["id"], record["binding"]["generation"], record["binding"]["scene_uid"], record["created"],
            record.get("checks_digest"))

def rebind(window, name, args, approved, store=None, controller=None):
    """Rebind a frame-only change after proving unchanged verification, document,
    scene, view layer, settings and frame-independent inputs at the old frame.
    Returns current arguments/precondition/reason, a refusal when proof fails,
    or None without a frame change. Playback waits; nothing is staged here.
    """
    from .program_context import frame_reason, verification_identity
    from .scene_queries import object_revision_at_frame
    args = validate_program(name, args)
    approved = program_binding(approved)
    if approved is None:
        return None
    generation, scene_uid, frame, view_uid, inputs, settings, approved_checks, obligations = approved
    if obligations is not None:
        obligations.assert_integrity(args)
    backend = BlenderProgramBackend(window, store, controller)
    backend.ensure_main_thread()
    if (backend.generation, backend.scene_uid, backend.view_uid) != (generation, scene_uid, view_uid):
        return None
    if backend.frame == frame or playback_running(window):
        return None
    reason = frame_reason(frame, backend.frame)
    if writes_frame(args):  # The frame is this program's own setting: a move after approval is a changed setting.
        return {"refused": reason + " and this program sets the current frame", "error_code": "STALE_SETTING"}
    declared = {handle: (revision, fingerprint) for handle, revision, fingerprint in inputs}
    program = copy.deepcopy(args)
    for key in ("read_set", "write_set"):
        for entry in program[key]:
            handle = entry["handle"]
            if settings is not None and handle == settings[0]:
                # scene_settings.settings_revision has no frame field: equal means unchanged, revision kept.
                if entry["revision"] != settings[1] or backend._scene_revision() != settings[1]:
                    return {"refused": reason + " and the scene settings changed too", "error_code": "STALE_SETTING"}
                continue
            obj = backend._resolve(handle)
            if obj is None or obj not in list(backend.scene.objects) or handle not in declared:
                return {"refused": reason + " and a declared object is no longer bound", "error_code": "STALE_REVISION"}
            if declared[handle][0] != entry["revision"]:
                return {"refused": reason + " and a declared revision differs from the approved one", "error_code": "STALE_REVISION"}
            if not frame_independent(obj):
                return {"refused": reason + " and a declared object can change with time (animation, drivers, "
                                            "constraints, a parent, physics or a time-dependent modifier)",
                        "error_code": "STALE_DEPENDENCY", "refusal_details": {"handles": [handle], "opaque": True}}
            if (object_revision_at_frame(window, obj, frame[0]) != entry["revision"]
                    or _fingerprint(obj) != declared[handle][1]):
                return {"refused": reason + " and a declared object changed too", "error_code": "STALE_REVISION"}
            entry["revision"] = backend._revision(obj, handle)
    current = precondition(window, name, program, store, controller)
    if (current.checks_digest != approved_checks or obligations is not None and
            verification_identity(current.obligations) != verification_identity(obligations)):
        return {"refused": "The program's required verification changed; review and stage the program again"}
    return {"arguments": program, "precondition": current, "reason": reason}

def interaction_running():
    """Whether any window runs an artist's modal tool (``input_policy.tool_running``: not Angelo's overlays)."""
    manager = _bpy().context.window_manager
    return any(tool_running(window) for window in getattr(manager, "windows", ()))

def playback_running(window):
    """Whether animation playback is active. Blender plays one screen at a time."""
    try:
        screen = window.screen
        return bool(screen is not None and screen.is_animation_playing)
    except (AttributeError, ReferenceError):
        return False

def begin(window, name, args, controller=None, store=None, *, approved_obligations=None):
    args = validate_program(name, args)
    owner = _owner(controller, window)
    if name in {"angelo_scene_program", "angelo_accept_preview"}:
        BlenderProgramBackend(window, store, controller).ensure_main_thread()
        require_cleanup_complete()
    if name == "angelo_scene_program":
        backend = BlenderProgramBackend(window, store, controller)
        backend.measured_cameras = camera_guards.for_program(args)  # Read again at commit (program_commit).
        job = ProgramJob(backend, args, owner=owner, approved_obligations=approved_obligations)
    elif name == "angelo_accept_preview":
        record = PREVIEWS.get(args["preview_id"], owner)
        try:
            record["backend"].check_context(record["binding"])
        except Exception:
            PREVIEWS.discard(args["preview_id"], owner)
            raise
        job = ProgramJob.accept(args["preview_id"], owner)
    elif name == "angelo_discard_preview":
        return PREVIEWS.discard(args["preview_id"], owner)
    else:
        record = PREVIEWS.get(args["preview_id"], owner)
        record["backend"].verify(record["binding"])
        result = {"success": True, "final": True, "status": "preview", "preview_id": record["id"],
                "delta": copy.deepcopy(record["delta"]), "scene_changed": False,
                "readable_summary": record["delta"].get("summary", "Native staged preview")}
        for key in ("views", "preview_note"):
            if key in record["delta"]:
                result[key] = copy.deepcopy(record["delta"][key])
        return result
    _jobs[job.id] = job
    return job

def job_for_id(identity):
    job = _jobs.get(identity)
    if job is None:
        raise ProgramError("This staged native job is unavailable", code="EXPIRED")
    return job

def commit_job(identity, window=None, controller=None):
    job = job_for_id(identity)
    if window is not None and job.backend.window.as_pointer() != window.as_pointer():
        raise ProgramError("Commit belongs to a different Blender window", code="OWNERSHIP")
    if controller is not None and job.owner != _owner(controller, job.backend.window):
        raise ProgramError("Commit belongs to a different conversation", code="OWNERSHIP")
    result = job.commit()
    if job.closed:
        _jobs.pop(identity, None)
    return result

@contextlib.contextmanager
def job_context(identity):
    """Temporary native inspection context, never Main residency across yields.

    Intended for native visual/debug read-back. The suspended authoritative
    bundle is unchanged; a caller's temporary inspection edits are discarded.
    """
    job = job_for_id(identity)
    if job.closed or job.stage is None:
        raise ProgramError("No uncommitted staged program is available", code="EXPIRED")
    backend = job.backend
    backend.verify(job.binding)
    if isinstance(job.stage, SuspendedStage) and (job.stage.native_owner is not None or borrows_any(job.stage)):
        try:
            job.stage = backend.retain_stage(job.stage)
        except Exception:
            job.cancel()
            raise
    stage = backend.resume_stage(job.stage)
    try:
        yield stage
    finally:
        if isinstance(job.stage, SuspendedStage):
            backend._remove_stage_ids(stage, keep=set())

@contextlib.contextmanager
def preview_context(preview_id, controller=None, window=None):
    """Hydrate for native pixels/visualization, then remove every temporary ID.

    Usage: ``with preview_context(id, controller, window) as stage:``. The stage
    exposes scene, view_layer, collection, objects, context and the exact delta.
    Render only under a temp_override; never assign the artist's window.scene.
    """
    window = window or _bpy().context.window
    record = PREVIEWS.get(str(uuid.UUID(preview_id)), _owner(controller, window))
    backend = record["backend"]
    backend.verify(record["binding"])
    stage = backend.resume_stage(record["stage"])
    try:
        yield stage
    finally:
        # Retain the bundle for Accept; only hydrated Main IDs are temporary.
        backend._remove_stage_ids(stage, keep=set())

def has_active_programs():
    return WRITER.owner is not None

def runtime_is_staging():
    """Whether actual temporary Main scenes would contaminate a checkpoint.

    Call on the UI thread. A retained/suspended bundle owns no Main ID, and
    neither source cloning nor hydration yields before its private scene tag
    is present. The reserved tag also covers temporary visual inspections.
    The global writer/commit lease is independent and remains unchanged.
    """
    if threading.current_thread() is not threading.main_thread():
        raise ProgramError("Inspect native stage residency on Blender's main thread", code="INTERNAL_ERROR")
    # A stage left in a file saved before the rename carries the pre-rename tag; it still blocks capture.
    return has_pending_cleanup() or any(read_property(scene, PROGRAM_STAGE) for scene in _bpy().data.scenes)

def cleanup_all(*_args):
    global _generation
    _generation += 1
    for job in list(_jobs.values()):
        if not job.closed:
            job.cancel()
    _jobs.clear()
    PREVIEWS.clear()
    cleanup_pending()

def _before_save(*_args):
    # Unlike merely hiding collections, removing these Main IDs guarantees no
    # staged scene/material/action is serialized into an ordinary artist save.
    cleanup_pending()
    for job in list(_jobs.values()):
        if not job.closed:
            try:
                job.suspend()
            except Exception:
                job.cancel()

def register():
    global _registered
    if _registered:
        return
    bpy = _bpy()
    for name, callback in (("load_pre", cleanup_all), ("undo_pre", cleanup_all), ("redo_pre", cleanup_all),
                           ("save_pre", _before_save)):
        bpy.app.handlers.persistent(callback)
        handlers = getattr(bpy.app.handlers, name)
        if callback not in handlers:
            handlers.append(callback)
    _registered = True

def unregister():
    global _registered
    cleanup_all()
    bpy = _bpy()
    for name, callback in (("load_pre", cleanup_all), ("undo_pre", cleanup_all), ("redo_pre", cleanup_all),
                           ("save_pre", _before_save)):
        handlers = getattr(bpy.app.handlers, name)
        if callback in handlers:
            handlers.remove(callback)
    _registered = False
