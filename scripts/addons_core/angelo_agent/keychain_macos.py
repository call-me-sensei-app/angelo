# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""macOS login-Keychain generic passwords through the Security framework.

The macOS backend of ``key_store`` (which documents the shared contract). This
is the only module that calls Security. It uses SecItemAdd, SecItemUpdate,
SecItemCopyMatching and SecItemDelete through ctypes: no ``security`` command
line (which would put the secret in a process argument), no file, no
environment variable. Errors are ``KeychainError`` with a fixed message and
the numeric OSStatus; a secret is never formatted into text, logged or
printed.

Every call states whether macOS may show a Keychain prompt. The file-based
login keychain ignores ``kSecUseAuthenticationUI`` (Apple DTS: it applies only
to the data protection keychain), so the process-wide
``SecKeychainSetUserInteractionAllowed`` switch is set for the duration of the
call and then restored. Callers hold ``key_store.STORE_CALLS`` around every
call (``saved_keys.SavedKeys`` and ``team_token_store.TokenVault`` do): the
switch is shared by the whole process.

Measured on macOS 26.3 with prompts disallowed, for an item created by another
code identity (an ad-hoc signed app after a rebuild): reading fails at once
with errSecAuthFailed, SecItemUpdate of the secret succeeds but leaves the item
readable by no build until the person approves one, and deleting fails with
errSecInvalidOwnerEdit (``test_blender_keychain_live.py`` records the codes).
None of them prompts or blocks. A locked keychain fails with
errSecInteractionNotAllowed. Without a prompt, all three mean "this build needs
the person's approval", so they share the ``approval`` kind.
"""

import ctypes
from contextlib import contextmanager

from .key_store import COMMENT, SERVICE, KeyStoreError

SECURITY_PATH = "/System/Library/Frameworks/Security.framework/Security"
CORE_FOUNDATION_PATH = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"

SUCCESS, ITEM_NOT_FOUND, DUPLICATE_ITEM = 0, -25300, -25299
# OSStatus -> (kind, fixed message). Kinds drive the actionable status text.
_ERRORS = {
    -25308: ("approval", "Keychain needs your approval, or the keychain is locked"),
    -25293: ("denied", "Keychain access was denied"),
    -25244: ("denied", "Only the app build that created this Keychain item may delete it"),
    -128: ("denied", "Keychain access was canceled"),
    -25291: ("unavailable", "Keychain is not available"),
    -25292: ("unavailable", "Keychain is read-only"),
    -25294: ("unavailable", "No login keychain was found"),
    -25307: ("unavailable", "No default keychain was found"),
    -34018: ("unavailable", "This build is not entitled to use the Keychain"),
}


# Without a prompt these are not a person's refusal: this build lacks approval.
_NEEDS_APPROVAL = frozenset({-25293, -25244})


class KeychainError(KeyStoreError):
    """A Security call failed. Carries an OSStatus and a fixed, secret-free message."""

    def __init__(self, status, interactive=False):
        self.status = int(status)
        kind, message = _ERRORS.get(self.status, ("failed", "Keychain error"))
        if self.status in _NEEDS_APPROVAL and not interactive:
            kind, message = "approval", "This app build needs Keychain approval for this item"
        super().__init__(kind, message, "OSStatus %d" % self.status)


class Const:
    """A Security/CoreFoundation constant referenced by its exported name."""

    __slots__ = ("name",)

    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return isinstance(other, Const) and other.name == self.name

    def __hash__(self):
        return hash(self.name)

    def __repr__(self):
        return self.name


def _query(service, account):
    return {Const("kSecClass"): Const("kSecClassGenericPassword"),
            Const("kSecAttrService"): service, Const("kSecAttrAccount"): account}


class MacKeychain:
    """Generic passwords for one Keychain service; ``account`` identifies the item."""

    comment = COMMENT  # The item kind's comment (``key_store``).

    def __init__(self, framework=None, service=SERVICE):
        self.framework = framework if framework is not None else SecurityFramework()
        self.service = service

    @contextmanager
    def _interaction(self, allowed):
        previous = self.framework.interaction_allowed()
        self.framework.set_interaction_allowed(bool(allowed))
        try:
            yield
        finally:
            self.framework.set_interaction_allowed(previous)

    def read(self, account, interactive=False):
        """The stored secret, or None when this account has no item."""
        query = _query(self.service, account)
        query.update({Const("kSecReturnData"): True, Const("kSecMatchLimit"): Const("kSecMatchLimitOne")})
        with self._interaction(interactive):
            status, data = self.framework.copy_data(query)
        if status == ITEM_NOT_FOUND:
            return None
        if status != SUCCESS:
            raise KeychainError(status, interactive)
        try:
            return bytes(data or b"").decode("utf-8")
        except UnicodeDecodeError:
            raise KeychainError(-26275) from None  # errSecDecode; never echo the bytes.

    def write(self, account, secret, label, interactive=False):
        """Create the item or replace its secret in place, then read it back.

        True: this build reads back exactly ``secret``. False: the secret was
        saved, but the item belongs to another build of the app, which alone
        may read it until the person approves this build (an explicit load).
        """
        data = str(secret).encode("utf-8")
        attributes = {Const("kSecValueData"): data, Const("kSecAttrLabel"): label,
                      Const("kSecAttrDescription"): self.service, Const("kSecAttrComment"): self.comment}
        with self._interaction(interactive):
            status = self.framework.add({**_query(self.service, account), **attributes})
            if status == DUPLICATE_ITEM:
                status = self.framework.update(_query(self.service, account),
                                               {Const("kSecValueData"): data, Const("kSecAttrLabel"): label})
        if status != SUCCESS:
            raise KeychainError(status, interactive)
        try:
            stored = self.read(account, interactive)
        except KeychainError as error:
            if error.kind == "approval":
                return False
            raise
        if stored != str(secret):
            raise KeychainError(-26275)  # Saved value did not read back; report, never echo it.
        return True

    def delete(self, account, interactive=False):
        """Remove the item; False when it did not exist."""
        with self._interaction(interactive):
            status = self.framework.delete(_query(self.service, account))
        if status == ITEM_NOT_FOUND:
            return False
        if status != SUCCESS:
            raise KeychainError(status, interactive)
        return True

    def erase(self, account, interactive=False):
        """Overwrite the secret with nothing when this build may not delete the item.

        Updating needs no ownership, so a removed connection's key does not stay
        in the item; an empty item reads as absent. Success is the OSStatus
        only: reading another build's item back would need a prompt. False when
        the item did not exist.
        """
        with self._interaction(interactive):
            status = self.framework.update(_query(self.service, account), {Const("kSecValueData"): b""})
        if status == ITEM_NOT_FOUND:
            return False
        if status != SUCCESS:
            raise KeychainError(status, interactive)
        return True


class _CallBacks(ctypes.Structure):
    # Only the address of CoreFoundation's exported callback tables is used.
    _fields_ = [("opaque", ctypes.c_void_p * 6)]


class SecurityFramework:
    """Thin ctypes binding: Python dicts in, OSStatus (and bytes) out.

    Every CoreFoundation object created for a call is released before return.
    ctypes releases the GIL during each foreign call, so a blocked Keychain
    prompt on the worker thread never stalls Blender's main thread.
    """

    def __init__(self, security_path=SECURITY_PATH, core_foundation_path=CORE_FOUNDATION_PATH):
        sec, cf = ctypes.CDLL(security_path), ctypes.CDLL(core_foundation_path)
        void, status = ctypes.c_void_p, ctypes.c_int32
        for name, restype, argtypes in (
                ("CFStringCreateWithCString", void, [void, ctypes.c_char_p, ctypes.c_uint32]),
                ("CFDataCreate", void, [void, ctypes.c_char_p, ctypes.c_long]),
                ("CFDataGetLength", ctypes.c_long, [void]),
                ("CFDataGetBytePtr", void, [void]),
                ("CFDataGetTypeID", ctypes.c_ulong, []),
                ("CFGetTypeID", ctypes.c_ulong, [void]),
                ("CFDictionaryCreate", void, [void, ctypes.POINTER(void), ctypes.POINTER(void), ctypes.c_long, void, void]),
                ("CFRelease", None, [void])):
            function = getattr(cf, name)
            function.restype, function.argtypes = restype, argtypes
        for name, argtypes in (("SecItemAdd", [void, ctypes.POINTER(void)]), ("SecItemUpdate", [void, void]),
                               ("SecItemCopyMatching", [void, ctypes.POINTER(void)]), ("SecItemDelete", [void]),
                               ("SecKeychainGetUserInteractionAllowed", [ctypes.POINTER(ctypes.c_ubyte)]),
                               ("SecKeychainSetUserInteractionAllowed", [ctypes.c_ubyte])):
            # Missing interaction control (a future macOS) must fail here:
            # without it an automatic call could block on a prompt.
            function = getattr(sec, name)
            function.restype, function.argtypes = status, argtypes
        self.sec, self.cf = sec, cf
        self.true = void.in_dll(cf, "kCFBooleanTrue").value
        self.key_callbacks = ctypes.addressof(_CallBacks.in_dll(cf, "kCFTypeDictionaryKeyCallBacks"))
        self.value_callbacks = ctypes.addressof(_CallBacks.in_dll(cf, "kCFTypeDictionaryValueCallBacks"))
        self._constants = {}

    def _constant(self, const):
        if const.name not in self._constants:
            library = self.cf if const.name.startswith("kCF") else self.sec
            value = ctypes.c_void_p.in_dll(library, const.name).value
            if not value:
                raise OSError("Security constant %s is unavailable" % const.name)
            self._constants[const.name] = value
        return self._constants[const.name]

    def _value(self, value, owned):
        if isinstance(value, Const):
            return self._constant(value)
        if value is True:
            return self.true
        if isinstance(value, str):
            created = self.cf.CFStringCreateWithCString(None, value.encode("utf-8"), 0x08000100)
        elif isinstance(value, (bytes, bytearray)):
            created = self.cf.CFDataCreate(None, bytes(value), len(value))
        else:
            raise TypeError("Unsupported Keychain attribute value type")
        if not created:
            raise MemoryError("CoreFoundation could not allocate a Keychain attribute")
        owned.append(created)
        return created

    def _dictionary(self, values, owned):
        keys = (ctypes.c_void_p * len(values))(*[self._constant(key) for key in values])
        items = (ctypes.c_void_p * len(values))(*[self._value(value, owned) for value in values.values()])
        created = self.cf.CFDictionaryCreate(None, keys, items, len(values), self.key_callbacks, self.value_callbacks)
        if not created:
            raise MemoryError("CoreFoundation could not allocate a Keychain query")
        owned.append(created)
        return created

    def _release(self, owned):
        for value in reversed(owned):
            self.cf.CFRelease(value)

    def add(self, attributes):
        owned = []
        try:
            return self.sec.SecItemAdd(self._dictionary(attributes, owned), None)
        finally:
            self._release(owned)

    def update(self, query, attributes):
        owned = []
        try:
            return self.sec.SecItemUpdate(self._dictionary(query, owned), self._dictionary(attributes, owned))
        finally:
            self._release(owned)

    def copy_data(self, query):
        owned, result = [], ctypes.c_void_p()
        try:
            status = self.sec.SecItemCopyMatching(self._dictionary(query, owned), ctypes.byref(result))
            if result.value:
                owned.append(result.value)
            if status != SUCCESS:
                return status, None
            if not result.value or self.cf.CFGetTypeID(result.value) != self.cf.CFDataGetTypeID():
                return -26275, None  # errSecDecode: not the data this module stored.
            length = self.cf.CFDataGetLength(result.value)
            return status, ctypes.string_at(self.cf.CFDataGetBytePtr(result.value), length)
        finally:
            self._release(owned)

    def delete(self, query):
        owned = []
        try:
            return self.sec.SecItemDelete(self._dictionary(query, owned))
        finally:
            self._release(owned)

    def interaction_allowed(self):
        state = ctypes.c_ubyte(1)
        status = self.sec.SecKeychainGetUserInteractionAllowed(ctypes.byref(state))
        if status != SUCCESS:
            raise KeychainError(status)
        return bool(state.value)

    def set_interaction_allowed(self, allowed):
        status = self.sec.SecKeychainSetUserInteractionAllowed(1 if allowed else 0)
        if status != SUCCESS:
            raise KeychainError(status)
