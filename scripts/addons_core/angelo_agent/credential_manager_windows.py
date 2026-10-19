# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Windows Credential Manager generic credentials through advapi32.

The Windows backend of ``key_store`` (which documents the shared contract).
This is the only module that calls the Credential Manager API: CredWriteW,
CredReadW, CredDeleteW and CredFree through ctypes. No ``cmdkey`` command line
(which would put the secret in a process argument), file or environment
variable is used.

Item: type CRED_TYPE_GENERIC, target ``<SERVICE>:<account>`` (unique per
connection and profile; Windows compares targets case-insensitively), user
name ``<account>``, persistence CRED_PERSIST_LOCAL_MACHINE (this user on this
computer; never roams to other computers), and the secret as UTF-8 bytes in
CredentialBlob (at most CRED_MAX_CREDENTIAL_BLOB_SIZE bytes). Windows encrypts
the vault for the signed-in user; like every generic credential, the item is
readable by any program running as that user, not only by this app.

Generic credentials never show a prompt, so ``interactive`` changes nothing
here and there is no ``approval`` failure. A sign-in session without a
credential vault (for example an SSH session signed in with a key) fails with
ERROR_NO_SUCH_LOGON_SESSION; ``probe`` reports it as ``unavailable``, which
keeps keys in memory for the session.

