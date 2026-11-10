# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Draft access without activating or reconstructing an editor buffer.

A draft is ``(body, caret)`` with the caret a character index into ``body``, as ``ConsoleLine.current_character``
reads and writes it (RNA converts to and from the native UTF-8 byte offset). Only the ``console.history_append``
operator takes its ``current_character`` as a byte offset: ``console_byte_offset`` converts for it.
"""


def console_byte_offset(body, caret):
    """The UTF-8 byte offset ``console.history_append(current_character=...)`` expects for a character caret."""
    return len(body[:max(0, caret)].encode("utf-8"))


def chat_draft_line(space):
    """The chat's draft line in a Console ``space``, or None: its agent input in the native build
    (``SpaceConsole.coding_agent_input``, also while the space shows Python); without it (a stock binary), the last
    line while the space shows the chat."""
    if hasattr(space, "coding_agent_input"):
        return space.coding_agent_input
    return space.history[-1] if space.language == "angelo" and space.history else None


def read_chat_draft(space, buffers):
    line = chat_draft_line(space)
    if line is not None:
        return line.body, line.current_character
    if hasattr(space, "coding_agent_input") or space.language == "angelo":
        return None
    saved = buffers.get(space.as_pointer(), {}).get("angelo")
    return saved["history"][-1] if saved and saved["history"] else None


def write_chat_draft(space, draft, buffers):
    line = chat_draft_line(space)
    if line is None and hasattr(space, "coding_agent_input"):
        return
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
    draft = (text, len(text))  # The caret after the text (a character index, as ConsoleLine.current_character).
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


def failed_request(controller, message=None):
    """``(text, references)`` of the person's own request whose turn failed, while that failure is the chat's latest
    row (``message``, when given, must be that row) and it was not put back yet; else None. A turn the person stopped,
    a message that never left (``restore_unsent`` puts that back by itself), an error after the turn ended (a refused
    send keeps its draft) or Angelo's own request has nothing to put back; nor has one sent again (``chat_resend``)."""
    rows = controller.state.get("messages") or []
    recovery = getattr(controller, "_resend_recovery", None)
    if recovery is not None:
        found = recovery.request(controller, message)
        if found is not None:
            return found
    outcome = getattr(controller, "turn_outcome", None)
    if (not rows or rows[-1].get("role") != "error" or (message is not None and rows[-1] is not message)
            or any(rows[-1] is vars(controller).get(name) for name in ("_put_back_row", "_resent_row"))
            or not outcome or outcome[0] != "failed"
            or outcome[1] or outcome[2] or outcome[3] != len(rows)):
        return None
    return request_before_error(rows, len(rows) - 1)


def request_before_error(rows, index):
    """Rebuild one error's request owner from retained history, never a mutable current-controller index."""
    from .request_rows import IMAGE_ONLY_TEXT, own_words, starts_request
    if not 0 <= index < len(rows) or rows[index].get("role") != "error":
        return None
    # Rebuild the relationship from the history BEFORE this error. The controller's current
    # index may already name a later request; a steer belongs to its original request instead.
    # Stop at the first request start, even when it is a host/worker/external request.
    request = next((row for row in reversed(rows[:index]) if starts_request(row)), None)
    if not own_words(request):
        return None
    text = str(request.get("text") or "")
    return ("" if text == IMAGE_ONLY_TEXT else text), [dict(r) for r in request.get("references") or () if isinstance(r, dict)]


def _appended(current, text):
    """``text`` after a draft the person has typed, on its own line; never in place of it."""
    body = (current.rstrip("\n") + "\n" + text) if current and text else (current or text)
    return body[:32000], min(len(body), 32000)


def put_back(controller, *, active, spaces, saved, read, write):
    """The person's "Put message back" on a failed turn's notice (``chat_notices``): its words go after whatever is in
    the composer now (never over it) and its attachments back beside any there now, up to the per-message limit. Never
    sends anything. True when something was put back."""
    found = failed_request(controller)
    if found is None:
        return False
    text, references = found
    controller._put_back_row = controller.state["messages"][-1]   # Its pill goes: once is what was asked.
    from .references import MAX_IMAGES
    held = {reference.get("id") for reference in controller.attachments}
    for reference in references:
        if len(controller.attachments) < MAX_IMAGES and reference.get("id") not in held:
            controller.attachments.append(reference)
            held.add(reference.get("id"))
    if hasattr(controller, "version"):
        controller.version += 1
    if not text:
        return True
    if not active:
        saved[controller.state["id"]] = _appended(saved.get(controller.state["id"], ("", 0))[0], text)
        return True
    restored = False
    for space in spaces:
        current = read(space)
        if current is not None:
            write(space, _appended(current[0], text))
            restored = True
    return restored or bool(references)
