/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <variant>
#include <vector>

#include "BLI_bounds_types.hh"
#include "BLI_math_vector_types.hh"
#include "BLI_span.hh"
#include "RNA_types.hh"

namespace blender {
struct Object;
struct Mesh;
}

namespace blender::bke {
class Attribute;
}

/**
 * Native replacements for catalogued geometry getters (doc/angelo/scene_intelligence.rst,
 * "Catalogued built-in getters"). Ordinary RNA getters for mesh, curves, point cloud and Grease
 * Pencil elements take writable spans or fill lazy runtime caches on the artist's data; these
 * copies read const storage or compute in an operation-private runtime instead.
 *
 * Nothing here evaluates the scene, loads a file or runs add-on code. A value that would need
 * one of those reports NOT_EVALUATED or LOADS_FILE, and the reader withholds it.
 */
namespace blender::angelo_read_geometry {

enum class Status {
  OK,
  INVALID_STORAGE,
  UNSUPPORTED,
  /** The value depends on an evaluation Blender has not made (for example a subdivision
   * wrapper that was never computed). Read again after evaluation, or use a trusted script. */
  NOT_EVALUATED,
  /** Computing the value would read a file (a font or a volume grid). Nothing is loaded. */
  LOADS_FILE,
  RESOURCE_LIMIT,
};
enum class BoundsMode { DATA, EVALUATED_CACHE_THEN_DATA };
using Family = uint16_t;

/* Source-owned identifiers. They are never accepted as caller-provided permission. */
enum : Family {
  NONE = 0,
  MESH_VERTEX_CO,
  MESH_VERTEX_INDEX,
  MESH_VERTEX_NORMAL,
  MESH_VERTEX_UNDEFORMED,
  MESH_VERTEX_SELECT,
  MESH_VERTEX_HIDE,
  MESH_EDGE_INDEX,
  MESH_EDGE_SELECT,
  MESH_EDGE_HIDE,
  MESH_EDGE_SEAM,
  MESH_EDGE_SHARP,
  MESH_EDGE_LOOSE,
  MESH_LOOP_INDEX,
  MESH_LOOP_VERTEX,
  MESH_LOOP_EDGE,
  MESH_LOOP_NORMAL,
  MESH_LOOP_TANGENT,
  MESH_LOOP_BITANGENT_SIGN,
  MESH_LOOP_BITANGENT,
  MESH_FACE_INDEX,
  MESH_FACE_VERTICES,
  MESH_FACE_LOOP_TOTAL,
  MESH_FACE_MATERIAL,
  MESH_FACE_SELECT,
  MESH_FACE_HIDE,
  MESH_FACE_SMOOTH,
  MESH_FACE_NORMAL,
  MESH_FACE_CENTER,
  MESH_FACE_AREA,
  MESH_TRI_INDEX,
  MESH_TRI_FACE,
  MESH_TRI_VERTICES,
  MESH_TRI_NORMAL,
  MESH_TRI_SPLIT_NORMALS,
  MESH_TRI_AREA,
  MESH_TRI_MATERIAL,
  MESH_TRI_SMOOTH,
  MESH_TEXSPACE_LOCATION,
  MESH_TEXSPACE_SIZE,
  MESH_NORMALS_DOMAIN,
  MESH_UV_PIN,
  CURVE_POINT_LOCATION,
  CURVE_POINT_INDEX,
  CURVE_POINT_RADIUS,
  CURVE_SLICE_INDEX,
  CURVE_SLICE_FIRST,
  CURVE_SLICE_LENGTH,
  POINT_LOCATION,
  POINT_INDEX,
  POINT_RADIUS,
  GP_FRAME_NUMBER,
  GP_LAYER_TINT,
  GP_LAYER_TINT_FACTOR,
  GP_LAYER_RADIUS_OFFSET,
  GP_LAYER_PASS,
  GP_LAYER_MATRIX_LOCAL,
  GP_LAYER_MATRIX_PARENT,
  LEGACY_CURVE_TEXSPACE_LOCATION,
  LEGACY_CURVE_TEXSPACE_SIZE,

