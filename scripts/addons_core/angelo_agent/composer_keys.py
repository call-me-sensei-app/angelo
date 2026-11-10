# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The composer's keyboard shortcuts, per platform, as data: the keys a native multi-line text field takes.

The composer edits like the platform's own multi-line text field (an ``NSTextView`` on macOS, a multi-line edit
control on Windows and GTK's text view on Linux), not like the single-line Console it is drawn over. Each entry is
``(event type, modifiers, action, shift extends)``: ``modifiers`` names the modifiers that must be held ("cmd",
"ctrl", "alt"; "shift" when Shift is part of the shortcut itself); with ``shift extends`` and no "shift" among them,
Shift may be held as well and extends the selection instead of moving the caret alone; with "shift" among them the
entry itself extends it (macOS Shift+Home). The actions are
``composer_edit.apply``'s.

These keys reach the composer only while it is editing: the chat's input handler (``chat_view``) reads them while
the composer holds keyboard focus or the pointer rests on the chat, after Escape they are Blender's again. The
handler is a window modal handler, not a keymap item, so a keyconfig preset reload never removes them.

1.0.9 (maintainer report): the composer took only the Console's single-line keys, so select line, top and bottom,
word moves and draft Undo did nothing, and Cmd+Z undid the scene while typing.
"""

import sys

MAC, PC = "mac", "pc"

# Caret moves: the selection extends with Shift.
_MOVES_SHARED = (
    ('LEFT_ARROW', "", "char_left", True),
    ('RIGHT_ARROW', "", "char_right", True),
    ('UP_ARROW', "", "row_up", True),
    ('DOWN_ARROW', "", "row_down", True),
)
_EDITS_SHARED = (
    ('BACK_SPACE', "", "delete_back", False),
    ('BACK_SPACE', "shift", "delete_back", False),
    ('DEL', "", "delete_forward", False),
)

TABLE = {
    # NSTextView's standard key bindings (AppKit StandardKeyBinding.dict) and the Emacs keys it honours.
    MAC: _MOVES_SHARED + _EDITS_SHARED + (
        ('LEFT_ARROW', "cmd", "line_start", True),           # moveToLeftEndOfLine (the visual row)
        ('RIGHT_ARROW', "cmd", "line_end", True),            # moveToRightEndOfLine
        ('UP_ARROW', "cmd", "doc_start", True),              # moveToBeginningOfDocument
        ('DOWN_ARROW', "cmd", "doc_end", True),              # moveToEndOfDocument
        ('LEFT_ARROW', "alt", "word_left", True),            # moveWordLeft
        ('RIGHT_ARROW', "alt", "word_right_end", True),      # moveWordRight: to the end of the word
        ('UP_ARROW', "alt", "para_up", True),                # moveBackward + moveToBeginningOfParagraph
        ('DOWN_ARROW', "alt", "para_down", True),            # moveForward + moveToEndOfParagraph
        ('HOME', "", "scroll_top", False),                   # scrollToBeginningOfDocument: the caret stays
        ('END', "", "scroll_bottom", False),                 # scrollToEndOfDocument
        ('HOME', "shift", "doc_start", True),                # moveToBeginningOfDocumentAndModifySelection
        ('END', "shift", "doc_end", True),
        ('PAGE_UP', "", "scroll_page_up", False),            # scrollPageUp: the caret stays
        ('PAGE_DOWN', "", "scroll_page_down", False),
        ('PAGE_UP', "alt", "page_up", True),                 # pageUp: the caret moves a page
        ('PAGE_DOWN', "alt", "page_down", True),
        ('PAGE_UP', "shift", "page_up", True),               # pageUpAndModifySelection
        ('PAGE_DOWN', "shift", "page_down", True),
        ('BACK_SPACE', "alt", "delete_word_back", False),    # deleteWordBackward
        ('BACK_SPACE', "cmd", "delete_line_start", False),   # deleteToBeginningOfLine
        ('DEL', "alt", "delete_word_forward_end", False),    # Fn+Option+Delete: deleteWordForward
        ('DEL', "cmd", "delete_line_end", False),            # Fn+Cmd+Delete: deleteToEndOfLine
        ('A', "cmd", "select_all", False),
        ('C', "cmd", "copy", False),
        ('X', "cmd", "cut", False),
        ('V', "cmd", "paste", False),
        ('Z', "cmd", "undo", False),
        ('Z', "cmd shift", "redo", False),
        # Emacs keys every Cocoa text field honours (Control, never Command).
        ('A', "ctrl", "para_start", True),                   # moveToBeginningOfParagraph
        ('E', "ctrl", "para_end", True),                     # moveToEndOfParagraph
        ('B', "ctrl", "char_left", True),                    # moveBackward
        ('F', "ctrl", "char_right", True),                   # moveForward
        ('P', "ctrl", "row_up", True),                       # moveUp
        ('N', "ctrl", "row_down", True),                     # moveDown
        ('D', "ctrl", "delete_forward", False),              # deleteForward
        ('H', "ctrl", "delete_back", False),                 # deleteBackward
        ('K', "ctrl", "kill_para_end", False),               # deleteToEndOfParagraph (into the kill buffer)
        ('Y', "ctrl", "yank", False),                        # yank
        ('T', "ctrl", "transpose", False),                   # transpose
        # Blender on macOS takes Control as Command for its own copy, paste and Undo, and 1.0.9's composer did:
        # kept, so a Blender habit never reaches the scene's Undo while the draft has its own.
        ('C', "ctrl", "copy", False),
        ('X', "ctrl", "cut", False),
        ('V', "ctrl", "paste", False),
        ('Z', "ctrl", "undo", False),
        ('Z', "ctrl shift", "redo", False),
    ),
    # A Windows multi-line edit control. Linux takes it too: GTK's text view has the same keys, except that its
    # Ctrl+Right stops at a word's end, where the Windows control (and this table) goes on to the next word's start.
    PC: _MOVES_SHARED + _EDITS_SHARED + (
        ('HOME', "", "line_start", True),
        ('END', "", "line_end", True),
        ('HOME', "ctrl", "doc_start", True),
        ('END', "ctrl", "doc_end", True),
        ('LEFT_ARROW', "ctrl", "word_left", True),
        ('RIGHT_ARROW', "ctrl", "word_right_start", True),   # To the start of the next word.
        ('PAGE_UP', "", "page_up", True),
        ('PAGE_DOWN', "", "page_down", True),
        ('BACK_SPACE', "ctrl", "delete_word_back", False),
        ('DEL', "ctrl", "delete_word_forward_start", False),
        ('A', "ctrl", "select_all", False),
        ('C', "ctrl", "copy", False),
        ('X', "ctrl", "cut", False),
        ('V', "ctrl", "paste", False),
        ('INSERT', "ctrl", "copy", False),
        ('INSERT', "shift", "paste", False),
        ('DEL', "shift", "cut", False),
        ('Z', "ctrl", "undo", False),
        ('Y', "ctrl", "redo", False),
        ('Z', "ctrl shift", "redo", False),
    ),
}

# Keys another module of the add-on answers in the composer, checked before ``TABLE``: (event type, modifiers,
# condition, action). ``modifiers`` must be held exactly; ``condition`` names a ``CONDITIONS`` test of the draft (a
# ``composer_edit.Draft``); ``action`` is "module.function" in this add-on, called as ``function(line, session)``
# (the draft's ConsoleLine, the chat's id) and returning True when it took the key, False to leave it to the table.
# Data like the table, so no registration can be lost (a reload, a keyconfig preset). The chat's pending messages:
# Up (no modifier) in an empty composer brings the newest pending message back to edit; Up in a draft still moves
# the caret a row, and with nothing pending the key stays the table's.
EXTERNAL = (("UP_ARROW", "", "empty", "chat_pending.recall_newest"),)
CONDITIONS = {"empty": lambda draft: not draft.value,
              "any": lambda draft: True}

# Keys the field owns whatever else is held: an unlisted combination does what the key alone does (1.0.9 moved the
# caret for any modifier), so it never falls through to the Console keymap under the chat, which edits its line.
FIELD_KEYS = frozenset({'LEFT_ARROW', 'RIGHT_ARROW', 'UP_ARROW', 'DOWN_ARROW', 'HOME', 'END', 'PAGE_UP', 'PAGE_DOWN',
                        'BACK_SPACE', 'DEL'})
_EVENT_FLAG = {"cmd": "oskey", "ctrl": "ctrl", "alt": "alt", "shift": "shift"}


def platform_of(name=None):
    """The table a ``sys.platform`` value (or ``MAC``/``PC`` itself) uses: macOS's, or the Windows and Linux one."""
    name = name or sys.platform
    return name if name in TABLE else MAC if name == "darwin" else PC


