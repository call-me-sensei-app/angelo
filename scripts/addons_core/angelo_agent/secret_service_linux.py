# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Linux desktop keyring (freedesktop Secret Service) through libsecret.

The Linux backend of ``key_store`` (which documents the shared contract). This
is the only module that calls libsecret, through ctypes on
``libsecret-1.so.0`` and the GLib libraries it already depends on. No
``secret-tool`` command line (which would put the secret in a process
argument), file, environment variable or Python package is used. The library
is loaded only when the store is opened, never at import.

Item: schema ``SCHEMA`` with string attributes ``service`` (``SERVICE``) and
``account`` (``<connection UUID>@<profile>``), label ``Angelo API key ·
<service name>``, in the collection behind the ``default`` alias (normally the
login keyring). ``secret-tool lookup service 'Angelo API key' account <account>``
finds the same item. An unlocked collection is readable by any program in the
person's desktop session, not only by this app.

Prompts. libsecret's convenience calls (``secret_password_store_sync``,
``secret_password_lookup_sync``) unlock a locked keyring by showing the unlock
prompt and offer no way to refuse, so they would prompt at startup. This module
uses the SecretService/SecretCollection/SecretItem calls instead, which match
the macOS contract: automatic calls search without ``SECRET_SEARCH_UNLOCK`` and
fail with ``approval`` when the keyring is locked; only an explicit load
(``interactive=True``) searches with ``SECRET_SEARCH_UNLOCK`` and may show the
unlock prompt. Saves and deletions never prompt. A provider that prompts on its
own (for example to confirm access) is bounded by ``saved_keys`` deadlines.

Availability. ``open`` (main thread) needs a D-Bus session bus address, a
sandbox allowed to call the Secret Service and a loadable libsecret with every
function used here; ``probe`` (worker thread) needs a running Secret Service
with a ``default`` collection. Without them (headless, SSH, containers, a
desktop without a Secret Service provider) keys stay in memory and the reason
is shown, naming the fix for KDE Plasma (KWallet) and Flatpak.

Flatpak. Inside the sandbox this path is unchanged; it needs
``--talk-name=org.freedesktop.secrets``. libsecret's Secret portal backend
serves only the ``secret_password_*`` calls this module avoids, and the
portal's own backends may prompt (KWallet opens the wallet, oo7-portal unlocks
the collection), so it is not used; see "Linux keyrings, KDE, Flatpak and
Snap" in ``doc/angelo/ai_providers.rst``.

