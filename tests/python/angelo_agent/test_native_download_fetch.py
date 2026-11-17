# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_download``'s transfer (``download_fetch``; doc/angelo/downloads.rst) with a stand-in resolver and
stand-in connections: no bpy, no network.

Covered: the host is resolved once and every answer must be a public address; the connection goes to that resolved
address (pinned), with the host name kept for TLS and the Host header, so a resolver that changes its answer later
(DNS rebinding) changes nothing; through a proxy the tunnel is opened to the pinned address; redirects are followed
only to https and each hop is resolved and checked the same way, at most five; the size cap (a declared length is
refused before any byte, a longer body when it passes the cap) and the time cap; a file whose bytes are not its kind
is removed; the file lands only in ``assets/downloads`` under the chosen folder, never through a link that leads
elsewhere, never over another file, never executable; the result gives the relative path, size and SHA-256; and the
transfer refuses while agent code runs (grant_guard.entry).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).
import machine_clock
import platform_env

import hashlib
import os
import socket
import ssl
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

from angelo_agent import download_fetch as fetch, grant_guard  # noqa: E402
from angelo_agent.download_fetch import Refused  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(256)) * 8
PUBLIC, PUBLIC2, PRIVATE = "93.184.216.34", "93.184.216.35", "10.0.0.5"


class Answer:
    """One scripted HTTP answer: what ``getresponse`` returns."""

    def __init__(self, status=200, body=b"", headers=None, chunk=4096, stall=None):
        self.status, self.body, self.chunk, self.stall = status, body, chunk, stall
        self.headers = dict(headers or {})
        self.position = 0

    def getheaders(self):
        return list(self.headers.items())

    def getheader(self, name, default=None):
        return next((v for k, v in self.headers.items() if k.lower() == name.lower()), default)

    def read(self, size=-1):
        if self.stall:
            self.stall()
        size = self.chunk if size is None or size < 0 else min(size, self.chunk)
        data = self.body[self.position:self.position + size]
        self.position += len(data)
        return data

    def close(self):
        pass


class Net:
    """The stand-in network: ``resolve`` answers per host (a list, or a list per call), ``connect`` records each
    connection and serves the scripted answer for its host and path."""

    def __init__(self):
        self.dns, self.answers, self.connections, self.lookups = {}, {}, [], []

    def resolve(self, host, port, *args, **kwargs):
        self.lookups.append(host)
        found = self.dns[host]
        if isinstance(found, Exception):   # Before it is read as a list of answers.
            raise found
        if found and isinstance(found[0], (list, Exception)):   # A different answer for each lookup (rebinding).
            found = found.pop(0) if len(found) > 1 else found[0]
        if isinstance(found, Exception):
            raise found
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
                for ip in found]

    def connect(self, host, address, port, proxy, timeout):
        net = self

        class Connection:
            def request(self, method, path, headers=None):
                self.method, self.path, self.headers = method, path, dict(headers or {})

            def getresponse(self):
                net.connections.append({"host": host, "address": address, "port": port, "proxy": proxy,
                                        "method": self.method, "path": self.path, "headers": self.headers})
                answer = net.answers[(host, self.path)]
                return answer() if callable(answer) else answer

            def close(self):
                pass
        return Connection()


