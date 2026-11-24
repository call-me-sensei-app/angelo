/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup geo
 *
 * The editable polygon mesh the dislocation cleanup works on, shared by the strip collapse
 * (`retopo_cleanup.cc`) and the row insertions (`retopo_cleanup_split.cc`).
 */

#pragma once

#include <algorithm>
#include <cstdlib>
#include <map>
#include <utility>
#include <vector>

#include "retopo_edges.hh"
#include "retopo_mesh.hh"

namespace blender::geometry::retopo::cleanup {

/** A rung longer than this many lattice spacings is not one row of the lattice: the strip is not
 * collapsed; a rung shorter than one over it is too short to split again. */
inline constexpr double MAX_RUNG_OF_SCALE = 1.6;

struct Mesh {
  std::vector<Vec3> positions;
  std::vector<FeatureKind> kind;
  std::vector<std::vector<int>> faces;
  std::vector<uint8_t> dead;
  /** Faces changed (or removed) during the current pass: an edit planned before must be planned
   * again. */
  std::vector<uint8_t> changed;
  std::vector<std::vector<int>> vert_faces;
  std::map<uint64_t, std::vector<int>> edge_faces;
  /** Live faces and their corners: what the finished mesh will count (the quad split turns each
   * face into as many quads as it has corners). */
  int live_faces = 0;
  int live_corners = 0;

  void link(const int f)
  {
    const std::vector<int> &face = faces[size_t(f)];
    live_faces++;
    live_corners += int(face.size());
    for (size_t c = 0; c < face.size(); c++) {
      vert_faces[size_t(face[c])].push_back(f);
      edge_faces[edge_key(face[c], face[(c + 1) % face.size()])].push_back(f);
    }
  }
  void unlink(const int f)
  {
    const std::vector<int> &face = faces[size_t(f)];
    live_faces--;
    live_corners -= int(face.size());
    for (size_t c = 0; c < face.size(); c++) {
      std::vector<int> &vf = vert_faces[size_t(face[c])];
      vf.erase(std::find(vf.begin(), vf.end(), f));
      const uint64_t key = edge_key(face[c], face[(c + 1) % face.size()]);
      std::vector<int> &ef = edge_faces[key];
      ef.erase(std::find(ef.begin(), ef.end(), f));
      if (ef.empty()) {
        edge_faces.erase(key);
      }
    }
  }
  /** Whether any of `fs` was removed or changed in this pass. */
  bool stale(const std::vector<int> &fs) const
  {
    for (const int f : fs) {
      if (dead[size_t(f)] || changed[size_t(f)]) {
        return true;
      }
    }
    return false;
  }
};

/** A quad strip: quads entered and left through opposite edges (the rungs), from a non-quad face
 * to another non-quad face or a border. */
struct Strip {
  std::vector<std::array<int, 2>> rungs;
  /** The start face, the strip's quads, then the end face (if not a border). */
  std::vector<int> faces;
};

/** The face across edge (a, b) from `f`, or -1 at a border or a non-manifold edge. */
inline int across(const Mesh &m, const int f, const int a, const int b)
{
  const auto it = m.edge_faces.find(edge_key(a, b));
  if (it == m.edge_faces.end() || it->second.size() != 2) {
    return -1;
  }
  return it->second[0] == f ? it->second[1] : it->second[0];
}

/** Walk the strip that leaves face `f` through its edge at corner `c`. False when it doesn't end
 * cleanly (a non-manifold edge, a strip closing on itself, or longer than `max_quads`). */
bool trace_strip(const Mesh &m, int f, int c, int max_quads, Strip &strip);

/** How far `v`'s valence is from regular (4 inside, 3 on a border; a feature corner never counts),
 * over `v`'s faces as `face_of` gives them. */
template<typename FaceOf>
int irregularity(const Mesh &m, const int v, const std::vector<int> &vfaces, const FaceOf &face_of)
{
  if (m.kind[size_t(v)] == FeatureKind::Corner) {
    return 0;
  }
  std::vector<int> nb;
  for (const int f : vfaces) {
    const std::vector<int> &face = face_of(f);
    const int n = int(face.size());
    for (int c = 0; c < n; c++) {
      if (face[size_t(c)] != v) {
        continue;
      }
      nb.push_back(face[size_t((c + 1) % n)]);
      nb.push_back(face[size_t((c + n - 1) % n)]);
    }
  }
  if (nb.empty()) {
    return 0;
  }
  /* A neighbour seen once (through a single face) is across a border edge. */
  std::sort(nb.begin(), nb.end());
  bool border = false;
  int distinct = 0;
  for (size_t i = 0; i < nb.size();) {
    size_t j = i;
    while (j < nb.size() && nb[j] == nb[i]) {
      j++;
    }
    border |= (j - i) == 1;
    distinct++;
    i = j;
  }
  return std::abs(distinct - (border ? 3 : 4));
}

bool on_border(const Mesh &m, int v);

/**
 * An edit that removes faces and adds new ones, possibly on new vertices (appended to
 * #Mesh::positions with their kinds): applied only when it lowers the irregularity of every face
 * and vertex it touches and leaves the mesh manifold with no pinched face.
 */
struct Edit {
  std::vector<int> removed;
  std::vector<std::vector<int>> added;
  std::vector<Vec3> new_positions;
  std::vector<FeatureKind> new_kinds;
  /** Existing vertices moved by the edit (applied with it). */
  std::vector<std::pair<int, Vec3>> moved;
  /** The rows of quads the edit moves: edits are tried shortest first. */
  int length = 0;
  /** Vertex ids from this one on in #added are the new vertices (the vertex count when the edit
   * was planned; renumbered if other edits added vertices since). */
  int base = 0;
};

bool apply_if_better(Mesh &m, const Edit &edit);

/** Insert the row that ends at a dislocation's triangle: the edge line leaving the triangle's
 * corner straight across the lattice is split in two, with a quad strip between the copies, up to
 * a border or another non-quad face. The inverse of a strip collapse. Returns the edits planned
 * from face `f` (none applied). */
void plan_line_splits(const Mesh &m, int f, int max_quads, std::vector<Edit> &edits);

/** Insert the row that ends at a dislocation's pentagon (or other even-sized face after the
 * split): every rung of the strip leaving `f` through corner `c` is split at its middle and its
 * quads cut in two. */
bool plan_strip_split(const Mesh &m, const Strip &strip, double scale, Edit &edit);

}  // namespace blender::geometry::retopo::cleanup
