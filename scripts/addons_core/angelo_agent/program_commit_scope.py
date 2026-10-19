# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private synchronous commit rights and actual ID-transfer correspondence.

This transports native capabilities and primitive identities, never semantic
completion evidence. The native owner validates Main membership and rights;
the controller owns approval, cancellation, writer retention and final delivery.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import threading

from .program_jobs import ProgramError
from .program_dependencies import _uid, MAX_STAGE_IDS


_ACTIVE = ContextVar("angelo_native_commit_scope", default=None)
_API = ("seal_approved_objects", "delete_stage", "delete_approved_objects", "new_collection",
        "delete_created_collections", "engine_prepare", "engine_apply", "stage_residue", "stage_owned")
# Each admitted Main ID owns at most two embedded IDs (Scene tree/master Collection);
# those embedded NodeTrees/Collections own no further embedded IDs. Keys count in Main.
MAX_DELETE_RECEIPT_IDS = 3 * MAX_STAGE_IDS


def _identity_rows(rows, *, limit=MAX_STAGE_IDS):
    if (type(rows) is not list and type(rows) is not tuple) or len(rows) > limit:
        raise ValueError("Invalid native identity rows")
    for row in rows:
        if (type(row) is not tuple or len(row) != 3 or type(row[0]) is not int or row[0] <= 0
                or type(row[1]) is not int or row[1] <= 0 or type(row[2]) is not str
                or not row[2] or len(row[2]) > 64):
            raise ValueError("Invalid native identity")
    return rows


def _stage_residue_rows(receipt):
    """Validate native transport without invoking caller-defined conversions."""
    if type(receipt) is not dict or len(receipt) != 3 or any(type(key) is not str for key in receipt):
        raise ValueError("Invalid native stage residue receipt")
    remaining, unknown, error = receipt.get("remaining"), receipt.get("unknown"), receipt.get("error")
    if (type(remaining) is not list or type(unknown) is not list or type(error) is not str
            or len(remaining) + len(unknown) > MAX_STAGE_IDS):
        raise ValueError("Invalid native stage residue receipt")
    if (remaining or unknown) and not error:
        raise ValueError("Native stage residue has no registration failure")
    _identity_rows(remaining)
    _identity_rows(unknown)
    return remaining, unknown, error[:1000]


def active():
    """Bookkeeping only: an unbound backend must not use public cleanup here."""
    return _ACTIVE.get()


def for_backend(backend):
    owner = getattr(backend, "_native_commit_scope", None)
    if owner is not active():
        raise ProgramError("Another backend owns the synchronous native commit scope", code="OWNERSHIP")
    if owner is not None:
        owner.check()
    return owner


def approved_deletions(program, originals):
    """(reference, original Object) for exactly the deletions the approved program's own operations make.

    object_delete's target, object_join's sources and object_convert's source
    without keep_original (``action_catalog.deleted_references``), each a
    declared object of the binding; nothing else is ever approved for removal.
    """
    from .action_catalog import deleted_references
    references = dict.fromkeys(reference for operation in (program or {}).get("operations", ())
                               for reference in deleted_references(operation))
    return tuple((reference, originals.get(reference)) for reference in references)


def identity(block):
    """Primitive identity captured while the actual ID is still owned/live."""
    return (int(block.as_pointer()), _uid(block), str(block.id_type)) if block is not None else None


@dataclass(frozen=True)
class CommittedTransfers:
    invocation_id: str
    job_id: str
    document: object
    epoch: int
    generation: int
    targets: tuple
    resources: tuple
    pre_cleanup_keep_ids: tuple


class _EngineRollback:
    """An ordinary settings-journal row backed by one exact native engine right."""

    def __init__(self, owner, right):
        self.owner, self.right = owner, right
        self.needed = True

    @property
    def value(self):
        return None

    @value.setter
    def value(self, _previous):
        if self.needed:
            self.owner.engine_apply(self.right, rollback=True)


