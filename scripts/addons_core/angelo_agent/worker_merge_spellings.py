# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How text spells Angelo's private folders, for ``worker_merge_paths`` (break-it E2E-20 F-L-2 and its reviews).

A folder is spelled as given and resolved; from the home folder (``~``, ``~user``); through every environment variable
whose value is the folder, one it is in or one inside it, as shells and scripts write one (``$V``, ``${V}``,
``%V%``, PowerShell's ``$env:V``; BREAKIT-C7A-9 M1, BREAKIT-C7A-10 M1: ``$TMPDIR``, ``%LOCALAPPDATA%``,
``$ANGELO_STATE_DIR``), the variables read from the environment, never named here; by its 8.3 short name on
Windows; in both Unicode forms; as JSON escapes its letters and URL-encoded. Each spelling is matched backwards from
its last folder name (``reversed_pattern``), a detour through another folder and back read over the text's folder
names, found once per text (``Components``), so a text costs its length and its hits however many names it repeats
(BREAKIT-C7A-9 L1).
"""

import bisect
import os
import re
import unicodedata
from pathlib import Path
from urllib.parse import quote

DETOUR = 4096   # How far back a detour is read: a path's longest spelling.
CACHED = 1 << 16   # Most entries a cache of folders holds; full, it starts again (bounded memory).
# Between two folder names in text, read backwards: any run of separators, either way round, either URL-encoded ("%2f"
# and "%5c" reversed: Windows' backslash encodes as "%5c", BREAKIT-C7A-6) and "." folders, so a doubled or mixed
# separator spells the same path (BREAKIT-C7A-4 M1). Possessive: nothing a separator matched is tried again, so a
# match costs the text it reads (BREAKIT-C7A-7 L1). A detour through another folder and back ("x/..") is read by
# ``PrivateFolders._detour``, which resolves it as a path does.
_ONE = r"(?:[\\/]|f2%|c5%)"
_RUN = _ONE + "++"
_LEADING = r"(?:\\\\|[\\/]|f2%|c5%)"
REVERSED_SEPARATOR = r"%s(?:\.%s)*+" % (_RUN, _RUN)
_SEPARATORS = re.compile(r"[\\/]|%2f|%5c")   # Between folder names in folded text.
# A folder spelled through a variable at a path's start (``holds`` reads it as the variable's value).
_VARIABLE = re.compile(r"(?:\$\{(?:env:)?(\w+)\}|\$env:(\w+)|\$(\w+)|%(\w+)%)(?=[\\/]|$)", re.IGNORECASE)


def folded_path(path):
    """A folder's spelling compared without case, Unicode form or the system's own spelling."""
    return unicodedata.normalize("NFC", os.path.normcase(os.path.normpath(str(path)))).casefold()


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


class Components:
    """A folded text's folder names (the runs between separators), found once, and where it spells ".."."""

    def __init__(self, folded):
        self.folded, self._spans, self._parents, self.ups, at = folded, None, None, [], folded.find("..")
        while at >= 0:
            self.ups.append(at)
            at = folded.find("..", at + 1)

    def up_between(self, low, high):
        """Whether ".." is spelled within ``[low, high)``."""
        at = bisect.bisect_left(self.ups, low)
        return at < len(self.ups) and self.ups[at] < high - 1

    @property
    def spans(self):
        """``[(start, end)]`` of each folder name, found when first asked (only a text that spells ".." needs them)."""
        if self._spans is None:
            self._spans, at = [], 0
            for match in _SEPARATORS.finditer(self.folded):
                if match.start() > at:
                    self._spans.append((at, match.start()))
                at = match.end()
            if at < len(self.folded):
                self._spans.append((at, len(self.folded)))
            self.ends = [end for _start, end in self._spans]
        return self._spans

    @property
    def parents(self):
        """For each folder name, the index of the name before it once ".." and "." are resolved (-1: none), found in
        one pass; None for "." and "..". A place's detour then reads only its spelling's own folders back
        (port review SHOULD 1: an alternating "x/.." line read back DETOUR characters at every place)."""
        if self._parents is None:
            folded, stack, self._parents = self.folded, [], []
            for first, stop in self.spans:
                name = folded[first:stop]
                if name == ".":
                    self._parents.append(None)
                elif name == "..":
                    self._parents.append(None)
                    if stack:
                        stack.pop()
                else:
                    self._parents.append(stack[-1] if stack else -1)
                    stack.append(len(self._parents) - 1)
        return self._parents

    def ending_at(self, end):
        """The index of the folder name that ends at ``end``, or None."""
        self.spans
        at = bisect.bisect_left(self.ends, end)
        return at if at < len(self.ends) and self.ends[at] == end else None


