/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup overlay
 *
 * Angelo: tension map. Colors each mesh whose evaluated geometry carries a tension attribute,
 * blue where it is compressed and red where it is stretched against its rest shape. The value
 * comes from the object's last viewport-enabled Tension modifier (its vertex attribute), or from
 * a float attribute named "tension" that anything else wrote.
 *
 * Nothing runs for a viewport with the overlay off, and nothing is extracted for a mesh without
 * the attribute, so scenes that do not use it pay nothing.
 */

#pragma once

#include "BKE_attribute.hh"
#include "BKE_modifier.hh"
#include "DNA_modifier_types.h"

#include "draw_cache.hh"
#include "overlay_base.hh"

namespace blender::draw::overlay {

class Tension : Overlay {
 private:
  PassMain ps_ = {"tension_ps_"};

 public:
  void begin_sync(Resources &res, const State &state) final
  {
    ps_.init();
    enabled_ = state.is_space_v3d() && !res.is_selection() && state.show_tension();
    if (!enabled_) {
      return;
    }
    ps_.bind_ubo(OVERLAY_GLOBALS_SLOT, &res.globals_buf);
    ps_.bind_ubo(DRW_CLIPPING_UBO_SLOT, &res.clip_planes_buf);
    ps_.state_set(DRW_STATE_WRITE_COLOR | DRW_STATE_DEPTH_LESS_EQUAL | DRW_STATE_BLEND_ALPHA,
                  state.clipping_plane_count);
    ps_.shader_set(res.shaders->tension_mesh.get());
    ps_.push_constant("compress_max", math::max(state.overlay.tension_compress_max, 1e-4f));
    ps_.push_constant("stretch_max", math::max(state.overlay.tension_stretch_max, 1e-4f));
    ps_.push_constant("opacity", state.overlay.tension_opacity);
  }

  void object_sync(Manager &manager,
                   const ObjectRef &ob_ref,
                   Resources & /*res*/,
                   const State & /*state*/) final
  {
    if (!enabled_ || ob_ref.object->type != OB_MESH || ob_ref.preview_base_geometry()) {
      return;
    }
    Object &object = *ob_ref.object;
    const Mesh &mesh = DRW_object_get_data_for_drawing<Mesh>(object);
    const StringRefNull attribute = attribute_name(object);
    const std::optional<bke::AttributeMetaData> meta_data = mesh.attributes().lookup_meta_data(
        attribute);
    if (!meta_data || meta_data->data_type != bke::AttrType::Float ||
        meta_data->domain == bke::AttrDomain::Edge)
    {
      return;
    }
    gpu::Batch *batch = DRW_cache_mesh_surface_tension_get(&object, attribute);
    ps_.draw(batch, manager.unique_handle(ob_ref));
  }

  void pre_draw(Manager &manager, View &view) final
  {
    if (!enabled_) {
      return;
    }
    manager.generate_commands(ps_, view);
  }

  void draw_line(Framebuffer &framebuffer, Manager &manager, View &view) final
  {
    if (!enabled_) {
      return;
    }
    GPU_framebuffer_bind(framebuffer);
    manager.submit_only(ps_, view);
  }

 private:
  /** The vertex attribute of the last Tension modifier enabled in the viewport. */
  static StringRefNull attribute_name(const Object &object)
  {
    const TensionModifierData *found = nullptr;
    for (const ModifierData &md : object.modifiers) {
      if (md.type == eModifierType_Tension && (md.mode & eModifierMode_Realtime)) {
        found = reinterpret_cast<const TensionModifierData *>(&md);
      }
    }
    return found ? StringRefNull(found->attribute_name) : StringRefNull("tension");
  }
};

}  // namespace blender::draw::overlay
