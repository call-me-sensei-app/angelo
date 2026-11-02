/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup overlay
 */

#include <atomic>
#include <chrono>
#include <thread>

#include "CLG_log.h"

#include "BLI_array.hh"
#include "BLI_sys_types.h"

#include "BKE_image.hh"

#include "GPU_texture.hh"

#include "overlay_uv_checker_patterns.hh"

namespace blender::draw::overlay::uv_checker {

/* `--log draw.uv_checker --log-level debug`: how long each image took to make and to upload. */
static CLG_LogRef LOG = {"draw.uv_checker"};

static double ms_since(const std::chrono::steady_clock::time_point start)
{
  return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
}

/** A new generated Image's size (Blender's default). */
static constexpr int SIZE = 1024;
/** log2(SIZE) + 1. */
static constexpr int MIP_LEN = 11;

struct Pattern {
  std::thread worker;
  std::atomic<bool> filled = false;
  double fill_ms = 0.0;
  Array<uchar> pixels;
  gpu::Texture *texture = nullptr;
};

static Pattern patterns[2];
static gpu::Texture *placeholder_texture = nullptr;

gpu::Texture *pattern(const Image image, const bool wait)
{
  Pattern &item = patterns[int(image)];
  if (item.texture) {
    return item.texture;
  }
  if (!item.worker.joinable()) {
    item.pixels = Array<uchar>(size_t(SIZE) * SIZE * 4);
    item.worker = std::thread([&item, image]() {
      const auto start = std::chrono::steady_clock::now();
      if (image == Image::Checker) {
        BKE_image_buf_fill_checker(item.pixels.data(), nullptr, SIZE, SIZE);
      }
      else {
        BKE_image_buf_fill_checker_color(item.pixels.data(), nullptr, SIZE, SIZE);
      }
      item.fill_ms = ms_since(start);
      item.filled = true;
    });
  }
  if (!item.filled && !wait) {
    return nullptr;
  }
  const auto wait_start = std::chrono::steady_clock::now();
  item.worker.join();
  const double wait_ms = ms_since(wait_start);
  /* Upload and mipmaps on the GPU: the only part on the drawing thread. */
  const auto upload_start = std::chrono::steady_clock::now();
  item.texture = GPU_texture_create_2d("uv_checker_pattern",
                                       SIZE,
                                       SIZE,
                                       MIP_LEN,
                                       gpu::TextureFormat::UNORM_8_8_8_8,
                                       GPU_TEXTURE_USAGE_SHADER_READ | GPU_TEXTURE_USAGE_SHADER_WRITE,
                                       nullptr);
  GPU_texture_update(item.texture, GPU_DATA_UBYTE, item.pixels.data());
  const double upload_ms = ms_since(upload_start);
  const auto mip_start = std::chrono::steady_clock::now();
  GPU_texture_update_mipmap_chain(item.texture);
  const double mip_ms = ms_since(mip_start);
  item.pixels = {};
  CLOG_DEBUG(&LOG,
             "%s image: made in %.2f ms on a worker (the drawing thread waited %.2f ms), uploaded in %.2f ms, "
             "mipmaps queued in %.2f ms",
             image == Image::Checker ? "UV Grid" : "Color Grid",
             item.fill_ms,
             wait_ms,
             upload_ms,
             mip_ms);
  return item.texture;
}

gpu::Texture *placeholder()
{
  if (placeholder_texture == nullptr) {
    const float white[4] = {1.0f, 1.0f, 1.0f, 1.0f};
    placeholder_texture = GPU_texture_create_2d("uv_checker_placeholder",
                                                1,
                                                1,
                                                1,
                                                gpu::TextureFormat::UNORM_8_8_8_8,
                                                GPU_TEXTURE_USAGE_SHADER_READ,
                                                white);
  }
  return placeholder_texture;
}

void free_patterns()
{
  for (Pattern &item : patterns) {
    if (item.worker.joinable()) {
      item.worker.join();
    }
    item.filled = false;
    item.pixels = {};
    GPU_TEXTURE_FREE_SAFE(item.texture);
  }
  GPU_TEXTURE_FREE_SAFE(placeholder_texture);
}

}  // namespace blender::draw::overlay::uv_checker
