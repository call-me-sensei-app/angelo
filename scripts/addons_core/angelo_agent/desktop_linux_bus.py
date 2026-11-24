# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A D-Bus connection through GIO by ctypes, for ``desktop_linux``'s worker thread only (never the main thread).

``GioBus`` opens its own private connection to the session bus (``g_dbus_address_get_for_bus_sync`` then
``g_dbus_connection_new_for_address_sync``), never GIO's shared one: that one's ``exit-on-close`` raises SIGTERM in
the whole app when the bus goes away, and its first use can wait without a bound. A private connection's
``exit-on-close`` is false, and opening it happens on the worker, so a stalled bus stalls only the worker.

``call`` converts Python values by D-Bus signature (``s``, ``u``, ``i``, ``b``, ``y``, ``o``, ``as``, ``a{sv}``,
``v``) and back. Every value is checked before the first GVariant is made, so a value that cannot be sent (a lone
surrogate in a string) leaks nothing. A failed call raises ``Unavailable`` with the remote D-Bus error name, when there
is one, and whether it timed out, so callers can tell "no such method" from "no answer".

``LIBRARIES`` is an immutable tuple: what ``ctypes.CDLL`` loads must not be a container a script could change in
place. Standard library only; GIO and GLib are loaded on first use, never at import.
"""

import ctypes

LIBRARIES = (("glib", "libglib-2.0.so.0"), ("gobject", "libgobject-2.0.so.0"), ("gio", "libgio-2.0.so.0"))
G_BUS_TYPE_SESSION = 2
G_IO_ERROR_TIMED_OUT = 24
# G_DBUS_CONNECTION_FLAGS_AUTHENTICATION_CLIENT | G_DBUS_CONNECTION_FLAGS_MESSAGE_BUS_CONNECTION
_CLIENT_FLAGS = 1 | 8
_SIGNATURES = ("s", "o", "u", "i", "b", "y", "v", "as", "a{sv}")


class Unavailable(Exception):
    """The bus, GIO or the service is not there, or the call failed. ``remote``: the D-Bus error name the other side
    sent (``org.freedesktop.DBus.Error.UnknownMethod``), else "". ``timed_out``: no answer within the call's bound."""

    def __init__(self, message, remote="", timed_out=False):
        super().__init__(message)
        self.remote, self.timed_out = remote, timed_out


class _GError(ctypes.Structure):
    _fields_ = [("domain", ctypes.c_uint32), ("code", ctypes.c_int), ("message", ctypes.c_char_p)]


def _checked(signature, value):
    """Raise ValueError now for anything ``GioBus._value`` could not turn into a GVariant."""
    if signature not in _SIGNATURES:
        raise ValueError("unsupported D-Bus signature %r" % signature)
    if signature in ("s", "o"):
        str(value).encode("utf-8")
    elif signature in ("u", "i", "y"):
        int(value)
    elif signature == "v":
        inner, item = value
        _checked(inner, item)
    elif signature == "as":
        for item in value:
            _checked("s", item)
    elif signature == "a{sv}":
        for key, item in value.items():
            _checked("s", key)
            _checked("v", item)


