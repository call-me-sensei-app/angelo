/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup bke
 */

#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <random>

#define XXH_STATIC_LINKING_ONLY /* XXH3_state_t's definition, for a state on the stack. */
#include <xxhash.h>

#include "DNA_anim_enums.h" /* SELECT */
#include "DNA_anim_types.h"
#include "DNA_curve_enums.h"
#include "DNA_curve_types.h"

#include "BKE_angelo_fcurve_digest.hh"

namespace blender {

/* A per-process seed: digests live only within one launch (the agent's revisions embed the
 * document epoch), and a seed known to no file removes XXH3's known-key collisions. */
static uint64_t process_seed()
{
  static const uint64_t seed = [] {
    /* The seed's only job is to be unknown to any file. std::random_device may throw where no
     * entropy source is available; never let that cross the RNA call. */
    try {
      std::random_device device;
      return (uint64_t(device()) << 32) ^ uint64_t(device());
    }
    catch (...) {
      const uint64_t clock = uint64_t(
          std::chrono::steady_clock::now().time_since_epoch().count());
      return clock ^ (uint64_t(uintptr_t(&clock)) << 17);
    }
  }();
  return seed;
}

namespace {

/* Field-by-field records, buffered so the hash sees large updates. Every platform Blender
 * supports is little-endian; the values are copied as stored. */
class RecordWriter {
 public:
  explicit RecordWriter(XXH3_state_t *state) : state_(state) {}

  ~RecordWriter()
  {
    this->flush();
  }

  void add_float(const float value)
  {
    this->add_bytes(&value, sizeof(value));
  }

  void add_int(const int32_t value)
  {
    this->add_bytes(&value, sizeof(value));
  }

  void add_bytes(const void *data, const size_t size)
  {
    if (used_ + size > sizeof(buffer_)) {
      this->flush();
    }
    memcpy(buffer_ + used_, data, size);
    used_ += size;
  }

  void flush()
  {
    if (used_) {
      XXH3_128bits_update(state_, buffer_, used_);
      used_ = 0;
    }
  }

 private:
  XXH3_state_t *state_;
  unsigned char buffer_[16384];
  size_t used_ = 0;
};

}  // namespace

std::optional<std::string> BKE_angelo_fcurve_keys_digest(const FCurve &fcu, const bool selection)
{
  const int64_t keys = fcu.bezt ? fcu.totvert : 0;
  const int64_t samples = fcu.fpt ? fcu.totvert : 0;

  /* On the stack: no allocation per curve, and nothing to fail. */
  XXH3_state_t state_storage;
  XXH3_state_t *state = &state_storage;
  XXH3_INITSTATE(state);
  XXH3_128bits_reset_withSeed(state, process_seed());
  bool finite = true;
  {
    RecordWriter record(state);
    static const char tag[] = "angelo-keys-v1";
    record.add_bytes(tag, sizeof(tag) - 1);
    record.add_int(selection ? 1 : 0);
    record.add_bytes(&keys, sizeof(keys));
    record.add_bytes(&samples, sizeof(samples));
    for (int64_t i = 0; i < keys && finite; i++) {
      const BezTriple &bezt = fcu.bezt[i];
      const float floats[9] = {bezt.vec[1][0],
                               bezt.vec[1][1],
                               bezt.vec[0][0],
                               bezt.vec[0][1],
                               bezt.vec[2][0],
                               bezt.vec[2][1],
                               bezt.back,
                               bezt.amplitude,
                               bezt.period};
      for (const float value : floats) {
        finite = finite && std::isfinite(value);
        record.add_float(value);
      }
      record.add_int(int32_t(bezt.ipo));
      record.add_int(int32_t(bezt.easing));
      record.add_int(int32_t(bezt.hide)); /* The key type (#BEZKEYTYPE). */
      record.add_int(int32_t(bezt.h1));
      record.add_int(int32_t(bezt.h2));
      if (selection) {
        const unsigned char flags[3] = {(bezt.f2 & SELECT) != 0,
                                        (bezt.f1 & SELECT) != 0,
                                        (bezt.f3 & SELECT) != 0};
        record.add_bytes(flags, sizeof(flags));
      }
    }
    for (int64_t i = 0; i < samples && finite; i++) {
      const FPoint &point = fcu.fpt[i];
      finite = finite && std::isfinite(point.vec[0]) && std::isfinite(point.vec[1]);
      record.add_float(point.vec[0]);
      record.add_float(point.vec[1]);
      if (selection) {
        const unsigned char flag = (point.flag & SELECT) != 0;
        record.add_bytes(&flag, 1);
      }
    }
  }
  const XXH128_hash_t hash = XXH3_128bits_digest(state);
  if (!finite) {
    return std::nullopt;
  }
  char hex[33];
  snprintf(hex,
           sizeof(hex),
           "%016llx%016llx",
           (unsigned long long)hash.high64,
           (unsigned long long)hash.low64);
  return std::string(hex);
}

}  // namespace blender
