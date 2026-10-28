# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Windows named pipes for worker Blenders (``worker_channel``), through ``ctypes`` and kernel32/advapi32 only.

``create_server`` makes one pipe instance that only the current user can open: a protected DACL granting the user's
own SID ``GENERIC_ALL`` and nobody else anything, ``FILE_FLAG_FIRST_PIPE_INSTANCE`` (the call fails if anyone already
holds the name), one instance, and ``PIPE_REJECT_REMOTE_CLIENTS``. ``accept`` waits for the client and keeps it only
when its process ID is the worker's; ``connect`` (the worker) refuses a server that is not Angelo's own process.
Importable on every platform (the host tests stub ``ctypes`` calls); only Windows can call it.
"""

import ctypes
import os
import threading
import time

from .worker_channel import ChannelError

GENERIC_READ, GENERIC_WRITE, GENERIC_ALL = 0x80000000, 0x40000000, 0x10000000
PIPE_ACCESS_INBOUND, PIPE_ACCESS_OUTBOUND = 0x00000001, 0x00000002
FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
PIPE_TYPE_BYTE = PIPE_READMODE_BYTE = PIPE_WAIT = 0x0
PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
OPEN_EXISTING = 3
SECURITY_SQOS_PRESENT, SECURITY_IDENTIFICATION = 0x00100000, 0x00010000
ERROR_PIPE_CONNECTED, ERROR_PIPE_BUSY = 535, 231
TOKEN_QUERY, TOKEN_USER_CLASS = 0x0008, 1
SDDL_REVISION_1 = 1
BUFFER = 65536
INVALID_HANDLE = ctypes.c_void_p(-1).value


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("nLength", ctypes.c_uint32), ("lpSecurityDescriptor", ctypes.c_void_p), ("bInheritHandle", ctypes.c_int)]


def _api():
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
    kernel32.CreateNamedPipeW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                          wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    kernel32.ConnectNamedPipe.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
    kernel32.DisconnectNamedPipe.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.WaitNamedPipeW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD)
    kernel32.GetNamedPipeClientProcessId.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG))
    kernel32.GetNamedPipeServerProcessId.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG))
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
    advapi32.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
    advapi32.GetTokenInformation.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD))
    advapi32.ConvertSidToStringSidW.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR))
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p)
    return kernel32, advapi32, wintypes


def _fail(what):
    return ChannelError("%s failed (Windows error %d)" % (what, ctypes.get_last_error()))


def current_user_sid():
    """The current process token's user SID, as a string (``S-1-5-21-...``)."""
    kernel32, advapi32, wintypes = _api()
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
        raise _fail("OpenProcessToken")
    try:
        size = wintypes.DWORD()
        advapi32.GetTokenInformation(token, TOKEN_USER_CLASS, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(token, TOKEN_USER_CLASS, buffer, size, ctypes.byref(size)):
            raise _fail("GetTokenInformation")
        sid = ctypes.c_void_p.from_buffer(buffer).value   # TOKEN_USER.User.Sid is its first field.
        text = wintypes.LPWSTR()
        if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise _fail("ConvertSidToStringSidW")
        try:
            return text.value
        finally:
            kernel32.LocalFree(text)
    finally:
        kernel32.CloseHandle(token)


def user_only_sddl(sid):
    """A protected DACL with one entry: the user's SID, full access. No inheritance, no other principal."""
    if not sid.startswith("S-") or any(ch not in "S-0123456789" for ch in sid):
        raise ChannelError("Not a SID")
    return "D:P(A;;GA;;;%s)" % sid


class ServerPipe:
    """One pipe instance Angelo serves; ``stream`` hands its handle to a file object, which then owns it."""

    def __init__(self, handle, name, inbound):
        self.handle, self.name, self.inbound = handle, name, inbound
        self.owned = True


def create_server(name, inbound):
    kernel32, advapi32, _wintypes = _api()
    descriptor = ctypes.c_void_p()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            user_only_sddl(current_user_sid()), SDDL_REVISION_1, ctypes.byref(descriptor), None):
        raise _fail("ConvertStringSecurityDescriptorToSecurityDescriptorW")
    try:
        attributes = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), descriptor, 0)
        mode = (PIPE_ACCESS_INBOUND if inbound else PIPE_ACCESS_OUTBOUND) | FILE_FLAG_FIRST_PIPE_INSTANCE
        handle = kernel32.CreateNamedPipeW(name, mode, PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT
                                           | PIPE_REJECT_REMOTE_CLIENTS, 1, BUFFER, BUFFER, 0,
                                           ctypes.byref(attributes))
    finally:
        kernel32.LocalFree(descriptor)
    if handle in (None, 0, INVALID_HANDLE):
        raise _fail("CreateNamedPipeW")
    return ServerPipe(handle, name, inbound)


