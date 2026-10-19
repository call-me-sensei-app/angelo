/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

#include <algorithm>
#include <array>
#include <cerrno>
#include <cstddef>
#include <cstdlib>
#include <cstring>
#include <limits>

#include <zlib.h>
#define ZSTD_STATIC_LINKING_ONLY
#include <zstd.h>
#include <zstd_errors.h>

#ifdef WIN32
#  include <io.h>
#else
#  include <unistd.h>
#endif

#include "BLI_filereader.h"

#include "BLO_metadata_scan.hh"

namespace blender::blo {

/** One accounting owner is shared by every pass and decoder. */
struct MetadataScanBudget {
  const BLOMetadataScanLimits &limits;
  BLOMetadataScanResult &result;
  BLOMetadataScanCancelled cancelled;
  void *userdata;

  bool fail(BLOMetadataScanStatus status, const char *message)
  {
    if (result.status == BLOMetadataScanStatus::OK) {
      result.status = status;
      result.message = message;
    }
    return false;
  }

  /** No failure so far; does not ask the cancellation callback. */
  bool ok() const
  {
    return result.status == BLOMetadataScanStatus::OK;
  }

  /**
   * No failure, and the caller has not cancelled. The callback is asked at every block, every
   * descriptor read and every codec step: the read boundaries at which Stop is honoured.
   */
  bool check()
  {
    if (!ok()) {
      return false;
    }
    if (cancelled && cancelled(userdata)) {
      return fail(BLOMetadataScanStatus::CANCELLED, "Metadata scan cancelled");
    }
    return true;
  }

  /** Charges are arithmetic on the hot path; the read boundaries around them poll. */
  bool charge(uint64_t &used, uint64_t count, uint64_t limit, const char *message)
  {
    if (!ok()) {
      return false;
    }
    if (used > limit || count > limit - used) {
      return fail(BLOMetadataScanStatus::LIMIT_EXCEEDED, message);
    }
    used += count;
    return true;
  }
};

/** Codec allocation hooks prevent frame declarations from choosing allocation sizes. */
class MetadataCodecMemory {
  struct alignas(std::max_align_t) Allocation {
    size_t bytes;
  };
  MetadataScanBudget &budget_;
  uint64_t used_ = 0;

 public:
  explicit MetadataCodecMemory(MetadataScanBudget &budget) : budget_(budget) {}

  static void *allocate(void *opaque, size_t bytes)
  {
    auto &self = *static_cast<MetadataCodecMemory *>(opaque);
    if (bytes > std::numeric_limits<size_t>::max() - sizeof(Allocation) ||
        !self.budget_.charge(self.used_,
                            bytes + sizeof(Allocation),
                            self.budget_.limits.decoder_bytes,
                            "Codec memory limit exceeded"))
    {
      return nullptr;
    }
    auto *allocation = static_cast<Allocation *>(std::malloc(bytes + sizeof(Allocation)));
    if (!allocation) {
      self.used_ -= bytes + sizeof(Allocation);
      self.budget_.fail(BLOMetadataScanStatus::LIMIT_EXCEEDED, "Codec allocation unavailable");
      return nullptr;
    }
    allocation->bytes = bytes + sizeof(Allocation);
    return allocation + 1;
  }

  static void release(void *opaque, void *address)
  {
    if (!address) {
      return;
    }
    auto &self = *static_cast<MetadataCodecMemory *>(opaque);
    auto *allocation = static_cast<Allocation *>(address) - 1;
    self.used_ -= allocation->bytes;
    std::free(allocation);
  }

  static voidpf gzip_allocate(voidpf opaque, uInt count, uInt bytes)
  {
    if (count && bytes > std::numeric_limits<size_t>::max() / count) {
      auto &self = *static_cast<MetadataCodecMemory *>(opaque);
      self.budget_.fail(BLOMetadataScanStatus::LIMIT_EXCEEDED, "Codec allocation overflow");
      return nullptr;
    }
    return allocate(opaque, size_t(count) * bytes);
  }
};

/**
 * This is deliberately not a generic FileReader change: normal file opening keeps its existing
 * seekable-frame cache. Metadata scanning always streams into bounded caller buffers.
 *
 * Plain files are read through #input_, one positioned read per 64 KiB window, rather than one
 * per block header: a rigged scene writes one small block per weighted vertex, and a system call
 * per block made the scan several times slower than Blender's own library listing. A skip past
 * the window only moves the offset, so large payloads are still never read.
 */
class MetadataScanReader {
  static constexpr size_t chunk_bytes = 64 * 1024;
  enum class Codec { Plain, Gzip, Zstandard };

