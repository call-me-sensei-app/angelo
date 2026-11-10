/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

struct _object;

namespace blender {
/** The file a document save wrote, as the save identified it (`_bpy._angelo_saved`). */
_object *BPY_angelo_saved_module();
}  // namespace blender
