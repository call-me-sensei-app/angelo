/* SPDX-FileCopyrightText: 2025 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup bke
 */

#include <atomic>
#include <chrono>
#include <fcntl.h>
#include <sys/stat.h>
#ifndef WIN32
#  include <unistd.h>
#else
#  include "BLI_winstuff.hh"
#  include <io.h>
#endif
#include <fmt/format.h>
#include <mutex>
#include <xxhash.h>

#include "BKE_id_hash.hh"
#include "BKE_lib_id.hh"
#include "BKE_lib_query.hh"
#include "BKE_library.hh"
#include "BKE_main.hh"

#include "BLI_file_time.hh"
#include "BLI_fileops.hh"
#include "BLI_mmap.hh"
#include "BLI_mutex.hh"
#include "BLI_path_utils.hh"
#include "BLI_set.hh"
#include "BLI_vector.hh"

#include "CLG_log.h"

namespace blender::bke::id_hash {

static CLG_LogRef LOG = {"lib.id_hash"};

static std::optional<Vector<char>> read_file(const StringRefNull path)
{
  fstream stream{path.c_str(), std::ios_base::in | std::ios_base::binary};
  stream.seekg(0, std::ios_base::end);
  const int64_t size = stream.tellg();
  stream.seekg(0, std::ios_base::beg);

  Vector<char> buffer(size);
  stream.read(buffer.data(), size);
  if (stream.bad()) {
    return std::nullopt;
  }

  return buffer;
}

static std::optional<XXH128_hash_t> compute_file_hash_with_file_read(const StringRefNull path)
{
  const std::optional<Vector<char>> buffer = read_file(path);
  if (!buffer) {
    return std::nullopt;
  }
  return XXH3_128bits(buffer->data(), buffer->size());
}

static std::optional<XXH128_hash_t> compute_file_hash_with_memory_map(const int file)
{
  if (!BLI_file_descriptor_is_open(file)) {
    return std::nullopt;
  }
  BLI_mmap_file *mmap_file = BLI_mmap_open(file);
  if (!mmap_file) {
    return std::nullopt;
  }
  BLI_SCOPED_DEFER([&]() { BLI_mmap_free(mmap_file); });
  const size_t size = BLI_mmap_get_length(mmap_file);
  const void *data = BLI_mmap_get_pointer(mmap_file);
  const XXH128_hash_t hash = XXH3_128bits(data, size);
  if (BLI_mmap_any_io_error(mmap_file)) {
    return std::nullopt;
  }
  return hash;
}

static std::atomic<int64_t> file_reads = 0;

int64_t file_reads_for_tests()
{
  return file_reads.load();
}

/** The hash of the file open at `file` (whose path is `path`): read through that descriptor, the
 * one its version was taken from; by path only if it cannot be memory mapped. */
static std::optional<XXH128_hash_t> compute_file_hash(const int file, const StringRefNull path)
{
  file_reads.fetch_add(1);
  if (const std::optional<XXH128_hash_t> hash = compute_file_hash_with_memory_map(file)) {
    /* Make sure both code paths are tested even if memory mapping should almost always work. */
    BLI_assert(hash->low64 == compute_file_hash_with_file_read(path)->low64);
    return hash;
  }
  if (const std::optional<XXH128_hash_t> hash = compute_file_hash_with_file_read(path)) {
    return hash;
  }
  return std::nullopt;
}

/**
 * Angelo (an upstream candidate). Blender cached a file's hash by its path and modification time
 * in whole seconds, so two versions of an asset saved within one second got one hash, and packed
 * linking (and Undo's dedupe) took one for the other. Now:
 *
 * - A file is known by its version: the identity of the file the reader opens at that path (a
 * final link followed: device, file index, size, modification time in nanoseconds) and its status
 * change time (ctime, which a rewrite that restores an old modification time still changes).
 * - Each ID is checked first against the version it was read from; an ID read from another
 *   version never gets this one's hash, cached or not.
 * - A cached hash is reused only for the same version, and only if the file had not changed within
 *   #RACY_WINDOW_NS of the moment it was hashed: file systems may keep times in whole seconds (2 s
 * on FAT), so a file written again just after being hashed can keep its version (the "racily
 * clean" files of Git).
 * - One call (#compute_linked_id_deep_hashes) takes each file's version and hash once, whatever
 * the number of IDs from it.
 * - Blender's own writes drop what is known of the file (#source_file_changed).
 */
struct CachedFileHash {
  FileVersion version;
  int64_t hashed_at_ns = 0;
  XXH128_hash_t hash;
};

/** What one call knows of a source file: its version and hash, taken through one descriptor. */
struct FileHashMemo {
  bool exists = false;
  int64_t stat_mtime = 0;
  std::optional<FileVersion> version;
  std::optional<XXH128_hash_t> hash;
};
using FileHashMemos = Map<std::string, FileHashMemo>;

static constexpr int64_t RACY_WINDOW_NS = int64_t(2) * 1000 * 1000 * 1000;

static Map<std::string, CachedFileHash> &file_hash_cache()
{
  static Map<std::string, CachedFileHash> cache;
  return cache;
}

static Mutex &file_hash_mutex()
{
  static Mutex mutex;
  return mutex;
}

static bool same_file_version(const FileVersion &a, const FileVersion &b)
{
  return a.identity.device == b.identity.device &&
         a.identity.device_signed == b.identity.device_signed &&
         a.identity.index_low == b.identity.index_low &&
         a.identity.index_high == b.identity.index_high && a.identity.size == b.identity.size &&
         a.identity.modified_ns == b.identity.modified_ns && a.changed_ns == b.changed_ns;
}

static int64_t now_ns()
{
  return std::chrono::duration_cast<std::chrono::nanoseconds>(
             std::chrono::system_clock::now().time_since_epoch())
      .count();
}

std::optional<FileVersion> file_version_of_descriptor(const int file)
{
  /* A public function: the descriptor is the caller's, so ask before the C runtime sees it. */
  if (!BLI_file_descriptor_is_open(file)) {
    return std::nullopt;
  }
  FileVersion version;
  if (!BLI_file_identity_descriptor(file, &version.identity)) {
    return std::nullopt;
  }
#ifdef WIN32
  FILE_BASIC_INFO info;
  if (GetFileInformationByHandleEx(
          reinterpret_cast<HANDLE>(_get_osfhandle(file)), FileBasicInfo, &info, sizeof(info)))
  {
    version.changed_ns = file_time::nanoseconds_from_filetime(uint64_t(info.ChangeTime.QuadPart));
  }
#else
  struct stat st;
  if (fstat(file, &st) == 0) {
#  ifdef __APPLE__
    version.changed_ns = file_time::nanoseconds_saturated(int64_t(st.st_ctimespec.tv_sec),
                                                          int64_t(st.st_ctimespec.tv_nsec));
#  else
    version.changed_ns = file_time::nanoseconds_saturated(int64_t(st.st_ctim.tv_sec),
                                                          int64_t(st.st_ctim.tv_nsec));
#  endif
  }
#endif
  return version;
}

void source_file_version_record(ID &id, const FileVersion &version)
{
  id.runtime->src_blend_device = version.identity.device;
  id.runtime->src_blend_index_low = version.identity.index_low;
  id.runtime->src_blend_index_high = version.identity.index_high;
  id.runtime->src_blend_size = version.identity.size;
  id.runtime->src_blend_modified_ns = version.identity.modified_ns;
  id.runtime->src_blend_changed_ns = version.changed_ns;
}

/** Whether `id` was read from that version of its file. */
static bool source_file_version_is(const ID &id, const FileVersion &version)
{
  return id.runtime->src_blend_device == version.identity.device &&
         id.runtime->src_blend_index_low == version.identity.index_low &&
         id.runtime->src_blend_index_high == version.identity.index_high &&
         id.runtime->src_blend_size == version.identity.size &&
         id.runtime->src_blend_modified_ns == version.identity.modified_ns &&
         id.runtime->src_blend_changed_ns == version.changed_ns;
}

void source_file_changed(const StringRefNull filepath)
{
  std::lock_guard lock(file_hash_mutex());
  Vector<std::string> stale;
  for (const std::string &cached_path : file_hash_cache().keys()) {
    if (BLI_path_cmp_normalized(cached_path.c_str(), filepath.c_str()) == 0) {
      stale.append(cached_path);
    }
  }
  for (const std::string &cached_path : stale) {
    file_hash_cache().remove_as(cached_path);
  }
}

/** What is known of the file at `path` for one call: opened as the reader opens it (a final link
 * followed), its version and hash taken through that descriptor: the hash from the cache when it
 * can be trusted, else by reading the file. */
static FileHashMemo file_hash_memo_get(const StringRefNull path)
{
  FileHashMemo memo;
  BLI_stat_t stat;
  memo.exists = BLI_stat(path.c_str(), &stat) != -1;
  if (!memo.exists) {
    return memo;
  }
  memo.stat_mtime = stat.st_mtime;
  const int file = BLI_open(path.c_str(), O_BINARY | O_RDONLY, 0);
  if (file == -1) {
    return memo;
  }
  BLI_SCOPED_DEFER([&]() { close(file); });
  memo.version = file_version_of_descriptor(file);

  Map<std::string, CachedFileHash> &cache = file_hash_cache();
  std::lock_guard lock(file_hash_mutex());
  const CachedFileHash *cached = memo.version ? cache.lookup_ptr_as(path) : nullptr;
  if (cached && same_file_version(cached->version, *memo.version) &&
      memo.version->identity.modified_ns < cached->hashed_at_ns - RACY_WINDOW_NS &&
      memo.version->changed_ns < cached->hashed_at_ns - RACY_WINDOW_NS)
  {
    memo.hash = cached->hash;
    return memo;
  }
  /* Taken before the file is read: a write during the read leaves the result untrusted. */
  const int64_t hashed_at_ns = now_ns();
  CLOG_DEBUG(&LOG, "Hashing library file %s", path.c_str());
  memo.hash = compute_file_hash(file, path);
  if (memo.hash && memo.version) {
    cache.add_overwrite(path, CachedFileHash{*memo.version, hashed_at_ns, *memo.hash});
  }
  else {
    cache.remove_as(path);
  }
  return memo;
}

static std::optional<XXH128_hash_t> get_source_file_hash(const ID &id,
                                                         DeepHashErrors &r_errors,
                                                         FileHashMemos &memos)
{
  const StringRefNull path = id.lib->runtime->filepath_abs;
  const FileHashMemo &memo = memos.lookup_or_add_cb_as(path,
                                                       [&]() { return file_hash_memo_get(path); });

  if (!memo.exists) {
    r_errors.missing_files.add_as(path);
    return std::nullopt;
  }
  if ((id.tag & ID_TAG_MISSING) != 0) {
    r_errors.missing_from_files.add_as(path);
    return std::nullopt;
  }

  /* First, whether the file is still the version this ID was read from: an ID read from another
   * version never gets this one's hash. The modification time may not be set if the data-block is
   * added as linked data as part of versioning (e.g. in #do_versions_after_setup). */
  if (memo.version && id.runtime->src_blend_modified_ns != 0) {
    if (!source_file_version_is(id, *memo.version)) {
      r_errors.updated_files.add_as(path);
      return std::nullopt;
    }
  }
  else if (id.runtime->src_blend_modifification_time != 0) {
    if (memo.stat_mtime != id.runtime->src_blend_modifification_time) {
      r_errors.updated_files.add_as(path);
      return std::nullopt;
    }
  }

  if (memo.hash) {
    return *memo.hash;
  }
  r_errors.missing_files.add_as(path);
  return std::nullopt;
}

static std::optional<XXH128_hash_t> get_id_shallow_hash(const ID &id,
                                                        DeepHashErrors &r_errors,
                                                        FileHashMemos &memos)
{
  BLI_assert(ID_IS_LINKED(&id));
  const StringRefNull id_name = id.name;
  const std::optional<XXH128_hash_t> file_hash = get_source_file_hash(id, r_errors, memos);
  if (!file_hash) {
    return std::nullopt;
  }

  XXH3_state_t *hash_state = XXH3_createState();
  XXH3_128bits_reset(hash_state);
  XXH3_128bits_update(hash_state, id_name.data(), id_name.size());
  XXH3_128bits_update(hash_state, &*file_hash, sizeof(XXH128_hash_t));
  XXH128_hash_t shallow_hash = XXH3_128bits_digest(hash_state);
  XXH3_freeState(hash_state);
  return shallow_hash;
}

static void compute_deep_hash_recursive(const Main &bmain,
                                        const ID &id,
                                        Set<const ID *> &current_stack,
                                        Map<const ID *, IDHash> &r_hashes,
                                        DeepHashErrors &r_errors,
                                        FileHashMemos &memos)
{
  if (r_hashes.contains(&id)) {
    return;
  }
  if (!id.deep_hash.is_null()) {
    r_hashes.add(&id, id.deep_hash);
    return;
  }
  current_stack.add(&id);
  BLI_SCOPED_DEFER([&]() -> void { current_stack.remove(&id); });
  const std::optional<XXH128_hash_t> id_shallow_hash = get_id_shallow_hash(id, r_errors, memos);
  if (!id_shallow_hash) {
    return;
  }

  XXH3_state_t *hash_state = XXH3_createState();
  BLI_SCOPED_DEFER([&hash_state]() -> void { XXH3_freeState(hash_state); })
  XXH3_128bits_reset(hash_state);
  XXH3_128bits_update(hash_state, &*id_shallow_hash, sizeof(XXH128_hash_t));

  bool success = true;
  BKE_library_foreach_ID_link(
      const_cast<Main *>(&bmain),
      const_cast<ID *>(&id),
      [&](LibraryIDLinkCallbackData *cb_data) {
        if (cb_data->cb_flag & IDWALK_CB_LOOPBACK) {
          /* Loop-back pointer (e.g. from a shape-key to its owner geometry ID, or from a
           * collection to its parents) should always be ignored, as they do not represent an
           * actual dependency. The dependency relationship should already have been
           * processed from the owner to its dependency anyway (if applicable). */
          return IDWALK_RET_NOP;
        }
        if (cb_data->cb_flag & (IDWALK_CB_EMBEDDED | IDWALK_CB_EMBEDDED_NOT_OWNING)) {
          /* Embedded data are part of their owner's internal data, and as such already computed as
           * part of the owner's shallow hash. */
          return IDWALK_RET_NOP;
        }
        if (cb_data->cb_flag & IDWALK_CB_HASH_IGNORE) {
          /* This pointer is explicitly ignored for the hash computation. */
          return IDWALK_RET_NOP;
        }
        ID *referenced_id = *cb_data->id_pointer;
        if (!referenced_id) {
          /* Need to update the hash even if there is no id. There is a difference between the case
           * where there is no id and the case where this callback is not called at all.*/
          const int random_data = 452942579;
          XXH3_128bits_update(hash_state, &random_data, sizeof(int));
          return IDWALK_RET_NOP;
        }
        /* All embedded ID usages should already have been excluded above. */
        BLI_assert((referenced_id->flag & ID_FLAG_EMBEDDED_DATA) == 0);
        if (current_stack.contains(referenced_id)) {
          /* Somehow encode that we had a circular reference here. */
          const int random_data = 234632342;
          XXH3_128bits_update(hash_state, &random_data, sizeof(int));
          return IDWALK_RET_NOP;
        }
        compute_deep_hash_recursive(
            bmain, *referenced_id, current_stack, r_hashes, r_errors, memos);
        const IDHash *referenced_id_hash = r_hashes.lookup_ptr(referenced_id);
        if (!referenced_id_hash) {
          success = false;
          return IDWALK_RET_STOP_ITER;
        }
        XXH3_128bits_update(hash_state, referenced_id_hash->data, sizeof(IDHash));
        return IDWALK_RET_NOP;
      },
      nullptr,
      IDWALK_READONLY);

  if (!success) {
    return;
  }
  IDHash new_deep_hash;
  const XXH128_hash_t new_deep_hash_xxh128 = XXH3_128bits_digest(hash_state);
  static_assert(sizeof(IDHash) == sizeof(XXH128_hash_t));
  memcpy(new_deep_hash.data, &new_deep_hash_xxh128, sizeof(IDHash));
  r_hashes.add(&id, new_deep_hash);
}

IDHashResult compute_linked_id_deep_hashes(const Main &bmain, Span<const ID *> ids)
{
#ifndef NDEBUG
  for (const ID *id : ids) {
    BLI_assert(ID_IS_LINKED(id));
  }
#endif

  if (ids.is_empty()) {
    return ValidDeepHashes{};
  }

  Map<const ID *, IDHash> hashes;
  Set<const ID *> current_stack;
  DeepHashErrors errors;
  /* Angelo: each source file's version and hash are taken once for the whole call. */
  FileHashMemos memos;
  for (const ID *id : ids) {
    compute_deep_hash_recursive(bmain, *id, current_stack, hashes, errors, memos);
  }
  if (!errors.missing_files.is_empty() || !errors.missing_from_files.is_empty() ||
      !errors.updated_files.is_empty())
  {
    return errors;
  }
  return ValidDeepHashes{hashes};
}

std::string id_hash_to_hex(const IDHash &hash)
{
  std::string hex_str;
  for (const uint8_t byte : hash.data) {
    hex_str += fmt::format("{:02x}", byte);
  }
  return hex_str;
}

}  // namespace blender::bke::id_hash
