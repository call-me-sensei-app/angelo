# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which Angelo sources are scanned, and how.

``UI_MODULES`` draw or build interface text: every human-readable literal in
them must be wrapped (``n_``/``iface_``/``tip_``/``rpt_``/``plural_``/``notice``),
be a Blender-native literal (``text=``, ``bl_label``, property ``name=``), or
carry an inline ``# i18n: <reason>`` for text a user never reads translated
(model-facing wording, log lines, protocol constants). Every other module is
checked for chat notices and built native text only.

A new add-on module that looks like UI (it draws, defines operators or panels,
or already marks strings) must be classified here: the host test fails until it
is added to ``UI_MODULES`` or ``NON_UI_MODULES`` with a reason.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
STARTUP_FILES = ("scripts/startup/angelo_default_layout.py", "scripts/modules/_console_angelo.py",
                 "scripts/modules/_console_angelo_genai.py")
LOCALE_DIR = ROOT / "locale/angelo"
POT = LOCALE_DIR / "angelo.pot"

UI_MODULES = frozenset("""
about_card agent_workspace_ui approvals approval_review canvas_controls chat_composer chat_drop chat_popovers chat_settings chat_titles chat_view
chat_welcome checkpoint_images checkpoint_restore_ui checkpoint_seal connect_hero team_access
chat_replies chat_notices chat_start_states chat_toggle chat_hint chat_launcher asset_shelf_add media_shelf shelf_empty studio_toggle flow_open flow_window flow_start flow_menu studio_to_flow flow_text_field flow_setup asset_browser_entry asset_browser_states studio_workspaces chat_destinations
checkpoint_review_ui checkpoint_settings_ui console director display_text editor_types first_run flow_approvals
flow_chat flow_drop flow_nodes flow_recipe_ui flow_ui intro key_store managed_policy preferences_ui preview_branding
project_brief_rebind_ui quick_connect saved_keys_ui settings_import_card settings_import_ui snapshot_blocks splash_setup setup_card start_card firstlaunch_controls studio studio_draw studio_input studio_ops studio_asset_ops studio_library_ops media_drop studio_item_menu media_menus studio_approvals
studio_chat team_account_ui team_chat team_connections team_errors team_library_approvals team_pages
team_status_bar team_token_store ui voice_ui welcome_screen welcome_slides welcome_art flow_character_nodes flow_character_ui studio_workflow_ops studio_workflows
app_notices card_draw card_host card_kit crash_card crash_ui update_card update_restart update_ui updates_prefs whatsnew_card
approval_steps chat_card_draw chat_cards detail_keys review_card review_text job_approvals job_chat job_desktop job_ui trusted_scripts_ui recovery_review
activity_text chat_rows live_progress live_row chat_guard chat_blocks changes_popup
stop_notice
ui_kit ui_kit_builder ui_kit_controls ui_kit_field ui_kit_rows ui_kit_select ui_kit_status
chat_add_menu chosen_tools
web_export_ui web_preview web_shader web_shader_ui engine_link_ui
chat_usage usage_view
providers_menu providers_page_draw providers_page_host providers_page_model providers_page_team providers_sheet providers_page_rail
providers_page_external external_ui
session_rail session_rail_draw rail_minimized live_view_draw worker_approval worker_dispatch document_scene document_identity document_offer
worker_consent_lead worker_mirror_cards worker_mirror_opens
""".split())

# Modules whose text a model reads (tool specs, schemas, guidance, observations, prompts). They never translate:
# the host test forbids ui_text there, and the extractor skips them (their literals are not interface text).
MODEL_FACING = frozenset("""
tool_specs plan_schema agent_guidance tool_content schema_compaction strict_schema action_schemas action_catalog
scene_spec validation_spec program_spec memory_spec sculpt_spec view_spec studio_spec flow_spec anim_spec
node_edit_spec team_library_spec execution_spec identifiers blender_tools model_profiles model_routing turn_context
context_ledger context_rebase memory_context job_spec job_store
read_spec data_path data_read data_read_rows data_read_values data_read_policy
blend_file blend_file_listing blend_file_paths
external_mcp_tools
worker_spec visibility_facts
""".split())

