# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's own add-on stays enabled whenever Blender reads its preferences again.

Reading preferences disables every add-on, then enables the ones the new preferences list (``wm_homefile_read``:
``addon_utils.disable_all`` before, ``addon_utils.reset_all`` after). That happens on Load Factory Preferences and
Load Factory Settings (File > Defaults, Preferences > Save & Load), Revert to Saved Preferences, a switch to or from an
application template, Blender's Import Settings (``preferences.copy_prev``) and Angelo's settings import. Angelo's
start-up enables ``angelo_agent`` without a saved entry and factory preferences never list it, so without this the
chat and the agent were gone until a restart.

While the add-on is registered, ``install`` wraps both functions for the session:

* ``disable_all`` leaves Angelo's add-on loaded: its chats and their turns, the sign-ins, the keys held in memory and
  any open card stay as they are. Every other add-on is disabled, as in Blender.
* ``reset_all`` lists Angelo's add-on in the new preferences before enabling what they list. A new entry carries no
  settings, so factory preferences reset Angelo's own add-on preferences to their defaults, and saved preferences
  that list it bring back the saved ones. Everything else follows the new preferences, as in Blender.

Left alone on purpose: quitting (``_exit_pre`` takes the wrappers out first, so Blender's exit unregisters the add-on
and its chats are saved), Reload Scripts (it disables and enables each listed add-on itself) and a person disabling
Angelo in Preferences (unregistering takes the wrappers out, and nothing turns it back on).

``angelo_stays_enabled`` holds the add-on for one read its caller starts; with ``keep_loaded=False`` (an earlier
Angelo version's settings, whose copied agent state must load) the add-on reloads and only stays listed.

AI Providers connections are not preferences: their records are ``connections.json`` in Angelo's own data folder
(``storage.connection_directory``, ``connections.ConnectionStore``) and their keys are in the system's key store
(``saved_keys``). No preference read touches either, and the keys already loaded stay in memory with the add-on.
"""

import contextlib
import sys

CORE_ADDONS = ("angelo_agent",)
_blender = None        # Blender's own (disable_all, reset_all), which the wrappers call.
_installed = False
_names = CORE_ADDONS   # The add-ons kept (a test's stand-in names its own).
_keep_loaded = True
_holding = 0           # Reads a caller holds (angelo_stays_enabled): a reload's unregister keeps the wrappers.
# Called after every preference read; a read replaced the person's settings, it did not edit them (studio_assets_host).
READ_LISTENERS = []


def installed():
    return _installed


def install():
    """Wrap Blender's two add-on resets for the session; nothing changes when they are already wrapped."""
    global _blender, _installed
    import addon_utils
    import bpy
    _blender = (addon_utils.disable_all if addon_utils.disable_all is not _disable_all else _blender[0],
                addon_utils.reset_all if addon_utils.reset_all is not _reset_all else _blender[1])
    addon_utils.disable_all, addon_utils.reset_all = _disable_all, _reset_all
    _installed = True
    if _exit_pre not in bpy.app.handlers.exit_pre:
        bpy.app.handlers.exit_pre.append(bpy.app.handlers.persistent(_exit_pre))


def uninstall(force=False):
    """Blender's own functions back; not while a held read reloads the add-on on purpose (unless ``force``)."""
    global _installed
    import addon_utils
    import bpy
    if _holding and not force:
        return
    if _blender is not None:
        if addon_utils.disable_all is _disable_all:
            addon_utils.disable_all = _blender[0]
        if addon_utils.reset_all is _reset_all:
            addon_utils.reset_all = _blender[1]
    _installed = False
    if _exit_pre in bpy.app.handlers.exit_pre:
        bpy.app.handlers.exit_pre.remove(_exit_pre)


def _enabled(name):
    return bool(getattr(sys.modules.get(name), "__addon_enabled__", False))


def _disable_all():
    held = [sys.modules[name] for name in _names if _enabled(name)] if _installed and _keep_loaded else []
    for module in held:
        module.__addon_enabled__ = False   # Not "loaded" for this one pass, so it is not unregistered.
    try:
        _blender[0]()
    finally:
        for module in held:
            module.__addon_enabled__ = True


def _reset_all(**options):
    if _installed:
        import bpy
        addons = bpy.context.preferences.addons
        for name in _names:
            if name not in addons and (_holding or _enabled(name)):
                addons.new().module = name   # No settings yet: the add-on's own defaults.
    _blender[1](**options)
    for listener in list(READ_LISTENERS):
        try:
            listener()
        except Exception as error:  # noqa: BLE001 - one listener never stops the read or the others.
            print("Angelo: a preference-read listener failed:", error)


def _exit_pre(*_args):
    """Quitting is not a preference read: Blender's exit disables Angelo's add-on as before."""
    uninstall(force=True)


@contextlib.contextmanager
def angelo_stays_enabled(names=CORE_ADDONS, keep_loaded=True):
    """For one preference read the caller starts: keep ``names`` listed and (``keep_loaded``) loaded."""
    global _names, _keep_loaded, _holding
    temporary = not _installed
    if temporary:
        install()
    previous = _names, _keep_loaded
    _names, _keep_loaded, _holding = tuple(names), keep_loaded, _holding + 1
    try:
        yield
    finally:
        (_names, _keep_loaded), _holding = previous, _holding - 1
        if temporary:
            uninstall()