The ctypes buffer holding a secret to write is zeroed once libsecret has
copied it into its own secure memory, which libsecret wipes on release.
Python's own bytes and str copies cannot be zeroed.
"""

import configparser
import ctypes
import os
from contextlib import contextmanager

from .key_store import SERVICE, KeyStoreError, KeyStoreUnavailable, decode_secret, encode_secret

STORE = "Your keyring"
SCHEMA = "org.angelo.ApiKey"
LIBRARIES = {"secret": "libsecret-1.so.0", "glib": "libglib-2.0.so.0",
             "gobject": "libgobject-2.0.so.0", "gio": "libgio-2.0.so.0"}

# libsecret enums (secret-service.h, secret-item.h, secret-schema.h).
SECRET_SERVICE_OPEN_SESSION = 1 << 1
SECRET_COLLECTION_NONE = 0
SECRET_SEARCH_ALL = 1 << 1
SECRET_SEARCH_UNLOCK = 1 << 2
SECRET_SEARCH_LOAD_SECRETS = 1 << 3
SECRET_ITEM_CREATE_REPLACE = 1 << 1
SECRET_SCHEMA_NONE = 0
SECRET_SCHEMA_ATTRIBUTE_STRING = 0
SECRET_ERROR_IS_LOCKED = 2
SECRET_ERROR_NO_SUCH_OBJECT = 3
# GDBusError and GIOErrorEnum codes (gioenums.h).
G_DBUS_UNAVAILABLE = frozenset({2, 3, 11, 15})  # SERVICE_UNKNOWN, NAME_HAS_NO_OWNER, NO_SERVER, DISCONNECTED
G_DBUS_REFUSED = frozenset({9, 10})            # ACCESS_DENIED, AUTH_FAILED (sandbox or policy)
G_DBUS_NO_ANSWER = frozenset({4, 12, 20})      # NO_REPLY, TIMEOUT, TIMED_OUT
G_IO_ERROR_CANCELLED = 19

SECRETS_BUS_NAME = "org.freedesktop.secrets"
FLATPAK_INFO = "/.flatpak-info"
NO_SERVICE = "No keyring service (Secret Service) is running in this session"
NO_COLLECTION = "Your keyring has no default collection; set one up in your keyring manager"
# KDE Plasma's Secret Service is KWallet's (KDE Frameworks 5.97 and later, on by default). It is missing when
# KWallet or that interface is turned off, on older Frameworks, or until kwalletd/ksecretd has started (KWallet
# installs no D-Bus activation file for the name). See "Linux keyrings, KDE, Flatpak and Snap" in ai_providers.rst.
KDE_NO_SERVICE = ("KDE Wallet isn't providing the keyring service: turn on KDE Wallet and “Use KWallet for the "
                  "Secret Service interface” in System Settings, then restart Angelo")
KDE_NO_COLLECTION = "KDE Wallet has no default wallet yet; create one in KDE Wallet Manager, then restart Angelo"


def kde_session(environ):
    """True in a KDE Plasma session: startplasma sets XDG_CURRENT_DESKTOP=KDE and KDE_FULL_SESSION=true."""
    return "KDE" in environ.get("XDG_CURRENT_DESKTOP", "").split(":") or environ.get("KDE_FULL_SESSION") == "true"


def flatpak_bus_access(name, path=None):
    """Inside Flatpak, whether the sandbox may call session-bus ``name``; None outside Flatpak.

    ``/.flatpak-info`` holds the sandbox's effective permissions, overrides
    included, and is the file libsecret itself checks (``FLATPAK_ID`` alone is
    not trusted: a host process can inherit it). Access means the unfiltered
    session bus (``sockets`` lists ``session-bus``) or a ``talk``/``own``
    policy for the name or a prefix covering it (``org.freedesktop.*``).
    Hidden names answer like absent ones, so this is the only way to tell a
    sandbox refusal from a missing service. An unreadable file counts as
    access, leaving the decision to the D-Bus probe.
    """
    info = configparser.ConfigParser(interpolation=None, strict=False, delimiters=("=",))
    info.optionxform = str  # Bus names are case-sensitive.
    try:
        with open(FLATPAK_INFO if path is None else path, encoding="utf-8") as file:
            info.read_file(file)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, configparser.Error):
        return True
    if "session-bus" in info.get("Context", "sockets", fallback="").split(";"):
        return True
    policy = info["Session Bus Policy"] if info.has_section("Session Bus Policy") else {}
    for key, value in policy.items():
        covers = key == name or (key.endswith(".*") and name.startswith(key[:-1]))
        if covers and value.strip() in {"talk", "own"}:
            return True
    return False


def flatpak_reason(environ):
    return ("This Flatpak has no access to your keyring service; to allow it, run: flatpak override --user "
            "--talk-name=%s %s" % (SECRETS_BUS_NAME, environ.get("FLATPAK_ID") or "<app ID>"))


def session_bus_available(environ):
    """True when GLib can find the session bus without autolaunching one (no X11 dbus-launch)."""
    if environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return True
    runtime = environ.get("XDG_RUNTIME_DIR")
    return bool(runtime) and os.path.exists(os.path.join(runtime, "bus"))


class GError(ctypes.Structure):
    _fields_ = [("domain", ctypes.c_uint32), ("code", ctypes.c_int), ("message", ctypes.c_char_p)]


class GList(ctypes.Structure):
    pass


GList._fields_ = [("data", ctypes.c_void_p), ("next", ctypes.POINTER(GList)), ("prev", ctypes.POINTER(GList))]


class SchemaAttribute(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char_p), ("type", ctypes.c_int)]


class Schema(ctypes.Structure):
    """secret-schema.h SecretSchema: up to 32 attributes, a NULL name ends the list."""

    _fields_ = ([("name", ctypes.c_char_p), ("flags", ctypes.c_int), ("attributes", SchemaAttribute * 32),
                 ("reserved", ctypes.c_int)] + [("reserved%d" % index, ctypes.c_void_p) for index in range(1, 8)])


class GLibError(Exception):
    """A GError from libsecret: domain (``secret``, ``dbus``, ``io`` or the quark name) and code."""

    def __init__(self, domain, code):
        self.domain, self.code = domain, int(code)
        super().__init__("%s %d" % (domain, self.code))


_VOID, _ERROR = ctypes.c_void_p, ctypes.POINTER(ctypes.POINTER(GError))
_FUNCTIONS = {
    "secret": (
        ("secret_error_get_quark", ctypes.c_uint32, []),
        ("secret_service_get_sync", _VOID, [ctypes.c_int, _VOID, _ERROR]),
        ("secret_collection_for_alias_sync", _VOID, [_VOID, ctypes.c_char_p, ctypes.c_int, _VOID, _ERROR]),
        ("secret_collection_get_locked", ctypes.c_int, [_VOID]),
        ("secret_service_search_sync", _VOID, [_VOID, ctypes.POINTER(Schema), _VOID, ctypes.c_int, _VOID, _ERROR]),
        ("secret_item_get_locked", ctypes.c_int, [_VOID]),
        ("secret_item_get_secret", _VOID, [_VOID]),
        ("secret_item_create_sync", _VOID, [_VOID, ctypes.POINTER(Schema), _VOID, ctypes.c_char_p, _VOID,
                                            ctypes.c_int, _VOID, _ERROR]),
        ("secret_item_delete_sync", ctypes.c_int, [_VOID, _VOID, _ERROR]),
        ("secret_value_new", _VOID, [_VOID, ctypes.c_ssize_t, ctypes.c_char_p]),
        ("secret_value_get", _VOID, [_VOID, ctypes.POINTER(ctypes.c_size_t)]),
        ("secret_value_unref", None, [_VOID])),
    "glib": (
        ("g_strdup", _VOID, [ctypes.c_char_p]),
        ("g_hash_table_new_full", _VOID, [_VOID, _VOID, _VOID, _VOID]),
        ("g_hash_table_insert", ctypes.c_int, [_VOID, _VOID, _VOID]),
        ("g_hash_table_unref", None, [_VOID]),
        ("g_list_free", None, [_VOID]),
        ("g_error_free", None, [ctypes.POINTER(GError)]),
        ("g_quark_to_string", ctypes.c_char_p, [ctypes.c_uint32])),
    "gobject": (("g_object_unref", None, [_VOID]),),
    "gio": (("g_dbus_error_quark", ctypes.c_uint32, []), ("g_io_error_quark", ctypes.c_uint32, [])),
}


def _address(function):
    """A C function's address (host-test fakes supply plain integers)."""
    return function if isinstance(function, int) else ctypes.cast(function, ctypes.c_void_p).value


