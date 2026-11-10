/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * \ingroup bke
 *
 * Angelo: an exact digest of an F-Curve's keys and samples, for the agent's object revisions
 * and program fingerprints (`scripts/addons_core/angelo_agent/action_key_digest.py`,
 * doc/angelo/native_coverage.rst "Animation work bounds"). Python's `foreach_get` reads `co`
 * and both handles through a custom RNA getter per key; this walks the arrays once.
 */

#include <optional>
#include <string>

namespace blender {

struct FCurve;

/**
 * 32 lowercase hexadecimal digits: XXH3-128, seeded once per process, of a record built field
 * by field (never raw struct bytes): a version tag, whether selection is included, the key and
 * sample counts (`bezt ? totvert : 0`, `fpt ? totvert : 0`, as RNA counts them), then per key
 * `co`, left and right handle (`vec[1]`, `vec[0]`, `vec[2]`, x and y), `back`, `amplitude`,
 * `period`, `ipo`, `easing`, key type (`hide`), `h1`, `h2` and, with `selection`, the `f2`,
 * `f1` and `f3` select bits; then per sample `vec` and, with `selection`, its select bit.
 *
 * Returns nothing when any of those floats is not finite (the caller refuses the observation).
 * Reads only: no update, no tag.
 */
std::optional<std::string> BKE_angelo_fcurve_keys_digest(const FCurve &fcu, bool selection);

}  // namespace blender
