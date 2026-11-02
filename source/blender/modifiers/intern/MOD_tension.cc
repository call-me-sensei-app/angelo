/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup modifiers
 *
 * Angelo: Tension modifier. Measures how much each edge of the evaluated mesh is stretched or
 * compressed against a rest shape and writes the result as signed float attributes, so shaders
 * (an Attribute node), Geometry Nodes and the viewport's tension overlay can read it.
 *
 * - Edge value: `length / rest_length - 1` (0.1 is 10% stretched, -0.1 is 10% compressed).
 * - Vertex value: `sum(lengths) / sum(rest_lengths) - 1` over the vertex's edges, which weights
 *   each edge by its rest length so a tiny edge cannot dominate its vertex. Edges with no rest
 *   length (a degenerate rest, or geometry a modifier made with copied rest positions) are left
 *   out of both sums and read 0 themselves.
 *
 * The modifier changes no geometry. Placed before a subdivision modifier, its point attribute is
 * subdivided with the mesh, which is cheaper than measuring the subdivided result.
 */

#include "BLI_array.hh"
#include "BLI_math_vector.hh"
#include "BLI_offset_indices.hh"
#include "BLI_span.hh"
#include "BLI_string.h"
#include "BLI_task.hh"
#include "BLI_vector.hh"

#include "BLT_translation.hh"

#include "DNA_key_types.h"
#include "DNA_mesh_types.h"
#include "DNA_object_types.h"
#include "DNA_screen_types.h"

#include "BKE_attribute.hh"
#include "BKE_editmesh.hh"
#include "BKE_key.hh"
#include "BKE_mesh.hh"
#include "BKE_modifier.hh"

#include "UI_interface_layout.hh"
#include "UI_resources.hh"

#include "RNA_access.hh"
#include "RNA_prototypes.hh"

#include "MOD_modifiertypes.hh"
#include "MOD_ui_common.hh"

