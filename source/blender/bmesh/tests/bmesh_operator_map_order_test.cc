/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * A BMesh operator's mapping slot iterates in insertion order ("Mapping Slot Order" in
 * `bmesh_operators.cc`), so an operator that creates elements while iterating one (extrude's side
 * faces) gives the same element order wherever the heap put the elements. Bevel frees the faces it
 * rebuilds in insertion order too, so the next operation's new faces land in the same slots.
 */

#include <algorithm>

#include "testing/testing.h"

#include "BLI_rand.hh"
#include "BLI_vector.hh"

#include "BKE_gtest_base.hh"

#include "bmesh.hh"

namespace blender {

class BMeshOperatorMapOrderTest : public bke::BlenderGTestBase {};

static BMesh *new_bmesh()
{
  BMeshCreateParams params{};
  params.use_toolflags = true;
  return BM_mesh_create(&bm_mesh_allocsize_default, &params);
}

static void create_grid(BMesh *bm, const int segments)
{
  BMO_op_callf(bm,
               BMO_FLAG_DEFAULTS,
               "create_grid x_segments=%i y_segments=%i size=%f",
               segments,
               segments,
               1.0f);
}

/**
 * Make and free a grid in a shuffled order, so the element pools' free lists hand the next
 * elements out at addresses in an order unrelated to their creation order.
 */
static void scramble_pools(BMesh *bm, const uint32_t seed)
{
  create_grid(bm, 24);
  RandomNumberGenerator rng(seed);
  Vector<BMFace *> faces;
  Vector<BMEdge *> edges;
  Vector<BMVert *> verts;
  BMIter iter;
  BMFace *f;
  BMEdge *e;
  BMVert *v;
  BM_ITER_MESH (f, &iter, bm, BM_FACES_OF_MESH) {
    faces.append(f);
  }
  BM_ITER_MESH (e, &iter, bm, BM_EDGES_OF_MESH) {
    edges.append(e);
  }
  BM_ITER_MESH (v, &iter, bm, BM_VERTS_OF_MESH) {
    verts.append(v);
  }
  rng.shuffle<BMFace *>(faces);
  rng.shuffle<BMEdge *>(edges);
  rng.shuffle<BMVert *>(verts);
  for (BMFace *face : faces) {
    BM_face_kill(bm, face);
  }
  for (BMEdge *edge : edges) {
    BM_edge_kill(bm, edge);
  }
  for (BMVert *vert : verts) {
    BM_vert_kill(bm, vert);
  }
}

/** Every face's vertex indices, in face order: the element order a mesh export would see. */
static Vector<int> face_corner_order(BMesh *bm)
{
  BM_mesh_elem_index_ensure(bm, BM_VERT);
  Vector<int> order;
  BMIter iter, liter;
  BMFace *f;
  BMLoop *l;
  BM_ITER_MESH (f, &iter, bm, BM_FACES_OF_MESH) {
    BM_ITER_ELEM (l, &liter, f, BM_LOOPS_OF_FACE) {
      order.append(BM_elem_index_get(l->v));
    }
    order.append(-1);
  }
  return order;
}

TEST_F(BMeshOperatorMapOrderTest, MappingIteratesInInsertionOrder)
{
  BMesh *bm = new_bmesh();
  create_grid(bm, 8);
  Vector<BMVert *> verts;
  BMIter iter;
  BMVert *v;
  BM_ITER_MESH (v, &iter, bm, BM_VERTS_OF_MESH) {
    verts.append(v);
  }
  /* Insert in an order unrelated to the addresses. */
  Vector<BMVert *> inserted = verts;
  RandomNumberGenerator rng(7);
  rng.shuffle<BMVert *>(inserted);

  BMOperator op;
  BMO_op_init(bm, &op, BMO_FLAG_DEFAULTS, "weld_verts");
  BMOpSlot *slot = BMO_slot_get(op.slots_in, "targetmap");
  for (const int i : inserted.index_range()) {
    BMO_slot_map_elem_insert(&op, slot, inserted[i], inserted[(i + 1) % inserted.size()]);
  }
  /* Inserting an existing key replaces its value and keeps its place. */
  BMO_slot_map_elem_insert(&op, slot, inserted[0], inserted[2]);
  EXPECT_EQ(BMO_slot_map_len(op.slots_in, "targetmap"), inserted.size());

  Vector<BMVert *> iterated;
  BMOIter oiter;
  BMO_ITER (v, &oiter, op.slots_in, "targetmap", BM_VERT) {
    iterated.append(v);
    const int i = int(iterated.size()) - 1;
    BMVert *expected = (i == 0) ? inserted[2] : inserted[(i + 1) % inserted.size()];
    EXPECT_EQ(BMO_iter_map_value_ptr(&oiter), expected);
  }
  EXPECT_EQ(iterated, inserted);
  EXPECT_EQ(Vector<void *>(BMO_slot_map_keys_ordered(slot)),
            Vector<void *>(inserted.begin(), inserted.end()));

  /* A key written straight into the hash comes after the ordered ones. */
  BMVert *extra = BM_vert_create(bm, nullptr, nullptr, BM_CREATE_NOP);
  BLI_ghash_insert(BMO_SLOT_AS_GHASH(slot), extra, inserted[0]);
  Span<void *> keys = BMO_slot_map_keys_ordered(slot);
  ASSERT_EQ(keys.size(), inserted.size() + 1);
  EXPECT_EQ(keys.last(), extra);

  BMO_op_finish(bm, &op);
  BM_mesh_free(bm);
}

TEST_F(BMeshOperatorMapOrderTest, ExtrudeOrderIgnoresElementAddresses)
{
  /* The same grid extruded in BMeshes whose elements sit at differently ordered addresses. */
  Vector<Vector<int>> results;
  for (const uint32_t seed : {0u, 1u, 2u, 3u}) {
    BMesh *bm = new_bmesh();
    if (seed != 0) {
      scramble_pools(bm, seed);
    }
    create_grid(bm, 5);
    BMO_op_callf(bm, BMO_FLAG_DEFAULTS, "extrude_face_region geom=%af");
    results.append(face_corner_order(bm));
    BM_mesh_free(bm);
  }
  for (const int i : results.index_range().drop_front(1)) {
    EXPECT_EQ(results[i], results[0]) << "pool scramble seed " << i;
  }
}

TEST_F(BMeshOperatorMapOrderTest, FreeOrderIgnoresElementAddresses)
{
  /* Bevel frees its rebuilt faces in the order it found them; the next operation's faces fill
   * those slots, so their order must not follow the addresses the faces had. */
  Vector<Vector<int>> results;
  for (const uint32_t seed : {0u, 1u, 2u, 3u}) {
    BMesh *bm = new_bmesh();
    if (seed != 0) {
      scramble_pools(bm, seed);
    }
    create_grid(bm, 12);
    BMO_op_callf(bm,
                 BMO_FLAG_DEFAULTS,
                 "bevel geom=%av offset=%f segments=%i profile=%f affect=%i",
                 0.02f,
                 2,
                 0.5f,
                 int(BEVEL_AFFECT_VERTICES));
    BMO_op_callf(bm, BMO_FLAG_DEFAULTS, "triangulate faces=%af");
    results.append(face_corner_order(bm));
    BM_mesh_free(bm);
  }
  for (const int i : results.index_range().drop_front(1)) {
    EXPECT_EQ(results[i], results[0]) << "pool scramble seed " << i;
  }
}

}  // namespace blender
