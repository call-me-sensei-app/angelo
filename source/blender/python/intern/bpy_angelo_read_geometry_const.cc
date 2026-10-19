/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "bpy_angelo_read_geometry_const.hh"

#include <algorithm>
#include <array>
#include <climits>
#include <limits>
#include <mutex>
#include <cstring>
#include <string>
#include <unordered_map>
#include <unordered_set>

#include "ANIM_armature.hh"

#include "BLI_bounds.hh"
#include "BLI_hash.hh"
#include "BLI_math_geom.h"
#include "BLI_math_matrix.h"
#include "BLI_math_rotation.h"
#include "BLI_math_matrix.hh"
#include "BLI_math_vector.h"
#include "BLI_index_mask.hh"
#include "BLI_span.hh"
#include "BLI_threads.h"
#include "BLI_utildefines.h"

#include "BKE_action.hh"
#include "BKE_armature.hh"
#include "BKE_attribute.h"
#include "BKE_attribute.hh"
#include "BKE_bake_data_block_id.hh"
#include "BKE_attribute_legacy_convert.hh"
#include "BKE_attribute_storage.hh"
#include "BKE_curves.hh"
#include "BKE_curve.hh"
#include "BKE_customdata.hh"
#include "BKE_editmesh.hh"
#include "BKE_editmesh_cache.hh"
#include "BKE_displist.h"
#include "BKE_geometry_fields.hh"
#include "BKE_geometry_set.hh"
#include "BKE_global.hh"
#include "BKE_grease_pencil.hh"
#include "BKE_main.hh"
#include "BKE_mesh.h"
#include "BKE_mesh.hh"
#include "BKE_mesh_types.hh"
#include "BKE_mesh_wrapper.hh"
#include "BKE_subdiv_modifier.hh"
#include "BKE_object.hh"
#include "BKE_object_types.hh"
#include "BKE_pointcloud.hh"
#include "BKE_type_conversions.hh"

#include "DNA_action_types.h"
#include "DNA_armature_types.h"
#include "DNA_curves_types.h"
#include "DNA_lattice_types.h"
#include "DNA_object_types.h"
#include "DNA_curve_types.h"
#include "DNA_mesh_types.h"
#include "DNA_meshdata_types.h"
#include "DNA_pointcloud_types.h"


#include "RNA_access.hh"
#include "RNA_prototypes.hh"
#include "intern/rna_internal_types.hh"

namespace blender::angelo_read_geometry {

namespace {

using Role = RNAReadCallbackRole;
using bke::AttrDomain;

bool source_is(const PropertyRNA &property,
               const Role role,
               const char *file,
               const char *symbol)
{
  const RNAReadCallbackSource source = RNA_property_read_callback_source(&property, role);
  return source.state == RNAReadCallbackState::MATCHED &&
         source.origin == RNAReadCallbackOrigin::MANUAL && source.source_file &&
         source.source_identifier && STREQ(source.source_file, file) &&
         STREQ(source.source_identifier, symbol);
}

bool absent(const PropertyRNA &property, const Role role)
{
  return RNA_property_read_callback_source(&property, role).state ==
         RNAReadCallbackState::ABSENT;
}

bool no_selected_callback(const PropertyRNA &property, const Role role)
{
  const RNAReadCallbackState state = RNA_property_read_callback_source(&property, role).state;
  return state == RNAReadCallbackState::ABSENT || state == RNAReadCallbackState::NOT_APPLICABLE;
}

bool declaration(const PropertyRNA &property)
{
  return !(property.flag & PROP_IDPROPERTY) &&
         !(property.flag_internal & PROP_INTERN_RUNTIME);
}

struct ScalarBinding {
  Family family;
  const StructRNA *owner;
  const char *property;
  PropertyType type;
  Role role;
  const char *file;
  const char *symbol;
};

/* Every row binds both typed owner/property and the selected source callback. The helper never
 * dispatches a callback through RNA. This finite table is also the implementation proof ledger. */
Span<ScalarBinding> scalar_bindings()
{
  static const ScalarBinding bindings[] = {
      {MESH_VERTEX_CO, RNA_MeshVertex, "co", PROP_FLOAT, Role::GET_ARRAY, "rna_mesh.cc", "rna_MeshVertex_co_get"},
      {MESH_VERTEX_INDEX, RNA_MeshVertex, "index", PROP_INT, Role::GET, "rna_mesh.cc", "rna_MeshVertex_index_get"},
      {MESH_VERTEX_NORMAL, RNA_MeshVertex, "normal", PROP_FLOAT, Role::GET_ARRAY, "rna_mesh.cc", "rna_MeshVertex_normal_get"},
      {MESH_VERTEX_UNDEFORMED, RNA_MeshVertex, "undeformed_co", PROP_FLOAT, Role::GET_ARRAY, "rna_mesh.cc", "rna_MeshVertex_undeformed_co_get"},
      {MESH_VERTEX_SELECT, RNA_MeshVertex, "select", PROP_BOOLEAN, Role::GET, "rna_mesh.cc", "rna_MeshVertex_select_get"},
      {MESH_VERTEX_HIDE, RNA_MeshVertex, "hide", PROP_BOOLEAN, Role::GET, "rna_mesh.cc", "rna_MeshVertex_hide_get"},
      {MESH_EDGE_INDEX, RNA_MeshEdge, "index", PROP_INT, Role::GET, "rna_mesh.cc", "rna_MeshEdge_index_get"},
      {MESH_EDGE_SELECT, RNA_MeshEdge, "select", PROP_BOOLEAN, Role::GET, "rna_mesh.cc", "rna_MeshEdge_select_get"},
      {MESH_EDGE_HIDE, RNA_MeshEdge, "hide", PROP_BOOLEAN, Role::GET, "rna_mesh.cc", "rna_MeshEdge_hide_get"},
      {MESH_EDGE_SEAM, RNA_MeshEdge, "use_seam", PROP_BOOLEAN, Role::GET, "rna_mesh.cc", "rna_MeshEdge_use_seam_get"},
      {MESH_EDGE_SHARP, RNA_MeshEdge, "use_edge_sharp", PROP_BOOLEAN, Role::GET, "rna_mesh.cc", "rna_MeshEdge_use_edge_sharp_get"},
      {MESH_EDGE_LOOSE, RNA_MeshEdge, "is_loose", PROP_BOOLEAN, Role::GET, "rna_mesh.cc", "rna_MeshEdge_is_loose_get"},
      {MESH_LOOP_INDEX, RNA_MeshLoop, "index", PROP_INT, Role::GET, "rna_mesh.cc", "rna_MeshLoop_index_get"},
      {MESH_LOOP_VERTEX, RNA_MeshLoop, "vertex_index", PROP_INT, Role::GET, "rna_mesh.cc", "rna_MeshLoop_vertex_index_get"},
      {MESH_LOOP_EDGE, RNA_MeshLoop, "edge_index", PROP_INT, Role::GET, "rna_mesh.cc", "rna_MeshLoop_edge_index_get"},
      {MESH_LOOP_NORMAL, RNA_MeshLoop, "normal", PROP_FLOAT, Role::GET_ARRAY, "rna_mesh.cc", "rna_MeshLoop_normal_get"},
      {MESH_LOOP_TANGENT, RNA_MeshLoop, "tangent", PROP_FLOAT, Role::GET_ARRAY, "rna_mesh.cc", "rna_MeshLoop_tangent_get"},
      {MESH_LOOP_BITANGENT_SIGN, RNA_MeshLoop, "bitangent_sign", PROP_FLOAT, Role::GET, "rna_mesh.cc", "rna_MeshLoop_bitangent_sign_get"},
      {MESH_LOOP_BITANGENT, RNA_MeshLoop, "bitangent", PROP_FLOAT, Role::GET_ARRAY, "rna_mesh.cc", "rna_MeshLoop_bitangent_get"},
      {MESH_FACE_INDEX, RNA_MeshPolygon, "index", PROP_INT, Role::GET, "rna_mesh.cc", "rna_MeshPolygon_index_get"},
      {MESH_FACE_VERTICES, RNA_MeshPolygon, "vertices", PROP_INT, Role::GET_ARRAY, "rna_mesh.cc", "rna_MeshPoly_vertices_get"},
      {MESH_FACE_LOOP_TOTAL, RNA_MeshPolygon, "loop_total", PROP_INT, Role::GET, "rna_mesh.cc", "rna_MeshPolygon_loop_total_get"},
      {MESH_FACE_MATERIAL, RNA_MeshPolygon, "material_index", PROP_INT, Role::GET, "rna_mesh.cc", "rna_MeshPolygon_material_index_get"},
      {MESH_FACE_SELECT, RNA_MeshPolygon, "select", PROP_BOOLEAN, Role::GET, "rna_mesh.cc", "rna_MeshPolygon_select_get"},
      {MESH_FACE_HIDE, RNA_MeshPolygon, "hide", PROP_BOOLEAN, Role::GET, "rna_mesh.cc", "rna_MeshPolygon_hide_get"},
      {MESH_FACE_SMOOTH, RNA_MeshPolygon, "use_smooth", PROP_BOOLEAN, Role::GET, "rna_mesh.cc", "rna_MeshPolygon_use_smooth_get"},
      {MESH_FACE_NORMAL, RNA_MeshPolygon, "normal", PROP_FLOAT, Role::GET_ARRAY, "rna_mesh.cc", "rna_MeshPolygon_normal_get"},
      {MESH_FACE_CENTER, RNA_MeshPolygon, "center", PROP_FLOAT, Role::GET_ARRAY, "rna_mesh.cc", "rna_MeshPolygon_center_get"},
      {MESH_FACE_AREA, RNA_MeshPolygon, "area", PROP_FLOAT, Role::GET, "rna_mesh.cc", "rna_MeshPolygon_area_get"},
      {MESH_TRI_INDEX, RNA_MeshLoopTriangle, "index", PROP_INT, Role::GET, "rna_mesh.cc", "rna_MeshLoopTriangle_index_get"},
      {MESH_TRI_FACE, RNA_MeshLoopTriangle, "polygon_index", PROP_INT, Role::GET, "rna_mesh.cc", "rna_MeshLoopTriangle_polygon_index_get"},
      {MESH_TRI_VERTICES, RNA_MeshLoopTriangle, "vertices", PROP_INT, Role::GET_ARRAY, "rna_mesh.cc", "rna_MeshLoopTriangle_verts_get"},
      {MESH_TRI_NORMAL, RNA_MeshLoopTriangle, "normal", PROP_FLOAT, Role::GET_ARRAY, "rna_mesh.cc", "rna_MeshLoopTriangle_normal_get"},
      {MESH_TRI_SPLIT_NORMALS, RNA_MeshLoopTriangle, "split_normals", PROP_FLOAT, Role::GET_ARRAY, "rna_mesh.cc", "rna_MeshLoopTriangle_split_normals_get"},
      {MESH_TRI_AREA, RNA_MeshLoopTriangle, "area", PROP_FLOAT, Role::GET, "rna_mesh.cc", "rna_MeshLoopTriangle_area_get"},
      {MESH_TRI_MATERIAL, RNA_MeshLoopTriangle, "material_index", PROP_INT, Role::GET, "rna_mesh.cc", "rna_MeshLoopTriangle_material_index_get"},
      {MESH_TRI_SMOOTH, RNA_MeshLoopTriangle, "use_smooth", PROP_BOOLEAN, Role::GET, "rna_mesh.cc", "rna_MeshLoopTriangle_use_smooth_get"},
      {MESH_TEXSPACE_LOCATION, RNA_Mesh, "texspace_location", PROP_FLOAT, Role::GET_ARRAY, "rna_mesh.cc", "rna_Mesh_texspace_location_get"},
      {MESH_TEXSPACE_SIZE, RNA_Mesh, "texspace_size", PROP_FLOAT, Role::GET_ARRAY, "rna_mesh.cc", "rna_Mesh_texspace_size_get"},
      {MESH_NORMALS_DOMAIN, RNA_Mesh, "normals_domain", PROP_ENUM, Role::GET, "rna_mesh.cc", "rna_Mesh_normals_domain_get"},
      {MESH_UV_PIN, RNA_MeshUVLoop, "pin_uv", PROP_BOOLEAN, Role::GET, "rna_mesh.cc", "rna_MeshUVLoop_pin_uv_get"},
      {CURVE_POINT_LOCATION, RNA_CurvePoint, "position", PROP_FLOAT, Role::GET_ARRAY, "rna_curves.cc", "rna_CurvePoint_location_get"},
      {CURVE_POINT_INDEX, RNA_CurvePoint, "index", PROP_INT, Role::GET, "rna_curves.cc", "rna_CurvePoint_index_get"},
      {CURVE_POINT_RADIUS, RNA_CurvePoint, "radius", PROP_FLOAT, Role::GET, "rna_curves.cc", "rna_CurvePoint_radius_get"},
      {CURVE_SLICE_INDEX, RNA_CurveSlice, "index", PROP_INT, Role::GET, "rna_curves.cc", "rna_CurveSlice_index_get"},
      {CURVE_SLICE_FIRST, RNA_CurveSlice, "first_point_index", PROP_INT, Role::GET, "rna_curves.cc", "rna_CurveSlice_first_point_index_get"},
      {CURVE_SLICE_LENGTH, RNA_CurveSlice, "points_length", PROP_INT, Role::GET, "rna_curves.cc", "rna_CurveSlice_points_length_get"},
      {POINT_LOCATION, RNA_Point, "co", PROP_FLOAT, Role::GET_ARRAY, "rna_pointcloud.cc", "rna_Point_location_get"},
      {POINT_INDEX, RNA_Point, "index", PROP_INT, Role::GET, "rna_pointcloud.cc", "rna_Point_index_get"},
      {POINT_RADIUS, RNA_Point, "radius", PROP_FLOAT, Role::GET, "rna_pointcloud.cc", "rna_Point_radius_get"},
      {GP_FRAME_NUMBER, RNA_GreasePencilFrame, "frame_number", PROP_INT, Role::GET, "rna_grease_pencil.cc", "rna_Frame_frame_number_get"},
      {GP_LAYER_TINT, RNA_GreasePencilLayer, "tint_color", PROP_FLOAT, Role::GET_ARRAY, "rna_grease_pencil.cc", "rna_GreasePencilLayer_tint_color_get"},
      {GP_LAYER_TINT_FACTOR, RNA_GreasePencilLayer, "tint_factor", PROP_FLOAT, Role::GET, "rna_grease_pencil.cc", "rna_GreasePencilLayer_tint_factor_get"},
      {GP_LAYER_RADIUS_OFFSET, RNA_GreasePencilLayer, "radius_offset", PROP_FLOAT, Role::GET, "rna_grease_pencil.cc", "rna_GreasePencilLayer_radius_offset_get"},
      {GP_LAYER_PASS, RNA_GreasePencilLayer, "pass_index", PROP_INT, Role::GET, "rna_grease_pencil.cc", "rna_GreasePencilLayer_pass_index_get"},
      {GP_LAYER_MATRIX_LOCAL, RNA_GreasePencilLayer, "matrix_local", PROP_FLOAT, Role::GET_ARRAY, "rna_grease_pencil.cc", "rna_GreasePencilLayer_matrix_local_get"},
      {GP_LAYER_MATRIX_PARENT, RNA_GreasePencilLayer, "matrix_parent_inverse", PROP_FLOAT, Role::GET_ARRAY, "rna_grease_pencil.cc", "rna_GreasePencilLayer_matrix_parent_inverse_get"},
      {LEGACY_CURVE_TEXSPACE_LOCATION, RNA_Curve, "texspace_location", PROP_FLOAT, Role::GET_ARRAY, "rna_curve.cc", "rna_Curve_texspace_location_get"},
      {LEGACY_CURVE_TEXSPACE_SIZE, RNA_Curve, "texspace_size", PROP_FLOAT, Role::GET_ARRAY, "rna_curve.cc", "rna_Curve_texspace_size_get"},
      {LEGACY_CURVE_TEXSPACE_LOCATION, RNA_SurfaceCurve, "texspace_location", PROP_FLOAT, Role::GET_ARRAY, "rna_curve.cc", "rna_Curve_texspace_location_get"},
      {LEGACY_CURVE_TEXSPACE_SIZE, RNA_SurfaceCurve, "texspace_size", PROP_FLOAT, Role::GET_ARRAY, "rna_curve.cc", "rna_Curve_texspace_size_get"},
      {LEGACY_CURVE_TEXSPACE_LOCATION, RNA_TextCurve, "texspace_location", PROP_FLOAT, Role::GET_ARRAY, "rna_curve.cc", "rna_Curve_texspace_location_get"},
      {LEGACY_CURVE_TEXSPACE_SIZE, RNA_TextCurve, "texspace_size", PROP_FLOAT, Role::GET_ARRAY, "rna_curve.cc", "rna_Curve_texspace_size_get"},
  };
  return Span<ScalarBinding>(bindings, ARRAY_SIZE(bindings));
}

const ScalarBinding *scalar_binding(const StructRNA &owner, const PropertyRNA &property)
{
  for (const ScalarBinding &binding : scalar_bindings()) {
    if (&owner == binding.owner && property.identifier &&
        STREQ(property.identifier, binding.property) && property.type == binding.type &&
        source_is(property, binding.role, binding.file, binding.symbol))
    {
      return &binding;
    }
  }
  return nullptr;
}

/* Cache writes are permitted only to this operation-owned runtime. DNA storage is a const borrow;
 * Mesh's default destructor does not free DNA storage, and this view never enters Main or RNA. */
struct MeshView {
  Mesh mesh;
  bke::MeshRuntime runtime;

