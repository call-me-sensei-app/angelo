# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unpack a source send into the build machine's tree: exact names, nothing outside it (source_tree.py extract).

* Names stay their exact UTF-8 paths (Python's tarfile; Windows tar.exe decoded them through the OEM code page and
  wrote garbled twins), and keep the commit's spelling: on a case-insensitive file system an existing file or
  folder whose name differs only in letter case is renamed first (NTFS and APFS keep the old spelling otherwise).
* Only what the archive changes is written, and stamped with the current time: an unchanged file keeps its time
  (the incremental build recompiles nothing for it), and a changed one is always newer than the last build, even
  when an older commit is sent again (git archive stamps every member with its commit's time).
* A path whose type the commit changed is made way for inside the tree: a file where a folder must go, or a folder
  (with what is left in it: untracked or ignored files) where a file must go, is removed. A folder holding a git
  repository is never removed.
* Nothing is written outside the tree: absolute or ``..`` names, ``:`` (an NTFS stream), ``.git`` and link members
  are refused, and an existing link (a symbolic link or a Windows junction) in the way, or on the way to a listed
  deletion, is itself removed, never written or deleted through. tarfile's "data" filter guards the rest.

Standard library only; Python 3.12 or newer (tarfile filters).
"""

import os
import shutil
import stat
import sys
import tarfile
import uuid
from pathlib import Path, PurePosixPath


class SourceTreeError(Exception):
    pass


def shown(names, limit=8):
    """Names for a log line: ASCII only (the machine's console code page cannot print every name), at most ``limit``."""
    text = ", ".join(name.encode("ascii", "backslashreplace").decode("ascii") for name in names[:limit])
    return text + (", ..." if len(names) > limit else "")


def lexical(tree, name):
    """``tree / name`` for a /-separated relative name that cannot leave ``tree`` or touch its repository."""
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or ".." in path.parts or "\\" in name or any(":" in part for part in path.parts)
            or name.startswith('"') or any(part.lower() == ".git" for part in path.parts)):
        # A leading quote is git's C quoting, which core.quotePath=false keeps only for names holding a quote, a
        # backslash or a control character: none of them can be a Windows file name.
        raise SourceTreeError("%s is not a relative path inside the tree" % shown([name]))
    return Path(tree, *path.parts)


def is_link(path):
    """A symbolic link or a Windows junction (os.path.islink does not see a junction)."""
    return os.path.islink(path) or os.path.isjunction(path)


def remove_link(path):
    """Remove the link itself, never what it points to (a junction is a folder entry, removed as one)."""
    if os.path.isjunction(path):
        os.rmdir(path)
    else:
        _unlink(path)


def inside(tree, name):
    """``lexical`` and, besides, no folder on the way is a link: nothing reached through it can leave the tree."""
    path = lexical(tree, name)
    folder = Path(tree)
    for part in path.relative_to(tree).parts[:-1]:
        folder = folder / part
        if is_link(folder):
            raise SourceTreeError("%s is reached through the link %s" % (shown([name]), shown([str(folder)])))
    return path


def _unlink(path):
    try:
        os.unlink(path)
    except PermissionError:   # A read-only file on Windows.
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        os.unlink(path)


def _remove_folder(path):
    if any(part.name == ".git" for part in Path(path).rglob(".git")):   # rglob follows no link, nor rmtree
        raise SourceTreeError("%s holds a git repository; it is not removed to make way for a file" % shown([str(path)]))
    shutil.rmtree(path, onexc=lambda function, name, _error: (os.chmod(name, stat.S_IWRITE), function(name)))


def _prune(folder, tree):
    """Remove ``folder`` and its parents below ``tree`` while they are empty (git tracks no folders)."""
    tree = Path(tree).resolve()
    folder = Path(folder).resolve()
    while folder != tree and tree in folder.parents:
        try:
            folder.rmdir()
        except OSError:
            return
        folder = folder.parent


def remove_listed(tree, listing):
    """Remove the files ``listing`` names; returns how many were there. A folder or a missing path is left alone.

    A link on the way (a stray one: a commit holds none) is removed itself, which removes the listed path from the
    tree too; nothing is deleted through it.
    """
    removed = 0
    for line in Path(listing).read_bytes().decode("utf-8").split("\n"):
        if not line:
            continue
        target = lexical(tree, line)
        folder = Path(tree)
        for part in target.relative_to(tree).parts[:-1]:
            folder = folder / part
            if is_link(folder):
                remove_link(folder)
                break
        else:
            if is_link(target) or target.is_file():
                remove_link(target)
                _prune(target.parent, tree)
                removed += 1
    return removed


class Speller:
    """Gives each path below ``tree`` the exact spelling it is asked for, where the file system ignores case."""

    def __init__(self, tree):
        self.tree, self.listed = Path(tree), {}

    def names(self, folder):
        if folder not in self.listed:
            self.listed[folder] = set(os.listdir(folder)) if folder.is_dir() else set()
        return self.listed[folder]

    def misspelled(self, name):
        """``(folder, on disk, wanted)`` for the first component of ``name`` spelled otherwise on disk, or None."""
        folder = self.tree
        for part in PurePosixPath(name).parts:
            names = self.names(folder)
            if part not in names:
                variants = [entry for entry in names if entry.casefold() == part.casefold()]
                # The wanted spelling resolves although it is not listed: a case-insensitive file system.
                if variants and os.path.lexists(folder / part):
                    return folder, variants[0], part
                return None
            folder = folder / part
        return None

    def fix(self, name):
        """Rename what stands at ``name`` in another letter case to the wanted spelling; returns how many renames."""
        renamed = 0
        while True:
            found = self.misspelled(name)
            if not found:
                return renamed
            folder, on_disk, wanted = found
            temporary = folder / (".rename-%s" % uuid.uuid4().hex)
            os.rename(folder / on_disk, temporary)
            os.rename(temporary, folder / wanted)
            self.listed[folder] = (self.listed[folder] - {on_disk}) | {wanted}
            self.listed = {key: value for key, value in self.listed.items()
                           if key == folder or folder / on_disk not in [key, *key.parents]}
            renamed += 1


def _same(tar, member, path):
    """True when ``path`` is a plain file holding exactly ``member``'s bytes (and, off Windows, its executable bit)."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_size != member.size or info.st_nlink > 1:
        return False
    if os.name != "nt" and bool(info.st_mode & 0o100) != bool(member.mode & 0o100):
        return False
    with tar.extractfile(member) as packed, open(path, "rb") as present:
        while True:
            block = packed.read(1 << 20)
            if block != present.read(len(block) or 1):
                return False
            if not block:
                return True


def _make_way(tree, path, member, cleared):
    """Remove what stands where ``member`` must go: a non-folder on its way, or a folder/file of the other type."""
    folder = Path(tree)
    for part in path.relative_to(tree).parts[:-1]:
        folder = folder / part
        if folder in cleared:
            continue
        if is_link(folder) or (os.path.lexists(folder) and not folder.is_dir()):
            remove_link(folder)
        cleared.add(folder)
    if is_link(path):
        remove_link(path)
    elif member.isfile() and path.is_dir():
        _remove_folder(path)
    elif member.isdir() and os.path.lexists(path) and not path.is_dir():
        _unlink(path)


def _admit(member):
    if not (member.isfile() or member.isdir()):
        raise SourceTreeError("%s is a %s, not a file or folder: refused" % (
            shown([member.name]), "link" if member.issym() or member.islnk() else "special member"))


def extract(tree, archives, listing=None):
    """Remove what ``listing`` names, then extract ``archives`` in order into ``tree`` (see the module notes)."""
    if not hasattr(tarfile, "data_filter"):
        raise SourceTreeError("Python %s has no tarfile extraction filters: use 3.12 or newer" % sys.version.split()[0])
    tree = Path(tree)
    tree.mkdir(parents=True, exist_ok=True)
    removed = remove_listed(tree, listing) if listing else 0
    report = []
    for archive in archives:
        try:
            # Names are UTF-8 (git archive writes the bytes git stores; pax headers are UTF-8 by definition). Other
            # pax records may be binary (the Mac's tar adds extended attributes such as com.apple.provenance), so
            # undecodable bytes are kept as they are: a name that is not UTF-8 then fails to be created on Windows
            # rather than arriving under another name.
            with tarfile.open(archive, "r:*", encoding="utf-8", errors="surrogateescape") as tar:
                members = tar.getmembers()
                speller, cleared, write = Speller(tree), set(), []
                for member in members:
                    _admit(member)
                    path = lexical(tree, member.name)
                    _make_way(tree, path, member, cleared)
                    speller.fix(member.name)
                    inside(tree, member.name)
                    if member.isdir() or not _same(tar, member, path):
                        if member.isfile() and os.path.lexists(path):
                            _unlink(path)   # never written through: a link, a read-only or another file's inode
                        write.append(member)
                tar.extractall(tree, members=write, filter="data")
        except (tarfile.TarError, UnicodeError, OSError) as error:
            raise SourceTreeError("%s could not be extracted: %s" % (Path(archive).name, error)) from error
        changed = [member.name for member in write if member.isfile()]
        for name in changed:
            os.utime(lexical(tree, name))   # newer than any build output, whatever the commit's own date
        files = [member.name for member in members if member.isfile()]
        missing = [name for name in files if not os.path.lexists(lexical(tree, name))]
        if missing:
            raise SourceTreeError("%s: %d file(s) missing after extraction: %s" % (
                Path(archive).name, len(missing), shown(missing)))
        report.append("%s: %d files, %d written" % (Path(archive).name, len(files), len(changed)))
    print("extract: %s; %d listed file(s) removed first" % ("; ".join(report) or "no archive", removed))
