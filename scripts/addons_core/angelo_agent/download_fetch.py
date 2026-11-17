# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_download``'s transfer (doc/angelo/downloads.rst): one https file into ``assets/downloads``. Worker thread;
standard library only, no bpy.

* **Public addresses only, pinned.** Each host is resolved once and every answer must be public
  (``download_checks.refused_address``); the connection is made to that address, with the host name kept for TLS
  (certificate and SNI) and the Host header, so a name that resolves somewhere else a moment later (DNS rebinding)
  changes nothing. Through the system proxy (app_http's exact choice, ``_proxy_for``) the tunnel is opened to the
  pinned address too. Angelo's trust roots verify the certificate (``team_http.tls_context``).
* **Where only the proxy resolves names.** When this computer's lookup fails and a proxy is set for the name, a name
  that cannot be internal (``download_names``) goes to the proxy as a name; the result says the proxy checked it.
  A refused answer never falls back, and the route is pinned per name like an address (``_route``).
* **Redirects** only to https, at most ``MAX_REDIRECTS``, each hop resolved, checked and pinned the same way.
* **Caps.** A declared length over ``MAX_BYTES`` is refused before any byte; a body is cut off when it passes it. The
  whole transfer has ``TIME_LIMIT_S``; a connection or read that answers nothing for ``READ_TIMEOUT_S`` ends it. Stop
  ends it too.
* **The file.** Its type is read from its bytes and must be its kind (``download_checks.sniff``). It is written under
  a hidden temporary name in the folder, then placed under a safe name (``download_checks.safe_name``) that never
  replaces another file, created without execute permission, and its resolved path must sit inside the folder; the
  folder itself must be ``assets/downloads`` of the chosen root, reached through no link. A failed or refused
  transfer leaves nothing.

Every refusal is a ``Refused`` with a ``reason`` word and a closed refusal ``code``. ``download`` is a grant entry
(``grant_guard.entry``): agent code cannot call it, whatever thread it is on.
"""

import base64
import hashlib
import http.client
import ipaddress
import os
import secrets
import socket
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlsplit, unquote

from . import download_checks as checks, download_names as names, grant_guard, owned_paths, shared_replace
from .download_spec import FOLDER, MAX_BYTES, MAX_REDIRECTS, READ_TIMEOUT_S, TIME_LIMIT_S, check_url

CHUNK = 65536
HEAD = 65536            # The first bytes kept to read the type from.
REDIRECTS = {301, 302, 303, 307, 308}
TEMPORARY = ".angelo-download-"


class Refused(Exception):
    """A refused or failed download: ``reason`` (a word the agent reads), ``code`` (``refusal_codes``)."""

    def __init__(self, reason, code, message=""):
        super().__init__(message or reason.replace("_", " "))
        self.reason, self.code = reason, code


def tls_context():
    from . import team_http
    return team_http.tls_context()


class PinnedConnection(http.client.HTTPSConnection):
    """An HTTPS connection to ``address`` (the checked, resolved address) that verifies ``host``'s certificate, direct
    or through an HTTP proxy's CONNECT tunnel to that same address. ``proxy``: (host, port, Proxy-Authorization or
    None), or None."""

    def __init__(self, host, address, port, proxy, timeout, context=None):
        if address is None and proxy is None:
            raise ValueError("A direct connection goes to its checked address")
        super().__init__(proxy[0] if proxy else address, proxy[1] if proxy else port, timeout=timeout,
                         context=context if context is not None else tls_context())
        self._name, self._address, self._target_port, self._proxy = host, address, port, proxy
        self._raw = None   # The socket while it connects (``_open``), for the watch.

    def _open(self, host, port):
        """``socket.create_connection``, with each socket on the connection (``_raw``) before it connects, so the
        transfer's watch (``_Watch``) can end a connect that waits."""
        error = OSError("No address to connect to")
        for family, kind, proto, _name, address in socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM):
            sock = self._raw = socket.socket(family, kind, proto)
            try:
                sock.settimeout(self.timeout)
                sock.connect(address)
                return sock
            except OSError as failed:
                error = failed
                sock.close()
        raise error

    def connect(self):
        if self._proxy is None:
            sock = self._open(self._address, self._target_port)
        else:
            sock = self._open(self._proxy[0], self._proxy[1])
            at = self._name if self._address is None else self._address   # None: the proxy resolves the name.
            target = "%s:%d" % ("[%s]" % at if ":" in at else at, self._target_port)
            lines = ["CONNECT %s HTTP/1.1" % target, "Host: %s" % target]
            if self._proxy[2]:
                lines.append("Proxy-Authorization: %s" % self._proxy[2])
            sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
            reply = sock.makefile("rb")
            status = reply.readline(65537).split(None, 2)
            while True:
                line = reply.readline(65537)
                if line in (b"\r\n", b"\n", b""):
                    break
            if len(status) < 2 or status[1] != b"200":
                sock.close()
                raise OSError("The proxy refused the tunnel")
        self.sock = self._context.wrap_socket(sock, server_hostname=self._name, do_handshake_on_connect=False)
        self.sock.do_handshake()   # On the connection first, so the watch can end a handshake that waits.


