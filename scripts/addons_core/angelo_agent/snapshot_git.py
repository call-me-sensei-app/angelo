# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only git signal for snapshot retention.

When a project file lives in a git work tree and the version committed at HEAD
is byte-identical to the latest saved file on disk, the commit is a durable
recovery point. Snapshots of that project captured before the commit are then
removed first when a retention limit requires removal; protections still apply
and nothing is removed only because of this signal.

Safety contract:

* Only the ``git`` executable found on PATH is used; without it the signal is
  silently absent. Each call has a timeout, no shell, no stdin, sanitized
  ``GIT_*`` environment and ``GIT_OPTIONAL_LOCKS=0``.
* Only read-only plumbing commands run (rev-parse, cat-file, log). The file is
  hashed in Python instead of ``git hash-object``, because hash-object runs
  clean filters such as Git LFS, which can write into the repository.
* Git LFS pointers are compared by the pointer's SHA-256 and size. Anything
  unexpected (filters, unborn HEAD, untracked or modified file, timeouts) means
  "no signal", never an error.
* Runs on the snapshot worker thread only, never on Blender's main thread.
"""

import hashlib
import os
import shutil
import stat
import subprocess
from pathlib import Path

from . import child_process

GIT_TIMEOUT_SECONDS = 10
LFS_POINTER_MAX_BYTES = 1024
HASH_BUFFER_BYTES = 8 * 1024 * 1024
_CACHE_LIMIT = 32
_LFS_VERSION = b"version https://git-lfs.github.com/spec/v1"


def _environment():
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    return environment


def _run(git, cwd, args, timeout):
    try:
        completed = child_process.run([git, "-C", str(cwd), "-c", "core.fsmonitor=false", *args],
                                      stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                      timeout=timeout, env=_environment(), check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout if completed.returncode == 0 else None


def _text(value):
    try:
        return value.decode("utf-8").strip() if value is not None else None
    except UnicodeDecodeError:
        return None


def _hash_file(path, algorithm, prefix=b""):
    """Stream-hash a regular file; None if it changes while being read."""
    try:
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode):
            return None
        hasher = hashlib.new(algorithm)
        hasher.update(prefix % before.st_size if prefix else b"")
        with open(path, "rb", buffering=0) as handle:
            while data := handle.read(HASH_BUFFER_BYTES):
                hasher.update(data)
        after = os.lstat(path)
    except OSError:
        return None
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        return None
    return hasher.hexdigest()


def _lfs_pointer(content):
    lines = content.splitlines()
    if not lines or lines[0] != _LFS_VERSION:
        return None
    fields = dict(line.split(b" ", 1) for line in lines[1:] if b" " in line)
    oid, size = fields.get(b"oid", b""), fields.get(b"size", b"")
    if not oid.startswith(b"sha256:") or len(oid) != 71 or not size.isdigit():
        return None
    return oid[7:].decode("ascii"), int(size)


def _matches_head(git, top, relative, path, size, timeout):
    blob = _text(_run(git, top, ["rev-parse", "--verify", "--quiet", "HEAD:" + relative], timeout))
    if not blob:
        return False
    stored_size = _text(_run(git, top, ["cat-file", "-s", blob], timeout))
    if not stored_size or not stored_size.isdigit():
        return False
    if int(stored_size) <= LFS_POINTER_MAX_BYTES:
        pointer = _lfs_pointer(_run(git, top, ["cat-file", "blob", blob], timeout) or b"")
        if pointer:
            return pointer[1] == size and _hash_file(path, "sha256") == pointer[0]
    if int(stored_size) != size:
        return False
    algorithm = _text(_run(git, top, ["rev-parse", "--show-object-format"], timeout)) or "sha1"
    if algorithm not in {"sha1", "sha256"}:
        return False
    return _hash_file(path, algorithm, b"blob %d\0") == blob


def project_commit_time(path, *, git, timeout=GIT_TIMEOUT_SECONDS, cache=None):
    """Commit time (seconds) of HEAD's identical copy of ``path``, else None."""
    try:
        path = Path(path)
        if not path.is_absolute():
            return None
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode):
            return None
    except OSError:
        return None
    top = _text(_run(git, path.parent, ["rev-parse", "--show-toplevel"], timeout))
    head = _text(_run(git, path.parent, ["rev-parse", "--verify", "--quiet", "HEAD"], timeout)) if top else None
    if not top or not head:
        return None
    try:
        relative = path.resolve().relative_to(Path(top).resolve()).as_posix()
    except (OSError, ValueError):
        return None
    key = (str(path), info.st_size, info.st_mtime_ns, info.st_ino, head)
    if cache is not None and key in cache:
        return cache[key]
    result = None
    if _matches_head(git, top, relative, path, info.st_size, timeout):
        when = _text(_run(git, top, ["log", "-1", "--format=%ct", "HEAD", "--", relative], timeout))
        result = int(when) if when and when.isdigit() else None
    if cache is not None:
        cache[key] = result
        while len(cache) > _CACHE_LIMIT:
            cache.pop(next(iter(cache)))
    return result


def committed_project_times(paths, *, git=None, timeout=GIT_TIMEOUT_SECONDS, cache=None):
    """Map each project path whose saved file equals HEAD to its commit time."""
    git = git or shutil.which("git")
    if not git:
        return {}
    result = {}
    for path in sorted({value for value in paths if value}):
        when = project_commit_time(path, git=git, timeout=timeout, cache=cache)
        if when is not None:
            result[path] = when
    return result
