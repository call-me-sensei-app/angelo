/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup overlay
 *
 * Angelo: the viewport UV checker (`SpaceView3D.uv_checker`, doc/angelo/uv_checker.rst).
 *
 * Draws each visible mesh's UVs over its shaded surface, like Face Orientation draws its colors:
 * Blender's generated Color Grid or UV Grid image, texel density or stretch. The images live only
 * in a GPU texture of the overlay engine; no material, image, node or other data-block is made or
 * changed. Off, it sets up nothing and requests no batch.
 */

#pragma once

#include "BKE_paint.hh"
#include "BKE_unit.hh"

#include "GPU_texture.hh"

#include "draw_attributes.hh"
#include "draw_cache.hh"
#include "draw_cache_impl.hh"

#include "overlay_base.hh"
#include "overlay_uv_checker_patterns.hh"

namespace blender::draw::overlay {

class UVChecker : Overlay {

 private:
  PassMain ps_ = {"UV Checker"};
  /* One per UV map slot of a mesh (`cu0`..`cu3`, #UV_CHECKER_SLOTS). */
  PassMain::Sub *checker_ps_[UV_CHECKER_SLOTS] = {};
  PassMain::Sub *missing_ps_ = nullptr;

  bool selected_only_ = false;
  /* What a slot's sub-pass binds, kept for the slots set up on first use (#checker_sub). */
  Resources *res_ = nullptr;
  gpu::Texture *pattern_tx_ = nullptr;
  int pattern_ = 0;
  int flipped_ = 0;
  float pattern_scale_ = 1.0f, opacity_ = 1.0f, density_factor_ = 1.0f;
  eView3DUVCheckerUVMap uv_map_ = V3D_UV_CHECKER_UV_ACTIVE;
  StringRefNull uv_map_name_;

 public:
  static bool is_enabled(const Resources &res, const State &state)
  {
    return state.v3d && (state.v3d->uv_checker.flag & V3D_UV_CHECKER_SHOW) &&
           ELEM(state.v3d->shading.type, OB_SOLID, OB_MATERIAL) && !state.xray_enabled &&
           !res.is_selection() && !state.is_depth_only_drawing;
  }

  void begin_sync(Resources &res, const State &state) final
  {
    enabled_ = is_enabled(res, state);
    gpu::Texture *pattern_tx = nullptr;
    if (enabled_) {
      /* The first time it is turned on in a session its shaders compile and its pattern image is made on worker
       * threads, and an interactive view draws without it until they are ready, so turning it on never stalls a
       * frame. Other draws (offscreen, render) have no next redraw to wait for: they finish both here. */
      const bool wait = DRW_context_get()->mode != DRWContext::VIEWPORT;
      pattern_tx = pattern_texture(state.v3d->uv_checker.pattern, wait);
      bool ready = pattern_tx != nullptr;
      if (!wait) {
        /* Slot 0 serves every mesh unless two views show different maps of it; the others compile on first use. */
        res.shaders->uv_checker_missing.ensure_compile_async();
        res.shaders->uv_checker[0].ensure_compile_async();
        ready &= res.shaders->uv_checker_missing.is_ready() && res.shaders->uv_checker[0].is_ready();
      }
      if (!ready) {
        enabled_ = false;
        DRW_viewport_request_redraw();
      }
    }
    if (!enabled_) {
      /* Not used. But release the data. */
      ps_.init();
      return;
    }

    const View3DUVChecker &settings = state.v3d->uv_checker;
    selected_only_ = (settings.flag & V3D_UV_CHECKER_SELECTED_ONLY) != 0;
    uv_map_ = settings.uv_map;
    uv_map_name_ = settings.uv_map_name;

    const bool is_solid_viewport = state.v3d->shading.type == OB_SOLID;
    const bool use_cull = is_solid_viewport &&
                          (state.v3d->shading.flag & V3D_SHADING_BACKFACE_CULLING);
    /* As Face Orientation: depth equal over Workbench, less-equal over EEVEE. */
    const DRWState depth_state = is_solid_viewport ? DRW_STATE_DEPTH_EQUAL :
                                                     DRW_STATE_DEPTH_LESS_EQUAL;
    const float opacity = math::clamp(settings.opacity, 0.0f, 1.0f);
    /* Meters per unit; a scene without a unit system measures per unit. */
    const float unit_scale = state.scene ?
                                 float(BKE_unit_value_scale(state.scene->unit, B_UNIT_LENGTH, 1.0)) :
                                 1.0f;
    const float density_factor = float(math::max(settings.texture_size, 1)) /
                                 math::max(unit_scale * settings.target_density, 1e-6f);
    res_ = &res;
    pattern_tx_ = pattern_tx;
    pattern_ = int(settings.pattern);
    flipped_ = (settings.flag & V3D_UV_CHECKER_SHOW_FLIPPED) != 0;
    pattern_scale_ = math::max(settings.scale, 1e-3f);
    opacity_ = opacity;
    density_factor_ = density_factor;

    ps_.init();
    /* The depth test only keeps the visible surface; nothing to write (the depth is already that surface's). */
    ps_.state_set(DRW_STATE_WRITE_COLOR | depth_state |
                      (use_cull ? DRW_STATE_CULL_BACK : DRWState(0)),
                  state.clipping_plane_count);
    ps_.bind_ubo(OVERLAY_GLOBALS_SLOT, &res.globals_buf);
    ps_.bind_ubo(DRW_CLIPPING_UBO_SLOT, &res.clip_planes_buf);
    for (PassMain::Sub *&sub : checker_ps_) {
      sub = nullptr;
    }
    {
      auto &sub = ps_.sub("Missing UV Map");
      sub.shader_set(res.shaders->uv_checker_missing.get());
      sub.push_constant("opacity", opacity);
      missing_ps_ = &sub;
    }
  }

