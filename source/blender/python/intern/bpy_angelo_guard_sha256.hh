/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * SHA-256 (FIPS 180-4) in C, for `_bpy._angelo_guard`: the digest of a script's text is computed where no Python
 * can replace the function that computes it.
 */

#include <cstddef>
#include <cstdint>

namespace blender::angelo_guard {

void sha256(const void *data, size_t size, uint8_t r_digest[32]);

}  // namespace blender::angelo_guard