class GioBus:
    """A private connection to the session bus (or to ``address``: the host tests' own daemon)."""

    def __init__(self, address=None):
        try:
            libs = {name: ctypes.CDLL(path) for name, path in LIBRARIES}
        except OSError as error:
            raise Unavailable(str(error)) from None
        glib, gio = libs["glib"], libs["gio"]
        V, P, S = ctypes.c_void_p, ctypes.POINTER(ctypes.POINTER(_GError)), ctypes.c_char_p
        signatures = {
            (gio, "g_dbus_address_get_for_bus_sync"): (V, [ctypes.c_int, V, P]),
            (gio, "g_dbus_connection_new_for_address_sync"): (V, [S, ctypes.c_int, V, V, P]),
            (gio, "g_dbus_connection_get_unique_name"): (S, [V]),
            (gio, "g_dbus_connection_call_sync"): (V, [V, S, S, S, S, V, V, ctypes.c_int, ctypes.c_int, V, P]),
            (gio, "g_dbus_error_get_remote_error"): (V, [ctypes.POINTER(_GError)]),
            (gio, "g_io_error_quark"): (ctypes.c_uint32, []),
            (glib, "g_free"): (None, [V]),
            (glib, "g_error_free"): (None, [ctypes.POINTER(_GError)]),
            (glib, "g_variant_new_string"): (V, [S]),
            (glib, "g_variant_new_object_path"): (V, [S]),
            (glib, "g_variant_new_uint32"): (V, [ctypes.c_uint32]),
            (glib, "g_variant_new_int32"): (V, [ctypes.c_int32]),
            (glib, "g_variant_new_boolean"): (V, [ctypes.c_int]),
            (glib, "g_variant_new_byte"): (V, [ctypes.c_ubyte]),
            (glib, "g_variant_new_variant"): (V, [V]),
            (glib, "g_variant_new_dict_entry"): (V, [V, V]),
            (glib, "g_variant_new_tuple"): (V, [ctypes.POINTER(V), ctypes.c_size_t]),
            (glib, "g_variant_new_array"): (V, [V, ctypes.POINTER(V), ctypes.c_size_t]),
            (glib, "g_variant_type_new"): (V, [S]),
            (glib, "g_variant_type_free"): (None, [V]),
            (glib, "g_variant_get_type_string"): (S, [V]),
            (glib, "g_variant_n_children"): (ctypes.c_size_t, [V]),
            (glib, "g_variant_get_child_value"): (V, [V, ctypes.c_size_t]),
            (glib, "g_variant_get_variant"): (V, [V]),
            (glib, "g_variant_get_string"): (S, [V, V]),
            (glib, "g_variant_get_uint32"): (ctypes.c_uint32, [V]),
            (glib, "g_variant_get_int32"): (ctypes.c_int32, [V]),
            (glib, "g_variant_get_boolean"): (ctypes.c_int, [V]),
            (glib, "g_variant_get_byte"): (ctypes.c_ubyte, [V]),
            (glib, "g_variant_unref"): (None, [V]),
        }
        try:
            for (library, name), (restype, argtypes) in signatures.items():
                function = getattr(library, name)
                function.restype, function.argtypes = restype, argtypes
        except AttributeError as error:
            raise Unavailable(str(error)) from None
        self.glib, self.gio = glib, gio
        error = ctypes.POINTER(_GError)()
        if address is None:
            found = gio.g_dbus_address_get_for_bus_sync(G_BUS_TYPE_SESSION, None, ctypes.byref(error))
            if not found:
                raise self._take(error)
            address = ctypes.string_at(found).decode("utf-8", "replace")
            glib.g_free(found)
        self.connection = gio.g_dbus_connection_new_for_address_sync(address.encode(), _CLIENT_FLAGS, None, None,
                                                                      ctypes.byref(error))
        if not self.connection:
            raise self._take(error)
        self.unique_name = (gio.g_dbus_connection_get_unique_name(self.connection) or b"").decode("ascii", "replace")

    def _take(self, error):
        """An ``Unavailable`` for a filled GError, which is freed."""
        if not error:
            return Unavailable("failed")
        message = (error.contents.message or b"").decode("utf-8", "replace")
        timed_out = (error.contents.domain == self.gio.g_io_error_quark()
                     and error.contents.code == G_IO_ERROR_TIMED_OUT)
        name = self.gio.g_dbus_error_get_remote_error(error)
        remote = ctypes.string_at(name).decode("ascii", "replace") if name else ""
        if name:
            self.glib.g_free(name)
        self.glib.g_error_free(error)
        return Unavailable(message, remote, timed_out)

    def _value(self, signature, value):
        """A floating GVariant for one complete type ``signature`` (consumed by the call); ``_checked`` first."""
        glib = self.glib
        if signature == "s":
            return glib.g_variant_new_string(str(value).encode("utf-8"))
        if signature == "o":
            return glib.g_variant_new_object_path(str(value).encode("utf-8"))
        if signature == "u":
            return glib.g_variant_new_uint32(int(value))
        if signature == "i":
            return glib.g_variant_new_int32(int(value))
        if signature == "b":
            return glib.g_variant_new_boolean(1 if value else 0)
        if signature == "y":
            return glib.g_variant_new_byte(int(value))
        if signature == "v":
            inner, item = value
            return glib.g_variant_new_variant(self._value(inner, item))
        if signature == "as":
            return self._array("s", [self._value("s", item) for item in value])
        entries = [glib.g_variant_new_dict_entry(self._value("s", key), self._value("v", item))
                   for key, item in value.items()]
        return self._array("{sv}", entries)

    def _array(self, element, children):
        kind = self.glib.g_variant_type_new(element.encode("ascii"))
        try:
            items = (ctypes.c_void_p * max(len(children), 1))(*children)
            return self.glib.g_variant_new_array(kind, items, len(children))
        finally:
            self.glib.g_variant_type_free(kind)

    def _python(self, variant):
        glib = self.glib
        kind = glib.g_variant_get_type_string(variant).decode("ascii")
        if kind in ("s", "o", "g"):
            return glib.g_variant_get_string(variant, None).decode("utf-8", "replace")
        if kind == "u":
            return glib.g_variant_get_uint32(variant)
        if kind == "i":
            return glib.g_variant_get_int32(variant)
        if kind == "b":
            return bool(glib.g_variant_get_boolean(variant))
        if kind == "y":
            return glib.g_variant_get_byte(variant)
        if kind == "v":
            inner = glib.g_variant_get_variant(variant)
            try:
                return self._python(inner)
            finally:
                glib.g_variant_unref(inner)
        if kind.startswith(("(", "a")):
            values = []
            for index in range(glib.g_variant_n_children(variant)):
                child = glib.g_variant_get_child_value(variant, index)
                try:
                    values.append(self._python(child))
                finally:
                    glib.g_variant_unref(child)
            return tuple(values) if kind.startswith("(") else values
        return None   # A type no caller here reads.

    def call(self, destination, path, interface, method, signature=(), args=(), timeout_ms=1000):
        """Call ``method``; ``signature``: one complete type per argument. Returns the reply's values as a tuple."""
        for kind, value in zip(signature, args):
            _checked(kind, value)
        children = [self._value(kind, value) for kind, value in zip(signature, args)]
        items = (ctypes.c_void_p * max(len(children), 1))(*children)
        parameters = self.glib.g_variant_new_tuple(items, len(children))
        error = ctypes.POINTER(_GError)()
        reply = self.gio.g_dbus_connection_call_sync(
            self.connection, destination.encode(), path.encode(), interface.encode(), method.encode(), parameters,
            None, 0, int(timeout_ms), None, ctypes.byref(error))
        if not reply:
            raise self._take(error)
        try:
            return self._python(reply)
        finally:
            self.glib.g_variant_unref(reply)
