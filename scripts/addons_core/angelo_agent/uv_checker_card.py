# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The UV checker card (``card_host``): every option of one 3D view's UV checker (doc/angelo/uv_checker.rst).

A corner card, non-modal, so the person keeps orbiting, editing and unwrapping while each change shows live in the
view behind it. It reads and writes only that view's ``SpaceView3D.uv_checker`` (nothing in the scene, no Undo
step): on or off, the pattern (color grid, checker, texel density, stretch), how often it repeats, which objects and
which UV map, texel density's texture size and target with its legend, and flipped UVs. Opened by
``angelo.uv_checker`` (``uv_checker``) from the chat bar's + menu, Director's top bar, the 3D view header and F3.
``layout`` is pure apart from the view's settings it is given, so it is host-tested with a fake view.
"""

from . import card_kit as kit
from .ui_text import iface_, n_

KIND = "uv_checker"
WIDTH = 400
LABEL_WIDTH = 132   # The start column of a labelled row (Texture size, Target, Repeats, Objects).
TITLE = n_("UV checker")
SUBTITLE = n_("Drawn over the meshes in this view only. Materials, images and nodes stay as they are.")
SHOW = n_("Show UV checker")
# The switch's words carry Angelo's context: Blender's catalogue defines them in other senses (a verb for Stretch,
# "controller" for Checker in some languages), and ``iface_`` reads the Angelo context first (GLOSSARY.md).
PATTERN, REPEAT, OBJECTS, UV_MAP = n_("Pattern", "Angelo"), n_("Repeats per UV tile"), n_("Objects"), n_("UV map")
PATTERNS = (("COLOR_GRID", n_("Grid", "Angelo")), ("CHECKER", n_("Checker", "Angelo")),
            ("TEXEL_DENSITY", n_("Density", "Angelo")), ("STRETCH", n_("Stretch", "Angelo")))
WHY = {"COLOR_GRID": n_("Lettered, colored cells show where each part of the texture lands and which way it faces."),
       "CHECKER": n_("Even squares show stretching and scale at a glance."),
       "TEXEL_DENSITY": n_("Texels per meter against your target. Each color step is twice as many or half as many."),
       "STRETCH": n_("How far each face's UVs are from its 3D shape. Blue is none; red is a lot.")}
SCALES = (1, 2, 4, 8, 16)
TEXTURE, TARGET = n_("Texture size"), n_("Target texels per meter")
TEXTURES = (512, 1024, 2048, 4096, 8192)
TARGETS = (128, 256, 512, 1024, 2048)
EVERY, SELECTED = n_("All visible"), n_("Selected")
ACTIVE, RENDER = n_("Active (editing)"), n_("Render")
FLIPPED = n_("Mark flipped UVs")
MISSING = n_("Meshes without this UV map: {count} ({names}). They are hatched magenta and grey until they have "
             "one.")
WARN = "warn"   # i18n: notice kind (ui_kit_status), never shown
DENSITY_STEPS = ("¼×", "½×", n_("target"), "2×", "4×")
STRETCH_STEPS = (n_("none"), n_("high"))


def heat_color(t):
    """The overlay's ramp (``overlay_uv_checker_frag.glsl``, ``heat_color``): blue (0) to red (1)."""
    t = min(1.0, max(0.0, t))
    hue = (2.0 / 3.0) * (1.0 - t)
    rgb = []
    for offset in (1.0, 2.0 / 3.0, 1.0 / 3.0):
        value = abs(((hue + offset) % 1.0) * 6.0 - 3.0) - 1.0
        rgb.append(min(1.0, max(0.0, value)))
    return (rgb[0], rgb[1], rgb[2], 1.0)


def _draw_legend(view, rect, scale, ui, steps=()):
    """A ``ui_kit`` component: the ramp as a bar, ``steps`` spaced under it."""
    from . import ui_kit, ui_tokens as tk
    x, y, w, h = rect
    bar = 10 * scale
    top = y + h - bar
    slices = 48
    for index in range(slices):
        view.shape(x + w * index / slices, top, w / slices + 0.5, bar, heat_color((index + 0.5) / slices), 0)
    ui_kit.outline((x, top, w, bar), 0, max(1.0, scale), tk.EDGE)
    size = tk.TYPE_SMALL * scale
    for index, label in enumerate(steps):
        width = ui_kit.measure(view, label, size)
        cx = x + w * index / max(1, len(steps) - 1)
        tx = min(max(x, cx - width / 2), x + w - width)
        ui_kit.text(view, label, tx, y + 2 * scale, size, tk.MUTED)


def _register_legend():
    from . import ui_kit
    ui_kit.register_component("uv_heat_legend", _draw_legend)


_register_legend()


