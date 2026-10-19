# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private native Main/bundle ownership, hydration and deterministic cleanup.

ProgramStorageMixin writes only its dedicated private temporary directories.
Hydration rebinds topology and audited node provenance before any evaluation.
Successful staging keeps temporary Main IDs out of UI ticks; failed cleanup
retains explicit protected residue instead of claiming that isolation."""

import os
import shutil
import tempfile
import uuid
from pathlib import Path

from .program_jobs import ProgramError
from .refusal_codes import refusal_code, refusal_details
from .legacy_names import PROGRAM_STAGE
from .program_dependencies import Stage, SuspendedStage, MAX_STAGE_IDS, _bpy, _main_ids, _data_collections, _uid
from .program_stage_bundle import ProgramStageBundleMixin

# Compression remains the fallback. The native writer buffers at most 16 MiB
# and uses a plain file below that threshold; larger files use zstd. A failed
# plain write retries the buffered bytes compressed in the same temporary file.
STAGE_BUNDLE_COMPRESSION = True
STAGE_BUNDLE_COMPRESSION_MIN_SIZE = 16 * 1024 * 1024
_pending_cleanup = {}


class CleanupRecord(dict):
    """Exact residual identities, with no authority to bypass native refusal."""

    def __init__(self, identities, *, native_proof_required=False, reason="", unknown=(),
                 unresolved=False, completeness_uncertain=False, document=None, main_pointer=None):
        super().__init__(identities)
        self.native_proof_required = native_proof_required
        self.reason = str(reason)[:1000]
        self.unknown = unknown
        self.unresolved = unresolved
        self.completeness_uncertain = completeness_uncertain
        self.document = document
        self.main_pointer = main_pointer


def _filtered_cleanup(identities, previous):
    return CleanupRecord(identities, native_proof_required=getattr(previous, "native_proof_required", False),
                         reason=getattr(previous, "reason", ""), unknown=getattr(previous, "unknown", ()),
                         unresolved=getattr(previous, "unresolved", False), document=getattr(previous, "document", None),
                         completeness_uncertain=getattr(previous, "completeness_uncertain", False),
                         main_pointer=getattr(previous, "main_pointer", None))


def cleanup_error_text(error):
    """Bound cleanup diagnostics without executing exception string/truth hooks."""
    try:
        arguments = BaseException.args.__get__(error)
        if type(arguments) is tuple and arguments and type(arguments[0]) is str:
            return arguments[0][:1000]
    except Exception:
        pass
    return "Native cleanup raised an exception"


def current_main_pointer():
    """Read native StructRNA's Main pointer; a missing identity proves nothing."""
    try:
        pointer = _bpy().data.as_pointer()
    except Exception:
        return None
    return pointer if type(pointer) is int and pointer > 0 else None


def _document_identity(document):
    return (document if type(document) is tuple and len(document) == 2
            and type(document[0]) is str and type(document[1]) is int else None)


def retain_native_residue(remaining, unknown, reason, *, document, main_pointer, uncertain=False):
    """Receive validated native provenance without promoting it to deletion rights.

    These are last-native-observed rows, not a current post-tick membership proof.
    Python's removable-ID inventory can omit Key and other native Main members.
    Retirement therefore requires native proof or successful document/Main replacement.
    """
    if not remaining and not unknown and not uncertain:
        return {}
    retained = {pointer: (uid, kind) for pointer, uid, kind in remaining}
    document, unknown = _document_identity(document), tuple(dict.fromkeys(unknown))
    for key, record in list(_pending_cleanup.items()):
        if (getattr(record, "unresolved", False) and record.document == document
                and record.main_pointer == main_pointer):
            uncertain = uncertain or record.completeness_uncertain
            if record.reason and record.reason != reason:
                reason = record.reason[:500] + "; " + reason[:500]
            # Keep original evidence when native calls observed replacement at one address.
            unknown = tuple(dict.fromkeys((*record.unknown, *unknown,
                *((pointer, *identity) for pointer, identity in record.items()
                  if pointer in retained and retained[pointer] != identity))))
            retained = {**record, **retained}
            _pending_cleanup.pop(key, None)
    for key, record in list(_pending_cleanup.items()):
        rest = {pointer: identity for pointer, identity in record.items() if retained.get(pointer) != identity}
        if len(rest) != len(record):
            if rest or getattr(record, "unresolved", False):
                _pending_cleanup[key] = _filtered_cleanup(rest, record)
            else:
                _pending_cleanup.pop(key, None)
    _pending_cleanup[str(uuid.uuid4())] = CleanupRecord(retained, native_proof_required=True, reason=reason,
        unknown=unknown, unresolved=True, completeness_uncertain=uncertain, document=document, main_pointer=main_pointer)
    detail = reason or "Native stage registration retained unresolved temporary data"
    if unknown or uncertain:
        detail += "; native membership remains unresolved and requires proof or successful document replacement"
    return {"cleanup_error": detail[:1000], "cleanup_warning": True, "cleanup_pending": True,
            "temporary_ids_remaining": len({(pointer, *identity) for record in _pending_cleanup.values()
                                             for pointer, identity in record.items()})}


