# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The grant inventory: every Angelo operator and every name a state file is written under, each judged and given a
reason (doc/angelo/grant_operators.rst, "Audit" and "Authority in C"). Standard library only, no Blender: the host test
(``test_native_grant_operators.InventoryTests``) reads the source with these scanners, and the in-app suite
(``test_blender_grant_inventory``) checks Blender's live operator registry and C's denied-operator list against the same
lists. Nothing grant-like may escape by being missing from a list, or by being made in a way a scanner cannot read.
"""

import ast
import functools
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
SOURCE = ROOT / "source/blender"

# ---- Inventory: nothing grant-like escapes by being missing from a list (doc/angelo/grant_operators.rst, "Audit") ----
# Judged by what each operator DOES (its execute, invoke and modal and the functions they call), never by its name.
# A grant widens a permission, trusts or approves anything, stores keys or sign-ins, spends credits, connects or
# installs code, sends the person's data out, or writes grant state; such an operator belongs in AUDIT.

_OPENS = ("opens a page, menu, card, dialog, browser tab or window, or switches what one shows; the person acts "
          "there, and a grant there runs only in its own press's C ticket")
_INPUT = ("routes the person's real pointer and key events for an Angelo surface: it acts only on events Blender "
          "delivers, and a grant it reaches runs in that press's C ticket (grant_guard)")
_NARROWS = "narrows: stops, cancels, declines, removes or forgets the person's work; never widens"
_ACCOUNT = ("narrows the person's accounts, sign-ins or consents (signs out, removes a connection, cancels a sign-in, "
            "disconnects an app, forgets its allowed scripts): never a grant, yet refused to agent code in C as grants "
            "are (grant_guard.ACCOUNT_OPERATORS, the G2 ruling)")
_INSPECTS = ("inspects or refreshes: reads and reports, or checks a connection the person already made, with what "
             "they already gave it")
_SCENE = ("an undoable edit of the open document the person sees (objects, nodes, materials, render settings, the "
          "view), or a local render of it")
_LAYOUT = "changes the interface's layout, mode or chat shown (editors, docks, windows, tabs); no data, no permission"
_LOCAL = ("edits the person's own local data (drafts, flows, library folders, snapshots, the brief) through its own "
          "dialog, and changes no permission")
_EXPORT = ("writes a file the person chose in a file browser, copies text for the person to use, or shows the scene "
           "in their browser over loopback with a token")
_IMPORT = ("brings a file the person chose, or an item already in their library, into the scene, chat or library; "
           "no provider is called")
NOT_A_GRANT = {
    # 2.1.0 attachment markup (v210-agent-ux).
    "angelo.markup_picture": ("opens the picture viewer on a picture of the chat (a chip's, the newest, or the 3D view "
                              "captured into the chat's own pictures): no file outside the chat, no consent, no spend"),
    "angelo.markup_viewer": ("the picture viewer's passive canvas: draws marks on a copy of the picture in memory; its "
                             "paid edits go through Media Studio's own guarded card, its Send is the composer's send"),
    **dict.fromkeys((
        "angelo.show_about", "angelo.about", "angelo.welcome", "angelo.whats_new", "angelo.bake_maps", "angelo.learn",
        "angelo.conversations", "angelo.connection_menu", "angelo.game_physics", "angelo.changes", "angelo.flow_connect", "angelo.render_engines", "angelo.library_open", "angelo.open_media_studio",
        "angelo.toggle_studio", "angelo.show_flows", "angelo.toggle_chat", "angelo.open_chat", "angelo.flow_open",
        "angelo.flow_open_recipe", "angelo.external_app_open", "angelo.waiting_chat_open", "angelo.skeleton_check",
        "angelo.photo_drop", "angelo.media_open_image", "angelo.crash_report_last", "angelo.approval_policy",
        "angelo.retopology",
    ), _OPENS),
    **dict.fromkeys((
        "angelo.asset_browser_input", "angelo.asset_shelf_add_input", "angelo.card_host",
        "angelo.chat_launcher_input", "angelo.chat_input", "angelo.connect_hero", "angelo.flow_menu",
        "angelo.flow_window_input", "angelo.intro", "angelo.library_input", "angelo.providers_page_input",
        "angelo.render_region_input", "angelo.session_rail_input", "angelo.studio_input", "angelo.owned_area_key",
    ), _INPUT),
    **dict.fromkeys((
        "angelo.flow_stop", "angelo.job_cancel",
        "angelo.dismiss_native_suggestion", "angelo.decline", "angelo.stop", "angelo.remove_reference",
        "angelo.studio_library_off", "angelo.studio_library_free", "angelo.narrow",
    ), _NARROWS),
    **dict.fromkeys((
        "angelo.team_sign_out", "angelo.team_cancel_sign_in", "angelo.cancel_signin", "angelo.quick_connect_cancel",
        "angelo.connection_remove", "angelo.external_app_disconnect",
    ), _ACCOUNT),
    **dict.fromkeys((
        "angelo.performance_summary", "angelo.studio_library_report", "angelo.bake_maps_note",
    ), _INSPECTS),
    **dict.fromkeys((
        "angelo.flow_place_character", "angelo.flow_place", "angelo.flow_drop_items", "angelo.flow_add_recipe",
        "angelo.flow_pick_image",
        "angelo.flow_edit_text", "angelo.game_physics_edit_fit", "angelo.library_recover",
        "angelo.media_add_to_scene", "angelo.media_apply_to_selected", "angelo.media_drop",
        "angelo.render_engine_set", "angelo.render_region", "angelo.render_region_again",
        "angelo.render_region_revert", "angelo.render_region_draw", "angelo.select_through", "angelo.tension_map",
        "angelo.uv_checker", "angelo.uv_checker_toggle", "angelo.studio_turn_into_flow", "angelo.scatter",
        "angelo.camera_look", "angelo.shape_cut", "angelo.uv_layout", "angelo.uv_layout_run",
        "angelo.restore_checkpoint", "angelo.web_shader_uniforms", "angelo.splat_to_mesh", "angelo.fit_collision",
        # Rigging (v210-rigging, 2026-11-18; doc/angelo/rigging.rst): each an undoable edit of the armature in hand or a
        # new game rig beside it; none reads or writes a file, an account or a setting.
        "angelo.retarget_animation", "angelo.make_game_rig", "angelo.extract_root_motion", "angelo.pose_body",
        "angelo.add_ik_controls", "angelo.set_ik_fk",
    ), _SCENE),
    **dict.fromkeys((
        "angelo.editor_type", "angelo.dock_side", "angelo.pop_out", "angelo.return_dock", "angelo.switch_session", "angelo.new_chat",
        "angelo.workspace_tab_add",
    ), _LAYOUT),
    **dict.fromkeys((
        "angelo.checkpoint_retention", "angelo.checkpoint_settings",
        "angelo.learn_teach", "angelo.flow_save_recipe", "angelo.studio_prompt", "angelo.chat_example", "angelo.multiline",
    ), _LOCAL),
    **dict.fromkeys((
        "angelo.export_action_clips",   # Rigging: the file the person picks in its save browser, and clips beside it.
        ), _EXPORT),
    **dict.fromkeys((
        "angelo.attach_reference", "angelo.studio_workflow_image",
        "angelo.flow_pick_image_file", "angelo.media_add_to_chat", "angelo.media_drop_ui",
        ), _IMPORT),
    # One at a time, where a group's sentence would not say why.
    'angelo.discard_native_preview': "throws away a retained preview the agent built: narrows; the agent's own discard only says on its card what is lost",
    'angelo.flow_create': "makes a new Generation Flow, from a template or empty, in the open document: an undoable edit; the agent's angelo_flow_build asks on its card",
    'angelo.flow_new': "makes an empty Generation Flow in the node editor: an undoable edit; the agent's angelo_flow_build asks on its card",
    'angelo.flow_choose': "picks which result of a flow step goes on: the person's own choice; the agent's angelo_flow_choose asks on its card",
    'angelo.studio_folder': "makes a new Media Studio folder from its own dialog; the agent's angelo_studio_create_folder asks on its card",
    'angelo.studio_import_3d': "imports a generated model into the scene after its own confirmation: an undoable edit; the agent's angelo_studio_import_3d asks on its card",
    'angelo.studio_files': "adds images the person chose in a file browser to Media Studio's library or form; no provider is called; the agent's angelo_studio_import_image asks on its card",
    'angelo.studio_asset': "moves a Media Studio item to another folder from its own dialog; the agent's angelo_studio_move_asset asks on its card",
    'angelo.studio_asset_action': "stops a running Media Studio job (narrows) or attaches a finished item to the form; the agent's angelo_studio_stop asks on its card",
    'angelo.director_toggle': "switches Director and Manual; the agent's angelo_set_workspace_mode asks on its card; no data, no permission",
    'angelo.director_toggle_viewport': "Director's Ctrl+Option+D over the 3D view, as director_toggle; no data, no permission",
    'angelo.check_signin': "runs the native CLI's own status check for a sign-in the person made (login stays off); sends nothing of theirs",
    'angelo.connection_check': "checks a connection the person made: the CLI's status command, or one request on its saved key to see that it still works",
    'angelo.endpoint_test': "sends a few short requests on a custom endpoint's saved key to check it answers: a small spend the person asked for on a connection they made",
    'angelo.refresh_models': "asks the person's connected providers for their model lists; prepares the chat with the connection already chosen (prepare_controller, an entry that refuses inside agent code)",
    'angelo.model_picker': 'opens the model picker and asks connected providers for their model lists; prepares the chat with the connection already chosen (prepare_controller, as refresh_models)',
    'angelo.enhancer_refresh': "asks the person's connected providers for their model lists, for AI Enhance's picker",
    'angelo.team_refresh': 'asks the team console which providers the signed-in team offers, on the sign-in the person made',
    'angelo.check_for_updates': "asks Angelo's update feed whether there is a newer release; downloading and installing are the update card's ticketed buttons",
    'angelo.update_show': "shows the update card, or asks the update feed first; installing is the card's ticketed button",
    'angelo.send_feedback': "opens the feedback form in the browser; the URL carries the app version and system, as stock Blender's does, and the person sends the form",
    'angelo.team_open_console': "opens a page of the team console in the browser; nothing is sent beyond the page's address",
    'angelo.copy_login': "copies a native CLI's sign-in command (and its CODEX_HOME path) for the person to run themselves; it signs nothing in",
    'angelo.team_copy_sign_in_link': 'copies the link of a team sign-in the person started; it carries the OAuth state and PKCE challenge but never the verifier, which stays in Angelo, so the link alone completes no sign-in',
    'angelo.library_credits': 'copies the attribution text of the Library items this file uses',
    'angelo.settings': 'opens Preferences at AI Providers; every change there is its own guarded control',
    'angelo.crash_save_report': 'writes a crash report to a file the person chose in a file browser; nothing is sent',
    'angelo.studio_export': 'copies a Media Studio file to a new path the person chose in a file browser, never over an existing file',
    'angelo.job_open': "shows a finished job's output file in the system's file browser or viewer",
    'angelo.studio_video': "adds Media Studio videos to the Sequencer (an undoable edit), or opens one in the system's player or file browser; the confinement refuses the last two to scripts",
    'angelo.export_splat_ply': "writes the selected splats to a PLY file the person confirmed in its save browser (which starts in the document's folder with one clean name); a path a script names is refused anywhere outside Angelo's own folders and files",
    'angelo.web_export': "writes a web export to a file the person chose; the agent's own export asks on its approval card, and the confinement refuses a script's write outside the project",
    'angelo.web_preview': "serves the scene to the person's browser on 127.0.0.1 with a token and a Host check, or stops it; the agent's own preview asks on its approval card",
    'angelo.workspace_mode': "switches Director and Manual; the agent's own switch asks on its approval card; no data, no permission",
    'angelo.delete_conversation': 'moves a chat to Recently Deleted; its saved options go with it and grant nothing while it is there',
    'angelo.rename_conversation': "changes a chat's title only; its options are untouched",
    'angelo.restore_conversation': 'brings a deleted chat back with the options the person had given it (approval level, Fast, model): their own earlier grant restored as it was, never a wider one',
    'angelo.project_brief': "saves the person's edits of a chat's Project Brief from its own dialog; the brief is context, not a permission",
    'asset.angelo_drag': 'starts the drag of Media Studio items the person pressed on, as the Asset Browser drags assets',
    'asset.angelo_media_drop': ("hands a drop of Media Studio items to angelo.media_drop or media_drop_ui, which put "
                                "items already in the person's library into the scene, chat or Media Studio; no "
                                "provider is called"),
    'asset.angelo_media_cannot_drop': "refuses: its poll is always false (its reason is the drop's tooltip), its exec cancels",
    'node.angelo_drop_assets': 'adds the dragged images as nodes where the person dropped them: an undoable edit',
    'sculpt.angelo_sculpt_pass': "a bounded sculpt pass with Blender's own brushes on the active object: one undoable step",
    'sculpt.angelo_sculpt_program': "up to eight bounded sculpt passes with Blender's own brushes: one undoable step",
    "angelo.commit_native_job": ("continues an approval already given: _commit_native_job refuses unless the call is "
                                 "authorized (approve, in AUDIT, authorizes it)"),
    "angelo.library_add": ("adds a free item from Angelo's public library catalog (no account, no credits); library "
                           "items added to the scene are not grants"),
    "angelo.studio_url": ("adds a public image URL to Media Studio's form; nothing is fetched until the person's "
                          "Generate (studio_generate, in AUDIT)"),
    "angelo.studio_library_on": ("puts Media Studio's own folder back in Preferences' Asset Libraries, as adding it "
                                 "there by hand does; no permission, no code"),
    "angelo.use_project_brief": ("confirms that a chat's public notes belong to this project, after its exact review; "
                                 "its dialog and code grant no edits, files, paid use or approvals"),
    "angelo.chat_rules": _OPENS,   # The chat's Rules page; its saves are the page's own ticketed controls.
}


# State files. Keys: "module.NAME" for a module-level constant; "path:<literal>" for a string literal used as a path part
# (x / "name", os.path.join(.., "name"), Path(.., "name"), .joinpath, .with_name), files and folders alike: a state
# file whose name is built in place, or held in a folder named in place, is never missed for having no constant.
_G_WHO = ("who may act on Angelo or connect to it: outside apps' registry, access levels, keys, organization record "
          "and allowed script files, the local endpoint and socket, the engine link's token record (Angelo's and the "
          "Unreal plugin's copy) and the access records a takeover owes; written only on the person's click "
          "(external_app_connect, external_app_copy_setup, godot_link: AUDIT)")
_G_APPROVAL = ("standing approval and each chat's approval policy: the always-allow list and its revocation marker, "
               "a chat's saved options (approval level, Fast, model, effort: set_options, an AUDIT entry), deleted "
               "chats' included, and a chat's lease (session_lease: which Angelo process may save the chat; a "
               "stolen or forged one stops the open chat's saves, and so its work, or lets another process write "
               "that chat's file)")
_G_SIGNIN = ("sign-ins and keys: connection records, native CLI homes and their auth files, the Fab browser profile's "
             "binding, the cache-affinity secret and the snapshot seal's key")
_G_TRUST = "which TLS keys Angelo refuses: removing one widens trust"
_G_PRIVATE = ("what is private: the registry of private stores and retained native profiles, and the bases of "
              "Angelo's state folders (private_roots); whatever lies under them is refused to the file tools")
_G_POLICY = "policy that limits Angelo: the team's and the computer administrator's, read as given"
_G_CODE = ("decides which code runs: update signing keys, a staged or kept app and its ready marker, the pinned agent "
           "runtime, Codex's config, Blender's preferences (enabled add-ons) and scripts folder, render-engine "
           "add-ons and game-engine plugins Angelo installs and their staging, other apps' MCP settings that start "
           "Angelo's server and their backups, and a settings import's backup and manifest, which decide what an "
           "Undo copies into the startup folders")
_G_SPEND = ("the connection and model a spend runs with: AI Enhance's (configure_enhancer, an AUDIT entry) and AI "
            "render's remembered account and model")
_G_RESTRICT = ("a restricting record: an entry holds something back (a chat bound to a worker's part file stays off "
               "Angelo's document); removing one widens what runs")
GRANT_STATE = {
    **dict.fromkeys(("external_cli_protocol.ENDPOINT_FILE", "engine_link_protocol.RECORD_FILE", "path:clients.json",
                     "path:keys", "path:mcp", "path:cli"), _G_WHO),
    **dict.fromkeys(("always_allow.FILE", "always_allow.REVOKED", "path:session.json", "path:sessions",
                     "session_lease.FOLDER", "path:.lease"), _G_APPROVAL),
    **dict.fromkeys(("cache_affinity_secret.FILE", "fab_browser_profile.BINDING",
                     "fab_browser_profile_session_windows.BINDING", "checkpoint_seal.KEYS", "path:connections.json",
                     "path:connections", "path:auth.json", "path:.codex", "path:.claude"), _G_SIGNIN),
    "path:distrusted_keys.json": _G_TRUST,
    **dict.fromkeys(("private_roots.RETAINED_PROFILES", "private_roots.USED_STORES", "private_roots.LAST_GOOD",
                     "path:.local/state", "path:AppData/Local", "path:AppData/Roaming",
                     "path:Library/Application Support", "path:.config", "path:angelo", "path:state"), _G_PRIVATE),
    **dict.fromkeys(("team_org_policy.FILE", "managed_policy.LINUX_FILE", "path:managed-settings.json",
                     "path:managed-settings.d", "path:/Library/Managed Preferences"), _G_POLICY),
    **dict.fromkeys(("update_feed.KEYS_FILE", "release_manifest.KEYS_FILE", "path:linux_release_keys.json",
                     "release_manifest.MANIFEST_NAME", "release_manifest.SIGNATURE_NAME",
                     "update_flow.READY_FILE", "update_install.READY_FILE",
                     "engine_link_ui.SETTINGS_FILE", "path:update_keys.json",
                     "path:staged", "path:staging", "path:mount", "path:previous.app", "path:agent_runtime.json",
                     "path:config.toml", "path:userpref.blend", "path:config", "path:scripts", "path:addons",
                     "path:__init__.py", "path:project.godot", "path:angelo_link", "path:AngeloLink",
                     "path:com.hyperbond.angelo-link", "path:claude_desktop_config.json", "path:.claude.json",
                     "path:mcp.json", "path:angelo_mcp.py", "path:.cursor", "path:Claude", "path:Code", "path:User"),
                    _G_CODE),
    "path:ai-enhance.json": _G_SPEND,
    **dict.fromkeys((
        "ai_render.FILE",
    ), _G_SPEND),
    **dict.fromkeys((
        "worker_routing.REGISTRY",
    ), _G_RESTRICT),
    **dict.fromkeys((
        "unreal_plugin/AngeloLink/Content/Python/angelo_link/link_client.RECORD_FILE",
        "path:Local",
        "path:.sock",
        "path:-cli-",
    ), _G_WHO),
    **dict.fromkeys((
        "settings_import.MANIFEST",
        "settings_import.BACKUP_FOLDER",
        "settings_import_apply.BACKUP_FOLDER",
        "path:.angelo-import-",
        "engine_link_assets.PLUGIN_STAGING",
        "unity_link_assets.ROOT",
        "path:.angelo-backup",
        "path:.import",
    ), _G_CODE),
    **dict.fromkeys((
        "state_paths.STATE_FOLDER",
        "state_paths.LEGACY_STATE_FOLDER",
        "storage.DATA_FOLDER",
        "storage.LEGACY_DATA_FOLDER",
        "path:Angelo",
        "private_families.FAB_BROWSER_PREFIX",
        "private_families.HEADLESS_PREFIX",
        "private_families.LEGACY_WORKER_PREFIX",
        "private_families.WORKER_PREFIX",
        "worker_process.PRIVATE_PREFIX",
        "private_roots.OWNER",
        "fab_browser_windows_store_setup.PRIVATE_LEAF",
    ), _G_PRIVATE),
    **dict.fromkeys((
        "checkpoint_seal.FOLDER",
        "checkpoint_seal.KEY",
        "path:-profile",
    ), _G_SIGNIN),
    **dict.fromkeys((
        "claude_managed.LINUX_FOLDER",
        "claude_managed.DOMAIN",
        "managed_policy.MAC_DIRECTORY",
        "team_org_policy.PENDING",
        "path:.plist",
    ), _G_POLICY),
    **dict.fromkeys((
        "conversations.TRASH",
    ), _G_APPROVAL),
    # 2.1.0 agent UX (v210-agent-ux): each written only in the person's own grant scope (grant_guard entries).
    "context_folder_grants.NAME": ("which folders of the person's a chat's agent may read, and which one it may write "
                                   "text files in (Folder as context, AUDIT entries); each grant is held in C too "
                                   "(grant_authority.CONTEXT_FOLDERS)"),
    "reviewed_scripts.NAME": ("standing approval: the scripts the person chose to Always run in a project, by digest; "
                              "each held in C too (grant_authority.REVIEWED_SCRIPTS)"),
    "project_rules.NAME": ("the person's standing rules and which project files' rules apply: what the agent is "
                           "told in every request; written only by the person's own save, Use or Don't use; what applies is held in C "
                           "(grant_authority.RULE_DECISIONS, GLOBAL_RULES)"),
}

_N_WORK = ("the person's own work and its history: chats, their index, search, drafts and references, goals, "
           "snapshots and their store, recoveries, jobs and outputs, flows, Media Studio's and the Library's items, "
           "and the provider threads a chat continues; no permission")
_N_UI = ("interface state that changes no permission: the welcome, hints, teaching, workspace mode, the asset shelf, "
         "engine suggestions, recent files, the render device, theme and presets, and Media Studio's Asset Browser "
         "copy with its catalog")
_N_SCRATCH = ("a working copy or temporary name of Angelo's own, created exclusively and renamed over its final file "
              "(whose own name is judged) or removed after use: atomic writes, downloads, staging, previews, checks, "
              "and unique-name templates")
_N_RECORD = ("records Angelo keeps about its own processes: crash reports, update progress and results, usage and "
             "telemetry, audit logs, carry-over notes and markers, locks, process IDs and logs, an import's Undo "
             "record, a custom endpoint's model metadata, a child Blender's scratch home")
NOT_GRANT_STATE = {
    "strict_protection.FILE": ("the person's Strict script protection choice, saved at once beside the preferences so "
                               "a crash can't lose it: read only to turn protection ON at start (On from either "
                               "wins), never to turn it off; it widens nothing"),
    "markup_store.DRAFT": ("an unsent markup of a chat's picture (marks, comments, instructions) in the chat's own "
                           "pictures folder: grants nothing; a script that edits it only changes what the person sees"),
    "markup_store.SENT": ("a sent markup, frozen with its message: a record of what the person sent, never read to allow "
                          "anything"),
    "session_page.STOPPED_FILE": ("the conversations whose live session page the person stopped sharing: it can only "
                                  "withhold a read-only page on this computer, never grant a script, a file or a "
                                  "consent; a script that edits it only shows or hides that page"),
    "path:writable-paths": "where 2.0.0 betas kept C's record of where Angelo's code may write: C removes the file "
                           "when Python names it at startup and never writes it (the record lives in the process "
                           "only); it gives a script nothing",
    **dict.fromkeys((
        "carry_over_records.CHAT_LIST", "checkpoint_scene_map.SUFFIX", "checkpoint_seal.CUTOFF",
        "checkpoint_timeline.NAME", "conversation_search.NAME", "conversations.ENTRY", "job_store.FILE_NAME",
        "snapshot_ingest.SCENE", "snapshot_store.INDEX_NAME", "snapshot_store.PACKS_NAME",
        "snapshot_store.STORE_NAME", "splash_setup.SCENE_CHATS", "runtime_threads.FILE",
        "codex_bases.FILE", "draft_overflow.FILE_NAME", "clipboard_image.FILE_NAME", "path:conversations.json", "path:checkpoint.json", "path:checkpoints", "path:recoveries", "path:references",
        "path:goals.json", "path:job-state.json", "path:flow-recipes", "path:folders", "path:media-studio",
        "path:outputs", "path:assets", "path:library", "path:index", "path:index.json", "path:thumbs", "path:files",
        "path:images", "path:models", "path:model.glb", "path:thumb.webp", "path:workspace", "path:pending",
        "path:collaborators", "path:scene.blend", "path:scene.blend@", "path:part.blend", "path:parallel",
        "path:catalogs", "path:batch.json", "path:results.json", "path:enhancer", "path:download",
        "path:download.bin", "path:fab", "path:receipt.json", "path:meta", "path:downloads", "path:angelo-downloads",
    ), _N_WORK),
    **dict.fromkeys((
        "intro.STATE_FILE", "chat_hint.FILE", "learn_option.NAME", "media_shelf.FILE", "engine_suggest.FILE_NAME",
        "version_carry_over.RENDER_DEVICE", "settings_import.GPU_STARTUP_RECORD", "studio_assets.CATS_FILE",
        "studio_assets.MANIFEST", "studio_assets.REGISTRATION", "studio_assets.WRITTEN_NAMES", "studio_assets.LOCK",
        "path:workspace-mode.json", "path:recent-files.txt", "path:Angelo theme.xml", "path:presets",
        "path:interface_theme",
    ), _N_UI),
    **dict.fromkeys((
        "crash_flow.LAST_REPORT_FILE", "crash_state.STATE_FILE", "update_state.STATE_FILE",
        "update_install.RESULT_FILE", "update_install.LOCK_DIR", "update_delta.MANIFEST", "team_usage_buffer.FILE",
        "team_usage_buffer.LOCK", "team_lock.LOCK_NAME", "team_refresh_marks.NAME", "team_refresh_marks.HOLDER",
        "snapshot_store.LOCK_NAME", "version_carry_over.LOCK", "version_carry_over.NOTE_NAME",
        "data_migration.NOTE_NAME", "moved_state_notice.NOTE_NAME", "moved_state_notice.RECORD",
        "state_migration.NOTE_NAME", "state_migration.MARKER", "state_paths.CARRY_OVER_RECORD",
        "session_kept.OWED", "fab_browser_diagnostics.NAME",
        "fab_browser_profile.PROFILE_LOCK", "fab_browser_profile_session_windows.PROFILE_LOCK",
        "endpoint_catalog.FILE_NAME",
        # Every GUI Angelo's flock on its state folder, with its pid, start time and hidden session id: the person's
        # launch signals that pid only while it is still that very process (headless_window_lock); grants nothing.
        "headless_window_lock.LOCK",
        "path:audit.jsonl", "path:bake.log", "path:child.log",
        "path:convert-failed.log", "path:failed.log", "path:worker.log", "path:writer.log", "path:performance.json",
        "path:usage.json", "path:updates.json", "path:updates", "path:previous.txt", "path:previous",
        # Linux: the fallback's kept folder and note as Angelo chose them; cleanup removes only what it names.
        "update_flow.RECOVERY_FILE",
        "path:connections.lock", "path:pycache", "path:tmp", "path:home", "path:datafiles", "path:extensions",
        "path:.gdignore", "path:README.txt",
    ), _N_RECORD),
    **dict.fromkeys((
        "checkpoint_capture_pins._NAMESPACE",
        "checkpoint_scene_map.FOLDER",
        "path:recovered-",
        "worker_paths.CHAT_ID",
        "worker_pool.SCRATCH",
        "worker_process.STATE",
        "path:asset",
        "path:image",
        "path:item",
        "path:preview",
    ), _N_WORK),
    **dict.fromkeys((
        "studio_assets.FOLDER",
    ), _N_UI),
    **dict.fromkeys((
        "carry_over_links.RELINKING",
        "carry_over_moves.CARRYING",
        "moved_state_notice.SHOWN_MARKER",
        "version_carry_over.NOTE_SEEN",
        "version_carry_over.KEPT_PREFIX",
        "version_carry_over.RECORD",
        "pycache_retention.LAST_USED",
        "worker_crash_evidence.TEMP",
    ), _N_RECORD),
    **dict.fromkeys((
        "private_roots.SCRATCH",
        "library_agent.PREVIEW_FOLDER",
        "stage_bundle_folder.PREFIX",
        "job_outputs.STAGING_PREFIX",
        "job_render_child.PREFIX",
        "path:clients.",
        "path:.connections-",
        "path:.retained-",
        "path:.goals-",
        "path:.performance-",
        "path:.usage-",
        "path:.receipt-",
        "path:.download-",
        "path:.fab-owner-",
        "path:.fab-profile-",
        "path:.convert-",
        "path:.angelo-link-",
        "path:.angelo-web-",
        "path:angelo-assets-",
        "path:angelo-endpoint-check-",
        "endpoint_catalog.LISTING_PREFIX",   # A catalog listing's empty CODEX_HOME, removed after it.
        "runtime_listing.LISTING_PREFIX",   # The same prefix where it is defined (the start check reads it).
        "path:angelo-library-",
        "path:angelo-link-live-",
        "path:angelo-merge-check-",
        "path:check.blend",   # The merge check's own copy, in its angelo-merge-check- folder.
        "path:sound-%s.flac",   # A worker's sound written again by the merge, staged in a folder of its own, then packed.
        "path:angelo-preflight-",
        "path:angelo-team-",
        "path:angelo-worker-%s-%s",
        "path:image-",
        "path:-preview.png",
        "path:.carry-%d.tmp",
        "path:%d%s",
        "path:%d-%s-%s",
        "path:%s (%d)",
        "path:%s-%d%s",
        "path:%s%.*d%s%s",
        "path:%s.%d.tmp",
        "path:%s.%s-%d.unreadable",
        "path:%s.%s.tmp",
        "path:%s.damaged-%d",
        "path:.%d.tmp",
        "path:.%s.%d.part",
        "path:.%s.%s.tmp",
        "path:.%s.tmp",
    ), _N_SCRATCH),
    "path:%s.%s%s.unreadable": ("a rules file this build could not read, moved aside under a time stamp (project_rules): "
                                "never read again, so it decides nothing"),
}

_X_NOT_FILE = ("not a file: an operator, tool, message, schema, host, URL, regular expression, data-block, setting or "
               "message-frame name")
_X_OUTPUT = ("a name or suffix for a file the person's own render, export, import or check makes (or its working "
             "copy), or a project file a game or web export writes")
_X_SUFFIX = "a bare file suffix: the file it ends is judged by its own name or folder"
_X_SHIPPED = ("shipped read-only inside the app: Angelo's own code and helpers, fonts, images, catalogs and their "
              "folders")
_X_OS_STARTUP = ("the operating system's own login and autostart locations (LaunchAgents, the Windows Startup "
                 "folder, XDG autostart, systemd user units), which the agent-script confinement makes read-only to "
                 "scripts (script_confinement_blender.os_startup): not Angelo's state")
_X_AUDIO_CONFIG = ("the audio stack's per-user configuration (ALSA, OpenAL Soft, PulseAudio, PipeWire, JACK), read by "
                   "those libraries when a sound device opens and made read-only to agent scripts with the startup "
                   "locations (script_confinement_blender.os_startup; break-it-confine-fixes-b31e34840ca MUST-3): "
                   "another program's files, never written by Angelo, not Angelo's state")
_X_OTHER = "another program's own files or a system location Angelo only reads or runs"
_X_PATH_RULE = "a name only given to a path rule (where a file would go), never opened, made or written"
NOT_STATE = {
    **dict.fromkeys((
        "action_operator_spec.PROPERTY_NAME", "action_world.COMMITTED_NAME", "auto_reasons.WRITE_FILE",
        "change_feed.TURN_CONTEXT_NAME", "crash_report.SCHEMA", "data_read_values.LOADS_FILE",
        "derived_checks.DEFAULT_MATERIAL_NAME", "derived_checks.DEFAULT_PRIMITIVE_NAME",
        "derived_checks.DEFAULT_SHADER_NAME", "external_cli.RUNTIME_NAME", "external_host.KILL_SWITCH_NAME",
        "external_mcp.SERVER_NAME", "fab_catalog.THUMB_HOST", "fab_settings.NAME",
        "fal_queue.QUEUE_HOST", "flow_nodes.VIDEO_IDNAME", "goal_spec.TOOL_NAME", "planning_spec.TOOL_NAME",
        "input_policy.RENDER_OPERATOR", "job_render_outputs.LAYERS_NAME", "managed_policy.DOMAIN",
        "render_extensions.ENGINE_PATH", "script_source.SCRIPT_FILE", "script_store.TOOL_NAME",
        "secret_service_linux.SCHEMA", "secret_service_linux.SECRETS_BUS_NAME", "studio_assets.LIBRARY_NAME",
        "desktop_linux.PORTAL", "desktop_linux.PORTAL_PATH",   # The desktop portal's D-Bus name and object path.
        "tension_map_logic.MODIFIER_NAME", "ik_controls.IK_NAME", "ik_controls.ROT_NAME", "ik_controls.FOLLOW_NAME",
        "view_passes.UNSET_SPACE", "view_spec.TOOL_NAME",
        "web_link_assets.DISPLAY", "worker_chat_arrival.CONSENT", "worker_chat_arrival.STOP",
        "path:..", "download_spec.NAME", "bake_check_host.SCENE_NAME", "scatter_nodes.GROUP_NAME",
        "look_nodes.GROUP_NAME", "look_nodes.NODE_NAME",
        # A lens length in millimetres (a number): an orbit's distance divides by it, which the scan reads as a join.
        "external_look_args.LENS_MM",
        # Strict script protection's preference property (an add-on preference Blender saves with the person's
        # preferences, never a file of Angelo's); turning it off is guarded (grant_guard.setting_allowed).
        "strict_protection.NAME",
    ), _X_NOT_FILE),
    **dict.fromkeys((
        "fal_video.VIDEO_FILE", "hidden_copy.SUFFIX", "job_render_outputs.JPEG2000_CODESTREAM",
        "job_render_video.EXTENSION", "library_files.PLAIN_GLB", "engine_preflight.REPORT_SUFFIX",
        "studio_video.THUMBNAIL", "engine_link_assets.WORKING_FOLDER", "path:export.glb", "path:live.glb",
        "path:scene.glb", "path:render.blend", "path:out.blend", "path:converted.blend", "path:converted.json",
        "path:job.json", "path:result.json", "path:spec.json", "path:world.blend", "path:world.exr",
        "path:linear.hdr", "path:shown.bmp", "path:character.png", "path:parts.png", "path:checks", "path:packed",
        "path:part.crash.txt.partial", "path:package.json",
        "path:%d-%d.exr",   # bake_check_host: a picture of the bake check, in its scratch folder.
        "download_folders.FOLDER", "download_fetch.TEMPORARY", "path:%s (%d)%s%s",   # angelo_download's file.
        # A connected app's attached picture, decoded into a private temporary folder for the conversation's own
        # reference store to copy (external_agent.AgentUi.attach), then removed.
        "path:attachment-%d%s",
    ), _X_OUTPUT),
    **dict.fromkeys((
        "cli_client._IDENTITY_FILE", "view_looks.MATCAP", "path:angelo_change_clock.py", "path:angelo_headless.py",
        "path:color_spaces.py", "path:fab_convert_child.py", "path:pdf_text_worker.py", "path:render_child.py",
        "path:studio_assets_convert.py", "path:tls_distrust.py", "path:video_encode_child.py",
        "path:view_exr_worker.py", "path:web_fetch_hook.py", "path:web_world_bake.py",
        "path:pypdf-6.21.2-py3-none-any.pyz", "path:vendor", "path:Inter.woff2", "path:welcome-banner.png",
        "path:index.html", "path:release_notes.json", "path:api_drift.json", "path:human_base_meshes",
        "path:human_base_meshes.json", "path:base_mesh_thumbs", "path:unity_package", "path:unreal_plugin",
        "path:godot_addon", "path:update_helpers", "path:web_preview", "path:session_page", "path:modules", "path:angelo_identity.json",
        "path:config.ocio", "path:release/branding/angelo/exports",
        "scatter_spec.PRESET_DIR", "path:biomes", "path:%s.angelo-biome.json",   # The shipped biome presets.
    ), _X_SHIPPED),
    **dict.fromkeys((
        "path:.bun", "path:.cargo", "path:.local", "path:/Applications", "path:/proc/self/mountinfo", "path:/tmp",
        "path:/usr/bin/sandbox-exec", "path:AppData", "path:Application Support", "path:ChatGPT",
        "path:CloudStorage", "path:Contents/Info.plist", "path:Contents/MacOS",
        "path:Google/Chrome/Application/chrome.exe", "path:Library", "path:Links", "path:MacOS", "path:Microsoft",
        "path:Mobile Documents", "path:Programs", "path:ProjectSettings", "path:ProjectVersion.txt", "path:Roaming",
        "path:SingletonLock", "path:System32", "path:Volta", "path:WinGet", "path:WindowsApps",
        "path:WindowsPowerShell", "path:apps", "path:bash.exe", "path:bin", "path:bus", "path:chocolatey",
        "path:cmd.exe", "path:current", "path:fnm", "path:git", "path:node-versions", "path:node.exe",
        "path:nodejs", "path:npm", "path:pnpm", "path:powershell.exe", "path:scoop", "path:shims", "path:stat",
        "path:v1.0",
        "path:/etc/resolv.conf",   # download_names reads this machine's DNS search domains.
        "path:comm",   # engine_detect reads /proc/<pid>/comm: a process's name, the kernel's.
        "path:log",   # runtime_launch: Codex's log folder for Angelo threads (codex-state), written by Codex only.
        "runtime_environment.SYSTEM_PATH",   # The system's own program folders, the PATH of Angelo's Linux runtime.
    ), _X_OTHER),
    **dict.fromkeys((
        "blender_asset_index.PREFIX",
        "card_host_input.PREFIX",
        "claude_stream.HOST_TOOL_PREFIX",
        "context_ledger.WORKSPACE_PREFIX",
        "conversations_popover.PREFIX",
        "data_read_values.PATH",
        "endpoint_connections.MODE_PREFIX",
        "gateway_capabilities.PATH",
        "godot_link_assets.NAME_PREFIX",
        "legacy_names.LEGACY_TOOL_PREFIX",
        "legacy_names.TOOL_PREFIX",
        "library_spec.PREFIX",
        "memory_context.RESULT_PREFIX",
        "product_env.PREFIX",
        "schema_compaction._REFERENCE_PREFIX",
        "stage_names.PREFIX",
        "team_library_spec.PREFIX",
        "team_signin.CALLBACK_PATH",
        "worker_spec.PREFIX",
        "web_link_assets.NAME_PREFIX",
        "blender_release.RELEASE",
        "cli_client.BLENDER_SERIES",
        "update_install.MAC_TOOL_PATH",
        "worker_channel.PIPE_PREFIX",
        "bake_maps_graph.PREFIX",
        "cache_affinity.PREFIX",
        "job_render_outputs.MAIN_PREFIX",
        "path:-",
        "path:.",
        "path:/",
    ), _X_NOT_FILE),
    **dict.fromkeys((
        "path:.blend",
        "path:.fbx",
        "path:.glb",
        "path:.img",
        "path:.json",
        "path:.lock",
        "path:.mp4",
        "path:.part",
        "path:.png",
        "path:.py",
        "path:.tmp",
        "path:.cmd",
    ), _X_SUFFIX),
    **dict.fromkeys((
        "base_mesh_thumbs.FOLDER",
        "connect_hero.ASSETS",
        "human_base_meshes.FOLDER",
        "human_base_meshes.RELATIVE",
        "pdf_text.VENDOR",
        "preview_branding._exports",
        "script_api_table.TABLE_PATH",
        "web_preview_server.PAGE_ROOT",
        "session_page_site.PAGE_ROOT",
        "update_install.HELPER_FOLDER",
        "path:fonts/DejaVuSansMono.woff2",
    ), _X_SHIPPED),
    **dict.fromkeys((
        "blender_folders.FOLDER_MAC",
        "blender_folders.FOLDER_UNIX",
        "desktop_os.CORE_FOUNDATION_PATH",
        "desktop_os.IOKIT_PATH",
        "keychain_macos.CORE_FOUNDATION_PATH",
        "keychain_macos.SECURITY_PATH",
        "secret_service_linux.FLATPAK_INFO",
        "update_stage_mac.TASKPOLICY",
        "path:/proc/%d/status",
        "path:exe",
        # update_card.linux_recovery: "<name> <n>" when a kept folder or note name beside the install is taken.
        "path:%s %d%s",
        "path:/proc/meminfo",
        # process_identity: Linux's own process facts (a start time, a parent, an owner), read only.
        "path:/proc/%d/stat", "path:/proc/stat", "process_identity.PROC_STATUS",
        # external_build.bundle_version: another Angelo build's own bundle Info.plist, read to name that build.
        "path:Contents", "path:Info.plist",
        # addon_compat_live._manifest: an installed extension's own manifest, read only to judge it against an update.
        "path:blender_manifest.toml",
        "path:/Script/Engine",
        "path:colormanagement",
        "path:fonts",
        "path:installation/bin",
    ), _X_OTHER),
    # script_confinement_blender.pycache_mirrors: asks importlib's cache_from_source where a folder's bytecode goes.
    "path:mirror.py": _X_PATH_RULE,
    **dict.fromkeys(("path:LaunchAgents", "path:Start Menu", "path:Startup", "path:Windows", "path:autostart",
                     "path:systemd", "path:user"), _X_OS_STARTUP),
    # `Preferences`: macOS's ~/Library/Preferences, named only as the folder of OpenAL Soft's alsoft.conf there.
    **dict.fromkeys(("path:Preferences", "path:alsoft.conf", "path:alsoft.ini", "path:alsa", "path:pulse",
                     "path:pipewire"), _X_AUDIO_CONFIG),
    # 2.1.0 agent UX (v210-agent-ux): a message, a data-block name, a sheet's action prefix, a picture suffix.
    **dict.fromkeys(("context_folder.NO_FOLDER", "project_rules_host.TEXT_NAME", "rules_sheet.PREFIX"), _X_NOT_FILE),
    # Several folders (2.1.0): the tools' messages to the model.
    **dict.fromkeys(("context_folder.NAME_FOLDER", "context_folder.UNKNOWN_FOLDER", "context_folder_write.MISSING_FILE",
                     "auto_reasons.CONFIG_FILE"), _X_NOT_FILE),   # The last: an Auto reason's code.
    "path:.jpg": _X_SUFFIX,
}


# ---- Operators: what the source declares, in Python and in C (the in-app suite checks the live registry) ----------

# Operators made at run time, which no class statement shows: "module.function" that makes them -> the idnames it makes.
# The in-app suite checks each against the live registry; a type() call with a bl_idname anywhere else fails the scan.
DYNAMIC = {"script_errors.grouped": ("angelo.apply_approved_script",)}
# Operators guarded through another's entry in grant_guard.REGISTRY rather than run by AuditTests (grouped siblings).
GROUPED = {"angelo.apply_approved_script": ("applies an approved script as approve does, as one grouped Undo step; "
                                            "guarded as its parent (grant_guard.REGISTRY copies its entry)")}

# Operators registered only under a condition: idname -> (module of the add-on, its function that says whether they
# are registered, why). The in-app suite asks the function: registered exactly when it answers True, never otherwise.
REGISTERED_WHEN = {
    "angelo.session_rail_input": ("parallel_access", "enabled",
                                  "the session rail exists only where parallel edit does (session_rail_input.register)"),
    "angelo.about": ("preview_branding", "installed",
                     "the stock-Blender preview's dialog: only its launchers install it (scripts/angelo/launch*.py)"),
    "angelo.welcome": ("preview_branding", "installed", "as angelo.about"),
}

_OPERATOR_BASE = re.compile(r"(^|\.)Operator$|_OT_")
_OPERATOR_ID = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9_]+$")   # "module.name": menus and panels are "ANGELO_MT_x".
_C_OPERATOR = re.compile(r"ot->idname\s*=\s*\"([A-Z][A-Z0-9]*_OT_[a-z0-9_]+)\"")
# ``ot->idname = NAME;`` with ``NAME = "X_OT_y"`` a constant of the same file (asset_angelo_media.cc's DROP_OP).
_C_OPERATOR_NAMED = re.compile(r"ot->idname\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\s*;")
_C_CONSTANT = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\"([A-Z][A-Z0-9]*_OT_[a-z0-9_]+)\"")


def python_idname(c_idname):
    """``ASSET_OT_angelo_drag`` -> ``asset.angelo_drag``, as ``bpy.ops`` names it."""
    module, _ot, name = c_idname.partition("_OT_")
    return "%s.%s" % (module.lower(), name)


def sources():
    """(key, tree) of every Python file in the add-on, subpackages included: a top-level module's key is its name, a
    nested file's its path below the add-on without ``.py``. Parsed once per run; no scanner changes a tree."""
    return iter(_parsed())


