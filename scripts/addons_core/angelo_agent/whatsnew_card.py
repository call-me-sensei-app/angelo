# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What's New: a centered card with the running release's highlights (``card_host``).

Shown once after an update (``release_notes.whats_new``) and from Help > What's New. Return, Escape, Got it and
a click outside close it. The release's wording comes from ``CHANGELOG.md`` through the bundled
``release_notes.json``. It is Angelo's own text, so each line goes through the interface translations
when it is drawn (the extractor lists the bundled release's lines, ``i18n/sources.py``); a line with
no catalogue entry stays English.
"""

from . import app_version, card_kit as kit
from .ui_text import iface_, n_

TITLE = n_("What's new in Angelo {version}")
SHOW_ALL, HIDE_ALL = n_("Show everything in this release"), n_("Show less")
GOT_IT, ONLINE = n_("Got it"), n_("Read the release notes online")
UNRELEASED = n_("Not released yet")
BASED_ON = n_("Based on Blender {version}")
WIDTH = 560
LIST_HEIGHT = 230


def full_notes(notes):
    """Every section of a release for the scrolling list: ``[(style, text)]`` (``card_kit.Builder.scrollbox``)."""
    blocks = []
    for heading, items in notes.sections:
        blocks.append(("heading", iface_(heading)))
        blocks += [("item", iface_(item)) for item in items]
    return blocks


class WhatsNewCard(kit.BaseCard):
    kind, modal, priority = "whatsnew", True, 50
    cancel_action, default_action, outside_dismisses = "close", "close", True
    retry_when_lost = True

    def __init__(self, notes, open_link, finished):
        self.notes, self.open_link, self.finished = notes, open_link, finished
        self.expanded = False

    def version(self):
        return self.expanded

    def layout(self, view, region_w, region_h, scale, ui):
        def plan(size):
            builder = self._build(view, size, region_w, ui)
            top = min((region_h + builder.height()) / 2, region_h - 10 * builder.s)   # A card that cannot fit keeps its heading in view.
            return builder.place((region_w - builder.width) / 2, top, True, (region_w, region_h))
        return kit.fit(plan, region_w, region_h, scale)

    def _build(self, view, scale, region_w, ui):
        notes = self.notes
        width = min(WIDTH * scale, region_w - 40 * scale)
        b = kit.Builder(view, scale, width, ui, padding=28)
        b.icon("spark", b.pad, b.y, 44)
        text_x = b.pad + 58 * scale
        top = b.y
        b.paragraph(iface_(TITLE, version=app_version.display(notes.version)), 22, kit.TEXT, x=text_x,
                    width=width - text_x - b.pad - 24 * scale, leading=1.25)
        if not notes.date:
            b.paragraph(iface_(UNRELEASED), 12.5, kit.AMBER, x=text_x, leading=1.4)
        elif notes.blender:
            b.paragraph(iface_(BASED_ON, version=notes.blender), 12.5, kit.MUTED, x=text_x, leading=1.4)
        b.y = max(b.y, top + 44 * scale) + 14 * scale
        if notes.summary:
            b.paragraph(iface_(notes.summary), 14, kit.MUTED, leading=1.5)
            b.gap(12)
        b.bullets([iface_(line) for line in notes.highlights], size=14, color=kit.TEXT)
        b.gap(10)
        if notes.sections:
            b.disclosure("toggle", iface_(HIDE_ALL if self.expanded else SHOW_ALL), self.expanded)
            if self.expanded:
                b.gap(8)
                b.scrollbox("notes", full_notes(notes), LIST_HEIGHT, size=13, mono=False)
        b.gap(16)
        b.buttons([("close", None, iface_(GOT_IT), "primary", True)], left=[("online", None, iface_(ONLINE), "ghost", True)])
        b.close_button("close")
        return b

    def act(self, action, value, host):
        if action == "toggle":
            self.expanded = not self.expanded
        elif action == "online":
            self.open_link(app_version.link("release_notes"))
        elif action in {"close", "dismiss"}:
            host.close("closed")

    def closed(self, how):
        if how not in {"lost", "replaced"}:
            self.finished()