class LibSecret:
    """Thin ctypes binding: Python values in and out, GErrors raised as ``GLibError``.

    Handles (services, collections, items) are owned references; the caller
    returns each one with ``release``. ``load`` exists for host tests. ctypes
    releases the GIL during each call, so a blocked prompt never stalls
    Blender's main thread.
    """

    def __init__(self, load=None):
        load = ctypes.CDLL if load is None else load
        libraries = {key: load(name) for key, name in LIBRARIES.items()}  # OSError when missing.
        for key, functions in _FUNCTIONS.items():
            for name, restype, argtypes in functions:
                function = getattr(libraries[key], name)  # AttributeError when too old.
                function.restype, function.argtypes = restype, argtypes
        self.secret, self.glib = libraries["secret"], libraries["glib"]
        self.gobject, self.gio = libraries["gobject"], libraries["gio"]
        self.str_hash, self.str_equal = _address(self.glib.g_str_hash), _address(self.glib.g_str_equal)
        self.free = _address(self.glib.g_free)
        self.domains = {self.secret.secret_error_get_quark(): "secret", self.gio.g_dbus_error_quark(): "dbus",
                        self.gio.g_io_error_quark(): "io"}
        self.schema = Schema(name=SCHEMA.encode(), flags=SECRET_SCHEMA_NONE)
        for index, attribute in enumerate((b"service", b"account")):
            self.schema.attributes[index] = SchemaAttribute(attribute, SECRET_SCHEMA_ATTRIBUTE_STRING)

    def _call(self, function, *args):
        """Call a ``*_sync`` function, adding its trailing (cancellable, error) arguments."""
        error = ctypes.POINTER(GError)()
        result = function(*args, None, ctypes.byref(error))
        if error:
            domain, code = error.contents.domain, error.contents.code
            self.glib.g_error_free(error)  # Its message is never read: it is not ours to show.
            name = self.domains.get(domain)
            if name is None:
                name = (self.glib.g_quark_to_string(domain) or b"glib").decode("ascii", "replace")
            raise GLibError(name, code)
        return result

    @contextmanager
    def _attributes(self, attributes):
        table = self.glib.g_hash_table_new_full(self.str_hash, self.str_equal, self.free, self.free)
        try:
            for key, value in attributes.items():
                self.glib.g_hash_table_insert(table, self.glib.g_strdup(key.encode("utf-8")),
                                              self.glib.g_strdup(value.encode("utf-8")))
            yield table
        finally:
            self.glib.g_hash_table_unref(table)

    def release(self, handle):
        if handle:
            self.gobject.g_object_unref(handle)

    def service(self):
        return self._call(self.secret.secret_service_get_sync, SECRET_SERVICE_OPEN_SESSION)

    def default_collection(self, service):
        """The ``default`` alias's collection, or None when the keyring has none."""
        return self._call(self.secret.secret_collection_for_alias_sync, service, b"default", SECRET_COLLECTION_NONE)

    def collection_locked(self, collection):
        return bool(self.secret.secret_collection_get_locked(collection))

    def item_locked(self, item):
        return bool(self.secret.secret_item_get_locked(item))

    def search(self, service, attributes, flags):
        """Matching items (unlocked first), as owned handles."""
        with self._attributes(attributes) as table:
            head = self._call(self.secret.secret_service_search_sync, service, ctypes.byref(self.schema), table, flags)
        items, node = [], ctypes.cast(head, ctypes.POINTER(GList)) if head else None
        while node:
            items.append(node.contents.data)
            node = node.contents.next
        if head:
            self.glib.g_list_free(head)
        return items

    def item_secret(self, item):
        """The loaded secret's bytes, or None when it was not loaded (a locked item)."""
        value = self.secret.secret_item_get_secret(item)
        if not value:
            return None
        try:
            length = ctypes.c_size_t()
            data = self.secret.secret_value_get(value, ctypes.byref(length))
            return ctypes.string_at(data, length.value) if data and length.value else b""
        finally:
            self.secret.secret_value_unref(value)

    def create_item(self, collection, attributes, label, data):
        """Create the item, replacing one with the same attributes in this collection."""
        buffer = ctypes.create_string_buffer(bytes(data), len(data))
        try:
            value = self.secret.secret_value_new(buffer, len(data), b"text/plain")
        finally:
            ctypes.memset(buffer, 0, len(data))  # libsecret holds its own copy now.
        try:
            with self._attributes(attributes) as table:
                item = self._call(self.secret.secret_item_create_sync, collection, ctypes.byref(self.schema), table,
                                  label.encode("utf-8"), value, SECRET_ITEM_CREATE_REPLACE)
            self.release(item)
        finally:
            self.secret.secret_value_unref(value)

    def delete_item(self, item):
        self._call(self.secret.secret_item_delete_sync, item)


