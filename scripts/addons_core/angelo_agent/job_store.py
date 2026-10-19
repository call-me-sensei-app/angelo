# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Background job records, their file, and what an agent's next message says about them.

No Blender dependency. One store serves every chat (one heavy job runs at a time
across the application). Records survive a restart; a job that was queued or
running when the application ended is reconciled as ``interrupted`` on the next
start, and its staging folder is removed (``job_outputs.discard_path``).

The jobs block of a turn message (``context_entries``) lists this chat's jobs
whose state its native context has not received yet; ``context_ledger`` keeps
the digests, so a finished job is reported once and a running one when its
progress moved.
"""

import json
import os
import re
import time
from pathlib import Path, PurePosixPath, PureWindowsPath

from .job_spec import JOB_ID, PREVIEW

ACTIVE = frozenset({"queued", "starting", "running", "stopping"})
TERMINAL = frozenset({"done", "cancelled", "failed", "interrupted"})
STATUSES = ACTIVE | TERMINAL
MAX_RECORDS = 64
# Finished jobs a full (first) message still lists: the latest few of this chat, within a day.
RECENT_TERMINAL = 5
RECENT_SECONDS = 24 * 3600
FILE_NAME = "jobs.json"
VERSION = 1
SHOWN_VERSION = 1  # Marks made under the proven-input delivery contract.
# What identifies one published output (long_jobs.rst, "Output identity"); a model's entry lists at most MAX_OUTPUTS.
OUTPUT_KEYS = ("file", "view", "views", "node", "slot", "layers", "image", "name", "map", "tile", "cache", "frames")
MAX_OUTPUTS = 24
FRAME_MARK = "####"  # Stands for the frame number in the ``file`` of an animation's output (job_render_outputs).


def new_record(identity, session, call_id, request, *, now):
    return {"id": identity, "session": session, "call_id": call_id, "kind": request["kind"],
            "request": dict(request), "status": "queued", "reason": "", "created": now, "started": None,
            "ended": None, "progress": 0.0, "eta_s": None, "detail": {}, "receipt": {}, "shown": {},
            "shown_version": SHOWN_VERSION}


def _shown(record):
    """Keep earlier, weaker delivery claims as unknown history, separate from new proof."""
    shown = record.get("shown") if type(record.get("shown")) is dict else {}
    if type(record.get("shown_version")) is not int or record["shown_version"] != SHOWN_VERSION:
        if shown:
            record["shown_unconfirmed"] = dict(shown)
        shown = {}
    record["shown"], record["shown_version"] = shown, SHOWN_VERSION
    return shown


def _clean(record):
    """A whole valid record from disk, or None (history is data, never authority)."""
    if (type(record) is not dict or not isinstance(record.get("id"), str) or not JOB_ID.fullmatch(record["id"])
            or record.get("status") not in STATUSES or not isinstance(record.get("session"), str)
            or not isinstance(record.get("kind"), str) or type(record.get("request")) is not dict):
        return None
    for key, kind in (("detail", dict), ("receipt", dict), ("reason", str), ("call_id", str), ("shown", dict)):
        if type(record.get(key)) is not kind:
            record[key] = kind()
    for key in ("created", "started", "ended", "eta_s"):
        if not (record.get(key) is None or type(record.get(key)) in (int, float)):
            record[key] = None
    if type(record.get("progress")) not in (int, float):
        record["progress"] = 0.0
    _shown(record)
    return record


class JobStore:
    """``jobs.json`` in the agent state folder: the latest MAX_RECORDS records, oldest first."""

    def __init__(self, directory):
        self.path = Path(directory) / FILE_NAME
        self.records = {}
        self.next_number = 1
        self._saved_at = 0.0
        self.unsaved = False  # A ``shown`` mark made off the main thread waits for the next save (mark_shown).
        self.load()

    def load(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            raw = {}
        records = raw.get("jobs") if type(raw) is dict and raw.get("version") == VERSION else []
        for record in records if type(records) is list else ():
            record = _clean(record)
            if record is not None:
                self.records[record["id"]] = record
        number = raw.get("next") if type(raw) is dict else None
        highest = max((int(identity[1:]) for identity in self.records), default=0)
        self.next_number = max(highest + 1, number if type(number) is int and number > 0 else 1)

    def allocate(self):
        identity = self.peek()
        self.next_number += 1
        return identity

    def peek(self):
        """The ID ``allocate`` gives next."""
        return "j%d" % self.next_number

    def add(self, record):
        self.records[record["id"]] = record
        while len(self.records) > MAX_RECORDS:
            oldest = next((key for key, value in self.records.items() if value["status"] in TERMINAL), None)
            if oldest is None:
                break
            del self.records[oldest]
        self.save()

    def get(self, identity):
        return self.records.get(identity)

    def remove(self, identity):
        """Forget one record (a finished look: its tool call already answered for it)."""
        if self.records.pop(identity, None) is not None:
            self.save()

    def save(self, *, throttle=0.0, now=None):
        """Atomic write; ``throttle`` skips a progress-only save made sooner than that many seconds."""
        now = time.monotonic() if now is None else now
        if throttle and now - self._saved_at < throttle:
            return False
        payload = json.dumps({"version": VERSION, "next": self.next_number, "jobs": list(self.records.values())},
                             ensure_ascii=False, allow_nan=False)
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(temporary, self.path)
        except OSError:
            return False
        self._saved_at, self.unsaved = now, False
        return True

    def interrupted(self, *, now):
        """Records a previous run left queued or running, now ``interrupted`` (startup reconciliation)."""
        changed = []
        for record in self.records.values():
            if record["status"] in ACTIVE:
                record.update(status="interrupted", reason="app_closed", ended=now, eta_s=None)
                changed.append(record)
        if changed:
            self.save()
        return changed


def _scalar(value):
    return type(value) is int or (type(value) is str and len(value) <= 256)


def output_entries(receipt):
    """A receipt's published outputs as clean, bounded data (a record on disk is data, never authority)."""
    outputs = receipt.get("outputs")
    clean = []
    for item in outputs if type(outputs) is list else ():
        entry = {key: item[key] for key in OUTPUT_KEYS if type(item) is dict and key in item and (
            _scalar(item[key]) or (type(item[key]) is list and len(item[key]) <= 32 and all(map(_scalar, item[key]))))}
        if entry:
            clean.append(entry)
    return clean


