# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A console whose certificate no trusted root vouches for is said to be that, never "check your network".

On a network that inspects TLS, the console's and the gateway's certificates are signed by the organization's own
root. Angelo's own requests trust the bundled roots plus the managed policy's ``ExtraCACertificates``
(``team_http.tls_context``), so without the policy the handshake fails, on macOS even when the operating system
trusts that root. Up to the 2.0.0 candidate the team account then said "Couldn't reach {origin}. Check your
network; Angelo retries every minute", which sends the person to the wrong place: the network is fine and a retry
never helps.

Pinned here, against a loopback HTTPS server with a throwaway root (as ``test_native_api_tls``): the failure is
still ``NotSent`` (nothing was presented to the server, so a refresh token is not spent), it says that the
certificate was not trusted, and the person's message names the certificate; a refused connection keeps the
network message; a root the policy adds is trusted as before.

The class, not the team account alone: every place one of Angelo's own requests reports a failed connection.
The update check and the library's downloads (``app_http``) know an unverified certificate from another failure
and say so (the update check keeps its stable ``detail``); the team library's answer to the agent does; and no
function of the add-on can word a "check your network" message without handling the certificate case, nor call
``unreachable`` without the error it reports (a scan of the sources).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).
from host_waits import CAP_S  # noqa: E402  A completion wait's cap: only a hang reaches it.

import ast
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from host_test_policy import required_skip
from unittest.mock import patch
from urllib.parse import urlsplit

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
from angelo_agent import api_connections, app_http, library_fetch, managed_policy  # noqa: E402
from angelo_agent import team_errors, team_http, team_library_transfer, update_check  # noqa: E402
from angelo_agent.team_errors import NotSent  # noqa: E402

ORIGIN = "https://console.example"


# Module-level texts that stay for the failures they are true of, and the caller that keeps a certificate failure
# away from them: the shared sentence (``unreachable`` chooses), and the sign-in page a browser shows.
ROUTED = {"team_errors.py": ("team_errors.py", 'Text(UNTRUSTED if getattr(error, "untrusted", False) else UNREACHABLE'),
          "team_pages.py": ("team_account.py", 'listener.finish("error", reason=message)')}


def untrusted_failure():
    failure = app_http.NetworkError(app_http.TLS, "The secure connection could not be verified")
    failure.untrusted = True
    return failure


class _Answer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *_args):
        pass


