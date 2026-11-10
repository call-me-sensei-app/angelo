# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A conversation's name as the person set it (Rename in the Conversations list). Standard library only.

A chat's shown title is its own name when the person gave it one, else the automatic one: the person's first
request, else "New chat" (``sessions.SessionCollection.update``, the one place a title is derived). The name is
saved in the chat's own file as its top-level ``title`` (``sessions.SessionStore``), so it survives a restart, a
damaged chat list (rebuilt from the chat files) and a later automatic title; an empty name removes it, and the
automatic title shows again. A name is data: it is never translated and never sent to a model.
"""

import unicodedata

# The longest name kept, in characters: the chat list's own limit for a title (``session_validation.index_record``).
LIMIT = 64


def clean_name(text):
    """``text`` as a name: one line, its runs of white space one space, no control or format characters (a pasted
    newline, a bidirectional override, a lone surrogate), at most ``LIMIT`` characters. "" when nothing printable
    is left: the automatic title then shows again."""
    if not isinstance(text, str):
        return ""
    kept = "".join(" " if character.isspace() else character for character in text
                   if character.isspace() or unicodedata.category(character)[0] != "C")
    return " ".join(kept.split())[:LIMIT].strip()


def custom_title(value):
    """The saved name ``value`` when it is one this build keeps (``clean_name`` leaves it as it is), else None."""
    return value if isinstance(value, str) and value and clean_name(value) == value else None


def title_field(state):
    """The saved field of the chat state ``state``'s name: ``[("title", name)]``, or ``[]`` when it has none."""
    name = custom_title(state.get("title"))
    return [("title", name)] if name else []


def set_name(state, text):
    """Give the chat state ``state`` the name ``text`` (cleaned), or take its name away when it cleans to "".
    Returns the name kept, or ""."""
    name = clean_name(text)
    if name:
        state["title"] = name
    else:
        state.pop("title", None)
    return name
