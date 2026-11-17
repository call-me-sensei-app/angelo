/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup blenloader
 *
 * Angelo: which GPU backend a preference file from before Blender 5.3 comes out with.
 *
 * Blender 5.3 makes Vulkan the default on Windows x64 and Linux and moves every earlier
 * preference file to it (`versioning_userdef.cc`, 503.10), whatever the file says. An earlier
 * file cannot say whether its OpenGL was chosen: OpenGL was the default there, stored as the
 * same value. So there are two honest behaviors and no third:
 *
 * - move every earlier file to the default, as Blender does (the constant below is false), or
 * - keep what every earlier file says, so nobody who updates is moved and only new installs
 *   start with Vulkan (true).
 *
 * Either way a Vulkan start that dies before its first frame falls back to OpenGL at the next
 * launch (`wm_angelo_gpu_startup.cc`).
 */

#pragma once

#include "DNA_userdef_types.h"

namespace blender {

/** The one switch: whether a preference file from before 5.3 keeps its GPU backend. */
constexpr bool ANGELO_EARLIER_PREFERENCES_KEEP_GPU_BACKEND = false;

/** The backend a preference file from before 5.3 gets, where 5.3 changed the default: \a stored
 * is what the file says, \a keep_stored the switch. */
constexpr eUserPref_GPUBackendType angelo_gpu_backend_of_earlier_preferences(
    const eUserPref_GPUBackendType stored, const bool keep_stored)
{
  return keep_stored ? stored : USER_GPU_BACKEND_DEFAULT;
}

}  // namespace blender