class CommitScope:
    def __init__(self, controller, call, preparation, bridge, scope):
        from .program_storage import current_main_pointer
        self.controller, self.call, self.preparation = controller, call, preparation
        self.bridge, self.native_scope = bridge, scope
        self.job, self.backend = preparation.job, preparation.job.backend
        self.job_id = self.job.id
        self.document, self.main_pointer = preparation.document, current_main_pointer()
        self.transfers = {}
        self.created_collections = set()
        self.sealed = False
        self.approved_deletions = dict(preparation.approved_deletions)
        self.approved_engine = None
        scene = preparation.binding.get("scene")
        for operation in (preparation.approved_program or {}).get("operations", ()):
            if (scene is not None and operation["target"] == scene["handle"]
                    and operation["kind"] in {"property_set", "property_keyframe"}
                    and "render.engine" in operation["parameters"]["values"]):
                self.approved_engine = operation["parameters"]["values"]["render.engine"]

    def retain_stage_residue(self):
        """Hand failed registration evidence off before the native lease closes."""
        from .program_storage import retain_native_residue
        self.check()
        try:
            remaining, unknown, error = _stage_residue_rows(self.bridge.stage_residue(self.native_scope))
            uncertain = bool(error)  # A registration completeness error can lack a resolvable original row.
        except Exception:
            remaining, unknown = [], []
            error, uncertain = "Native stage registration residue transport failed; remaining membership is unknown", True
        cleanup = retain_native_residue(remaining, unknown, error, document=self.document,
                                        main_pointer=self.main_pointer, uncertain=uncertain)
        if cleanup:
            self.controller.record_native_residue(self.call, self.preparation, cleanup)

    def stage_owned_ids(self):
        """Resolve only the native loader's admitted identities, including Keys."""
        from .program_storage import _bpy, _main_ids, retain_native_residue
        self.check()
        rows = []
        try:
            rows = self.bridge.stage_owned(self.native_scope)
            if type(rows) is not list:
                raise ValueError("Invalid native stage ownership receipt")
            rows = _identity_rows(rows)
        except Exception as failure:
            error = self._delete_uncertainty("delete_stage", "Native stage ownership transport failed")
            raise error from failure
        try:
            current = _main_ids()
            # This native Main family has no public remove() and is omitted by _main_ids.
            current.update((block.as_pointer(), block) for block in getattr(_bpy().data, "shape_keys", ()))
            resolved = tuple(current[pointer] for pointer, uid, kind in rows
                             if pointer in current and (_uid(current[pointer]), current[pointer].id_type) == (uid, kind))
            if len(resolved) != len(rows):
                raise ValueError("An admitted native stage identity could not be resolved")
            return resolved
        except Exception as failure:
            cleanup = retain_native_residue(rows, (), "An admitted native stage identity could not be resolved",
                document=self.document, main_pointer=self.main_pointer, uncertain=True)
            error = ProgramError("An admitted native stage identity could not be resolved", code="CLEANUP_FAILED")
            for key, value in cleanup.items():
                setattr(error, key, value)
            raise error from failure

    def check(self):
        if (threading.current_thread() is not threading.main_thread() or active() is not self
                or getattr(self.backend, "_native_commit_scope", None) is not self
                or self.call.native_preparation is not self.preparation
                or self.preparation.job is not self.job or self.job.backend is not self.backend
                or self.job.id != self.job_id):
            raise ProgramError("The exact native commit scope is no longer owned", code="OWNERSHIP")

    def seal_approval(self):
        self.check()
        program = self.preparation.approved_program
        if not isinstance(program, dict):
            raise ProgramError("Native preparation has no original approved program", code="OWNERSHIP")
        from .action_catalog import deleted_references
        originals = self.approved_deletions
        roots = []
        for operation in program.get("operations", ()):
            for target in deleted_references(operation):  # object_delete, object_join's sources, a convert's source.
                if originals.get(target) is None or target.startswith("$"):
                    raise ProgramError("The approved deletion has no exact original Object", code="OWNERSHIP")
                block = originals[target]
                if not any(block is previous for previous in roots):
                    roots.append(block)
        self.bridge.seal_approved_objects(self.native_scope, tuple(roots))

    def validate_stage(self, stage):
        """Check deletion intent before even unlinking an artist Object."""
        self.check()
        if (stage.binding is not self.preparation.binding
                or set(stage.extras.get("deleted", ())) != set(self.approved_deletions)):
            raise ProgramError("Staged deletion differs from the original approved targets", code="OWNERSHIP")
        for reference, original in self.approved_deletions.items():
            if identity(stage.originals.get(reference)) != identity(original):
                raise ProgramError("A staged deletion no longer names its approved original Object", code="OWNERSHIP")

    def _delete_uncertainty(self, method, message):
        from .program_storage import retain_native_residue
        error = ProgramError(message, code="CLEANUP_FAILED")
        error.partial_changes_possible = True
        if method == "delete_stage" or method == "delete_created_collections" and self.created_collections:
            cleanup = retain_native_residue([], (), message, document=self.document,
                                            main_pointer=self.main_pointer, uncertain=True)
            for key, value in cleanup.items():
                setattr(error, key, value)
        return error

    def _delete(self, method, roots, *args):
        self.check()
        try:
            result = getattr(self.bridge, method)(self.native_scope, tuple(roots), *args)
            if (type(result) is not dict or any(type(key) is not str for key in result)
                    or type(result.get("success")) is not bool or type(result.get("kernel_called")) is not bool
                    or "scope_valid_after" in result and type(result["scope_valid_after"]) is not bool
                    or any(key in result and type(result[key]) is not str for key in ("error", "callback_error"))):
                raise ValueError("Invalid native deletion receipt")
            remaining = _identity_rows(result.get("remaining"), limit=MAX_DELETE_RECEIPT_IDS)
            removed = _identity_rows(result.get("removed"), limit=MAX_DELETE_RECEIPT_IDS)
            if len(remaining) + len(removed) > MAX_DELETE_RECEIPT_IDS:
                raise ValueError("Native deletion receipt exceeds its ID budget")
        except Exception as failure:
            raise self._delete_uncertainty(method, "Native deletion returned no exact effect receipt") from failure
        if (method in {"delete_stage", "delete_created_collections"} and result["kernel_called"] is True
                and result.get("scope_valid_after") is True):
            from .program_storage import retire_native_receipt
            retire_native_receipt(self.document, self.main_pointer, removed)
        if not result["success"] or result["remaining"]:
            message = result.get("error")
            if type(message) is not str or not message:
                message = result.get("callback_error")
            if type(message) is not str or not message:
                message = "Native deletion left owned IDs in Main" if result["remaining"] else "Native deletion did not complete cleanly"
            error = ProgramError(message[:1000], code="CLEANUP_FAILED")
            error.native_delete_result = result
            error.cleanup_pending = bool(result["remaining"])
            error.partial_changes_possible = bool(result["kernel_called"] or result["remaining"])
            if method in {"delete_stage", "delete_created_collections"} and result["remaining"]:
                from .program_storage import retain_native_residue
                if method == "delete_created_collections":
                    remaining = [row for row in remaining if row in self.created_collections]
                cleanup = retain_native_residue(remaining, (), message, document=self.document,
                                                main_pointer=self.main_pointer)
                for key, value in cleanup.items():
                    setattr(error, key, value)
            raise error
        return result

    def delete_stage(self, roots, keep=()):
        return self._delete("delete_stage", roots, tuple(keep))

    def delete_approved_objects(self, roots):
        return self._delete("delete_approved_objects", roots)

    def new_collection(self, name):
        self.check()
        collection = self.bridge.new_collection(self.native_scope, name)
        self.created_collections.add(identity(collection))
        return collection

    def delete_created_collections(self, roots):
        roots = tuple(roots)
        known = {pointer: (uid, kind) for pointer, uid, kind in map(identity, roots)
                 if (pointer, uid, kind) in self.created_collections}
        try:
            return self._delete("delete_created_collections", roots)
        except Exception:
            # Only actual collections created by this native owner enter the
            # protected registry. Artist Object deletion has no such retry path.
            from .program_storage import _owned_cleanup_ids, retain_cleanup
            live = _owned_cleanup_ids(known) if known else {}
            retain_cleanup({pointer: known[pointer] for pointer in live}, reason="Native created-Collection cleanup retained owned IDs")
            raise

    def engine_transfer(self, scene, expected, target, journal):
        self.check()
        if scene is not self.backend.scene or self.approved_engine is None or target != self.approved_engine:
            raise ProgramError("The engine transfer differs from the original approved Scene change", code="OWNERSHIP")
        right = self.bridge.engine_prepare(self.native_scope, scene, expected, target)
        rollback = _EngineRollback(self, right)
        journal.append((rollback, "value", expected))
        try:
            self.engine_apply(right)
        except ProgramError as error:
            effect = getattr(error, "native_engine_result", None)
            if isinstance(effect, dict) and effect.get("setter_called") is False:
                rollback.needed = False
            raise

    def engine_apply(self, right, *, rollback=False):
        self.check()
        result = self.bridge.engine_apply(self.native_scope, right, rollback)
        if not isinstance(result, dict) or result.get("success") is not True:
            message = ((result.get("error") or result.get("callback_error") or "Native engine transfer failed")
                       if isinstance(result, dict) else "Native engine transfer returned no effect receipt")
            error = ProgramError(str(message), code="INTERNAL_ERROR")
            error.native_engine_result = result
            error.partial_changes_possible = True
            raise error
        return result

    def record_transfer(self, staged, live, reason, occurrence=""):
        self.check()
        if self.sealed:
            raise ProgramError("Committed ID correspondence is already sealed", code="OWNERSHIP")
        if staged is not None and live is not None:
            row = (identity(staged), identity(live), str(reason), str(occurrence))
            self.transfers[row] = None

    def seal_transfers(self, stage, new_objects, retained):
        """Freeze actual choices before cleanup; no stage RNA or public receipt."""
        self.check()
        if self.sealed or stage.binding is not self.preparation.binding:
            raise ProgramError("The exact stage cannot seal its committed ID correspondence", code="OWNERSHIP")
        deleted = set(self.approved_deletions)
        targets = [(reference, identity(block)) for reference, block in stage.originals.items()
                   if reference not in deleted]
        created = {int(block.as_pointer()) for block in new_objects}
        targets.extend(("$" + alias.lstrip("$"), identity(block)) for alias, block in stage.context.aliases.items()
                       if int(block.as_pointer()) in created)
        for reference, staged in stage.objects.items():
            if reference not in deleted:
                live = stage.originals[reference]
                self.record_transfer(staged, live, "target", reference)
                staged_data, live_data = getattr(staged, "data", None), getattr(live, "data", None)
                self.record_transfer(staged_data, live_data, "target_data", reference)
                self.record_transfer(getattr(staged_data, "shape_keys", None), getattr(live_data, "shape_keys", None),
                                     "target_shape_keys", reference)
        for block in new_objects:
            self.record_transfer(block, block, "created_target")
        self.job.committed_transfers = CommittedTransfers(
            self.preparation.invocation_id, self.job_id, self.preparation.document,
            self.preparation.epoch, self.preparation.generation, tuple(targets), tuple(self.transfers),
            tuple(identity(block) for block in retained))
        self.sealed = True


@contextmanager
def bind(controller, call, preparation, bridge, scope):
    """Bind one exact backend through commit and cleanup, before native finish."""
    if call.native_preparation is not preparation or preparation.job is None or active() is not None:
        raise ProgramError("Native commit scope binding requires its exact preparation owner", code="OWNERSHIP")
    if any(not callable(getattr(bridge, name, None)) for name in _API):
        raise ProgramError("This binary lacks the required native commit rights", code="RECOVERY_REQUIRED")
    owner = CommitScope(controller, call, preparation, bridge, scope)
    if getattr(owner.backend, "_native_commit_scope", None) is not None:
        raise ProgramError("The backend already owns a native commit scope", code="OWNERSHIP")
    owner.backend._native_commit_scope = owner
    token = _ACTIVE.set(owner)
    try:
        owner.seal_approval()
        yield owner
    finally:
        try:
            controller.prepare_native_finish(call, preparation)
        finally:
            try:
                owner.retain_stage_residue()
            finally:
                owner.backend._native_commit_scope = None
                _ACTIVE.reset(token)