  explicit MeshView(const Mesh &source) : mesh(dna::shallow_copy(source))
  {
    mesh.runtime = &runtime;
    runtime.edit_mesh = source.runtime->edit_mesh;
    runtime.wrapper_type = source.runtime->wrapper_type;
    if (source.runtime->edit_data) {
      runtime.edit_data = std::make_unique<bke::EditMeshData>();
      runtime.edit_data->vert_positions = source.runtime->edit_data->vert_positions;
    }
    /* Copy valid data, never SharedCache itself: SharedCache copy would share its writable cache.
     * Seeding valid results preserves existing warm-cache and frozen triangulation behavior. */
#define COPY_CACHE(name) \
    if (source.runtime->name.is_cached()) { \
      runtime.name.ensure([&](auto &data) { data = source.runtime->name.data(); }); \
    }
    COPY_CACHE(bounds_cache)
    COPY_CACHE(corner_tri_faces_cache)
    COPY_CACHE(vert_normals_cache)
    COPY_CACHE(vert_normals_true_cache)
    COPY_CACHE(face_normals_cache)
    COPY_CACHE(face_normals_true_cache)
    COPY_CACHE(corner_normals_cache)
    COPY_CACHE(vert_to_face_offset_cache)
    COPY_CACHE(vert_to_face_map_cache)
    COPY_CACHE(vert_to_corner_map_cache)
    COPY_CACHE(corner_to_face_map_cache)
#undef COPY_CACHE
    if (source.runtime->corner_tris_cache.data.is_cached()) {
      runtime.corner_tris_cache.data.ensure(
          [&](Array<int3> &data) { data = source.runtime->corner_tris_cache.data.data(); });
    }
    runtime.corner_tris_cache.frozen = source.runtime->corner_tris_cache.frozen;
    runtime.corner_tris_cache.dirty_while_frozen =
        source.runtime->corner_tris_cache.dirty_while_frozen;
    const auto copy_loose = [](const auto &input, auto &output) {
      if (input.is_cached()) {
        output.ensure([&](bke::LooseGeomCache &data) {
          Array<int64_t> indices(input.data().mask.size());
          input.data().mask.to_indices(indices.as_mutable_span());
          data.mask = IndexMask::from_indices(indices.as_span(), data.allocator);
        });
      }
    };
    copy_loose(source.runtime->loose_edges_cache, runtime.loose_edges_cache);
    copy_loose(source.runtime->loose_verts_cache, runtime.loose_verts_cache);
    copy_loose(source.runtime->verts_no_face_cache, runtime.verts_no_face_cache);
  }
};

/* Base DNA object has no owning destructor. Its bke wrapper is used only while this private
 * runtime is alive; no CurvesGeometry owning copy/destructor sees the borrowed DNA fields. */
struct CurvesView {
  blender::CurvesGeometry geometry;
  bke::CurvesGeometryRuntime runtime;
  Array<float3> normals;
  bool normals_ready = false;

  explicit CurvesView(const blender::CurvesGeometry &source) : geometry(source)
  {
    geometry.runtime = &runtime;
    runtime.type_counts = source.runtime->type_counts;
    runtime.check_type_counts = source.runtime->check_type_counts;
#define COPY_CACHE(name) \
    if (source.runtime->name.is_cached()) { \
      runtime.name.ensure([&](auto &data) { data = source.runtime->name.data(); }); \
    }
    COPY_CACHE(evaluated_offsets_cache)
    COPY_CACHE(has_cyclic_curve_cache)
    COPY_CACHE(nurbs_basis_cache)
    COPY_CACHE(evaluated_position_cache)
    COPY_CACHE(evaluated_tangent_cache)
    COPY_CACHE(evaluated_normal_cache)
    COPY_CACHE(custom_knot_offsets_cache)
    COPY_CACHE(bounds_cache)
    COPY_CACHE(bounds_with_radius_cache)
#undef COPY_CACHE
  }

  Span<float3> point_normals()
  {
    if (!normals_ready) {
      normals.reinitialize(geometry.point_num);
      const VArray<float3> values = bke::curve_normals_varray(geometry.wrap(), AttrDomain::Point);
      if (values.size() != normals.size()) {
        return {};
      }
      values.materialize(normals.as_mutable_span());
      normals_ready = true;
    }
    return normals;
  }
};

struct PointCloudView {
  PointCloud points;
  bke::PointCloudRuntime runtime;

  explicit PointCloudView(const PointCloud &source) : points(source)
  {
    points.runtime = &runtime;
    if (source.runtime->bounds_cache.is_cached()) {
      runtime.bounds_cache.ensure([&](auto &data) { data = source.runtime->bounds_cache.data(); });
    }
    if (source.runtime->bounds_with_radius_cache.is_cached()) {
      runtime.bounds_with_radius_cache.ensure(
          [&](auto &data) { data = source.runtime->bounds_with_radius_cache.data(); });
    }
  }
};

struct LegacyCurveView {
  Curve curve;
  std::optional<Bounds<float3>> bounds;
  Status status = Status::OK;
  bool bounds_ready = false;
  explicit LegacyCurveView(const Curve &source) : curve(dna::shallow_copy(source)) {}
};

template<typename T> Value owned_scalar(T data)
{
  Value result;
  result.status = Status::OK;
  result.data = data;
  return result;
}

template<typename T> Value owned_array(const Span<T> values)
{
  Value result;
  result.status = Status::OK;
  result.actual_length = values.size();
  const int64_t retained = std::min<int64_t>(64, values.size());
  std::vector<T> output;
  if (retained) {
    output.assign(values.begin(), values.begin() + retained);
  }
  result.data = std::move(output);
  return result;
}

Value owned_vector(const float3 &value)
{
  return owned_array<float>({value, 3});
}

Value invalid()
{
  Value result;
  result.status = Status::INVALID_STORAGE;
  return result;
}

/* Keeps the reason (NOT_EVALUATED, LOADS_FILE, ...) a helper refused with. */
Value failed(const Status status)
{
  Value result;
  result.status = status;
  return result;
}

template<typename T> int64_t pointer_index(const void *pointer, const Span<T> values)
{
  const uintptr_t candidate = uintptr_t(pointer), begin = uintptr_t(values.data());
  if (!pointer || candidate < begin || candidate - begin >= uint64_t(values.size_in_bytes()) ||
      (candidate - begin) % sizeof(T))
  {
    return -1;
  }
  return int64_t((candidate - begin) / sizeof(T));
}

int domain_size(const Mesh &mesh, AttrDomain domain)
{
  switch (domain) {
    case AttrDomain::Point: return mesh.verts_num;
    case AttrDomain::Edge: return mesh.edges_num;
    case AttrDomain::Face: return mesh.faces_num;
    case AttrDomain::Corner: return mesh.corners_num;
    default: return -1;
  }
}

bool attribute_valid(const bke::Attribute &attribute, const int64_t count)
{
  if (count < 0 || int(attribute.data_type()) < int(bke::AttrType::Bool) ||
      int(attribute.data_type()) > int(bke::AttrType::Float4) ||
      !bke::attr_type_to_custom_data_type(attribute.data_type())) {
    return false;
  }
  if (const auto *data = std::get_if<bke::Attribute::ArrayData>(&attribute.data())) {
    return data->size == count && (count == 0 || data->data);
  }
  if (const auto *data = std::get_if<bke::Attribute::SingleData>(&attribute.data())) {
    return data->value != nullptr;
  }
  return false;
}

bool mesh_valid(const Mesh &mesh)
{
  if (!mesh.runtime || !mesh.attribute_storage.runtime || mesh.verts_num < 0 || mesh.edges_num < 0 || mesh.faces_num < 0 ||
      mesh.corners_num < 0)
  {
    return false;
  }
  const bke::AttributeStorage &storage = mesh.attribute_storage.wrap();
  const auto array = [&](const char *name, const AttrDomain domain, const bke::AttrType type,
                         const int count) {
    const bke::Attribute *attribute = storage.lookup(name);
    if (!attribute) {
      return count == 0;
    }
    return attribute->domain() == domain && attribute->data_type() == type &&
           std::holds_alternative<bke::Attribute::ArrayData>(attribute->data()) &&
           attribute_valid(*attribute, count);
  };
  if (!array("position", AttrDomain::Point, bke::AttrType::Float3, mesh.verts_num) ||
      !array(".edge_verts", AttrDomain::Edge, bke::AttrType::Int32_2D, mesh.edges_num) ||
      !array(".corner_vert", AttrDomain::Corner, bke::AttrType::Int32, mesh.corners_num) ||
      !array(".corner_edge", AttrDomain::Corner, bke::AttrType::Int32, mesh.corners_num))
  {
    return false;
  }
  for (const bke::Attribute &attribute : storage) {
    if (!attribute_valid(attribute, domain_size(mesh, attribute.domain()))) {
      return false;
    }
  }
  const Span<int> offsets = mesh.face_offsets();
  if (mesh.faces_num && (!offsets.data() || offsets[0] != 0 ||
                         offsets.last() != mesh.corners_num))
  {
    return false;
  }
  for (const int face : IndexRange(mesh.faces_num)) {
    if (offsets[face + 1] - offsets[face] < 3 || offsets[face] < 0 ||
        offsets[face + 1] > mesh.corners_num)
    {
      return false;
    }
  }
  for (const int2 &edge : mesh.edges()) {
    if (edge[0] < 0 || edge[1] < 0 || edge[0] >= mesh.verts_num || edge[1] >= mesh.verts_num) {
      return false;
    }
  }
  for (const int index : mesh.corner_verts()) {
    if (index < 0 || index >= mesh.verts_num) {
      return false;
    }
  }
  for (const int index : mesh.corner_edges()) {
    if (index < 0 || index >= mesh.edges_num) {
      return false;
    }
  }
  for (const MDeformVert &vertex : mesh.deform_verts()) {
    if (vertex.totweight < 0 || (vertex.totweight && !vertex.dw)) {
      return false;
    }
  }
  return true;
}

bool curves_valid(const blender::CurvesGeometry &geometry)
{
  if (!geometry.runtime || !geometry.attribute_storage.runtime || geometry.point_num < 0 || geometry.curve_num < 0 ||
      (geometry.curve_num && !geometry.curve_offsets))
  {
    return false;
  }
  const bke::AttributeStorage &storage = geometry.attribute_storage.wrap();
  const bke::Attribute *positions = storage.lookup("position");
  if (geometry.point_num &&
      (!positions || positions->domain() != AttrDomain::Point ||
       positions->data_type() != bke::AttrType::Float3 ||
       !std::holds_alternative<bke::Attribute::ArrayData>(positions->data())))
  {
    return false;
  }
  for (const bke::Attribute &attribute : storage) {
    const int count = attribute.domain() == AttrDomain::Point ? geometry.point_num :
                      attribute.domain() == AttrDomain::Curve ? geometry.curve_num : -1;
    if (!attribute_valid(attribute, count)) {
      return false;
    }
  }
  if (geometry.curve_num &&
      (geometry.curve_offsets[0] != 0 ||
       geometry.curve_offsets[geometry.curve_num] != geometry.point_num))
  {
    return false;
  }
  for (const int curve : IndexRange(geometry.curve_num)) {
    if (geometry.curve_offsets[curve + 1] <= geometry.curve_offsets[curve] ||
        geometry.curve_offsets[curve] < 0 ||
        geometry.curve_offsets[curve + 1] > geometry.point_num)
    {
      return false;
    }
  }
  return true;
}

struct Hierarchy {
  std::vector<const bke::greasepencil::Layer *> layers;
  std::vector<const bke::greasepencil::LayerGroup *> groups;
  std::vector<const bke::greasepencil::TreeNode *> roots;
  std::unordered_set<const bke::greasepencil::TreeNode *> visited;
  Status status = Status::OK;

