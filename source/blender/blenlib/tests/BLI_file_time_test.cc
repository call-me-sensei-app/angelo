/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * A file's time as 64-bit nanoseconds, for any time a file system can hand over.
 *
 * Windows gives a file time in 100 ns steps since 1601. Multiplying today's value by 100 does not
 * fit 64 bits (about 1.34e19 against 9.22e18): signed overflow, which is undefined, in the two
 * places that did it for every file (found by an external review with UBSan, 2026-11-15). Seconds
 * times 1e9 has the same fault for a file stamped before 1677 or after 2262, which broken clocks
 * and careless tools do write.
 *
 * Every case is asked twice: as a constant expression, where a compiler must refuse undefined
 * behaviour rather than pick a result, and at run time on values the optimizer cannot see, for a
 * build with `-fsanitize=undefined`.
 */

#include "testing/testing.h"

#include <cstdint>
#include <limits>

#include "BLI_file_time.hh"

namespace blender::tests {

using namespace blender::file_time;

constexpr int64_t int64_max = std::numeric_limits<int64_t>::max();
constexpr int64_t int64_min = std::numeric_limits<int64_t>::min();
constexpr int64_t second_ns = 1000000000;
/* 2026-11-15 00:00:00 UTC: the value the review put through the old expression. */
constexpr uint64_t filetime_2026 = 134391744000000000ull;
constexpr int64_t seconds_2026 = 1794700800;
/* 1970-01-01 in Windows steps. */
constexpr uint64_t filetime_1970 = 116444736000000000ull;

/* -------------------------------------------------------------------- */
/** \name As constant expressions
 * \{ */

static_assert(nanoseconds_from_filetime(filetime_2026) == seconds_2026 * second_ns);
static_assert(nanoseconds_from_filetime(filetime_2026 + 1234567) ==
              seconds_2026 * second_ns + 123456700);
static_assert(nanoseconds_from_filetime(filetime_1970) == 0);
static_assert(nanoseconds_from_filetime(filetime_1970 + 1) == 100);
static_assert(nanoseconds_from_filetime(filetime_1970 - 1) == -100);
/* 1601 lies before what 64-bit nanoseconds since 1970 hold (1677): the nearest value. */
static_assert(nanoseconds_from_filetime(0) == int64_min);
/* The last step 64-bit nanoseconds hold, in 2262, and everything after it. */
static_assert(nanoseconds_from_filetime(filetime_1970 + uint64_t(int64_max / 100)) ==
              int64_max / 100 * 100);
static_assert(nanoseconds_from_filetime(filetime_1970 + uint64_t(int64_max / 100) + 1) ==
              int64_max);
static_assert(nanoseconds_from_filetime(uint64_t(int64_max)) == int64_max);
static_assert(nanoseconds_from_filetime(std::numeric_limits<uint64_t>::max()) == int64_max);

static_assert(nanoseconds_saturated(seconds_2026, 999999999) ==
              seconds_2026 * second_ns + 999999999);
static_assert(nanoseconds_saturated(0, 0) == 0);
static_assert(nanoseconds_saturated(-1, 999999999) == -1);
static_assert(nanoseconds_saturated(-1, 0) == -second_ns);
/* The edges exactly, and one nanosecond past each. */
static_assert(nanoseconds_saturated(9223372036, 854775807) == int64_max);
static_assert(nanoseconds_saturated(9223372036, 854775808) == int64_max);
static_assert(nanoseconds_saturated(9223372037, 0) == int64_max);
static_assert(nanoseconds_saturated(-9223372037, 145224192) == int64_min);
static_assert(nanoseconds_saturated(-9223372037, 145224193) == int64_min + 1);
static_assert(nanoseconds_saturated(-9223372037, 145224191) == int64_min);
static_assert(nanoseconds_saturated(-9223372038, 999999999) == int64_min);
static_assert(nanoseconds_saturated(int64_max, 999999999) == int64_max);
static_assert(nanoseconds_saturated(int64_min, 0) == int64_min);
/* A nanosecond field that is not one (a file system's or a driver's mistake) is still defined. */
static_assert(nanoseconds_saturated(1, 2 * second_ns) == 3 * second_ns);
static_assert(nanoseconds_saturated(1, -2 * second_ns) == -second_ns);
static_assert(nanoseconds_saturated(9223372036, int64_max) == int64_max);
static_assert(nanoseconds_saturated(-9223372037, int64_min) == int64_min);

static_assert(difference_saturated(5, 3) == 2);
static_assert(difference_saturated(3, 5) == -2);
static_assert(difference_saturated(int64_max, int64_min) == int64_max);
static_assert(difference_saturated(int64_min, int64_max) == int64_min);
static_assert(difference_saturated(0, int64_min) == int64_max);
static_assert(difference_saturated(-1, int64_min) == int64_max);
static_assert(difference_saturated(int64_min, 1) == int64_min);
static_assert(difference_saturated(int64_max, -1) == int64_max);
static_assert(difference_saturated(int64_min, int64_min) == 0);

/* "Changed within the last three seconds, or stamped later than now". */
constexpr int64_t now_2026 = seconds_2026 * second_ns;
constexpr int64_t window = 3 * second_ns;
static_assert(changed_within(now_2026, now_2026, window));
static_assert(changed_within(now_2026 - window + 1, now_2026, window));
static_assert(!changed_within(now_2026 - window, now_2026, window));
static_assert(!changed_within(now_2026 - 3600 * second_ns, now_2026, window));
static_assert(changed_within(now_2026 + 1, now_2026, window)); /* A share's clock runs ahead. */
static_assert(changed_within(int64_max, now_2026, window));    /* Stamped past 2262. */
static_assert(!changed_within(int64_min, now_2026, window));   /* Stamped before 1677. */
static_assert(!changed_within(int64_min, int64_max, window));
static_assert(changed_within(int64_max, int64_min, window));
static_assert(!changed_within(now_2026, now_2026, 0));
static_assert(changed_within(now_2026 + 1, now_2026, 0));
static_assert(changed_within(int64_min, now_2026, int64_max) == false);
static_assert(changed_within(now_2026 - 3600 * second_ns, now_2026, int64_max));

/** \} */

/* -------------------------------------------------------------------- */
/** \name At run time
 * \{ */

/* A value the optimizer has to read, so the arithmetic is done by the built code. */
template<typename T> static T seen_at_run_time(const T value)
{
  volatile T stored = value;
  return stored;
}

TEST(file_time, windows_times_of_today_and_of_every_other_day)
{
  const struct {
    uint64_t steps;
    int64_t nanoseconds;
  } cases[] = {
      {filetime_2026, seconds_2026 * second_ns},
      {filetime_2026 + 1234567, seconds_2026 * second_ns + 123456700},
      {filetime_1970, 0},
      {filetime_1970 + 1, 100},
      {filetime_1970 - 1, -100},
      {0, int64_min},
      {1, int64_min},
      {filetime_1970 + uint64_t(int64_max / 100), int64_max / 100 * 100},
      {filetime_1970 + uint64_t(int64_max / 100) + 1, int64_max},
      {uint64_t(int64_max), int64_max},
      {uint64_t(int64_max) + 1, int64_max},
      {std::numeric_limits<uint64_t>::max(), int64_max},
  };
  for (const auto &item : cases) {
    EXPECT_EQ(nanoseconds_from_filetime(seen_at_run_time(item.steps)), item.nanoseconds)
        << item.steps;
  }
}

TEST(file_time, seconds_and_nanoseconds_of_every_size)
{
  const struct {
    int64_t seconds, nanoseconds, result;
  } cases[] = {
      {seconds_2026, 999999999, seconds_2026 * second_ns + 999999999},
      {0, 0, 0},
      {-1, 999999999, -1},
      {-1, 0, -second_ns},
      {9223372036, 854775807, int64_max},
      {9223372036, 854775808, int64_max},
      {9223372037, 0, int64_max},
      {-9223372037, 145224192, int64_min},
      {-9223372037, 145224193, int64_min + 1},
      {-9223372037, 145224191, int64_min},
      {-9223372038, 999999999, int64_min},
      {int64_max, 999999999, int64_max},
      {int64_max, int64_max, int64_max},
      {int64_min, 0, int64_min},
      {int64_min, int64_min, int64_min},
      {1, 2 * second_ns, 3 * second_ns},
      {1, -2 * second_ns, -second_ns},
      {9223372036, int64_max, int64_max},
      {-9223372037, int64_min, int64_min},
      {0, int64_max, int64_max},
      {0, int64_min, int64_min},
  };
  for (const auto &item : cases) {
    EXPECT_EQ(
        nanoseconds_saturated(seen_at_run_time(item.seconds), seen_at_run_time(item.nanoseconds)),
        item.result)
        << item.seconds << " s, " << item.nanoseconds << " ns";
  }
}

TEST(file_time, differences_and_the_window_before_now)
{
  const int64_t values[] = {int64_min,
                            int64_min + 1,
                            -second_ns,
                            -1,
                            0,
                            1,
                            now_2026 - window,
                            now_2026 - window + 1,
                            now_2026,
                            now_2026 + 1,
                            int64_max - 1,
                            int64_max};
  for (const int64_t a : values) {
    for (const int64_t b : values) {
      const int64_t difference = difference_saturated(seen_at_run_time(a), seen_at_run_time(b));
      /* The same sign as the true difference, and the true difference whenever it fits. */
      EXPECT_EQ(difference > 0, a > b) << a << " - " << b;
      EXPECT_EQ(difference == 0, a == b) << a << " - " << b;
      if ((a >= 0) == (b >= 0)) {
        EXPECT_EQ(difference, a - b) << a << " - " << b;
      }
      for (const int64_t width : {int64_t(0), int64_t(1), window, int64_max}) {
        /* Later than now always counts; otherwise the age decides. */
        const bool within = changed_within(seen_at_run_time(a), seen_at_run_time(b), width);
        if (a > b) {
          EXPECT_TRUE(within) << a << " after " << b;
        }
        else {
          EXPECT_EQ(within, difference_saturated(b, a) < width) << a << " before " << b;
        }
      }
    }
  }
  EXPECT_TRUE(changed_within(now_2026 - window + 1, now_2026, window));
  EXPECT_FALSE(changed_within(now_2026 - window, now_2026, window));
  EXPECT_FALSE(changed_within(seen_at_run_time(int64_min), now_2026, window));
  EXPECT_TRUE(changed_within(seen_at_run_time(int64_max), now_2026, window));
}

/** \} */

}  // namespace blender::tests
