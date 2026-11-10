# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

bl_info = {
    "name": "Angelo Agent Chat", "author": "Hyperbond Studio Pte. Ltd.", "version": (0, 1, 0),
    "blender": (5, 2, 0), "location": "Window > Angelo Chat; Preferences > AI Providers",
    "description": "Native docked/pop-out agent chat with official local runtimes and approved Angelo tools",
    "category": "Interface",
}


def _stamp_time():
    """Record this file's time as ``addon_utils.enable`` does after its own import. Angelo's start-up layout imports
    the package before enabling it (``opted_out`` in the start-up script), as do the GUI suites and Blender's console,
    and ``enable`` reloads a module without ``__time__`` ("module changed on disk ... reloading"): this file ran twice
    at every start-up. A file changed on disk since this import still differs, and is reloaded as before.
    ``addon_utils`` stamps its ``bl_pkg`` the same way."""
    import os
    try:
        return os.path.getmtime(__file__)
    except OSError:
        return 0


__time__ = _stamp_time()


def __getattr__(name):
    # Blender's glTF exporter looks this up on every enabled add-on; it acts only on Angelo's web exports.
    if name == "glTF2ExportUserExtension":
        from .web_export import WebExportExtension
        return WebExportExtension
    raise AttributeError(name)


def register():
    import os, bpy  # noqa: E401
    if bpy.app.background and os.environ.get("ANGELO_HEADLESS") == "1":   # The headless child registers its own.
        return
    import sys
    from . import tls_distrust
    tls_distrust.install()   # First: every TLS connection in the app refuses compromised CA keys (tls_distrust).
    from . import change_clock
    change_clock.prime()   # Windows: which volumes keep coarse times, listed on a thread; no settle waits for it.
    import bpy
    from . import data_migration, product_env, state_migration
    # Before anything opens the account store, Media Studio library or agent state.
    data_migration.migrate_on_startup()
    state_migration.migrate_on_startup(bpy.utils.user_resource('CONFIG'))
    deprecated = product_env.deprecated_in_use()
    if deprecated:
        print("Angelo: deprecated environment variables in use; rename them to ANGELO_*:", ", ".join(deprecated))
    from . import console, layout_hold, studio, timer_jobs, ui
    timer_jobs.register()   # A load or a quit closes every running timer job before Blender's data goes (timer_jobs).
    layout_hold.register()   # A lock whose timer a load discarded is released.
    sys.modules["_console_angelo"] = console
    sys.modules["_console_angelo_genai"] = studio
    ui.register()
    from . import document_identity
    document_identity.register()   # The document's saved id (save_pre) and a failed load (load_post_fail).
    from . import restored_document   # Only a file under the state folder's checkpoints is a Restore's working copy.
    restored_document.set_state_root(ui.state_directory())
    from . import tool_specs
    tool_specs.refresh_worker_count()   # The start tool's count, now that Workers at once is readable (parallel_setting).
    # Registered here, not in ui.py (pinned at its size by test_native_module_size).
    from . import session_rail_input
    session_rail_input.register()   # Concurrent sessions: the session rail and live views (before later overlays).
    from . import about_card, app_notices, font_watch, job_ui, prefs_reload, pycache_retention, settings_import_ui
    prefs_reload.install()   # Reading preferences again (factory, revert, a template) keeps Angelo enabled.
    font_watch.register()    # Rebinds the chat's fonts after Blender reloads its own (a theme preset, Text Rendering).
    app_notices.register()   # Update notices, crash reports and What's New (doc/angelo/updates_and_reports.rst).
    job_ui.register()        # Background jobs: their timer, file events, status bar and operators.
    from . import refused_saves
    refused_saves.register()   # A save another program keeps refusing (Windows) is told on a card.
    about_card.register()    # The About Angelo card (the Angelo menu's About).
    settings_import_ui.register()   # Import Blender Settings… and its undo (File > Defaults, Preferences).
    pycache_retention.schedule()   # Earlier builds' bytecode caches, a minute after startup, on a thread.
    from . import studio_assets_host
    studio_assets_host.register()   # Media Studio as a Blender asset library (a timer; nothing on the startup path).
    from . import asset_shelf_add, chat_launcher, chat_toggle, media_shelf, studio_toggle
    chat_toggle.register()   # Show or hide the chat from the top bar, or Ctrl+Option+N.
    studio_toggle.register()   # Media Studio from Director's top bar and the Angelo menu.
    from . import ai_render_card, render_engines
    render_engines.register()   # The render engine switcher: Director's top bar, Render > Render Engine (1.0.8).
    ai_render_card.register()   # AI render: the camera's view restyled into Media Studio (1.0.8).
    from . import bake_maps_card
    bake_maps_card.register()   # Bake Maps: every PBR map of the selection as a texture set (long_jobs.rst).
    from . import render_region
    render_region.register()   # A region rendered over the last render (doc/angelo/render_region.rst).
    from . import uv_checker
    uv_checker.register()   # The viewport UV checker's toggle, card and + menu row (doc/angelo/uv_checker.rst).
    from . import skeleton_check_ui
    skeleton_check_ui.register()   # Check a rig for a game engine: + menu row and card (doc/angelo/engine_skeleton.rst).
    chat_launcher.register()   # Angelo in the 3D view's corner while the chat is hidden; a click opens it.
    asset_shelf_add.register()   # Generate on every Asset Shelf: Media Studio on the shelf's tab.
    media_shelf.register()   # Object mode's Asset Shelf: models and materials, Media Studio's included.
    from . import media_shelf_cover
    media_shelf_cover.register()   # The shelf's samples while the start-up cover hides the 3D views.
    from . import photo_drop
    photo_drop.register()   # A photo dropped on the 3D view: Angelo's drop card (doc/angelo/photo_drop.rst).
    from . import tension_map
    tension_map.register()   # Tension map: stretch and compression on the 3D view (doc/angelo/tension_map.rst).
    from . import shelf_empty
    shelf_empty.register()   # A shelf that lists nothing says so and offers Media Studio.
    from . import export_baked, web_export_ui, web_shader_ui
    export_baked.register()   # Object.angelo_baked: a target's baked material for Angelo's glTF exports (long_jobs.rst).
    web_shader_ui.register()   # Material.angelo_web_shader and the material's Web Shader panel.
    from . import game_physics_ui
    game_physics_ui.register()   # Game physics: Properties > Physics and its popup (doc/angelo/game_physics.rst).
    web_export_ui.register()   # File > Export > Web (.glb, Three.js) and Live Web Preview (doc/angelo/web_export.rst).
    from . import shot_capture_ui
    shot_capture_ui.register()   # Make Video from This Shot: Render menu, camera menu, chat + menu (long_jobs.rst).
    from . import engine_link_ui, engine_suggest_ui
    engine_link_ui.register()   # The engine live link and its requests (doc/angelo/engine_link.rst).
    engine_suggest_ui.register()   # Suggests the link once when an engine's editor runs (a timer and a thread).
    from . import library_ui
    library_ui.register()   # The Angelo Library: the 3D view's search pill and browser (doc/angelo/library.rst).
    from . import director_select_through
    director_select_through.register()   # Select Through's + menu row (the header and Director draw their own).
    from . import feedback_ui
    feedback_ui.register()   # Report a bug / Request a feature: the + menu's Feedback rows (app_identity.rst).


