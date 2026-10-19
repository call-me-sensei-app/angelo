/* SPDX-FileCopyrightText: 2004 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

#include <algorithm>
#include <limits>

/** \file
 * \ingroup blenloader
 * External write-file function prototypes.
 */

#include "BLI_filereader.h"
#include "BLI_implicit_sharing.hh"
#include "BLI_map.hh"

#include "DNA_listBase.h"

namespace blender {

struct Main;
struct Scene;
struct WriteData;
struct WriteDataStableAddressIDs;

/** Private writer-owner admission counter. Failure is sticky; no charge occurs after refusal. */
struct MemFileWriteBudget {
  size_t limit;
  size_t accounted = 0;
  bool failed = false;

  bool charge(const size_t bytes)
  {
    if (failed) {
      return false;
    }
    if (accounted > limit || bytes > limit - accounted) {
      failed = true;
      return false;
    }
    accounted += bytes;
    return true;
  }

  bool charge_product(const size_t count, const size_t element_size)
  {
    if (element_size && count > std::numeric_limits<size_t>::max() / element_size) {
      failed = true;
      return false;
    }
    return charge(count * element_size);
  }
};

/** Default BLI Map/Set use 1/2 load and power-of-two slot arrays. Charge the new allocation,
 * including coexistence with the old table, before reserve; inline arrays are already in owners.
 * These writer containers never remove individual keys (only clear). */
template<typename Container>
bool BLO_memfile_budget_reserve_table(Container &table,
                                      MemFileWriteBudget *budget,
                                      const int64_t count,
                                      const int64_t inline_slots)
{
  if (!budget) {
    return true;
  }
  if (budget->failed || count < 0 || count > INT64_MAX / 8 || table.size() > INT64_MAX / 8) {
    budget->failed = true;
    return false;
  }
  if (table.capacity() > 1 && count <= table.capacity() / 2) {
    return true;
  }
  int64_t slots, usable;
  LoadFactor(1, 2).compute_total_and_usable_slots(
      inline_slots, std::max(count, table.size() * 2), &slots, &usable);
  if (uint64_t(slots) > SIZE_MAX / uint64_t(table.size_per_element())) {
    budget->failed = true;
    return false;
  }
  if (slots > inline_slots &&
      !budget->charge_product(size_t(slots), size_t(table.size_per_element())))
  {
    return false;
  }
  table.reserve(count);
  return true;
}

template<typename Key, typename Value>
bool BLO_memfile_budget_reserve(Map<Key, Value> &map,
                                MemFileWriteBudget *budget,
                                const int64_t count)
{
  return BLO_memfile_budget_reserve_table(
      map, budget, count,
      LoadFactor::compute_total_slots(default_inline_buffer_capacity(sizeof(Key) + sizeof(Value)),
                                     1, 2));
}

struct MemFileSharedStorage {
  /**
   * Maps the address id to the shared data and corresponding sharing info..
   */
  Map<uint64_t, ImplicitSharingInfoAndData> sharing_info_by_address_id;

  ~MemFileSharedStorage();
};

struct MemFileChunk {
  void *next, *prev;
  const char *buf;
  /** Size in bytes. */
  size_t size;
  /** When true, this chunk doesn't own the memory, it's shared with a previous #MemFileChunk */
  bool is_identical;
  /** When true, this chunk is also identical to the one in the next step (used by undo code to
   * detect unchanged IDs).
   * Defined when writing the next step (i.e. last undo step has those always false). */
  bool is_identical_future;
  /** Session UID of the ID being currently written (MAIN_ID_SESSION_UID_UNSET when not writing
   * ID-related data). Used to find matching chunks in previous memundo step. */
  uint id_session_uid;
};

struct MemFile {
  ListBaseT<MemFileChunk> chunks;
  size_t size;
  /**
   * Some data is not serialized into a new buffer because the undo-step can take ownership of it
   * without making a copy. This is faster and requires less memory.
   */
  MemFileSharedStorage *shared_storage;
};

struct MemFileWriteData {
  MemFileWriteBudget *budget = nullptr;
  MemFile *written_memfile;
  MemFile *reference_memfile;

  uint current_id_session_uid;
  MemFileChunk *reference_current_chunk;

  /** Maps an ID session uid to its first reference MemFileChunk, if existing. */
  Map<uint, MemFileChunk *> id_session_uid_mapping;
};

struct MemFileUndoData {
  char filepath[/*FILE_MAX*/ 1024];
  MemFile memfile;
  size_t undo_size;
};

/* FileReader-compatible wrapper for reading MemFiles */
struct UndoReader {
  FileReader reader;

  MemFile *memfile;
  int undo_direction;

  bool memchunk_identical;
};

/* Actually only used `writefile.cc`. */

void BLO_memfile_write_init(WriteData *wd,
                            MemFileWriteData *mem_data,
                            MemFile *written_memfile,
                            MemFile *reference_memfile);
void BLO_memfile_write_finalize(WriteData *wd, MemFileWriteData *mem_data);

void BLO_memfile_chunk_add(MemFileWriteData *mem_data, const char *buf, size_t size);

/* exports */

/**
 * Not memfile itself.
 */
/* **************** support for memory-write, for undo buffers *************** */

void BLO_memfile_free(MemFile *memfile);
/**
 * Result is that 'first' is being freed.
 * To keep the #MemFile linked list of consistent, `first` is always first in list.
 */
void BLO_memfile_merge(MemFile *first, MemFile *second);
/**
 * Clear is_identical_future before adding next memfile.
 */
void BLO_memfile_clear_future(MemFile *memfile);

/* Utilities. */

Main *BLO_memfile_main_get(MemFile *memfile, Main *bmain, Scene **r_scene);

FileReader *BLO_memfile_new_filereader(MemFile *memfile, int undo_direction);

}  // namespace blender