  struct ReaderAdapter {
    FileReader file{};
    MetadataScanReader *owner = nullptr;
  } adapter_;

  MetadataScanBudget &budget_;
  MetadataCodecMemory memory_;
  int descriptor_;
  uint64_t physical_size_;
  uint64_t input_offset_ = 0;
  Codec codec_ = Codec::Plain;
  std::array<std::byte, chunk_bytes> input_;
  z_stream gzip_{};
  bool gzip_initialized_ = false;
  bool stream_finished_ = false;
  ZSTD_DCtx *zstd_ = nullptr;
  ZSTD_inBuffer zstd_input_{};
  /** Plain files only: #input_ holds the file bytes [plain_start_, plain_start_ + plain_bytes_).
   */
  uint64_t plain_start_ = 0;
  size_t plain_bytes_ = 0;
#ifdef WIN32
  int64_t initial_offset_ = -1;
#endif

  /**
   * One positioned read at #input_offset_, which it advances. Never reads past the size the
   * descriptor had when admitted; counts every byte read against the input budget.
   */
  int64_t raw_read(void *data, size_t bytes)
  {
    if (!budget_.check()) {
      return -1;
    }
    bytes = std::min<uint64_t>(bytes, physical_size_ - input_offset_);
    if (!bytes) {
      return 0;
    }
    if (bytes > budget_.limits.input_bytes - budget_.result.input_bytes) {
      budget_.fail(BLOMetadataScanStatus::LIMIT_EXCEEDED, "Encoded input limit exceeded");
      return -1;
    }
    int64_t read_bytes;
    do {
#ifdef WIN32
      if (_lseeki64(descriptor_, int64_t(input_offset_), SEEK_SET) < 0) {
        budget_.fail(BLOMetadataScanStatus::IO_ERROR, "Descriptor seek failed");
        return -1;
      }
      read_bytes = _read(descriptor_, data, unsigned(bytes));
#else
      read_bytes = pread(descriptor_, data, bytes, off_t(input_offset_));
#endif
    } while (read_bytes < 0 && errno == EINTR && budget_.check());
    if (read_bytes < 0) {
      budget_.fail(BLOMetadataScanStatus::IO_ERROR, "Descriptor read failed");
      return -1;
    }
    input_offset_ += uint64_t(read_bytes);
    budget_.result.input_bytes += uint64_t(read_bytes);
    return read_bytes;
  }

  /**
   * Plain files: copy from the buffered window, refilling it at the read offset when the offset
   * is outside it. The refill asks for a whole window, clamped to the file size and to the input
   * bytes still allowed, so read-ahead alone never fails a scan whose own reads fit the budget.
   */
  int64_t plain_read(void *data, size_t bytes)
  {
    size_t done = 0;
    while (done < bytes) {
      const uint64_t offset = input_offset_;
      if (offset < plain_start_ || offset >= plain_start_ + plain_bytes_) {
        const uint64_t allowed = budget_.limits.input_bytes - budget_.result.input_bytes;
        const size_t window = size_t(
            std::min<uint64_t>(input_.size(), std::max<uint64_t>(allowed, 1)));
        plain_bytes_ = 0;
        const int64_t got = raw_read(input_.data(), window);
        if (got < 0) {
          return -1;
        }
        plain_start_ = offset;
        plain_bytes_ = size_t(got);
        input_offset_ = offset;
        if (got == 0) {
          break; /* End of the admitted size: the caller reports an incomplete file. */
        }
      }
      const uint64_t available = plain_start_ + plain_bytes_ - offset;
      const size_t amount = size_t(std::min<uint64_t>(bytes - done, available));
      std::memcpy(
          static_cast<std::byte *>(data) + done, input_.data() + (offset - plain_start_), amount);
      done += amount;
      input_offset_ = offset + amount;
    }
    return int64_t(done);
  }

