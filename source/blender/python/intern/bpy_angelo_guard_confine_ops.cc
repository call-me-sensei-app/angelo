/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The confinement's gates on Blender's own entry points (doc/angelo/script_confinement.rst): an operator a confined
 * script calls (`bpy.ops`, `bpy_operator_function.cc`), and a file or folder path a confined script sets on any RNA
 * property or passes to an RNA function (`bpy_rna.cc`). Blender's writers (text and image saves, `.blend` and
 * library writes, render output, caches, exporters) then use only paths that were judged when they were set.
 */

#include <Python.h>

#include <cstring>
#include <string>

#include "BKE_blender_project.hh"
#include "BKE_context.hh"
#include "BKE_image_format.hh"
#include "BKE_main.hh"
#include "BKE_path_templates.hh"

#include "BLI_path_utils.hh"
#include "BLI_string.hh"
#include "BLI_utildefines.hh"

#include "DNA_freestyle_types.h"
#include "DNA_image_types.h"
#include "DNA_scene_types.h"
#include "DNA_text_types.h"
#include "DNA_userdef_types.h"
#include "DNA_windowmanager_types.h"

#include "RNA_access.hh"
#include "RNA_prototypes.hh"

#include "WM_api.hh"
#include "WM_types.hh"

#include "bpy_angelo_guard_confine.hh"
#include "bpy_angelo_guard_confine_changes.hh"
#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_paths.hh"

