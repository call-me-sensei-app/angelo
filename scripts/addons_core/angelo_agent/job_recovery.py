# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An animation render's finished frames, kept when Angelo closed, crashed or could not publish. No Blender dependency.

Blender writes each finished frame into the job's hidden staging folder
(``job_outputs.Staging``). Stock Blender keeps the frames it finished when it
quits or crashes, because it writes into the output folder itself; a job used to
remove its staging folder at the next start, which threw every finished frame
away (long_jobs.rst, "Keeping frames after a crash").

``recover`` runs at startup for every animation render a previous run left with
a staging folder (one that was running, or one whose publication failed): it
reopens the folder through held, no-follow handles (only a folder carrying the
job's marker, reached without a link), keeps every frame whose files are all
present, planned and whole (``job_frame_check``: a frame torn by the crash or by
a full disk is not whole, and the frame being written when the render stopped is
dropped unless its format proves it whole), publishes them as the job's output
under its own name, else the next free one, and never overwrites anything.

It is safe to run again at any point, so a crash or a full disk during recovery
loses nothing: the name a publication goes to is saved on the record before its
first link (``recover_into``), the next attempt links what is still missing into
that folder (a file already there from the earlier attempt is the same file;
anything else there is left as it is), and the staging folder is removed only
after every kept file is published. A publication that fails on a file-system
error (a full disk) keeps the staging folder and the record says so; the next
start tries again.

The record keeps what a later resume needs (``detail["recovery"]``, local only,
never sent to a model): the plan of files, the frame range and which document
and scene rendered it.
"""

import errno
import os
import stat
import sys
from contextlib import ExitStack
from pathlib import Path, PurePosixPath

from . import checkpoint_filesystem as cf
from . import job_frame_check, job_outputs
from . import job_render_outputs as render_outputs
from .job_output_names import candidates
from .job_spec import FORMATS, MAX_FRAMES
from .ui_text import n_

KIND = "RENDER_ANIMATION"
# Receipt notes: English for the model, translated by the chat card where shown.
KEPT = n_("Angelo closed while this render ran. The frames that finished are kept; each is a whole frame.")
KEPT_FILE_CLOSED = n_("The document was closed or reloaded, which stopped this render. The frames that finished are "
                      "kept; each is a whole frame.")
NONE_FINISHED = n_("Angelo closed while this render ran, before any frame finished. Nothing was published.")
NOT_YET = n_("The frames that finished are kept, but they couldn't be published yet (the disk may be full). Angelo "
             "tries again when it next starts.")
RETAINED = n_("The finished frames couldn't be published; they are kept for the next start")
# The note of a render that ended by itself or was stopped, whose kept frames a later start published.
KEPT_LATER = n_("Kept the frames that finished; each is a whole frame.")
UNREACHABLE = n_("The frames that finished are kept in the job's hidden folder, which can't be reached now (a drive "
                 "that is not connected, or a folder that was moved). Angelo tries again when it next starts.")
UNLISTED = n_("Files the render's approval did not list were not published.")
LEFT_HIDDEN = n_("Angelo closed while this render ran. Its files were left as they were in the job's hidden folder "
                 "beside the output, because this version can't tell which of them are whole frames.")


class _Reopened(job_outputs.Staging):
    """A previous run's staging folder, held again from the workspace root without creating anything."""

    unreachable = False   # The folders down to it could not be walked now (a drive away, a link on the way).

    def reopen(self):
        cf.require_anchored_files()
        try:
            folder = self._chain.enter_context(cf._private_directory(self.root))
            for part in self.output.parent.relative_to(self.root).parts:
                folder = self._chain.enter_context(cf._private_child(folder, part))
            self.parent = folder
        except (OSError, ValueError):
            self.unreachable = True   # Never "gone": its frames may be there when the drive or folder is back.
            self.release()
            return None
        try:
            self.folder = self._chain.enter_context(cf._private_child(folder, self.name))
        except FileNotFoundError:
            self.release()   # The folder it was in is there and the staging folder is not: nothing to keep.
            return None
        except (OSError, ValueError):
            self.unreachable = True   # A link or something unreadable at its name: left as it is.
            self.release()
            return None
        if not job_outputs._marked(self.folder, self.identity):
            self.release()   # Not this job's folder: never touched.
            return None
        self._created = True   # Ours (its marker): discard removes it.
        return self

    def release(self):
        release(self)


def release(staging):
    """Let a staging folder's held folders go without removing anything: it is kept for the next attempt."""
    staging.folder = staging.parent = None
    staging._created = False
    staging._made, staging._target, staging.published = [], None, None
    staging._chain.close()


def facts(runner):
    """What recovery and a resume need, saved on the record when an animation render starts (local only)."""
    plan = getattr(runner, "plan", None)
    if runner.request.get("kind") != KIND or plan is None or runner.staging is None:
        return None
    return {"root": str(runner.workspace.root), "output": str(runner.output), "skip": bool(runner.skip_requested),
            "plan": [dict(output) for output in plan.outputs], "frames": [runner.start_frame, runner.end_frame],
            "blend": getattr(runner, "blend_path", ""), "scene": getattr(runner, "scene_name", ""),
            "into": getattr(runner, "resume_into", None)}


def _legacy(record):
    """Recovery facts for a record written before they were saved (1.0.3): the render's own frames only."""
    detail, request = record["detail"], record["request"]
    frames, staging = detail.get("frames"), Path(detail["staging"])
    relative = PurePosixPath(detail.get("writes") or request.get("output") or "")
    if (type(frames) is not list or len(frames) != 2 or not all(type(value) is int for value in frames)
            or request.get("format", "PNG") not in FORMATS or not relative.name):
        return None
    root = staging.parent
    for _part in relative.parent.parts:
        root = root.parent
    return {"root": str(root), "output": str(staging.parent / PurePosixPath(request["output"]).name),
            "skip": bool(detail.get("writes")), "frames": frames, "legacy": True,
            "plan": [{"prefix": render_outputs.MAIN_PREFIX, "suffix": "", "main": True, "labels": {},
                      "extension": FORMATS[request.get("format", "PNG")]}]}


def _usable(saved):
    if type(saved) is not dict or not all(isinstance(saved.get(key), str) for key in ("root", "output")):
        return None
    frames, plan = saved.get("frames"), saved.get("plan")
    if (type(frames) is not list or len(frames) != 2 or not all(type(value) is int for value in frames)
            or frames[1] < frames[0] or type(plan) is not list or not plan):
        return None
    if frames[1] - frames[0] + 1 > MAX_FRAMES or len(plan) > render_outputs.MAX_OUTPUTS:
        return None   # A record on disk is data: never a range or plan larger than a job may have.
    clean = []
    for output in plan:
        if (type(output) is not dict or not all(isinstance(output.get(key), str) for key in
                                                ("prefix", "suffix", "extension"))
                or type(output.get("labels", {})) is not dict):
            return None
        clean.append(dict(output, labels=dict(output.get("labels", {})), main=output.get("main") is True))
    if not any(output["main"] for output in clean):
        return None
    root, output = Path(saved["root"]), Path(saved["output"])
    if not output.is_relative_to(root) or output == root:
        return None
    return dict(saved, plan=clean)


def _regular(folder, name, published=None, relative=""):
    """A regular file with no other name, or whose one other name is the same file in ``published`` (an earlier
    attempt's link of it, at the same relative path)."""
    try:
        info = cf.entry_info(folder, name)
        if not stat.S_ISREG(info.st_mode):
            return False
        if info.st_nlink == 1:
            return True
        if info.st_nlink != 2 or published is None:
            return False
        with ExitStack() as stack:
            target = published
            for part in PurePosixPath(relative).parts:
                target = stack.enter_context(cf._private_child(target, part))
            return job_outputs._entry_identity(target, name) == (info.st_dev, info.st_ino)
    except (OSError, ValueError):
        return False


def present_files(staging, plan, published=None):
    """The staged regular files in the folders the plan writes (relative posix paths); links and files shared with
    anything but ``published`` (the folder an earlier attempt linked them into) are left out. Only the plan's own
    names among them are ever published (``job_render_outputs.sort_files``)."""
    found = set()
    folders = sorted({output["prefix"].rpartition("/")[0] for output in plan})
    for relative in folders:
        try:
            with ExitStack() as stack:
                folder = staging.folder
                for part in PurePosixPath(relative).parts:
                    folder = stack.enter_context(cf._private_child(folder, part))
                for name in cf.list_names(folder):
                    if _regular(folder, name, published, relative):
                        found.add(relative + "/" + name if relative else name)
        except (OSError, ValueError):
            continue   # A link or a missing node folder: nothing in it is published.
    return found


def unlisted(staging, plan, start, end, present):
    """Names in staging that are not a present planned file (a Finder ``.DS_Store``, a link): never published."""
    expected = {render_outputs.file_name(output, frame) for output in plan for frame in range(start, end + 1)}
    folders = sorted({output["prefix"].rpartition("/")[0] for output in plan} - {""})
    found = []
    for relative in [""] + folders:
        try:
            with ExitStack() as stack:
                folder = staging.folder
                for part in PurePosixPath(relative).parts:
                    folder = stack.enter_context(cf._private_child(folder, part))
                for name in cf.list_names(folder):
                    path = relative + "/" + name if relative else name
                    if not relative and (name == job_outputs.MARKER or name in folders):
                        continue
                    if path not in present or path not in expected:   # A link, a shared file or a stray name.
                        found.append(path)
        except (OSError, ValueError):
            found.append(relative)   # A planned folder that is a link: nothing in it is published.
    return found


def current_path(staging):
    """Where the held staging folder is now (it may have moved with a folder above it), or None."""
    folder = staging.folder
    try:
        if isinstance(folder, job_outputs.anchored_windows.WindowsDirectory):
            return Path(folder.final_path())
        if isinstance(folder, int) and sys.platform == "darwin":
            import fcntl
            return Path(os.fsdecode(fcntl.fcntl(folder, fcntl.F_GETPATH, bytes(1024)).split(b"\0", 1)[0]))
        if isinstance(folder, int):
            return Path(os.readlink("/proc/self/fd/%d" % folder))
    except (OSError, ValueError, AttributeError):
        pass
    return None


def discard(staging):
    """Remove a staging folder whose kept files are published, its marker last, so a crash midway leaves a folder
    the next start still knows as this job's."""
    try:
        for name in cf.list_names(staging.folder):
            if name != job_outputs.MARKER:
                job_outputs._remove(staging.folder, name, job_outputs._Budget(job_outputs.REMOVE_ALL))
    except (OSError, ValueError, job_outputs.OutputMoved):
        pass
    staging.discard()


def _valid_into(name, staging):
    """An earlier attempt's folder name beside the output: one plain name, never a staging folder's."""
    return (isinstance(name, str) and name not in ("", ".", "..") and "/" not in name and "\\" not in name
            and not name.startswith(job_outputs.STAGING_PREFIX) and name != staging.name)


def staged_whole(staging, relative):
    """``job_frame_check``'s answer for one staged file (False when it can't be reached without a link)."""
    *folders, name = PurePosixPath(relative).parts
    try:
        with ExitStack() as stack:
            folder = staging.folder
            for part in folders:
                folder = stack.enter_context(cf._private_child(folder, part))
            return job_frame_check.check_entry(folder, name)
    except (OSError, ValueError):
        return False


def whole_frames(staging, plan, start, end, *, strict_last, last_only=False, published=None):
    """``(frames, files, torn, found)``: the finished, whole frames in staging, their files, the frames dropped as
    torn, and ``job_render_outputs.sort_files``' answer."""
    return _whole(staging, plan, start, end, present_files(staging, plan, published), strict_last=strict_last,
                  last_only=last_only)


def _whole(staging, plan, start, end, present, *, strict_last, last_only=False):
    found = render_outputs.sort_files(plan, start, end, present)
    torn = render_outputs.torn_frames(plan, found["frames"], present, lambda path: staged_whole(staging, path),
                                      strict_last=strict_last, last_only=last_only)
    frames = [frame for frame in found["frames"] if frame not in torn]
    files = [path for frame in frames for path in render_outputs.frame_files(plan, frame, present)]
    return frames, files, sorted(torn), found


def publish(staging, files, into=None, announce=None):
    """Link ``files`` (staged, relative posix paths) into a published folder and return ``(path, linked, theirs)``.

    ``into`` is a folder name an earlier attempt reserved beside the output; without it (or when it is a link) a
    new folder under the first free name (``job_output_names``; the requested name skipped when it was taken at
    approval). ``announce(name)`` saves the name on the job's record before the folder is made, so a crash at any
    later point finds it again; a save that fails stops the publication before anything is made (OSError). A name
    that exists already is never replaced: the very staged file (an earlier attempt's link) counts as published,
    anything else stays as it is and is counted in ``theirs``. A file-system error leaves what was linked and
    raises: the next attempt goes on with ``into``.
    """
    names = [into] if _valid_into(into, staging) else []
    plain = candidates(staging.output.name, staging.identity, True, False)
    names += [name for name in (plain[1:] if staging.skip_requested else plain) if name not in names]
    for name in names:
        reserved = name == names[0] and _valid_into(into, staging)
        if not reserved and job_outputs._exists(staging.parent, name):
            continue
        if announce is not None and announce(name) is False:
            raise OSError(errno.EIO, "The job's record could not be saved; nothing was published")
        try:
            cf.make_directory(staging.parent, name)
        except FileExistsError:
            if not reserved:
                continue   # Taken meanwhile: the next name.
        except job_outputs.anchored_windows.UnusableName:
            continue
        except OSError as error:
            if error.errno == errno.ENAMETOOLONG:
                continue
            raise
        try:
            with cf._private_child(staging.parent, name) as target:
                linked, theirs = _link_files(staging, target, files)
        except ValueError:   # A link or junction at the name: never followed, the next name instead.
            continue
        return staging.output.parent / name, linked, theirs
    raise job_outputs.OutputMoved(job_outputs.TAKEN)


def _link_files(staging, target, files):
    linked = theirs = 0
    for relative in files:
        *folders, name = PurePosixPath(relative).parts
        with ExitStack() as stack:
            source, destination = staging.folder, target
            for part in folders:
                source = stack.enter_context(cf._private_child(source, part))
                try:
                    cf.make_directory(destination, part)
                except FileExistsError:
                    pass
                destination = stack.enter_context(cf._private_child(destination, part))
            try:
                cf.link_into(source, name, destination, name)
                linked += 1
            except FileExistsError:
                if job_outputs._entry_identity(destination, name) != job_outputs._entry_identity(source, name):
                    theirs += 1   # Something else has the name: left as it is, never replaced.
                else:
                    linked += 1   # An earlier attempt's link: the very same file.
    return linked, theirs


def kept_receipt(plan, frames, total, files, linked, theirs, torn, *, single, note=KEPT):
    """The receipt of a render whose finished frames were kept (counts for the card, the files for the agent)."""
    receipt = {"kept": "%d/%d frames" % (len(frames), total), "frames_total": total, "frames_kept": len(frames),
               "files": linked, "note": note}
    if not single:
        names = set(files)
        written = [output for output in plan
                   if any(render_outputs.file_name(output, frame) in names for frame in frames)]
        receipt["outputs"] = render_outputs.entries(written)
    if torn:
        receipt["torn"] = len(torn)   # Frames whose files were not whole: rendered again by a resume.
    if theirs:
        receipt["present"] = theirs   # Names someone else's files had: left as they were.
    return receipt


def recover(record, save):
    """Keep what an animation render finished. True when this handled the record (published, kept for a later
    attempt, or nothing to keep); False when it is not a render this can keep: the caller leaves its folder as it is
    (never removes it). ``save()`` writes the store and returns whether it did."""
    detail = record["detail"]
    if record["kind"] != KIND or not isinstance(detail.get("staging"), str):
        return False
    saved = _usable(detail.get("recovery")) or _legacy(record)
    if saved is None:
        return False
    root, output = Path(saved["root"]), Path(saved["output"])
    start, end = saved["frames"]
    plan, total = saved["plan"], end - start + 1
    single = len(plan) == 1
    reopened = _Reopened(root, output, record["id"], saved.get("skip", False))
    staging = reopened.reopen()
    into = next((name for name in (detail.get("recover_into"), saved.get("into"))
                 if _valid_into(name, reopened)), None)
    if staging is None:
        if reopened.unreachable:   # Kept for a later start: a drive away, a moved folder, a link on the way.
            record["receipt"] = dict(record["receipt"], note=UNREACHABLE)
            save()
            return True
        if into is not None and (output.parent / into).is_dir():   # Published before a crash removed staging.
            return _finished_earlier(record, output.parent / into, saved, save)
        detail.pop("staging", None)   # Gone, or a folder that is not this job's (no marker): never touched.
        save()
        return True
    try:
        with ExitStack() as stack:
            published = None
            if into is not None:
                try:
                    published = stack.enter_context(cf._private_child(staging.parent, into))
                except (OSError, ValueError):
                    published = None
            present = present_files(staging, plan, published)
            frames, files, torn, found = _whole(staging, plan, start, end, present, strict_last=True)
            stray = unlisted(staging, plan, start, end, present)
        if saved.get("legacy") and stray:
            staging.release()   # Views or File Output files this version can't name: left exactly as they were.
            detail.pop("staging", None)
            record["receipt"] = {"note": LEFT_HIDDEN}
            save()
            return True
        if not frames and into is None:
            discard(staging)
            detail.pop("staging", None)
            record["receipt"] = {"note": record["receipt"].get("note") if record.get("reason") == "file_closed"
                                 else NONE_FINISHED}
            save()
            return True

        def announce(name):
            detail["recover_into"] = name
            return save()
        try:
            published, linked, theirs = publish(staging, files, into, announce)
        except (OSError, ValueError, job_outputs.OutputMoved) as error:
            staging.release()   # Kept as it is: the next start tries again.
            record["receipt"] = dict(kept_receipt(plan, frames, total, files, 0, 0, torn, single=single, note=NOT_YET),
                                     error=(getattr(error, "strerror", None) or str(error) or type(error).__name__)[:120])
            save()
            return True
        note = (KEPT if record.get("reason") == "app_closed" else KEPT_FILE_CLOSED
                if record.get("reason") == "file_closed" else KEPT_LATER)
        receipt = kept_receipt(plan, frames, total, files, linked, theirs, torn, single=single, note=note)
        if stray:
            receipt["unexpected"] = [path.rsplit("/", 1)[-1][:80] for path in stray[:5]]
        relative = str(PurePosixPath(*published.relative_to(root).parts))
        requested = str(PurePosixPath(*output.relative_to(root).parts))
        if relative != requested:
            receipt["output"] = relative
        discard(staging)   # Only now: every kept file is published.
    except BaseException:
        staging.release()
        raise
    record["receipt"] = receipt
    if record.get("reason") == RETAINED:   # Published now: the card says what was kept, not what failed.
        record["reason"] = ""
    detail.update(output_path=str(published), frames_done=len(frames), frames_total=total,
                  kept_frames=frames_ranges(frames))
    for key in ("staging", "recover_into", "publishing"):
        detail.pop(key, None)
    save()
    return True


def _finished_earlier(record, published, saved, save):
    """An earlier attempt published and removed the staging folder, then closed before saving: say what is there."""
    start, end = saved["frames"]
    plan, detail = saved["plan"], record["detail"]
    from .job_store import output_names
    names = set()
    for folder in sorted({output["prefix"].rpartition("/")[0] for output in plan}):
        names |= {(folder + "/" + name if folder else name) for name in output_names(published / folder)}
    frames = published_frames(names, saved)
    files = [path for frame in frames for path in render_outputs.frame_files(plan, frame, names)]
    record["receipt"] = {"kept": "%d/%d frames" % (len(frames), end - start + 1), "frames_total": end - start + 1,
                         "frames_kept": len(frames), "files": len(files), "note": KEPT}
    detail.update(output_path=str(published), frames_done=len(frames), frames_total=end - start + 1,
                  kept_frames=frames_ranges(frames))
    for key in ("staging", "recover_into", "publishing"):
        detail.pop(key, None)
    save()
    return True


def frames_ranges(frames):
    """``[[first, last], ...]`` of consecutive frames, at most 32 ranges (what a resume renders around)."""
    ranges = []
    for frame in sorted(frames):
        if ranges and frame == ranges[-1][1] + 1:
            ranges[-1][1] = frame
        else:
            ranges.append([frame, frame])
    return ranges[:32]


def pending(records):
    """Animation renders a previous run left with a staging folder after they ended (a publication that failed)."""
    return [record for record in records if record["kind"] == KIND and record["status"] in ("interrupted", "failed",
                                                                                            "cancelled", "done")
            and isinstance(record["detail"].get("staging"), str)]


def published_frames(names, saved):
    """The frames whose files of the render's own image are all in ``names`` (a published folder's top level)."""
    start, end = saved["frames"]
    mains = [output for output in saved["plan"] if output.get("main")]
    names = set(names)
    return [frame for frame in range(start, end + 1)
            if all(render_outputs.file_name(output, frame) in names for output in mains)]
