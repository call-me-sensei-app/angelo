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
chat_replies chat_notices chat_start_states chat_toggle chat_hint chat_launcher
checkpoint_review_ui checkpoint_settings_ui console director display_text editor_types first_run flow_approvals
flow_chat flow_nodes flow_recipe_ui flow_ui intro key_store managed_policy preferences_ui preview_branding
project_brief_rebind_ui quick_connect saved_keys_ui settings_import_card settings_import_ui snapshot_blocks splash_setup setup_card start_card firstlaunch_controls studio studio_approvals
studio_chat team_account_ui team_chat team_connections team_errors team_library_approvals team_pages
team_status_bar team_token_store ui voice_ui welcome_screen
app_notices card_draw card_host card_kit crash_card crash_ui update_card update_restart update_ui updates_prefs whatsnew_card
approval_steps chat_card_draw chat_cards detail_keys review_card review_text job_approvals job_chat job_desktop job_ui trusted_scripts_ui recovery_review
activity_text chat_rows live_progress live_row
stop_notice
ui_kit ui_kit_builder ui_kit_controls ui_kit_field ui_kit_rows ui_kit_select ui_kit_status
chat_add_menu chosen_tools
chat_usage usage_view
providers_menu providers_page_draw providers_page_host providers_page_model providers_page_team providers_sheet
providers_page_external external_ui
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
""".split())

# Modules with UI-looking code that never shows translatable chrome, and why.
NON_UI_MODULES = {
    "input_policy": "which modal operators hold a canvas's input; returns Blender's operator names, which the chat "
                    "translates (Blender's Operator context) where it shows them",
    "chosen_tools_host": "performs the person's chosen tools; its outcome text is model-facing data, and its chat "
                         "rows are notice() templates",
    "flow_spec": "model-facing flow tool schema and node templates",
    "ui_text": "the translation helpers themselves; no interface text of their own",
    "rtl_text": "right-to-left text processing; no interface text of its own",
    "canvas_rtl": "right-to-left canvas drawing; no interface text of its own",
    "canvas_wrap": "incremental wrapping of already-localized canvas text; no interface text of its own",
    "text_layout": "line breaking; no interface text of its own",
    "provider_registry": "provider metadata; only marks the provider group names that Preferences shows",
    "font_watch": "rebinds the canvases' fonts after Blender reloads its own; no interface text",
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
    ("", "Native coding-agent chat with approved Blender tools", "source/blender/makesrna/intern/rna_screen.cc"),
    ("", "Generative AI", "source/blender/makesrna/intern/rna_screen.cc"),
    ("", "Image and 3D generation with a local media library", "source/blender/makesrna/intern/rna_screen.cc"),
    ("", "AI Providers", "source/blender/makesrna/intern/rna_userdef.cc"),
    ("", "Connect AI subscriptions and API keys for the native scene agent",
     "source/blender/makesrna/intern/rna_userdef.cc"),
    ("Operator", "About Angelo", "source/blender/windowmanager/intern/wm_splash_screen.cc"),
    ("", "Open a window with information about Angelo and its Blender base",
     "source/blender/windowmanager/intern/wm_splash_screen.cc"),
    ("Operator", "Quit Angelo", "source/blender/windowmanager/intern/wm_operators.cc"),
    ("", "Quit Angelo", "source/blender/windowmanager/intern/wm_operators.cc"),
) + _release_notes_msgids()

# The repository's own msgfmt (from a build) when set; otherwise gettext's.
MSGFMT = os.environ.get("ANGELO_MSGFMT", "")

# Blender's own UI scripts that Angelo edited (the AI Providers preferences panel, the renamed top-bar menu and the
# Help menu, the URL presets with the splash and About): their text is Blender's except what Blender's catalogues
# do not know.
SHARED_PYTHON_FILES = ("scripts/startup/bl_ui/space_userpref.py", "scripts/startup/bl_ui/space_topbar.py",
                       "scripts/startup/bl_operators/wm.py")