def retire_native_receipt(document, main_pointer, removed):
    """Consume exact kernel/scope-valid removal proof supplied by the bound adapter."""
    document = _document_identity(document)
    if document is None or type(main_pointer) is not int or main_pointer <= 0:
        return
    removed = set(removed)
    for key, record in list(_pending_cleanup.items()):
        if (not getattr(record, "unresolved", False) or record.document != document
                or record.main_pointer != main_pointer):
            continue
        remaining = {pointer: identity for pointer, identity in record.items() if (pointer, *identity) not in removed}
        unknown = tuple(row for row in record.unknown if row not in removed)
        if remaining or unknown or record.completeness_uncertain:
            retained = _filtered_cleanup(remaining, record)
            retained.unknown = unknown
            _pending_cleanup[key] = retained
        else:
            _pending_cleanup.pop(key, None)


def retire_native_residue_after_load(document):
    """Successful load_post only: retire uncertainty for a proved replaced Main."""
    document, main_pointer = _document_identity(document), current_main_pointer()
    if document is None or main_pointer is None:
        return
    for key, record in list(_pending_cleanup.items()):
        if (getattr(record, "unresolved", False) and record.document is not None
                and record.main_pointer is not None and record.document[0] != document[0]
                and record.main_pointer != main_pointer):
            _pending_cleanup.pop(key, None)


# Returned by a guard that itself proved its captured identities are gone, so an
# enclosing guard over the same live set need not scan Main again.
VERIFIED_REMOVAL = object()
# Main collection for each ID type, so removal never searches every collection.
_ID_COLLECTIONS = {"OBJECT": "objects", "MESH": "meshes", "MATERIAL": "materials", "NODETREE": "node_groups",
                   "SCENE": "scenes", "COLLECTION": "collections", "ACTION": "actions", "IMAGE": "images",
                   "TEXTURE": "textures", "CAMERA": "cameras", "LIGHT": "lights", "CURVE": "curves",
                   "CURVES": "hair_curves", "ARMATURE": "armatures", "LATTICE": "lattices", "WORLD": "worlds",
                   "GREASEPENCIL": "grease_pencils", "POINTCLOUD": "pointclouds", "VOLUME": "volumes",
                   "PARTICLE": "particles"}


def retain_cleanup(identities, *, reason=""):
    """Keep exact ownership after a job drops its potentially freed RNA wrappers."""
    from .program_commit_scope import active
    protected = active() is not None or getattr(identities, "native_proof_required", False)
    reason = reason or getattr(identities, "reason", "") or (
        "Native cleanup requires a fresh ownership/closure/reference proof before any later removal" if protected else "")
    new = dict(identities)
    promoted = {}
    for key, retained in list(_pending_cleanup.items()):
        ordinary = dict(retained)
        for pointer, identity in retained.items():
            if new.get(pointer) == identity:
                new.pop(pointer)
                if protected and not getattr(retained, "native_proof_required", False):
                    promoted[pointer] = identity
                    ordinary.pop(pointer)
        if len(ordinary) != len(retained):
            if ordinary:
                _pending_cleanup[key] = _filtered_cleanup(ordinary, retained)
            else:
                _pending_cleanup.pop(key, None)
    new.update(promoted)
    if new:
        _pending_cleanup[str(uuid.uuid4())] = CleanupRecord(new, native_proof_required=protected, reason=reason)


