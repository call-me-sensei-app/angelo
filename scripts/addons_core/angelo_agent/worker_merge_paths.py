# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which folders are Angelo's own, and the paths into them that a merge cuts (``worker_merge_privacy``, break-it
E2E-20 F-L-2 and its reviews, BREAKIT-C7A and BREAKIT-C7A-2).

``PrivateFolders`` (read once per merge): every application-state folder the file tools refuse (``private_roots``: the
state and data folders, the account store, retained and native profiles, earlier and pre-rename ones), each folder a
creator registered, each declared family's members, the parts' own folders, and any folder holding Angelo's owner
file (an allocated folder wherever it was moved). A path is in one when one of its folders is that folder by identity
(device and inode, as ``files.FileWorkspace``), so a case, Unicode or link spelling of a private folder is still it,
or by its spelled path where no folder exists to compare. The chat's default workspace is the one exception, as for
the file tools: a file there is a working file of the person's chats, kept where it is.

``clean`` cuts each private path in a string to its file name: a whole string that is one path by where it resolves,
and in text each path found by a spelling of its folder (``worker_merge_spellings``: every spelling, through a variable
or the home folder too, any run of separators; text and spellings folded alike, ``str.casefold``), its end found after
that spelling, never inside it (a folder named with a space, as macOS's Application Support, BREAKIT-C7A-2 H1).
Text is searched only where it holds a folder's own name, each spelling matched backwards from there, so a long text
costs its length and its hits, never its length times the spellings (BREAKIT-C7A-5 L1). ``blender_paths`` cuts what
Blender's own path walk visits, after ``worker_merge_media`` keeps what can be kept of a file there;
``worker_merge_strings`` does every other string; ``worker_merge_verify`` reads what was written.
"""

import bisect
import os
import re
from pathlib import Path

from .worker_merge_spellings import (CACHED, DETOUR, Components, Detours, cached as _cached,  # noqa: F401
                                     expanded as _expanded,
                                     fold, folded_path as _folded, looks_absolute as _looks_absolute,
                                     needles as _needles, reversed_alternation as _alternation, within as _within)

FILL = "_" * 4095   # Longer than any path buffer: Blender's copy cuts it to the buffer's size, filling it whole.
ENDS = re.compile(r"[\s\"'`,;()<>|]")   # Where a path in text ends, after its folder's spelling.
_LINE_END = re.compile(r"[\n\0]")   # Where it ends at the latest.
_RELATIVE = re.compile(r"(?<![^\s\"'`,;()<>|=])//(?!/)")   # Blender's "//" starting a token (never a URL's).
_SEPARATOR = re.compile(r"[\\/]|%2[fF]|%5[cC]")
_BREAKS = re.compile("[\n\0]")   # Where a line ends: a path never reads past one.
_UP = re.compile(r"(?:^|[\\/]|%2[fF]|%5[cC])\.\.(?=[\\/]|%2[fF]|%5[cC]|[\s\"'`,;()<>|]|$)")   # A ".." folder.


class PrivateFolders:
    """Angelo's own folders now, read once per merge: ``part_paths`` are its parts' files."""

    def __init__(self, part_paths):
        from . import private_roots
        parts = [Path(path).parent for path in part_paths]
        self.parts = [os.path.abspath(part) for part in parts]   # Where Blender's "//" in a part's text resolves.
        roots = parts + [Path(item) for item in private_roots.registered()] + _registry()
        families = private_roots.families()
        members, prefixes = [], {}
        for parent, prefix in families:
            prefixes.setdefault(str(parent), []).append(prefix)
        for parent, names in prefixes.items():   # Each family's folders now (an earlier process's too): each parent
            named = re.compile("|".join(map(re.escape, names)), re.IGNORECASE)   # read once, a temporary folder's
            try:                                                                  # many entries by name alone.
                with os.scandir(parent) as entries:
                    members += [Path(entry.path) for entry in entries if named.match(entry.name) and entry.is_dir()]
            except OSError:
                continue
        bases = list(roots)   # Members are found by their family's needle, never one each.
        roots += members
        self._identities, self._owned, self._decided, self._exists = {}, {}, {}, {}
        self.identities = {identity for identity in map(self._identity, roots) if identity is not None}
        self.lexical = {_folded(Path(root).absolute()) for root in roots}
        self.families = [(self._identity(parent), _folded(Path(parent).absolute()), _folded(prefix))
                         for parent, prefix in families]
        workspace = _workspace()
        self.workspace = (self._identity(workspace), _folded(workspace)) if workspace is not None else None
        # The registry remembers every state folder ever used: one gone from the disk holds nothing to find, and each
        # adds its spellings to every search (BREAKIT-C7A-3 H3), so only folders here now are searched for.
        self.needles = _needles([base for base in bases if os.path.isdir(base)], families)
        # Where a spelling can end: each needle's last folder name, folded. Text is searched only there, by the
        # spellings that end in that name, read backwards from it, as one pattern sharing their common pieces.
        ending = {}
        for needle in sorted(self.needles, key=len, reverse=True):
            ending.setdefault(needle.rsplit("/", 1)[-1], []).append(needle)
        self.literals = sorted(set(ending) - {""})
        self.backwards = {literal: re.compile(_alternation(ending[literal])) for literal in self.literals}
        reversed_parts = {}   # Each spelling's folder names last first, and whether it starts at a separator.
        for needle in self.needles:   # (Its own name: ``parts`` is this merge's part folders, read below.)
            pieces = needle.split("/")
            reversed_parts.setdefault(pieces[-1], []).append(([piece for piece in reversed(pieces) if piece],
                                                              not pieces[0]))
        self.detours = {literal: Detours(spellings) for literal, spellings in reversed_parts.items()}
        # Also found in written bytes (``worker_merge_verify``): each of this merge's part folders and this process's
        # own private folders by name (a path relative to the person's file spells no root).
        self.names = sorted({Path(root).name.casefold() for root in parts + [Path(item) for item in
                                                                           private_roots.registered()]
                             if len(Path(root).name) >= 8})

    def _identity(self, folder):
        def identity(key):
            try:
                info = os.stat(key)
                return (info.st_dev, info.st_ino) if info.st_ino else None
            except (OSError, ValueError):
                return None
        return _cached(self._identities, str(folder), identity)

    def _owner(self, folder):
        """Whether ``folder`` holds Angelo's owner file (``private_roots.OWNER``): an allocated folder, moved too."""
        from .private_roots import OWNER
        return _cached(self._owned, str(folder), lambda key: os.path.isfile(os.path.join(key, OWNER)))

    def holds(self, path):
        """Whether ``path`` (absolute, or from the home folder) is in a private folder: the nearest of its folders that
        is private, or the default workspace, decides; each folder's decision kept (review H-C)."""
        text = str(path)
        if text.startswith("//"):   # Blender's relative path: where it resolves (``_relative_holds``).
            return self._relative_holds(text[2:])
        if os.sep == "/" and "\\" in text:   # A Windows spelling of a path here: its folders are the same folders.
            text = text.replace("\\", "/")
        try:
            folder = os.path.abspath(_expanded(text))
        except (TypeError, ValueError):
            return False
        walked, found = [], False
        while True:
            if folder in self._decided:
                found = self._decided[folder]
                break
            walked.append(folder)
            here = self._decides(folder)
            if here is not None:
                found = here
                break
            parent = os.path.dirname(folder)
            if parent == folder:
                break
            folder = parent
        for key in walked:   # A folder no rule decides takes its nearest decided folder's answer.
            _cached(self._decided, key, lambda _key: found)
        return found

    def _decides(self, folder):
        """True when ``folder`` (a normal absolute path) itself is private, False when it is the default workspace,
        else None."""
        identity, folded = self._identity(folder), _folded(folder)
        if self.workspace is not None and ((identity is not None and identity == self.workspace[0])
                                           or folded == self.workspace[1]):
            return False
        if (identity is not None and identity in self.identities) or folded in self.lexical or (
                identity is not None and self._owner(folder)):
            return True
        name, parent = _folded(os.path.basename(folder)), os.path.dirname(folder)
        for family_identity, family_folded, prefix in self.families:
            if name.startswith(prefix) and parent != folder and (
                    (family_identity is not None and self._identity(parent) == family_identity)
                    or _folded(parent) == family_folded):
                return True
        return None

    def _elsewhere(self, longer, private, seen):
        """Whether the spelling ``longer`` names a file or folder here besides the ``private`` path it starts with: it,
        or a folder it is in past ``private``, exists ("state backup" beside "state", its file gone or not;
        BREAKIT-C7A-7 L4). ``seen``: folders found missing already."""
        try:
            folder, cut = os.path.normpath(_expanded(longer)), os.path.normpath(_expanded(private))
        except (TypeError, ValueError):
            return False
        while not _within(cut, folder) and os.path.dirname(folder) != folder and folder not in seen:
            if _cached(self._exists, folder, os.path.lexists):
                return True
            seen.add(folder)
            folder = os.path.dirname(folder)
        return False

    def finds(self, text):
        """``(start, end)`` in ``text`` of each private folder's spelling, in order: where one's last folder name ends
        in the folded text, the longest spelling that ends there."""
        folded, index = fold(text)
        backwards, components, breaks, size, starts = None, None, None, len(folded), {}
        for literal in self.literals:
            at = folded.find(literal)
            while at >= 0:
                if backwards is None:
                    backwards = folded[::-1]
                end = at + len(literal)
                match = self.backwards[literal].match(backwards, size - end)
                start = size - match.end() if match else None
                if start is None and literal in self.detours:
                    if components is None:   # Read once per text: its names, and where its lines break.
                        components = Components(folded)
                        breaks = [match.start() for match in _BREAKS.finditer(folded)]
                    line = bisect.bisect_left(breaks, end) - 1   # A path never goes past a line's start (path_end's).
                    low = max(0, end - DETOUR, breaks[line] + 1 if line >= 0 else 0)
                    if components.up_between(low, end):
                        start = self.detours[literal].start(folded, components, low, end)
                if start is not None:   # The longest spelling ending here.
                    starts[end] = min(starts.get(end, end), start)
                at = folded.find(literal, at + 1)
        found = sorted((start, end) for end, start in starts.items())
        found = [(index(start), index(end - 1) + 1) for start, end in found] if index else found
        relative = self._relative(text)
        if relative:   # Each end's longest spelling, either way it was found.
            ends = {end: start for start, end in found}
            for start, end in relative:
                ends[end] = min(ends.get(end, end), start)
            found = sorted((start, end) for end, start in ends.items())
        return found

    def _relative_holds(self, rest):
        """Whether a path relative to a .blend file (``rest``: after Blender's "//") reaches a private folder from a
        part's folder (the worker wrote it there) or from the person's saved file's (where it resolves once merged):
        outside the part folders themselves, where the cleaning's own "//" + file name points (port review NIT)."""
        bases = list(self.parts)
        try:
            import bpy
            if isinstance(bpy.data.filepath, str) and bpy.data.filepath:
                bases.append(os.path.dirname(os.path.abspath(bpy.data.filepath)))
        except (ImportError, AttributeError):
            pass
        for base in bases:
            try:
                resolved = os.path.abspath(os.path.join(base, rest.replace("\\", "/") if os.sep == "/" else rest))
            except (TypeError, ValueError):
                continue
            if not any(_within(resolved, part) for part in self.parts) and self.holds(resolved):
                return True
        return False

    def _relative(self, text):
        """``[(start, end)]`` of each spelling relative to a part's file (Blender's "//" starting a token) that
        resolves into a private folder: its first end where it does, tried at each place a path could end up to the
        line's end, the next such spelling or ``DETOUR`` on (so a name with a space is read through, and each
        spelling's stretch is read once). Found by where it resolves, never by a name's length (port review NIT)."""
        if "//" not in text or not self.parts:
            return []
        places, found = [match.start() for match in _RELATIVE.finditer(text)], []
        for number, start in enumerate(places):
            limit = min(len(text), start + DETOUR, places[number + 1] if number + 1 < len(places) else len(text))
            line = _LINE_END.search(text, start, limit)
            limit, at = (line.start() if line else limit), start + 2
            while at <= limit:
                stop = ENDS.search(text, at, limit)
                end = stop.start() if stop else limit
                if end > start + 2 and self.holds(_unquoted(text[start:end])):
                    found.append((start, end))
                    break
                if stop is None:
                    break
                at = end + 1
        return found

    def path_end(self, text, start, found):
        """Where the private path from ``start`` (a folder's spelling ending at ``found``) ends in ``text``, or None.
        Each place a path could end (a space, a quote, a bracket), up to the line's end, is tried while what follows
        could take it elsewhere (BREAKIT-C7A-7 M1: "My Renders" in the workspace, then "../.."): the rest of a folder's
        name ("workspace backup"), or past a ".." folder (review H-C). A longer spelling naming a file or folder here
        that is not private ("state backup" beside "state") is that one, read until its folder is missing."""
        limit = min(len(text), found + DETOUR)
        line = _LINE_END.search(text, found, limit)
        limit = line.start() if line else limit
        at, private, seen = found, None, set()
        while True:
            stop = ENDS.search(text, at, limit)
            end = stop.start() if stop else limit
            if private is None:
                if self.holds(_unquoted(text[start:end])):
                    private = end
                elif stop is None:
                    return None
                else:
                    separator = _SEPARATOR.search(text, end, limit)
                    if separator is None or not self.holds(_unquoted(text[start:separator.start()])):
                        up = _UP.search(text, end, limit)   # Only a ".." folder can take it elsewhere now.
                        if up is None:
                            return None
                        at = up.end()
                        continue
            else:
                longer = _unquoted(text[start:end])
                cut = _unquoted(text[start:private])
                if not self.holds(longer) and self._elsewhere(longer, cut, seen):
                    return None
                if self._past(longer, cut, seen):   # Nothing longer can be here either.
                    return private
            if stop is None:
                return private
            at = end + 1

    def _past(self, longer, private, seen):
        """Whether the folder the spelling ``longer`` is in lies past the ``private`` path and is missing."""
        try:
            folder, cut = os.path.dirname(os.path.normpath(_expanded(longer))), os.path.normpath(_expanded(private))
        except (TypeError, ValueError):
            return False
        return not _within(cut, folder) and (folder in seen or not _cached(self._exists, folder, os.path.lexists))

    def clean(self, text, item, path_like):
        """``text`` with every private path in it cut to its file name, or None when it holds none. ``path_like``: a
        path property, whose relative path is resolved against ``item``'s file."""
        import bpy
        stripped = text.strip()
        if stripped and "\n" not in stripped and (path_like or _looks_absolute(stripped)):
            try:
                resolved = _expanded(bpy.path.abspath(stripped, library=getattr(item, "library", None)))
            except (TypeError, ValueError):
                resolved = None
            if resolved and os.path.isabs(resolved) and self.holds(resolved):
                return ("//" if path_like else "") + _basename(stripped)
        pieces, at = [], 0   # Text, or a path that is not private by itself (it may hold another: "a;b").
        for start, found in self.finds(text):
            if start < at:
                continue
            end = self.path_end(text, start, found)
            if end is not None:
                pieces += [text[at:start], _basename(text[start:end])]
                at = end
        return "".join(pieces + [text[at:]]) if pieces else None


def _unquoted(text):
    """``text`` as the path it spells: a ``file://`` URL or percent-encoding decoded, JSON's escapes undone."""
    from urllib.parse import unquote
    if text.lower().startswith("file://"):
        text = text[7:]
    if "%" in text:
        text = unquote(text)
    if "\\" in text:
        import json
        try:
            text = json.loads('"%s"' % text.replace('"', '\\"'))
        except ValueError:
            pass
    return text


def _registry():
    """Every application-state folder (``private_roots.private_roots``, as the file tools read it)."""
    from . import private_roots, storage, ui
    try:
        return [Path(item) for item in private_roots.private_roots(
            ui.state_directory(), ui.connection_directory(), ui.connections().records.values())]
    except Exception:   # A host without the full registry (host tests): its state and data folders.
        found = []
        for getter in (ui.state_directory, storage.data_directory):
            try:
                found.append(Path(getter()))
            except Exception:
                continue
        return found


def _workspace():
    """The chat's default workspace (``Controller.workspace``), which the file tools grant."""
    from . import ui
    try:
        return Path(ui.state_directory()).absolute() / "workspace"
    except Exception:
        return None


def _basename(path):
    return os.path.basename(_unquoted(path).replace("\\", "/").rstrip("/")) or "file"


def type_name(item):
    return type(item).bl_rna.identifier


def overwrite(owner, name, value):
    """``owner.name = value`` with the whole fixed-size buffer written first: Blender saves a string's buffer whole,
    bytes after its terminator included, so a shorter value alone leaves the end of the old one in every save."""
    size = owner.bl_rna.properties[name].length_max
    if size > 1:
        setattr(owner, name, FILL[:size - 1])
    setattr(owner, name, value)


def blender_paths(bpy, kept, private):
    """Blender's own path walk over ``kept`` (local datablocks): what a worker's file brings written again from what
    Blender decoded (``worker_merge_media``), then every path written again whole, a private one cut to its file name.
    Returns (packed, detached, unsafe) names and the datablocks written again."""
    from . import private_roots, worker_merge_media
    found, seen, subset = [], set(), set(kept)

    def visit(item, path, meta):
        if path and not meta.is_readonly and item.as_pointer() not in seen and private.holds(
                bpy.path.abspath(path, library=item.library)):
            seen.add(item.as_pointer())
            found.append(item)
    bpy.data.file_path_foreach(visit, subset=subset, flags=set())
    with private_roots.TemporaryDirectory(prefix="angelo-merge-check-") as scratch:
        written, detached, unsafe = worker_merge_media.secure(bpy, kept, found, private, scratch)
    values = []   # In visiting order, which two walks of the same set share.

    def fill(item, path, meta):
        if not path or meta.is_readonly:
            return None
        values.append("//" + _basename(path) if private.holds(bpy.path.abspath(path, library=item.library))
                      else path)
        return FILL

    def value(item, path, meta):
        return values.pop(0) if path == FILL[:len(path)] and len(path) > 1 and values else None
    bpy.data.file_path_foreach(fill, subset=subset, flags=set())
    bpy.data.file_path_foreach(value, subset=subset, flags=set())
    return sorted(item.name for item in written), detached, unsafe, written