namespace blender {

using namespace angelo_guard;

/** The absolute form of a path as Blender's writers read it (`//` is the open file's folder). */
static std::string absolute_path(const char *value)
{
  char path[FILE_MAX];
  STRNCPY(path, value);
  BLI_path_abs(path, BKE_main_blendfile_path_from_global());
  return path;
}

/** `change`: what the record keeps of it. A file an operator only reads, or a path a script sets, is NAMED (code read
 * from it later is the script's; never its authorship): only what a writer writes is the script's file (the
 * coordinator's ruling on the re-read of f9f24cc6ffd; the write itself is recorded where it happens, too). */
static const char *judge_written(const char *value, const Change change = Change::Named)
{
  if (value == nullptr || value[0] == '\0') {
    return nullptr;
  }
  const std::string path = absolute_path(value);
  switch (folders().judge(path, true, -1, false, change)) {
    case Refusal::Private:
      return PRIVATE;
    case Refusal::Startup:
      return STARTUP;
    case Refusal::ToolOutput:
      return TOOL_OUTPUT;
    case Refusal::None:
      break;
  }
  return nullptr;
}

static bool path_subtype(PropertyRNA *prop)
{
  const PropertySubType subtype = RNA_property_subtype(prop);
  return subtype == PROP_FILEPATH || subtype == PROP_DIRPATH;
}

bool BPY_angelo_guard_is_grant_operator(const char *idname)
{
  if (idname == nullptr || idname[0] == '\0') {
    return false;
  }
  char py_idname[OP_MAX_TYPENAME];
  WM_operator_py_idname(py_idname, idname);
  return BPY_angelo_guard_operator_refusal_id(idname, py_idname) == GRANT;
}

const char *BPY_angelo_guard_path_property_refusal(PropertyRNA *prop, const char *value)
{
  if (!path_subtype(prop) || !confined_now()) {
    return nullptr;
  }
  return judge_written(value);
}

/** Preferences and the keymap: `U` itself and every struct in it, add-on preferences, the key configurations. */
static bool preference_type(StructRNA *type)
{
  for (StructRNA *kind : {RNA_Preferences, RNA_AddonPreferences, RNA_KeyConfigurations, RNA_KeyConfig, RNA_KeyMaps,
                          RNA_KeyMap, RNA_KeyMapItems, RNA_KeyMapItem})
  {
    if (type != nullptr && RNA_struct_is_a(type, kind)) {
      return true;
    }
  }
  return false;
}

static bool in_preferences(const void *data)
{
  return data >= static_cast<const void *>(&U) && data < static_cast<const void *>(&U + 1);
}

static bool preferences_pointer(const PointerRNA *ptr)
{
  if (ptr->owner_id != nullptr && GS(ptr->owner_id->name) != ID_WM) {
    return false;
  }
  if ((ptr->owner_id == nullptr && in_preferences(ptr->data)) || preference_type(ptr->type)) {
    return true;
  }
  for (const AncestorPointerRNA &ancestor : ptr->ancestors) {
    if ((ptr->owner_id == nullptr && in_preferences(ancestor.data)) || preference_type(ancestor.type)) {
      return true;
    }
  }
  return false;
}

/** An open file browser's settings (its folder and file name: `space_data.params`) or the operator it answers for
 * (`active_operator`, its properties): what the person confirms there is recorded as their pick, so a confined
 * write would choose it for them (the re-read of 9dcfb883ebb, SHOULD-7). */
static bool file_browser_pointer(const PointerRNA *ptr)
{
  if (RNA_struct_is_a(ptr->type, RNA_FileSelectParams)) {
    return true;
  }
  if (WM_angelo_fileselect_operator_data(ptr->data)) {
    return true;
  }
  for (const AncestorPointerRNA &ancestor : ptr->ancestors) {
    if (WM_angelo_fileselect_operator_data(ancestor.data)) {
      return true;
    }
  }
  return false;
}

const char *BPY_angelo_guard_write_refusal(const PointerRNA *ptr)
{
  if (ptr == nullptr || ptr->type == nullptr || !confined_now()) {
    return nullptr;
  }
  if (preferences_pointer(ptr)) {
    return PREFERENCES;
  }
  return file_browser_pointer(ptr) ? POINTER : nullptr;
}

const char *BPY_angelo_guard_input_refusal(const PointerRNA *ptr, const char *function)
{
  if (ptr == nullptr || ptr->type == nullptr || function == nullptr) {
    return nullptr;
  }
  /* Any function of an open file browser (its selection, its active file): what a person's confirm answers for, a
   * multi-file confirm's `files` included, is the person's pick, never the agent's (the re-read of 0a0d4e95743,
   * SHOULD-3; its properties: `file_browser_pointer`, its operators: SHOULD-6). */
  const bool file_browser = RNA_struct_is_a(ptr->type, RNA_SpaceFileBrowser) ||
                            RNA_struct_is_a(ptr->type, RNA_FileSelectParams);
  if (!confined_now()) {
    /* The modal of an operator an agent script started runs unconfined, yet it is the agent's: a file browser it
     * opens is refused as the script's own (the tag on its handler uses the same predicate; the re-read of
     * 9dcfb883ebb, SHOULD-2). */
    return WM_angelo_in_agent_modal() &&
                   (file_browser ||
                    (RNA_struct_is_a(ptr->type, RNA_WindowManager) && STREQ(function, "fileselect_add"))) ?
               POINTER :
               nullptr;
  }
  if (file_browser) {
    return POINTER;
  }
  if (RNA_struct_is_a(ptr->type, RNA_Window)) {
    return STREQ(function, "cursor_warp") || STREQ(function, "event_simulate") ? POINTER : nullptr;
  }
  if (RNA_struct_is_a(ptr->type, RNA_WindowManager)) {
    /* popup_menu, popover and popup_menu_pie are Python wrappers (_bpy_types.py) over the *_begin__internal. */
    /* fileselect_add: a file browser the person's confirm would answer for the script (the re-review of piece 10,
     * SHOULD 1); the window manager also tags any a confined invoke opens in C. */
    for (const char *opens : {"popmenu_begin__internal", "popover_begin__internal", "piemenu_begin__internal",
                              "invoke_popup", "invoke_props_popup", "invoke_props_dialog", "invoke_confirm",
                              "invoke_search_popup", "fileselect_add"})
    {
      if (STREQ(function, opens)) {
        return POINTER;
      }
    }
  }
  return nullptr;
}

const char *BPY_angelo_guard_value_refusal(const PointerRNA *ptr,
                                           PropertyRNA *prop,
                                           const char *string_value,
                                           int int_value,
                                           bool pointer_set)
{
  if (ptr == nullptr || ptr->type == nullptr || ptr->data == nullptr || !confined_now()) {
    return nullptr;
  }
  const char *name = RNA_property_identifier(prop);
  if (RNA_struct_is_a(ptr->type, RNA_Driver)) {
    if (STREQ(name, "expression")) {
      return driver_refusal(ptr->data, string_value ? string_value : "", -1);
    }
    if (STREQ(name, "type")) {
      return driver_refusal(ptr->data, nullptr, int_value);
    }
  }
  if (RNA_struct_is_a(ptr->type, RNA_FreestyleModuleSettings)) {
    const FreestyleModuleConfig *module = static_cast<const FreestyleModuleConfig *>(ptr->data);
    if ((STREQ(name, "script") && pointer_set) || (STREQ(name, "use") && int_value && module->script)) {
      return STYLE_MODULE;
    }
  }
  return nullptr;
}

const char *BPY_angelo_guard_load_time_refusal(PointerRNA *ptr, PropertyRNA *prop)
{
  if (!confined_now() || ptr == nullptr || !RNA_struct_is_a(ptr->type, RNA_Text) ||
      !STREQ(RNA_property_identifier(prop), "use_module"))
  {
    return nullptr;
  }
  return AUTORUN;
}

/** Whether a Blender writer may write `value` for Angelo's own code: the open document over itself, or where C says
 * Angelo may write (writable.cc). */
static bool angelo_may_write(const char *value)
{
  if (value == nullptr || value[0] == '\0') {
    return true;
  }
  const std::string path = absolute_path(value);
  const char *document = BKE_main_blendfile_path_from_global();
  if (document != nullptr && document[0] != '\0' && BLI_path_cmp(document, path.c_str()) == 0) {
    return true; /* Saving the open document to its own path. */
  }
  return may_write(path);
}

/** A writer operator Angelo's own (unconfined) Python called: every file or folder it is given must be one C says
 * Angelo may write, whatever Python state named it (the review of piece 10, ruling 5). A writer is an operator whose
 * file browser saves (`check_existing` defaults on: `FILE_SAVE`, Python's `ExportHelper`), every exporter included,
 * decided here for all of them rather than per operator. */
/** What a render writes for the scene's current frame: `render.filepath` with its frame, extension and template
 * values (the scene's and its camera's names now). Empty when it can't be expanded. */
static std::string render_output_now(Main *bmain, Scene *scene)
{
  bke::path_templates::VariableMap variables;
  BKE_blender_project_read_callback(bmain, [&](const bke::BlenderProject *project) {
    BKE_add_template_variables_general(variables, &scene->id, project);
  });
  BKE_add_template_variables_for_render_path(variables, *scene);
  char filepath[FILE_MAX];
  const Vector<bke::path_templates::Error> errors = BKE_image_path_from_imformat(filepath,
                                                                                 scene->r.pic,
                                                                                 BKE_main_blendfile_path(bmain),
                                                                                 &variables,
                                                                                 scene->r.cfra,
                                                                                 &scene->r.im_format,
                                                                                 (scene->r.scemode & R_EXTENSION) != 0,
                                                                                 true,
                                                                                 nullptr);
  return errors.is_empty() ? std::string(filepath) : std::string();
}

/** Blender's render writers write the scene's own output path (`render.filepath`), a value from scene data. */
static const char *render_writer_refusal(bContext *C, wmOperatorType *ot, PointerRNA *ptr)
{
  if (!STREQ(ot->idname, "RENDER_OT_render") && !STREQ(ot->idname, "RENDER_OT_opengl")) {
    return nullptr;
  }
  const bool writes = (RNA_struct_find_property(ptr, "write_still") && RNA_boolean_get(ptr, "write_still")) ||
                      (RNA_struct_find_property(ptr, "animation") && RNA_boolean_get(ptr, "animation"));
  Scene *scene = CTX_data_scene(C);
  if (!writes || scene == nullptr || scene->r.pic[0] == '\0') {
    return nullptr;
  }
  if (strchr(scene->r.pic, '{') != nullptr) {
    /* Template values (the scene's or camera's name) are data a script changes without setting this path: what it
     * expands to now is judged, never the text the person typed (the re-read of 9dcfb883ebb, SHOULD-3). */
    const std::string now = render_output_now(CTX_data_main(C), scene);
    return !now.empty() && may_write(now) ? nullptr : WRITE;
  }
  /* The person's own output path counts here, for this property only (the re-review of piece 10, SHOULD 2). */
  const std::string tag = typed_tag(&scene->id, &scene->r, "filepath");
  return may_write_for(absolute_path(scene->r.pic), &tag) ? nullptr : WRITE;
}

static const char *writer_refusal(PointerRNA *ptr)
{
  PropertyRNA *check = RNA_struct_find_property(ptr, "check_existing");
  if (check == nullptr || RNA_property_type(check) != PROP_BOOLEAN || !RNA_property_boolean_get_default(ptr, check)) {
    return nullptr;
  }
  std::string directory;
  bool refused = false;
  RNA_STRUCT_BEGIN (ptr, prop) { /* `break`, never `return`: the macro's end frees its iterator. */
    if (RNA_property_type(prop) == PROP_STRING && path_subtype(prop)) {
      const std::string value = RNA_property_string_get(ptr, prop);
      if (!angelo_may_write(value.c_str())) {
        refused = true;
        break;
      }
      if (STREQ(RNA_property_identifier(prop), "directory")) {
        directory = value;
      }
    }
  }
  RNA_STRUCT_END;
  if (!refused && !directory.empty()) {
    PropertyRNA *files = RNA_struct_find_property(ptr, "files");
    if (files != nullptr && RNA_property_type(files) == PROP_COLLECTION) {
      RNA_PROP_BEGIN (ptr, item, files) {
        const std::string joined = directory + SEP_STR + RNA_string_get(&item, "name");
        if (!angelo_may_write(joined.c_str())) {
          refused = true;
          break;
        }
      }
      RNA_PROP_END;
    }
  }
  return refused ? WRITE : nullptr;
}

const char *BPY_angelo_guard_operator_refusal(bContext *C, wmOperatorType *ot, PointerRNA *ptr)
{
  if (!confined_now()) {
    /* Angelo's own call (through its package's code): the writers only, by C's record. */
    if (!angelo_on_stack()) {
      return nullptr;
    }
    const char *refusal = writer_refusal(ptr);
    return refusal != nullptr ? refusal : render_writer_refusal(C, ot, ptr);
  }
  char py_idname[OP_MAX_TYPENAME];
  WM_operator_py_idname(py_idname, ot->idname);
  if (const char *refusal = BPY_angelo_guard_operator_refusal_id(ot->idname, py_idname)) {
    return refusal;
  }
  /* Any operator run with an open file browser as its context space (a context override: `file.select`,
   * `select_bookmark`, `directory_new`, ...): what the person confirms there is their pick, so a script chooses none
   * of it (the re-read of f9f24cc6ffd, SHOULD-6, the coordinator's ruling: by context, not by a list of names). */
  if (CTX_wm_space_file(C) != nullptr) {
    return POINTER;
  }
  /* A home file read that names an app template switches it: like read_factory_settings it resets the add-ons
   * (`addon_utils.reset_all`) inside the region, so it is refused as that one is. */
  if (STREQ(ot->idname, "WM_OT_read_homefile")) {
    PropertyRNA *app_template = RNA_struct_find_property(ptr, "app_template");
    if (app_template != nullptr && RNA_property_is_set(ptr, app_template)) {
      return OPERATOR;
    }
  }
  /* Every file and folder path the operator is given; a file browser's list of names is joined with its folder.
   * A writer (its browser saves: `check_existing` on by default, as `writer_refusal` decides) writes them; any other
   * operator (an open, an import, an append) only names them. */
  PropertyRNA *check_existing = RNA_struct_find_property(ptr, "check_existing");
  const Change named_as = (check_existing != nullptr && RNA_property_type(check_existing) == PROP_BOOLEAN &&
                           RNA_property_boolean_get_default(ptr, check_existing)) ?
                              Change::Content :
                              Change::Named;
  std::string directory;
  const char *found = nullptr;
  /* `break`, never `return`, inside the iterator macros: their end frees the iterator. */
  RNA_STRUCT_BEGIN (ptr, prop) {
    if (RNA_property_type(prop) == PROP_STRING && path_subtype(prop)) {
      const std::string value = RNA_property_string_get(ptr, prop);
      found = judge_written(value.c_str(), named_as);
      if (found) {
        break;
      }
      if (STREQ(RNA_property_identifier(prop), "directory")) {
        directory = value;
      }
    }
  }
  RNA_STRUCT_END;
  if (found) {
    return found;
  }
  if (!directory.empty()) {
    PropertyRNA *files = RNA_struct_find_property(ptr, "files");
    if (files != nullptr && RNA_property_type(files) == PROP_COLLECTION) {
      RNA_PROP_BEGIN (ptr, item, files) {
        const std::string name = RNA_string_get(&item, "name");
        const std::string joined = directory + SEP_STR + name;
        found = judge_written(joined.c_str(), named_as);
        if (found) {
          break;
        }
      }
      RNA_PROP_END;
    }
  }
  if (found) {
    return found;
  }
  /* What an operator writes from context rather than from a property. */
  if (STREQ(ot->idname, "TEXT_OT_save")) {
    if (const Text *text = CTX_data_edit_text(C)) {
      if (const char *refusal = judge_written(text->filepath, Change::Content)) {
        return refusal;
      }
    }
  }
  if (STREQ(ot->idname, "IMAGE_OT_save")) {
    if (const Image *image = CTX_data_edit_image(C)) {
      if (const char *refusal = judge_written(image->filepath, Change::Content)) {
        return refusal;
      }
    }
  }
  if (STREQ(ot->idname, "RENDER_OT_render") || STREQ(ot->idname, "RENDER_OT_opengl")) {
    const bool writes = (RNA_struct_find_property(ptr, "write_still") && RNA_boolean_get(ptr, "write_still")) ||
                        (RNA_struct_find_property(ptr, "animation") && RNA_boolean_get(ptr, "animation"));
    if (const Scene *scene = CTX_data_scene(C); writes && scene) {
      if (const char *refusal = judge_written(scene->r.pic, Change::Content)) {
        return refusal;
      }
    }
  }
  return nullptr;
}

}  // namespace blender
