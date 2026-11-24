# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Camera Look card (``card_host``): a lens and film look on the scene's camera, in one click.

Contract: doc/angelo/camera_look.rst, "The card". Opened by ``angelo.camera_look`` from the chat bar's + menu and F3
(the operator's EXEC path is the agent's). Drawn with ``card_kit``:

* **compose** (centred, modal): the camera; the look (a preset); **Focus breathing** where the look's lens breathes;
  **Show in the camera view** (the viewport compositor); **Match exposure**: the light is taken as right for the
  camera's f-stop at 1/50 s and ISO 100 when it was turned on, and the camera's shutter and ISO chosen here move the
  film's exposure by the stops between (``look_spec.exposure_stops``); **Take Off** when a look is on. Apply is one
  Undo step.
* **done / failed** (corner): the look and its stages, or the reason.

Everything Blender is imported inside the functions that need it, so the card's state and layout are host-tested.
"""

from . import card_kit as kit
from . import look_spec
from .ui_text import iface_, n_

WIDTH, CORNER_WIDTH = 520, 380
TITLE = n_("Lens and film look")
SUBTITLE = n_("Vignette, glare, grain, bokeh and grade for {camera}, in every render engine and the camera view. "
              "Take Off puts your settings back.")
NO_CAMERA = n_("Add a camera and make it the scene's, then open Camera Look again.")
LOOK = n_("Look", "Angelo")   # As the operator's preset property (look_ui), in the same context.
BREATHING = n_("Focus breathing")
BREATHING_WHY = n_("The view narrows as focus comes close; while it is on, the look sets the focal length")
SHOW = n_("Show in the camera view")
SHOW_WHY = n_("The 3D view's camera view runs the compositor")
MATCH = n_("Match exposure")
MATCH_WHY = n_("Lit for f/{f_stop}, 1/50 s, ISO 100: the shutter and ISO below change the exposure in stops")
SHUTTER, ISO = n_("Shutter"), n_("ISO")
STOPS = n_("{stops} stops")
APPLY, TAKE_OFF, CANCEL, CLOSE = n_("Apply"), n_("Take Off"), n_("Cancel"), n_("Close")
DONE = n_("{look} look on {camera}")
CLEARED = n_("No look on {camera}")
FAILED = n_("The look wasn't applied")
PRESET_LABELS = {"CLEAN": n_("Clean", "Angelo"), "VINTAGE": n_("Vintage", "Angelo"),   # As look_ui.PRESET_ITEMS.
                 "ANAMORPHIC": n_("Anamorphic", "Angelo"), "DOCUMENTARY": n_("Documentary", "Angelo"),
                 "WARM_PORTRAIT": n_("Warm Portrait", "Angelo"), "BLEACH_BYPASS": n_("Bleach Bypass", "Angelo"),
                 "NOIR": n_("Noir", "Angelo")}
# The stages in the context "Angelo" too: Blender's own words for some name other things in some languages.
STAGE_LABELS = {"lens": n_("Lens", "Angelo"), "vignette": n_("Vignette", "Angelo"), "glare": n_("Glare", "Angelo"),
                "halation": n_("Halation", "Angelo"), "film": n_("Film", "Angelo"), "grain": n_("Grain", "Angelo")}
SHUTTERS = (25, 50, 100, 250, 500)   # 1/N second.
ISOS = (100, 200, 400, 800, 1600, 3200)
REFERENCE_SHUTTER, REFERENCE_ISO = 50, 100
WARN, ERROR = "warn", "error"   # i18n: notice kinds (ui_kit_status), never shown


class LookCard(kit.BaseCard):
    kind, priority, cancel_action, default_action, outside_dismisses = "camera_look", 50, "close", None, False

    def __init__(self, camera, f_stop=5.6, applied=None):
        self.phase, self.error = "compose", ""
        self.camera, self.f_stop, self.applied = camera, f_stop, applied   # applied: the scene's look, or None.
        self.preset = applied if applied in look_spec.PRESETS and applied != "CLEAN" else "VINTAGE"
        self.preset_open = False
        self.breathing, self.show = False, True
        self.match, self.reference_f_stop = False, f_stop
        self.shutter, self.iso = REFERENCE_SHUTTER, REFERENCE_ISO
        self.receipt, self._version = None, 0

    @property
    def modal(self):
        return self.phase == "compose"

    def version(self):
        return (self._version, self.phase, self.preset, self.preset_open, self.breathing, self.show, self.match,
                self.shutter, self.iso)

    def breathes(self):
        return bool(look_spec.PRESETS[self.preset].get("camera", {}).get("breathing"))

    def exposure(self):
        """The ``exposure`` argument the card's choices make, or None."""
        if not self.match:
            return None
        return {"reference": {"f_stop": self.reference_f_stop, "shutter": 1.0 / REFERENCE_SHUTTER,
                              "iso": REFERENCE_ISO},
                "setting": {"shutter": 1.0 / self.shutter, "iso": self.iso}}

    def stops(self):
        exposure = self.exposure()
        return look_spec.exposure_stops(exposure, self.f_stop) if exposure else 0.0

    # -- layout --------------------------------------------------------------------------------------------
    def layout(self, view, region_w, region_h, scale, ui):
        from .update_card import corner_plan
        if self.phase == "compose":
            def plan(size):
                b = self._compose(view, size, region_w, ui)
                top = min(region_h - 16 * b.s, (region_h + b.height()) / 2)
                return b.place((region_w - b.width) / 2, top, True, (region_w, region_h))
            return kit.fit(plan, region_w, region_h, scale)
        return kit.fit(lambda size: corner_plan(self._result(view, size, region_w, ui), region_w, region_h),
                       region_w, region_h, scale)

    def _heading(self, b, icon, title, subtitle):
        top = b.y
        b.icon(icon, b.pad, b.y, 40)
        text_x = b.pad + 54 * b.s
        text_w = b.width - text_x - b.pad - 24 * b.s
        b.paragraph(title, 17, kit.TEXT, x=text_x, width=text_w, leading=1.3)
        b.paragraph(subtitle, 13, kit.MUTED, x=text_x, width=text_w, leading=1.45)
        b.y = max(b.y, top + 40 * b.s) + 14 * b.s

    def _compose(self, view, scale, region_w, ui):
        b = kit.Builder(view, scale, min(WIDTH * scale, region_w - 40 * scale), ui, padding=26)
        self._heading(b, "look", iface_(TITLE), iface_(SUBTITLE, camera=self.camera or "—"))
        if not self.camera:
            b.notice(WARN, iface_(NO_CAMERA))
            b.gap(18)
            b.buttons([("close", None, iface_(CLOSE), "primary", True)])
            b.close_button("close")
            return b
        b.select("preset_open", None, [(name, iface_(PRESET_LABELS[name])) for name in look_spec.PRESETS
                                       if name != "CLEAN"], self.preset, open=self.preset_open, label=iface_(LOOK),
                 option_action="preset")
        b.gap(12)
        b.toggle("breathing", None, iface_(BREATHING), self.breathing and self.breathes(),
                 caption=iface_(BREATHING_WHY), enabled=self.breathes())
        b.gap(6)
        b.toggle("show", None, iface_(SHOW), self.show, caption=iface_(SHOW_WHY))
        b.gap(6)
        b.toggle("match", None, iface_(MATCH), self.match,
                 caption=iface_(MATCH_WHY, f_stop="%g" % round(self.reference_f_stop, 1)))
        if self.match:
            b.gap(8)
            b.paragraph(iface_(SHUTTER), 12.5, x=b.pad + 2 * b.s)
            b.gap(4)
            b.segmented("shutter", [(str(n), "1/%d" % n) for n in SHUTTERS], str(self.shutter))
            b.gap(8)
            b.paragraph(iface_(ISO), 12.5, x=b.pad + 2 * b.s)
            b.gap(4)
            b.segmented("iso", [(str(n), str(n)) for n in ISOS], str(self.iso))
            b.gap(6)
            b.paragraph(iface_(STOPS, stops="%+.1f" % self.stops()), 12, kit.MUTED)
        if self.error:
            b.gap(10)
            b.notice(ERROR, self.error)
        b.gap(18)
        left = [("take_off", None, iface_(TAKE_OFF), "ghost", True)] if self.applied else ()
        b.buttons([("close", None, iface_(CANCEL), "secondary", True), ("apply", None, iface_(APPLY), "primary", True)],
                  left=left)
        b.close_button("close")
        return b

    def _result(self, view, scale, region_w, ui):
        b = kit.Builder(view, scale, min(CORNER_WIDTH * scale, region_w - 40 * scale), ui, padding=22)
        receipt = self.receipt or {}
        if self.phase == "done" and receipt.get("group"):
            stages = ", ".join(iface_(STAGE_LABELS[name]) for name in receipt.get("stages", ()))
            self._heading(b, "check", iface_(DONE, look=iface_(PRESET_LABELS[receipt["preset"]]),
                                             camera=receipt.get("camera", "")), stages)
        elif self.phase == "done":
            self._heading(b, "check", iface_(CLEARED, camera=receipt.get("camera", "")), "")
        else:
            self._heading(b, "alert", iface_(FAILED), self.error)
        b.buttons([("close", None, iface_(CLOSE), "primary", True)])
        b.close_button("close")
        return b

    # -- input ---------------------------------------------------------------------------------------------
    def key_event(self, event, host):
        if self.preset_open and event.type == 'ESC':
            self.preset_open = False
            return True
        return False

    def act(self, action, value, host):
        if action == "preset_open":
            self.preset_open = not self.preset_open
        elif action == "preset":
            self.preset, self.preset_open = value, False
        elif action == "breathing":
            self.breathing = not self.breathing
        elif action == "show":
            self.show = not self.show
        elif action == "match":
            self.match = not self.match
        elif action == "shutter":
            self.shutter = int(value)
        elif action == "iso":
            self.iso = int(value)
        elif action == "apply":
            self._apply(host, self.preset)
        elif action == "take_off":
            self._apply(host, "CLEAN")
        elif action in {"close", "dismiss"}:
            host.close("closed")
            return
        self._version += 1
        host.redraw()

    def request(self, preset=None):
        """``look_system.apply``'s keyword arguments for the card's choices (``preset`` CLEAN: take the look off)."""
        preset = preset or self.preset
        if preset == "CLEAN":
            return {"preset": "CLEAN"}
        amount = look_spec.PRESETS[preset].get("camera", {}).get("breathing", 0.0) if self.breathing else 0.0
        return {"preset": preset, "breathing": amount, "show_in_viewport": self.show, "exposure": self.exposure()}

    def _apply(self, host, preset):
        import bpy
        from . import look_system
        camera = bpy.data.objects.get(self.camera or "")
        window = host.window()
        try:
            if camera is None or camera.type != 'CAMERA':
                raise look_system.LookError(iface_(NO_CAMERA), code="NOT_FOUND")
            scene = window.scene if window is not None else bpy.context.scene
            screens = [window.screen] if window is not None and self.show else ()
            self.receipt = look_system.apply(scene, camera, screens=screens, **self.request(preset))
            bpy.ops.ed.undo_push(message="Camera Look")  # i18n: undo history entry, named in English
            self.phase, self.error = "done", ""
        except look_spec.LookError as error:
            self.error = str(error)   # The look's own refusal (English, as the agent reads it), with the fix.
