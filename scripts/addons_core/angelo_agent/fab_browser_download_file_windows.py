# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Held Windows download objects, not browser-writer, account or import authority.

Source preparation only. Actual NT/DACL/Chrome ABI validation is still required.
The shared browser owner must prove writer drain before removing the landing root.
"""
import ctypes
import hashlib
import re
from pathlib import Path
import uuid
import threading
import time

from . import private_roots
from .fab_browser_process import BrowserUnavailable
from .fab_browser_profile_windows import PrivateWindowsProfile, _WindowsProfileAPI
from .team_library_files import MAX_BYTES

CHUNK = 1024 * 1024


class DownloadFileUnavailable(BrowserUnavailable):
    def __init__(self, code, *, owner=None):
        super().__init__(code)
        self.owner = owner


def _retained(error, owner):
    # Preserve cancellation/deadline exceptions while publishing their exact resource owner.
    try:
        error.owner = owner
    except BaseException:
        raise DownloadFileUnavailable('DOWNLOAD_FILE_OWNER_RETAINED', owner=owner) from error


class _WindowsDownloadAPI(_WindowsProfileAPI):
    """Reuse accepted SID, object-relative no-reparse and full 128-bit identity primitives."""
    def __init__(self):
        self._unknown_close = {}
        self._unknown_free = {}
        self._pending_read = None
        self._pending_create = self._created_root = None
        super().__init__()  # Fail closed off Windows; no POSIX fallback.
        w = self.w
        for name, args, result in (
            ('GetFileType', (w.HANDLE,), w.DWORD),
            ('ReadFile', (w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p), w.BOOL),
            ('SetFilePointerEx', (w.HANDLE, ctypes.c_int64, ctypes.c_void_p, w.DWORD), w.BOOL),
        ):
            function = getattr(self.k, name)
            function.argtypes, function.restype = args, result
        self.n.NtCreateFile.argtypes = (
            ctypes.POINTER(w.HANDLE), w.DWORD, ctypes.POINTER(self.Attributes),
            ctypes.POINTER(self.IOStatus), ctypes.c_void_p, w.DWORD, w.DWORD,
            w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD)
        self.n.NtCreateFile.restype = ctypes.c_int32

    def known(self):
        if (self._unknown_close or self._unknown_free or self._pending_read is not None or
                self._pending_create is not None):
            raise DownloadFileUnavailable('DOWNLOAD_NATIVE_CLEANUP_UNPROVED')

    def close(self, handle):
        key = id(handle)
        if key in self._unknown_close:
            raise DownloadFileUnavailable('DOWNLOAD_HANDLE_CLOSE_UNPROVED')
        if handle.value is None:
            return
        # Publish the mutable HANDLE object before the call, never retry its numeric value.
        self._unknown_close[key] = handle
        if not self.k.CloseHandle(handle):
            raise DownloadFileUnavailable('DOWNLOAD_HANDLE_CLOSE_UNPROVED')
        handle.value = None
        del self._unknown_close[key]

    def _release_temporary(self, slot):
        value = self._temporary[slot]
        if value is None:
            return
        if slot in self._unknown_free:
            raise DownloadFileUnavailable('DOWNLOAD_SECURITY_FREE_UNPROVED')
        pointer = ctypes.cast(value, ctypes.c_void_p).value
        if pointer:
            if slot == 0:
                self.close(value)
            else:
                self._unknown_free[slot] = value
                if self.k.LocalFree(pointer):
                    raise DownloadFileUnavailable('DOWNLOAD_SECURITY_FREE_UNPROVED')
                self._temporary[slot] = None
                del self._unknown_free[slot]
        self._temporary[slot] = None

    def cleanup(self):
        self.known()
        super().cleanup()

    def create_directory_into(self, parent, name, sid, output):
        """One FILE_CREATE relative to the held scratch, returning the created object itself.

        NtCreateFile user-mode contract: Microsoft winternl/nf-winternl-ntcreatefile.
        No CreateDirectoryW -> pathname-open window and no collision adoption.
        """
        self.known()
        if (self._created_root is not None or type(name) is not str or not name or
                name in ('.', '..') or any(c in name for c in ('\\', '/', ':', '\0'))):
            raise DownloadFileUnavailable('DOWNLOAD_LANDING_CREATE')
        raw = name.encode('utf-16-le', 'strict')
        if not raw or len(raw) > 65532:
            raise DownloadFileUnavailable('DOWNLOAD_LANDING_CREATE')
        descriptor = self._temporary_slot(2, ctypes.c_void_p())
        self._check(self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            'O:' + sid + 'D:P(A;OICI;FA;;;' + sid + ')', 1, ctypes.byref(descriptor), None),
            'DOWNLOAD_LANDING_ACL')
        try:
            buffer = ctypes.create_string_buffer(raw + b'\0\0')
            text = self.Unicode(len(raw), len(raw) + 2, ctypes.cast(buffer, ctypes.c_void_p))
            attributes = self.Attributes(ctypes.sizeof(self.Attributes), parent.value,
                                         ctypes.pointer(text), 0x40 | 0x1000, descriptor, None)
            ios = self.IOStatus()
            ios.value.status = 0x103
            # Output belongs to the Landing before acquisition; all native-call storage stays
            # reachable even if a signal/pending return prevents publication of the result.
            arena = (output, ios, parent, buffer, text, attributes, descriptor)
            self._pending_create = arena
            status = self.n.NtCreateFile(ctypes.byref(output), 0x00020000 | 0x80 | 0x00100000 | 0x1 | 0x00010000,
                ctypes.byref(attributes), ctypes.byref(ios), None, 0x10, 1, 2, 0x1 | 0x20 | 0x00200000, None, 0)
            if status == 0x103:
                raise DownloadFileUnavailable('DOWNLOAD_LANDING_CREATE_PENDING')
            if status == 0 and output.value and ios.value.status == 0 and ios.information == 2:
                self._created_root = arena  # SAME already allocated arena; FILE_CREATED.
                self._pending_create = None
                return True
            if (status & 0xffffffff) == 0xc0000035 and not output.value:
                self._pending_create = None  # Exact NAME_COLLISION, no owned object was opened.
                return False
            # An unrecognized result is retained rather than adopting a possibly existing root.
            raise DownloadFileUnavailable('DOWNLOAD_LANDING_CREATE_UNPROVED')
        finally:
            if self._pending_create is None:
                self._release_temporary(2)

    def created_root(self, handle):
        return (self._pending_create is None and self._created_root is not None and
                self._created_root[0] is handle and handle.value is not None and
                self._created_root[1].value.status == 0 and self._created_root[1].information == 2)

    def open_file_into(self, parent, guid, output):
        self.known()
        # _open publishes pending native-I/O buffers before NtOpenFile. FILE_SHARE_READ only:
        # existing/future data writers and delete/rename handles conflict with this lease.
        self._open(parent, guid, output, directory=False, delete=True, no_reparse=True)

    def file_information(self, handle):
        self.known()
        identity, directory, reparse = self.entry_information(handle)
        if directory or reparse or self.k.GetFileType(handle) != 1:  # FILE_TYPE_DISK
            raise DownloadFileUnavailable('DOWNLOAD_FILE_TYPE')
        class Info(ctypes.Structure):
            _fields_ = [('attributes', ctypes.c_uint32), ('times', ctypes.c_uint32 * 6),
                        ('volume', ctypes.c_uint32), ('size_hi', ctypes.c_uint32),
                        ('size_lo', ctypes.c_uint32), ('links', ctypes.c_uint32),
                        ('index_hi', ctypes.c_uint32), ('index_lo', ctypes.c_uint32)]
        class Basic(ctypes.Structure):
            _fields_ = [('created', ctypes.c_int64), ('accessed', ctypes.c_int64),
                        ('written', ctypes.c_int64), ('changed', ctypes.c_int64),
                        ('attributes', ctypes.c_uint32)]
        info, basic = Info(), Basic()
        self._check(self.k.GetFileInformationByHandle(handle, ctypes.byref(info)), 'DOWNLOAD_FILE_INFO')
        self._check(self.k.GetFileInformationByHandleEx(handle, 0, ctypes.byref(basic), ctypes.sizeof(basic)),
                    'DOWNLOAD_FILE_BASIC')
        if info.links != 1 or (info.attributes | basic.attributes) & (0x10 | 0x400):
            raise DownloadFileUnavailable('DOWNLOAD_FILE_TYPE')
        # Access time is deliberately excluded: our own reads may update it.
        return (identity, (info.size_hi << 32) | info.size_lo, basic.created, basic.written,
                basic.changed, basic.attributes, info.links)

    def verify_file_acl(self, handle, sid):
        """A child may inherit the root's single-user ACE without inheriting DACL_PROTECTED."""
        self.known()
        owner, dacl = ctypes.c_void_p(), ctypes.c_void_p()
        descriptor = self._temporary_slot(3, ctypes.c_void_p())
        result = self.a.GetSecurityInfo(handle, 1, 0x1 | 0x4, ctypes.byref(owner), None,
                                        ctypes.byref(dacl), None, ctypes.byref(descriptor))
        if result:
            raise DownloadFileUnavailable('DOWNLOAD_FILE_ACL')
        try:
            control, revision = ctypes.c_uint16(), self.w.DWORD()
            self._check(self.a.GetSecurityDescriptorControl(descriptor, ctypes.byref(control),
                                                           ctypes.byref(revision)), 'DOWNLOAD_FILE_ACL')
            if not control.value & 0x4 or not dacl.value or self.sid_text(owner) != sid:
                raise DownloadFileUnavailable('DOWNLOAD_FILE_ACL')
            class AclSize(ctypes.Structure):
                _fields_ = [('count', ctypes.c_uint32), ('used', ctypes.c_uint32), ('free', ctypes.c_uint32)]
            size = AclSize()
            self._check(self.a.GetAclInformation(dacl, ctypes.byref(size), ctypes.sizeof(size), 2),
                        'DOWNLOAD_FILE_ACL')
            if size.count != 1:
                raise DownloadFileUnavailable('DOWNLOAD_FILE_ACL')
            ace = ctypes.c_void_p()
            self._check(self.a.GetAce(dacl, 0, ctypes.byref(ace)), 'DOWNLOAD_FILE_ACL')
            header = ctypes.string_at(ace, 4)
            ace_size = int.from_bytes(header[2:4], 'little')
            # Allow OI/CI and native INHERITED_ACE only, never INHERIT_ONLY or another ACE class.
            if header[0] != 0 or header[1] & ~0x13 or ace_size < 20:
                raise DownloadFileUnavailable('DOWNLOAD_FILE_ACL')
            prefix = ctypes.string_at(ace, 10)
            sid_size = 8 + 4 * prefix[9]
            if (prefix[9] > 15 or 8 + sid_size > ace_size or
                    int.from_bytes(prefix[4:8], 'little') != 0x001f01ff or
                    self.sid_text(ace.value + 8) != sid):
                raise DownloadFileUnavailable('DOWNLOAD_FILE_ACL')
        finally:
            self._release_temporary(3)

    def read(self, handle, maximum):
        self.known()
        if type(maximum) is not int or not 0 < maximum <= CHUNK:
            raise DownloadFileUnavailable('DOWNLOAD_READ_BOUND')
        buffer, count = ctypes.create_string_buffer(maximum), self.w.DWORD()
        self._pending_read = (handle, buffer, count)  # Retain kernel-writable storage across uncertainty.
        success = self.k.ReadFile(handle, buffer, maximum, ctypes.byref(count), None)
        if not success and ctypes.get_last_error() == 997:
            raise DownloadFileUnavailable('DOWNLOAD_READ_PENDING')
        self._pending_read = None  # Synchronous completion or a definite non-pending native error.
        self._check(success, 'DOWNLOAD_READ')
        if count.value > maximum:
            raise DownloadFileUnavailable('DOWNLOAD_READ_BOUND')
        return buffer.raw[:count.value]

    def rewind(self, handle):
        self.known()
        self._check(self.k.SetFilePointerEx(handle, 0, None, 0), 'DOWNLOAD_REWIND')


