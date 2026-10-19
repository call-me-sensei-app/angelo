/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

struct _object;

namespace blender {
/** Main-thread progress and stop for the window-manager jobs the Angelo job contract starts. */
_object *BPY_angelo_jobs_module();
}  // namespace blender
