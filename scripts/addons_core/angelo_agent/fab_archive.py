# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unpack a Fab zip into a fresh private folder, refusing every archive that could write outside it or blow up.

A downloaded archive is untrusted. Before a byte is written:

* **Names.** Every entry name is checked as each supported file system would read it: no absolute path, drive or
  UNC prefix, ``..``, backslash, colon (Windows streams), control character, Windows device name (``CON``,
  ``LPT1``...) or trailing dot/space, at most ``MAX_NAME`` characters a part and ``MAX_PATH`` in all. Two names
  that one case-insensitive, Unicode-normalising file system would merge are refused (macOS and Windows).
* **Kinds.** Symbolic links, devices and encrypted entries are refused; directories are made by Angelo. Only the
  entries the format table keeps (``fab_formats.keep_member``: the model, its side files and textures) are
  written; the rest (read-me files, other formats, nested archives) are counted and left.
* **Sizes.** At most ``MAX_ENTRIES`` entries, ``MAX_TOTAL`` declared bytes in all and ``MAX_RATIO`` expansion per
  entry (zip bombs, including entries that share compressed data). While writing, the reader stops at each
  entry's declared size and the CRC must match; a stop, a short read or a mismatch removes the folder.

Writing uses exclusive creation without following links, inside a folder only Angelo has written to.
Standard library only; no bpy. Blocking worker code.
"""

import lzma
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import unicodedata
import zipfile
import zlib

from . import fab_formats

MiB = 1024 * 1024
MAX_ENTRIES = 4096
MAX_TOTAL = 1024 * MiB         # Declared uncompressed bytes of the kept entries.
MAX_RATIO = 200                # Uncompressed / compressed, per entry with more than RATIO_FLOOR bytes.
RATIO_FLOOR = 1 * MiB
MAX_NAME, MAX_PATH = 200, 400
MAX_JSON = 64 * MiB             # A glTF description read for the side files it names (gltf_checks' own bound).
CHUNK = 1024 * 1024
_DEVICES = frozenset({'con', 'prn', 'aux', 'nul', 'conin$', 'conout$',
                      *('com%d' % n for n in range(10)), *('lpt%d' % n for n in range(10))})
# What a damaged member raises while it is read (a bad CRC is BadZipFile). Cancellation and disk errors propagate.
_DAMAGED = (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, zlib.error, lzma.LZMAError, NotImplementedError)
_METHODS = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA})


class ArchiveRefused(ValueError):
    """A fixed sentence for people; never an entry name or archive text."""


def entry_path(name):
    """The checked relative POSIX path of a zip entry name, or raises ``ArchiveRefused``."""
    if type(name) is not str or not name or len(name) > MAX_PATH:
        raise ArchiveRefused('This download names a file Angelo will not write (too long or empty).')
    if '\\' in name or ':' in name or name.startswith('/') or '\x00' in name:
        raise ArchiveRefused('This download names a file outside its own folder; nothing was unpacked.')
    parts = name.rstrip('/').split('/')
    for part in parts:
        if part in ('', '.', '..'):
            raise ArchiveRefused('This download names a file outside its own folder; nothing was unpacked.')
        if (len(part) > MAX_NAME or any(unicodedata.category(c) in ('Cc', 'Cf', 'Cs') for c in part)
                or part != part.rstrip(' .') or part.split('.', 1)[0].lower() in _DEVICES):
            raise ArchiveRefused('This download names a file Angelo will not write on every system; '
                                 'nothing was unpacked.')
    return PurePosixPath(*parts)


def _fold(path):
    return unicodedata.normalize('NFC', str(path)).casefold()


def _is_link_or_device(info):
    """A Unix entry whose type is set and is neither a file nor a folder (link, device, socket, pipe).

    Only the type bits count: many tools (Python's zipfile among them) write permission bits with no type, and such
    an entry is a regular file (security review 2026-11-11, F2)."""
    kind = stat.S_IFMT(info.external_attr >> 16)
    return info.create_system == 3 and kind not in (0, stat.S_IFREG, stat.S_IFDIR)


def plan(archive, fmt):
    """The entries to write as ``[(ZipInfo, PurePosixPath)]`` and the number left packed; raises on any refusal."""
    entries = archive.infolist()
    if not entries or len(entries) > MAX_ENTRIES:
        raise ArchiveRefused('This download is empty or holds more than %d files.' % MAX_ENTRIES)
    seen, folders, files, total = set(), set(), [], 0
    for info in entries:
        path = entry_path(info.filename)
        folded = _fold(path)
        if folded in seen:
            raise ArchiveRefused('This download holds two files with the same name; nothing was unpacked.')
        seen.add(folded)
        if info.flag_bits & 0x1:
            raise ArchiveRefused('This download is encrypted; Angelo cannot read it.')
        if _is_link_or_device(info):
            raise ArchiveRefused('This download holds a link or a device file; nothing was unpacked.')
        if info.is_dir():
            folders.add(folded)
            continue
        if info.compress_type not in _METHODS:
            raise ArchiveRefused('This download uses a compression method Angelo does not read.')
        files.append((info, path))
    names = {_fold(path) for _info, path in files}
    for _info, path in files:
        # A file whose name another entry uses as a folder (a/b and a/b/c) cannot both exist.
        if any(_fold(parent) in names for parent in path.parents if str(parent) != '.'):
            raise ArchiveRefused('This download uses one name for both a file and a folder; nothing was unpacked.')
    kept, others = [], []
    for info, path in files:
        total += info.file_size
        if (info.file_size > RATIO_FLOOR and
                (info.compress_size <= 0 or info.file_size > MAX_RATIO * info.compress_size)):
            raise ArchiveRefused('This download expands far beyond its size (a zip bomb); nothing was unpacked.')
        if total > MAX_TOTAL:
            raise ArchiveRefused('This download unpacks to more than %d MB; choose a lower quality or a smaller part '
                                 'of the listing.' % (MAX_TOTAL // MiB))
        with archive.open(info) as member:
            head = member.read(65536)
        kind = fab_formats.kind_of_content(head, info.file_size)
        if kind in ('png', 'jpeg', 'webp'):
            from .team_library_files import image_size, MAX_SIDE, MAX_PIXELS
            size = image_size(head)
            if size and not (0 < size[0] <= MAX_SIDE and 0 < size[1] <= MAX_SIDE and size[0] * size[1] <= MAX_PIXELS):
                raise ArchiveRefused('A texture in this download is larger than %d pixels a side; choose a lower '
                                     'quality.' % MAX_SIDE)
        if fab_formats.keep_member(fmt, kind):
            kept.append((info, path, kind))
        else:
            others.append((info, path))
    if fmt.named_sidecars:
        named = _named_by_models(archive, kept)
        for info, path in others:
            if _fold(path) in named:
                kept.append((info, path, 'sidecar'))
        others = [(info, path) for info, path in others if _fold(path) not in named]
    return kept, len(others)


def _named_by_models(archive, kept):
    """Folded paths a glTF model's buffer and image URIs name (percent-decoded, relative to the model; ``data:`` and
    escaping URIs name nothing here, and the converter's own check refuses an escaping one)."""
    import json
    import posixpath
    from urllib.parse import unquote
    named = set()
    for info, path, kind in kept:
        if kind != 'gltf' or info.file_size > MAX_JSON:
            continue
        with archive.open(info) as member:
            try:
                document = json.loads(member.read(MAX_JSON + 1))
            except (ValueError, UnicodeError, RecursionError):
                continue
        if not isinstance(document, dict):
            continue
        for field in ('buffers', 'images'):
            records = document.get(field)
            for record in records if isinstance(records, list) else ():
                uri = record.get('uri') if isinstance(record, dict) else None
                if not isinstance(uri, str) or uri.startswith('data:') or '\x00' in uri:
                    continue
                target = posixpath.normpath(posixpath.join(path.parent.as_posix(), unquote(uri.replace('\\', '/'))))
                if not target.startswith('../') and target != '..':
                    named.add(_fold(PurePosixPath(target)))
    return named


def extract(source, folder, fmt, check=lambda: None):
    """Unpack the kept entries of zip ``source`` into new folder ``folder`` (which must not exist).

    Returns ``{'members': [(relative, kind)], 'left': n, 'bytes': n}``. Any failure removes ``folder``.
    """
    folder = Path(folder)
    folder.mkdir(mode=0o700)
    try:
        try:
            archive = zipfile.ZipFile(source)
        except (zipfile.BadZipFile, OSError, ValueError):
            raise ArchiveRefused('This download is not a readable zip archive.') from None
        with archive:
            try:
                kept, left = plan(archive, fmt)
                written = 0
                members = []
                for info, path, kind in kept:
                    check()
                    written += _write(archive, info, folder, path, check)
                    members.append((path.as_posix(), kind))
            except _DAMAGED:
                raise ArchiveRefused('This download is damaged (a file failed its check); nothing was unpacked.') \
                    from None
        return {'members': members, 'left': left, 'bytes': written}
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def _write(archive, info, folder, path, check):
    target = folder
    for part in path.parts[:-1]:
        target = target / part
        try:
            target.mkdir(mode=0o700)
        except FileExistsError:
            pass
        if not stat.S_ISDIR(target.lstat().st_mode):
            raise ArchiveRefused('This download names a file outside its own folder; nothing was unpacked.')
    target = target / path.parts[-1]
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    done = 0
    with archive.open(info) as member, os.fdopen(os.open(target, flags, 0o600), 'wb') as handle:
        while data := member.read(CHUNK):
            check()
            done += len(data)
            if done > info.file_size:
                raise ArchiveRefused('This download is damaged (a file failed its check); nothing was unpacked.')
            handle.write(data)
    if done != info.file_size:
        raise ArchiveRefused('This download is damaged (a file failed its check); nothing was unpacked.')
    return done
