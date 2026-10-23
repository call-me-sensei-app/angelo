# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which .blend a headless session may open, how it notices a change on disk, and the new file its edits go to.

The source file is only ever read. Edits are saved to a new file next to it (so relative image and library paths
stay valid), named ``<stem>.angelo-<YYYYMMDD-HHMMSS>.blend`` and claimed with ``O_CREAT|O_EXCL`` so an existing file
is never replaced; ``-2``, ``-3``... follow on a collision. Pure standard library and no ``bpy``: the launcher and
the child both use it (doc/angelo/external_mcp.rst, "Headless").
"""

import hashlib
import os
import secrets
import time
from pathlib import Path

SUFFIX = ".blend"
OUTPUT_TAG = ".angelo-"
MAX_COLLISIONS = 999
# A .blend starts with "BLENDER"; Blender 3.0+ may compress it with Zstandard, older releases with gzip.
MAGIC = (b"BLENDER", b"\x28\xb5\x2f\xfd", b"\x1f\x8b")
MAX_PATH_CHARS = 4096


class FileProblem(ValueError):
    """A path a headless session cannot use; ``code`` is the MCP-facing code (``invalid``, ``output_unwritable``)."""

    def __init__(self, message, code="invalid"):
        super().__init__(message)
        self.code = code


def check_source(value):
    """The real path of a readable .blend the app named absolutely, or ``FileProblem``."""
    if type(value) is not str or not value or len(value) > MAX_PATH_CHARS or "\x00" in value:
        raise FileProblem("Give the .blend file's absolute path")
    path = Path(os.path.expanduser(value))
    if not path.is_absolute():
        raise FileProblem("Give the .blend file's absolute path (this one is relative)")
    if path.suffix.lower() != SUFFIX:
        raise FileProblem("Only .blend files can be opened")
    try:
        real = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise FileProblem("No such file: " + str(path)) from None
    if not real.is_file():
        raise FileProblem("Not a file: " + str(path))
    try:
        with open(real, "rb") as handle:
            head = handle.read(8)
    except OSError as error:
        raise FileProblem("The file cannot be read (%s)" % error.strerror) from None
    if not head.startswith(MAGIC):
        raise FileProblem("This is not a .blend file Blender can read")
    return real


def check_resume(source, value):
    """A session's own earlier output to continue from: a .blend next to ``source`` named as its outputs are."""
    path, source = check_source(value), Path(source).resolve()
    if path.parent != source.parent or not path.name.startswith(source.stem + OUTPUT_TAG):
        raise FileProblem("Only this session's own output file can be continued")
    return path


def identity(path):
    """What a change on disk changes: device, inode, size and modification time; None when it is gone."""
    try:
        info = os.stat(path)
    except OSError:
        return None
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def folder_writable(source):
    folder = Path(source).parent
    return os.access(folder, os.W_OK | os.X_OK)


def output_candidates(source, now=None):
    """The names an output may take, in order."""
    source = Path(source)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(time.time() if now is None else now))
    base = source.stem + OUTPUT_TAG + stamp
    yield source.with_name(base + SUFFIX)
    for number in range(2, MAX_COLLISIONS + 1):
        yield source.with_name("%s-%d%s" % (base, number, SUFFIX))


def claim_output(source, now=None):
    """Create a new, empty output file next to ``source`` and return its path; never opens an existing one.

    Blender then saves over this placeholder (its own temporary file and rename), so between the claim and the save
    only a file this session created can be replaced.
    """
    if not folder_writable(source):
        raise FileProblem("Angelo cannot write a new file in this folder, so edits cannot be saved",
                          code="output_unwritable")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    for candidate in output_candidates(source, now):
        try:
            descriptor = os.open(candidate, flags, 0o666)
        except FileExistsError:
            continue
        except OSError as error:
            raise FileProblem("Angelo cannot create a new file here (%s)" % error.strerror,
                              code="output_unwritable") from None
        os.close(descriptor)
        return candidate
    raise FileProblem("Too many saved copies of this file already exist here", code="output_unwritable")


def path_digest(path):
    """A stable digest of a file's real path: how the launcher asks whether Angelo's window has the same file open."""
    try:
        real = str(Path(path).resolve(strict=True))
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    return hashlib.sha256(("angelo-open-file/1\0" + real).encode("utf-8", "surrogatepass")).hexdigest()


def place(staged, target):
    """Put the finished file ``staged`` (in a folder only this user can reach) at ``target`` in the shared folder.

    Nothing in the shared folder is ever opened by a name that could already exist: the bytes go into a new
    hidden file this call creates with ``O_CREAT|O_EXCL|O_NOFOLLOW`` and writes through its descriptor, and that
    file is then renamed onto ``target``. A rename replaces a directory entry and never writes through a link, so
    whatever someone puts at either name, no other file is written.
    """
    target = Path(target)
    temporary = target.with_name(".%s.%s.tmp" % (target.name, secrets.token_hex(6)))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o666)
    try:
        with os.fdopen(descriptor, "wb") as handle, open(staged, "rb") as source:
            while True:
                chunk = source.read(1 << 20)
                if not chunk:
                    break
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        os.rename(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise

