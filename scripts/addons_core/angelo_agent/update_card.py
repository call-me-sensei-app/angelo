# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The update notice and the small result notices, as corner cards (``card_host``).

The update notice follows the update through its phases (``live``, the
``update_ready`` module, says which one):

* **notify**: "Angelo 1.1 is available", the release's highlights, Download
  (opens the link in the browser), Release notes, Later (three days; one day
  for a critical update) and Skip this version (not offered for a critical
  update or an unsupported version). Shown when this install can't update
  itself, with one line saying why.
* **downloading** / **preparing**: a progress bar, or a spinner while the new
  version is unpacked and verified. Shown only when the person asked (Check
  for Updates…, the status-bar item); an automatic check stays quiet.
* **ready**: "Angelo 1.1 is ready" and **Restart to Update**, which is disabled
  with the reason while work a restart would stop is running (``work_guard``).
* **restarting**: a spinner until Angelo quits.

It never takes the keyboard or blocks the 3D view. ``NoticeCard`` is the same
card with a message and one or two buttons: the result of "Check for Updates…"
and the confirmation after a crash report was sent.

Text is translated where it is laid out (``ui_text``). The highlights come from Angelo's signed feed and
go through the interface translations too: a line that matches a catalogue entry (the bundled release's
lines are extracted) is shown translated, any other line stays English.
"""

from . import app_version, card_kit as kit
from .ui_text import iface_, n_

TITLE = n_("Angelo {version} is available")
TITLE_IMPORTANT = n_("Angelo {version} is an important update")
TITLE_DOWNLOADING = n_("Downloading Angelo {version}")
TITLE_PREPARING = n_("Getting Angelo {version} ready")
TITLE_READY = n_("Angelo {version} is ready")
TITLE_RESTARTING = n_("Restarting Angelo")
SUBTITLE = n_("You have Angelo {current}.")
SUBTITLE_IMPORTANT = n_("This update contains important fixes. You have Angelo {current}.")
SUBTITLE_UNSUPPORTED = n_("Angelo {current} is no longer supported. Please update.")
SUBTITLE_PREPARING = n_("Angelo is checking the new version. This takes a minute.")
SUBTITLE_READY = n_("Restart Angelo to finish updating. Your file opens again afterwards.")
SUBTITLE_RESTARTING = n_("Angelo opens again in a moment.")
PROGRESS = n_("{done} MB of {total} MB")
DOWNLOAD, RELEASE_NOTES, LATER, SKIP = n_("Download"), n_("Release notes"), n_("Later"), n_("Skip this version")
RESTART, HIDE = n_("Restart to Update"), n_("Hide")
# Why Angelo can't install this update itself (notify phase), by ``update_ready.unsupported_reason``.
WHY_MANUAL = {
    "location": n_("To get updates automatically, move Angelo to your Applications folder and open it from there."),
    "location_windows": n_("This copy of Angelo can't update itself. Install Angelo with its installer to get "
                           "updates automatically."),
    "permission": n_("An administrator installed Angelo here, so Angelo can't update itself."),
    "machine": n_("Angelo is installed for all users of this computer, so an administrator installs its updates."),
    "failed": n_("Angelo couldn't prepare this update by itself. Download it to install it."),
}
# Why Restart to Update waits (ready phase), by ``work_guard`` reason.
WHY_WAIT = {
    "scene": n_("You can restart when the chat waiting for its scene finishes: switch back to that scene, or stop "
                "the chat."),
    "agent": n_("You can restart when the agent finishes its task."),
    "preview": n_("You can restart after you accept or discard the agent's preview."),
    "flow": n_("You can restart when the Generation Flow run finishes."),
    "generation": n_("You can restart when the current generation finishes."),
    "job": n_("You can restart when the render, bake or export finishes."),
    "render": n_("You can restart when the render, bake or export finishes."),
    "interaction": n_("You can restart when you finish using the current tool."),
    "unknown": n_("You can restart when Angelo finishes its current work."),
}
WIDTH = 392
MB = 1000 * 1000


def open_link(url):
    """The one place the notices leave the app, so window fixtures can record the link instead of opening a browser."""
    import bpy
    bpy.ops.wm.url_open(url=url)


def corner_plan(builder, region_w, region_h):
    """Places a built card at the 3D view's bottom-right corner."""
    s = builder.s
    top = min(20 * s + builder.height(), region_h - 10 * s)   # A card that cannot fit keeps its heading in view.
    return builder.place(region_w - builder.width - 20 * s, top, False, (region_w, region_h))


