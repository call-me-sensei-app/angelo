# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's agent add-on is never started from saved preferences in a background Angelo: ``held``.

Angelo starts its add-on in its window by a timer (``scripts/startup/angelo_default_layout.py``), and the running
add-on keeps an entry in the preferences for its settings (``angelo_agent.ui.register``). Once the preferences are
saved, which a quit does, they list the add-on, and Blender enables every add-on its preferences list in every
process that reads them: a person's ``angelo --background`` render, an engine's import of a ``.blend`` (Godot runs
one on every save). There the add-on has no window to serve, yet it started all of itself: its sign-in checks, its
state, and at the file's load the active chat, which it bound to that file and wrote. An Angelo with that chat open
then found another writer's content in the chat's file and refused every later call (the showcase's Godot
session, 2026-11-14: reproduced in four seconds with ``angelo -b file.blend --python-expr pass``, and not with
``--factory-startup``, which reads no preferences).

So the two places where Blender enables the add-ons its preferences list (``addon_utils._initialize_once`` at
start-up, ``addon_utils.reset_all`` when preferences are read again) skip Angelo's agent add-on in a background
process. Nothing else changes: ``addon_utils.enable`` by name (a script, a suite, ``--addons angelo_agent``) starts
it as before, Angelo's own background children start what they need themselves (``headless_main``), and in a
window the add-on starts as it always did. ``doc/angelo/godot_link.rst`` already said "Angelo's add-on starts
nothing in a background session"; this is what makes it so.

No import of the add-on; ``bpy`` only when asked without ``background``.
"""

AGENT = "angelo_agent"


def held(module_name, background=None):
    """True when preferences list ``module_name`` but this process must not start it from them."""
    if module_name != AGENT:
        return False
    if background is None:
        import bpy
        background = bpy.app.background
    return bool(background)
