# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A new Angelo's reading of the agent add-on's saved preferences, run by a GUI suite as a second process on the
suite's own isolated profile (``rail_positions_stages.persistence``)::

  Angelo [--background] --disable-autoexec --python prefs_read_back.py -- NAME [NAME ...]

Prints one ``ANGELO_PREFS_READ {json}`` line: whether the preferences list the add-on and whether it started, and each
named property of its preferences (None while they are unreadable).

* In a window the add-on starts from the saved preferences, as at any launch: ``at_start`` is the person's next start.
* In a background process it never does (``angelo_background_addons``: a person's command-line render or an engine's
  import starts no agent); ``by_name`` is the reading once a script enables it by name, as that module says one may.

It only reads: the preferences are never saved back, and a window quits through Blender (``wm.quit_blender``), never
by ending the process, so Blender's own exit runs (the Vulkan first-frame record among it).
"""

import json
import sys

import addon_utils
import bpy

AGENT = "angelo_agent"
NAMES = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def read():
    entry = bpy.context.preferences.addons.get(AGENT)
    preferences = getattr(entry, "preferences", None)
    values = {name: getattr(preferences, name, None) for name in NAMES}
    return {"listed": entry is not None, "started": bool(addon_utils.check(AGENT)[1]), "values": values}


def main():
    preferences = bpy.context.preferences
    preferences.use_preferences_save = False   # Never written back from here.
    result = {"background": bpy.app.background, "at_start": read()}
    if bpy.app.background:
        addon_utils.enable(AGENT, default_set=False)
        result["by_name"] = read()
    print("ANGELO_PREFS_READ " + json.dumps(result), flush=True)
    if not bpy.app.background:
        bpy.ops.wm.quit_blender()   # Run from a script it quits at once: no save prompt (only its invoke asks).


main()
