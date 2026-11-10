# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The render job of one RENDERED look (``view_render``): Blender's F12 render of the scene camera to a private PNG.

``LookRunner`` is the job ``view_render.begin`` submits to the shared ``JobManager``. It admits the scene (no add-on
or script Python without the person's render approval: ``render_extensions.refuse_look``), sets the look's settings
on the scene for the job only (``view_render_restore.LookSettings``) in its own Render Result slot, stops the render
once it is past its budget with samples to show, and puts the person's settings and slot back however it ends.
Model-facing text is English and never translated.
"""

import time
from pathlib import Path

import bpy

from . import job_deadline, job_render_samples, render_extensions, view_scene_check
from .job_manager import JobStartError
from .job_runners import SAMPLES as SAMPLE_PATHS, RenderRunner, native
from .job_settings import DisplayNone
from .view_render_restore import LookSettings, ResultSlot, picture_values, written_keys

NO_IMAGE = "Blender ended the render without writing an image; see its Info log"
# Settings a look writes besides its render settings (ResultSlot, job_settings.DisplayNone): message-bus keys.
OTHER_WRITES = frozenset({("RenderSlots", "active_index"), ("PreferencesView", "render_display_type"),
                          ("Preferences", "is_dirty")})


class LookRunner(RenderRunner):
    """One still of the scene camera on Blender's render job, written to a private PNG."""

    def __init__(self, window, request, path, limit, sampling, mode="ASK"):
        # ``limit``: seconds after which a first sample ends the look; ``sampling``: seconds it samples at least.
        self.path, self.limit, self.sampling = Path(path), limit, sampling
        self.progress, self.began, self.first_sample, self.seconds = 0.0, None, None, 0.0
        self.unrestored, self.slot, self.over, self.exact_colour = [], None, False, True
        self.mode, self.check, self.scene_check, self.handlers_ran = mode, None, None, []  # Approval mode; check.
        self.unknown = False  # Whether this build could not list message-bus subscribers.
        super().__init__(window, request, None)  # Admission: camera, stereoscopy, File Output nodes, bounds.

    def admit(self):
        super().admit()
        # Add-on or script Python runs only after a render approval; the Auto modes let depsgraph handlers and
        # message-bus subscribers to the look's own settings run.
        written = written_keys(self.scene, self._values()) | OTHER_WRITES
        self.extensions = render_extensions.refuse_look(self.scene, self.mode, written)

    def _values(self):
        """The job's settings, in the order they are written (the image format last, its type first)."""
        scene, render, request = self.scene, self.scene.render, self.request
        frame = request.get("frame_size") or request["resolution"]
        values = {"render.resolution_x": frame[0], "render.resolution_y": frame[1],
                  "render.resolution_percentage": 100, "render.filepath": str(self.path),
                  "render.use_file_extension": False}
        if "border" in request:  # A region: Blender renders that part of the frame only and crops to it.
            values.update({"render.use_border": True, "render.use_crop_to_border": True})
            values.update(zip(("render.border_min_x", "render.border_min_y", "render.border_max_x",
                               "render.border_max_y"), request["border"]))
        values.update({"render." + name: False for name in ("use_render_cache", "use_auto_generate_texture_cache")
                       if hasattr(render, name)})
        if "samples" in request:  # With every rendered layer's count: held for the render (job_render_samples).
            values[SAMPLE_PATHS[render.engine]] = request["samples"]
            values.update(job_render_samples.settings(self.window, request["samples"], look=True))
        picture, self.exact_colour = picture_values(scene)
        values.update(picture)
        return values

    def start(self):
        native()
        if self.window.scene != self.scene:
            raise JobStartError("The look's scene is no longer shown in its window")
        self.admit()
        self.handlers_ran = self.extensions.view_python  # Run by the writes below (Auto modes only).
        self.unknown = self.extensions.subscribers is None
        self.check = view_scene_check.SceneCheck(self.window, deep=bool(self.handlers_ran))
        self.overrides, self.slot = LookSettings(self.scene, self.identity), ResultSlot()
        try:
            self.overrides.apply(self._values())
            self.slot.enter()
            with DisplayNone():
                self._invoke(bpy.ops.render.render, write_still=True, use_viewport=False)
        except Exception:
            self._restore()
            raise
        self.began = time.monotonic()

    def _restore(self):
        try:
            if self.overrides is not None:
                self.unrestored = self.overrides.restore() or self.unrestored
        finally:   # The person's render slot comes back whatever the settings did (break-it SW-M-1).
            if self.slot is not None:
                self.slot.leave()

    def poll(self):
        progress = native().progress(self.scene, self.job_type)
        if progress is None:
            return None
        elapsed = time.monotonic() - self.began
        if progress > 0 and self.first_sample is None:
            self.first_sample = elapsed
        self.progress = max(self.progress, min(1.0, progress))
        if self.progress > 0 and not self.over and elapsed > max(self.limit, self.first_sample + self.sampling):
            self.over = True  # Past its budget with something to show: stop here (finish reports a partial look).
            self.stop()
        return {"progress": min(self.progress, 0.999)}

    def written(self):
        try:
            with self.path.open("rb") as handle:
                return handle.read(8) == b"\x89PNG\r\n\x1a\n"
        except OSError:
            return False

    def finish(self, stopped):
        """Restore the person's settings and slot, then say whether an image exists: ``(status, reason, receipt)``."""
        own = list(self.overrides.originals) if self.overrides is not None else []
        self._restore()
        if self.check is not None:  # Did anything else change the document meanwhile?
            self.scene_check = self.check.finish(own)
        self.seconds = time.monotonic() - self.began if self.began is not None else 0.0
        job_deadline.catch_up(self)   # Samples its render reported after the last poll.
        # A stopped render still writes the samples that finished; one stopped before its first has none.
        image = self.written() and (not (stopped or self.over) or self.progress > 0)
        checked = {"scene_check": self.scene_check} if self.scene_check is not None else {}
        if stopped:
            return "cancelled", "", dict(checked, image=image)  # The manager's reason stands.
        if self.over:
            return "cancelled", "time_budget", dict(checked, image=image)
        return ("done", "", dict(checked, image=True)) if image else ("failed", NO_IMAGE, dict(checked, image=False))

    def abandon(self):
        self._restore()
