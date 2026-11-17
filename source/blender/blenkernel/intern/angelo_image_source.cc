/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup bke
 *
 * Angelo: `Image.angelo_source_revision` (see BKE_angelo_image_source.hh).
 */

#include <algorithm>
#include <array>
#include <atomic>
#include <cstring>
#include <ctime>
#include <mutex>
#include <optional>
#include <sys/stat.h>

#ifdef WIN32
#  include <io.h>
#  include <windows.h>
#else
#  include <unistd.h>
#endif

#include "MEM_guardedalloc.h"

#include "BLI_array.hh"
#include "BLI_file_time.hh"
#include "BLI_fileops.hh"
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
  if (!BLI_file_descriptor_is_open(fd)) {
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

/* -------------------------------------------------------------------- */
/** \name Racily clean loads
 *
 * A file's metadata decides whether it changed between two loads, unless the second write can
 * share every time field with the first: two writes within one tick of the clock the file system
 * stamps times from (about 15.6 ms on NTFS, a jiffy for Linux's ctime, 2 s on FAT, more on some
 * shares) leave the same stamp, and a writer can also put the write time back. So a load that
 * finds the file changed within #racy_window_ns of now (or in the future, as a share's skewed
 * clock can) also digests the bytes, as git does for a racily clean index entry. The slot's next
 * load digests them too, so a rewrite in the same tick is found after the window has passed. Two
 * loads compare their digests only when both have one: a file whose loads move out of the window
 * never changes its record by that alone. A file last changed well before it is loaded costs
 * nothing.
 * \{ */

/**
 * The later of a file's two times, as nanoseconds since 1970. A time outside what 64-bit
 * nanoseconds hold (before 1677, after 2262) is the nearest value that fits (BLI_file_time.hh):
 * today's Windows time alone, 100 ns steps since 1601, does not fit when it is multiplied out.
 */
int64_t stamp_latest_ns(const FileStamp &stamp)
{
#ifdef WIN32
  return file_time::nanoseconds_from_filetime(std::max(stamp.modified, stamp.changed));
#else
  return std::max(
      file_time::nanoseconds_saturated(int64_t(stamp.modified), int64_t(stamp.modified_ns)),
      file_time::nanoseconds_saturated(int64_t(stamp.changed), int64_t(stamp.changed_ns)));
#endif
}

/** Now, on the same clock and epoch as #stamp_latest_ns. */
int64_t now_ns()
{
#ifdef WIN32
  FILETIME now;
  GetSystemTimePreciseAsFileTime(&now);
  return file_time::nanoseconds_from_filetime((uint64_t(now.dwHighDateTime) << 32) |
                                              uint64_t(now.dwLowDateTime));
#else
  std::timespec now{};
  std::timespec_get(&now, TIME_UTC);
  return file_time::nanoseconds_saturated(int64_t(now.tv_sec), int64_t(now.tv_nsec));
#endif
}

/** Covers the coarsest common file-time clock (FAT's 2 s) and some skew between clocks. */
std::atomic<int64_t> racy_window_ns = int64_t(3) * 1000000000;
std::atomic<int64_t> content_digests = 0;

/**
 * The bytes the decoder will read, digested through the same descriptor without moving it
 * (XXH3, far faster than decoding the same bytes). False when they cannot all be read.
 */
bool digest_file_bytes(const int fd, LoadIdentity &r_digest)
{
  if (!BLI_file_descriptor_is_open(fd)) {
    return false;
  }
  content_digests.fetch_add(1, std::memory_order_relaxed);
  XXH3_128Stream stream;
  constexpr int64_t chunk = 1 << 20;
  Array<uint8_t> buffer(chunk, NoInitialization());
  bool ok = true;
#ifdef WIN32
  const int64_t start = _lseeki64(fd, 0, SEEK_CUR);
  ok = start != -1 && _lseeki64(fd, 0, SEEK_SET) != -1;
  while (ok) {
    const int got = _read(fd, buffer.data(), unsigned(chunk));
    if (got <= 0) {
      ok = got == 0;
      break;
    }
    stream.update(buffer.data(), size_t(got));
  }
  ok = start != -1 && _lseeki64(fd, start, SEEK_SET) != -1 && ok;
#else
  for (int64_t offset = 0;; ) {
    const ssize_t got = pread(fd, buffer.data(), size_t(chunk), off_t(offset));
    if (got <= 0) {
      ok = got == 0;
      break;
    }
    stream.update(buffer.data(), size_t(got));
    offset += got;
  }
#endif
  r_digest = stream.finalize();
  return ok;
}

/** \} */

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
    /** The file had changed within the racy window: the slot's next load digests the bytes too. */
    bool racy = false;
    /** The bytes' digest, when the load took one (only compared with another load's). */
    std::optional<LoadIdentity> content;
  };
  Map<LoadSlot, Load> last_loads;
};