@functools.lru_cache(maxsize=1)
def _parsed():
    out = []
    for path in sorted(ADDON.rglob("*.py")):
        relative = path.relative_to(ADDON).with_suffix("")
        out.append(((relative.name if len(relative.parts) == 1 else relative.as_posix()),
                    ast.parse(path.read_text(encoding="utf-8"))))
    return tuple(out)


def _text(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _class_idname(node):
    for item in node.body:   # ``bl_idname = "x.y"`` or ``bl_idname: str = "x.y"``.
        target = item.targets[0] if isinstance(item, ast.Assign) else item.target if isinstance(item, ast.AnnAssign) \
            else None
        if getattr(target, "id", "") == "bl_idname":
            return _text(item.value) or "(not a literal: %s)" % ast.unparse(item.value)
    return None


def _maker(tree, call):
    """"function" (or "Class.method") whose body holds ``call``, or "<module>"."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(sub is call for sub in ast.walk(node)):
            return node.name
    return "<module>"


def source_operators():
    """{bl_idname: ["module.Class", ...]} of every operator the add-on's source declares: classes anywhere in a file
    (nested in functions and branches too), subclasses of Angelo operators, annotated bl_idnames, and literal
    ``type(...)`` classes; and the makers of non-literal ``type()`` operators, "module.function" -> idnames, which must
    be in DYNAMIC. Two classes with one idname stay two entries."""
    found, makers = {}, {}
    for key, tree in sources():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                idname = _class_idname(node)
                if any(_OPERATOR_BASE.search(ast.unparse(base)) for base in node.bases) or \
                        _OPERATOR_ID.match(idname or ""):
                    found.setdefault(idname or "(no bl_idname) %s.%s" % (key, node.name), []).append(
                        "%s.%s" % (key, node.name))
            elif isinstance(node, ast.Call) and getattr(node.func, "id", "") == "type" and len(node.args) == 3 \
                    and isinstance(node.args[2], ast.Dict):
                for name, value in zip(node.args[2].keys, node.args[2].values):
                    if _text(name) == "bl_idname":
                        literal = _text(value)
                        if literal:
                            found.setdefault(literal, []).append("%s.type()" % key)
                        else:
                            makers.setdefault("%s.%s" % (key, _maker(tree, node)), []).append(ast.unparse(value))
    return found, makers


def c_operators():
    """{bpy idname: "file"} of every operator Blender's C/C++ registers whose idname names Angelo, or None in a checkout
    without the C sources (a sparse worktree; the in-app suite still sees them live)."""
    if not SOURCE.is_dir():
        return None
    found = {}
    for path in sorted(SOURCE.rglob("*")):
        if path.suffix in (".c", ".cc", ".cpp", ".h", ".hh"):
            text = path.read_text(encoding="utf-8", errors="replace")
            constants = dict(_C_CONSTANT.findall(text))
            idnames = [match.group(1) for match in _C_OPERATOR.finditer(text)]
            idnames += [constants.get(match.group(1), "") for match in _C_OPERATOR_NAMED.finditer(text)]
            for c_idname in idnames:
                if "angelo" in c_idname.lower():
                    found[python_idname(c_idname)] = path.relative_to(ROOT).as_posix()
    return found


# NOT_A_GRANT operators that do reach grant code (``grant_reach``), each with why that does not make it a grant.
# Anything else reaching grant code fails InventoryTests: it is a grant (AUDIT) or a misreading to fix.
REACHES_GRANT_CODE = {
    "angelo.model_picker": ("prepare_controller binds the chat to the connection and options the person already chose "
                            "(choosing them is AUDIT's select_model, connection_use and use_connection); as an entry it "
                            "refuses inside agent code"),
    "angelo.refresh_models": "as model_picker: prepare_controller applies choices already made, and refuses inside agent code",
    "angelo.library_input": ("routes the person's press on the Library's Fab Connect, a C region of the overlay, to "
                             "fab_ui's connection request in that press's ticket scope; library_host.fab_connect starts "
                             "a connection only in a C grant scope and is an entry, so a search, a script or a timer "
                             "starts none (v201-local-models, 2026-11-15)"),
    "angelo.narrow": ("only narrows, whichever choice: Ask or Read only, Fast off, voice stopped, the engine link off, "
                      "this chat's script trust off, Always allow scripts off; each effect is run and judged narrowing "
                      "by test_native_grant_operators (test_every_choice_only_narrows)"),
    "angelo.external_app_disconnect": ("external_setup.disconnect is an entry only so that agent code can't call it "
                                       "(ACCOUNT_OPERATORS, refused to scripts); at the person's click it takes "
                                       "Angelo out of the outside app's settings: narrows, no grant"),
}


# NOT_A_GRANT operators that write grant state (``grant_state_reach``; break-it S1), each with why that is no grant:
# it narrows, or puts back exactly what the person had granted. Anything else writing grant state fails.
WRITES_GRANT_STATE = {
    "angelo.markup_viewer": ("the person's own Send in the picture viewer sends the chat's message as the composer does "
                             "(console.send_message): the chat saves with its messages; its options never change"),
    "angelo.chat_input": ("routes the person's real keys and clicks in the chat: a sent message saves the chat with its "
                          "messages (and binds a worker's chat to its part file); the chat's options change only "
                          "through set_options, an entry, in the press's C ticket"),
    "angelo.delete_conversation": ("moves a chat, its saved options with it, to Recently Deleted, where they grant "
                                   "nothing: narrows"),
    "angelo.restore_conversation": ("brings a deleted chat back with exactly the options the person had given it: their "
                                    "own earlier grant, never a wider one"),
    "angelo.external_app_disconnect": ("takes Angelo out of the outside app's settings and puts back the app's own backup "
                                       "of them: narrows"),
    "angelo.team_sign_out": "ends the team sign-in, and with it the cache-affinity secret it held: narrows",
    "angelo.narrow": ("turning Always allow scripts off writes its revocation marker: narrows (each choice is run and "
                      "judged by test_every_choice_only_narrows)"),
}

# Every tool the agent may call only after the person's approval card (tool_specs: not read-only) -> the operators
# that do the same thing at the person's own click, and why the card does not apply to them (break-it S3). A twin is
# in AUDIT, or in NOT_A_GRANT with a reason of its own. A new card-gated tool fails until it is mapped here.
_NO_TWIN = "no operator does this at a click: only the agent's call, after its card"
APPROVAL_TWINS = {
    "angelo_accept_preview": (("angelo.accept_native_preview",), "the person's Accept is itself a grant: AUDIT"),
    "angelo_discard_preview": (("angelo.discard_native_preview",), "discarding narrows: the card only says what is lost"),
    "angelo_app_feature": (("angelo.godot_link_install", "angelo.godot_link", "angelo.markup_picture"),
                           "the + menu row's own grants: AUDIT; MARKUP opens the picture viewer (NOT_A_GRANT)"),
    "angelo_engine_link": (("angelo.godot_link",), "turning the link on is a grant: AUDIT"),
    "angelo_flow_run": (("angelo.flow_run",), "a paid run: AUDIT"),
    "angelo_studio_generate": (("angelo.studio_generate",), "a paid generation: AUDIT"),
    "angelo_studio_enhance": (("angelo.studio_enhance",), "a paid enhancement: AUDIT"),
    "angelo_flow_build": (("angelo.flow_create", "angelo.flow_new"),
                          "the person's own new flow: an undoable edit of their document"),
    "angelo_flow_choose": (("angelo.flow_choose",), "the person's own choice of a step's output"),
    "angelo_library_import": (("angelo.library_add",), "the person's own pick from the Library"),
    "angelo_team_library_import": (("angelo.library_add",), "the person's own pick from their team's Library"),
    "angelo_memory_update": (("angelo.project_brief",), "the person's own edit of the brief, in its dialog"),
    "angelo_set_workspace_mode": (("angelo.workspace_mode", "angelo.director_toggle", "angelo.director_toggle_viewport"),
                                  "the person's own switch of the layout"),
    "angelo_studio_create_folder": (("angelo.studio_folder",), "the person's own new folder"),
    "angelo_studio_export": (("angelo.studio_export",), "a copy to a path the person chose in a file browser"),
    "angelo_studio_import_3d": (("angelo.studio_import_3d",), "the person's own import, after its confirmation"),
    "angelo_studio_import_image": (("angelo.studio_files",), "files the person chose in a file browser"),
    "angelo_studio_move_asset": (("angelo.studio_asset",), "the person's own move, in its dialog"),
    "angelo_studio_stop": (("angelo.studio_asset_action",), "stopping narrows"),
    "angelo_web_export": (("angelo.web_export",), "a file the person chose"),
    "angelo_download": ((), _NO_TWIN),   # Its card, in Ask and in Auto (download_approvals).
    "angelo_web_preview": (("angelo.web_preview",), "the person's own preview, on loopback"),
    "angelo_sculpt_program": (("sculpt.angelo_sculpt_program",), "the person's own sculpt passes: one undoable step"),
    "angelo_sculpt_batch": (("sculpt.angelo_sculpt_program",), "as angelo_sculpt_program"),
    "angelo_sculpt_region": (("sculpt.angelo_sculpt_pass",), "the person's own sculpt pass: one undoable step"),
    "angelo_sculpt_resolution": ((), _NO_TWIN),
    "angelo_prepare_sculpt": ((), _NO_TWIN),
    "angelo_apply_batch": ((), _NO_TWIN),
    "angelo_apply_recipe": ((), _NO_TWIN),
    "angelo_scene_program": ((), _NO_TWIN),
    "angelo_create_cube": ((), _NO_TWIN),
    "angelo_set_transform": ((), _NO_TWIN),
    "angelo_execute_python": ((), "applying an approved script is Approve's and its grouped sibling's: AUDIT, GROUPED"),
    "angelo_write_file": ((), _NO_TWIN),
    "angelo_delete_file": ((), _NO_TWIN),
    "angelo_context_folder_write": ((), _NO_TWIN),   # The working folder's text file: only the agent's call.
    "angelo_engine_fix": ((), _NO_TWIN),
    "angelo_job_start": ((), _NO_TWIN),
    "angelo_team_library_save": ((), _NO_TWIN),
}


def carded_tools():
    """The agent's tools that are not read-only (``tool_specs.TOOL_MAP``): each waits for the person's approval card
    unless their own standing consent covers it."""
    import importlib
    import sys
    if str(ADDON.parent) not in sys.path:
        sys.path.insert(0, str(ADDON.parent))
    specs = importlib.import_module("angelo_agent.tool_specs")
    return {name for name, spec in specs.TOOL_MAP.items() if not spec["annotations"]["readOnlyHint"]}


# ---- What an operator reaches: grant code inside a NOT_A_GRANT operator is a misclassification (break-it M1) -------

# Grant code: the ledger's writes and Angelo's own decisions (grant_authority), Angelo's automatic scope (grant_guard),
# a chat's options, and every function wrapped by grant_guard.entry.
_LEDGER = {"grant_authority": {"record", "approve_script", "automatic", "mirror"}, "grant_guard": {"automatic"}}
_ALWAYS = {"set_options"}


def _aliases(tree, module):
    """Names in ``tree`` that stand for add-on modules (``from . import x as y``, ``import angelo_agent.x``) and names
    imported from them (``from .x import f``): {local: module} and {local: (module, name)}."""
    modules, names = {}, {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level >= 1:
            for alias in node.names:
                if node.module:
                    names[alias.asname or alias.name] = (node.module.split(".")[-1], alias.name)
                else:
                    modules[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("angelo_agent"):
            for alias in node.names:
                if node.module == "angelo_agent":
                    modules[alias.asname or alias.name] = alias.name
                else:
                    names[alias.asname or alias.name] = (node.module.split(".")[-1], alias.name)
    return modules, names


def entries():
    """{(module, function)} of every function the add-on wraps with ``grant_guard.entry`` (``f = x.entry(f)``,
    ``Class.method = x.entry(Class.method)``): what an audited operator does, which a script could call without it."""
    found = set()
    for key, tree in sources():
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) \
                    and ast.unparse(node.value.func).endswith("guard.entry") and node.value.args:
                target = node.value.args[0]
                found.add((key, target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")))
    return found


def _functions(tree):
    """{name: [FunctionDef]} of every function and method in ``tree``."""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.setdefault(node.name, []).append(node)
    return out


def _grant_calls(body_nodes, modules, names, entry_names, unique_methods):
    """The grant code the statements in ``body_nodes`` call directly, as "module.function"."""
    hits = set()
    for top in body_nodes:
        for node in ast.walk(top):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute):
                owner = modules.get(getattr(func.value, "id", ""), getattr(func.value, "id", ""))
                if func.attr in _LEDGER.get(owner, ()) or (owner, func.attr) in entry_names:
                    hits.add("%s.%s" % (owner, func.attr))
                elif func.attr in _ALWAYS or func.attr in unique_methods:
                    hits.add("%s.%s" % (unique_methods.get(func.attr, "?"), func.attr))
            elif isinstance(func, ast.Name):
                module, name = names.get(func.id, (None, func.id))
                if module and (name in _LEDGER.get(module, ()) or (module, name) in entry_names):
                    hits.add("%s.%s" % (module, name))
                elif name in _ALWAYS:
                    hits.add("?.%s" % name)
    return hits


def _layout(tree):
    """(module-level functions {name: def}, classes {name: {method: def}}) of ``tree``."""
    functions = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    classes = {n.name: {m.name: m for m in n.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
               for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    return functions, classes


class _Resolver:
    """Where a call in the add-on goes, as far as the source says: a function of its module or one it imports
    (``f()``, ``module.f()``), a method of a named class (``ANGELO_OT_x.execute(self, ...)``), or, for a method called
    on an object whose type the source does not say (``controller.review_script``), every definition of that name
    when at most two places in the add-on define it. Calls on ``self`` are the class's own methods."""

    def __init__(self):
        self.trees = dict(sources())
        self.layouts = {module: _layout(tree) for module, tree in self.trees.items()}
        self.aliases = {module: _aliases(tree, module) for module, tree in self.trees.items()}
        self.defined = {}
        for module, tree in self.trees.items():
            for name, functions in _functions(tree).items():
                self.defined.setdefault(name, []).extend((module, function) for function in functions)

    def classes(self, module):
        modules, names = self.aliases[module]
        found = {name: (module, methods) for name, methods in self.layouts[module][1].items()}
        for local, (other, name) in names.items():
            if other in self.layouts and name in self.layouts[other][1]:
                found[local] = (other, self.layouts[other][1][name])
        return found

    def targets(self, module, function):
        """(module, FunctionDef) of every function the calls in ``function`` may run (cached per function)."""
        cache = self.__dict__.setdefault("_target_cache", {})
        if id(function) not in cache:
            cache[id(function)] = self._targets(module, function)
        return cache[id(function)]

    def _targets(self, module, function):
        modules, names = self.aliases[module]
        classes = self.classes(module)
        out = []
        for call in (n for n in ast.walk(function) if isinstance(n, ast.Call)):
            func = call.func
            if isinstance(func, ast.Name):
                other, name = names.get(func.id, (module, func.id))
                if other in self.layouts and name in self.layouts[other][0]:
                    out.append((other, self.layouts[other][0][name]))
            elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                owner = func.value.id
                if owner in ("self", "cls"):
                    continue
                if owner in modules and modules[owner] in self.layouts:
                    found = self.layouts[modules[owner]][0].get(func.attr)
                    out += [(modules[owner], found)] if found else []
                elif owner in classes:
                    other, methods = classes[owner]
                    out += [(other, methods[func.attr])] if func.attr in methods else []
                elif len(self.defined.get(func.attr, ())) <= 2:
                    out += self.defined.get(func.attr, [])
            elif isinstance(func, ast.Attribute) and len(self.defined.get(func.attr, ())) <= 2:
                out += self.defined.get(func.attr, [])
        return out

    def operators(self):
        """(bl_idname, module, [method defs]) of every literal operator class."""
        for module, tree in self.trees.items():
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    idname = _class_idname(node)
                    if idname and (_OPERATOR_ID.match(idname) or any(_OPERATOR_BASE.search(ast.unparse(base))
                                                                     for base in node.bases)):
                        yield idname, module, [item for item in node.body
                                               if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))]