class _LandingProfile(PrivateWindowsProfile):
    """Reuse the accepted held-tree cleanup owner; only root creation differs from profiles."""
    def __init__(self, *, _api):
        self._lock = threading.RLock()
        self._api = _api
        self.path = self.identity = self._sid = None
        self._deleted = self._marked = False
        self._paths = ()
        self._handles = []
        self._cleanup_handles = [None] * 65
        scratch = Path(private_roots.scratch_root()).absolute()
        if not scratch.drive or str(scratch.drive).startswith('\\\\') or len(scratch.parents) > 64:
            raise DownloadFileUnavailable('DOWNLOAD_LANDING_ROOT', owner=self)
        self._paths = tuple(reversed(scratch.parents)) + (scratch,)
        self._handles = [None] * (len(self._paths) + 1)
        private_roots.declare(private_roots.FAB_BROWSER_PREFIX, scratch)
        try:
            self._sid = self._api.current_sid()
            for index, path in enumerate(self._paths):
                self._handles[index] = ctypes.c_void_p()
                self._api.open_directory_into(path, self._handles[index])
                self._api.information(self._handles[index])
            self.path = scratch / (private_roots.FAB_BROWSER_PREFIX + uuid.uuid4().hex)
            self._handles[-1] = ctypes.c_void_p()
            if not self._api.create_directory_into(self._handles[-2], self.path.name, self._sid,
                                                    self._handles[-1]):
                self.path = None
                raise DownloadFileUnavailable('DOWNLOAD_LANDING_COLLISION', owner=self)
            self.identity = self._api.information(self._handles[-1])
            self._api.verify_acl(self._handles[-1], self._sid)
        except BaseException as error:
            _retained(error, self)
            raise

    def remove(self, deadline):
        with self._lock:
            # A failed first identity query may be retried ONLY through the original FILE_CREATED
            # handle, never by opening whichever object currently occupies its pathname.
            if (self.path is not None and self.identity is None and self._handles and
                    self._api.created_root(self._handles[-1])):
                self.identity = self._api.information(self._handles[-1])
                self._api.verify_acl(self._handles[-1], self._sid)
            return super().remove(deadline)


