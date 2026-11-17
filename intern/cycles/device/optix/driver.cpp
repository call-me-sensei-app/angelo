/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: Apache-2.0 */

#include "device/optix/driver.h"

#include "kernel/types.h"

#include "util/log.h"

#ifdef _WIN32
#  include "util/windows.h"
#else
#  include <dlfcn.h>
#endif

CCL_NAMESPACE_BEGIN

/* First driver with the OSL pipeline link failure, and the first one with NVIDIA's fix.
 * See #nvidia_driver_links_optix_osl.
 *
 * Evidence for the bounds (Blender issues #163420, #163703, #165252):
 * - Fail: 591.44 (#165252), 595.99 (#163703), 596.36, 596.49 and 597.06 (#163420), and 596.86
 *   (Tesla T4, where Blender 5.2's kernels still linked).
 * - Render: 581.80 and 582.66 below the range, 610.47, 610.88 and 615.71 above it (#163420,
 *   #163703).
 * - NVIDIA's engineer in #163420: introduced in the r595 drivers, fixed in r610 and later. The
 *   lower bound is 590 and not 595 because of the first-hand report on 591.44, and because the
 *   issue's triage found "the 590 series" affected. No driver from 590 to 594 is known to render;
 *   a report of one that does is the reason to raise the lower bound. */
static const int NVIDIA_DRIVER_OSL_LINK_BROKEN_SINCE = 590;
static const int NVIDIA_DRIVER_OSL_LINK_FIXED_IN = 610;

/* Parse a run of decimal digits at `pos`, advancing it. No value without a digit, or for a number
 * too long to be a version component. */
static std::optional<int> parse_version_component(const string &text, size_t &pos)
{
  const size_t start = pos;
  int value = 0;
  while (pos < text.size() && text[pos] >= '0' && text[pos] <= '9') {
    if (pos - start >= 6) {
      return std::nullopt;
    }
    value = value * 10 + (text[pos] - '0');
    pos++;
  }
  if (pos == start) {
    return std::nullopt;
  }
  return value;
}

std::optional<NvidiaDriverVersion> nvidia_driver_version_parse(const string &text)
{
  /* Spaces and line ends around the version are not part of it. */
  const char *whitespace = " \t\r\n";
  const size_t first = text.find_first_not_of(whitespace);
  if (first == string::npos) {
    return std::nullopt;
  }
  const string trimmed = text.substr(first, text.find_last_not_of(whitespace) - first + 1);

  /* Two or more components separated by single dots, and nothing else. */
  vector<int> components;
  size_t pos = 0;
  while (true) {
    const std::optional<int> component = parse_version_component(trimmed, pos);
    if (!component.has_value()) {
      return std::nullopt;
    }
    components.push_back(*component);
    if (pos == trimmed.size()) {
      break;
    }
    if (trimmed[pos] != '.') {
      return std::nullopt;
    }
    pos++;
  }
  if (components.size() < 2) {
    return std::nullopt;
  }

  NvidiaDriverVersion version;
  version.major = components[0];
  version.minor = components[1];
  return version;
}

/* The part of NVML used here. Every NVIDIA driver installs this library, `nvidia-smi` is built
 * on it. Its functions return 0 on success. */
using NvmlInit = int (*)();
using NvmlShutdown = int (*)();
using NvmlSystemGetDriverVersion = int (*)(char *version, unsigned int length);

/* Size NVML documents for the version string (NVML_SYSTEM_DRIVER_VERSION_BUFFER_SIZE). */
static const unsigned int NVML_DRIVER_VERSION_BUFFER_SIZE = 80;

/* Only plain data in here: on Windows the calls into the driver's library are guarded the way
 * CUDA initialization is, and such a guard does not allow objects with destructors. */
static bool nvml_call_driver_version(const NvmlInit init,
                                     const NvmlShutdown shutdown,
                                     const NvmlSystemGetDriverVersion get_driver_version,
                                     char *buffer,
                                     const unsigned int buffer_size)
{
#ifdef _MSC_VER
  __try {
#endif
    if (init() != 0) {
      return false;
    }
    const bool ok = get_driver_version(buffer, buffer_size) == 0;
    shutdown();
    return ok;
#ifdef _MSC_VER
  }
  __except (EXCEPTION_EXECUTE_HANDLER) {
    /* A broken driver install: go on without a version. */
    return false;
  }
#endif
}

