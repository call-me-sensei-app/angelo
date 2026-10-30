# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host-only project continuity, separate from ephemeral native document IDs.

Receipts are runtime objects, never tool parameters or rehydrated JSON. A saved
project requires exact path AND file-content continuity to rebind on restart.
An explicit same-project snapshot recovery may retain the current live brief,
including an unsaved project's brief, but cannot revive one from another launch.
No receipt grants scene, file, provider, or approval authority.
"""

import hashlib
import os
import stat
import threading
from dataclasses import dataclass, field
from pathlib import Path

from . import live_windows
from .memory_evidence import scope_token
from .checkpoint_capture_state import require_capture_idle, scene_usable
from .checkpoint_transactions import _transaction

MAX_PROJECT_BYTES = 64 * 1024 * 1024
_ISSUER = object()


def _stamp(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


@dataclass(frozen=True)
class LoadedProjectScope:
    """Native attestation captured on the UI thread, safe to pass to file worker."""
    profile: str
    document: str
    _native_path: str = field(repr=False)
    _issuer: object = field(default=None, repr=False, compare=False)

    def valid_for(self, document, profile_id):
        return (self._issuer is _ISSUER and self.document == scope_token(document)
                and self.profile == scope_token(profile_id))


def capture_loaded_project(window, document, profile_id):
    """Main-thread native check, not a caller's claim about an arbitrary path.

    No hashing or file reads. Capture at an actual load/save/confirmation
    lifecycle boundary, then give this immutable attestation to the file-only
    worker. Recovery uses its pre-open RestoreProjectScope, not a temp filepath.
    """
    if threading.current_thread() is not threading.main_thread():
        raise ValueError("Capture the loaded project on Blender's main thread")
    import bpy
    if (not isinstance(window, bpy.types.Window) or not isinstance(document, (tuple, list)) or len(document) != 2
            or type(document[0]) is not str or type(document[1]) is not int
            or document[1] != window.scene.session_uid
            or live_windows.find(bpy.context.window_manager, window.as_pointer(), live=False) is None):
        raise ValueError("Loaded-project scope must match a live native window and scene identity")
    path = str(Path(bpy.data.filepath).resolve()) if bpy.data.filepath else ""
    return LoadedProjectScope(scope_token(profile_id), scope_token(document), path, _ISSUER)


@dataclass(frozen=True)
class SavedProjectReceipt:
    project: str
    revision: str
    profile: str
    document: str
    _path: str = field(repr=False)
    _file_stamp: tuple = field(repr=False)
    _issuer: object = field(default=None, repr=False, compare=False)
    _loaded_project: LoadedProjectScope = field(default=None, repr=False, compare=False)

    def valid_for(self, document, profile_id):
        if (self._issuer is not _ISSUER or self.document != scope_token(document)
                or self.profile != scope_token(profile_id) or not isinstance(self._loaded_project, LoadedProjectScope)
                or not self._loaded_project.valid_for(document, profile_id) or self._loaded_project._native_path != self._path):
            return False
        # Through an open descriptor, as at admission: on Windows os.stat's st_ctime is the creation time but
        # os.fstat's is the last change, so a rewritten file never matched a stamp taken from the other.
        try:
            fd = os.open(self._path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        except OSError:
            return False
        try:
            info = os.fstat(fd)
        finally:
            os.close(fd)
        return stat.S_ISREG(info.st_mode) and _stamp(info) == self._file_stamp


def admit_saved_project(path, document, profile_id, *, loaded_project=None):
    """Check an ACTUALLY loaded/saved .blend path at a host lifecycle boundary.

    The path must match a runtime-only capture_loaded_project attestation of
    bpy.data.filepath, not a model/memory path. File I/O is background-safe; do not call on
    every draw/tick. Over-budget/changing/unknown files fail closed for automatic
    continuity. The receipt retains a private path only until runtime teardown.
    """
    if not isinstance(path, (str, Path)) or not str(path) or document is None or profile_id is None:
        raise ValueError("Saved-project continuity requires a real saved project and current host scope")
    target = Path(path).resolve(strict=True)
    if (not isinstance(loaded_project, LoadedProjectScope) or not loaded_project.valid_for(document, profile_id)
            or loaded_project._native_path != str(target)):
        raise ValueError("Saved-project admission needs the actual native loaded-document attestation")
    if target.suffix.lower() != ".blend":
        raise ValueError("Saved-project continuity requires an actual Blender project")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(target, flags)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or not 12 <= info.st_size <= MAX_PROJECT_BYTES:
            raise ValueError("Saved project is not a regular file within the continuity verification budget")
        header = handle.read(12)
        if not (header.startswith(b"BLENDER") or header.startswith(b"\x1f\x8b") or header.startswith(b"\x28\xb5\x2f\xfd")):
            raise ValueError("Saved project has no recognized Blender file header")
        digest = hashlib.sha256(header)
        remaining = info.st_size - len(header)
        while remaining:
            chunk = handle.read(min(65536, remaining))
            if not chunk:
                raise ValueError("Saved project changed while verifying continuity")
            digest.update(chunk)
            remaining -= len(chunk)
        if handle.read(1) or _stamp(os.fstat(handle.fileno())) != _stamp(info):
            raise ValueError("Saved project changed while verifying continuity")
    return SavedProjectReceipt(scope_token(["saved-project-path", str(target)]), "sha256:" + digest.hexdigest(),
                               scope_token(profile_id), scope_token(document), str(target), _stamp(info), _ISSUER, loaded_project)


@dataclass(frozen=True)
class RestoreProjectScope:
    """One human-selected recovery, captured from a currently bound controller."""
    checkpoint_id: str
    document: str
    profile: str
    _owner: object = field(repr=False, compare=False)
    _issuer: object = field(default=None, repr=False, compare=False)


def admit_restore_scope(controller, checkpoint_id, *, source_controller=None):
    """Host-only: call before opening the explicitly selected ready snapshot. A sibling chat shares the source's scope
    when both are bound to the same load of the open document (any of its scenes: the scope binds to the target's
    own document, ``restore_memory``); a reopen of the same document rebinds its chats to the new load
    (``document_identity``), so a chat from before the reopen is no other document."""
    from .document_scene import same_file
    source = source_controller or controller
    if (not same_file(source.document, controller.document)
            or source.store.directory.resolve() != controller.store.directory.resolve()):
        raise ValueError("Cross-chat recovery scope requires the same current native document and profile")
    with _transaction(source.checkpoints.root):
        record = source.checkpoints.load(checkpoint_id)
        require_capture_idle(source.checkpoints, checkpoint_id)
        if not scene_usable(record) or controller.document is None:
            raise ValueError("A same-project recovery needs a ready snapshot and current host document")
        if record.get("version") == 4 and not source.checkpoints.availability(checkpoint_id)["available"]:
            raise ValueError("The complete Sculpt checkpoint reader and component pair are unavailable")
    return RestoreProjectScope(record["id"], scope_token(controller.document),
                               scope_token(str(controller.store.directory.resolve())), controller._project_owner, _ISSUER)


def rebind_review(controller):
    """Exact public notes for the human confirmation dialog, not live evidence."""
    from . import task_memory
    memory = task_memory.clean_memory(controller.state.get("task_memory"), profile_id=str(controller.store.directory.resolve()))
    return {key: memory[key] for key in ("revision", "brief", "references", "assets", "planned_steps")}


def confirm_rebind_memory(controller, loaded_project, expected_revision):
    """Host-only human confirmation after reviewing these exact public notes.

    This is NOT model-callable and does not accept JSON ``approved`` flags.
    UI supplies an actual freshly captured native attestation after explicit
    confirmation. Unknown/large/changed files can retain notes without claiming
    automatic file continuity. All retained evidence/source receipts stay stale.
    """
    from . import task_memory
    profile = str(controller.store.directory.resolve())
    if (not isinstance(loaded_project, LoadedProjectScope) or not loaded_project.valid_for(controller.document, profile)
            or controller.busy or controller.document_invalid or type(expected_revision) is not int
            or expected_revision != rebind_review(controller)["revision"]):
        raise ValueError("Review the current exact brief and explicitly confirm it for this loaded native document")
    state = task_memory.on_restore(controller.state, controller.document, reason="restart", profile_id=profile,
                                    evidence_id="host:human-project-rebind")
    # An explicit transfer is not proof of exact saved-file continuity.
    state["task_memory"]["scope"].update(project=None, project_revision=None)
    state["messages"] = list(state["messages"]) + [{"role": "user", "text":
        "Use this prior public task brief for the same project I explicitly confirmed. Notes only; this does not approve scene edits, paid use, replay, or tool/file access.",
        "request_kind": "task_memory_rebind"}]
    return state


def bind_saved_memory(state, document, profile_id, receipt):
    """Return (new_state, rebound). No state/receipt JSON can assert continuity."""
    from . import task_memory
    if not isinstance(receipt, SavedProjectReceipt) or not receipt.valid_for(document, profile_id):
        raise ValueError("Project binding needs a current host-admitted saved-project receipt")
    memory = task_memory.clean_memory(state.get("task_memory"), profile_id=profile_id)
    scope = memory["scope"]
    current = scope_token(document)
    same_document = scope["document"] in {None, current}
    same_saved_revision = scope.get("project") == receipt.project and scope.get("project_revision") == receipt.revision
    if not same_document and not same_saved_revision:
        return dict(state, task_memory=memory), False
    scope.update(project=receipt.project, project_revision=receipt.revision)
    if scope["document"] != current:
        updated = task_memory.on_restore(dict(state, task_memory=memory), document, reason="restart", profile_id=profile_id)
    else:
        updated = dict(state, task_memory=memory)
    return updated, True


def restore_memory(controller, document, checkpoint_id, project_scope):
    """Rebind only verified saved continuity or an admitted live recovery scope."""
    from . import task_memory
    profile = str(controller.store.directory.resolve())
    state = controller.state
    memory = task_memory.clean_memory(state.get("task_memory"), profile_id=profile)
    valid_live = (isinstance(project_scope, RestoreProjectScope) and project_scope._issuer is _ISSUER
                  and project_scope._owner is controller._project_owner and project_scope.checkpoint_id == checkpoint_id
                  and project_scope.profile == scope_token(profile) and project_scope.document == memory["scope"]["document"])
    if isinstance(project_scope, SavedProjectReceipt):
        state, valid_saved = bind_saved_memory(state, document, profile, project_scope)
    else:
        valid_saved = False
    rebound = valid_live or valid_saved
    state = task_memory.on_restore(state, document if rebound else None,
                                   reason="restore" if rebound else "document_change", profile_id=profile,
                                   evidence_id="host:restore:" + str(checkpoint_id))
    return state, rebound
