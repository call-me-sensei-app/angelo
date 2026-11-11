# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The one notice after a start that did not finish: which add-ons this launch left off, and how to turn one back on.

``angelo_safe_start`` (scripts/modules) leaves third-party add-ons off before any loads when the last start never
completed, and calls ``show`` once this start has completed (doc/angelo/app_identity.rst, "A start that did not
finish"). The notice is its own card kind, so an update's notice queued beside it does not replace it
(``card_host.request`` keeps one card per kind); it stays until closed. Names are read as Blender lists them
(``bl_info`` or an extension's manifest, read as text), never by loading the add-on.

Main thread only.
"""

from .ui_text import iface_, n_

TITLE = n_("Angelo started without some add-ons")
BODY = n_("The last start did not finish, so Angelo opened with only the add-ons that come with it. Left off: "
          "{names}. To turn one back on, enable it in Preferences > Add-ons. If Angelo then cannot start, it "
          "opens without it again.")

shown = None   # (title, body) of the notice this launch showed, for its fixtures.


def display_names(modules):
    """``modules`` as the person knows them: each add-on's own name, else its module name."""
    import addon_utils
    try:
        known = {module.__name__: module for module in addon_utils.modules(refresh=False)}
    except Exception:   # A name is a convenience: the module name stands.
        known = {}
    names = []
    for module in modules:
        found = known.get(module)
        name = addon_utils.module_bl_info(found).get("name") if found is not None else None
        names.append(name or module)
    return names


def text(modules):
    """``(title, body)`` for the add-ons left off."""
    return iface_(TITLE), iface_(BODY, names=", ".join(display_names(modules)))


def show(modules):
    global shown
    if not modules:
        return
    from . import card_host
    from .update_card import NoticeCard

    class SafeStartCard(NoticeCard):
        kind = "safe_start"

    title, body = text(modules)
    shown = (title, body)
    card_host.request(SafeStartCard("alert", title, body, lifetime=0))
    card_host.pump(lambda: True)
