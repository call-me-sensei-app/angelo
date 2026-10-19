/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

struct _object;

namespace blender {
/** Save a picture as a render would, without writing the scene (`_bpy._angelo_picture`). */
_object *BPY_angelo_picture_module();
}  // namespace blender
