# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The renames of one carry-over (``version_carry_over``) and how a clash with state already in the new folder ends.

Nothing is ever deleted that is not proven to be in the new folder: an entry the new side lacks is renamed into
place (one rename moves a whole folder); a file the new side holds with the same bytes is that copy, so the new
side's name is replaced by the original (keeping the original's identity: its inode, links and seals); every other
clash keeps one side in place and moves the other, whole, into ``kept-from-<version>`` inside the new state folder.

Which side stays (``decide``), from content and age, never from which folder it is in:

* a file that is a byte prefix of the other one is the other's interrupted copy or an older state of the same
  append-only journal, so the longer one stays;
* otherwise the one modified last stays (an earlier version used again after a copy, or the newer version used
  after it).

A file written after the first attempt of this move (``fresh_since``) is a default the failed launch wrote, never
the person's: the original stays. Bringing back chats made in an earlier version after the move (``prefer_new``) never
replaces anything here: the other side of every clash is kept.

**The snapshot system is one unit** (``SNAPSHOT_UNIT``): its store's index, packs, records, timeline and seal only
work together. When both sides hold one and the new one is a copy of the old (every file there holds the start of,
or all of, the old file at its path), merging file by file gives the old unit back exactly; otherwise the unit that
holds the other (its continuation), else the one with more history, stays whole and the other is kept aside whole,
so no snapshot ever mixes two stores. How the units end is decided once, before the first rename
(``unit_plan``), so a resumed move finishes that decision instead of taking its own half for a rival; what a session
or the earlier version started during a stop goes aside (``_split_unit``), and a capture made then does not restore on
its own. A unit moved across volumes gets new file identities, so captures sealed on
them no longer verify there (a known limit of linking a version folder to another disk).

**Across volumes** (a version folder linked to another disk), where a rename cannot work, an entry is copied to a
temporary name beside its target, verified byte for byte, renamed into place and only then removed from the old
folder; an interrupted copy is discarded and made again. A junction, at the top or inside a copied folder, is made
again as a junction naming the same folder (``carry_over_links.copy_junction``), never copied as its folder's content,
and removed from the old folder as a link; a disk mounted at a folder is refused before anything is copied.

Every filesystem call goes through ``long_path``, so on Windows no path meets ``MAX_PATH``; links are never
followed (``os.lstat``). Before each rename, links whose meaning it would change are pinned (``carry_over_links``,
``Mover.plan``). Standard library only.
"""

import errno
import os
import shutil
import stat
import time
from pathlib import Path

from . import carry_over_links as links, folder_moves, shared_replace

SNAPSHOT_UNIT = frozenset({"snapshot-store", "checkpoints", "checkpoint-timeline.json", "checkpoint-seal",
                           "checkpoint-captures", "checkpoint-scenes"})
CARRYING = ".carrying-"   # The temporary name of a cross-volume copy, beside its target.
CHUNK = 1 << 20
SHARING_RETRIES = (0.1, 0.3, 0.9)   # Seconds; Windows: an antivirus scan or an index may hold a file for a moment.


def long_path(path):
    """``path`` in Windows' extended-length form, which ignores ``MAX_PATH``; unchanged elsewhere."""
    if os.name != "nt":
        return str(path)
    text = os.path.abspath(str(path))
    if text.startswith("\\\\?\\"):
        return text
    return "\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text


def info(path):
    """``os.lstat`` of ``path`` or None when nothing is there (a link is described, never followed)."""
    try:
        return os.lstat(long_path(path))
    except FileNotFoundError:
        return None


def is_real_dir(path):
    """A folder of its own: never a link, nor a junction (Windows' lstat calls one a folder; it is a link to merge
    as one, never a folder to walk into or empty)."""
    found = info(path)
    return found is not None and stat.S_ISDIR(found.st_mode) and not links.is_junction(path)


def entries(path):
    return sorted(os.listdir(long_path(path)))


def _files(root):
    """``{relative path: lstat}`` of every non-directory entry under ``root`` (``root`` itself when it is one)."""
    found, top = {}, info(root)
    if top is None:
        return found
    if not stat.S_ISDIR(top.st_mode) or links.is_junction(root):   # A junction is one entry, never walked into.
        return {(): top}
    for name in entries(root):
        for relative, value in _files(Path(root) / name).items():
            found[(name,) + relative] = value
    return found


def _compare(first, second):
    """``"same"``, ``"first-prefix"`` (the first is the start of the second), ``"second-prefix"`` or
    ``"different"`` for two regular files' bytes."""
    with open(long_path(first), "rb") as a, open(long_path(second), "rb") as b:
        sizes = os.fstat(a.fileno()).st_size, os.fstat(b.fileno()).st_size
        left = min(sizes)
        while left > 0:
            block = min(CHUNK, left)
            if a.read(block) != b.read(block):
                return "different"
            left -= block
    if sizes[0] == sizes[1]:
        return "same"
    return "first-prefix" if sizes[0] < sizes[1] else "second-prefix"