class _Watch:
    """Ends a transfer the moment its time runs out or Stop is pressed, whatever it waits on (a connect, the TLS
    handshake, a header line, a body read): from its own thread it shuts the open sockets, which wakes the blocked
    call. A server that sends a byte now and then answers every read within ``READ_TIMEOUT_S``, so checks between
    reads alone let it hold the download far past ``TIME_LIMIT_S`` and Stop (Fusion audit trial, 508360d287d)."""

    POLL_S = 0.05
    REASONS = {"too_slow": ("LIMIT_EXCEEDED", "The download took longer than its time limit"),
               "cancelled": ("CANCELLED", "Stopped")}

    def __init__(self, cancel, seconds):
        self.cancel, self.seconds, self.reason = cancel, seconds, None
        self.started, self.done, self.lock, self.connections = time.monotonic(), threading.Event(), threading.Lock(), []
        threading.Thread(target=self._run, name="AngeloDownloadWatch", daemon=True).start()

    def track(self, connection):
        with self.lock:
            self.connections.append(connection)
            if self.reason is not None:
                self._shut(connection)

    def refused(self):
        """The ``Refused`` for why the watch ended the transfer, or None."""
        return Refused(self.reason, *self.REASONS[self.reason]) if self.reason else None

    def end(self):
        self.done.set()

    def _run(self):
        while not self.done.wait(self.POLL_S):
            if self.cancel is not None and self.cancel.is_set():
                reason = "cancelled"
            elif time.monotonic() - self.started > self.seconds:
                reason = "too_slow"
            else:
                continue
            with self.lock:
                self.reason = reason
                for connection in self.connections:
                    self._shut(connection)
            return

    @staticmethod
    def _shut(connection):
        for sock in (getattr(connection, "sock", None), getattr(connection, "_raw", None)):
            if sock is None:
                continue
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except (OSError, ValueError):
                pass


def _connect(host, address, port, proxy, timeout):
    return PinnedConnection(host, address, port, proxy, timeout)


class _HostPort(http.client.HTTPConnection):
    """``HTTPSConnection``'s parse of "host:port" (443 when none), without its SSL context: proxy discovery never
    depends on TLS (Windows VM CI 9c8fa44f2952-20261115102146: a fresh process's default context failed there)."""
    default_port = http.client.HTTPS_PORT


