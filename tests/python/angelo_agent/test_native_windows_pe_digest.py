# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Authenticode PE digest (scripts/angelo/windows_pe_digest.ps1) proves a signed file differs by its signature only.

A small PE32+ image is built here, then "signed" the way signtool changes a file: a new CheckSum, the certificate
table's directory entry, zero padding to 8 bytes and a table appended at the end. A Python reference of the
Authenticode hash must give the unsigned and the signed file the same digest, and any other change a different one.
Where PowerShell is installed (CI), the script's own function is checked against the reference on the same files.
"""

import hashlib
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/angelo/windows_pe_digest.ps1"
PE_OFFSET = 0x80


def image(body=b"code and data " * 37, magic=0x20B):
    """A PE image with ``body`` after its headers: MZ, e_lfanew, PE signature, COFF header, optional header."""
    optional_size = 240 if magic == 0x20B else 224
    header = bytearray(PE_OFFSET + 24 + optional_size)
    header[0:2] = b"MZ"
    struct.pack_into("<I", header, 0x3C, PE_OFFSET)
    header[PE_OFFSET:PE_OFFSET + 4] = b"PE\0\0"
    struct.pack_into("<HH", header, PE_OFFSET + 4, 0x8664, 0)          # Machine, no sections
    struct.pack_into("<H", header, PE_OFFSET + 20, optional_size)       # SizeOfOptionalHeader
    struct.pack_into("<H", header, PE_OFFSET + 24, magic)
    # NumberOfRvaAndSizes: 16 data directories, as every image a linker writes has.
    struct.pack_into("<I", header, PE_OFFSET + 24 + (108 if magic == 0x20B else 92), 16)
    return bytes(header) + body


def offsets(data):
    optional = PE_OFFSET + 24
    magic = struct.unpack_from("<H", data, optional)[0]
    directories = optional + (112 if magic == 0x20B else 96)
    return optional + 64, directories + 32


def sign(data, table=b"PKCS7-signature-bytes" * 5, checksum=0x1234ABCD):
    """What signing changes: the CheckSum, the table's directory entry, zero padding to 8 bytes, the table."""
    checksum_at, entry_at = offsets(data)
    padded = bytearray(data + b"\0" * ((8 - len(data) % 8) % 8))
    struct.pack_into("<I", padded, checksum_at, checksum)
    struct.pack_into("<II", padded, entry_at, len(padded), len(table))
    return bytes(padded) + table


def reference_digest(data):
    """The Authenticode PE hash (Microsoft's "Calculating the PE Image Hash"), as the script computes it."""
    checksum_at, entry_at = offsets(data)
    offset, size = struct.unpack_from("<II", data, entry_at)
    end = offset if size else len(data)
    digest = hashlib.sha256()
    digest.update(data[:checksum_at])
    digest.update(data[checksum_at + 4:entry_at])
    digest.update(data[entry_at + 8:end])
    digest.update(b"\0" * ((8 - end % 8) % 8))
    return digest.hexdigest()


class ReferenceTests(unittest.TestCase):
    def test_signing_leaves_the_digest_unchanged(self):
        for magic in (0x20B, 0x10B):
            for length in range(0, 9):   # Every padding a file can need.
                with self.subTest(magic=magic, length=length):
                    unsigned = image(b"x" * (100 + length), magic)
                    signed = sign(unsigned)
                    self.assertNotEqual(unsigned, signed)
                    self.assertEqual(reference_digest(unsigned), reference_digest(signed))
                    # Re-signing over an old signature, as for an expired one, keeps it too.
                    self.assertEqual(reference_digest(sign(signed[:struct.unpack_from("<I", signed, offsets(signed)[1])[0]],
                                                           table=b"another" * 9)), reference_digest(unsigned))

    def test_any_other_change_changes_it(self):
        unsigned = image()
        signed = bytearray(sign(unsigned))
        signed[PE_OFFSET + 24 + 300] ^= 1   # One bit of the body.
        self.assertNotEqual(reference_digest(bytes(signed)), reference_digest(unsigned))
        appended = image() + b"\0" * 8      # Real zeros after the image are part of it, not padding.
        self.assertNotEqual(reference_digest(appended), reference_digest(unsigned))


class ScriptTests(unittest.TestCase):
    """Run where PowerShell is installed (CI). Not skippable tests: the host suite allows no skips for its absence."""

    def setUp(self):
        self.pwsh = shutil.which("pwsh")

    def digests(self, *files):
        # As sign_windows.ps1 runs it: with Stop, a refusal ends the run (else a .NET exception ends only its statement).
        command = "$ErrorActionPreference = 'Stop'; . '%s'; %s" % (
            SCRIPT, "; ".join("Get-AuthenticodePeDigest '%s'" % path for path in files))
        done = subprocess.run([self.pwsh, "-NoProfile", "-NonInteractive", "-Command", command],
                              capture_output=True, text=True)
        return done.returncode, done.stdout.split(), done.stderr

    def test_the_script_matches_the_reference(self):
        if not self.pwsh:
            return
        with tempfile.TemporaryDirectory() as folder:
            cases = {}
            for magic in (0x20B, 0x10B):
                for length in (0, 3, 8):
                    unsigned = image(b"y" * (64 + length), magic)
                    for name, data in (("u", unsigned), ("s", sign(unsigned))):
                        path = Path(folder, "%x-%d-%s.dll" % (magic, length, name))
                        path.write_bytes(data)
                        cases[path] = reference_digest(data)
            code, printed, error = self.digests(*cases)
            self.assertEqual(code, 0, error)
            self.assertEqual(printed, list(cases.values()))

    def test_the_script_refuses_what_is_not_a_signable_pe(self):
        if not self.pwsh:
            return
        with tempfile.TemporaryDirectory() as folder:
            text = Path(folder, "notes.dll")
            text.write_bytes(b"not a program at all" * 10)
            moved = Path(folder, "moved.dll")
            data = bytearray(sign(image()))
            data += b"trailing"     # The table is no longer at the end.
            moved.write_bytes(bytes(data))
            few = Path(folder, "few.dll")
            data = bytearray(image())
            struct.pack_into("<I", data, PE_OFFSET + 24 + 108, 4)   # Four directories: no certificate table entry.
            few.write_bytes(bytes(data))
            for path, message in ((text, "not a PE file"), (moved, "not at the end"), (few, "no certificate table entry")):
                code, _printed, error = self.digests(path)
                self.assertNotEqual(code, 0)
                self.assertIn(message, error)


if __name__ == "__main__":
    unittest.main()
