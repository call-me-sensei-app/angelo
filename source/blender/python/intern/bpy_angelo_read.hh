/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

struct _object;

namespace blender {
/** Bounded reads which bypass RNA getters that allocate, load, or validate saved storage. */
_object *BPY_angelo_read_module();
}  // namespace blender