def grant_reach():
    """{bl_idname: sorted "grant code (via function)"} for every literal operator class whose own methods, or a function
    one call down (``_Resolver``), call grant code: the ledger's writes, Angelo's automatic scope, a chat's options, a
    grant entry. Method entries (``Controller.approve_tool``) count by name only when that name is defined once."""
    resolver = _Resolver()
    entry_names = entries()
    unique_methods = {name: module for module, name in entry_names if len(resolver.defined.get(name, ())) == 1}
    reach = {}
    for idname, module, methods in resolver.operators():
        hits = set(_grant_calls(methods, *resolver.aliases[module], entry_names, unique_methods))
        for method in methods:
            for other, function in resolver.targets(module, method):
                hits |= {"%s (via %s.%s)" % (hit, other, function.name) for hit in _grant_calls(
                    [function], *resolver.aliases[other], entry_names, unique_methods)}
        if hits:
            reach[idname] = sorted(hits)
    return reach


def grant_state_reach():
    """{bl_idname: sorted "module.function: keys"} for every literal operator class whose methods, or a function they
    call (``_Resolver``), write grant state (``grant_state_writers``, propagated to callers): an operator that writes
    grant state is a grant (break-it S1), unless it only narrows or puts back the person's own earlier grant."""
    resolver = _Resolver()
    writers = grant_state_writers(resolver)
    qualnames = {module: _qualnames(tree) for module, tree in resolver.trees.items()}
    reach = {}
    for idname, module, methods in resolver.operators():
        hits = set()
        for method in methods:
            for other, function in resolver.targets(module, method) + [(module, method)]:
                name = qualnames[other][id(function)]
                if (other, name) in writers:
                    hits.add("%s.%s: %s" % (other, name, ", ".join(writers[(other, name)])))
        if hits:
            reach[idname] = sorted(hits)
    return reach


