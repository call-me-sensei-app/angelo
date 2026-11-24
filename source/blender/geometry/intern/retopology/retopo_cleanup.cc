/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup geo
 *
 * Dislocation cleanup on the extracted mesh. See `retopo_extract.hh`.
 *
 * A position-field singularity extracts as a dislocation: a non-quad face (usually a triangle)
 * with an irregular vertex beside it, where a half row of quads ends. The half row is a quad strip
 * (quads entered and left through opposite edges) that ends at that face. When the same strip ends
 * at another dislocation, or at a border, it is the surplus row between them: collapsing every
 * edge it crosses (its "rungs") removes the row, and both dislocations with it. The other way to
 * cancel the pair is to insert the missing half row on the far side (`retopo_cleanup_split.cc`).
 * Every edit is tried shortest first, since a short edit moves the least surface, except that
 * while the mesh has fewer faces than asked for, edits that remove faces wait until the others
 * were tried (and the other way round while it has more): each pair can be cancelled either way,
 * and taking the way that brings the count nearer the target keeps the rows near their average
 * instead of drifting to the shortest ring's count. An edit is kept only when it lowers the mesh's
 * irregularity (face sizes off 4, valences off 4 inside or off 3 on a border), keeps the mesh
 * manifold and never merges across a feature corner, so it can't make a mesh worse.
 */

#include "retopo_cleanup.hh"
#include "retopo_extract.hh"