class UntrustedConsoleCertificate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        openssl = shutil.which("openssl")
        if not openssl:
            raise unittest.SkipTest(required_skip("OpenSSL is required for the loopback TLS fixture"))
        cls.directory = tempfile.TemporaryDirectory(prefix="angelo-team-tls-")
        cls.addClassCleanup(cls.directory.cleanup)
        root = Path(cls.directory.name)
        cls.cert, key, config = root / "root.pem", root / "key.pem", root / "openssl.cnf"
        config.write_text("[req]\ndistinguished_name=dn\nx509_extensions=extensions\nprompt=no\n"
                          "[dn]\nCN=Angelo test inspection root\n[extensions]\nbasicConstraints=critical,CA:TRUE\n"
                          "subjectAltName=IP:127.0.0.1\n", encoding="utf-8")
        subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-sha256",
                        "-config", str(config), "-keyout", str(key), "-out", str(cls.cert)],
                       check=True, capture_output=True, timeout=30)
        cls.server = HTTPServer(("127.0.0.1", 0), _Answer)
        cls.addClassCleanup(cls.server.server_close)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cls.cert, key)
        cls.server.socket = context.wrap_socket(cls.server.socket, server_side=True)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.addClassCleanup(cls.stop_server)

    @classmethod
    def stop_server(cls):
        cls.server.shutdown()
        cls.worker.join(timeout=CAP_S)

    def request(self, policy, port=None):
        with patch.object(managed_policy, "current", return_value=policy):
            return team_http.http_transport("GET", "https://127.0.0.1:%d/" % (port or self.server.server_port))

    def test_an_untrusted_certificate_is_not_sent_and_says_it_was_the_certificate(self):
        with self.assertRaises(NotSent) as caught:
            self.request(managed_policy.Policy())
        self.assertTrue(getattr(caught.exception, "untrusted", False))
        message = team_errors.unreachable(ORIGIN, caught.exception).message
        self.assertIn("certificate", message.lower())
        self.assertIn(ORIGIN, message)
        self.assertNotIn("Check your network", message)

    def test_a_refused_connection_keeps_the_network_message(self):
        with socket.socket() as free:   # A port nothing listens on once this socket closes.
            free.bind(("127.0.0.1", 0))
            port = free.getsockname()[1]
        with self.assertRaises(NotSent) as caught:
            self.request(managed_policy.Policy(), port)
        self.assertFalse(getattr(caught.exception, "untrusted", False))
        message = team_errors.unreachable(ORIGIN, caught.exception).message
        self.assertEqual(message, team_errors.unreachable(ORIGIN, None).message)
        self.assertIn("Check your network", message)

    def test_a_root_the_policy_adds_is_trusted(self):
        status, _headers, body = self.request(managed_policy.Policy(extra_ca_certificates=str(self.cert)))
        self.assertEqual((status, body), (200, b"{}"))

    def test_a_root_added_to_angelos_context_later_vouches_for_nothing(self):
        """A root put into Angelo's built context in place (as a script that reached it could), through a folder
        OpenSSL reads only during a handshake, so nothing before the handshake can see it: the request is refused,
        because the verified chain must end at a root the context was built with (``team_http._AnchoredSocket``)."""
        folder = Path(self.directory.name) / "capath"
        folder.mkdir(exist_ok=True)
        subject_hash = subprocess.run([shutil.which("openssl"), "x509", "-hash", "-noout", "-in", str(self.cert)],
                                      check=True, capture_output=True, text=True, timeout=30).stdout.strip()
        shutil.copy(self.cert, folder / (subject_hash + ".0"))
        with patch.object(team_http, "_SHARED", ()):   # Angelo's own context for this test alone.
            with patch.object(managed_policy, "current", return_value=managed_policy.Policy()):
                team_http.tls_context().load_verify_locations(capath=str(folder))
            with self.assertRaises(NotSent) as caught:
                self.request(managed_policy.Policy())
        self.assertTrue(getattr(caught.exception, "untrusted", False))

    def test_the_update_and_library_client_knows_an_unverified_certificate_from_another_failure(self):
        url = "https://127.0.0.1:%d/" % self.server.server_port
        with patch.object(managed_policy, "current", return_value=managed_policy.Policy()):
            with self.assertRaises(app_http.NetworkError) as caught:
                app_http.request("GET", url, proxies={})
        self.assertEqual(caught.exception.kind, app_http.TLS)
        self.assertTrue(getattr(caught.exception, "untrusted", False))
        with socket.socket() as free:
            free.bind(("127.0.0.1", 0))
            port = free.getsockname()[1]
        with self.assertRaises(app_http.NetworkError) as caught:
            app_http.request("GET", "https://127.0.0.1:%d/" % port, proxies={})
        self.assertFalse(getattr(caught.exception, "untrusted", False))

    def refused_port(self):
        with socket.socket() as free:   # A port nothing listens on once this socket closes.
            free.bind(("127.0.0.1", 0))
            return free.getsockname()[1]

    def test_a_provider_check_and_a_storage_download_say_it_was_the_certificate(self):
        """The two other requests of Angelo's own that open a connection themselves, against the same server."""
        import urllib.request
        with patch.object(managed_policy, "current", return_value=managed_policy.Policy()), \
                tempfile.TemporaryDirectory() as folder:
            for port, untrusted in ((self.server.server_port, True), (self.refused_port(), False)):
                url = "https://127.0.0.1:%d/v1/models?key=secret" % port
                attempts = {
                    "provider check": (api_connections.ConnectionCheckError,
                                       lambda: api_connections.read_probe(urllib.request.Request(url))),
                    "storage download": (team_library_transfer.TransferError,
                                         lambda: team_library_transfer.download(
                                             url, Path(folder) / ("file-%d" % port), limit=1024)),
                }
                for name, (kind, attempt) in attempts.items():
                    with self.subTest(request=name, untrusted=untrusted), self.assertRaises(kind) as caught:
                        attempt()
                    message = str(caught.exception)
                    self.assertEqual("certificate" in message.lower(), untrusted, message)
                    self.assertEqual("check your network" in message.lower(), not untrusted, message)
                    self.assertNotIn("secret", message)
                    if untrusted:
                        self.assertIn("https://127.0.0.1:%d" % port, message)