namespace blender {

/** Below this rest length an edge counts as degenerate and reads as unstrained. */
static constexpr float TENSION_MIN_REST_LENGTH = 1e-12f;

static void init_data(ModifierData *md)
{
  TensionModifierData *tmd = reinterpret_cast<TensionModifierData *>(md);
  INIT_DEFAULT_STRUCT_AFTER(tmd, modifier);
}

/**
 * The rest positions for the modifier's input, one per vertex of `mesh`, or an empty span after
 * setting the modifier's error. `r_storage` owns positions that had to be copied.
 */
static Span<float3> rest_positions_get(const TensionModifierData &tmd,
                                       const ModifierEvalContext &ctx,
                                       const Mesh &mesh,
                                       ModifierData *md,
                                       Array<float3> &r_storage)
{
  Object *ob = ctx.object;
  const bke::AttributeAccessor attributes = mesh.attributes();
  const int verts_num = mesh.verts_num;

  auto from_attribute = [&](const StringRef name) -> Span<float3> {
    const bke::AttributeReader<float3> reader = attributes.lookup<float3>(name);
    if (!reader) {
      return {};
    }
    /* A stored point attribute is read in place; anything converted is copied, since the
     * converted array does not outlive this function. */
    if (reader.domain == bke::AttrDomain::Point && reader.varray.is_span()) {
      return reader.varray.get_internal_span();
    }
    r_storage.reinitialize(verts_num);
    attributes.lookup<float3>(name, bke::AttrDomain::Point).varray.materialize(r_storage);
    return r_storage;
  };

  switch (tmd.rest_source) {
    case MOD_TENSION_REST_ATTRIBUTE: {
      const Span<float3> rest = from_attribute(tmd.rest_attribute);
      if (rest.is_empty() && verts_num > 0) {
        BKE_modifier_set_error(ob,
                               md,
                               "Rest attribute \"%s\" not found, capture a rest shape first",
                               tmd.rest_attribute);
      }
      return rest;
    }
    case MOD_TENSION_REST_SHAPE_KEY: {
      if (ob->type != OB_MESH) {
        BKE_modifier_set_error(ob, md, "Object is not a mesh");
        return {};
      }
      Mesh *object_mesh = id_cast<Mesh *>(ob->data);
      KeyBlock *key_block = object_mesh->key ?
                                BKE_keyblock_find_name(object_mesh->key, tmd.shape_key) :
                                nullptr;
      if (key_block == nullptr || key_block->data == nullptr) {
        BKE_modifier_set_error(ob, md, "Shape key \"%s\" not found", tmd.shape_key);
        return {};
      }
      if (key_block->totelem != verts_num) {
        BKE_modifier_set_error(ob,
                               md,
                               "Shape key has %d vertices, the mesh has %d: move Tension above "
                               "modifiers that add geometry",
                               key_block->totelem,
                               verts_num);
        return {};
      }
      return {static_cast<const float3 *>(key_block->data), key_block->totelem};
    }
    case MOD_TENSION_REST_ORIGINAL:
    default: {
      /* Blender's own copy of the positions before shape keys and modifiers. Subdivision and Mirror
       * carry it consistently; modifiers that copy it unchanged into new geometry (Solidify, Array,
       * Screw) leave their new edges with no or wrong rest lengths, so Tension belongs above them. */
      if (const Span<float3> rest = from_attribute("rest_position"); !rest.is_empty()) {
        return rest;
      }
      if (ob->type != OB_MESH) {
        BKE_modifier_set_error(ob, md, "Object is not a mesh");
        return {};
      }
      int original_num;
      if (BMEditMesh *em = BKE_editmesh_from_object(ob)) {
        original_num = em->bm->totvert;
        if (original_num == verts_num) {
          r_storage = BKE_editmesh_vert_coords_alloc_orco(em);
        }
      }
      else {
        const Mesh *object_mesh = id_cast<const Mesh *>(ob->data);
        original_num = object_mesh->verts_num;
        if (original_num == verts_num) {
          return object_mesh->vert_positions();
        }
      }
      if (original_num != verts_num) {
        BKE_modifier_set_error(ob,
                               md,
                               "Original mesh has %d vertices, the input has %d: turn on Add "
                               "Rest Position or move Tension above modifiers that add geometry",
                               original_num,
                               verts_num);
        return {};
      }
      return r_storage;
    }
  }
}

/** An edge's current and rest length. */
static float2 edge_lengths(const int2 verts, const Span<float3> positions, const Span<float3> rest)
{
  return {math::distance(positions[verts[0]], positions[verts[1]]),
          math::distance(rest[verts[0]], rest[verts[1]])};
}

static float strain(const float length_sum, const float rest_sum)
{
  return rest_sum > TENSION_MIN_REST_LENGTH ? length_sum / rest_sum - 1.0f : 0.0f;
}

/**
 * Per-vertex strain: every vertex gathers its own edges, in parallel and deterministic, through
 * the vertex to edge map (a topology cache shared by every frame's evaluated copy). Linear in the
 * edges whatever the faces' sizes or the vertices' valence.
 */
static void vertex_strain(const GroupedSpan<int> vert_to_edge,
                          const Span<int2> edges,
                          const Span<float3> positions,
                          const Span<float3> rest,
                          MutableSpan<float> r_values)
{
  threading::parallel_for(r_values.index_range(), 4096, [&](const IndexRange range) {
    for (const int vert : range) {
      float length_sum = 0.0f;
      float rest_sum = 0.0f;
      for (const int edge : vert_to_edge[vert]) {
        const float2 lengths = edge_lengths(edges[edge], positions, rest);
        if (lengths[1] > TENSION_MIN_REST_LENGTH) {
          length_sum += lengths[0];
          rest_sum += lengths[1];
        }
      }
      r_values[vert] = strain(length_sum, rest_sum);
    }
  });
}

/** Averages each vertex value with its neighbors, `iterations` times, in parallel. */
static void smooth_vertex_values(const GroupedSpan<int> vert_to_edge,
                                 const Span<int2> edges,
                                 const int iterations,
                                 MutableSpan<float> values)
{
  if (iterations <= 0) {
    return;
  }
  Array<float> previous(values.size());
  for ([[maybe_unused]] const int iteration : IndexRange(iterations)) {
    previous.as_mutable_span().copy_from(values);
    threading::parallel_for(values.index_range(), 4096, [&](const IndexRange range) {
      for (const int vert : range) {
        const Span<int> vert_edges = vert_to_edge[vert];
        if (vert_edges.is_empty()) {
          continue;
        }
        float sum = 0.0f;
        for (const int edge : vert_edges) {
          sum += previous[bke::mesh::edge_other_vert(edges[edge], vert)];
        }
        values[vert] = 0.5f * previous[vert] + 0.5f * sum / float(vert_edges.size());
      }
    });
  }
}

/** The attribute the rest shape is read from, if it is one. */
static StringRef rest_attribute_read(const TensionModifierData &tmd)
{
  switch (tmd.rest_source) {
    case MOD_TENSION_REST_ATTRIBUTE:
      return tmd.rest_attribute;
    case MOD_TENSION_REST_ORIGINAL:
      return "rest_position";
    default:
      return "";
  }
}

/**
 * Whether `name` can receive an output without replacing data Blender or this modifier relies on:
 * not empty, not built in, not internal (a leading dot), not Blender's `rest_position` (another
 * Tension modifier's rest) and not the attribute this modifier reads its rest from.
 */
static bool output_name_valid(const bke::AttributeAccessor &attributes,
                              const TensionModifierData &tmd,
                              const StringRef name)
{
  return !name.is_empty() && !name.startswith(".") && !attributes.is_builtin(name) &&
         name != "rest_position" && name != rest_attribute_read(tmd);
}

static Mesh *modify_mesh(ModifierData *md, const ModifierEvalContext *ctx, Mesh *mesh)
{
  const TensionModifierData &tmd = *reinterpret_cast<const TensionModifierData *>(md);
  const StringRef point_name = tmd.attribute_name;
  const StringRef edge_name = tmd.edge_attribute_name;

  const StringRef rest_read = rest_attribute_read(tmd);
  if (!rest_read.is_empty() && (rest_read == point_name || rest_read == edge_name)) {
    BKE_modifier_set_error(
        ctx->object, md, "The rest attribute cannot also be an output attribute");
    return mesh;
  }
  if (!output_name_valid(mesh->attributes(), tmd, point_name)) {
    BKE_modifier_set_error(ctx->object, md, "Choose an attribute name that is not built in");
    return mesh;
  }
  if (!edge_name.is_empty() &&
      (edge_name == point_name || !output_name_valid(mesh->attributes(), tmd, edge_name)))
  {
    BKE_modifier_set_error(
        ctx->object, md, "The edge attribute needs its own name that is not built in");
    return mesh;
  }

  Array<float3> rest_storage;
  const Span<float3> rest = rest_positions_get(tmd, *ctx, *mesh, md, rest_storage);
  if (rest.size() != mesh->verts_num) {
    return mesh;
  }

  const Span<float3> positions = mesh->vert_positions();
  const Span<int2> edges = mesh->edges();

  /* The outputs are written in place: no per-frame temporaries beyond the attributes themselves.
   * Neither can be the rest attribute (checked above), so the rest span stays valid. */
  bke::MutableAttributeAccessor attributes = mesh->attributes_for_write();
  attributes.remove(point_name);
  bke::SpanAttributeWriter<float> vert_out = attributes.lookup_or_add_for_write_only_span<float>(
      point_name, bke::AttrDomain::Point);
  const GroupedSpan<int> vert_to_edge = mesh->vert_to_edge_map();
  vertex_strain(vert_to_edge, edges, positions, rest, vert_out.span);
  smooth_vertex_values(vert_to_edge, edges, tmd.smooth_iterations, vert_out.span);
  vert_out.finish();

  if (!edge_name.is_empty()) {
    attributes.remove(edge_name);
    bke::SpanAttributeWriter<float> edge_out = attributes.lookup_or_add_for_write_only_span<float>(
        edge_name, bke::AttrDomain::Edge);
    threading::parallel_for(edges.index_range(), 8192, [&](const IndexRange range) {
      for (const int edge : range) {
        const float2 lengths = edge_lengths(edges[edge], positions, rest);
        edge_out.span[edge] = lengths[1] > TENSION_MIN_REST_LENGTH ?
                                  lengths[0] / lengths[1] - 1.0f :
                                  0.0f;
      }
    });
    edge_out.finish();
  }
  return mesh;
}

static void panel_draw(const bContext * /*C*/, Panel *panel)
{
  ui::Layout &layout = *panel->layout;

  PointerRNA ob_ptr;
  PointerRNA *ptr = modifier_panel_get_property_pointers(panel, &ob_ptr);

  layout.use_property_split_set(true);

  layout.prop(ptr, "rest_source", UI_ITEM_NONE, std::nullopt, ICON_NONE);
  const int rest_source = RNA_enum_get(ptr, "rest_source");
  if (rest_source == MOD_TENSION_REST_SHAPE_KEY) {
    PointerRNA mesh_ptr = RNA_pointer_get(&ob_ptr, "data");
    PointerRNA key_ptr = RNA_pointer_is_null(&mesh_ptr) ? PointerRNA_NULL :
                                                           RNA_pointer_get(&mesh_ptr, "shape_keys");
    if (!RNA_pointer_is_null(&key_ptr)) {
      layout.prop_search(ptr, "shape_key", &key_ptr, "key_blocks", std::nullopt, ICON_SHAPEKEY_DATA);
    }
    else {
      layout.prop(ptr, "shape_key", UI_ITEM_NONE, std::nullopt, ICON_SHAPEKEY_DATA);
    }
  }
  else if (rest_source == MOD_TENSION_REST_ATTRIBUTE) {
    layout.prop(ptr, "rest_attribute", UI_ITEM_NONE, std::nullopt, ICON_NONE);
  }

  layout.prop(ptr, "attribute_name", UI_ITEM_NONE, IFACE_("Vertex Attribute"), ICON_NONE);
  layout.prop(ptr, "edge_attribute_name", UI_ITEM_NONE, IFACE_("Edge Attribute"), ICON_NONE);
  layout.prop(ptr, "smooth_iterations", UI_ITEM_NONE, std::nullopt, ICON_NONE);

  modifier_error_message_draw(layout, ptr);
}

static void panel_register(ARegionType *region_type)
{
  modifier_panel_register(region_type, eModifierType_Tension, panel_draw);
}

ModifierTypeInfo modifierType_Tension = {
    /*idname*/ "Tension",
    /*name*/ N_("Tension"),
    /*struct_name*/ "TensionModifierData",
    /*struct_size*/ sizeof(TensionModifierData),
    /*srna*/ &RNA_TensionModifier,
    /*type*/ ModifierTypeType::NonGeometrical,
    /*flags*/
    (eModifierTypeFlag_AcceptsMesh | eModifierTypeFlag_SupportsMapping |
     eModifierTypeFlag_SupportsEditmode),
    /*icon*/ ICON_FORCE_HARMONIC,

    /*copy_data*/ BKE_modifier_copydata_generic,

    /*deform_verts*/ nullptr,
    /*deform_matrices*/ nullptr,
    /*deform_verts_EM*/ nullptr,
    /*deform_matrices_EM*/ nullptr,
    /*modify_mesh*/ modify_mesh,
    /*modify_geometry_set*/ nullptr,

    /*init_data*/ init_data,
    /*required_data_mask*/ nullptr,
    /*free_data*/ nullptr,
    /*is_disabled*/ nullptr,
    /*update_depsgraph*/ nullptr,
    /*depends_on_time*/ nullptr,
    /*depends_on_normals*/ nullptr,
    /*foreach_ID_link*/ nullptr,
    /*foreach_tex_link*/ nullptr,
    /*free_runtime_data*/ nullptr,
    /*panel_register*/ panel_register,
    /*blend_write*/ nullptr,
    /*blend_read*/ nullptr,
    /*foreach_cache*/ nullptr,
    /*foreach_working_space_color*/ nullptr,
};

}  // namespace blender
