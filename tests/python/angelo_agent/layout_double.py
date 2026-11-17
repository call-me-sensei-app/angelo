# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A stand-in for Blender's ``UILayout`` that suites draw a page through without a window (the AI Providers page).

It has ``UILayout``'s own layout calls with their real keyword arguments (``column(align=True)``, ``split(factor=)``),
each returning a layout as Blender's do, and its settable properties (``PROPERTIES``, read from rna_ui.cc by
test_native_layout_double). A suite subclasses it and records what it needs (``label``, ``prop``, ``operator``) in the
attributes it names in ``RECORDS``. A call ``UILayout`` has that this does not is an AttributeError, and so is setting
anything else (a misspelled flag, ``use_property_splt``, would pass silently in a plain object and fail in Blender):
add it here, with Blender's signature, for every suite at once; never a suite's own copy that drifts from Blender
(the cut's GUI round on 3fc1f94df29: a section using ``column(align=True)`` broke four suites' private copies).
"""

# UILayout's settable properties (its read-only ``direction`` is no draw's to set).
PROPERTIES = frozenset({"active", "active_default", "activate_init", "operator_context", "enabled", "alert",
                        "alignment", "scale_x", "scale_y", "ui_units_x", "ui_units_y", "emboss",
                        "use_property_split", "use_property_decorate"})


class UILayoutDouble:
    RECORDS = ()   # A suite's own recording attributes (``self.labels``): the only other names it may set.

    def __setattr__(self, name, value):
        if name not in PROPERTIES and name not in type(self).RECORDS:
            raise AttributeError("UILayout has no settable property %r" % name)
        object.__setattr__(self, name, value)

    # Display flags a draw sets on a layout (``UILayout``'s properties).
    active = enabled = alert = True
    active_default = activate_init = False
    use_property_split = use_property_decorate = False
    scale_x = scale_y = 1.0
    ui_units_x = ui_units_y = 0.0
    alignment = 'EXPAND'
    emboss = 'NORMAL'
    operator_context = 'INVOKE_DEFAULT'

    def row(self, align=False, heading="", heading_ctxt="", translate=True):
        return self

    def column(self, align=False, heading="", heading_ctxt="", translate=True):
        return self

    def box(self):
        return self

    def split(self, factor=0.0, align=False):
        return self

    def grid_flow(self, row_major=False, columns=0, even_columns=False, even_rows=False, align=False):
        return self

    def column_flow(self, columns=0, align=False):
        return self

    def separator(self, factor=1.0, type='AUTO'):   # noqa: A002 - Blender's own keyword.
        return None

    def separator_spacer(self):
        return None

    def label(self, text="", text_ctxt="", translate=True, icon='NONE', icon_value=0):
        return None

    def prop(self, data, property, **kwargs):   # noqa: A002 - Blender's own keyword.
        return None

    def operator(self, operator, **kwargs):
        from types import SimpleNamespace
        return SimpleNamespace()

    def menu(self, menu, **kwargs):
        return None

    def popover(self, panel, **kwargs):
        return None