# Modules with UI-looking code that never shows translatable chrome, and why.
NON_UI_MODULES = {
    "rail_redraw": "redraw scheduling and per-window badge signatures; no interface text of its own",
    "chat_tables": "lays out a reply's markdown table (geometry and runs of the model's own text); no interface "
                   "text of its own",
    "rig_host": "the hand-rigging tools' results and refusals: model-facing English data, no interface text",
    "hand_pose": "hand pose solving for the rig tools: model-facing English results, no interface text",
    "input_policy": "which modal operators hold a canvas's input; returns Blender's operator names, which the chat "
                    "translates (Blender's Operator context) where it shows them",
    "chosen_tools_host": "performs the person's chosen tools; its outcome text is model-facing data, and its chat "
                         "rows are notice() templates",
    "flow_spec": "model-facing flow tool schema and node templates",
    "flow_templates": "bundled flow templates: titles and summaries are n_ constants drawn by flow_open, the + menu and "
                      "Media Studio; prompts and step names are model-visible English",
    "flow_graph": "flow evaluation, no drawing ('layout' appears only in a docstring)",
    "flow_parts": "parts-sheet geometry, no drawing ('layout' appears only in a docstring)",
    "studio_workflow_view": "what the Workflows tab shows (pure, no drawing); its part-kind titles are n_ constants "
                            "drawn by studio_workflows",
    "character_compose": "Place Character's scene code, no drawing ('layout.' is the character_layout module)",
    "parts_ground": "Fit to My Image's request and reply: its prompt is model input (English); the notices of its "
                    "failures are n_ templates in studio_enhance",
    "ui_text": "the translation helpers themselves; no interface text of their own",
    "worker_sessions": "starts the lead's workers: its brief and status are model-facing English; the rail draws "
                       "the states it reads",
    "worker_queue": "worker places: its start answer and a parked chat's refusal are model-facing English; its one "
                    "chat notice is an n_ template shown through display_text",
    "worker_ending": "why a worker stopped for good: n_ reasons as display_text.Text, model-facing as English and nested "
                     "in a parked chat's restart notice (translated whole where shown); it draws nothing",
    "worker_save_recovery": "failed-save recovery: model-facing status facts and n_ chat notice templates",
    "worker_chat_lifecycle": "a worker chat's ops (open, close, hold, lease): its errors are log and model-facing "
                             "English; its one chat notice (an open that failed) is an n_ template shown as a notice",
    "worker_merge_gate": "a merge's worker holds and leases: its refusals are model-facing English; its one chat "
                         "notice (a merge a worker did not let read) is an n_ template shown as a notice",
    "worker_mirror": "a worker-hosted chat's mirror: its refusals are n_ templates raised as display_text.Text, which "
                     "the chat translates where it shows them; it draws nothing",
    "worker_mirror_ops": "a mirror's asynchronous chat ops: its chat rows are n_ notice() templates and its status "
                         "word an n_ constant; it draws nothing",
    "worker_mirror_pins": "the lead's pinned route and approval of a worker chat: its one chat row (TAMPERED) is an n_ "
                          "notice() template; its log line is English; it draws nothing",
    "worker_paths": "how long a worker's file paths get: its refusal (TOO_LONG) is an n_ notice() template for the "
                    "lead's chat, and the same English sentence goes to the model",
    "worker_part_failures": "a worker's failed part save: its one chat row (UNSAVED) is an n_ notice() template; its "
                            "log line is English; it draws nothing",
    "worker_mirror_settle": "an unanswered Approve settled from the worker's account: its one chat row (LOST) is an "
                            "n_ notice() template; it draws nothing",
    "runtime_relay": "a worker's model runtime launches through Angelo: the n_ notice() template of a launch Angelo "
                     "refused, kept by the chat's error row",
    "worker_reasons": "why a worker chat's request did not go through: n_ reason templates nested in chat notices "
                      "(translated whole where shown); its log lines are English; it draws nothing",
    "worker_resume": "the worker-done event: its request is model-facing English; its one chat notice (a send that "
                     "failed) is an n_ template shown through display_text",
    "worker_routing": "which document a chat works on: its refusals are model-facing English; its chat notices (why "
                      "a worker's turn ended, a message not sent) are n_ templates shown through display_text",
    "live_view": "links worker parts into Angelo's live scenes; only log lines, no interface text",
    "worker_merge": "merges workers' parts: its refusals and results are model-facing English, and its 'layout' is a "
                    "worker's reported scene layout, not Blender's UI layout",
    "worker_placement": "checks where a merge placed each copy: its reasons are model-facing English",
    "worker_material_reach": "finds what a material swap would reach (dependencies, renders): returns data to the "
                             "merge and draws nothing; its refusals are model-facing English",
    "session_rail_input": "input routing for the session rail and live views; its operator's label and description "
                          "Blender translates itself, and it draws nothing",
    "session_rail_strip": "pure layout of the Sessions panel's strips (the session rail's tiles side by side); no "
                          "interface text of its own (session_rail_draw draws and translates it)",
    "rtl_text": "right-to-left text processing; no interface text of its own",
    "canvas_rtl": "right-to-left canvas drawing; no interface text of its own",
    "canvas_wrap": "incremental wrapping of already-localized canvas text; no interface text of its own",
    "text_layout": "line breaking; no interface text of its own",
    "provider_registry": "provider metadata; only marks the provider group names that Preferences shows",
    "studio_assets": "Media Studio's asset files: marks the two catalogue names (n_) Blender's Asset Browser shows; "
                     "asset names, descriptions and tags are the person's prompts, the rest file contents",
    "studio_assets_host": "writes the catalogue names in the interface language (iface_); no interface of its own",
    "media_place": "places Media Studio items for media_drop; its refusals are n_ constants media_drop shows with "
                   "error_text, the rest data names",
    "studio_assets_space": "the Asset Browser library's files and size; marks the four size units (n_) its caller "
                           "shows with iface_, the rest file contents",
    "studio_assets_landing": "packs the pictures of appended Media Studio image assets; no interface text, only log "
                             "lines",
    "font_watch": "rebinds the canvases' fonts after Blender reloads its own; no interface text",
    "top_bar_fit": "measures labels its callers already translated, to size their top-bar buttons; no text of its own",
    "job_manager": "background-job lifecycle: its refusals are model-facing English; the receipt notes it marks "
                   "with n_ are stored English and translated where the job card shows them",
    "job_runners": "render and export jobs: refusals are model-facing English; marked reasons and notes are "
                   "translated where the job card shows them",
    "job_bakes": "bake jobs: refusals are model-facing English; marked reasons and notes are translated where the "
                 "job card shows them",
    "job_fluid": "Mantaflow bake jobs: refusals are model-facing English; marked reasons and notes are translated "
                 "where the job card shows them",
    "job_desktop_prefs": "the job_desktop preference properties, whose names and descriptions Blender translates "
                         "itself; the AI Providers page shows them",
    "render_extensions": "add-on or script Python a render or bake runs: refusals are model-facing English; the "
                         "queued-change reason it marks with n_ is translated where the job card shows it",
    "job_host": "the job tools' executor adapter and scene guard: refusals are model-facing English; the locked-turn "
                "error is a display_text.Text translated where the chat shows it",
    "job_recovery": "an animation render's frames kept after a crash: receipt notes are model-facing English; the "
                    "notes and resume reasons it marks with n_ are translated where the job card shows them",
    "job_resume": "rendering the frames a kept render lacks: its refusal reasons are marked with n_ and translated where "
                  "the job card shows them",
    "job_render_keep": "an animation render's end that keeps its finished frames: reasons and notes are model-facing "
                       "English, marked with n_ and translated where the job card shows them",
    "web_compat": "web export findings: marked templates (n_) the preview page translates with render(); codes, "
                  "node labels and object names are data",
    "web_export": "web export settings and reports: model-facing English, marked notes (n_) translated on the page",
    "godot_link_assets": "Godot asset rules and refusals: English the Godot plugin shows (its text is English)",
    "engine_link_assets": "shared engine-link asset rules; refusal texts come from each engine's adapter",
    "web_host": "the agent tool's host route; refusals and results are model-facing English",
    "web_export_spec": "model-facing tool schema",
    "web_facts": "reads a scene into plain facts for web_compat; no text of its own",
    "web_merge": "joins glTF primitives; no text of its own",
    "web_shader_spec": "web shader definitions: uniform names, GLSL and extras are data",
    "view_web": "the WEB picture source; its results and refusals are model-facing English",
    "web_preview_server": "HTTP server: headers, paths and protocol constants",
    "job_exports": "USD and Alembic export jobs: refusals are model-facing English; marked reasons are translated "
                   "where the job card shows them",
    "job_outputs": "anchored job output publication: its refusals are model-facing English; the fixed reasons it "
                   "marks with n_ are translated where the job card shows them",
    "data_read_values": "model-facing value encoding of angelo_read_data: it names the RNA property bl_label "
                        "among the label properties and draws nothing",
    "script_errors": "a reviewed script that raised: its tool result is model-facing English; the three chat rows "
                     "are notices, translated where the chat shows them",
    "view_editor": "editor captures of angelo_view: results and refusals are model-facing English; the chat row "
                   "announcing a capture is a notice, translated where the chat shows it",
    "cache_affinity": "cache-affinity keys and the agent runtime's support check are wire data; the check's three "
                      "failures are notices, translated where the chat shows the error",
    "settings_import": "copies a Blender user folder; its product names and error texts go to the log, and the "
                       "cards that show the result translate their own words (settings_import_card)",
    "settings_import_apply": "applies a settings import; it only prints English log lines, and the notice that "
                             "reports the result is worded in settings_import_card",
    "blender_folders": "names stock Blender's user folders; no interface text",
    "base_mesh_thumbs": "draws the base meshes' preview pictures in the + menu rows; no interface text",
    "chrome_guard": "keeps Angelo's own editors without stock header, sidebar and toolbar; no interface text",
    "top_bar_anchor": "where the top bar's Chat button is drawn, for the hint, the Flows popup and GUI suites; no interface text",
    "layout_after": "runs Studio and Flows entries once a layout change has settled; it only prints English log lines",
}