def unregister():
    import os, bpy  # noqa: E401
    if bpy.app.background and os.environ.get("ANGELO_HEADLESS") == "1":   # The headless child registers its own.
        return
    import sys
    from . import (about_card, app_notices, codex_runtime, font_watch, job_ui, prefs_reload, pycache_retention,
                   settings_import_ui, ui)
    from . import asset_shelf_add, chat_launcher, chat_toggle, media_shelf, studio_toggle, web_export_ui, web_shader_ui
    from . import game_physics_ui
    from . import director_select_through, engine_link_ui, engine_suggest_ui, library_ui, shelf_empty, shot_capture_ui
    from . import feedback_ui
    feedback_ui.unregister()
    director_select_through.unregister()
    engine_suggest_ui.unregister()
    library_ui.unregister()   # Its workers stop; nothing is downloading after the add-on.
    engine_link_ui.unregister()   # Stops the engine link: its server and record never outlive the add-on.
    shot_capture_ui.unregister()
    from . import tension_map
    tension_map.unregister()
    from . import photo_drop
    photo_drop.unregister()   # Gives Blender's own image drop on the 3D view back.
    web_export_ui.unregister()   # Stops a running web preview: its server never outlives the add-on.
    game_physics_ui.unregister()
    web_shader_ui.unregister()
    from . import export_baked
    export_baked.unregister()
    shelf_empty.unregister()
    from . import media_shelf_cover
    media_shelf_cover.unregister()
    media_shelf.unregister()
    asset_shelf_add.unregister()
    from . import ai_render_card, bake_maps_card, render_engines, render_region, skeleton_check_ui, uv_checker
    skeleton_check_ui.unregister()
    bake_maps_card.unregister()
    render_region.unregister()
    uv_checker.unregister()
    ai_render_card.unregister()
    render_engines.unregister()
    studio_toggle.unregister()
    chat_launcher.unregister()
    chat_toggle.unregister()
    prefs_reload.uninstall()   # Disabled (by the person, or at exit): Blender's own add-on resets again.
    pycache_retention.cancel()
    from . import studio_assets_host
    studio_assets_host.unregister()
    settings_import_ui.unregister()
    about_card.unregister()
    job_ui.unregister()
    from . import refused_saves
    refused_saves.unregister()
    app_notices.unregister()
    font_watch.unregister()
    from . import session_rail_input
    session_rail_input.unregister()
    ui.unregister()
    from . import document_identity
    document_identity.unregister()
    codex_runtime.close_all()  # Shared Codex app-servers outlive their chats until the add-on unloads.
    from . import write_turn
    write_turn.close_all(2.0)   # Then the receipts their warm-ups left: nothing writes into the state folder after.
    from . import layout_hold, timer_jobs
    layout_hold.unregister()
    timer_jobs.unregister()   # Last: each module above unregistered its own timers; the jobs left end, aborted.
    sys.modules.pop("_console_angelo", None)
    sys.modules.pop("_console_angelo_genai", None)
