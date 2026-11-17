# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which Python a Python-defined property runs, read from its ``bpy.props`` definition.

``rna_rules`` admits a few Python-defined settings (Cycles' render settings, game physics) because writing them
runs no Python, and the suites that audit those lists read the installed definitions. A definition's callbacks are
the keywords whose value Blender calls: ``update``, ``get``, ``set``, ``get_transform`` and ``set_transform`` (Blender
calls them around every read and write, bpy_props.cc), ``poll`` and computed enum ``items``, and any callback a later
Blender adds. They are found by their value, never by a list of keyword names, so a new kind of callback cannot pass
the audit unseen. A pointer's or collection's ``type`` is a class, not a callback.
"""


def python_callbacks(definition):
    """The sorted keyword names of ``definition`` (a deferred ``bpy.props`` property) whose value Blender calls."""
    return sorted(name for name, value in definition.keywords.items()
                  if callable(value) and not isinstance(value, type))
