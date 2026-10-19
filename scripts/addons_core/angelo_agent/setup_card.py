# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The first-launch setup card: language, look and Blender settings, then Continue (``card_host``, modal).

It replaces Blender's Quick Setup form with the same effects (``splash_setup`` sequences it): the language and
look apply as they are chosen, so the card itself switches language; "Use my Blender settings" brings the earlier
settings ``settings_import`` finds (an earlier Angelo version's, else stock Blender's own folder) when Continue is
pressed, and its note names that app and version; the mouse-select and Spacebar choices sit behind a disclosure
under it. The import keeps the language and look chosen here, and the mouse and Spacebar choices when they were
changed (``keymap_choices``). Continue saves the preferences (the import saves its own) and the welcome screen
follows. Escape closes an open language list, then skips setup without saving, as clicking away from Blender's
Quick Setup did.

Layout is pure (host-testable with a fake view); ``act`` changes Blender preferences through ``Effects``.
"""

from . import card_kit as kit, firstlaunch_controls as fc, settings_import as si, ui_tokens as tokens
from .ui_text import iface_, n_

WIDTH = 540
TITLE = n_("Set up Angelo")
SUBTITLE = n_("Choose a language and a look. You can change both later in Preferences.")
LANGUAGE, LOOK = n_("Language"), n_("Look", "Angelo")  # Blender's "Look" is a verb in some catalogues.
DARK, LIGHT = n_("Angelo Dark"), n_("Angelo Light")
IMPORT = n_("Use my Blender settings")
IMPORT_ANGELO = n_("Use my earlier Angelo settings")
IMPORT_NOTE = n_("Brings over your preferences, shortcuts and add-ons from {app} {version}")
IMPORT_NONE = n_("No earlier Blender settings were found on this computer")
IMPORT_SHARED = n_("Angelo already uses the settings folder set for Blender")
OPTIONS = n_("Mouse and Spacebar")
SELECT, SELECT_LEFT, SELECT_RIGHT = n_("Select with"), n_("Left click"), n_("Right click")
SPACE, SPACE_PLAY, SPACE_TOOLS, SPACE_SEARCH = n_("Spacebar"), n_("Play"), n_("Tools"), n_("Search")
CONTINUE = n_("Continue")

# Angelo's 21 interface languages, each in its own name (never translated). Codes are Blender's locale prefixes.
LANGUAGES = (
    ("en_US", "English"), ("ar", "العربية"), ("de", "Deutsch"), ("es", "Español"), ("fr", "Français"),  # i18n: each language in its own name
    ("hi", "हिन्दी"), ("id", "Bahasa Indonesia"), ("it", "Italiano"), ("ja", "日本語"), ("ko", "한국어"),  # i18n: each language in its own name
    ("nl", "Nederlands"), ("pl", "Polski"), ("pt_PT", "Português"), ("pt_BR", "Português do Brasil"),  # i18n: each language in its own name
    ("ru", "Русский"), ("sv", "Svenska"), ("th", "ไทย"), ("tr", "Türkçe"), ("vi", "Tiếng Việt"),  # i18n: each language in its own name
    ("zh_HANS", "简体中文"), ("zh_HANT", "繁體中文"),  # i18n: each language in its own name
)
LOOKS = (("dark", DARK, fc.SWATCH_DARK), ("light", LIGHT, fc.SWATCH_LIGHT))
SELECTS = (("LEFT", SELECT_LEFT), ("RIGHT", SELECT_RIGHT))
SPACES = (("PLAY", SPACE_PLAY), ("TOOL", SPACE_TOOLS), ("SEARCH", SPACE_SEARCH))


# The two looks are Angelo's bundled theme presets (scripts/presets/interface_theme), named as the card names them.
LOOK_PRESETS = {"dark": "Angelo_Dark", "light": "Angelo_Light"}


def look_preset_path(bpy, look):
    """The bundled preset file for ``look``: the app's own scripts first, then any preset folder (a development
    run with an overridden scripts folder)."""
    import os
    name = LOOK_PRESETS[look] + ".xml"
    bundled = bpy.utils.system_resource('SCRIPTS', path=os.path.join("presets", "interface_theme", name))
    if bundled and os.path.isfile(bundled):
        return bundled
    return next((os.path.join(folder, name) for folder in bpy.utils.preset_paths("interface_theme")
                 if os.path.isfile(os.path.join(folder, name))), None)


def resolve(code, identifiers):
    """Blender's language identifier for one of ``LANGUAGES`` (``ar`` -> ``ar_EG``), or None when not built in."""
    if code in identifiers:
        return code
    return next((i for i in identifiers if i.split("_")[0] == code.split("_")[0] and "_" not in code), None)


def native_name(identifier):
    return next((name for code, name in LANGUAGES if identifier == code or identifier.split("_")[0] == code), identifier)