  void append(const bke::greasepencil::LayerGroup &group, const bool root)
  {
    const GreasePencilLayerTreeNode *previous = nullptr;
    for (const GreasePencilLayerTreeNode &child : group.children) {
      if (child.prev != previous || !visited.insert(&child.wrap()).second) {
        status = Status::INVALID_STORAGE;
        return;
      }
      previous = &child;
      const bke::greasepencil::TreeNode &node = child.wrap();
      if (root) {
        roots.push_back(&node);
      }
      if (node.is_layer()) {
        layers.push_back(&node.as_layer());
      }
      else if (node.is_group()) {
        groups.push_back(&node.as_group());
        append(node.as_group(), false);
        if (status != Status::OK) {
          return;
        }
      }
      else {
        status = Status::INVALID_STORAGE;
        return;
      }
    }
    if (previous != group.children.last) {
      status = Status::INVALID_STORAGE;
    }
  }
};

}  // namespace

struct Scope::Impl {
  /* Operation-private views of borrowed geometry; destroyed with the read's Scope. */
  std::unordered_map<const Mesh *, std::unique_ptr<MeshView>> meshes;
  std::unordered_map<const blender::CurvesGeometry *, std::unique_ptr<CurvesView>> curves;
  std::unordered_map<const GreasePencil *, std::unique_ptr<Hierarchy>> hierarchies;
  std::unordered_map<const bke::greasepencil::Layer *, std::vector<int>> frame_keys;
  std::unordered_map<const Curve *, std::unique_ptr<LegacyCurveView>> legacy_curves;
  std::unordered_map<const PointCloud *, std::unique_ptr<PointCloudView>> pointclouds;
  std::unordered_set<const Object *> bounds_stack;

  LegacyCurveView &legacy_curve(const Curve &source)
  {
    auto [found, inserted] = legacy_curves.try_emplace(&source);
    if (inserted) {
      found->second = std::make_unique<LegacyCurveView>(source);
    }
    return *found->second;
  }

  Status legacy_bounds(const Curve &source, std::optional<Bounds<float3>> &result)
  {
    LegacyCurveView &view = legacy_curve(source);
    if (!view.bounds_ready) {
      const ListBaseT<Nurb> *nurbs = BKE_curve_nurbs_get_for_read(&source);
      std::unordered_set<const Nurb *> visited;
      const Nurb *previous = nullptr;
      for (const Nurb &nurb : *nurbs) {
        if (!visited.insert(&nurb).second || nurb.prev != previous ||
            nurb.pntsu < 0 || nurb.pntsv < 0 ||
            (nurb.type == CU_BEZIER ? (nurb.pntsu && !nurb.bezt) :
             (int64_t(nurb.pntsu) * nurb.pntsv && !nurb.bp)))
        {
          view.status = Status::INVALID_STORAGE;
          break;
        }
        previous = &nurb;
      }
      if (previous != nurbs->last) view.status = Status::INVALID_STORAGE;
      if (view.status == Status::OK) {
        /* A text curve keeps no stored splines: Blender's bounds would convert its glyphs, which
         * can load the font file. Other curves compute bounds from their stored splines only. */
        if (nurbs->is_empty() && source.len != 0) {
          view.status = Status::LOADS_FILE;
        }
        else {
          view.bounds = BKE_curve_minmax(&source, true);
        }
      }
      view.bounds_ready = true;
    }
    result = view.bounds;
    return view.status;
  }

  Status mesh_bounds(const Mesh &source, std::optional<Bounds<float3>> &result)
  {
    if (!source.runtime) return Status::INVALID_STORAGE;
    if (!ELEM(source.runtime->wrapper_type, ME_WRAPPER_TYPE_BMESH,
              ME_WRAPPER_TYPE_MDATA, ME_WRAPPER_TYPE_SUBD)) return Status::INVALID_STORAGE;
    const int count = source.runtime->wrapper_type == ME_WRAPPER_TYPE_BMESH ?
                      (source.runtime->edit_mesh && source.runtime->edit_mesh->bm ?
                           source.runtime->edit_mesh->bm->totvert : -1) : source.verts_num;
    if (count < 0) return Status::INVALID_STORAGE;
    if (!count) { result.reset(); return Status::OK; }
    if (source.runtime->bounds_cache.is_cached()) {
      result = source.runtime->bounds_cache.data();
      return Status::OK;
    }
    if (source.runtime->wrapper_type == ME_WRAPPER_TYPE_BMESH) {
      if (!source.runtime->edit_data) return Status::INVALID_STORAGE;
      /* This source kernel is already const: it iterates BMesh or a stored deformation span,
       * without tables, indices, normals or caches. Avoid validating unrelated DNA topology. */
      result = BKE_editmesh_cache_calc_minmax(*source.runtime->edit_mesh, *source.runtime->edit_data);
      return Status::OK;
    }
    MeshView *view = mesh(source);
    if (!view) return Status::INVALID_STORAGE;
    result = view->mesh.bounds_min_max();
    return Status::OK;
  }

  Status curve_bounds(const blender::CurvesGeometry &source,
                      bool use_radius,
                      std::optional<Bounds<float3>> &result)
  {
    if (!source.runtime || source.point_num < 0 || source.curve_num < 0) return Status::INVALID_STORAGE;
    if (source.wrap().is_empty()) { result.reset(); return Status::OK; }
    const auto &cache = use_radius ? source.runtime->bounds_with_radius_cache : source.runtime->bounds_cache;
    if (cache.is_cached()) { result = cache.data(); return Status::OK; }
    CurvesView *view = curve(source);
    if (!view) return Status::INVALID_STORAGE;
    result = view->geometry.wrap().bounds_min_max(use_radius);
    return Status::OK;
  }

  Status pointcloud_bounds(const PointCloud &source,
                           bool use_radius,
                           std::optional<Bounds<float3>> &result)
  {
    if (!source.runtime || !source.attribute_storage.runtime || source.totpoint < 0) {
      return Status::INVALID_STORAGE;
    }
    if (source.totpoint == 0) {
      result.reset();
      return Status::OK;
    }
    const auto &cache = use_radius ? source.runtime->bounds_with_radius_cache :
                                    source.runtime->bounds_cache;
    if (cache.is_cached()) {
      result = cache.data();
      return Status::OK;
    }
    for (const bke::Attribute &attribute : source.attribute_storage.wrap()) {
      if (attribute.domain() != AttrDomain::Point || !attribute_valid(attribute, source.totpoint)) {
        return Status::INVALID_STORAGE;
      }
    }
    const bke::Attribute *position = source.attribute_storage.wrap().lookup("position");
    if (!position || position->data_type() != bke::AttrType::Float3 ||
        !std::holds_alternative<bke::Attribute::ArrayData>(position->data()))
    {
      return Status::INVALID_STORAGE;
    }
    auto [found, inserted] = pointclouds.try_emplace(&source);
    if (inserted) found->second = std::make_unique<PointCloudView>(source);
    result = found->second->points.bounds_min_max(use_radius);
    return Status::OK;
  }

  Status grease_pencil_bounds(const GreasePencil &source,
                             bool use_radius,
                             std::optional<Bounds<float3>> &result)
  {
    if (!source.runtime || source.drawing_array_num < 0 ||
        (source.drawing_array_num && !source.drawing_array)) return Status::INVALID_STORAGE;
    Hierarchy *tree = hierarchy(source);
    if (!tree || tree->status != Status::OK) return Status::INVALID_STORAGE;
    result.reset();
    for (const bke::greasepencil::Layer *layer : tree->layers) {
      if (!layer->is_visible()) continue;
      if (!layer->runtime) return Status::INVALID_STORAGE;
      const std::vector<int> &sorted = keys(*layer);
      const auto next = std::upper_bound(sorted.begin(), sorted.end(), source.runtime->eval_frame);
      if (next == sorted.begin()) continue;
      const GreasePencilFrame *frame = layer->frames().lookup_ptr(*std::prev(next));
      if (!frame) return Status::INVALID_STORAGE;
      if (frame->is_end() || frame->drawing_index == -1) continue;
      if (frame->drawing_index < 0 || frame->drawing_index >= source.drawing_array_num) {
        return Status::INVALID_STORAGE;
      }
      const GreasePencilDrawingBase *drawing = source.drawing_array[frame->drawing_index];
      if (!drawing) return Status::INVALID_STORAGE;
      /* Canonical get_drawing_at intentionally ignores drawing references. */
      if (drawing->type != GP_DRAWING) continue;
      const auto &strokes = reinterpret_cast<const GreasePencilDrawing *>(drawing)->wrap().strokes();
      if (strokes.is_empty()) continue;
      CurvesView *view = curve(strokes);
      if (!view) return Status::INVALID_STORAGE;
      const bke::CurvesGeometry &curves = view->geometry.wrap();
      const float4x4 transform = layer->local_transform();
      std::optional<Bounds<float3>> drawing_bounds;
      if (transform == float4x4::identity()) {
        drawing_bounds = curves.bounds_min_max(use_radius);
      }
      else {
        const VArray<float> radius = curves.radius();
        Array<float3> positions_world(curves.evaluated_points_num());
        math::transform_points(curves.evaluated_positions(), transform, positions_world);
        if (!use_radius) {
          drawing_bounds = bounds::min_max(positions_world.as_span());
        }
        else if (const std::optional single = radius.get_if_single()) {
          /* Preserve canonical GP single-radius branch, including its untransformed bounds. */
          drawing_bounds = curves.bounds_min_max(false);
          if (drawing_bounds) drawing_bounds->pad(*single);
        }
        else if (curves.is_single_type(CURVE_TYPE_POLY)) {
          drawing_bounds = bounds::min_max_with_radii(positions_world.as_span(), radius.get_internal_span());
        }
        else {
          curves.ensure_can_interpolate_to_evaluated(); /* Private runtime only. */
          Array<float> radii(curves.evaluated_points_num());
          curves.interpolate_to_evaluated(radius.get_internal_span(), radii.as_mutable_span());
          drawing_bounds = bounds::min_max_with_radii(positions_world.as_span(), radii.as_span());
        }
      }
      result = bounds::merge(result, drawing_bounds);
    }
    return Status::OK;
  }

  Status volume_bounds(const Volume & /*volume*/, std::optional<Bounds<float3>> &result)
  {
    /* Volume bounds read grid trees, which Blender loads from disk on demand. */
    result.reset();
    return Status::LOADS_FILE;
  }

  Status evaluated_bounds(const Object &object, std::optional<Bounds<float3>> &result)
  {
    if (const bke::GeometrySet *geometry = object.runtime->geometry_set_eval) {
      std::optional<Bounds<float3>> component;
      Status status;
      const bool use_radius = object.type != OB_CURVES_LEGACY;
      if (const PointCloud *points = geometry->get_pointcloud()) {
        status = pointcloud_bounds(*points, use_radius, component);
        if (status != Status::OK) return status;
        result = bounds::merge(result, component);
      }
      if (const Mesh *source = geometry->get_mesh()) {
        if (!source->runtime) return Status::INVALID_STORAGE;
        const Mesh &mesh = source->runtime->mesh_eval ? *source->runtime->mesh_eval : *source;
        status = mesh_bounds(mesh, component);
        if (status != Status::OK) return status;
        result = bounds::merge(result, component);
      }
      if (const Volume *volume = geometry->get_volume()) {
        status = volume_bounds(*volume, component);
        if (status != Status::OK) return status;
        result = bounds::merge(result, component);
      }
      if (const Curves *curves = geometry->get_curves()) {
        status = curve_bounds(curves->geometry, use_radius, component);
        if (status != Status::OK) return status;
        result = bounds::merge(result, component);
      }
      if (const GreasePencil *gp = geometry->get_grease_pencil()) {
        status = grease_pencil_bounds(*gp, use_radius, component);
        if (status != Status::OK) return status;
        result = bounds::merge(result, component);
      }
      return Status::OK;
    }
    if (object.runtime->curve_cache) {
      float3 min(std::numeric_limits<float>::max()), max(std::numeric_limits<float>::lowest());
      BKE_displist_minmax(&object.runtime->curve_cache->disp, min, max);
      result = Bounds<float3>{min, max};
    }
    return Status::OK;
  }

  Status bounds(const Object &object, BoundsMode mode, std::optional<Bounds<float3>> &result)
  {
    result.reset();
    if (!object.runtime) return Status::INVALID_STORAGE;
    if (mode == BoundsMode::EVALUATED_CACHE_THEN_DATA && object.runtime->bounds_eval) {
      result = object.runtime->bounds_eval;
      return Status::OK;
    }
    if (!bounds_stack.insert(&object).second) return Status::INVALID_STORAGE;
    const auto pop = [&]() { bounds_stack.erase(&object); };
    struct Pop { decltype(pop) &fn; ~Pop() { fn(); } } guard{pop};
    switch (object.type) {
      case OB_MESH:
        if (!object.data || GS(object.data->name) != ID_ME) return Status::INVALID_STORAGE;
        return mesh_bounds(*reinterpret_cast<const Mesh *>(object.data), result);
      case OB_CURVES:
        if (!object.data || GS(object.data->name) != ID_CV) return Status::INVALID_STORAGE;
        return curve_bounds(reinterpret_cast<const Curves *>(object.data)->geometry, true, result);
      case OB_CURVES_LEGACY: case OB_SURF: case OB_FONT:
        if (!object.data || GS(object.data->name) != ID_CU_LEGACY) return Status::INVALID_STORAGE;
        return legacy_bounds(*reinterpret_cast<const Curve *>(object.data), result);
      case OB_POINTCLOUD:
        if (!object.data || GS(object.data->name) != ID_PT) return Status::INVALID_STORAGE;
        return pointcloud_bounds(*reinterpret_cast<const PointCloud *>(object.data), true, result);
      case OB_GREASE_PENCIL:
        if (!object.data || GS(object.data->name) != ID_GP) return Status::INVALID_STORAGE;
        return grease_pencil_bounds(*reinterpret_cast<const GreasePencil *>(object.data), true, result);
      case OB_MBALL:
        return evaluated_bounds(object, result);
      case OB_LATTICE: {
        if (!object.data || GS(object.data->name) != ID_LT) return Status::INVALID_STORAGE;
        const Lattice *lattice = reinterpret_cast<const Lattice *>(object.data);
        if (lattice->editlatt) lattice = lattice->editlatt->latt;
        if (!lattice || lattice->pntsu < 0 || lattice->pntsv < 0 || lattice->pntsw < 0) {
          return Status::INVALID_STORAGE;
        }
        const int64_t count = int64_t(lattice->pntsu) * lattice->pntsv * lattice->pntsw;
        if (!count) return Status::OK;
        if (!lattice->def || count > INT_MAX) return Status::INVALID_STORAGE;
        float3 min(lattice->def[0].vec), max(min);
        for (int64_t i = 0; i < count; i++) minmax_v3v3_v3(min, max, lattice->def[i].vec);
        result = Bounds<float3>{min, max};
        return Status::OK;
      }
      case OB_ARMATURE:
        return armature_bounds(object, result);
      case OB_VOLUME:
        if (!object.data || GS(object.data->name) != ID_VO) return Status::INVALID_STORAGE;
        return volume_bounds(*reinterpret_cast<const Volume *>(object.data), result);
      default:
        return Status::OK;
    }
  }

