/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup bke
 *
 * Read-compatibility for identifiers saved before the 2026-10-07 product rename.
 * The tables are frozen: they list exactly what the product shipped under its
 * pre-rename names and must not grow with new Angelo identifiers.
 */

#include <cstring>

#include "BLI_listbase_iterator.hh"
#include "BLI_string.h"
#include "BLI_utildefines.h"

#include "DNA_userdef_types.h"
#include "DNA_windowmanager_types.h"

#include "BKE_addon.h"
#include "BKE_angelo_legacy_names.hh"

namespace blender {

struct LegacyName {
  const char *legacy;
  const char *current;
};

static const char *ADDON_MODULE = "angelo_agent";
static const char *LEGACY_ADDON_MODULE = "toonlab_agent";

/* Operators and menus, in their stored `PREFIX_OT_name` / `PREFIX_MT_name` form. */
static const LegacyName legacy_idnames[] = {
    {"SCULPT_OT_sensei_sculpt_pass", "SCULPT_OT_angelo_sculpt_pass"},
    {"SCULPT_OT_sensei_sculpt_program", "SCULPT_OT_angelo_sculpt_program"},
    {"SENSEI_MT_workspace_mode", "ANGELO_MT_workspace_mode"},
    {"SENSEI_OT_director_input", "ANGELO_OT_director_input"},
    {"SENSEI_OT_director_toggle", "ANGELO_OT_director_toggle"},
    {"SENSEI_OT_enhancer_refresh", "ANGELO_OT_enhancer_refresh"},
    {"SENSEI_OT_enhancer_settings", "ANGELO_OT_enhancer_settings"},
    {"SENSEI_OT_studio_asset", "ANGELO_OT_studio_asset"},
    {"SENSEI_OT_studio_asset_action", "ANGELO_OT_studio_asset_action"},
    {"SENSEI_OT_studio_enhance", "ANGELO_OT_studio_enhance"},
    {"SENSEI_OT_studio_export", "ANGELO_OT_studio_export"},
    {"SENSEI_OT_studio_files", "ANGELO_OT_studio_files"},
    {"SENSEI_OT_studio_folder", "ANGELO_OT_studio_folder"},
    {"SENSEI_OT_studio_generate", "ANGELO_OT_studio_generate"},
    {"SENSEI_OT_studio_import_3d", "ANGELO_OT_studio_import_3d"},
    {"SENSEI_OT_studio_input", "ANGELO_OT_studio_input"},
    {"SENSEI_OT_studio_prompt", "ANGELO_OT_studio_prompt"},
    {"SENSEI_OT_studio_url", "ANGELO_OT_studio_url"},
    {"SENSEI_OT_workspace_mode", "ANGELO_OT_workspace_mode"},
    {"TOONLAB_MT_approval", "ANGELO_MT_approval"},
    {"TOONLAB_MT_chat", "ANGELO_MT_chat"},
    {"TOONLAB_MT_editor_type", "ANGELO_MT_editor_type"},
    {"TOONLAB_MT_models", "ANGELO_MT_models"},
    {"TOONLAB_MT_sessions", "ANGELO_MT_sessions"},
    {"TOONLAB_MT_view", "ANGELO_MT_view"},
    {"TOONLAB_OT_accept_native_preview", "ANGELO_OT_accept_native_preview"},
    {"TOONLAB_OT_api_key", "ANGELO_OT_api_key"},
    {"TOONLAB_OT_apply_approved_tool", "ANGELO_OT_apply_approved_tool"},
    {"TOONLAB_OT_approve", "ANGELO_OT_approve"},
    {"TOONLAB_OT_attach_reference", "ANGELO_OT_attach_reference"},
    {"TOONLAB_OT_cancel_signin", "ANGELO_OT_cancel_signin"},
    {"TOONLAB_OT_changes", "ANGELO_OT_changes"},
    {"TOONLAB_OT_chat_input", "ANGELO_OT_chat_input"},
    {"TOONLAB_OT_check_signin", "ANGELO_OT_check_signin"},
    {"TOONLAB_OT_checkpoint_retention", "ANGELO_OT_checkpoint_retention"},
    {"TOONLAB_OT_checkpoint_settings", "ANGELO_OT_checkpoint_settings"},
    {"TOONLAB_OT_commit_native_job", "ANGELO_OT_commit_native_job"},
    {"TOONLAB_OT_connection_check", "ANGELO_OT_connection_check"},
    {"TOONLAB_OT_connection_load_key", "ANGELO_OT_connection_load_key"},
    {"TOONLAB_OT_connection_menu", "ANGELO_OT_connection_menu"},
    {"TOONLAB_OT_connection_remove", "ANGELO_OT_connection_remove"},
    {"TOONLAB_OT_connection_signin", "ANGELO_OT_connection_signin"},
    {"TOONLAB_OT_connection_use", "ANGELO_OT_connection_use"},
    {"TOONLAB_OT_copy_login", "ANGELO_OT_copy_login"},
    {"TOONLAB_OT_custom_model", "ANGELO_OT_custom_model"},
    {"TOONLAB_OT_decline", "ANGELO_OT_decline"},
    {"TOONLAB_OT_discard_native_preview", "ANGELO_OT_discard_native_preview"},
    {"TOONLAB_OT_dock_side", "ANGELO_OT_dock_side"},
    {"TOONLAB_OT_editor_type", "ANGELO_OT_editor_type"},
    {"TOONLAB_OT_model_picker", "ANGELO_OT_model_picker"},
    {"TOONLAB_OT_multiline", "ANGELO_OT_multiline"},
    {"TOONLAB_OT_native_signin", "ANGELO_OT_native_signin"},
    {"TOONLAB_OT_new_chat", "ANGELO_OT_new_chat"},
    {"TOONLAB_OT_new_connection", "ANGELO_OT_new_connection"},
    {"TOONLAB_OT_open_chat", "ANGELO_OT_open_chat"},
    {"TOONLAB_OT_performance_summary", "ANGELO_OT_performance_summary"},
    {"TOONLAB_OT_pop_out", "ANGELO_OT_pop_out"},
    {"TOONLAB_OT_project_brief", "ANGELO_OT_project_brief"},
    {"TOONLAB_OT_refresh_models", "ANGELO_OT_refresh_models"},
    {"TOONLAB_OT_remove_reference", "ANGELO_OT_remove_reference"},
    {"TOONLAB_OT_restore_checkpoint", "ANGELO_OT_restore_checkpoint"},
    {"TOONLAB_OT_return_dock", "ANGELO_OT_return_dock"},
    {"TOONLAB_OT_review_tool", "ANGELO_OT_review_tool"},
    {"TOONLAB_OT_select_model", "ANGELO_OT_select_model"},
    {"TOONLAB_OT_sensei_about", "ANGELO_OT_about"},
    {"TOONLAB_OT_sensei_welcome", "ANGELO_OT_welcome"},
    {"TOONLAB_OT_set_approval", "ANGELO_OT_set_approval"},
    {"TOONLAB_OT_set_effort", "ANGELO_OT_set_effort"},
    {"TOONLAB_OT_settings", "ANGELO_OT_settings"},
    {"TOONLAB_OT_stop", "ANGELO_OT_stop"},
    {"TOONLAB_OT_switch_session", "ANGELO_OT_switch_session"},
    {"TOONLAB_OT_toggle_fast", "ANGELO_OT_toggle_fast"},
    {"TOONLAB_OT_use_connection", "ANGELO_OT_use_connection"},
    {"TOONLAB_OT_use_project_brief", "ANGELO_OT_use_project_brief"},
    {"TOONLAB_OT_voice", "ANGELO_OT_voice"},
};

/* `SpaceConsole.language` values saved in .blend files. */
static const LegacyName legacy_console_languages[] = {
    {"toonlab", "angelo"},
    {"sensei_genai", "angelo_genai"},
};

/* Add-on properties that Quick Favorites may name (`bUserMenuItem_Prop.prop_id`). */
static const LegacyName legacy_properties[] = {
    {"toonlab_preferences_section", "angelo_preferences_section"},
};

template<size_t N> static const char *lookup(const LegacyName (&table)[N], const char *name)
{
  for (const LegacyName &entry : table) {
    if (STREQ(entry.legacy, name)) {
      return entry.current;
    }
  }
  return nullptr;
}

const char *BKE_angelo_legacy_idname(const char *idname)
{
  /* Cheap rejection: keymap items are created for every built-in operator. */
  if (!(STRPREFIX(idname, "TOONLAB_") || STRPREFIX(idname, "SENSEI_") ||
        STRPREFIX(idname, "SCULPT_OT_sensei_")))
  {
    return nullptr;
  }
  return lookup(legacy_idnames, idname);
}

const char *BKE_angelo_legacy_console_language(const char *language)
{
  return lookup(legacy_console_languages, language);
}

template<size_t N> static void rename_in_place(char (&name)[N], const char *current)
{
  if (current) {
    BLI_strncpy(name, current, N);
  }
}

static void rename_keymap_item(wmKeyMapItem *kmi)
{
  if (kmi) {
    rename_in_place(kmi->idname, BKE_angelo_legacy_idname(kmi->idname));
  }
}

void BKE_angelo_legacy_userdef_update(UserDef *userdef)
{
  if (bAddon *legacy = BKE_addon_find(&userdef->addons, LEGACY_ADDON_MODULE)) {
    if (BKE_addon_find(&userdef->addons, ADDON_MODULE)) {
      /* Preferences saved by Angelo itself are newer than the pre-rename entry. */
      BKE_addon_remove_safe(&userdef->addons, LEGACY_ADDON_MODULE);
    }
    else {
      /* Keeps the add-on's saved preferences (`bAddon.prop`). */
      STRNCPY(legacy->module, ADDON_MODULE);
    }
  }

  for (wmKeyMap &keymap : userdef->user_keymaps) {
    for (wmKeyMapItem &kmi : keymap.items) {
      rename_keymap_item(&kmi);
    }
    for (wmKeyMapDiffItem &kmdi : keymap.diff_items) {
      rename_keymap_item(kmdi.add_item);
      rename_keymap_item(kmdi.remove_item);
    }
  }

  for (bUserMenu &menu : userdef->user_menus) {
    for (bUserMenuItem &item : menu.items) {
      if (item.type == USER_MENU_TYPE_OPERATOR) {
        bUserMenuItem_Op *op = reinterpret_cast<bUserMenuItem_Op *>(&item);
        rename_in_place(op->op_idname, BKE_angelo_legacy_idname(op->op_idname));
      }
      else if (item.type == USER_MENU_TYPE_MENU) {
        bUserMenuItem_Menu *mt = reinterpret_cast<bUserMenuItem_Menu *>(&item);
        rename_in_place(mt->mt_idname, BKE_angelo_legacy_idname(mt->mt_idname));
      }
      else if (item.type == USER_MENU_TYPE_PROP) {
        bUserMenuItem_Prop *prop = reinterpret_cast<bUserMenuItem_Prop *>(&item);
        rename_in_place(prop->prop_id, lookup(legacy_properties, prop->prop_id));
      }
    }
  }
}

}  // namespace blender
