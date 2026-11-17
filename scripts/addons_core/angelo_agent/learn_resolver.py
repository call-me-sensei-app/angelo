# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the running app calls a command and where it is: the lookup behind a how-to card (learn mode).

``learn_cards.steps`` asks a ``Lookup`` for every label, menu title and key it shows. Each answer is read from the
app as it is now: an operator's name and its settings from its RNA, a menu's title from its class, a key from the
person's own key configuration. Nothing is remembered between apps, languages or keymaps, so a card follows a
rebound key, the Industry Compatible keymap and the interface language by itself. **Anything the app does not have
is answered None, "" or ()**; the card then leaves that part out.

Keys: a key is one the person can press in the editor and mode the step belongs to. ``KEYMAPS`` names the keymaps
that apply there, in the order Blender consults them. An item counts when it is active, is a keyboard item, and
every setting it fixes is the action's value or, for one the action does not name, the operator's default (Ctrl B
is the edge bevel; Ctrl Shift B, which sets ``affect`` to vertices, is not; H hides the selection, and Shift H,
which turns ``unselected`` on, is another action). A setting the key leaves alone takes its default, so where the
action's value is not the default the key must set it, unless the card leaves that setting to the person (``free``:
the choice a key's menu asks for, Alt P's Clear Parent menu; a setting the card lists for Adjust Last Operation).
Text is Blender's own (``KeyMapItem.to_string``), as ``shortcuts.text`` prints
Angelo's window shortcuts.

Main thread only (it reads ``bpy``). ``card(message)`` keeps the last ``MAX_CARDS`` cards for ``FRESH_S`` seconds
each, keyed by the row, the interface language and the active key configuration, so a transcript that redraws an
open card every frame looks nothing up again and a rebinding still shows within ``FRESH_S``. ``forget()`` drops
them (add-on unregister).
"""

import math
import time
from collections import OrderedDict

from . import learn_cards
from .learn_routes import EDIT, NODE_EDITOR, OBJECT, POSE, PROPERTIES, UV_EDITOR, VIEW_3D
from .ui_text import iface_, n_

_EVERYWHERE = ("Window", "Screen", "User Interface")   # i18n: Blender's keymap names
_VIEW = ("3D View", "3D View Generic")   # i18n: Blender's keymap names
# (editor, mode) -> the keymaps a key press reaches there, most specific first.
KEYMAPS = {
    (VIEW_3D, OBJECT): ("Object Mode", "Object Non-modal") + _VIEW,   # i18n: Blender's keymap names
    (VIEW_3D, EDIT): ("Mesh", "Curve", "Armature", "Lattice", "Font", "Curves",
                      "Grease Pencil Edit Mode", "Object Non-modal") + _VIEW,   # i18n: Blender's keymap names
    (VIEW_3D, POSE): ("Pose", "Object Non-modal") + _VIEW,   # i18n: Blender's keymap names
    (UV_EDITOR, EDIT): ("UV Editor", "Image", "Image Generic"),   # i18n: Blender's keymap names
    (PROPERTIES, OBJECT): ("Property Editor",),   # i18n: Blender's keymap names
    (PROPERTIES, EDIT): ("Property Editor",),   # i18n: Blender's keymap names
    (NODE_EDITOR, OBJECT): ("Node Editor", "Node Generic"),   # i18n: Blender's keymap names
}
_MENU_OPERATORS = ("wm.call_menu", "wm.call_menu_pie", "wm.call_panel")
# An operator whose choice is listed only where it runs (a dynamic enum): the type that names the same items.
_CHOICES = {("object.modifier_add", "type"): ("Modifier", "type"), ("object.constraint_add", "type"): ("Constraint", "type"),
            ("object.light_add", "type"): ("Light", "type"), ("object.empty_add", "type"): ("Object", "empty_display_type")}
ON, OFF = n_("On", "Angelo"), n_("Off", "Angelo")
MAX_CARDS = 32
FRESH_S = 2.0
_cards = OrderedDict()   # (id of the row, locale, key configuration) -> (the row, when built, its steps).


def _translate(text, context=None):
    import bpy
    return bpy.app.translations.pgettext_iface(text, context) if text else ""


def _same(bound, value):
    """Whether a key's bound property value is the action's: numbers within float32's precision, arrays by item."""
    if isinstance(bound, (set, frozenset)):   # An enum flag: the same members in any order.
        try:
            return set(bound) == set(value if isinstance(value, (list, tuple, set, frozenset)) else (value,))
        except TypeError:
            return False
    if isinstance(value, (list, tuple)):
        try:
            return len(bound) == len(value) and all(_same(a, b) for a, b in zip(bound, value))
        except TypeError:
            return False
    if isinstance(value, float) and isinstance(bound, (int, float)) and not isinstance(bound, bool):
        return abs(bound - value) <= 1e-6 * max(1.0, abs(value))
    return bound == value