class Detours:
    """Where a private folder's spelling that goes through other folders and back ("x/..") starts in a text.

    ``spellings``: each spelling's folder names, last first, and whether it starts at a separator. They are indexed by
    how many names they hold and by all but the outermost, so a place reads the names its text resolves to once, as
    far as the longest spelling needs, and looks up only spellings of each length: its cost is the place's own
    folders, never the number of spellings (a registry of 40 state folders spells 480: an alternating 250 KB line
    read every one at every place, 2.6 s; Windows spells more of each, VM CI on 4d5d7168d10)."""

    def __init__(self, spellings):
        self.sizes = {}
        for names, absolute in spellings:
            self.sizes.setdefault(len(names), {}).setdefault(tuple(names[:-1]), []).append((names[-1], absolute))
        self.order = sorted(self.sizes)

    def start(self, folded, components, low, end):
        """Where a spelling ending at ``end`` starts, never before ``low``; None for none. A spelling that starts at
        no separator (a drive, "$HOME", "~") starts where its outermost name does, which may share its run with what
        precedes it on the line ("see C:", "-I$HOME", a long run of text): that name is read at the run's end, as
        permissively as the plain backwards reader (a false find refuses the merge; a miss would leak), and only it
        may sit in a run that reaches before ``low`` (re-read of ef03d0e1f83, MUST)."""
        last = components.ending_at(end)
        parents = components.parents
        if last is None or parents[last] is None:
            return None
        spans, chain, names, item, best = components.spans, [], [], last, None
        for size in self.order:
            while len(chain) < size and item >= 0:   # The names the text resolves to, last first.
                first, stop = spans[item]
                chain.append((first, stop))
                names.append(folded[first:stop])
                item = parents[item] if first >= low else -1   # A run reaching before ``low``: only an outermost name.
            if len(chain) < size:
                break   # Lengths ascend: no longer spelling fits either.
            first, stop = chain[size - 1]
            for outer, absolute in self.sizes[size].get(tuple(names[:size - 1]), ()):
                if absolute:   # Its own run, after one separator in any spelling.
                    if names[size - 1] != outer or first < low:
                        continue
                    width = next((len(mark) for mark in ("\\\\", "%2f", "%5c", "/", "\\")
                                  if folded.endswith(mark, low, first)), 0)
                    if not width:
                        continue
                    at = first - width
                else:   # At its run's end: wherever the run starts.
                    at = stop - len(outer)
                    if at < max(first, low) or folded[at:stop] != outer:
                        continue
                best = at if best is None else min(best, at)
        return best


def cached(cache, key, compute):
    """``cache[key]``, computed by ``compute(key)`` when missing; a full cache is emptied first."""
    if key not in cache:
        if len(cache) >= CACHED:
            cache.clear()
        cache[key] = compute(key)
    return cache[key]


def within(path, folder):
    """Whether ``folder`` is ``path`` or a folder ``path`` is in (both normal)."""
    return path == folder or path.startswith(folder.rstrip("\\/") + os.sep)


def expanded(text):
    """``text`` with the folder its start spells through a variable (``$V``, ``${V}``, ``%V%``, ``$env:V``; the home
    folder for ``$HOME`` and ``%USERPROFILE%`` where they are not set) or the home folder (``~``, ``~user``) as that
    folder."""
    match = _VARIABLE.match(text)
    if match:
        name = next(group for group in match.groups() if group)
        value = os.environ.get(name) or next((value for key, value in os.environ.items()
                                              if key.casefold() == name.casefold()), None)
        if not value and name.upper() in ("HOME", "USERPROFILE"):
            value = os.path.expanduser("~")
        if value:
            text = value + text[match.end():]
    return os.path.expanduser(text)


def looks_absolute(text):
    return text.startswith(("/", "\\", "~")) or bool(re.match(r"^[A-Za-z]:[\\/]", text) or _VARIABLE.match(text))


