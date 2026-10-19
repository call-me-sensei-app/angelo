# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A connection row's ⋯ menu on the AI Providers page: anchored to its button, with in-place confirmation.

The same items and operators as ``ANGELO_OT_connection_menu`` (Cancel connection / Sign in again, Load saved
key, Refresh status, Remove connection…), drawn as a ``card_kit`` card under the button (above it when there
is no room). Remove asks in the menu itself, with the store's ``remove_description``, and only then runs
``angelo.connection_remove``. ``items`` is the menu as data; ``layout`` is pure.
"""

from . import card_kit as kit
from .card_kit import MUTED, TEXT
from .ui_text import iface_


def items(ui, record):
    """``[(label, action, value)]`` for ``record``, as the stock menu offered them."""
    from . import saved_keys_ui
    rows = []
    identity = record["mode"] if record["reuse_native"] else record["id"]
    if identity in ui._authentication.running:
        rows.append((iface_("Cancel connection"), "menu_cancel_signin", identity))
    elif record["method"] == "SUBSCRIPTION":
        rows.append((iface_("Sign in again"), "menu_reconnect", record["id"]))
    if record["method"] == "API_KEY":
        rows.append((iface_("Replace key…"), "edit", record["id"]))
    if saved_keys_ui.can_load(record, bool(ui._memory_keys.get(record["id"]))):
        rows.append((iface_(saved_keys_ui.store_text().load_label), "menu_load_key", record["id"]))
    rows.append((iface_("Refresh status"), "menu_check", record["id"]))
    rows.append((iface_("Remove connection…"), "menu_remove", record["id"]))
    return rows


def layout(view, anchor, rows, confirm, region, scale, ui):
    """The menu's plan under ``anchor`` (the ⋯ button's rect, region pixels). ``confirm``: (name, description)."""
    s = scale
    width = min(280 * s, region[0] - 16 * s)
    b = kit.Builder(view, s, width, ui, padding=10)
    if confirm:
        name, description = confirm
        b.gap(4)
        b.paragraph(iface_("Remove {name}?", name=name), 14, TEXT)
        b.gap(2)
        b.paragraph(description, 12, MUTED)
        b.gap(10)
        b.buttons([("menu_close", None, iface_("Cancel"), "secondary", True),
                   ("menu_remove_confirm", None, iface_("Remove"), "destructive", True)], size=12.5, height=30)
    else:
        for label, action, value in rows:
            b.buttons([(action, value, label, "ghost", True)], align="left", size=13, height=30)
            b.gap(2)
    height = b.height()
    x0 = max(8 * s, min(anchor[0] + anchor[2] - width, region[0] - width - 8 * s))
    below = anchor[1] - 6 * s
    y_top = below if below - height > 8 * s else min(region[1] - 8 * s, anchor[1] + anchor[3] + 6 * s + height)
    return b.place(x0, y_top, False, region)