def protected_cleanup(identities):
    """A later public remover cannot acquire a prior refused native closure."""
    return any(getattr(retained, "native_proof_required", False)
               and (any(identities.get(pointer) == identity for pointer, identity in retained.items())
                    or any(identities.get(pointer) == (uid, kind)
                           for pointer, uid, kind in getattr(retained, "unknown", ())))
               for retained in _pending_cleanup.values())


def cleanup_identities(ids, previous=None):
    """Capture immutable ownership BEFORE removal; prior captures survive retries."""
    identities = dict(previous or {})
    for block in ids:
        if block is None:
            continue
        try:
            pointer = int(block.as_pointer())
            if pointer:
                # RNA subclasses can change without replacing the ID (e.g.
                # PointLight -> SpotLight). ID.id_type is the stable DNA family.
                identities[pointer] = (_uid(block), block.id_type)
        except ReferenceError:
            continue  # Already freed by an earlier attempt, never inspect again.
    return identities


def _owned_cleanup_ids(identities):
    # Pointer membership first: only captured pointers can match, so the other
    # Main IDs never pay for a session-UID and type lookup.
    return {pointer: block for pointer, block in _main_ids().items()
            if pointer in identities and identities[pointer] == (_uid(block), block.id_type)}


def _remove_owned(identities, remove):
    """Guard every teardown, including an overridden remover that partially fails.

    Only current Main IDs with the captured pointer AND UID AND type may enter
    the ordinary batch fallback. A captured native commit, or residue from its
    refused cleanup, never acquires that fallback. Residual ownership lives
    outside the job, so releasing its writer cannot hide the remaining IDs.
    """
    from .program_commit_scope import active
    scoped = active() is not None
    blocked = not scoped and protected_cleanup(identities)
    error, fallback, verified = None, None, False
    try:
        # Only an inner guard over this same live set returns VERIFIED_REMOVAL;
        # an overridden remover returns anything else and is checked here.
        if blocked:
            raise ProgramError("Retained native stage data needs a fresh native cleanup proof; public retry was not run",
                               code="CLEANUP_FAILED")
        verified = remove() is VERIFIED_REMOVAL
    except Exception as failure:
        error = failure
    remaining = {} if verified else _owned_cleanup_ids(identities)
    if remaining and not scoped and not protected_cleanup(identities):
        try:
            _bpy().data.batch_remove(ids=list(remaining.values()))
        except Exception as failure:
            fallback = failure
        remaining = _owned_cleanup_ids(identities)
    for key, retained in list(_pending_cleanup.items()):
        if getattr(retained, "unresolved", False):
            continue  # A partial Python inventory cannot resolve failed native registration.
        retained = {pointer: identity for pointer, identity in retained.items()
                    if identities.get(pointer) != identity or pointer in remaining}
        if retained:
            _pending_cleanup[key] = _filtered_cleanup(retained, _pending_cleanup[key])
        else:
            _pending_cleanup.pop(key, None)
    retain_cleanup({pointer: identities[pointer] for pointer in remaining}, reason="Native stage teardown retained owned IDs")
    # A native receipt covers closure IDs omitted by the public removable-ID inventory.
    reported_remaining, pending = len(remaining), bool(remaining)
    if scoped and type(error) is ProgramError:
        native_count = getattr(error, "temporary_ids_remaining", None)
        if type(native_count) is int:
            reported_remaining = max(reported_remaining, native_count)
        pending = pending or getattr(error, "cleanup_pending", None) is True
    if error is not None or fallback is not None or remaining:
        details = "; ".join(cleanup_error_text(item) for item in (error, fallback) if item is not None)
        state = ("temporary data cleanup is pending" if pending else
                 "owned temporary data was removed" if scoped else "owned temporary data was removed by fallback")
        failure = ProgramError("Native stage teardown failed; " + state + (": " + details if details else ""), code='CLEANUP_FAILED')
        failure.cleanup_error = str(failure)
        failure.cleanup_pending = pending
        failure.temporary_ids_remaining = reported_remaining
        failure.partial_changes_possible = pending
        raise failure from error
    return VERIFIED_REMOVAL