  MESH_VERTICES = 256,
  MESH_EDGES,
  MESH_LOOPS,
  MESH_FACES,
  MESH_TRIANGLES,
  MESH_TRIANGLE_FACES,
  MESH_VERTEX_NORMALS,
  MESH_FACE_NORMALS,
  MESH_CORNER_NORMALS,
  CURVE_POINTS,
  CURVE_SLICES,
  CURVE_POSITIONS,
  CURVE_OFFSETS,
  CURVE_NORMALS,
  CURVE_SLICE_POINTS,
  POINTS,
  GP_LAYERS,
  GP_GROUPS,
  GP_ROOT_NODES,
  GP_GROUP_CHILDREN,
  GP_FRAMES,
  GP_DRAWING_OFFSETS,
  ATTRIBUTE_DATA_LENGTH,
};

Family scalar_family(const StructRNA &owner_type, const PropertyRNA &property);
Family collection_family(const StructRNA &owner_type, const PropertyRNA &property);
/** Whether any of this declaration's read callbacks is one this module replaces. The core
 * withholds such a property when no family matched it exactly, rather than run the callback. */
bool catalogued(const PropertyRNA &property);

struct Value {
  Status status = Status::UNSUPPORTED;
  std::variant<std::monostate,
               bool,
               int,
               float,
               std::vector<bool>,
               std::vector<int>,
               std::vector<float>> data;
  int64_t actual_length = 0;
};

struct Item {
  PointerRNA pointer;
  Family family = NONE;
  int64_t index = -1;
  /* Optional provenance from the core's already const-selected legacy UV element. A SINGLE
   * attribute uses index as its logical index; pointer alone cannot recover that index. */
  const bke::Attribute *attribute_source = nullptr;
};

class Scope {
 public:
  Scope();
  ~Scope();
  Scope(const Scope &) = delete;
  Scope &operator=(const Scope &) = delete;

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
  friend Value value(Scope &, Family, const PointerRNA &, const PropertyRNA &, const Item *);
  friend Status length(Scope &, Family, const PointerRNA &, const PropertyRNA &, int64_t &);
  friend Status mesh_vertex_normals_for_read(Scope &, const Mesh &, Span<float3> &);
  friend Status mesh_subdivision_for_read(Scope &, const Mesh &, const Mesh *&);
  friend Status lock_mesh_for_read(Scope &, const Mesh &);
  friend Status object_evaluated_mesh_for_read(Scope &, const Object &, const Mesh *&);
  friend Status object_bounds(Scope &, const Object &, BoundsMode, std::optional<Bounds<float3>> &);
  friend Status item(Scope &, Family, const PointerRNA &, const PropertyRNA &, int64_t, Item &);
};

/* Numeric kernel dependency only. Output is const borrowed storage, length exactly verts_num,
 * consumed before Scope destruction. Caller admits the original RNA owner/source separately. */
Status mesh_vertex_normals_for_read(Scope &scope, const Mesh &mesh, Span<float3> &result);
/* The mesh RNA's Object.data selects for an original mesh: the mesh itself, or the subdivision
 * result Blender already computed. A subdivision that was never computed is NOT_EVALUATED; it is
 * never computed here. Holds the source evaluation mutex through Scope. */
Status mesh_subdivision_for_read(Scope &scope, const Mesh &mesh, const Mesh *&result);
Status lock_mesh_for_read(Scope &scope, const Mesh &mesh);
/* The evaluated mesh Blender already keeps for this object, if any; never evaluates. */
Status object_evaluated_mesh_for_read(Scope &scope, const Object &object, const Mesh *&result);

/* Bounds from the evaluated cache when present, otherwise from stored data in a private runtime.
 * Text objects whose glyphs are not converted and volumes report LOADS_FILE.
 * OK + nullopt means the canonical source has no bounds. */
Status object_bounds(Scope &scope,
                     const Object &object,
                     BoundsMode mode,
                     std::optional<Bounds<float3>> &result);

Value value(Scope &scope,
            Family family,
            const PointerRNA &owner,
            const PropertyRNA &property,
            const Item *selected_item = nullptr);
Status length(Scope &scope,
              Family family,
              const PointerRNA &owner,
              const PropertyRNA &property,
              int64_t &result);
Status item(Scope &scope,
            Family family,
            const PointerRNA &owner,
            const PropertyRNA &property,
            int64_t index,
            Item &result);

}  // namespace blender::angelo_read_geometry
