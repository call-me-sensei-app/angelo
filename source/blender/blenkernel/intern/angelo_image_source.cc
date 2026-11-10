/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup bke
 *
 * Angelo: `Image.angelo_source_revision` (see BKE_angelo_image_source.hh).
 */

#include <array>
#include <atomic>
#include <cstring>
#include <mutex>
#include <sys/stat.h>

#ifdef WIN32
#  include <io.h>
#  include <windows.h>
#else
#  include <unistd.h>
#endif

#include "MEM_guardedalloc.h"

#include "BLI_hash.hh"
#include "BLI_hash_xxh3.hh"
#include "BLI_map.hh"
#include "BLI_mutex.hh"

#include "DNA_image_types.h"
#include "DNA_packedFile_types.h"

#include "BKE_angelo_image_source.hh"
#include "BKE_image.hh"

namespace blender::bke {

namespace {

/** One view, tile and frame of an image: the unit one load fills. */
struct LoadSlot {
  int view = 0;
  int tile = 0;
  int frame = 0;

  uint64_t hash() const
  {
    return get_default_hash(view, tile, frame);
  }
  friend bool operator==(const LoadSlot &a, const LoadSlot &b) = default;
};

using LoadIdentity = std::array<uint8_t, 16>;

/** What a load reads, as one digest, so a record costs the same for any path length. */
enum class SourceKind : uint8_t { File = 1, MissingFile = 2, Packed = 3, Unsupported = 4 };

/** The file's identity as the file system reports it; false when it cannot be opened. */
struct FileStamp {
  uint64_t device = 0;
  std::array<uint8_t, 16> inode{};
  uint64_t bytes = 0, modified = 0, changed = 0;
  uint32_t modified_ns = 0, changed_ns = 0;
};

bool file_stamp(const int fd, FileStamp &stamp)
{
  if (fd == -1) {
    return false;
  }
  bool ok = true;
#ifdef WIN32
  const HANDLE handle = reinterpret_cast<HANDLE>(_get_osfhandle(fd));
  FILE_ID_INFO identity{};
  FILE_STANDARD_INFO standard{};
  FILE_BASIC_INFO basic{};
  if (handle == INVALID_HANDLE_VALUE ||
      !GetFileInformationByHandleEx(handle, FileIdInfo, &identity, sizeof(identity)) ||
      !GetFileInformationByHandleEx(handle, FileStandardInfo, &standard, sizeof(standard)) ||
      !GetFileInformationByHandleEx(handle, FileBasicInfo, &basic, sizeof(basic)))
  {
    ok = false;
  }
  else {
    stamp.device = identity.VolumeSerialNumber;
    std::memcpy(stamp.inode.data(), identity.FileId.Identifier, stamp.inode.size());
    stamp.bytes = uint64_t(standard.EndOfFile.QuadPart);
    /* 100 ns units: no separate nanosecond field needed. */
    stamp.modified = uint64_t(basic.LastWriteTime.QuadPart);
    stamp.changed = uint64_t(basic.ChangeTime.QuadPart);
  }
#else
  struct stat info;
  if (fstat(fd, &info) != 0) {
    ok = false;
  }
  else {
    stamp.device = uint64_t(info.st_dev);
    const uint64_t inode = uint64_t(info.st_ino);
    std::memcpy(stamp.inode.data(), &inode, sizeof(inode));
    stamp.bytes = uint64_t(info.st_size);
#  ifdef __APPLE__
    stamp.modified = uint64_t(info.st_mtimespec.tv_sec);
    stamp.modified_ns = uint32_t(info.st_mtimespec.tv_nsec);
    stamp.changed = uint64_t(info.st_ctimespec.tv_sec);
    stamp.changed_ns = uint32_t(info.st_ctimespec.tv_nsec);
#  else
    stamp.modified = uint64_t(info.st_mtim.tv_sec);
    stamp.modified_ns = uint32_t(info.st_mtim.tv_nsec);
    stamp.changed = uint64_t(info.st_ctim.tv_sec);
    stamp.changed_ns = uint32_t(info.st_ctim.tv_nsec);
#  endif
  }
#endif
  return ok;
}

template<typename T> void feed(XXH3_128Stream &stream, const T &value)
{
  stream.update(&value, sizeof(value));
}

void feed_text(XXH3_128Stream &stream, const char *text)
{
  /* With the terminator, so two adjacent texts cannot shift into each other. */
  stream.update(text, std::strlen(text) + 1);
}

/** The settings every load reads with: the same bytes decoded differently are different pixels. */
void feed_load_settings(XXH3_128Stream &stream, const Image &image, const uint32_t load_flags)
{
  feed(stream, load_flags);
  feed_text(stream, image.colorspace_settings.name);
}

/** Guards every image's record (never held while reading or hashing a file). */
Mutex records_mutex;

std::atomic<uint64_t> next_revision = 1;

}  // namespace

struct AngeloImageSourceLoads {
  /** The revision this record belongs to, kept so an undo step that moves the record keeps it. */
  uint64_t revision = 0;
  struct Load {
    LoadIdentity identity;
    bool supported;
  };
  Map<LoadSlot, Load> last_loads;
};

uint64_t angelo_image_source_revision_fresh()
{
  return next_revision.fetch_add(1, std::memory_order_relaxed);
}

static AngeloImageSourceLoads &ensure_record(Image &image)
{
  ImageRuntime &runtime = *image.runtime;
  if (runtime.angelo_source_loads == nullptr) {
    runtime.angelo_source_loads = MEM_new<AngeloImageSourceLoads>(__func__);
    runtime.angelo_source_loads->revision = runtime.angelo_source_revision.load(
        std::memory_order_relaxed);
  }
  return *runtime.angelo_source_loads;
}

/** Caller holds #records_mutex. */
static void bump_locked(Image &image, AngeloImageSourceLoads &record)
{
  record.revision = angelo_image_source_revision_fresh();
  image.runtime->angelo_source_revision.store(record.revision, std::memory_order_relaxed);
}

void angelo_image_source_changed(Image &image)
{
  std::scoped_lock lock(records_mutex);
  AngeloImageSourceLoads &record = ensure_record(image);
  record.last_loads.clear();
  bump_locked(image, record);
}

static void note_load(Image &image,
                      const LoadSlot &slot,
                      const LoadIdentity &identity,
                      const bool supported)
{
  std::scoped_lock lock(records_mutex);
  AngeloImageSourceLoads &record = ensure_record(image);
  bool changed = false;
  record.last_loads.add_or_modify(
      slot,
      [&](AngeloImageSourceLoads::Load *value) {
        new (value) AngeloImageSourceLoads::Load{identity, supported};
      },
      [&](AngeloImageSourceLoads::Load *value) {
        changed = value->identity != identity || value->supported != supported;
        *value = {identity, supported};
      });
  if (changed) {
    bump_locked(image, record);
  }
}

AngeloImageSourceFileWitness angelo_image_source_prepare_file_load(const int file_descriptor,
                                                                  const char *filepath)
{
  XXH3_128Stream stream;
  FileStamp stamp;
  const bool supported = file_stamp(file_descriptor, stamp);
  if (supported) {
    feed(stream, SourceKind::File);
    feed(stream, stamp.device);
    feed(stream, stamp.inode);
    feed(stream, stamp.bytes);
    feed(stream, stamp.modified);
    feed(stream, stamp.modified_ns);
    feed(stream, stamp.changed);
    feed(stream, stamp.changed_ns);
  }
  else {
    feed(stream, SourceKind::MissingFile);
  }
  feed_text(stream, filepath);
  return {stream.finalize(), supported};
}

void angelo_image_source_note_file_load(Image &image,
                                        const int view,
                                        const int tile,
                                        const int frame,
                                        const AngeloImageSourceFileWitness &witness,
                                        const uint32_t load_flags)
{
  XXH3_128Stream stream;
  feed(stream, witness.identity);
  feed_load_settings(stream, image, load_flags);
  note_load(image, LoadSlot{view, tile, frame}, stream.finalize(), witness.supported);
}

void angelo_image_source_note_packed_load(Image &image,
                                          const int view,
                                          const int tile,
                                          const PackedFile &packed_file,
                                          const uint32_t load_flags)
{
  /* Packed bytes are immutable while shared, but a repack frees them and may reuse the address:
   * hash the bytes, never the pointer. XXH3 runs far faster than decoding the same bytes. */
  XXH3_128Stream stream;
  feed(stream, SourceKind::Packed);
  const int64_t size = packed_file.size;
  feed(stream, size);
  if (packed_file.data != nullptr && size > 0) {
    stream.update(packed_file.data, size_t(size));
  }
  feed_load_settings(stream, image, load_flags);
  /* A packed image is never a sequence: frame 0. */
  note_load(image,
            LoadSlot{view, tile, 0},
            stream.finalize(),
            packed_file.data != nullptr && size > 0);
}

void angelo_image_source_note_unsupported_load(Image &image, const int view)
{
  XXH3_128Stream stream;
  feed(stream, SourceKind::Unsupported);
  /* The opaque decoder cannot identify its held source. Ordinary frame reads must not
   * masquerade as source changes, so retain a stable unsupported record. */
  note_load(image, LoadSlot{view, 0, 0}, stream.finalize(), false);
}

bool angelo_image_source_loads_supported(const Image &image)
{
  std::scoped_lock lock(records_mutex);
  const AngeloImageSourceLoads *record = image.runtime->angelo_source_loads;
  if (record == nullptr || record->last_loads.is_empty()) {
    return false;
  }
  for (const AngeloImageSourceLoads::Load &load : record->last_loads.values()) {
    if (!load.supported) {
      return false;
    }
  }
  return true;
}

void angelo_image_source_restore_after_undo(Image &image)
{
  std::scoped_lock lock(records_mutex);
  if (const AngeloImageSourceLoads *record = image.runtime->angelo_source_loads) {
    image.runtime->angelo_source_revision.store(record->revision, std::memory_order_relaxed);
  }
}

void angelo_image_source_free(Image &image)
{
  std::scoped_lock lock(records_mutex);
  MEM_SAFE_DELETE(image.runtime->angelo_source_loads);
}

}  // namespace blender::bke
