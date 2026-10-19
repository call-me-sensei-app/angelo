# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scoped, bounded file tools with anchored effects and versioned recovery."""

import os
import stat
import uuid
from pathlib import Path

from .checkpoint_filesystem import (_file_info, _open_file, _private_directory,
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


class FileWorkspace:
    def __init__(self, root, excluded=()):
        self.root = Path(root).resolve()
        self.excluded = [Path(path).resolve() for path in excluded if not self.root.is_relative_to(Path(path).resolve())]

    def path(self, relative):
        lexical = self.root / relative_path(relative)
        # A Windows junction is not a symlink to Path.is_symlink(); both are links here.
        if any(part.is_symlink() or part.is_junction() for part in (lexical, *lexical.parents)
               if part.is_relative_to(self.root)):
            raise ValueError("File tools cannot follow workspace links")
        path = lexical.resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("File tools cannot follow a link outside the selected workspace")
        if any(path.is_relative_to(private) for private in self.excluded):
            raise ValueError("Application profiles, credentials, transcripts and snapshots aren't file-tool inputs")
        relative_path(str(path.relative_to(self.root)))
        return path

    def precondition(self, name, arguments):
        path = self.path(arguments["path"])
        require_anchored_files()
        if name == "angelo_list_files":
            with _private_directory(path, within=self.root) as folder:
                info = directory_info(folder)
                return "directory", info.st_mtime_ns, info.st_ino, info.st_dev
        try:
            with _private_directory(path.parent, within=self.root) as folder:
                info = optional_info(folder, path.name)
        except FileNotFoundError:
            info = None
        return file_signature(info) if info is not None else ("missing",)

    def _list(self, path):
        entries = []
        with _private_directory(path, within=self.root) as folder:
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
                if stat.S_ISLNK(info.st_mode) or any((path / name).is_relative_to(private) for private in self.excluded):
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
        with _private_directory(path.parent, within=self.root) as folder:
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
            with _private_directory(path.parent, create=name == "angelo_write_file", within=self.root) as folder:
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
