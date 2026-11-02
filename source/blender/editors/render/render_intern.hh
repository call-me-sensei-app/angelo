/* SPDX-FileCopyrightText: 2008 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup edrend
 */

#pragma once

#include "DNA_vec_types.h"

namespace blender {

struct Main;
struct Render;
struct ReportList;
struct wmOperator;
struct Scene;
struct ScrArea;
struct bContext;
struct wmOperatorType;

/* `render_shading.cc` */

void OBJECT_OT_material_slot_add(wmOperatorType *ot);
void OBJECT_OT_material_slot_remove(wmOperatorType *ot);
void OBJECT_OT_material_slot_assign(wmOperatorType *ot);
void OBJECT_OT_material_slot_select(wmOperatorType *ot);
void OBJECT_OT_material_slot_deselect(wmOperatorType *ot);
void OBJECT_OT_material_slot_copy(wmOperatorType *ot);
void OBJECT_OT_material_slot_move(wmOperatorType *ot);
void OBJECT_OT_material_slot_remove_unused(wmOperatorType *ot);
void OBJECT_OT_material_slot_remove_all(wmOperatorType *ot);

void MATERIAL_OT_new(wmOperatorType *ot);
void TEXTURE_OT_new(wmOperatorType *ot);
void WORLD_OT_new(wmOperatorType *ot);

void MATERIAL_OT_copy(wmOperatorType *ot);
void MATERIAL_OT_paste(wmOperatorType *ot);

void SCENE_OT_view_layer_add(wmOperatorType *ot);
void SCENE_OT_view_layer_remove(wmOperatorType *ot);
void SCENE_OT_view_layer_add_aov(wmOperatorType *ot);
void SCENE_OT_view_layer_remove_aov(wmOperatorType *ot);
void SCENE_OT_view_layer_add_lightgroup(wmOperatorType *ot);
void SCENE_OT_view_layer_remove_lightgroup(wmOperatorType *ot);
void SCENE_OT_view_layer_add_used_lightgroups(wmOperatorType *ot);
void SCENE_OT_view_layer_remove_unused_lightgroups(wmOperatorType *ot);

void OBJECT_OT_lightprobe_cache_bake(wmOperatorType *ot);
void OBJECT_OT_lightprobe_cache_free(wmOperatorType *ot);

void SCENE_OT_render_view_add(wmOperatorType *ot);
void SCENE_OT_render_view_remove(wmOperatorType *ot);

#ifdef WITH_FREESTYLE
void SCENE_OT_freestyle_module_add(wmOperatorType *ot);
void SCENE_OT_freestyle_module_remove(wmOperatorType *ot);
void SCENE_OT_freestyle_module_move(wmOperatorType *ot);
void SCENE_OT_freestyle_lineset_add(wmOperatorType *ot);
void SCENE_OT_freestyle_lineset_copy(wmOperatorType *ot);
void SCENE_OT_freestyle_lineset_paste(wmOperatorType *ot);
void SCENE_OT_freestyle_lineset_remove(wmOperatorType *ot);
void SCENE_OT_freestyle_lineset_move(wmOperatorType *ot);
void SCENE_OT_freestyle_linestyle_new(wmOperatorType *ot);
void SCENE_OT_freestyle_color_modifier_add(wmOperatorType *ot);
void SCENE_OT_freestyle_alpha_modifier_add(wmOperatorType *ot);
void SCENE_OT_freestyle_thickness_modifier_add(wmOperatorType *ot);
void SCENE_OT_freestyle_geometry_modifier_add(wmOperatorType *ot);
void SCENE_OT_freestyle_modifier_remove(wmOperatorType *ot);
void SCENE_OT_freestyle_modifier_move(wmOperatorType *ot);
void SCENE_OT_freestyle_modifier_copy(wmOperatorType *ot);
void SCENE_OT_freestyle_stroke_material_create(wmOperatorType *ot);
#endif

void TEXTURE_OT_slot_copy(wmOperatorType *ot);
void TEXTURE_OT_slot_paste(wmOperatorType *ot);
void TEXTURE_OT_slot_move(wmOperatorType *ot);

/* `render_texture_cache.cc` */

void RENDER_OT_generate_texture_cache(wmOperatorType *ot);
void RENDER_OT_clear_texture_cache(wmOperatorType *ot);

/* `render_internal.cc` */

/* Base class for all WM_JOB_TYPE_RENDER jobs. */
struct RenderJobBase {
  Scene *scene = nullptr;
  Scene *current_scene = nullptr;
};

/**
 * Contextual render, using current scene, view3d?
 */
void RENDER_OT_render(wmOperatorType *ot);
void RENDER_OT_shutter_curve_preset(wmOperatorType *ot);

/* `render_view.cc` */

/**
 * New window uses x,y to set position.
 */
ScrArea *render_view_open(bContext *C, int mx, int my, ReportList *reports);

void RENDER_OT_view_show(wmOperatorType *ot);
void RENDER_OT_view_cancel(wmOperatorType *ot);

/* `render_opengl.cc` */

void RENDER_OT_opengl(wmOperatorType *ot);

/* `render_region.cc` (Angelo: a region render over the last render). */

/** What a region render changed in the scene for the render's duration. */
struct RenderRegionJob {
  bool active = false;
  /** The scene's `R_BORDER`/`R_CROP`/`R_PERSISTENT_DATA` bits and render region before the render. */
  int mode = 0;
  rctf border = {};
};

/** Adds `region_over_last`, `region` and `region_padding` to the render operator. */
void render_region_operator_properties(wmOperatorType *ot);
/**
 * Before the render: when the operator asks for a region over the last render, arms the scene's
 * render with the render the Image Editor shows and sets the scene's render region for the
 * render's duration. Returns false (and reports why) when it cannot; nothing changed then.
 */
bool render_region_begin(
    wmOperator *op, Main *bmain, Scene *scene, bool is_animation, RenderRegionJob *r_job);
/** After the render, however it ended: the scene's render region back, the result settled. */
void render_region_end(Scene *scene, Render *re, RenderRegionJob *job, ReportList *reports);
void RENDER_OT_region_revert(wmOperatorType *ot);

}  // namespace blender
