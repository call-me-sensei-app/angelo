# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Brings earlier settings into the running app (``settings_import`` decides what and copies it).

Main thread only. ``run`` copies, has Blender read the copied preferences, keeps what Angelo needs and saves:

* **Angelo stays enabled.** Reading preferences makes Blender disable every add-on and then enable the ones the
  new preferences list. A stock Blender's preferences do not list ``angelo_agent``, so ``prefs_reload`` (every
  preference read, not only this one) leaves it out of the disabling and lists it before the enabling: the chat,
  its sessions and any card stay as they are. An earlier Angelo's import is Blender's own
  (``preferences.copy_prev``): the earlier folder whole except the agent state, which ``version_carry_over`` moved
  when the add-on registered (``settings_import.previous_ignore``), and it reloads the start-up file. There Angelo's
  add-on is only kept listed (``keep_loaded=False``), so it reloads and binds its chat to the reloaded file.
* **What the person chose in Angelo is kept** (``Kept``): the language (and its translation switches), the exact
  theme (the setup card's look; captured as Blender's own theme preset XML and put back without changing which
  preset the menu names), Angelo's own add-on preferences, and the setup card's mouse and Spacebar choices when
  they were changed there.
* **A failed add-on is reported, never fatal.** Blender prints its traceback; ``failed_addons`` names every add-on
  the preferences enable that did not load, for one calm line, and ``outcome["causes"]`` gives why for each that
  raised (``angelo_safe_start.causes``). It stays enabled: the failed add-ons' own notice offers to turn it off.

