# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where work from outside the person's present input lands in the add-on, for test_native_dialog_consent (the
reviews of c76f2f1 and a6edbf3: B, S2). Every ``bpy.app.timers.register`` site and every main-thread drain of a
worker queue (a ``get_nowait`` consumer) is here: one that applies results reaches the note at the apply-sites named,
``(module, function)`` whose own body calls it; any other says why it lands none. A new one fails the test until it
is classified here. test_native_landing proves the notes come once per result, never per progress tick."""

NOTES = ("grant_gestures.landed", "grant_gestures.agent_changed")

# Timer (file, callback as written, first 40 characters) -> the apply-sites that note it.
LANDS = {("flow_host.py", "pump"): (("flow_runner.py", "advance"), ("studio_generation.py", "pump")),
         ("studio.py", "pump"): (("studio.py", "pump"), ("studio_generation.py", "pump")),
         ("photo_drop_jobs.py", "_watch"): (("photo_drop_jobs.py", "_step"),),
         ("library_host.py", "_tick"): (("library_host.py", "pump"), ("fab_host.py", "pump")),
         ("job_ui.py", "_tick"): (("job_manager.py", "_end"),),
         ("engine_link.py", "_tick"): (("engine_link.py", "_handle"),),
         ("ui.py", "pump"): (("blender_tools.py", "execute"),)}   # Agent tool calls: the agent's change.

# Persistent timers that land nothing, and why.
LANDS_NONE = {
    ("app_notices.py", "tick"): "shows app notices", ("chat_toggle.py", "_watch"): "redraws bars, moves the corner",
    ("chat_view.py", "animate_popovers"): "animation", ("engine_link_ui.py", "_start_remembered"): "starts a link",
    ("engine_suggest_ui.py", "tick"): "scans for engines to suggest", ("font_watch.py", "check"): "reloads fonts",
    ("job_render_video.py", "reap"): "an encoder's end; its job ends in JobManager._end",
    ("job_ui.py", "_startup"): "restores job tags at startup, before any dialog",
    ("job_ui.py", "_keep_pending"): "publishes a stopped render's frames as files",
    ("media_shelf_cover.py", "_tick"): "shelf cover drawing",
    ("parallel_sessions.py", "_tick"): "worker chats' progress; their edits land through the merge tool",
    ("pycache_retention.py", "start"): "a cleanup thread", ("refused_saves.py", "_tell"): "shows a card",
    ("session_rail_input.py", "_watch"): "the rail's input handler and pictures",
    ("studio_assets_host.py", "_tick"): "the asset browser's lists", ("studio_library_ops.py", "_poll_free"):
    "reports freed space", ("thread_start.py", "function"): "retries starting a thread",
    ("update_ui.py", "tick"): "update checks", ("update_ui.py", "_ready_tick"): "update card pace",
    ("web_preview.py", "_tick"): "exports the scene for the preview (reads)",
    ("web_readback.py", "_tick"): "delivers a web export's read-back to its chat (no scene change)",
    ("worker_pool.py", "_timer"): "worker processes ahead of need",
    ("worker_pool.py", "worker_sessions._tick"): "binds workers; their edits land through the merge tool",
    ("worker_queue.py", "worker_sessions._tick"): "binds workers; their edits land through the merge tool"}

# Timers that are not persistent, by why they land nothing.
TIMERS_NONE = {
    "a hidden-window session's life (its saves, its end, its window brought up for the person)": {
        ('headless_window_app.py', 'tick'), ('headless_window_app.py', 'lambda: _bring_up(show, tries - 1) and N')},
    'AI Providers redraw': {('providers_page_endpoints.py', '_follow')},
    'AI Providers sheet and animation': {('providers_page_host.py', '_animate'), ('providers_page_host.py', '_watch_sheet'), ('providers_page_host.py', 'live_windows.deferred(lambda: bpy.contex'), ('providers_page_host.py', 'run')},
    'Preferences redraw': {('external_ui.py', '_redraw')},
    "a bake's next step (its end lands in JobManager._end)": {('job_bake_maps.py', 'self.chain')},
    "a control's repeat while held": {('canvas_controls.py', 'fire')},
    "a look's progress (it ends in JobManager._end)": {('look_notify.py', '_timer')},
    'asks to free space': {('studio_assets_host.py', 'studio_library_ops.ask_free')},
    'builds Studio tabs': {('studio_tab_build.py', 'step')},
    'builds Studio workspaces': {('studio_workspaces.py', '_first_check'), ('studio_workspaces.py', 'live_windows.deferred(lambda: bpy.contex'), ('studio_workspaces.py', 'step')},
    'cards: animation and wake-up': {('card_host.py', '_animate'), ('card_host.py', '_end_nudge'), ('card_host.py', 'run')},
    'chat docking and placement': {('chat_toggle.py', '_dock_when_drawn'), ('chat_toggle.py', 'chat_launcher.update')},
    'crash notices and owed crash state': {('crash_ui.py', '_settle'), ('crash_ui.py', 'check')},
    'frames the view after a merge': {('merge_framing.py', 'attempt'), ('merge_framing.py', 'lambda: _settled(lead_id, held())')},
    'frees data no one uses': {('studio_assets_landing.py', '_free_unused')},
    'hides editor chrome': {('chrome_guard.py', 'lambda: _hide_space(space_pointer) and N'), ('chrome_guard.py', 'lambda: guard_all() and None'), ('chrome_guard.py', 'settle')},
    'hint expiry': {('grant_hints.py', '_expire')},
    'live view redraw': {('live_view_draw.py', 'redraw')},
    'overlay redraw': {('library_actions.py', 'tick')},
    'popover redraw': {('conversations_popover.py', '_tick')},
    'quits a crashed copy': {('crash_gpu.py', 'quit_this_one')},
    'records binding digests': {('export_baked.py', '_record')},
    "runs held work after the person's tool": {('edit_tool_hold.py', 'function')},
    "runs the person's Learn step": {('learn_ui.py', 'lambda: getattr(getattr(bpy.ops, group),')},
    'scrollbar fade': {('chat_scrollbar.py', '_fade')},
    'shelf catalogue and redraw': {('media_shelf.py', 'choose'), ('media_shelf.py', 'run')},
    'shows a card': {('about_card.py', '_show_whats_new')},
    'shows a suggestion card': {('engine_suggest_ui.py', '_pump')},
    "shows a waiting request's card": {('engine_link_ui.py', '_pump')},
    "shows an AI render's result card": {('ai_render_card.py', '_watch_run')},
    'shows the region render card': {('render_region.py', 'lambda: _draw_when_shown(name)'), ('render_region.py', 'show')},
    "the Director bar's clock": {('director.py', '_timer')},
    "the chat's layout": {('chat_paint.py', 'ui._layout_timer')},
    "the chat's layout and closing": {('ui.py', '_layout_timer'), ('ui.py', 'closing')},
    "the chat's layout and the person's presses": {('chat_hide.py', '_apply_presses'), ('chat_hide.py', 'ui._layout_timer')},
    "the composer's drag scroll": {('composer_input.py', 'tick')},
    "the connect prompt's fade": {('connect_hero.py', '_tick')},
    "the installer's poll": {('update_restart_handoff.py', 'restart._poll_installer')},
    "the launcher's blink and placement": {('chat_launcher.py', '_idle'), ('chat_launcher.py', 'live_windows.deferred(lambda: bpy.contex')},
    "the marble's animation": {('marble_host.py', '_tick')},
    "the person's card action after their tool": {('render_region_card.py', 'edit_tool_hold.guarded(lambda: _run(acti')},
    "the person's drop steps": {('photo_drop.py', 'step')},
    "the person's library action after their tool": {('library_ui.py', 'edit_tool_hold.guarded(later, waiting=Tr')},
    "the person's settings import and its cards": {('settings_import_ui.py', '_ask_import'), ('settings_import_ui.py', '_ask_undo'), ('settings_import_ui.py', 'lambda: _show_queued() and None'), ('settings_import_ui.py', 'lambda: after_setup(source, keymap_choic'), ('settings_import_ui.py', 'lambda: function() and None')},
    "the rail's pictures": {('rail_pictures.py', '_tick')},
    "the rail's redraw": {('rail_redraw.py', '_recheck')},
    'the region pills (window UI)': {('render_region_pills.py', 'edit_tool_hold.guarded(run, waiting=True'), ('render_region_pills.py', 'live_windows.deferred(lambda: bpy.contex')},
    'the welcome slides': {('welcome_slides.py', 'live_windows.deferred(lambda: bpy.contex'), ('welcome_slides.py', 'step')},
    'the welcome slideshow': {('intro.py', '_tick'), ('intro.py', 'open_when_ready')},
    'tile previews': {('studio_tile_previews.py', '_tick')},
    'top bar redraw': {('top_bar_anchor.py', 'redraw')},
    "waits for the document to settle before the hand-over": {('update_restart_handoff.py', 'settled')},
    'warms view shaders': {('view_warm.py', 'run'), ('view_warm.py', 'spread')},
    "watches the person's Save As": {('update_restart.py', '_watch_save')},
    'window UI, deferred': {('asset_browser_entry.py', 'live_windows.deferred(lambda: bpy.contex'), ('asset_shelf_add.py', 'live_windows.deferred(lambda: bpy.contex'), ('chat_drop.py', 'live_windows.deferred(lambda: bpy.contex'), ('chat_hint.py', 'live_windows.deferred(lambda: bpy.contex'), ('flow_open.py', 'live_windows.deferred(lambda: bpy.contex'), ('flow_window.py', 'live_windows.deferred(lambda: bpy.contex'), ('layout_after.py', 'live_windows.deferred(lambda: bpy.contex'), ('library_overlay.py', 'live_windows.deferred(lambda: bpy.contex'), ('splash_setup.py', 'live_windows.deferred(lambda: bpy.contex'), ('studio.py', 'live_windows.deferred(lambda: bpy.contex'), ('studio_toggle.py', 'live_windows.deferred(lambda: bpy.contex')},
}

# Main-thread drains of worker queues (file, function) -> the apply-sites that note them, or why they land none.
DRAINS_LAND = {("library_host.py", "pump"): (("library_host.py", "pump"),),
               ("studio.py", "pump"): (("studio.py", "pump"),),
               ("studio_commits.py", "pump"): (("studio_generation.py", "pump"),),
               ("controller.py", "_pump_tools"): (("blender_tools.py", "execute"),),
               ("studio_agent.py", "execute"): (("blender_tools.py", "execute"),)}
DRAINS_NONE = {
    ("external_requests.py", "take"): "status, session new and an app's messages to Angelo's agent: its turns land "
                                      "through the chat's own tool pump (controller._pump_tools), never here",
    ("headless_window_app.py", "step"): "the hidden session's launcher messages (close, show, a decline) and signals",
    ("api_connections.py", "pump"): "a connection's check result (Preferences)",
    ("authentication.py", "pump"): "a sign-in's result", ("catalogs.py", "pump"): "model catalogues",
    ("controller_recovery.py", "rebind_after_restore"): "drops stale tool results after a restore",
    ("edit_tool_hold.py", "next_event"): "the person's own held input, replayed",
    ("engine_link_writeback.py", "finished"): "an engine-side save's completion (files in its project)",
    ("guard_work.py", "shutdown"): "drains at shutdown",
    ("memory_project_ui.py", "clear"): "task memory panel", ("memory_project_ui.py", "pump"): "task memory panel",
    ("saved_keys.py", "pump"): "saved keys' status", ("snapshot_service.py", "pump"): "checkpoint notices",
    ("web_readback.py", "_tick"): "a web export's read-back for its chat",
    ("worker_mirror_pump.py", "pump"): "a worker chat's mirror; its edits land through the merge tool"}
