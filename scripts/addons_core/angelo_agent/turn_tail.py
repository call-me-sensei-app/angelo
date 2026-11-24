# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A chat's last row as its turn left it: past the rows the host adds as the turn ends.

A turn's end can add rows after its last words (``turn_end.end_turn_calls``): the account of a step that never ran
(``turn_end: True``) and a card for the person, such as a share a script was refused (``notice: True``,
``person_only``). They come before the turn's outcome is recorded (``Controller.turn_outcome``), so a failed turn's
error row is no longer the chat's last row. What reads "the latest failure" (Put back ``drafts.failed_request``,
Resend ``chat_resend``, the error card's own pills ``chat_blocks``/``chat_notices``, the rail's failed mark
``session_rail_draw``) asks here instead of ``rows[-1]`` (review of 04c159c S5). A row added after the outcome still
makes the outcome's row count stale, so a later row hides the failure's buttons as before.

No ``bpy``: host-testable.
"""


def added_at_end(message):
    """Whether ``message`` is a row the host adds as a turn ends, never the turn's own outcome (an error row)."""
    return (isinstance(message, dict) and message.get("role") != "error"
            and (message.get("turn_end") is True or message.get("notice") is True or message.get("person_only") is True))


def last(rows):
    """The index of ``rows``' last row before what the host added at the end, or None."""
    index = len(rows) - 1
    while index >= 0 and added_at_end(rows[index]):
        index -= 1
    return index if index >= 0 else None


def last_row(rows):
    """``rows``' last row before what the host added at the end, or None."""
    index = last(rows or ())
    return rows[index] if index is not None else None
