/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * A BMesh operator's mapping slot iterates in insertion order ("Mapping Slot Order" in
 * `bmesh_operators.cc`), so an operator that creates elements while iterating one (extrude's side
 * faces) gives the same element order wherever the heap put the elements. Bevel frees the faces it
 * rebuilds in insertion order too, so the next operation's new faces land in the same slots.
 *
 * The contract is the same element order for the same history at other addresses, which is what
 * repeats an operation across processes. A BMesh's element order follows its own history by
 * design (a freed slot is the next one used), so the tests change only the addresses: live padding
 * elements, a different number per run, sit before the scene in every pool, hidden and outside
 * every operator's input. Each pool's order and every operator's history stay the same while
 * each scene element sits a different number of elements further on, low address bits included.
 * An operator that walked a pointer-keyed container on its output path would order its results
 * differently. (The first version freed a large grid in shuffled order instead; pools of one
 * chunk keep such a shuffled free list, so it compared different histories and failed on every
 * platform once run: 2.0.0's Windows and macOS GTests.)
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
 * `count` loose triangles, hidden, far from the scene: live elements in every pool (vertices,
 * edges, loops, faces) before the scene's, which no operator here takes as input.
 */
static void pad_pools(BMesh *bm, const int count)
{
  for (int i = 0; i < count; i++) {
    BMVert *verts[3];
    for (int j = 0; j < 3; j++) {
      const float co[3] = {float(i), float(j), 1000.0f};
      verts[j] = BM_vert_create(bm, co, nullptr, BM_CREATE_NOP);
    }
    BMFace *f = BM_face_create_verts(bm, verts, 3, nullptr, BM_CREATE_NOP, true);
    BM_face_select_set(bm, f, false);
    BM_elem_flag_enable(f, BM_ELEM_HIDDEN);
    for (int j = 0; j < 3; j++) {
      BM_elem_flag_enable(verts[j], BM_ELEM_HIDDEN);
    }
    BMIter iter;
    BMEdge *e;
    BM_ITER_ELEM (e, &iter, f, BM_EDGES_OF_FACE) {
      BM_elem_flag_enable(e, BM_ELEM_HIDDEN);
    }
  }
}

/** Padding per run: a different number of elements in front of the scene each time. */
static constexpr int padding_counts[] = {0, 1, 7, 33};

/**
 * Every scene face's vertex indices, in face order: the element order a mesh export would see.
 * The padding is left out: scene vertices are numbered in pool order among scene vertices.
 */
static Vector<int> face_corner_order(BMesh *bm)
{
  BMIter iter, liter;
  BMVert *v;
  int index = 0;
  BM_ITER_MESH (v, &iter, bm, BM_VERTS_OF_MESH) {
    BM_elem_index_set(v, BM_elem_flag_test(v, BM_ELEM_HIDDEN) ? -1 : index++); /* set_dirty! */
  }
  bm->elem_index_dirty |= BM_VERT; /* Not the mesh's own indices: the next user rebuilds them. */
  Vector<int> order;
  BMFace *f;
  BMLoop *l;
  BM_ITER_MESH (f, &iter, bm, BM_FACES_OF_MESH) {
    if (BM_elem_flag_test(f, BM_ELEM_HIDDEN)) {
      continue;
    }
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
  /* The same grid extruded with its elements at other addresses, the same history. */
  Vector<Vector<int>> results;
  for (const int padding : padding_counts) {
    BMesh *bm = new_bmesh();
    pad_pools(bm, padding);
    create_grid(bm, 5);
    BMO_op_callf(bm, BMO_FLAG_DEFAULTS, "extrude_face_region geom=%Hf", BM_ELEM_HIDDEN);
    results.append(face_corner_order(bm));
    BM_mesh_free(bm);
  }
  for (const int i : results.index_range().drop_front(1)) {
    EXPECT_EQ(results[i], results[0]) << "padding " << padding_counts[i];
  }
}

TEST_F(BMeshOperatorMapOrderTest, FreeOrderIgnoresElementAddresses)
{
  /* Bevel frees its rebuilt faces in the order it found them; the next operation's faces fill
   * those slots, so their order must not follow the addresses the faces had. */
  Vector<Vector<int>> results;
  for (const int padding : padding_counts) {
    BMesh *bm = new_bmesh();
    pad_pools(bm, padding);
    create_grid(bm, 12);
    BMO_op_callf(bm,
                 BMO_FLAG_DEFAULTS,
                 "bevel geom=%Hv offset=%f segments=%i profile=%f affect=%i",
                 BM_ELEM_HIDDEN,
                 0.02f,
                 2,
                 0.5f,
                 int(BEVEL_AFFECT_VERTICES));
    BMO_op_callf(bm, BMO_FLAG_DEFAULTS, "triangulate faces=%Hf", BM_ELEM_HIDDEN);
    results.append(face_corner_order(bm));
    BM_mesh_free(bm);
  }
  for (const int i : results.index_range().drop_front(1)) {
    EXPECT_EQ(results[i], results[0]) << "padding " << padding_counts[i];
  }
}

}  // namespace blender
