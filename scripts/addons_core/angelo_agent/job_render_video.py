# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An animation render's H.264 MP4: format MP4 renders PNG frames, then encodes them once every frame is whole.

Why afterwards, and not a render straight to a movie file: everything a render
job guarantees is per frame. Each frame is named before the job starts
(``job_render_outputs``), checked whole after a failed write, kept after a
Cancel, a quit or a crash, and the missing ones can be rendered later
(``job_recovery``, ``job_resume``). A movie written by the render itself is one
file that is unreadable until its last frame is written, so a stop at frame 900
of 1,000 would keep nothing. Here the frames behave as they always have, and
the MP4 is one more output published beside them.

The encode runs in a child Angelo (``video_encode_child.py``): the app's own
Sequencer and its bundled FFmpeg, so nothing else is installed, and the
person's document is never touched. The child starts once Blender's render job
has ended with every frame whole; ``RenderRunner.poll`` keeps the job running
while it encodes, Cancel and the time budget stop it, and the job's end
publishes the MP4 with the frames only when the child exited cleanly and the
file is a complete MP4 (``mp4_whole``). A render that stopped early, a resumed
render (it holds part of the frames) and the frames a restart recovers are
published as frames only. A build without FFmpeg (the development build) refuses
format MP4 at admission. Reasons are English (the model reads them); fixed ones
are marked with ``n_`` so the job card translates them.
"""

import atexit
import json
import os
import re
import struct
import subprocess
import tempfile
import time
from pathlib import Path, PurePosixPath

from .job_spec import JobRequestError
from .ui_text import n_

CHILD = Path(__file__).with_name("video_encode_child.py")
MARK = "ANGELO-VIDEO "     # The child's last line: the file it wrote, as JSON.
EXTENSION = ".mp4"
CODEC = "H.264"
STOP_SECONDS = 5           # How long a stopped encoder may take to end before it is killed.
REAP_SECONDS = 0.25        # How often a stopped encoder is looked at until it has ended.
QUIT_SECONDS = 1           # The same at quit, for every encoder together.
LOG_TAIL = 65536           # Bytes of the child's output read back for its result line.
MAX_BOXES = 100000         # Top-level MP4 boxes a check walks before calling the file malformed.

NO_FFMPEG = ("This build of Angelo writes no video (it was built without FFmpeg); render PNG frames, or use a "
             "release build")
ONE_VIEW = "An MP4 holds one picture per frame; turn Stereoscopy off, or render PNG frames"
FAILED = n_("Every frame was rendered and published, but Angelo could not make the MP4 from them")
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def available():
    """Whether this build writes video (Blender's FFmpeg support is compiled in)."""
    try:
        import bpy
    except ImportError:
        return False
    return bool(getattr(getattr(bpy.app, "ffmpeg", None), "supported", False))


def video_name(output, taken=()):
    """The MP4's file name inside an animation's output folder: the folder's own name, made file-safe, never one of
    ``taken`` (the File Output nodes' folders), ignoring case."""
    stem = _UNSAFE.sub("_", PurePosixPath(output or "").name).strip("._")[:80] or "video"
    name, number = stem + EXTENSION, 1
    taken = {entry.lower() for entry in taken}
    while name.lower() in taken:
        number += 1
        name = "%s-%d%s" % (stem, number, EXTENSION)
    return name


def admit(plan, output):
    """The MP4's file name for one render plan (raises JobRequestError when the build or the plan can't make one)."""
    if not available():
        raise JobRequestError(NO_FFMPEG, code="UNSUPPORTED")
    if len([entry for entry in plan.outputs if entry["main"]]) != 1:
        raise JobRequestError(ONE_VIEW, code="UNSUPPORTED")
    return video_name(output, {entry["prefix"].split("/", 1)[0] for entry in plan.outputs if not entry["main"]})


def even_size(width, height):
    """H.264 with 4:2:0 colour needs even sides: the frames are scaled to the nearest smaller even size."""
    return max(2, int(width) // 2 * 2), max(2, int(height) // 2 * 2)


def child_request(folder, output, frames, fps, fps_base, size, name, parent=None):
    """What the child encodes: the frame files by their pattern (never a list: a long render would outgrow a command
    line), their rate, the video's size and the MP4's path. ``frames`` is ``(start, end, step)``; a stepped render
    plays at the scene's speed (each frame lasts ``step`` scene frames)."""
    start, end, step = frames
    return {"directory": str(folder), "prefix": output["prefix"], "suffix": output["suffix"],
            "extension": output["extension"], "digits": output.get("digits", 4), "frames": [start, end, max(1, step)],
            "fps": int(fps), "fps_base": float(fps_base) * max(1, step), "size": list(even_size(*size)),
            "dest": str(Path(folder) / name), "parent": os.getpid() if parent is None else parent}


def command(binary, request):
    """The child Angelo's command line: background, factory settings, no auto-run scripts, offline."""
    return [str(binary), "--background", "--factory-startup", "--disable-autoexec", "--offline-mode",
            "--python-exit-code", "1", "--python", str(CHILD), "--", json.dumps(request)]


def mp4_whole(path):
    """Whether ``path`` is a complete MP4: ``ftyp`` first, its top-level boxes filling the file exactly, a ``moov``
    (the index FFmpeg writes last) and an ``mdat``. A file cut short or still being written is not."""
    try:
        size = os.path.getsize(path)
        seen, offset = [], 0
        with open(path, "rb") as handle:
            while offset < size and len(seen) < MAX_BOXES:
                handle.seek(offset)
                head = handle.read(8)
                if len(head) < 8:
                    return False
                length, kind = struct.unpack(">I4s", head)
                if length == 1:
                    wide = handle.read(8)
                    if len(wide) < 8:
                        return False
                    length = struct.unpack(">Q", wide)[0]
                elif length == 0:   # The last box runs to the end of the file.
                    length = size - offset
                if length < 8:
                    return False
                seen.append(kind)
                offset += length
    except OSError:
        return False
    return offset == size and bool(seen) and seen[0] == b"ftyp" and {b"moov", b"mdat"} <= set(seen)


_running = set()   # Encoders whose child may still run: ended when the app quits (``_end_all``).
_ending = set()    # Stopped encoders whose child hasn't ended yet: looked at on a timer (``reap``).


class Encoder:
    """One child Angelo encoding one MP4; its output goes to a temporary file, never a pipe that could fill.

    Nothing here waits for the child: a stop asks it to end and returns, and it is killed ``STOP_SECONDS`` later if
    it has not (``running``, looked at by its job's poll or by ``reap``), so Cancel or a closing document never holds
    the interface. The child also ends itself when this process is gone (a crash: ``video_encode_child`` checks its
    parent at every frame), and every running child is ended when the app quits normally (``atexit``; no ``bpy``)."""

    def __init__(self, arguments, dest, popen=None, owner=None):
        from . import child_process
        self.dest, self.owner = Path(dest), owner   # owner: the job's id (``writing``).
        self.deadline, self.killed, self.on_end = None, False, []
        self.log = tempfile.TemporaryFile()
        try:
            self.process = (popen or child_process.popen)(arguments, stdout=self.log, stderr=subprocess.STDOUT,
                                                          stdin=subprocess.DEVNULL, env=dict(os.environ))
        except Exception:
            self.log.close()
            raise
        _running.add(self)

    def running(self):
        """Whether the child still runs; one stopped longer than ``STOP_SECONDS`` ago is killed (never waited on)."""
        if self.process.poll() is not None:
            return False
        if self.deadline is not None and not self.killed and time.monotonic() >= self.deadline:
            self.killed = True
            try:
                self.process.kill()
            except OSError:
                pass   # It ended meanwhile.
        return True

    def stop(self, on_end=None):
        """Ask the child to end (Cancel, the time budget, a closing document) and return at once; ``on_end`` runs once
        it has ended (at once when it already has). Never raises."""
        if on_end is not None:
            self.on_end.append(on_end)
        self._terminate()
        if self.running():
            _ending.add(self)
            _schedule()
        else:
            self._ended()

    def _terminate(self):
        if self.deadline is None:
            self.deadline = time.monotonic() + STOP_SECONDS
            try:
                self.process.terminate()
            except Exception:   # noqa: BLE001 - already gone; the kill or its parent check ends it otherwise.
                pass

    def _ended(self):
        self.close()
        waiting, self.on_end = self.on_end, []
        for callback in waiting:
            try:
                callback()
            except Exception as error:   # noqa: BLE001 - one failing never strands the rest.
                print("Angelo: after the encoder ended:", type(error).__name__, error)

    def close(self):
        _running.discard(self)
        _ending.discard(self)
        try:
            self.log.close()
        except OSError:
            pass

    def result(self):
        """``(True, "")`` when the child wrote a whole MP4 at ``dest``, else ``(False, why)`` (English)."""
        code = self.process.poll()
        try:
            self.log.seek(0, os.SEEK_END)
            self.log.seek(max(0, self.log.tell() - LOG_TAIL))
            tail = self.log.read().decode("utf-8", "replace")
        except (OSError, ValueError):
            tail = ""
        self.close()
        if code != 0:
            return False, "the encoder ended with exit status %s" % code
        lines = [line for line in tail.splitlines() if line.startswith(MARK)]
        try:
            written = Path(json.loads(lines[-1][len(MARK):])["file"]) if lines else None
        except (ValueError, KeyError, TypeError):
            written = None
        if written is None or written.name != self.dest.name:
            return False, "the encoder did not name the video it wrote"
        if not mp4_whole(self.dest):
            return False, "the encoder's video is incomplete"
        return True, ""


def reap():
    """Timer: look at the stopped children that have not ended (killing one past its time) and run what waited on
    each one that has. Seconds to the next look, or None once none is left."""
    for encoder in list(_ending):
        if not encoder.running():
            encoder._ended()
    return REAP_SECONDS if _ending else None


def _schedule():
    try:
        import bpy
        if not bpy.app.timers.is_registered(reap):
            bpy.app.timers.register(reap, first_interval=REAP_SECONDS, persistent=True)
    except (ImportError, AttributeError, ValueError):
        pass   # No Blender (host tests): its job's poll or the caller looks at it.


def writing(owner):
    """Whether a child of job ``owner`` still runs: it may still write into the job's staging folder, so that folder
    is neither published nor removed yet (``EncodePhase.abandon``, ``job_render_keep``, ``job_ui._keep_pending``)."""
    from . import job_render_child   # Or its render child (job_render_child), which writes the frames there.
    return owner is not None and (any(encoder.owner == owner and encoder.running() for encoder in list(_running))
                                  or job_render_child.writing(owner))


def _end_all():
    """The app quits (no ``bpy`` here): ask every child to end, give them ``QUIT_SECONDS`` together, then kill."""
    encoders = list(_running)
    for encoder in encoders:
        encoder._terminate()
        encoder.deadline = min(encoder.deadline, time.monotonic() + QUIT_SECONDS)
    give_up = time.monotonic() + 2 * QUIT_SECONDS
    while any([encoder.running() for encoder in encoders]) and time.monotonic() < give_up:
        time.sleep(0.05)
    for encoder in encoders:
        encoder.close()


atexit.register(_end_all)


def _ended_by_budget(runner):
    """Whether the render's own deadline stopped it (job_deadline): a stop, never an encode."""
    from . import job_deadline
    from .job_manager import shared
    jobs = shared()
    record = jobs.store.get(runner.identity) if jobs is not None and runner.identity else None
    return record is not None and bool(job_deadline.ended_by_budget(record))


def begin(runner, binary=None, popen=None):
    """Blender's render job has ended: start encoding when every frame is whole, and return the job's state while the
    child runs; None when there is nothing to encode (the job finishes). ``runner`` is a ``RenderRunner``.

    The job's render settings are put back before the child starts (``Runner.restore_now``): the child reads only the
    staged frames, and the person's F12, a viewport render or an agent edit during the encode meet their own scene,
    never the job's engine, size or a path into staging."""
    from . import job_recovery
    if runner.video_result is not None or runner.stopping or runner.moved is not None or runner.staging is None \
            or runner.resume_into is not None or _ended_by_budget(runner):
        return None
    try:
        frames = job_recovery.whole_frames(runner.staging, runner.plan.outputs, runner.start_frame, runner.end_frame,
                                           strict_last=True, last_only=not runner.plan.nodes, step=runner.step)[0]
    except OSError:
        return None   # Unreadable now: the finish keeps the frames for the next start, and no video is made.
    if len(frames) != runner.total:
        return None   # It ended early: its frames are kept, a video of part of them would mislead.
    runner.restore_now()
    if binary is None:
        import bpy
        binary = bpy.app.binary_path
    main = next(entry for entry in runner.plan.outputs if entry["main"])
    request = child_request(runner.staging.path, main, (runner.start_frame, runner.end_frame, runner.step),
                            runner.fps[0], runner.fps[1], runner.facts["resolution"], runner.video)
    try:
        runner.encoder = Encoder(command(binary, request), request["dest"], popen, owner=runner.identity)
    except (OSError, ValueError) as error:
        runner.video_result = (False, "the encoder did not start (%s)" % type(error).__name__)
        return None
    return state(runner)


def state(runner):
    """The job's state while it encodes: every frame done, progress just short of the end."""
    return {"progress": 0.999, "frames_done": runner.total, "frames_total": runner.total, "encoding": True}


def poll(runner):
    """The encoding job's state, or None once the child ended (its result is kept for the finish). A stopped child is
    polled the same way until it has ended, so the job, and its staging folder, outlive it (``halt``)."""
    runner._check_output_path()   # A moved output stops the job (``Runner.stop`` ends the child).
    encoder = runner.encoder
    if encoder is None:
        return None
    if encoder.running():
        return state(runner) if encoder.deadline is None else dict(state(runner), encoding=False)
    result, runner.encoder = encoder.result(), None
    if runner.video_result is None:   # A stopped one already says so.
        runner.video_result = result
    return None


def halt(runner):
    """Stop a running encoder (Cancel, the time budget, a moved output) and return at once: the job's poll keeps
    looking at it until the child has ended. Its result is a failure."""
    if runner.encoder is not None:
        runner.encoder.stop()
        if runner.video_result is None:
            runner.video_result = (False, "the encoding was stopped")


def detach(runner, on_end=None):
    """The job ends now (a discard, a closing document, a finish): stop its encoder, leave the child to ``reap``, and
    run ``on_end`` once it has ended (at once when none runs)."""
    encoder, runner.encoder = runner.encoder, None
    if encoder is None:
        if on_end is not None:
            on_end()
        return
    if runner.video_result is None:
        runner.video_result = (False, "the encoding was stopped")
    encoder.stop(on_end)


STOPPED_EARLY = "the render ended before every frame was whole"


def outcome(runner, complete):
    """``(file name or None, receipt additions)`` for a finished animation: the MP4 is published only when the
    render completed and the encoder wrote a whole file; otherwise the receipt says why there is none."""
    if not runner.video or runner.resume_into is not None:
        return None, {}
    ok, why = runner.video_result or (False, "the encoder did not run" if complete else STOPPED_EARLY)
    if ok and complete:
        return runner.video, {"video": runner.video}
    return None, {"video_error": (why if not ok else "the job was stopped after encoding")[:200]}


class EncodePhase:
    """``RenderRunner``'s part (before ``KeepFrames`` and ``Runner``): a stop, a discard or a closing document ends
    the child first. Format MP4: ``video`` is the video's file name, ``encoder`` the running child, ``video_result``
    its ``(ok, why)``; ``stopping`` once Cancel or the time budget asked the job to stop."""
    video = encoder = video_result = None
    stopping = False

    def restore_now(self):
        """Put the job's settings back before its end: the encode runs after the render (``begin``)."""
        if self.overrides is not None:
            self.restored_early, self.overrides = self.overrides.restore(), None

    def stop(self):
        self.stopping = True   # Never an encode after a stop, even one that raced the render's own end.
        if self.encoder is not None:
            halt(self)
            return
        super().stop()

    def abandon(self):
        detach(self, super().abandon)   # The staging folder goes once its child has ended (it writes the MP4 there).

    def keep_frames(self):
        detach(self)   # The document is closing: its frames are kept (published once the child ended), not the MP4.
        return super().keep_frames()


def frame_rate(scene):
    """``(fps, fps_base)`` of the scene the job renders."""
    render = scene.render
    return int(render.fps), float(render.fps_base)