def output_name(record):
    """A job's workspace-relative output as it is really named: the receipt's when it ended, where it writes while it
    runs when its name was taken (job_host), the admission's when several files made a still's output a folder, else
    the request's."""
    detail = record["detail"]
    return (record["receipt"].get("output") or detail.get("writes") or detail.get("output")
            or record["request"].get("output"))


def output_file_name(record, name, frame=None):
    """The published file one output of a finished job names, relative to the job's output, or None.

    ``name`` is an output's ``file`` exactly as the job reported it. An
    animation reports a pattern with #### for the frame number: ``frame`` picks
    the frame, which must be one of the job's. Anything the job did not report
    is None, so a caller can never be steered to another file.
    """
    if not isinstance(name, str) or not any(entry.get("file") == name for entry in output_entries(record["receipt"])):
        return None
    if FRAME_MARK not in name:
        return name if frame is None else None
    frames = record["detail"].get("frames")
    if type(frame) is not int or type(frames) is not list or len(frames) != 2 or not frames[0] <= frame <= frames[1]:
        return None
    return name.replace(FRAME_MARK, "%.4d" % frame)


def published_file(base, record, name=None, frame=None):
    """The file of one output below a job's published output ``base`` (a path or None), or None.

    Without a name, ``base`` itself when the job published one file. With one, only a name the job
    reported (``output_file_name``) that still exists and is no link: never a path of the caller's making.
    """
    if base is None:
        return None
    if name is None:
        return base if regular_output(base) else None
    relative = output_file_name(record, name, frame)
    if (relative is None or "\\" in relative or "\0" in relative or PureWindowsPath(relative).drive
            or PurePosixPath(relative).is_absolute()
            or ".." in PurePosixPath(relative).parts):
        return None
    path = base.joinpath(*PurePosixPath(relative).parts)
    return path if regular_output(path) else None


def regular_output(path):
    """Whether a published path is a regular file, reached without following any directory or file link.

    This is discovery, not permission to reopen by path. A consumer must still
    anchor its read (``copy_output``) because an external process can rename it.
    """
    from . import checkpoint_filesystem as cf
    try:
        cf.require_anchored_files()
        with cf._private_directory(path.parent) as folder:
            cf._file_info(folder, path.name)
        return True
    except (OSError, ValueError):
        return False


def copy_output(path, destination, maximum):
    """Copy one published file to private storage through held handles, with a bounded stable read.

    Decoders need paths, so they consume this private copy rather than reopening
    an output whose ancestors or leaf can change after discovery. The existing
    file helper rejects links and detects a write during the streaming copy.
    """
    from . import checkpoint_filesystem as cf
    cf.require_anchored_files()
    with cf._private_directory(path.parent) as folder:
        before = cf._file_info(folder, path.name)
        opened = cf.copy_stable_file(folder, path.name, destination, maximum=maximum)
        after = cf._file_info(folder, path.name)
        fields = ("st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")
        if (not cf.same_file(before, opened) or not cf.same_file(opened, after)
                or any(getattr(before, field) != getattr(opened, field) or
                       getattr(opened, field) != getattr(after, field) for field in fields)):
            raise ValueError("The published file changed during access")
        cf.assert_directory_identity(path.parent, folder)


def output_names(folder):
    """Regular file names of an output folder, without following links; empty if it cannot be anchored."""
    from . import checkpoint_filesystem as cf
    try:
        cf.require_anchored_files()
        with cf._private_directory(folder) as held:
            names = []
            for name in cf.iter_names(held):
                try:
                    cf._file_info(held, name)
                except (OSError, ValueError):
                    continue
                names.append(name)
            return names
    except (OSError, ValueError):
        return []