class Landing:
    """One connection-owned root, retained until the shared owner proves whole browser/Job drain."""
    def __init__(self, *, _api=None):
        self._lock = threading.RLock()
        self._files = set()
        self._profile = None
        self._api = None
        self.path = self.identity = None
        self._removed = False
        try:
            self._api = _WindowsDownloadAPI() if _api is None else _api
            # Publish the profile owner before its constructor can acquire anything.
            self._profile = _LandingProfile.__new__(_LandingProfile)
            self._profile.__init__(_api=self._api)
            self.path, self.identity = self._profile.path, self._profile.identity
        except BaseException as error:
            _retained(error, self)
            raise

    def _verify(self):
        self._api.known()
        if self._removed or self._profile is None or self.identity is None:
            raise DownloadFileUnavailable('DOWNLOAD_LANDING_UNAVAILABLE', owner=self)
        self._profile.verify()
        if self._profile.identity != self.identity:
            raise DownloadFileUnavailable('DOWNLOAD_LANDING_CHANGED', owner=self)

    def remove(self):
        """Caller must first prove whole browser/Job drain; terminal+deny is not root removal proof."""
        with self._lock:
            try:
                if self._files:
                    raise DownloadFileUnavailable('DOWNLOAD_FILE_LEASE_HELD', owner=self)
                if self._profile is not None:
                    self._profile.remove(time.monotonic() + 10)
                    self._profile = None
                elif self._api is not None:
                    self._api.cleanup()
                self.path = None
                self._removed = True
            except BaseException as error:
                _retained(error, self)
                raise


