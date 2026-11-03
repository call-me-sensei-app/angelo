# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the engine link last did for its linked asset, and what each engine's editor said back. No Blender.

``engine_link`` records here every live push, every save and its write-back, and each acknowledgement an editor
sends; ``engine_link_tool_host`` reads it for the agent's ``angelo_engine_link_status`` and for the waits of PUSH
and SAVE. Facts belong to one linked asset: they start empty when a link begins.

**Acknowledgements** (doc/angelo/engine_link.rst, "Acknowledgements"): an editor whose auth frame lists the ``acks``
capability answers each live ``scene`` it shows with ``status`` ``previewed`` (``version``, ``instances``) or
``preview_failed``, and each ``saved`` it re-imports with ``reimported`` or ``reimport_failed`` echoing that frame's
``save``. They are the editor's report (authenticated, same user), never authority: Angelo only relays them. An
editor without ``acks`` may still send some (Unity's and Unreal's earlier plugins sent ``reimported``); they are
kept, but nothing waits for one.

Times are ``time.monotonic()`` here; the agent sees seconds ago. Errors kept here come from the OS, Blender and the
write-back child, whose texts name files: ``plain`` keeps only a path's last name, so nothing the agent reads carries
a path (break-it F1).
"""

import os
import re
import time

ACKS = "acks"   # The capability: this editor acknowledges what it showed and re-imported.
PREVIEW_CODES = frozenset({"previewed", "preview_failed"})
SAVE_CODES = frozenset({"reimported", "reimport_failed"})
MAX_EDITORS = 8   # As many as the server admits (engine_link_server).
MAX_MESSAGE = 120   # Characters of an editor's failure message, and of an error, the agent reads.
_ERRNO = re.compile(r"\[Errno (\d{1,4})\]")
SCAN_MARGIN = 1024   # Characters past the kept text that are still read, so a path cut at the limit is still seen.
# Characters that draw as a slash or backslash without being one, which NFKC keeps; any other character whose
# Unicode name says it is a slash, solidus or diagonal reads as a backslash, which is always a path (break-it 6-5).
_SLASHES = {"∕": "/", "⁄": "/", "⧸": "/", "╱": "/", "∖": "\\", "⧵": "\\", "⧹": "\\", "╲": "\\"}
_NON_ASCII = re.compile(r"[^\x00-\x7f]")
_SPLIT = re.compile(r"[\s'\"`<>()\[\]{},;|=]+")
_PARTS = re.compile(r"[/\\:]+")
# The only tokens with a separator that are names, never places (break-it 5-1: fail closed, anything else is a path).
_NAMES = (re.compile(r"(?:res|user|uid)://[^/\\:~$][^\\:~$]*"),     # Godot's res://, user://, uid:// names.
          re.compile(r"/(?:Game|Engine|Script)/[\w./-]*"),            # Unreal's own mount points.
          re.compile(r"[/\\]|~\d[\w.]*"))                            # A lone slash; "~3 s".
_IN_WORD = re.compile(r"[\w+.-]+(?:/[\w+.-]+)+")   # "Rig/Body", "3/4", "glTF/GLB", "Assets/Ríg.fbx": see _places.
# Where macOS, Windows and Linux keep people's files and mounts: a place wherever the text came from (an editor on
# another platform's paths), whatever this machine's root holds.
PEOPLE_ROOTS = frozenset({"users", "home", "volumes", "mnt", "media", "root"})
_places_read = []


PLACES_SCAN = 5000   # Entries of the root or home folder read at most; past it no slash inside a word is trusted.


def listing(folder):
    """``folder``'s entries one at a time, as the system lists them: never read whole or sorted first, so a walk stops
    at its bound having read no more than it (audit 2026-11-03: a 100,000-entry folder was read whole and sorted
    before the bound was checked). Raises OSError when it can't be listed."""
    entries = os.scandir(folder)
    try:
        yield from entries
    finally:
        getattr(entries, "close", lambda: None)()


