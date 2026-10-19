# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a one-shot run left behind besides the still its brief asked for.

A failed "the final still was rendered and saved" check says only that no
file of the asked name was found. It does not say whether the side rendered
nothing at all, or rendered and saved under another name: a review still, or
a render job whose result it never saw before it ended its turn. Those are
different failures, so every run records what it did write:

* ``images``: the image files written after the message was sent, in the
  folders only this side writes to (its file workspace, its Blender's own
  temporary folder, its trial folder, the client's working folder), where
  the scene's render path points, and at any path the side itself named in
  its reply, its scripts or its job requests. A folder other programs write
  to as well (the system's temporary folder, the home folder) is never
  walked: a file there counts only when the side named it or the scene's
  render path is that file, so another program's picture is never taken for
  the side's work. The harness's own folders and its copy of the still
  (``agent-final.*``) are left out.
* ``text``: the same with the side's render jobs, one cell of a report.
* ``missing_still``: the sentence for "Why a side did not pass".

Names, pixel sizes and bytes only; no full path is printed. Standard library
only (``scenario_checks.image_size`` reads a header, nothing is decoded).
"""

import os
import re
import tempfile

# Folders of the harness's own files inside a side's folders: never a side's work.
HARNESS_FOLDERS = frozenset({"references", "scenario-capture", "oneshot-final", "report-assets", "demo", "stages",
                             "blender-user", "checkpoints", "snapshot-store", "sessions", "accounts", "config",
                             "codex-sqlite"})
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".exr", ".tif", ".tiff", ".webp", ".bmp", ".tga", ".hdr")
_SUFFIX = r"\.(?:%s)" % "|".join(suffix[1:] for suffix in IMAGE_SUFFIXES)
# A path as plain text ends at a blank; inside quotes (a script's string, a JSON value) it may hold blanks.
STATED = re.compile(r"[\"']((?:~|/)[^\"'\n\\<>|*?]*%s)\\?[\"']|(?<![\w.~-])(~?/[^\s\"'`<>|*?\\]*%s)(?![A-Za-z0-9])"
                    % (_SUFFIX, _SUFFIX), re.IGNORECASE)
MAX_DEPTH, MAX_ENTRIES, SLACK_S = 4, 20000, 2.0


def stated(text):
    """Absolute or home-relative image paths a text states (a reply, a script, a job request), in order."""
    found = []
    for match in STATED.finditer(str(text or "")):
        path = os.path.expanduser(match.group(1) or match.group(2))
        if path not in found:
            found.append(path)
    return found[:200]


def shared(folder):
    """Whether other programs write to a folder too: the system's temporary folders, the home folder, the root."""
    places = (tempfile.gettempdir(), "/tmp", "/var/tmp", os.path.expanduser("~"), os.sep)
    return os.path.realpath(str(folder)) in {os.path.realpath(place) for place in places}


def _own(roots):
    """Image files under the folders that are this side's alone, at most ``MAX_DEPTH`` folders down."""
    found, visited = [], 0
    for root in dict.fromkeys(os.path.realpath(str(root)) for root in roots if root):
        if shared(root):
            continue
        for folder, names, files in os.walk(root):
            depth = 0 if folder == root else os.path.relpath(folder, root).count(os.sep) + 1
            names[:] = sorted(name for name in names if not name.startswith(".") and name not in HARNESS_FOLDERS) \
                if depth < MAX_DEPTH else []
            visited += len(files)
            found += [os.path.join(folder, name) for name in sorted(files)]
            if visited > MAX_ENTRIES:
                break
    return found


def _at_render_path(render_path):
    """Files a plain render writes for a scene's render path: the path itself, with a suffix, with a frame number."""
    if not render_path:
        return []
    path = os.path.abspath(str(render_path))
    if os.path.isdir(path):
        folder, stem = path, ""
    else:
        folder, stem = os.path.dirname(path), os.path.splitext(os.path.basename(path))[0]
    try:
        names = sorted(os.listdir(folder))[:5000]
    except OSError:
        return []
    if not stem and shared(folder):
        return []   # Blender's default path: every program's frames land there, none can be called this side's.
    numbered = re.compile(re.escape(stem) + r"\d{0,6}$")
    return [os.path.join(folder, name) for name in names
            if numbered.match(os.path.splitext(name)[0]) and (stem or os.path.splitext(name)[0])]


def images(roots, since=None, render_path="", said="", limit=20):
    """The image files a run wrote: ``[{"file", "size": [w, h] or None, "bytes", "where"}]``, newest first.

    ``since``: the wall-clock time the message was sent (an older file is not
    this run's). ``where`` is ``own folder``, ``render path`` or ``named by
    the side``.
    """
    import scenario_checks
    candidates = [(path, "own folder") for path in _own(roots)]
    candidates += [(path, "render path") for path in _at_render_path(render_path)]
    candidates += [(path, "named by the side") for path in stated(said)]
    found = {}
    for path, where in candidates:
        name = os.path.basename(path)
        real = os.path.realpath(path)
        # A reference the side read by its path, a harness render: in a harness folder, not the side's work.
        if (not name.lower().endswith(IMAGE_SUFFIXES) or name.startswith("agent-final") or real in found
                or HARNESS_FOLDERS.intersection(real.split(os.sep)[-3:-1]) or not os.path.isfile(real)):
            continue
        try:
            modified = os.path.getmtime(real)
            if since is not None and modified < since - SLACK_S:
                continue
            size = scenario_checks.image_size(real)
            found[real] = (modified, {"file": name, "size": list(size) if size else None,
                                      "bytes": os.path.getsize(real), "where": where})
        except (OSError, ValueError):
            continue
    newest = sorted(found.values(), key=lambda item: -item[0])
    return [entry for _, entry in newest[:limit]]


def _file(entry):
    return "`%s`%s" % (entry["file"], " (%d x %d)" % tuple(entry["size"]) if entry.get("size") else "")


def _job(job):
    """One render job: id, kind, status, output name and, where the add-on marks it, whether the agent was shown it."""
    shown = job.get("shown")
    mark = "" if not isinstance(shown, dict) else (
        ", its picture was shown to the agent" if shown.get("image_at") else
        ", its result was told to the agent but not shown" if shown.get("result_at") else
        ", never shown to the agent")
    return "%s %s %s%s%s" % (job.get("job"), job.get("kind"), job.get("status"),
                             " -> `%s`" % os.path.basename(job["output"]) if job.get("output") else "", mark)


LATE = "looked for afterwards in the trial folder only: this run kept no list"


def text(produced, jobs, late=False):
    """One report cell: the image files written (``None``: not recorded) and the render jobs started.

    ``jobs`` is None for a side that has no render jobs (stock Blender renders inside the call).
    ``late``: the files were looked for after the run, by a report of a run that recorded none.
    """
    files = "not recorded" if produced is None else (", ".join(_file(entry) for entry in produced) or "none")
    files += " (%s)" % LATE if late else ""
    if jobs is None:
        return "files: %s" % files
    return "files: %s; jobs: %s" % (files, ", ".join(_job(job) for job in jobs) or "none")


def missing_still(name, produced, jobs, late=False):
    """Why ``name`` is missing, in one sentence: rendered something else, or rendered nothing."""
    if late:
        return missing_still(name, produced, jobs)[:-1] + " (%s)." % LATE
    others = [entry for entry in produced or () if entry["file"].lower() != str(name).lower()]
    same = [entry for entry in produced or () if entry["file"].lower() == str(name).lower()]
    parts = []
    if same:
        parts.append("a file of that name was written but did not pass (%s)" % ", ".join(_file(e) for e in same))
    if others:
        parts.append("it wrote %s" % ", ".join(_file(entry) for entry in others[:8]))
    if jobs:
        parts.append("it started %d render job(s): %s" % (len(jobs), ", ".join(_job(job) for job in jobs[:8])))
    if parts:
        return "`%s` is missing, but the side did render: %s." % (name, "; ".join(parts))
    if produced is None:
        return ("`%s` is missing; what else the side wrote was not recorded in this run, and it started no render "
                "job." % name)
    return ("`%s` is missing and the side rendered nothing: no image file was written in its folders, at the "
            "scene's render path or at a path it named, and no render job was started." % name)
