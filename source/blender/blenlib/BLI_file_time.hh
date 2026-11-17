/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * \ingroup bli
 *
 * A file's time as 64-bit nanoseconds since 1970, from what a file system hands over, for any
 * value it can hand over.
 *
 * 64-bit nanoseconds since 1970 reach from 1677 to 2262. A Windows file time counts 100 ns steps
 * since 1601 in 64 unsigned bits, a POSIX one counts seconds in a `time_t`: both hold times far
 * outside that span, and files do carry them (a camera with no clock, a tool that writes zero or
 * all ones, a share with a broken one). Converting by plain multiplication is undefined for those
 * and, for a Windows time multiplied by 100 before its epoch is taken off, for every file of
 * today. The functions here give the nearest representable value instead, which keeps the two
 * properties the callers need: a file's own stamp always converts to the same number, and "before
 * or after now" stays true.
 *
 * All are constant expressions, so a compiler checks them for undefined behaviour wherever they
 * are evaluated with constants (BLI_file_time_test.cc does, for the edges).
 */

#include <cstdint>
#include <limits>

namespace blender::file_time {

/** Nanoseconds in a second, and seconds from 1601-01-01 to 1970-01-01. */
constexpr int64_t nanoseconds_per_second = 1000000000;
constexpr int64_t seconds_from_1601_to_1970 = 11644473600;

/** `a + b`, or the nearest `int64_t` where the sum does not fit. */
constexpr int64_t sum_saturated(const int64_t a, const int64_t b)
{
  if (b > 0 && a > std::numeric_limits<int64_t>::max() - b) {
    return std::numeric_limits<int64_t>::max();
  }
  if (b < 0 && a < std::numeric_limits<int64_t>::min() - b) {
    return std::numeric_limits<int64_t>::min();
  }
  return a + b;
}

/** `a - b`, or the nearest `int64_t` where the difference does not fit. */
constexpr int64_t difference_saturated(const int64_t a, const int64_t b)
{
  if (b < 0 && a > std::numeric_limits<int64_t>::max() + b) {
    return std::numeric_limits<int64_t>::max();
  }
  if (b > 0 && a < std::numeric_limits<int64_t>::min() + b) {
    return std::numeric_limits<int64_t>::min();
  }
  return a - b;
}

/**
 * `seconds` and `nanoseconds` as nanoseconds, or the nearest `int64_t` where they do not fit.
 * `nanoseconds` may be any value, not only a fraction of a second.
 */
constexpr int64_t nanoseconds_saturated(const int64_t seconds, const int64_t nanoseconds)
{
  /* Whole seconds move out of the nanoseconds, which leaves 0 up to a second. */
  int64_t carried = nanoseconds / nanoseconds_per_second;
  int64_t rest = nanoseconds % nanoseconds_per_second;
  if (rest < 0) {
    rest += nanoseconds_per_second;
    carried -= 1;
  }
  const int64_t whole = sum_saturated(seconds, carried);
  /* The last whole seconds on each side whose nanoseconds may still fit:
   * `max` is 9223372036 s and 854775807 ns, `min` is -9223372037 s and 145224192 ns. */
  constexpr int64_t last = std::numeric_limits<int64_t>::max() / nanoseconds_per_second;
  constexpr int64_t last_rest = std::numeric_limits<int64_t>::max() % nanoseconds_per_second;
  constexpr int64_t first = -last - 1;
  constexpr int64_t first_rest = nanoseconds_per_second - last_rest - 1;
  if (whole > last || (whole == last && rest > last_rest)) {
    return std::numeric_limits<int64_t>::max();
  }
  if (whole < first || (whole == first && rest < first_rest)) {
    return std::numeric_limits<int64_t>::min();
  }
  if (whole == first) {
    /* `first` seconds alone do not fit: one second less of them, one second less of the rest. */
    return (whole + 1) * nanoseconds_per_second + (rest - nanoseconds_per_second);
  }
  return whole * nanoseconds_per_second + rest;
}

/**
 * A Windows file time (`FILETIME`, `LARGE_INTEGER` time fields: 100 ns steps since 1601) as
 * nanoseconds since 1970, or the nearest `int64_t` for a time before 1677 or after 2262.
 */
constexpr int64_t nanoseconds_from_filetime(const uint64_t steps)
{
  constexpr uint64_t steps_per_second = 10000000;
  return nanoseconds_saturated(int64_t(steps / steps_per_second) - seconds_from_1601_to_1970,
                               int64_t(steps % steps_per_second) * 100);
}

/**
 * Whether a file stamped `time_ns` changed less than `window_ns` before `now_ns`, or is stamped
 * later than now (a share's clock that runs ahead, a stamp past 2262). A stamp too old to
 * represent is as old as can be.
 */
constexpr bool changed_within(const int64_t time_ns, const int64_t now_ns, const int64_t window_ns)
{
  return difference_saturated(now_ns, time_ns) < window_ns;
}

}  // namespace blender::file_time