  void object_sync(Manager &manager,
                   const ObjectRef &ob_ref,
                   Resources & /*res*/,
                   const State &state) final
  {
    if (!enabled_) {
      return;
    }
    Object &object = *ob_ref.object;
    if (object.type != OB_MESH || object.dt < OB_SOLID || !DRW_object_is_renderable(&object)) {
      return;
    }
    if (selected_only_ && !(object.base_flag & BASE_SELECTED)) {
      return;
    }
    /* Sculpt Mode draws its own batches from the paint BVH; that object keeps its shading. */
    if (BKE_sculptsession_use_pbvh_draw(&object, state.rv3d) && !state.is_image_render) {
      return;
    }
    Mesh &mesh = DRW_object_get_data_for_drawing<Mesh>(object);
    int slot = 0;
    gpu::Batch *geom = DRW_mesh_batch_cache_get_surface_uv_checker(
        object, mesh, uv_map_, uv_map_name_, slot);
    if (geom) {
      if (PassMain::Sub *sub = checker_sub(slot)) {
        sub->draw(geom, manager.unique_handle(ob_ref));
      }
      return;
    }
    if (gpu::Batch *surface = DRW_cache_mesh_surface_get(&object)) {
      missing_ps_->draw(surface, manager.unique_handle(ob_ref));
    }
  }

  void pre_draw(Manager &manager, View &view) final
  {
    if (!enabled_) {
      return;
    }
    manager.generate_commands(ps_, view);
  }

  void draw(Framebuffer &framebuffer, Manager &manager, View &view) final
  {
    if (!enabled_) {
      return;
    }
    GPU_framebuffer_bind(framebuffer);
    manager.submit_only(ps_, view);
  }

 private:
  /**
   * The sub-pass of a UV map slot, set up the first time a mesh needs it this redraw; slots past 0 only when views
   * show different maps of one mesh. In an interactive view a slot whose shader is still compiling is skipped (and
   * another redraw asked for) rather than stalling the frame.
   */
  PassMain::Sub *checker_sub(const int slot)
  {
    if (checker_ps_[slot]) {
      return checker_ps_[slot];
    }
    StaticShader &shader = res_->shaders->uv_checker[slot];
    if (slot > 0 && DRW_context_get()->mode == DRWContext::VIEWPORT) {
      shader.ensure_compile_async();
      if (!shader.is_ready()) {
        DRW_viewport_request_redraw();
        return nullptr;
      }
    }
    const GPUSamplerState sampler = {GPU_SAMPLER_FILTERING_LINEAR | GPU_SAMPLER_FILTERING_MIPMAP |
                                         GPU_SAMPLER_FILTERING_ANISOTROPIC_4,
                                     GPU_SAMPLER_EXTEND_MODE_REPEAT,
                                     GPU_SAMPLER_EXTEND_MODE_REPEAT};
    auto &sub = ps_.sub("Checker");
    sub.shader_set(shader.get());
    sub.bind_texture("pattern_tx", pattern_tx_, sampler);
    sub.push_constant("pattern", pattern_);
    sub.push_constant("pattern_scale", pattern_scale_);
    sub.push_constant("opacity", opacity_);
    sub.push_constant("density_factor", density_factor_);
    sub.push_constant("show_flipped", flipped_ != 0);
    checker_ps_[slot] = &sub;
    return &sub;
  }

  /** The pattern image to bind: shared, made once per session; texel density and stretch sample none. */
  static gpu::Texture *pattern_texture(const eView3DUVCheckerPattern pattern, const bool wait)
  {
    switch (pattern) {
      case V3D_UV_CHECKER_COLOR_GRID:
        return uv_checker::pattern(uv_checker::Image::ColorGrid, wait);
      case V3D_UV_CHECKER_CHECKER:
        return uv_checker::pattern(uv_checker::Image::Checker, wait);
      case V3D_UV_CHECKER_TEXEL_DENSITY:
      case V3D_UV_CHECKER_STRETCH:
        break;
    }
    return uv_checker::placeholder();
  }
};

}  // namespace blender::draw::overlay