def _listed(folder):
    """The names in ``folder``, read entry by entry; OSError past ``PLACES_SCAN`` (``_places`` then trusts none)."""
    names = []
    for entry in listing(folder):
        if len(names) >= PLACES_SCAN:
            raise OSError("too many entries to read")
        names.append(entry.name)
    return names


def _places():
    """Names that put a word on a disk, folded as text is (``_fold``, casefolded): each entry at this machine's
    filesystem root and in the home folder, ``PEOPLE_ROOTS``, each part of the home folder's path, and the user name
    (break-it 6-1, 7-2). Returns (single words, names holding a space, quote or other split character); read once;
    None when the root or home can't be listed, so no slash inside a word is then trusted."""
    if not _places_read:
        import getpass
        import os
        from pathlib import Path
        try:
            home = Path.home()
            names = {*_listed(os.path.abspath(os.sep)), *_listed(home), *home.parts[1:], getpass.getuser()}
            folded = PEOPLE_ROOTS | {_fold(name).casefold() for name in names if name}
            _places_read.append((frozenset(name for name in folded if not _SPLIT.search(name)),
                                 tuple(name for name in folded if _SPLIT.search(name) and name.strip())))
        except (OSError, KeyError, ImportError, RuntimeError):
            _places_read.append(None)
    return _places_read[0]


def _is_path(token):
    """A word that is a place on a disk: one with a separator, "~", "$", ".." or two single colons ("Disk:Users:x",
    never C++'s "std::x" or a time), unless it is an engine's own name, a lone slash, or a slash between word parts
    none of which is a root folder, a part of the home folder or the user name."""
    token = token.rstrip(":.!?")   # Sentence punctuation after a name ("res://a.glb: missing") is not part of it.
    if not any(mark in token for mark in "/\\~$:"):
        return False
    places = _places()
    if places is None or not places[0].isdisjoint(part.casefold() for part in _PARTS.split(token)):
        return True
    if token.count(":") - 2 * token.count("::") >= 2 and "://" not in token:
        return not re.fullmatch(r"[\d:.,]+", token)   # "12:30:45" is a time.
    if not any(mark in token for mark in "/\\~$"):
        return False
    return ".." in token or not (any(pattern.fullmatch(token) for pattern in _NAMES) or _IN_WORD.fullmatch(token))


# A quote that opens a name (at the start, or after a space or "(:[=,") and one that closes it (before the end, a
# space or punctuation): an apostrophe inside a word ("didn't", "Doe's") is neither.
_OPENS = re.compile(r"(?:^|(?<=[\s:(\[=,]))(['\"`\u2018\u201c])")
_CLOSES = re.compile(r"(['\"`\u2019\u201d])(?=$|[\s.,;:!?)\]])")
_CLOSER = {"\u2018": "\u2019", "\u201c": "\u201d"}


def _cut_name(text):
    """Whether ``text`` ends inside a quoted name (a path cut before its first separator: its words are unknown).
    Linear: an opening quote is unclosed when no closing one of its kind comes after it."""
    last = {}
    for match in _CLOSES.finditer(text):
        last[match.group(1)] = match.start()
    return any(last.get(_CLOSER.get(quote, quote), -1) <= start
               for start, quote in ((match.start(1), match.group(1)) for match in _OPENS.finditer(text)))


def _slash(match):
    import unicodedata
    char = match.group(0)
    named = unicodedata.name(char, "")
    return _SLASHES.get(char) or ("\\" if any(word in named for word in ("SOLIDUS", "SLASH", "DIAGONAL")) else char)


def _fold(text):
    """One round of reading ``text`` as its reader would: Unicode compatibility forms, HTML entities, slashes that
    only look like one, JSON's escaped slash, percent-encoding."""
    import html
    import unicodedata
    from urllib.parse import unquote
    return unquote(_NON_ASCII.sub(_slash, html.unescape(unicodedata.normalize("NFKC", text))).replace("\\/", "/"))


