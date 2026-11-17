/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * `_bpy._angelo_guard`: the state Angelo's guards depend on, kept in C where no Python can rebind it
 * (doc/angelo/script_confinement.rst, doc/angelo/grant_operators.rst).
 *
 * - The confinement of agent scripts (`bpy_angelo_guard_confine.hh`): whether this thread runs agent code, and the
 *   audit hook that refuses what a confined thread may not do.
 * - The authority behind grants (`bpy_angelo_guard_authority.hh`): approval modes, trust, consent, click tickets.
 */

struct _object;

namespace blender {
/** The `_bpy._angelo_guard` module: the confinement's and the authority's methods. */
_object *BPY_angelo_guard_module();
}  // namespace blender
