# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The composer's editing, as pure functions over the draft: no Blender, so every action is testable alone.

A draft is ``Draft(value, caret, anchor, upstream)``: the text, the caret (a character index), the selection's other
end (None, or the caret itself, for no selection) and the caret's affinity. Where a row wraps inside a word or
between Chinese or Japanese characters, the end of that row and the start of the next are one index; ``upstream``
says the caret is at the end of the earlier row (after End, a click past that row's end, Up or Down onto it), as an
``NSTextView``'s or a Windows edit control's caret is, so it is drawn there and Home, End, Up and Down go from there. ``rows`` are the draft's visual rows as wrapped and drawn
(``((text, offset), ...)``, ``chat_view.wrap(..., ordered=False)``): a row followed by one starting right after its
last character is soft-wrapped; one followed after a skipped newline ends its paragraph. ``vertical(caret, up)`` is
the caret one visual row up or down at the same x from row ``current`` (``chat_composer.vertical_caret`` as drawn).

``apply(action, ...)`` performs one ``composer_keys`` action; ``History`` is the draft's own Undo, never the scene's.
"""

import unicodedata
from dataclasses import dataclass, replace
from typing import Optional

LIMIT = 32000   # A message's characters (``chat_composer.set_draft``).
PAGE_ROWS = 7   # A page: the composer's eight visible rows less one, so a row of context stays in view.
_APOSTROPHES = "'’"
COALESCE = frozenset({"type", "delete_back", "delete_forward"})   # Runs of these are one Undo step.


@dataclass(frozen=True)
class Draft:
    value: str
    caret: int
    anchor: Optional[int] = None
    upstream: bool = False

    @property
    def selection(self):
        """``(start, end)``, or None when nothing is selected."""
        if self.anchor is None or self.anchor == self.caret:
            return None
        return (min(self.anchor, self.caret), max(self.anchor, self.caret))


@dataclass(frozen=True)
class Edit:
    draft: Draft
    clipboard: Optional[str] = None   # Text to put on the clipboard (copy, cut).
    kill: Optional[str] = None        # Text for the kill buffer (macOS Ctrl+K).
    scroll: Optional[str] = None      # "top", "bottom", "page_up", "page_down": the view moves, the caret stays.
    kind: Optional[str] = None        # The Undo grouping of a change ("type", "delete_back", ...); None: a move.


# -- words, rows and paragraphs ---------------------------------------------------------------------------------

def _word_char(char):
    return char.isalnum() or char == "_" or unicodedata.category(char).startswith("M")


def in_word(value, index):
    """Whether ``value[index]`` belongs to a word: a letter, digit, mark, underscore, or an apostrophe inside one."""
    char = value[index]
    if _word_char(char):
        return True
    return (char in _APOSTROPHES and 0 < index < len(value) - 1 and _word_char(value[index - 1])
            and _word_char(value[index + 1]))


def word_left(value, caret):
    """The start of the word before the caret (Option+Left, Ctrl+Left): past spaces and punctuation, then the word."""
    index = caret
    while index > 0 and not in_word(value, index - 1):
        index -= 1
    while index > 0 and in_word(value, index - 1):
        index -= 1
    return index


def word_right_end(value, caret):
    """The end of the word after the caret (macOS Option+Right)."""
    index = caret
    while index < len(value) and not in_word(value, index):
        index += 1
    while index < len(value) and in_word(value, index):
        index += 1
    return index


def word_right_start(value, caret):
    """The start of the next word (Windows and Linux Ctrl+Right): past this word or punctuation mark, then the
    spaces after it; a line's end is a stop of its own."""
    index, end = caret, len(value)
    if index >= end:
        return end
    if value[index] == "\n":
        return index + 1
    if in_word(value, index):
        while index < end and in_word(value, index):
            index += 1
    elif not value[index].isspace():
        index += 1
    while index < end and value[index].isspace() and value[index] != "\n":
        index += 1
    return index


def word_at(value, index):
    """``(start, end)`` of the word, space run or single mark at ``index`` (a double-click selects it)."""
    if not value:
        return 0, 0
    index = max(0, min(len(value) - 1, index))
    if in_word(value, index):
        start = end = index
        while start > 0 and in_word(value, start - 1):
            start -= 1
        while end < len(value) and in_word(value, end):
            end += 1
        return start, end
    if value[index].isspace() and value[index] != "\n":
        start = end = index
        while start > 0 and value[start - 1].isspace() and value[start - 1] != "\n":
            start -= 1
        while end < len(value) and value[end].isspace() and value[end] != "\n":
            end += 1
        return start, end
    return index, index + 1


def para_start(value, caret):
    return value.rfind("\n", 0, caret) + 1


def para_end(value, caret):
    end = value.find("\n", caret)
    return len(value) if end < 0 else end


def paragraph_at(value, index):
    """``(start, end)`` of the paragraph at ``index``, its newline included when it has one (a triple-click)."""
    start, end = para_start(value, index), para_end(value, index)
    return start, min(len(value), end + 1)


def row_index(rows, caret):
    return max((i for i, (_, offset) in enumerate(rows) if offset <= caret), default=0)


def soft_wrapped(rows, index):
    return index + 1 < len(rows) and rows[index + 1][1] == rows[index][1] + len(rows[index][0])


def glued(rows, index):
    """Whether row ``index`` wraps inside a word or between Chinese or Japanese characters (no space at the break):
    its end and the next row's start are one index, told apart by the caret's affinity."""
    return soft_wrapped(rows, index) and not breaks_at_space(rows, index)


def caret_row(rows, caret, upstream=False):
    """The row the caret is drawn on: the later row at a shared index, unless the caret is ``upstream``."""
    index = row_index(rows, caret)
    if upstream and index > 0 and rows[index][1] == caret and glued(rows, index - 1):
        return index - 1
    return index


def at_row_end(rows, index, caret):
    """Whether ``caret`` is the end of row ``index`` where that end is the next row's start (an upstream caret)."""
    return glued(rows, index) and caret == rows[index][1] + len(rows[index][0])


def line_start(rows, caret, upstream=False):
    return rows[caret_row(rows, caret, upstream)][1] if rows else 0


def line_end(rows, caret, upstream=False):
    """The visual row's end. A row wrapped at a space ends before that space (a caret after it is the next row's);
    a row wrapped inside a word or between Chinese or Japanese characters (``text_layout.line_break``) ends after
    its last character, with the caret upstream there."""
    if not rows:
        return caret
    index = caret_row(rows, caret, upstream)
    text, offset = rows[index]
    return offset + len(text) - (1 if breaks_at_space(rows, index) else 0)


def breaks_at_space(rows, index):
    """Whether row ``index`` is soft-wrapped at a trailing space."""
    text = rows[index][0]
    return soft_wrapped(rows, index) and bool(text) and text[-1].isspace()


# -- actions ----------------------------------------------------------------------------------------------------

def _rows_target(action, draft, rows, vertical, platform):
    """``(caret, upstream)`` after a move by rows (Up, Down, a page): from the row the caret is drawn on."""
    up = action in {"row_up", "page_up"}
    current = caret_row(rows, draft.caret, draft.upstream) if rows else 0
    if action in {"row_up", "row_down"} and platform == "mac" and rows and (
            current == 0 if up else current == len(rows) - 1):
        return (0 if up else len(draft.value)), False   # An NSTextView's first row goes to the start, its last to the end.
    caret = draft.caret
    for _ in range(PAGE_ROWS if action in {"page_up", "page_down"} else 1):
        target = max(0, min(len(rows) - 1, current + (-1 if up else 1)))
        caret, current = vertical(caret, up, current), target
    return caret, bool(rows) and at_row_end(rows, current, caret)


def _move_target(action, draft, rows, vertical, platform):
    """``(caret, upstream)`` after a move, or None for an action that moves nothing."""
    caret = draft.caret
    if action in {"row_up", "row_down", "page_up", "page_down"}:
        return _rows_target(action, draft, rows, vertical, platform)
    if action == "line_end":
        end = line_end(rows, caret, draft.upstream)
        return end, bool(rows) and at_row_end(rows, caret_row(rows, caret, draft.upstream), end)
    target = _plain_target(action, draft, rows)
    return None if target is None else (target, False)


def _plain_target(action, draft, rows):
    value, caret = draft.value, draft.caret
    if action == "char_left":
        return max(0, caret - 1)
    if action == "char_right":
        return min(len(value), caret + 1)
    if action == "line_start":
        return line_start(rows, caret, draft.upstream)
    if action == "doc_start":
        return 0
    if action == "doc_end":
        return len(value)
    if action == "word_left":
        return word_left(value, caret)
    if action == "word_right_end":
        return word_right_end(value, caret)
    if action == "word_right_start":
        return word_right_start(value, caret)
    if action == "para_start":
        return para_start(value, caret)
    if action == "para_end":
        return para_end(value, caret)
    if action == "para_up":   # Again at a paragraph's start: the previous paragraph's start.
        start = para_start(value, caret)
        return para_start(value, caret - 1) if start == caret and caret > 0 else start
    if action == "para_down":
        end = para_end(value, caret)
        return para_end(value, caret + 1) if end == caret and caret < len(value) else end
    return None


_BACKWARD = {"char_left", "row_up", "page_up", "line_start", "doc_start", "word_left", "para_start", "para_up"}


def _replace(draft, start, end, text):
    """The draft with ``[start, end)`` replaced by ``text``, caret after it; within the message's limit."""
    text = text[:max(0, LIMIT - (len(draft.value) - (end - start)))]
    return Draft(draft.value[:start] + text + draft.value[end:], start + len(text))


def insert(draft, text, kind=None):
    """``text`` typed or pasted over the selection, or at the caret. A typed character is one step of a typing run
    (``kind`` "type"); a newline or a paste is a step of its own."""
    kind = kind or ("type" if len(text) == 1 and text != "\n" else "insert")
    start, end = draft.selection or (draft.caret, draft.caret)
    return Edit(_replace(draft, start, end, text), kind=kind)


def _delete_range(action, draft, rows, platform):
    value, caret = draft.value, draft.caret
    if action == "delete_back":
        return max(0, caret - 1), caret
    if action == "delete_forward":
        return caret, min(len(value), caret + 1)
    if action == "delete_word_back":
        return word_left(value, caret), caret
    if action == "delete_word_forward_end":
        return caret, word_right_end(value, caret)
    if action == "delete_word_forward_start":
        return caret, word_right_start(value, caret)
    if action == "delete_line_start":   # At the row's start, the character before it (the newline that ends a line).
        start = line_start(rows, caret, draft.upstream)
        return (start if start < caret else max(0, caret - 1)), caret
    if action == "delete_line_end":
        end = line_end(rows, caret, draft.upstream)
        return caret, (end if end > caret else min(len(value), caret + 1))
    if action == "kill_para_end":   # At a paragraph's end it takes the newline, as Emacs and Cocoa do.
        end = para_end(value, caret)
        return caret, (end if end > caret else min(len(value), caret + 1))
    return None


def apply(action, extend, draft, rows, vertical, platform="pc", clipboard="", kill=""):
    """The draft after one ``composer_keys`` action (undo and redo are ``History``'s)."""
    selection = draft.selection
    target = _move_target(action, draft, rows, vertical, platform)
    if target is not None or action in {"scroll_top", "scroll_bottom", "scroll_page_up", "scroll_page_down"}:
        if target is None:
            return Edit(draft, scroll=action[len("scroll_"):])
        caret, upstream = target
        if extend:
            return Edit(Draft(draft.value, caret, draft.caret if draft.anchor is None else draft.anchor, upstream))
        if selection and action in {"char_left", "char_right"}:   # Collapses to the selection's edge.
            return Edit(Draft(draft.value, selection[0] if action == "char_left" else selection[1]))
        if selection:   # Moves from the edge in its direction.
            edge = selection[0] if action in _BACKWARD else selection[1]
            return Edit(Draft(draft.value, *_move_target(action, Draft(draft.value, edge), rows, vertical, platform)))
        return Edit(Draft(draft.value, caret, None, upstream))
    if action == "select_all":
        return Edit(Draft(draft.value, len(draft.value), 0))
    if action == "copy":
        return Edit(draft, clipboard=draft.value[selection[0]:selection[1]] if selection else None)
    if action == "cut":
        if not selection:
            return Edit(draft)
        return Edit(_replace(draft, selection[0], selection[1], ""), clipboard=draft.value[selection[0]:selection[1]],
                    kind="cut")
    if action == "paste":
        text = (clipboard or "").replace("\r\n", "\n").replace("\r", "\n")
        return insert(draft, text, "paste") if text else Edit(draft)
    if action == "yank":
        return insert(draft, kill, "paste") if kill else Edit(draft)
    if action == "transpose":
        value, caret = draft.value, draft.caret
        if selection or len(value) < 2 or caret == 0:
            return Edit(draft)
        left = caret - 1 if caret < len(value) else caret - 2
        swapped = value[:left] + value[left + 1] + value[left] + value[left + 2:]
        return Edit(Draft(swapped, left + 2), kind="transpose")
    span = selection or _delete_range(action, draft, rows, platform)
    if span is None:
        raise ValueError("Unknown composer action: %s" % action)
    if span[0] == span[1]:
        return Edit(Draft(draft.value, draft.caret))
    removed = draft.value[span[0]:span[1]]
    kind = action if action in COALESCE and not selection else "delete"
    return Edit(_replace(draft, span[0], span[1], ""), kill=removed if action == "kill_para_end" else None, kind=kind)


# -- the draft's own Undo -----------------------------------------------------------------------------------------

class History:
    """The draft's Undo and Redo, never the scene's. A typing or deleting run is one step, ended by any other
    change, by the caret moving elsewhere or by a selection (typing over one starts a step of its own, so Undo brings
    the selected text back); a change made outside the composer (a sent message, a voice
    transcript, another chat's draft) starts a new history, so Undo never brings back a sent message."""

    LIMIT = 500

    def __init__(self):
        self.undo, self.redo = [], []
        self.value, self.caret, self.kind = None, None, None

    def sync(self, value):
        """Called before every edit with the draft as it is: a draft changed elsewhere starts a new history."""
        if value != self.value:
            self.undo.clear()
            self.redo.clear()
            self.value, self.caret, self.kind = value, None, None

    @property
    def active(self):
        """Whether the draft has Undo or Redo steps: then Cmd/Ctrl+Z is the draft's, else Blender's."""
        return bool(self.undo or self.redo)

    def record(self, before, after, kind):
        if after.value == before.value:
            if after.caret != before.caret:
                self.kind = None   # A caret move ends a typing run.
            self.caret = after.caret
            return
        joins = before.selection is None and before.caret == self.caret and self.undo
        if not (kind in COALESCE and kind == self.kind and joins):
            self.undo.append(before)
            del self.undo[:-self.LIMIT]
        self.redo.clear()
        self.value, self.caret, self.kind = after.value, after.caret, kind

    def step(self, current, back):
        """The draft after Undo (``back``) or Redo, or None when there is nothing to take."""
        source, target = (self.undo, self.redo) if back else (self.redo, self.undo)
        if not source:
            return None
        target.append(current)
        restored = source.pop()
        self.value, self.caret, self.kind = restored.value, restored.caret, None
        return replace(restored)