  Status armature_bounds(const Object &object, std::optional<Bounds<float3>> &result)
  {
    if (!object.pose) return Status::OK;
    if (!object.data || GS(object.data->name) != ID_AR) return Status::INVALID_STORAGE;
    const auto &armature = *reinterpret_cast<const bArmature *>(object.data);
    if (!armature.runtime) return Status::INVALID_STORAGE;
    std::unordered_map<const bPoseChannel *, const Bone *> bones;
    const bool warm = armature.runtime->is_bones_array_valid() &&
                      armature.runtime->bones_generation_count == object.runtime->pose_bones_generation_count;
    for (const bPoseChannel &channel : object.pose->chanbase) {
      const Bone *bone = nullptr;
      if (warm && channel.runtime.bone_index != BONE_INDEX_UNKNOWN) {
        if (channel.runtime.bone_index < 0 || channel.runtime.bone_index >= armature.runtime->bones.size()) {
          return Status::INVALID_STORAGE;
        }
        bone = armature.runtime->bones[channel.runtime.bone_index];
      }
      bones.emplace(&channel, bone);
    }
    if (!warm) {
      BKE_armature_foreach_bone(armature, [&](int, const Bone &bone) {
        if (const bPoseChannel *channel = BKE_pose_channel_find_name(object.pose, bone.name)) {
          bones[channel] = &bone;
        }
      });
    }
    float3 min(std::numeric_limits<float>::max()), max(std::numeric_limits<float>::lowest());
    bool found = false;
    for (const bPoseChannel &channel : object.pose->chanbase) {
      const Bone *bone = bones.at(&channel);
      if (!bone || !animrig::bone_is_visible(&armature, bke::PChanBoneConst{&channel, bone})) continue;
      const Object *custom = !(armature.flag & ARM_NO_CUSTOM) && channel.custom &&
                             channel.custom->type != OB_ARMATURE ? channel.custom : nullptr;
      const bPoseChannel &transform_channel = custom && channel.custom_tx ? *channel.custom_tx : channel;
      std::optional<Bounds<float3>> custom_bounds;
      if (custom) {
        const Status status = bounds(*custom, BoundsMode::DATA, custom_bounds);
        if (status != Status::OK) return status;
      }
      if (custom_bounds) {
        float4x4 mat, scale, rotation, transform;
        const bke::PChanBoneConst pair{&channel, bone};
        scale_m4_fl(scale.ptr(), PCHAN_CUSTOM_BONE_LENGTH(pair));
        rescale_m4(scale.ptr(), channel.custom_scale_xyz);
        eulO_to_mat4(rotation.ptr(), channel.custom_rotation_euler, ROT_MODE_XYZ);
        copy_m4_m4(transform.ptr(), transform_channel.pose_mat);
        translate_m4(transform.ptr(), channel.custom_translation[0], channel.custom_translation[1], channel.custom_translation[2]);
        mul_m4_series(mat.ptr(), transform.ptr(), rotation.ptr(), scale.ptr());
        const Bounds transformed = bounds::transform_bounds(mat, *custom_bounds);
        min = math::min(min, transformed.min);
        max = math::max(max, transformed.max);
      }
      else {
        minmax_v3v3_v3(min, max, transform_channel.pose_head);
        minmax_v3v3_v3(min, max, transform_channel.pose_tail);
      }
      found = true;
    }
    if (found) result = Bounds<float3>{min, max};
    return Status::OK;
  }

  const std::vector<int> &keys(const bke::greasepencil::Layer &layer)
  {
    auto [found, inserted] = frame_keys.try_emplace(&layer);
    if (inserted) {
      if (layer.runtime->sorted_keys_cache_.is_cached()) {
        const Span<int> cached = layer.runtime->sorted_keys_cache_.data();
        found->second.assign(cached.begin(), cached.end());
        return found->second;
      }
      found->second.reserve(layer.frames().size());
      for (const auto &[number, frame] : layer.frames().items()) {
        UNUSED_VARS(frame);
        found->second.push_back(number);
      }
      std::sort(found->second.begin(), found->second.end());
    }
    return found->second;
  }

  MeshView *mesh(const Mesh &source)
  {
    auto found = meshes.find(&source);
    if (found != meshes.end()) {
      return found->second.get();
    }
    if (!mesh_valid(source)) {
      return nullptr;
    }
    auto view = std::make_unique<MeshView>(source);
    MeshView *result = view.get();
    meshes.emplace(&source, std::move(view));
    return result;
  }

  CurvesView *curve(const blender::CurvesGeometry &source)
  {
    auto found = curves.find(&source);
    if (found != curves.end()) {
      return found->second.get();
    }
    if (!curves_valid(source)) {
      return nullptr;
    }
    auto view = std::make_unique<CurvesView>(source);
    CurvesView *result = view.get();
    curves.emplace(&source, std::move(view));
    return result;
  }

