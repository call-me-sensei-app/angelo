/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

struct _object;

namespace blender {
/**
 * ``_angelo_stage.layer_flags(scene, view_layer_name)``: (depth, name, bits, linked, session
 * UID) of each layer collection below the master, depth first, after syncing the view layer.
 * Main thread only.
 */
_object *BPY_angelo_stage_layer_flags(_object *self, _object *args);
/**
 * ``_angelo_stage.set_layer_flags(scene, view_layer_name, bits)``: write back every layer
 * collection's exclude and previously-excluded bits, depth first, after syncing the view layer;
 * refused when the count differs. Main thread only.
 */
_object *BPY_angelo_stage_set_layer_flags(_object *self, _object *args);
}  // namespace blender
