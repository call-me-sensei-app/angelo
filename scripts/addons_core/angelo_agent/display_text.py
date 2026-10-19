# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Messages built away from the UI that the UI translates where it shows them.

Worker-thread and bpy-free modules (the team account, the managed policy)
produce sentences for the person long before a draw call shows them. A
``Text`` is that English sentence (it *is* a ``str``, so logs, tests and
existing string handling keep working) and also remembers its template and
named fields, so ``shown`` can translate the template and fill the fields at
display time (ui_text's rules: whole sentences, named placeholders, translate
only where shown). Templates are marked with ``ui_text.n_`` so the extractor
finds them. A plain ``str`` passed to ``shown`` is looked up as a whole.

Concatenating a ``Text`` gives a plain ``str`` (English only): compose with a
template instead.
"""

from .ui_text import iface_, notice


class Text(str):
    def __new__(cls, template, **fields):
        text = super().__new__(cls, template.format(**fields) if fields else template)
        text.template, text.fields = template, dict(fields)
        return text

    def __reduce__(self):
        return (_rebuild, (self.template, tuple(self.fields.items())))


def _rebuild(template, fields):
    return Text(template, **dict(fields))


def error_notice(error):
    """The body of a chat error row for ``error``: a ``Text`` message keeps its template for display, any other is its text."""
    first = error.args[0] if error.args else None
    return notice(first.template, **first.fields) if isinstance(first, Text) else {"text": str(error)}


def shown(value):
    """``value`` in the interface language; fields that are themselves ``Text`` are translated too."""
    if isinstance(value, Text):
        fields = {key: shown(item) if isinstance(item, Text) else item for key, item in value.fields.items()}
        return iface_(value.template, **fields)
    return iface_(value) if value else value
