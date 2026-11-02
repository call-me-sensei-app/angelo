# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The region render card (``card_host``; doc/angelo/render_region.rst): what a region render did and what is next.

Shown in the render window (the Image Editor showing the Render Result; the largest 3D view when none shows), in its
corner and non-modal, so the render stays in view:

* **applied**: the region was rendered over the last render; Revert, Render again, New region.
* **reverted**: the region shows the last render's pixels again; Show region render, Render again, New region.
* **restored**: the region did not render (cancelled or refused); the last render is kept; Try again.
* **no_render**: there is no render to render over yet; Render the full frame.
"""

import bpy

from . import card_kit as kit, live_windows, render_region
from .ui_text import iface_, language, n_

WIDTH = 400
APPLIED_PHASE, REVERTED_PHASE, RESTORED_PHASE, NO_RENDER_PHASE = "applied", "reverted", "restored", "no_render"
HEADINGS = {
    APPLIED_PHASE: n_("Region rendered over the last render"),
    REVERTED_PHASE: n_("The region shows the last render again"),
    RESTORED_PHASE: n_("The region did not render"),
    NO_RENDER_PHASE: n_("Render the full frame first"),
}
LEADS = {
    APPLIED_PHASE: n_("Every pass outside the region is exactly as it was. Compositing ran again on the whole frame, "
                      "so an effect like Glare can reach past the region."),
    REVERTED_PHASE: n_("Nothing is lost: show the region render again whenever you like."),
    RESTORED_PHASE: n_("The last render is kept exactly as it was."),
    NO_RENDER_PHASE: n_("A region renders over the last render, so Angelo needs a full render to start from."),
}
REVERT, SHOW_REGION = n_("Revert region"), n_("Show region render")
AGAIN, NEW_REGION, TRY_AGAIN = n_("Render again"), n_("New region"), n_("Try again")
RENDER_FULL, CLOSE = n_("Render the full frame"), n_("Close")


def phase_for(state):
    return {"APPLIED": APPLIED_PHASE, "REVERTED": REVERTED_PHASE}.get(state, RESTORED_PHASE)


class RegionRenderCard(kit.BaseCard):
    kind, modal, priority, cancel_action, default_action, outside_dismisses = (
        "render_region", False, 40, "close", None, False)

    def __init__(self, phase):
        self.phase = phase

    def target_area(self, window):
        return next((area for area in live_windows.areas(window) if render_region.shows_render(area)), None)

    def version(self):
        return self.phase, language()

    def layout(self, view, region_w, region_h, scale, ui):
        def plan(size):
            builder = self._build(view, size, region_w, ui)
            margin = 16 * builder.s
            return builder.place(region_w - builder.width - margin, region_h - margin, False, (region_w, region_h))
        return kit.fit(plan, region_w, region_h, scale)

    def _build(self, view, scale, region_w, ui):
        b = kit.Builder(view, scale, min(WIDTH * scale, region_w - 32 * scale), ui, padding=20)
        b.heading(iface_(HEADINGS[self.phase]), 16)
        b.gap(6)
        b.paragraph(iface_(LEADS[self.phase]), 12.5, kit.MUTED)
        b.gap(14)
        b.buttons(self._buttons())
        b.close_button("close")
        return b

    def _buttons(self):
        if self.phase == NO_RENDER_PHASE:
            return [("close", None, iface_(CLOSE), "secondary", True),
                    ("render_full", None, iface_(RENDER_FULL), "primary", True)]
        if self.phase == RESTORED_PHASE:
            again = render_region.stored(bpy.context.scene) is not None
            return [("close", None, iface_(CLOSE), "secondary", True),
                    ("again", None, iface_(TRY_AGAIN), "primary", again)]
        toggle = REVERT if self.phase == APPLIED_PHASE else SHOW_REGION
        return [("revert", None, iface_(toggle), "secondary", True),
                ("again", None, iface_(AGAIN), "secondary", True),
                ("new", None, iface_(NEW_REGION), "primary", True)]

    def act(self, action, value, host):
        if action in {"close", "dismiss"}:
            host.close("done")
            return
        host.close("done")   # Each choice changes the render (or starts one): the card's reading is over.
        bpy.app.timers.register(lambda: _run(action), first_interval=0.0)


def _run(action):
    """A card choice, after the card's own event: from the window the render shows in."""
    context = bpy.context
    window = next((w for w, area in live_windows.window_areas(context.window_manager)
                   if render_region.shows_render(area)), context.window)
    operator = {"revert": bpy.ops.angelo.render_region_revert, "again": bpy.ops.angelo.render_region_again,
                "new": bpy.ops.angelo.render_region, "render_full": bpy.ops.render.render}.get(action)
    if operator is None:
        return None
    try:
        with context.temp_override(window=window):
            operator('INVOKE_DEFAULT')
    except RuntimeError as error:   # Blender's own reason, in the status bar like any refused operator.
        print("Angelo: region render:", " ".join(str(error).split()))
    if action == "revert":   # The card stays useful after a toggle: show it again in its new phase.
        show(phase_for(render_region.state(context.scene)))
    return None


def show(phase):
    from . import card_host
    if card_host.is_active("render_region"):
        card_host.close("done")
    card_host.request(RegionRenderCard(phase))
    card_host.pump(lambda: True)


def register():
    pass


def unregister():
    from . import card_host
    if card_host.is_active("render_region"):
        card_host.close("done")