def accept(server, process_id, timeout, alive=None):
    """Wait for the worker to open ``server``; refuse (and disconnect) any other process. ``alive()`` False (the
    worker ended before it connected) ends the wait at once."""
    kernel32, _advapi32, wintypes = _api()
    outcome = []

    def wait():
        if kernel32.ConnectNamedPipe(server.handle, None) or ctypes.get_last_error() == ERROR_PIPE_CONNECTED:
            outcome.append(True)
        else:
            outcome.append(False)
    thread = threading.Thread(target=wait, daemon=True, name="AngeloWorkerPipeAccept")
    thread.start()
    deadline = time.monotonic() + timeout
    while thread.is_alive() and time.monotonic() < deadline and (alive is None or alive()):
        thread.join(0.1)
    if thread.is_alive():   # Unblock the waiting call with a connection of our own, then fail the start.
        _unblock(server)
        thread.join(5.0)
        raise ChannelError("The worker did not connect")
    if not outcome or not outcome[0]:
        raise _fail("ConnectNamedPipe")
    client = wintypes.ULONG()
    if not kernel32.GetNamedPipeClientProcessId(server.handle, ctypes.byref(client)) or client.value != process_id:
        kernel32.DisconnectNamedPipe(server.handle)
        raise ChannelError("A process other than the worker opened its pipe")


def _unblock(server):
    kernel32, _advapi32, _wintypes = _api()
    handle = kernel32.CreateFileW(server.name, GENERIC_WRITE if server.inbound else GENERIC_READ, 0, None,
                                  OPEN_EXISTING, 0, None)
    if handle not in (None, 0, INVALID_HANDLE):
        kernel32.CloseHandle(handle)


def connect(name, server_process_id, write):
    """The worker's end of one pipe, only when the process serving it is ``server_process_id`` (Angelo)."""
    kernel32, _advapi32, wintypes = _api()
    for _attempt in range(50):
        handle = kernel32.CreateFileW(name, GENERIC_WRITE if write else GENERIC_READ, 0, None, OPEN_EXISTING,
                                      SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION, None)
        if handle not in (None, 0, INVALID_HANDLE):
            break
        if ctypes.get_last_error() != ERROR_PIPE_BUSY or not kernel32.WaitNamedPipeW(name, 200):
            raise _fail("CreateFileW")
    else:
        raise ChannelError("The worker pipe stayed busy")
    server = wintypes.ULONG()
    if not kernel32.GetNamedPipeServerProcessId(handle, ctypes.byref(server)) or server.value != server_process_id:
        kernel32.CloseHandle(handle)
        raise ChannelError("The worker pipe is not served by Angelo")
    return ServerPipe(handle, name, not write)


def stream(pipe, mode):
    """A binary file object over the pipe's handle; closing it closes the handle."""
    import msvcrt
    flags = (os.O_RDONLY if "r" in mode else os.O_WRONLY) | getattr(os, "O_BINARY", 0)
    descriptor = msvcrt.open_osfhandle(pipe.handle, flags)
    pipe.owned = False
    return os.fdopen(descriptor, mode, buffering=0)


def close_handle(pipe):
    if pipe is not None and pipe.owned:
        pipe.owned = False
        kernel32, _advapi32, _wintypes = _api()
        kernel32.CloseHandle(pipe.handle)