def _main_collection(block):
    """The Main collection that owns this block, or None for an embedded ID."""
    data = _bpy().data
    name = _ID_COLLECTIONS.get(getattr(block, "id_type", None))
    collection = getattr(data, name, None) if name else None
    if collection is not None:
        found = collection.get(block.name)
        if found is not None and found == block:
            return collection
    for collection in _data_collections():
        if any(candidate == block for candidate in collection):
            return collection
    return None


def cleanup_pending():
    """Retry only exact owned Main identities; never follow freed RNA wrappers."""
    from .program_commit_scope import active
    if active() is not None:
        return bool(_pending_cleanup)  # A captured commit cannot borrow another cleanup's authority.
    bpy = _bpy()
    for key, identities in list(_pending_cleanup.items()):
        if getattr(identities, "unresolved", False):
            continue
        owned = list(_owned_cleanup_ids(identities).values())
        if owned and not protected_cleanup(identities):
            try:
                bpy.data.batch_remove(ids=owned)
            except Exception:
                pass  # Ownership below remains visible, not dropped on a retry failure.
        identities = _pending_cleanup.get(key)
        if identities is None:
            continue  # A reentrant native refusal promoted this record; never recreate its public authority.
        remaining = {pointer: identities[pointer] for pointer in _owned_cleanup_ids(identities)}
        if remaining:
            _pending_cleanup[key] = _filtered_cleanup(remaining, identities)
        else:
            _pending_cleanup.pop(key, None)
    return bool(_pending_cleanup)


def has_pending_cleanup():
    return bool(_pending_cleanup)


def require_cleanup_complete():
    """Main-thread admission gate; retry cleanup, but never build over residue."""
    if has_pending_cleanup() and cleanup_pending():
        protected = any(getattr(record, "native_proof_required", False) for record in _pending_cleanup.values())
        recovery = ("A native cleanup refusal retained exact IDs; automatic public removal is disabled. "
                    "A fresh native ownership/reference proof or durable checkpoint recovery is required."
                    if protected else "Retry cleanup or restore the durable checkpoint.")
        if any(getattr(record, "unresolved", False) for record in _pending_cleanup.values()):
            recovery += (" Native membership is unresolved; retained counts are last-native-observed identities, "
                         "not current membership proof. Python inventory scans cannot clear it. "
                         "Recovery must successfully replace the document and native Main; missing Main identity retains uncertainty.")
        failure = ProgramError("Temporary native program data could not be cleaned. New staging and checkpoints "
                               "are blocked. " + recovery,
                               code='CLEANUP_FAILED')
        failure.cleanup_pending = True
        failure.temporary_ids_remaining = sum(len(record) for record in _pending_cleanup.values())
        raise failure


def _hydration_cleanup_error(error, cleanup):
    """CLEANUP_FAILED for a failed hydration whose own cleanup also failed.

    The hydration failure keeps its classification (``cause_code``). Nothing on
    this path reads the live fingerprint, so an unclassified cause is marked
    ``unclassified_cause`` and the job never states ``scene_changed: false``.
    """
    cause = refusal_code(error)
    failure = ProgramError("Native stage hydration failed: " + str(error)[:1000] + "; " + str(cleanup),
                           code='CLEANUP_FAILED', details=dict(refusal_details(error), cause_code=cause))
    for name in ("cleanup_error", "cleanup_pending", "temporary_ids_remaining", "partial_changes_possible"):
        setattr(failure, name, getattr(cleanup, name))
    failure.unclassified_cause = cause in {"UNKNOWN", "INTERNAL_ERROR"}
    return failure


