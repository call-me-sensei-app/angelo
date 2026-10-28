# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""msi_compound.py reads an MSI's streams as data, and a signed copy must hold exactly the packed MSI's streams.

Compound Files are written here by a small writer: version 3, small streams in the mini stream, large ones in
sectors. They are read back stream by stream (a real MSI's short last sector is covered by ANGELO_TEST_MSI). The signed-copy rules are
checked: the two signature streams are allowed and required, and any other added, missing or changed stream fails.
Where the Windows 1.0.3 MSI is at hand (ANGELO_TEST_MSI), its 50 entries are read too.
"""

import os
import struct
import sys
import tempfile
import unittest

from host_test_policy import environment_skip
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts/angelo"))

import msi_compound  # noqa: E402

SECTOR, MINI, CUTOFF = 512, 64, 4096
FREE, END, FATSECT = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD


def write(streams):
    """A version-3 Compound File holding ``streams`` ({name: bytes}) at its root."""
    names = list(streams)
    small = [n for n in names if len(streams[n]) < CUTOFF]
    mini_stream, mini_fat, mini_start = b"", [], {}
    for name in small:
        data = streams[name]
        count = max(1, -(-len(data) // MINI)) if data else 0
        mini_start[name] = len(mini_fat) if count else END
        for i in range(count):
            mini_fat.append(len(mini_fat) + 1 if i < count - 1 else END)
        mini_stream += data.ljust(count * MINI, b"\0")
    sectors, fat = [], []

    def allocate(blob):
        if not blob:
            return END
        count = -(-len(blob) // SECTOR)
        start = len(sectors)
        for i in range(count):
            sectors.append(blob[i * SECTOR:(i + 1) * SECTOR].ljust(SECTOR, b"\0"))
            fat.append(start + i + 1 if i < count - 1 else END)
        return start

    entries = [("Root Entry", 5, 1 if names else FREE, FREE, 0, 0)]
    for i, name in enumerate(names):
        entries.append((name, 2, FREE, i + 2 if i + 1 < len(names) else FREE, 0, len(streams[name])))
    directory = b""
    for name, kind, child, right, _start, size in entries:
        encoded = (name + "\0").encode("utf-16-le")
        raw = bytearray(128)
        raw[:len(encoded)] = encoded
        struct.pack_into("<HBB", raw, 64, len(encoded), kind, 1)
        struct.pack_into("<III", raw, 68, FREE, right, child)
        struct.pack_into("<IQ", raw, 116, 0, size)
        directory += bytes(raw)
    first_directory = allocate(directory.ljust(-(-len(directory) // SECTOR) * SECTOR, b"\0"))
    first_mini_fat = allocate(struct.pack("<%dI" % len(mini_fat), *mini_fat)) if mini_fat else END
    root_start = allocate(mini_stream)
    starts = {name: mini_start[name] for name in small}
    for name in names:
        if name not in starts:
            starts[name] = allocate(streams[name])
    # Start sectors into the directory, then the sector table after everything else.
    directory = bytearray(b"".join(sectors[first_directory:first_directory + -(-len(entries) * 128 // SECTOR)]))
    struct.pack_into("<I", directory, 116, root_start)
    struct.pack_into("<Q", directory, 120, len(mini_stream))
    for i, name in enumerate(names):
        struct.pack_into("<I", directory, (i + 1) * 128 + 116, starts[name])
    for i in range(len(directory) // SECTOR):
        sectors[first_directory + i] = bytes(directory[i * SECTOR:(i + 1) * SECTOR])
    fat_count = 1
    while (len(sectors) + fat_count) > fat_count * (SECTOR // 4):
        fat_count += 1
    fat_first = len(sectors)
    fat.extend([FATSECT] * fat_count)
    fat.extend([FREE] * (fat_count * (SECTOR // 4) - len(fat)))
    for i in range(fat_count):
        sectors.append(struct.pack("<128I", *fat[i * 128:(i + 1) * 128]))
    header = bytearray(SECTOR)
    header[:8] = bytes.fromhex("d0cf11e0a1b11ae1")
    struct.pack_into("<HHHHH", header, 0x18, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<IIIIIIIII", header, 0x28, 0, fat_count, first_directory, 0, CUTOFF, first_mini_fat,
                     len(mini_fat) and -(-len(mini_fat) * 4 // SECTOR), END, 0)
    difat = [fat_first + i for i in range(fat_count)] + [FREE] * (109 - fat_count)
    struct.pack_into("<109I", header, 0x4C, *difat)
    return bytes(header) + b"".join(sectors)


TABLES = {"䡀㿿䏤䇬䗤": b"string pool " * 30, "\x05SummaryInformation": b"\xfe\xff" + b"s" * 500,
          "䈰䌧㡤䆾䅤": b"MSCF" + bytes(range(256)) * 40, "䡀䆊䌷䑲": b"t" * 5000}
SIGNATURES = {"\x05DigitalSignature": b"pkcs7" * 300, "\x05MsiDigitalSignatureEx": b"ex" * 20}


def streams(data):
    """{name: bytes} of the root's streams, as the writer made them."""
    return {path[0]: value for path, value in msi_compound.CompoundFile(data).entries().items()
            if len(path) == 1 and not isinstance(value, tuple)}


class ReaderTests(unittest.TestCase):
    def test_every_stream_reads_back_mini_and_regular(self):
        self.assertEqual(streams(write(TABLES)), TABLES)

    def test_anything_windows_could_read_otherwise_is_refused(self):
        """Break-it review 2, N5: one file, one meaning."""
        good = write(TABLES)
        self.assertEqual(streams(good + b"\0" * 1024), TABLES, "trailing zeros are a free sector")
        cases = {"appended data": good + b"jar file" * 200,
                 "a version and sector size that disagree": good[:0x1A] + struct.pack("<H", 4) + good[0x1C:],
                 "a slash in a name": write({"a/b": b"x" * 10, **TABLES}),
                 "a NUL in a name": write({"a\0b": b"x" * 10, **TABLES})}
        for what, data in cases.items():
            with self.subTest(what):
                with self.assertRaises(msi_compound.CompoundFileError):
                    msi_compound.CompoundFile(data).entries()

    def test_a_mini_sector_outside_the_mini_stream_is_refused(self):
        data = bytearray(write({"small": b"s" * 100, **TABLES}))
        reader = msi_compound.CompoundFile(bytes(data))
        entry = next(e for e in reader.directory if e and e["name"] == "small")
        index = reader.directory.index(entry)
        directory_offset = (reader.first_directory + 1) * reader.sector + index * 128
        struct.pack_into("<I", data, directory_offset + 116, reader.mini_count + 3)
        with self.assertRaises(msi_compound.CompoundFileError):
            msi_compound.CompoundFile(bytes(data)).entries()

    def test_what_is_not_a_compound_file_is_refused(self):
        for data in (b"", b"MZ" + b"\0" * 600, write(TABLES)[:700]):
            with self.assertRaises(msi_compound.CompoundFileError):
                msi_compound.CompoundFile(data).entries()

    @unittest.skipUnless(os.environ.get("ANGELO_TEST_MSI"), environment_skip("set ANGELO_TEST_MSI to a built Angelo MSI"))
    def test_a_real_msi(self):
        found = msi_compound.digests(os.environ["ANGELO_TEST_MSI"])
        self.assertIn(("\x05SummaryInformation",), found)
        self.assertGreater(len(found), 20)


class SameTests(unittest.TestCase):
    def problems(self, packed, signed):
        with tempfile.TemporaryDirectory() as folder:
            a, b = Path(folder, "packed.msi"), Path(folder, "signed.msi")
            a.write_bytes(write(packed))
            b.write_bytes(write(signed))
            return msi_compound.same_problems(a, b)

    def test_only_the_signature_streams_may_be_added(self):
        self.assertEqual(self.problems(TABLES, {**TABLES, **SIGNATURES}), [])
        self.assertEqual(self.problems(TABLES, {**TABLES, "\x05DigitalSignature": b"sig" * 100}), [])

    def test_every_other_difference_fails(self):
        changed = dict(TABLES, **SIGNATURES)
        changed["䈰䌧㡤䆾䅤"] = b"MSCF" + b"\1" * 1000
        self.assertIn("differs", " ".join(self.problems(TABLES, changed)))
        missing = {k: v for k, v in {**TABLES, **SIGNATURES}.items() if k != "\x05SummaryInformation"}
        self.assertIn("missing from the signed MSI", " ".join(self.problems(TABLES, missing)))
        extra = {**TABLES, **SIGNATURES, "䡀㿿䄀": b"custom action" * 50}
        self.assertIn("only in the signed MSI", " ".join(self.problems(TABLES, extra)))
        self.assertEqual(self.problems(TABLES, TABLES), ["the signed MSI carries no \\x05DigitalSignature stream"])
        self.assertEqual(self.problems({**TABLES, **SIGNATURES}, {**TABLES, **SIGNATURES}), ["the packed MSI is already signed"])


if __name__ == "__main__":
    unittest.main()
