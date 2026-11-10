# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private held-relative Windows metadata I/O; no browser engine or registration."""
import ctypes
from .fab_browser_download_file_windows import _WindowsDownloadAPI

_LIMIT = 8192


class ProfileUnavailable(RuntimeError):
    def __init__(self, code, *, owner=None):
        self.code, self.owner = code, owner
        super().__init__(code)


class _SessionAPI(_WindowsDownloadAPI):
    """Only the missing held-relative metadata/byte-lock operations; no browser/process engine."""
    def __init__(self):
        self._session_pending = None
        super().__init__()
        w = self.w
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.user.FindWindowExW.argtypes = (w.HWND, w.HWND, w.LPCWSTR, w.LPCWSTR)
        self.user.FindWindowExW.restype = w.HWND
        for name, args in (
            ('WriteFile', (w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p)),
            ('FlushFileBuffers', (w.HANDLE,)),
            ('LockFileEx', (w.HANDLE, w.DWORD, w.DWORD, w.DWORD, w.DWORD, ctypes.c_void_p)),
        ):
            function = getattr(self.k, name)
            function.argtypes, function.restype = args, w.BOOL
        self.k.GetFinalPathNameByHandleW.argtypes = (w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD)
        self.k.GetFinalPathNameByHandleW.restype = w.DWORD
        self.k.GetDriveTypeW.argtypes = (w.LPCWSTR,)
        self.k.GetDriveTypeW.restype = w.UINT

    def known(self):
        if (self._session_pending is not None or getattr(self, '_pending_open', None) is not None or
                getattr(self, '_pending_query', None) is not None):
            raise ProfileUnavailable('PROFILE_NATIVE_OPERATION_UNPROVED', owner=self)
        super().known()

    def ensure_persistent_directory_into(self, parent, name, sid, output):
        """Atomic existing/new app container, READ pin only; never ACL-edit or delete an existing root.

        FILE_OPEN_IF applies the supplied SD only to a newly created object. The caller must
        verify the SAME returned directory and strict private ACL where its fixed role requires
        that. This is not the removable profile/Landing creator, whose behavior is unchanged.
        """
        self.known()
        if (type(name) is not str or not name or name in ('.', '..') or
                any(c in name for c in ('\\', '/', ':', '\0')) or output.value is not None):
            raise ProfileUnavailable('PROFILE_STORE_DIRECTORY_NAME', owner=self)
        raw = name.encode('utf-16-le', 'strict')
        if not raw or len(raw) > 65532:
            raise ProfileUnavailable('PROFILE_STORE_DIRECTORY_NAME', owner=self)
        descriptor = self._temporary_slot(2, ctypes.c_void_p())
        self._check(self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            'O:' + sid + 'D:P(A;OICI;FA;;;' + sid + ')', 1, ctypes.byref(descriptor), None),
            'PROFILE_STORE_DIRECTORY_ACL')
        try:
            buffer = ctypes.create_string_buffer(raw + b'\0\0')
            text = self.Unicode(len(raw), len(raw) + 2, ctypes.cast(buffer, ctypes.c_void_p))
            attributes = self.Attributes(ctypes.sizeof(self.Attributes), parent.value,
                                         ctypes.pointer(text), 0x40 | 0x1000, descriptor, None)
            ios = self.IOStatus()
            ios.value.status = 0x103
            arena = (output, ios, parent, buffer, text, attributes, descriptor)
            self._pending_create = arena  # Before HANDLE acquisition or any possible lost return.
            access = 0x00020000 | 0x80 | 0x00100000 | 0x1  # NO DELETE; SHARE_READ still denies rename.
            status = self.n.NtCreateFile(ctypes.byref(output), access, ctypes.byref(attributes),
                ctypes.byref(ios), None, 0x10, 1, 3, 0x1 | 0x20 | 0x00200000, None, 0)
            if status == 0 and output.value and ios.value.status == 0 and ios.information in (1, 2):
                if ios.information == 2:
                    self._created_root = arena
                self._pending_create = None
                return 'created' if ios.information == 2 else 'opened'
            if (status & 0xffffffff) == 0xc0000043 and not output.value:
                self._pending_create = None  # Definite sharing violation acquired no object.
                raise ProfileUnavailable('PROFILE_STORE_BUSY', owner=self)
            raise ProfileUnavailable('PROFILE_STORE_DIRECTORY_OPEN_UNPROVED', owner=self)
        finally:
            if self._pending_create is None:
                self._release_temporary(2)

    def file_into(self, parent, name, sid, output, *, create=False, write=False, shared=False, delete=True):
        self.known()
        if (type(name) is not str or not name or name in ('.', '..') or
                any(c in name for c in ('\\', '/', ':', '\0'))):
            raise ProfileUnavailable('PROFILE_METADATA_NAME')
        raw = name.encode('utf-16-le', 'strict')
        if len(raw) > 65532:
            raise ProfileUnavailable('PROFILE_METADATA_NAME')
        descriptor = None
        if create:
            descriptor = self._temporary_slot(2, ctypes.c_void_p())
            self._check(self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                'O:' + sid + 'D:P(A;OICI;FA;;;' + sid + ')', 1, ctypes.byref(descriptor), None),
                'PROFILE_METADATA_ACL')
        buffer = ctypes.create_string_buffer(raw + b'\0\0')
        text = self.Unicode(len(raw), len(raw) + 2, ctypes.cast(buffer, ctypes.c_void_p))
        attributes = self.Attributes(ctypes.sizeof(self.Attributes), parent.value,
                                    ctypes.pointer(text), 0x40 | 0x1000, descriptor, None)
        ios = self.IOStatus()
        ios.value.status = 0x103
        arena = (output, ios, parent, buffer, text, attributes, descriptor)
        self._session_pending = arena  # Publish every output/buffer BEFORE native acquisition.
        access = 0x00020000 | 0x80 | 0x00100000 | 0x1 | (0x00010000 if delete else 0)
        if write:
            access |= 0x2
        status = self.n.NtCreateFile(ctypes.byref(output), access, ctypes.byref(attributes),
            ctypes.byref(ios), None, 0x80, 3 if shared else 1, 2 if create else 1,
            0x40 | 0x20 | 0x00200000, None, 0)  # NON_DIRECTORY/SYNCHRONOUS/OPEN_REPARSE_POINT.
        unsigned = status & 0xffffffff
        if status == 0 and output.value and ios.value.status == 0 and ios.information == (2 if create else 1):
            self._session_pending = None
            if create:
                self._release_temporary(2)
            return True
        absent = unsigned in (0xc0000034, 0xc000003a)
        collision = unsigned == 0xc0000035
        if unsigned == 0xc0000043 and not output.value:  # Definite sharing violation, no acquired HANDLE.
            self._session_pending = None
            if create:
                self._release_temporary(2)
            raise ProfileUnavailable('PROFILE_IN_USE' if name == 'lockfile' else 'PROFILE_BUSY', owner=self)
        if not output.value and (absent and not create or collision and create):
            self._session_pending = None
            if create:
                self._release_temporary(2)
            return False
        raise ProfileUnavailable('PROFILE_METADATA_OPEN_UNPROVED', owner=self)

    def file_identity(self, handle, sid):
        result = self.file_information(handle)
        self.verify_file_acl(handle, sid)
        return result[0]

    def lock_first_byte(self, handle):
        self.known()
        class Overlapped(ctypes.Structure):
            _fields_ = [('internal', ctypes.c_size_t), ('internal_high', ctypes.c_size_t),
                        ('offset', ctypes.c_uint32), ('offset_high', ctypes.c_uint32), ('event', ctypes.c_void_p)]
        overlap = Overlapped()
        self._session_pending = (handle, overlap)
        if self.k.LockFileEx(handle, 3, 0, 1, 0, ctypes.byref(overlap)):
            self._session_pending = None
            return
        code = ctypes.get_last_error()
        if code == 33:  # ERROR_LOCK_VIOLATION, FAIL_IMMEDIATELY acquired nothing.
            self._session_pending = None
            raise ProfileUnavailable('PROFILE_BUSY')
        raise ProfileUnavailable('PROFILE_LOCK_UNPROVED', owner=self)

    def write(self, handle, payload):
        self.known()
        if type(payload) is not bytes or len(payload) > _LIMIT:
            raise ProfileUnavailable('PROFILE_METADATA_SHAPE')
        buffer, count = ctypes.create_string_buffer(payload), self.w.DWORD()
        self._session_pending = (handle, buffer, count)
        if not self.k.WriteFile(handle, buffer, len(payload), ctypes.byref(count), None) or count.value != len(payload):
            raise ProfileUnavailable('PROFILE_METADATA_WRITE_UNPROVED', owner=self)
        self._session_pending = (handle,)
        if not self.k.FlushFileBuffers(handle):
            raise ProfileUnavailable('PROFILE_METADATA_FLUSH_UNPROVED', owner=self)
        self._session_pending = None

    def replace(self, handle, parent, name):
        self.known()
        raw = name.encode('utf-16-le')
        class Rename(ctypes.Structure):
            _fields_ = [('replace', ctypes.c_ubyte), ('root', ctypes.c_void_p),
                        ('length', ctypes.c_uint32), ('name', ctypes.c_uint16 * (len(raw) // 2))]
        value = Rename()
        value.replace, value.root, value.length = 1, parent.value, len(raw)
        ctypes.memmove(ctypes.addressof(value) + Rename.name.offset, raw, len(raw))
        self._session_pending = (handle, parent, value)
        if not self.k.SetFileInformationByHandle(handle, 3, ctypes.byref(value), ctypes.sizeof(value)):
            raise ProfileUnavailable('PROFILE_METADATA_RENAME_UNPROVED', owner=self)
        self._session_pending = None

    def observed_busy(self, path):
        self.known()
        # Chromium base/win/message_window.cc uses this class and exact profile-path title. This is an
        # observed busy check only; missing windows cannot certify absence on other sessions/desktops.
        return bool(self.user.FindWindowExW(ctypes.c_void_p(-3), None, 'Chrome_MessageWindow', str(path)))

    def final_path(self, handle):
        self.known()
        buffer = ctypes.create_unicode_buffer(32768)
        self._session_pending = (handle, buffer)
        size = self.k.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)  # NORMALIZED + VOLUME_NAME_DOS.
        if not 0 < size < len(buffer):
            raise ProfileUnavailable('PROFILE_CANONICAL_PATH_UNPROVED', owner=self)
        value = buffer.value
        if (not value.startswith('\\\\?\\') or value.startswith('\\\\?\\UNC\\') or
                len(value) < 7 or value[5:7] != ':\\'):
            raise ProfileUnavailable('PROFILE_CANONICAL_PATH_UNPROVED', owner=self)
        value = value[4:]
        drive = value[:3]  # Query the actual canonical DOS drive root, including its required trailing slash.
        self._session_pending = (handle, buffer, drive)
        if self.k.GetDriveTypeW(drive) not in (2, 3, 6):  # REMOVABLE, FIXED, RAMDISK; no remote/unknown fallback.
            raise ProfileUnavailable('PROFILE_LOCAL_VOLUME_UNPROVED', owner=self)
        self._session_pending = None
        return value

    def require_local_drive(self, drive):
        self.known()
        if type(drive) is not str or len(drive) != 3 or drive[1:] != ':\\':
            raise ProfileUnavailable('PROFILE_LOCAL_VOLUME_UNPROVED', owner=self)
        self._session_pending = (drive,)
        if self.k.GetDriveTypeW(drive) not in (2, 3, 6):
            raise ProfileUnavailable('PROFILE_LOCAL_VOLUME_UNPROVED', owner=self)
        self._session_pending = None
