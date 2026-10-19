# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where the team sign-in's refresh token is kept: the OS credential store, never a file.

The same backends as saved API keys (``key_store``: macOS login Keychain,
Windows Credential Manager, Linux Secret Service), with a separate item kind:
service ``ACCOUNT_SERVICE`` and account ``<console id>@<profile>``, where the
console id is derived from the console origin (a development console never
reads the production sign-in) and the profile is the connections profile
(``saved_keys.profile_id``). Two more item kinds hold what every Angelo
process on the computer shares: the coding agents' grant key
(``GRANT_SERVICE``, ``team_grants``) and the latest access token
(``ACCESS_SERVICE``, ``team_access_share``).

``TokenVault`` calls the backend synchronously from the account's worker
threads, never from Blender's main thread, without prompts, each call bounded
by ``TIMEOUT``. Every store call in the process, including the saved-key
worker's, holds ``key_store.STORE_CALLS``, because the macOS Keychain's
interaction switch is process-wide. When the store can't be used, the sign-in
lasts for this session only and ``reason`` says why.

Standard library only; no bpy.
"""

import threading
import uuid

from . import key_store
from .display_text import Text
from .key_store import KeyStoreError
from .managed_policy import DOMAIN
from .ui_text import n_

# Item kinds, named from the bundle identifier (doc/angelo/app_identity.rst).
ACCOUNT_SERVICE = DOMAIN + ".team-sign-in"  # i18n: OS item service name, a stable identifier
ACCOUNT_COMMENT = ("Saved by Angelo to keep you signed in to your Angelo team. "  # i18n: comment stored in the OS item
                   "Signing out deletes this item.")
GRANT_SERVICE = DOMAIN + ".team-grant-key"  # i18n: OS item service name, a stable identifier
GRANT_COMMENT = ("Saved by Angelo: the key its coding agents use with your Angelo team, "  # i18n: OS item comment
                 "shared by its windows. Signing out deletes this item.")
ACCESS_SERVICE = DOMAIN + ".team-access-token"  # i18n: OS item service name, a stable identifier
ACCESS_COMMENT = ("Saved by Angelo: your Angelo team sign-in's current 10-minute access, "  # i18n: OS item comment
                  "shared by its windows. Signing out deletes this item.")
SIGN_IN_LABEL = "Angelo team sign-in"  # i18n: label of the OS item, a stable identifier
TIMEOUT = 10.0
UNREADABLE = n_("Couldn't read your saved Angelo sign-in ({problem}); sign in again.")
PROBLEMS = {"approval": n_("the key store needs your approval"), "denied": n_("access was denied"),
            "timeout": n_("the key store didn't respond")}
# Fixed fallbacks of a failed call: ``Text``, so the account's problem line translates them where it is shown.
STORE_BUSY = n_("The key store is busy")
STORE_SILENT = n_("The key store didn't respond")
STORE_UNAVAILABLE = n_("The key store is unavailable")
STORE_ERROR = n_("Key store error")
STORE_ERROR_SHORT = n_("key store error")
_NAMESPACE = uuid.UUID("5b4f8a52-1f7e-4c0b-9e3a-6a1d2c7f0b11")  # Angelo team sign-in items.


def account_name(console_url, profile):
    return "%s@%s" % (uuid.uuid5(_NAMESPACE, console_url), profile)


def open_backend(environ, platform, background, service=ACCOUNT_SERVICE, comment=ACCOUNT_COMMENT):
    """``(backend, "")`` for this platform's store with one item kind, or ``(None, reason)``."""
    backend, reason = key_store.open_store(environ, platform, background)
    if backend is None:
        return None, key_store.for_team(reason)
    # Each open_store call returns a new backend; these name this item kind.
    backend.service, backend.comment = service, comment
    return backend, ""


