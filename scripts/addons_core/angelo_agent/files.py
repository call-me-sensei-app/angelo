# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scoped, bounded file tools with anchored effects and versioned recovery."""

from contextlib import ExitStack, contextmanager
import os
import stat
import sys
import uuid
from pathlib import Path

from . import anchored_windows, private_conventions, private_roots
from .private_conventions import subtree_mounts  # noqa: F401 - file tools' name for it
from .checkpoint_filesystem import (_file_info, _open_directory_at, _open_file, _private_directory,
    _replace_file, _unlink_file, assert_directory_identity, directory_info, entry_info,
    iter_names, publish_new_file, require_anchored_files, same_file, sync_directory)
from .file_transactions import journaled_file_effect

BLOCKED = {".git", ".ssh", ".aws", ".codex", ".claude", ".env", ".env.local", ".env.production"}
MAX_FILE = 20 * 1024 * 1024


def relative_path(relative):
    """Pure lexical admission, also used when reading durable journal metadata."""
    if not isinstance(relative, str) or len(relative) > 4096:
        raise ValueError("Use a bounded workspace-relative path")
    supplied = Path(relative or ".")
    # A Windows drive (``C:notes``) or root (``\\notes``) is never relative, though is_absolute() needs both.
    if (supplied.is_absolute() or supplied.drive or supplied.root or ".." in supplied.parts
            or any(part.startswith(".") and part != "." for part in supplied.parts)):
        raise ValueError("Use a non-hidden relative path inside this chat's file workspace")
    if any(part in BLOCKED for part in supplied.parts):
        raise ValueError("Credential and repository-internal paths aren't available to these file tools")
    return supplied


def file_signature(info):
    return info.st_size, info.st_mtime_ns, info.st_ino, info.st_dev


def optional_info(folder, name):
    try:
        info = _file_info(folder, name)
    except FileNotFoundError:
        return None
    if info.st_size > MAX_FILE:
        raise ValueError("Choose a regular file up to 20 MiB")
    return info


PRIVATE_MESSAGE = "Application profiles, credentials, transcripts and snapshots aren't file-tool inputs"


def folder_identity(info):
    """(device, inode) of a stat result, or None where the filesystem has no stable inode (then only the path
    comparison applies)."""
    inode = getattr(info, "st_ino", 0)
    return (info.st_dev, inode) if inode else None


def _canonical_path(descriptor):
    """The path the system has for an opened folder, as spelled on disk (macOS F_GETPATH, Linux /proc, Windows
    GetFinalPathNameByHandleW), or None."""
    try:
        if isinstance(descriptor, anchored_windows.WindowsDirectory):
            return Path(descriptor.final_path())
        if sys.platform == "darwin":
            import fcntl
            raw = fcntl.fcntl(descriptor, fcntl.F_GETPATH, bytes(1024))
            return Path(os.fsdecode(raw.split(b"\0", 1)[0]))
        if sys.platform.startswith("linux"):
            return Path(os.readlink("/proc/self/fd/%d" % descriptor))
    except (OSError, AttributeError, ValueError):
        pass
    return None


def _open_child(folder, name, create):
    """One verified child folder of an open folder: a POSIX descriptor, or a Windows handle (``anchored_windows``)."""
    if isinstance(folder, anchored_windows.WindowsDirectory):
        handle = anchored_windows._open_component(folder.api, folder.fd, name, create)
        return anchored_windows.WindowsDirectory(handle, folder.path / name, folder.api)
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=folder)
        except FileExistsError:
            pass
    return _open_directory_at(folder, name)


def _close(folder):
    os.close(folder.fd if isinstance(folder, anchored_windows.WindowsDirectory) else folder)


def _owner_token(folder=None, path=None):
    """The owner token inside an opened folder (a descriptor or a Windows handle) or at ``path``, or None."""
    # Never blocks and never follows: no wait on a FIFO, no link, only a regular file, a bounded read (R10-M-3).
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        if type(folder) is int:
            descriptor = os.open(private_roots.OWNER, flags, dir_fd=folder)
        else:
            target = (_canonical_path(folder) if folder is not None else None) or path
            if target is None:
                return None
            descriptor = os.open(Path(target) / private_roots.OWNER, flags)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                return None
            return os.read(descriptor, 64).decode("utf-8", "replace")
        finally:
            os.close(descriptor)
    except (OSError, ValueError):
        return None


