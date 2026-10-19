# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Files dropped on the chat in builds without the native multi-file handler.

Installed-binary compatibility: stock Console inserts a quoted dropped path.
The chat observes only a real native drop event (``EVT_DROP``) and, a moment
later, turns that inserted path into an attachment and restores the draft.
"""

from pathlib import Path

import bpy

from . import display_text
from .ui_text import n_


def defer_console_drop(window, area, controller):
    """Turn the path stock Console inserts for a dropped file into an attachment, and restore the draft."""
    from . import console
    from .chat_view import cursor_position, set_draft
    if not area.spaces.active.history:
        return
    line = area.spaces.active.history[-1]
    before, cursor = line.body, cursor_position(line)
    pointer, window_pointer = area.as_pointer(), window.as_pointer()

    def attach():
        target = next((w for w in bpy.context.window_manager.windows if w.as_pointer() == window_pointer), None)
        current = next((a for a in target.screen.areas if a.as_pointer() == pointer and console.is_chat(a)), None) if target else None
        if current is None or not current.spaces.active.history:
            return None
        draft = current.spaces.active.history[-1]
        inserted = draft.body[cursor:len(draft.body) - len(before[cursor:]) if before[cursor:] else None]
        try:
            if not inserted.startswith('"') or not inserted.endswith('"'):
                return None
            path = inserted[1:-1]  # Native Console quotes, not Python evaluation.
            set_draft(draft, before, cursor)
            if not Path(path).is_file():
                raise ValueError(display_text.Text(n_("Drop a supported regular file, not a directory")))
            if controller.busy:
                raise ValueError(display_text.Text(n_("Wait for this turn to finish before dropping attachments")))
            controller.attachments.extend(controller.references.import_images([path], controller.attachments))
            controller.version += 1
        except (OSError, ValueError, SyntaxError) as error:
            controller.emit("message", {"role": "error", **display_text.error_notice(error)})
        current.tag_redraw()
        return None
    bpy.app.timers.register(attach, first_interval=0.02)