class ProgramStorageMixin(ProgramStageBundleMixin):
    memory_staging = True



    def _remove_bundle(self, bundle):
        path = Path(bundle)
        # Only locally-created dedicated directories are removed; no caller
        # supplied path ever enters this private cleanup boundary.
        if path.parent.name.startswith("angelo-native-preview-") and path.parent.parent == Path(tempfile.gettempdir()):
            shutil.rmtree(path.parent, ignore_errors=True)


    def _remove_ids(self, ids):
        """Remove exactly these blocks under one guard; returns VERIFIED_REMOVAL.

        Identities are captured from the given blocks, so the remover takes
        them directly; the guard's post-removal check proves none remain.
        """
        from .exact_fence import end
        end()  # Deleting IDs changes Main.
        identities = cleanup_identities(ids)
        from .program_commit_scope import for_backend
        scope = for_backend(self)
        return _remove_owned(identities, lambda: scope.delete_stage(tuple(ids)) if scope else self._remove_ids_unchecked(ids))


    def _remove_ids_unchecked(self, ids):
        from .exact_fence import end
        end()  # Deleting IDs changes Main.
        from .program_commit_scope import for_backend
        scope = for_backend(self)
        if scope is not None:
            return scope.delete_stage(tuple(ids))
        bpy = _bpy()
        unique = {}
        for block in ids:
            try:
                if block is not None:
                    unique[block.as_pointer()] = block
            except ReferenceError:
                continue  # Freed by an earlier attempt; never dereference it again.
        # Each individual removal scans Main and remaps users. A batch pays for
        # that once, useful in object/collection-heavy scenes or large stages.
        # Keep the cheaper individual path for small scenes, as measured.
        if (len(getattr(bpy.data, "objects", ())) >= 1024 or
                len(getattr(bpy.data, "collections", ())) >= 256 or len(unique) >= 32):
            owned = [block for block in unique.values() if _main_collection(block) is not None]
            if owned:
                bpy.data.batch_remove(ids=owned)
            return  # The enclosing immutable-identity guard still verifies removal.
        # Objects and scenes first release dependent mesh/material/action users.
        ordered = sorted(unique.values(), key=lambda block: 0 if isinstance(block, bpy.types.Object) else 1 if isinstance(block, bpy.types.Scene) else 2)
        for block in ordered:
            try:
                collection = _main_collection(block)
                if collection is not None:
                    collection.remove(block, do_unlink=True)
            except ReferenceError:
                continue  # Embedded IDs may have been freed with their owner.


    def _remove_stage_ids(self, stage, keep):
        from .exact_fence import end
        end()  # Deleting IDs changes Main.
        ids = list(stage.owned_ids) + (list(stage.context.created_ids) if stage.context else [])
        stage._cleanup_identities = cleanup_identities(ids, getattr(stage, "_cleanup_identities", None))
        identities = {pointer: identity for pointer, identity in stage._cleanup_identities.items() if pointer not in keep}
        from .program_commit_scope import for_backend
        scope = for_backend(self)
        if scope is not None:
            live = _owned_cleanup_ids(stage._cleanup_identities)
            roots = tuple(block for pointer, block in live.items() if pointer not in keep)
            retained = tuple(block for pointer, block in live.items() if pointer in keep)
            _remove_owned(identities, lambda: scope.delete_stage(roots, retained))
        else:
            _remove_owned(identities, lambda: self._remove_ids(list(_owned_cleanup_ids(identities).values())))


    def discard(self, stage):
        self.ensure_main_thread()
        from .exact_fence import end
        end()  # Deleting IDs changes Main.
        if isinstance(stage, SuspendedStage):
            if stage.native_owner is not None:
                from _bpy import _angelo_stage
                _angelo_stage.discard(stage.native_owner)
            elif stage.bundle:
                self._remove_bundle(stage.bundle)
        else:
            try:
                self._remove_stage_ids(stage, keep=set())
            finally:
                if stage.bundle:
                    self._remove_bundle(stage.bundle)
