# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The returning user's start panel (``card_host``, modal), instead of Blender's New File splash page.

"Continue where you left off" shows the most recent saved scenes as thumbnail tiles (the preview each file
saved, its name, and its last chat title or when it was last edited); a tile opens its scene. "New scene" keeps the fresh scene Angelo started with,
"Open…" is Blender's file browser and "What's new" opens the release card. No templates, no Recover Last
Session row and no Donate (that lives in About). Escape, a click outside and New scene close it.

Opening a file drops every modal operator, so the card closes first and the file opens from a timer
(``splash_setup.after_close``). Layout is pure; files are read when the card is made, never while drawing.
"""

import os
import time

from . import card_kit as kit, firstlaunch_controls as fc, ui_tokens as tokens
from .ui_text import iface_, n_, plural_

WIDTH = 680
MAX_TILES = 6
TITLE = n_("Welcome back")
SUBTITLE = n_("Pick up a scene where you left it, or start something new.")
RECENT = n_("Continue where you left off")
NO_RECENT = n_("Scenes you save appear here.")
NEW, OPEN, WHATS_NEW = n_("New scene"), n_("Open…"), n_("What's new")


def recent_scenes(history_path, exists=os.path.isfile, limit=MAX_TILES):
    """Blender's recent-files list (``recent-files.txt``, newest first), only files that still exist."""
    try:
        with open(history_path, encoding="utf-8") as handle:
            lines = [line.strip() for line in handle]
    except (OSError, UnicodeDecodeError):
        return []
    seen, scenes = set(), []
    for path in lines:
        if path and path not in seen and path.lower().endswith(".blend") and exists(path):
            seen.add(path)
            scenes.append(path)
        if len(scenes) >= limit:
            break
    return scenes


def edited(mtime, now=None):
    """When a scene was last saved, in words: "Edited today", "Edited yesterday", "Edited 3 days ago"… (local days)."""
    if mtime is None:
        return ""
    now = time.time() if now is None else now
    day = 86400
    then, today = time.localtime(mtime), time.localtime(now)
    days = int((time.mktime(today[:3] + (12, 0, 0, 0, 0, -1)) - time.mktime(then[:3] + (12, 0, 0, 0, 0, -1)) + day / 2)
               // day)
    if days <= 0:
        return iface_("Edited today")
    if days == 1:
        return iface_("Edited yesterday")
    if days < 7:
        return plural_(days, "Edited {count} day ago", "Edited {count} days ago")
    if days < 30:
        return plural_(days // 7, "Edited {count} week ago", "Edited {count} weeks ago")
    if days < 365:
        return plural_(days // 30, "Edited {count} month ago", "Edited {count} months ago")
    return plural_(days // 365, "Edited {count} year ago", "Edited {count} years ago")


def tile_lines(path, title=None, mtime=None, now=None):
    """A tile's text: the scene's name, then its last chat title when known, else when it was last edited."""
    name = os.path.splitext(os.path.basename(path))[0]
    second = title or edited(mtime, now)
    return [(name, 13.5, kit.TEXT), (second, 11.5, kit.MUTED)]


def modified_times(paths):
    """Each scene file's last-modified time (read when the card is made, never while drawing)."""
    times = {}
    for path in paths:
        try:
            times[path] = os.path.getmtime(path)
        except OSError:
            pass
    return times


class StartCard(kit.BaseCard):
    kind, modal, priority = "start", True, 80
    cancel_action, default_action, outside_dismisses = "new", "new", True

    def __init__(self, scenes, act, titles=None, mtimes=None):
        self.scenes, self.on_act = list(scenes)[:MAX_TILES], act
        self.titles, self.mtimes = titles or {}, mtimes or {}

    def layout(self, view, region_w, region_h, scale, ui):
        def plan(size):
            builder = self._build(view, size, region_w, ui)
            top = min((region_h + builder.height()) / 2, region_h - 10 * builder.s)
            return builder.place((region_w - builder.width) / 2, top, True, (region_w, region_h))
        return kit.fit(plan, region_w, region_h, scale, minimum=0.5)

    def _build(self, view, s, region_w, ui):
        width = min(WIDTH * s, region_w - 32 * s)
        b = fc.Builder(view, s, width, ui, padding=28)
        banner_h = width * fc.BANNER_ASPECT
        b.custom(0, 0, width, banner_h, fc.banner, fc.banner_ready())
        b.y = banner_h * 0.32
        b.paragraph(iface_(TITLE), 26, kit.TEXT, width=width * 0.6, leading=1.25)
        b.paragraph(iface_(SUBTITLE), 13.5, tokens.SUBTITLE, width=width * 0.56, leading=1.45)
        b.y = max(b.y + 10 * s, banner_h + 22 * s)
        b.paragraph(iface_(RECENT), tokens.TYPE_SMALL, kit.MUTED, leading=1.4)
        b.gap(8)
        if self.scenes:
            columns = 3 if b.inner >= 520 * s else 2 if b.inner >= 300 * s else 1
            gap = 12 * s
            tile_w = (b.inner - gap * (columns - 1)) / columns
            tile_h = (tile_w - 12 * s) * 0.6 + 12 * s + 50 * s
            for index, path in enumerate(self.scenes):
                column, row = index % columns, index // columns
                lines = [(_elide(b, text, tile_w - 24 * s, size), size, color)
                         for text, size, color in tile_lines(path, self.titles.get(path), self.mtimes.get(path))]
                b.custom(b.pad + column * (tile_w + gap), b.y + row * (tile_h + gap), tile_w, tile_h, fc.tile,
                         path, lines, hit=("scene", index))
            rows = -(-len(self.scenes) // columns)
            b.y += rows * tile_h + (rows - 1) * gap
        else:
            b.paragraph(iface_(NO_RECENT), tokens.TYPE_BODY, kit.TEXT, leading=1.5)
        b.gap(24)
        b.buttons([("open", None, iface_(OPEN), "secondary", True), ("new", None, iface_(NEW), "primary", True)],
                  left=[("whats_new", None, iface_(WHATS_NEW), "ghost", True)], size=14, height=40)
        b.close_button("new")
        return b

    def act(self, action, value, host):
        path = self.scenes[value] if action == "scene" else None
        host.close("done")
        self.on_act(action, path)


def _elide(b, text, width, size):
    from . import canvas_rtl
    return canvas_rtl.elide(text, lambda value: b.measure(value, size * b.s), width)