# ---- State files: every name a path is built from where Angelo reads or writes files (break-it M4) ---------------

_FILE_SHAPE = re.compile(r"^\.?[^/\\:\s]*\.[A-Za-z][A-Za-z0-9]{0,8}$")
_NAMED = re.compile(r"(FILE|NAME|FOLDER|PREFIX|DIRECTORY|DIR|PATH)$")
# Filesystem calls and the arguments that are paths. Qualified where a bare name is also a string or list method.
_ALL_POSITIONAL = ("os.path.join", "joinpath", "Path", "PurePath", "PurePosixPath", "PureWindowsPath")
_FIRST = ("with_name", "with_suffix", "os.mkdir", "os.makedirs", "os.remove", "os.unlink", "os.rmdir", "os.listdir",
          "os.scandir", "os.path.exists", "os.path.isfile", "os.path.isdir", "shutil.rmtree", "atomic_json",
          "read_json", "write_json", "sqlite3.connect", "os.open", "io.open")
_FIRST_TWO = ("os.replace", "os.rename", "os.symlink", "os.link", "shutil.copy", "shutil.copy2", "shutil.copyfile",
              "shutil.copytree", "shutil.move")
_RESOURCE = ("user_resource", "resource_path", "system_resource")    # Blender's: (kind, path=...) -> a folder.
_TEMPORARY = ("mkdtemp", "mkstemp", "NamedTemporaryFile", "TemporaryDirectory")   # prefix=, suffix=, dir=.
_PRIVATE_API = ("allocate", "declare")   # private_roots: folders Angelo's own processes keep private state in.


