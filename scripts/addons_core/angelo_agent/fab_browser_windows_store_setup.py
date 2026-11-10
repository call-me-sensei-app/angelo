# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fixed Windows Fab store directories, using the existing native directory owners.

Shared connections/scratch parents keep their existing ACLs. Only newly created app-owned
components and the dedicated Fab leaves require the exact private DACL. No files, credentials,
registry or browser are read here; the Store owns global-lock → private-lock transactions.
"""
import ctypes
import time
from pathlib import Path

from . import private_roots, storage
from .fab_browser_profile_metadata_windows import _SessionAPI, ProfileUnavailable

PRIVATE_LEAF = 'fab-browser-windows'


def _retain(error, owner):
    try:
        error.owner = owner
    except BaseException:
        raise ProfileUnavailable('PROFILE_STORE_SETUP_RETAINED', owner=owner) from error


class _ParentPins:
    """Bounded ordinary app-directory pins; existing shared ACLs are neither rewritten nor weakened.

    Native handles have no DELETE access and SHARE_READ, so another READ ancestor pin is
    compatible while deletion/rename stays denied. Persistent directories are never removed.
    """
    def __new__(cls, *args, **kwargs):
        owner = super().__new__(cls)
        owner._started = owner._ready = owner._complete = owner._released = False
        owner._api = None
        owner._paths, owner._handles, owner._identities = (), [], []
        return owner

    def __init__(self, path, allowed_creation):
        if self._started or self._released:
            raise ProfileUnavailable('PROFILE_STORE_PARENT_REUSED', owner=self)
        self._started = True
        self._api = None
        self._ready = self._complete = self._released = False
        self._sid = self._canonical = None
        self._paths, self._handles, self._identities = (), [], []
        self.path = Path(path)
        if (not self.path.is_absolute() or not self.path.drive or
                str(self.path.drive).startswith('\\\\') or len(self.path.parents) > 64 or
                '..' in self.path.parts):
            raise ProfileUnavailable('PROFILE_STORE_PARENT_PATH', owner=self)
        self._paths = tuple(reversed(self.path.parents)) + (self.path,)
        self._handles = [None] * len(self._paths)
        self._identities = [None] * len(self._paths)
        self._api = _SessionAPI.__new__(_SessionAPI)
        _SessionAPI.__init__(self._api)
        self._ready = True
        self._api.require_local_drive(self.path.anchor)
        self._sid = self._api.current_sid()
        for index, node in enumerate(self._paths):
            output = ctypes.c_void_p()
            self._handles[index] = output  # Before any native acquisition, including a lost return.
            created = False
            if index == 0:
                self._api.open_directory_into(node, output)
            elif node in allowed_creation:
                status = self._api.ensure_persistent_directory_into(
                    self._handles[index - 1], node.name, self._sid, output)
                if status not in ('created', 'opened'):
                    raise ProfileUnavailable('PROFILE_STORE_PARENT_RESULT', owner=self)
                created = status == 'created'
            else:
                self._api._open(self._handles[index - 1], node.name, output,
                                directory=True, delete=False, no_reparse=True)
            self._identities[index] = self._api.information(output)
            if created:
                self._api.verify_acl(output, self._sid)
        self._canonical = self._api.final_path(self._handles[-1])
        self._complete = True
        self.verify()

    def verify(self):
        if not self._complete or self._released:
            raise ProfileUnavailable('PROFILE_STORE_PARENT_UNAVAILABLE', owner=self)
        self._api.known()
        if self._api.current_sid() != self._sid:
            raise ProfileUnavailable('PROFILE_STORE_PARENT_SID_CHANGED', owner=self)
        for handle, expected in zip(self._handles, self._identities):
            if handle is None or self._api.information(handle) != expected:
                raise ProfileUnavailable('PROFILE_STORE_PARENT_CHANGED', owner=self)
        if self._api.final_path(self._handles[-1]) != self._canonical:
            raise ProfileUnavailable('PROFILE_STORE_PARENT_CANONICAL_CHANGED', owner=self)
        return True

    def release(self):
        if self._released:
            return
        # API construction acquires no token/file resources; native work starts only after _ready.
        if self._ready:
            self._api.known()
            for index in reversed(range(len(self._handles))):
                handle = self._handles[index]
                if handle is not None:
                    self._api.close(handle)
                    self._handles[index] = None  # Only after a known native close return.
            self._api.cleanup()
        self._released = True


class _SetupRoots:
    """One setup lifetime retained by the Store; it owns the SAME two persistent root objects.

    Release never deletes a directory, including ones created in this attempt. The Store must
    finish every active profile lease first. Unknown close prevents release of ancestor guards.
    """
    def __new__(cls, *args, **kwargs):
        owner = super().__new__(cls)
        owner._metadata = owner._scratch = None
        owner.directory = owner.binding_directory = owner.scratch = None
        owner._parents, owner._legacy_handles = [None, None], [None]
        owner._started = owner._complete = owner._released = owner._legacy_uncertain = False
        owner._store_owner = None
        return owner

    def __init__(self, directory, scratch_parent):
        if self._started or self._released:
            raise ProfileUnavailable('PROFILE_STORE_SETUP_REUSED', owner=self)
        self._started = True
        self._metadata = self._scratch = None
        self._parents = [None, None]
        self._complete = self._released = False
        self._store_owner = None  # Claimed once by the exact Store; lifetime only, not authority.
        self._legacy_handles = [None]
        self._legacy_uncertain = False
        self.directory = Path(directory)
        self.binding_directory = self.directory / PRIVATE_LEAF
        self.scratch = Path(scratch_parent) / PRIVATE_LEAF
        data = Path(storage.data_directory())
        if (self.directory != Path(storage.connection_directory()) or
                Path(scratch_parent) != data / private_roots.SCRATCH or
                self.binding_directory == self.scratch):
            raise ProfileUnavailable('PROFILE_STORE_SETUP_PATHS', owner=self)
        # This is the actual default app configuration, not a generic mkdir/override engine.
        # A missing configured connections leaf may be created; arbitrary missing ancestors may not.
        allowed = frozenset((data, self.directory, Path(scratch_parent)))
        try:
            for index, path in enumerate((self.directory, Path(scratch_parent))):
                parent = _ParentPins.__new__(_ParentPins)
                self._parents[index] = parent
                _ParentPins.__init__(parent, path, allowed)
            from .fab_browser_profile_session_windows import _PinnedRoot
            self._metadata = _PinnedRoot.__new__(_PinnedRoot)
            _PinnedRoot.__init__(self._metadata, self.binding_directory, create=True, persistent=True)
            self._scratch = _PinnedRoot.__new__(_PinnedRoot)
            _PinnedRoot.__init__(self._scratch, self.scratch, create=True, persistent=True)
            self.binding_directory, self.scratch = self._metadata.path, self._scratch.path
            self._complete = True
            self.verify()
        except BaseException as error:
            _retain(error, self)
            raise

    def verify(self):
        try:
            if not self._complete or self._released:
                raise ProfileUnavailable('PROFILE_STORE_SETUP_UNAVAILABLE', owner=self)
            for parent in self._parents:
                if parent.verify() is not True:
                    raise ProfileUnavailable('PROFILE_STORE_PARENT_UNPROVED', owner=self)
            if (not self._metadata._persistent or not self._scratch._persistent or
                    self._metadata is self._scratch or self._metadata.identity == self._scratch.identity):
                raise ProfileUnavailable('PROFILE_STORE_SETUP_ROLE', owner=self)
            if self._metadata.verify() is not True or self._scratch.verify() is not True:
                raise ProfileUnavailable('PROFILE_STORE_SETUP_UNPROVED', owner=self)
            return True
        except BaseException as error:
            _retain(error, self)
            raise

    def refuse_legacy(self):
        """Startup-only observation, under caller's global D → private P locks.

        No migration/deletion/fresh profile is allowed beside old-layout evidence. Do NOT call
        this on finish: current owned download Landings may legitimately use the direct S family.
        Existing registry parsing/unknown handling stays in private_roots, not a second registry.
        """
        self.verify()
        if self._legacy_uncertain:
            raise ProfileUnavailable('PROFILE_STORE_LEGACY_PROBE_UNPROVED', owner=self)
        directory, scratch = self._parents
        from .fab_browser_profile_session_windows import BINDING
        output = ctypes.c_void_p()
        self._legacy_handles[0] = output
        self._legacy_uncertain = True  # Before native acquisition, including a lost return.
        try:
            present = directory._api.file_into(directory._handles[-1], BINDING, directory._sid,
                output, create=False, write=False, shared=True, delete=False)
            directory._api.known()
            if present is not True and present is not False:
                raise ProfileUnavailable('PROFILE_STORE_LEGACY_PROBE_RESULT', owner=self)
            if (present and output.value is None) or (not present and output.value is not None):
                raise ProfileUnavailable('PROFILE_STORE_LEGACY_PROBE_RESULT', owner=self)
            if output.value is not None:
                directory._api.close(output)
            self._legacy_handles[0] = None
            self._legacy_uncertain = False
            if present:
                raise ProfileUnavailable('PROFILE_LEGACY_LAYOUT_REQUIRES_RECOVERY', owner=self)
            try:
                records = private_roots.retained_profiles(self.directory)
            except private_roots.RetainedUnknown as error:
                raise ProfileUnavailable('PROFILE_PRIVACY_REGISTRY_UNKNOWN', owner=self) from error
            for value in records:
                path = Path(value)
                if path.parent == scratch.path and path.name.startswith(private_roots.FAB_BROWSER_PREFIX):
                    raise ProfileUnavailable('PROFILE_LEGACY_LAYOUT_REQUIRES_RECOVERY', owner=self)
            deadline = time.monotonic() + 2
            count = 0
            for name in scratch._api.entries(scratch._handles[-1], deadline):
                count += 1
                if count > 4096 or time.monotonic() >= deadline:
                    raise ProfileUnavailable('PROFILE_STORE_LEGACY_SCAN_BOUND', owner=self)
                if name.startswith(private_roots.FAB_BROWSER_PREFIX):
                    raise ProfileUnavailable('PROFILE_LEGACY_LAYOUT_REQUIRES_RECOVERY', owner=self)
            self.verify()
        except BaseException as error:
            _retain(error, self)
            raise

    def release(self):
        if self._released:
            return
        if self._legacy_uncertain:
            raise ProfileUnavailable('PROFILE_STORE_LEGACY_PROBE_UNPROVED', owner=self)
        # A failed child close can still borrow parent guards. Stop on that failure rather than
        # closing ancestors. No catch-and-continue cleanup, path deletion or ACL changes.
        try:
            for root in (self._scratch, self._metadata):
                if root is not None:
                    root.release()
            for parent in reversed(self._parents):
                if parent is not None:
                    parent.release()
            self._released = True
        except BaseException as error:
            _retain(error, self)
            raise


def prepare_roots(directory, scratch_parent):
    """Provision only default Windows store topology; caller adopts this exact composite owner.

    No global metadata lock is taken here: a missing global directory is established first.
    Native OPEN_IF + verified pinned identities handle concurrent setup. No binding is read or
    registry updated until the Store subsequently takes existing global → private binding locks.
    """
    owner = _SetupRoots.__new__(_SetupRoots)
    try:
        _SetupRoots.__init__(owner, directory, scratch_parent)
        return owner
    except BaseException as error:
        _retain(error, owner)
        raise