def decide(source, target, fresh_since=None):
    """Which of two clashing entries stays: ``"same"`` (the target is a copy), ``"source"`` or ``"target"``."""
    old, new = info(source), info(target)
    if stat.S_ISREG(old.st_mode) and stat.S_ISREG(new.st_mode):
        relation = _compare(source, target)
        if relation == "same":
            return "same"
        if relation == "second-prefix":
            return "source"   # The new side holds the start of the old file: an interrupted copy, an older journal.
        if relation == "first-prefix":
            return "target"
    if stat.S_ISLNK(old.st_mode) and stat.S_ISLNK(new.st_mode) and os.readlink(long_path(source)) == os.readlink(
            long_path(target)):
        return "same"
    if fresh_since is not None and new.st_mtime_ns >= fresh_since * 1e9 > old.st_mtime_ns:
        return "source"
    return "source" if old.st_mtime_ns > new.st_mtime_ns else "target"


def _contains(bigger, smaller, places):
    """Whether every file of ``smaller`` is, at the same path in ``bigger``, the same file or its start (a copy of
    it, finished or not, or an earlier state of an append-only file). Same size and time count as the same file."""
    for name, value in smaller.items():
        other = bigger.get(name)
        if other is None or not (stat.S_ISREG(value.st_mode) and stat.S_ISREG(other.st_mode)) \
                or value.st_size > other.st_size:
            return False
        if value.st_size == other.st_size and value.st_mtime_ns == other.st_mtime_ns:
            continue
        if _compare(places(bigger, name), places(smaller, name)) not in {"same", "second-prefix"}:
            return False
    return True


def unit_winner(source_units, target_units):
    """How the snapshot units of both sides end: ``"copy"`` when the new one is held in the old one (a copy of it,
    finished or not: merging file by file gives the old unit back exactly); otherwise which unit stays whole,
    ``"source"`` or ``"target"``: the one that holds the other (its continuation), else the one with more history
    (bytes), so a store started afresh never hides a person's whole history. The other is kept, whole."""
    old = {(path.name,) + relative: value for path in source_units for relative, value in _files(path).items()}
    new = {(path.name,) + relative: value for path in target_units for relative, value in _files(path).items()}
    roots = {id(old): source_units, id(new): target_units}

    def place(files, name):
        return next(path for path in roots[id(files)] if path.name == name[0]).joinpath(*name[1:])
    if _contains(old, new, place):
        return "copy"
    if _contains(new, old, place):
        return "target"
    size = lambda files: sum(value.st_size for value in files.values())  # noqa: E731
    return "source" if size(old) >= size(new) else "target"


def unit_plan(source, current, names):
    """How the snapshot units end: ``"move"`` when only one side has one, else ``unit_winner``'s answer."""
    unit = [source / name for name in names if name in SNAPSHOT_UNIT]
    theirs = [current / name for name in sorted(SNAPSHOT_UNIT) if info(current / name) is not None]
    return unit_winner(unit, theirs) if unit and theirs else "move"


