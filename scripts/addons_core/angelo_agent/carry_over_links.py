# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Links a carry-over's renames would change the meaning of (``carry_over_moves``), pinned first.

A relative link means a place relative to the folder it sits in, so a rename can change what it reaches. The rule, per
link, decided before the rename that moves it (``Plan``, the whole carry-over's view):

* its target moves with the carry-over to exactly the place its relative text will reach from the link's new folder
  (two entries that both move, a link inside one folder): it stays as it is, relative;
* its target stays behind (outside the old state folder, or inside an entry that stays, such as a retained native
  sign-in profile): it gets the absolute target naming that place now, verified to reach the same place first;
* its target moves elsewhere (into or out of an entry kept aside in ``kept-from-<version>``, one level deeper): it is
  left as it is, a known limit.

A link that is a junction (Windows) is absolute already, and is never walked into. Paths compare as the file system
does: case-insensitively on macOS and Windows. A folder this process cannot list is skipped: its links cannot be read
either, and the rename moves it whole, as before pinning existed.

Each change is a twin beside the link (``RELINKING``) renamed onto it: on POSIX one atomic replace; on Windows, which
replaces no folder link, the link is removed and the twin takes its name, retried while something holds it, and the
original link is put back when that still fails. A crash between the two leaves the twin, which ``finish_relink``
renames into place at the next launch (``carry_over_moves.Mover.rename``; ``version_carry_over._move`` first, for the
state folder itself). Without the privilege to create a symbolic link (Windows' error 1314), a folder target gets a
junction instead, and any other link is kept as it is. Standard library only.
"""

import errno
import os
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path

RELINKING = ".angelo-relink-"   # A link's absolute twin, beside it, until it takes the link's name.
SHARING_RETRIES = (0.05, 0.1, 0.2, 0.4, 0.8)
NO_SYMLINK_PRIVILEGE = 1314     # Windows' ERROR_PRIVILEGE_NOT_HELD.
LONG, LONG_UNC = "\\\\?\\", "\\\\?\\UNC\\"   # Windows' extended-length prefixes.
FOLDER_ATTRIBUTE = 0x10         # FILE_ATTRIBUTE_DIRECTORY: a folder link on Windows, dangling or not.


def long_path(path):
    from .carry_over_moves import long_path as extended
    return extended(path)


def _info(path):
    try:
        return os.lstat(long_path(path))
    except OSError:
        return None


def is_junction(path):
    junction = getattr(os.path, "isjunction", None)
    return bool(junction and junction(long_path(path)))


def is_link(path):
    found = _info(path)
    return found is not None and (stat.S_ISLNK(found.st_mode) or is_junction(path))


def _key(path):
    """``path`` as the file system compares it: normalized, and case-folded where names ignore case."""
    text = os.path.normcase(os.path.normpath(str(path)))
    return text.casefold() if sys.platform in ("darwin", "win32") else text


def _under(path, root):
    """The parts of ``path`` below ``root`` ([] for the root itself), or None when it is not inside it."""
    path, root = _key(path), _key(root)
    if path == root:
        return []
    prefix = root.rstrip(os.sep) + os.sep
    return path[len(prefix):].split(os.sep) if path.startswith(prefix) else None


@dataclass
class Plan:
    """The whole carry-over: ``root`` (the old state folder) moves to ``dest``, except its top-level ``stays``."""
    root: Path
    dest: Path
    stays: frozenset = frozenset()

    def destination(self, place):
        """Where ``place`` will be once the carry-over is done (itself when it stays behind)."""
        parts = _under(place, self.root)
        if parts is None or (parts and parts[0] in {_key(name) for name in self.stays}):
            return place
        relative = os.path.relpath(os.path.normpath(str(place)), os.path.normpath(str(self.root)))
        return os.path.normpath(os.path.join(str(self.dest), relative)) if parts else str(self.dest)


def _absolute(target):
    return os.path.isabs(target) or bool(os.path.splitdrive(target)[0]) or target.startswith(("/", "\\"))


# Folders of the state folder that hold no link by construction, so a walk skips them: the snapshot store writes only
# regular files, descriptor-relative, and never follows a link (checkpoint_filesystem). It is most of a large state.
NO_LINKS = frozenset({"checkpoints"})
SLOW_WALK = 0.5   # Seconds: a walk longer than this says so in the log, with its size.


def _links(entry, top=None):
    """Every link in the folder ``entry``, never followed: symbolic links, and junctions, never walked into. ``top``:
    the state folder, whose ``NO_LINKS`` folders are skipped."""
    started, stack, found, folders = time.monotonic(), [Path(entry)], [], 0
    skipped = [Path(top) / name for name in NO_LINKS] if top is not None else []
    while stack:
        folder = stack.pop()
        folders += 1
        if any(_under(folder, store) is not None for store in skipped):
            continue   # In the snapshot store, at any depth (a merge's entry may be one of its folders).
        try:
            listing = list(os.scandir(long_path(folder)))
        except OSError:
            continue   # Unlistable: its links cannot be read either; the rename moves it whole (module docstring).
        for item in listing:
            junction = getattr(item, "is_junction", lambda: False)()
            if item.is_symlink() or junction:
                found.append(folder / item.name)
            elif item.is_dir(follow_symlinks=False):
                stack.append(folder / item.name)
    took = time.monotonic() - started
    if took > SLOW_WALK:
        print("Angelo version carry-over: looked for links in %d folders under %s in %.1f s" % (folders, entry, took))
    return found


def pin_links(entry, target, plan):
    """Before ``entry`` is renamed to ``target``: pin each link in it (or itself) whose target stays behind and whose
    relative text would reach somewhere else from its new folder (module docstring). Returns how many changed."""
    finish_relink(entry)
    found = _info(entry)
    if found is None or is_junction(entry):
        return 0
    candidates = ([Path(entry)] if stat.S_ISLNK(found.st_mode)
                  else _links(entry, plan.root) if stat.S_ISDIR(found.st_mode) else [])
    pinned = 0
    for link in candidates:
        if link.name.startswith(RELINKING):
            finish_relink(link.with_name(link.name[len(RELINKING):]))
            continue
        if not is_link(link) or is_junction(link):
            continue   # Its twin took the name meanwhile (absolute already), or a junction (absolute).
        text = os.readlink(long_path(link))
        if _absolute(text):
            continue
        old_folder = os.path.dirname(str(link))
        moved = Path(target) / os.path.relpath(old_folder, str(entry)) if Path(link) != Path(entry) else None
        new_folder = str(moved) if moved is not None else os.path.dirname(str(target))
        reaches_now = os.path.normpath(os.path.join(old_folder, text))
        goes_to = plan.destination(reaches_now)
        if _key(os.path.join(new_folder, text)) == _key(goes_to):
            continue   # Its target moves with it to where its text will reach: it stays relative.
        if _key(goes_to) != _key(reaches_now):
            continue   # Its target moves elsewhere (a kept-aside entry): a known limit, left as it is.
        if _relink(link, _resolved(os.path.join(old_folder, text))):
            pinned += 1
    return pinned


def _resolved(place):
    """The absolute spelling of ``place`` with no ``..`` left, its folders resolved as the system resolves them now
    (POSIX applies a ``..`` after the folder before it, which a move takes away; Windows applies it lexically). The
    last part stays as named, so a link to a link stays one."""
    head, tail = os.path.split(place)
    if tail in ("", ".", ".."):
        return os.path.realpath(place)
    return os.path.join(os.path.realpath(head), tail)


def _folder_link(link):
    """Whether ``link`` is a folder link, dangling or not (Windows records it; elsewhere: what it reaches now)."""
    attributes = getattr(_info(link), "st_file_attributes", None)
    if attributes is not None:
        return bool(attributes & FOLDER_ATTRIBUTE)
    return os.path.isdir(long_path(link))


def _make_twin(target, twin, folder):
    """The absolute twin: a symbolic link, or without the privilege for one a junction for a folder. False: neither
    can be made here, so the link is kept as it is."""
    try:
        os.symlink(target, long_path(twin), target_is_directory=folder)
        return True
    except OSError as error:
        if getattr(error, "winerror", None) != NO_SYMLINK_PRIVILEGE:
            raise
    if folder and os.path.isdir(target):
        try:
            _junction(target, twin)
            return True
        except OSError:
            pass   # A target a junction cannot name (another machine's share): kept as it is, below.
    print("Angelo version carry-over: kept the link %s as it is (no privilege to make links here)" % twin.name[len(
        RELINKING):])
    return False


def _junction(target, path):
    import _winapi
    _winapi.CreateJunction(target, long_path(path))


def plain_target(text):
    r"""A junction's target as ``CreateJunction`` takes it: without the ``\\?\`` (or ``\\?\UNC\``) prefix
    ``os.readlink`` returns, to which it would add its own and name nothing."""
    if text.startswith(LONG_UNC):
        return "\\\\" + text[len(LONG_UNC):]
    return text[len(LONG):] if text.startswith(LONG) else text


# A mount point's readlink text: a volume mounted at a folder, which a junction cannot name (CreateJunction would make
# ``Volume{...}`` a folder of the working directory).
VOLUME_TARGETS = ("\\\\?\\volume{", "\\\\?\\globalroot\\")


def mounted_volume(path):
    """The volume ``path`` mounts (its readlink text) when it is a disk mounted at a folder, else None."""
    if not is_junction(path):
        return None
    try:
        text = os.readlink(long_path(path))
    except OSError:
        return None
    return text if text.casefold().startswith(VOLUME_TARGETS) else None


def refuse_mounted(paths):
    """Raise, naming it, for the first of ``paths`` that is a disk mounted at a folder: a carry-over to another disk
    cannot recreate one, so it says so before anything is copied, and the person unmounts it or moves it out."""
    for path in paths:
        volume = mounted_volume(path)
        if volume is not None:
            raise OSError(errno.EXDEV, "%s is a disk mounted at a folder (%s), which Angelo cannot move to another "
                                       "disk: unmount it or move it out of Angelo's folder, then open Angelo again"
                          % (path, volume))


def copy_junction(source, temporary):
    """A junction made at ``temporary`` naming the folder ``source`` names, verified to reach it, else removed and
    raised (a junction that named nothing would lose the person's external folder). A disk mounted at a folder is
    refused, naming it (``refuse_mounted``)."""
    refuse_mounted([source])
    target = plain_target(os.readlink(long_path(source)))
    _junction(target, temporary)
    if os.path.exists(long_path(source)):
        same = _same(temporary, source)
    else:   # Its folder is on a disk not attached: neither resolves, so the two must name it alike.
        same = _key(plain_target(os.readlink(long_path(temporary)))) == _key(target)
    if not same:
        _unlink_link(temporary)
        raise OSError(errno.EIO, "A junction made for %s does not reach the same folder" % source)


def _relink(link, target):
    """Give ``link`` the absolute ``target`` naming the same place; False when it is kept as it is."""
    twin = link.with_name(RELINKING + link.name)
    if _info(twin) is not None:
        _unlink_link(twin)
    text, folder = os.readlink(long_path(link)), _folder_link(link)
    if not _make_twin(target, twin, folder):
        return False
    if os.path.exists(long_path(link)) and not os.path.samefile(long_path(twin), long_path(link)):
        _unlink_link(twin)
        raise OSError(errno.EIO, "A link's absolute form did not reach the same place as %s" % link)
    if os.name != "nt":
        os.replace(long_path(twin), long_path(link))
        return True
    return _swap(twin, link, text, folder)


def _swap(twin, link, text, folder, sleep=time.sleep):
    """Windows, which replaces no folder link: the link goes and the twin takes its free name, retried while something
    holds it; still refused, the original link comes back (or, when it cannot, the twin stays for ``finish_relink``)."""
    _unlink_link(link)
    for delay in SHARING_RETRIES + (None,):
        try:
            os.rename(long_path(twin), long_path(link))
            return True
        except OSError:
            if delay is None:
                break
            sleep(delay)
    try:   # Still refused: the original link comes back, so nothing reads as moved meanwhile.
        os.symlink(text, long_path(link), target_is_directory=folder)
        _unlink_link(twin)
    except OSError:
        pass   # The twin stays: finish_relink renames it into place at the next launch.
    raise OSError(errno.EBUSY, "%s was held while it was given its absolute form; it stays for the next launch" % link)


def _unlink_link(link):
    """Remove a link (never its target): a folder link on Windows is removed as a folder."""
    if os.name == "nt" and _folder_link(link):
        os.rmdir(long_path(link))
    else:
        os.unlink(long_path(link))


def twin_of(link):
    return Path(link).with_name(RELINKING + Path(link).name)


def finish_relink(link):
    """After a crash mid-relink: a twin whose link is gone takes its name; one beside its link that reaches the same
    place is left over and goes. Anything else at the link's name (a folder an earlier version made since) never costs
    the twin, which may be the only thing still reaching the state: that raises, naming it. Only a twin that is a link
    is touched: anything else of that name is the person's."""
    twin = twin_of(link)
    if not is_link(twin):
        return
    if _info(link) is None:
        os.rename(long_path(twin), long_path(link))
    elif is_link(link) and (_same(twin, link) or _leftover(twin, link)):
        _unlink_link(twin)
    else:
        raise OSError(errno.EEXIST, f"{link} is not the link {twin.name} stands for; the twin is kept (it may be "
                                    "the only way to the state): move one of them aside, then open Angelo again")


def _leftover(twin, link):
    """A twin whose text is the absolute form of the link's own (both may dangle: a disk not attached): made for it."""
    try:
        own = _resolved(os.path.join(os.path.dirname(str(link)), plain_target(os.readlink(long_path(link)))))
        # Windows' readlink text has the \\?\ prefix: compared without it on both sides.
        return _key(plain_target(os.readlink(long_path(twin)))) == _key(plain_target(own))
    except OSError:
        return False


def _same(first, second):
    try:
        return os.path.samefile(long_path(first), long_path(second))
    except OSError:
        return False   # One of them dangles: not provably the same place.
