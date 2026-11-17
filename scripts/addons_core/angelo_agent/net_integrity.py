# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's own network clients, against what a confined agent script could change in the process
(doc/angelo/script_confinement.rst, "Angelo's own I/O").

What Angelo's requests are built from (``ssl``'s context classes and factories, ``urllib.request``'s opener and
handlers, ``http.client``'s connections, ``socket``'s connect) is captured as this module is imported, at the add-on's
registration, before any script runs. ``check`` refuses a request when any of them is no longer what was captured:
the confinement puts back what a script rebinds when it ends, so a difference means the process is not as Angelo
set it up, and Angelo fails closed rather than send a request (and its credentials) through it. The checks run only
in the app (the native guard), where agent scripts run.

Standard library only; no bpy.
"""

import http.client
import socket
import ssl
import urllib.request
from urllib.parse import urlsplit


class Tampered(ssl.SSLError):
    """Angelo's network security setup was changed in this session: a TLS failure, so every client says so."""

    def __init__(self, what):
        super().__init__("Angelo's network security settings were changed in this session (%s); restart Angelo" % what)
        self.what = what


_CAPTURED = tuple((module, name, getattr(module, name)) for module, names in (
    (ssl, ("SSLContext", "SSLSocket", "SSLObject", "create_default_context", "_create_default_https_context")),
    (urllib.request, ("build_opener", "OpenerDirector", "HTTPSHandler", "HTTPHandler", "ProxyHandler",
                      "HTTPRedirectHandler", "Request")),
    (http.client, ("HTTPSConnection", "HTTPConnection")),
    (socket, ("socket", "create_connection", "getaddrinfo")),
) for name in names if hasattr(module, name))
# Where Angelo's requests are sent through: the proxy a credentialed request goes to.
_CAPTURED += tuple((urllib.request, name, getattr(urllib.request, name)) for name in (
    "getproxies", "proxy_bypass", "getproxies_environment", "proxy_bypass_environment")
    if hasattr(urllib.request, name))


def check():
    """Raise ``Tampered`` if anything Angelo's requests are built from is not what it was at registration. Only where
    an agent script can run (the native guard): outside the app, a caller that swaps these (a test's fence, a tool's
    proxy) is no script's doing, as with ``checked``."""
    if not _native_guard():
        return
    for module, name, original in _CAPTURED:
        if module.__dict__.get(name) is not original:
            raise Tampered("%s.%s" % (module.__name__, name))


# The consoles Angelo's code named outside any script (an account made at sign-in or startup): a credentialed request
# goes only to one of them, whatever address an object holding it says now. Immutable, so a script can only rebind
# it, which the confinement puts back.
_ORIGINS = frozenset()


def _origin(url):
    parts = urlsplit(url)
    return parts.scheme.lower(), (parts.hostname or "").lower(), parts.port


def allow_origin(url):
    """Angelo's code names a console it sends credentials to (never from inside a script: nothing is added)."""
    global _ORIGINS
    if not confined():
        _ORIGINS = _ORIGINS | {_origin(url)}


def checked(url):
    """``url`` when its origin is one Angelo named outside a script; otherwise refuse (``Tampered``). Outside the app
    (no native guard: no agent script can run there) every address passes, as before."""
    if _origin(url) not in _ORIGINS and _native_guard():
        raise Tampered("the console address its requests go to")
    return url


def _native_guard():
    try:
        import _bpy
    except ImportError:
        return False
    return getattr(_bpy, "_angelo_guard", None) is not None


def confined():
    """Whether this thread runs an agent script now (outside the app: never)."""
    try:
        import _bpy
    except ImportError:
        return False
    guard = getattr(_bpy, "_angelo_guard", None)
    return bool(guard is not None and guard.confined())
