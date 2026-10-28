# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One message's box in the Changes popup (``ui.ANGELO_OT_show_changes``), drawn on its own so a report that cannot be
read never stops the popup (``chat_guard``). Moved out of ui.py unchanged otherwise (its size pin)."""

from .chat_changes import file_report, scene_change_report
from .ui_text import iface_


def draw_message(box, message, title):
    """``title``: the chat's own name for the tool that reported (``chat_view.tool_title``)."""
    box.label(text=title(message))
    if message["result"].get("partial_changes_possible"):
        box.label(text="Operation failed; partial changes may remain.", icon='ERROR')
        box.label(text="Inspect or restore the request snapshot before retrying.")
    files = file_report(message["result"])
    if files is not None:
        for path in files["written"]:
            box.label(text=iface_("Written: {path}", path=path), translate=False)
        for path in files["deleted"]:
            box.label(text=iface_("Deleted: {path}", path=path), translate=False)
        if message["result"].get("journaled") is True:
            box.label(text="Restoring a snapshot reverts this file; Undo doesn't.")
        else:
            box.label(text="Check the workspace files and request snapshot journal.")
        return
    changes = scene_change_report(message["result"])
    if changes is None:
        return
    for record in changes["added"][:40]:
        box.label(text="+ " + record["name"] + " · " + record["type"], translate=False)  # Scene data.
    for record in changes["removed"][:40]:
        box.label(text="− " + record["name"], translate=False)
    for record in changes["modified"][:40]:
        box.label(text="~ " + record["name"] + " · " + ", ".join(record["fields"]), translate=False)
    if changes["truncated"]:
        box.label(text="Large scene: details truncated.")
