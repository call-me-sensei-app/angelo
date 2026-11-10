/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

namespace blender {

/** Keep a numeric array's current storage writable until its matching release. A resize
 * detaches all exports of the old storage; releasing does not access the originating property. */
void IDP_ArrayBuffer_acquire(void *data);
void IDP_ArrayBuffer_release(void *data);

}  // namespace blender