def _proxy_for(host, proxies):
    """(host, port, Proxy-Authorization or None) of the proxy an https request to ``host`` tunnels through, or None:
    exactly app_http's choice (urllib's ProxyHandler over ``getproxies``): the ``https`` entry, whatever its scheme, as
    a CONNECT proxy (a SOCKS entry fails as app_http's does; it never goes direct), its port or 443 (urllib's
    HTTPSConnection), Basic credentials only with both a user name and a password, and ``proxy_bypass``. The tunnel
    then goes to the checked address (``PinnedConnection``). ``proxies``: a stand-in for tests, with its own ``no``."""
    if proxies is None:
        proxies, bypass = urllib.request.getproxies(), urllib.request.proxy_bypass
    else:
        bypass = lambda name: "no" in proxies and urllib.request.proxy_bypass_environment(name, proxies)  # noqa: E731
    url = proxies.get("https")
    if not url or bypass(host):
        return None
    _scheme, user, password, hostport = urllib.request._parse_proxy(url)   # urllib's own parse (ProxyHandler).
    auth = None
    if user and password:
        token = "%s:%s" % (unquote(user), unquote(password))
        auth = "Basic " + base64.b64encode(token.encode()).decode("ascii")
    proxy = _HostPort(unquote(hostport))   # Its host and port as urllib's HTTPSConnection reads them.
    return proxy.host, proxy.port, auth


class _NotFound(Exception):
    """This computer could not look the name up (``_route`` decides whether the proxy may)."""


def proxy_lookup_possible(host):
    """Whether a download of ``host`` may let the system proxy find its address: a proxy is set for it and the name
    cannot be internal (``download_names``). No network: the card says so before anything runs."""
    try:
        proxy = _proxy_for(host, None)
    except ValueError:   # A proxy setting urllib cannot read: the transfer reads as offline, and so does this.
        return False
    return proxy is not None and names.proxy_may_resolve(host, names.search_domains())[0]


def _route(host, port, resolver, proxy, domains):
    """The address this host is reached at, or None when the proxy resolves it: only when this computer could not
    look the name up, a proxy is set for it and the name cannot be internal. A refused answer is never routed."""
    try:
        return _resolve(host, port, resolver)
    except _NotFound:
        if proxy is None or not names.proxy_may_resolve(host, names.search_domains() if domains is False
                                                        else domains)[0]:
            raise Refused("offline", "UNSUPPORTED", "The host's name could not be resolved") from None
        return None


def _resolve(host, port, resolver):
    """The one address this host is reached at: a literal address as written, else the first answer of one lookup,
    and only when every answer is a public address."""
    try:
        ipaddress.ip_address(host)
        found = [host]
    except ValueError:
        try:
            found = [info[4][0].split("%", 1)[0] for info in resolver(host, port, type=socket.SOCK_STREAM)]
        except (OSError, UnicodeError):
            raise _NotFound() from None
    if not found:
        raise Refused("offline", "UNSUPPORTED", "The host's name has no address")
    for address in found:
        reason = checks.refused_address(address)
        if reason is not None:
            raise Refused("address_" + reason, "VALIDATION",
                          "The link leads to a %s address, which downloads never reach" % reason.replace("_", "-"))
    return found[0]


def prepare_folder(root):
    """``<root>/assets/downloads``, made if missing; refused when any part of it is a link or it resolves anywhere
    else."""
    root = Path(root)
    folder = root.joinpath(*FOLDER)
    for index in range(1, len(FOLDER) + 1):
        part = root.joinpath(*FOLDER[:index])
        if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
            raise Refused("outside_folder", "VALIDATION", "The downloads folder is a link to another place")
    try:
        os.makedirs(owned_paths.require_writable(folder), mode=0o755, exist_ok=True)
        real = folder.resolve(strict=True)
        expected = root.resolve(strict=True).joinpath(*FOLDER)
    except OSError:
        raise Refused("folder_unavailable", "UNSUPPORTED", "The downloads folder could not be made") from None
    if real != expected or not real.is_dir():
        raise Refused("outside_folder", "VALIDATION", "The downloads folder leads to another place")
    return real


