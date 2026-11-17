/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: Apache-2.0 */

#pragma once

#include <cstdint>
#include <optional>

#include "util/string.h"

CCL_NAMESPACE_BEGIN

/* What the installed NVIDIA GPU driver can do for the OptiX device, decided from its version.
 *
 * Everything here builds and runs on every platform and without the OptiX SDK, so the decisions
 * are covered by unit tests on machines that have no NVIDIA GPU. */

/* Version of the NVIDIA GPU driver as NVIDIA numbers it, for example 596.86. */
struct NvidiaDriverVersion {
  int major = 0;
  int minor = 0;
};

/* Parse a driver version as the driver reports it: "596.86" on Windows, "595.58.03" on Linux.
 * Anything that is not exactly such a version gives no value. */
std::optional<NvidiaDriverVersion> nvidia_driver_version_parse(const string &text);

/* Version of the installed NVIDIA driver, read once per process from the management library the
 * driver installs (NVML). No value when there is no NVIDIA driver, or when it can not be read. */
std::optional<NvidiaDriverVersion> nvidia_driver_version_installed();

/* Whether this driver links an OptiX pipeline for Open Shading Language.
 *
 * The drivers from 590 up to, not including, 610 fail to link the shading pipeline of the OSL
 * kernels (OPTIX_ERROR_PIPELINE_LINK_ERROR): with OSL shading after minutes of compiling them,
 * with only an OSL camera within seconds. NVIDIA fixed this in the 610 drivers, the 580 drivers
 * never had it, and nothing on the application side works around it. See Blender issue #163420. */
bool nvidia_driver_links_optix_osl(const NvidiaDriverVersion &version);

/* The error to end kernel loading with when the OptiX device can not render `kernel_features`
 * with the given driver (OSL shading or an OSL camera on a driver that does not link them), or an
 * empty string when it can. An unknown version is never an error: loading then goes on as it
 * would without this check. */
string optix_osl_driver_error(const std::optional<NvidiaDriverVersion> &version,
                              uint64_t kernel_features);

CCL_NAMESPACE_END
