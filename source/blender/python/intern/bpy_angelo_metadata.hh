/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

struct PyMethodDef;

namespace blender {
/** Descriptor-only methods for the private read module; callable from the request's worker. */
PyMethodDef *BPY_angelo_metadata_methods();
}  // namespace blender
