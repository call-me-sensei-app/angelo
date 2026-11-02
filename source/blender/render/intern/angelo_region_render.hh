/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * \ingroup render
 *
 * Angelo: the render pipeline's side of a region render over the last render
 * (RE_angelo_region.hh has the contract and the caller's side).
 */

namespace blender {

struct Render;
struct RenderResult;

namespace render::angelo {

/**
 * A rendered tile (`tile->tilerect` relative to the rendered result) also lands on the previous
 * frame shown during the render, inside the region only. Render thread, like the tile's merge.
 */
void region_render_merge_tile(Render *re, const RenderResult *tile);

/**
 * Where an ordinary border render inserts its result into a black frame: inserts the region
 * of `re->result` into the previous frame instead, which becomes `re->result`. When `cancelled`,
 * the previous frame comes back untouched. Returns false when `re` is not armed.
 * The caller holds the result write lock.
 */
bool region_render_compose(Render *re, bool cancelled);

/** The result a display shows while `re` renders a region: the previous frame, or null. */
RenderResult *region_render_display_result(const Render *re);

/** A new render starts on `re`: a finished region render's kept pixels go, an armed one stays. */
void region_render_new_render(Render *re);

}  // namespace render::angelo
}  // namespace blender
