# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the approval card says for ``angelo_app_feature`` (``app_feature_spec``): the + menu row's own words, so the
card's title says what the click does ("Turn on the live link"), and what does not change."""

from .ui_text import iface_


def message(name, args, usage_label=""):
    """Title, body and warning of the card; only ENGINE_LINK so far (``app_feature_spec.ACTIONS``)."""
    from . import engine_link_ui
    if args["action"] == "ADD_TO_PROJECT":
        return {"title": iface_(engine_link_ui.INSTALL_ROW[args["engine"]]),
                "body": iface_("You choose the project's folder. Angelo copies the plugin there and turns the engine "
                               "live link on."),
                "warning": iface_("Only the folder you choose changes. Nothing in your scene changes.")}
    if args["action"] == "ON":
        return {"title": iface_(engine_link_ui.TURN_ON), "body": iface_(engine_link_ui.TURN_ON_WHY),
                "warning": iface_("Listens on this computer only; turn it off from the chat bar's + menu at any time.")}
    return {"title": iface_(engine_link_ui.TURN_OFF), "body": iface_(engine_link_ui.TURN_OFF_WHY),
            "warning": iface_("Nothing in your scene changes.")}
