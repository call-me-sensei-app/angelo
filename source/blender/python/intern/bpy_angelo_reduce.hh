/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 */

#pragma once

struct _object;

namespace blender {

/** `_bpy._angelo_reduce`: mesh reduction and its measurement for the game-ready check. */
_object *BPY_angelo_reduce_module();

}  // namespace blender
