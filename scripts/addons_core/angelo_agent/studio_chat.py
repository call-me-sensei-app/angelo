# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Studio output previews inside the chat transcript, without opening Studio."""

from .legacy_names import current_tool_name


def has_preview(message):
    # Chats saved before the rename record pre-rename tool names.
    return bool(message.get("role") == "activity" and current_tool_name(message.get("tool", "")).startswith("angelo_studio_")
        and message.get("result", {}).get("views") and message.get("result", {}).get("id"))


def draw_preview(message, rect):
    """Generated media as a framed, rounded tile in the card language (``ui_tokens``), the picture contained."""
    from . import chat_view, studio, ui_tokens
    asset = studio.library().assets.get(message.get("result", {}).get("id"))
    if asset:
        x, y, w, h = rect
        chat_view.shape(x, y, w, h, ui_tokens.EDGE, ui_tokens.RADIUS_PANEL)
        chat_view.shape(x + 1, y + 1, w - 2, h - 2, ui_tokens.PANEL, ui_tokens.RADIUS_PANEL - 1)
        studio.thumbnail(asset, (x + 6, y + 6, w - 12, h - 12))
