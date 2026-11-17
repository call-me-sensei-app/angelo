# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether a file or folder belongs to the user this process runs as, on every platform: ``owned_by_this_user``.

POSIX answers with the owner ID (``st_uid`` against ``os.geteuid()``). Windows has neither: ``os.getuid`` and
``os.geteuid`` do not exist there, so ``info.st_uid != os.getuid()`` raises ``AttributeError`` (which no
``except OSError`` takes), and ``st_uid`` is always 0. There the answer is the object's owner SID
(``GetNamedSecurityInfoW``) against this process's own: its token's user, and its token's default owner, which is
what Windows records as the owner of what the process creates (the Administrators group for an elevated
administrator). The check is asked where POSIX asks it, never skipped: before the external audit of 2026-11-14 (F1)
the headless sweep (``headless_working_copy.sweep``) would have died on Windows at this question, and a version
that skipped it there would remove folders without knowing whose they are.

What cannot be read is not this user's: a missing path, a denied query and a failed call all answer False.
A link is asked about as Windows resolves it; callers that must not follow one refuse links first, as the sweep does.

Standard library only; no bpy. ``ctypes`` is loaded on Windows only, at the first question.
"""

import os

WINDOWS = os.name == "nt"
_SE_FILE_OBJECT, _OWNER_SECURITY_INFORMATION = 1, 0x00000001
_TOKEN_QUERY, _TOKEN_USER, _TOKEN_OWNER = 0x0008, 1, 4
_own = None   # This process's (user SID, default owner SID): a token's do not change while it runs.


def owned_by_this_user(path, info=None):
    """True when ``path`` is owned by the user this process runs as. ``info``: its ``os.lstat``, when the caller
    already has it (POSIX reads the owner from it; Windows asks by name)."""
    if WINDOWS:
        try:
            return _windows_owner(path) in _windows_own()
        except OSError:
            return False
    try:
        info = os.lstat(path) if info is None else info
    except OSError:
        return False
    return info.st_uid == os.geteuid()


def _api():
    import ctypes
    from ctypes import wintypes
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.GetNamedSecurityInfoW.argtypes = (
        wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.ConvertSidToStringSidW.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR))
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD))
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
    kernel32.LocalFree.restype = ctypes.c_void_p
    return ctypes, wintypes, advapi32, kernel32


def _sid_text(api, sid):
    """A SID as text (``S-1-5-21-...``)."""
    ctypes, wintypes, advapi32, kernel32 = api
    text = wintypes.LPWSTR()
    if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return text.value
    finally:
        kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))


def _windows_owner(path):
    """The owner SID of the file or folder at ``path``, as text. Raises ``OSError``."""
    api = _api()
    ctypes, _wintypes, advapi32, kernel32 = api
    owner, descriptor = ctypes.c_void_p(), ctypes.c_void_p()
    status = advapi32.GetNamedSecurityInfoW(os.fspath(path), _SE_FILE_OBJECT, _OWNER_SECURITY_INFORMATION,
                                            ctypes.byref(owner), None, None, None, ctypes.byref(descriptor))
    if status:   # A Windows error code; 0 is success.
        raise ctypes.WinError(status)
    try:
        return _sid_text(api, owner.value)   # The SID lives in the descriptor, freed below.
    finally:
        kernel32.LocalFree(descriptor)


def _windows_own():
    """``(this process's user SID, its default owner SID)``, as text. Raises ``OSError``."""
    global _own
    if _own is None:
        api = _api()
        ctypes, wintypes, advapi32, kernel32 = api
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            _own = tuple(_token_sid(api, token, kind) for kind in (_TOKEN_USER, _TOKEN_OWNER))
        finally:
            kernel32.CloseHandle(token)
    return _own


def _token_sid(api, token, kind):
    """The SID a token names as its user or its default owner: the first field of either structure."""
    ctypes, wintypes, advapi32, _kernel32 = api
    size = wintypes.DWORD()
    advapi32.GetTokenInformation(token, kind, None, 0, ctypes.byref(size))   # Asks for the size; fails by design.
    buffer = ctypes.create_string_buffer(size.value)
    if not advapi32.GetTokenInformation(token, kind, buffer, size, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    return _sid_text(api, ctypes.c_void_p.from_buffer(buffer).value)