class UVCheckerCard(kit.BaseCard):
    kind, priority, modal, cancel_action, outside_dismisses = KIND, 40, False, "close", False

    def __init__(self, space_pointer, uv_map_names=(), count_missing=None):
        """``space_pointer``: the 3D view's ``as_pointer()``; ``uv_map_names``: the Named choices (visible meshes');
        ``count_missing(settings)``: ``(count, first names)`` of the visible meshes without the chosen map
        (``uv_checker.meshes_without_map``), counted as the card opens and after each change made in it."""
        self.space_pointer = space_pointer
        self.names = list(uv_map_names)
        self.count_missing = count_missing
        self.missing = (0, [])
        self._recount()
        self.select_open = False
        self._version = 0

    def target_area(self, window):
        """The 3D view the card was opened for, while it exists; ``card_host`` falls back to the largest."""
        from . import live_windows
        return next((area for area in live_windows.areas(window)
                     if area.type == 'VIEW_3D' and area.spaces.active.as_pointer() == self.space_pointer), None)

    @property
    def settings(self):
        """The view's ``uv_checker``, found again each time: the card never holds screen data a layout change frees."""
        import bpy
        from . import live_windows
        for window in live_windows.windows(bpy.context.window_manager):
            area = self.target_area(window)
            if area is not None:
                return area.spaces.active.uv_checker
        return None

    def version(self):
        s = self.settings
        if s is None:
            return (self._version, None)
        return (self._version, self.select_open, self.missing[0], s.show, s.pattern, s.scale, s.uv_map, s.uv_map_name,
                s.use_selected_only, s.texture_size, s.target_density, s.show_flipped)

    # -- layout --------------------------------------------------------------------------------------------
    def layout(self, view, region_w, region_h, scale, ui):
        from .update_card import corner_plan
        return kit.fit(lambda size: corner_plan(self._build(view, size, region_w, ui), region_w, region_h),
                       region_w, region_h, scale)

    def _build(self, view, scale, region_w, ui):
        s = self.settings or _Defaults()
        b = kit.Builder(view, scale, min(WIDTH * scale, region_w - 40 * scale), ui, padding=22)
        top = b.y
        b.icon("uv_checker", b.pad, b.y, 40)
        text_x = b.pad + 54 * scale
        text_w = b.width - text_x - b.pad - 24 * scale
        b.paragraph(iface_(TITLE), 17, kit.TEXT, x=text_x, width=text_w, leading=1.3)
        b.paragraph(iface_(SUBTITLE), 12.5, kit.MUTED, x=text_x, width=text_w, leading=1.45)
        b.y = max(b.y, top + 40 * scale) + 10 * scale
        b.toggle("show", None, iface_(SHOW), bool(s.show))
        b.gap(10)
        enabled = bool(s.show)
        self._label(b, iface_(PATTERN))
        b.segmented("pattern", [(value, iface_(label)) for value, label in PATTERNS], s.pattern, width=b.inner,
                    enabled=enabled)
        b.gap(6)
        b.paragraph(iface_(WHY[s.pattern]), 12, kit.MUTED)
        if s.pattern in {"TEXEL_DENSITY", "STRETCH"}:
            steps = DENSITY_STEPS if s.pattern == "TEXEL_DENSITY" else STRETCH_STEPS
            b.gap(4)
            b.kit("uv_heat_legend", 30 * scale, {"steps": tuple(iface_(step) for step in steps)})
        b.gap(10)
        if s.pattern == "TEXEL_DENSITY":
            self._row(b, iface_(TEXTURE), "texture", [(str(size), _pixels(size)) for size in TEXTURES],
                      str(s.texture_size), enabled)
            self._row(b, iface_(TARGET), "target", [(str(value), str(value)) for value in TARGETS],
                      _preset(s.target_density), enabled)
        else:
            self._row(b, iface_(REPEAT), "scale", [(str(value), "%d×" % value) for value in SCALES], _preset(s.scale),
                      enabled)
        self._row(b, iface_(OBJECTS), "objects", [("ALL", iface_(EVERY)), ("SELECTED", iface_(SELECTED))],
                  "SELECTED" if s.use_selected_only else "ALL", enabled)
        b.select("uv_open", None, self.uv_options(), self.uv_choice(), open=self.select_open, label=iface_(UV_MAP),
                 option_action="uv_map", enabled=enabled)
        b.gap(6)
        count, names = self.missing
        if count and enabled:   # Where the hatching comes from, so a missing map is never a mystery.
            shown = ", ".join(names) + (", …" if count > len(names) else "")
            b.notice(WARN, iface_(MISSING, count=count, names=shown))
            b.gap(6)
        b.toggle("flipped", None, iface_(FLIPPED), bool(s.show_flipped), enabled=enabled)
        b.close_button("close")
        return b

    @staticmethod
    def _label(b, text):
        b.paragraph(text, 12.5, x=b.pad + 2 * b.s)
        b.y += 4 * b.s

    @staticmethod
    def _row(b, label, action, segments, selected, enabled):
        """A label at the start and its segmented control beside it, one row (taller when the label wraps)."""
        from . import ui_tokens as tk
        height, column = tk.SEGMENTED_HEIGHT * b.s, LABEL_WIDTH * b.s
        top = b.y
        b.y = top + max(0.0, (height - 12.5 * 1.45 * b.s) / 2)
        b.paragraph(label, 12.5, x=b.pad + 2 * b.s, width=column - 10 * b.s, leading=1.45)
        bottom = b.y
        b.y = top
        b.segmented(action, segments, selected, width=b.inner - column, enabled=enabled, x=b.pad + column)
        b.y = max(b.y, bottom) + 8 * b.s

    def uv_options(self):
        """Active, Render, then each visible mesh's UV map by name (a name is shown as it is, never translated)."""
        return [("ACTIVE", iface_(ACTIVE)), ("RENDER", iface_(RENDER))] + [("named:" + n, n) for n in self.names]

    def uv_choice(self):
        s = self.settings or _Defaults()
        if s.uv_map == "NAMED":
            return "named:" + s.uv_map_name
        return s.uv_map

    # -- input ---------------------------------------------------------------------------------------------
    def key_event(self, event, host):
        if self.select_open and event.type == 'ESC':
            self.select_open = False
            return True
        return False

    def act(self, action, value, host):
        s = self.settings
        if s is None and action not in {"close", "dismiss"}:   # Its view was closed: nothing left to set.
            host.close("lost")
            return
        try:
            if action == "show":
                s.show = not s.show
            elif action == "pattern":
                s.pattern = value
            elif action == "scale":
                s.scale = float(value)
            elif action == "texture":
                s.texture_size = int(value)
            elif action == "target":
                s.target_density = float(value)
            elif action == "objects":
                s.use_selected_only = value == "SELECTED"
            elif action == "uv_open":
                self.select_open = not self.select_open
            elif action == "uv_map":
                if value.startswith("named:"):
                    s.uv_map_name, s.uv_map = value[len("named:"):], "NAMED"
                else:
                    s.uv_map = value
                self.select_open = False
            elif action == "flipped":
                s.show_flipped = not s.show_flipped
            elif action in {"close", "dismiss"}:
                host.close("closed")
                return
        except ReferenceError:   # The view was closed (a layout change): nothing left to set.
            host.close("lost")
            return
        self._version += 1
        self._recount()
        _redraw_views()
        host.redraw()

    def opened(self, host):
        """While the card is open the count follows the scene: a depsgraph update only marks it stale (no walk in the
        handler), and a half-second timer counts again when it is (``host.after`` stops when the card closes)."""
        import bpy
        self._stale = False
        _open_cards.add(self)
        if _mark_stale not in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.append(_mark_stale)

        def poll():
            if self._stale:
                self._stale = False
                before = self.missing
                self._recount()
                if self.missing != before:
                    self._version += 1
                    host.redraw()
            return 0.5
        host.after(0.5, poll)

    def closed(self, how):
        import bpy
        _open_cards.discard(self)
        if not _open_cards and _mark_stale in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.remove(_mark_stale)

    def _recount(self):
        settings = self.settings if self.count_missing else None
        if settings is not None:
            try:
                self.missing = self.count_missing(settings)
            except ReferenceError:   # The scene went away (a file load): nothing to count.
                self.missing = (0, [])


