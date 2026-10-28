# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The AI Providers page's Sessions panel row, beside Workers at once in its Parallel edit section: the row as data
(``providers_page_model`` adds it) and the action its buttons run (``providers_page_host.act``).

Where the Sessions panel (``session_rail``) stands in the 3D view is the person's choice (``parallel_setting``; the
maintainer, 2026-10-25: "We can let users decide"): Left, a column down the view (the default), or Top or Bottom, a
strip centred across it. A button each, in ``parallel_setting.RAIL_POSITIONS`` order, the current one primary, as
Workers at once draws its choices. A click keeps the choice with the preferences (``set_rail_position``) and redraws
every 3D view: the panel draws in the view's own region, so a view in another window would keep the old place until
something else redrew it. Minimizing is done on the panel itself, never here. Every string is translated here.
"""

from . import live_windows, parallel_setting
from .ui_text import iface_, n_

ACTION = "rail_position"
ACTIONS = (ACTION,)
# One per parallel_setting.RAIL_POSITIONS, in the "Angelo" context: Blender's own "Top" and "Bottom" are a shape's
# peak and base in several languages (Indonesian "Puncak", Vietnamese "Đỉnh/Đáy"), never a place in the view.
LABELS = {"LEFT": n_("Left", "Angelo"), "TOP": n_("Top", "Angelo"), "BOTTOM": n_("Bottom", "Angelo")}


def row():
    """The Sessions panel row: what it sets, and a choice per place with the current one chosen."""
    from .providers_page_model import sentence
    current = parallel_setting.rail_position()
    return {"kind": "connection", "glyph": "list", "name": iface_("Sessions panel", "Angelo"),
            "lines": [sentence(iface_("Where the Sessions panel stands in the 3D view"))],
            "choices": [(iface_(LABELS[place], "Angelo"), ACTION, place, place == current)
                        for place in parallel_setting.RAIL_POSITIONS]}


def act(action, value):
    """A click on a place: kept as the person's (a value that is not a place changes nothing), and every 3D view
    drawn again so the panel stands there at once. The page redraws itself after any action."""
    if action == ACTION:
        parallel_setting.set_rail_position(value)
        redraw_views()


def redraw_views():
    """Tag every 3D view of every live window for a redraw (``live_windows``: a window between screens is skipped)."""
    import bpy
    for _window, area in live_windows.window_areas(bpy.context.window_manager):
        if area.type == 'VIEW_3D':
            area.tag_redraw()