The ctypes buffer holding a secret to write is zeroed after the call, and the
returned CredentialBlob is zeroed before CredFree. Python's own bytes and str
copies cannot be zeroed.
"""

import ctypes

from .key_store import COMMENT, SERVICE, KeyStoreError, decode_secret, encode_secret

STORE = "Credential Manager"
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
CRED_MAX_CREDENTIAL_BLOB_SIZE = 5 * 512  # bytes (wincred.h)
CRED_MAX_STRING_LENGTH = 256             # characters, for Comment

ERROR_ACCESS_DENIED = 5
ERROR_NOT_SUPPORTED = 50
ERROR_INVALID_PARAMETER = 87
ERROR_INVALID_FLAGS = 1004
ERROR_NOT_FOUND = 1168
ERROR_NO_SUCH_LOGON_SESSION = 1312
ERROR_BAD_USERNAME = 2202

# Windows error code -> (kind, fixed message).
_ERRORS = {
    ERROR_NO_SUCH_LOGON_SESSION: ("unavailable", "Credential Manager isn't available in this sign-in session "
                                                 "(for example over SSH)"),
    ERROR_NOT_SUPPORTED: ("unavailable", "Credential Manager isn't supported on this Windows edition"),
    ERROR_ACCESS_DENIED: ("denied", "Credential Manager access was denied"),
    ERROR_INVALID_PARAMETER: ("failed", "Credential Manager rejected the request"),
    ERROR_INVALID_FLAGS: ("failed", "Credential Manager rejected the request"),
    ERROR_BAD_USERNAME: ("failed", "Credential Manager rejected the request"),
}


class CredentialManagerError(KeyStoreError):
    def __init__(self, code):
        self.status = int(code)
        kind, message = _ERRORS.get(self.status, ("failed", "Credential Manager error"))
        super().__init__(kind, message, "Windows error %d" % self.status)


DWORD = ctypes.c_uint32
LPBYTE = ctypes.POINTER(ctypes.c_ubyte)


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", DWORD), ("dwHighDateTime", DWORD)]


class CREDENTIALW(ctypes.Structure):
    """wincred.h CREDENTIALW, field for field."""

    _fields_ = [("Flags", DWORD), ("Type", DWORD), ("TargetName", ctypes.c_wchar_p), ("Comment", ctypes.c_wchar_p),
                ("LastWritten", FILETIME), ("CredentialBlobSize", DWORD), ("CredentialBlob", LPBYTE),
                ("Persist", DWORD), ("AttributeCount", DWORD), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", ctypes.c_wchar_p), ("UserName", ctypes.c_wchar_p)]


PCREDENTIALW = ctypes.POINTER(CREDENTIALW)
_BOOL = ctypes.c_int
_FUNCTIONS = (
    ("CredWriteW", _BOOL, [PCREDENTIALW, DWORD]),
    ("CredReadW", _BOOL, [ctypes.c_wchar_p, DWORD, DWORD, ctypes.POINTER(PCREDENTIALW)]),
    ("CredDeleteW", _BOOL, [ctypes.c_wchar_p, DWORD, DWORD]),
    ("CredFree", None, [ctypes.c_void_p]),
)


class CredentialApi:
    """Thin ctypes binding: Python values in, Windows error codes (and bytes) out.

    ``library`` and ``last_error`` exist for host tests; by default advapi32
    is loaded and GetLastError is read through ctypes' saved copy. ctypes
    releases the GIL during each call.
    """

    def __init__(self, library=None, last_error=None):
        if library is None:
            library = ctypes.WinDLL("advapi32", use_last_error=True)  # AttributeError off Windows.
            last_error = ctypes.get_last_error
        for name, restype, argtypes in _FUNCTIONS:
            function = getattr(library, name)
            function.restype, function.argtypes = restype, argtypes
        self.library, self.last_error = library, last_error

    def write(self, target, user_name, comment, data):
        """0, or the Windows error code."""
        size = len(data)
        blob = ctypes.create_string_buffer(bytes(data), size)
        credential = CREDENTIALW(Type=CRED_TYPE_GENERIC, TargetName=target, Comment=comment,
                                 CredentialBlobSize=size, CredentialBlob=ctypes.cast(blob, LPBYTE),
                                 Persist=CRED_PERSIST_LOCAL_MACHINE, UserName=user_name)
        try:
            return 0 if self.library.CredWriteW(ctypes.byref(credential), 0) else self.last_error()
        finally:
            ctypes.memset(blob, 0, size)

    def read(self, target):
        """``(0, bytes)``, or ``(Windows error code, None)``."""
        pointer = PCREDENTIALW()
        if not self.library.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            return self.last_error(), None
        try:
            credential = pointer.contents
            size = credential.CredentialBlobSize
            if not size or not credential.CredentialBlob:
                return 0, b""
            data = ctypes.string_at(credential.CredentialBlob, size)
            ctypes.memset(credential.CredentialBlob, 0, size)
            return 0, data
        finally:
            self.library.CredFree(pointer)

    def delete(self, target):
        """0, or the Windows error code."""
        return 0 if self.library.CredDeleteW(target, CRED_TYPE_GENERIC, 0) else self.last_error()


class CredentialManagerStore:
    """Generic credentials for one service; ``account`` identifies the item."""

    comment = COMMENT  # The item kind's comment (``key_store``).

    def __init__(self, api=None, service=SERVICE):
        self.api = api if api is not None else CredentialApi()
        self.service = service

    def target(self, account):
        return "%s:%s" % (self.service, account)

    def probe(self):
        """Fail as ``unavailable`` when this sign-in session has no credential vault."""
        status, data = self.api.read(self.target("availability-check"))
        del data  # Never an Angelo item: accounts are <uuid>@<profile>.
        if status not in (0, ERROR_NOT_FOUND):
            raise CredentialManagerError(status)

    def read(self, account, interactive=False):
        """The stored secret, or None when this account has no item. Never prompts."""
        status, data = self.api.read(self.target(account))
        if status == ERROR_NOT_FOUND:
            return None
        if status:
            raise CredentialManagerError(status)
        return decode_secret(data, STORE)

    def write(self, account, secret, label, interactive=False):
        """Create the item or replace its secret, then read it back. True when it matches."""
        data = encode_secret(secret, STORE, CRED_MAX_CREDENTIAL_BLOB_SIZE)
        comment = ("%s. %s" % (label, self.comment))[:CRED_MAX_STRING_LENGTH - 1]
        status = self.api.write(self.target(account), account, comment, data)
        if status:
            raise CredentialManagerError(status)
        if self.read(account) != str(secret):
            raise KeyStoreError("failed", "The saved API key did not read back")  # Never echo it.
        return True

    def delete(self, account, interactive=False):
        """Remove the item; False when it did not exist."""
        status = self.api.delete(self.target(account))
        if status == ERROR_NOT_FOUND:
            return False
        if status:
            raise CredentialManagerError(status)
        return True