_open_cards = set()


def _mark_stale(*_args):
    """``depsgraph_update_post`` while a UV checker card is open: only a flag, the count runs on the card's timer."""
    for card in _open_cards:
        card._stale = True


class _Defaults:
    """What the card shows for a view that no longer exists (it closes on the next click)."""

    show = use_selected_only = show_flipped = False
    pattern, uv_map, uv_map_name, scale, texture_size, target_density = "COLOR_GRID", "ACTIVE", "", 1.0, 1024, 512.0


def _pixels(size):
    return "%dK" % (size // 1024) if size >= 1024 else str(size)


def _preset(value):
    """A preset's segment value, or None when the setting is between presets (no segment is marked)."""
    rounded = round(float(value))
    return str(int(rounded)) if abs(float(value) - rounded) < 1e-4 else None


def _redraw_views():
    import bpy
    from . import chat_toggle, live_windows
    for _window, area in live_windows.window_areas(bpy.context.window_manager):
        if area.type == 'VIEW_3D':
            area.tag_redraw()
    chat_toggle.redraw_top_bars()


def close():
    """Withdraw the card (the add-on is being disabled)."""
    import bpy
    from . import card_host
    _open_cards.clear()
    if _mark_stale in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_mark_stale)
    if card_host.is_active(KIND):
        card_host.close("unregistered")
    card_host.withdraw(KIND)
