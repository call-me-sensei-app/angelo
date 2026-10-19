# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""About Angelo: a centered card (``card_host``) opened from the Angelo menu's "About Angelo".

Its header is Angelo's brand art, the welcome screen's raven banner (``welcome_screen.prepare_banner``), with the
product name over the banner's dark side; without the art (an undownloaded asset) a plain mark stands in. It names the
product and version in the title bar's convention ("Angelo 1.0", ``APP_VERSION_DISPLAY``), says
what it is based on ("Based on Blender 5.2.2"), the licence and the build (date and hash; never the source
branch). Angelo's links come first (What's New, Credits, License, angelo.studio); donations and Blender's own
links sit in a quieter row below a rule. Return, Escape, Close and a click outside close it. Blender's
``WM_MT_splash_about`` popup stays as the fallback when the add-on is not loaded.

Contract: doc/angelo/app_identity.rst ("Links").
"""

import bpy
from bpy.types import Operator

from . import card_kit as kit, welcome_screen
from .ui_text import iface_, n_

# The About popup's and What's New's wording, so the catalogues already carry it.
BASED_ON = n_("Based on Blender {version}")
FREE, LICENSED = n_("Angelo is free software"), n_("Licensed under the GNU General Public License")
# Link labels in the "Operator" context, as on the About popup's buttons (the catalogues carry them there).
WHATS_NEW, CREDITS, LICENSE = n_("What's New", "Operator"), n_("Credits", "Operator"), n_("License", "Operator")
DONATE, SUPPORT_BLENDER = n_("Donate", "Operator"), n_("Support Blender", "Operator")
CLOSE = n_("Close")
OP = "Operator"
WIDTH = 520
LINKS = {   # Action -> key in the one URL table (``angelo_identity.json``).
    "credits": "credits", "license": "license", "site": "site",
    "donate": "donate", "blender": "blender_website", "fund": "blender_fund",
}


def build_text():
    """Date and short hash of this build (no words to translate), or None without a source identity."""
    date = bpy.app.build_commit_date.decode("utf-8", "replace")
    commit = bpy.app.build_hash.decode("ascii", "replace")[:11]
    return None if not commit or commit == "unknown" else date + " · " + commit


def open_link(action):
    import angelo_identity
    from . import update_card
    update_card.open_link(angelo_identity.url(LINKS[action]))


class AboutCard(kit.BaseCard):
    kind, modal, priority = "about", True, 60
    cancel_action, default_action, outside_dismisses = "close", "close", True

    def layout(self, view, region_w, region_h, scale, ui):
        banner = banner_ready()

        def plan(size):
            builder = self._build(view, size, region_w, ui, banner)
            top = min((region_h + builder.height()) / 2, region_h - 10 * builder.s)
            placed = builder.place((region_w - builder.width) / 2, top, True, (region_w, region_h))
            if banner:   # Over the card's own fill (``place`` puts it before every content op), under the text.
                x, y, w, h = placed.card
                # Inset a little, so the art's smaller corner radius stays inside the card's rounded corners.
                inset = 3 * placed.scale
                art_w = w - 2 * inset
                art = (x + inset, y + h - inset - art_w * BANNER_ASPECT, art_w, art_w * BANNER_ASPECT)
                index = next(i for i, op in enumerate(placed.ops) if op[0] == "shape" and op[1] == placed.card) + 1
                placed.ops.insert(index, ("paint", _draw_banner, art))
            return placed
        return kit.fit(plan, region_w, region_h, scale)

    def opened(self, host):
        welcome_screen.prepare_banner()

    def closed(self, how):
        from . import intro
        if not intro.is_open():   # The welcome screen shares the banner's pixels.
            welcome_screen.release()

    def _build(self, view, scale, region_w, ui, banner):
        import angelo_identity
        width = min(WIDTH * scale, region_w - 40 * scale)
        b = kit.Builder(view, scale, width, ui, padding=30)
        name = angelo_identity.PRODUCT_NAME + " " + angelo_identity.APP_VERSION_DISPLAY
        based_on = iface_(BASED_ON, version=bpy.app.version_string)
        if banner:
            # The banner's left half is dark and text-free; the raven sits on its right (mirrored in Arabic).
            banner_h = width * BANNER_ASPECT
            b.y = banner_h * 0.30
            b.paragraph(name, 32, kit.TEXT, width=width * 0.52, leading=1.2)
            b.paragraph(based_on, 13.5, kit.TEAL, width=width * 0.52, leading=1.4)
            b.y = max(b.y + 12 * scale, banner_h + 4 * scale)
        else:
            b.icon("spark", b.pad, b.y + 4 * scale, 48)
            text_x = b.pad + 64 * scale
            top = b.y
            b.paragraph(name, 30, kit.TEXT, x=text_x, width=width - text_x - b.pad - 24 * scale, leading=1.2)
            b.paragraph(based_on, 13.5, kit.TEAL, x=text_x, leading=1.4)
            b.y = max(b.y, top + 52 * scale) + 16 * scale
        b.paragraph(iface_(FREE), 13.5, kit.TEXT, leading=1.45)
        b.paragraph(iface_(LICENSED), 13.5, kit.MUTED, leading=1.45)
        build = build_text()
        if build:
            b.gap(6)
            b.paragraph(build, 12, kit.MUTED, mono=True, leading=1.4)
        b.gap(18)
        b.buttons([("site", None, "angelo.studio", "secondary", True)],
                  left=[("whatsnew", None, iface_(WHATS_NEW, OP), "ghost", True),
                        ("credits", None, iface_(CREDITS, OP), "ghost", True),
                        ("license", None, iface_(LICENSE, OP), "ghost", True)])
        b.gap(14)
        b.rule()
        b.gap(14)
        b.buttons([("close", None, iface_(CLOSE), "primary", True)],
                  left=[("donate", None, iface_(DONATE, OP), "ghost", True),
                        ("blender", None, "blender.org", "ghost", True),
                        ("fund", None, iface_(SUPPORT_BLENDER, OP), "ghost", True)], size=12.5, height=32)
        b.close_button("close")
        return b

    def act(self, action, value, host):
        if action in LINKS:
            open_link(action)
        elif action == "whatsnew":
            host.close("closed")
            bpy.app.timers.register(_show_whats_new, first_interval=0.05)
        elif action in {"close", "dismiss"}:
            host.close("closed")


BANNER_ASPECT = welcome_screen.BANNER_ASPECT


def banner_ready():
    """The brand banner's pixels are loaded (``prepare_banner`` runs outside drawing, when the card opens)."""
    return welcome_screen._banner["pixels"] is not None


def _draw_banner(rect):
    import gpu
    welcome_screen._draw_banner(gpu, rect)


def _show_whats_new():
    from . import card_host
    bpy.ops.angelo.whats_new()
    card_host.pump(lambda: True)


# Not "angelo.about": that idname belongs to the stock-Blender preview's dialog (``preview_branding``).
class ANGELO_OT_show_about(Operator):
    bl_idname = "angelo.show_about"
    bl_label = "About Angelo"
    bl_description = "Angelo's version, what it is based on, its licence and credits"

    def execute(self, context):
        from . import card_host
        welcome_screen.prepare_banner()   # Read once, outside drawing; the card's first layout then has the art.
        card_host.request(AboutCard())
        card_host.pump(lambda: True)   # Shown now, or after the card in front of it (``app_notices`` pumps).
        return {'FINISHED'}


def register():
    bpy.utils.register_class(ANGELO_OT_show_about)


def unregister():
    bpy.utils.unregister_class(ANGELO_OT_show_about)