def _request(url, pinned, resolver, connect, proxies, timeout, domains, watch=None):
    parts = urlsplit(url)
    host = parts.hostname.encode("idna").decode("ascii") if not parts.hostname.isascii() else parts.hostname
    port = parts.port or 443
    proxy = _proxy_for(host, proxies)
    if host not in pinned:   # Once per download: a later answer for the name never swaps the route.
        pinned[host] = _route(host, port, resolver, proxy, domains)
    connection = connect(host, pinned[host], port, proxy, timeout)
    if watch is not None:
        watch.track(connection)
    shown = "[%s]" % host if ":" in host else host
    from .app_http import user_agent
    headers = {"Host": shown if port == 443 else "%s:%d" % (shown, port), "User-Agent": user_agent(),
               "Accept": "*/*", "Accept-Encoding": "identity"}
    path = (parts.path or "/") + ("?" + parts.query if parts.query else "")
    try:
        connection.request("GET", path, headers=headers)
        return host, connection, connection.getresponse()
    except Refused:
        connection.close()
        raise
    except (OSError, http.client.HTTPException, ValueError) as error:
        connection.close()
        import ssl
        reason = ("tls" if isinstance(error, ssl.SSLError) else "timeout" if isinstance(error, TimeoutError)
                  else "offline")
        raise Refused(reason, "UNSUPPORTED", "The server could not be reached (%s)" % reason) from None


def _answer(url, resolver, connect, proxies, timeout, clock, deadline, pinned, domains, watch=None):
    """(final url, host, connection, response) of the first answer that is not a redirect. A redirect may go to 443
    or to the port the person's link named: another port on a checked public address is another service."""
    ports = {443, urlsplit(url).port or 443}
    for hop in range(MAX_REDIRECTS + 1):
        if clock() > deadline:
            raise Refused("too_slow", "LIMIT_EXCEEDED", "The download took longer than its time limit")
        host, connection, response = _request(url, pinned, resolver, connect, proxies, timeout, domains, watch)
        if response.status not in REDIRECTS:
            return url, host, connection, response
        location = response.getheader("Location") or ""
        connection.close()
        if hop == MAX_REDIRECTS:
            raise Refused("too_many_redirects", "LIMIT_EXCEEDED", "More than %d redirects" % MAX_REDIRECTS)
        try:
            url = check_url(urljoin(url, location))
        except ValueError:
            raise Refused("redirect_not_https", "VALIDATION", "The server redirected to a link that is not https") \
                from None
        if (urlsplit(url).port or 443) not in ports:
            raise Refused("redirect_port", "VALIDATION", "The server redirected to another port, which downloads never "
                          "follow")
    raise AssertionError("unreachable")


def _transfer(response, handle, kind, max_bytes, cancel, clock, deadline):
    """Write the body to ``handle``: (bytes, sha256, head)."""
    declared = response.getheader("Content-Length")
    if declared is not None and declared.strip().isdigit() and int(declared) > max_bytes:
        raise Refused("too_large", "LIMIT_EXCEEDED", "The file is larger than %d bytes" % max_bytes)
    digest, head, total, checked = hashlib.sha256(), bytearray(), 0, False
    while True:
        if cancel is not None and cancel.is_set():
            raise Refused("cancelled", "CANCELLED", "Stopped")
        if clock() > deadline:
            raise Refused("too_slow", "LIMIT_EXCEEDED", "The download took longer than its time limit")
        try:
            chunk = response.read(CHUNK)
        except (OSError, http.client.HTTPException) as error:
            reason = "timeout" if isinstance(error, TimeoutError) else "offline"
            raise Refused(reason, "UNSUPPORTED", "The transfer broke off (%s)" % reason) from None
        if not chunk:
            return total, digest.hexdigest(), bytes(head)
        total += len(chunk)
        if total > max_bytes:
            raise Refused("too_large", "LIMIT_EXCEEDED", "The file is larger than %d bytes" % max_bytes)
        if len(head) < HEAD:
            head += chunk[:HEAD - len(head)]
        if not checked and len(head) >= HEAD:   # Early: a page or a program is not read to the end.
            checked = True
            if checks.sniff(kind, bytes(head), None) is None:
                raise Refused("wrong_type", "CHECK_FAILED", "The file is not a %s" % kind)
        digest.update(chunk)
        handle.write(chunk)


