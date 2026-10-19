/* SPDX-FileCopyrightText: 2023 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup bmesh
 *
 * The BMLog is an interface for storing undo/redo steps as a BMesh is
 * modified. It only stores changes to the BMesh, not full copies.
 *
 * Currently it supports the following types of changes:
 *
 * - Adding and removing vertices
 * - Adding and removing faces
 * - Moving vertices
 * - Setting vertex paint-mask values
 * - Setting vertex hflags
 */

#include <atomic>
#include <memory>
#include <limits>
#include <cstring>

#include "MEM_guardedalloc.h"

#include "BLI_listbase.h"
#include "BLI_map.hh"
#include "BLI_math_vector.h"
#include "BLI_pool.hh"
#include "BLI_set.hh"
#include "BLI_utildefines.h"

#include "BKE_customdata.hh"

#include "bmesh.hh"
#include "bmesh_log.hh"

#include "range_tree.h"

#include "BLI_strict_flags.h" /* IWYU pragma: keep. Keep last. */

namespace blender {

struct BMLogFace;
struct BMLogVert;

struct BMLogReplayValues {
  Vector<std::unique_ptr<BMLogVert>, 0> verts;
  Vector<std::unique_ptr<BMLogFace>, 0> faces;
};

struct BMLogEntry {
  BMLogEntry *next, *prev;

  /** Stable even while this entry has no live BMLog owner. */
  uint64_t epoch;
  uint64_t identity;

  /* The following members map from an element ID to one of the log types above. */

  /** Elements that were in the previous entry, but have been deleted. */
  Map<uint, BMLogVert *, 0> deleted_verts;
  Map<uint, BMLogFace *, 0> deleted_faces;

  /** Elements that were not in the previous entry, but are in the result of this entry. */
  Map<uint, BMLogVert *, 0> added_verts;
  Map<uint, BMLogFace *, 0> added_faces;

  /** Vertices whose coordinates, mask value, or hflag have changed. */
  Map<uint, BMLogVert *, 0> modified_verts;
  Map<uint, BMLogFace *, 0> modified_faces;

  /* Private replay maps can own independently staged values. Ordinary entries leave this
   * empty; original pool allocations remain owned and are destroyed through the old path. */
  std::unique_ptr<BMLogReplayValues> replay_values;

  Pool<BMLogVert> vert_pool;
  Pool<BMLogFace> face_pool;

  Vector<BMLogVert *, 0> allocated_verts;
  Vector<BMLogFace *, 0> allocated_faces;

  /**
   * This is only needed for dropping BMLogEntries while still in
   * dynamic-topology mode, as that should release vert/face IDs
   * back to the BMLog but no BMLog pointer is available at that time.
   *
   * This field is not guaranteed to be valid, any use of it should
   * check for nullptr.
   */
  BMLog *log;
};

struct BMLog {
  uint64_t epoch;

  /** Tree of free IDs */
  RangeTreeUInt *unused_ids;

  /**
   * Mapping from unique IDs to vertices and faces
   *
   * Each vertex and face in the log gets a unique `uint`
   * assigned. That ID is taken from the set managed by the
   * unused_ids range tree.
   *
   * The ID is needed because element pointers will change as they
   * are created and deleted.
   */
  Map<uint, BMElem *, 0> id_to_elem;
  Map<BMElem *, uint, 0> elem_to_id;

  /** All #BMLogEntrys, ordered from earliest to most recent. */
  ListBaseT<BMLogEntry> entries;

  /**
   * The current log entry from entries list
   *
   * If null, then the original mesh from before any of the log
   * entries is current (i.e. there is nothing left to undo.)
   *
   * If equal to the last entry in the entries list, then all log
   * entries have been applied (i.e. there is nothing left to redo.)
   */
  BMLogEntry *current_entry;
};

struct BMLogVert {
  float3 position;
  float3 normal;
  char hflag;
  float mask;
};

struct BMLogFace {
  std::array<uint, 3> v_ids;
  char hflag;
};

struct BMLogSnapshot {
  struct Face {
    uint id;
    std::array<uint, 3> vertex_ids;
  };