  int64_t decode_chunk(void *data, size_t bytes)
  {
    if (codec_ == Codec::Plain) {
      return plain_read(data, bytes);
    }
    if (stream_finished_) {
      return 0;
    }
    size_t produced = 0;
    while (produced < bytes && budget_.check()) {
      bool needs_input = codec_ == Codec::Gzip ? gzip_.avail_in == 0 :
                                                zstd_input_.pos == zstd_input_.size;
      if (needs_input) {
        const int64_t got = raw_read(input_.data(), input_.size());
        if (got < 0) {
          return -1;
        }
        /* A decoder can still have buffered output when its final input chunk is exhausted.
         * Give it empty input before deciding that the stream is truncated. */
        if (codec_ == Codec::Gzip) {
          gzip_.next_in = reinterpret_cast<Bytef *>(input_.data());
          gzip_.avail_in = uInt(got);
        }
        else {
          zstd_input_ = {input_.data(), size_t(got), 0};
        }
      }
      if (codec_ == Codec::Gzip) {
        gzip_.next_out = static_cast<Bytef *>(data) + produced;
        gzip_.avail_out = uInt(bytes - produced);
        const uInt before_in = gzip_.avail_in;
        const uInt before_out = gzip_.avail_out;
        const int status = inflate(&gzip_, Z_NO_FLUSH);
        produced += before_out - gzip_.avail_out;
        if (status == Z_STREAM_END) {
          stream_finished_ = true;
          break;
        }
        if (status != Z_OK || (before_in == gzip_.avail_in && before_out == gzip_.avail_out)) {
          budget_.fail(status == Z_MEM_ERROR ? BLOMetadataScanStatus::LIMIT_EXCEEDED :
                                              BLOMetadataScanStatus::INVALID,
                       "Gzip decoding failed");
          return -1;
        }
      }
      else {
        ZSTD_outBuffer output{data, bytes, produced};
        const size_t before_in = zstd_input_.pos;
        const size_t status = ZSTD_decompressStream(zstd_, &output, &zstd_input_);
        if (ZSTD_isError(status)) {
          budget_.fail(ZSTD_getErrorCode(status) == ZSTD_error_frameParameter_windowTooLarge ?
                           BLOMetadataScanStatus::LIMIT_EXCEEDED :
                           BLOMetadataScanStatus::INVALID,
                       "Zstandard decoding failed");
          return -1;
        }
        if (status == 0 && zstd_input_.pos == zstd_input_.size &&
            input_offset_ == physical_size_)
        {
          stream_finished_ = true;
          produced = output.pos;
          break;
        }
        if (before_in == zstd_input_.pos && produced == output.pos) {
          budget_.fail(BLOMetadataScanStatus::INVALID, "Zstandard decoder made no progress");
          return -1;
        }
        produced = output.pos;
      }
    }
    return budget_.check() ? int64_t(produced) : -1;
  }

  int64_t read(void *data, size_t bytes)
  {
    size_t done = 0;
    while (done < bytes && budget_.check()) {
      const size_t amount = std::min(bytes - done, chunk_bytes);
      if (amount > budget_.limits.decoded_bytes - budget_.result.decoded_bytes) {
        budget_.fail(BLOMetadataScanStatus::LIMIT_EXCEEDED, "Decoded work limit exceeded");
        return -1;
      }
      const int64_t got = decode_chunk(static_cast<std::byte *>(data) + done, amount);
      if (got < 0) {
        return -1;
      }
      done += size_t(got);
      adapter_.file.offset += got;
      budget_.result.decoded_bytes += uint64_t(got);
      if (got != int64_t(amount)) {
        break;
      }
    }
    return budget_.ok() ? int64_t(done) : -1;
  }

 public:
  MetadataScanReader(int descriptor, uint64_t physical_size, MetadataScanBudget &budget)
      : budget_(budget), memory_(budget), descriptor_(descriptor), physical_size_(physical_size)
  {
    adapter_.owner = this;
    adapter_.file.read = [](FileReader *file, void *data, size_t bytes) {
      return reinterpret_cast<ReaderAdapter *>(file)->owner->read(data, bytes);
    };
#ifdef WIN32
    initial_offset_ = _lseeki64(descriptor_, 0, SEEK_CUR);
#endif
  }

