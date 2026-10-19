# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private checkpoint destinations and publication for the native capture owner.

Resources have one synchronous, quiescent owner. Creation observations are
historical metadata, not crash-continuity, cleanup, READY or recovery authority.
Native writers may borrow a descriptor once and must never close or retain it.
"""

from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import time

from .checkpoint_capture_pins import acquire_capture_pin, abandon_capture_pin
from .checkpoint_capture_records import file_identity, output_names, validate_record_bytes, validate_transition
from .checkpoint_filesystem import (_file_info, _open_file, _private_child, _private_directory, _read_json,
    _read_metadata_bytes, _write_json, _unlink_file, assert_directory_identity, directory_info, entry_info, iter_names, require_anchored_files, descriptor_witness, file_witness,
    make_directory, publish_new_file, same_file, sync_directory)
from .checkpoint_policy import EVERY_REQUEST, _identity
from .checkpoint_transactions import _transaction
from .checkpoint_publication import CheckpointPublicationMixin


@dataclass(frozen=True, slots=True)
class _Binding:
    root: Path
    session_id: str
    capture_id: str
    boundary: int
    path: Path
    sealed: tuple
    directory: tuple


class _OutputResource:
    __slots__ = ("descriptor", "opened", "acknowledged", "borrow_attempted")

    def __init__(self):
        self.descriptor = self.opened = None
        self.acknowledged = self.borrow_attempted = False


class _CaptureResource:
    __slots__ = ("stack", "folder", "pin", "outputs", "declared", "create_attempts",
                 "declare_attempts", "ledger", "failed", "busy", "native_active", "native_attempted",
                 "publish_attempted", "finish_attempted", "published", "verified_outputs", "sealed_outputs",
                 "components", "phase", "started", "validated")

    def __init__(self):
        self.stack = ExitStack()
        self.folder = self.pin = self.ledger = None
        self.outputs = {}
        self.declared = set()
        self.create_attempts = set()
        self.declare_attempts = set()
        self.failed = self.busy = False
        self.native_active = self.native_attempted = False
        self.publish_attempted = self.finish_attempted = False
        self.published = set()
        self.verified_outputs = {}
        self.sealed_outputs = {}
        self.components = None
        self.phase = "reserved"
        self.started = time.monotonic()
        self.validated = None  # The last fresh read's exact record bytes and validated result.


def _native_facts(capture, owner):
    # The native function checks its non-constructible capability and retained
    # owner/descriptor bindings. A caller-supplied dictionary grants no authority.
    import _bpy
    facts = _bpy._angelo_checkpoint.capture_facts(capture, owner)
    keys = {"capture_id", "scene_bytes", "runtime_bytes", "owner_count", "format_version", "post_state"}
    if (type(facts) is not dict or set(facts) != keys or facts["capture_id"] != owner.capture_id
            or any(type(facts[key]) is not int or facts[key] < 0 for key in
                   ("scene_bytes", "runtime_bytes", "owner_count", "format_version"))
            or facts["scene_bytes"] == 0 or facts["post_state"] not in {"pending", "complete", "failed"}):
        raise ValueError("Native checkpoint verification returned invalid facts")
    if ((facts["owner_count"] > 0 and (facts["runtime_bytes"] == 0 or facts["format_version"] != 1))
            or (facts["owner_count"] == 0 and (facts["runtime_bytes"] or facts["format_version"]))):
        raise ValueError("Native checkpoint components do not agree")
    return facts


def _stable_file(before, after):
    return (same_file(before, after)
        and all(getattr(before, key) == getattr(after, key) for key in
                ("st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink"))
        and getattr(before, "st_change_time", None) == getattr(after, "st_change_time", None))


def _component_binding(record):
    """Immutable primitives only; never alias a metadata or returned receipt dict."""
    sculpt = record.get("sculpt")
    return (record.get("scene_bytes"), record.get("scene_hash"), record.get("storage", "full"),
            tuple(sorted(sculpt.items())) if sculpt is not None else None)


def _hash_descriptor(descriptor, expected_size):
    """Bounded reads of the retained file, preserving its synchronous writer offset."""
    before = descriptor_witness(descriptor)
    if before.st_size != expected_size or before.st_nlink != 1:
        raise ValueError("The verified capture component changed before hashing")
    saved_offset = os.lseek(descriptor, 0, os.SEEK_CUR)
    digest = hashlib.blake2b(digest_size=32)
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        remaining = expected_size
        while remaining:
            data = os.read(descriptor, min(remaining, 8 * 1024 * 1024))
            if not data:
                raise ValueError("The verified capture component became truncated")
            digest.update(data)
            remaining -= len(data)
        if os.read(descriptor, 1) or not _stable_file(before, descriptor_witness(descriptor)):
            raise ValueError("The verified capture component changed while hashing")
        return digest.hexdigest()
    finally:
        os.lseek(descriptor, saved_offset, os.SEEK_SET)


def _close_output(resource, errors):
    descriptor, resource.descriptor = resource.descriptor, None
    if descriptor is None:
        return
    # An unexposed acquisition whose initial fstat failed is still ours. Once
    # observed, reject a lost/reused descriptor rather than close a foreign file.
    if resource.opened is not None:
        try:
            if not same_file(resource.opened, os.fstat(descriptor)):
                raise ValueError("The capture output descriptor was replaced")
        except BaseException as error:
            errors.append(("output descriptor identity", error))
            return
    try:
        os.close(descriptor)
    except BaseException as error:
        errors.append(("output descriptor close", error))  # Never retry ambiguous close.


def _raise_cleanup(errors):
    if errors:
        raise BaseExceptionGroup("Capture resource cleanup failed", [
            BaseExceptionGroup(phase, [error]) for phase, error in errors])


class OwnedOutput:
    """A retained resource, not a serializable receipt or a public output API."""

    __slots__ = ("_owner", "_role", "_resource")

    def __init__(self):
        raise TypeError("Capture outputs are created only by their live owner")

    def borrow_descriptor(self):
        """Borrow once for the synchronous private writer; caller never closes it."""
        owner, role, output = self._owner, self._role, self._resource
        with owner._operation() as resource:
            if resource.outputs.get(role) is not self or not output.acknowledged:
                raise ValueError("The output has no acknowledged live owner")
            if output.borrow_attempted:
                raise ValueError("The output writer reference was already requested")
            output.borrow_attempted = True
            try:
                with _transaction(owner._binding.root):
                    owner._fresh(resource)
                    owner._matching_output(resource, role, output)
                    current = descriptor_witness(output.descriptor)
                    if current.st_size or os.lseek(output.descriptor, 0, os.SEEK_CUR):
                        raise ValueError("The output is no longer a fresh empty writer destination")
                return output.descriptor
            except BaseException:
                resource.failed = True
                raise


class CaptureOwner(CheckpointPublicationMixin):
    """Own private destinations across callbacks, without holding a profile lock.

    Call abandon in finally. It closes resources once, leaving metadata, pin
    names and partial outputs for their existing conservative consumers.
    """

    __slots__ = ("_binding", "_resource", "_cleanup_errors")

    def __init__(self):
        raise TypeError("Capture owners are created only by reserve")

    @classmethod
    def reserve(cls, store, original_path="", *, capture_mode=EVERY_REQUEST, purpose="request"):
        # A local import avoids a store/module initialization cycle. Copy its
        # binding now; later mutation of the caller's store cannot redirect I/O.
        from .checkpoints import CheckpointStore
        if cls is not CaptureOwner or type(store) is not CheckpointStore:
            raise TypeError("Capture reservation needs the private checkpoint store")
        require_anchored_files()
        root, session_id = Path(store.root), _identity(store.session_id)
        frozen_store = CheckpointStore(root, session_id)
        if frozen_store.root != root:
            raise ValueError("The capture profile binding changed")
        owner = object.__new__(CaptureOwner)
        owner._binding = None
        owner._resource = resource = _CaptureResource()
        owner._cleanup_errors = ()
        try:
            with _transaction(root):
                record = frozen_store._reserve_capture_record(original_path, capture_mode, purpose)
                path = frozen_store.folder(record["id"])
                with _private_directory(frozen_store.directory, create=True) as parent:
                    make_directory(parent, record["id"])
                    resource.folder = resource.stack.enter_context(_private_child(parent, record["id"]))
                # A UUID collision or unproved preexisting folder is never adopted.
                with closing(iter_names(resource.folder)) as names:
                    if next(names, None) is not None:
                        raise ValueError("The capture destination already contains files")
                observed = directory_info(resource.folder)
                owner._binding = _Binding(root, session_id, record["id"], record["boundary"], path,
                    tuple((key, record[key]) for key in ("id", "boundary", "created", "original_path", "capture_mode", "purpose")),
                    (observed.st_dev, observed.st_ino))
                resource.pin = acquire_capture_pin(root, session_id, record["id"])
                assert_directory_identity(path, resource.folder)
                _write_json(resource.folder, "checkpoint.json", record)
                owner._fresh(resource)
            return owner
        except BaseException as error:
            try:
                owner.abandon()
            except BaseException as cleanup_error:
                raise BaseExceptionGroup("Capture reservation and cleanup failed", [error, cleanup_error]) from None
            raise

    @property
    def capture_id(self):
        return self._binding.capture_id

    @property
    def cleanup_errors(self):
        return self._cleanup_errors

    def _native_begin(self):
        """Private bridge entry; native owns the interval until _native_end."""
        with self._operation() as resource:
            if resource.native_attempted:
                raise ValueError("The native capture was already attempted")
            resource.native_attempted = True
            with _transaction(self._binding.root):
                self._fresh(resource)
            resource.native_active = True
            return self.capture_id, str(self._binding.path / "scene.blend")

    def _native_output(self, role):
        """Called outside serializer hooks by the live native bridge only."""
        resource = self._resource
        if resource is None or not resource.native_active or resource.publish_attempted:
            raise ValueError("There is no active native capture destination interval")
        self.declare_output(role)
        return self.create_output(role).borrow_descriptor()

    def _native_end(self):
        """The native bridge calls this after releasing its WM capture owner."""
        resource = self._resource
        if resource is None or not resource.native_active or resource.busy:
            raise ValueError("The native capture interval is not available for release")
        resource.native_active = False

    def _native_purpose(self):
        """Original purpose only; it selects native witness cost, not authority."""
        with self._operation() as resource, _transaction(self._binding.root):
            if not resource.native_active:
                raise ValueError("The capture purpose requires its live native interval")
            self._fresh(resource)
            return dict(self._binding.sealed)["purpose"]

    @contextmanager
    def _operation(self, *, allow_failed=False):
        resource = self._resource
        if resource is None:
            raise ValueError("The capture owner is closed")
        if resource.busy:
            raise ValueError("The capture owner is already in use")
        if resource.failed and not allow_failed:
            raise ValueError("The capture attempt failed; no writer handoff or retry is allowed")
        resource.busy = True
        try:
            yield resource
        finally:
            resource.busy = False

    def _fresh(self, resource, *, require_ledger=True):
        binding = self._binding
        assert_directory_identity(binding.path, resource.folder)
        observed = directory_info(resource.folder)
        if (observed.st_dev, observed.st_ino) != binding.directory:
            raise ValueError("The capture directory identity changed")
        record, resource.validated = validate_record_bytes(
            _read_metadata_bytes(resource.folder, "checkpoint.json"), binding.capture_id, resource.validated)
        expected_state = "READY" if resource.phase == "published" else "RESERVED"
        if (record["state"] != expected_state or record["capture_validation"] != "PENDING"
                or any(record.get(key) != value for key, value in binding.sealed)):
            raise ValueError("The capture reservation binding changed")
        if require_ledger and record.get("capture_outputs") != resource.ledger:
            raise ValueError("The capture output ledger changed outside its owner")
        if resource.phase == "published" and _component_binding(record) != resource.components:
            raise ValueError("The published capture component declarations changed")
        return record

    def _matching_output(self, resource, role, output):
        assert_directory_identity(self._binding.path, resource.folder)
        named = file_witness(resource.folder, output_names(role)[role in resource.published])
        current = descriptor_witness(output.descriptor)
        if (output.opened is None or not same_file(output.opened, current)
                or not _stable_file(current, named) or current.st_nlink != 1):
            raise ValueError("The capture output no longer names its exact retained file")
        return current

    def declare_output(self, role):
        output_names(role)
        with self._operation() as resource:
            if role in resource.declare_attempts:
                raise ValueError("The output declaration was already attempted")
            resource.declare_attempts.add(role)
            try:
                with _transaction(self._binding.root):
                    current = self._fresh(resource)
                    ledger = current.get("capture_outputs")
                    if ledger is None:
                        ledger = {"version": 1, "directory": file_identity(*self._binding.directory), "roles": {}}
                    roles = dict(ledger["roles"])
                    roles[role] = {"created": None}
                    candidate = dict(current, capture_outputs=dict(ledger, roles=roles))
                    clean = validate_transition(current, candidate, kind="declare_output")
                    _write_json(resource.folder, "checkpoint.json", clean)
                    assert_directory_identity(self._binding.path, resource.folder)
                resource.ledger = clean["capture_outputs"]
                resource.declared.add(role)
            except BaseException:
                resource.failed = True
                raise

    def create_output(self, role):
        output_names(role)
        with self._operation() as resource:
            if role not in resource.declared:
                raise ValueError("The output declaration has not been acknowledged")
            if role in resource.create_attempts:
                raise ValueError("The output creation was already attempted")
            resource.create_attempts.add(role)
            output = object.__new__(OwnedOutput)
            output._owner, output._role, output._resource = self, role, _OutputResource()
            resource.outputs[role] = output
            owned = output._resource
            try:
                with _transaction(self._binding.root):
                    current = self._fresh(resource)
                    owned.descriptor = _open_file(resource.folder, output_names(role)[0],
                                                 os.O_RDWR | os.O_CREAT | os.O_EXCL)
                    owned.opened = os.fstat(owned.descriptor)
                    os.set_inheritable(owned.descriptor, False)
                    self._matching_output(resource, role, owned)
                    os.fsync(owned.descriptor)
                    sync_directory(resource.folder)
                    roles = dict(current["capture_outputs"]["roles"])
                    roles[role] = {"created": file_identity(owned.opened.st_dev, owned.opened.st_ino)}
                    candidate = dict(current, capture_outputs=dict(current["capture_outputs"], roles=roles))
                    clean = validate_transition(current, candidate, kind="record_output")
                    _write_json(resource.folder, "checkpoint.json", clean)
                    self._matching_output(resource, role, owned)
                resource.ledger = clean["capture_outputs"]
                owned.acknowledged = True
                return output
            except BaseException:
                resource.failed = True
                raise

    def observe_outputs(self):
        """Measure the two declared slots; never infer creation or modify metadata."""
        with self._operation(allow_failed=True) as resource, _transaction(self._binding.root):
            current = self._fresh(resource, require_ledger=False)
            roles = current.get("capture_outputs", {}).get("roles", {})
            observations = {}
            for role, slot in roles.items():
                names = {}
                output = resource.outputs.get(role)
                owned = output._resource if output is not None else None
                live = None
                if owned is not None and owned.descriptor is not None and owned.opened is not None:
                    try:
                        observed = os.fstat(owned.descriptor)
                        if same_file(owned.opened, observed) and observed.st_nlink == 1:
                            live = observed
                    except OSError:
                        pass  # A missing live witness grants no replacement authority.
                for name in output_names(role):
                    try:
                        info = _file_info(resource.folder, name)
                    except FileNotFoundError:
                        names[name] = {"present": False}
                    else:
                        names[name] = {"present": True, "bytes": info.st_size,
                            "identity": file_identity(info.st_dev, info.st_ino),
                            "matches_live_output": live is not None and same_file(live, info)}
                observations[role] = {"creation_observed": slot["created"] is not None,
                    "writer_acknowledged": owned is not None and owned.acknowledged and not resource.failed,
                    "names": names}
            return {"capture_id": self.capture_id, "failed": resource.failed,
                    "ledger_changed": current.get("capture_outputs") != resource.ledger, "outputs": observations}

    def abandon(self):
        """Consume resources before I/O; close all once, without metadata or unlink."""
        resource = self._resource
        if resource is None:
            return
        if resource.busy:
            raise ValueError("Cannot abandon a capture while its operation is in progress")
        if resource.native_active:
            raise ValueError("Cannot abandon a capture while the native writer owns it")
        self._resource = None
        errors = []
        try:
            for output in resource.outputs.values():
                _close_output(output._resource, errors)
        finally:
            try:
                pin, resource.pin = resource.pin, None
                if pin is not None:
                    try:
                        abandon_capture_pin(pin)
                    except BaseException as error:
                        errors.append(("capture pin abandonment", error))
            finally:
                stack, resource.stack = resource.stack, None
                try:
                    stack.close()
                except BaseException as error:
                    errors.append(("capture directory close", error))
        self._cleanup_errors = tuple(errors)
        _raise_cleanup(errors)


def capture_checkpoint(store, original_path="", *, capture_mode=EVERY_REQUEST, purpose="request", retain_native=False):
    """Complete one native capture lifecycle; failures never become request success."""
    if retain_native and purpose != "safety":
        raise ValueError("Only mandatory safety captures retain native resources")
    import _bpy
    native = _bpy._angelo_checkpoint
    owner = CaptureOwner.reserve(store, original_path, capture_mode=capture_mode, purpose=purpose)
    capture, record, errors = None, None, []
    post_attempted = False
    try:
        capture = native.capture(owner)
        owner.publish(capture)
        post_attempted = True
        try:
            native.capture_post(capture, owner)
        except BaseException as error:
            errors.append(("native POST", error))
        try:
            record = owner.finish(capture)
        except BaseException as error:
            errors.append(("capture terminal record", error))
    except BaseException as error:
        errors.append(("capture publication", error))
        if capture is not None and not post_attempted:
            try:
                native.capture_abort(capture, owner)
            except BaseException as abort_error:
                errors.append(("native SAVE_POST_FAIL", abort_error))
    finally:
        if capture is not None:
            try:
                native.capture_close(capture)
            except BaseException as error:
                errors.append(("native capture release", error))
        if not retain_native or errors:
            if capture is not None:
                try:
                    native.capture_release(capture)
                except BaseException as error:
                    errors.append(("native capture resource release", error))
            try:
                owner.abandon()
            except BaseException as error:
                errors.append(("capture resource release", error))
    if errors:
        if len(errors) == 1:
            raise errors[0][1]
        raise BaseExceptionGroup("Checkpoint capture did not complete", [
            BaseExceptionGroup(phase, [error]) for phase, error in errors])
    return (record, capture) if retain_native else record
