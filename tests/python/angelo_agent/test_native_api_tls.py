# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""API-key checks honor managed roots without weakening TLS verification.

Uses a loopback HTTPS server and a throwaway certificate, never a provider or
real key. OpenSSL is also required by the managed-policy host tests.
"""

import json
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import api_connections, managed_policy  # noqa: E402


class _Models(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"data": [{"id": "fixture-model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class ManagedConnectionTrustTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        openssl = shutil.which("openssl")
        if not openssl:
            raise unittest.SkipTest("OpenSSL is required for the loopback TLS fixture")
        cls.directory = tempfile.TemporaryDirectory(prefix="angelo-api-tls-")
        cls.addClassCleanup(cls.directory.cleanup)
        root = Path(cls.directory.name)
        cls.cert, key = root / "root.pem", root / "key.pem"
        # The certificate admits 127.0.0.1, deliberately not localhost. That
        # exercises hostname validation even when the root is explicitly trusted.
        config = root / "openssl.cnf"
        config.write_text("[req]\ndistinguished_name=dn\nx509_extensions=extensions\nprompt=no\n"
                          "[dn]\nCN=Angelo test root\n[extensions]\nbasicConstraints=critical,CA:TRUE\n"
                          "subjectAltName=IP:127.0.0.1\n", encoding="utf-8")
        subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-config", str(config), "-keyout", str(key), "-out", str(cls.cert)],
                       check=True, capture_output=True, timeout=30)
        cls.server = HTTPServer(("127.0.0.1", 0), _Models)
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
        cls.worker.join(timeout=5)

    def probe(self, host, policy):
        request = urllib.request.Request("https://%s:%s/models" % (host, self.server.server_port),
                                         headers={"Authorization": "Bearer test-only-key"})
        # Proxies are outside this fixture's scope; no inherited proxy may
        # receive even the stand-in key. Production proxy behavior is unchanged.
        with patch.object(managed_policy, "current", return_value=policy), \
                patch.object(urllib.request, "getproxies", return_value={}):
            return api_connections.read_probe(request)

    def test_managed_root_allows_the_connection_check(self):
        policy = managed_policy.Policy(extra_ca_certificates=str(self.cert))
        self.assertEqual(self.probe("127.0.0.1", policy), {"data": [{"id": "fixture-model"}]})

    def test_untrusted_root_is_still_rejected(self):
        with self.assertRaises(api_connections.ConnectionCheckError) as caught:
            self.probe("127.0.0.1", managed_policy.Policy())
        self.assertNotIn("test-only-key", str(caught.exception))

    def test_trusted_root_does_not_disable_hostname_verification(self):
        policy = managed_policy.Policy(extra_ca_certificates=str(self.cert))
        with self.assertRaises(api_connections.ConnectionCheckError):
            self.probe("localhost", policy)


if __name__ == "__main__":
    unittest.main()