  ~MetadataScanReader()
  {
    if (gzip_initialized_) {
      inflateEnd(&gzip_);
    }
    ZSTD_freeDCtx(zstd_);
#ifdef WIN32
    if (initial_offset_ >= 0 && _lseeki64(descriptor_, initial_offset_, SEEK_SET) < 0) {
      budget_.fail(BLOMetadataScanStatus::IO_ERROR, "Descriptor cursor restoration failed");
    }
#endif
  }

  bool initialize()
  {
    std::array<unsigned char, 7> magic{};
    if (raw_read(magic.data(), magic.size()) != int64_t(magic.size())) {
      return budget_.fail(BLOMetadataScanStatus::INVALID, "Incomplete file magic");
    }
    input_offset_ = 0;
    if (std::memcmp(magic.data(), "BLENDER", 7) == 0) {
      codec_ = Codec::Plain;
      return true;
    }
    if (magic[0] == 0x1f && magic[1] == 0x8b) {
      codec_ = Codec::Gzip;
      gzip_.zalloc = MetadataCodecMemory::gzip_allocate;
      gzip_.zfree = MetadataCodecMemory::release;
      gzip_.opaque = &memory_;
      gzip_initialized_ = inflateInit2(&gzip_, 16 + MAX_WBITS) == Z_OK;
      return gzip_initialized_ ||
             budget_.fail(BLOMetadataScanStatus::INVALID, "Gzip initialization failed");
    }
    if (magic[0] == 0x28 && magic[1] == 0xb5 && magic[2] == 0x2f && magic[3] == 0xfd) {
      codec_ = Codec::Zstandard;
      const ZSTD_customMem allocator{MetadataCodecMemory::allocate,
                                     MetadataCodecMemory::release,
                                     &memory_};
      zstd_ = ZSTD_createDCtx_advanced(allocator);
      if (!zstd_ || ZSTD_isError(ZSTD_DCtx_setMaxWindowSize(zstd_,
                                                         budget_.limits.zstd_window_bytes)))
      {
        return budget_.fail(BLOMetadataScanStatus::INVALID,
                            "Zstandard initialization failed");
      }
      return true;
    }
    return budget_.fail(BLOMetadataScanStatus::INVALID, "Unrecognized file magic");
  }

  FileReader *file()
  {
    return &adapter_.file;
  }

  bool read_exact(void *data, size_t bytes)
  {
    return read(data, bytes) == int64_t(bytes) ||
           budget_.fail(BLOMetadataScanStatus::INVALID, "File is incomplete");
  }

  bool skip(uint64_t bytes)
  {
    if (codec_ == Codec::Plain) {
      if (bytes > physical_size_ - input_offset_) {
        return budget_.fail(BLOMetadataScanStatus::INVALID, "Block exceeds file size");
      }
      if (!budget_.charge(budget_.result.decoded_bytes,
                          bytes,
                          budget_.limits.decoded_bytes,
                          "Decoded work limit exceeded"))
      {
        return false;
      }
      input_offset_ += bytes;
      adapter_.file.offset += int64_t(bytes);
      return true;
    }
    std::array<std::byte, chunk_bytes> discarded;
    while (bytes) {
      const size_t amount = size_t(std::min<uint64_t>(bytes, discarded.size()));
      if (!read_exact(discarded.data(), amount)) {
        return false;
      }
      bytes -= amount;
    }
    return budget_.check();
  }

  /** Validate the final codec footer/seek-table stream as well as the ENDB payload. */
  bool finish()
  {
    if (codec_ == Codec::Plain) {
      return budget_.check();
    }
    std::array<std::byte, chunk_bytes> discarded;
    while (!stream_finished_) {
      if (!budget_.check()) {
        return false;
      }
      const uint64_t remaining = budget_.limits.decoded_bytes - budget_.result.decoded_bytes;
      /* One overflow byte distinguishes a zero-output checksum/footer from decoded content
       * beyond the budget. Failed probes report that byte and return no metadata. */
      const size_t amount = size_t(std::min<uint64_t>(remaining + 1, discarded.size()));
      const int64_t got = decode_chunk(discarded.data(), amount);
      if (got < 0) {
        return false;
      }
      budget_.result.decoded_bytes += uint64_t(got);
      if (uint64_t(got) > remaining) {
        return budget_.fail(BLOMetadataScanStatus::LIMIT_EXCEEDED,
                            "Decoded work limit exceeded");
      }
      adapter_.file.offset += got;
    }
    return budget_.check();
  }
};

}  // namespace blender::blo
