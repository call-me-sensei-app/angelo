/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

#include <algorithm>
#include <cstddef>
#include <string>
#include <vector>

namespace blender {

class WriteWrap {
 public:
  virtual ~WriteWrap() = default;
  virtual bool open(const char *filepath) = 0;
  virtual bool close() = 0;
  virtual bool write(const void *buf, size_t buf_len) = 0;

  /** Buffer output (only needed when output is not already buffered). */
  bool use_buf = true;
};

/** Delay compression until the actual serialized size exceeds a bounded prefix.
 *
 * Small temporary files avoid compressor setup and decompression. Large files
 * retain the normal compressed stream, without serializing their IDs twice.
 * Both writers address the same temporary file, never the final destination.
 */
class MinimumSizeWriteWrap : public WriteWrap {
 private:
  WriteWrap &raw_;
  WriteWrap &compressed_;
  const size_t limit_;
  std::string filepath_;
  std::vector<char> prefix_;
  bool compressed_open_ = false;
  bool raw_open_ = false;
  bool failed_ = false;

  bool start_compression()
  {
    if (!raw_.close()) {
      raw_open_ = false;
      return false;
    }
    raw_open_ = false;
    if (!compressed_.open(filepath_.c_str())) {
      return false;
    }
    compressed_open_ = true;
    /* Match the normal writer's 1 MiB chunks, preserving parallel compression. */
    for (size_t offset = 0; offset < prefix_.size(); offset += 1 << 20) {
      const size_t size = std::min(size_t(1 << 20), prefix_.size() - offset);
      if (!compressed_.write(prefix_.data() + offset, size)) {
        return false;
      }
    }
    std::vector<char>().swap(prefix_);
    return true;
  }

 public:
  MinimumSizeWriteWrap(WriteWrap &raw, WriteWrap &compressed, size_t limit)
      : raw_(raw), compressed_(compressed), limit_(limit)
  {
  }

  bool open(const char *filepath) override
  {
    filepath_ = filepath;
    raw_open_ = raw_.open(filepath);
    return raw_open_;
  }

  bool write(const void *buf, size_t size) override
  {
    if (failed_) {
      return false;
    }
    if (size == 0) {
      return true;
    }
    if (compressed_open_) {
      return !(failed_ = !compressed_.write(buf, size));
    }
    if (size <= limit_ - prefix_.size()) {
      /* Reserve exactly the bound, avoiding vector growth beyond the limit. */
      if (prefix_.capacity() == 0) {
        prefix_.reserve(limit_);
      }
      const char *bytes = static_cast<const char *>(buf);
      prefix_.insert(prefix_.end(), bytes, bytes + size);
      return true;
    }
    failed_ = !start_compression() || !compressed_.write(buf, size);
    return !failed_;
  }

  bool close() override
  {
    if (!failed_ && raw_open_) {
      if (raw_.write(prefix_.data(), prefix_.size())) {
        raw_open_ = false;
        return raw_.close();
      }
      /* A small raw file may not fit on disk. Retry the retained prefix using
       * compression, truncating the same temporary file, with no ID re-copy. */
      failed_ = !start_compression();
    }
    const bool closed = compressed_open_ ? compressed_.close() : raw_open_ ? raw_.close() : true;
    compressed_open_ = raw_open_ = false;
    return closed && !failed_;
  }
};

}  // namespace blender