  Hierarchy *hierarchy(const GreasePencil &source)
  {
    auto found = hierarchies.find(&source);
    if (found != hierarchies.end()) {
      return found->second.get();
    }
    if (!source.root_group_ptr) {
      return nullptr;
    }
    auto result = std::make_unique<Hierarchy>();
    result->append(source.root_group(), true);
    Hierarchy *pointer = result.get();
    hierarchies.emplace(&source, std::move(result));
    return pointer;
  }
};

Scope::Scope() : impl_(std::make_unique<Impl>()) {}
Scope::~Scope() = default;

Status object_bounds(Scope &scope, const Object &object, BoundsMode mode,
                     std::optional<Bounds<float3>> &result)
{
  result.reset();
  if (!BLI_thread_is_main() || object.id.orig_id ||
      (object.id.tag & (ID_TAG_COPIED_ON_EVAL | ID_TAG_COPIED_ON_EVAL_FINAL_RESULT)))
  {
    return Status::INVALID_STORAGE;
  }
  return scope.impl_->bounds(object, mode, result);
}

Status lock_mesh_for_read(Scope & /*scope*/, const Mesh &source)
{
  /* Reads run synchronously on the main thread and never evaluate, so no wrapper conversion can
   * run concurrently on these original or already evaluated meshes. The evaluation mutex is not
   * held: built-in getters that may run later in the same read take it themselves. */
  if (!BLI_thread_is_main() || !source.runtime) return Status::INVALID_STORAGE;
  return Status::OK;
}

Status mesh_subdivision_for_read(Scope &scope, const Mesh &source, const Mesh *&result)
{
  result = nullptr;
  const Status locked = lock_mesh_for_read(scope, source);
  if (locked != Status::OK) return locked;
  result = &source;
  const SubsurfRuntimeData *settings = source.runtime->subsurf_runtime_data;
  if (!settings) return Status::OK;
  if (source.runtime->wrapper_type == ME_WRAPPER_TYPE_SUBD) {
    if (source.runtime->mesh_eval) result = source.runtime->mesh_eval;
    return Status::OK;
  }
  if (settings->settings.level == 0 || settings->resolution < 3) return Status::OK;
  /* Blender would compute the subdivided mesh here and keep it on the artist's mesh. The read
   * never computes it: the value is reported as not evaluated. */
  result = nullptr;
  return Status::NOT_EVALUATED;
}

Status object_evaluated_mesh_for_read(Scope &scope, const Object &object, const Mesh *&result)
{
  result = nullptr;
  if (!BLI_thread_is_main() || !object.runtime) return Status::INVALID_STORAGE;
  const Mesh *mesh = BKE_object_get_evaluated_mesh_no_subsurf(&object);
  return mesh ? mesh_subdivision_for_read(scope, *mesh, result) : Status::OK;
}

Family scalar_family(const StructRNA &owner_type, const PropertyRNA &property)
{
  if (!declaration(property)) {
    return NONE;
  }
  const ScalarBinding *binding = scalar_binding(owner_type, property);
  if (!binding || !no_selected_callback(property, Role::GET_EX) ||
      !no_selected_callback(property, Role::GET_ARRAY_EX) ||
      !no_selected_callback(property, Role::GET_TRANSFORM) ||
      !no_selected_callback(property, Role::GET_ARRAY_TRANSFORM))
  {
    return NONE;
  }
  if (binding->role == Role::GET_ARRAY) {
    const unsigned int expected_length = binding->family == MESH_TRI_SPLIT_NORMALS ? 9 :
                                         ELEM(binding->family, GP_LAYER_MATRIX_LOCAL,
                                              GP_LAYER_MATRIX_PARENT) ? 16 : 3;
    if (!property.arraydimension ||
        (binding->family != MESH_FACE_VERTICES && property.totarraylength != expected_length))
    {
      return NONE;
    }
  }
  else if (property.arraydimension || property.totarraylength) {
    return NONE;
  }
  if (binding->family == MESH_FACE_VERTICES) {
    if (!source_is(property, Role::ARRAY_LENGTH, "rna_mesh.cc", "rna_MeshPoly_vertices_get_length")) {
      return NONE;
    }
  }
  else if (!absent(property, Role::ARRAY_LENGTH)) {
    return NONE;
  }
  return binding->family;
}

namespace {

template<typename T>
std::optional<T> layer_attribute(const GreasePencil &grease_pencil,
                                 const Hierarchy &hierarchy,
                                 const char *name,
                                 const int64_t index,
                                 const T &default_value)
{
  if (index < 0 || index >= int64_t(hierarchy.layers.size())) {
    return std::nullopt;
  }
  const bke::Attribute *attribute = grease_pencil.attribute_storage.wrap().lookup(name);
  if (!attribute) {
    return default_value;
  }
  if (!attribute_valid(*attribute, hierarchy.layers.size())) {
    return std::nullopt;
  }
  /* The canonical GP accessor supplies Layer domain independently of attr.domain. It reads
   * storage directly, then performs the fixed native type conversion. No GP domain_size call. */
  const CPPType &type = bke::attribute_type_to_cpp_type(attribute->data_type());
  GVArray values;
  if (const auto *array = std::get_if<bke::Attribute::ArrayData>(&attribute->data())) {
    values = GVArray::from_span(GSpan(type, array->data, array->size));
  }
  else {
    const auto &single = std::get<bke::Attribute::SingleData>(attribute->data());
    values = GVArray::from_single_ref(type, hierarchy.layers.size(), single.value);
  }
  values = bke::get_implicit_type_conversions().try_convert(std::move(values), CPPType::get<T>());
  if (!values) {
    return default_value;
  }
  return values.typed<T>()[index];
}

const Mesh *admitted_texture_mesh(const Mesh &source)
{
  if (!source.texcomesh) {
    return &source;
  }
  if (!G_MAIN) {
    return nullptr;
  }
  /* Membership before dereference; evaluated texture geometry is outside existing policy. */
  for (const Mesh &candidate : G_MAIN->meshes) {
    if (&candidate == source.texcomesh) {
      return !candidate.id.orig_id &&
                     !(candidate.id.tag & (ID_TAG_COPIED_ON_EVAL |
                                           ID_TAG_COPIED_ON_EVAL_FINAL_RESULT)) ?
                 &candidate : nullptr;
    }
  }
  return nullptr;
}

int64_t mesh_element_index(const PointerRNA &owner, MeshView &view, const Item *selected)
{
  const Mesh &mesh = view.mesh;
  int64_t index = -1;
  Family expected = NONE;
  if (owner.type == RNA_MeshVertex) {
    index = pointer_index(owner.data, mesh.vert_positions());
    expected = MESH_VERTICES;
  }
  else if (owner.type == RNA_MeshEdge) {
    index = pointer_index(owner.data, mesh.edges());
    expected = MESH_EDGES;
  }
  else if (owner.type == RNA_MeshLoop) {
    index = pointer_index(owner.data, mesh.corner_verts());
    expected = MESH_LOOPS;
  }
  else if (owner.type == RNA_MeshPolygon) {
    index = pointer_index(owner.data, mesh.face_offsets());
    if (index >= mesh.faces_num) {
      index = -1;
    }
    expected = MESH_FACES;
  }
  else if (owner.type == RNA_MeshLoopTriangle) {
    index = pointer_index(owner.data, mesh.corner_tris()); /* Private runtime only. */
    if (index < 0) {
      const Mesh &source = *reinterpret_cast<const Mesh *>(owner.owner_id);
      if (source.runtime->corner_tris_cache.data.is_cached()) {
        index = pointer_index(owner.data, source.runtime->corner_tris_cache.data.data().as_span());
      }
    }
    expected = MESH_TRIANGLES;
  }
  if (selected && expected != NONE &&
      (selected->family != expected || selected->index != index))
  {
    return -1;
  }
  return index;
}

std::optional<bool> uv_pin(const PointerRNA &owner,
                         const Mesh &source,
                         Mesh &scratch,
                         const Item *selected)
{
  const bke::Attribute *attribute = nullptr;
  int64_t index = -1;
  const uintptr_t pointer = uintptr_t(owner.data);
  if (selected && selected->attribute_source) {
    /* SINGLE would be expanded into a new unique array by the ordinary legacy item route.
     * Resolve its admitted logical provenance before comparing its scalar address with arrays. */
    for (const bke::Attribute &candidate : source.attribute_storage.wrap()) {
      if (&candidate == selected->attribute_source && candidate.domain() == AttrDomain::Corner &&
          candidate.data_type() == bke::AttrType::Float2)
      {
        if (const auto *single = std::get_if<bke::Attribute::SingleData>(&candidate.data())) {
          if (single->value == owner.data) {
            attribute = &candidate;
            index = selected->index;
          }
        }
        break;
      }
    }
  }
  /* Match ordinary first-containing-array reverse lookup, including shared arrays. */
  for (const bke::Attribute &candidate : source.attribute_storage.wrap()) {
    if (attribute) break;
    if (candidate.domain() != AttrDomain::Corner || candidate.data_type() != bke::AttrType::Float2) {
      continue;
    }
    if (const auto *array = std::get_if<bke::Attribute::ArrayData>(&candidate.data())) {
      if (array->data && pointer >= uintptr_t(array->data)) {
        const uint64_t offset = (pointer - uintptr_t(array->data)) / sizeof(float2);
        if (offset < uint64_t(array->size)) {
          attribute = &candidate;
          index = offset;
          break;
        }
      }
    }
  }
  if (!attribute) {
    return false;
  }
  if (index < 0 || index >= source.corners_num ||
      attribute->name().size() >= MAX_CUSTOMDATA_LAYER_NAME - 4)
  {
    return std::nullopt;
  }
  char buffer[MAX_CUSTOMDATA_LAYER_NAME];
  const StringRef name = BKE_uv_map_pin_name_get(attribute->name(), buffer);
  /* Crucially, use scratch's canonical lookup: vertex groups precede storage, domain adaptation
   * precedes fixed type conversion, and any connectivity/loose cache belongs to scratch. */
  const VArray<bool> pins = *scratch.attributes().lookup_or_default<bool>(
      name, AttrDomain::Corner, false);
  return pins[index];
}

std::optional<Span<float3>> corner_normals(Mesh &scratch)
{
  const bke::GAttributeReader custom = scratch.attributes().lookup("custom_normal");
  /* Canonical corner_normals leaves an uninitialized variant for a float3 custom normal on a
   * non-Corner domain when sharpness selects Corner. There is no valid native result to copy. */
  if (!scratch.runtime->corner_normals_cache.is_cached() && scratch.faces_num &&
      scratch.normals_domain() == bke::MeshNormalDomain::Corner &&
      custom && custom.varray.type().is<float3>() && custom.domain != AttrDomain::Corner)
  {
    return std::nullopt;
  }
  const Span<float3> result = scratch.corner_normals();
  if (result.size() != (scratch.faces_num ? scratch.corners_num : 0)) {
    return std::nullopt;
  }
  return result;
}

template<typename T>
std::optional<Span<T>> stored_mesh_span(const Mesh &mesh,
                                       const char *name,
                                       const AttrDomain domain)
{
  const int count = domain_size(mesh, domain);
  if (!mesh.attribute_storage.runtime || count < 0) return std::nullopt;
  const bke::Attribute *attribute = mesh.attribute_storage.wrap().lookup(name);
  if (!attribute) return count == 0 ? std::optional<Span<T>>(Span<T>()) : std::nullopt;
  if (attribute->domain() != domain ||
      attribute->data_type() != bke::cpp_type_to_attribute_type(CPPType::get<T>()) ||
      !attribute_valid(*attribute, count)) return std::nullopt;
  const auto *array = std::get_if<bke::Attribute::ArrayData>(&attribute->data());
  if (!array) return std::nullopt;
  return Span<T>(static_cast<const T *>(array->data), count);
}

std::optional<Span<float3>> existing_normals(const Mesh &mesh, const Family collection)
{
  const auto &cache = collection == MESH_VERTEX_NORMALS ? mesh.runtime->vert_normals_cache :
                      collection == MESH_FACE_NORMALS ? mesh.runtime->face_normals_cache :
                                                       mesh.runtime->corner_normals_cache;
  if (!cache.is_cached()) return std::nullopt;
  const bke::NormalsCache &data = cache.data();
  const int count = collection == MESH_VERTEX_NORMALS ? mesh.verts_num :
                    collection == MESH_FACE_NORMALS ? mesh.faces_num : mesh.corners_num;
  if (std::holds_alternative<bke::NormalsCache::UseTrueCache>(data.data)) {
    if (collection == MESH_CORNER_NORMALS) return std::nullopt;
    const auto &true_cache = collection == MESH_VERTEX_NORMALS ? mesh.runtime->vert_normals_true_cache :
                                                               mesh.runtime->face_normals_true_cache;
    if (!true_cache.is_cached() || true_cache.data().size() != count) return std::nullopt;
    return true_cache.data().as_span();
  }
  const Span<float3> values = data.get_span();
  if (values.size() != count || (count && !values.data())) return std::nullopt;
  return values;
}

/* Common stored and warm-cache observations retain their O(1) storage paths. A coordinate read
 * must not copy every warm normal/connectivity cache or scan the whole mesh. The private view is
 * reserved for selected computations whose canonical source path could write a live cache. */
std::optional<Value> stored_mesh_value(const Mesh &mesh,
                                       const PointerRNA &owner,
                                       const Family family,
                                       const Item *selected)
{
  if (!mesh.runtime || !mesh.attribute_storage.runtime) return invalid();
  int64_t index = -1;
  int64_t flag_element = -1;
  Family expected = NONE;
  if (owner.type == RNA_MeshVertex) {
    const auto positions = stored_mesh_span<float3>(mesh, "position", AttrDomain::Point);
    if (!positions) return invalid();
    index = pointer_index(owner.data, *positions);
    expected = MESH_VERTICES;
    if (index < 0) return invalid();
    if (selected && (selected->family != expected || selected->index != index)) return invalid();
    if (family == MESH_VERTEX_CO) return owned_vector((*positions)[index]);
    if (family == MESH_VERTEX_INDEX) return owned_scalar(int(index));
    if (family == MESH_VERTEX_NORMAL) {
      if (const auto normals = existing_normals(mesh, MESH_VERTEX_NORMALS)) {
        return owned_vector((*normals)[index]);
      }
    }
  }
  else if (owner.type == RNA_MeshEdge) {
    const auto edges = stored_mesh_span<int2>(mesh, ".edge_verts", AttrDomain::Edge);
    if (!edges) return invalid();
    index = pointer_index(owner.data, *edges);
    expected = MESH_EDGES;
    if (index < 0) return invalid();
    if (selected && (selected->family != expected || selected->index != index)) return invalid();
    if (family == MESH_EDGE_INDEX) return owned_scalar(int(index));
    if (family == MESH_EDGE_LOOSE && mesh.runtime->loose_edges_cache.is_cached()) {
      return owned_scalar(mesh.runtime->loose_edges_cache.data().mask.contains(index));
    }
  }
  else if (owner.type == RNA_MeshLoop) {
    const auto corners = stored_mesh_span<int>(mesh, ".corner_vert", AttrDomain::Corner);
    if (!corners) return invalid();
    index = pointer_index(owner.data, *corners);
    expected = MESH_LOOPS;
    if (index < 0) return invalid();
    if (selected && (selected->family != expected || selected->index != index)) return invalid();
    if (family == MESH_LOOP_INDEX) return owned_scalar(int(index));
    if (family == MESH_LOOP_VERTEX) return owned_scalar((*corners)[index]);
    if (family == MESH_LOOP_EDGE) {
      const auto edges = stored_mesh_span<int>(mesh, ".corner_edge", AttrDomain::Corner);
      return edges ? owned_scalar((*edges)[index]) : invalid();
    }
    if (family == MESH_LOOP_NORMAL) {
      if (const auto normals = existing_normals(mesh, MESH_CORNER_NORMALS)) {
        return owned_vector((*normals)[index]);
      }
    }
    if (family == MESH_LOOP_TANGENT || family == MESH_LOOP_BITANGENT_SIGN ||
        family == MESH_LOOP_BITANGENT)
    {
      const float4 *tangents = static_cast<const float4 *>(CustomData_get_layer(&mesh.corner_data, CD_MLOOPTANGENT));
      if (family == MESH_LOOP_BITANGENT_SIGN) return owned_scalar(tangents ? tangents[index][3] : 0.0f);
      if (!tangents) return owned_vector(float3(0.0f));
      if (family == MESH_LOOP_TANGENT) return owned_array<float>({tangents[index], 3});
      if (const auto normals = existing_normals(mesh, MESH_CORNER_NORMALS)) {
        float3 output;
        cross_v3_v3v3(output, (*normals)[index], tangents[index]);
        mul_v3_fl(output, tangents[index][3]);
        return owned_vector(output);
      }
    }
  }
  else if (owner.type == RNA_MeshPolygon) {
    if (mesh.faces_num < 0 || (mesh.faces_num && !mesh.face_offset_indices)) return invalid();
    index = pointer_index(owner.data, mesh.face_offsets());
    expected = MESH_FACES;
    if (index < 0 || index >= mesh.faces_num) return invalid();
    if (selected && (selected->family != expected || selected->index != index)) return invalid();
    if (family == MESH_FACE_INDEX) return owned_scalar(int(index));
    const int first = mesh.face_offset_indices[index];
    const int last = mesh.face_offset_indices[index + 1];
    if (first < 0 || last < first || last > mesh.corners_num) return invalid();
    if (family == MESH_FACE_LOOP_TOTAL) return owned_scalar(last - first);
    if (family == MESH_FACE_VERTICES) {
      const auto corners = stored_mesh_span<int>(mesh, ".corner_vert", AttrDomain::Corner);
      return corners ? owned_array<int>(corners->slice(first, last - first)) : invalid();
    }
  }
  else if (owner.type == RNA_MeshLoopTriangle && mesh.runtime->corner_tris_cache.data.is_cached()) {
    const Span<int3> triangles = mesh.runtime->corner_tris_cache.data.data();
    index = pointer_index(owner.data, triangles);
    if (index < 0) return std::nullopt; /* May be an operation-owned cold triangulation item. */
    expected = MESH_TRIANGLES;
    if (selected && (selected->family != expected || selected->index != index)) return invalid();
    if (family == MESH_TRI_INDEX) return owned_scalar(int(index));
    const int3 triangle = triangles[index];
    for (const int corner : Span<int>(triangle, 3)) {
      if (corner < 0 || corner >= mesh.corners_num) return invalid();
    }
    if (family == MESH_TRI_SPLIT_NORMALS) {
      if (const auto normals = existing_normals(mesh, MESH_CORNER_NORMALS)) {
        std::array<float, 9> output;
        for (int corner = 0; corner < 3; corner++) {
          copy_v3_v3(output.data() + corner * 3, (*normals)[triangle[corner]]);
        }
        return owned_array<float>(output);
      }
    }
    if (ELEM(family, MESH_TRI_VERTICES, MESH_TRI_NORMAL, MESH_TRI_AREA)) {
      const auto corners = stored_mesh_span<int>(mesh, ".corner_vert", AttrDomain::Corner);
      if (!corners) return invalid();
      const int3 vertices((*corners)[triangle[0]], (*corners)[triangle[1]], (*corners)[triangle[2]]);
      if (family == MESH_TRI_VERTICES) return owned_array<int>({vertices, 3});
      const auto positions = stored_mesh_span<float3>(mesh, "position", AttrDomain::Point);
      if (!positions) return invalid();
      for (const int vertex : Span<int>(vertices, 3)) {
        if (vertex < 0 || vertex >= positions->size()) return invalid();
      }
      if (family == MESH_TRI_AREA) {
        return owned_scalar(area_tri_v3((*positions)[vertices[0]], (*positions)[vertices[1]], (*positions)[vertices[2]]));
      }
      float3 output;
      normal_tri_v3(output, (*positions)[vertices[0]], (*positions)[vertices[1]], (*positions)[vertices[2]]);
      return owned_vector(output);
    }
    if (ELEM(family, MESH_TRI_FACE, MESH_TRI_MATERIAL, MESH_TRI_SMOOTH) &&
        mesh.runtime->corner_tri_faces_cache.is_cached())
    {
      const Span<int> faces = mesh.runtime->corner_tri_faces_cache.data();
      if (index >= faces.size()) return invalid();
      flag_element = faces[index];
      if (flag_element < 0 || flag_element >= mesh.faces_num) return invalid();
      if (family == MESH_TRI_FACE) return owned_scalar(int(flag_element));
    }
  }
  if (selected && expected != NONE && (selected->family != expected || selected->index != index)) {
    return invalid();
  }
  const char *name = nullptr;
  AttrDomain domain = AttrDomain::Auto;
  bool invert = false;
  switch (family) {
    case MESH_VERTEX_SELECT: name = ".select_vert"; domain = AttrDomain::Point; break;
    case MESH_VERTEX_HIDE: name = ".hide_vert"; domain = AttrDomain::Point; break;
    case MESH_EDGE_SELECT: name = ".select_edge"; domain = AttrDomain::Edge; break;
    case MESH_EDGE_HIDE: name = ".hide_edge"; domain = AttrDomain::Edge; break;
    case MESH_EDGE_SEAM: name = "uv_seam"; domain = AttrDomain::Edge; break;
    case MESH_EDGE_SHARP: name = "sharp_edge"; domain = AttrDomain::Edge; break;
    case MESH_FACE_SELECT: name = ".select_poly"; domain = AttrDomain::Face; break;
    case MESH_FACE_HIDE: name = ".hide_poly"; domain = AttrDomain::Face; break;
    case MESH_FACE_SMOOTH: name = "sharp_face"; domain = AttrDomain::Face; invert = true; break;
    case MESH_FACE_MATERIAL: name = "material_index"; domain = AttrDomain::Face; break;
    case MESH_TRI_SMOOTH:
      if (flag_element >= 0) { name = "sharp_face"; domain = AttrDomain::Face; invert = true; }
      break;
    case MESH_TRI_MATERIAL:
      if (flag_element >= 0) { name = "material_index"; domain = AttrDomain::Face; }
      break;
    default: break;
  }
  if (name && index >= 0) {
    const int64_t element = flag_element >= 0 ? flag_element : index;
    if (const bke::Attribute *stored = mesh.attribute_storage.wrap().lookup(name)) {
      if (!attribute_valid(*stored, domain_size(mesh, stored->domain()))) return invalid();
    }
    const bke::AttributeAccessor attributes = mesh.attributes();
    const bke::GAttributeReader actual = attributes.lookup(name);
    if (!actual || actual.domain == domain) {
      if (actual && actual.varray.size() != domain_size(mesh, domain)) return invalid();
      if (family == MESH_FACE_MATERIAL || family == MESH_TRI_MATERIAL) {
        return owned_scalar((*attributes.lookup_or_default<int>(name, domain, 0))[element]);
      }
      const bool flag = (*attributes.lookup_or_default<bool>(name, domain, false))[element];
      return owned_scalar(invert ? !flag : flag);
    }
  }
  return std::nullopt;
}

}  // namespace

/* Same arithmetic as BKE_curve_texspace_calc, applied to the operation-private Curve copy. */
static void curve_texspace_from_bounds(Curve &curve, std::optional<Bounds<float3>> bounds)
{
  if (!bounds) {
    bounds = Bounds<float3>{float3(-FLT_MAX), -float3(FLT_MAX)};
  }
  float texspace_location[3], texspace_size[3];
  mid_v3_v3v3(texspace_location, bounds->min, bounds->max);
  for (int a = 0; a < 3; a++) {
    texspace_size[a] = (bounds->max[a] - bounds->min[a]) / 2.0f;
    if (texspace_size[a] == 0.0f) {
      texspace_size[a] = 1.0f;
    }
    else if (texspace_size[a] > 0.0f && texspace_size[a] < 0.00001f) {
      texspace_size[a] = 0.00001f;
    }
    else if (texspace_size[a] < 0.0f && texspace_size[a] > -0.00001f) {
      texspace_size[a] = -0.00001f;
    }
  }
  copy_v3_v3(curve.texspace_location, texspace_location);
  copy_v3_v3(curve.texspace_size, texspace_size);
  curve.texspace_flag |= CU_TEXSPACE_FLAG_AUTO_EVALUATED;
}

Status mesh_vertex_normals_for_read(Scope &scope, const Mesh &source, Span<float3> &result)
{
  result = {};
  if (!BLI_thread_is_main() || !source.runtime || source.verts_num < 0) {
    return Status::INVALID_STORAGE;
  }
  if (!source.verts_num) return Status::OK;
  if (const std::optional existing = existing_normals(source, MESH_VERTEX_NORMALS)) {
    result = *existing;
    return Status::OK;
  }
  MeshView *view = scope.impl_->mesh(source);
  if (!view) return Status::INVALID_STORAGE;
  result = view->mesh.vert_normals(); /* Only the Scope's private runtime can be ensured. */
  if (result.size() != source.verts_num || !result.data()) {
    result = {};
    return Status::INVALID_STORAGE;
  }
  return Status::OK;
}

Value value(Scope &scope,
            const Family family,
            const PointerRNA &owner,
            const PropertyRNA &property,
            const Item *selected)
{
  if (!BLI_thread_is_main() || !owner.type || !owner.data || !owner.owner_id || owner.owner_id->orig_id ||
      (owner.owner_id->tag & (ID_TAG_COPIED_ON_EVAL | ID_TAG_COPIED_ON_EVAL_FINAL_RESULT)) || family == NONE ||
      scalar_family(*owner.type, property) != family)
  {
    return {};
  }
  if (selected && (selected->pointer.owner_id != owner.owner_id ||
                   selected->pointer.type != owner.type || selected->pointer.data != owner.data))
  {
    return invalid();
  }
  if (family >= MESH_VERTEX_CO && family <= MESH_UV_PIN) {
    if (GS(owner.owner_id->name) != ID_ME) {
      return invalid();
    }
    const Mesh &source = *reinterpret_cast<const Mesh *>(owner.owner_id);
    if (owner.type == RNA_Mesh && owner.data != owner.owner_id) return invalid();
    if (const std::optional<Value> stored = stored_mesh_value(source, owner, family, selected)) {
      return *stored;
    }
    if ((family == MESH_TEXSPACE_LOCATION || family == MESH_TEXSPACE_SIZE) &&
        (!(source.texspace_flag & ME_TEXSPACE_FLAG_AUTO) ||
         (source.texspace_flag & ME_TEXSPACE_FLAG_AUTO_EVALUATED)))
    {
      return owned_array<float>({family == MESH_TEXSPACE_LOCATION ? source.texspace_location :
                                                                      source.texspace_size,
                                 3});
    }
    MeshView *view = scope.impl_->mesh(source);
    if (!view) {
      return invalid();
    }
    Mesh &mesh = view->mesh;
    if (family == MESH_TEXSPACE_LOCATION || family == MESH_TEXSPACE_SIZE) {
      if (mesh.runtime->wrapper_type == ME_WRAPPER_TYPE_BMESH &&
          (!mesh.runtime->edit_mesh || !mesh.runtime->edit_data))
      {
        return invalid();
      }
      BKE_mesh_texspace_ensure(&mesh); /* Writes only private Mesh fields/runtime. */
      return owned_array<float>({family == MESH_TEXSPACE_LOCATION ? mesh.texspace_location :
                                                                      mesh.texspace_size,
                                 3});
    }
    if (family == MESH_NORMALS_DOMAIN) {
      return owned_scalar(int(mesh.normals_domain()));
    }
    if (family == MESH_UV_PIN) {
      const std::optional<bool> pin = uv_pin(owner, source, mesh, selected);
      return pin ? owned_scalar(*pin) : invalid();
    }
    const int64_t index = mesh_element_index(owner, *view, selected);
    if (index < 0 || index > INT_MAX) {
      return invalid();
    }
    const bke::AttributeAccessor attributes = mesh.attributes();
    const auto flag = [&](const char *name, const AttrDomain domain, const int64_t element,
                          const bool invert = false) {
      const VArray<bool> values = *attributes.lookup_or_default<bool>(name, domain, false);
      return owned_scalar(invert ? !values[element] : values[element]);
    };
    switch (family) {
      case MESH_VERTEX_INDEX:
      case MESH_EDGE_INDEX:
      case MESH_LOOP_INDEX:
      case MESH_FACE_INDEX:
      case MESH_TRI_INDEX:
        return owned_scalar(int(index));
      case MESH_VERTEX_CO: return owned_vector(mesh.vert_positions()[index]);
      case MESH_VERTEX_NORMAL: {
        const Span<float3> normals = mesh.vert_normals();
        return index < normals.size() ? owned_vector(normals[index]) : invalid();
      }
      case MESH_VERTEX_SELECT: return flag(".select_vert", AttrDomain::Point, index);
      case MESH_VERTEX_HIDE: return flag(".hide_vert", AttrDomain::Point, index);
      case MESH_EDGE_SELECT: return flag(".select_edge", AttrDomain::Edge, index);
      case MESH_EDGE_HIDE: return flag(".hide_edge", AttrDomain::Edge, index);
      case MESH_EDGE_SEAM: return flag("uv_seam", AttrDomain::Edge, index);
      case MESH_EDGE_SHARP: return flag("sharp_edge", AttrDomain::Edge, index);
      case MESH_EDGE_LOOSE: return owned_scalar(mesh.loose_edges().contains(index));
      case MESH_LOOP_VERTEX: return owned_scalar(mesh.corner_verts()[index]);
      case MESH_LOOP_EDGE: return owned_scalar(mesh.corner_edges()[index]);
      case MESH_LOOP_NORMAL: {
        const auto normals = corner_normals(mesh);
        return normals && index < normals->size() ? owned_vector((*normals)[index]) : invalid();
      }
      case MESH_FACE_SELECT: return flag(".select_poly", AttrDomain::Face, index);
      case MESH_FACE_HIDE: return flag(".hide_poly", AttrDomain::Face, index);
      case MESH_FACE_SMOOTH: return flag("sharp_face", AttrDomain::Face, index, true);
      case MESH_FACE_MATERIAL: {
        const VArray<int> values = *attributes.lookup_or_default<int>("material_index", AttrDomain::Face, 0);
        return owned_scalar(values[index]);
      }
      case MESH_FACE_VERTICES:
      case MESH_FACE_LOOP_TOTAL:
      case MESH_FACE_NORMAL:
      case MESH_FACE_CENTER:
      case MESH_FACE_AREA: {
        const Span<int> vertices = mesh.corner_verts().slice(mesh.faces()[index]);
        if (family == MESH_FACE_VERTICES) return owned_array<int>(vertices);
        if (family == MESH_FACE_LOOP_TOTAL) return owned_scalar(int(vertices.size()));
        if (family == MESH_FACE_NORMAL) return owned_vector(bke::mesh::face_normal_calc(mesh.vert_positions(), vertices));
        if (family == MESH_FACE_CENTER) return owned_vector(bke::mesh::face_center_calc(mesh.vert_positions(), vertices));
        return owned_scalar(bke::mesh::face_area_calc(mesh.vert_positions(), vertices));
      }
      case MESH_LOOP_TANGENT:
      case MESH_LOOP_BITANGENT_SIGN:
      case MESH_LOOP_BITANGENT: {
        const float4 *tangents = static_cast<const float4 *>(CustomData_get_layer(&mesh.corner_data, CD_MLOOPTANGENT));
        if (family == MESH_LOOP_BITANGENT_SIGN) return owned_scalar(tangents ? tangents[index][3] : 0.0f);
        float3 output(0.0f);
        if (tangents) {
          if (family == MESH_LOOP_TANGENT) copy_v3_v3(output, tangents[index]);
          else {
            const auto normals = corner_normals(mesh);
            if (!normals || index >= normals->size()) return invalid();
            cross_v3_v3v3(output, (*normals)[index], tangents[index]);
            mul_v3_fl(output, tangents[index][3]);
          }
        }
        return owned_vector(output);
      }
      case MESH_VERTEX_UNDEFORMED: {
        const float3 *orco = static_cast<const float3 *>(CustomData_get_layer(&mesh.vert_data, CD_ORCO));
        if (!orco) return owned_vector(mesh.vert_positions()[index]);
        const Mesh *texture_source = admitted_texture_mesh(source);
        if (!texture_source) return {};
        MeshView *texture = scope.impl_->mesh(*texture_source);
        if (!texture) return invalid();
        if (texture->runtime.wrapper_type == ME_WRAPPER_TYPE_BMESH &&
            (!texture->runtime.edit_mesh || !texture->runtime.edit_data)) return invalid();
        float3 location, size, output;
        BKE_mesh_texspace_get(&texture->mesh, location, size);
        madd_v3_v3v3v3(output, location, orco[index], size);
        return owned_vector(output);
      }
      case MESH_TRI_FACE:
      case MESH_TRI_VERTICES:
      case MESH_TRI_NORMAL:
      case MESH_TRI_SPLIT_NORMALS:
      case MESH_TRI_AREA:
      case MESH_TRI_MATERIAL:
      case MESH_TRI_SMOOTH: {
        const int3 triangle = mesh.corner_tris()[index];
        for (const int corner : Span<int>(triangle, 3)) {
          if (corner < 0 || corner >= mesh.corners_num) return invalid();
        }
        if (family == MESH_TRI_SPLIT_NORMALS) {
          const auto normals = corner_normals(mesh);
          if (!normals) return invalid();
          std::array<float, 9> output;
          for (int corner = 0; corner < 3; corner++) copy_v3_v3(output.data() + corner * 3, (*normals)[triangle[corner]]);
          return owned_array<float>(output);
        }
        const int3 vertices(mesh.corner_verts()[triangle[0]], mesh.corner_verts()[triangle[1]], mesh.corner_verts()[triangle[2]]);
        if (family == MESH_TRI_VERTICES) return owned_array<int>({vertices, 3});
        if (family == MESH_TRI_NORMAL) {
          float3 output;
          normal_tri_v3(output, mesh.vert_positions()[vertices[0]], mesh.vert_positions()[vertices[1]], mesh.vert_positions()[vertices[2]]);
          return owned_vector(output);
        }
        if (family == MESH_TRI_AREA) return owned_scalar(area_tri_v3(mesh.vert_positions()[vertices[0]], mesh.vert_positions()[vertices[1]], mesh.vert_positions()[vertices[2]]));
        const Span<int> triangle_faces = mesh.corner_tri_faces();
        if (index >= triangle_faces.size()) return invalid();
        const int face = triangle_faces[index];
        if (face < 0 || face >= mesh.faces_num) return invalid();
        if (family == MESH_TRI_FACE) return owned_scalar(face);
        if (family == MESH_TRI_SMOOTH) return flag("sharp_face", AttrDomain::Face, face, true);
        const VArray<int> values = *attributes.lookup_or_default<int>("material_index", AttrDomain::Face, 0);
        return owned_scalar(values[face]);
      }
      default: return {};
    }
  }
  if (family >= CURVE_POINT_LOCATION && family <= CURVE_SLICE_LENGTH) {
    if (GS(owner.owner_id->name) != ID_CV) return invalid();
    const Curves &curves = *reinterpret_cast<const Curves *>(owner.owner_id);
    CurvesView *view = scope.impl_->curve(curves.geometry);
    if (!view) return invalid();
    const bke::CurvesGeometry &geometry = view->geometry.wrap();
    if (owner.type == RNA_CurvePoint) {
      const int64_t index = pointer_index(owner.data, geometry.positions());
      if (index < 0) return invalid();
      if (family == CURVE_POINT_INDEX) return owned_scalar(int(index));
      if (family == CURVE_POINT_LOCATION) return owned_vector(geometry.positions()[index]);
      const VArray<float> radius = *geometry.attributes().lookup_or_default<float>("radius", AttrDomain::Point, 0.0f);
      return owned_scalar(radius[index]);
    }
    const int64_t index = pointer_index(owner.data, Span<int>(geometry.curve_offsets, geometry.curve_num + 1));
    if (index < 0 || index >= geometry.curve_num) return invalid();
    if (family == CURVE_SLICE_INDEX) return owned_scalar(int(index));
    if (family == CURVE_SLICE_FIRST) return owned_scalar(geometry.curve_offsets[index]);
    return owned_scalar(geometry.curve_offsets[index + 1] - geometry.curve_offsets[index]);
  }
  if (family >= POINT_LOCATION && family <= POINT_RADIUS) {
    if (GS(owner.owner_id->name) != ID_PT) return invalid();
    const PointCloud &points = *reinterpret_cast<const PointCloud *>(owner.owner_id);
    if (!points.attribute_storage.runtime || points.totpoint < 0) return invalid();
    const bke::Attribute *positions = points.attribute_storage.wrap().lookup("position");
    if (!positions || positions->domain() != AttrDomain::Point ||
        positions->data_type() != bke::AttrType::Float3 ||
        !std::holds_alternative<bke::Attribute::ArrayData>(positions->data()) ||
        !attribute_valid(*positions, points.totpoint)) return invalid();
    const int64_t index = pointer_index(owner.data, points.positions());
    if (index < 0) return invalid();
    if (family == POINT_INDEX) return owned_scalar(int(index));
    if (family == POINT_LOCATION) return owned_vector(points.positions()[index]);
    const bke::Attribute *radius = points.attribute_storage.wrap().lookup("radius");
    if (radius && radius->domain() == AttrDomain::Point && radius->data_type() == bke::AttrType::Float &&
        !attribute_valid(*radius, points.totpoint)) return invalid();
    return owned_scalar(points.radius()[index]);
  }
  if (family >= GP_FRAME_NUMBER && family <= GP_LAYER_MATRIX_PARENT) {
    if (GS(owner.owner_id->name) != ID_GP) return invalid();
    const GreasePencil &grease_pencil = *reinterpret_cast<const GreasePencil *>(owner.owner_id);
    Hierarchy *hierarchy = scope.impl_->hierarchy(grease_pencil);
    if (!hierarchy || hierarchy->status != Status::OK) return invalid();
    if (family == GP_FRAME_NUMBER) {
      for (const bke::greasepencil::Layer *layer : hierarchy->layers) {
        for (const auto &[number, frame] : layer->frames().items()) {
          if (&frame == owner.data) return owned_scalar(int(number));
        }
      }
      return invalid();
    }
    const auto found = std::find(hierarchy->layers.begin(), hierarchy->layers.end(), owner.data);
    if (found == hierarchy->layers.end()) return invalid();
    const int64_t index = found - hierarchy->layers.begin();
    const bke::greasepencil::Layer &layer = **found;
    if (family == GP_LAYER_MATRIX_LOCAL || family == GP_LAYER_MATRIX_PARENT) {
      const float4x4 matrix = family == GP_LAYER_MATRIX_LOCAL ? layer.local_transform() : layer.parent_inverse();
      return owned_array<float>({matrix.base_ptr(), 16});
    }
    if (!grease_pencil.attribute_storage.runtime) return invalid();
    if (family == GP_LAYER_TINT || family == GP_LAYER_TINT_FACTOR) {
      const auto tint = layer_attribute(grease_pencil, *hierarchy, "tint_color", index, ColorGeometry4f(0.0f));
      if (!tint) return invalid();
      return family == GP_LAYER_TINT_FACTOR ? owned_scalar((*tint)[3]) : owned_array<float>({*tint, 3});
    }
    if (family == GP_LAYER_RADIUS_OFFSET) {
      const auto radius = layer_attribute(grease_pencil, *hierarchy, "radius_offset", index, 0.0f);
      return radius ? owned_scalar(*radius) : invalid();
    }
    const auto pass = layer_attribute(grease_pencil, *hierarchy, "pass_index", index, 0);
    return pass ? owned_scalar(*pass) : invalid();
  }
  if (family == LEGACY_CURVE_TEXSPACE_LOCATION || family == LEGACY_CURVE_TEXSPACE_SIZE) {
    if (GS(owner.owner_id->name) != ID_CU_LEGACY || owner.data != owner.owner_id) return invalid();
    const Curve &source = *static_cast<const Curve *>(owner.data);
    if (!(source.texspace_flag & CU_TEXSPACE_FLAG_AUTO) ||
        (source.texspace_flag & CU_TEXSPACE_FLAG_AUTO_EVALUATED))
    {
      return owned_array<float>({family == LEGACY_CURVE_TEXSPACE_LOCATION ? source.texspace_location :
                                                                            source.texspace_size,
                                 3});
    }
    std::optional<Bounds<float3>> bounds;
    const Status status = scope.impl_->legacy_bounds(source, bounds);
    if (status != Status::OK) return failed(status);
    LegacyCurveView &view = scope.impl_->legacy_curve(source);
    curve_texspace_from_bounds(view.curve, bounds);
    return owned_array<float>({family == LEGACY_CURVE_TEXSPACE_LOCATION ? view.curve.texspace_location :
                                                                        view.curve.texspace_size,
                               3});
  }
  return {};
}

namespace {

struct CollectionBinding {
  Family family;
  const StructRNA *owner;
  const char *property;
  StructRNA *item_type;
  const char *file;
  const char *begin;
  const char *next;
  const char *end;
  const char *get;
  const char *length;
  const char *lookup;
};

bool collection_slot(const PropertyRNA &property,
                     const Role role,
                     const char *file,
                     const char *expected)
{
  if (expected) {
    return source_is(property, role, file, expected);
  }
  if (absent(property, role)) {
    return true;
  }
  /* Exact compiler-owned fallback slot for this already source-bound property, never arbitrary
   * manual native callbacks. These generated callbacks are observed but are not invoked here. */
  const RNAReadCallbackSource source = RNA_property_read_callback_source(&property, role);
  if (source.state != RNAReadCallbackState::MATCHED || !source.source_file ||
      !STREQ(source.source_file, file) || !source.source_identifier)
  {
    return false;
  }
  if (role == Role::COLLECTION_END) {
    return source.origin == RNAReadCallbackOrigin::GENERATED_NOOP &&
           STREQ(source.source_identifier, "COLLECTION_END");
  }
  if (role == Role::COLLECTION_LOOKUP_INT || role == Role::COLLECTION_LOOKUP_STRING) {
    return source.origin == RNAReadCallbackOrigin::GENERATED_LOOKUP &&
           STREQ(source.source_identifier, role == Role::COLLECTION_LOOKUP_INT ?
                                              "COLLECTION_LOOKUP_INT" : "COLLECTION_LOOKUP_STRING");
  }
  return false;
}

Span<CollectionBinding> collection_bindings()
{
#define ARRAY_PROTOCOL "rna_iterator_array_next", "rna_iterator_array_end", "rna_iterator_array_get"
  static const CollectionBinding bindings[] = {
      {MESH_VERTICES, RNA_Mesh, "vertices", RNA_MeshVertex, "rna_mesh.cc", "rna_Mesh_vertices_begin", ARRAY_PROTOCOL, "rna_Mesh_vertices_length", "rna_Mesh_vertices_lookup_int"},
      {MESH_EDGES, RNA_Mesh, "edges", RNA_MeshEdge, "rna_mesh.cc", "rna_Mesh_edges_begin", ARRAY_PROTOCOL, "rna_Mesh_edges_length", "rna_Mesh_edges_lookup_int"},
      {MESH_LOOPS, RNA_Mesh, "loops", RNA_MeshLoop, "rna_mesh.cc", "rna_Mesh_loops_begin", ARRAY_PROTOCOL, "rna_Mesh_loops_length", "rna_Mesh_loops_lookup_int"},
      {MESH_FACES, RNA_Mesh, "polygons", RNA_MeshPolygon, "rna_mesh.cc", "rna_Mesh_polygons_begin", ARRAY_PROTOCOL, "rna_Mesh_polygons_length", "rna_Mesh_polygons_lookup_int"},
      {MESH_TRIANGLES, RNA_Mesh, "loop_triangles", RNA_MeshLoopTriangle, "rna_mesh.cc", "rna_Mesh_loop_triangles_begin", ARRAY_PROTOCOL, "rna_Mesh_loop_triangles_length", "rna_Mesh_loop_triangles_lookup_int"},
      {MESH_TRIANGLE_FACES, RNA_Mesh, "loop_triangle_polygons", RNA_ReadOnlyInteger, "rna_mesh.cc", "rna_Mesh_loop_triangle_polygons_begin", ARRAY_PROTOCOL, "rna_Mesh_loop_triangles_length", "rna_Mesh_loop_triangle_polygons_lookup_int"},
      {MESH_VERTEX_NORMALS, RNA_Mesh, "vertex_normals", RNA_MeshNormalValue, "rna_mesh.cc", "rna_Mesh_vertex_normals_begin", ARRAY_PROTOCOL, "rna_Mesh_vertex_normals_length", "rna_Mesh_vertex_normals_lookup_int"},
      {MESH_FACE_NORMALS, RNA_Mesh, "polygon_normals", RNA_MeshNormalValue, "rna_mesh.cc", "rna_Mesh_poly_normals_begin", ARRAY_PROTOCOL, "rna_Mesh_poly_normals_length", "rna_Mesh_poly_normals_lookup_int"},
      {MESH_CORNER_NORMALS, RNA_Mesh, "corner_normals", RNA_MeshNormalValue, "rna_mesh.cc", "rna_Mesh_corner_normals_begin", ARRAY_PROTOCOL, "rna_Mesh_corner_normals_length", "rna_Mesh_corner_normals_lookup_int"},
      {CURVE_POINTS, RNA_Curves, "points", RNA_CurvePoint, "rna_curves.cc", "rna_Curves_position_data_begin", ARRAY_PROTOCOL, "rna_Curves_position_data_length", "rna_Curves_points_lookup_int"},
      {CURVE_SLICES, RNA_Curves, "curves", RNA_CurveSlice, "rna_curves.cc", "rna_Curves_curves_begin", ARRAY_PROTOCOL, "rna_Curves_curves_length", "rna_Curves_curves_lookup_int"},
      {CURVE_POSITIONS, RNA_Curves, "position_data", RNA_FloatVectorAttributeValue, "rna_curves.cc", "rna_Curves_position_data_begin", ARRAY_PROTOCOL, "rna_Curves_position_data_length", "rna_Curves_position_data_lookup_int"},
      {CURVE_OFFSETS, RNA_Curves, "curve_offset_data", RNA_IntAttributeValue, "rna_curves.cc", "rna_Curves_curve_offset_data_begin", ARRAY_PROTOCOL, "rna_Curves_curve_offset_data_length", "rna_Curves_curve_offset_data_lookup_int"},
      {CURVE_NORMALS, RNA_Curves, "normals", RNA_FloatVectorValueReadOnly, "rna_curves.cc", "rna_Curves_normals_begin", ARRAY_PROTOCOL, "rna_Curves_position_data_length", nullptr},
      {CURVE_SLICE_POINTS, RNA_CurveSlice, "points", RNA_CurvePoint, "rna_curves.cc", "rna_CurveSlice_points_begin", ARRAY_PROTOCOL, "rna_CurveSlice_points_length_get", nullptr},
      {POINTS, RNA_PointCloud, "points", RNA_Point, "rna_pointcloud.cc", "rna_PointCloud_points_begin", ARRAY_PROTOCOL, "rna_PointCloud_points_length", "rna_PointCloud_points_lookup_int"},
      {GP_DRAWING_OFFSETS, RNA_GreasePencilDrawing, "curve_offsets", RNA_IntAttributeValue, "rna_grease_pencil.cc", "rna_GreasePencilDrawing_curve_offset_data_begin", ARRAY_PROTOCOL, "rna_GreasePencilDrawing_curve_offset_data_length", "rna_GreasePencilDrawing_curve_offset_data_lookup_int"},
      {GP_FRAMES, RNA_GreasePencilLayer, "frames", RNA_GreasePencilFrame, "rna_grease_pencil.cc", "rna_GreasePencilLayer_frames_begin", "rna_iterator_array_next", "rna_iterator_array_end", "rna_GreasePencilLayer_frames_get", "rna_GreasePencilLayer_frames_length", "rna_GreasePencilLayer_frames_lookup_int"},
      {GP_LAYERS, RNA_GreasePencil, "layers", RNA_GreasePencilLayer, "rna_grease_pencil.cc", "rna_iterator_grease_pencil_layers_begin", "rna_iterator_grease_pencil_layers_next", nullptr, "rna_iterator_grease_pencil_layers_get", "rna_iterator_grease_pencil_layers_length", nullptr},
      {GP_GROUPS, RNA_GreasePencil, "layer_groups", RNA_GreasePencilLayerGroup, "rna_grease_pencil.cc", "rna_iterator_grease_pencil_layer_groups_begin", "rna_iterator_grease_pencil_layer_groups_next", nullptr, "rna_iterator_grease_pencil_layer_groups_get", "rna_iterator_grease_pencil_layer_groups_length", nullptr},
      {GP_ROOT_NODES, RNA_GreasePencil, "root_nodes", RNA_GreasePencilTreeNode, "rna_grease_pencil.cc", "rna_iterator_grease_pencil_root_nodes_begin", "rna_iterator_grease_pencil_root_nodes_next", nullptr, "rna_iterator_grease_pencil_root_nodes_get", "rna_iterator_grease_pencil_root_nodes_length", nullptr},
      {GP_GROUP_CHILDREN, RNA_GreasePencilLayerGroup, "children", RNA_GreasePencilTreeNode, "rna_grease_pencil.cc", "rna_GreasePencilLayerGroup_children_begin", "rna_GreasePencilLayerGroup_children_next", nullptr, "rna_GreasePencilLayerGroup_children_get", "rna_GreasePencilLayerGroup_children_length", "rna_GreasePencilLayerGroup_children_lookup_int"},
  };
#undef ARRAY_PROTOCOL
  return Span<CollectionBinding>(bindings, ARRAY_SIZE(bindings));
}

const CollectionBinding *collection_binding(const StructRNA &owner, const PropertyRNA &property)
{
  if (property.type != PROP_COLLECTION) {
    return nullptr;
  }
  const auto &collection = static_cast<const CollectionPropertyRNA &>(property);
  for (const CollectionBinding &binding : collection_bindings()) {
    if (&owner == binding.owner && property.identifier &&
        STREQ(property.identifier, binding.property) && collection.item_type == binding.item_type &&
        collection_slot(property, Role::COLLECTION_BEGIN, binding.file, binding.begin) &&
        collection_slot(property, Role::COLLECTION_NEXT, binding.file, binding.next) &&
        collection_slot(property, Role::COLLECTION_END, binding.file, binding.end) &&
        collection_slot(property, Role::COLLECTION_GET, binding.file, binding.get) &&
        collection_slot(property, Role::COLLECTION_LENGTH, binding.file, binding.length) &&
        collection_slot(property, Role::COLLECTION_LOOKUP_INT, binding.file, binding.lookup) &&
        collection_slot(property, Role::COLLECTION_LOOKUP_STRING, binding.file, nullptr))
    {
      return &binding;
    }
  }
  return nullptr;
}

const bke::Attribute *find_attribute(const PointerRNA &owner,
                                    const bke::AttributeStorage &storage)
{
  for (const bke::Attribute &attribute : storage) {
    if (&attribute == owner.data) {
      return &attribute;
    }
  }
  return nullptr;
}

bool original_owner(const PointerRNA &owner)
{
  return owner.owner_id && !owner.owner_id->orig_id &&
         !(owner.owner_id->tag & (ID_TAG_COPIED_ON_EVAL | ID_TAG_COPIED_ON_EVAL_FINAL_RESULT));
}

}  // namespace

bool catalogued(const PropertyRNA &property)
{
  if (!declaration(property)) {
    return false;
  }
  for (const ScalarBinding &binding : scalar_bindings()) {
    if (source_is(property, binding.role, binding.file, binding.symbol)) {
      return true;
    }
  }
  if (property.type == PROP_COLLECTION) {
    for (const CollectionBinding &binding : collection_bindings()) {
      for (const auto &[role, symbol] : {std::pair{Role::COLLECTION_BEGIN, binding.begin},
                                         std::pair{Role::COLLECTION_GET, binding.get},
                                         std::pair{Role::COLLECTION_LENGTH, binding.length},
                                         std::pair{Role::COLLECTION_LOOKUP_INT, binding.lookup}})
      {
        /* The shared array iterator protocol is not itself catalogued. */
        if (symbol && !STREQ(symbol, "rna_iterator_array_get") &&
            source_is(property, role, binding.file, symbol))
        {
          return true;
        }
      }
    }
  }
  return false;
}

Family collection_family(const StructRNA &owner, const PropertyRNA &property)
{
  if (!declaration(property)) {
    return NONE;
  }
  if (property.type == PROP_COLLECTION && property.identifier && STREQ(property.identifier, "data") &&
      RNA_struct_is_a(&owner, RNA_Attribute) &&
      source_is(property, Role::COLLECTION_LENGTH, "rna_attribute.cc", "rna_Attribute_data_length"))
  {
    return ATTRIBUTE_DATA_LENGTH; /* Items retain the existing explicit policy exclusion. */
  }
  const CollectionBinding *binding = collection_binding(owner, property);
  return binding ? binding->family : NONE;
}

Status length(Scope &scope,
              const Family family,
              const PointerRNA &owner,
              const PropertyRNA &property,
              int64_t &result)
{
  result = 0;
  if (!BLI_thread_is_main() || !owner.type || !owner.data || !original_owner(owner) || family == NONE ||
      collection_family(*owner.type, property) != family)
  {
    return Status::UNSUPPORTED;
  }
  if (family >= MESH_VERTICES && family <= MESH_CORNER_NORMALS) {
    if (GS(owner.owner_id->name) != ID_ME || owner.data != owner.owner_id) return Status::INVALID_STORAGE;
    const Mesh &mesh = *static_cast<const Mesh *>(owner.data);
    switch (family) {
      case MESH_VERTICES:
      case MESH_VERTEX_NORMALS: result = mesh.verts_num; break;
      case MESH_EDGES: result = mesh.edges_num; break;
      case MESH_LOOPS:
      case MESH_CORNER_NORMALS: result = mesh.corners_num; break;
      case MESH_FACES:
      case MESH_FACE_NORMALS: result = mesh.faces_num; break;
      default: result = int64_t(mesh.corners_num) - int64_t(mesh.faces_num) * 2; break;
    }
    return result >= 0 ? Status::OK : Status::INVALID_STORAGE;
  }
  if (family >= CURVE_POINTS && family <= CURVE_SLICE_POINTS) {
    if (GS(owner.owner_id->name) != ID_CV) return Status::INVALID_STORAGE;
    const Curves &curves = *reinterpret_cast<const Curves *>(owner.owner_id);
    const blender::CurvesGeometry &geometry = curves.geometry;
    if (family == CURVE_SLICE_POINTS) {
      if (!geometry.curve_offsets || geometry.curve_num < 0) return Status::INVALID_STORAGE;
      const int64_t slice = pointer_index(owner.data, Span<int>(geometry.curve_offsets, geometry.curve_num));
      if (slice < 0) return Status::INVALID_STORAGE;
      result = geometry.curve_offsets[slice + 1] - geometry.curve_offsets[slice];
    }
    else if (family == CURVE_OFFSETS) result = int64_t(geometry.curve_num) + 1;
    else if (family == CURVE_SLICES) result = geometry.curve_num;
    else result = geometry.point_num;
    return result >= 0 ? Status::OK : Status::INVALID_STORAGE;
  }
  if (family == POINTS) {
    if (GS(owner.owner_id->name) != ID_PT || owner.data != owner.owner_id) return Status::INVALID_STORAGE;
    result = static_cast<const PointCloud *>(owner.data)->totpoint;
    return result >= 0 ? Status::OK : Status::INVALID_STORAGE;
  }
  if (family == GP_DRAWING_OFFSETS) {
    if (GS(owner.owner_id->name) != ID_GP) return Status::INVALID_STORAGE;
    const GreasePencilDrawing &drawing = *static_cast<const GreasePencilDrawing *>(owner.data);
    result = int64_t(drawing.geometry.curve_num) + 1;
    return result >= 1 ? Status::OK : Status::INVALID_STORAGE;
  }
  if (family >= GP_LAYERS && family <= GP_FRAMES) {
    if (GS(owner.owner_id->name) != ID_GP) return Status::INVALID_STORAGE;
    const GreasePencil &grease_pencil = *reinterpret_cast<const GreasePencil *>(owner.owner_id);
    Hierarchy *hierarchy = scope.impl_->hierarchy(grease_pencil);
    if (!hierarchy || hierarchy->status != Status::OK) return Status::INVALID_STORAGE;
    if (family == GP_LAYERS) result = hierarchy->layers.size();
    else if (family == GP_GROUPS) result = hierarchy->groups.size();
    else if (family == GP_ROOT_NODES) result = hierarchy->roots.size();
    else if (family == GP_FRAMES) {
      const auto found = std::find(hierarchy->layers.begin(), hierarchy->layers.end(), owner.data);
      if (found == hierarchy->layers.end()) return Status::INVALID_STORAGE;
      result = (*found)->frames().size();
    }
    else {
      const auto found = std::find(hierarchy->groups.begin(), hierarchy->groups.end(), owner.data);
      if (found == hierarchy->groups.end()) return Status::INVALID_STORAGE;
      for (const GreasePencilLayerTreeNode &node : (*found)->children) {
        UNUSED_VARS(node);
        result++;
      }
    }
    return Status::OK;
  }
  if (family == ATTRIBUTE_DATA_LENGTH) {
    switch (GS(owner.owner_id->name)) {
      case ID_ME: {
        const Mesh &mesh = *reinterpret_cast<const Mesh *>(owner.owner_id);
        if (!mesh.runtime || !mesh.attribute_storage.runtime) return Status::INVALID_STORAGE;
        if (mesh.runtime->edit_mesh) return Status::OK;
        const bke::Attribute *attribute = find_attribute(owner, mesh.attribute_storage.wrap());
        if (!attribute) return Status::INVALID_STORAGE;
        result = std::max(0, domain_size(mesh, attribute->domain()));
        return Status::OK;
      }
      case ID_CV: {
        const Curves &curves = *reinterpret_cast<const Curves *>(owner.owner_id);
        if (!curves.geometry.attribute_storage.runtime) return Status::INVALID_STORAGE;
        const bke::Attribute *attribute = find_attribute(owner, curves.geometry.attribute_storage.wrap());
        if (!attribute) return Status::INVALID_STORAGE;
        result = attribute->domain() == AttrDomain::Point ? curves.geometry.point_num :
                 attribute->domain() == AttrDomain::Curve ? curves.geometry.curve_num : 0;
        return result >= 0 ? Status::OK : Status::INVALID_STORAGE;
      }
      case ID_PT: {
        const PointCloud &points = *reinterpret_cast<const PointCloud *>(owner.owner_id);
        if (!points.attribute_storage.runtime) return Status::INVALID_STORAGE;
        const bke::Attribute *attribute = find_attribute(owner, points.attribute_storage.wrap());
        if (!attribute) return Status::INVALID_STORAGE;
        result = attribute->domain() == AttrDomain::Point ? points.totpoint : 0;
        return result >= 0 ? Status::OK : Status::INVALID_STORAGE;
      }
      case ID_GP: {
        const GreasePencil &grease_pencil = *reinterpret_cast<const GreasePencil *>(owner.owner_id);
        if (!grease_pencil.attribute_storage.runtime) return Status::INVALID_STORAGE;
        if (find_attribute(owner, grease_pencil.attribute_storage.wrap())) {
          Hierarchy *hierarchy = scope.impl_->hierarchy(grease_pencil);
          if (!hierarchy || hierarchy->status != Status::OK) return Status::INVALID_STORAGE;
          const bke::Attribute *attribute = find_attribute(owner, grease_pencil.attribute_storage.wrap());
          result = attribute->domain() == AttrDomain::Layer ? hierarchy->layers.size() : 0;
          return Status::OK;
        }
        for (const GreasePencilDrawingBase *base : grease_pencil.drawings()) {
          if (!base || base->type != GP_DRAWING) continue;
          const auto &drawing = *reinterpret_cast<const GreasePencilDrawing *>(base);
          if (!drawing.geometry.attribute_storage.runtime) return Status::INVALID_STORAGE;
          if (const bke::Attribute *attribute = find_attribute(owner, drawing.geometry.attribute_storage.wrap())) {
            result = attribute->domain() == AttrDomain::Point ? drawing.geometry.point_num :
                     attribute->domain() == AttrDomain::Curve ? drawing.geometry.curve_num : 0;
            return result >= 0 ? Status::OK : Status::INVALID_STORAGE;
          }
        }
        return Status::INVALID_STORAGE;
      }
      default: return Status::UNSUPPORTED;
    }
  }
  return Status::UNSUPPORTED;
}

Status item(Scope &scope,
            const Family family,
            const PointerRNA &owner,
            const PropertyRNA &property,
            const int64_t index,
            Item &result)
{
  result = {};
  int64_t count;
  const Status current = length(scope, family, owner, property, count);
  if (current != Status::OK) return current;
  if (family == ATTRIBUTE_DATA_LENGTH) return Status::UNSUPPORTED;
  if (index < 0 || index >= count) return Status::INVALID_STORAGE;
  const void *data = nullptr;
  StructRNA *type = static_cast<const CollectionPropertyRNA &>(property).item_type;
  int64_t selected_index = index;
  Family selected_family = family;
  if (family >= MESH_VERTICES && family <= MESH_CORNER_NORMALS) {
    const Mesh &source = *static_cast<const Mesh *>(owner.data);
    if (family >= MESH_VERTICES && family <= MESH_FACES) {
      if (family == MESH_VERTICES) {
        const auto values = stored_mesh_span<float3>(source, "position", AttrDomain::Point);
        if (!values) return Status::INVALID_STORAGE;
        data = &(*values)[index];
      }
      else if (family == MESH_EDGES) {
        const auto values = stored_mesh_span<int2>(source, ".edge_verts", AttrDomain::Edge);
        if (!values) return Status::INVALID_STORAGE;
        data = &(*values)[index];
      }
      else if (family == MESH_LOOPS) {
        const auto values = stored_mesh_span<int>(source, ".corner_vert", AttrDomain::Corner);
        if (!values) return Status::INVALID_STORAGE;
        data = &(*values)[index];
      }
      else {
        if (!source.face_offset_indices || index >= source.faces_num) return Status::INVALID_STORAGE;
        data = &source.face_offset_indices[index];
      }
    }
    else if (source.runtime && family == MESH_TRIANGLES && source.runtime->corner_tris_cache.data.is_cached()) {
      const Span<int3> triangles = source.runtime->corner_tris_cache.data.data();
      if (index >= triangles.size()) return Status::INVALID_STORAGE;
      data = &triangles[index];
    }
    else if (source.runtime && family == MESH_TRIANGLE_FACES && source.runtime->corner_tri_faces_cache.is_cached()) {
      const Span<int> faces = source.runtime->corner_tri_faces_cache.data();
      if (index >= faces.size()) return Status::INVALID_STORAGE;
      data = &faces[index];
    }
    else if (source.runtime && family >= MESH_VERTEX_NORMALS && family <= MESH_CORNER_NORMALS) {
      if (const auto normals = existing_normals(source, family)) {
        if (index >= normals->size()) return Status::INVALID_STORAGE;
        data = &(*normals)[index];
      }
    }
    if (data) {
      result.pointer = PointerRNA(owner.owner_id, type, const_cast<void *>(data), owner);
      result.family = family;
      result.index = index;
      return Status::OK;
    }
    MeshView *view = scope.impl_->mesh(source);
    if (!view) return Status::INVALID_STORAGE;
    Mesh &mesh = view->mesh;
    switch (family) {
      case MESH_VERTICES: data = &mesh.vert_positions()[index]; break;
      case MESH_EDGES: data = &mesh.edges()[index]; break;
      case MESH_LOOPS: data = &mesh.corner_verts()[index]; break;
      case MESH_FACES: data = &mesh.face_offsets()[index]; break;
      case MESH_TRIANGLES: {
        const Span<int3> triangles = mesh.corner_tris();
        if (index >= triangles.size()) return Status::INVALID_STORAGE;
        data = &triangles[index];
        break;
      }
      case MESH_TRIANGLE_FACES: {
        const Span<int> faces = mesh.corner_tri_faces();
        if (index >= faces.size()) return Status::INVALID_STORAGE;
        data = &faces[index];
        break;
      }
      default: {
        Span<float3> normals;
        if (family == MESH_CORNER_NORMALS) {
          const auto values = corner_normals(mesh);
          if (!values) return Status::INVALID_STORAGE;
          normals = *values;
        }
        else {
          normals = family == MESH_VERTEX_NORMALS ? mesh.vert_normals() : mesh.face_normals();
        }
        if (index >= normals.size()) return Status::INVALID_STORAGE;
        data = &normals[index];
        break;
      }
    }
  }
  else if (family >= CURVE_POINTS && family <= CURVE_SLICE_POINTS) {
    const Curves &curves = *reinterpret_cast<const Curves *>(owner.owner_id);
    CurvesView *view = scope.impl_->curve(curves.geometry);
    if (!view) return Status::INVALID_STORAGE;
    const auto &geometry = view->geometry.wrap();
    if (family == CURVE_SLICES || family == CURVE_OFFSETS) {
      if (!geometry.curve_offsets && family == CURVE_OFFSETS && geometry.curve_num == 0) {
        /* RNA counts one offset for no curves but stores none: an empty slot, not invalid data. */
        result.family = selected_family;
        result.index = selected_index;
        return Status::OK;
      }
      if (!geometry.curve_offsets) return Status::INVALID_STORAGE;
      data = &geometry.curve_offsets[index];
    }
    else if (family == CURVE_NORMALS) {
      const Span<float3> normals = view->point_normals();
      if (index >= normals.size()) return Status::INVALID_STORAGE;
      data = &normals[index];
    }
    else {
      if (family == CURVE_SLICE_POINTS) {
        const int64_t slice = pointer_index(owner.data, Span<int>(geometry.curve_offsets, geometry.curve_num));
        if (slice < 0) return Status::INVALID_STORAGE;
        selected_index += geometry.curve_offsets[slice];
        selected_family = CURVE_POINTS;
      }
      const Span<float3> positions = geometry.positions();
      if (selected_index >= positions.size()) return Status::INVALID_STORAGE;
      data = &positions[selected_index];
    }
  }
  else if (family == POINTS) {
    const PointCloud &points = *static_cast<const PointCloud *>(owner.data);
    if (!points.attribute_storage.runtime) return Status::INVALID_STORAGE;
    const bke::Attribute *attribute = points.attribute_storage.wrap().lookup("position");
    if (!attribute || attribute->domain() != AttrDomain::Point || attribute->data_type() != bke::AttrType::Float3 ||
        !std::holds_alternative<bke::Attribute::ArrayData>(attribute->data()) || !attribute_valid(*attribute, count))
    {
      return Status::INVALID_STORAGE;
    }
    data = &points.positions()[index];
  }
  else if (family == GP_DRAWING_OFFSETS) {
    const auto &drawing = *static_cast<const GreasePencilDrawing *>(owner.data);
    CurvesView *view = scope.impl_->curve(drawing.geometry);
    if (!view || !view->geometry.curve_offsets) return Status::INVALID_STORAGE;
    data = &view->geometry.curve_offsets[index];
  }
  else {
    const auto &grease_pencil = *reinterpret_cast<const GreasePencil *>(owner.owner_id);
    Hierarchy *hierarchy = scope.impl_->hierarchy(grease_pencil);
    if (!hierarchy || hierarchy->status != Status::OK) return Status::INVALID_STORAGE;
    if (family == GP_LAYERS) data = hierarchy->layers[index];
    else if (family == GP_GROUPS) data = hierarchy->groups[index];
    else if (family == GP_ROOT_NODES) data = hierarchy->roots[index];
    else if (family == GP_GROUP_CHILDREN) {
      const auto &group = *static_cast<const GreasePencilLayerTreeGroup *>(owner.data);
      int64_t current_index = 0;
      for (const GreasePencilLayerTreeNode &node : group.children) {
        if (current_index++ == index) {
          data = &node;
          break;
        }
      }
    }
    else if (family == GP_FRAMES) {
      const auto &layer = static_cast<const GreasePencilLayer *>(owner.data)->wrap();
      const std::vector<int> &keys = scope.impl_->keys(layer);
      if (index >= int64_t(keys.size())) return Status::INVALID_STORAGE;
      data = layer.frames().lookup_ptr(keys[index]);
    }
    if (type == RNA_GreasePencilTreeNode && data) {
      const auto &node = static_cast<const GreasePencilLayerTreeNode *>(data)->wrap();
      type = node.is_layer() ? RNA_GreasePencilLayer : RNA_GreasePencilLayerGroup;
    }
  }
  if (!data) return Status::INVALID_STORAGE;
  result.pointer = PointerRNA(owner.owner_id, type, const_cast<void *>(data), owner);
  result.family = selected_family;
  result.index = selected_index;
  return Status::OK;
}

}  // namespace blender::angelo_read_geometry