def _has_path(text):
    folded = text
    for _round in range(4):   # Each fold may uncover another (an entity inside percent-encoding: break-it 7-3).
        step = _fold(folded)
        if step == folded:
            break
        folded = step
    else:
        return True   # Still changing after four rounds: what it says is unknown, so it counts as a path.
    places = _places()
    lowered = folded.casefold()
    if places is None or any(name + mark in lowered or mark + name in lowered for name in places[1] for mark in "/\\:"):
        return True   # A place whose name holds a space or quote ("Jack Doe/x"), next to a separator.
    return _cut_name(folded) or any(_is_path(token) for token in _SPLIT.split(folded) if token)


def plain(text, limit=MAX_MESSAGE):
    """``text`` for the agent, holding no path (break-it 3-1 .. 5-2). Fail closed: kept as written only when it ends
    inside no quoted name (one cut before its first separator) and no word of it is a path (``_is_path``); otherwise
    rebuilt from structured parts alone (the exception's own type when it is one, Python's own exception names, the
    operating system's words for an ``[Errno N]``). Reads at most ``limit + SCAN_MARGIN`` characters, each once
    (linear: one long line never stalls the main thread). Never raises."""
    import builtins
    import os
    if isinstance(text, OSError) and type(text.errno) is int and 0 < text.errno < 2 ** 31:
        return ("%s: %s" % (type(text).__name__, os.strerror(text.errno)))[:limit]
    kind = [type(text).__name__] if isinstance(text, BaseException) else []   # Never a name read from the text.
    try:
        text = "%s: %s" % (type(text).__name__, text) if isinstance(text, BaseException) else str(text)
    except Exception:   # An exception whose text can't be read: its name alone.
        return type(text).__name__[:limit]
    window = text[:limit + SCAN_MARGIN]
    if not _has_path(window):
        return text[:limit]
    named = [word for word in re.findall(r"\b[A-Z][A-Za-z]{0,60}(?:Error|Exception)\b", window)   # Python's own only:
             if isinstance(getattr(builtins, word, None), type)]                        # never a folder named so.
    parts = list(dict.fromkeys(kind + named))
    parts += [os.strerror(int(number)) for number in dict.fromkeys(_ERRNO.findall(window))]
    return ("; ".join(parts) or "(text that named a file; Angelo's log has it)")[:limit]


def new():
    """The facts of a link that has just begun."""
    return {"push": None, "save": None, "editors": {}}


def _count(value):
    return value if type(value) is int and 0 <= value < 2 ** 53 else None


def pushed(facts, version, size, export_ms, sent):
    facts["push"] = {"version": version, "at": time.monotonic(), "bytes": size, "export_ms": round(export_ms, 1),
                     "editors": sent}


def push_failed(facts, error):
    facts["push"] = dict(facts["push"] or {}, failed=plain(error), failed_at=time.monotonic())


RUN = __import__("secrets").token_hex(8)   # This Angelo process: a save's number alone repeats in the next run.


def save_key(facts, number=None):
    """The exact name of save ``number`` (default: the linked asset's last save), in any run: ``"<RUN>:<number>"``, or
    None before one. The engine preflight is given it and echoes it in its report (``save_key``), which
    ``engine_link_tool_host._describes`` matches (break-it 9-H1, 10-N1: neither a scene version nor a file's identity
    names one save on every volume). A write-back's end passes its own save's ``number``, never the last save's."""
    if number is not None:
        return "%s:%d" % (RUN, number)
    found = facts["save"]
    return found["key"] if found is not None else None


def save_started(facts, number, scene_version, write_back):
    """Save ``number`` of the linked asset began (the ``saved`` frame's ``save``; numbered once per Angelo process,
    never per link, so a save of an earlier link can't pass for this one's: break-it F3). ``write_back``: the asset is
    written from the saved .blend in the background (anything but a .blend)."""
    now = time.monotonic()
    facts["save"] = {"save": number, "at": now, "scene_version": scene_version, "key": save_key(facts, number),
                     "state": "writing" if write_back else "written", "write_back": write_back}
    if not write_back:
        facts["save"]["written_at"] = now
    return number