class Mover:
    """One carry-over's renames; ``moved``, ``copies`` and ``kept`` count what happened. ``unit``: ``unit_plan``'s
    answer, recorded before the first rename; ``unit_settled()`` runs once its side is out of the way."""

    def __init__(self, kept_root, allow_copy=True, *, unit="move", unit_settled=None, fresh_since=None,
                 prefer_new=False):
        self.kept_root, self.allow_copy = Path(kept_root), allow_copy
        self.unit, self.unit_settled, self.fresh_since, self.prefer_new = unit, unit_settled, fresh_since, prefer_new
        self.moved = self.copies = self.kept = 0
        self.kept_at = {}   # Top-level name -> where this carry-over kept it.
        self.plan = None    # carry_over_links.Plan: the whole carry-over, set before the first rename.

    def merge(self, source, target, relative):
        """Move ``source`` to ``target``; when ``target`` exists, merge folders, else keep one side (``decide``)."""
        if info(target) is None:
            os.makedirs(long_path(target.parent), mode=0o700, exist_ok=True)
            self.rename(source, target)
            self.moved += 1
        elif is_real_dir(source) and is_real_dir(target):
            for name in entries(source):
                self.merge(source / name, target / name, relative + (name,))
            folder_moves.remove_if_empty(Path(long_path(source)))
        else:
            winner = decide(source, target, self.fresh_since)
            if winner == "source" and self.prefer_new:
                winner = "target"
            if winner == "same":
                self.replace_copy(source, target)
            elif winner == "source":
                self.keep(target, relative or (target.name,))
                self.rename(source, target)
                self.moved += 1
            else:
                self.keep(source, relative or (source.name,))

    def merge_top(self, source, current, names):
        """Merge the top-level ``names`` of ``source`` into ``current``, the snapshot system as one unit."""
        if self.unit == "source":
            for name in sorted(SNAPSHOT_UNIT):
                if info(current / name) is not None:
                    self.keep(current / name, (name,))
            if self.unit_settled is not None:
                self.unit_settled()   # From here on a unit file in the new folder is the one that moved.
        elif self.unit == "copy":
            pass   # Merged file by file: the old unit comes back exactly.
        elif self.unit == "target":
            for name in names:
                if name in SNAPSHOT_UNIT and info(source / name) is not None:
                    self.keep(source / name, (name,))
            names = [name for name in names if name not in SNAPSHOT_UNIT]
        else:
            names = self._split_unit(source, current, names)
        for name in sorted(names, key=lambda item: item not in SNAPSHOT_UNIT):   # The unit first: few renames apart.
            if info(source / name) is not None:
                self.merge(source / name, current / name, (name,))

    def _split_unit(self, source, current, names):
        """After a stop, a unit entry on both sides was started by whoever ran meanwhile; each entry moves whole. The
        store and its records are each decided once, as a group: the side with more history stays, so the history
        that moved never leaves, and the other side's start goes aside."""
        left = [name for name in names if name not in SNAPSHOT_UNIT]
        store, records = {"snapshot-store"}, SNAPSHOT_UNIT - {"snapshot-store"}
        for group in (records, store):
            mine = [name for name in names if name in group]

            def size(root):
                return sum(value.st_size for name in sorted(group) for value in _files(root / name).values())
            clashing = [name for name in mine if info(source / name) is not None and info(current / name) is not None]
            source_stays = bool(clashing) and size(source) > size(current)
            for name in clashing:
                self.keep(*((current / name, (name,)) if source_stays else (source / name, (name,))))
            left += [name for name in mine if name not in clashing or source_stays]
        return left

    def replace_copy(self, source, target):
        """The target is a copy of the source: the original takes its name (identity, links and seals kept)."""
        if (os.path.samestat(os.lstat(long_path(source)), os.lstat(long_path(target)))
                and not os.path.samefile(long_path(Path(source).parent), long_path(Path(target).parent))):
            os.unlink(long_path(source))   # One file under two names: a move stopped between link and unlink.
            return
        try:
            shared_replace.replace(long_path(source), long_path(target))   # Retried while a reader holds it.
        except OSError as error:
            if error.errno != errno.EXDEV:
                raise
            os.unlink(long_path(source))   # The same bytes are already on the new volume.
        self.copies += 1

    def keep(self, path, relative):
        aside, number = self.kept_root.joinpath(*relative), 1
        while info(aside) is not None:
            number += 1
            aside = aside.with_name("%s (%d)" % (relative[-1], number))
        os.makedirs(long_path(aside.parent), mode=0o700, exist_ok=True)
        self.rename(path, aside)
        self.kept += 1
        self.kept_at.setdefault(relative[0], aside)

    def rename(self, source, target):
        if self.plan is not None:   # Links the rename would change the meaning of (carry_over_links).
            links.pin_links(source, target, self.plan)
        for delay in SHARING_RETRIES + (None,):
            try:
                folder_moves.move_entry(Path(long_path(source)), Path(long_path(target)))
                return
            except PermissionError:
                if os.name != "nt" or delay is None:
                    raise
                time.sleep(delay)
            except OSError as error:
                if error.errno != errno.EXDEV or not self.allow_copy:
                    raise
                self.copy_across(source, target)
                return

    def copy_across(self, source, target):
        """A move to another volume: copied beside the target, verified, renamed into place, then removed."""
        temporary = target.with_name(CARRYING + target.name)
        if info(temporary) is not None:   # An interrupted earlier copy of this same entry: made again.
            shutil.rmtree(long_path(temporary)) if is_real_dir(temporary) else links._unlink_link(temporary)
        if links.is_junction(source):   # A junction moves as itself: a new one naming the same folder, then the old.
            links.copy_junction(source, temporary)   # Verified to reach the same folder first.
            os.rename(long_path(temporary), long_path(target))
            links._unlink_link(source)
            return
        if is_real_dir(source):
            # A junction inside is made again as a junction (copy_junction), never copied as its folder's content.
            junctions = [path for path in links._links(source) if links.is_junction(path)]
            links.refuse_mounted(junctions)   # Before anything is copied: a mounted disk cannot be made again.
            shutil.copytree(long_path(source), long_path(temporary), symlinks=True, ignore=lambda folder, names: [
                name for name in names if links.is_junction(Path(folder) / name)])
            for junction in junctions:
                links.copy_junction(junction, Path(temporary).joinpath(*junction.relative_to(source).parts))
        else:
            shutil.copy2(long_path(source), long_path(temporary), follow_symlinks=False)
        copied, original = _files(temporary), _files(source)
        if set(copied) != set(original) or any(
                stat.S_ISREG(value.st_mode) and _compare(Path(source).joinpath(*name), Path(temporary).joinpath(*name))
                != "same" for name, value in original.items()):
            raise OSError(errno.EIO, "A copy to the other disk did not match %s" % source)
        os.rename(long_path(temporary), long_path(target))
        now = {name: (value.st_size, value.st_mtime_ns) for name, value in _files(source).items()}
        if now != {name: (value.st_size, value.st_mtime_ns) for name, value in original.items()}:
            raise OSError(errno.EBUSY, "%s changed while it was copied; it stays for the next launch" % source)
        if is_real_dir(source):
            for junction in [path for path in links._links(source) if links.is_junction(path)]:
                links._unlink_link(junction)   # Removed as links first: the folders they name are not this copy's.
            shutil.rmtree(long_path(source))
        else:
            os.unlink(long_path(source))