def _default(prop):
    """An RNA property's default as a value ``_same`` compares."""
    if prop.type == 'ENUM':
        return prop.default_flag if prop.is_enum_flag else prop.default
    if getattr(prop, "is_array", False):
        return tuple(prop.default_array)
    return prop.default


def _item(prop, value):
    """An enum property's item for ``value``; an enum listed only in context keeps its full list in
    ``enum_items_static``."""
    found = prop.enum_items.get(value)
    return found if found is not None else prop.enum_items_static.get(value)


class Lookup:
    """The running app's answers for one card (``learn_cards``); built per card, on the main thread."""

    def __init__(self, context=None):
        import bpy
        self._bpy = bpy
        self._context = context or bpy.context
        self._operators = {}
        self._keymaps = None

    def _operator(self, idname):
        if idname not in self._operators:
            from .api_lookup import _operator
            self._operators[idname] = _operator(self._bpy, idname) if idname.count(".") == 1 else None
        return self._operators[idname]

    def operator_label(self, idname):
        rna = self._operator(idname)
        return _translate(rna.name, "Operator") if rna is not None and rna.name else None

    def enum_label(self, idname, name, value):
        rna = self._operator(idname)
        prop = rna.properties.get(name) if rna is not None else None
        item = _item(prop, value) if prop is not None and prop.type == 'ENUM' else None
        if item is None and (idname, name) in _CHOICES:
            owner, field = _CHOICES[idname, name]
            prop = getattr(self._bpy.types, owner).bl_rna.properties.get(field)
            item = _item(prop, value) if prop is not None else None
        return _translate(item.name, prop.translation_context) if item is not None else None

    def setting(self, idname, name, value):
        rna = self._operator(idname)
        prop = rna.properties.get(name) if rna is not None else None
        if prop is None or not prop.name:
            return None
        return _translate(prop.name, prop.translation_context), self._value(prop, value)

    def _value(self, prop, value):
        if isinstance(value, bool):
            return iface_(ON if value else OFF)
        if isinstance(value, (list, tuple)):
            return ", ".join(self._value(prop, item) for item in value)
        if prop.type == 'ENUM' and isinstance(value, str):
            item = _item(prop, value)
            return _translate(item.name, prop.translation_context) if item is not None else value
        if isinstance(value, (int, float)) and prop.type == 'FLOAT':
            if prop.unit == 'ROTATION':
                return "%.4g°" % math.degrees(value)
            if prop.unit == 'LENGTH':
                units = self._context.scene.unit_settings
                return self._bpy.utils.units.to_string(units.system, 'LENGTH', value * units.scale_length, precision=4)
            return "%.4g" % value
        return str(value)

    def menu_label(self, menu):
        cls = getattr(self._bpy.types, menu, None)
        label = getattr(cls, "bl_label", "") if cls is not None else ""
        return _translate(label, getattr(cls, "bl_translation_context", None)) if label else None

    def item_label(self, text):
        """A menu's own text for a command, as Blender translates an operator button's text."""
        return _translate(text, "Operator")

    def _enum(self, owner, field, value):
        item = _item(getattr(self._bpy.types, owner).bl_rna.properties[field], value)
        return _translate(item.name) if item is not None else ""

    def editor_label(self, editor):
        return self._enum("Space", "type", editor)

    def tab_label(self, tab):
        return self._enum("SpaceProperties", "context", tab)

    def mode_label(self, mode):
        return self._enum("Object", "mode", mode)

    def _items(self, editor, mode):
        """The active keyboard items a key press reaches in ``editor`` and ``mode``, most specific keymap first."""
        if self._keymaps is None:
            keyconfig = self._context.window_manager.keyconfigs.user
            self._keymaps = {keymap.name: keymap for keymap in keyconfig.keymaps} if keyconfig else {}
        for name in KEYMAPS.get((editor, mode), ()) + _EVERYWHERE:
            keymap = self._keymaps.get(name)
            for item in (keymap.keymap_items if keymap is not None else ()):
                if item.active and item.map_type == 'KEYBOARD':
                    yield item

    @staticmethod
    def _is(item, properties, fixed=()):
        """Whether ``item`` runs the action ``properties`` name. Every setting the key itself fixes must be the
        action's value, or, for one the action does not name, the operator's own default: a key that also turns
        something on (Shift H hides the *unselected*; Shift 1 *extends* the selection) is another action's key.
        Each name in ``fixed`` (the action's value is not the default) must be one the key sets."""
        given = item.properties
        if given is None:
            return not fixed
        if not all(given.is_property_set(name) for name in fixed if name in given.bl_rna.properties):
            return False
        wanted = dict(properties)
        for prop in given.bl_rna.properties:
            name = prop.identifier
            if name == "rna_type" or prop.type in {'POINTER', 'COLLECTION'} or not given.is_property_set(name):
                continue
            if not _same(getattr(given, name), wanted[name] if name in wanted else _default(prop)):
                return False
        return True

    def _fixed(self, idname, properties, free):
        """The action's settings a key must set itself: named, not left to the person, and not the default."""
        rna = self._operator(idname)
        found = []
        for name, value in properties:
            prop = rna.properties.get(name) if rna is not None and name not in free else None
            if prop is not None and name != "rna_type" and not _same(_default(prop), value):
                found.append(name)
        return tuple(found)

    def keys(self, idname, properties, editor, mode, limit=2, free=()):
        """The keys bound to the command now, most specific keymap first; at most ``limit`` (a card shows the first
        and one alternative). ``free`` names the settings the card leaves to the person after the key."""
        found = []
        fixed = self._fixed(idname, properties, free)
        for item in self._items(editor, mode):
            if item.idname == idname and self._is(item, properties, fixed):
                text = item.to_string(compact=True)
                if text and text not in found:
                    found.append(text)
        return tuple(found[:limit])

    def menu_key(self, menu, editor, mode):
        for item in self._items(editor, mode):
            if item.idname in _MENU_OPERATORS and getattr(item.properties, "name", None) == menu:
                return item.to_string(compact=True)
        return ""


def card(message, context=None):
    """The how-to card of a step's row (``learn_cards.steps``), looked up in the running app; () when it has none."""
    if not learn_cards.has_card(message):
        return ()
    import bpy
    context = context or bpy.context
    keyconfig = context.window_manager.keyconfigs.active
    key = (id(message), bpy.app.translations.locale, keyconfig.name if keyconfig else "")
    kept, now = _cards.get(key), time.monotonic()
    if kept is not None and kept[0] is message and now - kept[1] < FRESH_S:
        _cards.move_to_end(key)
        return kept[2]
    built = learn_cards.steps(message, Lookup(context))
    _cards[key] = (message, now, built)
    _cards.move_to_end(key)
    while len(_cards) > MAX_CARDS:
        _cards.popitem(last=False)
    return built


def forget():
    """Drop every kept card (unregister, a file load: the rows they were built from are gone)."""
    _cards.clear()
