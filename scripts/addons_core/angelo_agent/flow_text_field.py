# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A flow node's text field that never cuts its text: one line when it fits, else every line of it wrapped.

Blender draws a text property on one line and cuts the middle of anything longer ("Turn the image into …lours and
materials.", 1.0.5 review), whatever the node or the language. ``draw`` measures the value at the widget font against
the node's width: when it fits, the field is drawn as before; when it does not, the whole text is shown wrapped (by
words, or by characters for a language written without spaces) with an Edit button that opens it in a wide field.
What was drawn is recorded (``drawn``) so a GUI suite can compare it with the value.
"""

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from .ui_text import iface_

# Room a wrapped line loses inside the node, in node units: the node's own padding (NODE_DY, 20), the box around the
# lines (style boxspace, 5 a side) and a label's text margins (UI_TEXT_MARGIN_X 8, UI_TEXT_CLIP_MARGIN + 1 = 6).
# 34 left lines 1 to 10 units too long, clipped with "…" (the template prompt's at the default width: 219 > 216).
MARGINS = 44
BUTTON_MARGIN = 12   # A centred button's text loses only its clip margins (about 6 a side).
drawn = {}     # (tree, node, property) -> the lines last drawn, or [value] when the field was drawn.


def _measure(text, points):
    import blf
    blf.size(0, points)
    return blf.dimensions(0, text)[0]


def wrap(text, width, measure):
    """``text`` in lines no wider than ``width``: by words, a word wider than a line by characters."""
    lines, current = [], ""
    for word in text.replace("\n", " ").split(" "):
        candidate = (current + " " + word) if current else word
        if measure(candidate) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = ""
        for character in word:   # A word (or a whole sentence without spaces) longer than a line.
            if current and measure(current + character) > width:
                lines.append(current)
                current = ""
            current += character
    if current:
        lines.append(current)
    return lines or [""]


def _points():
    return bpy.context.preferences.ui_styles[0].widget.points


def lines(text, node_width, icon=True):
    """``text`` wrapped to the labels of a node ``node_width`` wide, measured: the node's padding (NODE_DY 20), a
    label's text margins (UI_TEXT_MARGIN_X 8, UI_TEXT_CLIP_MARGIN + 1 = 6) and its icon (20)."""
    points = _points()
    return wrap(text, max(40, node_width - 20 - 14 - (20 if icon else 0)), lambda part: _measure(part, points))


def columns(node, texts, most, extra=0):
    """How many buttons labelled ``texts`` (translated) fit side by side across ``node``, at most ``most``: a German
    or Russian label is often wider than an even share of the node ("Остановка для проверки" in 108 units)."""
    points, count = _points(), max(1, most)
    widest = max((_measure(text, points) + extra for text in texts), default=0)
    while count > 1 and widest > (node.width - 20) / count - BUTTON_MARGIN:
        count -= 1
    return count


def row(layout, node, texts, extra=0):
    """A row for buttons labelled ``texts`` when each fits its share of the node, else a column that stacks them."""
    fits = columns(node, texts, len(texts), extra) == len(texts)
    return layout.row(align=True) if fits else layout.column(align=True)


def draw(layout, node, prop, placeholder=""):
    value = getattr(node, prop)
    points = bpy.context.preferences.ui_styles[0].widget.points
    width = max(40, node.width - MARGINS)
    key = (node.id_data.name, node.name, prop)
    if not value or _measure(value, points) <= width:
        layout.prop(node, prop, text="", placeholder=placeholder, translate=False)
        drawn[key] = [value]
        return
    lines = wrap(value, width, lambda text: _measure(text, points))
    column = layout.column(align=True)
    box = column.box().column(align=True)
    for line in lines:
        box.label(text=line, translate=False)
    op = column.operator("angelo.flow_edit_text", text=iface_("Edit"), icon='GREASEPENCIL', translate=False)
    op.tree, op.node, op.prop = key
    drawn[key] = lines


class ANGELO_OT_flow_edit_text(Operator):
    """Edit this text in a wide field"""
    bl_idname = "angelo.flow_edit_text"
    bl_label = "Edit"
    bl_options = {'INTERNAL', 'UNDO'}
    tree: StringProperty(options={'SKIP_SAVE'})
    node: StringProperty(options={'SKIP_SAVE'})
    prop: StringProperty(options={'SKIP_SAVE'})
    original: StringProperty(options={'SKIP_SAVE', 'HIDDEN'})

    def _target(self):
        tree = bpy.data.node_groups.get(self.tree)
        return tree.nodes.get(self.node) if tree else None

    def invoke(self, context, event):
        node = self._target()
        if node is None:
            return {'CANCELLED'}
        # The field edits the node as it is typed: Cancel puts back the text the dialog opened with (1.0.5 review).
        self.original = getattr(node, self.prop)
        return context.window_manager.invoke_props_dialog(self, width=640)

    def draw(self, context):
        node = self._target()
        if node is not None:
            self.layout.activate_init = True   # Typing starts at once.
            self.layout.prop(node, self.prop, text="", translate=False)

    def execute(self, context):
        return {'FINISHED'}

    def cancel(self, context):
        node = self._target()
        if node is not None and getattr(node, self.prop) != self.original:
            setattr(node, self.prop, self.original)


CLASSES = (ANGELO_OT_flow_edit_text,)
