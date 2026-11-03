# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the approval card says for ``angelo_engine_link`` (``engine_link_tool_spec``): what the click does to the
linked asset, in the engine's own name, and what does not change."""

import os

from .ui_text import iface_, n_

PUSH_TITLE = n_("Send the live update to {engine}")
PUSH_BODY = n_("{engine} shows {asset} as it is in Angelo now, in place of its instances.")
PUSH_WARNING = n_("Nothing in your scene or in the project's files changes.")
SAVE_TITLE = n_("Save {asset} for {engine}")
SAVE_BODY = n_("Saves the open file, as File > Save does. Angelo then writes {asset} into the {engine} project, and "
               "{engine} re-imports it.")
SAVE_BODY_BLEND = n_("Saves the open file, as File > Save does, and {engine} re-imports it.")
SAVE_WARNING = n_("Replaces {asset} in the project. Undo in Angelo doesn't take the written file back.")
CLOSE_TITLE = n_("Stop editing {asset} in {engine}")
CLOSE_BODY = n_("{engine} shows the asset as last saved. The file stays open here.")
CLOSE_WARNING = n_("Nothing is saved or written.")
NOT_LINKED = n_("No engine asset is linked")
OPEN_TITLE = n_("Ask to open {asset} here")   # The agent's OPEN: its own card is the request card (engine_link_agent.ask).


def message(name, args, usage_label=""):
    """Title, body and warning of the card for the linked asset (none linked: the tool then refuses)."""
    from . import engine_link, engine_link_assets as assets
    if args["action"] == "OPEN":   # Never shown as a card of its own: the request card asks the person.
        return {"title": iface_(OPEN_TITLE, asset=args["asset"].rstrip("/").rsplit("/", 1)[-1]), "body": "",
                "warning": ""}
    asset = engine_link.session()
    if asset is None:
        return {"title": iface_(NOT_LINKED), "body": "", "warning": ""}
    names = {"engine": assets.display_name(asset.engine), "asset": os.path.basename(asset.path)}
    action = args["action"]
    if action == "PUSH":
        return {"title": iface_(PUSH_TITLE, **names), "body": iface_(PUSH_BODY, **names),
                "warning": iface_(PUSH_WARNING)}
    if action == "SAVE":
        body = SAVE_BODY_BLEND if asset.kind == ".blend" else SAVE_BODY
        return {"title": iface_(SAVE_TITLE, **names), "body": iface_(body, **names),
                "warning": iface_(SAVE_WARNING, **names)}
    return {"title": iface_(CLOSE_TITLE, **names), "body": iface_(CLOSE_BODY, **names),
            "warning": iface_(CLOSE_WARNING)}
