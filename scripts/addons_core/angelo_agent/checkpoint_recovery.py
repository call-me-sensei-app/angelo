# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Anchored working copies lent only to the private native Sculpt reader.

Metadata and hashes admit bytes, never runtime fidelity. Native preparation
must decode and resolve the complete pair before mandatory safety and Restore.
"""

from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import uuid

from .checkpoint_capture import _OutputResource, _close_output, _hash_descriptor, _raise_cleanup, _stable_file
from .checkpoint_capture_state import decode_record, require_capture_idle
from .checkpoint_filesystem import (_open_file, _private_directory, _read_json,
    assert_directory_identity, directory_info, require_anchored_files, descriptor_witness, file_witness, same_file, sync_directory)
from .checkpoint_policy import _identity
from .checkpoint_seal import verify as verify_seal
from .checkpoint_transactions import _transaction


@dataclass(frozen=True, slots=True)
class _RecoveryBinding:
    capture_id: str
    path: Path
    runtime_name: str
    metadata: str
    directory: tuple


class _RecoveryResource:
    __slots__ = ("stack", "folder", "outputs", "seals", "active", "attempted", "busy")

    def __init__(self):
        self.stack = ExitStack()
        self.folder = None
        self.outputs, self.seals = {}, {}
        self.active = self.attempted = self.busy = False


def _copy_verified(folder, name, destination, expected_size, expected_hash):
    descriptor = _open_file(folder, name, os.O_RDONLY)
    # This source descriptor never escapes this block. fdopen takes ownership;
    # the retained destination is closed once by the recovery owner on failure.
    with os.fdopen(descriptor, "rb", buffering=0) as source:
        opened = descriptor_witness(source.fileno())
        named = file_witness(folder, name)
        if not same_file(opened, named) or opened.st_size != expected_size:
            raise ValueError("The recovery component does not match its declared bytes")
        digest = hashlib.blake2b(digest_size=32)
        remaining = expected_size
        while remaining:
            chunk = source.read(min(remaining, 8 * 1024 * 1024))
            if not chunk:
                raise ValueError("The recovery component became truncated")
            digest.update(chunk)
            remaining -= len(chunk)
            pending = memoryview(chunk)
            while pending:
                count = os.write(destination, pending)
                if count <= 0 or count > len(pending):
                    raise OSError("The recovery working copy could not be written completely")
                pending = pending[count:]
        if (source.read(1) or not _stable_file(opened, descriptor_witness(source.fileno()))
                or not same_file(opened, file_witness(folder, name))):
            raise ValueError("The recovery component changed while being copied")
        if digest.hexdigest() != expected_hash:
            raise ValueError("The recovery component hash does not match the checkpoint")


class CheckpointRecovery:
    """One private working pair; never a public path/descriptor recovery API."""

    __slots__ = ("_binding", "_resource")

    def __init__(self):
        raise TypeError("Recovery resources are created only by prepare")

    @classmethod
    def prepare(cls, store, identity):
        from .checkpoints import CheckpointStore
        if cls is not CheckpointRecovery or type(store) is not CheckpointStore:
            raise TypeError("Recovery needs the exact private checkpoint store")
        require_anchored_files()
        identity = _identity(identity)
        frozen_store = CheckpointStore(Path(store.root), _identity(store.session_id))
        owner = object.__new__(CheckpointRecovery)
        owner._binding = None
        owner._resource = resource = _RecoveryResource()
        try:
            with _transaction(frozen_store.root):
                require_capture_idle(frozen_store, identity)
                with _private_directory(frozen_store.folder(identity)) as source:
                    record = decode_record(_read_json(source, "checkpoint.json"), identity)
                    if (record.get("version") != 4 or record["state"] != "READY"
                            or record["capture_validation"] not in {"VALIDATED", "INTERRUPTED_VALID"}
                            or record.get("storage", "full") != "full"):
                        raise ValueError("The complete full-storage Sculpt checkpoint is unavailable")
                    # Declared hashes alone cannot tell a component of another capture apart.
                    verify_seal(frozen_store.root, record, record["scene_hash"])
                    directory = frozen_store.directory / "recoveries"
                    resource.folder = resource.stack.enter_context(_private_directory(directory, create=True))
                    unique = "recovered-" + str(uuid.uuid4())
                    path = directory / (unique + ".blend")
                    runtime_name = unique + ".runtime"
                    info = directory_info(resource.folder)
                    owner._binding = _RecoveryBinding(identity, path, runtime_name,
                        json.dumps(record, ensure_ascii=False, allow_nan=False), (info.st_dev, info.st_ino))
                    for role, original, name, size, digest in (
                        ("BASE", "scene.blend", path.name, record["scene_bytes"], record["scene_hash"]),
                        ("RUNTIME", "sculpt.runtime", runtime_name, record["sculpt"]["bytes"], record["sculpt"]["hash"]),
                    ):
                        output = resource.outputs[role] = _OutputResource()
                        output.descriptor = _open_file(resource.folder, name, os.O_RDWR | os.O_CREAT | os.O_EXCL)
                        output.opened = os.fstat(output.descriptor)
                        os.set_inheritable(output.descriptor, False)
                        owner._matching(role)
                        _copy_verified(source, original, output.descriptor, size, digest)
                        os.fsync(output.descriptor)
                        verified = descriptor_witness(output.descriptor)
                        if _hash_descriptor(output.descriptor, size) != digest:
                            raise ValueError("The actual recovery working copy failed verification")
                        os.lseek(output.descriptor, 0, os.SEEK_SET)
                        owner._matching(role)
                        if (not _stable_file(verified, descriptor_witness(output.descriptor))
                                or not _stable_file(verified, file_witness(resource.folder, name))):
                            raise ValueError("The hash-verified recovery working copy changed")
                        resource.seals[role] = verified
                    sync_directory(resource.folder)
                    owner._validate()
            return owner
        except BaseException as error:
            try:
                owner.close()
            except BaseException as cleanup:
                raise BaseExceptionGroup("Recovery preparation and cleanup failed", [error, cleanup]) from None
            raise

    @property
    def path(self):
        return self._binding.path

    @property
    def record(self):
        return decode_record(json.loads(self._binding.metadata), self._binding.capture_id)

    def _matching(self, role):
        resource, binding = self._resource, self._binding
        assert_directory_identity(binding.path.parent, resource.folder)
        info = directory_info(resource.folder)
        if (info.st_dev, info.st_ino) != binding.directory:
            raise ValueError("The recovery working directory changed")
        output = resource.outputs[role]
        name = binding.path.name if role == "BASE" else binding.runtime_name
        current, named = descriptor_witness(output.descriptor), file_witness(resource.folder, name)
        if not same_file(output.opened, current) or not same_file(current, named) or current.st_nlink != 1:
            raise ValueError("The recovery component no longer names its retained file")

    def _validate(self):
        resource = self._resource
        if resource is None:
            raise ValueError("The recovery resource is closed")
        for role, seal in resource.seals.items():
            self._matching(role)
            if not _stable_file(seal, descriptor_witness(resource.outputs[role].descriptor)):
                raise ValueError("The verified recovery working copy changed")

    def _native_begin(self):
        resource = self._resource
        if resource is None or resource.attempted or resource.busy:
            raise ValueError("The native recovery resource was already requested")
        resource.attempted = resource.busy = True
        try:
            self._validate()
            resource.active = True
            return (self._binding.capture_id, str(self.path),
                    resource.outputs["BASE"].descriptor, resource.outputs["RUNTIME"].descriptor)
        finally:
            resource.busy = False

    def _native_end(self):
        resource = self._resource
        if resource is None or not resource.active or resource.busy:
            raise ValueError("The native recovery resource is not active")
        resource.active = False

    def close(self):
        resource = self._resource
        if resource is None:
            return
        if resource.active or resource.busy:
            raise ValueError("Cannot close recovery resources borrowed by the native reader")
        self._resource = None
        errors = []
        try:
            for output in resource.outputs.values():
                _close_output(output, errors)
        finally:
            stack, resource.stack = resource.stack, None
            try:
                stack.close()
            except BaseException as error:
                errors.append(("recovery directory close", error))
        _raise_cleanup(errors)
