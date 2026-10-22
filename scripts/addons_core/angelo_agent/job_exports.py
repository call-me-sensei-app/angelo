# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""USD and Alembic exports on Blender's own background export jobs (``job_runners.Runner``'s contract).

A USD export is published with the textures and volumes its exporter writes beside it (a set), only when every
asset reference resolves; an Alembic export is one file. Reasons are English; fixed ones are marked with ``n_`` so
the job card translates them.
"""

from pathlib import Path

import bpy

from . import checkpoint_filesystem as cf
from .job_manager import JobStartError
from .job_runners import CANCELLED_NOTHING, NOTHING_WRITTEN, NOT_PUBLISHED, Runner
from .job_spec import check_frames, check_work
from .ui_text import n_


def unresolved_usd_references(path):
    """The asset references of a published USD that don't resolve to an existing file (empty when all do)."""
    from pxr import UsdUtils
    _layers, assets, unresolved = UsdUtils.ComputeAllDependencies(str(path))
    return sorted({str(item) for item in unresolved} | {str(item) for item in assets if not Path(str(item)).is_file()})


class ExportRunner(Runner):
    """USD or Alembic, written by Blender's own background export job into staging, then published as a set."""
    publishes = "file_set"

    def __init__(self, window, request, workspace):
        super().__init__(window, request, workspace)
        self.job_type = "USD_EXPORT" if request["kind"] == "EXPORT_USD" else "ALEMBIC_EXPORT"
        # The USD exporter writes the materials' textures into ./textures and volumes into ./volumes beside the file
        # (_start; io/usd usd_writer_volume.cc): a set. Alembic writes the file alone.
        usd = request["kind"] == "EXPORT_USD"
        self.beside, self.publishes = (("textures", "volumes"), "file_set") if usd else ((), "file")
        self.animated = "frame_start" in request
        self.admit()

    def admit(self):
        start = self.request.get("frame_start", self.scene.frame_current)
        end = self.request.get("frame_end", start)
        self.frames = (start, end)
        check_work(self.request, 1, 1, 1, check_frames(start, end))

    @property
    def locks_interface(self):
        return True

    def describe(self):
        return {"frames": list(self.frames) if self.animated else None}

    def _start(self):
        path = str(self.staging.path / self.output.name)
        with bpy.context.temp_override(window=self.window):
            if self.job_type == "USD_EXPORT":
                if self.animated:  # USD exports the scene's frame range: set it for the job.
                    self.overrides.apply({"frame_start": self.frames[0], "frame_end": self.frames[1]})
                # Textures go beside the file (./textures), relative, so the published set is self-contained.
                result = bpy.ops.wm.usd_export(filepath=path, check_existing=False, as_background_job=True,
                                               export_animation=self.animated, export_textures_mode='NEW',
                                               relative_paths=True)
            else:
                result = bpy.ops.wm.alembic_export(filepath=path, check_existing=False, as_background_job=True,
                                                   start=self.frames[0], end=self.frames[1])
        if 'FINISHED' not in result:
            raise JobStartError("Blender did not start the export; see the Info log for its reason")

    def _finish(self, stopped):
        if stopped:
            return self._discard("cancelled", "", CANCELLED_NOTHING)
        if self.output.name not in self.staging.files((self.output.suffix.lower(),)):
            return self._discard("failed", n_("Blender ended the export without writing the file; see its Info log"),
                                 NOTHING_WRITTEN)
        size = cf.entry_info(self.staging.folder, self.output.name).st_size
        if self.job_type == "USD_EXPORT":   # Checked on the staged set (same layout), so publishing is the last step.
            missing = unresolved_usd_references(self.staging.path / self.output.name)
            if missing:  # Never report success with a reference that doesn't resolve.
                status, reason, receipt = self._discard("failed", DANGLING, NOT_PUBLISHED)
                receipt["missing"] = [name.rsplit("/", 1)[-1] for name in missing[:5]]
                return status, reason, receipt
        self.staging.publish_file(self.output.name)
        return "done", "", self._published({"bytes": size})


DANGLING = n_("The exported USD references files that don't exist; nothing was published")
