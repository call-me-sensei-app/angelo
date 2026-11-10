# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every replace of a file the add-on keeps (a save written whole to a temporary name, then moved over the old one),
and the reads of such files. No bpy; standard library only, so a background Angelo's plain script uses it too.

Windows refuses ``os.replace`` (``MoveFileExW``) onto a name any handle holds open, ``PermissionError`` with
``[WinError 5]``, and the files kept here are open often: the library's reload or another window reads a job's state
while its worker saves it, another Angelo starts on the same profile, an engine re-imports the asset just written,
the person's editor, a sync client or a scan holds one for a moment. A save refused that way is lost; a caller that
did not expect it strands what it was saving (a finished job stayed "generating", CI 41887060350).

* ``replace`` is ``os.replace`` on POSIX. On Windows it is ``win32_replace`` (POSIX semantics: it lands while a
  reader that shares delete holds the file, and that reader keeps the file it opened), retried with growing pauses
  while a handle that does not share delete holds either name, ``ATTEMPTS`` tries (100 ms of pauses in all). A folder,
  which ``win32_replace`` cannot open, is moved with ``os.replace`` under the same retries, onto a free name only
  (Windows refuses a folder onto any existing name: ``FileExistsError`` at once). A folder at the name of a file is
  never a reader: ``IsADirectoryError`` at once. ``retried`` gives a rename of another kind (an anchored
  one, ``checkpoint_filesystem``) the same retries. A refusal that outlasts the retries raises ``Refused`` (a
  ``PermissionError``): its caller says so and keeps what it was saving (``test_native_shared_replace_sites`` names
  each caller and its path). Every call gets its retries; a caller that tries again from a timer or a UI tick waits
  ``RETRY_SECONDS`` after a ``Refused`` first, so a file held for good never costs the main thread at every tick.
* ``open_shared``, ``open_file``, ``read_bytes`` and ``read_text`` read such a file sharing read, write and delete
  (``session_journal.open_shared``), so a reader of ours never refuses a writer's replace. A plain script outside
  the add-on package (``studio_assets_convert``) uses ``replace`` and ``retried`` only: the readers need the
  package on Windows.

