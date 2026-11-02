/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup draw
 *
 * Angelo: the tension map's value per corner, read from a float attribute of the evaluated mesh
 * (written by the Tension modifier, or by anything else under the same name).
 */

#include "BLI_task.hh"

#include "BKE_attribute.hh"

#include "draw_subdivision.hh"
#include "extract_mesh.hh"

namespace blender::draw {

static const GPUVertFormat &tension_format()
{
  static const GPUVertFormat format = GPU_vertformat_from_attribute("tension",
                                                                    gpu::VertAttrType::SFLOAT_32);
  return format;
}

gpu::VertBufPtr extract_tension(const MeshRenderData &mr, const StringRef name)
{
  gpu::VertBufPtr vbo = gpu::VertBufPtr(GPU_vertbuf_create_with_format(tension_format()));
  GPU_vertbuf_data_alloc(*vbo, mr.corners_num);
  MutableSpan<float> vbo_data = vbo->data<float>();

  /* The edit-mode BMesh carries no evaluated attributes: the overlay does not draw it. */
  if (mr.extract_type != MeshExtractType::Mesh || name.is_empty()) {
    vbo_data.fill(0.0f);
    return vbo;
  }
  const bke::AttributeAccessor attributes = mr.mesh->attributes();
  const bke::AttributeReader<float> reader = attributes.lookup<float>(name);
  /* The Tension modifier's point values: a parallel gather through the corners' vertices. It
   * redraws every frame of a playing deformation, where the generic domain adaptation below was
   * the larger part of the map's cost (1.0.8 playback measurement, a million vertices). */
  if (reader && reader.domain == bke::AttrDomain::Point && reader.varray.is_span()) {
    const Span<float> values = reader.varray.get_internal_span();
    const Span<int> corner_verts = mr.corner_verts;
    threading::parallel_for(corner_verts.index_range(), 8192, [&](const IndexRange range) {
      for (const int corner : range) {
        vbo_data[corner] = values[corner_verts[corner]];
      }
    });
    return vbo;
  }
  const VArray<float> values = *attributes.lookup_or_default<float>(
      name, bke::AttrDomain::Corner, 0.0f);
  values.materialize(vbo_data);
  return vbo;
}

gpu::VertBufPtr extract_tension_subdiv(const MeshRenderData &mr,
                                       const DRWSubdivCache &subdiv_cache,
                                       const StringRef name)
{
  gpu::VertBufPtr vbo = gpu::VertBufPtr(
      GPU_vertbuf_create_on_device(tension_format(), subdiv_cache.num_subdiv_loops));
  gpu::VertBufPtr coarse = extract_tension(mr, name);
  draw_subdiv_interp_custom_data(subdiv_cache, *coarse, *vbo, GPU_COMP_F32, 1, 0);
  return vbo;
}

}  // namespace blender::draw