namespace blender::geometry::retopo {

namespace cleanup {

static constexpr int MAX_PASSES = 8;

bool trace_strip(const Mesh &m, const int f, const int c, const int max_quads, Strip &strip)
{
  strip.rungs.clear();
  strip.faces = {f};
  const std::vector<int> &start = m.faces[size_t(f)];
  int a = start[size_t(c)], b = start[(size_t(c) + 1) % start.size()];
  int cur = f;
  while (true) {
    strip.rungs.push_back({a, b});
    const auto it = m.edge_faces.find(edge_key(a, b));
    if (it == m.edge_faces.end() || it->second.size() > 2) {
      return false;
    }
    if (it->second.size() == 1) {
      return true; /* ends at a border */
    }
    const int next = across(m, cur, a, b);
    if (std::find(strip.faces.begin(), strip.faces.end(), next) != strip.faces.end()) {
      return false; /* the strip closes on itself */
    }
    strip.faces.push_back(next);
    const std::vector<int> &face = m.faces[size_t(next)];
    if (face.size() != 4) {
      return true; /* ends at another non-quad face */
    }
    if (int(strip.faces.size()) > max_quads + 1) {
      return false;
    }
    /* Leave the quad through the edge opposite the one entered. */
    const int i = int(std::find(face.begin(), face.end(), a) - face.begin());
    const int j = int(std::find(face.begin(), face.end(), b) - face.begin());
    const bool forward = (i + 1) % 4 == j; /* face walks a -> b */
    const int na = forward ? face[size_t((i + 3) % 4)] : face[size_t((i + 1) % 4)];
    const int nb = forward ? face[size_t((j + 1) % 4)] : face[size_t((j + 3) % 4)];
    a = na;
    b = nb;
    cur = next;
  }
}

bool on_border(const Mesh &m, const int v)
{
  for (const int f : m.vert_faces[size_t(v)]) {
    const std::vector<int> &face = m.faces[size_t(f)];
    for (size_t c = 0; c < face.size(); c++) {
      if (face[c] == v || face[(c + 1) % face.size()] == v) {
        const auto it = m.edge_faces.find(edge_key(face[c], face[(c + 1) % face.size()]));
        if (it != m.edge_faces.end() && it->second.size() == 1) {
          return true;
        }
      }
    }
  }
  return false;
}

/** Collapse `strip` if that lowers the irregularity and keeps the mesh valid. */
static bool try_collapse(Mesh &m, const Strip &strip, const double scale)
{
  if (m.stale(strip.faces)) {
    return false;
  }
  /* Rung vertices: distinct, no corner, rungs about one lattice step. */
  std::map<int, int> merge;
  for (const auto &r : strip.rungs) {
    if (merge.count(r[0]) || merge.count(r[1]) || r[0] == r[1]) {
      return false;
    }
    /* Only like merges like: a row on a feature line or a border never folds onto one off it (that
     * would pull the line, or a mirror seam, out of place). */
    if (m.kind[size_t(r[0])] != m.kind[size_t(r[1])] ||
        m.kind[size_t(r[0])] == FeatureKind::Corner || on_border(m, r[0]) != on_border(m, r[1]))
    {
      return false;
    }
    /* Two border vertices merge only along their border edge: across the inside they'd pinch the
     * border into two. */
    if (on_border(m, r[0])) {
      const auto it = m.edge_faces.find(edge_key(r[0], r[1]));
      if (it == m.edge_faces.end() || it->second.size() != 1) {
        return false;
      }
    }
    if (distance(m.positions[size_t(r[0])], m.positions[size_t(r[1])]) > MAX_RUNG_OF_SCALE * scale)
    {
      return false;
    }
    merge[r[1]] = r[0];
    merge[r[0]] = r[0];
  }
  auto mapped = [&](const int v) {
    const auto it = merge.find(v);
    return it == merge.end() ? v : it->second;
  };
  /* Faces that change, and every vertex whose valence can. */
  std::vector<int> changed;
  for (const auto &kv : merge) {
    for (const int f : m.vert_faces[size_t(kv.first)]) {
      changed.push_back(f);
    }
  }
  std::sort(changed.begin(), changed.end());
  changed.erase(std::unique(changed.begin(), changed.end()), changed.end());
  std::map<int, std::vector<int>> after;
  for (const int f : changed) {
    std::vector<int> face;
    for (const int v : m.faces[size_t(f)]) {
      const int u = mapped(v);
      if (face.empty() || face.back() != u) {
        face.push_back(u);
      }
    }
    while (face.size() > 1 && face.front() == face.back()) {
      face.pop_back();
    }
    std::vector<int> sorted = face;
    std::sort(sorted.begin(), sorted.end());
    if (std::adjacent_find(sorted.begin(), sorted.end()) != sorted.end()) {
      return false; /* a face would pinch */
    }
    after[f] = face.size() >= 3 ? face : std::vector<int>();
  }
  std::vector<int> touched;
  for (const int f : changed) {
    touched.insert(touched.end(), m.faces[size_t(f)].begin(), m.faces[size_t(f)].end());
  }
  std::sort(touched.begin(), touched.end());
  touched.erase(std::unique(touched.begin(), touched.end()), touched.end());

  /* Manifold after: no edge in more than two faces. */
  {
    std::map<uint64_t, int> uses;
    std::vector<int> all;
    for (const int v : touched) {
      all.insert(all.end(), m.vert_faces[size_t(v)].begin(), m.vert_faces[size_t(v)].end());
    }
    std::sort(all.begin(), all.end());
    all.erase(std::unique(all.begin(), all.end()), all.end());
    for (const int f : all) {
      const auto it = after.find(f);
      const std::vector<int> &face = it == after.end() ? m.faces[size_t(f)] : it->second;
      for (size_t c = 0; c < face.size(); c++) {
        uses[edge_key(face[c], face[(c + 1) % face.size()])]++;
      }
    }
    for (const auto &kv : uses) {
      if (kv.second > 2) {
        return false;
      }
    }
  }

  /* Irregularity before and after over the faces and vertices that change. */
  auto before_face = [&](const int f) -> const std::vector<int> & { return m.faces[size_t(f)]; };
  auto after_face = [&](const int f) -> const std::vector<int> & {
    const auto it = after.find(f);
    return it == after.end() ? m.faces[size_t(f)] : it->second;
  };
  int before = 0, later = 0;
  for (const int f : changed) {
    before += std::abs(int(m.faces[size_t(f)].size()) - 4);
    later += after[f].empty() ? 0 : std::abs(int(after[f].size()) - 4);
  }
  std::map<int, std::vector<int>> later_faces;
  for (const int v : touched) {
    before += irregularity(m, v, m.vert_faces[size_t(v)], before_face);
    for (const int f : m.vert_faces[size_t(v)]) {
      if (!after_face(f).empty()) {
        later_faces[mapped(v)].push_back(f);
      }
    }
  }
  for (auto &kv : later_faces) {
    std::sort(kv.second.begin(), kv.second.end());
    kv.second.erase(std::unique(kv.second.begin(), kv.second.end()), kv.second.end());
    later += irregularity(m, kv.first, kv.second, after_face);
  }
  if (later >= before) {
    return false;
  }

  /* Apply. A merged vertex keeps a feature vertex's place and kind; two alike meet in the middle.
   */
  for (const auto &r : strip.rungs) {
    const int keep = r[0], gone = r[1];
    const FeatureKind ka = m.kind[size_t(keep)], kb = m.kind[size_t(gone)];
    if (ka == kb) {
      m.positions[size_t(keep)] = (m.positions[size_t(keep)] + m.positions[size_t(gone)]) * 0.5;
    }
    else if (kb != FeatureKind::None) {
      m.positions[size_t(keep)] = m.positions[size_t(gone)];
      m.kind[size_t(keep)] = kb;
    }
  }
  for (const int f : changed) {
    m.unlink(f);
  }
  for (const int f : changed) {
    m.changed[size_t(f)] = 1;
    if (after[f].empty()) {
      m.dead[size_t(f)] = 1;
      m.faces[size_t(f)].clear();
      continue;
    }
    m.faces[size_t(f)] = after[f];
    m.link(f);
  }
  return true;
}

namespace {

/** One planned edit: a strip to collapse, or an insertion. */
struct Candidate {
  int length = 0;
  bool collapse = false;
  Strip strip;
  Edit edit;
};

}  // namespace

}  // namespace cleanup

DislocationCleanup cancel_dislocations(Extracted &x,
                                       const double scale,
                                       const int max_strip_quads,
                                       const double wanted_faces,
                                       const bool count_corners)
{
  using namespace cleanup;
  Mesh m;
  m.positions = x.mesh.positions;
  m.kind = x.feature_kind;
  m.vert_faces.resize(m.positions.size());
  for (int f = 0; f < x.mesh.faces_num(); f++) {
    const int *v = &x.mesh.corner_verts[size_t(x.mesh.face_offsets[size_t(f)])];
    m.faces.emplace_back(v, v + x.mesh.face_size(f));
  }
  m.dead.assign(m.faces.size(), 0);
  m.changed.assign(m.faces.size(), 0);
  for (int f = 0; f < int(m.faces.size()); f++) {
    m.link(f);
  }

  DislocationCleanup done;
  for (int pass = 0; pass < MAX_PASSES; pass++) {
    std::fill(m.changed.begin(), m.changed.end(), 0);
    std::vector<Candidate> candidates;
    const int faces_now = int(m.faces.size());
    for (int f = 0; f < faces_now; f++) {
      if (m.dead[size_t(f)] || m.faces[size_t(f)].size() == 4) {
        continue;
      }
      for (int c = 0; c < int(m.faces[size_t(f)].size()); c++) {
        Candidate cand;
        if (!trace_strip(m, f, c, max_strip_quads, cand.strip)) {
          continue;
        }
        cand.collapse = true;
        cand.length = int(cand.strip.rungs.size());
        Candidate split;
        split.length = cand.length;
        if (plan_strip_split(m, cand.strip, scale, split.edit)) {
          candidates.push_back(std::move(split));
        }
        candidates.push_back(std::move(cand));
      }
      std::vector<Edit> lines;
      plan_line_splits(m, f, max_strip_quads, lines);
      for (Edit &e : lines) {
        Candidate cand;
        cand.length = e.length;
        cand.edit = std::move(e);
        candidates.push_back(std::move(cand));
      }
    }
    /* Shortest first; a collapse before an insertion of the same length (it leaves fewer faces to
     * relax); ties in discovery order, so the result is deterministic. */
    std::stable_sort(
        candidates.begin(), candidates.end(), [](const Candidate &a, const Candidate &b) {
          return a.length != b.length ? a.length < b.length : a.collapse && !b.collapse;
        });
    auto count = [&]() { return double(count_corners ? m.live_corners : m.live_faces); };
    /* A collapse removes faces and an insertion adds them: an edit that moves the count away from
     * the target waits for the second round. */
    auto away = [&](const Candidate &cand) {
      return cand.collapse ? count() < wanted_faces : count() > wanted_faces;
    };
    int applied = 0;
    std::vector<const Candidate *> waiting;
    auto attempt = [&](const Candidate &cand) {
      if (cand.collapse) {
        if (try_collapse(m, cand.strip, scale)) {
          done.rows_removed++;
          applied++;
        }
      }
      else if (!m.stale(cand.edit.removed) && apply_if_better(m, cand.edit)) {
        done.rows_inserted++;
        applied++;
      }
    };
    for (const Candidate &cand : candidates) {
      if (away(cand)) {
        waiting.push_back(&cand);
        continue;
      }
      attempt(cand);
    }
    for (const Candidate *cand : waiting) {
      attempt(*cand);
    }
    if (applied == 0) {
      break;
    }
  }
  if (done.rows_removed + done.rows_inserted == 0) {
    return done;
  }

  /* Rebuild, keeping the vertices live faces use, in order of first use. */
  PolyMesh out;
  std::vector<FeatureKind> kind;
  std::vector<int> remap(m.positions.size(), -1);
  for (size_t f = 0; f < m.faces.size(); f++) {
    if (m.dead[f]) {
      continue;
    }
    std::vector<int> face = m.faces[f];
    for (int &v : face) {
      if (remap[size_t(v)] < 0) {
        remap[size_t(v)] = int(out.positions.size());
        out.positions.push_back(m.positions[size_t(v)]);
        kind.push_back(m.kind[size_t(v)]);
      }
      v = remap[size_t(v)];
    }
    out.add_face(face.data(), int(face.size()));
  }
  x.mesh = std::move(out);
  x.feature_kind = std::move(kind);
  return done;
}

}  // namespace blender::geometry::retopo
