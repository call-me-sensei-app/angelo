/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup wm
 *
 * The checkpoint RUNTIME companion's write loop, outside the capture so a GTest can drive it with
 * short, interrupted and zero writes (wm_checkpoint_write_test.cc).
 */

#include <algorithm>
#include <cerrno>
#include <climits>

#ifdef WIN32
#  include <io.h>
#else
#  include <unistd.h>
#endif

#include "BLI_hash_xxh3.hh"

#include "wm_checkpoint.hh"

namespace blender {

int64_t WM_checkpoint_descriptor_write(const int descriptor, const void *data, const size_t size)
{
#ifdef WIN32
  return _write(descriptor, data, unsigned(std::min<size_t>(size, INT_MAX)));
#else
  return ::write(descriptor, data, size);
#endif
}

bool WM_checkpoint_write_all(const int descriptor,
                             Span<std::byte> bytes,
                             XXH3_128Stream *content,
                             const WMCheckpointWriteFn write_fn)
{
  while (!bytes.is_empty()) {
    errno = 0;
    const int64_t count = write_fn(descriptor, bytes.data(), size_t(bytes.size()));
    if (count < 0 && errno == EINTR) {
      continue;
    }
    if (count <= 0 || count > bytes.size()) {
      /* An error, a write that makes no progress, or a count the call cannot have written. */
      return false;
    }
    /* Exactly the bytes this call accepted, in stream order: a short write hashes its prefix and
     * the rest is offered again, so the binding covers what the descriptor holds. */
    if (content) {
      content->update(bytes.take_front(count));
    }
    bytes = bytes.drop_front(count);
  }
  return true;
}

}  // namespace blender
