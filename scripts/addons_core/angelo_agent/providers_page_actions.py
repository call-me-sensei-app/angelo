# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The AI Providers page's operator tables and its controls as C's consent regions (split from
``providers_page_host``: 500-line rule).

Every control the page, its open ⋯ menu or a connection sheet draws is a consent region (``grant_guard``,
doc/angelo/grant_operators.rst "Authority in C"); a click on one runs in the grant scope of its press's ticket. An
open menu or sheet registers only its own controls: the page beneath takes no press while one is open.
"""

from . import fab_ui, grant_guard, providers_page_endpoints
from .ui_text import is_rtl

SIMPLE_OPERATORS = {"team_sign_in": ("team_sign_in", 'INVOKE_DEFAULT', None), "team_sign_out": ("team_sign_out", 'INVOKE_DEFAULT', None),
                    "team_cancel": ("team_cancel_sign_in", 'EXEC_DEFAULT', None), "team_refresh": ("team_refresh", 'EXEC_DEFAULT', None),
                    "team_copy_link": ("team_copy_sign_in_link", 'EXEC_DEFAULT', None), **providers_page_endpoints.OPERATORS,
                    "team_console": ("team_open_console", 'EXEC_DEFAULT', "page"), "enhancer": ("enhancer_settings", 'INVOKE_DEFAULT', None), **fab_ui.PREF_OPERATORS,
                    "forget_agent_changes": ("forget_agent_changes", 'INVOKE_DEFAULT', None)}   # Its folder browser.
MENU_OPERATORS = {"menu_cancel_signin": ("cancel_signin", "mode"), "menu_reconnect": ("connection_signin", "connection_id"),
                  "menu_load_key": ("connection_load_key", "connection_id"), "menu_check": ("connection_check", "connection_id")}


def register(window, region, plan, menu, sheet, ui=None):
    """The controls drawn now in ``region``: the sheet's if one is open, else the menu's if open, else the page's; and
    the one a key acts on (``ui``'s focus; in a sheet, else the field being typed in: ``sheet_focus``)."""
    if window is None:
        return
    focus = getattr(ui, "focus", None)
    if sheet is not None:
        from . import providers_page_host
        focus = sheet_focus(providers_page_host._sheet, ui)
    surface, prefix, hits = ("providers", "page.", getattr(plan, "hits", None) or [])
    if sheet is not None:
        prefix, hits = "sheet.", getattr(sheet, "hits", None) or []
    elif menu is not None:
        hits = getattr(menu, "hits", None) or []
    grant_guard.canvas_regions(window, region, surface, hits, 1.0, is_rtl(), prefix=prefix, focus=focus)


def sheet_focus(sheet, ui):
    """``(action, value)`` of the sheet's control a key acts on, as C's focus: its keyboard focus, else the field the
    person is typing in (Return there runs the sheet's primary action, in that field's ticket); ``("", None)``."""
    if ui.focus:
        return ui.focus
    fields = sheet._fields() if sheet is not None and hasattr(sheet, "_fields") else {}
    editing = next((name for name, field in fields.items() if getattr(field, "editing", False)), None)
    return ("field", editing) if editing else ("", None)


OWN = ("enable", "script_limits")   # The page's actions that run no operator of Angelo's.


def act_own(action):
    """Enable the add-on, or open the published limits of agent scripts (Always allow scripts' link): only that
    address, never one a page action carries (script_access.LIMITS_URL)."""
    import bpy
    if action == "enable":
        bpy.ops.preferences.addon_enable(module="angelo_agent")
    elif action == "script_limits":
        from . import script_access
        bpy.ops.wm.url_open(url=script_access.LIMITS_URL)