class UpdateCard(kit.BaseCard):
    kind, modal, priority, cancel_action = "update", False, 10, "later"

    def __init__(self, offer, current, decide, live=None):
        """``decide(choice, offer)`` records "later" or "skip" (``update_check.Checker.snooze`` / ``skip``).

        ``live`` (``update_ready``) reports the phase and runs a restart; without it the card is the notice only.
        """
        self.offer, self.current, self.decide, self.live = offer, current, decide, live

    @property
    def animated(self):
        return self.phase() in {"downloading", "preparing", "restarting"}

    def phase(self):
        return self.live.phase(self.offer) if self.live is not None else "notify"

    def layout(self, view, region_w, region_h, scale, ui):
        return kit.fit(lambda size: corner_plan(self._build(view, size, region_w, ui), region_w, region_h),
                       region_w, region_h, scale)

    def _heading(self, b, title, subtitle):
        scale = b.s
        icon = 40 * scale
        b.icon("update", b.pad, b.y, 40)
        text_x = b.pad + icon + 14 * scale
        text_w = b.width - text_x - b.pad - 20 * scale
        top = b.y
        b.paragraph(title, 17, kit.TEXT, x=text_x, width=text_w, leading=1.3)
        b.paragraph(subtitle, 13, kit.MUTED, x=text_x, width=text_w, leading=1.45)
        b.y = max(b.y, top + icon) + 12 * scale

    def _build(self, view, scale, region_w, ui):
        width = min(WIDTH * scale, region_w - 40 * scale)
        b = kit.Builder(view, scale, width, ui, padding=22)
        phase = self.phase()
        version = app_version.display(self.offer.release.version)
        if phase in {"downloading", "preparing"}:
            self._build_progress(b, phase, version)
        elif phase == "ready":
            self._build_ready(b, version)
        elif phase == "restarting":
            self._heading(b, iface_(TITLE_RESTARTING), iface_(SUBTITLE_RESTARTING))
            b.spinner(b.width - b.pad - 10 * scale, b.pad + 20 * scale, 9)
            return b
        else:
            self._build_notice(b, version)
        b.gap(6)
        progress = phase in {"downloading", "preparing"}
        left = [] if self.offer.critical or progress else [("skip", None, iface_(SKIP), "ghost", True)]
        b.buttons([("later", None, iface_(HIDE if progress else LATER), "ghost", True)], left=left)
        b.close_button("later")
        return b

    def _build_notice(self, b, version):
        offer, current = self.offer, app_version.display(self.current)
        title = iface_(TITLE_IMPORTANT if offer.critical else TITLE, version=version)
        subtitle = (iface_(SUBTITLE_UNSUPPORTED, current=current) if offer.unsupported
                    else iface_(SUBTITLE_IMPORTANT if offer.critical else SUBTITLE, current=current))
        self._heading(b, title, subtitle)
        self._highlights(b)
        why = self.live.unsupported_reason(offer) if self.live is not None else ""
        if why in WHY_MANUAL:
            b.paragraph(iface_(WHY_MANUAL[why]), 12.5, kit.MUTED, leading=1.45)
            b.gap(8)
        if offer.download is not None:
            b.buttons([("notes", None, iface_(RELEASE_NOTES), "secondary", True),
                       ("download", None, iface_(DOWNLOAD), "primary", True)])
        else:  # No build for this platform in the feed: the release page is the way forward.
            b.buttons([("notes", None, iface_(RELEASE_NOTES), "primary", True)])

    def _build_progress(self, b, phase, version):
        title = TITLE_DOWNLOADING if phase == "downloading" else TITLE_PREPARING
        done, total = self.live.progress(self.offer)
        subtitle = (iface_(PROGRESS, done="%d" % (done // MB), total="%d" % max(1, total // MB))
                    if phase == "downloading" else iface_(SUBTITLE_PREPARING))
        self._heading(b, iface_(title, version=version), subtitle)
        if phase == "downloading":
            b.progress(done / total if total else 0.0)
        else:
            b.spinner(b.width - b.pad - 10 * b.s, b.pad + 20 * b.s, 9)
        b.gap(14)
        b.buttons([("notes", None, iface_(RELEASE_NOTES), "secondary", True)])

    def _build_ready(self, b, version):
        title = TITLE_IMPORTANT if self.offer.critical else TITLE_READY
        self._heading(b, iface_(title, version=version), iface_(SUBTITLE_READY))
        self._highlights(b)
        wait = self.live.wait_reason()
        b.buttons([("notes", None, iface_(RELEASE_NOTES), "secondary", True),
                   ("restart", None, iface_(RESTART), "primary", not wait)])
        if wait:
            b.gap(8)
            b.paragraph(iface_(WHY_WAIT.get(wait, WHY_WAIT["unknown"])), 12.5, kit.MUTED, leading=1.45)

    def _highlights(self, b):
        if self.offer.release.highlights:
            b.bullets([iface_(line) for line in self.offer.release.highlights[:3]], size=13, color=kit.MUTED)
            b.gap(6)

    def act(self, action, value, host):
        if action == "download" and self.offer.download is not None:
            open_link(self.offer.download.url)
            self.decide("later", self.offer)
            host.close("download")
        elif action == "notes":
            open_link(self.offer.release.notes_url)
        elif action == "restart" and self.live is not None:
            self.live.restart(self.offer, host)
        elif action in {"later", "skip"}:
            if self.phase() == "restarting":
                return
            self.decide(action, self.offer)
            host.close(action)

    def version(self):
        if self.live is None:
            return self.offer.release.version
        done, total = self.live.progress(self.offer)
        step = int(100 * done / total) if total else 0
        return (self.offer.release.version, self.phase(), step, self.live.wait_reason(),
                self.live.unsupported_reason(self.offer))


class NoticeCard(kit.BaseCard):
    """A message with up to two buttons. ``buttons`` is ``[(action, label, style)]``; ``on_action(action, host)``."""

    kind, modal, priority, cancel_action = "notice", False, 30, "close"

    def __init__(self, icon, title, body, buttons=(), on_action=None, lifetime=0):
        self.icon, self.title, self.body, self.buttons = icon, title, body, tuple(buttons)
        self.on_action, self.lifetime = on_action, lifetime

    def layout(self, view, region_w, region_h, scale, ui):
        return kit.fit(lambda size: corner_plan(self._build(view, size, region_w, ui), region_w, region_h),
                       region_w, region_h, scale)

    def _build(self, view, scale, region_w, ui):
        width = min(WIDTH * scale, region_w - 40 * scale)
        b = kit.Builder(view, scale, width, ui, padding=22)
        icon = 40 * scale
        b.icon(self.icon, b.pad, b.y, 40)
        text_x = b.pad + icon + 14 * scale
        text_w = width - text_x - b.pad - 20 * scale
        top = b.y
        b.paragraph(self.title, 17, kit.TEXT, x=text_x, width=text_w, leading=1.3)
        if self.body:
            b.paragraph(self.body, 13, kit.MUTED, x=text_x, width=text_w, leading=1.45)
        b.y = max(b.y, top + icon)
        if self.buttons:
            b.gap(14)
            b.buttons([(action, None, label, style, True) for action, label, style in self.buttons])
        b.close_button("close")
        return b

    def act(self, action, value, host):
        if self.on_action is not None:
            self.on_action(action, host)
        if action == "close":
            host.close("closed")

    def opened(self, host):
        if self.lifetime:
            host.after(self.lifetime, lambda: host.close("expired"))
