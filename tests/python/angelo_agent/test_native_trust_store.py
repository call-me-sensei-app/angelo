# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's TLS refuses compromised CA keys, and no trust store it ships holds one.

Evidence: the eMudhra disclosure of 2026-10-20. Eight intermediate CA keys were recoverable from public data;
each intermediate chains to emSign ECC Root CA - G3 (through the emSign Root TLS/CS CA - G3 cross-certificates)
and, through a Sectigo cross-sign, to AAA Certificate Services. ``tls_distrust_fixtures`` holds the real
certificates (public, from Certificate Transparency: crt.sh) and the two roots.

A "forged chain" here is what an attacker holding one of those keys would serve: a leaf for 127.0.0.1 naming the
compromised intermediate as its issuer, then the real intermediate (and cross-certificate). We do not hold the
keys, so the leaf's signature never verifies; what the tests read is whether OpenSSL could anchor the chain in
the trust store at all (``X509_V_ERR_UNABLE_TO_GET_ISSUER_CERT_LOCALLY``, 20, means it could not). Anchored with
the real key, the chain would have been accepted.

The shipped stores are read from the pinned wheels (``ANGELO_PYTHON_WHEELS_DIR``; CI fetches them with
``scripts/angelo/trust_store.py fetch-wheels``) and/or an installed app (``ANGELO_TRUST_INSTALL``). OpenSSL makes
the throwaway keys, as in the managed-policy and API TLS tests; every command works with OpenSSL 3 and LibreSSL (macOS ``/usr/bin/openssl``, which signs with SHA-1 unless told).
"""

import base64
import glob
import hashlib
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent / "tls_distrust_fixtures"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(ROOT / "scripts/angelo"))
from angelo_agent import managed_policy, team_http, tls_distrust  # noqa: E402
from angelo_agent.team_errors import NotSent  # noqa: E402
import trust_store  # noqa: E402

UNANCHORED = 20   # X509_V_ERR_UNABLE_TO_GET_ISSUER_CERT_LOCALLY
KEYS = ("em_dv_tls_ca_g3b", "em_ov_tls_ca_g3b", "em_ev_tls_ca_g3b", "em_dv_tls_ca_g3c", "em_ov_tls_ca_g3c",
        "em_ev_tls_ca_g3c", "em_ov_cs_ca_g3b", "em_ov_cs_ca_g3c")


def der(path):
    return ssl.PEM_cert_to_DER_cert(Path(path).read_text(encoding="ascii"))


def openssl(*args):
    return subprocess.run([shutil.which("openssl")] + [str(arg) for arg in args], check=True, capture_output=True,
                          text=True, timeout=30).stdout


# ``req`` needs a [req] section in LibreSSL; ``ca`` marks a stand-in issuer.
CA_CONFIG = ("[req]\ndistinguished_name=dn\n[dn]\n[ca]\nbasicConstraints=critical,CA:TRUE\n"
             "keyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\n")


def emsign_path(key):
    """The intermediate plus the cross-certificate that takes it to emSign ECC Root CA - G3."""
    cross = "emsign_root_cs_ca_g3" if "_cs_" in key else "emsign_root_tls_ca_g3"
    return [FIXTURES / ("%s.emsign.pem" % key), FIXTURES / ("%s.cross.pem" % cross)]


def aaa_path(key):
    return [FIXTURES / ("%s.aaa.pem" % key)]


class _Server:
    """A loopback TLS server presenting one chain; every handshake's outcome is the client's business."""

    def __init__(self, chain, key):
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(chain, key)
        self.socket = socket.create_server(("127.0.0.1", 0))
        self.port = self.socket.getsockname()[1]
        self.worker = threading.Thread(target=self.serve, daemon=True)
        self.worker.start()

    def serve(self):
        while True:
            try:
                connection, _ = self.socket.accept()
            except OSError:
                return
            try:
                with self.context.wrap_socket(connection, server_side=True) as tls:
                    tls.recv(1)
            except (OSError, ssl.SSLError):
                pass

    def close(self):
        self.socket.close()
        self.worker.join(timeout=5)


def handshake(port, context):
    """None when the handshake verified, else the verification code (or the error's text)."""
    with socket.create_connection(("127.0.0.1", port), timeout=10) as raw:
        try:
            with context.wrap_socket(raw, server_hostname="127.0.0.1"):
                return None
        except ssl.SSLCertVerificationError as error:
            return error.verify_code if error.verify_code else error.verify_message
        except ssl.SSLError as error:
            return str(error)


def context_with_roots(bundle):
    """``team_http.tls_context()`` whose default roots are exactly ``bundle`` (no system file, folder or store)."""
    empty = tempfile.mkdtemp(prefix="angelo-no-roots-")
    try:
        with patch.dict(os.environ, {"SSL_CERT_FILE": str(bundle), "SSL_CERT_DIR": empty}), \
                patch.object(ssl.SSLContext, "_load_windows_store_certs", lambda *_args: None, create=True):
            return team_http.tls_context(managed_policy.Policy())
    finally:
        os.rmdir(empty)


class DistrustListTests(unittest.TestCase):
    def test_every_compromised_key_is_listed_once_from_its_real_certificates(self):
        listed = {entry["spki_sha256"]: entry["name"] for entry in tls_distrust.entries()}
        for key in KEYS:
            emsign = tls_distrust.spki_sha256(der(FIXTURES / ("%s.emsign.pem" % key)))
            cross_signed = tls_distrust.spki_sha256(der(FIXTURES / ("%s.aaa.pem" % key)))
            self.assertEqual(emsign, cross_signed, "%s: a cross-sign certifies the same key" % key)
            self.assertIn(emsign, listed, key)
        root = tls_distrust.spki_sha256(der(FIXTURES / "emsign_ecc_root_ca_g3.root.pem"))
        self.assertIn(root, listed)
        self.assertEqual(len(listed), len(tls_distrust.entries()), "a key is listed twice")
        self.assertEqual(len(listed), len(KEYS) + 1, "every listed key has its certificate in the fixtures")
        trust_store.distrusted_keys()    # Every entry's spki matches its digest.
        subjects = {tls_distrust.spki_sha256(der(path)): tls_distrust.subject_name(der(path))
                    for path in FIXTURES.glob("*.emsign.pem")}
        subjects[root] = tls_distrust.subject_name(der(FIXTURES / "emsign_ecc_root_ca_g3.root.pem"))
        for entry in tls_distrust.entries():
            self.assertEqual(base64.b64decode(entry["subject"]), subjects[entry["spki_sha256"]], entry["name"])

    def test_spki_digest_matches_openssl(self):
        if not shutil.which("openssl"):
            self.skipTest("OpenSSL is required to cross-check the DER reader")
        import hashlib
        for path in sorted(FIXTURES.glob("*.pem")):
            reference = subprocess.run([shutil.which("openssl"), "x509", "-in", str(path), "-noout", "-pubkey"],
                                       check=True, capture_output=True, text=True).stdout
            spki = ssl.PEM_cert_to_DER_cert(reference.replace("PUBLIC KEY", "CERTIFICATE"))
            self.assertEqual(tls_distrust.spki_sha256(der(path)), hashlib.sha256(spki).hexdigest(), path.name)


class KeyScanTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="angelo-trust-scan-"))
        self.addCleanup(shutil.rmtree, self.folder)

    def test_a_pem_bundle_or_a_compiled_in_anchor_with_a_distrusted_key_is_refused(self):
        clean = (FIXTURES / "aaa_certificate_services.root.pem").read_text(encoding="ascii")
        (self.folder / "cacert.pem").write_text(clean, encoding="ascii")
        self.assertEqual(trust_store.key_problems(self.folder), [])
        (self.folder / "cacert.pem").write_text(
            clean + (FIXTURES / "emsign_ecc_root_ca_g3.root.pem").read_text(encoding="ascii"), encoding="ascii")
        root = der(FIXTURES / "emsign_ecc_root_ca_g3.root.pem")
        root_bits, root_subject = tls_distrust.key_bits(tls_distrust.spki_der(root)), tls_distrust.subject_name(root)
        # A compiled-in anchor (webpki-roots: subject and key as separate statics) is refused ...
        (self.folder / "codex").write_bytes(os.urandom(512) + root_subject + os.urandom(4096) + root_bits + os.urandom(64))
        # ... a deny list holding the bare keys (rustls-webpki 0.103.16's DISTRUSTED_SPKIS, the fix itself) is not.
        intermediate_bits = [tls_distrust.key_bits(tls_distrust.spki_der(der(FIXTURES / ("%s.emsign.pem" % key))))
                             for key in KEYS]
        (self.folder / "codex-fixed").write_bytes(os.urandom(512) + b"".join(intermediate_bits) + root_bits)
        found = trust_store.key_problems(self.folder)
        self.assertEqual(len(found), 2, found)   # The PEM bundle's certificate and the binary's compiled-in anchor.
        self.assertTrue(all("emSign ECC Root CA - G3" in problem for problem in found))
        self.assertFalse(any(problem.startswith("codex-fixed") for problem in found))

    def test_a_known_exception_accepts_only_its_exact_file_and_key(self):
        root = der(FIXTURES / "emsign_ecc_root_ca_g3.root.pem")
        anchor = os.urandom(256) + tls_distrust.subject_name(root) + os.urandom(256) + \
            tls_distrust.key_bits(tls_distrust.spki_der(root))
        helper = self.folder / "agent-runtime" / "codex-windows-sandbox-setup.exe"
        helper.parent.mkdir()
        helper.write_bytes(anchor)
        allowance = {"file": helper.name, "sha256": hashlib.sha256(anchor).hexdigest(),
                     "key": "emSign ECC Root CA - G3", "runtime_version": "0.163.0", "until": "stable 0.164"}
        found, accepted = trust_store.key_scan(self.folder, allowances=[allowance])
        self.assertEqual(found, [])
        self.assertEqual(len(accepted), 1)
        self.assertIn("known exception until stable 0.164", accepted[0])
        for changed in (dict(allowance, sha256="0" * 64), dict(allowance, key="EM DV TLS CA - G3B"),
                        dict(allowance, file="codex.exe")):
            found, accepted = trust_store.key_scan(self.folder, allowances=[changed])
            self.assertEqual((len(found), accepted), (1, []), changed)

    def test_every_listed_exception_is_live_for_the_pinned_runtime_and_names_an_end(self):
        pin = json.loads((ROOT / "scripts/addons_core/angelo_agent/agent_runtime.json").read_text(encoding="utf-8"))
        listed = trust_store.exceptions()
        names = {entry[0] for entry in trust_store.distrusted_keys()}
        for entry in listed:
            self.assertEqual(entry["runtime_version"], pin["version"], entry["file"])
            self.assertIn(entry["key"], names)
            self.assertRegex(entry["sha256"], "^[0-9a-f]{64}$")
            self.assertTrue(entry["until"] and entry["reason"] and entry["decided"], entry["file"])
            self.assertIn(entry["file"], pin["platforms"]["windows"].get("upstream_signed", []),
                          "only third-party files Angelo ships unchanged")
        self.assertEqual(trust_store.version_problems(self.folder, runtime_pin=dict(pin, version="0.164.0"),
                                                      newest={})[:len(listed)],
                         ["the exception for %s (Codex %s) has expired: the bundled runtime is 0.164.0; remove it from "
                          "trust_store_exceptions.json" % (entry["file"], entry["runtime_version"]) for entry in listed])


class VersionTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="angelo-trust-versions-"))
        self.addCleanup(shutil.rmtree, self.folder)
        self.pin = trust_store.pins()

    def install(self, folder, name, version):
        (self.folder / folder / name).mkdir(parents=True, exist_ok=True)
        (self.folder / folder / ("%s-%s.dist-info" % (name, version))).mkdir()

    def certifi(self, folder, version):
        (self.folder / folder).mkdir(parents=True, exist_ok=True)
        (self.folder / folder / "__init__.py").write_text('__version__ = "%s"\n' % version, encoding="utf-8")

    def test_stale_copies_versions_and_newer_releases_are_refused(self):
        version = json.loads((ROOT / "scripts/addons_core/angelo_agent/agent_runtime.json").read_text())["version"]
        runtime = {"version": version, "trust_roots": {"webpki-roots": self.pin["floors"]["webpki-roots"]}}
        for package in self.pin["packages"]:
            self.install("site-packages", package["name"], package["version"])
        self.certifi("site-packages/certifi", self.pin["floors"]["certifi"])
        self.assertEqual(trust_store.version_problems(self.folder, runtime_pin=runtime), [])
        self.certifi("site-packages/pip/_vendor/certifi", "2026.01.04")
        newest = {"certifi": "2099.1.1", "webpki-roots": OSError("offline")}
        found = trust_store.version_problems(self.folder, runtime_pin={"version": version}, newest=newest)
        self.assertTrue(any("pip/_vendor/certifi is certifi 2026.01.04" in problem for problem in found), found)
        self.assertTrue(any("declares no trust_roots" in problem for problem in found), found)
        self.assertTrue(any("2099.1.1 is published" in problem for problem in found), found)
        self.assertTrue(any("could not be looked up" in problem for problem in found), found)


    def test_every_other_copy_of_a_pin_matches_the_json(self):
        # A precompiled-library rebuild (versions.cmake) and CI's pip (pip-bootstrap.txt) use the same versions.
        pinned = {package["name"]: package for package in self.pin["packages"]}
        versions = (ROOT / "build_files/build_environment/cmake/versions.cmake").read_text(encoding="utf-8")
        self.assertIn("set(CERTIFI_VERSION %s)" % pinned["certifi"]["version"], versions)
        self.assertIn("set(PYTHON_PIP_VERSION %s)" % pinned["pip"]["version"], versions)
        bootstrap = (ROOT / ".github/pip-bootstrap.txt").read_text(encoding="utf-8")
        self.assertIn("pip==%s --hash=sha256:%s" % (pinned["pip"]["version"], pinned["pip"]["sha256"]), bootstrap)
        for package in self.pin["packages"]:
            self.assertTrue(package["url"].endswith("/%s-%s-py3-none-any.whl" % (package["name"], package["version"])))
            self.assertGreaterEqual(trust_store.version_key(pinned["certifi"]["version"]),
                                    trust_store.version_key(self.pin["floors"]["certifi"]))


