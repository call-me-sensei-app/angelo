/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup overlay
 *
 * Angelo: the viewport UV checker's pattern images (doc/angelo/uv_checker.rst, "Drawing"). Each is made once per
 * session on a worker thread, by the functions that make a new generated Image, and shared by every viewport; nothing
 * is made for a pattern nobody shows.
 */

#pragma once

namespace blender::gpu {
class Texture;
}

namespace blender::draw::overlay::uv_checker {

/** #V3D_UV_CHECKER_COLOR_GRID or #V3D_UV_CHECKER_CHECKER. */
enum class Image { ColorGrid = 0, Checker = 1 };

/**
 * The pattern's texture, or null while a worker thread still makes it (the caller draws without it and asks for
 * another redraw). With `wait` it is finished here: for draws that have no next redraw (offscreen, render).
 */
gpu::Texture *pattern(Image image, bool wait);

/** A 1 x 1 texture the shader binds where it samples no pattern (texel density, stretch). */
gpu::Texture *placeholder();

/** Join the workers and free the textures (the overlay engine's static data is freed). */
void free_patterns();

}  // namespace blender::draw::overlay::uv_checker