def _path_arguments(node):
    """The argument expressions of ``node`` that name a path, or []."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return [node.left, node.right]
    if not isinstance(node, ast.Call):
        return []
    name = ast.unparse(node.func)
    last = name.rsplit(".", 1)[-1]
    keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
    if name.endswith(_ALL_POSITIONAL):
        return list(node.args)
    if last in ("with_name", "with_suffix", "atomic_json", "read_json", "write_json") or name.endswith(_FIRST) \
            or name == "open":
        return node.args[:1] + [keywords[k] for k in ("file", "path") if k in keywords]
    if name.endswith(_FIRST_TWO):
        return node.args[:2] + [keywords[k] for k in ("src", "dst") if k in keywords]
    if last in _RESOURCE:
        return node.args[1:] + [keywords[k] for k in ("path",) if k in keywords]
    if last in _TEMPORARY:
        return [keywords[k] for k in ("prefix", "suffix", "dir") if k in keywords]
    if last in _PRIVATE_API:
        return list(node.args) + list(keywords.values())
    return []


def _module_strings(tree):
    """{NAME: expression} of the module-level names whose value is a string, a tuple or list of them, or names."""
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            for name, value in _assigned(node):
                if isinstance(value, (ast.Name, ast.Attribute)) or any(_text(sub) is not None
                                                                       for sub in ast.walk(value)):
                    out[name] = value   # A string, or made of strings or of other constants; never a number.
    return out


def _assigned(node):
    """(name, value) of a module-level assignment, tuple unpacking included (``A, B = "a.json", "b.lock"``)."""
    for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
        if isinstance(target, ast.Name):
            yield target.id, node.value
        elif isinstance(target, ast.Tuple) and isinstance(node.value, ast.Tuple) \
                and len(target.elts) == len(node.value.elts):
            yield from ((name.id, value) for name, value in zip(target.elts, node.value.elts)
                        if isinstance(name, ast.Name))


def _builds_path(node):
    return any(isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.Div)
               or isinstance(sub, ast.Call) and ast.unparse(sub.func).endswith(_ALL_POSITIONAL) for sub in ast.walk(node))


class _Collector:
    """The keys an expression's path components name (see ``state_names``), across the add-on's modules."""

    def __init__(self):
        self.trees = dict(sources())
        self.strings = {key: _module_strings(tree) for key, tree in self.trees.items()}
        self.aliases = {key: _aliases(tree, key) for key, tree in self.trees.items()}

    def keys(self, module, node, literals=True, seen=None, out=None):
        """``out`` (a dict, key -> module) gains every name ``node`` is built from: a literal, the literal parts of an
        f-string, a concatenation or a ``%``/``format`` template, a conditional's branches, ``str()``/``fspath()`` of
        one, and the module-level constants it names, followed through names, imports, ``module.NAME`` and tuples.
        Never a subscript's key, a call's other arguments or an environment variable's name."""
        out = {} if out is None else out
        seen = set() if seen is None else seen
        modules, names = self.aliases[module]
        if _text(node) is not None:
            if literals and node.value.strip() and not node.value.startswith(" "):   # Not a message's tail.
                out.setdefault("path:" + node.value, module)
        elif isinstance(node, ast.JoinedStr):
            for part in node.values:
                self.keys(module, part.value if isinstance(part, ast.FormattedValue) else part, literals, seen, out)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
            self.keys(module, node.left, literals, seen, out)
            if isinstance(node.op, ast.Add):
                self.keys(module, node.right, literals, seen, out)
        elif isinstance(node, ast.IfExp):
            self.keys(module, node.body, literals, seen, out)
            self.keys(module, node.orelse, literals, seen, out)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for element in node.elts:
                self.keys(module, element, literals, seen, out)
        elif isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "format":
            self.keys(module, node.func.value, literals, seen, out)
        elif isinstance(node, ast.Call) and ast.unparse(node.func) in ("str", "os.fspath", "fspath") and node.args:
            self.keys(module, node.args[0], literals, seen, out)
        elif isinstance(node, ast.Name):
            if node.id in self.strings[module]:
                self.constant(module, node.id, seen, out)
            elif node.id in names:
                self.constant(*names[node.id], seen, out)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in modules:
            self.constant(modules[node.value.id], node.attr, seen, out)
        return out

    def constant(self, module, name, seen, out):
        if (module, name) in seen or name not in self.strings.get(module, {}):
            return
        seen.add((module, name))
        out["%s.%s" % (module, name)] = module
        self.keys(module, self.strings[module][name], False, seen, out)

    def path_keys(self, module, tree):
        """Every key the path arguments anywhere in ``tree`` (a module or one function) name."""
        out = {}
        for node in ast.walk(tree):
            for argument in _path_arguments(node):
                self.keys(module, argument, out=out)
        return out


