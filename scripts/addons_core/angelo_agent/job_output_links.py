# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A staged folder's files published into a new output folder, and the folders made for it held by identity. Pure.

Split from ``job_outputs`` (its module limit): every name it uses is that module's (``jo``).

A staged file is published as a new hard link where the volume has them, so the staging copy stays whole until the
publication ends. FAT and exFAT have none (macOS ``ENOTSUP``, Linux ``EPERM``, Windows ``ENOTSUP`` from
``anchored_windows``): there the staged file itself is moved into place (``publish_entry``), a rename on the same
volume, as instant as a link and with no second copy; ``moved`` records each, and an unpublish moves it back into
staging (``restore``), so a failed or retried publication still leaves the staging folder whole. A move never
replaces another's entry: an exclusive rename (Windows, macOS ``RENAME_EXCL``, Linux ``RENAME_NOREPLACE``); where
the volume has none (exFAT on macOS, network shares) the new name is first created exclusively and the file renamed
over that empty file. Residual, stated (``long_jobs.rst``, "Publication"): another process that replaces that empty
file in the moment before the rename loses its file to the published one. Metadata partner preflight and
postchecks likewise do not make the primary/companion namespace atomic against same-user races.
"""

import errno
import os
import stat
import struct
import sys
from types import SimpleNamespace
from contextlib import ExitStack, contextmanager

from . import anchored_windows, checkpoint_filesystem as cf, exclusive_rename

# A hard link this volume can't make (and that is no taken name or other failure).
NO_LINKS = {errno.EPERM, errno.ENOSYS, getattr(errno, "ENOTSUP", 45), getattr(errno, "EOPNOTSUPP", 95)}
OTHER_VOLUME = "The job's staging folder is on another volume than its output; nothing was moved"


class MoveJournal(list):
    """Moves plus metadata identities created by this publication, never guessed during removal."""
    def __init__(self):
        super().__init__()
        self.metadata = set()


def _key(info):
    return info.st_dev, info.st_ino


def _partner_name(name):
    return '._' + name


def volume_info(folder):
    """Fresh descriptor-bound Darwin mount observation (same public statfs layout as change_clock)."""
    import ctypes
    class Statfs(ctypes.Structure):
        _fields_ = [("bsize", ctypes.c_uint32), ("iosize", ctypes.c_int32), ("counts", ctypes.c_uint64 * 5),
                    ("fsid", ctypes.c_int32 * 2), ("owner", ctypes.c_uint32), ("type", ctypes.c_uint32),
                    ("flags", ctypes.c_uint32), ("subtype", ctypes.c_uint32), ("kind", ctypes.c_char * 16),
                    ("mount", ctypes.c_char * 1024), ("source", ctypes.c_char * 1024),
                    ("extended", ctypes.c_uint32), ("reserved", ctypes.c_uint32 * 7)]
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, "fstatfs$INODE64", None) or libc.fstatfs
    function.argtypes, function.restype = [ctypes.c_int, ctypes.POINTER(Statfs)], ctypes.c_int
    info = Statfs()
    if function(folder, ctypes.byref(info)):
        raise OSError(ctypes.get_errno(), 'Cannot identify the job output volume')
    return dict(kind=info.kind.decode('ascii', 'strict'), device=os.fstat(folder).st_dev,
                fsid=list(info.fsid))


def _paired_platform(folder):
    if sys.platform != 'darwin' or not isinstance(folder, int):
        return False
    # These native-xattr filesystems keep literal ._ files independent; preserve ordinary payload behavior.
    # Other Darwin volumes use conservative pair checks; postconditions, not this label, prove a move.
    return volume_info(folder)['kind'] not in {'apfs', 'hfs'}


def partner(folder, name):
    """A validated AppleDouble companion of a held primary; absent is None, unknown refuses.

    This is job-output-specific. XNU pairs names without inspecting magic, so callers must
    independently refuse ANY destination partner before a move, including malformed ones.
    """
    if not _paired_platform(folder):
        return None
    try:
        candidate = cf.entry_info(folder, _partner_name(name))
    except FileNotFoundError:
        return None
    if name.startswith('._') or not stat.S_ISREG(candidate.st_mode) or candidate.st_nlink != 1:
        raise ValueError('Unknown job metadata companion; its files were retained')
    primary = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=folder)
    try:
        main = os.fstat(primary)
        if not (stat.S_ISREG(main.st_mode) or stat.S_ISDIR(main.st_mode)):
            raise ValueError('Job metadata has no regular primary')
        descriptor = os.open(_partner_name(name), os.O_RDONLY | os.O_NOFOLLOW, dir_fd=folder)
        try:
            opened = os.fstat(descriptor)
            if _key(opened) != _key(candidate) or not 26 <= opened.st_size <= 1024 * 1024:
                raise ValueError('Job metadata companion changed or exceeds its bound')
            header = os.read(descriptor, 26)
            if len(header) != 26:
                raise ValueError('Incomplete job metadata header')
            magic, version = struct.unpack_from('>II', header)
            count = struct.unpack_from('>H', header, 24)[0]
            if magic != 0x00051607 or version != 0x00020000 or not 1 <= count <= 2:
                raise ValueError('Unrecognized job metadata header')
            table = os.read(descriptor, 12 * count)
            if len(table) != 12 * count:
                raise ValueError('Incomplete job metadata table')
            entries = [struct.unpack_from('>III', table, n * 12) for n in range(count)]
            if (len({tag for tag, _, _ in entries}) != count or
                    any(tag not in {2, 9} or offset < 26 + 12 * count or
                        offset + size > opened.st_size for tag, offset, size in entries)):
                raise ValueError('Invalid job metadata entries')
            ranges = sorted((offset, offset + size) for _, offset, size in entries if size)
            if (any(end > later for (_, end), (later, _) in zip(ranges, ranges[1:])) or
                    any(tag == 9 and size < 32 for tag, _, size in entries)):
                raise ValueError('Overlapping or incomplete job metadata entries')
            if _key(cf.entry_info(folder, name)) != _key(main):
                raise ValueError('Job metadata primary changed')
            return opened
        finally:
            os.close(descriptor)
    finally:
        os.close(primary)


def names(folder, budget=None):
    """Logical job entries, after proving every skipped companion is paired metadata.

    Never silently discard an arbitrary dot file. Marker companions pair with the marker and
    are not payload. Re-enumerate after operations: a main rename can move its partner itself.
    """
    listed = cf.list_names(folder)
    if budget is not None and len(listed) > budget.left:
        raise ValueError("Too many physical job entries")
    if not _paired_platform(folder):
        return listed
    logical = []
    for name in listed:
        if name.startswith('._'):
            if partner(folder, name[2:]) is None:
                raise ValueError('Unpaired job metadata was retained')
            if budget is not None:
                budget.spend()
        else:
            partner(folder, name)  # Refuse malformed partners before publishing any entry.
            logical.append(name)
    return logical


def _destination_free(folder, name):
    if _paired_platform(folder):
        try:
            cf.entry_info(folder, _partner_name(name))
        except FileNotFoundError:
            return
        raise FileExistsError(errno.EEXIST, 'A metadata partner already occupies this output name', name)


def charge_metadata(folder, name, budget):
    if partner(folder, name) is not None:
        budget.spend()


def make_parent(folder, name):
    try:
        cf.entry_info(folder, name)
    except FileNotFoundError:
        _destination_free(folder, name)
        return cf.make_directory(folder, name)
    raise FileExistsError(errno.EEXIST, 'Parent already exists', name)


def create_staging(folder, name, created):
    _destination_free(folder, name)
    cf.make_directory(folder, name)
    created()  # Own the new primary before the fallible metadata observation.
    metadata = partner(folder, name)
    return {_key(metadata)} if metadata is not None else set()


def require_owned_partner(folder, name, owned_metadata):
    metadata = partner(folder, name)
    if metadata is not None and _key(metadata) not in owned_metadata:
        raise ValueError('Another metadata companion was retained')
    return metadata


def create_directory(target, name, private, journal=None):
    _destination_free(target, name)
    identity = exclusive_rename.create_directory(target, name, private)
    metadata = partner(target, name)
    if metadata is not None and journal is not None and hasattr(journal, "metadata"):
        journal.metadata.add(_key(metadata))
    return identity


def remove_entry(folder, name, owned_metadata=None):
    """Remove a primary only when its implicit kernel metadata deletion is also authorized."""
    metadata = (partner(folder, name) if owned_metadata is None else
                require_owned_partner(folder, name, owned_metadata))
    cf.remove_entry(folder, name)
    if metadata is not None:
        try:
            remaining = cf.entry_info(folder, _partner_name(name))
        except FileNotFoundError:
            return
        if _key(remaining) != _key(metadata):
            raise ValueError('Metadata companion changed during removal')
        cf.remove_entry(folder, _partner_name(name))


def publish_entry(source, name, target, new_name, moved=None, relative=(), place=None, before_move=None):
    """The staged regular file ``name`` of ``source`` as the new entry ``new_name`` of ``target``, never over an
    existing name (``FileExistsError``); returns the (device, file ID) of the entry made: a link is the staging file,
    identified on the private staging folder before the link exists; a moved file is identified on a handle held
    across its move, after it (FAT numbers a file by its directory entry: a move gives it another ID). A move is
    recorded in ``moved``: (``place``, where it went from the publication's target folder; its path in staging,
    ``relative`` + ``name``; that identity), which ``restore`` replays. ``before_move()`` runs just before a move."""
    from . import job_outputs as jo
    identity = jo._entry_identity(source, name)
    source_metadata = partner(source, name)
    _destination_free(target, new_name)
    try:
        if source_metadata is not None:
            raise OSError(errno.ENOTSUP, "A paired job file moves with its metadata")
        cf.link_into(source, name, target, new_name)
        return identity
    except FileExistsError:
        raise
    except OSError as error:
        if error.errno not in NO_LINKS:
            raise
    if _device(source) != _device(target):
        raise OSError(errno.EXDEV, OTHER_VOLUME, new_name)   # Never a copy: the outputs stay in staging, kept.
    record = [tuple(place or (new_name,)), (*relative, name), None, {"observed": False}]
    before_move and before_move()
    info = _move(source, name, target, new_name, lambda: moved is not None and moved.append(record))
    if info is None:
        raise OSError(errno.ESTALE, "The published file or metadata pair could not be identified", new_name)
    record[2] = identity = (info.st_dev, info.st_ino) if info is not None else None
    record[3] = {"observed": info is not None, "identity": getattr(info, "companion", None)}
    if moved is not None and hasattr(moved, "metadata") and getattr(info, "companion", None) is not None:
        moved.metadata.add(info.companion)
    if moved is not None:   # Unidentified (None): restore never moves it back, and it is said to stay published.
        moved[moved.index(record)] = tuple(record)
    return identity


def _device(folder):
    return cf.directory_info(folder).st_dev


def _move(source, name, target, new_name, done=None):
    """Rename the file ``name`` of the held folder ``source`` to ``new_name`` in the held folder ``target``, never
    over an existing entry (``FileExistsError``; the module docstring's residual where no exclusive rename exists);
    ``done()`` records the move before postchecks; returns held-file and companion identities.
    Unknown poststate raises with the move retained, never reports a completed pair."""
    if isinstance(source, anchored_windows.WindowsDirectory):
        return source.move_into(name, target, new_name, done)
    if not isinstance(source, int) or not isinstance(target, int):
        raise ValueError("Safe descriptor-relative publication is unavailable")
    _destination_free(target, new_name)
    metadata = partner(source, name)
    metadata_fd = (os.open(_partner_name(name), os.O_RDONLY | os.O_NOFOLLOW, dir_fd=source)
                   if metadata is not None else None)
    if metadata_fd is not None and _key(os.fstat(metadata_fd)) != _key(metadata):
        os.close(metadata_fd)
        raise ValueError("Source metadata changed before publication")
    try:
        held = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0), dir_fd=source)
    except PermissionError:   # A staged file this user can't read (a rename needs no read access): see below.
        held = None
    except BaseException:
        if metadata_fd is not None:
            os.close(metadata_fd)
        raise
    try:
        try:
            exclusive_rename.rename(source, name, target, new_name)
        except exclusive_rename.Unsupported:
            claim = cf._open_file(target, new_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)   # Taken: FileExistsError.
            try:
                claimed = os.fstat(claim)
            finally:
                os.close(claim)
            claimed_metadata = partner(target, new_name)
            try:
                now = partner(target, new_name)
                if ((_key(now) if now is not None else None) !=
                        (_key(claimed_metadata) if claimed_metadata is not None else None)):
                    raise ValueError("Output metadata claim changed")
                os.replace(name, new_name, src_dir_fd=source, dst_dir_fd=target)
            except BaseException:
                _remove_claim(target, new_name, claimed, claimed_metadata)
                raise
        done and done()
        # Through the handle held across the move; for a file it couldn't open, its entry in the held target folder
        # once moved (only a process that can write that folder could have put another there in between).
        info = os.fstat(held) if held is not None else cf.entry_info(target, new_name)
        after = partner(target, new_name)
        if metadata_fd is None:
            if after is not None:
                raise OSError(errno.ESTALE, "Unexpected metadata appeared after publication")
        elif after is None or _key(after) != _key(os.fstat(metadata_fd)):
            raise OSError(errno.ESTALE, "Published metadata could not be identified")
        return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino,
                               companion=_key(after) if after is not None else None)
    finally:
        if held is not None:
            os.close(held)
        if metadata_fd is not None:
            os.close(metadata_fd)


def _remove_claim(target, name, claimed, metadata=None):   # The empty file ``_move`` created, while it is still that one.
    try:
        info = cf.entry_info(target, name)
        if (info.st_dev, info.st_ino) == (claimed.st_dev, claimed.st_ino) and info.st_size == 0:
            allowed = {_key(metadata)} if metadata is not None else set()
            remove_entry(target, name, allowed)
    except (ValueError, OSError):
        pass


class NotRestored(OSError):
    """A moved file ``restore`` could not put back: what holds it is never removed."""


def restore(staging, target, top, moved):
    """Before an unpublish removes ``top`` from ``target``: every move a publication recorded under it (``moved``,
    newest first) is undone, each only while the entry at its place is still the file moved there; returns whether
    ``top`` itself was such a file (moved back: nothing is left to remove). ``NotRestored`` when one can't be,
    once every other has gone back: the caller then removes nothing of ``top``."""
    itself, failed = False, None
    for record in reversed([record for record in moved if record[0][0] == top]):
        place, home, identity = record[:3]
        proof = record[3] if len(record) == 4 else {}
        if identity is None or not proof.get("observed"):
            failed = failed or NotRestored(errno.ESTALE, "A moved file could not be identified", place[-1])
            continue
        try:
            with ExitStack() as stack:
                folder = target
                for part in place[:-1]:
                    folder = stack.enter_context(cf._private_child(folder, part))
                info = cf.entry_info(folder, place[-1])
                if (info.st_dev, info.st_ino) != identity or not stat.S_ISREG(info.st_mode):
                    raise NotRestored(errno.ESTALE, "Another file is at a published name", place[-1])
                metadata = partner(folder, place[-1])
                if (_key(metadata) if metadata is not None else None) != proof.get("identity"):
                    raise NotRestored(errno.ESTALE, "Published metadata changed", place[-1])
                back = staging
                for part in home[:-1]:
                    back = stack.enter_context(cf._private_child(back, part))
                _move(folder, place[-1], back, home[-1])
        except NotRestored as error:
            failed = failed or error
            continue   # The others still go back; what holds this one is never removed.
        except (ValueError, OSError) as error:
            failed = failed or NotRestored(errno.EIO, "A published file could not be moved back: %s" % error,
                                           place[-1])
            continue
        moved.remove(record)
        itself = itself or len(place) == 1
    if failed is not None:
        raise failed
    return itself


def remove_made(staging, target, name, ours=None):
    """Remove the entry ``name`` of ``target`` that an unpublish takes back: a file only while it is one of the
    staging folder's own (a link to a staged file has its identity), a folder only once nothing else is left in it:
    a file another program put into a folder this publication made stays, with the folders down to it. ``ours``:
    those identities, when the caller has them (``staged_identities``); else read here."""
    from . import job_outputs as jo
    budget = jo._Budget(jo.REMOVE_ALL)
    if ours is None:
        ours = staged_identities(staging)

    def remove(folder, entry, depth):
        budget.spend()
        info = cf.entry_info(folder, entry)
        if stat.S_ISDIR(info.st_mode):
            if depth > jo.MAX_DEPTH:
                return
            with cf._private_child(folder, entry) as inner:
                for child in names(inner, budget):
                    remove(inner, child, depth + 1)
                if cf.list_names(inner):
                    return   # Something not ours is in it: kept.
        elif (info.st_dev, info.st_ino) not in ours:
            return
        remove_entry(folder, entry, ours)
    remove(target, name, 0)


def staged_identities(staging):
    """The (device, file ID) of every regular file in the held staging folder: what ``remove_made`` may remove."""
    from . import job_outputs as jo
    ours, budget = set(), jo._Budget(jo.REMOVE_ALL)

    def staged(folder, depth):
        for child in names(folder, budget):
            budget.spend()
            info = cf.entry_info(folder, child)
            if stat.S_ISDIR(info.st_mode) and depth < jo.MAX_DEPTH:
                with cf._private_child(folder, child) as inner:
                    staged(inner, depth + 1)
            elif stat.S_ISREG(info.st_mode):
                ours.add((info.st_dev, info.st_ino))
                metadata = partner(folder, child)
                if metadata is not None:
                    ours.add(_key(metadata))
    staged(staging, 0)
    return ours


def link_tree(source, target, budget, depth=0, moved=None, relative=(), placed=()):
    """Publish every file of ``source`` into the empty folder ``target`` (same names, same layout; ``publish_entry``:
    ``moved``; ``relative``, ``source``'s path in staging; ``placed``, ``target``'s from the publication's target).

    A failure leaves what it made for the caller's ``Staging.unpublish``, which every publication runs on any
    failure: it moves back what was moved (``restore``), then removes only the staging folder's own files and the
    folders left empty (``remove_made``). Never removed here with a whole folder: another program can have put a
    file into a folder this made, and that file stays. Each folder is opened through ``held``: the one
    ``exclusive_rename.create_directory`` made, known from a handle on it.
    """
    from . import job_outputs as jo
    if depth > jo.MAX_DEPTH:
        raise jo.OutputMoved(jo.UNSAFE)
    for name in names(source, budget):
        if depth == 0 and name == jo.MARKER:
            continue
        budget.spend()
        if jo._kind(source, name) == "file":
            publish_entry(source, name, target, name, moved, relative, (*placed, name))
            budget.files += 1
            continue
        identity = create_directory(target, name, source, moved)
        with cf._private_child(source, name) as inner, held(target, name, identity) as made:
            link_tree(inner, made, budget, depth + 1, moved, (*relative, name), (*placed, name))


@contextmanager
def held(target, name, identity):
    """The folder at ``name`` in ``target``, held, when it is the one made (``identity``); else ``OutputMoved``."""
    from . import job_outputs as jo
    with cf._private_child(target, name) as folder:
        if jo._identity(folder) != identity:
            raise jo.OutputMoved(jo.MOVED)
        yield folder