uint64_t angelo_image_source_revision_fresh()
{
  return next_revision.fetch_add(1, std::memory_order_relaxed);
}

/** Records allocated and not yet freed (#angelo_image_source_records_in_use). */
static std::atomic<int64_t> records_in_use = 0;

static AngeloImageSourceLoads &ensure_record(Image &image)
{
  ImageRuntime &runtime = *image.runtime;
  if (runtime.angelo_source_loads == nullptr) {
    runtime.angelo_source_loads = MEM_new<AngeloImageSourceLoads>(__func__);
    records_in_use.fetch_add(1, std::memory_order_relaxed);
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

static void note_load(Image &image, const LoadSlot &slot, const AngeloImageSourceLoads::Load &load)
{
  std::scoped_lock lock(records_mutex);
  AngeloImageSourceLoads &record = ensure_record(image);
  bool changed = false;
  record.last_loads.add_or_modify(
      slot,
      [&](AngeloImageSourceLoads::Load *value) { new (value) AngeloImageSourceLoads::Load(load); },
      [&](AngeloImageSourceLoads::Load *value) {
        changed = value->identity != load.identity || value->supported != load.supported ||
                  (value->content && load.content && *value->content != *load.content);
        *value = load;
      });
  if (changed) {
    bump_locked(image, record);
  }
}

/** Whether the image's last load of this slot was racy (its next load digests the bytes too). */
static bool last_load_racy(const Image *image, const LoadSlot &slot)
{
  if (image == nullptr) {
    return false;
  }
  std::scoped_lock lock(records_mutex);
  const AngeloImageSourceLoads *record = image->runtime->angelo_source_loads;
  const AngeloImageSourceLoads::Load *load = record ? record->last_loads.lookup_ptr(slot) :
                                                      nullptr;
  return load != nullptr && load->racy;
}

AngeloImageSourceFileWitness angelo_image_source_prepare_file_load(const int file_descriptor,
                                                                  const char *filepath,
                                                                  const Image *image,
                                                                  const int view,
                                                                  const int tile,
                                                                  const int frame)
{
  XXH3_128Stream stream;
  FileStamp stamp;
  const bool supported = file_stamp(file_descriptor, stamp);
  AngeloImageSourceFileWitness witness;
  if (supported) {
    feed(stream, SourceKind::File);
    feed(stream, stamp.device);
    feed(stream, stamp.inode);
    feed(stream, stamp.bytes);
    feed(stream, stamp.modified);
    feed(stream, stamp.modified_ns);
    feed(stream, stamp.changed);
    feed(stream, stamp.changed_ns);
    /* The hash never runs while #records_mutex is held. */
    witness.racy = file_time::changed_within(
        stamp_latest_ns(stamp), now_ns(), racy_window_ns.load(std::memory_order_relaxed));
    if (witness.racy || last_load_racy(image, LoadSlot{view, tile, frame})) {
      LoadIdentity digest;
      if (!digest_file_bytes(file_descriptor, digest)) {
        digest.fill(0); /* Unreadable now: never the digest of bytes that were read. */
        witness.racy = true;
      }
      witness.content = digest;
    }
  }
  else {
    feed(stream, SourceKind::MissingFile);
  }
  feed_text(stream, filepath);
  witness.identity = stream.finalize();
  witness.supported = supported;
  return witness;
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
  note_load(image,
            LoadSlot{view, tile, frame},
            {stream.finalize(), witness.supported, witness.racy, witness.content});
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
  const bool supported = packed_file.data != nullptr && size > 0;
  note_load(image, LoadSlot{view, tile, 0}, {stream.finalize(), supported});
}

void angelo_image_source_note_unsupported_load(Image &image, const int view)
{
  XXH3_128Stream stream;
  feed(stream, SourceKind::Unsupported);
  /* The opaque decoder cannot identify its held source. Ordinary frame reads must not
   * masquerade as source changes, so retain a stable unsupported record. */
  note_load(image, LoadSlot{view, 0, 0}, {stream.finalize(), false});
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
  if (image.runtime->angelo_source_loads != nullptr) {
    records_in_use.fetch_sub(1, std::memory_order_relaxed);
  }
  MEM_SAFE_DELETE(image.runtime->angelo_source_loads);
}

int64_t angelo_image_source_records_in_use()
{
  return records_in_use.load(std::memory_order_relaxed);
}

int64_t angelo_image_source_content_digests()
{
  return content_digests.load(std::memory_order_relaxed);
}

int64_t angelo_image_source_set_racy_window_ns(const int64_t window_ns)
{
  return racy_window_ns.exchange(window_ns, std::memory_order_relaxed);
}

}  // namespace blender::bke
