# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Publishing installers to R2 (scripts/angelo/publish_installers.py, r2_client.py, release_downloads.py).

Dependency-free and offline: a loopback stand-in for R2 (its S3 API and its public custom domain) that checks
every request's signature against the headers actually received, keeps objects with their headers, honours
``If-None-Match: *`` and can answer with errors; a recording stand-in for codesign, stapler and spctl; and tiny
hand-built Compound Files for the MSI checks. Never a real bucket. Covered: the download layout and SHA256SUMS;
SigV4 against AWS's published examples; publishing both installers with their headers, SHA256SUMS last and the
public download verified byte for byte; every refusal (a name that is not the version's, a Git LFS pointer, an
unsigned, unstapled or unnotarized disk image, a disk image off macOS, an MSI without Authenticode, an existing
object, a lost race, a refused token, missing credentials) before anything is sent; a corrupted or mislabelled
download; the dry run and ``--verify-only`` without credentials; ``--check`` (probe written, read, served by the
domain and deleted); the Keychain wrapper; that no credential or provider error body is ever printed; and the
update feed and the macOS receipt naming the same addresses.
"""

import hashlib
import io
import json
import os
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/angelo"))
import builder_paths  # noqa: E402
import publish_installers as tool  # noqa: E402
import r2_client  # noqa: E402
import release_downloads as layout  # noqa: E402
import sign_notarize_macos  # noqa: E402
import sign_update_feed  # noqa: E402

ACCOUNT = "0123456789abcdef0123456789abcdef"
KEY_ID = "TESTKEYID-NEVER-PRINTED"
SECRET = "TESTSECRET-NEVER-PRINTED-0123456789"
ENVIRON = {"R2_ACCOUNT_ID": ACCOUNT, "R2_DOWNLOADS_ACCESS_KEY_ID": KEY_ID, "R2_DOWNLOADS_SECRET_ACCESS_KEY": SECRET}
PROVIDER_SECRET = "PROVIDER-BODY-MUST-NOT-BE-PRINTED"
DMG = "Angelo-1.0.0-macos-arm64.dmg"
MSI = "Angelo-1.0.0-windows-x64.msi"


def compound_file(names, broken=False):
    """A minimal Compound File (MS-CFB version 3): the FAT in sector 0, the directory in sector 1."""
    header = bytearray(512)
    header[0:8] = tool.CFB_MAGIC
    struct.pack_into("<HHHHH", header, 0x18, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<IIIIIIIII", header, 0x28, 0, 1, 1, 0, 4096, tool.CFB_END, 0, tool.CFB_END, 0)
    struct.pack_into("<109I", header, 0x4C, 0, *([tool.CFB_FREE] * 108))
    fat = [0xFFFFFFFD, 1 if broken else tool.CFB_END] + [tool.CFB_FREE] * 126   # broken: the directory loops.
    directory = bytearray(512)
    for index, (name, kind) in enumerate([("Root Entry", 5)] + [(name, 2) for name in names]):
        encoded = (name + "\0").encode("utf-16-le")
        directory[index * 128:index * 128 + len(encoded)] = encoded
        struct.pack_into("<HB", directory, index * 128 + 64, len(encoded), kind)
    return bytes(header) + struct.pack("<128I", *fat) + bytes(directory) + b"msi body" * 300


class FakeR2:
    """The bucket's S3 API at ``/<bucket>/<key>`` and its custom domain at ``/public/<key>``, on loopback."""

    def __init__(self):
        self.objects, self.log, self.fail, self.public_fault = {}, [], {}, None
        self.bad_signatures = 0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _answer(self, status, headers=None, body=b""):
                """A HEAD answer carries the object's Content-Length (given in ``headers``) and no body."""
                headers = dict(headers or {})
                length = headers.pop("Content-Length", str(len(body)))
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", length)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def handle_any(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                if self.path.startswith("/public/"):
                    return fake.public(self, self.path[len("/public/"):])
                return fake.s3(self, body)

            do_GET = do_HEAD = do_PUT = do_DELETE = handle_any

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        port = self.server.server_address[1]
        self.target = tool.Target("http://127.0.0.1:%d" % port, "http://127.0.0.1:%d/public" % port)

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def signature_ok(self, handler, body):
        """Recompute the signature from what arrived: the method, path and every header it names."""
        authorization = handler.headers.get("Authorization", "")
        if not authorization.startswith("AWS4-HMAC-SHA256 Credential=%s/" % KEY_ID):
            return False
        names = authorization.split("SignedHeaders=")[1].split(",")[0].split(";")
        if not {"host", "x-amz-date", "x-amz-content-sha256"} <= set(names):
            return False
        if "/auto/s3/aws4_request" not in authorization:
            return False
        if handler.headers["x-amz-content-sha256"] != hashlib.sha256(body).hexdigest():
            return False
        received = {name: handler.headers[name] for name in names}
        stamp = datetime.strptime(handler.headers["x-amz-date"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        payload = handler.headers["x-amz-content-sha256"]
        expected = r2_client.sign(handler.command, unquote(handler.path), received, payload,
                                  r2_client.Credentials(KEY_ID, SECRET), now=stamp)
        return expected["Authorization"] == authorization

    def s3(self, handler, body):
        method, key = handler.command, handler.path.split("/", 2)[2]
        self.log.append((method, key, dict(handler.headers)))
        if not self.signature_ok(handler, body):
            self.bad_signatures += 1
            return handler._answer(403, {}, b"<Error>SignatureDoesNotMatch</Error>")
        if (method, key) in self.fail:
            return handler._answer(self.fail[(method, key)], {"Content-Type": "application/xml"},
                                   ("<Error><Message>%s</Message></Error>" % PROVIDER_SECRET).encode())
        stored = self.objects.get(key)
        if method == "PUT":
            if handler.headers.get("If-None-Match") == "*" and stored:
                return handler._answer(412)
            kept = {name: handler.headers[name] for name in ("Content-Type", "Cache-Control", "Content-Disposition")
                    if handler.headers.get(name)}
            self.objects[key] = (body, kept)
            return handler._answer(200, {"ETag": '"%s"' % hashlib.md5(body).hexdigest()})
        if stored is None:
            return handler._answer(404 if method != "DELETE" else 204)
        if method == "DELETE":
            del self.objects[key]
            return handler._answer(204)
        return handler._answer(200, dict(stored[1], **{"Content-Length": str(len(stored[0]))}), stored[0])

    def public(self, handler, key):
        stored = self.objects.get(key)
        if stored is None:
            return handler._answer(404, {}, b"not found")
        data, headers = stored
        headers = dict(headers)
        if self.public_fault == "corrupt":
            data = data[:-1] + bytes([data[-1] ^ 1])
        elif self.public_fault == "type":
            headers["Content-Type"] = "application/octet-stream"
        elif self.public_fault == "missing":
            return handler._answer(404, {}, b"not found")
        return handler._answer(200, dict(headers, **{"Content-Length": str(len(data))}), data)


class NotarizedTools:
    """codesign, stapler and spctl as for a stapled, notarized image; ``refuse`` names a tool that fails."""

    def __init__(self, refuse=None, verdict="Notarized Developer ID"):
        self.log, self.refuse, self.verdict = [], refuse, verdict

    # What the mounted image holds (hdiutil attach): a clean app unless a test puts a builder path in it.
    image_files = {"Angelo.app/Contents/MacOS/Angelo": b"\xcf\xfa\xed\xfe clean executable"}

    def __call__(self, argv, check=True, timeout=None, env=None):
        self.log.append(list(argv))
        if self.refuse and self.refuse in argv:
            raise sign_notarize_macos.ReleaseError("%s failed (65): does not have a ticket stapled to it" % self.refuse)
        if argv[:2] == ["hdiutil", "attach"]:
            mount = Path(argv[argv.index("-mountpoint") + 1])
            for name, data in self.image_files.items():
                (mount / name).parent.mkdir(parents=True, exist_ok=True)
                (mount / name).write_bytes(data)
        elif argv[:2] == ["hdiutil", "detach"]:
            shutil.rmtree(argv[2], ignore_errors=True)
            Path(argv[2]).mkdir(exist_ok=True)
        result = SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[0] == "spctl":
            result.stderr = "%s: accepted\nsource=%s\n" % (argv[-1], self.verdict)
        return result


class Case(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.dmg = self.folder / DMG
        self.dmg.write_bytes(os.urandom(200_000))
        self.msi = self.folder / MSI
        self.msi.write_bytes(compound_file(["\x05DigitalSignature", "\x05SummaryInformation"]))
        self.write_receipt(self.msi)
        self.r2 = FakeR2()
        self.addCleanup(self.r2.close)
        self.tools = NotarizedTools()

    def write_receipt(self, msi, hits=0):
        """The receipt the Windows stage writes after scanning the MSI's unpacked payload (builder_paths)."""
        receipt = {"kind": builder_paths.RECEIPT_KIND, "installer": msi.name, "sha256": builder_paths.sha256_of(msi),
                   "files": 9000, "hits": hits, "allow_sha256": builder_paths.sha256_of(builder_paths.ALLOW_FILE)}
        msi.with_name(msi.name + ".builder-paths.json").write_text(json.dumps(receipt))

    def run_tool(self, *argv, environ=ENVIRON, host="darwin", target="fake"):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = tool.main(list(argv), target=self.r2.target if target == "fake" else target, runner=self.tools,
                             environ=environ, host_platform=host)
        printed = out.getvalue() + err.getvalue()
        for secret in (KEY_ID, SECRET, PROVIDER_SECRET, "Signature="):
            self.assertNotIn(secret, printed)
        self.errors = err.getvalue()
        return code, out.getvalue()

    def publish(self, *extra, **options):
        return self.run_tool("--version", "1.0.0", "--dmg", str(self.dmg), "--msi", str(self.msi), *extra, **options)

    def puts(self):
        return [key for method, key, _ in self.r2.log if method == "PUT"]


class LayoutTests(unittest.TestCase):
    def test_names_addresses_headers_and_sums(self):
        self.assertEqual(layout.installer_url("1.0.0", "macos-arm64"),
                         "https://downloads.angelo.studio/1.0.0/Angelo-1.0.0-macos-arm64.dmg")
        self.assertEqual(layout.installer_url("1.2.3", "windows-x64"),
                         "https://downloads.angelo.studio/1.2.3/Angelo-1.2.3-windows-x64.msi")
        self.assertEqual(layout.url("1.0.0", layout.SUMS), "https://downloads.angelo.studio/1.0.0/SHA256SUMS")
        self.assertEqual(layout.headers(DMG), {"Content-Type": "application/x-apple-diskimage",
                                               "Cache-Control": "public, max-age=31536000, immutable",
                                               "Content-Disposition": "attachment"})
        self.assertEqual(layout.headers(MSI)["Content-Type"], "application/x-msi")
        self.assertEqual(layout.headers(layout.SUMS)["Content-Type"], "text/plain")
        self.assertEqual(layout.sums_text({MSI: "b" * 64, DMG: "a" * 64}),
                         "%s  %s\n%s  %s\n" % ("a" * 64, DMG, "b" * 64, MSI))
        for version in ("1.0", "v1.0.0", "1.0.0-rc7", "1.0.0/../x", ""):
            with self.assertRaises(layout.LayoutError):
                layout.installer_name(version, "macos-arm64")
        with self.assertRaises(layout.LayoutError):
            layout.installer_name("1.0.0", "linux-x64")
        with self.assertRaises(layout.LayoutError):
            layout.headers("notes.txt")


class SignatureTests(unittest.TestCase):
    """AWS's published Signature Version 4 examples for S3 (GET with a range, PUT with a storage class)."""

    def test_aws_examples(self):
        credentials = r2_client.Credentials("AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
        now = datetime(2013, 5, 24, tzinfo=timezone.utc)
        get = r2_client.sign("GET", "/test.txt", {"Host": "examplebucket.s3.amazonaws.com", "Range": "bytes=0-9"},
                             r2_client.EMPTY_SHA256, credentials, now=now, region="us-east-1")
        self.assertEqual(get["Authorization"],
                         "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
                         "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
                         "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")
        body = b"Welcome to Amazon S3."
        put = r2_client.sign("PUT", "/test$file.text", {"Host": "examplebucket.s3.amazonaws.com",
                                                        "Date": "Fri, 24 May 2013 00:00:00 GMT",
                                                        "x-amz-storage-class": "REDUCED_REDUNDANCY"},
                             hashlib.sha256(body).hexdigest(), credentials, now=now, region="us-east-1")
        self.assertTrue(put["Authorization"].endswith(
            "Signature=98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd"))
        self.assertEqual(put["x-amz-date"], "20130524T000000Z")

    def test_credentials_never_show_and_only_https_or_loopback(self):
        self.assertNotIn(SECRET, repr(r2_client.Credentials(KEY_ID, SECRET)))
        self.assertNotIn(KEY_ID, repr(r2_client.Credentials(KEY_ID, SECRET)))
        with self.assertRaises(ValueError):
            r2_client.connection("http://downloads.angelo.studio", 5)
        self.assertEqual(tool.Target.production(ACCOUNT).endpoint, "https://%s.r2.cloudflarestorage.com" % ACCOUNT)
        self.assertEqual(tool.Target.production(ACCOUNT).public_base, "https://downloads.angelo.studio")


class PublishTests(Case):
    def test_publishes_both_with_headers_sums_last_and_verifies_the_download(self):
        code, out = self.publish()
        self.assertEqual(code, 0, self.errors)
        self.assertEqual(self.r2.bad_signatures, 0)
        self.assertEqual(self.puts(), ["1.0.0/" + DMG, "1.0.0/" + MSI, "1.0.0/SHA256SUMS"])
        for method, key, headers in self.r2.log:
            if method == "PUT":
                self.assertEqual(headers["If-None-Match"], "*")
                self.assertNotIn("Transfer-Encoding", headers)
        for name in (DMG, MSI, "SHA256SUMS"):
            self.assertEqual(self.r2.objects["1.0.0/" + name][1], layout.headers(name))
        self.assertEqual(self.r2.objects["1.0.0/" + DMG][0], self.dmg.read_bytes())
        digest = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (self.dmg, self.msi)}
        self.assertEqual(self.r2.objects["1.0.0/SHA256SUMS"][0].decode(), layout.sums_text(digest))
        result = json.loads(out)
        self.assertEqual(result["website_sha256"], {"macos": digest[DMG], "windows": digest[MSI]})
        self.assertEqual(result["feed_downloads"], ["macos-arm64=%s" % self.dmg, "windows-x64=%s" % self.msi])
        self.assertEqual(len(result["verified"]), 3)
        self.assertEqual(result["sums"], "https://downloads.angelo.studio/1.0.0/SHA256SUMS")
        self.assertIn(["xcrun", "stapler", "validate", str(self.dmg)], self.tools.log)

    def test_one_platform_alone(self):
        code, _ = self.run_tool("--version", "1.0.0", "--msi", str(self.msi))
        self.assertEqual(code, 0, self.errors)
        self.assertEqual(self.puts(), ["1.0.0/" + MSI, "1.0.0/SHA256SUMS"])
        self.assertEqual(self.tools.log, [], "no disk image, no macOS tools")

    def test_an_existing_object_is_never_overwritten(self):
        self.r2.objects["1.0.0/SHA256SUMS"] = (b"old", {})
        code, _ = self.publish()
        self.assertEqual(code, 1)
        self.assertIn("already published, and never overwritten: 1.0.0/SHA256SUMS", self.errors)
        self.assertEqual(self.puts(), [])
        self.assertEqual(self.r2.objects["1.0.0/SHA256SUMS"][0], b"old")

    def test_a_lost_race_and_a_refused_token_stop_without_the_providers_words(self):
        self.r2.fail[("PUT", "1.0.0/" + MSI)] = 412
        code, _ = self.publish()
        self.assertEqual(code, 1)
        self.assertIn("appeared while publishing (HTTP 412)", self.errors)
        self.assertNotIn("1.0.0/SHA256SUMS", self.puts(), "the version is not completed")
        self.r2.objects.clear()
        self.r2.log.clear()
        self.r2.fail = {("HEAD", "1.0.0/" + DMG): 403}
        code, _ = self.publish()
        self.assertEqual(code, 1)
        self.assertIn("HTTP 403; the token is refused: it needs Object Read & Write on angelo-downloads", self.errors)
        self.assertEqual(self.puts(), [])

    def test_a_download_that_differs_from_the_upload_fails(self):
        for fault, words in (("corrupt", "is not the file published"),
                             ("type", "Content-Type 'application/octet-stream'"), ("missing", "answered 404")):
            with self.subTest(fault=fault):
                self.r2.objects.clear()
                self.r2.public_fault = fault
                code, _ = self.publish()
                self.assertEqual(code, 1)
                self.assertIn(words, self.errors)

    def test_dry_run_and_verify_only_need_no_credentials(self):
        code, out = self.publish("--dry-run")
        self.assertEqual(code, 0, self.errors)
        self.assertEqual(self.r2.log, [])
        plan = json.loads(out)
        self.assertEqual([item["url"] for item in plan["objects"]],
                         [layout.url("1.0.0", name) for name in (DMG, MSI, "SHA256SUMS")])
        self.assertIn("  %s\n" % DMG, plan["sha256sums"])
        self.assertEqual(self.publish("--verify-only")[0], 1, "nothing is published yet")
        self.assertEqual(self.run_tool("--version", "1.0.0", "--dmg", str(self.dmg), "--msi", str(self.msi))[0], 0)
        self.r2.log.clear()
        self.tools.log.clear()
        code, _ = self.run_tool("--version", "1.0.0", "--dmg", str(self.dmg), "--msi", str(self.msi), "--verify-only",
                                environ={}, host="linux")
        self.assertEqual(code, 0, self.errors)
        self.assertEqual((self.r2.log, self.tools.log), ([], []), "public reads only; no signature tools")

    def test_missing_or_malformed_credentials_send_nothing(self):
        code, _ = self.publish(environ={"R2_ACCOUNT_ID": ACCOUNT})
        self.assertEqual(code, 1)
        self.assertIn("R2_DOWNLOADS_ACCESS_KEY_ID, R2_DOWNLOADS_SECRET_ACCESS_KEY not set", self.errors)
        code, _ = self.publish(environ=dict(ENVIRON, R2_ACCOUNT_ID="not-an-account"))
        self.assertIn("R2_ACCOUNT_ID is not a Cloudflare account id", self.errors)
        self.assertEqual(self.r2.log, [])


class RefusalTests(Case):
    def refused(self, words, *argv, host="darwin"):
        code, _ = self.run_tool("--version", "1.0.0", *argv, host=host)
        self.assertEqual(code, 1)
        self.assertIn(words, self.errors)
        self.assertEqual(self.r2.log, [], "refused before anything was sent")

    def test_names_pointers_and_versions(self):
        other = self.folder / "Angelo-1.0.1-macos-arm64.dmg"
        shutil.copy(self.dmg, other)
        self.refused("must be named Angelo-1.0.0-macos-arm64.dmg", "--dmg", str(other))
        self.refused("must be named Angelo-1.0.0-windows-x64.msi", "--msi", str(self.dmg))
        self.msi.write_bytes(b"version https://git-lfs.github.com/spec/v1\noid sha256:%s\nsize 9\n" % (b"0" * 64))
        self.refused("is a Git LFS pointer", "--msi", str(self.msi))
        code, _ = self.run_tool("--version", "1.0", "--dmg", str(self.dmg))
        self.assertIn("MAJOR.MINOR.PATCH", self.errors)
        self.refused("give the disk image (--dmg), the MSI (--msi) or both")

    def test_a_disk_image_must_be_signed_stapled_and_notarized_and_checked_on_macos(self):
        self.refused("can only be checked on macOS", "--dmg", str(self.dmg), host="linux")
        self.tools = NotarizedTools(refuse="stapler")
        self.refused("is not a signed, stapled and notarized disk image", "--dmg", str(self.dmg))
        self.tools = NotarizedTools(refuse="codesign")
        self.refused("is not a signed, stapled and notarized disk image", "--dmg", str(self.dmg))
        self.tools = NotarizedTools(verdict="Unnotarized Developer ID")
        self.refused("Gatekeeper does not see a notarized image", "--dmg", str(self.dmg))

    def test_an_installer_with_a_builder_path_is_never_published(self):
        """The mounted disk image is scanned; an MSI needs a clean receipt for exactly its bytes."""
        self.tools.image_files = {"Angelo.app/Contents/MacOS/Angelo": b"assert at /Users/somebody/src/a.cc:12"}
        code, _ = self.publish("--dry-run")
        self.assertEqual(code, 1)
        self.assertIn("embeds builder paths", self.errors)
        self.assertIn("Angelo.app/Contents/MacOS/Angelo", self.errors)
        self.assertTrue(any(argv[:2] == ["hdiutil", "detach"] for argv in self.tools.log), "the image is detached")
        self.tools.image_files = NotarizedTools.image_files
        for broken in ("missing", "stale", "dirty"):
            with self.subTest(receipt=broken):
                receipt = self.msi.with_name(self.msi.name + ".builder-paths.json")
                if broken == "missing":
                    receipt.unlink()
                elif broken == "stale":
                    self.write_receipt(self.msi)
                    self.msi.write_bytes(self.msi.read_bytes() + b"re-signed")
                else:
                    self.write_receipt(self.msi, hits=3)
                code, _ = self.run_tool("--version", "1.0.0", "--msi", str(self.msi), "--dry-run")
                self.assertEqual(code, 1)
                self.assertIn("builder-path", self.errors)
                self.assertEqual(self.puts(), [])
                self.write_receipt(self.msi)

    def test_an_msi_needs_authenticode_unless_knowingly_allowed(self):
        self.msi.write_bytes(compound_file(["\x05SummaryInformation"]))
        self.write_receipt(self.msi)  # its own scan's receipt: these bytes are another build
        self.refused("has no Authenticode signature", "--msi", str(self.msi))
        code, _ = self.run_tool("--version", "1.0.0", "--msi", str(self.msi), "--allow-unsigned-msi", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("warning: publishing %s without an Authenticode signature" % MSI, self.errors)
        self.msi.write_bytes(b"MZ not a compound file" * 50)
        self.refused("is not a Windows Installer package", "--msi", str(self.msi))
        self.msi.write_bytes(compound_file(["\x05DigitalSignature"], broken=True))
        self.refused("has a broken directory chain", "--msi", str(self.msi))

    def test_msi_directory_names(self):
        self.assertEqual(tool.msi_entries(self.msi), ["\x05DigitalSignature", "\x05SummaryInformation"])


class CheckTests(Case):
    def test_probe_is_written_read_served_and_deleted(self):
        code, out = self.run_tool("--check")
        self.assertEqual(code, 0, out)
        self.assertIn("angelo-downloads: ready to publish.", out)
        self.assertEqual(self.r2.objects, {})
        put = [(key, headers) for method, key, headers in self.r2.log if method == "PUT"]
        self.assertEqual(len(put), 1)
        self.assertTrue(put[0][0].startswith("_check/"))
        self.assertEqual(put[0][1]["Cache-Control"], "no-store")
        self.assertEqual([method for method, _, _ in self.r2.log], ["PUT", "GET", "DELETE", "HEAD"])

    def test_a_domain_that_does_not_serve_the_bucket_fails_and_the_probe_is_still_deleted(self):
        self.r2.public_fault = "missing"
        code, out = self.run_tool("--check")
        self.assertEqual(code, 1)
        self.assertIn("FAIL", out)
        self.assertIn("connect downloads.angelo.studio to angelo-downloads as a custom domain", out)
        self.assertIn("angelo-downloads: not ready.", out)
        self.assertEqual(self.r2.objects, {})

    def test_check_takes_nothing_else_and_needs_credentials(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            tool.main(["--check", "--version", "1.0.0"], target=self.r2.target, environ=ENVIRON)
        self.assertEqual(self.run_tool("--check", environ={})[0], 1)
        self.assertIn("R2_ACCOUNT_ID, R2_DOWNLOADS_ACCESS_KEY_ID, R2_DOWNLOADS_SECRET_ACCESS_KEY not set", self.errors)


class WrapperTests(unittest.TestCase):
    """publish_installers.sh with stand-ins for ``security`` and ``python3`` on PATH (no Keychain is touched)."""

    def setUp(self):
        if not shutil.which("bash"):
            self.fail("bash is required")
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.bin = Path(folder.name)
        self.calls = self.bin / "calls.txt"
        self._stub("security", 'echo "security $*" >> "%s"; echo "value-for-$5"' % self.calls)
        # Reports which variables arrived and their lengths, never their values.
        self._stub("python3", 'for n in R2_ACCOUNT_ID R2_DOWNLOADS_ACCESS_KEY_ID R2_DOWNLOADS_SECRET_ACCESS_KEY; do '
                              'v="${!n:-}"; echo "$n=${#v}"; done; echo "args=$*"')

    def _stub(self, name, body):
        path = self.bin / name
        path.write_text("#!/usr/bin/env bash\n%s\n" % body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def run_wrapper(self, *argv, **env):
        environment = {"PATH": "%s:/usr/bin:/bin" % self.bin, "HOME": str(self.bin)}
        environment.update(env)
        return subprocess.run(["bash", str(ROOT / "scripts/angelo/publish_installers.sh")] + list(argv),
                              capture_output=True, text=True, env=environment, timeout=60)

    def test_reads_the_three_names_from_the_named_service_into_the_environment(self):
        result = self.run_wrapper("--check", ANGELO_KEYCHAIN_SERVICE="release-service")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("R2_DOWNLOADS_SECRET_ACCESS_KEY=%d" % len("value-for-R2_DOWNLOADS_SECRET_ACCESS_KEY"),
                      result.stdout)
        self.assertNotIn("value-for", result.stdout + result.stderr)
        calls = self.calls.read_text().splitlines()
        self.assertEqual(calls, ["security find-generic-password -s release-service -a %s -w" % name
                                 for name in ("R2_ACCOUNT_ID", "R2_DOWNLOADS_ACCESS_KEY_ID",
                                              "R2_DOWNLOADS_SECRET_ACCESS_KEY")])

    def test_the_environment_wins_and_dry_runs_never_open_the_keychain(self):
        result = self.run_wrapper("--check", R2_ACCOUNT_ID="a", R2_DOWNLOADS_ACCESS_KEY_ID="b",
                                  R2_DOWNLOADS_SECRET_ACCESS_KEY="c")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_wrapper("--version", "1.0.0", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("args=", result.stdout)
        self.assertFalse(self.calls.exists(), "no Keychain read")
        result = self.run_wrapper("--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("set ANGELO_KEYCHAIN_SERVICE", result.stderr)


class SharedLayoutTests(unittest.TestCase):
    """The update feed and the macOS receipt name the addresses publish_installers.py publishes."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dmg = Path(folder.name) / DMG
        self.dmg.write_bytes(b"disk image")

    def test_feed_downloads_follow_the_layout(self):
        platform, entry = sign_update_feed.download("macos-arm64=%s" % self.dmg, "1.0.0")
        self.assertEqual((platform, entry["url"], entry["size"]),
                         ("macos-arm64", layout.installer_url("1.0.0", "macos-arm64"), 10))
        self.assertEqual(entry["sha256"], hashlib.sha256(b"disk image").hexdigest())
        with self.assertRaises(sign_update_feed.FeedToolError):
            sign_update_feed.download("macos-arm64=%s" % self.dmg, "1.0.1")       # Not the 1.0.1 image.
        with self.assertRaises(sign_update_feed.FeedToolError):
            sign_update_feed.download("windows-x64=%s" % self.dmg, "1.0.0")
        with self.assertRaises(sign_update_feed.FeedToolError):                  # Another version's folder.
            sign_update_feed.download("macos-arm64=https://downloads.angelo.studio/0.9.0/%s=%s" % (DMG, self.dmg),
                                      "1.0.0")
        _, other = sign_update_feed.download("macos-arm64=https://github.com/o/r/releases/download/v1/x.dmg=%s"
                                             % self.dmg, "1.0.0")
        self.assertTrue(other["url"].startswith("https://github.com/"))

    def test_the_signer_receipt_and_the_image_check(self):
        source = (ROOT / "scripts/angelo/sign_notarize_macos.py").read_text(encoding="utf-8")
        self.assertIn('release_downloads.url(IDENTITY["version"]["label"], image.name)', source)
        self.assertNotIn("angelo.studio/downloads", source)
        tools = NotarizedTools()
        verdict = sign_notarize_macos.check_notarized_image(self.dmg, tools)
        self.assertEqual(verdict[-1], "source=Notarized Developer ID")
        self.assertEqual([argv[0] for argv in tools.log], ["xcrun", "spctl"])


if __name__ == "__main__":
    unittest.main()