def _held(event):
    return frozenset(name for name, flag in _EVENT_FLAG.items() if getattr(event, flag, False))


def _match(kind, held, platform):
    for entry_kind, modifiers, action, extends in TABLE[platform]:
        if entry_kind != kind:
            continue
        required = frozenset(modifiers.split())
        if "shift" in required:   # Shift is part of the shortcut (Shift+Home on macOS extends by its entry).
            if held == required:
                return action, extends
        elif extends:
            if held - {"shift"} == required:
                return action, "shift" in held
        elif held == required:
            return action, False
    return None


def lookup(event, platform=None):
    """``(action, extend)`` for a key press in the composer, or None when the table has no entry for it."""
    platform, held = platform_of(platform), _held(event)
    found = _match(event.type, held, platform)
    if found is None and event.type in FIELD_KEYS:   # An unlisted combination: without Shift (Shift+Option+Backspace
        # deletes a word, as Option+Backspace), else the key alone, Shift still selecting.
        found = (_match(event.type, held - {"shift"}, platform) or _match(event.type, held & {"shift"}, platform)
                 or _match(event.type, frozenset(), platform))
    return found


def external(event, draft, entries=None):
    """The ``EXTERNAL`` actions ("module.function") whose key, exact modifiers and condition match, in order."""
    held = _held(event)
    return [action for kind, modifiers, condition, action in (EXTERNAL if entries is None else entries)
            if kind == event.type and frozenset(modifiers.split()) == held and CONDITIONS[condition](draft)]


def bound(event, entries=None):
    """Whether an ``EXTERNAL`` entry names this key with exactly these modifiers, whatever its condition."""
    held = _held(event)
    return any(kind == event.type and frozenset(modifiers.split()) == held
               for kind, modifiers, _condition, _action in (EXTERNAL if entries is None else entries))


def rows(platform=None):
    """The table as a person reads it: (keys, action, Shift extends), for documents and the GUI matrix."""
    platform = platform_of(platform)
    names = {"cmd": "Cmd", "ctrl": "Ctrl", "alt": "Option" if platform == MAC else "Alt", "shift": "Shift"}
    return [("+".join([names[m] for m in modifiers.split()] + [kind]), action, extends)
            for kind, modifiers, action, extends in TABLE[platform]]
