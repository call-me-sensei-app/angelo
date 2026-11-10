/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

struct _object;

namespace blender {
/** What a bake read that Python cannot reach, for the baked-material digest (`_bpy._angelo_digest`). */
_object *BPY_angelo_digest_module();
}  // namespace blender