Cost on the success path (``test_native_shared_replace``): POSIX, one Python call and one flag test over
``os.replace``; Windows, the journal's own rename (``CreateFileW``, ``SetFileInformationByHandle``, ``CloseHandle``)
in place of ``MoveFileExW``. The checker ``test_native_shared_replace_sites`` fails on any other replace or rename in
the add-on that it does not list with its reason.
"""

import errno
import os
import stat
import time

try:
    from . import win32_replace
except ImportError:   # A background Angelo's plain script (``studio_assets_convert``): its sibling module.
    import win32_replace

_WINDOWS = os.name == "nt"
ATTEMPTS = 5
PAUSE = 0.01   # Seconds before the second try; the next pauses grow by as much (10, 20, 30 and 40 ms).
# A caller that saves from a timer or UI tick tries a refused save (``Refused``) again no sooner than this: a file
# held for good then costs the main thread one replace's pauses (100 ms) this often, never at every tick.
RETRY_SECONDS = 5.0
_ATTRIBUTE_DIRECTORY = 0x10   # FILE_ATTRIBUTE_DIRECTORY (``st_file_attributes``, Windows)
# Told each ``Refused`` target (any thread): the add-on's window shows the person a card (``refused_saves``).
on_refused = None


class Refused(PermissionError):
    """Windows still refused a replace after its retries: a handle that does not share delete holds a name. Never
    raised on POSIX, where no reader refuses a replace (a ``PermissionError`` there is the folder's own)."""


def replace(source, target, attempts=ATTEMPTS, shown=None, told=True):
    """Replace ``target`` with ``source`` (module docstring); ``Refused`` when Windows still refuses it. ``shown``:
    the file the person knows, told in place of ``target`` (a move aside names the file it moves); ``told=False``
    for a caller that tells the person itself (``on_refused`` is not called)."""
    if not _WINDOWS:
        return os.replace(source, target)
    return retried(_windows_replace, source, target, attempts, shown, told)


def remove(path, attempts=ATTEMPTS, told=True):
    """Remove ``path`` (already gone: done). Windows refuses a delete while a handle that does not share delete holds
    the file: retried as ``replace`` is, then ``Refused`` (told unless ``told=False``)."""
    if not _WINDOWS:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        return

    def unlink(source, _target):
        try:
            os.unlink(source)
        except FileNotFoundError:
            pass
        except PermissionError as error:
            if os.path.isdir(source):   # A folder at the name refuses every try: never a holder to wait for.
                raise IsADirectoryError(errno.EISDIR, "A folder is where the file is", os.fspath(source)) from error
            raise
    retried(unlink, path, path, attempts, None, told)


def tell(path):
    """Tell the person ``path`` is held (``on_refused``), for a caller with its own retries that gave up."""
    _tell(path)


def discard(temporary):
    """Remove a save's temporary file after its replace failed; quietly, when it is gone or cannot go now."""
    try:
        os.remove(temporary)
    except OSError:
        pass


def retried(move, source, target, attempts=ATTEMPTS, shown=None, told=True):
    """``move(source, target)``, tried again with growing pauses while Windows refuses it (``PermissionError``): a
    handle that does not share delete holds a name. ``replace`` and the anchored renames (``checkpoint_filesystem``)
    use it; ``IsADirectoryError`` (a folder in a file's place) is never retried. Windows' last refusal raises
    ``Refused``."""
    for attempt in range(1, attempts + 1):
        try:
            return move(source, target)
        except PermissionError as error:
            if not _WINDOWS:
                raise   # POSIX: no reader refuses a replace, so a permission error is the folder's own.
            if attempt >= attempts:
                if told:
                    _tell(target if shown is None else shown)
                if isinstance(error, Refused):
                    raise
                raise Refused(error.errno, error.strerror, error.filename, getattr(error, "winerror", None),
                              error.filename2) from error
            time.sleep(PAUSE * attempt)


def _tell(target):
    told = on_refused
    if told is not None:
        try:
            told(target)
        except Exception:   # Telling the person never changes what the caller is told.
            pass


def _windows_replace(source, target):
    try:
        return win32_replace.replace(source, target)
    except PermissionError as error:
        if _folder(source):   # A folder (or a link to one): ``win32_replace`` opens files only.
            if os.path.lexists(target):   # Windows never moves a folder onto a name in use: no reader to wait for.
                raise FileExistsError(errno.EEXIST, "Something is already where the folder goes",
                                      os.fspath(target)) from error
            return os.replace(source, target)
        if os.path.isdir(target):   # A folder at a file's name refuses every try: never a reader to wait for.
            raise IsADirectoryError(errno.EISDIR, "A folder is where the file goes", os.fspath(target)) from error
        raise


def _folder(path):
    """A folder, or a link or junction to one (dangling too: Windows marks the link itself as a directory)."""
    try:
        status = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(status.st_mode) or bool(getattr(status, "st_file_attributes", 0) & _ATTRIBUTE_DIRECTORY) or (
        stat.S_ISLNK(status.st_mode) and os.path.isdir(path))


def open_shared(path, flags=os.O_RDONLY | getattr(os, "O_BINARY", 0)):
    """A read-only descriptor for ``path`` that never refuses a writer's replace. POSIX, where no reader refuses one:
    ``os.open(path, flags)`` (a reader that must not follow a link or wait on a FIFO passes ``O_NOFOLLOW`` and
    ``O_NONBLOCK``). Windows: ``session_journal.open_shared``, sharing read, write and delete (no FIFOs there, and
    ``O_NOFOLLOW`` is unknown to Windows' ``os.open`` as well)."""
    if not _WINDOWS:
        return os.open(path, flags)
    from . import session_journal
    return session_journal.open_shared(path)


def open_file(path, flags=os.O_RDONLY | getattr(os, "O_BINARY", 0)):
    """``open_shared`` as a binary file object. A descriptor that is no file (a folder at the name, which POSIX
    opens) is closed again and the error names ``path``, never the descriptor."""
    fd = open_shared(path, flags)
    try:
        return open(fd, "rb")
    except OSError as error:
        os.close(fd)
        raise type(error)(error.errno, error.strerror, os.fspath(path)) from None
    except BaseException:
        os.close(fd)
        raise


def read_bytes(path):
    """The whole file at ``path``, read through ``open_shared``."""
    with open_file(path) as handle:
        return handle.read()


def read_text(path, encoding="utf-8"):
    """The whole file at ``path`` as text, read through ``open_shared`` (no newline translation: the text written)."""
    return read_bytes(path).decode(encoding)
