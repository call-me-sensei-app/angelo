# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where a path really is, for the agent-script confinement's checks (script_confinement, doc/angelo/
script_confinement.rst).

``make(platform)`` builds the checks from functions bound once, at install: the system's own ``lstat``, ``readlink``,
``getcwd`` and final-path calls, never ``os.path`` or a builtin looked up when a check runs. A script may rebind
``os.path.realpath``, ``os.lstat`` or ``builtins.isinstance``; these closures still hold the originals, and nothing
outside the confinement's hook holds them.

A path is resolved the way the system will resolve it:

* relative to the working folder, or to the folder a ``dir_fd`` names (``os.open(name, dir_fd=...)``: the private
  roots registry holds its folders open, ``private_roots.hold``);
* through every symbolic link, junction and ``..``, as far as the path exists (a new file's folder resolves);
* ``/dev/fd/N`` and ``/proc/self/fd/N`` as the file that descriptor has open, and an integer path as its file;
* on macOS and Windows, compared case-insensitively and in one Unicode form (the systems' own rule), and by folder
  identity (device and inode, or volume and file index), so a firmlink, a hard-linked folder, an 8.3 short name or
  another spelling of a private folder is still that folder.
"""


def make(platform=None):
    """The resolver and the root checks as closures (``Checks``)."""
    import sys
    platform = platform or sys.platform
    windows = platform == "win32"
    folding = windows or platform == "darwin"
    import _stat
    import unicodedata
    if windows:
        import nt as system
    else:
        import posix as system
    lstat, stat, getcwd = system.lstat, system.stat, system.getcwd
    readlink = getattr(system, "readlink", None)
    S_ISLNK, S_ISDIR = _stat.S_ISLNK, _stat.S_ISDIR
    normalize = unicodedata.normalize
    encoding = sys.getfilesystemencoding()
    _str, _bytes, _int, _isinstance, _tuple, _OSError = str, bytes, int, isinstance, tuple, OSError
    _TypeError, _ValueError, _any, _set, _len = TypeError, ValueError, any, set, len
    sep = "\\" if windows else "/"
    fd_path = _fd_path_function(platform, readlink)
    final = getattr(system, "_getfinalpathname", None)
    full = getattr(system, "_getfullpathname", None)

    def text(path):
        if _isinstance(path, _str):
            return path
        if _isinstance(path, _bytes):
            return path.decode(encoding, "surrogateescape")
        raise _TypeError("path")

    def posix_resolve(path):
        if not path.startswith("/"):
            path = getcwd() + "/" + path
        pending = [part for part in path.split("/") if part not in ("", ".")]
        pending.reverse()
        current, links = [], 0
        while pending:
            name = pending.pop()
            if name == "..":
                if current:
                    current.pop()
                continue
            candidate = "/" + "/".join(current + [name])
            try:
                info = lstat(candidate)
            except _OSError:
                current.append(name)
                continue
            if S_ISLNK(info.st_mode) and readlink is not None:
                links += 1
                if links > 64:
                    raise _OSError("too many links")
                target = readlink(candidate)
                if target.startswith("/"):
                    current = []
                more = [part for part in target.split("/") if part not in ("", ".")]
                more.reverse()
                pending.extend(more)
            else:
                current.append(name)
        return "/" + "/".join(current)

    def windows_resolve(path):
        whole = full(path)
        head, tail = whole, []
        while True:
            try:
                head = final(head)
                break
            except _OSError:
                cut = head.rstrip("\\").rfind("\\")
                if cut <= 2:
                    break
                tail.insert(0, head[cut + 1:])
                head = head[:cut]
        if head.startswith("\\\\?\\UNC\\"):
            head = "\\\\" + head[8:]
        elif head.startswith("\\\\?\\"):
            head = head[4:]
        return "\\".join([head.rstrip("\\")] + tail) if tail else head

    resolve_text = windows_resolve if windows else posix_resolve

    def key(resolved):
        return normalize("NFC", resolved).casefold() if folding else resolved

    def resolve(path, dir_fd=None):
        """The real path ``path`` names (an integer: the file that descriptor has open), or None when it names no file
        (a pipe, a socket)."""
        if _isinstance(path, _int):
            return fd_path(path)
        path = text(path)
        if dir_fd is not None and dir_fd != -1 and not (path.startswith(sep) or (windows and path[1:2] == ":")):
            base = fd_path(dir_fd)
            if base is None:
                raise _OSError("unknown folder")
            path = base + sep + path
        resolved = resolve_text(path)
        if not windows:
            for prefix in ("/dev/fd/", "/proc/self/fd/"):
                if resolved.startswith(prefix) and resolved[len(prefix):].isdigit():
                    return fd_path(_int(resolved[len(prefix):]))
        return resolved

    def identity(path):
        try:
            info = stat(path)
        except (_OSError, _ValueError):
            return None
        return (info.st_dev, info.st_ino) if S_ISDIR(info.st_mode) else None

    def ancestors(resolved):
        """Each existing folder from ``resolved`` up to the root: identities, nearest first."""
        found, current = [], resolved
        while True:
            info = None
            try:
                info = stat(current)
            except (_OSError, _ValueError):
                pass
            if info is not None:
                found.append((info.st_dev, info.st_ino))
            cut = current.rstrip(sep).rfind(sep)
            if cut < 0 or current.rstrip(sep) == current[:cut + 1].rstrip(sep):
                break
            parent = current[:cut] or sep
            if windows and parent.endswith(":"):
                parent += sep
            if parent == current:
                break
            current = parent
        return found

    class Roots:
        """A set of folders: their keys (resolved, compared by the system's rule) and identities. Add-only: a folder
        once private stays private for the life of the process."""
        __slots__ = ("keys", "ids")

        def __init__(self):
            self.keys, self.ids = [], _set()

        def add(self, folder):
            try:
                resolved = resolve(folder)
            except (_OSError, _TypeError, _ValueError):
                return
            if resolved is None:
                return
            folder_key = key(resolved).rstrip(sep) or sep
            if folder_key not in self.keys:
                self.keys.append(folder_key)
            found = identity(resolved)
            if found is not None:
                self.ids.add(found)

        def holds(self, resolved, chain=None):
            """Whether ``resolved`` is one of these folders or inside one."""
            path_key = key(resolved)
            for root in self.keys:
                if path_key == root or path_key.startswith(root if root.endswith(sep) else root + sep):
                    return True
            if self.ids:
                for found in (chain if chain is not None else ancestors(resolved)):
                    if found in self.ids:
                        return True
            return False

        def above(self, resolved):
            """Whether ``resolved`` is a folder that holds one of these (a link to it reaches them)."""
            path_key = key(resolved).rstrip(sep) or sep
            prefix = path_key if path_key.endswith(sep) else path_key + sep
            return _any(root == path_key or root.startswith(prefix) for root in self.keys)

        def deepest(self, resolved):
            """The key of the deepest of these folders that holds ``resolved`` by its spelling (never by identity),
            or None."""
            path_key, best = key(resolved), None
            for root in self.keys:
                if (path_key == root or path_key.startswith(root if root.endswith(sep) else root + sep)) and (
                        best is None or _len(root) > _len(best)):
                    best = root
            return best

        def inside_of(self, resolved, outer, chain, outer_chain):
            """Whether one of these folders is ``outer`` (a key) or lies inside it, and holds ``resolved``: by its
            spelling, or by an identity in ``chain`` not in ``outer_chain`` (the identities strictly above it)."""
            path_key, start = key(resolved), outer if outer.endswith(sep) else outer + sep
            for root in self.keys:
                if (root == outer or root.startswith(start)) and (
                        path_key == root or path_key.startswith(root if root.endswith(sep) else root + sep)):
                    return True
            return _any(found in self.ids and found not in outer_chain for found in chain)

    class Families:
        """Folders named ``<prefix>*`` directly in a parent folder (``private_roots.families``)."""
        __slots__ = ("pairs",)

        def __init__(self):
            self.pairs = []

        def add(self, parent, prefix):
            try:
                resolved = resolve(parent)
            except (_OSError, _TypeError, _ValueError):
                return
            if resolved is None:
                return
            pair = (key(resolved).rstrip(sep) or sep, key(prefix))
            if pair not in self.pairs:
                self.pairs.append(pair)

        def holds(self, resolved):
            path_key = key(resolved)
            for parent, prefix in self.pairs:
                start = parent if parent.endswith(sep) else parent + sep
                if path_key.startswith(start) and path_key[len(start):].startswith(prefix):
                    return True
            return False

    return Checks(resolve=resolve, key=key, ancestors=ancestors, roots=Roots, families=Families, sep=sep,
                  windows=windows)


class Checks:
    __slots__ = ("resolve", "key", "ancestors", "roots", "families", "sep", "windows")

    def __init__(self, **parts):
        for name, value in parts.items():
            object.__setattr__(self, name, value)


def _fd_path_function(platform, readlink):
    """The path of the file a descriptor has open (macOS F_GETPATH, Linux /proc, Windows its final path), or None."""
    if platform == "darwin":
        import fcntl
        call, command = fcntl.fcntl, getattr(fcntl, "F_GETPATH", 50)
        _OSError, _ValueError, _bytes = OSError, ValueError, bytes

        def fd_path(descriptor):
            try:
                raw = call(descriptor, command, _bytes(1024))
            except (_OSError, _ValueError):
                return None
            return raw.split(b"\0", 1)[0].decode("utf-8", "surrogateescape") or None
        return fd_path
    if platform == "win32":
        try:
            import ctypes
            import msvcrt
            from ctypes import wintypes
            handle_of = msvcrt.get_osfhandle
            get_final = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
            get_final.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
            get_final.restype = wintypes.DWORD
            buffer_type = ctypes.create_unicode_buffer
            _OSError, _ValueError = OSError, ValueError
        except (ImportError, OSError, AttributeError):
            return lambda descriptor: None

        def fd_path(descriptor):
            try:
                handle = handle_of(descriptor)
                buffer = buffer_type(32768)
                length = get_final(handle, buffer, 32768, 0)
            except (_OSError, _ValueError):
                return None
            if not length:
                return None
            value = buffer.value
            if value.startswith("\\\\?\\UNC\\"):
                return "\\\\" + value[8:]
            return value[4:] if value.startswith("\\\\?\\") else value
        return fd_path

    _errors = (OSError, ValueError, TypeError)

    def fd_path(descriptor):
        if readlink is None:
            return None
        try:
            target = readlink("/proc/self/fd/%d" % descriptor)
        except _errors:
            return None
        return target if target.startswith("/") else None
    return fd_path
