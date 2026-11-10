# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What Return does in a text field: one rule for every Angelo text field, as the chat composer has it.

* **Return** runs the field's primary action (Generate for a prompt, Apply for the expanded prompt). When that action
  is unavailable it does nothing and the reason shows; it never inserts a line instead.
* **Shift+Return** inserts a line in a field that takes paragraphs.
* A field with no primary action confirms its text and lets go of the keyboard (it blurs).

    enter(multiline, shift, primary) -> LINE | PRIMARY | BLOCKED | BLUR
        ``primary``: None when the field has no primary action, else whether it is available now.

Pure (no Blender): Media Studio uses it (``studio_enter``), and the chat's composer can adopt it.
"""

LINE, PRIMARY, BLOCKED, BLUR = "line", "primary", "blocked", "blur"


def enter(multiline, shift, primary):
    if shift and multiline:
        return LINE
    if primary is None:
        return BLUR
    return PRIMARY if primary else BLOCKED