class EveryFailedConnectionMessage(unittest.TestCase):
    """No loopback server: what each caller says once its transport reports an unverified certificate."""

    def test_a_library_download_names_the_certificate(self):
        url = "https://downloads.example/library/latest.json"
        for untrusted in (True, False):
            failure = untrusted_failure() if untrusted else app_http.NetworkError(app_http.OFFLINE, "Could not reach")
            with self.subTest(untrusted=untrusted), tempfile.TemporaryDirectory() as folder, \
                    patch.object(library_fetch.catalog, "allowed_url", return_value=True), \
                    patch.object(library_fetch.app_http, "request", side_effect=failure), \
                    patch.object(library_fetch.app_http, "stream", side_effect=failure):
                for attempt in (lambda: library_fetch._get(url, 1024, "list"),
                                lambda: library_fetch._download(url, Path(folder) / "file", {}, None)):
                    with self.assertRaises(library_fetch.FetchError) as caught:
                        attempt()
                    message = str(caught.exception)
                    self.assertEqual("certificate" in message.lower(), untrusted, message)
                    self.assertEqual("check your network" in message.lower(), not untrusted, message)
                    if untrusted:
                        self.assertIn("https://downloads.example", message)
                    # The library's own surfaces learn which failure it was (its overlay's one line).
                    self.assertEqual(caught.exception.untrusted, untrusted)
                    kept = library_fetch.reported(caught.exception)
                    self.assertEqual(kept, message)
                    self.assertEqual(isinstance(kept, library_fetch.Untrusted), untrusted)

    def test_an_update_check_keeps_its_detail_and_names_the_origin(self):
        feed = urlsplit(update_check.feed_url(managed_policy.Policy(), {}))
        origin = "%s://%s" % (feed.scheme, feed.netloc)
        cases = ((untrusted_failure(), origin), (app_http.NetworkError(app_http.TLS, "Another TLS failure"), ""),
                 (app_http.NetworkError(app_http.OFFLINE, "Could not reach the server"), ""))
        for failure, expected in cases:
            def fetch(*_args, failure=failure, **_kwargs):
                raise failure
            with self.subTest(failure=str(failure)), tempfile.TemporaryDirectory() as folder:
                checker = update_check.Checker(folder, fetch=fetch, clock=lambda: 1_800_000_000.0, keys={"test": b"k"},
                                               current="2.0.0", platform="macos-arm64", policy=managed_policy.Policy(),
                                               environ={})
                result = checker.check(manual=True)
                self.assertEqual(result.detail, failure.kind)
                self.assertEqual(result.untrusted_origin, expected)

    def test_the_update_notice_says_it_was_the_certificate(self):
        source = (ADDON / "update_ui.py").read_text(encoding="utf-8")
        notice = source.split("def _result_notice(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("untrusted_origin", notice)
        self.assertIn("UNTRUSTED", notice)

    def test_no_function_words_a_network_message_without_the_certificate_case(self):
        """A function (or module level) holding a "check your network" text also reads ``untrusted``; the one
        constant every caller shares is ``team_errors.UNREACHABLE``, which ``unreachable`` pairs with ``UNTRUSTED``."""
        found, texts = [], 0
        for path in sorted(ADDON.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            scopes = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
            inside = {id(child) for scope in scopes for child in ast.walk(scope)}
            for scope in scopes + [tree]:
                nodes = (list(ast.walk(scope)) if scope is not tree
                         else [node for node in ast.walk(tree) if id(node) not in inside])
                says = [n for n in nodes if isinstance(n, ast.Constant) and isinstance(n.value, str)
                        and "check your network" in n.value.lower()]
                handles = any(isinstance(n, ast.Attribute) and n.attr == "untrusted" or isinstance(n, ast.Name)
                              and n.id in ("UNTRUSTED", "UNTRUSTED_LINE", "untrusted", "untrusted_text") for n in nodes)
                texts += len(says)
                if says and not handles and not (path.name in ROUTED and scope is tree):
                    found.append("%s:%d" % (path.name, says[0].lineno))
        self.assertEqual(found, [])
        for holder, (router, text) in ROUTED.items():   # A text kept for other failures: its caller routes this one.
            with self.subTest(holder=holder):
                self.assertIn(text, (ADDON / router).read_text(encoding="utf-8"))
        self.assertGreaterEqual(texts, 3, "the scan found too few such messages to mean anything")
        source = (ADDON / "team_errors.py").read_text(encoding="utf-8")
        self.assertIn("UNTRUSTED", source.split("def unreachable(", 1)[1].split("\ndef ", 1)[0])

    def test_every_caller_hands_unreachable_the_error_it_reports(self):
        calls = []
        for path in sorted(ADDON.glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "unreachable"
                        and len(node.args) + len(node.keywords) < 2):
                    calls.append("%s:%d" % (path.name, node.lineno))
        self.assertEqual(calls, [])

    def test_the_team_library_s_answer_to_the_agent_names_the_certificate(self):
        source = (ADDON / "team_library_client.py").read_text(encoding="utf-8")
        handler = source.split("except NotSent", 1)[1].split("except Unanswered", 1)[0]
        self.assertIn("untrusted", handler)
        self.assertTrue(hasattr(team_errors, "untrusted_text"))
        text = team_errors.untrusted_text("https://console.example/api/orgs/1/library/stats")
        self.assertIn("certificate", text.lower())
        self.assertIn("https://console.example", text)
        self.assertNotIn("/api/", text)


if __name__ == "__main__":
    unittest.main()
