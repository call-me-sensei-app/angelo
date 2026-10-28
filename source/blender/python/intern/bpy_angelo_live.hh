/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

struct _object;

namespace blender {
/** A worker's saved part read into a private Main, evaluated and drawn there (`_bpy._angelo_live`). */
_object *BPY_angelo_live_module();
}  // namespace blender