def _starts():
    """``{folder: [spellings]}``: each folder a variable names, and how text spells it; the home folder's too."""
    found = {}
    for name, value in os.environ.items():
        if not value or os.pathsep in value or not os.path.isabs(os.path.expanduser(value)):
            continue   # A list of folders (PATH) or not a folder.
        for folder in {os.path.normpath(value), os.path.realpath(value)}:
            if os.path.dirname(folder) != folder:   # A drive's or the system's root names every path: never one.
                found.setdefault(folder, set()).update(
                    {"$" + name, "${%s}" % name, "%%%s%%" % name, "$env:" + name, "${env:%s}" % name})
    home = os.path.expanduser("~")
    if home and os.path.dirname(home) != home:
        spellings = found.setdefault(os.path.normpath(home), set())
        spellings |= {"~", "$HOME", "${HOME}", "%USERPROFILE%"}
        user = os.path.basename(home)
        if user and os.path.expanduser("~" + user) == home:
            spellings.add("~" + user)
    return found


def _short(path):
    """``path``'s 8.3 short spelling on Windows (where a volume keeps one), else ``path``."""
    if os.name != "nt":
        return path
    import ctypes
    buffer = ctypes.create_unicode_buffer(32768)
    size = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer))
    return buffer.value if 0 < size < len(buffer) else path


def spellings(path, suffix=""):
    """Every way text can spell ``path`` (then ``suffix``), separators aside (the module's list)."""
    import json
    found, starts = set(), _starts()
    for spelled in {str(Path(path).absolute()), os.path.realpath(str(path)), str(_short(Path(path).absolute()))}:
        spelled += suffix
        forms = {spelled}
        normal = os.path.normcase(spelled)
        for folder, names in starts.items():
            start = os.path.normcase(folder).rstrip("\\/")
            if normal == start or normal.startswith(start + os.sep):   # The folder, or one it is in.
                forms |= {name + spelled[len(folder):] for name in names}
            elif start.startswith(normal.rstrip("\\/") + os.sep):   # A folder inside it: every path from there is.
                forms |= set(names)
        for form in list(forms):
            forms |= {unicodedata.normalize("NFC", form), unicodedata.normalize("NFD", form)}
        for form in forms:
            found |= {form, json.dumps(form)[1:-1].replace("\\\\", "/"), quote(form.replace("\\", "/"))}
    return {item for item in found if len(item) > 3}


def _reversed_tokens(needle):
    """``needle`` read backwards as pattern pieces, each separator any run of them (``REVERSED_SEPARATOR``) but a
    leading one, which is one separator in any of its spellings (JSON's escaped backslash is two characters): what
    comes before it ("file://") is not the path's."""
    parts = needle.split("/")
    tokens = [re.escape(parts[-1][::-1])]
    for index in range(len(parts) - 2, -1, -1):
        tokens += [_LEADING if index == 0 and not parts[0] else REVERSED_SEPARATOR, re.escape(parts[index][::-1])]
    return [token for token in tokens if token]


def reversed_pattern(needle):
    """``needle`` read backwards as one pattern (``_reversed_tokens``)."""
    return "".join(_reversed_tokens(needle))


def reversed_alternation(spellings):
    """One pattern matching any of ``spellings`` read backwards, its pieces shared where spellings share them (a trie),
    so a place costs the pieces it reads, never the number of spellings: the regex engine tries an alternation's
    branches one by one (a registry of 40 state folders spells 480: 0.6 s of one 250 KB line's 0.75 s was this match).
    No spelling holds another (``needles``), so at most one matches, as with the plain alternation; a longer branch is
    tried before a spelling's end all the same."""
    trie = {}
    for spelling in spellings:
        node = trie
        for token in _reversed_tokens(spelling):
            node = node.setdefault(token, {})
        node[None] = {}

    def emit(node):
        branches = [token + emit(child) for token, child in node.items() if token is not None]
        if None in node:
            branches.append("")
        return branches[0] if len(branches) == 1 else "(?:%s)" % "|".join(branches)
    return emit(trie)


def needles(roots, families):
    """The fewest spellings that find every private path: each root no other root holds, each family as its parent
    and prefix (so leftover members cost nothing), every spelling that holds another left out."""
    bases = [root for root in roots
             if not any(other != root and folded_path(root).startswith(folded_path(other).rstrip(os.sep) + os.sep)
                        for other in roots)]
    found = set().union(*(spellings(base) for base in bases)) if bases else set()
    for parent, prefix in families:
        found |= spellings(Path(parent), os.sep + prefix)
    ordered = sorted({re.sub(r"[\\/]+", "/", item).casefold().rstrip("/") for item in found}, key=len)
    return [needle for index, needle in enumerate(ordered)
            if not any(shorter in needle for shorter in ordered[:index])]
