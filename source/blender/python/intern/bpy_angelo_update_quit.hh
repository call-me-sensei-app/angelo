/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 * SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once
struct _object;
namespace blender {
_object *BPY_angelo_update_quit_catalog_generation(_object *, _object *);
_object *BPY_angelo_update_quit_protocol(_object *, _object *);
_object *BPY_angelo_update_quit_request(_object *, _object *);
_object *BPY_angelo_update_quit_revoke(_object *, _object *);
_object *BPY_angelo_update_quit_status(_object *, _object *);
}  // namespace blender
