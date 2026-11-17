# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every main-thread callback of the add-on is guarded or reviewed: the person's operation is never touched.

Angelo's own work reaches Blender's data from timers and app handlers (and the UI pump's stages). Each one either
goes through the one choke point, ``edit_tool_hold.guarded`` (it waits while the person's operation is in progress
and claims the modal operators it starts as Angelo's), or is reviewed here as touching no data the person's operation
edits, with the reason. ``edit_tool_inventory.sites`` finds them all by AST; a new one fails
``test_every_callback_is_guarded_or_reviewed`` until it is guarded or reviewed, and a reviewed one that went away
fails as stale. Break-it re-review 2026-11-13 (F1, F2, S5): a pending import's poll and the texture-set bake chain
escaped a pin of named routes.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import unittest

from edit_tool_inventory import modals, sites

UI = "draws or animates Angelo's own interface (cards, chat, rail, popovers, notices); reads no scene data"
INPUT = "starts Angelo's own canvas input handler (a passive modal) in a window once it is drawn"
LAYOUT = ("lays out Angelo's own areas or workspaces after the person's click or at start-up; a layout change waits "
          "for interactions itself (layout_hold, layout_blocking)")
CLICK = "follows the person's own click or drop on Angelo's interface: their act, in a window they are using"
PROCESS = "a process, thread, file or network step; touches no Blender data"
LIFECYCLE = ("runs at a file load, save, undo, redo or exit, none of which happens during an operation in progress "
             "(Blender's autosave waits by the same rule; the person's own Save or Undo ends theirs first)")
MARK = "a depsgraph handler that only marks: it reads the update list (ids and flags), never object or edit data"
EDIT_MARK = ("a live preview's edit notice (scene_changes.edited): runs as Angelo's own tool finishes and only notes "
             "the time; reads no object or edit data")
CHILD = "runs in a child Blender (a render, encode or headless worker), which has no person's window"
IDLE = "frames or draws only while its window runs no interaction (its own tool_running or interaction_active check)"
PUMP = "the UI pump: each stage that touches data holds itself (test_native_edit_tool_paths, PUMP_STAGES)"
SELF = "edit_tool_hold's own retry: it runs its function only once nothing is in progress"

DRAW = ("a draw callback: draws Angelo's own overlay from its own state; never reads object or edit data (no drawing "
        "module imports scene_queries, scene_geometry or bmesh)")
CARD = "a card's own poll or close: reads the card's own state or a job's progress record; no scene data"
BAKE_END = "marks a bake's end; the next step runs from the guarded chain or the guarded job tick"
SPLIT = ("ticks once (edit_tool_hold.ticked) and keeps its interface moving; its data stages hold "
         "(test_native_edit_tool_paths, test_split_ticks_keep_their_interface_and_hold_their_data)")
STAGE = "keeps Angelo's private stage scenes from evaluating; touches only those scenes"

REVIEWED = {
    ('about_card.py', 'AboutCard.act', 'timer', '_show_whats_new'): UI,
    ('ai_render_card.py', 'AiRenderCard._render', 'timer', '_watch_run'): UI,
    ('app_notices.py', 'register', 'timer', 'tick'): UI,
    ('asset_browser_entry.py', '_start_input', 'timer', 'live_windows.deferred(lambda: bpy.context.window_manager, pointer, start)'): INPUT,
    ('asset_shelf_add.py', '_ensure_input', 'timer', 'live_windows.deferred(lambda: bpy.context.window_manager, pointer, start)'): INPUT,
    ('asset_shelf_add.py', 'activate', 'timer', 'live_windows.deferred(lambda: bpy.context.window_manager, window_pointer, run, s'): CLICK,
    ('asset_shelf_add.py', 'register', 'handler:load_post', '_load_post'): LIFECYCLE,
    ('canvas_controls.py', '_redraw_later', 'timer', 'fire'): UI,
    ('grant_hints.py', 'show', 'draw', '_draw'): UI,
    ('grant_hints.py', 'show', 'timer', '_expire'): UI,
    ('card_host.py', '_wake', 'timer', '_end_nudge'): UI,
    ('card_host.py', 'register', 'handler:load_pre', '_load_pre'): LIFECYCLE,
    ('card_host.py', 'Host.after', 'timer', 'run'): UI,
    ('card_host.py', '_redraw', 'timer', '_animate'): UI,
    ('chat_drop.py', 'defer_console_drop', 'timer', 'live_windows.deferred(lambda: bpy.context.window_manager, window_pointer, attach'): CLICK,
    ('chat_hide.py', 'request_hide', 'timer', 'ui._layout_timer'): LAYOUT,
    ('chat_hide.py', 'after_move', 'timer', '_apply_presses'): LAYOUT,
    ('chat_hint.py', 'ChatHint.act', 'timer', 'live_windows.deferred(lambda: bpy.context.window_manager, pointer, toggle, settl'): CLICK,
    ('chat_launcher.py', '_open', 'timer', 'live_windows.deferred(lambda: bpy.context.window_manager, pointer, run, settle=T'): UI,
    ('chat_launcher.py', 'register', 'handler:load_post', '_load_post'): LIFECYCLE,
    ('chat_launcher.py', 'update', 'timer', '_idle'): UI,
    ('chat_paint.py', 'finish_dock', 'timer', 'ui._layout_timer'): LAYOUT,
    ('chat_scrollbar.py', '_fade_later', 'timer', '_fade'): UI,
    ('chat_toggle.py', 'register', 'handler:load_post', 'loaded'): LIFECYCLE,
    ('chat_toggle.py', 'loaded', 'timer', 'chat_launcher.update'): LAYOUT,
    ('chat_toggle.py', 'register', 'timer', '_watch'): LAYOUT,
    ('chat_toggle.py', 'follow', 'timer', '_dock_when_drawn'): LAYOUT,
    ('chat_view.py', 'register', 'timer', 'animate_popovers'): UI,
    ('chrome_guard.py', 'guard', 'timer', 'settle'): LAYOUT,
    ('chrome_guard.py', 'register', 'handler:load_post', '_loaded'): LIFECYCLE,
    ('chrome_guard.py', '_changed', 'timer', 'lambda: _hide_space(space_pointer) and None'): LAYOUT,
    ('chrome_guard.py', '_loaded', 'timer', 'lambda: guard_all() and None'): LAYOUT,
    ('composer_input.py', '_start_autoscroll', 'timer', 'tick'): UI,
    ('connect_hero.py', 'watch', 'timer', '_tick'): UI,
    ('conversations_popover.py', '_keep_drawing', 'timer', '_tick'): UI,
    ('crash_gpu.py', 'relaunch', 'timer', 'quit_this_one'): CLICK,
    ('crash_ui.py', '_send_automatically', 'timer', 'check'): UI,
    ('crash_ui.py', '_settle_later', 'timer', '_settle'): UI,
    ('director.py', 'request_mode', 'timer', '_timer'): LAYOUT,
    ('director.py', 'register', 'handler:load_pre', 'load_pre'): LIFECYCLE,
    ('edit_tool_hold.py', '_later', 'timer', 'function'): SELF,
    ('engine_link.py', 'register', 'handler:exit_pre', '_exit_pre'): LIFECYCLE,
    ('engine_link_ui.py', 'register', 'timer', '_start_remembered'): PROCESS,
    ('engine_link_ui.py', '_show_request', 'timer', '_pump'): UI,
    ('engine_suggest_ui.py', '_suggest', 'timer', '_pump'): UI,
    ('engine_suggest_ui.py', 'register', 'timer', 'tick'): UI,
    ('external_ui.py', '_in_background.run', 'timer', '_redraw'): UI,
    ('flow_capture.py', 'register', 'handler:depsgraph_update_post', '_scene_updated'): MARK,
    ('flow_host.py', 'register', 'handler:load_pre', '_load_pre'): LIFECYCLE,
    ('flow_menu.py', 'register', 'handler:load_pre', '_load_pre'): LIFECYCLE,
    ('flow_open.py', 'frame_when_drawn', 'timer', 'live_windows.deferred(lambda: bpy.context.window_manager, pointer, tick)'): LAYOUT,
    ('flow_open.py', 'show_on_tab', 'timer', 'live_windows.deferred(lambda: bpy.context.window_manager, pointer, assign)'): LAYOUT,
    ('flow_window.py', '_start_input', 'timer', 'live_windows.deferred(lambda: bpy.context.window_manager, pointer, start)'): INPUT,
    ('flow_window.py', 'register', 'handler:load_post', 'bpy.app.handlers.persistent(_load_post)'): LIFECYCLE,
    ('font_watch.py', 'register', 'timer', 'check'): UI,
    ('headless_main.py', '_register', 'handler:load_pre', 'ui.document_load_pre'): CHILD,
    ('headless_main.py', '_register', 'handler:load_post', 'after_load'): CHILD,
    ('headless_main.py', '_register', 'handler:depsgraph_update_post', 'track_scene_changes'): CHILD,
    ('headless_main.py', '_serve', 'handler:save_post', 'after_save'): CHILD,
    ('headless_working_copy.py', 'open_read', 'handler:save_post', 'note'): CHILD,
    ('intro.py', 'schedule_first_launch', 'timer', 'open_when_ready'): UI,
    ('intro.py', 'ANGELO_OT_intro.invoke', 'timer', '_tick'): UI,
    ('job_ui.py', 'register', 'handler:load_pre', '_load_pre'): LIFECYCLE,
    ('job_ui.py', 'register', 'handler:load_post', '_load_post'): LIFECYCLE,
    ('job_ui.py', 'register', 'handler:undo_post', '_undo_post'): LIFECYCLE,
    ('job_ui.py', 'register', 'handler:redo_post', '_undo_post'): LIFECYCLE,
    ('layout_after.py', 'run_after', 'timer', 'live_windows.deferred(lambda: bpy.context.window_manager, pointer, wait)'): LAYOUT,
    ('learn_ui.py', 'LearnCard.act', 'timer', "lambda: getattr(getattr(bpy.ops, group), name)('INVOKE_DEFAULT', True, **dict(ro"): CLICK,
    ('library_actions.py', '_watch_progress', 'timer', 'tick'): UI,
    ('library_overlay.py', '_ensure_input', 'timer', 'live_windows.deferred(lambda: bpy.context.window_manager, pointer, start)'): INPUT,
    ('library_overlay.py', 'register', 'handler:load_post', '_load_post'): LIFECYCLE,
    ('live_view.py', 'register', 'handler:exit_pre', '_exit_pre'): LIFECYCLE,
    ('live_view_draw.py', 'held', 'timer', 'redraw'): UI,
    ('look_notify.py', 'watch', 'timer', '_timer'): UI,
    ('marble_host.py', 'poll', 'timer', '_tick'): UI,
    ('media_shelf.py', 'show_studio_tab', 'timer', 'choose'): UI,
    ('media_shelf.py', '_redraw_later', 'timer', 'run'): UI,
    ('media_shelf.py', '_defer', 'timer', 'run'): UI,
    ('media_shelf.py', 'register', 'handler:load_post', '_load_post'): LIFECYCLE,
    ('media_shelf_cover.py', 'register', 'handler:load_post', '_load_post'): LIFECYCLE,
    ('media_shelf_cover.py', 'start', 'timer', '_tick'): UI,
    ('merge_framing.py', 'after_merge', 'timer', 'attempt'): IDLE,
    ('merge_framing.py', 'after_merge.attempt', 'timer', 'lambda: _settled(lead_id, held())'): IDLE,
    ('photo_drop.py', 'Drop.attach', 'timer', 'step'): CLICK,
    ('photo_drop_jobs.py', 'register', 'handler:load_pre', '_load_pre'): LIFECYCLE,
    ('prefs_reload.py', 'install', 'handler:exit_pre', 'bpy.app.handlers.persistent(_exit_pre)'): LIFECYCLE,
    ('providers_page_endpoints.py', 'ANGELO_OT_endpoint_test.execute', 'timer', '_follow'): UI,
    ('providers_page_host.py', 'register', 'handler:load_pre', '_load_pre'): LIFECYCLE,
    ('providers_page_host.py', '_SheetHost.after', 'timer', 'run'): UI,
    ('providers_page_host.py', 'open_sheet', 'timer', '_watch_sheet'): UI,
    ('providers_page_host.py', 'draw_panel', 'timer', 'live_windows.deferred(lambda: bpy.context.window_manager, pointer, _start_input)'): INPUT,
    ('providers_page_host.py', '_draw_themed', 'timer', '_animate'): UI,
    ('providers_page_host.py', '_draw_themed', 'timer', '_animate'): UI,
    ('pycache_retention.py', 'schedule', 'timer', 'start'): PROCESS,
    ('rail_redraw.py', 'tick', 'timer', '_recheck'): UI,
    ('refused_saves.py', 'register', 'timer', '_tell'): UI,
    ('render_child.py', 'main', 'handler:render_post', 'report_memory'): CHILD,
    ('render_child.py', 'main', 'handler:render_post', 'report_kernels'): CHILD,
    ('render_child.py', 'main', 'handler:render_stats', 'report_progress'): CHILD,
    ('render_child.py', 'main', 'handler:render_pre', 'lambda *_args: say(progress=0.0)'): CHILD,
    ('render_region.py', '_render_ended', 'timer', 'show'): UI,
    ('render_region.py', 'ANGELO_OT_render_region.execute', 'timer', 'lambda: _draw_when_shown(name)'): UI,
    ('render_region_pills.py', '_ensure_input', 'timer', 'live_windows.deferred(lambda: bpy.context.window_manager, pointer, start)'): INPUT,
    ('render_region_pills.py', 'register', 'handler:load_post', '_load_post'): LIFECYCLE,
    ('session_rail_input.py', 'register', 'handler:load_post', '_document_loaded'): LIFECYCLE,
    ('session_rail_input.py', 'register', 'handler:load_post', '_load_post'): LIFECYCLE,
    ('session_rail_input.py', 'register', 'handler:depsgraph_update_post', '_depsgraph_changed'): MARK,
    ('session_rail_input.py', 'register', 'timer', '_watch'): UI,
    ('settings_import_ui.py', '_later', 'timer', 'lambda: function() and None'): CLICK,
    ('settings_import_ui.py', '_on_notice', 'timer', '_ask_undo'): CLICK,
    ('settings_import_ui.py', 'after_setup', 'timer', 'lambda: after_setup(source, keymap_choices, welcome) and None'): CLICK,
    ('settings_import_ui.py', 'ANGELO_OT_import_blender_settings.execute', 'timer', '_ask_import'): CLICK,
    ('settings_import_ui.py', 'ANGELO_OT_undo_settings_import.execute', 'timer', '_ask_undo'): CLICK,
    ('settings_import_ui.py', '_ask_import.confirmed', 'timer', 'lambda: _show_queued() and None'): CLICK,
    ('settings_import_ui.py', '_ask_undo.confirmed', 'timer', 'lambda: _show_queued() and None'): CLICK,
    ('splash_setup.py', 'after_close', 'timer', 'live_windows.deferred(lambda: bpy.context.window_manager, pointer, run)'): CLICK,
    ('splash_setup.py', '_redirect', 'timer', 'live_windows.deferred(lambda: bpy.context.window_manager, pointer, run)'): CLICK,
    ('splash_setup.py', 'register', 'handler:save_post', '_remember_scene_chat'): LIFECYCLE,
    ('studio.py', 'defer_drop', 'timer', 'live_windows.deferred(lambda: bpy.context.window_manager, wp, finish)'): CLICK,
    ('studio_assets_host.py', 'register', 'handler:load_factory_preferences_post', 'preferences_read'): LIFECYCLE,
    ('studio_tab_build.py', 'run', 'timer', 'step'): LAYOUT,
    ('studio_toggle.py', '_select_tab_when_shown', 'timer', 'live_windows.deferred(lambda: bpy.context.window_manager, pointer, select)'): LAYOUT,
    ('studio_views.py', '_watch', 'handler:load_pre', '_forget'): LIFECYCLE,
    ('studio_workspaces.py', 'ensure', 'timer', 'live_windows.deferred(lambda: bpy.context.window_manager, pointer, wait_then_bui'): LAYOUT,
    ('studio_workspaces.py', 'add_tab', 'timer', 'step'): LAYOUT,
    ('studio_workspaces.py', 'register', 'handler:load_post', '_loaded'): LIFECYCLE,
    ('studio_workspaces.py', 'register', 'timer', '_first_check'): LAYOUT,
    ('studio_workspaces.py', 'ensure.wait_then_build.retry', 'timer', 'live_windows.deferred(lambda: bpy.context.window_manager, pointer, wait_then_bui'): LAYOUT,
    ('thread_start.py', 'daemon_or_later', 'timer', 'function'): PROCESS,
    ('top_bar_anchor.py', '_settle_anchor_later', 'timer', 'redraw'): UI,
    ('ui.py', 'request_chat_dock', 'timer', '_layout_timer'): LAYOUT,
    ('ui.py', 'register', 'handler:load_pre', 'document_load_pre'): LIFECYCLE,
    ('ui.py', 'register', 'handler:load_post', 'document_load_post'): LIFECYCLE,
    ('ui.py', 'register', 'handler:save_post', 'document_save_post'): LIFECYCLE,
    ('ui.py', 'register', 'handler:undo_post', 'workspace_undo_post'): LIFECYCLE,
    ('ui.py', 'register', 'handler:redo_post', 'workspace_redo_post'): LIFECYCLE,
    ('ui.py', 'register', 'handler:depsgraph_update_post', 'track_scene_changes'): MARK,
    ('ui.py', 'register', 'timer', 'pump'): PUMP,
    ('ui.py', 'ANGELO_OT_return_dock.execute.close_source', 'timer', 'closing'): LAYOUT,
    ('update_restart.py', '_after_prompt', 'timer', '_watch_save'): PROCESS,
    ('update_restart_handoff.py', 'launch', 'timer', 'restart._poll_installer'): PROCESS,
    ('update_ui.py', 'register', 'timer', 'tick'): UI,
    ('update_ui.py', 'register', 'timer', '_ready_tick'): UI,
    ('uv_checker_card.py', 'UVCheckerCard.opened', 'handler:depsgraph_update_post', '_mark_stale'): MARK,
    ('video_encode_child.py', 'encode', 'handler:frame_change_pre', 'check_parent'): CHILD,
    ('view_warm.py', 'soon', 'timer', 'run'): IDLE,
    ('view_warm.py', '_spread', 'timer', 'spread'): IDLE,
    ('web_preview.py', 'register', 'handler:exit_pre', '_exit_pre'): LIFECYCLE,
    ('web_preview.py', 'start', 'helper:scene_changes.watch', '_on_depsgraph'): MARK,
    ('web_preview.py', 'start', 'helper:scene_changes.on_edit', '_on_edit'): EDIT_MARK,
    ('web_world_key.py', '_watch', 'handler:depsgraph_update_post', 'handler'): MARK,
    ('welcome_slides.py', 'open_feature', 'timer', 'live_windows.deferred(lambda: bpy.context.window_manager, pointer, run, settle=T'): LAYOUT,
    ('welcome_slides.py', '_open_chat', 'timer', 'step'): LAYOUT,
    ('welcome_slides.py', '_open_assets', 'timer', 'step'): LAYOUT,
    ('ai_render_card.py', 'AiRenderCard._render', 'helper:host.after', 'lambda: _poll(self, host)'): CARD,
    ('asset_browser_entry.py', 'register', 'draw', '_draw'): DRAW,
    ('asset_shelf_add.py', 'register', 'draw', '_draw'): DRAW,
    ('bake_maps_card.py', 'BakeMapsCard._bake', 'helper:host.after', 'lambda: _poll(self, host)'): CARD,
    ('card_host.py', 'ANGELO_OT_card_host.invoke', 'draw', '_draw'): DRAW,
    ('chat_launcher.py', '_subscribe', 'msgbus', '_update_safely'): LAYOUT,
    ('chat_launcher.py', 'register', 'draw', '_pre_view'): DRAW,
    ('chat_launcher.py', 'register', 'draw', '_draw'): DRAW,
    ('chat_paint.py', 'finish_dock', 'draw', '_record'): DRAW,
    ('chat_toggle.py', 'subscribe', 'msgbus', '_follow_safely'): LAYOUT,
    ('chat_view.py', 'register', 'draw', 'draw'): DRAW,
    ('chrome_guard.py', 'guard_all', 'msgbus', '_shown'): LAYOUT,
    ('chrome_guard.py', 'register', 'msgbus', '_shown'): LAYOUT,
    ('chrome_guard.py', 'guard', 'msgbus', '_changed'): LAYOUT,
    ('connect_hero.py', '_show', 'draw', '_draw'): DRAW,
    ('crash_card.py', 'CrashCard._poll', 'helper:host.after', 'check'): CARD,
    ('edit_tool_hold.py', 'deferred', 'helper:_later', 'retry'): SELF,
    ('engine_link.py', '_listen', 'handler-loop', '_HANDLERS'): MARK,
    ('engine_link_follow.py', 'listen', 'helper:scene_changes.watch', '_on_depsgraph'): MARK,
    ('engine_link_follow.py', 'listen', 'helper:scene_changes.on_edit', '_on_edit'): EDIT_MARK,
    ('endpoint_sheet.py', 'EndpointSheet._follow', 'helper:host.after', 'lambda: self._follow(host)'): CARD,
    ('engine_status.py', 'EngineStatusCard.opened', 'helper:host.after', 'lambda: _poll(self, host)'): CARD,
    ('engine_suggest_ui.py', 'SuggestionCard.opened', 'helper:host.after', "lambda: host.close('stale')"): CARD,
    ('flow_menu.py', 'ANGELO_OT_flow_menu.invoke', 'draw', '_draw'): DRAW,
    ('flow_window.py', 'prepare_area', 'draw', '_draw'): DRAW,
    ('flow_window.py', 'register', 'draw', '_draw'): DRAW,
    ('intro.py', 'ANGELO_OT_intro.invoke', 'draw', '_draw'): DRAW,
    ('job_bake_maps.py', 'TextureSetRunner._start', 'handler-loop', 'self.handlers'): BAKE_END,
    ('job_bakes.py', 'TextureBakeRunner._start', 'handler-loop', 'self.handlers'): BAKE_END,
    ('layout_hold.py', 'register', 'handler:load_post', '_load_post'): LIFECYCLE,
    ('library_overlay.py', 'register', 'draw', '_draw'): DRAW,
    ('media_shelf.py', '_sample', 'helper:_defer', 'lambda: _offer(pointer)'): UI,
    ('media_shelf.py', '_sample', 'helper:_defer', '_write'): UI,
    ('media_shelf.py', '_expand_on_tab', 'helper:_defer', "lambda: _apply(pointer, 'expanded')"): UI,
    ('media_shelf.py', '_sample', 'helper:_defer', 'lambda: _apply(pointer, wanted)'): UI,
    ('media_shelf.py', 'register', 'draw', '_sample'): DRAW,
    ('photo_drop_jobs.py', '_ensure_draw', 'draw', '_draw'): DRAW,
    ('providers_page_host.py', 'ensure_handler', 'draw', '_draw'): DRAW,
    ('providers_sheet.py', 'ConnectionSheet._check', 'helper:host.after', "lambda: host.close('done')"): CARD,
    ('providers_sheet.py', 'ConnectionSheet._check', 'helper:host.after', 'lambda: self._check(host)'): CARD,
    ('references.py', '_redraw_when_set', 'timer', 'poll'): UI,
    ('render_drivers.py', 'watch', 'msgbus', '_preference_changed'): PROCESS,
    ('render_region.py', 'register', 'handler-loop', '(bpy.app.handlers.render_complete, bpy.app.handlers.render_cancel)'): UI,
    ('render_region_draw.py', 'ANGELO_OT_render_region_draw.invoke', 'draw', '_draw'): DRAW,
    ('render_region_pills.py', 'register', 'draw', '_draw'): DRAW,
    ('session_rail_input.py', 'register', 'draw', 'rail_redraw.pre_view'): DRAW,
    ('session_rail_input.py', 'register', 'draw', 'live_view_draw.draw'): DRAW,
    ('session_rail_input.py', 'register', 'draw', 'session_rail_draw.draw'): DRAW,
    ('settings_import_ui.py', '_ask_import', 'helper:_later', 'confirmed'): CLICK,
    ('settings_import_ui.py', '_ask_undo', 'helper:_later', 'confirmed'): CLICK,
    ('setup_card.py', 'SetupCard.act', 'helper:host.after', 'lambda: self._settle(host)'): CARD,
    ('shelf_empty.py', 'register', 'draw', '_draw'): DRAW,
    ('skeleton_report_card.py', 'SkeletonReportCard.act', 'helper:host.after', "lambda: (setattr(self, 'copied', False), host.redraw())"): CARD,
    ('stage_guard.py', '_watch', 'handler-loop', '((handlers.depsgraph_update_pre, _before), (handlers.frame_change_pre, _before),'): STAGE,
    ('studio.py', 'register', 'draw', 'draw'): DRAW,
    ('update_card.py', 'NoticeCard.opened', 'helper:host.after', "lambda: host.close('expired')"): CARD,
    ('update_restart.py', '_after_prompt', 'handler-loop', '((_on_saved, bpy.app.handlers.save_post), (_on_save_failed, bpy.app.handlers.sav'): LIFECYCLE,
    ('worker_consent_lead.py', '_spawn', 'helper:thread_start.daemon_or_later', "'angelo-worker-consent'"): PROCESS,
    ('worker_ending.py', '_stop_relaunch', 'helper:thread_start.daemon_or_later', "'AngeloWorkerLaunchStop'"): PROCESS,
    ('worker_queue.py', 'release', 'helper:thread_start.daemon_or_later', "'AngeloWorkerLaunchStop'"): PROCESS,
    ('worker_sessions.py', 'cancel_starts', 'helper:thread_start.daemon_or_later', "'AngeloWorkerLaunchStop'"): PROCESS,
    ('studio.py', 'register', 'timer', 'pump'): SPLIT,
    ('library_host.py', '_wake', 'timer', '_tick'): SPLIT,
    ('rail_pictures.py', 'ensure_timer', 'timer', '_tick'): SPLIT,
    ('document_identity.py', 'register', 'handler:dynamic', 'callback'): LIFECYCLE,
    ('job_ui.py', '_keep_ticking', 'timer', '_tick'): SPLIT,
    ('job_ui.py', '_changed', 'timer', '_tick'): SPLIT,
    ('parallel_sessions.py', '_file_handlers', 'handler:dynamic', 'data_changed'): MARK,
    ('restore_load_handlers.py', 'resume', 'handler:dynamic', 'callback'): LIFECYCLE,
    ('script_watch.py', 'HistoryWatch.start', 'handler:dynamic', 'self._seen'): MARK,
    ('studio_assets_landing.py', 'add_landing_handlers', 'handler:dynamic', 'handler'): LIFECYCLE,
    ('studio_tab_build.py', 'register', 'handler:dynamic', 'handler'): LIFECYCLE,
    ('timer_jobs.py', 'register', 'handler:dynamic', 'handler'): LIFECYCLE,
    ('native_programs.py', 'register', 'handler:dynamic', 'callback'): LIFECYCLE,
}


# Every Angelo operator's modal(): guarded (it asks the gate before any work) or reviewed (break-it review 3).
CANVAS = ("acts only on the person's own presses over its canvas, card or overlay; timers and window events pas"
          's on (input_policy.overlay_passes or its own type check), so nothing runs on a TIMER tick')
REVIEWED_MODALS = {
    ('agent_workspace_ui.py', 'workspace_operators.ANGELO_OT_accept_native_preview'): "the person's own Accept on a retained preview; accept_native_preview refuses under an operation",
    ('render_region_draw.py', 'ANGELO_OT_render_region_draw'): "the person's own drag of a render region; nothing on a timer",
    ('bake_maps_card.py', 'ANGELO_OT_bake_maps_note'): "shows a texture set's progress text in the top bar; reads no scene data",
    ('asset_browser_entry.py', 'ANGELO_OT_asset_browser_input'): CANVAS,
    ('asset_shelf_add.py', 'ANGELO_OT_asset_shelf_add_input'): CANVAS,
    ('card_host.py', 'ANGELO_OT_card_host'): CANVAS,
    ('chat_launcher.py', 'ANGELO_OT_chat_launcher_input'): CANVAS,
    ('chat_view.py', 'ANGELO_OT_chat_input'): CANVAS,
    ('connect_hero.py', 'ANGELO_OT_connect_hero'): CANVAS,
    ('flow_menu.py', 'ANGELO_OT_flow_menu'): CANVAS,
    ('flow_window.py', 'ANGELO_OT_flow_window_input'): CANVAS,
    ('intro.py', 'ANGELO_OT_intro'): CANVAS,
    ('library_overlay.py', 'ANGELO_OT_library_input'): CANVAS,
    ('providers_page_host.py', 'ANGELO_OT_providers_page_input'): CANVAS,
    ('render_region_pills.py', 'ANGELO_OT_render_region_input'): CANVAS,
    ('session_rail_input.py', 'ANGELO_OT_session_rail_input'): CANVAS,
    ('studio_input.py', 'ANGELO_OT_studio_input'): CANVAS,
}


class Inventory(unittest.TestCase):
    def test_every_callback_is_guarded_or_reviewed(self):
        found = sites()
        loose = sorted(key for key, guarded in found if not guarded and key not in REVIEWED)
        self.assertEqual(loose, [], "guard it with edit_tool_hold.guarded, or review it in REVIEWED with its reason")
        self.assertGreater(sum(guarded for _key, guarded in found), 20)   # Never vacuous.

    def test_no_handler_is_guarded(self):   # A handler's return value is ignored: a held call would be lost (N1).
        self.assertEqual([key for key, guarded in sites() if guarded and key[2].startswith("handler")], [])

    def test_no_reviewed_entry_is_stale_or_guarded(self):
        found = dict(sites())
        self.assertEqual(sorted(key for key in REVIEWED if key not in found), [], "no longer registered: remove it")
        self.assertEqual(sorted(key for key in REVIEWED if found.get(key)), [], "now guarded: remove it")

    def test_the_writers_the_reviews_found_are_guarded(self):
        found = dict(sites())
        for key in (("job_bake_maps.py", "TextureSetRunner._handler.handler", "timer", "self.chain"),   # F2
                    ("flow_host.py", "register", "timer", "pump")):
            self.assertTrue(found.get(key), key)

    def test_every_angelo_modal_is_guarded_or_reviewed(self):
        found = dict(modals())
        self.assertEqual(sorted(key for key, guarded in found.items() if not guarded and key not in REVIEWED_MODALS), [])
        self.assertEqual(sorted(key for key in REVIEWED_MODALS if key not in found or found[key]), [], "stale or guarded")
        self.assertTrue(found[("web_export_ui.py", "ANGELO_OT_web_export")])   # Its TIMER step exports the scene.


if __name__ == "__main__":
    unittest.main()
