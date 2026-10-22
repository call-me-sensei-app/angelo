# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Claude plan's reply while it streams: drawn in the chat, never stored before the plan arrives.

``claude_stream.PlanReply`` streams a plan's ``reply`` as the model writes the
plan. Until the provider's final row for that plan (``message_complete``) the
text is not the assistant's: the round can still fail, be refused by the
runtime's schema check, be superseded or be stopped, and then the plan never
ran. So the text lives here, on the controller, under the request generation
it belongs to, and only the chat draws it (``shown``). The transcript, its
saves, handoffs and rebases never contain it; the final row writes the plan's
reply, or leaves the row as it was when the plan has none, and ``finished`` or
a newer request drops whatever is left. A stopped request's later events never
arrive (the event pump drops them by generation), which is why nothing here
waits for one.

The text is redacted whole each time it is drawn, and a tail that could begin
the connection's key is held back until the next fragment shows what it is,
so no part of a key split across fragments is ever drawn.

Main thread only: the event pump writes and the chat draws.
"""

ATTRIBUTE = "_streamed_replies"


def update(controller, kind, data):
    """Apply one chat event; True when it was a streamed reply fragment, which then needs nothing else.

    ``finished`` drops every streamed reply: one whose plan never arrived is not the assistant's.
    """
    if kind == "finished":
        controller.__dict__.pop(ATTRIBUTE, None)
        return False
    replies = controller.__dict__.setdefault(ATTRIBUTE, {})
    key = (controller._request_generation, data.get("item"))
    if kind == "message_complete":
        replies.pop(key, None)  # The plan arrived: its row says what it replied, or keeps what it said.
        return False
    if kind != "delta" or not (data.get("plan_reply") or data.get("replace") or key in replies):
        return False
    replies[key] = ("" if data.get("replace") else replies.get(key, "")) + str(data.get("text", ""))
    return True


def drawable(controller, text):
    """``text`` as the chat may draw it: redacted, without a tail that could begin the connection's key."""
    text, secret = controller.redact(text), getattr(controller, "api_key", "") or ""
    for length in range(min(len(secret) - 1, len(text)), 2, -1):  # Two characters reveal nothing.
        if text.endswith(secret[:length]):
            return text[:-length]  # Perhaps the start of the key: wait for the next fragment.
    return text


def shown(controller, messages):
    """``messages`` as the chat draws them: a streaming reply in its item's row, or after the last row."""
    replies = {item: text for (generation, item), text in (controller.__dict__.get(ATTRIBUTE) or {}).items()
               if generation == controller._request_generation}
    if not replies:
        return messages
    rows = {index: item for item, index in controller._items.items() if item in replies}
    drawn = [dict(message, text=drawable(controller, replies[rows[index]])) if index in rows else message
             for index, message in enumerate(messages)]
    return drawn + [{"role": "assistant", "text": drawable(controller, text)}
                    for item, text in replies.items() if item not in controller._items]
