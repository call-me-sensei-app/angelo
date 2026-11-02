# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The agent's region render over the last render: ``angelo_job_start`` kind RENDER_REGION (doc/angelo/render_region.rst).

Blender's own render job, started as the person's Render Region does (``render.render(region_over_last=True)``): the
region renders into Blender's Render Result and every pass of the last render stays outside it (compositing runs
again on the whole frame). It writes no file, so it
has no output, staging or publication; the agent sees the frame with ``angelo_view`` image RENDER_RESULT. Unlike the
person's route it leaves the scene untouched (the scene keeps no drawn region): the native operator sets the render
region for the render's duration only and restores it however the render ends.

Reasons and notes are English (the model reads them); fixed ones are marked with ``n_`` so the chat card can
translate them where it shows them.
"""

import bpy

from . import render_region
from .job_runners import Runner
from .job_spec import JobRequestError
from .ui_text import n_

NO_RENDER = "There is no render to render a region over; render the full frame first (F12, or RENDER_STILL)"
OTHER_SIZE = "The last render is {last} pixels and the scene now renders {now}; render the full frame first"
DONE = n_("The region was rendered over the last render; every pass is unchanged outside it, and compositing ran "
          "again on the whole frame.")
KEPT = n_("The region did not render; the last render is kept unchanged.")
CANCELLED = n_("Cancelled; the last render is kept unchanged.")


def blender_region(region):
    """angelo_view's region ([left, top, right, bottom] from the top-left) as Blender's render region fractions
    (left, bottom, right, top from the bottom-left)."""
    left, top, right, bottom = region
    return (left, 1.0 - bottom, right, 1.0 - top)


class RegionRenderRunner(Runner):
    job_type = "RENDER"

    def admit(self):
        scene = self.scene
        if scene.camera is None:
            raise JobRequestError("The scene has no active camera to render from", code="VALIDATION")
        last, now = render_region.last_render_size(scene), render_region.frame_size(scene)
        if last is None:
            raise JobRequestError(NO_RENDER, code="VALIDATION")
        if tuple(last) != tuple(now):
            raise JobRequestError(OTHER_SIZE.format(last="%dx%d" % last, now="%dx%d" % now), code="STALE_SETTING")
        if not render_region.large_enough(blender_region(self.request["region"]), *now):
            raise JobRequestError("region covers fewer than %d pixels a side of the %dx%d frame"
                                  % (render_region.MIN_PIXELS, *now), code="VALIDATION")

    def describe(self):
        width, height = render_region.frame_size(self.scene)
        left, top, right, bottom = self.request["region"]
        return {"region_pixels": [round(left * width), round(top * height), round(right * width),
                                  round(bottom * height)], "engine": self.scene.render.engine}

    @property
    def locks_interface(self):
        return bool(self.scene.render.use_lock_interface)

    def _start(self):
        self._invoke(bpy.ops.render.render, region_over_last=True, region=blender_region(self.request["region"]),
                     use_viewport=False)

    def _finish(self, stopped):
        shown = render_region.state(self.scene) if self.scene is not None else "NONE"
        if shown == "APPLIED":
            return "done", "", {"note": DONE}
        if stopped:
            return "cancelled", "", {"note": CANCELLED}
        return "failed", n_("Angelo ended the region render without composing it"), {"note": KEPT}