class SitecustomizeTests(unittest.TestCase):
    """The bundled ``sitecustomize.py`` installs the check in every bundled interpreter, after its first
    ``import ssl`` and never before (importing ``ssl`` costs ~20 ms per start)."""

    PROBE = """
import ast, os, sys
source = open(sys.argv[1], encoding="utf-8").read()
function = next(node for node in ast.parse(source).body
                if isinstance(node, ast.FunctionDef) and node.name == "_angelo_tls_distrust")
namespace = {"os": os, "sys": sys}
exec(compile(ast.Module([function], []), sys.argv[1], "exec"), namespace)
namespace["_angelo_tls_distrust"](sys.argv[2])
print("ssl before:", "ssl" in sys.modules)
import ssl
print("socket:", ssl.SSLContext.sslsocket_class.__name__, "object:", ssl.SSLContext.sslobject_class.__name__)
"""

    def probe(self, module_text):
        version = Path(tempfile.mkdtemp(prefix="angelo-site-"))
        self.addCleanup(shutil.rmtree, version)
        folder = version / "scripts/addons_core/angelo_agent"
        folder.mkdir(parents=True)
        (folder / "tls_distrust.py").write_text(module_text, encoding="utf-8")
        shutil.copy(ROOT / "scripts/addons_core/angelo_agent/distrusted_keys.json", folder)
        return subprocess.run([sys.executable, "-S", "-c", self.PROBE, str(ROOT / "scripts/site/sitecustomize.py"),
                               str(version)], capture_output=True, text=True, timeout=60)

    def test_installs_after_the_first_ssl_import_and_not_before(self):
        result = self.probe((ROOT / "scripts/addons_core/angelo_agent/tls_distrust.py").read_text(encoding="utf-8"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ssl before: False", result.stdout)
        self.assertIn("socket: DistrustCheckingSocket object: DistrustCheckingObject", result.stdout)

    def test_a_broken_module_never_stops_the_interpreter(self):
        result = self.probe("raise RuntimeError('broken on purpose')\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("socket: SSLSocket object: SSLObject", result.stdout)
        self.assertIn("the TLS distrust check is not installed: RuntimeError", result.stderr)


class _OpenSSLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("openssl"):
            raise unittest.SkipTest("OpenSSL is required for the throwaway keys")
        cls.directory = Path(tempfile.mkdtemp(prefix="angelo-trust-tls-"))
        cls.addClassCleanup(shutil.rmtree, cls.directory)


def forge(directory, intermediates, name):
    """A chain file (forged leaf + ``intermediates``) and the leaf's key, as a holder of the key would serve."""
    issuer = intermediates[0]
    issuer_key, leaf_key = directory / ("%s.issuer.key" % name), directory / ("%s.leaf.key" % name)
    for key in (issuer_key, leaf_key):
        openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", key)
    config = directory / ("%s.cnf" % name)
    # A stand-in issuer with the intermediate's subject; its key is ours, not the compromised one. The leaf names
    # the real intermediate's key identifier, so OpenSSL builds the chain through the real intermediate.
    subject = "/" + openssl("x509", "-in", issuer, "-noout", "-subject", "-nameopt", "compat").split("/", 1)[1].strip()
    text = openssl("x509", "-in", issuer, "-noout", "-text").splitlines()
    skid = text[text.index(next(line for line in text if "Subject Key Identifier" in line)) + 1].strip()
    skid = skid.replace("keyid:", "").replace(":", "")
    config.write_text(CA_CONFIG + "[leaf]\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n"
                      "extendedKeyUsage=serverAuth\nsubjectAltName=IP:127.0.0.1\n2.5.29.35=DER:30:16:80:14:%s\n"
                      % ":".join(skid[i:i + 2] for i in range(0, len(skid), 2)), encoding="ascii")
    openssl("req", "-x509", "-new", "-sha256", "-key", issuer_key, "-subj", subject, "-days", "1", "-config", config,
            "-extensions", "ca", "-out", directory / ("%s.issuer.pem" % name))
    openssl("req", "-new", "-key", leaf_key, "-subj", "/CN=127.0.0.1", "-config", config,
            "-out", directory / ("%s.csr" % name))
    openssl("x509", "-req", "-sha256", "-in", directory / ("%s.csr" % name), "-CA", directory / ("%s.issuer.pem" % name),
            "-CAkey", issuer_key, "-set_serial", "7", "-days", "1", "-extfile", config, "-extensions", "leaf",
            "-out", directory / ("%s.leaf.pem" % name))
    chain = directory / ("%s.chain.pem" % name)
    chain.write_text("".join(Path(path).read_text(encoding="ascii")
                             for path in [directory / ("%s.leaf.pem" % name)] + list(intermediates)),
                     encoding="ascii")
    return chain, leaf_key


class ForgedChainTests(_OpenSSLTests):
    """Through either path, a chain under a compromised key finds no anchor in the shipped stores."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.servers = {}
        for key in KEYS:
            for path_name, path in (("emsign", emsign_path(key)), ("aaa", aaa_path(key))):
                server = _Server(*forge(cls.directory, path, "%s.%s" % (key, path_name)))
                cls.addClassCleanup(server.close)
                cls.servers[(key, path_name)] = server

    def codes(self, bundle, path_name):
        context = context_with_roots(bundle)
        return {key: handshake(self.servers[(key, path_name)].port, context) for key in KEYS}

    def test_the_controls_anchor_every_forged_chain(self):
        # The roots the shipped stores must not hold: with either one, every chain on its path is anchored, so
        # the test below would see a regression to certifi 2025 (emSign) or a store that adds AAA.
        for root, path_name in (("emsign_ecc_root_ca_g3", "emsign"), ("aaa_certificate_services", "aaa")):
            for key, code in self.codes(FIXTURES / ("%s.root.pem" % root), path_name).items():
                self.assertNotIn(code, (None, UNANCHORED), "%s via %s" % (key, root))

    def test_the_shipped_stores_anchor_no_forged_chain_and_hold_no_distrusted_key(self):
        bundles = shipped_bundles(self.directory)
        if not bundles:
            self.skipTest("set ANGELO_PYTHON_WHEELS_DIR (trust_store.py fetch-wheels) or ANGELO_TRUST_INSTALL")
        for bundle in bundles:
            self.assertEqual(trust_store.key_problems(bundle.parent), [], bundle)
            for path_name in ("emsign", "aaa"):
                for key, code in self.codes(bundle, path_name).items():
                    self.assertEqual(code, UNANCHORED, "%s via %s anchored by %s" % (key, path_name, bundle))


def shipped_bundles(directory):
    """Every certifi ``cacert.pem`` the pinned wheels and/or an installed app carry."""
    bundles = []
    wheels = os.environ.get("ANGELO_PYTHON_WHEELS_DIR")
    if wheels:
        for package in trust_store.pins()["packages"]:
            wheel = Path(wheels) / package["url"].rsplit("/", 1)[-1]
            with zipfile.ZipFile(wheel) as archive:
                for name in archive.namelist():
                    if name.endswith("certifi/cacert.pem"):
                        target = directory / package["name"] / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(archive.read(name))
                        bundles.append(target)
    install = os.environ.get("ANGELO_TRUST_INSTALL")
    if install:
        bundles += [Path(path) for path in glob.glob(os.path.join(install, "**", "certifi", "cacert.pem"),
                                                      recursive=True)]
    return bundles


class DistrustCheckTests(_OpenSSLTests):
    """A chain through a distrusted key fails even when a trusted root (an OS store, a policy root) anchors it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        d = cls.directory
        config = d / "ca.cnf"
        config.write_text(CA_CONFIG + "authorityKeyIdentifier=keyid:always\n"
                          "[leaf]\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n"
                          "extendedKeyUsage=serverAuth\nsubjectAltName=IP:127.0.0.1\nsubjectKeyIdentifier=hash\n"
                          "authorityKeyIdentifier=keyid:always\n", encoding="ascii")
        for name in ("root", "intermediate", "leaf"):
            openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", d / ("%s.key" % name))
        openssl("req", "-x509", "-new", "-sha256", "-key", d / "root.key", "-subj", "/CN=Angelo test root", "-days", "1",
                "-config", config, "-extensions", "ca", "-out", d / "root.pem")
        for name, issuer, section in (("intermediate", "root", "ca"), ("leaf", "intermediate", "leaf")):
            subject = "/CN=127.0.0.1" if name == "leaf" else "/CN=Angelo test intermediate"
            openssl("req", "-new", "-key", d / ("%s.key" % name), "-subj", subject, "-out", d / ("%s.csr" % name),
                    "-config", config)
            openssl("x509", "-req", "-sha256", "-in", d / ("%s.csr" % name), "-CA", d / ("%s.pem" % issuer), "-CAkey",
                    d / ("%s.key" % issuer), "-set_serial", "9", "-days", "1", "-extfile", config,
                    "-extensions", section, "-out", d / ("%s.pem" % name))
        (d / "chain.pem").write_text((d / "leaf.pem").read_text() + (d / "intermediate.pem").read_text())
        cls.server = _Server(d / "chain.pem", d / "leaf.key")
        cls.addClassCleanup(cls.server.close)
        cls.policy = managed_policy.Policy(extra_ca_certificates=str(d / "root.pem"))

    def connect(self, distrusted=()):
        keys = frozenset(tls_distrust.spki_sha256(der(self.directory / ("%s.pem" % name))) for name in distrusted)
        with patch.object(tls_distrust, "_KEYS", keys):
            return handshake(self.server.port, team_http.tls_context(self.policy))

    def test_a_trusted_chain_still_connects(self):
        self.assertIsNone(self.connect())

    def test_a_distrusted_intermediate_or_root_fails_verification(self):
        for name in ("intermediate", "root"):
            self.assertEqual(self.connect([name]), "distrusted key", name)

    def plain_context(self):
        context = ssl.create_default_context(cafile=str(self.directory / "root.pem"))
        return context

    def installed(self, distrusted):
        """``tls_distrust.install()`` for one test, with ``distrusted`` (fixture names) as the list."""
        keys = frozenset(tls_distrust.spki_sha256(der(self.directory / ("%s.pem" % name))) for name in distrusted)
        self.addCleanup(setattr, ssl.SSLContext, "sslsocket_class", ssl.SSLContext.sslsocket_class)
        self.addCleanup(setattr, ssl.SSLContext, "sslobject_class", ssl.SSLContext.sslobject_class)
        tls_distrust.install()
        patcher = patch.object(tls_distrust, "_KEYS", keys)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_installed_every_context_in_the_process_refuses_it(self):
        # Blender's extension downloads, add-ons and requests build their own contexts; on Windows those hold the
        # system's ROOT store (AAA Certificate Services) as anchors.
        self.installed([])
        self.assertIsNone(handshake(self.server.port, self.plain_context()))
        self.installed(["intermediate"])
        self.assertEqual(handshake(self.server.port, self.plain_context()), "distrusted key")

    def test_installed_asyncio_connections_refuse_it_too(self):
        import asyncio

        async def connect():
            reader, writer = await asyncio.open_connection("127.0.0.1", self.server.port, ssl=self.plain_context(),
                                                           server_hostname="127.0.0.1")
            writer.close()

        self.installed([])
        asyncio.run(connect())
        self.installed(["intermediate"])
        with self.assertRaises(ssl.SSLCertVerificationError):
            asyncio.run(connect())

    def test_a_resumed_session_is_not_refused(self):
        # A resumed session reports no verified chain; its chain was checked when the session was made.
        self.installed([])
        context = self.plain_context()
        context.maximum_version = ssl.TLSVersion.TLSv1_2
        with socket.create_connection(("127.0.0.1", self.server.port), timeout=10) as raw:
            with context.wrap_socket(raw, server_hostname="127.0.0.1") as tls:
                tls.sendall(b"x")
                session = tls.session
        with socket.create_connection(("127.0.0.1", self.server.port), timeout=10) as raw:
            with context.wrap_socket(raw, server_hostname="127.0.0.1", session=session) as tls:
                self.assertTrue(tls.session_reused)

    def test_the_team_transport_reports_it_as_never_sent(self):
        keys = frozenset([tls_distrust.spki_sha256(der(self.directory / "intermediate.pem"))])
        with patch.object(tls_distrust, "_KEYS", keys), \
                patch.object(managed_policy, "current", return_value=self.policy):
            with self.assertRaises(NotSent):
                team_http.http_transport("GET", "https://127.0.0.1:%d/" % self.server.port)


if __name__ == "__main__":
    unittest.main()