class Effects:
    """What the card changes in Blender; a fixture or host test swaps it for a recorder."""

    def __init__(self):
        import bpy
        self.bpy = bpy

    def languages(self):
        prop = self.bpy.context.preferences.view.bl_rna.properties["language"]
        ids = [item.identifier for item in prop.enum_items]
        return [(identifier, name) for identifier, name in ((resolve(c, ids), n) for c, n in LANGUAGES) if identifier]

    def language(self):
        return self.bpy.context.preferences.view.language

    def set_language(self, identifier):
        self.bpy.context.preferences.view.language = identifier

    def look(self):
        """The theme preset last applied: its file, saved with the preferences, else the presets menu's label."""
        import os
        filepath = self.bpy.context.preferences.themes[0].filepath
        name = os.path.splitext(os.path.basename(filepath))[0] if filepath else \
            self.bpy.types.USERPREF_MT_interface_theme_presets.bl_label.replace(" ", "_")
        return "light" if name == LOOK_PRESETS["light"] else "dark"

    def set_look(self, look):
        """Apply Angelo's own bundled preset, never a same-named file among the person's (imported) presets."""
        path = look_preset_path(self.bpy, look)
        if path:
            self.bpy.ops.script.execute_preset(filepath=path, menu_idname="USERPREF_MT_interface_theme_presets")
            from . import font_watch
            font_watch.check()   # A theme reloads Blender's fonts: rebind the card's now, not at the next poll.

    def keymap(self):
        return self.bpy.context.window_manager.keyconfigs.active.preferences

    def get(self, name, default):
        prefs = self.keymap()
        return getattr(prefs, name, default) if prefs is not None else default

    def set(self, name, value):
        prefs = self.keymap()
        if prefs is not None and hasattr(prefs, name):
            setattr(prefs, name, value)

    def import_source(self):
        """``(settings_import.Source or None, reason)``: the earlier settings "Use my Blender settings" brings."""
        from . import settings_import_apply
        return settings_import_apply.find_source()


