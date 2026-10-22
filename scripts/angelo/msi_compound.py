# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An MSI's streams, read as data: the signed copy of an MSI must hold exactly the packed MSI's streams and storages.

An MSI is a Compound File (Microsoft's [MS-CFB]). Every table, the summary information, every embedded cabinet
and every Binary-table entry is a stream in it. Signing adds two streams at the root
(``\\x05DigitalSignature`` and ``\\x05MsiDigitalSignatureEx``) and rewrites the file around them, so the bytes
always change. Comparing every other stream and storage, by path, content and class id, shows that nothing else
changed.

The reader is strict, so that no file can mean one thing here and another to Windows (break-it review 2, N5):
- the header's version must match its sector size;
- the sector table is read by position;
- every sector of the file is accounted for exactly once, as a table, a stream's or free, and any sector after
  the last one in use holds only zeros;
- mini sectors lie inside the root's declared mini stream, and none is shared;
- names follow [MS-CFB]: no '/', '\\', ':' or '!', and no NUL;
- paths are tuples of names, so a stream named "a/b" can never stand for a storage.
Anything else is a CompoundFileError, and so is any malformed field, never a crash.

Nothing in an MSI is executed here: no Windows Installer call and no administrative install, which would run its
AdminExecuteSequence custom actions before the copy is proven to be ours. Standard library only, so it runs on any
host and with the build's bundled Python. ``release_checks.py msi-same`` is its command.
"""

import hashlib
import struct

SIGNATURE_STREAMS = frozenset((("\x05DigitalSignature",), ("\x05MsiDigitalSignatureEx",)))
_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
_FREE, _END_OF_CHAIN, _FAT_SECTOR, _DIFAT_SECTOR = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFC
_MAX_SECTOR = 0xFFFFFFFA
_STORAGE, _STREAM, _ROOT = 1, 2, 5
_BAD_NAME = set("/\\:!\0")


class CompoundFileError(ValueError):
    pass


class CompoundFile:
    """A read-only Compound File: ``entries()`` gives every stream's path and bytes, and every storage's path."""

    def __init__(self, data):
        try:
            self._read(data)
        except CompoundFileError:
            raise
        except (struct.error, IndexError, UnicodeDecodeError, ValueError, OverflowError, MemoryError) as error:
            raise CompoundFileError("a malformed Compound File (%s)" % type(error).__name__) from None

    def _read(self, data):
        if len(data) < 512 or data[:8] != _MAGIC:
            raise CompoundFileError("not a Compound File (no MSI header)")
        self.data = data
        major, byte_order, sector_shift, mini_shift = struct.unpack_from("<HHHH", data, 0x1A)
        if byte_order != 0xFFFE or mini_shift != 6 or (major, sector_shift) not in ((3, 9), (4, 12)):
            raise CompoundFileError("an unsupported header (version %d, sector shift %d, mini shift %d)"
                                    % (major, sector_shift, mini_shift))
        self.sector = 1 << sector_shift
        self.mini = 1 << mini_shift
        if len(data) <= self.sector:
            raise CompoundFileError("no sectors after the header")
        # Windows may leave the last sector short; it reads as zero-padded.
        self.count = -(-(len(data) - self.sector) // self.sector)
        fat_count, self.first_directory = struct.unpack_from("<II", data, 0x2C)
        cutoff, first_mini_fat, mini_fat_count, first_difat, difat_count = struct.unpack_from("<IIIII", data, 0x38)
        if cutoff != 4096:
            raise CompoundFileError("unsupported mini stream cutoff %d" % cutoff)
        if fat_count > self.count or difat_count > self.count or mini_fat_count > self.count:
            raise CompoundFileError("table counts larger than the file")
        self.owner = {}   # sector -> what holds it: every sector is accounted for exactly once.
        difat = list(struct.unpack_from("<109I", data, 0x4C))
        sector = first_difat
        for _ in range(difat_count):
            self._claim(sector, "the sector table's index")
            values = struct.unpack_from("<%dI" % (self.sector // 4), self._sector(sector))
            difat.extend(values[:-1])
            sector = values[-1]
        if difat_count and sector not in (_END_OF_CHAIN, _FREE):
            raise CompoundFileError("the sector table's index runs past its count")
        # By position, as Windows reads it: the first fat_count entries are the table's sectors, with no hole.
        fat_sectors = difat[:fat_count]
        if any(s > _MAX_SECTOR for s in fat_sectors) or any(s != _FREE for s in difat[fat_count:]):
            raise CompoundFileError("the sector table's index has a hole or extra entries")
        self.fat = []
        for s in fat_sectors:
            self._claim(s, "the sector table")
            self.fat.extend(struct.unpack_from("<%dI" % (self.sector // 4), self._sector(s)))
        if len(self.fat) < self.count:
            raise CompoundFileError("the sector table does not cover the file")
        for s, value in enumerate(self.fat):
            if s >= self.count and value != _FREE:
                raise CompoundFileError("the sector table names sectors beyond the file")
        for s in fat_sectors:
            if self.fat[s] != _FAT_SECTOR:
                raise CompoundFileError("a sector-table sector is not marked as one")
        self.mini_fat = []
        if mini_fat_count:
            raw = self._chain(first_mini_fat, "the mini sector table", limit=mini_fat_count)
            self.mini_fat = list(struct.unpack_from("<%dI" % (len(raw) // 4), raw))
        directory = self._chain(self.first_directory, "the directory")
        self.directory = [self._entry(directory, i) for i in range(len(directory) // 128)]
        root = self.directory[0] if self.directory else None
        if root is None or root["type"] != _ROOT:
            raise CompoundFileError("the first directory entry is not the root")
        self.mini_owner = {}
        self.mini_stream = self._chain(root["start"], "the mini stream")[:root["size"]] if root["size"] else b""
        if len(self.mini_stream) < root["size"]:
            raise CompoundFileError("the mini stream is shorter than its size")
        self.mini_count = root["size"] // self.mini

    def _sector(self, index):
        offset = (index + 1) * self.sector
        if index > _MAX_SECTOR or index >= self.count:
            raise CompoundFileError("a sector lies beyond the end of the file")
        return self.data[offset:offset + self.sector].ljust(self.sector, b"\0")

    def _claim(self, index, what):
        if index > _MAX_SECTOR or index >= self.count:
            raise CompoundFileError("%s names a sector beyond the file" % what)
        if index in self.owner:
            raise CompoundFileError("%s shares a sector with %s" % (what, self.owner[index]))
        self.owner[index] = what

    def _chain(self, start, what, limit=None):
        parts, sector = [], start
        while sector != _END_OF_CHAIN:
            self._claim(sector, what)
            parts.append(self._sector(sector))
            sector = self.fat[sector]
            if limit is not None and len(parts) > limit:
                raise CompoundFileError("%s's chain is longer than its size" % what)
        return b"".join(parts)

    def _mini_chain(self, start, what, size):
        parts, sector, limit = [], start, -(-size // self.mini)
        while sector != _END_OF_CHAIN:
            if sector >= self.mini_count or sector >= len(self.mini_fat):
                raise CompoundFileError("%s names a mini sector outside the mini stream" % what)
            if sector in self.mini_owner:
                raise CompoundFileError("%s shares a mini sector with %s" % (what, self.mini_owner[sector]))
            self.mini_owner[sector] = what
            parts.append(self.mini_stream[sector * self.mini:(sector + 1) * self.mini])
            sector = self.mini_fat[sector]
            if len(parts) > limit:
                raise CompoundFileError("%s's chain is longer than its size" % what)
        return b"".join(parts)

    def _entry(self, directory, index):
        raw = directory[index * 128:(index + 1) * 128]
        name_length, kind = struct.unpack_from("<HB", raw, 64)
        if kind == 0:
            return None
        if kind not in (_STORAGE, _STREAM, _ROOT) or name_length < 2 or name_length > 64 or name_length % 2:
            raise CompoundFileError("a directory entry with a bad type or name length")
        name = raw[:name_length - 2].decode("utf-16-le")
        if raw[name_length - 2:name_length] != b"\0\0" or (kind != _ROOT and (not name or set(name) & _BAD_NAME)):
            raise CompoundFileError("a directory entry with a name [MS-CFB] does not allow: %r" % name)
        left, right, child = struct.unpack_from("<III", raw, 68)
        start, size = struct.unpack_from("<IQ", raw, 116)
        if self.sector == 512:
            size &= 0xFFFFFFFF   # Version 3 files: the high half may hold garbage ([MS-CFB] 2.6.3).
        return {"name": name, "type": kind, "left": left, "right": right, "child": child, "start": start,
                "size": size, "clsid": bytes(raw[80:96])}

    def _stream(self, entry, path):
        size = entry["size"]
        if size == 0:
            return b""
        what = "stream %r" % "/".join(path)
        if size < 4096:
            data = self._mini_chain(entry["start"], what, size)
        else:
            data = self._chain(entry["start"], what, limit=-(-size // self.sector))
        if len(data) < size:
            raise CompoundFileError("%s is shorter than its size" % what)
        return data[:size]

    def entries(self):
        """{path tuple: bytes for a stream, ("storage", class id) for a storage}, with the root's class id under ()."""
        found = {(): ("storage", self.directory[0]["clsid"])}
        pending, visited = [(self.directory[0]["child"], ())], set()
        while pending:
            index, parent = pending.pop()
            if index == _FREE:
                continue
            if index in visited or index >= len(self.directory) or self.directory[index] is None:
                raise CompoundFileError("a broken directory tree")
            visited.add(index)
            entry = self.directory[index]
            if entry["type"] == _ROOT:
                raise CompoundFileError("a second root entry")
            path = parent + (entry["name"],)
            if path in found:
                raise CompoundFileError("two entries named %r" % "/".join(path))
            if entry["type"] == _STREAM:
                found[path] = self._stream(entry, path)
            else:
                found[path] = ("storage", entry["clsid"])
                pending.append((entry["child"], path))
            pending.extend(((entry["left"], parent), (entry["right"], parent)))
        # Every directory entry in use is reached, and every sector after the last one in use is free: nothing is
        # hidden after the data or outside the tree.
        if any(e is not None and i not in visited and i != 0 for i, e in enumerate(self.directory)):
            raise CompoundFileError("a directory entry outside the tree")
        last = max(self.owner) if self.owner else -1
        for s in range(self.count):
            if s not in self.owner and self.fat[s] != _FREE:
                raise CompoundFileError("sector %d is in use but belongs to nothing" % s)
        # Free sectors after the last one in use may only be zeros: appended data would sit outside what the signature
        # covers, readable by some other tool (the CVE-2020-1464 class).
        for s in range(last + 1, self.count):
            if self._sector(s).strip(b"\0"):
                raise CompoundFileError("data follows the last sector in use")
        return found


def digests(path):
    """{entry path tuple: SHA-256 of a stream's bytes, or "storage:<class id>"} for the MSI at ``path``."""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as error:
        raise CompoundFileError("cannot read %s (%s)" % (path, error)) from None
    entries = CompoundFile(data).entries()
    return {name: ("storage:" + value[1].hex() if isinstance(value, tuple) else hashlib.sha256(value).hexdigest())
            for name, value in entries.items()}


def _shown(path):
    return "/".join(path).encode("unicode_escape").decode("ascii") or "(root)"


def same_problems(packed, signed):
    """[] when the signed MSI holds exactly the packed MSI's entries (the signature streams aside); else differences."""
    before, after = digests(packed), digests(signed)
    if any(name in before for name in SIGNATURE_STREAMS):
        return ["the packed MSI is already signed"]
    if ("\x05DigitalSignature",) not in after:
        return ["the signed MSI carries no \\x05DigitalSignature stream"]
    problems = []
    for name in sorted(before):
        if name not in after:
            problems.append("%s: missing from the signed MSI" % _shown(name))
        elif after[name] != before[name]:
            problems.append("%s: differs" % _shown(name))
    problems += ["%s: only in the signed MSI" % _shown(name) for name in sorted(after)
                 if name not in before and name not in SIGNATURE_STREAMS]
    return problems