  uint64_t epoch;
  uint64_t cursor_id;
  Vector<uint, 0> vertex_ids;
  Vector<Face, 0> faces;
};

struct BMLogReplayEntry {
  BMLogEntry *target = nullptr;
  uint64_t identity = 0;
  Map<uint, BMLogVert *, 0> deleted_verts, added_verts, modified_verts;
  Map<uint, BMLogFace *, 0> deleted_faces, added_faces, modified_faces;
  std::unique_ptr<BMLogReplayValues> values;
};

struct BMLogSnapshotRestore {
  /** The prepared log remains detached until every old runtime is ready to be replaced. */
  BMLog *log;
  Vector<BMLogEntry *, 0> entries;
  const BMLog *replay_owner = nullptr;
  Vector<BMLogReplayEntry, 0> replay;
};

/* IDs are process-local identities, not element IDs or serialized file data. Sharing one counter
 * makes every entry identity strictly increasing, including across log reconstruction. */
static uint64_t bm_log_identity_create()
{
  static std::atomic<uint64_t> next_identity{1};
  const uint64_t identity = next_identity.fetch_add(1, std::memory_order_relaxed);
  BLI_assert(identity != 0);
  return identity;
}

static void bm_log_error_set(const char **r_error, const char *error)
{
  if (r_error) {
    *r_error = error;
  }
}

/* -------------------------------------------------------------------- */
/** \name Get/Set Element IDs
 * \{ */

/* Get the vertex's unique ID from the log */
static uint bm_log_vert_id_get(BMLog *log, BMVert *v)
{
  return log->elem_to_id.lookup(reinterpret_cast<BMElem *>(v));
}

/* Set the vertex's unique ID in the log */
static void bm_log_vert_id_set(BMLog *log, BMVert *v, const uint id)
{
  log->id_to_elem.add_overwrite(id, reinterpret_cast<BMElem *>(v));
  log->elem_to_id.add_overwrite(reinterpret_cast<BMElem *>(v), id);
}

/* Get a vertex from its unique ID */
static BMVert *bm_log_vert_from_id(BMLog *log, const uint id)
{
  return reinterpret_cast<BMVert *>(log->id_to_elem.lookup(id));
}

/* Get the face's unique ID from the log */
static uint bm_log_face_id_get(BMLog *log, BMFace *f)
{
  return log->elem_to_id.lookup(reinterpret_cast<BMElem *>(f));
}

/* Set the face's unique ID in the log */
static void bm_log_face_id_set(BMLog *log, BMFace *f, const uint id)
{
  log->id_to_elem.add_overwrite(id, reinterpret_cast<BMElem *>(f));
  log->elem_to_id.add_overwrite(reinterpret_cast<BMElem *>(f), id);
}

/* Get a face from its unique ID */
static BMFace *bm_log_face_from_id(BMLog *log, const uint id)
{
  return reinterpret_cast<BMFace *>(log->id_to_elem.lookup(id));
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name BMLogVert / BMLogFace
 * \{ */

/* Get a vertex's paint-mask value
 *
 * Returns zero if no paint-mask layer is present */
static float vert_mask_get(BMVert *v, const int cd_vert_mask_offset)
{
  if (cd_vert_mask_offset != -1) {
    return BM_ELEM_CD_GET_FLOAT(v, cd_vert_mask_offset);
  }
  return 0.0f;
}

/* Set a vertex's paint-mask value
 *
 * Has no effect is no paint-mask layer is present */
static void vert_mask_set(BMVert *v, const float new_mask, const int cd_vert_mask_offset)
{
  if (cd_vert_mask_offset != -1) {
    BM_ELEM_CD_SET_FLOAT(v, cd_vert_mask_offset, new_mask);
  }
}

/* Update a BMLogVert with data from a BMVert */
static void bm_log_vert_bmvert_copy(BMLogVert *lv, BMVert *v, const int cd_vert_mask_offset)
{
  copy_v3_v3(lv->position, v->co);
  copy_v3_v3(lv->normal, v->no);
  lv->mask = vert_mask_get(v, cd_vert_mask_offset);
  lv->hflag = v->head.hflag;
}

/* Allocate and initialize a BMLogVert */
static BMLogVert *bm_log_vert_alloc(BMLog *log, BMVert *v, const int cd_vert_mask_offset)
{
  BMLogEntry *entry = log->current_entry;
  BMLogVert *lv = &entry->vert_pool.construct();
  entry->allocated_verts.append(lv);

  bm_log_vert_bmvert_copy(lv, v, cd_vert_mask_offset);

  return lv;
}

/* Allocate and initialize a BMLogFace */
static BMLogFace *bm_log_face_alloc(BMLog *log, BMFace *f)
{
  BMLogEntry *entry = log->current_entry;
  BMLogFace *lf = &entry->face_pool.construct();
  entry->allocated_faces.append(lf);
  BMVert *v[3];

  BLI_assert(f->len == 3);

  // BM_iter_as_array(nullptr, BM_VERTS_OF_FACE, f, (void **)v, 3);
  BM_face_as_array_vert_tri(f, v);

  lf->v_ids[0] = bm_log_vert_id_get(log, v[0]);
  lf->v_ids[1] = bm_log_vert_id_get(log, v[1]);
  lf->v_ids[2] = bm_log_vert_id_get(log, v[2]);

  lf->hflag = f->head.hflag;
  return lf;
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Helpers for Undo/Redo
 * \{ */

static void bm_log_verts_unmake(BMesh *bm, BMLog *log, const Map<uint, BMLogVert *, 0> &verts)
{
  const int cd_vert_mask_offset = CustomData_get_offset_named(
      &bm->vdata, CD_PROP_FLOAT, ".sculpt_mask");

  for (const auto item : verts.items()) {
    BMVert *v = bm_log_vert_from_id(log, item.key);

    /* Ensure the log has the final values of the vertex before
     * deleting it */
    bm_log_vert_bmvert_copy(item.value, v, cd_vert_mask_offset);

    BM_vert_kill(bm, v);
  }
}

static void bm_log_faces_unmake(BMesh *bm, BMLog *log, const Map<uint, BMLogFace *, 0> &faces)
{
  for (const uint id : faces.keys()) {
    BMFace *f = bm_log_face_from_id(log, id);
    std::array<BMEdge *, 3> e_tri;

    BMLoop *l_iter = BM_FACE_FIRST_LOOP(f);
    for (uint i = 0; i < e_tri.size(); i++, l_iter = l_iter->next) {
      e_tri[i] = l_iter->e;
    }

    /* Remove any unused edges */
    BM_face_kill(bm, f);
    for (uint i = 0; i < e_tri.size(); i++) {
      if (BM_edge_is_wire(e_tri[i])) {
        BM_edge_kill(bm, e_tri[i]);
      }
    }
  }
}

static void bm_log_verts_restore(BMesh *bm, BMLog *log, const Map<uint, BMLogVert *, 0> &verts)
{
  const int cd_vert_mask_offset = CustomData_get_offset_named(
      &bm->vdata, CD_PROP_FLOAT, ".sculpt_mask");

  for (const auto item : verts.items()) {
    BMLogVert *lv = item.value;
    BMVert *v = BM_vert_create(bm, lv->position, nullptr, BM_CREATE_NOP);
    vert_mask_set(v, lv->mask, cd_vert_mask_offset);
    v->head.hflag = lv->hflag;
    copy_v3_v3(v->no, lv->normal);
    bm_log_vert_id_set(log, v, item.key);
  }
}

static void bm_log_faces_restore(BMesh *bm, BMLog *log, const Map<uint, BMLogFace *, 0> &faces)
{
  const int cd_face_sets = CustomData_get_offset_named(
      &bm->pdata, CD_PROP_INT32, ".sculpt_face_set");

  for (const auto item : faces.items()) {
    BMLogFace *lf = item.value;
    BMVert *v[3] = {
        bm_log_vert_from_id(log, lf->v_ids[0]),
        bm_log_vert_from_id(log, lf->v_ids[1]),
        bm_log_vert_from_id(log, lf->v_ids[2]),
    };

    BMFace *f = BM_face_create_verts(bm, v, 3, nullptr, BM_CREATE_NOP, true);
    f->head.hflag = lf->hflag;
    bm_log_face_id_set(log, f, item.key);

    /* Ensure face sets have valid values.  Fixes #80174. */
    if (cd_face_sets != -1) {
      BM_ELEM_CD_SET_INT(f, cd_face_sets, 1);
    }
  }
}

static void bm_log_vert_values_swap(BMesh *bm, BMLog *log, const Map<uint, BMLogVert *, 0> &verts)
{
  const int cd_vert_mask_offset = CustomData_get_offset_named(
      &bm->vdata, CD_PROP_FLOAT, ".sculpt_mask");

  for (const auto item : verts.items()) {
    BMLogVert *lv = item.value;
    BMVert *v = bm_log_vert_from_id(log, item.key);

    swap_v3_v3(v->co, lv->position);
    swap_v3_v3(v->no, lv->normal);
    std::swap(v->head.hflag, lv->hflag);
    float mask = lv->mask;
    lv->mask = vert_mask_get(v, cd_vert_mask_offset);
    vert_mask_set(v, mask, cd_vert_mask_offset);
  }
}

static void bm_log_face_values_swap(BMLog *log, const Map<uint, BMLogFace *, 0> &faces)
{

  for (const auto item : faces.items()) {
    BMLogFace *lf = item.value;
    BMFace *f = bm_log_face_from_id(log, item.key);

    std::swap(f->head.hflag, lf->hflag);
  }
}

/* Assign unique IDs to all vertices and faces already in the BMesh */
static void bm_log_assign_ids(BMesh *bm, BMLog *log)
{
  BMIter iter;

  BMVert *v;
  /* Generate vertex IDs */
  BM_ITER_MESH (v, &iter, bm, BM_VERTS_OF_MESH) {
    uint id = range_tree_uint_take_any(log->unused_ids);
    bm_log_vert_id_set(log, v, id);
  }

  BMFace *f;
  /* Generate face IDs */
  BM_ITER_MESH (f, &iter, bm, BM_FACES_OF_MESH) {
    uint id = range_tree_uint_take_any(log->unused_ids);
    bm_log_face_id_set(log, f, id);
  }
}

/* Allocate an empty log entry */
static BMLogEntry *bm_log_entry_create()
{
  BMLogEntry *entry = MEM_new<BMLogEntry>(__func__);

  return entry;
}

/* Free the data in a log entry
 *
 * NOTE: does not free the log entry itself. */
static void bm_log_entry_free(BMLogEntry *entry)
{
  BLI_assert(entry->vert_pool.size() == entry->allocated_verts.size());
  BLI_assert(entry->face_pool.size() == entry->allocated_faces.size());

  for (BMLogVert *log_vert : entry->allocated_verts) {
    entry->vert_pool.destruct(*log_vert);
  }

  for (BMLogFace *log_face : entry->allocated_faces) {
    entry->face_pool.destruct(*log_face);
  }

  BLI_assert(entry->vert_pool.is_empty());
  BLI_assert(entry->face_pool.is_empty());
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Public API
 * \{ */

BMLog *BM_log_create(BMesh *bm)
{
  BMLog *log = MEM_new<BMLog>(__func__);
  log->epoch = bm_log_identity_create();
  const uint reserve_num = uint(bm->totvert + bm->totface);

  log->unused_ids = range_tree_uint_alloc(0, uint(-1));
  log->id_to_elem.reserve(reserve_num);
  log->elem_to_id.reserve(reserve_num);

  /* Assign IDs to all existing vertices and faces */
  bm_log_assign_ids(bm, log);

  return log;
}

void BM_log_cleanup_entry(BMLogEntry *entry)
{
  BMLog *log = entry->log;

  if (log) {
    /* Take all used IDs */
    for (const uint id : entry->deleted_verts.keys()) {
      range_tree_uint_retake(log->unused_ids, id);
    }
    for (const uint id : entry->deleted_faces.keys()) {
      range_tree_uint_retake(log->unused_ids, id);
    }
    for (const uint id : entry->added_verts.keys()) {
      range_tree_uint_retake(log->unused_ids, id);
    }
    for (const uint id : entry->added_faces.keys()) {
      range_tree_uint_retake(log->unused_ids, id);
    }
    for (const uint id : entry->modified_verts.keys()) {
      range_tree_uint_retake(log->unused_ids, id);
    }
    for (const uint id : entry->modified_faces.keys()) {
      range_tree_uint_retake(log->unused_ids, id);
    }

    /* delete entries to avoid releasing ids in node cleanup */
    entry->deleted_verts.clear();
    entry->deleted_faces.clear();
    entry->added_verts.clear();
    entry->added_faces.clear();
    entry->modified_verts.clear();

    /* Is this last one needed? */
    entry->modified_faces.clear();
  }
}

BMLog *BM_log_from_existing_entries_create(BMesh *bm, BMLogEntry *entry)
{
  BMLog *log = BM_log_create(bm);
  log->epoch = entry->epoch;

  if (entry->prev) {
    log->current_entry = entry;
  }
  else {
    log->current_entry = nullptr;
  }

  /* Let BMLog manage the entry list again */
  log->entries.first = log->entries.last = entry;

  {
    while (entry->prev) {
      entry = entry->prev;
      log->entries.first = entry;
    }
    entry = static_cast<BMLogEntry *>(log->entries.last);
    while (entry->next) {
      entry = entry->next;
      log->entries.last = entry;
    }
  }

  for (entry = static_cast<BMLogEntry *>(log->entries.first); entry; entry = entry->next) {
    entry->log = log;

    /* Take all used IDs */
    for (const uint id : entry->deleted_verts.keys()) {
      range_tree_uint_retake(log->unused_ids, id);
    }
    for (const uint id : entry->deleted_faces.keys()) {
      range_tree_uint_retake(log->unused_ids, id);
    }
    for (const uint id : entry->added_verts.keys()) {
      range_tree_uint_retake(log->unused_ids, id);
    }
    for (const uint id : entry->added_faces.keys()) {
      range_tree_uint_retake(log->unused_ids, id);
    }
    for (const uint id : entry->modified_verts.keys()) {
      range_tree_uint_retake(log->unused_ids, id);
    }
    for (const uint id : entry->modified_faces.keys()) {
      range_tree_uint_retake(log->unused_ids, id);
    }
  }

  return log;
}

BMLogSnapshot *BM_log_snapshot_create(BMesh *bm, const BMLog *log, const char **r_error)
{
  bm_log_error_set(r_error, nullptr);
  if (!log || !log->epoch) {
    bm_log_error_set(r_error, "The BMesh log is not attached to a valid history epoch");
    return nullptr;
  }
  if (log->current_entry && log->current_entry->epoch != log->epoch) {
    bm_log_error_set(r_error, "The current BMesh log entry belongs to another history epoch");
    return nullptr;
  }

  BMLogSnapshot *snapshot = MEM_new<BMLogSnapshot>(__func__);
  snapshot->epoch = log->epoch;
  snapshot->cursor_id = log->current_entry ? log->current_entry->identity : 0;
  snapshot->vertex_ids.reserve(bm->totvert);
  snapshot->faces.reserve(bm->totface);
  Set<uint> live_ids;
  live_ids.reserve(int64_t(bm->totvert) + bm->totface);

  const auto live_id = [&](BMElem *elem) -> const uint * {
    const uint *id = log->elem_to_id.lookup_ptr(elem);
    if (!id) {
      return nullptr;
    }
    BMElem *const *mapped_elem = log->id_to_elem.lookup_ptr(*id);
    if (!mapped_elem || *mapped_elem != elem || !live_ids.add(*id)) {
      return nullptr;
    }
    return id;
  };
  const auto fail = [&](const char *error) -> BMLogSnapshot * {
    bm_log_error_set(r_error, error);
    BM_log_snapshot_free(snapshot);
    return nullptr;
  };

  BMIter iter;
  BMVert *vert;
  BM_ITER_MESH (vert, &iter, bm, BM_VERTS_OF_MESH) {
    const uint *id = live_id(reinterpret_cast<BMElem *>(vert));
    if (!id) {
      return fail("A live BMesh vertex has no unique log identity");
    }
    snapshot->vertex_ids.append(*id);
  }

  BMFace *face;
  BM_ITER_MESH (face, &iter, bm, BM_FACES_OF_MESH) {
    if (face->len != 3) {
      return fail("A BMesh log snapshot requires triangle faces");
    }
    const uint *id = live_id(reinterpret_cast<BMElem *>(face));
    if (!id) {
      return fail("A live BMesh face has no unique log identity");
    }
    BMVert *verts[3];
    BM_face_as_array_vert_tri(face, verts);
    BMLogSnapshot::Face snapshot_face;
    snapshot_face.id = *id;
    for (int i = 0; i < 3; i++) {
      snapshot_face.vertex_ids[i] = log->elem_to_id.lookup(reinterpret_cast<BMElem *>(verts[i]));
    }
    snapshot->faces.append(snapshot_face);
  }
  return snapshot;
}

void BM_log_snapshot_free(BMLogSnapshot *snapshot)
{
  MEM_delete(snapshot);
}

size_t BM_log_snapshot_memory_size(const BMLogSnapshot *snapshot)
{
  return sizeof(*snapshot) + size_t(snapshot->vertex_ids.capacity()) * sizeof(uint) +
         size_t(snapshot->faces.capacity()) * sizeof(BMLogSnapshot::Face);
}

bool BM_log_snapshot_equals(const BMLogSnapshot *a, const BMLogSnapshot *b)
{
  if (a == b) {
    return true;
  }
  if (!a || !b || a->epoch != b->epoch || a->cursor_id != b->cursor_id ||
      a->vertex_ids.as_span() != b->vertex_ids.as_span() || a->faces.size() != b->faces.size())
  {
    return false;
  }
  for (const int64_t index : a->faces.index_range()) {
    if (a->faces[index].id != b->faces[index].id ||
        a->faces[index].vertex_ids != b->faces[index].vertex_ids)
    {
      return false;
    }
  }
  return true;
}

uint64_t BM_log_snapshot_epoch(const BMLogSnapshot *snapshot)
{
  return snapshot->epoch;
}

uint64_t BM_log_snapshot_cursor_id(const BMLogSnapshot *snapshot)
{
  return snapshot->cursor_id;
}

uint64_t BM_log_epoch(const BMLog *log)
{
  return log->epoch;
}

uint64_t BM_log_cursor_id(const BMLog *log)
{
  return log->current_entry ? log->current_entry->identity : 0;
}

uint64_t BM_log_entry_epoch(const BMLogEntry *entry)
{
  return entry->epoch;
}

uint64_t BM_log_entry_identity(const BMLogEntry *entry)
{
  return entry->identity;
}

static bool bm_log_snapshot_entries_validate(const uint64_t epoch,
                                             const Span<BMLogEntry *> entries,
                                             const char **r_error)
{
  uint64_t previous_id = 0;
  for (const int64_t i : entries.index_range()) {
    const BMLogEntry *entry = entries[i];
    if (!entry || entry->epoch != epoch || entry->identity <= previous_id) {
      bm_log_error_set(r_error, "The retained BMesh history has an invalid epoch or entry order");
      return false;
    }
    const BMLogEntry *previous = i ? entries[i - 1] : nullptr;
    const BMLogEntry *next = i + 1 < entries.size() ? entries[i + 1] : nullptr;
    if (entry->prev != previous || entry->next != next) {
      bm_log_error_set(r_error,
                       "The retained BMesh history is not a complete surviving entry chain");
      return false;
    }
    previous_id = entry->identity;
  }
  return true;
}

static void bm_log_snapshot_reserve_entry_ids(BMLog *log, const BMLogEntry *entry)
{
  const auto reserve_keys = [&](const auto &map) {
    for (const uint id : map.keys()) {
      range_tree_uint_retake(log->unused_ids, id);
    }
  };
  const auto reserve_faces = [&](const auto &map) {
    reserve_keys(map);
    for (const BMLogFace *face : map.values()) {
      for (const uint id : face->v_ids) {
        range_tree_uint_retake(log->unused_ids, id);
      }
    }
  };
  reserve_keys(entry->deleted_verts);
  reserve_keys(entry->added_verts);
  reserve_keys(entry->modified_verts);
  reserve_faces(entry->deleted_faces);
  reserve_faces(entry->added_faces);
  reserve_faces(entry->modified_faces);
}

#ifdef WITH_GTESTS
static int checkpoint_restore_failure_stage = 0;
void BM_log_snapshot_restore_test_fail_stage(const int stage)
{
  checkpoint_restore_failure_stage = stage;
}
static bool checkpoint_restore_test_fail(const int stage)
{
  if (checkpoint_restore_failure_stage != stage) { return false; }
  checkpoint_restore_failure_stage = 0;
  return true;
}
#else
static bool checkpoint_restore_test_fail(const int) { return false; }
#endif

BMLogSnapshotRestore *BM_log_snapshot_restore_prepare(BMesh *bm,
                                                      const BMLogSnapshot *snapshot,
                                                      const Span<BMLogEntry *> entries,
                                                      const BMLog *expected_old_log,
                                                      const char **r_error)
{
  bm_log_error_set(r_error, nullptr);
  if (!snapshot || !snapshot->epoch) {
    bm_log_error_set(r_error, "The BMesh log snapshot has no history epoch");
    return nullptr;
  }
  if (bm->totvert != snapshot->vertex_ids.size() || bm->totface != snapshot->faces.size()) {
    bm_log_error_set(r_error, "The reconstructed BMesh element counts differ from the snapshot");
    return nullptr;
  }
  if (!bm_log_snapshot_entries_validate(snapshot->epoch, entries, r_error)) {
    return nullptr;
  }

  bool has_owned_entries = false;
  for (const BMLogEntry *entry : entries) {
    if (entry->log) {
      if (entry->log != expected_old_log) {
        bm_log_error_set(r_error, "A retained BMesh entry belongs to an unexpected live log");
        return nullptr;
      }
      has_owned_entries = true;
    }
  }
  if (has_owned_entries && (expected_old_log->epoch != snapshot->epoch ||
                            expected_old_log->entries.first != entries.first() ||
                            expected_old_log->entries.last != entries.last()))
  {
    bm_log_error_set(r_error, "The expected BMesh owner does not contain the retained history");
    return nullptr;
  }

  std::unique_ptr<BMLog, decltype(&BM_log_free)> owned_log(
      MEM_new<BMLog>(__func__), BM_log_free);
  BMLog *log = owned_log.get();
  log->unused_ids = nullptr; log->entries = {}; log->current_entry = nullptr;
  log->epoch = snapshot->epoch;
  log->unused_ids = range_tree_uint_alloc(0, uint(-1));
  const int64_t element_count = int64_t(bm->totvert) + bm->totface;
  log->id_to_elem.reserve(element_count);
  log->elem_to_id.reserve(element_count);
  if (checkpoint_restore_test_fail(1)) {
    bm_log_error_set(r_error, "Injected detached-log staging refusal"); return nullptr;
  }
  const auto fail = [&](const char *error) -> BMLogSnapshotRestore * {
    bm_log_error_set(r_error, error);
    return nullptr;
  };

  BMIter iter;
  int index;
  BMVert *vert;
  BM_ITER_MESH_INDEX (vert, &iter, bm, BM_VERTS_OF_MESH, index) {
    const uint id = snapshot->vertex_ids[index];
    if (!range_tree_uint_retake(log->unused_ids, id)) {
      return fail("The BMesh snapshot repeats a live element identity");
    }
    bm_log_vert_id_set(log, vert, id);
  }
  BMFace *face;
  BM_ITER_MESH_INDEX (face, &iter, bm, BM_FACES_OF_MESH, index) {
    if (face->len != 3) {
      return fail("The reconstructed BMesh contains a non-triangle face");
    }
    const BMLogSnapshot::Face &snapshot_face = snapshot->faces[index];
    BMVert *verts[3];
    BM_face_as_array_vert_tri(face, verts);
    for (int i = 0; i < 3; i++) {
      if (bm_log_vert_id_get(log, verts[i]) != snapshot_face.vertex_ids[i]) {
        return fail(
            "The reconstructed BMesh face order or vertex order differs from the snapshot");
      }
    }
    if (!range_tree_uint_retake(log->unused_ids, snapshot_face.id)) {
      return fail("The BMesh snapshot repeats a live element identity");
    }
    bm_log_face_id_set(log, face, snapshot_face.id);
  }

  for (const BMLogEntry *entry : entries) {
    bm_log_snapshot_reserve_entry_ids(log, entry);
  }
  for (BMLogEntry *entry : entries) {
    if (entry->identity <= snapshot->cursor_id) {
      log->current_entry = entry;
    }
  }
  std::unique_ptr<BMLogSnapshotRestore, decltype(&BM_log_snapshot_restore_discard)> restore(
      MEM_new<BMLogSnapshotRestore>(__func__), BM_log_snapshot_restore_discard);
  restore->log = owned_log.release();
  restore->entries.extend(entries);
  if (checkpoint_restore_test_fail(2)) {
    bm_log_error_set(r_error, "Injected prepared-log staging refusal"); return nullptr;
  }
  return restore.release();
}

BMLogSnapshotRestore *BM_log_snapshot_restore_prepare_bounded(
    BMesh *bm, const BMLogSnapshot *snapshot, const BMLog *expected_old_log,
    const size_t limit, size_t &used, const char **r_error)
{
  bm_log_error_set(r_error, nullptr);
  if (!bm || !snapshot || !expected_old_log || snapshot->epoch != expected_old_log->epoch ||
      bm->totvert < 0 || bm->totface < 0) {
    bm_log_error_set(r_error, "Finite history replay requires the exact live epoch owner"); return nullptr;
  }
  size_t count = size_t(bm->totvert) + size_t(bm->totface), entries_count = 0;
  const BMLogEntry *previous = nullptr;
  for (const BMLogEntry &entry : expected_old_log->entries) {
    if (entry.prev != previous || entry.log != expected_old_log || entry.epoch != snapshot->epoch ||
        (previous && entry.identity <= previous->identity) || entries_count == SIZE_MAX) {
      bm_log_error_set(r_error, "Finite history replay has an inconsistent live chain"); return nullptr;
    }
    entries_count++;
    for (const auto *map : {&entry.deleted_verts, &entry.added_verts, &entry.modified_verts}) {
      for (const BMLogVert *value : map->values()) {
        if (!value) { bm_log_error_set(r_error, "History replay has a null vertex buffer"); return nullptr; }
      }
    }
    for (const auto *map : {&entry.deleted_faces, &entry.added_faces, &entry.modified_faces}) {
      if (uint64_t(map->size()) > SIZE_MAX / 4) {
        bm_log_error_set(r_error, "History face reservation overflows"); return nullptr;
      }
      for (const BMLogFace *value : map->values()) {
        if (!value) { bm_log_error_set(r_error, "History replay has a null face buffer"); return nullptr; }
      }
    }
    /* Four reservations per face value include its ID and all three vertex references.
     * 512 bytes per reservation covers range-tree nodes, hash tables, duplicate map values,
     * staging vectors and both old/new table overlap. Empty pools/control have 64 KiB reserve. */
    for (const size_t n : {size_t(entry.deleted_verts.size()), size_t(entry.added_verts.size()),
                           size_t(entry.modified_verts.size()), size_t(entry.deleted_faces.size()) * 4,
                           size_t(entry.added_faces.size()) * 4, size_t(entry.modified_faces.size()) * 4}) {
      if (n > SIZE_MAX - count) {
        bm_log_error_set(r_error, "Finite history replay size overflows"); return nullptr;
      }
      count += n;
    }
    previous = &entry;
  }
  if (previous != expected_old_log->entries.last || entries_count > INT64_MAX || count > (SIZE_MAX - 65536) / 512 ||
      entries_count > (SIZE_MAX - 65536 - count * 512) / (sizeof(BMLogReplayEntry) * 4 + 512)) {
    bm_log_error_set(r_error, "Finite history replay size overflows"); return nullptr;
  }
  const size_t charge = 65536 + count * 512 + entries_count * (sizeof(BMLogReplayEntry) * 4 + 512);
  if (used > limit || charge > limit - used) {
    bm_log_error_set(r_error, "History replay exceeds finite preallocation admission"); return nullptr;
  }
  used += charge;
  Vector<BMLogEntry *, 0> entries;
  entries.reserve(int64_t(entries_count));
  for (const BMLogEntry &entry : expected_old_log->entries) {
    entries.append(const_cast<BMLogEntry *>(&entry));
  }
  std::unique_ptr<BMLogSnapshotRestore, decltype(&BM_log_snapshot_restore_discard)> restore(
      BM_log_snapshot_restore_prepare(bm, snapshot, entries.as_span(), expected_old_log, r_error),
      BM_log_snapshot_restore_discard);
  if (!restore) { return nullptr; }
  restore->replay_owner = expected_old_log;
  restore->replay.reserve(entries.size());
  for (BMLogEntry *entry : entries) {
    BMLogReplayEntry copy;
    copy.target = entry; copy.identity = entry->identity;
    copy.values = std::make_unique<BMLogReplayValues>();
    Map<const BMLogVert *, BMLogVert *, 0> vert_aliases;
    Map<const BMLogFace *, BMLogFace *, 0> face_aliases;
    auto verts = [&](const auto &source, auto &destination) {
      destination.reserve(source.size());
      for (const auto &item : source.items()) {
        BMLogVert *value = vert_aliases.lookup_default(item.value, nullptr);
        if (!value) {
          auto owned = std::make_unique<BMLogVert>(*item.value); value = owned.get();
          vert_aliases.add_new(item.value, value); copy.values->verts.append(std::move(owned));
        }
        destination.add_new(item.key, value);
      }
    };
    auto faces = [&](const auto &source, auto &destination) {
      destination.reserve(source.size());
      for (const auto &item : source.items()) {
        BMLogFace *value = face_aliases.lookup_default(item.value, nullptr);
        if (!value) {
          auto owned = std::make_unique<BMLogFace>(*item.value); value = owned.get();
          face_aliases.add_new(item.value, value); copy.values->faces.append(std::move(owned));
        }
        destination.add_new(item.key, value);
      }
    };
    verts(entry->deleted_verts, copy.deleted_verts); verts(entry->added_verts, copy.added_verts);
    verts(entry->modified_verts, copy.modified_verts);
    faces(entry->deleted_faces, copy.deleted_faces); faces(entry->added_faces, copy.added_faces);
    faces(entry->modified_faces, copy.modified_faces);
    restore->replay.append(std::move(copy));
    if (checkpoint_restore_test_fail(3)) {
      bm_log_error_set(r_error, "Injected retained-map staging refusal"); return nullptr;
    }
  }
  return restore.release();
}

bool BM_log_snapshot_restore_replay_validate(const BMLogSnapshotRestore *restore,
                                              const BMLog *live_log)
{
  if (!restore || !live_log || restore->replay_owner != live_log ||
      restore->log->epoch != live_log->epoch || restore->entries.size() != restore->replay.size()) {
    return false;
  }
  /* Traverse only the live owner's chain before dereferencing any saved entry pointer. */
  int64_t i = 0;
  for (const BMLogEntry &entry : live_log->entries) {
    if (i >= restore->replay.size() || &entry != restore->replay[i].target ||
        entry.identity != restore->replay[i].identity || entry.log != live_log) { return false; }
    i++;
  }
  return i == restore->replay.size() &&
         bm_log_snapshot_entries_validate(live_log->epoch, restore->entries, nullptr);
}

bool BM_log_snapshot_restore_replay_equals(const BMLogSnapshotRestore *a,
                                            const BMLogSnapshotRestore *b)
{
  if (!a || !b) { return a == b; }
  if (a->log->epoch != b->log->epoch || a->replay.size() != b->replay.size()) { return false; }
  auto verts_equal = [](const auto &x, const auto &y) {
    if (x.size() != y.size()) { return false; }
    for (const auto &item : x.items()) {
      const auto *other = y.lookup_ptr(item.key);
      if (!other || std::memcmp(item.value->position, (*other)->position, sizeof(float3)) ||
          std::memcmp(item.value->normal, (*other)->normal, sizeof(float3)) ||
          std::memcmp(&item.value->mask, &(*other)->mask, sizeof(float)) ||
          item.value->hflag != (*other)->hflag) { return false; }
    }
    return true;
  };
  auto faces_equal = [](const auto &x, const auto &y) {
    if (x.size() != y.size()) { return false; }
    for (const auto &item : x.items()) {
      const auto *other = y.lookup_ptr(item.key);
      if (!other || item.value->v_ids != (*other)->v_ids || item.value->hflag != (*other)->hflag) { return false; }
    }
    return true;
  };
  for (const int64_t i : a->replay.index_range()) {
    const auto &x = a->replay[i]; const auto &y = b->replay[i];
    if (x.target != y.target || x.identity != y.identity ||
        !verts_equal(x.deleted_verts, y.deleted_verts) || !verts_equal(x.added_verts, y.added_verts) ||
        !verts_equal(x.modified_verts, y.modified_verts) || !faces_equal(x.deleted_faces, y.deleted_faces) ||
        !faces_equal(x.added_faces, y.added_faces) || !faces_equal(x.modified_faces, y.modified_faces)) { return false; }
  }
  return true;
}

void BM_log_snapshot_restore_discard(BMLogSnapshotRestore *restore)
{
  if (!restore) {
    return;
  }
  BM_log_free(restore->log);
  MEM_delete(restore);
}

BMLog *BM_log_snapshot_restore_commit(BMLogSnapshotRestore *restore)
{
  BLI_assert(restore && restore->log);
  BMLog *log = restore->log;
  BLI_assert(bm_log_snapshot_entries_validate(log->epoch, restore->entries, nullptr));
  BLI_assert(BLI_listbase_is_empty(&log->entries));
#ifndef NDEBUG
  for (const BMLogEntry *entry : restore->entries) {
    BLI_assert(entry->log == nullptr);
  }
#endif

  /* Private replay also restores the exact captured swap-buffer values while keeping the
   * entry addresses expected by artist Undo. All moves are allocation-free. Original pool
   * values remain owned by the entry and follow its ordinary destruction path. */
  for (BMLogReplayEntry &copy : restore->replay) {
    BMLogEntry &entry = *copy.target;
    std::swap(entry.deleted_verts, copy.deleted_verts); std::swap(entry.added_verts, copy.added_verts);
    std::swap(entry.modified_verts, copy.modified_verts);
    std::swap(entry.deleted_faces, copy.deleted_faces); std::swap(entry.added_faces, copy.added_faces);
    std::swap(entry.modified_faces, copy.modified_faces);
    std::swap(entry.replay_values, copy.values);
  }
  /* All validation and allocation precede the ownership change. Entry links keep their
   * orientation; only the owner and live element mapping are replaced. */
  if (!restore->entries.is_empty()) {
    log->entries.first = restore->entries.first();
    log->entries.last = restore->entries.last();
    for (BMLogEntry *entry : restore->entries) {
      entry->log = log;
    }
  }
  MEM_delete(restore);
  return log;
}

void BM_log_free(BMLog *log)
{
  if (log->unused_ids) {
    range_tree_uint_free(log->unused_ids);
  }

  /* Clear the BMLog references within each entry, but do not free
   * the entries themselves */
  for (BMLogEntry &entry : log->entries) {
    entry.log = nullptr;
  }

  MEM_delete(log);
}

BMLogEntry *BM_log_entry_add(BMLog *log)
{
  /* WARNING: Deleting any entries after the current one is now handled by the
   * UndoSystem: BKE_UNDOSYS_TYPE_SCULPT freeing here causes unnecessary complications. */

  /* Create and append the new entry */
  BMLogEntry *entry = bm_log_entry_create();
  entry->epoch = log->epoch;
  entry->identity = bm_log_identity_create();
  BLI_addtail(&log->entries, entry);
  entry->log = log;
  log->current_entry = entry;

  return entry;
}

void BM_log_entry_drop(BMLogEntry *entry)
{
  BMLog *log = entry->log;

  if (!log) {
    /* Unlink */
    BLI_assert(!(entry->prev && entry->next));
    if (entry->prev) {
      entry->prev->next = nullptr;
    }
    else if (entry->next) {
      entry->next->prev = nullptr;
    }

    bm_log_entry_free(entry);
    MEM_delete(entry);
    return;
  }

  if (!entry->prev) {
    /* Release IDs of elements that are deleted by this
     * entry. Since the entry is at the beginning of the undo
     * stack, and it's being deleted, those elements can never be
     * restored. Their IDs can go back into the pool. */

    /* This would never happen usually since first entry of log is
     * usually dyntopo enable, which, when reverted will free the log
     * completely. However, it is possible have a stroke instead of
     * dyntopo enable as first entry if nodes have been cleaned up
     * after sculpting on a different object than A, B.
     *
     * The steps are:
     * A dyntopo enable - sculpt
     * B dyntopo enable - sculpt - undo (A objects operators get cleaned up)
     * A sculpt (now A's log has a sculpt operator as first entry)
     *
     * Causing a cleanup at this point will call the code below, however
     * this will invalidate the state of the log since the deleted vertices
     * have been reclaimed already on step 2 (see BM_log_cleanup_entry)
     *
     * Also, design wise, a first entry should not have any deleted vertices since it
     * should not have anything to delete them -from-
     */
    // bm_log_id_ghash_release(log, entry->deleted_faces);
    // bm_log_id_ghash_release(log, entry->deleted_verts);
  }
  else if (!entry->next) {
    /* Release IDs of elements that are added by this entry. Since
     * the entry is at the end of the undo stack, and it's being
     * deleted, those elements can never be restored. Their IDs
     * can go back into the pool. */
    for (const uint id : entry->added_faces.keys()) {
      range_tree_uint_release(log->unused_ids, id);
    }
    for (const uint id : entry->added_verts.keys()) {
      range_tree_uint_release(log->unused_ids, id);
    }
  }
  else {
    BLI_assert_msg(0, "Cannot drop BMLogEntry from middle");
  }

  if (log->current_entry == entry) {
    log->current_entry = entry->prev;
  }

  bm_log_entry_free(entry);
  BLI_remlink(&log->entries, entry);
  MEM_delete(entry);
}

void BM_log_undo(BMesh *bm, BMLog *log)
{
  BMLogEntry *entry = log->current_entry;

  if (entry) {
    log->current_entry = entry->prev;

    /* Delete added faces and verts */
    bm_log_faces_unmake(bm, log, entry->added_faces);
    bm_log_verts_unmake(bm, log, entry->added_verts);

    /* Restore deleted verts and faces */
    bm_log_verts_restore(bm, log, entry->deleted_verts);
    bm_log_faces_restore(bm, log, entry->deleted_faces);

    /* Restore vertex coordinates, mask, and hflag */
    bm_log_vert_values_swap(bm, log, entry->modified_verts);
    bm_log_face_values_swap(log, entry->modified_faces);
  }
}

void BM_log_redo(BMesh *bm, BMLog *log)
{
  BMLogEntry *entry = log->current_entry;

  if (!entry) {
    /* Currently at the beginning of the undo stack, move to first entry */
    entry = static_cast<BMLogEntry *>(log->entries.first);
  }
  else if (entry->next) {
    /* Move to next undo entry */
    entry = entry->next;
  }
  else {
    /* Currently at the end of the undo stack, nothing left to redo */
    return;
  }

  log->current_entry = entry;

  if (entry) {
    /* Re-delete previously deleted faces and verts */
    bm_log_faces_unmake(bm, log, entry->deleted_faces);
    bm_log_verts_unmake(bm, log, entry->deleted_verts);

    /* Restore previously added verts and faces */
    bm_log_verts_restore(bm, log, entry->added_verts);
    bm_log_faces_restore(bm, log, entry->added_faces);

    /* Restore vertex coordinates, mask, and hflag */
    bm_log_vert_values_swap(bm, log, entry->modified_verts);
    bm_log_face_values_swap(log, entry->modified_faces);
  }
}

void BM_log_vert_before_modified(BMLog *log, BMVert *v, const int cd_vert_mask_offset)
{
  BMLogEntry *entry = log->current_entry;
  const uint v_id = bm_log_vert_id_get(log, v);

  /* Find or create the BMLogVert entry */
  if (entry->added_verts.contains(v_id)) {
    bm_log_vert_bmvert_copy(entry->added_verts.lookup(v_id), v, cd_vert_mask_offset);
  }
  else {
    entry->modified_verts.lookup_or_add_cb(
        v_id, [&] { return bm_log_vert_alloc(log, v, cd_vert_mask_offset); });
  }
}

void BM_log_vert_added(BMLog *log, BMVert *v, const int cd_vert_mask_offset)
{
  const uint v_id = range_tree_uint_take_any(log->unused_ids);

  bm_log_vert_id_set(log, v, v_id);
  BMLogVert *lv = bm_log_vert_alloc(log, v, cd_vert_mask_offset);
  log->current_entry->added_verts.add(v_id, lv);
}

void BM_log_face_modified(BMLog *log, BMFace *f)
{
  const uint f_id = bm_log_face_id_get(log, f);

  BMLogFace *lf = bm_log_face_alloc(log, f);
  log->current_entry->modified_faces.add(f_id, lf);
}

void BM_log_face_added(BMLog *log, BMFace *f)
{
  const uint f_id = range_tree_uint_take_any(log->unused_ids);

  /* Only triangles are supported for now */
  BLI_assert(f->len == 3);

  bm_log_face_id_set(log, f, f_id);
  BMLogFace *lf = bm_log_face_alloc(log, f);
  log->current_entry->added_faces.add(f_id, lf);
}

void BM_log_vert_removed(BMLog *log, BMVert *v, const int cd_vert_mask_offset)
{
  BMLogEntry *entry = log->current_entry;
  const uint v_id = bm_log_vert_id_get(log, v);

  BLI_assert(!entry->added_verts.contains(v_id) ||
             (entry->added_verts.contains(v_id) && entry->added_verts.lookup(v_id) != nullptr));

  if (entry->added_verts.remove(v_id)) {
    range_tree_uint_release(log->unused_ids, v_id);
  }
  else {
    BMLogVert *lv = bm_log_vert_alloc(log, v, cd_vert_mask_offset);
    entry->deleted_verts.add(v_id, lv);

    /* If the vertex was modified before deletion, ensure that the
     * original vertex values are stored */
    if (std::optional<BMLogVert *> lv_mod = entry->modified_verts.lookup_try(v_id)) {
      *lv = *lv_mod.value();
      entry->modified_verts.remove(v_id);
    }
  }
}

void BM_log_face_removed(BMLog *log, BMFace *f)
{
  BMLogEntry *entry = log->current_entry;
  const uint f_id = bm_log_face_id_get(log, f);

  BLI_assert(!entry->added_faces.contains(f_id) ||
             (entry->added_faces.contains(f_id) && entry->added_faces.lookup(f_id) != nullptr));

  if (entry->added_faces.remove(f_id)) {
    range_tree_uint_release(log->unused_ids, f_id);
  }
  else {
    BMLogFace *lf = bm_log_face_alloc(log, f);
    entry->deleted_faces.add(f_id, lf);
  }
}

void BM_log_all_added(BMesh *bm, BMLog *log)
{
  const int cd_vert_mask_offset = CustomData_get_offset_named(
      &bm->vdata, CD_PROP_FLOAT, ".sculpt_mask");

  /* avoid unnecessary resizing on initialization */
  if (log->current_entry->added_verts.is_empty()) {
    log->current_entry->added_verts.reserve(bm->totvert);
  }

  if (log->current_entry->added_faces.is_empty()) {
    log->current_entry->added_faces.reserve(bm->totface);
  }

  BMIter bm_iter;
  BMVert *v;
  /* Log all vertices as newly created */
  BM_ITER_MESH (v, &bm_iter, bm, BM_VERTS_OF_MESH) {
    BM_log_vert_added(log, v, cd_vert_mask_offset);
  }

  BMFace *f;
  /* Log all faces as newly created */
  BM_ITER_MESH (f, &bm_iter, bm, BM_FACES_OF_MESH) {
    BM_log_face_added(log, f);
  }
}

void BM_log_before_all_removed(BMesh *bm, BMLog *log)
{
  const int cd_vert_mask_offset = CustomData_get_offset_named(
      &bm->vdata, CD_PROP_FLOAT, ".sculpt_mask");

  BMIter bm_iter;
  BMFace *f;
  /* Log deletion of all faces */
  BM_ITER_MESH (f, &bm_iter, bm, BM_FACES_OF_MESH) {
    BM_log_face_removed(log, f);
  }

  BMVert *v;
  /* Log deletion of all vertices */
  BM_ITER_MESH (v, &bm_iter, bm, BM_VERTS_OF_MESH) {
    BM_log_vert_removed(log, v, cd_vert_mask_offset);
  }
}

const float *BM_log_find_original_vert_co(BMLog *log, BMVert *v)
{
  BMLogEntry *entry = log->current_entry;
  const uint v_id = bm_log_vert_id_get(log, v);

  if (std::optional<BMLogVert *> log_vert = entry->modified_verts.lookup_try(v_id)) {
    return log_vert.value()->position;
  }
  return nullptr;
}

const float *BM_log_find_original_vert_mask(BMLog *log, BMVert *v)
{
  BMLogEntry *entry = log->current_entry;
  const uint v_id = bm_log_vert_id_get(log, v);

  if (std::optional<BMLogVert *> log_vert = entry->modified_verts.lookup_try(v_id)) {
    return &log_vert.value()->mask;
  }
  return nullptr;
}

void BM_log_original_vert_data(BMLog *log, BMVert *v, const float **r_co, const float **r_no)
{
  BMLogEntry *entry = log->current_entry;
  BLI_assert(entry);

  const uint v_id = bm_log_vert_id_get(log, v);

  BLI_assert(entry->modified_verts.contains(v_id));

  const BMLogVert *lv = entry->modified_verts.lookup(v_id);
  *r_co = lv->position;
  *r_no = lv->normal;
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Debugging and Testing
 * \{ */

#if !defined(NDEBUG) || defined(WITH_GTESTS)
BMLogEntry *BM_log_current_entry(BMLog *log)
{
  return log->current_entry;
}
#endif

#ifndef NDEBUG
RangeTreeUInt *BM_log_unused_ids(BMLog *log)
{
  return log->unused_ids;
}

/* Print the list of entries, marking the current one
 *
 * Keep around for debugging */
void BM_log_print(const BMLog *log, const char *description)
{
  const BMLogEntry *entry;
  const char *current = " <-- current";
  int i;

  printf("%s:\n", description);
  printf("    % 2d: [ initial ]%s\n", 0, (!log->current_entry) ? current : "");
  for (entry = static_cast<const BMLogEntry *>(log->entries.first), i = 1; entry;
       entry = entry->next, i++)
  {
    printf("    % 2d: [%p]%s\n", i, entry, (entry == log->current_entry) ? current : "");
  }
}

void BM_log_print_entry(BMesh *bm, BMLogEntry *entry)
{
  if (bm) {
    printf("BM { totvert=%d totedge=%d totloop=%d faces_num=%d\n",
           bm->totvert,
           bm->totedge,
           bm->totloop,
           bm->totface);

    if (!bm->totvert) {
      printf("%s: Warning: empty bmesh\n", __func__);
    }
  }
  else {
    printf("BM { totvert=unknown totedge=unknown totloop=unknown faces_num=unknown\n");
  }

  printf("v | added: %d, removed: %d, modified: %d\n",
         int(entry->added_verts.size()),
         int(entry->deleted_verts.size()),
         int(entry->modified_verts.size()));
  printf("f | added: %d, removed: %d, modified: %d\n",
         int(entry->added_faces.size()),
         int(entry->deleted_faces.size()),
         int(entry->modified_faces.size()));
  printf("}\n");
}
#endif

/** \} */

}  // namespace blender
