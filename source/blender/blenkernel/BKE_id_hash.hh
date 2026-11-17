/* SPDX-FileCopyrightText: 2025 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup bke
 */

#include <optional>
#include <string>
#include <variant>

#include "BKE_main.hh"

#include "BLI_fileops.hh"
#include "BLI_map.hh"
#include "BLI_vector.hh"

#include "DNA_ID.h"

namespace blender::bke::id_hash {

/**
 * The hash of all the root IDs and their dependencies.
 */
struct ValidDeepHashes {
  Map<const ID *, IDHash> hashes;
};

struct DeepHashErrors {
  /**
   * A list of missing files paths in the case that the deep hashes could not be computed.
   */
  VectorSet<std::string> missing_files;

  /**
   * Files that exist, but do not contain the expected IDs (i.e. the given ID from that library was
   * tagged as `ID_TAG_MISSING`).
   */
  VectorSet<std::string> missing_from_files;

  /**
   * Files that were modified since the linked ID was loaded. So the currently linked ID would not
   * be matching the deep hash computed based on the source file.
   */
  VectorSet<std::string> updated_files;
};

using IDHashResult = std::variant<ValidDeepHashes, DeepHashErrors>;

/**
 * Compute a hash of the given IDs, including all their dependencies.
 * This needs access to the original .blend files that the linked data-blocks come from to be able
 * to compute their hash.
 */
IDHashResult compute_linked_id_deep_hashes(const Main &bmain, Span<const ID *> root_ids);

/**
 * Angelo (an upstream candidate): the version of a file, by which its content hash is known: its
 * identity (device, file index, size, modification time in nanoseconds) and its status change
 * time, which a rewrite restoring an old modification time still changes.
 */
struct FileVersion {
  FileIdentity identity;
  int64_t changed_ns = 0;
};

/** The version of the file open at descriptor `file`. */
std::optional<FileVersion> file_version_of_descriptor(int file);

/** Record on `id` the version of the file it is read from (see #ID_Runtime). */
void source_file_version_record(ID &id, const FileVersion &version);

/**
 * Forget what is known of the file at `filepath` (its content hash): Blender wrote it, or may
 * have. Angelo, an upstream candidate: the hash of a file is otherwise reused while the file keeps
 * its identity (see #get_source_file_hash).
 */
void source_file_changed(StringRefNull filepath);

/** How many times a source file was read to hash it, since start (for tests). */
int64_t file_reads_for_tests();

/** Utility to convert the hash into a readable string. */
std::string id_hash_to_hex(const IDHash &hash);

}  // namespace blender::bke::id_hash