def state_names():
    """{key: module} of every name a state file could be written under: every path argument's names (``_Collector``)
    in every module, keyword paths included (``user_resource(..., path=STATE_FOLDER)``, ``mkdtemp(prefix=...)``).
    Also every module-level string named like a path or shaped like a file name, used or not, so a constant waiting
    for its first use is judged too."""
    collector = _Collector()
    found = {}
    for key, tree in collector.trees.items():
        found.update({name: module for name, module in collector.path_keys(key, tree).items() if name not in found})
        for name, value in collector.strings[key].items():
            text = _text(value)
            if (text is not None and (_NAMED.search(name) or _FILE_SHAPE.match(text))) or \
                    (text is None and _NAMED.search(name) and _builds_path(value)):
                found.setdefault("%s.%s" % (key, name), key)
    return found


# ---- Operators that write grant state are grants (break-it S1) ----------------------------------------------------

# A write: a call that creates, changes or removes a file or folder. Bare method names are judged with the path they
# write (a function must also build a path from grant state), so a string's replace never counts alone.
_WRITES = {"atomic_json", "write_json", "write_text", "write_bytes", "dump", "replace", "rename", "unlink", "remove",
           "rmtree", "rmdir", "mkdir", "makedirs", "touch", "copy", "copy2", "copyfile", "copytree", "move", "symlink"}


