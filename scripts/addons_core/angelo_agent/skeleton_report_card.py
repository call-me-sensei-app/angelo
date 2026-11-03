# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The person's engine-skeleton check: the "Check a rig for a game engine" card (``card_host``).

A corner card, non-modal: the person keeps editing while it shows the same report the agent's
``angelo_skeleton_check`` returns (``skeleton_check_host.report``), translated. The engine (Godot, Unity, Unreal
Engine) and the file format switch on the card, and Check Again runs it after a change. Problems come first, each a
notice with what was found and the fix (what the check looked for under Details); notes fold under one disclosure;
passed checks are chips. Taller than the 3D view, the findings scroll between the switches and the buttons at full
size. Copy Report puts the report on the clipboard as shown. Nothing in the scene changes.

Opened by ``angelo.skeleton_check`` (``skeleton_check_ui``) from the chat bar's + menu (Inspect) and F3.
doc/angelo/engine_skeleton.rst.
``layout`` reads only the report it was given, so it is host-tested with a fake view.
"""

from . import card_kit as kit
from .skeleton_checks import FAIL, INFO, PASS, WARN, render
from .skeleton_profiles import FORMATS, PROFILES, TARGETS
from .skeleton_menu import GLYPH, KIND, LAST_TARGET
from .ui_text import iface_, n_

WIDTH = 460
TITLE = n_("Rig check for {engine}")   # {engine}: Godot, Unity or Unreal Engine, never translated
SUBTITLE = n_("{armature}, as the engine's {format} import reads it. Nothing in the scene changes.")
VERDICT = {PASS: ("done", n_("Ready")), INFO: ("done", n_("Ready")), WARN: ("needs_you", n_("Works, with warnings")),
           FAIL: ("failed", n_("Not ready"))}
CHECKS = {"bone_match": n_("Bone names and hierarchy"), "auto_retarget": n_("Unreal auto-retarget"),
          "single_root": n_("Single root bone"), "root_motion": n_("Root bone for root motion"),
          "deform_only": n_("Deforming bones only"), "leaf_bones": n_("Leaf bones"), "rest_pose": n_("Rest pose"),
          "facing": n_("Facing"), "bone_axes": n_("Bone axes"), "transform": n_("Armature transform"),
          "constraints": n_("Live constraints"), "bendy_bones": n_("B-Bones"),
          "influences": n_("Weights per vertex")}
NOTICE_KIND = {FAIL: "error", WARN: "warn", INFO: "info"}   # i18n: notice kinds (ui_kit_status), never shown
LOOKED_FOR = n_("Looked for: {text}")
NOTES = n_("Notes ({count})")
MORE = n_("More problems ({count})")
FIRST_PROBLEMS = 2   # problems shown before More problems, so the first look stays short
PASSED = n_("Passed")
COPY, COPIED, AGAIN = n_("Copy Report"), n_("Copied"), n_("Check Again")
FORMAT_LABELS = {"GLB": n_("glTF (.glb)"), "FBX": "FBX"}
GONE = n_("The armature {armature} is gone")
FAILED = n_("The rig check couldn't run: {reason}")


def _sentence(text):
    """A finding's text as a line on the card: the agent's lower-case fragment with a capital first letter (scripts
    without case are unchanged)."""
    return text[:1].upper() + text[1:]


class SkeletonReportCard(kit.BaseCard):
    kind, priority, modal, cancel_action, outside_dismisses = KIND, 40, False, "close", False

    def __init__(self, armature_name, scene_ref, target, fmt="AUTO", runner=None):
        """``scene_ref()``: the scene, or None once freed (``live_target``); ``runner(scene, name, target, fmt)``:
        the report (default ``skeleton_check_host.report_for``), so tests give their own."""
        self.armature_name, self.scene_ref = armature_name, scene_ref
        self.target, self.fmt = target, fmt
        self.runner = runner
        self.report, self.error = None, None
        self.expanded, self.notes_open, self.more_open, self.copied = set(), False, False, False
        self._version = 0
        self.run()

    def run(self):
        from . import skeleton_check_host
        runner = self.runner or skeleton_check_host.report_for
        scene = self.scene_ref()
        try:
            if scene is None:
                raise LookupError(iface_(GONE, armature=self.armature_name))
            self.report, self.error = runner(scene, self.armature_name, self.target, self.fmt), None
            self.fmt = self.report["format"]
        except Exception as error:   # Shown on the card; the person can fix the scene and check again.
            self.report, self.error = None, str(error)[:300]
        self._version += 1

    def version(self):
        return (self._version, tuple(sorted(self.expanded)), self.notes_open, self.more_open, self.copied)

    def layout(self, view, region_w, region_h, scale, ui):
        from .update_card import corner_plan

        def plan(size):   # Taller than the 3D view: the findings scroll, at full size (corner_plan's margins).
            builder = self._build(view, size, region_w, ui)
            builder.limit(region_h - 30 * size)
            return corner_plan(builder, region_w, region_h)
        return kit.fit(plan, region_w, region_h, scale)

    def _build(self, view, scale, region_w, ui):
        b = kit.Builder(view, scale, min(WIDTH * scale, region_w - 40 * scale), ui, padding=22)
        engine = PROFILES[self.target].display
        top = b.y
        b.icon(GLYPH, b.pad, b.y, 40)
        text_x = b.pad + 54 * scale
        text_w = b.width - text_x - b.pad - 24 * scale
        b.paragraph(iface_(TITLE, engine=engine), 17, kit.TEXT, x=text_x, width=text_w, leading=1.3)
        fmt_label = iface_(FORMAT_LABELS.get(self.fmt, FORMAT_LABELS["GLB"]))
        b.paragraph(iface_(SUBTITLE, armature=self.armature_name, format=fmt_label), 12.5, kit.MUTED, x=text_x,
                    width=text_w, leading=1.45)
        b.y = max(b.y, top + 40 * scale) + 10 * scale
        b.segmented("target", [(t, PROFILES[t].display) for t in TARGETS], self.target, width=b.inner)
        b.gap(8)
        b.segmented("format", [(f, iface_(FORMAT_LABELS[f])) for f in FORMATS], self.fmt, width=b.inner)
        b.gap(14)
        b.scroll_begin("findings")
        if self.report is None:
            b.notice("error", iface_(FAILED, reason=self.error or ""))   # i18n: notice kind (ui_kit_status), never shown
        else:
            self._findings(b)
        b.scroll_end()
        b.gap(14)
        b.buttons([("copy", None, iface_(COPIED if self.copied else COPY), "secondary", self.report is not None),
                   ("again", None, iface_(AGAIN), "primary", True)])
        b.close_button("close")
        return b

    def _findings(self, b):
        report = self.report
        kind, label = VERDICT[report["verdict"]]
        b.chip(kind, iface_(label))
        b.gap(12)
        problems = sorted((f for f in report["findings"] if f["status"] in (FAIL, WARN)),
                          key=lambda f: f["status"] != FAIL)   # failures first, each group in check order
        notes = [f for f in report["findings"] if f["status"] == INFO]
        shown = problems if self.more_open else problems[:FIRST_PROBLEMS]
        for found in shown + (notes if self.notes_open else []):
            text = render(found, iface_)
            text.update(found=_sentence(text["found"]), fix=_sentence(text["fix"]))   # "Looked for: ..." stays
            body = text["found"] + ("\n" + text["fix"] if text["fix"] else "")
            b.notice(NOTICE_KIND[found["status"]], iface_(CHECKS[found["check"]]), body,
                     details=iface_(LOOKED_FOR, text=text["looked_for"]), details_action="details",
                     details_value=found["check"], expanded=found["check"] in self.expanded)
            b.gap(8)
        if len(problems) > FIRST_PROBLEMS:
            b.disclosure("more", iface_(MORE, count=len(problems) - FIRST_PROBLEMS), self.more_open)
            b.gap(8)
        if notes:
            b.disclosure("notes", iface_(NOTES, count=len(notes)), self.notes_open)
            b.gap(8)
        passed = [f for f in report["findings"] if f["status"] == PASS]
        if passed:
            b.paragraph(iface_(PASSED), 12.5, kit.MUTED)
            b.gap(4)
            b.chips([("done", iface_(CHECKS[f["check"]])) for f in passed])

    def text(self):
        """The report as shown, for the clipboard."""
        report = self.report
        lines = [iface_(TITLE, engine=PROFILES[self.target].display),
                 iface_(SUBTITLE, armature=self.armature_name, format=iface_(FORMAT_LABELS[self.fmt])),
                 iface_(VERDICT[report["verdict"]][1]), ""]
        for found in report["findings"]:
            text = render(found, iface_)
            mark = {PASS: "+", INFO: "i", WARN: "!", FAIL: "x"}[found["status"]]
            lines.append("[%s] %s: %s" % (mark, iface_(CHECKS[found["check"]]), text["found"]))
            if text["fix"]:
                lines.append("    " + text["fix"])
        return "\n".join(lines)

    def act(self, action, value, host):
        if action in {"close", "dismiss"}:
            host.close("closed")
            return
        if action == "target" and value in TARGETS:   # Another engine: its usual export format again.
            self.target, self.fmt = value, "AUTO"
            LAST_TARGET[0] = value
            self.run()
        elif action == "format" and value in FORMATS:
            self.fmt = value
            self.run()
        elif action == "again":
            self.run()
        elif action == "details":
            self.expanded ^= {value}
        elif action == "notes":
            self.notes_open = not self.notes_open
        elif action == "more":
            self.more_open = not self.more_open
        elif action == "copy" and self.report is not None:
            import bpy
            bpy.context.window_manager.clipboard = self.text()
            self.copied = True
            host.after(2.0, lambda: (setattr(self, "copied", False), host.redraw()))
        if action != "copy":
            self.copied = False
        host.redraw()
