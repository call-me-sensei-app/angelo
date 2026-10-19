# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The gallery card ``test_blender_ui_kit_gallery`` shows: every ``ui_kit`` component in every state.

Fixture text is English sample content (the components' own labels are translated by the kit). Pages:
0 status (chips, notices, buttons), 1 controls (code blocks, toggles, segmented controls), 2 fields and
dropdowns, 3 rows, provider tiles and the scrollbar (pages 2 and 3 exist once those components have landed).
"""

from angelo_agent import card_kit as kit, ui_kit

try:
    from angelo_agent import ui_kit_field  # noqa: F401
    from angelo_agent import ui_kit_select  # noqa: F401
    HAS_FIELDS = True
except ImportError:
    HAS_FIELDS = False
try:
    from angelo_agent import ui_kit_rows  # noqa: F401
    HAS_ROWS = True
except ImportError:
    HAS_ROWS = False
PAGES = 2 + HAS_FIELDS + HAS_ROWS

SCRIPT = ("import bpy\n"
          "cube = bpy.data.objects.get(\"Cube\")\n"
          "cube.location.z += 1.0  # a long comment that runs past the edge of the block to show the fade clipping\n"
          "print(cube.name)")


def inside(outer, inner):
    return (outer[0] - 0.5 <= inner[0] and inner[0] + inner[2] <= outer[0] + outer[2] + 0.5
            and outer[1] - 0.5 <= inner[1] and inner[1] + inner[3] <= outer[1] + outer[3] + 0.5)


def _chat_view_demo(view, rect, scale, ui, **params):
    """The same components drawn with the chat canvas module itself as the view (blf measuring), as chat
    surfaces will draw them: a chip, a switch and a list row."""
    from angelo_agent import chat_view, ui_kit_controls, ui_kit_rows, ui_kit_status
    w, h = ui_kit_status.chip_size(chat_view, "needs_you", scale)
    ui_kit_status.draw_chip(chat_view, (rect[0], rect[1] + (rect[3] - h) / 2, w, h), scale, None, "needs_you")
    tw, th = ui_kit_controls.toggle_size(scale)
    ui_kit_controls.draw_toggle(chat_view, (rect[0] + w + 12 * scale, rect[1] + (rect[3] - th) / 2, tw, th), scale,
                                None, True)
    x = rect[0] + w + tw + 24 * scale
    ui_kit_rows.draw_list_row(chat_view, (x, rect[1], rect[0] + rect[2] - x, rect[3]), scale, None,
                              "Drawn by the chat canvas", lead=("letter", "C", "ok"), trailing=("check",))


ui_kit.register_component("chat_view_demo", _chat_view_demo)


class Gallery(kit.BaseCard):
    kind = "ui_kit_gallery"
    modal = True
    priority = 100
    animated = True

    def __init__(self):
        self.page, self.ver, self.narrow = 0, 0, False
        self.expanded, self.copied = False, None
        self.on = {"sync": True, "beta": False, "locked": True}
        self.theme, self.swatch = "dark", "teal"
        self.log = []
        self.extra = {}   # Page 2 and 3 state (fields, dropdowns, rows), owned by the drivers in those modules.

    def version(self):
        return (self.ver, self.page, self.narrow)

    def layout(self, view, region_w, region_h, scale, ui):
        def build(s):
            width = min((320 if self.narrow else 600) * s, region_w - 32 * s)
            b = kit.Builder(view, s, width, ui, padding=22)
            b.close_button("close")
            b.heading("UI kit · " + ("Status", "Controls", "Fields", "Rows")[self.page], size=19)
            b.gap(10)
            getattr(self, "_page%d" % self.page)(b)
            return b.place((region_w - b.width) / 2, (region_h + b.height()) / 2, True, (region_w, region_h))
        return kit.fit(build, region_w, region_h, scale, minimum=0.5)

    def _page0(self, b):
        b.chips([("running", None), ("done", None), ("failed", None), ("needs_you", None), ("preview", None),
                 ("paid", None)])
        b.gap(14)
        b.notice("info", "Angelo can see your scene", "It reads object names and sizes to plan the next step.")
        b.gap(10)
        b.notice("warn", "Sign in again to keep working", "Your Codex sign-in expires in 2 days.",
                 actions=[("retry", None, "Sign in", "primary"), ("later", None, "Later", "ghost")])
        b.gap(10)
        b.notice("error", "Could not reach the provider", "Check your connection, then try again.",
                 actions=[("again", None, "Try again", "secondary")],
                 details="NET-3: connection timed out after 30 s\nhost: api.example.invalid:443",
                 details_action="details", expanded=self.expanded)
        b.gap(10)
        b.notice("success", "Key saved", "Angelo will use it for the next message.")
        b.gap(14)
        b.kit("chat_view_demo", 44 * b.s, {})
        b.gap(10)
        b.buttons([("delete", None, "Remove key", "destructive", True), ("nodelete", None, "Delete", "destructive", False),
                   ("ok", None, "Save", "primary", True)], left=[("cancel", None, "Cancel", "ghost", True)])

    def _page1(self, b):
        b.code_block(SCRIPT, line_numbers=True, label="Python", copy_action="copy", copy_value="script",
                     copied=self.copied == "script")
        b.gap(10)
        b.code_block("bpy.ops.mesh.primitive_cube_add(size=2.0, enter_editmode=False, align='WORLD')")
        b.gap(12)
        b.toggle("toggle", "sync", "Sync scene changes", self.on["sync"], caption="Angelo sees edits as you make them")
        b.toggle("toggle", "beta", "Remember in Keychain", self.on["beta"],
                 caption="On: saved in your macOS login Keychain for this profile. Off: memory only; enter it again "
                         "after restarting.")
        b.toggle("toggle", "locked", "Send usage data", self.on["locked"], caption="Set by your organisation",
                 enabled=False)
        b.gap(12)
        b.segmented("theme", [("dark", "Dark"), ("light", "Light"), ("auto", "Auto")], self.theme)
        b.gap(10)
        b.segmented("swatch", [("teal", "Teal", kit.TEAL), ("amber", "Amber", kit.AMBER),
                               ("dark", "Angelo Dark", (0.090, 0.098, 0.118, 1))], self.swatch)

    def _page2(self, b):
        from ui_kit_gallery_fields import page_fields
        page_fields(self, b)

    def _page3(self, b):
        from ui_kit_gallery_fields import page_rows
        page_rows(self, b)

    def key_event(self, event, host):
        from ui_kit_gallery_fields import key_event
        return key_event(self, event, host)

    def act(self, action, value, host):
        self.log.append((action, value))
        if action == "details":
            self.expanded = not self.expanded
        elif action == "toggle":
            self.on[value] = not self.on[value]
        elif action == "theme":
            self.theme = value
        elif action == "swatch":
            self.swatch = value
        elif action == "copy":
            self.copied = value
        elif action == "close":
            host.close("done")
        else:
            from ui_kit_gallery_fields import act
            act(self, action, value, host)
        self.ver += 1


def drive_fields(checks, card, state):
    from ui_kit_gallery_fields import drive_fields as drive
    yield from drive(checks, card, state)


def drive_rows(checks, card, state):
    from ui_kit_gallery_rows import drive_rows as drive
    yield from drive(checks, card, state)