def _pure_names(tree):
    """Names a module binds to ``dataclasses``' functions (``from dataclasses import replace [as copy]``): a call of one
    copies a value and writes nothing, though ``replace`` is also ``os.replace``'s name."""
    return frozenset(alias.asname or alias.name for node in ast.walk(tree)
                     if isinstance(node, ast.ImportFrom) and node.module == "dataclasses" for alias in node.names)


def _text_substitution(node):
    """``text.replace("a", "b"[, n])``: a string method, told apart from a path's or a module's ``replace`` by its
    first two arguments, both string literals (any other ``replace(a, b)`` may move a file and counts as a write)."""
    return (isinstance(node.func, ast.Attribute) and node.func.attr == "replace" and len(node.args) >= 2
            and all(isinstance(arg, ast.Constant) and isinstance(arg.value, str) for arg in node.args[:2]))


def _writes(function, pure=frozenset()):
    """Whether ``function`` writes a file: a call of a writing name, or ``open`` in a writing mode. ``pure``: names its
    module binds to functions that write nothing (``_pure_names``)."""
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if isinstance(node.func, ast.Name) and name in pure or _text_substitution(node):
                continue
            if name in _WRITES:
                return True
            if name == "open":   # open(path, "w") or path.open("a"): a mode that writes.
                modes = [_text(arg) for arg in node.args] + [_text(k.value) for k in node.keywords if k.arg == "mode"]
                if any(mode and set(mode) & set("wax+") for mode in modes):
                    return True
    return False