def _member(prefixes, name):
    """Whether ``name`` (as on disk, else as requested) is a declared private family member's, by the system's own
    path rule (``os.path.normcase``: unchanged on POSIX)."""
    folded = os.path.normcase(name)
    return any(folded.startswith(os.path.normcase(prefix)) for prefix in prefixes)


def _identity(path):
    try:
        return folder_identity(os.stat(path))
    except (OSError, ValueError):
        return None


class Families(dict):
    def of(self, identity, path):
        """The prefixes of the families whose parent is this folder: by identity, else by its spelled path."""
        return ((self.get(identity) if identity is not None else None)
                or (self.get(("path", private_conventions.key(path))) if path is not None else None))


class FileWorkspace:
    def __init__(self, root, excluded=(), own=None):
        """``excluded``: every application-state folder (``private_roots.private_roots``), refused to every file tool
        wherever the workspace is, even when it lies inside one (break-it r2m4c F2). Compared by folder identity
        (device and inode), so a case or Unicode spelling of a private folder is still that folder (R2-M-2), and by
        path where a filesystem has no inode. The one exception is explicit: ``own``, the chat's default workspace,
        when this workspace is exactly that folder, which is a real folder (its own path no link or junction: a
        linked default workspace grants nothing, R2-M-3) and not itself private; then only the folders above it are
        not refused, never anything in it."""
        self.root = Path(root).resolve()
        # ``private_roots.Roots`` refreshes: each check consumes the registry as it is now (round 7 M-2).
        self.source = excluded if callable(getattr(excluded, "refresh", None)) else None
        self.initial = [Path(item).absolute() for item in excluded]
        self.excluded = list(self.initial)
        self.identity = _identity(self.root)   # Every walk starts at this very folder.
        self.known = {identity for identity in map(private_roots.hold, self.excluded) if identity is not None}
        self.convention = private_conventions.Convention()
        self.canonical = private_conventions.canonical(self.root)
        self.own = self._is_own(own)

    @property
    def lexical(self):
        return self._once("lexical", self._lexical_now)

    def _lexical_now(self):
        self._private()   # The registry as it is now.
        return [path for path in (item.resolve() for item in self.excluded)
                if not (self.own and self.root.is_relative_to(path))]

    def _current(self):
        """The registry's folders now (cached by the list file's identity and time), keeping every one seen."""
        if self.source is not None:
            fresh = [Path(item).absolute() for item in self.source.refresh()]
            self.excluded = self.initial + [item for item in fresh if item not in self.initial]
            self.known |= {identity for identity in map(private_roots.hold, fresh) if identity is not None}
        return self.excluded

    def _private(self):
        return self._once("private", self._private_now)

    def _private_now(self):
        """The identities of every private folder now: this workspace's, the registry's as it is now, and the
        process registry's (``private_roots.registered``: worker, headless and scratch folders, made later too)."""
        folders = self._current() + private_roots.registered()
        self.known -= {identity for identity in self.known if private_roots.alive(identity) is False}   # For good.
        return (set(self.known)   # R9-M-6, R10-M-1
                | {identity for identity in map(_identity, folders) if identity is not None})

    def _refuse_convention(self, path):
        """Refuse ``path`` (as the system spells it) when it is application state by the OS convention
        (``private_conventions``); in the chat's own default workspace only a profile-family folder below it counts."""
        if self.convention.private_path(path, below=self.canonical if self.own else None):
            raise ValueError(PRIVATE_MESSAGE)

    def _families(self):
        return self._once("families", self._families_now)

    @staticmethod
    def _families_now():
        """Each declared family's prefixes by its parent folder's identity, and by its path as the system spells it
        where the filesystem has no inode (``Families.of``; break-it r2m4c R8-M-6)."""
        found = Families()
        for parent, prefix in private_roots.families():
            for key in (_identity(parent), ("path", private_conventions.key(private_conventions.canonical(parent)))):
                if key is not None:
                    found.setdefault(key, []).append(prefix)
        return found

    def _refuse_member(self, folder, families):
        """Refuse ``folder`` or a folder above it that is a declared family's member (a process folder, crashed or
        not), by the names on disk."""
        for item in (folder, *folder.parents):
            prefixes = families.of(_identity(item.parent), item.parent) if item.parent != item else None
            if prefixes and _member(prefixes, item.name):
                raise ValueError(PRIVATE_MESSAGE)

    def folder(self, path, create=False):
        """The one admission for opening a workspace folder (``_folder``): every reader and writer that opens a folder
        it got from ``path()`` (the .blend metadata reader, Studio exports) uses it (break-it r2m4c R8-M-5)."""
        return self._folder(path, create=create)

    @contextmanager
    def _folder(self, path, create=False):
        """Open the workspace folder ``path`` one level at a time from the workspace, refusing every folder actually
        opened on the way that is private (by identity), a process-family member, or a bind mount of a subfolder
        (``subtree_mounts``): whatever path reached it, even one changed after ``path()`` admitted it (break-it r2m4c
        round 3 M-2). The same walk on POSIX descriptors and on Windows' verified handles (round 4 M-3: a handle's
        ``fstat`` is its volume serial and file index); the workspace opened must be the one set up."""
        with ExitStack() as stack:
            current = stack.enter_context(_private_directory(self.root, within=self.root))
            if not (type(current) is int or isinstance(current, anchored_windows.WindowsDirectory)):
                # No descriptor-relative opens on this platform: the folder opened is checked.
                self._refuse_member(path, self._families())
                self._refuse_convention(path)
                with _private_directory(path, create=create, within=self.root) as folder:
                    self._refuse_opened(directory_info(folder), folder)
                    yield folder
                return
            private, mounts, families = self._private(), subtree_mounts(), self._families()
            owned = False   # The workspace's own descriptor is closed by its context; each child here.
            try:
                opened = folder_identity(directory_info(current))
                if self._is_private(opened, private, folder=current):
                    raise ValueError(PRIVATE_MESSAGE)
                if self.identity is not None and opened is not None and opened != self.identity:
                    raise ValueError("The selected workspace folder was replaced; reopen the project or chat")
                if families:
                    self._refuse_member(_canonical_path(current) or self.root, families)
                lexical = self.root
                for part in path.relative_to(self.root).parts:
                    lexical = lexical / part
                    parent = families.of(folder_identity(directory_info(current)),
                                         _canonical_path(current) or lexical.parent)
                    child = _open_child(current, part, create)
                    if owned:
                        _close(current)
                    current, owned = child, True
                    if self._is_private(folder_identity(directory_info(current)), private, folder=current) \
                            or str(lexical) in mounts:
                        raise ValueError(PRIVATE_MESSAGE)
                    if parent and _member(parent, (_canonical_path(current) or lexical).name):
                        raise ValueError(PRIVATE_MESSAGE)
                # The convention, on the path the system has for the folder actually opened: it holds every folder
                # above it, as they really are (an exempt store inside ~/Library is reached through ~/Library).
                self._refuse_convention(_canonical_path(current) or lexical)
                yield current
            finally:
                if owned:
                    _close(current)

    def _is_own(self, own):
        if own is None:
            return False
        own = Path(own).absolute()
        if own.is_symlink() or own.is_junction():
            return False
        identity = _identity(own)
        return identity is not None and identity == _identity(self.root) and identity not in self._private()

    def _refuse_private(self, path):
        """Refuse ``path`` when it, a folder between it and the workspace, the workspace, or (unless this is the
        chat's own workspace) a folder above the workspace is an application-state folder."""
        if any(path.is_relative_to(private) for private in self.lexical):
            raise ValueError(PRIVATE_MESSAGE)
        private = self._private()
        below = [self.root]
        for part in path.relative_to(self.root).parts:
            below.append(below[-1] / part)
        for folder in ([] if self.own else list(self.root.parents)) + below:
            identity = _identity(folder)
            if self._is_private(identity, private, path=folder):
                raise ValueError(PRIVATE_MESSAGE)
            if identity is None and folder.is_relative_to(self.root):
                break   # Not there yet (a new file): nothing below it exists either.

    def _refuse_opened(self, info, folder=None):
        """The folder actually opened is never an application-state folder (whatever spelling reached it)."""
        identity = folder_identity(info)
        if identity is not None and self._is_private(identity, self._private(), folder=folder):
            raise ValueError(PRIVATE_MESSAGE)

    @staticmethod
    def _is_private(identity, private, folder=None, path=None):
        """``identity`` is a private folder's: a registered root's; a folder Angelo allocated, while it exists wherever it
        was moved, whatever happened to its owner file (held handle: a known allocation missing its evidence reads
        private, R9-M-1); or any folder holding an owner file (read from the opened ``folder`` or at ``path``), so an
        earlier process's allocation is found after a restart."""
        if identity is None:
            return False
        if identity in private:
            return True
        if private_roots.owner_token(identity) is not None and private_roots.alive(identity) is not False:
            return True
        return _owner_token(folder, path) is not None

    def path(self, relative):
        lexical = self.root / relative_path(relative)
        # A Windows junction is not a symlink to Path.is_symlink(); both are links here.
        if any(part.is_symlink() or part.is_junction() for part in (lexical, *lexical.parents)
               if part.is_relative_to(self.root)):
            raise ValueError("File tools cannot follow workspace links")
        path = lexical.resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("File tools cannot follow a link outside the selected workspace")
        self._refuse_private(path)
        self._refuse_convention(path)
        relative_path(str(path.relative_to(self.root)))
        return path

    def precondition(self, name, arguments):
        path = self.path(arguments["path"])
        require_anchored_files()
        if name == "angelo_list_files":
            with self._folder(path) as folder:
                info = directory_info(folder)
                return "directory", info.st_mtime_ns, info.st_ino, info.st_dev
        try:
            with self._folder(path.parent) as folder:
                info = optional_info(folder, path.name)
        except FileNotFoundError:
            info = None
        return file_signature(info) if info is not None else ("missing",)

    def _list(self, path):
        entries = []
        mounts = subtree_mounts()
        with self._folder(path) as folder:
            private = self._private()
            lexical, spelled = self.lexical, _canonical_path(folder) or path
            prefixes = self._families().of(folder_identity(directory_info(folder)), spelled)
            below = self.canonical if self.own else None
            # Stream directory admission before sorting, rather than allocating
            # an unbounded list merely to return at most 200 entries.
            names = []
            for entry in iter_names(folder):
                if len(names) == 4096:
                    raise ValueError("Choose a workspace directory with at most 4096 entries")
                names.append(entry)
            for name in sorted(names, key=str.lower):
                if name.startswith("."):
                    continue
                info = entry_info(folder, name)
                if (stat.S_ISLNK(info.st_mode) or self._is_private(folder_identity(info), private, path=path / name)
                        or str(path / name) in mounts
                        or (prefixes and _member(prefixes, name))
                        or self.convention.private_path(spelled / name, below=below)
                        or any((path / name).is_relative_to(item) for item in lexical)):
                    continue
                entries.append({"name": name, "directory": stat.S_ISDIR(info.st_mode),
                                "bytes": info.st_size if stat.S_ISREG(info.st_mode) else None})
                if len(entries) == 200:
                    break
        return {"path": str(path.relative_to(self.root)), "entries": entries, "limit": 200}

    def _read(self, path, arguments):
        if (type(arguments["start"]) is not int or not 0 <= arguments["start"] <= 2**53 - 1 or
                type(arguments["length"]) is not int or not 0 <= arguments["length"] <= MAX_FILE):
            raise ValueError("File excerpts require bounded non-negative whole-number offsets/lengths")
        self.precondition("angelo_read_file", arguments)
        with self._folder(path.parent) as folder:
            info = optional_info(folder, path.name)
            if info is None:
                raise ValueError("The file doesn't exist")
            with os.fdopen(_open_file(folder, path.name, os.O_RDONLY), "rb") as handle:
                opened = os.fstat(handle.fileno())
                if not same_file(info, opened) or opened.st_nlink != 1 or opened.st_size > MAX_FILE:
                    raise ValueError("The workspace file changed while opening it")
                handle.seek(arguments["start"])
                data = handle.read(arguments["length"])
                if file_signature(os.fstat(handle.fileno())) != file_signature(opened):
                    raise ValueError("The workspace file changed while reading it")
        try:
            value = data.decode("utf-8")
        except UnicodeError as error:
            raise ValueError("This file isn't UTF-8 text. Attach it as an asset/image or use approved Blender import code.") from error
        return {"path": arguments["path"], "text": value, "bytes": opened.st_size, "start": arguments["start"]}

    def execute(self, name, arguments, checkpoint_store=None, checkpoint=None):
        """One file-tool operation; the private set is gathered once for it (``_memo``), then read again next time."""
        self._memo = {}
        try:
            return self._execute(name, arguments, checkpoint_store, checkpoint)
        finally:
            self._memo = None

    def _once(self, name, compute):
        memo = getattr(self, "_memo", None)
        if memo is None:
            return compute()
        if name not in memo:
            memo[name] = compute()
        return memo[name]

    def _execute(self, name, arguments, checkpoint_store=None, checkpoint=None):
        require_anchored_files()
        path = self.path(arguments["path"])
        if name == "angelo_list_files":
            return self._list(path)
        if name == "angelo_read_file":
            return self._read(path, arguments)
        if name not in {"angelo_write_file", "angelo_delete_file"}:
            raise ValueError("Unknown file tool")
        if name == "angelo_write_file" and (type(arguments.get("content")) is not str or
                len(arguments["content"].encode("utf-8")) > MAX_FILE):
            raise ValueError("File writes require UTF-8 text up to 20 MiB")
        if not checkpoint_store or not checkpoint:
            raise ValueError("File mutations require a request snapshot and journal")
        before = self.precondition(name, arguments)
        relative = str(relative_path(arguments["path"]))
        with journaled_file_effect(checkpoint_store, checkpoint, self.root, relative) as effect:
            with self._folder(path.parent, create=name == "angelo_write_file") as folder:
                assert_directory_identity(path.parent, folder)
                info = optional_info(folder, path.name)
                if (file_signature(info) if info else ("missing",)) != before:
                    raise ValueError("The workspace file changed before publication; no effect was applied")
                if name == "angelo_delete_file":
                    if info is None:
                        raise ValueError("The file doesn't exist")
                    try:
                        _unlink_file(folder, path.name)
                    except (OSError, ValueError):
                        try:
                            after = optional_info(folder, path.name)
                        except (OSError, ValueError):
                            effect.uncertain = True
                        else:
                            effect.applied = after is None
                            effect.uncertain = after is not None and (not same_file(info, after) or
                                file_signature(info) != file_signature(after) or info.st_ctime_ns != after.st_ctime_ns)
                        raise
                    effect.applied = True
                    result = {"deleted": True, "file_changes": {"written": [], "deleted": [arguments["path"]]}}
                else:
                    result = self._write(folder, path, arguments, info, effect)
                sync_directory(folder)
        return dict(result, success=True, path=arguments["path"], journaled=True, blender_undoable=False)

    def _write(self, folder, path, arguments, before, effect):
        content = arguments["content"].encode("utf-8")
        if len(content) > MAX_FILE:
            raise ValueError("File writes must be at most 20 MiB")
        temporary = path.name + ".angelo-" + str(uuid.uuid4()) + ".tmp"
        descriptor = _open_file(folder, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        created = os.fstat(descriptor)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            assert_directory_identity(path.parent, folder)
            current = optional_info(folder, path.name)
            if (file_signature(current) if current else None) != (file_signature(before) if before else None):
                raise ValueError("The workspace output changed during writing; no overwrite was performed")
            try:
                if before is None:
                    publish_new_file(folder, temporary, path.name)
                else:
                    _replace_file(folder, temporary, path.name)
                effect.applied = True
            except (OSError, ValueError):
                try:
                    after = entry_info(folder, path.name)
                    effect.applied = same_file(created, after)
                    if before is not None and not effect.applied:
                        effect.uncertain = not same_file(before, after) or file_signature(before) != file_signature(after)
                except FileNotFoundError:
                    effect.uncertain = before is not None
                except (OSError, ValueError):
                    effect.uncertain = True
                raise
        finally:
            try:
                if same_file(created, entry_info(folder, temporary)):
                    _unlink_file(folder, temporary)
            except FileNotFoundError:
                pass
        return {"bytes": len(content), "file_changes": {"written": [arguments["path"]], "deleted": []}}
