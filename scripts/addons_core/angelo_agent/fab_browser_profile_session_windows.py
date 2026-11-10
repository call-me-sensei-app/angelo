# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private Windows browser profile retention; binding records never establish authentication.
Dormant until the browser owner integrates the exact drain/path binding and Windows native acceptance passes.
The existing private_roots registry and connections.lock first-byte protocol remain the only shared owners.
"""
import ctypes
from contextlib import contextmanager
import json
from pathlib import Path
import re
import threading
import time
import uuid

from . import private_roots, storage
from .child_process import ChildProcess
from .fab_browser_download_file_windows import _LandingProfile
from .fab_browser_profile_metadata_windows import _SessionAPI, ProfileUnavailable, _LIMIT

BINDING = 'fab-browser-profile-windows.json'
PROFILE_LOCK = '.angelo-fab-profile.lock'
OWNER_MARKER = '.angelo-fab-profile-owner'


def _retain(error, owner):
    try:
        error.owner = owner
    except BaseException:
        raise ProfileUnavailable('PROFILE_OWNER_RETAINED', owner=owner) from error


class _PinnedRoot(_LandingProfile):
    """Reuse accepted held-tree verification/deletion, replacing ONLY root acquisition."""
    def __init__(self, path, *, create, api=None, persistent=False):
        self._lock = threading.RLock()
        self._api = None
        self._persistent, self._released = persistent, False
        self.path, self.identity, self._sid = Path(path), None, None
        self._deleted = self._marked = False
        self._paths, self._handles = (), []
        self._cleanup_handles = [None] * 65
        if (type(persistent) is not bool or not self.path.is_absolute() or
                not self.path.drive or str(self.path.drive).startswith('\\\\') or
                len(self.path.parents) > 64):
            raise ProfileUnavailable('PROFILE_ROOT_UNSAFE', owner=self)
        self._paths = tuple(reversed(self.path.parents))
        self._handles = [None] * (len(self._paths) + 1)
        try:
            if api is None:
                self._api = _SessionAPI.__new__(_SessionAPI)
                self._api.__init__()
            else:
                self._api = api
            self._api.require_local_drive(self.path.anchor)  # Refuse known remote input BEFORE filesystem opens.
            self._sid = self._api.current_sid()
            for i, ancestor in enumerate(self._paths):
                self._handles[i] = ctypes.c_void_p()
                self._api.open_directory_into(ancestor, self._handles[i])
                self._api.information(self._handles[i])
            self._handles[-1] = ctypes.c_void_p()
            if persistent and create:
                observed = self._api.ensure_persistent_directory_into(self._handles[-2], self.path.name,
                                                                      self._sid, self._handles[-1])
                if type(observed) is not str or observed not in ('created', 'opened'):
                    raise ProfileUnavailable('PROFILE_PERSISTENT_OPEN_UNPROVED', owner=self)
            elif persistent:
                self._api._open(self._handles[-2], self.path.name, self._handles[-1],
                                directory=True, delete=False, no_reparse=True)
            elif create:
                if not self._api.create_directory_into(self._handles[-2], self.path.name, self._sid, self._handles[-1]):
                    raise ProfileUnavailable('PROFILE_COLLISION', owner=self)
            else:
                self._api.open_relative_into(self._handles[-2], self.path.name, self._handles[-1])
            self.identity = self._api.information(self._handles[-1])
            self._api.verify_acl(self._handles[-1], self._sid)
            self.path = Path(self._api.final_path(self._handles[-1]))
        except BaseException as error:
            _retain(error, self)
            raise

    def verify(self):
        if self._released:
            raise ProfileUnavailable('PROFILE_ROOT_CLOSED', owner=self)
        if self._api.current_sid() != self._sid:
            raise ProfileUnavailable('PROFILE_SID_CHANGED', owner=self)
        self._api.known()
        if self._api.final_path(self._handles[-1]) != str(self.path):
            raise ProfileUnavailable('PROFILE_CANONICAL_PATH_CHANGED', owner=self)
        return super().verify()

    def release(self):
        if self._released:
            return
        if self._api is None:
            self._released = True
            return
        self._api.known()
        for i in reversed(range(len(self._handles))):
            handle = self._handles[i]
            if handle is not None:
                self._api.close(handle)  # Unknown-close is permanently latched by mutable HANDLE identity.
                self._handles[i] = None
        self._api.cleanup()
        self._released = True

    def remove(self, deadline):
        if self._persistent:
            raise ProfileUnavailable('PROFILE_PERSISTENT_ROOT_NOT_REMOVABLE', owner=self)
        return super().remove(deadline)


class WindowsProfileStore:
    """One Windows binding, no account data. No production registration/activation is performed here."""
    def __init__(self, directory=None, scratch=None, *, _root_factory=None):
        self.directory = self.binding_directory = self.scratch = None
        self._factory = _PinnedRoot if _root_factory is None else _root_factory
        self._metadata = self._scratch = self._partial = None
        self._setup = self._global_context = None
        self._handles = []
        self._uncertain = False
        self._closed = self._close_failed = False
        self._ready = False
        try:
            self.directory = Path(directory) if directory is not None else storage.connection_directory()
            scratch_parent = Path(scratch) if scratch is not None else storage.data_directory() / private_roots.SCRATCH
            from .fab_browser_windows_store_setup import _SetupRoots
            roots = _SetupRoots.__new__(_SetupRoots)
            if type(roots) is not _SetupRoots or getattr(roots, '_store_owner', None) is not None:
                raise ProfileUnavailable('PROFILE_STORE_OWNER_REQUIRED', owner=self)
            self._setup = roots  # Before setup can acquire native parents/roots or lose its return.
            _SetupRoots.__init__(roots, self.directory, scratch_parent)
            self._adopt_setup(roots)
            private_roots.declare(private_roots.FAB_BROWSER_PREFIX, self.scratch)
            with self._transaction():
                self._setup.refuse_legacy()  # Startup only: cleanup must not classify current download landings.
            self._ready = True
        except BaseException as error:
            if self._setup is not None:
                self._adopt_setup(self._setup)
            _retain(error, self)
            raise

    def _adopt_setup(self, roots):
        # Adopt the published objects, never reopened names or recreated roots.
        roots._store_owner = self
        self._metadata, self._scratch = roots._metadata, roots._scratch
        self.directory, self.binding_directory, self.scratch = roots.directory, roots.binding_directory, roots.scratch

    def _current(self):
        if self._closed:
            raise ProfileUnavailable('PROFILE_STORE_CLOSED', owner=self)
        if self._close_failed:
            raise ProfileUnavailable('PROFILE_STORE_CLOSE_UNPROVED', owner=self)
        if self._uncertain:
            raise ProfileUnavailable('PROFILE_METADATA_CLOSE_UNPROVED', owner=self._partial or self)
        if self._setup is None or self._setup._store_owner is not self:
            raise ProfileUnavailable('PROFILE_STORE_OWNER_REQUIRED', owner=self)
        self._setup.verify()

    @contextmanager
    def _file(self, root, name, *, create=False, write=False, shared=False):
        output = ctypes.c_void_p()
        self._handles.append((root, output))  # Publish before native open/return allocation can fail.
        primary = None
        try:
            opened = root._api.file_into(root._handles[-1], name, root._sid, output,
                                         create=create, write=write, shared=shared, delete=name != 'connections.lock')
            if opened:
                root._api.file_identity(output, root._sid)
            yield output if opened else None
        except BaseException as error:
            primary = error
            _retain(error, self._partial or self)
            raise
        finally:
            try:
                root._api.known()
                if output.value is not None:
                    root._api.close(output)
                self._handles.remove((root, output))
            except BaseException as cleanup:
                self._uncertain = True
                if primary is None:
                    _retain(cleanup, self._partial or self)
                    raise
                # Preserve cancellation/the operation's original exception; also publish cleanup uncertainty.
                _retain(primary, self._partial or self)
                primary.cleanup_error = cleanup

    @contextmanager
    def _transaction(self):
        from .connections import metadata_lock
        self._current()
        context = metadata_lock(self.directory)
        self._global_context = context
        primary = None
        try:
            with context:
                try:
                    with self._private_transaction():
                        yield
                except BaseException as error:
                    primary = error
                    raise
        except BaseException as error:
            if error is not primary:
                self._uncertain = True
                if primary is not None:
                    primary.cleanup_error = error
                    _retain(primary, self._partial or self)
                    raise primary from error
            _retain(error, self._partial or self)
            raise
        finally:
            if not self._uncertain:
                self._global_context = None

    @contextmanager
    def _private_transaction(self):
        self._current()
        with self._file(self._metadata, 'connections.lock', write=True, shared=True) as handle:
            if handle is None:
                with self._file(self._metadata, 'connections.lock', create=True, write=True, shared=True) as made:
                    if made is None:
                        raise ProfileUnavailable('PROFILE_BUSY', owner=self)
                    self._metadata._api.lock_first_byte(made)
                    self._current()
                    yield
            else:
                self._metadata._api.lock_first_byte(handle)
                self._current()
                yield

    def _binding(self):
        with self._file(self._metadata, BINDING) as handle:
            if handle is None:
                return None
            if self._metadata._api.file_information(handle)[1] > _LIMIT:
                raise ProfileUnavailable('PROFILE_METADATA_SHAPE')
            raw = self._metadata._api.read(handle, _LIMIT + 1)
        try:
            row = json.loads(raw)
        except (ValueError, UnicodeError):
            raise ProfileUnavailable('PROFILE_METADATA_SHAPE') from None
        keys = {'version', 'sid', 'path', 'volume', 'file_id', 'token', 'state'}
        if (type(row) is not dict or set(row) != keys or type(row['version']) is not int or row['version'] != 1 or
                row['sid'] != self._metadata._sid or type(row['path']) is not str or
                type(row['volume']) is not int or not 0 <= row['volume'] < 2**64 or
                type(row['file_id']) is not str or not re.fullmatch('[0-9a-f]{32}', row['file_id']) or
                type(row['token']) is not str or not re.fullmatch('[0-9a-f]{32}', row['token']) or
                row['state'] not in ('pending', 'retained', 'cleanup_required')):
            raise ProfileUnavailable('PROFILE_METADATA_SHAPE')
        path = Path(row['path'])
        if path.parent != self.scratch or path.name != private_roots.FAB_BROWSER_PREFIX + row['token']:
            raise ProfileUnavailable('PROFILE_METADATA_SHAPE')
        return row

    def _write(self, row):
        self._current()
        payload = json.dumps(row, separators=(',', ':'), ensure_ascii=True).encode('ascii')
        name = '.fab-profile-' + uuid.uuid4().hex
        with self._file(self._metadata, name, create=True, write=True) as handle:
            if handle is None:
                raise ProfileUnavailable('PROFILE_METADATA_COLLISION', owner=self)
            self._metadata._api.write(handle, payload)
            self._current()
            self._metadata._api.replace(handle, self._metadata._handles[-1], BINDING)

    def _remember(self, path=None):
        records = [] if path is None else [{'native_profile': str(path)}]
        if not private_roots.remember_profiles(self.directory, records, locked=True):
            raise ProfileUnavailable('PROFILE_PRIVACY_REGISTRATION_FAILED', owner=self._partial or self)

    def create(self):
        if not self._ready:
            raise ProfileUnavailable('PROFILE_STORE_SETUP_UNAVAILABLE', owner=self)
        with self._transaction():
            if self._binding() is not None:
                raise ProfileUnavailable('PROFILE_EXISTS')
            token = uuid.uuid4().hex
            lease = WindowsProfileLease.__new__(WindowsProfileLease)
            self._partial = lease
            lease._initialize(self, token)
            try:
                lease._open(create=True)
                self._remember(lease.path)
                self._write(lease._row)
                return lease
            except BaseException as error:
                _retain(error, lease)
                raise

    def resume(self):
        return self._resume()

    def resume_expected(self, expected):
        """Reopen only this detached retained row under the existing metadata transaction."""
        keys = {'version', 'sid', 'path', 'volume', 'file_id', 'token', 'state'}
        if (type(expected) is not dict or len(expected) != len(keys) or
                any(type(key) is not str for key in expected) or set(expected) != keys or
                any(type(expected[key]) is not int for key in ('version', 'volume')) or
                any(type(expected[key]) is not str for key in ('sid', 'path', 'file_id', 'token', 'state')) or
                expected['version'] != 1 or not 0 <= expected['volume'] < 2**64 or
                not re.fullmatch('[0-9a-f]{32}', expected['file_id']) or
                not re.fullmatch('[0-9a-f]{32}', expected['token']) or expected['state'] != 'retained'):
            raise ProfileUnavailable('PROFILE_METADATA_SHAPE')
        return self._resume(dict(expected))

    def _resume(self, expected=None):
        if not self._ready:
            raise ProfileUnavailable('PROFILE_STORE_SETUP_UNAVAILABLE', owner=self)
        with self._transaction():
            row = self._binding()
            if row is None:
                raise ProfileUnavailable('PROFILE_MISSING')
            if expected is not None and row != expected:
                raise ProfileUnavailable('PROFILE_CHANGED')
            if row['state'] != 'retained':
                raise ProfileUnavailable('PROFILE_RECOVERY_REQUIRED')
            lease = WindowsProfileLease.__new__(WindowsProfileLease)
            self._partial = lease
            lease._initialize(self, row['token'], row)
            try:
                lease._open(create=False)
                # A crash after reopening must never leave a resumable 'retained' binding.
                lease._row = dict(row, state='pending')
                self._write(lease._row)
                return lease
            except BaseException as error:
                _retain(error, lease)
                raise

    def close(self):
        if self._closed:
            return
        if self._close_failed:
            raise ProfileUnavailable('PROFILE_STORE_CLOSE_UNPROVED', owner=self)
        if self._uncertain:
            raise ProfileUnavailable('PROFILE_METADATA_CLOSE_UNPROVED', owner=self)
        if self._partial is not None and (type(self._partial) is not WindowsProfileLease or not self._partial._released):
            raise ProfileUnavailable('PROFILE_LEASE_STILL_OWNED', owner=self._partial)
        try:
            if self._setup is not None:
                if self._setup._store_owner is not self:
                    raise ProfileUnavailable('PROFILE_STORE_OWNER_REQUIRED', owner=self)
                self._setup.release()
        except BaseException as error:
            self._close_failed = True
            _retain(error, self)
            raise
        self._closed = True


class WindowsProfileLease:
    def _initialize(self, store, token, row=None):
        self.store, self.generation = store, token
        self.path = store.scratch / (private_roots.FAB_BROWSER_PREFIX + token)
        self.identity = None
        self._root = self._browser = None
        self._handles = []
        self._lock_identity = self._marker_identity = None
        self._released = False
        self._row = dict(row) if row is not None else None

    def _hold(self, name, *, create, write=False):
        handle = ctypes.c_void_p()
        self._handles.append(handle)
        api = self._root._api
        if not api.file_into(self._root._handles[-1], name, self._root._sid, handle,
                             create=create, write=write, shared=name == PROFILE_LOCK, delete=False):
            raise ProfileUnavailable('PROFILE_MARKER_OR_LOCK_MISSING', owner=self)
        identity = api.file_identity(handle, self._root._sid)
        return handle, identity

    def _open(self, *, create):
        self._root = self.store._factory.__new__(self.store._factory)
        self.store._factory.__init__(self._root, self.path, create=create)
        self.identity = self._root.identity
        if self._row is None:
            self._row = dict(version=1, sid=self._root._sid, path=str(self.path), volume=self.identity[0],
                             file_id=self.identity[1].hex(), token=self.generation, state='pending')
        elif self.identity != (self._row['volume'], bytes.fromhex(self._row['file_id'])):
            raise ProfileUnavailable('PROFILE_CHANGED', owner=self)
        lock, self._lock_identity = self._hold(PROFILE_LOCK, create=create, write=True)
        self._root._api.lock_first_byte(lock)
        marker, self._marker_identity = self._hold(OWNER_MARKER, create=create, write=create)
        if create:
            self._root._api.write(marker, self.generation.encode('ascii'))
            self._root._api.rewind(marker)
        if self._root._api.read(marker, 65) != self.generation.encode('ascii'):
            raise ProfileUnavailable('PROFILE_MARKER_CHANGED', owner=self)
        self.verify()

    def verify(self):
        if self._released or self._root is None or self.identity is None:
            raise ProfileUnavailable('PROFILE_UNAVAILABLE', owner=self)
        self.store._current()
        self._root.verify()
        if self._root.identity != self.identity or self._root._sid != self._row['sid'] or len(self._handles) != 2:
            raise ProfileUnavailable('PROFILE_CHANGED', owner=self)
        for handle, expected in zip(self._handles, (self._lock_identity, self._marker_identity)):
            if self._root._api.file_identity(handle, self._root._sid) != expected:
                raise ProfileUnavailable('PROFILE_CHANGED', owner=self)
        marker = self._handles[1]
        if self._root._api.file_information(marker)[1] != 32:
            raise ProfileUnavailable('PROFILE_MARKER_CHANGED', owner=self)
        self._root._api.rewind(marker)
        if self._root._api.read(marker, 65) != self.generation.encode('ascii'):
            raise ProfileUnavailable('PROFILE_MARKER_CHANGED', owner=self)
        return True

    def claim(self, browser):
        from .fab_browser_process_windows import WindowsBrowserProcess
        self.verify()
        if type(browser) is not WindowsBrowserProcess or self._browser is not None:
            raise ProfileUnavailable('PROFILE_BUSY', owner=self)
        if getattr(browser, '_profile_lease', None) is not self:
            raise ProfileUnavailable('PROFILE_BROWSER_BINDING_UNPROVED', owner=self)
        self._inactive_observation()
        # This does not prove arbitrary external inactivity. DRV must additionally establish exact initial
        # retained-Job/CDP/path ownership after spawn before any account/approval or retained reuse is accepted.
        self._browser = browser
        return self.path

    def _inactive_observation(self):
        if self._root._api.observed_busy(self.path):
            raise ProfileUnavailable('PROFILE_IN_USE', owner=self)
        # Chrome holds its own lockfile GENERIC_WRITE/FILE_SHARE_READ/DELETE_ON_CLOSE. A nonmutating
        # held-relative read+DELETE open conflicts with that live owner. Never create its lockfile/mutex,
        # retry a sharing violation, notify another browser, or treat absence as a future inactivity guarantee.
        with self.store._file(self._root, 'lockfile'):
            pass

    def _release_files(self):
        api = self._root._api
        api.known()
        for handle in reversed(self._handles):
            api.close(handle)
        self._handles.clear()

    def finish_drained(self, browser, *, retain=False):
        from .fab_browser_process_windows import WindowsBrowserProcess
        if (type(retain) is not bool or type(browser) is not WindowsBrowserProcess or self._browser is not browser or
                getattr(browser, '_profile_lease', None) is not self or browser._closed is not True or
                browser._pipes is not None or type(browser._process) is not ChildProcess or browser._drained is not True):
            raise ProfileUnavailable('PROFILE_DRAIN_UNPROVED', owner=self)
        self._finish(retain)

    def _finish(self, retain, *, allow_missing=False):
        self.verify()
        with self.store._transaction():
            current = self.store._binding()
            if current != self._row and not (allow_missing and current is None):
                raise ProfileUnavailable('PROFILE_METADATA_CHANGED', owner=self)
            # Never publish resumable metadata before every profile-owned native close is known successful.
            updated = dict(self._row, state='cleanup_required')
            self.store._write(updated)
            self._row = updated
            self._release_files()
            if retain:
                self._root.release()
                updated = dict(self._row, state='retained')
                self.store._write(updated)  # Verifies independent metadata/scratch roots, not released profile pins.
                self._row = updated
            else:
                self._root.remove(time.monotonic() + 5)
                with self.store._file(self.store._metadata, BINDING) as handle:
                    if handle is None:
                        raise ProfileUnavailable('PROFILE_METADATA_CHANGED', owner=self)
                    self.store._metadata._api.mark_delete(handle)
                self.store._remember()
        self._browser, self._released = None, True

    def abort_claim_before_spawn(self, browser):
        from .fab_browser_process_windows import WindowsBrowserProcess
        if (type(browser) is not WindowsBrowserProcess or self._browser is not browser or
                getattr(browser, '_profile_lease', None) is not self or
                getattr(browser, '_spawn_attempted', None) is not False or browser._process is not None or
                browser._closed is not True or browser._pipes is not None):
            raise ProfileUnavailable('PROFILE_PRE_SPAWN_UNPROVED', owner=self)
        self._inactive_observation()
        self._finish(False)

    def cancel_pending(self):
        if self._browser is not None:
            raise ProfileUnavailable('PROFILE_BUSY', owner=self)
        # Definitely pre-spawn only. A claimed/failed native launch belongs to its browser cleanup owner.
        if self._row is None or self._row['state'] != 'pending':
            raise ProfileUnavailable('PROFILE_RECOVERY_REQUIRED', owner=self)
        self._inactive_observation()
        self._finish(False, allow_missing=True)

    def close(self):
        if self._released:
            return
        if self._browser is not None:
            self._browser.close()  # Stop never skips the existing independent pipe/Job drain.
            return
        self.cancel_pending()
