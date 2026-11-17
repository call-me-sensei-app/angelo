# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The merge check's reading of written bytes, streamed (``worker_merge_verify``; break-it E2E-20 F-L-2, BREAKIT-C7A-7
H1 and L2): every byte of a datablock is read, whatever its size, in bounded memory and in time linear in its size.

A datablock's bytes are read ``CHUNK`` bytes at a time and searched in C (``bytes.lower``, ``bytes.find``) for each
place a private folder's name could be: each folder's last name (``PrivateFolders.literals``) and each own folder's
name (``names``), by the start (``ANCHOR`` letters) of its longest run of letters no text transport escapes
(``[a-z0-9._~-]``, three or more), in any ASCII case, as UTF-8 and as UTF-16 either way round. Only the bytes
around each place found are read as text, in the views a text transport gives them (``views``: as UTF-8 for a place
found as UTF-8, as UTF-16 either way round and either alignment for one found as UTF-16, each as written and with
percent- and form-encoding, HTML entities and backslash escapes undone until nothing changes), folded as the spellings
are (``str.casefold``): an own folder's name there is a leak; a last name after a separator is read as a path, in each
view it was found in, with all the context the cleaning's rule reads around it (``DETOUR`` characters after it, and
before it as many plus the longest spelling, further through a run of separators), and what ``holds`` calls private
is a leak. The bytes around a place are read wider until they hold that context or the datablock ends; a place that
still lacks it at ``MARGIN`` bytes either side cannot be read, and the check fails closed (``Unreadable``). Places so
many that their windows would cover a chunk make the whole chunk read as text. A name with no such run (one of
three letters, or in another script) is searched in the text views of every chunk instead: slower, as rare.

