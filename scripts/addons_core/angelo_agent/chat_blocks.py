# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat transcript and the cards after it as blocks to draw, one per row or card, each built on its own
(``chat_guard``): one that raises is a plain row (an approval a plain card that still answers), every other is built as
designed. Moved out of ``chat_view._draw_chat`` (its size pin)."""

from . import chat_guard


def build(controller, ui, expanded, state, width, size, line_height, bubble_max_width):
    """``(role, message, rows, height, identity, bubble_width)`` for each shown message, in order."""
    from . import reply_overlay
    blocks = []
    messages = controller.state["messages"]
    for message in (chat_guard.call("The shown rows", reply_overlay.shown, controller, messages) or messages)[-160:]:
        identity = (controller.state["id"], id(message))
        try:
            identity = message.get("call_id") or identity
            block = _block(controller, ui, message, identity, expanded, state, width, size, line_height,
                           bubble_max_width)
        except Exception as error:   # noqa: BLE001 - one row never stops the chat (chat_guard).
            block = chat_guard.fallback_block(message, identity, error, width, size, line_height)
        if block is not None:
            blocks.append(block)
    return chat_guard.checked(blocks, width, size, line_height)   # Before grouping, which unpacks them (N7).


def _block(controller, ui, message, identity, expanded, state, width, size, line_height, bubble_max_width):
    from . import chat_notices, chat_replies, chat_rows, chat_view, console
    role = "system" if message.get("request_kind") == "angelo_event" else message["role"]   # Angelo's: a notice.
    if role == "tool" and message["text"].startswith("Approval required"):
        return None
    if role == "user":
        rows = chat_view.wrap(console.display_text(message), max(40, bubble_max_width - 32), size, chat_view._font)
        bubble_width = min(bubble_max_width, max(chat_view.measure(value, size) for value, _ in rows) + 32)
        return ("user", message, rows, len(rows) * line_height + 24 + 40, identity, bubble_width)
    if role in {"activity", "thinking"}:  # Progress rows and work groups: chat_rows.
        return chat_rows.activity_block(message, role, identity, expanded, width, size, line_height, state)
    if role in {"system", "tool"} and (block := chat_rows.notice_block(message, identity, expanded, width, size,
                                                                       line_height)):
        return block
    if role in {"system", "tool"}:
        # Native notices and legacy tools share Codex's quiet activity rows.
        rows = chat_view.wrap(console.display_text(message), max(40, width - 60), size, chat_view._font)
        return ("notice", message, rows, len(rows) * line_height + 24, identity, 0)
    if role == "assistant" and not message.get("text"):
        return None   # Nothing to draw (a streamed reply held back so far, reply_overlay).
    if role == "error":  # A notice card, never a reply (chat_notices).
        return chat_notices.block(message, identity, message is controller.state["messages"][-1], ui, controller,
                                  width, size, line_height, expanded)
    # Markdown laid out once per reply text and width (chat_replies).
    return chat_replies.block(message, identity, console.display_text(message), width, size, line_height)


def cards(controller, area, workspace_cards, expanded, state, width, size, line_height, flow_wrap, preview_available):
    """The blocks after the transcript: a pending approval (else the live progress row while it works) and the
    workspace's preview and job cards, each built on its own (REVIEW-CHAT-GUARD M2)."""
    from . import chat_card_draw, chat_rows, flow_chat
    blocks = []
    pending = next((c for c in tuple(controller.pending.values()) if c.approval), None)

    def approval():
        if flow_chat.is_run_review(pending):
            details = ("approval", pending.id) in expanded
            _, card_height = flow_chat.approval_layout(pending, 16, 0, width - 32, flow_wrap, line_height, details)
            return ("flow_approval", {"call": pending, "details": details}, (), card_height + 12, pending.id, 0)
        return chat_card_draw.approval_block(controller, pending, area, width, size, line_height, expanded, state)
    if pending is not None:
        blocks.append(chat_guard.card("An approval card", approval,   # i18n: a log line, never drawn
                                      lambda error: chat_guard.approval_block(pending, width, size, line_height,
                                                                              error)))
    elif controller.busy and not any(card.get("state") != "PREVIEW" for card in workspace_cards):
        blocks.append(chat_guard.card("The progress row",   # i18n: a log line, never drawn
                                      lambda: chat_rows.live_block(controller, width, size)))
    for card in workspace_cards:
        key = ("native_preview", card.get("preview_id")) if card.get("state") == "PREVIEW" else ("native_job", id(card))
        build = ((lambda card=card, key=key: chat_card_draw.preview_block(card, key, width, size, line_height, expanded,
                                                                         preview_available(card)))
                 if card.get("state") == "PREVIEW" else
                 (lambda card=card: chat_card_draw.job_block(card, width, size, line_height)))
        blocks.append(chat_guard.card("A workspace card", build, lambda error, card=card, key=key:   # i18n: a log line
                                      chat_guard.fallback_block(card, key, error, width, size, line_height)))
    return [block for block in blocks if block is not None]