def save_finished(facts, number, error=None):
    """The write-back of save ``number`` ended (``error``: why the asset wasn't written)."""
    found = facts["save"]
    if found is None or found["save"] != number:
        return
    found.update(state="failed" if error else "written", written_at=time.monotonic())
    if error:
        found["error"] = plain(error)


def save_written_as(facts, number, sha256):
    """The SHA-256 of the file save ``number``'s write-back wrote (``engine_link_saves.written``)."""
    found = facts["save"]
    if found is not None and found["save"] == number and isinstance(sha256, str):
        found["sha256"] = sha256


def from_editor(facts, asset, client, info, header):
    """Keep an editor's ``status`` when it acknowledges the linked ``asset``: only from an editor of its engine and
    project (``info``: what the editor said when it authenticated), about it by its name."""
    from .engine_link_assets import serves
    if header.get("asset") == asset.ident and serves(asset, info):
        acknowledge(facts, client, info, header)


def acknowledge(facts, client, info, header):
    """Keep an editor's acknowledgement of the linked asset (the caller checked that the editor serves it). True
    when the header was one."""
    code = header.get("code")
    if code not in PREVIEW_CODES | SAVE_CODES:
        return False
    editors = facts["editors"]
    if client not in editors and len(editors) >= MAX_EDITORS:
        return False
    entry = editors.setdefault(client, {})
    entry.update(engine=str(info.get("engine", "godot"))[:40], project_name=str(info.get("project_name", ""))[:80])
    now = time.monotonic()
    if code in PREVIEW_CODES:
        entry["preview"] = record = {"code": code, "version": _count(header.get("version")), "at": now,
                                     "instances": _count(header.get("instances"))}
    else:
        entry["save"] = record = {"code": code, "save": _count(header.get("save")), "at": now}
    message = header.get("message")
    if code.endswith("failed") and isinstance(message, str):
        record["message"] = plain(message)
    return True


def new_policy():
    """What the agent's requests to open an asset are held to (``engine_link_agent.refusal``): the agent requests in
    a row the person didn't open (``streak``), and when the latest of them was declined, per caller (``held``), per
    asset (``assets``) and in all (``last_decline``), on the monotonic clock the person's messages are timed on."""
    return {"streak": 0, "held": {}, "assets": {}, "last_decline": None}


def request_ended(policy, found, outcome, now=None):
    """A pending request left (``engine_link._withdraw``). The person opening any request, the engine's own
    included, releases everyone; an agent's request they didn't open holds its caller and asset from now and
    lengthens the streak; an engine's request they didn't open, or one that failed to open, changes nothing
    (break-it 4-2). Holds grow only with the person's own declines."""
    if outcome == "opened":
        policy.update(new_policy())
    elif outcome == "declined" and found.get("by_agent"):
        now = time.monotonic() if now is None else now
        asset = found["asset"]
        policy["streak"] += 1
        policy["held"][found["who"]] = policy["last_decline"] = now
        policy["assets"][(asset.engine, asset.project, asset.ident)] = now


def forget(facts, client):
    """An editor left: its acknowledgements go with it."""
    facts["editors"].pop(client, None)


def save_ack(facts, client, number):
    """The editor's acknowledgement of save ``number``, or None: only one that came once that save's asset was
    written (nothing could re-import it before), and that echoes this number or none (a plugin before ``acks``)."""
    found = (facts["editors"].get(client) or {}).get("save")
    save = facts["save"]
    if found is None or save is None or save["save"] != number or save.get("written_at") is None:
        return None
    if found["at"] < save["written_at"] or found["save"] not in (None, number):
        return None
    return found


def preview_ack(facts, client, version, since):
    """The editor's acknowledgement of live scene ``version`` (or a newer one of the same link) that came at or after
    ``since`` (the push), or None."""
    found = (facts["editors"].get(client) or {}).get("preview")
    return found if found is not None and found["at"] >= since and (found["version"] or 0) >= version else None


def ago(moment, now=None):
    """Seconds since ``moment`` (monotonic), rounded for the agent."""
    return round(max(0.0, (time.monotonic() if now is None else now) - moment), 1)
