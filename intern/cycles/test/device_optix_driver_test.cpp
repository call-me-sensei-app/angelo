/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: Apache-2.0 */

#include <gtest/gtest.h>

#include "device/optix/driver.h"

#include "kernel/types.h"

CCL_NAMESPACE_BEGIN

static NvidiaDriverVersion version(const int major, const int minor)
{
  NvidiaDriverVersion result;
  result.major = major;
  result.minor = minor;
  return result;
}

/* ******** Tests for nvidia_driver_version_parse() ******** */

TEST(device_optix_driver, parse_windows_and_linux_versions)
{
  const std::optional<NvidiaDriverVersion> windows = nvidia_driver_version_parse("596.86");
  ASSERT_TRUE(windows.has_value());
  EXPECT_EQ(windows->major, 596);
  EXPECT_EQ(windows->minor, 86);

  const std::optional<NvidiaDriverVersion> linux_version = nvidia_driver_version_parse(
      "595.58.03");
  ASSERT_TRUE(linux_version.has_value());
  EXPECT_EQ(linux_version->major, 595);
  EXPECT_EQ(linux_version->minor, 58);

  for (const char *text : {" 621.14\n", "621.14\r\n", "\t621.14 ", "621.14"}) {
    const std::optional<NvidiaDriverVersion> padded = nvidia_driver_version_parse(text);
    ASSERT_TRUE(padded.has_value()) << "\"" << text << "\"";
    EXPECT_EQ(padded->major, 621);
    EXPECT_EQ(padded->minor, 14);
  }
}

TEST(device_optix_driver, parse_refuses_what_is_not_a_version)
{
  for (const char *text : {"",
                           " ",
                           "\n",
                           "596 .86",
                           "596. 86",
                           "596",
                           "596.",
                           ".86",
                           "596.86.",
                           "596..86",
                           "596.86-beta",
                           "v596.86",
                           "-596.86",
                           "596.-86",
                           "five.ninety",
                           "596,86",
                           "596.86 621.14",
                           "99999999999999999999.1",
                           "596.99999999999999999999"})
  {
    EXPECT_FALSE(nvidia_driver_version_parse(text).has_value()) << "\"" << text << "\"";
  }
}

/* ******** Tests for nvidia_driver_links_optix_osl() ******** */

TEST(device_optix_driver, drivers_before_and_after_the_broken_ones_link)
{
  EXPECT_TRUE(nvidia_driver_links_optix_osl(version(581, 80)));
  EXPECT_TRUE(nvidia_driver_links_optix_osl(version(582, 99)));
  EXPECT_TRUE(nvidia_driver_links_optix_osl(version(589, 99)));
  EXPECT_TRUE(nvidia_driver_links_optix_osl(version(610, 0)));
  EXPECT_TRUE(nvidia_driver_links_optix_osl(version(610, 47)));
  EXPECT_TRUE(nvidia_driver_links_optix_osl(version(621, 14)));
}

TEST(device_optix_driver, broken_drivers_do_not_link)
{
  EXPECT_FALSE(nvidia_driver_links_optix_osl(version(590, 0)));
  EXPECT_FALSE(nvidia_driver_links_optix_osl(version(591, 44)));
  EXPECT_FALSE(nvidia_driver_links_optix_osl(version(595, 58)));
  EXPECT_FALSE(nvidia_driver_links_optix_osl(version(596, 86)));
  EXPECT_FALSE(nvidia_driver_links_optix_osl(version(597, 30)));
  EXPECT_FALSE(nvidia_driver_links_optix_osl(version(609, 99)));
}

/* ******** Tests for optix_osl_driver_error() ******** */

TEST(device_optix_driver, osl_on_a_broken_driver_is_an_error_that_names_the_fix)
{
  const string error = optix_osl_driver_error(version(596, 86), KERNEL_FEATURE_OSL_SHADING);
  EXPECT_FALSE(error.empty());
  EXPECT_NE(error.find("610"), string::npos) << error;
  EXPECT_NE(error.find("580"), string::npos) << error;
  /* One constant sentence: the interface looks its translation up by this exact text. */
  EXPECT_EQ(error, optix_osl_driver_error(version(591, 44), KERNEL_FEATURE_OSL_SHADING));
  EXPECT_EQ(error.find("596"), string::npos) << error;
}

TEST(device_optix_driver, an_osl_camera_alone_on_a_broken_driver_is_the_same_error)
{
  /* Node shading with a camera script links the same pipeline and fails the same way. */
  const string shading = optix_osl_driver_error(version(596, 86), KERNEL_FEATURE_OSL_SHADING);
  EXPECT_EQ(optix_osl_driver_error(version(596, 86), KERNEL_FEATURE_OSL_CAMERA), shading);
  EXPECT_EQ(optix_osl_driver_error(version(596, 86),
                                   KERNEL_FEATURE_OSL_CAMERA | KERNEL_FEATURE_PATH_TRACING),
            shading);
  EXPECT_EQ(optix_osl_driver_error(version(596, 86),
                                   KERNEL_FEATURE_OSL_SHADING | KERNEL_FEATURE_OSL_CAMERA),
            shading);
}

TEST(device_optix_driver, osl_on_a_working_driver_is_no_error)
{
  for (const uint64_t features :
       {uint64_t(KERNEL_FEATURE_OSL_SHADING),
        uint64_t(KERNEL_FEATURE_OSL_CAMERA),
        uint64_t(KERNEL_FEATURE_OSL_SHADING | KERNEL_FEATURE_OSL_CAMERA)})
  {
    EXPECT_EQ(optix_osl_driver_error(version(582, 99), features), "");
    EXPECT_EQ(optix_osl_driver_error(version(621, 14), features), "");
  }
}

TEST(device_optix_driver, without_osl_a_broken_driver_is_no_error)
{
  EXPECT_EQ(optix_osl_driver_error(version(596, 86), 0), "");
  EXPECT_EQ(optix_osl_driver_error(version(596, 86),
                                   KERNEL_FEATURE_PATH_TRACING | KERNEL_FEATURE_NODE_RAYTRACE |
                                       KERNEL_FEATURE_MNEE | KERNEL_FEATURE_VOLUME),
            "");
}

TEST(device_optix_driver, an_unknown_driver_version_is_no_error)
{
  /* The version could not be read: render as before, never refuse on a guess. */
  EXPECT_EQ(optix_osl_driver_error(std::nullopt, KERNEL_FEATURE_OSL_SHADING), "");
  EXPECT_EQ(optix_osl_driver_error(std::nullopt, KERNEL_FEATURE_OSL_CAMERA), "");
  EXPECT_EQ(
      optix_osl_driver_error(std::nullopt, KERNEL_FEATURE_OSL_SHADING | KERNEL_FEATURE_OSL_CAMERA),
      "");
}

/* ******** Tests for nvidia_driver_version_installed() ******** */

TEST(device_optix_driver, installed_version_is_absent_or_plausible_and_stable)
{
  /* Machines without an NVIDIA driver (and every Mac) have no version; reading must not fail. */
  const std::optional<NvidiaDriverVersion> first = nvidia_driver_version_installed();
  const std::optional<NvidiaDriverVersion> second = nvidia_driver_version_installed();
  EXPECT_EQ(first.has_value(), second.has_value());
  if (first.has_value()) {
    EXPECT_GT(first->major, 0);
    EXPECT_GE(first->minor, 0);
    EXPECT_EQ(first->major, second->major);
    EXPECT_EQ(first->minor, second->minor);
  }
}

CCL_NAMESPACE_END
