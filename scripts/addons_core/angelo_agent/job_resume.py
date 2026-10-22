# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Rendering the frames a kept render lacks, into its folder, on the person's click. No Blender dependency.

A render that kept only some frames (Angelo closed, the person cancelled, Blender stopped; ``job_recovery``) offers
to render the frames its output folder lacks, into that same folder, by the person's click on its card
(``job_ui.resume``; long_jobs.rst, "Keeping frames after a crash"). Only in the file and scene that rendered it, and
only while the scene would write the same files.
"""

from pathlib import Path, PurePosixPath

from . import job_render_outputs as render_outputs
from .job_recovery import KIND, _usable, published_frames
from .ui_text import n_

RESUMABLE = frozenset({"interrupted", "cancelled", "failed"})
RESUME_UNSAVED = n_("The file this render came from was never saved, so its scene can't be rendered again")
RESUME_OTHER_FILE = n_("Open the file and show the scene this render came from to render the rest")
RESUME_CHANGED = n_("The scene's views or File Output nodes changed since this render, so the rest would not match "
                    "its frames; start a new render")
RESUME_BUSY = n_("The rest of this render is already being rendered")


def resume_facts(record):
    """The recovery facts of an animation render that ended with frames kept, or None."""
    if record.get("kind") != KIND or record.get("status") not in RESUMABLE:
        return None
    if isinstance(record["detail"].get("staging"), str):
        return None   # Its frames still wait to be published at the next start.
    return _usable(record["detail"].get("recovery"))


def missing_frames(names, saved):
    """``(first, last, count)`` of the frames the folder lacks, or None when it has them all."""
    start, end = saved["frames"]
    have = set(published_frames(names, saved))
    missing = [frame for frame in range(start, end + 1) if frame not in have]
    return (missing[0], missing[-1], len(missing)) if missing else None


def resume_refusal(saved, *, blend, scene, signature=None):
    """Why the rest can't be rendered here and now (a message id), or None. ``blend``: the open file's path;
    ``scene``: the scene shown; ``signature``: the plan the scene would render now (``render_outputs.signature``)."""
    if not saved.get("blend"):
        return RESUME_UNSAVED
    if saved["blend"] != blend or saved.get("scene") != scene:
        return RESUME_OTHER_FILE
    if signature is not None and tuple(signature) != render_outputs.signature(saved["plan"]):
        return RESUME_CHANGED
    return None


def resume_request(record, first, last):
    """The job request that renders frames ``first`` to ``last`` of ``record`` into its published folder."""
    request = {key: value for key, value in record["request"].items()
               if key in ("kind", "format", "resolution", "samples", "minutes")}
    request.update(frame_start=first, frame_end=last, queue=True, resume_of=record["id"],
                   output=output_relative(record), sequence=list(record["request"].get("sequence")
                                                                 or _usable(record["detail"]["recovery"])["frames"]))
    return request


def output_relative(record):
    """The published folder of a render, relative to its workspace (posix), from its local facts."""
    saved = _usable(record["detail"].get("recovery"))
    path = record["detail"].get("output_path")
    if saved is None or not isinstance(path, str):
        return None
    try:
        return str(PurePosixPath(*Path(path).relative_to(saved["root"]).parts))
    except ValueError:
        return None