class TokenVault:
    """One refresh-token item. ``read`` / ``write`` / ``delete`` raise ``KeyStoreError``.

    ``write`` returns False when the token was saved but this build can't read
    it back (macOS: another build owns the item); the next launch then asks the
    person to sign in again instead of presenting a stale token.
    """

    def __init__(self, backend, console_url, profile, *, timeout=TIMEOUT, label=SIGN_IN_LABEL):
        self.backend, self.timeout = backend, timeout
        self.account = account_name(console_url, profile)
        self.label = "%s · %s" % (label, console_url.split("://", 1)[-1])
        self.reason = ""  # Set once the store proved unusable for this session.
        self._probed = False

    def _call(self, name, *args):
        if self.reason:
            raise KeyStoreError("unavailable", self.reason)
        outcome, done = {}, threading.Event()

        def run():
            if not key_store.STORE_CALLS.acquire(timeout=self.timeout):
                outcome["error"] = KeyStoreError("timeout", Text(STORE_BUSY))
                done.set()
                return
            try:
                if not self._probed:
                    self._probed = True
                    probe = getattr(self.backend, "probe", None)
                    if probe is not None:
                        probe()
                outcome["value"] = getattr(self.backend, name)(self.account, *args, interactive=False)
            except Exception as error:  # noqa: BLE001 - every failure becomes a KeyStoreError kind.
                outcome["error"] = error
            finally:
                key_store.STORE_CALLS.release()
                done.set()

        threading.Thread(target=run, daemon=True, name="AngeloAccountStore").start()
        if not done.wait(self.timeout):
            raise KeyStoreError("timeout", Text(STORE_SILENT))
        error = outcome.get("error")
        if error is None:
            return outcome.get("value")
        kind = getattr(error, "kind", "failed")
        if kind == "unavailable":
            self.reason = getattr(error, "message", "") or Text(STORE_UNAVAILABLE)
        raise KeyStoreError(kind if kind in key_store.KINDS else "failed",
                            getattr(error, "message", "") or Text(STORE_ERROR))

    def read(self):
        return self._call("read") or None

    def write(self, token):
        return bool(self._call("write", token, self.label))

    def delete(self):
        try:
            return bool(self._call("delete"))
        except KeyStoreError as error:
            # macOS: another build owns the item; overwrite the token instead
            # (an empty item reads as absent), as a removed API key is.
            if error.kind != "approval" or not hasattr(self.backend, "erase"):
                raise
            return bool(self._call("erase"))


_UNTRUSTED = object()  # ``last`` when the store may hold a stale token this process couldn't erase.


class StoredSignIn:
    """The refresh token in the store, and what this process last saw or put there.

    A stored token different from both this process's token and ``last``
    means another Angelo window rotated the sign-in: that token is the live
    one. Store failures never raise; ``write`` returns where the sign-in is
    kept ("saved", "unreadable", "session" or "failed", ``AccountView.stored``).
    """

    def __init__(self, vault):
        self.vault = vault
        self._lock = threading.Lock()
        self._last = None

    def read(self, adopt=False):
        """``(token or None, problem)``. ``adopt``: the token becomes what this process last saw (a restore)."""
        if self.vault is None:
            return None, ""
        try:
            token = self.vault.read()
        except KeyStoreError as error:
            problem = PROBLEMS.get(error.kind)
            return None, Text(UNREADABLE, problem=Text(problem) if problem else error.message or Text(STORE_ERROR_SHORT))
        if adopt and token:
            with self._lock:
                self._last = token
        return token, ""

    def renewed_elsewhere(self, stored, mine):
        with self._lock:
            return bool(stored) and self._last is not _UNTRUSTED and stored not in {mine, self._last}

    def write(self, token):
        if self.vault is None:
            return "session"
        try:
            readable = self.vault.write(token)
        except KeyStoreError:
            # Never leave an older, used token to be presented at the next launch;
            # if even deleting fails, never adopt what the store holds this session.
            if not self.delete():
                with self._lock:
                    self._last = _UNTRUSTED
            return "session" if self.vault.reason else "failed"
        with self._lock:
            self._last = token
        return "saved" if readable else "unreadable"

    def delete(self):
        """True when the store holds no token afterwards."""
        if self.vault is None:
            return True
        try:
            self.vault.delete()
        except KeyStoreError:
            return False  # Signing in again replaces it; the console refuses a used token anyway.
        with self._lock:
            self._last = None
        return True
