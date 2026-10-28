# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reading a kept render's folders for ``job_recovery`` without taking unknown for absent. No Blender dependency.

A folder or file that can't be read now (a permission or I/O error, a drive that answers with an error) is
neither missing nor empty: these raise ``OSError`` or answer None, and recovery keeps the frames and says so.
Only a name that is not there (``FileNotFoundError``) or a link (never followed) counts as nothing.
"""

import errno
import os
import stat
import sys
from contextlib import ExitStack
from pathlib import Path, PurePosixPath

from . import checkpoint_filesystem as cf
from . import job_frame_check, job_outputs


def current_path(staging):
    """Where the held staging folder is now (it may have moved with a folder above it), or None."""
    folder = staging.folder
    try:
        if isinstance(folder, job_outputs.anchored_windows.WindowsDirectory):
            return Path(folder.final_path())
        if isinstance(folder, int) and sys.platform == "darwin":
            import fcntl
            return Path(os.fsdecode(fcntl.fcntl(folder, fcntl.F_GETPATH, bytes(1024)).split(b"\0", 1)[0]))
        if isinstance(folder, int):
            return Path(os.readlink("/proc/self/fd/%d" % folder))
    except (OSError, ValueError, AttributeError):
        pass
    return None


def marker_is(folder, identity):
    """Whether a staging folder carries this job's marker: False when it has none, another job's, or a link at its
    name. A marker that can't be read now raises ``OSError`` (``job_outputs._marked`` answers False then, which only
    keeps a folder its callers would remove; recovery would forget a folder of finished frames)."""
    try:
        descriptor = cf._open_file(folder, job_outputs.MARKER, os.O_RDONLY)
    except (FileNotFoundError, ValueError):
        return False
    except OSError as error:
        if error.errno != errno.ELOOP:   # ELOOP: POSIX O_NOFOLLOW refused a link.
            raise
        return False
    with os.fdopen(descriptor, "rb") as handle:
        return handle.read(64) == identity.encode("utf-8", "replace")


def _why(error):
    """An error's reason for a receipt: its message, never a path."""
    return (getattr(error, "strerror", None) or str(error) or type(error).__name__)[:120]


def _folder_state(path):
    """True (a folder is there), False (nothing, or not a folder) or None: it can't be told now."""
    try:
        return stat.S_ISDIR(os.stat(path).st_mode)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except (OSError, ValueError):
        return None


def published_names(folder):
    """Regular file names of a published folder, without following a link: none when it is not there (or is a link);
    ``OSError`` when it can't be read now, so a count made from them never says fewer frames than exist."""
    cf.require_anchored_files()
    try:
        with cf._private_directory(folder) as held:
            names = []
            for name in cf.iter_names(held):
                try:
                    cf._file_info(held, name)
                except (FileNotFoundError, ValueError):
                    continue   # Gone since it was listed, or not a regular file.
                names.append(name)
            return names
    except (FileNotFoundError, ValueError):
        return []


def _regular(folder, name, published=None, relative=""):
    """A regular file with no other name, or whose one other name is the same file in ``published`` (an earlier
    attempt's link of it, at the same relative path). A name gone since it was listed is not one; one that can't be
    read now raises ``OSError`` (unknown is never absent)."""
    try:
        info = cf.entry_info(folder, name)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode):
        return False
    if info.st_nlink == 1:
        return True
    if info.st_nlink != 2 or published is None:
        return False
    try:
        with ExitStack() as stack:
            target = published
            for part in PurePosixPath(relative).parts:
                target = stack.enter_context(cf._private_child(target, part))
            return job_outputs._entry_identity(target, name) == (info.st_dev, info.st_ino)
    except (FileNotFoundError, ValueError):
        return False   # Its other name is not in ``published`` (or a link is on the way): another file has it.


def staged_whole(staging, relative):
    """``job_frame_check``'s answer for one staged file (False when it can't be reached without a link). A file or
    folder that can't be read now raises ``OSError``: unknown is never torn."""
    *folders, name = PurePosixPath(relative).parts
    with ExitStack() as stack:
        folder = staging.folder
        try:
            for part in folders:
                folder = stack.enter_context(cf._private_child(folder, part))
        except ValueError:
            return False
        return job_frame_check.check_entry(folder, name)