def _error(error, interactive=False, no_service=NO_SERVICE):
    """Map a GLibError to the shared kinds, with a fixed message."""
    domain, code = error.domain, error.code
    where = "%s %d" % (domain, code)
    if domain == "secret" and code == SECRET_ERROR_IS_LOCKED:
        return _locked(interactive, where)
    if domain == "secret" and code == SECRET_ERROR_NO_SUCH_OBJECT:
        return KeyStoreError("unavailable", "The keyring collection no longer exists", where)
    if domain == "dbus" and code in G_DBUS_UNAVAILABLE:
        return KeyStoreError("unavailable", no_service, where)
    if domain == "dbus" and code in G_DBUS_REFUSED:
        return KeyStoreError("unavailable", "The keyring service refused this app (sandbox or policy)", where)
    if domain == "dbus" and code in G_DBUS_NO_ANSWER:
        return KeyStoreError("timeout", "The keyring service didn't respond", where)
    if domain == "io" and code == G_IO_ERROR_CANCELLED:
        return KeyStoreError("denied", "The keyring prompt was canceled", where)
    return KeyStoreError("failed", "Keyring error", where)


def _locked(interactive, code=None):
    if interactive:
        return KeyStoreError("denied", "The keyring stayed locked", code)
    return KeyStoreError("approval", "Your keyring is locked; unlock it or choose Load Saved Key", code)


class SecretServiceStore:
    """API-key items for one service in the default keyring; ``account`` identifies the item.

    ``no_service`` and ``no_collection`` say why the store is unavailable, in
    this desktop's terms.
    """

    def __init__(self, library, service=SERVICE, no_service=NO_SERVICE, no_collection=NO_COLLECTION):
        self.library, self.service = library, service
        self.no_service, self.no_collection = no_service, no_collection

    @classmethod
    def open(cls, environ, load=None):
        """Main-thread checks only: a session bus address, sandbox access and a complete libsecret."""
        if not session_bus_available(environ):
            raise KeyStoreUnavailable("Your keyring can't be used without a desktop session bus (D-Bus), "
                                      "as over SSH or in a container")
        if flatpak_bus_access(SECRETS_BUS_NAME) is False:
            raise KeyStoreUnavailable(flatpak_reason(environ))
        try:
            library = LibSecret(load)
        except OSError:
            raise KeyStoreUnavailable("Your keyring can't be used: libsecret (%s) isn't installed"
                                      % LIBRARIES["secret"]) from None
        except AttributeError:
            raise KeyStoreUnavailable("Your keyring can't be used: the installed libsecret is too old") from None
        if kde_session(environ):
            return cls(library, no_service=KDE_NO_SERVICE, no_collection=KDE_NO_COLLECTION)
        return cls(library)

    def _attributes(self, account):
        return {"service": self.service, "account": account}

    @contextmanager
    def _session(self, interactive=False):
        """The Secret Service connection; every GError inside becomes a KeyStoreError."""
        try:
            service = self.library.service()
        except GLibError as error:
            raise KeyStoreError("unavailable", self.no_service, "%s %d" % (error.domain, error.code)) from None
        try:
            yield service
        except GLibError as error:
            raise _error(error, interactive, self.no_service) from None
        finally:
            self.library.release(service)

    @contextmanager
    def _items(self, service, account, flags):
        items = self.library.search(service, self._attributes(account), flags)
        try:
            yield items
        finally:
            for item in items:
                self.library.release(item)

    def probe(self):
        """Fail as ``unavailable`` without a running Secret Service or a default keyring. Never prompts."""
        with self._session() as service:
            collection = self.library.default_collection(service)
            if not collection:
                raise KeyStoreError("unavailable", self.no_collection)
            self.library.release(collection)

    def read(self, account, interactive=False):
        """The stored secret, or None. Only ``interactive=True`` may show the unlock prompt."""
        flags = SECRET_SEARCH_LOAD_SECRETS | (SECRET_SEARCH_UNLOCK if interactive else 0)
        with self._session(interactive) as service, self._items(service, account, flags) as items:
            if not items:
                return None
            if self.library.item_locked(items[0]):
                raise _locked(interactive)
            data = self.library.item_secret(items[0])
        if data is None:
            raise KeyStoreError("failed", "The keyring didn't return the saved key")
        return decode_secret(data, STORE)

    def write(self, account, secret, label, interactive=False):
        """Create the item or replace its secret, then read it back. Never prompts.

        False: saved, but the keyring locked again before the read-back.
        """
        data = encode_secret(secret, STORE)
        with self._session() as service:
            collection = self.library.default_collection(service)
            if not collection:
                raise KeyStoreError("unavailable", self.no_collection)
            try:
                if self.library.collection_locked(collection):
                    raise _locked(False)
                self.library.create_item(collection, self._attributes(account), label, data)
            finally:
                self.library.release(collection)
        try:
            stored = self.read(account)
        except KeyStoreError as error:
            if error.kind == "approval":
                return False
            raise
        if stored != str(secret):
            raise KeyStoreError("failed", "The saved API key did not read back")  # Never echo it.
        return True

    def delete(self, account, interactive=False):
        """Remove every matching item; False when none existed. Never prompts.

        Unlocked matches are deleted; a locked one is reported as ``approval``.
        """
        with self._session() as service, self._items(service, account, SECRET_SEARCH_ALL) as items:
            locked = [item for item in items if self.library.item_locked(item)]
            for item in items:
                if item not in locked:
                    self.library.delete_item(item)
            if locked:
                raise _locked(False)
            return bool(items)
