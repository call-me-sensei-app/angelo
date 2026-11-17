# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_download``'s arguments and checks (doc/angelo/downloads.rst): no bpy, no network.

Covered: the arguments (https only, a link of at most 2 KB, one of six kinds, an optional name); every address class
a download is refused (loopback, private, link-local and cloud metadata, shared/CGNAT, multicast, reserved and
unspecified, IPv6 unique-local and link-local, and an IPv4 address carried inside IPv6), with public addresses allowed;
the file's type read from its bytes for each kind (never from the server's header or the link), with programs and
scripts refused for every kind; a safe name (a server's file name is only a hint, cleaned; the extension comes from the
checked type; no reserved device name, no hidden name, no separator).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

from angelo_agent import download_checks as checks  # noqa: E402
from angelo_agent.download_spec import (DOWNLOAD_NAMES, DOWNLOAD_TOOLS, KINDS, MAX_URL_BYTES, NAME,  # noqa: E402
                                        validate_download)

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 64
EXR = b"\x76\x2f\x31\x01\x02\x00\x00\x00" + b"\x00" * 64
HDR = b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y 4 +X 4\n" + b"\x00" * 32
GLB = b"glTF" + struct.pack("<II", 2, 1024) + b"\x00" * 64
FBX = b"Kaydara FBX Binary  \x00\x1a\x00" + b"\x00" * 64
BLEND = b"BLENDER-v300RENDH" + b"\x00" * 64
OBJ = b"# exported\nmtllib a.mtl\no Cube\nv 1.0 1.0 -1.0\nv 1.0 -1.0 -1.0\nf 1 2 3\n"
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 64
OGG = b"OggS\x00\x02" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"\x00" * 64
PE = b"MZ\x90\x00" + b"\x00" * 64
ELF = b"\x7fELF\x02\x01\x01" + b"\x00" * 64
MACHO = b"\xcf\xfa\xed\xfe\x07\x00\x00\x01" + b"\x00" * 64
SHEBANG = b"#!/bin/sh\nrm -rf ~\n"


