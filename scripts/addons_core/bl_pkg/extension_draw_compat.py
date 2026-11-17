# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Draw one Extensions list row through ``bl_extension_ui.extension_draw_item``, whoever replaced it (Angelo).

Some add-ons replace that module function to add their own controls to each row. Blender 5.3 gave it a new
argument (``show_type``, a12f839dd9c), and a replacement written for 5.2 (BlenderKit, #166392) raised ``TypeError``
on every row, which ends the panel's draw: the whole list was empty. Here a replacement is given only the arguments
it takes, so its rows still draw; one that still fails has the row drawn as Blender draws it. Either says so in one
line per replacing function, naming its module. No ``bpy``: tests/python/angelo_agent/
test_native_extension_draw_compat.py.
"""

import inspect

__all__ = ("draw",)

_reported = set()


def _accepted(drawer, arguments):
    """``arguments`` restricted to the keywords ``drawer`` takes (all of them when it takes ``**kwargs``)."""
    try:
        parameters = list(inspect.signature(drawer).parameters.values())
    except (TypeError, ValueError):   # No signature to read: give it everything, as Blender would.
        return dict(arguments)
    if any(parameter.kind == parameter.VAR_KEYWORD for parameter in parameters):
        return dict(arguments)
    names = {parameter.name for parameter in parameters
             if parameter.kind in (parameter.KEYWORD_ONLY, parameter.POSITIONAL_OR_KEYWORD)}
    return {name: value for name, value in arguments.items() if name in names}


def _once(drawer, message, report):
    key = id(drawer)
    if key in _reported:
        return
    _reported.add(key)
    report("Extensions: %s.%s %s" % (getattr(drawer, "__module__", "?"), getattr(drawer, "__qualname__", "?"),
                                     message))


def draw(drawer, builtin, layout, arguments, report=print):
    """Draw one row with ``drawer`` (``extension_draw_item`` as the module holds it now) into ``layout``."""
    if drawer is builtin:
        return builtin(layout, **arguments)
    given = _accepted(drawer, arguments)
    try:
        result = drawer(layout, **given)
    except Exception as error:   # A failing row must not end the panel's draw: Blender draws it instead.
        _once(drawer, "could not draw an extension row (%s: %s); rows are drawn as Blender draws them"
              % (type(error).__name__, error), report)
        return builtin(layout, **arguments)
    left_out = sorted(set(arguments) - set(given))
    if left_out:
        _once(drawer, "replaces the extension row drawer and takes no %s; it draws the rows without them"
              % ", ".join(left_out), report)
    return result
