# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Text typed or pasted into the chat's composer is never cut without a word.

A message has a length limit, which ``chat_composer.set_draft`` keeps by cutting what is past it. Every insert into
the draft (a paste, a typed character, Shift+Return) is checked by ``edit`` before the pure composer truncates it.
The composer records the accepted edit in its own Undo history; ``insert`` remains the line adapter. A paste that does not fit is attached whole to the message as a
text file when it can be (the chat idle, a free attachment place: ``references.import_images``, as a dropped file
is), and the draft stays as it was. Otherwise only what fits goes in at the caret, the draft already written is kept
whole, and a notice in the chat says part of the text was left out (once while the draft stays full, so typing on
a full draft does not repeat it). No ``bpy``: host-testable.
"""

from pathlib import Path

from . import private_roots
from .ui_text import n_, notice

ATTACHED = n_("Your text was too long for one message, so it was attached as a text file instead.")
LEFT_OUT = n_("Part of your text didn't fit in one message and was left out. Attach long text as a file instead.")
FILE_NAME = "Pasted text.txt"   # i18n: the attachment's name, as the model and the chip see it (data)


def edit(controller, state, draft, text, kind=None):
    """The proposed text edit, before the pure composer truncates it; attachments leave the draft untouched."""
    from .composer_edit import Edit, LIMIT, insert as insert_text
    start, end = draft.selection or (draft.caret, draft.caret)
    over = len(draft.value) - (end - start) + len(text) - LIMIT
    if over <= 0:
        state.pop("draft_full", None)
        return insert_text(draft, text, kind)
    if len(text) > 1 and attach_text(controller, text):
        controller.emit("message", {"role": "system", **notice(ATTACHED)})
        return Edit(draft)
    if not state.get("draft_full"):
        state["draft_full"] = True
        controller.emit("message", {"role": "system", **notice(LEFT_OUT)})
    return insert_text(draft, text[:max(0, len(text) - over)], kind)


def insert(controller, state, line, value, caret, text):
    """Legacy line adapter; the current composer records ``edit`` through its own Undo owner."""
    from .chat_composer import set_draft
    from .composer_edit import Draft
    result = edit(controller, state, Draft(value, caret), text).draft
    set_draft(line, result.value, result.caret)


def _text_bytes(text):
    """Encode only text that can fit the existing attachment byte limit, with bounded temporary chunks."""
    from .references import MAX_FILE_BYTES
    if len(text) > MAX_FILE_BYTES:   # UTF-8 cannot use fewer bytes than code points.
        return None
    result = bytearray()
    for start in range(0, len(text), 64 * 1024):
        part = text[start:start + 64 * 1024].encode("utf-8")
        if len(result) + len(part) > MAX_FILE_BYTES:
            return None
        result.extend(part)
    return result


def attach_text(controller, text):
    """Attach ``text`` to the message being written as a UTF-8 text file. False when it can't be: a turn is running
    (as for a dropped file, ``chat_drop``), the message holds as many files as it can, or the text is too large."""
    if getattr(controller, "busy", False):
        return False
    try:
        data = _text_bytes(text)
        if data is None:
            return False
        with private_roots.TemporaryDirectory(prefix="angelo-assets-") as folder:
            path = Path(folder) / FILE_NAME
            path.write_bytes(data)
            controller.attachments.extend(controller.references.import_images([str(path)], controller.attachments))
    except (OSError, ValueError):   # UnicodeError is a ValueError.
        return False
    controller.version += 1
    return True