# C/C++ files that differ from Blender 5.2.2 and carry interface text (repo-relative, as the extractor records them).
CPP_FILES = tuple(ROOT / path for path in """
source/blender/editors/space_console/space_console.cc
source/blender/makesrna/intern/rna_screen.cc
source/blender/makesrna/intern/rna_userdef.cc
source/blender/windowmanager/intern/wm_splash_screen.cc
source/blender/editors/sculpt_paint/mesh/sculpt_ops.cc
source/blender/editors/sculpt_paint/mesh/sculpt.cc
source/blender/makesrna/intern/rna_mesh.cc
source/blender/makesrna/intern/rna_object_api.cc
source/blender/makesrna/intern/rna_space.cc
source/blender/makesrna/intern/rna_wm_api.cc
source/blender/editors/screen/screen_ops.cc
source/blender/editors/asset/intern/asset_angelo_media.cc
source/blender/editors/asset/intern/asset_angelo_drag.cc
source/blender/editors/space_node/node_add.cc
source/blender/editors/space_node/space_node.cc
source/blender/editors/space_api/angelo_studio_drop.cc
""".split())

def _release_notes_msgids():
    """The bundled release's own lines (What's New and the update notice translate them when drawn)."""
    import json
    origin = "scripts/addons_core/angelo_agent/assets/release_notes.json"
    try:
        releases = json.loads((ROOT / origin).read_text(encoding="utf-8"))["releases"]
    except (OSError, ValueError, KeyError):
        return ()
    lines = []
    for release in releases:
        lines += [release["summary"], *release["highlights"]]
        for section in release["sections"]:
            lines += [section["heading"], *section["items"]]
    return tuple(("", line, origin) for line in dict.fromkeys(lines) if line)