class FetchCase(unittest.TestCase):
    def setUp(self):
        self.net = Net()
        self.root = Path(tempfile.mkdtemp(prefix="angelo-download-test-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))

    def download(self, url, kind="image", name=None, **kwargs):
        options = dict(resolver=self.net.resolve, connect=self.net.connect, proxies={})
        options.update(kwargs)
        return fetch.download(url, kind, self.root, name, **options)

    def refused(self, url, reason, kind="image", **kwargs):
        with self.assertRaises(Refused) as caught:
            self.download(url, kind, **kwargs)
        self.assertEqual(caught.exception.reason, reason, str(caught.exception))
        return caught.exception

    def files(self):
        return sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file())


class PinningTests(FetchCase):
    def test_the_file_comes_from_the_resolved_public_address_with_the_host_kept_for_tls(self):
        self.net.dns["cdn.example.com"] = [PUBLIC]
        self.net.answers[("cdn.example.com", "/t/brick.png?v=2")] = Answer(body=PNG)
        result = self.download("https://cdn.example.com/t/brick.png?v=2")
        connection = self.net.connections[0]
        self.assertEqual((connection["host"], connection["address"], connection["port"]),
                         ("cdn.example.com", PUBLIC, 443))
        self.assertEqual(connection["headers"]["Host"], "cdn.example.com")
        self.assertEqual(result["path"], "assets/downloads/brick.png")
        self.assertEqual(result["bytes"], len(PNG))
        self.assertEqual(result["sha256"], hashlib.sha256(PNG).hexdigest())
        self.assertEqual(result["format"], "png")
        self.assertEqual((self.root / result["path"]).read_bytes(), PNG)
        self.assertEqual(self.files(), ["assets/downloads/brick.png"])

    def test_a_rebinding_resolver_changes_nothing_after_the_check(self):
        self.net.dns["rebind.example.com"] = [[PUBLIC], ["127.0.0.1"]]
        self.net.answers[("rebind.example.com", "/a.png")] = Answer(body=PNG)
        self.download("https://rebind.example.com/a.png")
        self.assertEqual(self.net.lookups, ["rebind.example.com"], "resolved once")
        self.assertEqual([c["address"] for c in self.net.connections], [PUBLIC])

    def test_each_refused_class_is_refused_before_any_connection(self):
        for address, reason in (("127.0.0.1", "loopback"), ("10.1.2.3", "private"), ("192.168.1.1", "private"),
                                ("169.254.169.254", "link_local"), ("100.64.0.1", "shared"),
                                ("224.0.0.1", "multicast"), ("fd00::1", "unique_local"), ("fe80::1", "link_local"),
                                ("::1", "loopback"), ("0.0.0.0", "unspecified")):
            with self.subTest(address=address):
                self.net.dns["inside.example.com"] = [address]
                error = self.refused("https://inside.example.com/a.png", "address_" + reason)
                self.assertEqual(error.code, "VALIDATION")
        self.assertEqual(self.net.connections, [])
        self.assertEqual(self.files(), [])

    def test_one_inside_address_among_public_ones_refuses(self):
        self.net.dns["mixed.example.com"] = [PUBLIC, "127.0.0.1"]
        self.refused("https://mixed.example.com/a.png", "address_loopback")
        self.assertEqual(self.net.connections, [])

    def test_an_address_written_in_the_link_is_checked_too(self):
        for link, reason in (("https://127.0.0.1/a.png", "address_loopback"),
                             ("https://[::1]/a.png", "address_loopback"),
                             ("https://169.254.169.254/latest/meta-data/", "address_link_local"),
                             ("https://2130706433/a.png", "address_loopback"),       # 127.0.0.1 as one number.
                             ("https://0x7f.1/a.png", "address_loopback")):
            with self.subTest(link=link):
                self.net.dns = {}   # No lookup reaches the resolver for a literal; a numeric form is the address.
                self.refused(link, reason, resolver=lambda host, port, *a, **k: [
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", (socket.inet_ntoa(socket.inet_aton(host)), port))])
        self.assertEqual(self.net.connections, [])

    def test_a_host_that_does_not_resolve_is_offline(self):
        self.net.dns["nowhere.example.com"] = socket.gaierror(8, "nodename nor servname provided")
        self.assertEqual(self.refused("https://nowhere.example.com/a.png", "offline").code, "UNSUPPORTED")


class ProxyTests(FetchCase):
    def test_a_proxy_tunnels_to_the_pinned_address(self):
        self.net.dns["cdn.example.com"] = [PUBLIC]
        self.net.answers[("cdn.example.com", "/a.png")] = Answer(body=PNG)
        self.download("https://cdn.example.com/a.png", proxies={"https": "http://proxy.corp.example:3128"})
        connection = self.net.connections[0]
        self.assertEqual(connection["proxy"], ("proxy.corp.example", 3128, None))
        self.assertEqual((connection["address"], connection["host"]), (PUBLIC, "cdn.example.com"))

    def test_no_proxy_rules_are_kept(self):
        self.net.dns["cdn.example.com"] = [PUBLIC]
        self.net.answers[("cdn.example.com", "/a.png")] = Answer(body=PNG)
        self.download("https://cdn.example.com/a.png",
                      proxies={"https": "http://proxy.corp.example:3128", "no": "example.com"})
        self.assertIsNone(self.net.connections[0]["proxy"])

    def test_the_real_connection_pins_the_address_and_verifies_the_host(self):
        """``PinnedConnection`` opens its socket to the address and wraps it with the host as the TLS name."""
        opened, wrapped = [], []

        class Context:
            def wrap_socket(self, sock, server_hostname=None, do_handshake_on_connect=True):
                wrapped.append(server_hostname)
                return sock

        class Sock:
            def __init__(self):
                self.sent = b""

            def sendall(self, data):
                self.sent += data

            def makefile(self, *args, **kwargs):
                import io
                return io.BytesIO(b"HTTP/1.1 200 Connection established\r\n\r\n")

            def close(self):
                pass

            def settimeout(self, value):
                pass

            def setsockopt(self, *args):
                pass

            def do_handshake(self):
                pass
        sock = Sock()
        # PinnedConnection._open is where it connects (the socket is on the connection first, for the watch).
        with mock.patch.object(fetch.PinnedConnection, "_open",
                               side_effect=lambda host, port: opened.append((host, port)) or sock):
            connection = fetch.PinnedConnection("cdn.example.com", PUBLIC, 443, None, 10, context=Context())
            connection.connect()
            self.assertEqual(opened, [(PUBLIC, 443)])
            self.assertEqual(wrapped, ["cdn.example.com"])
            opened.clear()
            wrapped.clear()
            connection = fetch.PinnedConnection("cdn.example.com", PUBLIC, 443, ("proxy.corp.example", 3128, None),
                                                10, context=Context())
            connection.connect()
            self.assertEqual(opened, [("proxy.corp.example", 3128)])
            self.assertIn(b"CONNECT %s:443 HTTP/1.1" % PUBLIC.encode(), sock.sent)
            self.assertEqual(wrapped, ["cdn.example.com"])

    def test_the_real_context_verifies_certificates(self):
        context = fetch.tls_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)


def proxy_environment(setting):
    """This process's environment with only its proxy variables replaced by ``setting``, through the one helper
    host tests clear the environment with (``platform_env.cleared``, which keeps what every process has): on
    Windows OpenSSL needs SYSTEMROOT to start, and the first SSL context of a process run alone was made inside a
    bare cleared one (VM CI 508360d287da-20261115112517: every Windows leg, alone only)."""
    kept = {name: value for name, value in os.environ.items() if "proxy" not in name.lower()}
    return platform_env.cleared(dict(kept, **setting))


def urllib_choice(host):
    """(proxy host, port, Proxy-Authorization or None) app_http's urllib ProxyHandler uses for https://host, or None:
    the oracle the download's choice must equal (the coordinator's condition for the pinned connection)."""
    request = urllib.request.Request("https://%s/a.png" % host)
    handler = urllib.request.ProxyHandler(urllib.request.getproxies())
    urllib.request.build_opener(handler)
    if hasattr(handler, "https_open"):
        handler.https_open(request)
    if request.host == host:   # Through a proxy, urllib sends an https request to it (set_proxy) and tunnels the host.
        return None
    class Https(urllib.request.http.client.HTTPConnection):   # HTTPSConnection's own parse, without an SSL context.
        default_port = urllib.request.http.client.HTTPS_PORT
    connection = Https(request.host)
    return connection.host, connection.port, request.headers.get("Proxy-authorization")


class ProxyParityTests(unittest.TestCase):
    """The system proxy is chosen exactly as app_http's urllib chooses it for an https request; the tunnel then goes
    to the checked address (ProxyTests)."""

    SETTINGS = ({"https_proxy": "http://proxy.corp.example:3128"},
                {"https_proxy": "https://proxy.corp.example:8443"},   # urllib tunnels with a plain CONNECT.
                {"https_proxy": "proxy.corp.example:3128"},
                {"https_proxy": "http://proxy.corp.example"},   # No port: urllib's HTTPSConnection uses 443.
                {"https_proxy": "http://us%40er:p%20ss@proxy.corp.example:3128"},
                {"https_proxy": "http://user@proxy.corp.example:3128"},   # No password: no Proxy-Authorization.
                {"https_proxy": "socks5://proxy.corp.example:1080"},   # Never bypassed by going direct.
                {"https_proxy": "http://[2001:db8::1]:3128"},
                {"https_proxy": "http://proxy.corp.example:3128", "no_proxy": "example.com"},
                {"https_proxy": "http://proxy.corp.example:3128", "no_proxy": "other.example"},
                {"https_proxy": "http://proxy.corp.example:3128", "no_proxy": "*"},
                {"http_proxy": "http://proxy.corp.example:3128"},   # Only an http proxy: https goes direct.
                {})

    def test_the_proxy_is_the_one_app_http_uses(self):
        for setting in self.SETTINGS:
            with self.subTest(setting=setting), proxy_environment(setting):
                self.assertEqual(fetch._proxy_for("cdn.example.com", None), urllib_choice("cdn.example.com"))


PROXY = {"https": "http://proxy.corp.example:3128"}
NXDOMAIN = socket.gaierror(8, "nodename nor servname provided")


class ProxyLookupTests(FetchCase):
    """Where only the proxy can resolve outside names (downloads.rst, "Through a proxy"): a name this computer cannot
    look up goes to the proxy as a name, but only one that cannot be internal, and never around a refused answer."""

    def via_proxy(self, url, **kwargs):
        return self.download(url, proxies=PROXY, search_domains=(), **kwargs)

    def test_a_name_this_computer_cannot_look_up_is_resolved_by_the_proxy(self):
        self.net.dns["cdn.example.net"] = NXDOMAIN
        self.net.answers[("cdn.example.net", "/a.png")] = Answer(body=PNG)
        result = self.via_proxy("https://cdn.example.net/a.png")
        connection = self.net.connections[0]
        self.assertEqual((connection["host"], connection["address"], connection["proxy"]),
                         ("cdn.example.net", None, ("proxy.corp.example", 3128, None)))
        self.assertEqual(result["address_checked_by"], "proxy")

    def test_an_address_found_here_is_checked_by_angelo(self):
        self.net.dns["cdn.example.net"] = [PUBLIC]
        self.net.answers[("cdn.example.net", "/a.png")] = Answer(body=PNG)
        self.assertEqual(self.via_proxy("https://cdn.example.net/a.png")["address_checked_by"], "angelo")
        self.assertEqual(self.net.connections[0]["address"], PUBLIC)

    def test_without_a_proxy_for_the_name_it_is_offline(self):
        self.net.dns["cdn.example.net"] = NXDOMAIN
        self.refused("https://cdn.example.net/a.png", "offline", proxies={}, search_domains=())
        self.refused("https://cdn.example.net/a.png", "offline", proxies=dict(PROXY, no="example.net"),
                     search_domains=())
        self.assertEqual(self.net.connections, [])

    def test_a_name_that_may_be_internal_is_never_sent_to_the_proxy(self):
        for url, domains in (("https://nas.local/a.png", ()), ("https://files.corp/a.png", ()),
                             ("https://intranet/a.png", ()), ("https://vault.internal/a.png", ()),
                             ("https://build.corp.example.com/a.png", ("corp.example.com",)),
                             ("https://cdn.example.net/a.png", None)):   # This machine's domains unknown.
            with self.subTest(url=url):
                self.net.dns[url.split("/")[2]] = NXDOMAIN
                self.refused(url, "offline", proxies=PROXY, search_domains=domains)
        self.assertEqual(self.net.connections, [])

    def test_a_refused_answer_never_falls_back_to_the_proxy(self):
        for answer, reason in (([PRIVATE], "address_private"), ([PUBLIC, PRIVATE], "address_private"),
                               (["169.254.169.254"], "address_link_local")):
            with self.subTest(answer=answer):
                self.net.dns["cdn.example.net"] = answer
                self.refused("https://cdn.example.net/a.png", reason, proxies=PROXY, search_domains=())
        self.assertEqual(self.net.connections, [])

    def test_an_address_in_the_link_never_goes_to_the_proxy_as_a_name(self):
        self.net.dns = {}
        self.refused("https://10.0.0.5/a.png", "address_private", proxies=PROXY, search_domains=())
        self.assertEqual(self.net.connections, [])

    def test_rebinding_through_the_fallback_changes_nothing(self):
        # The first lookup fails, a later one would answer a private address: the name is looked up once and stays
        # with the proxy for every hop of this download, so no later answer swaps anything in.
        self.net.dns["cdn.example.net"] = [NXDOMAIN, [PRIVATE]]
        self.net.answers[("cdn.example.net", "/x")] = Answer(302, headers={"Location": "/a.png"})
        self.net.answers[("cdn.example.net", "/a.png")] = Answer(body=PNG)
        self.via_proxy("https://cdn.example.net/x")
        self.assertEqual(self.net.lookups, ["cdn.example.net"])
        self.assertEqual([c["address"] for c in self.net.connections], [None, None])

    def test_a_redirect_from_a_proxy_resolved_name_is_checked_like_any_hop(self):
        self.net.dns["a.example.net"] = NXDOMAIN
        self.net.dns["b.example.net"] = [PRIVATE]
        self.net.dns["nas.local"] = NXDOMAIN
        self.net.answers[("a.example.net", "/x")] = Answer(302, headers={"Location": "https://b.example.net/y.png"})
        self.refused("https://a.example.net/x", "address_private", proxies=PROXY, search_domains=())
        self.net.answers[("a.example.net", "/x")] = Answer(302, headers={"Location": "https://nas.local/y.png"})
        self.refused("https://a.example.net/x", "offline", proxies=PROXY, search_domains=())
        self.assertEqual({c["host"] for c in self.net.connections}, {"a.example.net"})

    def test_the_card_knows_when_the_proxy_may_look_the_address_up(self):
        with proxy_environment({"https_proxy": PROXY["https"]}), \
                mock.patch.object(fetch.names, "search_domains", return_value=()):
            self.assertTrue(fetch.proxy_lookup_possible("cdn.example.net"))
            for host in ("nas.local", "10.0.0.5", "intranet"):
                self.assertFalse(fetch.proxy_lookup_possible(host), host)
        with proxy_environment({"https_proxy": PROXY["https"], "no_proxy": "*"}), \
                mock.patch.object(fetch.names, "search_domains", return_value=()):
            self.assertFalse(fetch.proxy_lookup_possible("cdn.example.net"))

    def test_the_real_connection_asks_the_proxy_for_the_name_only_when_angelo_has_no_address(self):
        sent = []

        class Sock:
            def sendall(self, data):
                sent.append(data)

            def makefile(self, *args, **kwargs):
                import io
                return io.BytesIO(b"HTTP/1.1 200 Connection established\r\n\r\n")

            def close(self):
                pass

            def do_handshake(self):
                pass

        class Context:
            def wrap_socket(self, sock, server_hostname=None, do_handshake_on_connect=True):
                self.name = server_hostname
                return sock
        context = Context()
        with mock.patch.object(fetch.PinnedConnection, "_open", return_value=Sock()):
            fetch.PinnedConnection("cdn.example.net", None, 443, ("proxy.corp.example", 3128, None), 10,
                                   context=context).connect()
        self.assertIn(b"CONNECT cdn.example.net:443 HTTP/1.1", sent[0])
        self.assertEqual(context.name, "cdn.example.net")
        with self.assertRaises(ValueError):   # Direct, a connection always has its checked address.
            fetch.PinnedConnection("cdn.example.net", None, 443, None, 10, context=context)


class RedirectTests(FetchCase):
    def setUp(self):
        super().setUp()
        self.net.dns.update({"a.example.com": [PUBLIC], "b.example.com": [PUBLIC2], "inside.example.com": [PRIVATE]})

    def test_a_redirect_keeps_to_443_or_the_links_own_port(self):
        # Another port on a checked public address is another service (Devin audit N2: https://same-host:22/ was
        # followed). A redirect may go to 443 or to the port the person's link named; any other is refused.
        for link, location, refused in (("https://a.example.com/x", "https://a.example.com:22/y.png", True),
                                        ("https://a.example.com/x", "https://b.example.com:8443/y.png", True),
                                        ("https://a.example.com:8443/x", "https://b.example.com:8443/y.png", False),
                                        ("https://a.example.com:8443/x", "https://b.example.com/y.png", False),
                                        ("https://a.example.com/x", "https://b.example.com:443/y.png", False)):
            with self.subTest(link=link, location=location):
                self.net.connections.clear()
                self.net.answers[("a.example.com", "/x")] = Answer(302, headers={"Location": location})
                self.net.answers[("b.example.com", "/y.png")] = Answer(body=PNG)
                self.net.answers[("a.example.com", "/y.png")] = Answer(body=PNG)
                if refused:
                    self.refused(link, "redirect_port")
                    self.assertEqual(len(self.net.connections), 1, "the redirect was never followed")
                else:
                    self.download(link)

    def test_an_https_redirect_is_followed_and_its_host_checked_and_pinned(self):
        self.net.answers[("a.example.com", "/x")] = Answer(302, headers={"Location": "https://b.example.com/y.png"})
        self.net.answers[("b.example.com", "/y.png")] = Answer(body=PNG)
        result = self.download("https://a.example.com/x")
        self.assertEqual([(c["host"], c["address"]) for c in self.net.connections],
                         [("a.example.com", PUBLIC), ("b.example.com", PUBLIC2)])
        self.assertEqual(result["host"], "b.example.com")
        self.assertEqual(result["path"], "assets/downloads/y.png")

    def test_a_relative_redirect_stays_on_the_pinned_host(self):
        self.net.answers[("a.example.com", "/x")] = Answer(301, headers={"Location": "/files/z.png"})
        self.net.answers[("a.example.com", "/files/z.png")] = Answer(body=PNG)
        self.download("https://a.example.com/x")
        self.assertEqual(self.net.lookups, ["a.example.com"])

    def test_no_redirect_to_http_or_another_scheme(self):
        for location in ("http://b.example.com/y.png", "ftp://b.example.com/y", "file:///etc/passwd",
                         "data:image/png;base64,AAAA"):
            with self.subTest(location=location):
                self.net.answers[("a.example.com", "/x")] = Answer(302, headers={"Location": location})
                self.assertEqual(self.refused("https://a.example.com/x", "redirect_not_https").code, "VALIDATION")
        self.assertEqual(self.files(), [])

    def test_a_redirect_to_an_inside_address_is_refused(self):
        self.net.answers[("a.example.com", "/x")] = Answer(302, headers={"Location": "https://inside.example.com/m"})
        self.refused("https://a.example.com/x", "address_private")
        self.net.answers[("a.example.com", "/x")] = Answer(
            307, headers={"Location": "https://169.254.169.254/latest/meta-data/"})
        self.refused("https://a.example.com/x", "address_link_local")
        self.assertNotIn("inside.example.com", [c["host"] for c in self.net.connections])

    def test_at_most_five_redirects(self):
        for step in range(7):
            self.net.answers[("a.example.com", "/%d" % step)] = Answer(302, headers={"Location": "/%d" % (step + 1)})
        self.assertEqual(self.refused("https://a.example.com/0", "too_many_redirects").code, "LIMIT_EXCEEDED")
        self.assertEqual(len(self.net.connections), 6)


class SlowServerTests(FetchCase):
    """A server that sends a byte now and then answers every read within the read timeout, so a check between reads
    never fires: the time limit and Stop must end the wait itself (Fusion audit trial, 508360d287d: a 3 s limit ended
    at 29.1 s in the body and 40.0 s in the headers, and Stop at 2 s took effect at 29.0 s)."""

    def trickle(self, first, then, interval=0.2, seconds=8.0):
        """A local server: ``first`` at once, then one byte of ``then`` every ``interval`` for at most ``seconds``."""
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)

        def serve():
            try:
                peer, _address = listener.accept()
            except OSError:
                return
            try:
                peer.recv(65536)
                peer.sendall(first)
                began = time.monotonic()
                for index in range(len(then)):
                    if time.monotonic() - began > seconds:
                        break
                    time.sleep(interval)
                    peer.sendall(then[index:index + 1])
            except OSError:
                pass
            finally:
                peer.close()
                listener.close()
        threading.Thread(target=serve, daemon=True).start()
        self.addCleanup(listener.close)
        port = listener.getsockname()[1]
        self.net.dns["slow.example.net"] = [PUBLIC]
        return lambda host, address, port_, proxy, timeout: __import__("http.client").client.HTTPConnection(
            "127.0.0.1", port, timeout=timeout)

    def ends_within(self, seconds, reason, **kwargs):
        """The download ends as ``reason`` within ``seconds`` (the time limit or Stop plus a margin, not the ~8 s the
        server would take), on this machine (machine_clock: only time the machine itself took away is added), and
        leaves no file."""
        window = machine_clock.start(self)
        self.refused("https://slow.example.net/a.png", reason, **kwargs)
        window.assert_within(seconds, "the download waited on the server past its %s" % reason)
        self.assertEqual(self.files(), [])

    def test_a_body_that_drips_ends_at_the_time_limit(self):
        connect = self.trickle(b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n" + PNG[:16], PNG[16:200])
        self.ends_within(3.0, "too_slow", connect=connect, time_limit=1.0, timeout=5.0)

    def test_headers_that_drip_end_at_the_time_limit(self):
        connect = self.trickle(b"HTTP/1.1 200 OK\r\n", b"X-Slow: " + b"y" * 200)
        self.ends_within(3.0, "too_slow", connect=connect, time_limit=1.0, timeout=5.0)

    def slow_proxy(self, reply, interval=0.2, seconds=8.0):
        """A local proxy that reads the CONNECT, then sends ``reply`` one byte every ``interval``."""
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)

        def serve():
            try:
                peer, _address = listener.accept()
            except OSError:
                return
            try:
                peer.recv(65536)
                began = time.monotonic()
                for index in range(len(reply)):
                    if time.monotonic() - began > seconds:
                        break
                    time.sleep(interval)
                    peer.sendall(reply[index:index + 1])
            except OSError:
                pass
            finally:
                peer.close()
                listener.close()
        threading.Thread(target=serve, daemon=True).start()
        self.addCleanup(listener.close)
        self.net.dns["slow.example.net"] = [PUBLIC]
        return {"https": "http://127.0.0.1:%d" % listener.getsockname()[1]}

    def test_a_proxy_that_drips_its_connect_reply_ends_at_the_time_limit(self):
        # The real PinnedConnection (its CONNECT read), not a stand-in connection (Devin audit D1).
        proxies = self.slow_proxy(b"HTTP/1.1 200 Connection established\r\n" + b"X-Slow: y\r\n" * 20 + b"\r\n")
        self.ends_within(3.0, "too_slow", connect=None, proxies=proxies, time_limit=1.0, timeout=5.0)

    def test_stop_ends_a_connect_reply_that_is_waiting(self):
        proxies = self.slow_proxy(b"HTTP/1.1 200 Connection established\r\n" + b"X-Slow: y\r\n" * 20 + b"\r\n")
        cancel = threading.Event()
        threading.Timer(0.3, cancel.set).start()
        self.ends_within(2.0, "cancelled", connect=None, proxies=proxies, cancel=cancel, time_limit=60.0,
                                   timeout=5.0)

    def test_stop_ends_a_read_that_is_waiting(self):
        connect = self.trickle(b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n" + PNG[:16], PNG[16:200])
        cancel = threading.Event()
        threading.Timer(0.3, cancel.set).start()
        self.ends_within(2.0, "cancelled", connect=connect, cancel=cancel, time_limit=60.0, timeout=5.0)


class CapTests(FetchCase):
    def setUp(self):
        super().setUp()
        self.net.dns["cdn.example.com"] = [PUBLIC]

    def test_a_declared_length_over_the_cap_is_refused_before_any_byte(self):
        answer = Answer(body=PNG, headers={"Content-Length": str(10 * 1024 * 1024)})
        self.net.answers[("cdn.example.com", "/a.png")] = answer
        self.assertEqual(self.refused("https://cdn.example.com/a.png", "too_large", max_bytes=1024 * 1024).code,
                         "LIMIT_EXCEEDED")
        self.assertEqual(answer.position, 0)
        self.assertEqual(self.files(), [])

    def test_a_body_longer_than_the_cap_stops_and_leaves_nothing(self):
        self.net.answers[("cdn.example.com", "/a.png")] = Answer(body=PNG * 50)
        self.refused("https://cdn.example.com/a.png", "too_large", max_bytes=len(PNG) * 3)
        self.assertEqual(self.files(), [])

    def test_the_time_cap(self):
        clock = [0.0]

        def stall():
            clock[0] += 40.0
        self.net.answers[("cdn.example.com", "/a.png")] = Answer(body=PNG * 20, chunk=1024, stall=stall)
        self.assertEqual(self.refused("https://cdn.example.com/a.png", "too_slow", clock=lambda: clock[0],
                                      time_limit=100.0).code, "LIMIT_EXCEEDED")
        self.assertEqual(self.files(), [])

    def test_stop_ends_the_transfer_and_leaves_nothing(self):
        stop = threading.Event()
        self.net.answers[("cdn.example.com", "/a.png")] = Answer(body=PNG * 20, chunk=1024, stall=stop.set)
        self.assertEqual(self.refused("https://cdn.example.com/a.png", "cancelled", cancel=stop).code, "CANCELLED")
        self.assertEqual(self.files(), [])

    def test_a_server_error_is_reported_with_its_status(self):
        self.net.answers[("cdn.example.com", "/a.png")] = Answer(404, body=b"<html>Not found</html>")
        error = self.refused("https://cdn.example.com/a.png", "http_404")
        self.assertEqual(error.code, "UNSUPPORTED")
        self.assertEqual(self.files(), [])


class FileTests(FetchCase):
    def setUp(self):
        super().setUp()
        self.net.dns["cdn.example.com"] = [PUBLIC]

    def test_bytes_that_are_not_the_kind_are_removed(self):
        self.net.answers[("cdn.example.com", "/sky.hdr")] = Answer(body=b"<!doctype html><title>Login</title>")
        self.assertEqual(self.refused("https://cdn.example.com/sky.hdr", "wrong_type", kind="hdri").code,
                         "CHECK_FAILED")
        self.net.answers[("cdn.example.com", "/tool.zip")] = Answer(body=b"MZ\x90\x00" + b"\x00" * 4096)
        self.refused("https://cdn.example.com/tool.zip", "wrong_type", kind="other")
        self.assertEqual(self.files(), [])

    def test_the_server_file_name_is_only_a_cleaned_hint(self):
        self.net.answers[("cdn.example.com", "/")] = Answer(
            body=PNG, headers={"Content-Disposition": 'attachment; filename="../../../evil.sh"'})
        result = self.download("https://cdn.example.com/")
        self.assertEqual(result["path"], "assets/downloads/evil.png")
        self.assertEqual(self.files(), ["assets/downloads/evil.png"])

    def test_the_name_argument_wins_and_never_overwrites(self):
        self.net.answers[("cdn.example.com", "/a.png")] = Answer(body=PNG)
        first = self.download("https://cdn.example.com/a.png", name="Brick wall")
        self.net.answers[("cdn.example.com", "/a.png")] = Answer(body=PNG[:-1] + b"!")
        second = self.download("https://cdn.example.com/a.png", name="Brick wall")
        self.assertEqual(first["path"], "assets/downloads/Brick wall.png")
        self.assertEqual(second["path"], "assets/downloads/Brick wall (2).png")
        self.assertEqual((self.root / first["path"]).read_bytes(), PNG)

    @unittest.skipIf(os.name == "nt", "[environment] POSIX permission bits")
    def test_a_downloaded_file_is_never_executable(self):
        self.net.answers[("cdn.example.com", "/a.bin")] = Answer(body=b"plain data " * 100)
        result = self.download("https://cdn.example.com/a.bin", kind="other")
        self.assertEqual(os.stat(self.root / result["path"]).st_mode & 0o111, 0)

    @unittest.skipIf(os.name == "nt", "[environment] symbolic links need a privilege on Windows")
    def test_a_link_that_leads_out_of_the_folder_is_refused(self):
        outside = Path(tempfile.mkdtemp(prefix="angelo-download-outside-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(outside, ignore_errors=True))
        self.net.answers[("cdn.example.com", "/a.png")] = Answer(body=PNG)
        for link in ("assets", "assets/downloads"):
            with self.subTest(link=link):
                for path in sorted(self.root.rglob("*"), reverse=True):
                    path.unlink() if path.is_file() or path.is_symlink() else path.rmdir()
                target = self.root / link
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(outside, target_is_directory=True)
                self.assertEqual(self.refused("https://cdn.example.com/a.png", "outside_folder").code, "VALIDATION")
                self.assertEqual(list(outside.rglob("*")), [])
        self.assertEqual(self.net.connections, [], "refused before the transfer")


class GuardTests(FetchCase):
    def test_agent_code_cannot_download(self):
        self.net.dns["cdn.example.com"] = [PUBLIC]
        self.net.answers[("cdn.example.com", "/a.png")] = Answer(body=PNG)
        with grant_guard.agent_script(), self.assertRaises(PermissionError):
            self.download("https://cdn.example.com/a.png")
        self.assertEqual(self.net.connections, [])
        # "module.function", whether the guard keys its entries by the short or the package-qualified module name.
        self.assertIn("download_fetch.download", {".".join(key.split(".")[-2:]) for key in grant_guard.ENTRIES})


if __name__ == "__main__":
    unittest.main()
