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
and in text each path found by a spelling of its folder (every spelling, any run of separators, both Unicode forms,
home-relative, JSON-escaped and URL-encoded; text and spellings folded alike, ``str.casefold``), its end found after
that spelling, never inside it (a folder named with a space, as macOS's Application Support, BREAKIT-C7A-2 H1).
Text is searched only where it holds a folder's own name, each spelling matched backwards from there, so a long text
costs its length and its hits, never its length times the spellings (BREAKIT-C7A-5 L1). ``blender_paths`` cuts what
Blender's own path walk visits, after ``worker_merge_media`` keeps what can be kept of a file there;
``worker_merge_strings`` does every other string; ``worker_merge_verify`` reads what was written.
"""

import bisect
import os
import re
import unicodedata
from pathlib import Path
from urllib.parse import quote

FILL = "_" * 4095   # Longer than any path buffer: Blender's copy cuts it to the buffer's size, filling it whole.
ENDS = re.compile(r"[\s\"'`,;()<>|]")   # Where a path in text ends, after its folder's spelling.


def _folded(path):
    """A folder's spelling compared without case, Unicode form or the system's own spelling."""
    return unicodedata.normalize("NFC", os.path.normcase(os.path.normpath(str(path)))).casefold()


# Between two folder names in text, read backwards: any run of separators, either way round, either URL-encoded ("%2f"
# and "%5c" reversed: Windows' backslash encodes as "%5c", BREAKIT-C7A-6), "." folders, and detours through other folders and back ("x/.."), so a doubled or mixed separator
# or a ".." spells the same path (BREAKIT-C7A-4 M1, BREAKIT-C7A-6). A detour may be matched wider than it resolves:
# ``holds`` decides each path found, by where it resolves.
_ONE = r"(?:[\\/]|f2%|c5%)"
_RUN = _ONE + "+"
_LEADING = r"(?:\\\\|[\\/]|f2%|c5%)"
_NAME = r"(?:(?![\\/]|f2%|c5%).)+"
REVERSED_SEPARATOR = r"%s(?:\.%s|(?:\.\.%s)+(?:%s%s)+)*" % (_RUN, _RUN, _RUN, _NAME, _RUN)


def fold(text):
    """``text`` folded as the spellings are (``str.casefold``), and a function from a folded offset back to ``text``'s
    (None when one for one, as for every text without a letter that folds to two: "İ", "ß", BREAKIT-C7A-5 M3). Built
    from where such letters are, never character by character, so a long text costs its length in C."""
    folded = text.casefold()
    if len(folded) == len(text):
        return folded, None
    starts, shifts, ends, shift = [], [], [], 0
    for match in _expanding().finditer(text):
        extra = len(match.group().casefold()) - 1
        starts.append(match.start() + shift)   # Where its folded letters begin, and how far later text moved.
        shifts.append(shift)
        shift += extra
        ends.append(match.start() + shift + 1)

    def back(offset):
        at = bisect.bisect_right(starts, offset) - 1
        if at < 0:
            return offset
        if offset < ends[at]:   # Inside one letter's folding: that letter.
            return starts[at] - shifts[at]
        return offset - shifts[at] - (ends[at] - starts[at] - 1)
    return folded, back


_EXPANDS = []


def _expanding():
    """A pattern of every character that folds to more than one."""
    if not _EXPANDS:
        letters = "".join(chr(code) for code in range(0x110000)
                          if not 0xD800 <= code <= 0xDFFF and len(chr(code).casefold()) > 1)
        _EXPANDS.append(re.compile("[%s]" % re.escape(letters)))
    return _EXPANDS[0]


def _spellings(path, suffix=""):
    """Every way text can spell ``path`` (then ``suffix``), separators aside: as given and resolved, from the home
    folder, both Unicode forms, as JSON escapes its letters (``\\u``) and URL-encoded (BREAKIT-C7A-3 H2)."""
    import json
    found = set()
    for spelled in {str(Path(path).absolute()), os.path.realpath(str(path))}:
        spelled += suffix
        home = os.path.expanduser("~")
        forms = {spelled, "~" + spelled[len(home):] if home and spelled.startswith(home + os.sep) else spelled}
        for form in list(forms):
            forms |= {unicodedata.normalize("NFC", form), unicodedata.normalize("NFD", form)}
        for form in forms:
            found |= {form, json.dumps(form)[1:-1].replace("\\\\", "/"), quote(form.replace("\\", "/"))}
    return {item for item in found if len(item) > 3}


def _reversed(needle):
    """``needle`` read backwards as a pattern, each separator any run of them (``REVERSED_SEPARATOR``) but a leading
    one, which is one separator in any of its spellings (JSON's escaped backslash is two characters): what comes
    before it ("file://") is not the path's."""
    parts = needle.split("/")
    pattern = re.escape(parts[-1][::-1])
    for index in range(len(parts) - 2, -1, -1):
        pattern += (_LEADING if index == 0 and not parts[0] else REVERSED_SEPARATOR) + re.escape(parts[index][::-1])
    return pattern


def _needles(roots, families):
    """The fewest spellings that find every private path: each root no other root holds, each family as its parent
    and prefix (so leftover members cost nothing), every spelling that holds another left out."""
    bases = [root for root in roots
             if not any(other != root and _folded(root).startswith(_folded(other).rstrip(os.sep) + os.sep)
                        for other in roots)]
    found = set().union(*(_spellings(base) for base in bases)) if bases else set()
    for parent, prefix in families:
        found |= _spellings(Path(parent), os.sep + prefix)
    ordered = sorted({re.sub(r"[\\/]+", "/", item).casefold().rstrip("/") for item in found}, key=len)
    return [needle for index, needle in enumerate(ordered)
            if not any(shorter in needle for shorter in ordered[:index])]


class PrivateFolders:
    """Angelo's own folders now, read once per merge: ``part_paths`` are its parts' files."""

    def __init__(self, part_paths):
        from . import private_roots
        parts = [Path(path).parent for path in part_paths]
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
        self._identities, self._owned = {}, {}
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
        # spellings that end in that name, read backwards from it (the longest first).
        ending = {}
        for needle in sorted(self.needles, key=len, reverse=True):
            ending.setdefault(needle.rsplit("/", 1)[-1], []).append(_reversed(needle))
        self.literals = sorted(set(ending) - {""})
        self.backwards = {literal: re.compile("|".join(ending[literal])) for literal in self.literals}
        # Also found in written bytes (``worker_merge_verify``): each of this merge's part folders and this process's
        # own private folders by name (a path relative to the person's file spells no root).
        self.names = sorted({Path(root).name.casefold() for root in parts + [Path(item) for item in
                                                                           private_roots.registered()]
                             if len(Path(root).name) >= 8})

    def _identity(self, folder):
        key = str(folder)
        if key not in self._identities:
            try:
                info = os.stat(key)
                self._identities[key] = (info.st_dev, info.st_ino) if info.st_ino else None
            except (OSError, ValueError):
                self._identities[key] = None
        return self._identities[key]

    def _owner(self, folder):
        """Whether ``folder`` holds Angelo's owner file (``private_roots.OWNER``): an allocated folder, moved too."""
        key = str(folder)
        if key not in self._owned:
            from .private_roots import OWNER
            self._owned[key] = os.path.isfile(os.path.join(key, OWNER))
        return self._owned[key]

    def holds(self, path):
        """Whether ``path`` (absolute, or from the home folder) is in a private folder: the nearest of its folders that
        is private, or the default workspace, decides."""
        text = str(path)
        if os.sep == "/" and "\\" in text:   # A Windows spelling of a path here: its folders are the same folders.
            text = text.replace("\\", "/")
        try:
            path = Path(os.path.abspath(os.path.expanduser(text)))
        except (TypeError, ValueError):
            return False
        for folder in (path, *path.parents):
            identity, folded = self._identity(folder), _folded(folder)
            if self.workspace is not None and ((identity is not None and identity == self.workspace[0])
                                               or folded == self.workspace[1]):
                return False
            if (identity is not None and identity in self.identities) or folded in self.lexical or (
                    identity is not None and self._owner(folder)):
                return True
            name, parent = _folded(folder.name), folder.parent
            for family_identity, family_folded, prefix in self.families:
                if name.startswith(prefix) and parent != folder and (
                        (family_identity is not None and self._identity(parent) == family_identity)
                        or _folded(parent) == family_folded):
                    return True
        return False

    def finds(self, text):
        """``(start, end)`` in ``text`` of each private folder's spelling, in order: where one's last folder name ends
        in the folded text, the longest spelling that ends there."""
        folded, index = fold(text)
        backwards, size, starts = None, len(folded), {}
        for literal in self.literals:
            at = folded.find(literal)
            while at >= 0:
                if backwards is None:
                    backwards = folded[::-1]
                end = at + len(literal)
                match = self.backwards[literal].match(backwards, size - end)
                if match:   # The longest spelling ending here.
                    starts[end] = min(starts.get(end, end), size - match.end())
                at = folded.find(literal, at + 1)
        found = sorted((start, end) for end, start in starts.items())
        return [(index(start), index(end - 1) + 1) for start, end in found] if index else found

    def clean(self, text, item, path_like):
        """``text`` with every private path in it cut to its file name, or None when it holds none. ``path_like``: a
        path property, whose relative path is resolved against ``item``'s file."""
        import bpy
        stripped = text.strip()
        if stripped and "\n" not in stripped and (path_like or _looks_absolute(stripped)):
            try:
                resolved = os.path.expanduser(bpy.path.abspath(stripped, library=getattr(item, "library", None)))
            except (TypeError, ValueError):
                resolved = None
            if resolved and os.path.isabs(resolved) and self.holds(resolved):
                return ("//" if path_like else "") + _basename(stripped)
        pieces, at = [], 0   # Text, or a path that is not private by itself (it may hold another: "a;b").
        for start, found in self.finds(text):
            if start < at:
                continue
            end = ENDS.search(text, found)
            end = end.start() if end else len(text)
            if self.holds(_unquoted(text[start:end])):
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


def _looks_absolute(text):
    return text.startswith(("/", "\\", "~")) or bool(re.match(r"^[A-Za-z]:[\\/]", text))


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
    Returns (packed, detached, unsafe) names."""
    from . import private_roots, worker_merge_media
    found, seen, subset = [], set(), set(kept)

    def visit(item, path, meta):
        if path and not meta.is_readonly and item.as_pointer() not in seen and private.holds(
                bpy.path.abspath(path, library=item.library)):
            seen.add(item.as_pointer())
            found.append(item)
    bpy.data.file_path_foreach(visit, subset=subset, flags=set())
    with private_roots.TemporaryDirectory(prefix="angelo-merge-check-") as scratch:
        packed, detached, unsafe = worker_merge_media.secure(bpy, kept, found, private, scratch)
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
    return packed, detached, unsafe