class CompletedFile:
    """Only the selected GUID under its actual Landing; the held HANDLE remains content authority."""
    def __init__(self, landing, guid, observation, check):
        self._landing = None
        self._handle = ctypes.c_void_p()
        self._closed = self._streaming = False
        self._consumed = self._marked = self._retired = self._retire_uncertain = False
        self.identity = self.size = self.sha256 = None
        self._check = check
        try:
            if (type(landing) is not Landing or type(guid) is not str or
                    not re.fullmatch('[A-Za-z0-9_-]{1,128}', guid) or not callable(check) or
                    observation.guid != guid or not observation.complete or
                    type(observation.received_bytes) is not int or
                    not 0 < observation.received_bytes <= MAX_BYTES['model']):
                raise DownloadFileUnavailable('DOWNLOAD_FILE_UNPROVED')
            self._landing = landing
            with landing._lock:
                landing._verify()
                landing._files.add(self)  # Strong owner BEFORE any file open or return allocation.
                check()
                landing._api.open_file_into(landing._profile._handles[-1], guid, self._handle)
                self.identity = landing._api.file_information(self._handle)
                self.size = self.identity[1]
                if self.size != observation.received_bytes:
                    raise DownloadFileUnavailable('DOWNLOAD_FILE_SIZE')
                landing._api.verify_file_acl(self._handle, landing._profile._sid)
                digest = hashlib.sha256()
                remaining = self.size
                while remaining:
                    check()
                    self._verify()
                    chunk = landing._api.read(self._handle, min(CHUNK, remaining))
                    if not chunk or len(chunk) > remaining:
                        raise DownloadFileUnavailable('DOWNLOAD_FILE_CHANGED')
                    digest.update(chunk)
                    remaining -= len(chunk)
                check()
                if landing._api.read(self._handle, 1):
                    raise DownloadFileUnavailable('DOWNLOAD_FILE_CHANGED')
                self._verify()
                check()
                self.sha256 = digest.hexdigest()
                landing._api.rewind(self._handle)
        except BaseException as error:
            _retained(error, self)
            raise

    def _verify(self):
        self._landing._verify()
        api = self._landing._api
        if api.file_information(self._handle) != self.identity:
            raise DownloadFileUnavailable('DOWNLOAD_FILE_CHANGED', owner=self)
        api.verify_file_acl(self._handle, self._landing._profile._sid)

    def chunks(self):
        landing = self._landing
        if landing is None:
            raise DownloadFileUnavailable('DOWNLOAD_FILE_UNAVAILABLE', owner=self)
        with landing._lock:
            if self._closed or self.sha256 is None or self._streaming:
                raise DownloadFileUnavailable('DOWNLOAD_FILE_UNAVAILABLE', owner=self)
            self._streaming = True
            self._consumed = False
        try:
            digest = hashlib.sha256()
            remaining = self.size
            with landing._lock:
                self._check()
                self._verify()
                landing._api.rewind(self._handle)
            while remaining:
                with landing._lock:
                    self._check()
                    self._verify()
                    chunk = landing._api.read(self._handle, min(CHUNK, remaining))
                    if not chunk or len(chunk) > remaining:
                        raise DownloadFileUnavailable('DOWNLOAD_FILE_CHANGED', owner=self)
                    remaining -= len(chunk)
                    digest.update(chunk)
                yield chunk
            with landing._lock:
                self._check()
                if landing._api.read(self._handle, 1) or digest.hexdigest() != self.sha256:
                    raise DownloadFileUnavailable('DOWNLOAD_FILE_CHANGED', owner=self)
                self._verify()
                self._check()
                self._consumed = True  # Only fully drained iterator + final digest/identity acceptance.
        except BaseException as error:
            _retained(error, self)
            raise
        finally:
            with landing._lock:
                self._streaming = False

    def retire(self):
        """Delete only this consumed GUID's held object; never remove its connection Landing."""
        landing = self._landing
        if landing is None:
            raise DownloadFileUnavailable('DOWNLOAD_FILE_UNAVAILABLE', owner=self)
        with landing._lock:
            try:
                if self._retired:
                    self.close()
                    return
                if (self._closed or self._streaming or not self._consumed or
                        self._retire_uncertain):
                    raise DownloadFileUnavailable('DOWNLOAD_FILE_RETIRE_UNPROVED', owner=self)
                self._check()
                self._verify()
                # Prepublish uncertainty before a native mark can take effect. A failed/lost mark
                # keeps this exact owner/handle; no pathname retry or replacement adoption.
                self._retire_uncertain = True
                landing._api.mark_delete(self._handle)
                self._marked = True
                self._retire_uncertain = False
                self.close()  # Unknown CloseHandle is latched by the adapter and never retried.
                self._retired = True
            except BaseException as error:
                _retained(error, self)
                raise

    def close(self):
        landing = self._landing
        if landing is None:
            self._closed = True
            return
        with landing._lock:
            try:
                if self._streaming or self._retire_uncertain:
                    raise DownloadFileUnavailable('DOWNLOAD_FILE_READER_HELD', owner=self)
                if not self._closed:
                    landing._api.known()
                    landing._api.close(self._handle)
                    self._closed = True
                landing._api.cleanup()
                self._retired = self._marked
                landing._files.discard(self)
            except BaseException as error:
                _retained(error, self)
                raise
