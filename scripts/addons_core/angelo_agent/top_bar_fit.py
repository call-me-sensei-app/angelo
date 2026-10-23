# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Top-bar buttons as wide as their label in the current language: Chat, Studio, Flows and the mode menu.

Blender sizes a header button from its own estimate, which clipped Angelo's custom-icon buttons (1.0.5: Director's
*Flows* drew as "Flo…" even in a wide window), and a translated label is often longer than the English one. Each
button is drawn in a row whose width is measured from the label it shows now (``chat_toggle.button_units``: icon,
label, padding), so no language clips it. What was fitted is recorded per window (``widths``) for the GUI check.
"""

widths = {}   # window pointer -> {name: (label, units, unit)}, as last drawn.
rooms = {}    # window pointer -> {"level", "left", "right", "width"} in pixels, as last decided (GUI suites).
SELECTOR_UNITS = 8   # A scene or view layer selector at the right (template_ID), in UI units.
# What gives way first when the top bar lacks room, least important first. Studio and Flows are permanent: they only
# ever lose their label (level 3), never their button, whatever the width, scale or language (maintainer, 1.0.5).
FULL, CHAT_SHORT, NO_VIEW_LAYER, STUDIO_ICONS, CHAT_ICON = range(5)


def compact(context, director, labels):
    """The top bar's compaction level (FULL .. CHAT_ICON): the lowest whose measured widths fit the window.

    ``labels`` holds the full labels: chat (with its shortcut), chat_short, studio and flows. The left side is
    Blender's menus and, in Manual, the workspace tabs; the right side Angelo's buttons, the mode menu and the scene
    (and in Manual the view layer) selectors, all measured at the widget font. At UI scale 2 the full labels pushed
    the right-hand region over the workspace tabs (1.0.5 review: "UV Editinc")."""
    import blf
    from bpy.app.translations import pgettext_iface
    if context.window is None:
        return FULL
    system = context.preferences.system
    unit = round(18.0 * system.ui_scale) + 2 * system.pixel_size
    blf.size(0, context.preferences.ui_styles[0].widget.points * system.ui_scale)

    def text(value):
        return blf.dimensions(0, value)[0]

    def button(label):
        return label_units(context, (label,))[0] * unit
    left = 1.5 * unit + sum(text(pgettext_iface(name)) + unit for name in ("File", "Edit", "Render", "Window", "Help"))
    if not director:
        left += unit + sum(text(workspace.name) + 1.6 * unit for workspace in context.blend_data.workspaces)
    widths = {}
    for level in range(CHAT_ICON + 1):
        chat = labels["chat"] if level < CHAT_SHORT else labels["chat_short"] if level < CHAT_ICON else ""
        studio_flows = ("", "") if level >= STUDIO_ICONS else (labels["studio"], labels["flows"])
        selectors = 1 if director or level >= NO_VIEW_LAYER else 2
        widths[level] = (button(chat) + sum(button(label) for label in studio_flows)
                         + max(6 * unit, button(labels.get("mode", ""))) + selectors * SELECTOR_UNITS * unit)
    level = next((level for level, right in widths.items() if left + right <= context.window.width), CHAT_ICON)
    rooms[context.window.as_pointer()] = {"level": level, "left": round(left), "right": round(widths[level]),
                                          "width": context.window.width}
    return level


def label_units(context, labels):
    """``(units, unit)``: the width in UI units the widest of ``labels`` needs beside an icon, and the UI unit in
    pixels, at the current UI scale and widget font."""
    import blf
    from .chat_toggle import button_units
    system = context.preferences.system
    unit = round(18.0 * system.ui_scale) + 2 * system.pixel_size   # UI_UNIT_X (wm_window.cc).
    blf.size(0, context.preferences.ui_styles[0].widget.points * system.ui_scale)
    return max(button_units(blf.dimensions(0, label)[0], unit) for label in labels), unit


def fitted_row(layout, context, name, label, also=(), minimum=0.0):
    """A row as wide as ``label`` needs (and every label in ``also`` that the same button can show, so it never
    changes width), at least ``minimum`` units. Draw the button into the returned row."""
    units, unit = label_units(context, (label,) + tuple(also))
    units = max(units, minimum)
    row = layout.row()
    row.ui_units_x = units
    if context.window is not None:
        widths.setdefault(context.window.as_pointer(), {})[name] = (label, units, unit)
    return row


def menu_units(context, label):
    """A header menu's width in UI units for ``label`` beside an icon: Blender's estimate (text + 1.1 units: the
    menu's 0.75 text and 0.35 icon padding in a header) plus a quarter unit, so the label is never clipped and stays
    beside its icon (a menu draws its label centred in the button)."""
    import blf
    system = context.preferences.system
    unit = round(18.0 * system.ui_scale) + 2 * system.pixel_size
    blf.size(0, context.preferences.ui_styles[0].widget.points * system.ui_scale)
    return blf.dimensions(0, label)[0] / unit + 1.35


def widget_text(context, labels):
    """``(widths, unit)``: each of ``labels``' width in pixels at the widget font and UI scale (what a header button
    or menu of Blender's own draws its label at), and the UI unit in pixels."""
    import blf
    system = context.preferences.system
    unit = round(18.0 * system.ui_scale) + 2 * system.pixel_size
    blf.size(0, context.preferences.ui_styles[0].widget.points * system.ui_scale)
    return [blf.dimensions(0, label)[0] for label in labels], unit
