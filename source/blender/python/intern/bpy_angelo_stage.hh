/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

struct _object;

namespace blender {
/** Private, main-thread-only ownership transfer for admitted native stages. */
_object *BPY_angelo_stage_module();
}  // namespace blender
