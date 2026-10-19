# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The settings import's confirmation card and result notice: layout and words only (host-tested with a fake view).

``ImportCard`` is a centred ``card_kit`` card for **Import Blender Settings…** (``mode="import"``: where the settings
come from, what is replaced, what is kept and that Angelo backs its own settings up first) and **Undo Settings
Import** (``"undo"``). Only its primary button acts; Escape, Cancel and the close button leave everything as it is.
``closed`` hands the choice to ``decide``, which ``settings_import_ui`` runs once the card's operator has ended.

``notice_text`` words the corner notice after an import: what came over and, calmly, an add-on that did not load,
what waits for the next start and files that could not be copied.
"""

import os

from . import card_kit as kit
from .ui_text import iface_, join, language, n_

IMPORT_TITLE = n_("Import your Blender settings?")
FROM = n_("From {app} {version}")
REPLACES, KEEPS = n_("Replaces"), n_("Keeps")
REPLACES_PREFERENCES = n_("Angelo's preferences, shortcuts, Quick Favorites and add-on choices")
REPLACES_FILES = n_("Add-ons, extensions, presets and other files that have the same name in Blender's folder")
KEEPS_CHOICES = n_("Your language, look, AI Providers and chats")
KEEPS_BLENDER = n_("Blender's folder: Angelo only reads it")
BACKUP_TITLE = n_("Angelo backs up its current settings first")
BACKUP_BODY = n_("To go back, choose Undo Settings Import in File > Defaults.")
IMPORT, CANCEL = n_("Import"), n_("Cancel")
UNDO_TITLE = n_("Put back your earlier settings?")
UNDO_LEAD = n_("Undoes the import from {app} {version}.")
UNDO_BACK = n_("The preferences, shortcuts and files Angelo had before come back")
UNDO_REMOVED = n_("Files the import added are removed")
UNDO_KEEPS = n_("Your language and look stay as they are")
PUT_BACK = n_("Put back")
DONE_TITLE = n_("Your {app} {version} settings are in")
DONE_BODY = n_("Preferences, shortcuts, add-ons and presets came over.")
NOT_LOADED = n_("Did not load: {names}. You can fix or turn off an add-on in Preferences > Add-ons.")
NEXT_START = n_("Startup scripts, fonts and color management take effect the next time you open Angelo.")
NOT_COPIED = n_("Some files could not be copied; the log names them.")
FAILED_TITLE = n_("Your settings could not be brought over")
FAILED_BODY = n_("The log has the details. If anything changed, Undo Settings Import in File > Defaults puts it "
                 "back.")
UNDO_BUTTON = n_("Undo import")
UNDONE_TITLE = n_("Your earlier settings are back")
WIDTH = 540
BACKUP_KIND = "info"   # The kit notice's accent (ui_kit_status.NOTICE_KINDS).


def short_path(path, home=None):
    """A folder as the person knows it: their home as ``~``."""
    home = os.path.expanduser("~") if home is None else str(home)
    text = str(path)
    return "~" + text[len(home):] if home and text.startswith(home.rstrip(os.sep) + os.sep) else text


class ImportCard(kit.BaseCard):
    """Confirms an import (``mode="import"``) or its undo (``"undo"``); ``decide("confirm" | "cancel")`` on close."""

    kind, modal, priority = "settings_import", True, 70
    cancel_action, default_action, outside_dismisses = "cancel", None, False

    def __init__(self, mode, app, version, folder, decide):
        self.mode, self.app, self.version_label, self.folder = mode, app, version, folder
        self.decide, self.choice = decide, None

    def version(self):
        return self.mode, language()   # A language switch lays the card out again in that language.

    def layout(self, view, region_w, region_h, scale, ui):
        def plan(size):
            builder = self._build(view, size, region_w, ui)
            top = min((region_h + builder.height()) / 2, region_h - 10 * builder.s)
            return builder.place((region_w - builder.width) / 2, top, True, (region_w, region_h))
        return kit.fit(plan, region_w, region_h, scale, minimum=0.55)

    def _build(self, view, scale, region_w, ui):
        width = min(WIDTH * scale, region_w - 32 * scale)
        b = kit.Builder(view, scale, width, ui, padding=28)
        title_w = b.inner - 36 * scale   # Clear of the close button.
        if self.mode == "undo":
            b.heading(iface_(UNDO_TITLE), 21, width=title_w)
            b.gap(6)
            b.paragraph(iface_(UNDO_LEAD, app=self.app, version=self.version_label), 13.5, kit.MUTED)
            b.gap(12)
            b.bullets([iface_(UNDO_BACK), iface_(UNDO_REMOVED), iface_(UNDO_KEEPS)], color=kit.TEXT)
            confirm = PUT_BACK
        else:
            b.heading(iface_(IMPORT_TITLE), 21, width=title_w)
            b.gap(6)
            b.paragraph(iface_(FROM, app=self.app, version=self.version_label), 13.5, kit.TEXT)
            b.paragraph(short_path(self.folder), 11.5, kit.MUTED, mono=True, leading=1.5)
            b.gap(14)
            self._section(b, iface_(REPLACES), [iface_(REPLACES_PREFERENCES), iface_(REPLACES_FILES)])
            b.gap(10)
            self._section(b, iface_(KEEPS), [iface_(KEEPS_CHOICES), iface_(KEEPS_BLENDER)])
            b.gap(14)
            b.notice(BACKUP_KIND, iface_(BACKUP_TITLE), iface_(BACKUP_BODY))
            confirm = IMPORT
        b.gap(22)
        b.buttons([("cancel", None, iface_(CANCEL), "secondary", True),
                   ("confirm", None, iface_(confirm), "primary", True)], size=14, height=40)
        b.close_button("cancel")
        return b

    @staticmethod
    def _section(b, label, lines):
        b.paragraph(label, 12, kit.MUTED, leading=1.6)
        b.gap(2)
        b.bullets(lines, size=13.5, color=kit.TEXT)

    def act(self, action, value, host):
        if action in {"confirm", "cancel", "dismiss", "close"}:
            self.choice = "confirm" if action == "confirm" else "cancel"
            host.close("done")
        host.redraw()

    def closed(self, how):
        self.decide(self.choice or "cancel")


def notice_text(outcome):
    """``(icon, title, body, calm)`` for the corner notice after an import's ``outcome`` (settings_import_apply)."""
    if outcome.get("exception"):
        return "alert", iface_(FAILED_TITLE), iface_(FAILED_BODY), False
    lines = [iface_(DONE_BODY)]
    if outcome.get("failed"):
        lines.append(iface_(NOT_LOADED, names=", ".join(outcome["failed"])))
    if outcome.get("restart"):
        lines.append(iface_(NEXT_START))
    if outcome.get("errors"):
        lines.append(iface_(NOT_COPIED))
    calm = not (outcome.get("failed") or outcome.get("errors"))
    title = iface_(DONE_TITLE, app=outcome["app"], version=outcome["version"])
    return "check" if calm else "alert", title, join(lines, " "), calm   # join: reading order in Arabic
