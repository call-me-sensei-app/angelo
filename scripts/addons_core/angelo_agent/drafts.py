# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Draft access without activating or reconstructing an editor buffer."""


def read_chat_draft(space, buffers):
    if hasattr(space, "coding_agent_input"):
        line = space.coding_agent_input
        if line is None:
            return
        return line.body, line.current_character
    if space.language == "angelo":
        return (space.history[-1].body, space.history[-1].current_character) if space.history else None
    saved = buffers.get(space.as_pointer(), {}).get("angelo")
    return saved["history"][-1] if saved and saved["history"] else None


def write_chat_draft(space, draft, buffers):
    if hasattr(space, "coding_agent_input"):
        line = space.coding_agent_input
        if line is None:
            return
    else:
        line = space.history[-1] if space.language == "angelo" and space.history else None
    if line is not None:
        line.body, line.current_character = draft
        return
    saved = buffers.get(space.as_pointer(), {}).get("angelo")
    if saved and saved["history"]:
        saved["history"][-1] = draft


def restore_unsent(controller, *, active, spaces, saved, read, write):
    """Put a message that never reached the agent back where the person types; True when it was put back.

    ``controller.unsent_draft`` is set by ``pump_events`` and consumed here once.
    A draft the person has already started is never overwritten: their
    message stays in the transcript and the notice asks them to send it again.
    """
    text, controller.unsent_draft = controller.unsent_draft, None
    if not text:
        return False
    draft = (text, len(text.encode("utf-8")))  # The caret after the text (a byte offset, as set_draft).
    if not active:
        if saved.get(controller.state["id"], ("", 0))[0]:
            return False
        saved[controller.state["id"]] = draft
        return True
    restored = False
    for space in spaces:
        current = read(space)
        if current is not None and not current[0]:
            write(space, draft)
            restored = True
    return restored
