# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A shot from the active camera, as the files a video model takes: its first and last frame and its camera move.

1.0.8's production loop (release plan, section B): block a shot in 3D, then turn it into video. The 3D scene is the
control signal: the first and last frames feed image-to-video, and the camera's move, rendered as an MP4, is the
motion reference of reference-to-video.

Every part is a real render on Blender's render job through the shared job manager (doc/angelo/long_jobs.rst),
never a viewport drawn for a scene no window shows:

* ``first`` and ``last``: stills of the scene's first and last frame in EEVEE, at the video's aspect and size;
* ``motion``: the frame range in Workbench at a smaller size, as a format MP4 animation (``job_render_video``).

The person's click is the approval, as F12 is (``job_ui.resume`` is the precedent): each part is a queued job with
progress and Cancel on the chat's card, the render settings it changes are put back, and its files are published in
the chat's file workspace under ``shots/<camera>-<time>/``. ``start`` returns a ``Shot``; ``job_ended`` (called by
``job_ui`` for every finished job) fills in its files, and once every part has ended the Shot is handed to
``on_done`` and to every registered receiver (``receivers``: Media Studio's Video tab registers one). The engine is a
host-only request field, never offered to the agent, which renders with ``angelo_job_start``.
"""

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .ui_text import n_

ASPECTS = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1), "4:3": (4, 3), "3:4": (3, 4), "21:9": (21, 9)}
FRAME_ENGINE = "BLENDER_EEVEE"
MOTION_ENGINE = "BLENDER_WORKBENCH"
FRAME_SIDE = 720          # The short side of the first and last frames (720p): what video models take as I2V input.
MOTION_SIDE = 480         # The short side of the move: a motion reference needs the camera's path, not detail.
PARTS = ("first", "last", "motion")
# Each part's render card heading (``job_chat.label_job``): what is happening, in the person's words.
JOB_TITLES = {"first": n_("Capturing the first frame"), "last": n_("Capturing the last frame"),
              "motion": n_("Recording the camera move")}
FRAME_MINUTES, MOTION_MINUTES = 10.0, 30.0
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

receivers = []            # callable(shot): told of every finished shot (Media Studio's Video tab).
_shots = {}               # job id -> Shot, while that part runs.


class ShotError(Exception):
    """A shot that could not be started; ``args[0]`` is an ``n_`` message, ``fields`` fill it."""

    def __init__(self, message, **fields):
        super().__init__(message)
        self.fields = fields


@dataclass
class Shot:
    """One capture: its parts' job IDs, and once each ends, its published file or why it has none."""
    camera: str
    aspect: str
    size: tuple
    folder: str                               # Workspace-relative, as the jobs name it.
    jobs: dict = field(default_factory=dict)  # part -> job ID
    files: dict = field(default_factory=dict)  # part -> absolute path, once published
    errors: dict = field(default_factory=dict)  # part -> reason (English, the job's own)
    on_done: object = None
    broadcast: bool = True                    # Told to every receiver; False for one part a receiver asked for.

    @property
    def finished(self):
        return all(part in self.files or part in self.errors for part in self.jobs)

    def as_dict(self):
        """Plain data for a receiver: the paths are local files, never sent to a model as such."""
        return {"camera": self.camera, "aspect": self.aspect, "size": list(self.size),
                "files": {part: str(path) for part, path in self.files.items()}, "errors": dict(self.errors)}


def frame_size(aspect, short_side):
    """``(width, height)`` of ``aspect`` with ``short_side`` pixels on its shorter side, both even (video codecs need
    even sides)."""
    if aspect not in ASPECTS:
        raise ValueError("aspect must be one of: " + ", ".join(ASPECTS))
    across, down = ASPECTS[aspect]
    short = max(2, int(short_side) // 2 * 2)
    if across >= down:
        return round(short * across / down / 2) * 2, short
    return short, round(short * down / across / 2) * 2


def folder_name(camera_name, now=None):
    """``shots/<camera>-<local time>``: a new folder for every capture, so no part's name is ever taken."""
    stem = _UNSAFE.sub("_", camera_name or "").strip("._")[:48] or "camera"
    return "shots/%s-%s" % (stem, time.strftime("%Y%m%d-%H%M%S", time.localtime(now)))


def requests(frames, folder, *, aspect="16:9", short_side=FRAME_SIDE, parts=PARTS, motion_side=MOTION_SIDE):
    """The render requests of the asked ``parts`` of one shot, ``[(part, request)]``, in ``PARTS`` order.
    ``frames`` is the scene's ``(first, last)`` frame. A one-frame scene has no move, and its last frame is its first
    (rendered once, as ``first``, when both are asked)."""
    first, final = frames
    size, small = frame_size(aspect, short_side), frame_size(aspect, motion_side)
    still = {"kind": "RENDER_STILL", "format": "PNG", "engine": FRAME_ENGINE, "resolution": list(size),
             "minutes": FRAME_MINUTES, "queue": True}
    found = []
    if "first" in parts:
        found.append(("first", dict(still, output=folder + "/first.png", frame_start=first)))
    if "last" in parts and (final != first or "first" not in parts):
        found.append(("last", dict(still, output=folder + "/last.png", frame_start=final)))
    if "motion" in parts and final != first:
        found.append(("motion", {"kind": "RENDER_ANIMATION", "format": "MP4", "engine": MOTION_ENGINE,
                                 "resolution": list(small), "frame_start": first, "frame_end": final,
                                 "output": folder + "/motion", "minutes": MOTION_MINUTES, "queue": True}))
    return found


def start(window, controller, *, aspect="16:9", short_side=FRAME_SIDE, parts=PARTS, on_done=None,
          broadcast=True, submit=None, build=None, now=None):
    """Queue the ``parts`` of the shot from ``window``'s scene camera into ``controller``'s file workspace; returns
    the Shot. A caller that needs one part (the Video tab's first frame, its motion reference) asks for that part.

    Raises ShotError when nothing could start; a part refused after an earlier one started is recorded in
    ``errors`` (the started ones still run). ``submit`` and ``build`` stand in for the job manager in tests."""
    from . import native_programs
    from .ui_text import n_
    scene = window.scene if window is not None else None
    if scene is None or controller is None:
        raise ShotError(n_("Open a chat in this window first"))
    if scene.camera is None:
        raise ShotError(n_("This scene has no active camera; add one or set one as active"))
    staging = getattr(native_programs, "runtime_is_staging", native_programs.has_active_programs)
    if staging():
        raise ShotError(n_("The agent is changing the scene; wait for it to finish"))
    if submit is None or build is None:
        from . import job_runners, job_ui
        build = build or job_runners.build
        submit = submit or (lambda request, runner: job_ui.manager().submit(controller.state["id"], "", request,
                                                                             runner))
    folder = folder_name(scene.camera.name, now)
    shot = Shot(camera=scene.camera.name, aspect=aspect, size=frame_size(aspect, short_side), folder=folder,
                on_done=on_done, broadcast=broadcast)
    from .job_spec import JobRequestError
    from .job_manager import JobStartError
    planned = requests((scene.frame_start, scene.frame_end), folder, aspect=aspect, short_side=short_side,
                       parts=parts)
    if not planned:   # Only the move was asked, and one frame has none.
        raise ShotError(n_("The scene's frame range is one frame, so its camera doesn't move; set a longer range"))
    for part, request in planned:
        try:
            runner = build(window, request, controller.files)
            runner.skip_requested = getattr(runner, "output", None) is not None and os.path.lexists(runner.output)
            record = submit(request, runner)
        except (JobRequestError, JobStartError) as error:
            if not shot.jobs:
                raise ShotError(n_("Angelo couldn't render this shot: {reason}"), reason=str(error)) from None
            shot.errors[part] = str(error)[:300]
            continue
        shot.jobs[part] = record["id"]
        _label(record["id"], part)
        if record["status"] in ("failed", "cancelled", "done"):   # Ended at once (its start failed).
            _settle(shot, part, record)
        else:
            _shots[record["id"]] = (shot, part)
    if shot.finished:
        _deliver(shot)
    return shot


def _label(job_id, part):
    """Its render card says what the part is in the person's words (display only: the job record is unchanged)."""
    try:
        from .job_chat import label_job
    except ImportError:   # Tests without the chat.
        return
    label_job(job_id, JOB_TITLES[part])


def _settle(shot, part, record):
    """One part's job ended: its published file, or the job's reason."""
    from . import job_ui
    path = job_ui.output_path(record) if record["status"] == "done" else None
    receipt = record.get("receipt") or {}
    if part == "motion" and path is not None:   # The MP4 inside the animation's folder (job_render_video).
        video = receipt.get("video")
        path = path / video if video and (path / video).is_file() else None
    elif path is not None and path.is_dir():   # A still of several files (views, File Output nodes): its picture.
        main = [entry.get("file") for entry in receipt.get("outputs") or () if not entry.get("node")]
        path = path / main[0] if main and main[0] and (path / main[0]).is_file() else None
    if path is not None:
        shot.files[part] = Path(path)
    else:
        shot.errors[part] = (receipt.get("video_error") or record.get("reason") or receipt.get("note")
                             or record["status"])[:300]


def job_ended(record):
    """``job_ui`` calls this for every finished job; a shot's part is settled, and a finished shot delivered."""
    entry = _shots.pop(record.get("id"), None)
    if entry is None:
        return
    shot, part = entry
    _settle(shot, part, record)
    if shot.finished:
        _deliver(shot)


def _deliver(shot):
    for receiver in [shot.on_done, *(receivers if shot.broadcast else ())]:
        if receiver is None:
            continue
        try:
            receiver(shot)
        except Exception as error:   # noqa: BLE001 - one receiver's failure never strands the others.
            print("Angelo: a shot receiver failed (%s: %s)" % (type(error).__name__, error))


def pending():
    """The shots still rendering (for a receiver that shows them)."""
    return list({id(shot): shot for shot, _part in _shots.values()}.values())


def clear():
    _shots.clear()
