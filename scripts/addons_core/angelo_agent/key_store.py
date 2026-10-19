# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The OS stores that remember BYOK API keys: one contract, three backends.

Each backend is the only caller of its OS API:

* macOS: ``keychain_macos.MacKeychain``, the login Keychain (Security framework).
* Windows: ``credential_manager_windows.CredentialManagerStore``, Credential
  Manager (advapi32).
* Linux: ``secret_service_linux.SecretServiceStore``, the Secret Service
  keyring (libsecret).

Every backend keeps one item per connection, identified by service
``SERVICE`` and account ``<connection UUID>@<profile>`` (``saved_keys``), so a
profile chosen with ANGELO_CONNECTIONS_DIR never reads another profile's keys.
No backend has a plaintext or file fallback: when the store can't be used,
keys stay in memory and the reason is shown.

Backend contract. ``saved_keys.SavedKeys`` calls it on ONE worker thread,
never on Blender's main thread, and applies its own deadlines:

``read(account, interactive=False)``
    The secret, or None when this account has no item. An empty secret reads
    as absent.
``write(account, secret, label, interactive=False)``
    Create the item or replace its secret, then read it back. True: this
    process reads back exactly ``secret``. False: saved, but this process
    can't read it back without the person's approval (macOS: another build
    owns the item; Linux: the keyring locked again before the read-back).
``delete(account, interactive=False)``
    Remove the item; False when it did not exist.
``erase(account, interactive=False)``, optional
    Overwrite the secret with nothing when ``delete`` needs an approval this
    build lacks (macOS only).
``probe()``, optional
    Run once on the worker before any other call. Raises ``KeyStoreError``
    with kind ``unavailable`` when the store can't be used in this session at
    all (no keyring service, no credential vault), which switches the session
    to memory-only.

