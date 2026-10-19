# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The "Angelo quit unexpectedly" dialog: a modal card over the 3D view (``card_host``).

Shown at the next launch after a crash (``app_notices``). It says what a report is, shows the
exact text that would be sent behind "Show what will be sent", and asks. Phases (``crash_flow``):
ask, sending, sent, failed, and review of a report sent earlier.

Until a deployment announces the crash-report endpoint (``crash_upload.endpoint``), Send report is
shown **disabled** in the same place, the text says sending is not available yet, and the dialog
offers Save report… and Report on the website (the report page with the system, graphics card
and version filled in, never the stack). The dialog never pretends to send.

Escape closes it without sending. Return does nothing until a button has been focused with Tab:
a stray key is never consent. Every control has hover, pressed, keyboard-focus and (Send report)
disabled looks (``card_draw``). Button labels are sentence case, like the welcome screen's.
"""

from . import card_kit as kit
from .crash_flow import ASK, FAILED, REVIEW, SENDING, SENT
from .ui_text import iface_, n_

TITLE = n_("Angelo quit unexpectedly")
INTRO_CAN_SEND = n_("Sorry about that. A crash report helps us find and fix the problem.")
DETAIL_CAN_SEND = n_("It contains technical details only: where in the program it stopped, your operating system "
                     "and your graphics card. It never contains your scenes, prompts, chats, files, file names or keys.")
INTRO_CANNOT_SEND = n_("Sorry about that. Sending crash reports from Angelo is not available yet.")
DETAIL_CANNOT_SEND = n_("You can save the report and attach it when you report the problem on the website. It holds "
                        "technical details only, never your scenes, prompts, chats, files, file names or keys.")
SHOW, HIDE = n_("Show what will be sent"), n_("Hide what will be sent")
SHOW_SENT, HIDE_SENT = n_("Show what was sent"), n_("Hide what was sent")
SHOW_REPORT, HIDE_REPORT = n_("Show the report"), n_("Hide the report")
REMEMBER = n_("Remember my choice")
SENDING_TITLE, SENDING_BODY = n_("Sending the report"), n_("This takes a moment.")
SENT_TITLE = n_("Thank you")
SENT_BODY = n_("The crash report was sent. It is not linked to you or your account.")
REPORT_ID = n_("Report ID: {id}")
FAILED_TITLE = n_("The report could not be sent")
FAILED_OFFLINE = n_("Check your internet connection and try again. You can also save the report.")
FAILED_REFUSED = n_("The server did not accept this report. You can save it and attach it when you report the problem.")
FAILED_BUSY = n_("The report service is busy right now. You can save the report and attach it when you report the "
                 "problem.")
REVIEW_TITLE = n_("The last crash report Angelo sent")
REVIEW_BODY = n_("This is exactly what was sent.")
SEND, DONT_SEND, SAVE, PROBLEM, CLOSE, TRY_AGAIN, COPY, STOP_AUTO = (
    n_("Send report"), n_("Don't send"), n_("Save report…"), n_("Report on the website"), n_("Close", "Angelo"),
    n_("Try again"), n_("Copy report"), n_("Ask me each time"))
WIDTH = 610
BOX_HEIGHT = 218


class CrashCard(kit.BaseCard):
    kind, modal, priority, cancel_action = "crash", True, 100, "dismiss"
    retry_when_lost = True

    def __init__(self, flow, actions):
        """``actions`` provides ``save()``, ``open_problem(url)``, ``copy(text)``, ``finished()`` and ``stop_automatic()``."""
        self.flow, self.actions = flow, actions
        self.expanded = flow.phase == REVIEW
        self._version = 0

    @property
    def animated(self):
        return self.flow.phase == SENDING

    def version(self):
        return (self._version, self.flow.phase, self.expanded, self.flow.remember, self.flow.saved)

    def layout(self, view, region_w, region_h, scale, ui):
        def plan(size):
            builder = self._build(view, size, region_w, ui)
            top = min((region_h + builder.height()) / 2, region_h - 10 * builder.s)   # A card that cannot fit keeps its heading in view.
            return builder.place((region_w - builder.width) / 2, top, True, (region_w, region_h))
        return kit.fit(plan, region_w, region_h, scale)

    def _build(self, view, scale, region_w, ui):
        flow, phase = self.flow, self.flow.phase
        width = min(WIDTH * scale, region_w - 40 * scale)
        b = kit.Builder(view, scale, width, ui, padding=28)
        icon_x, text_x = b.pad, b.pad + 56 * scale
        text_w = width - text_x - b.pad
        icon = {SENT: "check", REVIEW: "check", SENDING: "update"}.get(phase, "alert")   # Amber only for the crash itself.
        if phase == SENDING:
            b.spinner(icon_x + 22 * scale, b.y + 22 * scale, 14)
        else:
            b.icon(icon, icon_x, b.y, 44)
        top = b.y
        title, body = self._headline()
        b.paragraph(title, 22, kit.TEXT, x=text_x, width=text_w, leading=1.25)
        b.paragraph(body, 14, kit.MUTED, x=text_x, width=text_w, leading=1.5)
        if phase in {SENT, REVIEW}:   # The random ID on its own line, in the report's own type, so it can be read out.
            b.paragraph(iface_(REPORT_ID, id=flow.report_id), 12, kit.MUTED, x=text_x, width=text_w, leading=1.6, mono=True)
        b.y = max(b.y, top + 44 * scale)
        if phase == ASK:
            b.gap(8)
            b.paragraph(iface_(DETAIL_CAN_SEND if flow.can_send else DETAIL_CANNOT_SEND), 14, kit.MUTED, leading=1.5)
        if phase in {ASK, FAILED, SENT, REVIEW}:
            b.gap(12)
            labels = ((SHOW_SENT, HIDE_SENT) if phase in {SENT, REVIEW} else (SHOW, HIDE) if flow.can_send
                      else (SHOW_REPORT, HIDE_REPORT))
            if phase != REVIEW:
                b.disclosure("toggle", iface_(labels[1] if self.expanded else labels[0]), self.expanded)
            if self.expanded:
                b.gap(8)
                b.scrollbox("report", flow.text, BOX_HEIGHT)
                b.gap(8)
                b.buttons([("copy", None, iface_(COPY), "ghost", True)])
        if phase == ASK and flow.can_send:
            b.gap(14)
            b.checkbox("remember", None, iface_(REMEMBER), flow.remember)
        b.gap(18)
        self._buttons(b)
        return b

    def _headline(self):
        flow = self.flow
        if flow.phase == SENDING:
            return iface_(SENDING_TITLE), iface_(SENDING_BODY)
        if flow.phase == SENT:
            return iface_(SENT_TITLE), iface_(SENT_BODY)
        if flow.phase == FAILED:
            outcome = flow.outcome
            body = (FAILED_REFUSED if outcome is not None and outcome.kind == "refused"
                    else FAILED_BUSY if outcome is not None and outcome.busy else FAILED_OFFLINE)
            return iface_(FAILED_TITLE), iface_(body)
        if flow.phase == REVIEW:
            return iface_(REVIEW_TITLE), iface_(REVIEW_BODY)
        return iface_(TITLE), iface_(INTRO_CAN_SEND if flow.can_send else INTRO_CANNOT_SEND)

    def _buttons(self, b):
        flow = self.flow
        if flow.phase == SENDING:
            return
        problem = [("problem", None, iface_(PROBLEM), "ghost", True)]
        if flow.phase == ASK:   # Send report keeps its place; without an endpoint it is shown disabled, never hidden.
            middle = ("decline", None, iface_(DONT_SEND), "secondary", True) if flow.can_send else \
                ("dismiss", None, iface_(CLOSE), "secondary", True)
            b.buttons([("save", None, iface_(SAVE), "secondary", True), middle,
                       ("send", None, iface_(SEND), "primary", flow.can_send)], left=problem)
        elif flow.phase == FAILED:
            retry = flow.outcome is None or (flow.outcome.kind != "refused" and not flow.outcome.busy)
            right = [("save", None, iface_(SAVE), "primary" if not retry else "secondary", True)]
            if retry:
                right.append(("send", None, iface_(TRY_AGAIN), "primary", True))
            b.buttons(right, left=[("dismiss", None, iface_(CLOSE), "ghost", True)])
        elif flow.phase == SENT:
            b.buttons([("dismiss", None, iface_(CLOSE), "primary", True)], left=problem)
        else:  # REVIEW
            b.buttons([("dismiss", None, iface_(CLOSE), "primary", True)], left=[("stop", None, iface_(STOP_AUTO), "ghost", True)])

    def act(self, action, value, host):
        flow = self.flow
        if action == "toggle":
            self.expanded = not self.expanded
        elif action == "remember":
            flow.remember = not flow.remember
        elif action == "send" and flow.phase in {ASK, FAILED} and flow.can_send:
            if flow.phase == FAILED:
                flow.phase = ASK
            if flow.start_send(flow.remember):
                self._poll(host)
        elif action == "decline" and flow.phase == ASK:
            flow.decline(flow.remember)
            host.close("declined")
        elif action == "save":
            self.actions.save()
        elif action == "problem":
            self.actions.open_problem(flow.problem_url())
        elif action == "copy":
            self.actions.copy(flow.text)
        elif action == "stop":
            self.actions.stop_automatic()
            host.close("stopped")
        elif action in {"dismiss", "close"}:
            if flow.phase != SENDING:
                host.close("dismissed")
        self._version += 1

    def _poll(self, host):
        """Re-checks the upload until it finishes, then redraws (``card_host`` keeps the spinner turning)."""
        def check():
            if self.flow.poll():
                self._version += 1
                host.redraw()
                return None
            return 0.15

        host.after(0.15, check)

    def closed(self, how):
        if how in {"lost", "replaced"}:
            return
        self.flow.finish()
        self.actions.finished()
