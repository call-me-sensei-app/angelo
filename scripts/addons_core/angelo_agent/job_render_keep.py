# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How an animation render ends without ever removing a finished frame (``RenderRunner``'s part; job_runners).

A file load keeps the staging folder (``keep_frames``); the end publishes only the plan's whole frames through
``job_recovery`` (a stray file is left out and named, no walk budget), into a folder whose name is saved on the
job's record before it is made; any failure (a file-system error, every name taken, a finish that raises, a folder
on the way moved) keeps the staging folder for the next start (``_retain``), following a folder that moved inside
the workspace (long_jobs.rst, "Keeping frames after a crash"). Reasons and notes are English; fixed ones are marked
with ``n_`` so the job card translates them.
"""

from pathlib import PurePosixPath

from . import job_outputs, job_recovery
from . import job_render_outputs as render_outputs
from .ui_text import n_

TORN = n_("Blender stopped while writing a frame (the disk may be full); that frame was not kept")


class KeepFrames:
    """Mixed into ``RenderRunner`` before ``Runner``: its runner fields (plan, staging, frames, request, output)."""

    def recovery_facts(self):
        """What a restart needs to keep this render's finished frames, and a resume to render the rest."""
        return job_recovery.facts(self)

    def keep_frames(self):
        """The document is closing (Blender stops the render): keep the staging folder instead of removing it, so
        its finished frames are published once the next document is open (``job_ui``). True when kept."""
        if self.request["kind"] != "RENDER_ANIMATION" or self.staging is None or self.staging.folder is None:
            return False
        self.retained = str(job_recovery.current_path(self.staging) or self.staging.path)
        job_recovery.release(self.staging)
        return True

    def _finish_animation(self, stopped):
        """Publish an animation's whole finished frames, whatever else happened; never remove one on a failure.

        Only planned files are published (``job_recovery``: a stray file such as Finder's ``.DS_Store`` is not, and
        no longer fails the render), without a walk budget, into a folder whose name is saved on the record before
        it is made. A file-system error, a folder moved or every name taken keeps the staging folder for the next
        start (``retained``): the frames are published then (job_recovery.recover).
        """
        plan, staging = self.plan.outputs, self.staging
        present = job_recovery.present_files(staging, plan)
        finished = render_outputs.sort_files(plan, self.start_frame, self.end_frame, present)["frames"]
        early = not stopped and len(finished) < self.total
        # A plain render that ran here can be torn only at its last frame (a failed write stops the render).
        frames, files, torn, found = job_recovery._whole(staging, plan, self.start_frame, self.end_frame, present,
                                                         strict_last=early, last_only=not self.plan.nodes)
        stray = job_recovery.unlisted(staging, plan, self.start_frame, self.end_frame, present)
        complete = len(frames) == self.total and not stopped
        status = "done" if complete else "cancelled" if stopped else "failed"
        reason = "" if complete or stopped else (TORN if torn else n_("Blender ended the render early; see its Info log"))
        if not frames:
            job_recovery.discard(staging)
            return status, reason, {"note": n_("No frame finished; nothing was written.")}
        receipt = {} if self.plan.single else self._receipt(found, None)
        if not complete:
            # "kept" is the model's; the card counts with the numbers ("frames_total", in the language shown).
            receipt.update(kept="%d/%d frames" % (len(frames), self.total), frames_total=self.total,
                           frames_kept=len(frames), note=n_("Kept the frames that finished; each is a whole frame."))
        if torn:
            receipt["torn"] = len(torn)
        if stray:   # Never published, named for the agent.
            receipt["unexpected"] = [path.rsplit("/", 1)[-1][:80] for path in stray[:5]]
            receipt.setdefault("note", job_recovery.UNLISTED)
        try:
            path, linked, theirs = job_recovery.publish(staging, files, self.resume_into, self._announce_into)
        except (OSError, ValueError, job_outputs.OutputMoved) as error:   # A full disk, every name taken ...
            kept = job_recovery.kept_receipt(plan, frames, self.total, files, 0, 0, sorted(torn),
                                             single=self.plan.single, note=job_recovery.NOT_YET)
            kept["error"] = (getattr(error, "strerror", None) or str(error) or type(error).__name__)[:120]
            return self._retain(kept)
        receipt.update(files=linked)
        if theirs:
            receipt["present"] = theirs
        if path != self.output:   # The name (or the earlier render's folder) was taken: never overwritten.
            receipt["output"] = str(PurePosixPath(*path.relative_to(self.workspace.root).parts))
        self.published = path
        sequence = self.request.get("sequence")
        if self.resume_into is not None and type(sequence) is list and len(sequence) == 2:
            from .job_store import output_names
            have = len(job_recovery.published_frames(output_names(path), {"frames": sequence, "plan": plan}))
            receipt["sequence"] = "%d/%d frames" % (have, sequence[1] - sequence[0] + 1)
        job_recovery.discard(staging)   # Only now: every kept file is published.
        return status, reason, receipt

    def _announce_into(self, name):
        """Save the folder a publication goes to on the job's record before it is made (False: not saved)."""
        from .job_manager import shared
        jobs = shared()
        record = jobs.store.get(self.identity) if jobs is not None and self.identity else None
        if record is None:
            return True   # No record to keep it on (a stand-in manager): nothing a later start could read.
        record["detail"]["recover_into"] = name
        return jobs.store.save() is not False

    def _retain(self, receipt):
        """Keep the staging folder with its frames for the next start, wherever it is now."""
        path = job_recovery.current_path(self.staging)
        if path is not None and path != self.staging.path:   # It moved with a folder above it.
            self.recovery_update = {"output": str(path.parent / self.output.name)}
        self.retained = str(path or self.staging.path)
        job_recovery.release(self.staging)
        from . import job_ui
        job_ui.keep_soon()
        return "failed", job_recovery.RETAINED, receipt

    def _moved(self, reason):
        if self.request["kind"] != "RENDER_ANIMATION" or self.staging is None or self.staging.folder is None:
            return super()._moved(reason)
        return self._retain({"note": job_recovery.NOT_YET, "error": reason[:200]})