``undo`` puts back the backup the last import made (the menu's Undo Settings Import), with the same protections.
"""

import os
import sys
from pathlib import Path

import bpy

from . import owned_paths, private_roots
from . import settings_import as si
from .prefs_reload import CORE_ADDONS, angelo_stays_enabled

BACKUP_FOLDER = si.BACKUP_FOLDER   # In Angelo's per-version folder; one import is kept.
LANGUAGE_SETTINGS = ("language", "use_translate_interface", "use_translate_tooltips", "use_translate_reports",
                     "use_translate_new_dataname")
THEME_MENU = "USERPREF_MT_interface_theme_presets"


def destinations():
    """Angelo's own folders for each part (``BLENDER_USER_*`` overrides included) and its per-version folder."""
    found = {part: Path(bpy.utils.user_resource(part.upper())) for part in si.blender_folders.PARTS}
    found[si.ROOT] = Path(bpy.utils.resource_path('USER'))
    return found


def backup_folder():
    return Path(bpy.utils.resource_path('USER')) / BACKUP_FOLDER


def find_source(include_angelo=True):
    """``(Source or None, reason)`` for this app. ``include_angelo``: an earlier Angelo version's folder wins (the
    first-launch card); the menu's import brings Blender's only."""
    previous = None
    if include_angelo:
        copy_prev = bpy.types.PREFERENCES_OT_copy_prev
        previous = copy_prev.previous_version() if copy_prev.poll(bpy.context) else None
    return si.find_source(sys.platform, os.path.expanduser("~"), os.environ, bpy.app.version[:2], previous)


def can_undo():
    return si.backup_matches(backup_folder(), destinations()) is not None


class Kept:
    """What the person chose in Angelo, captured before an import and put back after it."""

    def __init__(self, keymap_choices=None):
        prefs = bpy.context.preferences
        self.language = {name: getattr(prefs.view, name) for name in LANGUAGE_SETTINGS if hasattr(prefs.view, name)}
        self.keymap_choices = dict(keymap_choices or {})
        self.addon_settings = {name: _set_values(prefs.addons[name].preferences) for name in CORE_ADDONS
                               if name in prefs.addons and prefs.addons[name].preferences is not None}
        self.folder = private_roots.TemporaryDirectory("angelo-kept-")
        self.theme = Path(self.folder.name) / "Angelo theme.xml"
        menu = getattr(bpy.types, THEME_MENU)
        self.theme_label, self.theme_file = menu.bl_label, prefs.themes[0].filepath
        import _rna_xml as rna_xml
        rna_xml.xml_file_write(bpy.context, str(self.theme), menu.preset_xml_map)

    def restore(self):
        prefs = bpy.context.preferences
        menu = getattr(bpy.types, THEME_MENU)
        try:
            bpy.ops.script.execute_preset(filepath=str(self.theme), menu_idname=THEME_MENU)
        finally:
            # The menu (and the setup card's look) still names the chosen look, and the theme its own preset file.
            menu.bl_label, prefs.themes[0].filepath = self.theme_label, self.theme_file
            self.folder.cleanup()
        for name, value in self.language.items():
            setattr(prefs.view, name, value)
        keyconfig = bpy.context.window_manager.keyconfigs.active.preferences
        for name, value in self.keymap_choices.items():
            if keyconfig is not None and hasattr(keyconfig, name):
                setattr(keyconfig, name, value)
        for module, values in self.addon_settings.items():
            entry = prefs.addons.get(module)
            for name, value in values.items():
                try:
                    setattr(entry.preferences, name, value)
                except (AttributeError, TypeError, ValueError, RuntimeError):
                    pass   # A setting with its own guard (Always allow scripts) keeps its own storage.


def _set_values(settings):
    """The add-on settings the person (or the app) set, as plain values."""
    values = {}
    for prop in settings.bl_rna.properties:
        name = prop.identifier
        if name == "rna_type" or prop.is_readonly or prop.type in {'POINTER', 'COLLECTION'}:
            continue
        if settings.is_property_set(name):
            value = getattr(settings, name)
            values[name] = tuple(value) if prop.type != 'STRING' and getattr(prop, "is_array", False) else value
    return values


def failed_addons():
    """Display names of the add-ons the preferences enable that did not load."""
    return [name for _module, name in _failed_entries()]


def _failed_entries():
    """``(module, display name)`` of each add-on the preferences enable that did not load."""
    import addon_utils
    modules = {module.__name__: module for module in addon_utils.modules(refresh=False)}
    found = []
    for entry in bpy.context.preferences.addons:
        if not addon_utils.check(entry.module)[1]:
            module = modules.get(entry.module)   # None when the add-on is not installed at all.
            found.append((entry.module,
                          (addon_utils.module_bl_info(module).get("name") if module else None) or entry.module))
    return found


def _causes(failed):
    """``{display name: cause}`` of the failed add-ons that raised as they loaded (``angelo_safe_start``)."""
    try:
        import angelo_safe_start
        causes = angelo_safe_start.causes()
    except Exception:   # A cause is a courtesy: the names stand.
        return {}
    return {name: causes[module] for module, name in failed if module in causes}


def _reload(read_preferences):
    if read_preferences:
        with angelo_stays_enabled():
            bpy.ops.wm.read_userpref()
    bpy.ops.wm.read_history()
    bpy.ops.wm.operator_presets_cleanup()   # As Blender's own import does: presets that kept a file path.
    bpy.context.preferences.studio_lights.refresh()   # MatCaps, HDRIs and studio lights brought along.


def run(source, keymap_choices=None):
    """Import ``source``; returns the outcome (a dict the notice and fixtures read)."""
    kept = Kept(keymap_choices)
    outcome = {"app": source.app, "version": source.version_label, "copied": 0, "errors": [], "failed": [],
               "summary": {}, "restart": False, "reloaded_layout": False}
    try:
        if source.app == si.ANGELO:
            # An Angelo upgrade: Blender's own import, unchanged (it may reload the start-up file). Angelo's add-on
            # reloads with the copied agent state; it only has to stay listed.
            # Blender reloads the start-up file only over no work of the person's (PREFERENCES_OT_copy_prev).
            outcome["reloaded_layout"] = bpy.data.is_saved is bpy.data.is_dirty is False
            with angelo_stays_enabled(keep_loaded=False):
                bpy.ops.preferences.copy_prev()
        else:
            places = destinations()
            items = si.plan(source, places)
            outcome["summary"] = si.summary(items, places)
            with owned_paths.vouched_during(places.values()):   # Blender's own folders, for this import only.
                result = si.copy(items, backup_folder(), source, places)
            outcome.update(copied=result.copied, errors=result.errors, restart=outcome["summary"]["restart"])
            _reload(outcome["summary"]["preferences"])
    finally:
        kept.restore()
    failed = _failed_entries()
    outcome["failed"] = [name for _module, name in failed]
    outcome["causes"] = _causes(failed)
    for name in outcome["failed"]:
        print("Angelo: an imported add-on did not load:", name)
    for path, message in outcome["errors"]:
        print("Angelo: not imported:", path, "-", message)
    bpy.ops.wm.save_userpref()
    return outcome


def undo():
    """Put back the settings from before the last import; returns ``{"restored", "errors"}``."""
    kept, result = Kept(), si.Result()
    try:
        places = destinations()
        with owned_paths.vouched_during(places.values()):   # Blender's own folders, for this Undo only.
            result = si.undo(backup_folder(), places)
        _reload(True)
    finally:
        kept.restore()
    bpy.ops.wm.save_userpref()
    for path, message in result.errors:
        print("Angelo: not restored:", path, "-", message)
    return {"restored": result.copied, "errors": result.errors}


def agent_busy():
    """An agent turn is running: an import waits until it ends (its add-ons and preferences would change under it)."""
    from . import ui
    return any(controller.busy for controller in ui._controllers.values())
