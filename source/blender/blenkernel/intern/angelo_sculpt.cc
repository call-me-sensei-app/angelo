/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 * SPDX-License-Identifier: GPL-2.0-or-later */
#include "BKE_angelo_sculpt.hh"
#include "BKE_attribute.hh"
#include "BKE_mesh.hh"
#include "BKE_object_types.hh"
#include "BKE_paint.hh"
#include "BKE_paint_bvh.hh"
#include "BKE_subdiv_ccg.hh"
#include "BLI_math_geom.h"
#include "BLI_math_vector.hh"
#include "DNA_mesh_types.h"
#include "DNA_object_types.h"

namespace blender::bke::angelo {

/** The live base-mesh positions the PBVH reads (#pbvh::vert_positions_eval). */
static Span<float3> live_mesh_positions(const FunctionRef<const Depsgraph &()> depsgraph,
                                        const Object &object)
{
  Span<float3> positions;
  if (!pbvh::vert_positions_eval_without_depsgraph(object, positions)) {
    positions = pbvh::vert_positions_eval(depsgraph(), object);
  }
  return positions;
}

std::optional<float3> surface_vertex(const FunctionRef<const Depsgraph &()> depsgraph,
                                     Object &object,
                                     const int index)
{
  const SculptSession *ss = object.runtime->sculpt_session;
  if (!ss || ss->bm || index < 0) {
    return std::nullopt;
  }
  const Span<float3> positions = ss->subdiv_ccg ? ss->subdiv_ccg->positions.as_span() :
                                                  live_mesh_positions(depsgraph, object);
  return index < positions.size() ? std::make_optional(positions[index]) : std::nullopt;
}

std::optional<float3> surface_vertex(const Depsgraph &depsgraph, Object &object, const int index)
{
  return surface_vertex([&]() -> const Depsgraph & { return depsgraph; }, object, index);
}

std::optional<SurfaceSample> surface_query(const Depsgraph &depsgraph,
                                           Object &object,
                                           const float3 &origin,
                                           const float3 &direction,
                                           const float max_distance)
{
  return surface_query(
      [&]() -> const Depsgraph & { return depsgraph; }, object, origin, direction, max_distance);
}

std::optional<SurfaceSample> surface_query(const FunctionRef<const Depsgraph &()> depsgraph,
                                           Object &object,
                                           const float3 &origin,
                                           const float3 &direction,
                                           const float max_distance)
{
  pbvh::Tree *tree = bke::object::pbvh_get(object);
  if (!tree || tree->type() == pbvh::Type::BMesh || tree->nodes_num() == 0) {
    return std::nullopt;
  }
  Mesh &mesh = *id_cast<Mesh *>(object.data);
  const SculptSession &ss = *object.runtime->sculpt_session;
  const SubdivCCG *ccg = ss.subdiv_ccg;
  Span<float3> positions;
  if (tree->type() == pbvh::Type::Grids) {
    /* Updating CCG normals also stitches duplicated grid-boundary positions.
     * Target the same canonical live surface whether or not a caller took a
     * mesh snapshot between strokes. Otherwise a near-seam Pose seed can
     * change between a grouped program and separate, observed passes. The
     * PBVH tracks dirty nodes, so subsequent queries do not repeat the work. */
    pbvh::update_normals(depsgraph(), object, *tree);
    tree->update_bounds(depsgraph(), object);
    positions = ccg->positions.as_span();
  }
  else {
    /* What #pbvh::Tree::update_bounds does for a mesh tree, without asking for the dependency
     * graph when the positions do not depend on it. */
    const Span<float3> mesh_positions = live_mesh_positions(depsgraph, object);
    tree->update_bounds_mesh(mesh_positions);
    positions = ccg ? ccg->positions.as_span() : mesh_positions;
  }
  const OffsetIndices<int> faces = mesh.faces();
  const Span<int> corners = mesh.corner_verts();
  const Span<int3> triangles = ccg ? Span<int3>() : mesh.corner_tris();
  const VArraySpan<bool> hidden = *mesh.attributes().lookup<bool>(".hide_poly", AttrDomain::Face);
  SurfaceSample sample;
  sample.distance = max_distance;
  bool found = false;
  if (math::length_squared(direction) > 1e-16f) {
    const float3 ray = math::normalize(direction);
    IsectRayPrecalc precalc;
    isect_ray_tri_watertight_v3_precalc(&precalc, ray);
    pbvh::raycast(
        *tree,
        [&](pbvh::Node &node, float *tmin) {
          if ((node.flag_ & pbvh::Node::FullyHidden) ||
              BKE_pbvh_node_get_tmin(&node) >= sample.distance)
          {
            return;
          }
          bool hit;
          if (ccg) {
            SubdivCCGCoord vertex;
            hit = pbvh::node_raycast_grids(*ccg,
                                           static_cast<pbvh::GridsNode &>(node),
                                           {},
                                           origin,
                                           ray,
                                           &precalc,
                                           &sample.distance,
                                           vertex,
                                           sample.grid,
                                           sample.normal);
            if (hit) {
              sample.vertex = vertex.to_index(BKE_subdiv_ccg_key_top_level(*ccg));
              sample.face = ccg->grid_to_face_map[sample.grid];
            }
          }
          else {
            hit = pbvh::node_raycast_mesh(static_cast<pbvh::MeshNode &>(node),
                                          {},
                                          positions,
                                          faces,
                                          corners,
                                          triangles,
                                          hidden,
                                          origin,
                                          ray,
                                          &precalc,
                                          &sample.distance,
                                          sample.vertex,
                                          sample.face,
                                          sample.normal);
          }
          if (hit) {
            found = true;
            *tmin = sample.distance;
          }
        },
        origin,
        ray,
        false);
    sample.position = origin + ray * sample.distance;
  }
  else {
    float best_sq = max_distance * max_distance;
    auto test_triangle = [&](const int a, const int b, const int c, const int face, const int grid) {
      float3 nearest;
      closest_on_tri_to_point_v3(nearest, origin, positions[a], positions[b], positions[c]);
      const float distance_sq = math::distance_squared(nearest, origin);
      if (distance_sq > best_sq) {
        return;
      }
      best_sq = distance_sq;
      found = true;
      sample.position = nearest;
      normal_tri_v3(sample.normal, positions[a], positions[b], positions[c]);
      sample.face = face;
      sample.grid = grid;
      sample.vertex = a;
      for (const int candidate : {b, c}) {
        if (math::distance_squared(nearest, positions[candidate]) <
            math::distance_squared(nearest, positions[sample.vertex])) {
          sample.vertex = candidate;
        }
      }
    };
    auto bounds_distance = [&](const pbvh::Node &node) {
      const Bounds<float3> &bounds = node.bounds();
      return math::distance_squared(origin, math::clamp(origin, bounds.min, bounds.max));
    };
    auto visit = [&](auto nodes) {
      Vector<int, 64> stack;
      stack.append(0);
      while (!stack.is_empty()) {
        const auto &node = nodes[stack.pop_last()];
        /* Visibility flags describe leaf primitives only. Empty internal
         * GridsNode ranges can carry FullyHidden even with visible children. */
        if (BKE_pbvh_node_fully_hidden_get(node) || bounds_distance(node) > best_sq) {
          continue;
        }
        if (!(node.flag_ & pbvh::Node::Leaf)) {
          const int a = node.children_offset_, b = a + 1;
          const bool a_first = bounds_distance(nodes[a]) < bounds_distance(nodes[b]);
          stack.append(a_first ? b : a);
          stack.append(a_first ? a : b);
          continue;
        }
        if constexpr (std::is_same_v<std::decay_t<decltype(node)>, pbvh::MeshNode>) {
          for (const int face : node.faces()) {
            if (!hidden.is_empty() && hidden[face]) {
              continue;
            }
            for (const int index : mesh::face_triangles_range(faces, face)) {
              const int3 tri = triangles[index];
              test_triangle(corners[tri[0]], corners[tri[1]], corners[tri[2]], face, -1);
            }
          }
        }
        else {
          for (const int grid : node.grids()) {
            const int face = ccg->grid_to_face_map[grid];
            if (!hidden.is_empty() && hidden[face]) {
              continue;
            }
            for (const int y : IndexRange(ccg->grid_size - 1)) {
              for (const int x : IndexRange(ccg->grid_size - 1)) {
                if (!ccg->grid_hidden.is_empty() &&
                    paint_is_grid_face_hidden(ccg->grid_hidden[grid], ccg->grid_size, x, y)) {
                  continue;
                }
                const int a = grid * ccg->grid_area + y * ccg->grid_size + x;
                const int b = a + ccg->grid_size, c = b + 1, d = a + 1;
                /* Match the native grid ray-cast diagonal and winding. */
                test_triangle(b, c, d, face, grid);
                test_triangle(b, d, a, face, grid);
              }
            }
          }
        }
      }
    };
    if (ccg) {
      visit(tree->nodes<pbvh::GridsNode>());
    }
    else {
      visit(tree->nodes<pbvh::MeshNode>());
    }
    sample.distance = std::sqrt(best_sq);
  }
  return found ? std::make_optional(sample) : std::nullopt;
}
}  // namespace blender::bke::angelo
