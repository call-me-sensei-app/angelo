# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reads the crash log Angelo's crash handler wrote, and keeps only the stack.

The handler (``source/creator/creator_signals.cc``) writes a text file to the
temporary folder (``angelo.crash.txt``, or ``<scene>.crash.txt`` when a file was
open; ``blender.crash.txt`` before the application identity landed). Besides
the stack it holds the Info editor's recent messages, which can name files, objects
and operations, and a Python backtrace with full file paths. **None of that
may leave the computer**, so this is an allowlist, not a scrubber: a line is kept
only if it matches one of the stack-frame grammars below, rebuilt field by
field from the match; everything else is dropped and counted (``dropped``), and
the person is told that. Paths are reduced to a file name, or for Python files to
a path inside the Angelo installation; any other Python file is ``<external>``.

Formats (all produced by Blender's own code, see ``BLI_system_backtrace``):

* macOS ``backtrace_symbols``: ``12  Angelo  0x00000001015a036c  symbol + 56``
* Linux: ``./angelo(symbol+0x38) [0x55d1c7a3b8c4]``
* Windows stack walk: ``Angelo.exe :0x00007FF6A1B2C3D4  symbol C:\\build\\file.cc:120``,
  the Exception Record block, ``Loaded Modules :`` and ``Threads:``
* Python: ``  File "<path>", line 548 in <function>``

Standard library only; no bpy.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

MAX_FILE = 512 * 1024
MAX_FRAMES, MAX_PYTHON, MAX_MODULES, MAX_THREADS, MAX_LINE = 128, 64, 200, 8, 600
MAX_AGE_DAYS = 14
ANGELO_IMAGE = re.compile(r"(?i)(angelo|angelo-launcher)(\.exe)?")
# Characters a C++ or Python symbol name legitimately holds. No path separators, quotes of text or whitespace runs.
_SYMBOL = r"[A-Za-z0-9_$.@<>:,~*&()\[\]=!?'`+\- ]{1,300}?"
_HEADER = re.compile(r"# (?P<name>[A-Za-z]{1,20}) (?P<version>\d{1,3}\.\d{1,3}(?:\.\d{1,3})?(?: [A-Za-z]{1,12})?)"
                     r"(?:, Commit date: (?P<date>\d{4}-\d{2}-\d{2}) (?P<time>\d{2}:\d{2}), Hash (?P<hash>[0-9a-f]{6,40})"
                     r"|, Unknown revision)")
_MAC = re.compile(r"\s*(?P<index>\d{1,3})\s+(?P<image>[^\s/\\]{1,80})\s+(?P<address>0x[0-9a-fA-F]{4,16})\s+"
                  r"(?P<symbol>\S.{0,300}?)(?:\s+\+\s+(?P<offset>\d{1,12}))?\s*")
_LINUX = re.compile(r"(?P<image>[^()\s]{1,300})\((?P<symbol>[^()+\s]{0,300})(?:\+(?P<offset>0x[0-9a-fA-F]{1,12}))?\)"
                    r"\s*\[(?P<address>0x[0-9a-fA-F]{4,16})\]\s*")
_WINDOWS = re.compile(r"(?P<image>[A-Za-z0-9_.+\-]{1,64})\s*:(?P<address>0x[0-9A-Fa-f]{8,16})\s+(?P<rest>\S.{0,500})")
_WINDOWS_SOURCE = re.compile(r"(?P<symbol>.*?)\s+(?P<file>(?:[A-Za-z]:)?[\\/][^:*?\"<>|]{1,400}):(?P<line>\d{1,7})")
_PYTHON = re.compile(r'\s+File "(?P<path>[^"]{1,1000})", line (?P<line>\d{1,7}) in (?P<function>[A-Za-z0-9_<>.]{1,100})\s*')
_EXCEPTION = re.compile(r"(?P<field>ExceptionCode|Exception Address|Exception Module)\s*:\s*(?P<value>\S.{0,200})")
_ACTION = re.compile(r"\s*Parameters\[(?P<index>[01])\]\s*(?:\((?:action|address)\))?\s*:\s*(?P<value>0x[0-9a-fA-F]{1,16})"
                     r"(?: \((?P<action>read|write|execute|unknown)\))?\s*")
_MODULE = re.compile(r"(?P<base>0x[0-9A-Fa-f]{8,16})\s+(?P<version>[0-9][0-9.]{0,30})?\s*(?P<module>[A-Za-z0-9_.+\-]{1,64})(?:\s.*)?")
_THREAD = re.compile(r"Thread\s*:\s*(?P<id>[0-9a-fA-F]{1,8})\s*")
_CODE = re.compile(r"(?P<name>[A-Z][A-Z0-9_]{2,60}) \((?P<code>0x[0-9a-fA-F]{8})\)")
_SECTIONS = {"# backtrace": "native", "# Python backtrace": "python", "Exception Record:": "exception",
             "Stack trace:": "native", "Loaded Modules :": "modules", "Threads:": "threads"}


@dataclass
class ParsedLog:
    build: dict = field(default_factory=dict)       # {name, version, date, hash} of the crashed build, if the header parsed.
    native: list = field(default_factory=list)      # The crashed thread's frames.
    threads: list = field(default_factory=list)     # Other threads (Windows): [{"id", "frames"}].
    python: list = field(default_factory=list)      # [{"file", "line", "function"}]
    exception: dict = field(default_factory=dict)   # Windows Exception Record fields.
    modules: list = field(default_factory=list)     # Windows: [{"module", "version"}].
    dropped: int = 0                                # Lines that matched no grammar and were left out.

    @property
    def images(self):
        found = [frame["image"] for frame in self.native]
        found += [frame["image"] for thread in self.threads for frame in thread["frames"]]
        found.append(os.path.basename(self.exception.get("module", "")))
        return found

    def owned_by_angelo(self):
        """True when the crashed process was Angelo's own executable (a stock Blender may share the temp folder)."""
        return any(ANGELO_IMAGE.fullmatch(image) for image in self.images)


def basename(path):
    return re.split(r"[\\/]", path.rstrip("\\/"))[-1]


def _symbol(text):
    """A symbol name, or "?" when it holds anything outside the symbol alphabet (never a path or free text)."""
    text = text.strip()
    return text if re.fullmatch(_SYMBOL, text) and "\\" not in text else "?"


def _frame_mac(match):
    offset = match.group("offset")
    return {"image": match.group("image"), "address": match.group("address").lower(),
            "symbol": _symbol(match.group("symbol")), "offset": int(offset) if offset else 0}


def _frame_linux(match):
    offset = match.group("offset")
    return {"image": basename(match.group("image")) or "?", "address": match.group("address").lower(),
            "symbol": _symbol(match.group("symbol")) if match.group("symbol") else "",
            "offset": int(offset, 16) if offset else 0}


def _frame_windows(match):
    frame = {"image": match.group("image"), "address": match.group("address").lower()}
    source = _WINDOWS_SOURCE.fullmatch(match.group("rest"))
    if source:
        frame.update(symbol=_symbol(source.group("symbol")), source=basename(source.group("file")),
                     line=int(source.group("line")))
    else:
        frame["symbol"] = _symbol(match.group("rest"))
    return frame


def python_file(path, roots):
    """A Python frame's file inside the installation (``scripts/…``, ``python/…``), else a placeholder."""
    if path.startswith("<"):
        return "<internal>"
    normal = path.replace("\\", "/").lower()
    for root in roots:
        prefix = root.replace("\\", "/").rstrip("/").lower() + "/"
        if normal.startswith(prefix) and ".." not in normal.split("/"):
            relative = path.replace("\\", "/")[len(prefix):]
            if relative.split("/", 1)[0] in {"scripts", "python", "datafiles"} and re.fullmatch(r"[A-Za-z0-9_./+\-]{1,300}", relative):
                return relative
    return "<external>"


def parse(text, roots=()):
    """A ``ParsedLog`` from a crash log's text; ``roots`` are the installation folders Python paths may be relative to.

    Only the header (the first line) and the tail after the **last** ``# backtrace`` line are read. The Info
    messages between them are the one place text of the person's (object names, file names, prompts echoed
    in a report) can appear, and a message can contain a line shaped like a marker; the real marker is written after
    every message, so the last one is the real one. Everything before it is dropped and counted.
    """
    log = ParsedLog()
    lines = [line.rstrip() for line in text.splitlines()]
    markers = [index for index, line in enumerate(lines) if line.strip() == "# backtrace"]
    first = next((index for index, line in enumerate(lines) if line.strip()), None)
    header = _HEADER.fullmatch(lines[first]) if first is not None and lines[first].startswith("# ") else None
    if header:
        log.build = {key: header.group(key) or "" for key in ("name", "version", "date", "time", "hash")}
    tail_start = markers[-1] + 1 if markers else len(lines)
    kept = first if header else None   # The header line becomes ``build``; every other line before the tail is dropped.
    log.dropped = sum(1 for index, line in enumerate(lines[:tail_start - 1 if markers else tail_start])
                      if line.strip() and index != kept)
    section, thread = "native", None
    for line in lines[tail_start:]:
        if not line.strip():
            continue
        if len(line) > MAX_LINE:
            log.dropped += 1
            continue
        if line.strip() in _SECTIONS:
            section, thread = _SECTIONS[line.strip()], None
            continue
        section_frames = log.native if thread is None else thread["frames"]
        if section in {"native", "threads"}:
            match = _THREAD.fullmatch(line) if section == "threads" else None
            if match:
                thread = {"id": match.group("id").lower(), "frames": []}
                if len(log.threads) < MAX_THREADS:
                    log.threads.append(thread)
                continue
            frame = _native_frame(line)
            if frame is not None and len(section_frames) < MAX_FRAMES:
                section_frames.append(frame)
                continue
        elif section == "python":
            match = _PYTHON.fullmatch(line)
            if match and len(log.python) < MAX_PYTHON:
                log.python.append({"file": python_file(match.group("path"), roots), "line": int(match.group("line")),
                                   "function": match.group("function")})
                continue
        elif section == "exception" and _exception_line(line, log.exception):
            continue
        elif section == "modules":
            match = _MODULE.fullmatch(line)
            if match and len(log.modules) < MAX_MODULES:
                log.modules.append({"module": match.group("module"), "version": match.group("version") or ""})
                continue
        log.dropped += 1
    return log


def _native_frame(line):
    for pattern, build in ((_MAC, _frame_mac), (_LINUX, _frame_linux), (_WINDOWS, _frame_windows)):
        match = pattern.fullmatch(line)
        if match:
            return build(match)
    return None


def _exception_line(line, exception):
    match = _EXCEPTION.fullmatch(line.strip())
    if match:
        value = match.group("value").strip()
        if match.group("field") == "ExceptionCode":
            code = _CODE.fullmatch(value)
            if code:
                exception.update(code_name=code.group("name"), code=code.group("code").lower())
                return True
        elif match.group("field") == "Exception Address" and re.fullmatch(r"0x[0-9A-Fa-f]{1,16}", value):
            exception["address"] = value.lower()
            return True
        elif match.group("field") == "Exception Module":
            exception["module"] = basename(value)[:64]
            return True
        return False
    match = _ACTION.fullmatch(line)
    if match:
        if match.group("index") == "0":
            exception["action"] = match.group("action") or "unknown"
        else:
            exception["fault_address"] = match.group("value").lower()
        return True
    return bool(re.fullmatch(r"Nested Exception Record:|Exception (Flags|Parameters)\s*:\s*0x[0-9a-fA-F]{1,16}", line.strip()))


def crash_files(temp_base, now, max_age_days=MAX_AGE_DAYS):
    """Candidate crash logs in the temporary folder, newest first: ``[(path, mtime_ns, size)]``.

    Only ``*.crash.txt`` regular files no larger than ``MAX_FILE`` and younger than ``max_age_days``. Whether one is
    Angelo's is decided by its content (``ParsedLog.owned_by_angelo``): a stock Blender may share the folder.
    """
    found = []
    try:
        entries = list(os.scandir(temp_base))
    except OSError:
        return []
    for entry in entries:
        if not entry.name.endswith(".crash.txt"):
            continue
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if entry.is_symlink() or not entry.is_file(follow_symlinks=False) or not 0 < info.st_size <= MAX_FILE:
            continue
        if now - info.st_mtime > max_age_days * 86400:
            continue
        found.append((Path(entry.path), info.st_mtime_ns, info.st_size))
    return sorted(found, key=lambda item: -item[1])


def read(path):
    """The log's text, decoded leniently; "" when unreadable."""
    try:
        with open(path, "rb") as handle:
            return handle.read(MAX_FILE).decode("utf-8", "replace")
    except OSError:
        return ""