def published_frames(base, record, name):
    """The frames of the pattern output ``name`` whose files exist below ``base``, in order; [] for any other name."""
    frames, relative = record["detail"].get("frames"), PurePosixPath(name if isinstance(name, str) else "")
    if (type(frames) is not list or len(frames) != 2 or output_file_name(record, name, frames[0]) is None
            or relative.is_absolute() or ".." in relative.parts or base is None):
        return []
    folder = base.joinpath(*relative.parent.parts)
    head, tail = relative.name.split(FRAME_MARK, 1)
    pattern = re.compile(re.escape(head) + r"(-?[0-9]{4,})" + re.escape(tail))
    numbers = {int(match.group(1)) for match in map(pattern.fullmatch, output_names(folder)) if match}
    return sorted(number for number in numbers if published_file(base, record, name, number) is not None)


def _rounded(value, step):
    return None if value is None else int(round(value / step) * step)


def context_entry(record, *, position=None):
    """One job's compact, model-facing status (no absolute paths, no local identifiers)."""
    entry = {"job": record["id"], "kind": record["kind"], "status": record["status"]}
    request, detail, receipt = record["request"], record["detail"], record["receipt"]
    output = output_name(record)
    if output:
        entry["output"] = output
    if record["status"] == "queued" and position:
        entry["position"] = position
    if record["status"] in {"running", "stopping", "starting"}:
        entry["progress"] = round(float(record["progress"]), 2)
        eta = _rounded(record.get("eta_s"), 5)
        if eta is not None:
            entry["eta_s"] = eta
    if detail.get("frames_total"):
        entry["frames"] = "%d/%d" % (detail.get("frames_done", 0), detail["frames_total"])
    if record["status"] in TERMINAL:
        if record.get("started") and record.get("ended"):
            entry["seconds"] = round(record["ended"] - record["started"], 1)
        outputs = output_entries(receipt)
        for key in ("files", "images", "caches", "kept", "missing", "unexpected", "unrestored", "note"):
            if receipt.get(key) and not (key == "images" and outputs):  # Each output names its image itself.
                entry[key] = receipt[key]
        if outputs:
            entry["outputs"] = outputs[:MAX_OUTPUTS]
            if len(outputs) > MAX_OUTPUTS:
                entry["outputs_more"] = len(outputs) - MAX_OUTPUTS
        if record["status"] != "done" and record["reason"]:
            entry["reason"] = record["reason"][:300]
    return entry


def context_entries(store, session, queue_order=(), *, now):
    """``{job id: entry}`` for one chat: active jobs and its recent finished ones.

    Safe on a provider worker thread: copying a dict or list is atomic under the
    GIL, so it reads a snapshot while the main thread advances the jobs.
    """
    mine = [dict(record) for record in list(store.records.values())
            if record["session"] == session and record["kind"] != PREVIEW]  # A look answers through its own call.
    queue_order = list(queue_order)
    recent = [record for record in mine if record["status"] in TERMINAL and record.get("ended")
              and now - record["ended"] <= RECENT_SECONDS][-RECENT_TERMINAL:]
    positions = {identity: index for index, identity in enumerate(queue_order, 1)}
    return {record["id"]: context_entry(record, position=positions.get(record["id"]))
            for record in mine if record["status"] in ACTIVE or record in recent}


def mark_shown(store, identity, what, *, now=None, file=None):
    """Record that a job reached the model: its ``result`` (the finished entry of a jobs block the provider
    accepted, ``context_ledger``) or its ``image`` (a view that shows the job's picture, ``request_path``).

    ``record["shown"]`` keeps the first time of each as ``result_at`` and ``image_at`` (epoch seconds) and counts the
    pictures in ``images``; missing marks mean delivery was not established, not proof it never happened.
    Unconfirmed Codex tool-image writes never mark a job shown. For a job that published several pictures,
    ``file`` is the output a view showed, as the job reported it: ``files`` lists each such output once (at most
    MAX_OUTPUTS; a name the job did not report is not kept). Safe on any thread: it only sets the record in
    memory, and the next save on the main thread writes it (``unsaved``).
    """
    record = store.get(identity) if isinstance(identity, str) else None
    if record is None or what not in ("result", "image"):
        return False
    shown = _shown(record)
    shown.setdefault(what + "_at", round(time.time() if now is None else now, 1))
    if what == "image":
        shown["images"] = shown.get("images", 0) + 1
        files = shown.get("files") if type(shown.get("files")) is list else []
        if (isinstance(file, str) and file not in files and len(files) < MAX_OUTPUTS
                and any(entry.get("file") == file for entry in output_entries(record.get("receipt") or {}))):
            shown["files"] = files + [file]
    store.unsaved = True
    return True