class ArgumentTests(unittest.TestCase):
    def test_one_tool_that_is_not_read_only(self):
        self.assertEqual(DOWNLOAD_NAMES, {NAME})
        self.assertEqual(NAME, "angelo_download")
        tool = DOWNLOAD_TOOLS[0]
        self.assertFalse(tool["annotations"]["readOnlyHint"])
        self.assertEqual(set(tool["inputSchema"]["properties"]), {"url", "kind", "name"})
        self.assertEqual(tool["inputSchema"]["required"], ["url", "kind"])
        self.assertEqual(set(KINDS), {"texture", "model", "hdri", "image", "audio", "other"})

    def test_https_only(self):
        ok = validate_download(NAME, {"url": "https://example.com/a.png", "kind": "image"})
        self.assertEqual(ok["url"], "https://example.com/a.png")
        for url in ("http://example.com/a.png", "ftp://example.com/a", "file:///etc/passwd", "HTTP://example.com/",
                    "javascript:alert(1)", "data:image/png;base64,AAAA", "//example.com/a.png", "example.com/a.png",
                    "https:///a.png", "https://user:pw@example.com/a.png", "https://example.com:0/a",
                    "https://example.com:99999/a", "https://exa mple.com/a", "https://example.com/a\nb",
                    "https://example.com/\x00"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_download(NAME, {"url": url, "kind": "image"})

    def test_a_link_of_at_most_two_kilobytes(self):
        base = "https://example.com/"
        validate_download(NAME, {"url": base + "a" * (MAX_URL_BYTES - len(base)), "kind": "other"})
        with self.assertRaises(ValueError):
            validate_download(NAME, {"url": base + "a" * (MAX_URL_BYTES - len(base) + 1), "kind": "other"})
        with self.assertRaises(ValueError):   # Counted in bytes, so a non-ASCII link can't pass by characters.
            validate_download(NAME, {"url": base + "é" * (MAX_URL_BYTES - len(base) - 10), "kind": "other"})

    def test_kind_and_name(self):
        for kind in KINDS:
            validate_download(NAME, {"url": "https://example.com/x", "kind": kind})
        for bad in ({"url": "https://example.com/x", "kind": "program"},
                    {"url": "https://example.com/x"},
                    {"url": "https://example.com/x", "kind": "image", "extra": 1},
                    {"url": "https://example.com/x", "kind": "image", "name": ""},
                    {"url": "https://example.com/x", "kind": "image", "name": "a" * 101},
                    {"url": "https://example.com/x", "kind": "image", "name": "a\nb"},
                    {"url": 3, "kind": "image"}, "not an object"):
            with self.subTest(arguments=bad), self.assertRaises(ValueError):
                validate_download(NAME, bad)
        self.assertEqual(validate_download(NAME, {"url": "https://example.com/x", "kind": "image",
                                                  "name": "Brick wall"})["name"], "Brick wall")

    def test_the_fragment_is_dropped_and_the_host_is_lowercase(self):
        args = validate_download(NAME, {"url": "https://Example.COM/a.png#top", "kind": "image"})
        self.assertEqual(args["url"], "https://example.com/a.png")


class AddressTests(unittest.TestCase):
    def test_every_class_a_download_never_reaches_is_refused(self):
        refused = {
            "127.0.0.1": "loopback", "127.8.9.10": "loopback", "::1": "loopback",
            "10.1.2.3": "private", "172.16.0.9": "private", "192.168.68.60": "private",
            "169.254.169.254": "link_local", "169.254.0.1": "link_local", "fe80::1": "link_local",
            "100.64.0.1": "shared", "100.127.255.254": "shared",
            "224.0.0.1": "multicast", "239.255.255.250": "multicast", "ff02::1": "multicast",
            "0.0.0.0": "unspecified", "::": "unspecified",
            "240.0.0.1": "reserved", "255.255.255.255": "reserved",
            "fc00::1": "unique_local", "fd12:3456::1": "unique_local",
            "::ffff:127.0.0.1": "loopback", "::ffff:10.0.0.1": "private", "::ffff:169.254.169.254": "link_local",
            "64:ff9b::a00:1": "private", "2002:a00:1::1": "private",   # NAT64 and 6to4 of 10.0.0.1.
            "192.0.0.8": "private", "198.18.0.1": "private",
        }
        for address, kind in refused.items():
            with self.subTest(address=address):
                self.assertEqual(checks.refused_address(address), kind)

    def test_public_addresses_are_allowed(self):
        for address in ("93.184.216.34", "8.8.8.8", "1.1.1.1", "2606:2800:220:1:248:1893:25c8:1946",
                        "2001:4860:4860::8888", "::ffff:93.184.216.34"):
            with self.subTest(address=address):
                self.assertIsNone(checks.refused_address(address))

    def test_something_that_is_not_an_address_is_refused(self):
        for value in ("", "example.com", "1.2.3", "256.1.1.1", None):
            with self.subTest(value=value):
                self.assertEqual(checks.refused_address(value), "not_an_address")


class TypeTests(unittest.TestCase):
    def test_each_kind_takes_its_own_formats(self):
        cases = {
            "image": {PNG: "png", JPEG: "jpg", EXR: "exr", HDR: "hdr"},
            "texture": {PNG: "png", JPEG: "jpg", EXR: "exr"},
            "hdri": {EXR: "exr", HDR: "hdr"},
            "model": {GLB: "glb", FBX: "fbx", BLEND: "blend", OBJ: "obj"},
            "audio": {WAV: "wav", OGG: "ogg"},
            "other": {PNG: "png", PDF: "pdf", GLB: "glb"},
        }
        for kind, found in cases.items():
            for head, fmt in found.items():
                with self.subTest(kind=kind, format=fmt):
                    self.assertEqual(checks.sniff(kind, head, len(head)), fmt)

    def test_a_file_that_is_not_its_kind_is_refused(self):
        for kind, head in (("hdri", JPEG), ("hdri", PNG), ("model", PNG), ("image", GLB), ("audio", PNG),
                           ("texture", WAV), ("image", b"<html><body>Not found</body></html>"),
                           ("model", b"<!doctype html><title>Sign in</title>")):
            with self.subTest(kind=kind, head=head[:12]):
                self.assertIsNone(checks.sniff(kind, head, len(head)))

    def test_programs_and_scripts_are_refused_for_every_kind(self):
        for kind in KINDS:
            for head in (PE, ELF, MACHO, SHEBANG, b"\xca\xfe\xba\xbe\x00\x00\x00\x02" + b"\x00" * 32):
                with self.subTest(kind=kind, head=head[:8]):
                    self.assertIsNone(checks.sniff(kind, head, len(head)))

    def test_other_keeps_unknown_bytes_as_data(self):
        self.assertEqual(checks.sniff("other", b"\x00\x01\x02 arbitrary bytes", 20), "bin")

    def test_a_binary_stl_is_checked_against_its_size(self):
        triangles = 3
        body = b"\x00" * 80 + struct.pack("<I", triangles) + b"\x00" * (50 * triangles)
        self.assertEqual(checks.sniff("model", body, len(body)), "stl")
        self.assertIsNone(checks.sniff("model", body, len(body) + 7))


class NameTests(unittest.TestCase):
    def test_the_checked_type_gives_the_extension(self):
        self.assertEqual(checks.safe_name("png", "Brick wall"), "Brick wall.png")
        self.assertEqual(checks.safe_name("png", "brick.jpg"), "brick.png")
        self.assertEqual(checks.safe_name("glb", "", "chair.glb"), "chair.glb")

    def test_a_hint_is_cleaned_and_never_a_path_or_a_program(self):
        for hint in ("../../evil.sh", "..\\..\\evil.bat", "/etc/passwd", "C:\\Windows\\evil.exe", ".hidden",
                     "evil.exe", "a/b/c.png", "name\x00.png", "\u202etxt.exe", "  ...  "):
            with self.subTest(hint=hint):
                name = checks.safe_name("png", hint)
                self.assertTrue(name.endswith(".png"), name)
                self.assertNotIn("/", name)
                self.assertNotIn("\\", name)
                self.assertNotIn("\x00", name)
                self.assertNotIn("\u202e", name)
                self.assertFalse(name.startswith("."), name)
                self.assertNotIn(".exe", name.lower())
                self.assertNotIn(".sh", name.lower())

    def test_reserved_device_names_and_empty_hints(self):
        for hint in ("CON", "nul.txt", "com1", "LPT9.png", "aux"):
            with self.subTest(hint=hint):
                stem = checks.safe_name("png", hint).rsplit(".", 1)[0]
                self.assertNotIn(stem.upper(), {"CON", "NUL", "COM1", "LPT9", "AUX"})
        self.assertEqual(checks.safe_name("png", "", "", ""), "download.png")
        self.assertLessEqual(len(checks.safe_name("png", "x" * 500)), 104)

    def test_a_server_file_name_is_only_a_hint(self):
        self.assertEqual(checks.disposition_name('attachment; filename="../../evil.sh"'), "../../evil.sh")
        self.assertEqual(checks.disposition_name("attachment; filename*=UTF-8''caf%C3%A9.png"), "café.png")
        self.assertEqual(checks.disposition_name("inline"), "")
        self.assertEqual(checks.safe_name("png", "", "", checks.disposition_name(
            'attachment; filename="../../evil.sh"')), "evil.png")

    def test_the_link_gives_a_name_hint(self):
        self.assertEqual(checks.link_name("https://example.com/textures/brick%20wall.jpg?x=1"), "brick wall.jpg")
        self.assertEqual(checks.link_name("https://example.com/"), "")


if __name__ == "__main__":
    unittest.main()