Only ``interactive=True`` (the person's explicit Load Saved Key) may show an OS
prompt. Failures raise ``KeyStoreError`` (or a subclass) whose ``kind`` is one
of ``KINDS`` and whose message is fixed and secret-free.

A backend's ``service`` (and, where the store has one, ``comment``) attribute
names its item kind: ``SERVICE`` for saved API keys, or the team sign-in's
refresh token (``team_token_store``). Every call to any backend holds
``STORE_CALLS``: the macOS Keychain's interaction switch is process-wide, so
the saved-key worker and the team sign-in must never call it concurrently.
"""

import os
import sys
import threading
from dataclasses import dataclass

from . import product_env
from .display_text import Text
from .ui_text import n_

SERVICE = "Angelo API key"  # i18n: OS item service name, a stable identifier
COMMENT = ("Saved by Angelo for one AI Providers connection. "  # i18n: comment stored in the OS item when it is written
           "Removing that connection deletes this item.")
STORE_CALLS = threading.Lock()  # Held by every backend call in this process (see above).

# ANGELO_DISABLE_KEYCHAIN=1 (or a deprecated spelling, through product_env,
# where the ANGELO_ name wins) turns every backend off. The name predates the
# Windows and Linux backends and is kept: suites, the evaluation harness and
# people's shells already set it.
DISABLE_FLAG = "DISABLE_KEYCHAIN"
DISABLE_ENV = product_env.PREFIX + DISABLE_FLAG

# Why no store is used, as whole sentences. Reasons are ``Text``: English for logs and
# tests, translated where the UI shows them (``display_text.shown``).
MEMORY_ONLY = Text(n_("Keys stay in memory; enter them again after restarting."))
TURNED_OFF = n_("{title} is turned off for this session ({variable}); keys stay in memory.")
BACKGROUND_MODE = n_("{title} isn't used in background mode; keys stay in memory.")
UNUSABLE = n_("{reason}; keys stay in memory.")
# The team sign-in shares these stores: what lasts only for the session is the sign-in, not keys.
_FOR_TEAM = {
    TURNED_OFF: n_("{title} is turned off for this session ({variable}); your team sign-in lasts until Angelo quits."),
    BACKGROUND_MODE: n_("{title} isn't used in background mode; your team sign-in lasts until Angelo quits."),
    UNUSABLE: n_("{reason}; your team sign-in lasts until Angelo quits."),
}

# Failure kinds. ``timeout`` is assigned by SavedKeys when a deadline passes.
KINDS = frozenset({"approval", "denied", "unavailable", "failed", "timeout", "too_long"})


class KeyStoreError(Exception):
    """A key-store call failed: a kind from ``KINDS``, a fixed message and the platform code."""

    def __init__(self, kind, message, code=None):
        self.kind, self.message, self.code = kind, message, code
        super().__init__(message if code is None else "%s (%s)" % (message, code))


class KeyStoreUnavailable(KeyStoreError):
    """The store can't be opened; ``message`` says why, for the person."""

    def __init__(self, message, code=None):
        super().__init__("unavailable", message, code)


def encode_secret(secret, store, limit=None):
    """UTF-8 bytes of a secret to save. Empty and oversized secrets are refused, never echoed."""
    data = str(secret).encode("utf-8")
    if not data:
        raise KeyStoreError("failed", "An empty API key is not saved")
    if limit is not None and len(data) > limit:
        raise KeyStoreError("too_long", "This API key is longer than %s accepts" % store,
                            "%d of %d bytes" % (len(data), limit))
    return data


def decode_secret(data, store):
    try:
        return bytes(data).decode("utf-8")
    except UnicodeDecodeError:
        raise KeyStoreError("failed", "%s returned data that is not an API key" % store) from None


@dataclass(frozen=True)
class StoreText:
    """How the UI names one platform's store. Sentences are built in ``saved_keys_ui``.

    Names are ``Text`` (translated where shown, then filled into the sentence templates). The
    last five are whole labels and tooltips for Blender's own widgets: they are registered at
    start-up, so each is a complete message id per platform rather than a template.
    """

    name: Text             # inside a sentence: "Key saved in <name>"
    title: Text            # at the start of a status: "<title> didn't respond"
    location: Text         # form: "On: saved in <location> for this profile."
    manager: Text          # where a person deletes an item by hand
    locked: Text           # a short reason, inside parentheses
    unavailable_here: Text  # the backend could not be opened at all
    waiting: Text          # status while an explicit load may show a prompt
    approval: Text         # status when an automatic load needs the person's approval
    remember: str          # the form's Remember checkbox
    remember_description: str
    load_label: str        # the explicit Load Saved Key operator
    load_description: str
    remove_description: str  # the Remove Connection tooltip


_TEXT = {
    "darwin": StoreText(
        name=Text(n_("Keychain")), title=Text(n_("Keychain")), location=Text(n_("your macOS login Keychain")),
        manager=Text(n_("Keychain Access")), locked=Text(n_("keychain locked or approval needed")),
        unavailable_here=Text(n_("Keychain is unavailable on this Mac")),
        waiting=Text(n_("Waiting for Keychain approval · answer the macOS prompt")),
        approval=Text(n_("Keychain approval needed · choose Load Saved Key, or re-enter the key")),
        remember=n_("Remember in Keychain"),
        remember_description=n_("Save this API key in your macOS login Keychain so it is available after restarting. "
                                "Off: the key stays in memory for this session only"),
        load_label=n_("Load Saved Key from Keychain"),
        load_description=n_("Read this connection's API key saved in your macOS Keychain. "
                            "macOS may ask you to allow access"),
        remove_description=n_("Remove this saved connection, stop its linked turns and delete its key saved in "
                              "Keychain; keep the provider account signed in")),
    "win32": StoreText(
        name=Text(n_("Credential Manager")), title=Text(n_("Credential Manager")),
        location=Text(n_("Windows Credential Manager")), manager=Text(n_("Credential Manager (Windows Credentials)")),
        locked=Text(n_("credential manager locked or approval needed")),
        unavailable_here=Text(n_("Credential Manager is unavailable on this computer")),
        waiting=Text(n_("Loading saved key from Credential Manager…")),
        approval=Text(n_("Credential Manager approval needed · choose Load Saved Key, or re-enter the key")),
        remember=n_("Remember in Credential Manager"),
        remember_description=n_("Save this API key in Windows Credential Manager so it is available after "
                                "restarting. Off: the key stays in memory for this session only"),
        load_label=n_("Load Saved Key from Credential Manager"),
        load_description=n_("Read this connection's API key saved in Windows Credential Manager"),
        remove_description=n_("Remove this saved connection, stop its linked turns and delete its key saved in "
                              "Credential Manager; keep the provider account signed in")),
    "linux": StoreText(
        name=Text(n_("your keyring")), title=Text(n_("Keyring")),
        location=Text(n_("your desktop keyring (Secret Service)")), manager=Text(n_("your keyring manager")),
        locked=Text(n_("keyring locked or approval needed")),
        unavailable_here=Text(n_("Your keyring can't be used here")),
        waiting=Text(n_("Waiting for your keyring · answer the unlock prompt")),
        approval=Text(n_("Keyring locked · choose Load Saved Key to unlock it, or re-enter the key")),
        remember=n_("Remember in your keyring"),
        remember_description=n_("Save this API key in your desktop keyring (Secret Service) so it is available "
                                "after restarting. Off: the key stays in memory for this session only"),
        load_label=n_("Load Saved Key from your keyring"),
        load_description=n_("Read this connection's API key saved in your keyring. "
                            "Your desktop may ask you to unlock the keyring"),
        remove_description=n_("Remove this saved connection, stop its linked turns and delete its key saved in "
                              "your keyring; keep the provider account signed in")),
}
_OTHER = StoreText(
    name=Text(n_("your system key store")), title=Text(n_("Key store")), location=Text(n_("your system key store")),
    manager=Text(n_("your key store")), locked=Text(n_("key store locked or approval needed")),
    unavailable_here=Text(n_("No supported key store on this system")),
    waiting=Text(n_("Loading saved key…")),
    approval=Text(n_("Key store approval needed · choose Load Saved Key, or re-enter the key")),
    remember=n_("Remember in your system key store"),
    remember_description=n_("Save this API key in your system key store so it is available after restarting. "
                            "Off: the key stays in memory for this session only"),
    load_label=n_("Load Saved Key from your system key store"),
    load_description=n_("Read this connection's saved API key"),
    remove_description=n_("Remove this saved connection, stop its linked turns and delete its key saved in "
                          "your system key store; keep the provider account signed in"))


def store_text(platform=None):
    return _TEXT.get(sys.platform if platform is None else platform, _OTHER)


def store_policy(environ, platform, background):
    """Whether to open this platform's store, and why not: ``(available, reason)``. No I/O."""
    if platform not in _TEXT:
        return False, MEMORY_ONLY
    text = store_text(platform)
    if product_env.flag(DISABLE_FLAG, environ):
        _value, variable = product_env.lookup(DISABLE_FLAG, environ)
        return False, Text(TURNED_OFF, title=text.title, variable=variable)
    if background:
        # No person can answer a prompt, and automation must not touch a person's key store.
        return False, Text(BACKGROUND_MODE, title=text.title)
    return True, ""


def for_team(reason):
    """A store ``reason`` worded for the team sign-in, which lasts for the session when no store is usable."""
    if isinstance(reason, Text) and reason.template in _FOR_TEAM:
        return Text(_FOR_TEAM[reason.template], **reason.fields)
    replaced = str(reason).replace("keys stay in memory", "your team sign-in lasts until Angelo quits")
    return reason if replaced == reason else replaced


def open_store(environ=None, platform=None, background=False):
    """This session's backend and ``""``, or ``(None, reason)``.

    Runs on the main thread, so it only loads the OS library and checks local
    state; anything that can wait on a service runs later in ``probe``.
    """
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    available, reason = store_policy(environ, platform, background)
    if not available:
        return None, reason
    try:
        if platform == "darwin":
            from .keychain_macos import MacKeychain
            return MacKeychain(), ""
        if platform == "win32":
            from .credential_manager_windows import CredentialManagerStore
            return CredentialManagerStore(), ""
        from .secret_service_linux import SecretServiceStore
        return SecretServiceStore.open(environ), ""
    except KeyStoreUnavailable as error:
        return None, Text(UNUSABLE, reason=error.message)
    except (OSError, AttributeError, ValueError, KeyStoreError):
        return None, Text(UNUSABLE, reason=store_text(platform).unavailable_here)