# Text Angelo adds to Blender's own RNA from C++ (dynamic enum items, renamed operators): (context, msgid, origin).
# The RNA dump records these under Blender's class names, so they are listed here and checked by the native suite.
EXTRA_MSGIDS = (
    ("", "AI", "source/blender/makesrna/intern/rna_screen.cc"),
    ("", "Coding Agent", "source/blender/makesrna/intern/rna_screen.cc"),
    ("", "Native coding-agent chat with approved Angelo tools", "source/blender/makesrna/intern/rna_screen.cc"),
    ("", "Media Studio", "source/blender/makesrna/intern/rna_screen.cc"),
    ("", "Image and 3D generation with a local media library", "source/blender/makesrna/intern/rna_screen.cc"),
    ("", "AI Providers", "source/blender/makesrna/intern/rna_userdef.cc"),
    ("", "Connect AI subscriptions and API keys for the native scene agent",
     "source/blender/makesrna/intern/rna_userdef.cc"),
    ("Operator", "About Angelo", "source/blender/windowmanager/intern/wm_splash_screen.cc"),
    ("", "Open a window with information about Angelo and its Blender base",
     "source/blender/windowmanager/intern/wm_splash_screen.cc"),
    ("Operator", "Quit Angelo", "source/blender/windowmanager/intern/wm_operators.cc"),
    ("", "Quit Angelo", "source/blender/windowmanager/intern/wm_operators.cc"),
    # Blender's bundled glTF importer: the warning for a file with morph targets imported unwelded (its
    # UNWELDED_MORPH_TARGETS, which test_native_gltf_morph keeps equal to this copy).
    ("", "This file has morph targets (shape keys) and Merge Vertices is off: its seams will shade apart when the "
         "shape keys move, because split seam vertices stay split and glTF morph normals are not kept. Import with "
         "Merge Vertices on (merge_vertices=True) to weld them", "io_scene_gltf2/__init__.py"),
    # Blender's own text that names the running app, reworded to Angelo (doc/angelo/localization.rst, "The
    # running app is Angelo"). Blender's catalogues translate only the old wording, so Angelo's catalogue does.
    ('', 'Angelo was compiled without a viewport denoiser',
     'intern/cycles/blender/addon/properties.py'),
    ('', "List all repositories stored in Angelo's preferences.",
     'scripts/addons_core/bl_pkg/bl_extension_cli.py'),
    ('', 'Angelo was launched in offline mode, which cannot be changed at runtime',
     'scripts/addons_core/bl_pkg/bl_extension_ops.py'),
    ('', 'Launch Angelo without --offline-mode',
     'scripts/addons_core/bl_pkg/bl_extension_ops.py'),
    ('', 'Online access required to install or update. Launch Angelo without --offline-mode',
     'scripts/addons_core/bl_pkg/bl_extension_ops.py'),
    ('', 'Warning! Before unlocking, ensure another instance of Angelo is not running.',
     'scripts/addons_core/bl_pkg/bl_extension_ops.py'),
    ('', 'Import custom normals, if available (otherwise Angelo will recompute them)',
     'scripts/addons_core/io_scene_fbx/__init__.py'),
    ('', "Allows managing UI translations directly from Angelo (update main .po files, update scripts' translations, etc.)",
     'scripts/addons_core/ui_translate/__init__.py'),
    ('', 'Angelo sub-process exited with error code {:d}',
     'scripts/startup/bl_operators/assets.py'),
    ('', 'Image editor could not be launched, ensure that the path in User Preferences > File is valid, and Angelo has rights to launch it',
     'scripts/startup/bl_operators/image.py'),
    ('', 'Force reload the image if it is already opened elsewhere in Angelo',
     'scripts/startup/bl_operators/image_as_planes.py'),
    ('', 'Downloading asset previews requires Python, and this Angelo is built without',
     'source/blender/asset_system/intern/library_types/remote_library.cc'),
    ('', 'Downloading assets requires Python, and this Angelo is built without',
     'source/blender/asset_system/intern/library_types/remote_library.cc'),
    ('', 'Angelo is compiled without motion tracking library',
     'source/blender/blenkernel/intern/tracking_solver.cc'),
    ('', 'Log the channel list info in the terminal. This operator is only available in debug builds of Angelo',
     'source/blender/editors/animation/anim_ops.cc'),
    ('', "Press 'Enter' to sample outside of an Angelo window",
     'source/blender/editors/interface/eyedroppers/eyedropper_color.cc'),
    ('', 'Sample a color from the Angelo window to store in a property',
     'source/blender/editors/interface/eyedroppers/eyedropper_color.cc'),
    ('', 'Sample a color from the Angelo Window and create Grease Pencil material',
     'source/blender/editors/interface/eyedroppers/eyedropper_grease_pencil_color.cc'),
    ('', 'This file is managed by the Angelo asset system and cannot be overridden',
     'source/blender/editors/interface/templates/interface_template_status.cc'),
    ('', 'Enable this to run the export in the background, disable to block Angelo while exporting. This option is deprecated; EXECUTE this operator to run in the foreground, and INVOKE it to run as a background job',
     'source/blender/editors/io/io_alembic.cc'),
    ('', 'Enable this to run the import in the background, disable to block Angelo while importing. This option is deprecated; EXECUTE this operator to run in the foreground, and INVOKE it to run as a background job',
     'source/blender/editors/io/io_alembic.cc'),
    ('', 'Import custom normals, if available (otherwise Angelo will compute them)',
     'source/blender/editors/io/io_fbx_ops.cc'),
    ('', 'Run the export as a background job instead of blocking Angelo while exporting',
     'source/blender/editors/io/io_usd.cc'),
    ('', 'Capture a picture of the whole Angelo window',
     'source/blender/editors/screen/screendump.cc'),
    ('', 'Create a new image file without modifying the current image in Angelo',
     'source/blender/editors/space_image/image_ops.cc'),
    ('', 'Update the display of reports in Angelo UI (internal use)',
     'source/blender/editors/space_info/info_ops.cc'),
    ('', "Add images as individual strips, unless their filenames match Angelo's numbered sequence pattern, in which case they are grouped into a single image sequence",
     'source/blender/editors/space_sequencer/sequencer_add.cc'),
    ('', "Import all selected images as a single image sequence. The sequence of images does not have to match Angelo's numbered sequence pattern, so placeholders cannot be inferred",
     'source/blender/editors/space_sequencer/sequencer_add.cc'),
    ('', 'File Deleted Outside Angelo',
     'source/blender/editors/space_text/text_ops.cc'),
    ('', 'File Modified Outside Angelo',
     'source/blender/editors/space_text/text_ops.cc'),
    ('', 'File Modified Outside and Inside Angelo',
     'source/blender/editors/space_text/text_ops.cc'),
    ('', 'True if this preview icon has been modified by py script, and is no more auto-generated by Angelo',
     'source/blender/makesrna/intern/rna_ID.cc'),
    ('', 'True if this preview image has been modified by py script, and is no more auto-generated by Angelo',
     'source/blender/makesrna/intern/rna_ID.cc'),
    ('', 'Name of the new collection. Angelo will ensure it is unique within the collections of the Armature.',
     'source/blender/makesrna/intern/rna_armature.cc'),
    ('', 'Assets and catalogs in this library can be edited from the current Angelo instance',
     'source/blender/makesrna/intern/rna_asset.cc'),
    ('', "Identifier for the asset's catalog, used by Angelo to look up the asset's catalog path. Must be a UUID according to RFC4122.",
     'source/blender/makesrna/intern/rna_asset.cc'),
    ('', 'Show basic building blocks and utilities coming with Angelo',
     'source/blender/makesrna/intern/rna_asset.cc'),
    ('', 'Show the assets currently available in this Angelo session',
     'source/blender/makesrna/intern/rna_asset.cc'),
    ('', 'The attribute is meant for internal use by Angelo',
     'source/blender/makesrna/intern/rna_attribute.cc'),
    ('', 'Multiply color by alpha (recommended for Angelo input)',
     'source/blender/makesrna/intern/rna_dynamicpaint.cc'),
    ('', 'Generate and export Mantaflow script from current domain settings during bake. This is only needed if you plan to analyze the cache (e.g. view grids, velocity vectors, particles) in Mantaflow directly (outside of Angelo) after baking the simulation.',
     'source/blender/makesrna/intern/rna_fluid.cc'),
    ('', 'Object space normal mapping, compatible with Angelo render baking',
     'source/blender/makesrna/intern/rna_nodetree.cc'),
    ('', 'World space normal mapping, compatible with Angelo render baking',
     'source/blender/makesrna/intern/rna_nodetree.cc'),
    ('', 'The temp directory used by Angelo',
     'source/blender/makesrna/intern/rna_render.cc'),
        ('', 'Angelo compiled without Audaspace support',
     'source/blender/makesrna/intern/rna_sequencer_api.cc'),
    ('', 'Angelo preferences space data',
     'source/blender/makesrna/intern/rna_space.cc'),
    ('', 'Allow Angelo to access the internet. Add-ons that follow this setting will only connect to the internet if enabled. However, Angelo cannot prevent third-party add-ons from violating this rule.',
     'source/blender/makesrna/intern/rna_userdef.cc'),
    ('', 'Allow Angelo to check for updates upon launch',
     'source/blender/makesrna/intern/rna_userdef.cc'),
    ('', 'Auto detect best GPU for running Angelo',
     'source/blender/makesrna/intern/rna_userdef.cc'),
    ('', 'Compilation method used for compiling shaders in parallel. Subprocess requires a lot more RAM for each worker but might compile shaders faster on some systems. Requires restarting Angelo for changes to take effect. (OpenGL only)',
     'source/blender/makesrna/intern/rna_userdef.cc'),
    ('', 'GPU backend to use (requires restarting Angelo for changes to take effect)',
     'source/blender/makesrna/intern/rna_userdef.cc'),
    ('', 'Make this Angelo version open blend files for all users. Requires elevated privileges.',
     'source/blender/makesrna/intern/rna_userdef.cc'),
    ('', 'Number of shader compilation threads or subprocesses, clamped at the max threads supported by the CPU (requires restarting Angelo for changes to take effect). A higher number increases the RAM usage while reducing compilation time. A value of 0 will use automatic configuration. (OpenGL only)',
     'source/blender/makesrna/intern/rna_userdef.cc'),
    ('', 'Preferred device to select during detection (requires restarting Angelo for changes to take effect)',
     'source/blender/makesrna/intern/rna_userdef.cc'),
    ('', 'Raw input pressure value that is interpreted as 100% by Angelo',
     'source/blender/makesrna/intern/rna_userdef.cc'),
    ('', 'Select the tablet API to use for pressure sensitivity (may require restarting Angelo for changes to take effect)',
     'source/blender/makesrna/intern/rna_userdef.cc'),
    ('', 'Settings for interacting with Angelo data',
     'source/blender/makesrna/intern/rna_userdef.cc'),
    ('', 'Show Angelo memory usage',
     'source/blender/makesrna/intern/rna_userdef.cc'),
    ('', 'Show Angelo version string',
     'source/blender/makesrna/intern/rna_userdef.cc'),
    ('', 'Size multiplier to use when displaying custom user interface elements, so that they are scaled correctly on screens with different DPI. This value is based on operating system DPI settings and Angelo display scale.',
     'source/blender/makesrna/intern/rna_userdef.cc'),
    ('', 'Suggested line thickness and point size in pixels, for add-ons displaying custom user interface elements, based on operating system settings and Angelo UI scale',
     'source/blender/makesrna/intern/rna_userdef.cc'),
    ('', 'Theme settings for the Angelo Preferences',
     'source/blender/makesrna/intern/rna_userdef.cc'),
    ('', 'Disabled, Angelo was compiled without OpenVDB',
     'source/blender/nodes/geometry/node_geometry_util.cc'),
    ('', 'Disabled, Angelo was compiled without Bullet',
     'source/blender/nodes/geometry/nodes/node_geo_convex_hull.cc'),
    ('', 'Disabled, Angelo was compiled without OBJ I/O',
     'source/blender/nodes/geometry/nodes/node_geo_import_obj.cc'),
    ('', 'Disabled, Angelo was compiled without PLY I/O',
     'source/blender/nodes/geometry/nodes/node_geo_import_ply.cc'),
    ('', 'Disabled, Angelo was compiled without STL I/O',
     'source/blender/nodes/geometry/nodes/node_geo_import_stl.cc'),
    ('', 'Disabled, Angelo was compiled without OpenSubdiv',
     'source/blender/nodes/geometry/nodes/node_geo_mesh_subdivide.cc'),
    ('', 'Calculation using the MikkTSpace library, consistent with tangents used elsewhere in Angelo',
     'source/blender/nodes/geometry/nodes/node_geo_uv_tangent.cc'),
    ('', 'Angelo will start next time as it is now.',
     'source/blender/windowmanager/intern/wm_files.cc'),
    ('', 'Exit Angelo after saving',
     'source/blender/windowmanager/intern/wm_files.cc'),
    ('', 'Save the restored scene over "{}"?',
     'source/blender/windowmanager/intern/wm_files.cc'),
    ('', 'The snapshot from before the restore stays available.',
     'source/blender/windowmanager/intern/wm_files.cc'),
    ('', 'Save the restored scene from File > Save first',
     'source/blender/windowmanager/intern/wm_files.cc'),
    ('', 'This scene is a recovery copy, not your file: save it with File > Save As',
     'source/blender/windowmanager/intern/wm_files.cc'),
    ('', 'Recovered scene.blend',
     'source/blender/windowmanager/intern/wm_files.cc'),
    ('', 'Recovered scene (not your file)',
     'source/blender/windowmanager/intern/wm_window.cc'),
    ('', 'This file is managed by the Angelo asset system. It can only be',
     'source/blender/windowmanager/intern/wm_files.cc'),
    ('WindowManager', 'Angelo will now close.',
     'source/blender/windowmanager/intern/wm_platform_support.cc'),
    ('WindowManager', 'Newer graphics drivers might be available with better Angelo compatibility.',
     'source/blender/windowmanager/intern/wm_platform_support.cc'),
    ('WindowManager', 'Upgrading to the latest macOS version may improve Angelo support',
     'source/blender/windowmanager/intern/wm_platform_support.cc'),
    ('', 'Whether this Angelo installation is a sandboxed Microsoft Store version',
     'source/blender/makesrna/intern/rna_userdef.cc'),
    ('', "The name of a json file storing those settings (unfortunately, Angelo's system does not work here)",
     'scripts/addons_core/ui_translate/settings.py'),
    ('', 'Saving it with this version of Angelo (Blender %s) may cause loss of data.',
     'source/blender/windowmanager/intern/wm_files.cc'),
    ('', 'Keep the operator running with Angelo',
     'source/blender/makesrna/intern/rna_wm.cc'),
    ('', 'Preserve all data layers in the mesh, like UV maps and vertex groups. By default Angelo only computes the subset of data layers needed for viewport display and rendering, for better performance.',
     'source/blender/makesrna/intern/rna_object_api.cc'),
    ('', 'Active repository lock held by this session, either wait until the operation is finished or restart Angelo',
     'scripts/addons_core/bl_pkg/bl_extension_ops.py'),
) + _release_notes_msgids()

# The repository's own msgfmt (from a build) when set; otherwise gettext's.
MSGFMT = os.environ.get("ANGELO_MSGFMT", "")

# Blender's own UI scripts that Angelo edited (the AI Providers preferences panel, the renamed top-bar menu and the
# Help menu, the URL presets with the splash and About): their text is Blender's except what Blender's catalogues
# do not know.
SHARED_PYTHON_FILES = ("scripts/startup/bl_ui/space_userpref.py", "scripts/startup/bl_ui/space_topbar.py",
                       "scripts/startup/bl_operators/wm.py")
