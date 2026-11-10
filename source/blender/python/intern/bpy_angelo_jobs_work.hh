/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

struct PyMethodDef;

namespace blender {
/** `_bpy._angelo_jobs.running_work` and the asset catalog checks: what quitting now would lose (main thread). */
PyMethodDef *BPY_angelo_jobs_work_methods();
}  // namespace blender