static std::optional<NvidiaDriverVersion> nvml_read_driver_version()
{
  char buffer[NVML_DRIVER_VERSION_BUFFER_SIZE] = {0};
  bool ok = false;

#ifdef _WIN32
  /* The driver installs `nvml.dll` in the system directory. Never load it from anywhere else. */
  const HMODULE library = LoadLibraryExW(L"nvml.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
  if (library == nullptr) {
    return std::nullopt;
  }
  const NvmlInit init = reinterpret_cast<NvmlInit>(GetProcAddress(library, "nvmlInit_v2"));
  const NvmlShutdown shutdown = reinterpret_cast<NvmlShutdown>(
      GetProcAddress(library, "nvmlShutdown"));
  const NvmlSystemGetDriverVersion get_driver_version =
      reinterpret_cast<NvmlSystemGetDriverVersion>(
          GetProcAddress(library, "nvmlSystemGetDriverVersion"));
  if (init && shutdown && get_driver_version) {
    ok = nvml_call_driver_version(init, shutdown, get_driver_version, buffer, sizeof(buffer));
  }
  FreeLibrary(library);
#else
  /* Linux. The same code is built on macOS, where no such library exists and nothing is read. */
  void *library = dlopen("libnvidia-ml.so.1", RTLD_NOW | RTLD_LOCAL);
  if (library == nullptr) {
    return std::nullopt;
  }
  const NvmlInit init = reinterpret_cast<NvmlInit>(dlsym(library, "nvmlInit_v2"));
  const NvmlShutdown shutdown = reinterpret_cast<NvmlShutdown>(dlsym(library, "nvmlShutdown"));
  const NvmlSystemGetDriverVersion get_driver_version =
      reinterpret_cast<NvmlSystemGetDriverVersion>(dlsym(library, "nvmlSystemGetDriverVersion"));
  if (init && shutdown && get_driver_version) {
    ok = nvml_call_driver_version(init, shutdown, get_driver_version, buffer, sizeof(buffer));
  }
  dlclose(library);
#endif

  if (!ok) {
    return std::nullopt;
  }
  buffer[sizeof(buffer) - 1] = '\0';
  return nvidia_driver_version_parse(buffer);
}

std::optional<NvidiaDriverVersion> nvidia_driver_version_installed()
{
  static const std::optional<NvidiaDriverVersion> version = []() {
    const std::optional<NvidiaDriverVersion> read = nvml_read_driver_version();
    if (read.has_value()) {
      LOG_INFO << "NVIDIA driver version: " << read->major << "." << read->minor;
    }
    else {
      LOG_INFO << "NVIDIA driver version: not available";
    }
    return read;
  }();
  return version;
}

bool nvidia_driver_links_optix_osl(const NvidiaDriverVersion &version)
{
  return version.major < NVIDIA_DRIVER_OSL_LINK_BROKEN_SINCE ||
         version.major >= NVIDIA_DRIVER_OSL_LINK_FIXED_IN;
}

string optix_osl_driver_error(const std::optional<NvidiaDriverVersion> &version,
                              const uint64_t kernel_features)
{
  /* An OSL camera alone is linked into the same pipeline and fails the same way (Tesla T4,
   * driver 596.86: link error with node shading and a camera script). */
  if ((kernel_features & (KERNEL_FEATURE_OSL_SHADING | KERNEL_FEATURE_OSL_CAMERA)) == 0) {
    return "";
  }
  if (!version.has_value() || nvidia_driver_links_optix_osl(*version)) {
    return "";
  }
  /* One constant sentence, without the installed version, so that the interface can translate
   * it as a whole. The version is in the log. */
  return "Open Shading Language cannot render on OptiX with the installed NVIDIA driver because "
         "of a driver bug. Update the NVIDIA driver to version 610 or newer, or use a 580 series "
         "driver";
}

CCL_NAMESPACE_END
