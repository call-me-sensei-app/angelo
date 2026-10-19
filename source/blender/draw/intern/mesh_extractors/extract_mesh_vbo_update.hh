/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

#include <algorithm>

#include "BLI_array.hh"

#include "GPU_vertex_buffer.hh"

namespace blender::draw {

/**
 * Upload a contiguous attribute range in bounded chunks. Subdivision's loose geometry can contain
 * many vertices, so avoid both a GPU command per element and an unbounded temporary CPU buffer.
 * The callback fills every element in the chunk; its range is relative to the uploaded range.
 */
template<typename T, typename FillFn>
void extract_mesh_vbo_update(gpu::VertBuf &vbo,
                             const int start,
                             const int size,
                             const FillFn &fill)
{
  if (size == 0) {
    return;
  }
  BLI_assert(start >= 0 && size > 0);
  BLI_assert(sizeof(T) == vbo.format.stride);
  BLI_assert(int64_t(start) + size <= vbo.vertex_len);

  /* At most 64 KiB, independent of mesh size. */
  constexpr int max_chunk_size = 65536 / sizeof(T);
  Array<T> buffer(std::min(size, max_chunk_size));
  GPU_vertbuf_use(&vbo);
  for (int offset = 0; offset < size;) {
    const int chunk_size = std::min<int>(buffer.size(), size - offset);
    MutableSpan<T> chunk = buffer.as_mutable_span().take_front(chunk_size);
    fill(IndexRange(offset, chunk_size), chunk);
    GPU_vertbuf_update_sub(
        &vbo, (start + offset) * sizeof(T), chunk.size_in_bytes(), chunk.data());
    offset += chunk_size;
  }
}

}  // namespace blender::draw
