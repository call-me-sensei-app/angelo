/* SPDX-FileCopyrightText: 2001-2002 NaN Holding BV. All rights reserved.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * \ingroup blenloader
 * \brief external `writefile.cc` function prototypes.
 */

#include "BLI_sys_types.h"

namespace blender {

struct BlendThumbnail;
struct Main;
struct MemFile;
struct ReportList;

/* -------------------------------------------------------------------- */
/** \name BLO Write File API
 *
 * \see #BLO_read_from_file for file reading.
 * \{ */

/**
 * Adjust paths when saving (kept unless #BlendFileWriteParams.use_save_as_copy is set).
 */
enum eBLO_WritePathRemap {
  /** No path manipulation. */
  BLO_WRITE_PATH_REMAP_NONE = 0,
  /** Remap existing relative paths (default). */
  BLO_WRITE_PATH_REMAP_RELATIVE = 1,
  /** Remap paths making all paths relative to the new location. */
  BLO_WRITE_PATH_REMAP_RELATIVE_ALL = 2,
  /** Make all paths absolute. */
  BLO_WRITE_PATH_REMAP_ABSOLUTE = 3,
};

/** Similar to #BlendFileReadParams. */
struct BlendFileWriteParams {
  eBLO_WritePathRemap remap_mode = {};
  /** Save `.blend1`, `.blend2`... etc. */
  uint use_save_versions : 1 = false;
  /** On write, restore paths after editing them (see #BLO_WRITE_PATH_REMAP_RELATIVE). */
  uint use_save_as_copy : 1 = false;
  uint use_userdef : 1 = false;
  /** This is writing a copy/paste buffer, not a regular blendfile. */
  uint is_copypaste_buffer : 1 = false;
  const BlendThumbnail *thumb = nullptr;
  /** With G_FILE_COMPRESS, keep files at or below this byte size uncompressed.
   * Zero preserves unconditional compression. Only temporary stage bundles opt in. */
  size_t compression_min_size = 0;
};

/**
 * \return Success.
 */
extern bool BLO_write_file(Main *mainvar,
                           const char *filepath,
                           int write_flags,
                           const BlendFileWriteParams *params,
                           ReportList *reports);

/**
 * Serialize a prepared Main to a borrowed descriptor, keeping the normal disk buffer enabled.
 *
 * Call synchronously on the main thread with exclusive access to the live Main and descriptor.
 * The caller must retain a freshly created, writable binary descriptor and a stable, absolute
 * logical filepath for this call. Admission checks only regular-file type, empty content and
 * current offset zero; they cannot establish creation provenance, writability or exclusive access.
 *
 * Require no path remapping, save versions, UserDef, thumbnail, clipboard or compression options,
 * including a zero compression_min_size, and require use_save_as_copy. Invalid options are refused
 * before mutable Main preparation. The logical filepath provides serializer context/diagnostics,
 * not BASE pathname ownership. This function never opens, closes, duplicates, truncates, renames
 * or removes the BASE destination. The caller owns descriptor closure and any partial output
 * cleanup.
 *
 * Existing preparation and ID writers may modify Main or perform external I/O. Flags keep their
 * serializer meanings: AUTOPACK does not perform resource packing here, NO_UI does not omit UI
 * IDs, and RECOVER_WRITE serializes Main.filepath and G.fileflags' compression preference even
 * though the output stream is uncompressed. No ordinary Save handlers or success bookkeeping run
 * here.
 *
 * \return Stream success only: all serialization buffers were accepted. This is not durability,
 * publication, verification, checkpoint completeness or SAVE_POST. Failure can leave preparation
 * effects and a partial prefix; neither success nor failure closes the borrowed descriptor.
 */
bool BLO_write_file_to_descriptor(Main *mainvar,
                                  const char *logical_filepath,
                                  int descriptor,
                                  int write_flags,
                                  const BlendFileWriteParams *params,
                                  ReportList *reports);

#ifdef WITH_GTESTS
/** Per-call test injection, using the same retained descriptor and serializer as the public API.
 */
struct BlendFileDescriptorTestHooks {
  /** Return write(2) semantics; accepted bytes must actually be written to the supplied
   * descriptor. */
  int64_t (*write)(int descriptor, const void *buffer, size_t size, void *user_data) = nullptr;
  void *user_data = nullptr;
  /** Runs after serialization, before error reporting, to test independence from ambient errno. */
  void (*after_stream)(void *user_data) = nullptr;
};

bool BLO_write_file_to_descriptor_for_test(Main *mainvar,
                                           const char *logical_filepath,
                                           int descriptor,
                                           int write_flags,
                                           const BlendFileWriteParams *params,
                                           ReportList *reports,
                                           const BlendFileDescriptorTestHooks &hooks);
#endif

/**
 * \return Success.
 */
extern bool BLO_write_file_mem(Main *mainvar, MemFile *compare, MemFile *current, int write_flags);

enum class BlendMemFileWriteResult { Success, BudgetExceeded, InvalidArguments, WriteError };

/**
 * Private standalone checkpoint witness writer; ordinary Save/Undo use BLO_write_file_mem.
 * Requires an empty destination. Failure frees partial output; invalid arguments leave it alone.
 * Admits writer-owned allocation requests before growth, using a conservative cumulative charge
 * (including freed scratch/replaced tables). Retained shared backing is not newly allocated.
 * Allocator overhead, stack, Main synchronization and ID-writer-private allocations need a separate
 * caller reserve. This is not a process/OS memory limit. No previous Undo MemFile is modified.
 * Serializer-owned GLOB/REND header identities are generated independently of the caller's
 * stack. All payload bytes and persistent source addresses retain normal Undo semantics.
 */
BlendMemFileWriteResult BLO_write_file_mem_bounded(Main *mainvar,
                                                  MemFile *current,
                                                  int write_flags,
                                                  size_t max_added_bytes,
                                                  size_t *r_accounted_bytes = nullptr);

/** \} */

}  // namespace blender