Out of scope, as for the cleaning (documented in ``doc/angelo/agent_integration.rst``): a spelling no text transport
makes, which takes deliberate obfuscation: base64, compression or encryption of a path, a name's own letters escaped
(a transport escapes separators, spaces and other scripts, never ``[a-z0-9._~-]``) or replaced by look-alikes that
fold to them ("ſ" for "s"), escapes nested more than ``DECODINGS`` deep; and a path a program assembles (``Path.home()
/ "Library"``, strings joined), which no text spells whole. A "+" is read as a space only in a run of text that holds
a percent escape, as a form-encoded value does (its separators are ``%2F``).
"""

import html
import re

from .worker_merge_paths import DETOUR, fold

CHUNK = 8 << 20   # Bytes searched at a time.
REACH = 32   # Bytes a letter can take in a spelling the views decode (an escape inside escapes: "%252525C3").
PIECE = 1 << 20   # Most bytes around places found read as text at once.
SLACK = 64   # Characters a view can gain or lose where its bytes are cut (a letter, an escape across the cut).
MARGIN = 8 << 20   # Most bytes read either side of a place before the check gives up on it (fails closed).
DECODINGS = 4   # Rounds of decoding (an encoded path inside an encoded URL inside JSON is three).
ANCHOR = 6   # Most letters of a name searched in bytes: enough to be rare in data, few enough to be shared.
WIDE = (("utf-16-le", 0), ("utf-16-le", 1), ("utf-16-be", 0), ("utf-16-be", 1))   # Views 1 to 4, by units' parity.
NARROW, WIDENED = frozenset((0,)), frozenset(range(1, len(WIDE) + 1))   # The families a place found is read in.
_UNESCAPED = re.compile(r"[a-z0-9._~-]{3,}")   # Letters no text transport escapes, folded.
_PERCENT = re.compile(r"(?:%[0-9a-fA-F]{2})+")
# A form-encoded value: a run of text between delimiters that holds a percent escape, where a "+" is a space; a "+"
# elsewhere is the character (a person's "state+backup" folder; BREAKIT-C7A-7 review M-2). Possessive, and tried at a
# run's start only, so text costs its length.
_FORM = re.compile(r"(?<![^\s\"'<>&=?#])[^\s\"'<>&=?#%]*+%[^\s\"'<>&=?#]*+")
_ENTITY = re.compile(r"&(?:#[0-9]+;?|#[xX][0-9a-fA-F]+;?|[A-Za-z][A-Za-z0-9]{0,31};?)")
# Backslash escapes: JSON's and Python's code points (``\u``); bytes as Python writes them (``\xc3\x84``) and as git and
# ``ls -b`` do (``\303\204``), a run of them read as UTF-8, else a character each; a backslash before any other ASCII
# mark or a space, as JSON and shells write them (``\/``, ``Application\ Support``; review H-A and M-3).
ESCAPES = re.compile(r"\\(?:u([0-9a-fA-F]{4})|((?:x[0-9a-fA-F]{2}|[23][0-7]{2})(?:\\(?:x[0-9a-fA-F]{2}|[23][0-7]{2}))*)"
                     r"|([ -/:-@\[-`{-~]))")
_LINE = re.compile(r"[\n\0]")   # Where a path in text ends at the latest (``PrivateFolders.path_end``).
_RUN_BYTES = b"/\\.%25fFcC\0"   # Bytes a run of separators can be spelled with (as UTF-8, escaped, or UTF-16).


class Unreadable(Exception):
    """A place in written bytes that could not be read with all its context: the check fails closed."""


def views(chunk, families=None, at=0):
    """``(family, text)``: ``chunk`` (bytes, at offset ``at`` of the file) as each text a text transport could make of
    it, each then decoded until nothing changes: as UTF-8 (family 0); as UTF-16, either byte order, its units at even
    or odd offsets of the file (families 1 to 4, so one family reads the same units in any piece of it)."""
    for family in range(len(WIDE) + 1):
        if families is None or family in families:
            for text in texts(chunk, family, at=at):
                yield family, text


def texts(chunk, family, plan=None, at=0):
    """``chunk``'s texts in one family of ``views`` (``chunk`` at offset ``at``): as read, then each text transport
    undone (``_STEPS``) a view of its own, round after round until nothing changes (at most ``DECODINGS`` rounds);
    with ``plan`` (``decoding``'s), exactly those steps, so two pieces of one window decode alike. UTF-16 text is
    decoded as UTF-8 text is (a URL or JSON a Windows tool keeps as UTF-16; BREAKIT-C7A-7 review H-B)."""
    return decoding(chunk, family, plan, at)[0]


def decoding(chunk, family, plan=None, at=0):
    """``(texts, plan)``: ``texts`` as above, and the steps that made each text after the first. One step per view,
    never one per round: a backslash escape undone with the HTML entity or percent escape that spelled the backslash
    read "\\.config" as ".config", joined to the folder before it (the cut's macOS run, 7059a1b)."""
    if family:
        codec, parity = WIDE[family - 1]
        found = [chunk[(parity - at) % 2:].decode(codec, "replace")]
    else:
        found = [chunk.decode("utf-8", "surrogateescape")]
    if plan is not None:
        for step in plan:
            found.append(_STEPS[step](found[-1]))
        return found, list(plan)
    plan = []
    for _ in range(DECODINGS):
        changed = False
        for step, undo in enumerate(_STEPS):
            decoded = undo(found[-1])
            if decoded != found[-1]:
                found.append(decoded)
                plan.append(step)
                changed = True
        if not changed:
            break
    return found, plan


def _percents(text):
    """Form- and percent-encoding undone (a run of escapes as UTF-8, as ``unquote_plus``), each where it is."""
    if "%" not in text:
        return text
    if "+" in text:
        text = _FORM.sub(lambda match: match.group().replace("+", " ") if _PERCENT.search(match.group())
                         else match.group(), text)
    return _PERCENT.sub(lambda match: bytes.fromhex(match.group().replace("%", "")).decode("utf-8", "surrogateescape"),
                        text)


def _entities(text):
    """HTML entities undone, each where it is."""
    return _ENTITY.sub(lambda match: html.unescape(match.group()), text) if "&" in text else text


def _escapes(text):
    """Backslash escapes undone, each where it is."""
    return ESCAPES.sub(_unescaped, text) if "\\" in text else text


_STEPS = (_percents, _entities, _escapes)   # One text transport each, in the order a round undoes them.


def _unescaped(match):
    code, run, mark = match.groups()
    if code:
        return chr(int(code, 16))
    if mark:
        return mark
    data = bytes(int(piece[1:], 16) if piece[0] == "x" else int(piece, 8) for piece in run.split("\\"))
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


class Scanner:
    """One merge's search of written bytes for ``private`` (``PrivateFolders``)."""

    def __init__(self, private):
        self.private = private
        self.literals = sorted(set(private.literals), key=len, reverse=True)
        self.names = sorted(set(private.names))
        self.longest = max((len(needle) for needle in private.needles), default=0)
        words = set(self.literals) | set(self.names)
        anchors, self.plain = set(), []
        for word in words:
            runs = _UNESCAPED.findall(word)   # Its longest run's start: names alike (a family's) share one.
            if runs:
                anchors.add(max(runs, key=len)[:ANCHOR].encode("ascii"))
            else:
                self.plain.append(word)
        anchors = sorted(anchors, key=len)   # One that holds a shorter one is found by it.
        anchors = [anchor for index, anchor in enumerate(anchors) if not any(other in anchor
                                                                           for other in anchors[:index])]
        # Each as UTF-8, read in the UTF-8 views, and as UTF-16 (either way round: its NULs between), in those.
        self.anchors = [(anchor, NARROW) for anchor in anchors] + [
            (anchor.decode("ascii").encode("utf-16-le")[:-1], WIDENED) for anchor in anchors]
        self.reach = REACH * (max(map(len, words), default=0) + 4)
        self.overlap = self.reach + max((len(anchor) for anchor, _families in self.anchors), default=0)
        self.near = 4 * (DETOUR + self.longest + SLACK)   # Bytes first read either side (UTF-16: 2 a letter).
        # A last name after a separator in folded text (``finds`` matches nothing else), the class first so the
        # search skips text without one in C; and a spelling that is one name, a variable's (BREAKIT-C7A-10 M1).
        self.occurrences = [re.compile(r"[\\/%%](?:(?<=%%)(?:2f|5c))?(?<!%%)(?:%s)" % "|".join(
            map(re.escape, self.literals)))] if self.literals else []
        whole = sorted({needle for needle in private.needles if "/" not in needle}, key=len, reverse=True)
        self.occurrences += [re.compile("|".join(map(re.escape, whole)))] if whole else []

    def hit(self, reader, lo, hi):
        """What in ``reader``'s bytes ``[lo, hi)`` is a private path or an own folder's name, or None. Raises
        ``Unreadable`` when a place cannot be read with its context."""
        windows, candidates = _Runs(PIECE, 0), _Runs(PIECE, 2 * self.near)
        for base in range(lo, hi, CHUNK):
            top = min(hi, base + CHUNK + self.overlap)
            chunk = reader.read(base, top)
            if self.plain:
                found = self._plain(reader, lo, hi, base, top, chunk)
                if found is not None:
                    return found
            low, own, places = chunk.lower(), min(CHUNK, top - base), []
            for anchor, families in self.anchors:   # Only places that start in this chunk's own bytes: the next
                stop, found = own + len(anchor) - 1, []   # has the rest.
                dense = own // (2 * self.reach + len(anchor)) + 1   # So many places that windows cover it all.
                at = low.find(anchor, 0, stop)
                while at >= 0 and len(found) < dense:
                    found.append((at, at + len(anchor), families))
                    at = low.find(anchor, at + 1, stop)
                places += found if len(found) < dense else [
                    (at, min(own, at + PIECE // 2), families) for at in range(0, own, PIECE // 2)]
            del low, chunk
            for start, end, families in sorted(places):
                for window in windows.add(max(lo, base + start - self.reach), min(hi, base + end + self.reach),
                                          families):
                    found = self._window(reader, lo, hi, window, candidates)
                    if found is not None:
                        return found
        for window in windows.flush():
            found = self._window(reader, lo, hi, window, candidates)
            if found is not None:
                return found
        for region in candidates.flush():
            found = self._analyse(reader, lo, hi, region)
            if found is not None:
                return found
        return None

    def _window(self, reader, lo, hi, window, candidates):
        """The bytes around places found (``window``: [start, end) and the view families they were found for) read as
        text: an own folder's name is a leak; a last name after a separator makes the window a candidate, analysed
        once nothing more joins it."""
        families = set()
        for family, text in views(reader.read(window[0], window[1]), window[2], window[0]):
            folded = text.casefold()
            for name in self.names:
                if name in folded:
                    return name
            if any(pattern.search(folded) for pattern in self.occurrences):
                families.add(family)
        if families:
            for region in candidates.add(window[0], window[1], families):
                found = self._analyse(reader, lo, hi, region)
                if found is not None:
                    return found
        return None

    def _plain(self, reader, lo, hi, base, top, chunk):
        """The words with no run to search in bytes, in the chunk's text views: a name is a leak; a last name makes
        the whole chunk a candidate, analysed a piece at a time."""
        families = set()
        for family, text in views(chunk, at=base):
            folded = text.casefold()
            for word in self.plain:
                if word in folded:
                    if word in self.names:
                        return word
                    families.add(family)
        for start in range(base, top, PIECE) if families else ():
            found = self._analyse(reader, lo, hi, (start, min(top, start + PIECE), families))
            if found is not None:
                return found
        return None

    def _analyse(self, reader, lo, hi, region):
        """The private path whose folder's last name ends in ``region`` ([start, end) and the families to read), read
        with as much around it as the cleaning's rule reads, or None."""
        start, end, families = region
        back = forth = self.near
        while True:
            low, high = _run_start(reader, lo, max(lo, start - back)), min(hi, end + forth)
            data = reader.read(low, high)
            short_back = short_forth = False
            for family in sorted(families):
                whole, plan = decoding(data, family, at=low)
                heads = texts(data[:start - low], family, plan, low)
                cores = texts(data[start - low:end - low], family, plan, start)
                for text, head, core in zip(whole, heads, cores):
                    first, last = len(head) - SLACK, len(head) + len(core) + SLACK
                    lacks_back, lacks_forth = self._short(text, first, last, low > lo, high < hi)
                    short_back, short_forth = short_back or lacks_back, short_forth or lacks_forth
                    if not (lacks_back or lacks_forth):
                        found = self._path(text, first, last)
                        if found is not None:
                            return found
            if not (short_back or short_forth):
                return None
            if (short_back and back >= MARGIN) or (short_forth and forth >= MARGIN):
                raise Unreadable("a place in written bytes lacks its context within %d bytes" % MARGIN)
            back, forth = back * (2 if short_back else 1), forth * (2 if short_forth else 1)

    def _short(self, text, first, last, cut_back, cut_forth):
        """Whether a last name after a separator ending in ``[first, last]`` of ``text`` lacks context before it
        (``cut_back``: the bytes start inside the datablock) or after it. Only the names near either end can: each end
        is searched in C, never every name one at a time (BREAKIT-C7A-9 L1: dense names in UTF-16)."""
        folded, back = fold(text)
        grown = len(folded) - len(text)   # A folded offset is at most this much past its text's.

        def any_end(low, high, after=-1):   # A name after a separator whose end (in text) is in [low, high], > after.
            low, high = max(low, first), min(high, last)
            for pattern in self.occurrences:
                for match in pattern.finditer(folded, max(0, low - 64), min(len(folded), high + grown + 64)):
                    end = back(match.end() - 1) + 1 if back else match.end()
                    if low <= end <= high and end > after:
                        return True
            return False
        zone = DETOUR + self.longest + SLACK
        lacks_back = cut_back and any_end(0, zone - 1)
        line = max(text.rfind("\n"), text.rfind("\0"))   # Past it, a path's end needs no more text.
        lacks_forth = cut_forth and any_end(len(text) - DETOUR - SLACK + 1, len(text), after=line)
        return lacks_back, lacks_forth

    def _path(self, text, first, last):
        """The first private path in ``text`` whose folder's spelling ends in ``[first, last]``, as the cleaning
        decides it (``PrivateFolders.finds``, ``path_end``), or None."""
        for start, found in self.private.finds(text):
            if first <= found <= last:
                end = self.private.path_end(text, start, found)
                if end is not None:
                    return text[start:end]
        return None


class _Runs:
    """Ranges given in order, joined while they touch (or are within ``gap``) and stay within ``size`` bytes;
    ``add`` returns the ranges that can no longer grow, ``flush`` the last."""

    def __init__(self, size, gap):
        self.size, self.gap, self.current = size, gap, None

    def add(self, start, end, families=frozenset()):
        current = self.current
        if current is not None and start <= current[1] + self.gap and max(end, current[1]) - current[0] <= self.size:
            self.current = (current[0], max(end, current[1]), current[2] | set(families))
            return ()
        self.current = (start, end, set(families))
        return (current,) if current is not None else ()

    def flush(self):
        current, self.current = self.current, None
        return (current,) if current is not None else ()


class FileReader:
    """Bytes of an open file, read where asked."""

    def __init__(self, file):
        self.file = file
        file.seek(0, 2)
        self.size = file.tell()

    def read(self, start, end):
        self.file.seek(start)
        return self.file.read(max(0, end - start))


class BytesReader:
    """Bytes in memory, read as a file's are."""

    def __init__(self, data):
        self.data, self.size = data, len(data)

    def read(self, start, end):
        return self.data[start:end]


def _run_start(reader, lo, low):
    """``low`` moved back to where a run of separators crossing it starts, so a window never starts inside one (a path
    spelled with a run longer than any margin is read through it whole, in one linear step). Raises ``Unreadable`` past
    ``MARGIN``."""
    step, start = 4096, low
    while low > lo:
        if start - low > MARGIN:
            raise Unreadable("a run of separators longer than %d bytes" % MARGIN)
        first = max(lo, low - step)
        chunk = reader.read(first, low)
        kept = len(chunk.rstrip(_RUN_BYTES))
        low = first + kept
        if kept:
            break
        step *= 2
    return low