class SetupCard(kit.BaseCard):
    kind, modal, priority = "setup", True, 90
    cancel_action, default_action, outside_dismisses = "escape", "continue", False
    retry_when_lost = False

    def __init__(self, effects, finished):
        self.effects, self.finished = effects, finished
        self.menu_open = self.options_open = self.use_blender = False
        self.source, self.source_reason = effects.import_source()
        self.keymap_choices = {}   # Mouse and Spacebar choices changed here: kept over an import's.
        self.outcome = None
        self.settled = 0   # Bumped after a theme change, once Blender has reloaded its interface fonts.

    def version(self):
        e = self.effects
        return (self.settled, self.menu_open, self.options_open, self.use_blender, e.language(), e.look(),
                e.get("select_mouse", "LEFT"), e.get("spacebar_action", "PLAY"))

    def layout(self, view, region_w, region_h, scale, ui):
        def plan(size):
            builder = self._build(view, size, region_w, ui)
            top = min((region_h + builder.height()) / 2, region_h - 10 * builder.s)
            return builder.place((region_w - builder.width) / 2, top, True, (region_w, region_h))
        return kit.fit(plan, region_w, region_h, scale, minimum=0.55)

    def _build(self, view, s, region_w, ui):
        width = min(WIDTH * s, region_w - 32 * s)
        b = fc.Builder(view, s, width, ui, padding=28)
        banner_h = width * fc.BANNER_ASPECT
        b.custom(0, 0, width, banner_h, fc.banner, fc.banner_ready())
        b.y = banner_h * 0.30
        b.paragraph(iface_(TITLE), 26, kit.TEXT, width=width * 0.62, leading=1.25)
        b.paragraph(iface_(SUBTITLE), 13.5, tokens.SUBTITLE, width=width * 0.58, leading=1.45)
        b.y = max(b.y + 10 * s, banner_h + 24 * s)
        stacked = b.inner < 420 * s
        label_w = 0 if stacked else 112 * s

        def row(label, height):
            """The label (beside or above the control); returns the control's (x, top, width)."""
            size = tokens.TYPE_BODY
            if stacked:
                b.paragraph(label, size, kit.MUTED, leading=1.4)
                b.gap(4)
                return b.pad, b.y, b.inner
            b.items.append(("text", label, b.pad, b.y + (height - size * s * 1.4) / 2, size * s, size * s * 1.4,
                            kit.MUTED, False))
            return b.pad + label_w, b.y, b.inner - label_w

        h = tokens.FIELD_HEIGHT * s
        x, top, w = row(iface_(LANGUAGE), h)
        b.custom(x, top, w, h, fc.dropdown_field, native_name(self.effects.language()), self.menu_open,
                 hit=("language_menu", None))
        menu_anchor = top + h + 6 * s
        b.y = top + h + 14 * s
        h = tokens.SEGMENTED_HEIGHT * s
        x, top, w = row(iface_(LOOK, "Angelo"), h)
        self._segments(b, x, top, w, "look", LOOKS, self.effects.look())
        b.y = top + h + 18 * s
        self._import_row(b)
        b.gap(8)
        b.disclosure("options", iface_(OPTIONS), self.options_open)
        if self.options_open:
            b.gap(8)
            x, top, w = row(iface_(SELECT), h)
            self._segments(b, x, top, w, "select", tuple((k, v, None) for k, v in SELECTS),
                           self.effects.get("select_mouse", "LEFT"))
            b.y = top + h + 10 * s
            x, top, w = row(iface_(SPACE), h)
            self._segments(b, x, top, w, "space", tuple((k, v, None) for k, v in SPACES),
                           self.effects.get("spacebar_action", "PLAY"))
            b.y = top + h
        b.gap(24)
        b.buttons([("continue", None, iface_(CONTINUE), "primary", True)], size=15, height=42)
        if self.menu_open:
            self._menu(b, menu_anchor)
        return b

    @staticmethod
    def _segments(b, x, top, w, action, choices, current):
        """The kit's segmented control (``b.segmented``, as on the Providers sheet), in the control column."""
        segments = tuple((value, iface_(label)) + ((swatch,) if swatch else ()) for value, label, swatch in choices)
        b.y = top
        b.segmented(action, segments, current, width=w, x=x)

    def _import_row(self, b):
        from . import ui_kit_controls as controls
        s, source = b.s, self.source
        available = source is not None
        toggle_w, toggle_h = controls.toggle_size(s)
        text_w = b.inner - 2 * 16 * s - toggle_w - 14 * s
        title = iface_(IMPORT_ANGELO) if available and source.app == si.ANGELO else iface_(IMPORT)
        if available:
            note = iface_(IMPORT_NOTE, app=source.app, version=source.version_label)
        else:
            note = iface_(IMPORT_SHARED) if self.source_reason == si.SHARED else iface_(IMPORT_NONE)
        title_lines = b.lines(title, text_w, tokens.TYPE_BODY * s)
        note_lines = b.lines(note, text_w, tokens.TYPE_SMALL * s)
        height = (len(title_lines) * tokens.TYPE_BODY * 1.45 + len(note_lines) * tokens.TYPE_SMALL * 1.45) * s + 26 * s
        top = b.y
        b.custom(b.pad, top, b.inner, height, fc.row_panel, self.use_blender,
                 hit=("import", None) if available else None)
        b.y = top + 13 * s
        b.paragraph(title, tokens.TYPE_BODY, kit.TEXT if available else kit.MUTED, x=b.pad + 16 * s,
                    width=text_w, leading=1.45)
        b.paragraph(note, tokens.TYPE_SMALL, kit.MUTED, x=b.pad + 16 * s, width=text_w, leading=1.45)
        b.y = top + (height - toggle_h) / 2   # The kit's switch, not a hit of its own: the whole row is.
        b.kit("toggle", toggle_h, {"row": False, "on": self.use_blender, "action": "import", "value": None,
                                   "enabled": available}, (), x=b.pad + b.inner - 16 * s - toggle_w, width=toggle_w)
        b.y = top + height

    def _menu(self, b, anchor):
        s = b.s
        languages = self.effects.languages()
        columns = 3 if b.inner >= 440 * s else 2
        item_h, pad = 30 * s, 8 * s
        rows = -(-len(languages) // columns)
        panel_w = b.inner
        cell = (panel_w - 2 * pad) / columns
        b.custom(0, 0, b.width, b.height(), lambda *a: None, hit=("menu_close", None), focus=False, overlay=True)
        b.custom(b.pad, anchor, panel_w, rows * item_h + 2 * pad, fc.menu_panel)
        current = self.effects.language()
        for index, (identifier, name) in enumerate(languages):
            column, row_ = index // rows, index % rows   # Column-major: alphabetical order reads down each column.
            b.custom(b.pad + pad + column * cell, anchor + pad + row_ * item_h, cell - 4 * s, item_h, fc.menu_item,
                     name, identifier == current, hit=("language", identifier), overlay=True)

    def act(self, action, value, host):
        e = self.effects
        if action == "language_menu":
            self.menu_open = not self.menu_open
        elif action == "menu_close":
            self.menu_open = False
        elif action == "language":
            self.menu_open = False
            if value != e.language():
                e.set_language(value)
        elif action == "look":
            if value != e.look():
                e.set_look(value)
                # A theme reloads the interface fonts: text measured meanwhile is lost, so lay out again after.
                host.after(0.3, lambda: self._settle(host))
        elif action == "import":
            self.use_blender = not self.use_blender and self.source is not None
        elif action == "options":
            self.options_open = not self.options_open
        elif action == "select":
            e.set("select_mouse", value)
            self.keymap_choices["select_mouse"] = value
        elif action == "space":
            e.set("spacebar_action", value)
            self.keymap_choices["spacebar_action"] = value
        elif action == "escape":
            if self.menu_open:
                self.menu_open = False
            else:
                self.outcome = "skip"
                host.close("skip")
        elif action == "continue":
            self.outcome = "import" if self.use_blender else "save"
            host.close("done")
        host.redraw()

    def _settle(self, host):
        self.settled += 1
        host.redraw()

    def closed(self, how):
        self.finished(self.outcome if how in {"done", "skip"} else how)
