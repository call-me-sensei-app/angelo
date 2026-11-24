/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup geo
 *
 * Row insertion for the dislocation cleanup (see `retopo_cleanup.cc`).
 *
 * A dislocation pair bounds a half row. Removing it (the strip collapse) is one way to cancel the
 * pair; completing it along the other way round between the two (inserting the missing half row)
 * is the other. Where a surface's rows change length (a torus: its outer equator is about twice
 * its inner one), the field places a half row wherever the length crosses one more lattice step;
 * removing all of them leaves every ring with the shortest ring's row count, and the result loses
 * up to a quarter of its faces (2026-11-15). Taking the shorter of the two ways for each pair, as
 * the cleanup does by trying every edit shortest first, keeps the rows near their average.
 *
 * Two insertions, one for each kind of half-row end:
 * - a triangle (the end of a half strip): the edge line leaving the triangle's corner straight
 *   across the lattice is split in two, and a quad strip fills the gap, up to a border or another
 *   non-quad face the line meets head on (the inverse of collapsing that strip);
 * - an even face after the split, usually a pentagon (the end of a half line): the strip leaving
 *   it is cut along its middle, every rung split at its midpoint.
 */

#include "retopo_cleanup.hh"

namespace blender::geometry::retopo::cleanup {

namespace {

/** The faces around a vertex in rotation order. Inside: `faces[i]` lies between `nbrs[i - 1]` and
 * `nbrs[i]` (cyclic). On a border: `faces[i]` lies between `nbrs[i]` and `nbrs[i + 1]`, and
 * `nbrs[0]`, `nbrs.back()` are across its border edges. */
struct Fan {
  std::vector<int> faces;
  std::vector<int> nbrs;
  bool border = false;
};

int corner_of(const std::vector<int> &face, const int v)
{
  return int(std::find(face.begin(), face.end(), v) - face.begin());
}
int next_of(const std::vector<int> &face, const int v)
{
  return face[(size_t(corner_of(face, v)) + 1) % face.size()];
}
int prev_of(const std::vector<int> &face, const int v)
{
  return face[(size_t(corner_of(face, v)) + face.size() - 1) % face.size()];
}

/** The face across edge (f's corner at v, w): -1 at a border, -2 at a non-manifold edge. */
int across_or_border(const Mesh &m, const int f, const int v, const int w)
{
  const auto it = m.edge_faces.find(edge_key(v, w));
  if (it == m.edge_faces.end() || it->second.size() > 2) {
    return -2;
  }
  if (it->second.size() == 1) {
    return -1;
  }
  return it->second[0] == f ? it->second[1] : it->second[0];
}

/** The fan of `v`, starting at `f0` when `v` is inside. False when `v`'s faces are not one fan. */
bool build_fan(const Mesh &m, const int v, const int f0, Fan &fan)
{
  const size_t limit = m.vert_faces[size_t(v)].size();
  fan = Fan();
  int start = f0;
  for (size_t i = 0;; i++) {
    if (i > limit) {
      return false;
    }
    const int h = across_or_border(m, start, v, prev_of(m.faces[size_t(start)], v));
    if (h == -2) {
      return false;
    }
    if (h == -1) {
      fan.border = true;
      break;
    }
    if (h == f0) {
      break;
    }
    start = h;
  }
  if (!fan.border) {
    start = f0;
  }
  else {
    fan.nbrs.push_back(prev_of(m.faces[size_t(start)], v));
  }
  int g = start;
  while (true) {
    if (fan.faces.size() > limit) {
      return false;
    }
    fan.faces.push_back(g);
    const int n = next_of(m.faces[size_t(g)], v);
    fan.nbrs.push_back(n);
    const int h = across_or_border(m, g, v, n);
    if (h == -2 || (h == -1) != fan.border) {
      return false;
    }
    if (h == -1 || h == start) {
      break;
    }
    g = h;
  }
  return fan.faces.size() == limit;
}

int index_of(const std::vector<int> &list, const int x)
{
  const auto it = std::find(list.begin(), list.end(), x);
  return it == list.end() ? -1 : int(it - list.begin());
}

Vec3 centroid(const Mesh &m, const std::vector<int> &face)
{
  Vec3 c;
  for (const int v : face) {
    c += m.positions[size_t(v)];
  }
  return c / double(face.size());
}

/** Where a path vertex is split: its faces on each side, and the face that takes both copies. */
struct PathVertex {
  int v = -1;
  std::vector<int> a, b;
  int both = -1;
  /** Where the side-A copy moves (the original vertex is the side-A copy) and where the new
   * side-B copy goes. */
  Vec3 pos_a, pos_b;
};

/** Faces `fan.faces[from], fan.faces[from + 1], ...` (`count` of them, cyclic). */
std::vector<int> fan_run(const Fan &fan, const int from, const int count)
{
  std::vector<int> run;
  const int k = int(fan.faces.size());
  for (int i = 0; i < count; i++) {
    run.push_back(fan.faces[size_t(((from + i) % k + k) % k)]);
  }
  return run;
}

void place_inside(const Mesh &m, PathVertex &p)
{
  const Vec3 &v = m.positions[size_t(p.v)];
  auto side = [&](const std::vector<int> &faces) {
    Vec3 c;
    for (const int f : faces) {
      c += centroid(m, m.faces[size_t(f)]);
    }
    return faces.empty() ? v : v + (c / double(faces.size()) - v) * 0.5;
  };
  p.pos_a = side(p.a);
  p.pos_b = side(p.b);
}

/** Turn the planned path into an edit. */
void path_edit(const Mesh &m, const std::vector<PathVertex> &path, Edit &edit)
{
  const int base = int(m.positions.size());
  std::map<int, int> index; /* path vertex -> its place on the path */
  for (size_t i = 0; i < path.size(); i++) {
    index[path[i].v] = int(i);
  }
  std::vector<int> faces;
  for (const PathVertex &p : path) {
    faces.insert(faces.end(), p.a.begin(), p.a.end());
    faces.insert(faces.end(), p.b.begin(), p.b.end());
    if (p.both >= 0) {
      faces.push_back(p.both);
    }
  }
  std::sort(faces.begin(), faces.end());
  faces.erase(std::unique(faces.begin(), faces.end()), faces.end());
  edit.removed = faces;
  for (const int f : faces) {
    std::vector<int> out;
    for (const int v : m.faces[size_t(f)]) {
      const auto it = index.find(v);
      if (it == index.end()) {
        out.push_back(v);
        continue;
      }
      const PathVertex &p = path[size_t(it->second)];
      const int copy_b = base + it->second;
      if (f == p.both) {
        /* The start face holds the copies B then A, the end face A then B (see below). */
        if (it->second == 0) {
          out.push_back(copy_b);
          out.push_back(v);
        }
        else {
          out.push_back(v);
          out.push_back(copy_b);
        }
      }
      else if (std::find(p.b.begin(), p.b.end(), f) != p.b.end()) {
        out.push_back(copy_b);
      }
      else {
        out.push_back(v);
      }
    }
    edit.added.push_back(out);
  }
  /* The new strip: side A's faces hold each path edge as v_i -> v_i+1, side B's as its copies
   * v_i+1 -> v_i, so the quad between them runs the other way round. */
  for (size_t i = 0; i + 1 < path.size(); i++) {
    edit.added.push_back({path[i + 1].v, path[i].v, base + int(i), base + int(i) + 1});
  }
  for (size_t i = 0; i < path.size(); i++) {
    edit.new_positions.push_back(path[i].pos_b);
    edit.new_kinds.push_back(m.kind[size_t(path[i].v)]);
    edit.moved.push_back({path[i].v, path[i].pos_a});
  }
  edit.length = int(path.size());
  edit.base = base;
}

}  // namespace

void plan_line_splits(const Mesh &m, const int f, const int max_quads, std::vector<Edit> &edits)
{
  const std::vector<int> &start_face = m.faces[size_t(f)];
  for (const int v0 : start_face) {
    if (m.kind[size_t(v0)] != FeatureKind::None) {
      continue;
    }
    Fan fan;
    if (!build_fan(m, v0, f, fan) || fan.border) {
      continue;
    }
    /* fan.faces[0] is the start face, between nbrs[k - 1] and nbrs[0]; the line leaves through
     * nbrs[b] with the other faces shared as evenly as the valence allows. */
    const int k = int(fan.faces.size());
    std::vector<int> choices = {(k - 1) / 2};
    if (k / 2 != (k - 1) / 2) {
      choices.push_back(k / 2);
    }
    for (const int b : choices) {
      if (b < 1 || b > k - 2) {
        continue;
      }
      std::vector<PathVertex> path(1);
      path[0].v = v0;
      path[0].a = fan_run(fan, 1, b);
      path[0].b = fan_run(fan, b + 1, k - 1 - b);
      path[0].both = f;
      place_inside(m, path[0]);
      int cur = v0, next = fan.nbrs[size_t(b)], via = fan.faces[size_t(b)];
      bool ok = false;
      while (int(path.size()) <= max_quads) {
        if (m.kind[size_t(next)] == FeatureKind::Corner ||
            std::any_of(path.begin(), path.end(), [&](const PathVertex &p) { return p.v == next; }))
        {
          break;
        }
        Fan nf;
        if (!build_fan(m, next, via, nf)) {
          break;
        }
        const int a = index_of(nf.nbrs, cur);
        if (a < 0) {
          break;
        }
        PathVertex p;
        p.v = next;
        if (nf.border) {
          /* The line meets a border: the copies stay on it, a third of the way to their border
           * neighbours, and the new strip opens onto it. */
          const int faces = int(nf.faces.size());
          if (a < 1 || a > faces - 1) {
            break;
          }
          p.a = fan_run(nf, a, faces - a);
          p.b = fan_run(nf, 0, a);
          const Vec3 &x = m.positions[size_t(next)];
          p.pos_a = x + (m.positions[size_t(nf.nbrs.back())] - x) * (1.0 / 3.0);
          p.pos_b = x + (m.positions[size_t(nf.nbrs.front())] - x) * (1.0 / 3.0);
          path.push_back(p);
          ok = true;
          break;
        }
        if (m.kind[size_t(next)] != FeatureKind::None) {
          break;
        }
        const int kk = int(nf.faces.size());
        if (kk == 4) {
          /* A regular vertex: straight on through the opposite edge. */
          p.a = fan_run(nf, a + 1, 2);
          p.b = fan_run(nf, a + 3, 2);
          place_inside(m, p);
          path.push_back(p);
          cur = next;
          next = nf.nbrs[size_t((a + 2) % 4)];
          via = nf.faces[size_t((a + 2) % 4)];
          continue;
        }
        if (kk % 2 == 1) {
          /* An odd vertex: the face straight across takes both copies; the line ends there. */
          const int h = (kk - 1) / 2;
          p.a = fan_run(nf, a + 1, h);
          p.both = nf.faces[size_t((a + h + 1) % kk)];
          p.b = fan_run(nf, a + h + 2, h);
          if (p.both == f) {
            break;
          }
          place_inside(m, p);
          path.push_back(p);
          ok = true;
        }
        break;
      }
      if (!ok) {
        continue;
      }
      Edit edit;
      path_edit(m, path, edit);
      edits.push_back(std::move(edit));
    }
  }
}

bool plan_strip_split(const Mesh &m, const Strip &strip, const double scale, Edit &edit)
{
  const int base = int(m.positions.size());
  for (size_t i = 0; i < strip.rungs.size(); i++) {
    const int a = strip.rungs[i][0], b = strip.rungs[i][1];
    if (distance(m.positions[size_t(a)], m.positions[size_t(b)]) * MAX_RUNG_OF_SCALE < scale) {
      return false;
    }
    const bool on_line = m.kind[size_t(a)] != FeatureKind::None &&
                         m.kind[size_t(b)] != FeatureKind::None;
    edit.new_positions.push_back((m.positions[size_t(a)] + m.positions[size_t(b)]) * 0.5);
    edit.new_kinds.push_back(on_line ? FeatureKind::Line : FeatureKind::None);
  }
  edit.removed = strip.faces;
  for (size_t j = 0; j < strip.faces.size(); j++) {
    const std::vector<int> &face = m.faces[size_t(strip.faces[j])];
    /* The face's rungs: the one it was entered through and the one it is left through. */
    std::vector<int> rung_ids;
    if (j > 0) {
      rung_ids.push_back(int(j) - 1);
    }
    if (j < strip.rungs.size()) {
      rung_ids.push_back(int(j));
    }
    std::vector<int> out, mids;
    for (size_t c = 0; c < face.size(); c++) {
      const int u = face[c], w = face[(c + 1) % face.size()];
      out.push_back(u);
      for (const int r : rung_ids) {
        const auto &rung = strip.rungs[size_t(r)];
        if ((rung[0] == u && rung[1] == w) || (rung[0] == w && rung[1] == u)) {
          mids.push_back(int(out.size()));
          out.push_back(base + r);
        }
      }
    }
    const int n = int(out.size());
    int cut_from = -1, cut_to = -1;
    if (mids.size() == 2) {
      cut_from = mids[0];
      cut_to = mids[1];
    }
    else if (mids.size() == 1 && n % 2 == 0 && n >= 6) {
      cut_from = mids[0];
      cut_to = (mids[0] + n / 2) % n;
    }
    if (cut_from < 0) {
      edit.added.push_back(out);
      continue;
    }
    for (const auto &[from, to] : {std::pair{cut_from, cut_to}, std::pair{cut_to, cut_from}}) {
      std::vector<int> half;
      for (int c = from;; c = (c + 1) % n) {
        half.push_back(out[size_t(c)]);
        if (c == to) {
          break;
        }
      }
      edit.added.push_back(half);
    }
  }
  edit.length = int(strip.rungs.size());
  edit.base = base;
  return true;
}

bool apply_if_better(Mesh &m, const Edit &edit)
{
  const int old_verts = int(m.positions.size());
  const int old_faces = int(m.faces.size());
  std::vector<std::vector<int>> added = edit.added;
  for (std::vector<int> &face : added) {
    for (int &v : face) {
      v += v >= edit.base ? old_verts - edit.base : 0;
    }
  }
  for (const std::vector<int> &face : added) {
    std::vector<int> sorted = face;
    std::sort(sorted.begin(), sorted.end());
    if (face.size() < 3 || std::adjacent_find(sorted.begin(), sorted.end()) != sorted.end()) {
      return false; /* a face would pinch */
    }
  }
  m.kind.insert(m.kind.end(), edit.new_kinds.begin(), edit.new_kinds.end());
  m.vert_faces.resize(m.kind.size());
  auto face_of = [&](const int f) -> const std::vector<int> & {
    return f < old_faces ? m.faces[size_t(f)] : added[size_t(f - old_faces)];
  };
  std::vector<int> removed = edit.removed;
  std::sort(removed.begin(), removed.end());
  auto is_removed = [&](const int f) {
    return std::binary_search(removed.begin(), removed.end(), f);
  };
  std::map<int, std::vector<int>> after; /* touched vertex -> its faces after the edit */
  for (const int f : removed) {
    for (const int v : m.faces[size_t(f)]) {
      after[v];
    }
  }
  for (size_t i = 0; i < added.size(); i++) {
    for (const int v : added[i]) {
      after[v].push_back(old_faces + int(i));
    }
  }
  int before = 0, later = 0;
  for (const int f : removed) {
    before += std::abs(int(m.faces[size_t(f)].size()) - 4);
  }
  for (const std::vector<int> &face : added) {
    later += std::abs(int(face.size()) - 4);
  }
  std::map<uint64_t, int> uses;
  for (auto &[v, faces] : after) {
    if (v < old_verts) {
      before += irregularity(m, v, m.vert_faces[size_t(v)], face_of);
      for (const int f : m.vert_faces[size_t(v)]) {
        if (!is_removed(f)) {
          faces.push_back(f);
        }
      }
    }
    std::sort(faces.begin(), faces.end());
    faces.erase(std::unique(faces.begin(), faces.end()), faces.end());
    later += irregularity(m, v, faces, face_of);
  }
  /* Manifold after: no edge of a touched vertex in more than two faces. */
  std::vector<int> all;
  for (const auto &[v, faces] : after) {
    all.insert(all.end(), faces.begin(), faces.end());
  }
  std::sort(all.begin(), all.end());
  all.erase(std::unique(all.begin(), all.end()), all.end());
  bool manifold = true;
  for (const int f : all) {
    const std::vector<int> &face = face_of(f);
    for (size_t c = 0; c < face.size() && manifold; c++) {
      manifold = ++uses[edge_key(face[c], face[(c + 1) % face.size()])] <= 2;
    }
  }
  if (!manifold || later >= before) {
    m.kind.resize(size_t(old_verts));
    m.vert_faces.resize(size_t(old_verts));
    return false;
  }
  m.positions.insert(m.positions.end(), edit.new_positions.begin(), edit.new_positions.end());
  for (const auto &[v, p] : edit.moved) {
    m.positions[size_t(v)] = p;
  }
  for (const int f : removed) {
    m.unlink(f);
    m.dead[size_t(f)] = 1;
    m.changed[size_t(f)] = 1;
    m.faces[size_t(f)].clear();
  }
  for (const std::vector<int> &face : added) {
    m.faces.push_back(face);
    m.dead.push_back(0);
    m.changed.push_back(1);
    m.link(int(m.faces.size()) - 1);
  }
  return true;
}

}  // namespace blender::geometry::retopo::cleanup