def _qualnames(tree):
    """{id(function node): "Class.method" or "function"} of every function in ``tree``."""
    out = {}

    def visit(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, prefix + child.name + ".")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out[id(child)] = prefix + child.name
                visit(child, prefix + child.name + ".<locals>.")
            else:
                visit(child, prefix)
    visit(tree, "")
    return out


def _self_paths(collector, module, cls):
    """{attribute: keys} of the paths a class keeps on ``self`` (``self.path = directory / NAME``), followed through
    other attributes it builds them from (``self.lock = self.path.with_name(...)``)."""
    found = {}
    assignments = [(node.targets[0].attr, node.value) for node in ast.walk(cls) if isinstance(node, ast.Assign)
                   and isinstance(node.targets[0], ast.Attribute) and getattr(node.targets[0].value, "id", "") == "self"]
    for _round in range(3):   # Attributes built from attributes, a few deep.
        for attribute, value in assignments:
            keys = set(collector.path_keys(module, ast.Expression(value)))
            keys |= {key for node in ast.walk(value) if isinstance(node, ast.Attribute)
                     and getattr(node.value, "id", "") == "self" for key in found.get(node.attr, ())}
            if isinstance(value, ast.BinOp) or _text(value) is not None or isinstance(value, ast.Call):
                keys |= set(collector.keys(module, value))
            found[attribute] = found.get(attribute, set()) | keys
    return found


def grant_state_writers(resolver=None):
    """{(module, function name): sorted GRANT_STATE keys} of every function that writes a path built from grant state:
    in its own body, or kept on ``self`` by its class (a store's ``save`` writing ``self.path``); and, to a fixpoint,
    every function that calls one (``_Resolver``). Not followed: a write through an object whose path arrives as an
    argument (a chat's session writer), which the operators' own reasons judge by hand."""
    collector, resolver = _Collector(), resolver or _Resolver()
    grant = set(GRANT_STATE)
    writers = {}
    qualnames = {module: _qualnames(tree) for module, tree in resolver.trees.items()}
    for module, tree in resolver.trees.items():
        owners = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                paths = None
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        owners[id(item)] = (node, lambda node=node: _self_paths(collector, module, node))
        cache, pure = {}, _pure_names(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not _writes(node, pure):
                continue
            keys = set(collector.path_keys(module, node))
            owner = owners.get(id(node))
            if owner is not None:
                if id(owner[0]) not in cache:
                    cache[id(owner[0])] = owner[1]()
                used = {sub.attr for sub in ast.walk(node) if isinstance(sub, ast.Attribute)
                        and getattr(sub.value, "id", "") == "self"}
                keys |= {key for attribute in used for key in cache[id(owner[0])].get(attribute, ())}
            if keys & grant:
                writers.setdefault((module, qualnames[module][id(node)]), set()).update(keys & grant)
    changed = True
    while changed:   # A caller of a writer writes what it writes.
        changed = False
        for module, tree in resolver.trees.items():
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                gained, own = set(), (module, qualnames[module][id(node)])
                for other, function in resolver.targets(module, node):
                    gained |= writers.get((other, qualnames[other][id(function)]), set())
                if gained - writers.get(own, set()):
                    writers.setdefault(own, set()).update(gained)
                    changed = True
    return {key: sorted(value) for key, value in writers.items()}
