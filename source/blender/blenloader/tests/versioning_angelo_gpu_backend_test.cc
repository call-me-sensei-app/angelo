/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "testing/testing.h"

#include "intern/versioning_angelo_gpu_backend.hh"

namespace blender::tests {

static const eUserPref_GPUBackendType STORED[] = {
    USER_GPU_BACKEND_OPENGL,
    USER_GPU_BACKEND_VULKAN,
    USER_GPU_BACKEND_METAL,
};

TEST(versioning_angelo_gpu_backend, MovedToTheDefaultAsBlenderDoes)
{
  /* Whatever an earlier file says: its OpenGL cannot be told from the earlier default. */
  for (const eUserPref_GPUBackendType stored : STORED) {
    EXPECT_EQ(angelo_gpu_backend_of_earlier_preferences(stored, false), USER_GPU_BACKEND_DEFAULT);
  }
}

TEST(versioning_angelo_gpu_backend, KeptWhenTheSwitchIsOn)
{
  for (const eUserPref_GPUBackendType stored : STORED) {
    EXPECT_EQ(angelo_gpu_backend_of_earlier_preferences(stored, true), stored);
  }
}

TEST(versioning_angelo_gpu_backend, TheSwitchShipsAsBlenderBehaves)
{
  /* Changing the shipped behavior is a decision: this fails until the test says so too. */
  EXPECT_FALSE(ANGELO_EARLIER_PREFERENCES_KEEP_GPU_BACKEND);
}

}  // namespace blender::tests
