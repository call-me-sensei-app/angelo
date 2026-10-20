# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

bl_info = {
    "name": "Angelo Agent Chat", "author": "Hyperbond Studio Pte. Ltd.", "version": (0, 1, 0),
    "blender": (5, 2, 0), "location": "Window > Angelo Chat; Preferences > AI Providers",
    "description": "Native docked/pop-out agent chat with official local runtimes and approved Blender tools",
    "category": "Interface",
}


def register():
    import sys
    import bpy
    from . import data_migration, product_env, state_migration
    # Before anything opens the account store, Media Studio library or agent state.
    data_migration.migrate_on_startup()
    state_migration.migrate_on_startup(bpy.utils.user_resource('CONFIG'))
    deprecated = product_env.deprecated_in_use()
    if deprecated:
        print("Angelo: deprecated environment variables in use; rename them to ANGELO_*:", ", ".join(deprecated))
    from . import console, studio, ui
    sys.modules["_console_angelo"] = console
    sys.modules["_console_angelo_genai"] = studio
    ui.register()
    # Registered here, not in ui.py (pinned at its size by test_native_module_size).
    from . import about_card, app_notices, font_watch, job_ui, prefs_reload, pycache_retention, settings_import_ui
    prefs_reload.install()   # Reading preferences again (factory, revert, a template) keeps Angelo enabled.
    font_watch.register()    # Rebinds the chat's fonts after Blender reloads its own (a theme preset, Text Rendering).
    app_notices.register()   # Update notices, crash reports and What's New (doc/angelo/updates_and_reports.rst).
    job_ui.register()        # Background jobs: their timer, file events, status bar and operators.
    about_card.register()    # The About Angelo card (the Angelo menu's About).
    settings_import_ui.register()   # Import Blender Settings… and its undo (File > Defaults, Preferences).
    pycache_retention.schedule()   # Earlier builds' bytecode caches, a minute after startup, on a thread.
    from . import render_device
    render_device.register(ui.state_directory())   # Cycles on the GPU when one exists (doc/angelo/long_jobs.rst).
    from . import chat_toggle
    chat_toggle.register()   # Show or hide the chat from the top bar, or Ctrl+Option+N.


def unregister():
    import sys
    from . import (about_card, app_notices, codex_runtime, font_watch, job_ui, prefs_reload, pycache_retention,
                   settings_import_ui, ui)
    from . import chat_toggle, render_device
    chat_toggle.unregister()
    render_device.unregister()
    prefs_reload.uninstall()   # Disabled (by the person, or at exit): Blender's own add-on resets again.
    pycache_retention.cancel()
    settings_import_ui.unregister()
    about_card.unregister()
    job_ui.unregister()
    app_notices.unregister()
    font_watch.unregister()
    ui.unregister()
    codex_runtime.close_all()  # Shared Codex app-servers outlive their chats until the add-on unloads.
    sys.modules.pop("_console_angelo", None)
    sys.modules.pop("_console_angelo_genai", None)