def _place(folder, temporary, name):
    """Move ``temporary`` to ``name`` in ``folder`` without replacing a file: the first free of "name", "name (2)",
    ... Each candidate is claimed with an exclusive create, then the finished file renamed over the claim."""
    owned_paths.require_writable(folder)
    stem, dot, ext = name.rpartition(".")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    for number in range(1, 1000):
        candidate = folder / (name if number == 1 else "%s (%d)%s%s" % (stem, number, dot, ext))
        try:
            os.close(os.open(candidate, flags, 0o644))
        except FileExistsError:
            continue
        try:   # On Windows a scanner may hold the new file for a moment (shared_replace retries).
            shared_replace.replace(temporary, candidate)
        except OSError:
            shared_replace.discard(candidate)   # The empty claim; download's own cleanup removes the temporary.
            raise
        if candidate.resolve(strict=True).parent != folder:
            candidate.unlink()
            raise Refused("outside_folder", "VALIDATION", "The file would land outside the downloads folder")
        return candidate
    raise Refused("name_taken", "LIMIT_EXCEEDED", "Too many files of this name in the downloads folder")


def download(url, kind, root, name=None, *, cancel=None, resolver=None, connect=None, proxies=None,
             clock=time.monotonic, max_bytes=MAX_BYTES, time_limit=TIME_LIMIT_S, timeout=READ_TIMEOUT_S,
             search_domains=False):
    """Download ``url`` (an https link) as a ``kind`` file into ``<root>/assets/downloads``; ``name`` is the
    requested file name. Returns {path (relative to ``root``, with /), bytes, sha256, format, host, kind,
    address_checked_by ("angelo", or "proxy" when a hop's name was resolved by the proxy)}; raises ``Refused``.
    ``resolver``, ``connect``, ``proxies`` and ``search_domains`` stand in for the network in tests."""
    try:
        url = check_url(url)
    except ValueError as error:
        raise Refused("bad_url", "VALIDATION", str(error)) from None
    folder = prepare_folder(root)
    watch = _Watch(cancel, time_limit)   # Ends any wait at the limit or Stop, not only between reads.
    try:
        return _download(url, kind, folder, name, cancel, resolver, connect, proxies, clock, max_bytes, time_limit,
                         timeout, search_domains, watch)
    except Exception:
        if watch.refused() is not None:   # The watch shut the socket: say why, not what the shut socket raised.
            raise watch.refused() from None
        raise
    finally:
        watch.end()


def _download(url, kind, folder, name, cancel, resolver, connect, proxies, clock, max_bytes, time_limit, timeout,
              search_domains, watch):
    deadline = clock() + time_limit
    pinned = {}
    final, host, connection, response = _answer(url, resolver or socket.getaddrinfo, connect or _connect, proxies,
                                                timeout, clock, deadline, pinned, search_domains, watch)
    temporary = folder / (TEMPORARY + secrets.token_hex(8) + ".part")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    placed = False
    try:
        owned_paths.require_writable(temporary)   # C first: the part file is made only in a folder it allows.
        if response.status != 200:
            raise Refused("http_%d" % response.status, "UNSUPPORTED", "The server answered %d" % response.status)
        with os.fdopen(os.open(temporary, flags, 0o644), "wb") as handle:
            size, sha256, head = _transfer(response, handle, kind, max_bytes, cancel, clock, deadline)
        fmt = checks.sniff(kind, head, size)
        if fmt is None:
            raise Refused("wrong_type", "CHECK_FAILED", "The file is not a %s" % kind)
        chosen = checks.safe_name(fmt, name or "", checks.link_name(final), checks.link_name(url),
                                  checks.disposition_name(response.getheader("Content-Disposition")))
        placed_at = _place(folder, temporary, chosen)
        placed = True
    finally:
        connection.close()
        if not placed:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    return {"path": "/".join(FOLDER + (placed_at.name,)), "bytes": size, "sha256": sha256, "format": fmt,
            "address_checked_by": "proxy" if None in pinned.values() else "angelo",
            "host": host, "kind": kind}


download = grant_guard.entry(download)
