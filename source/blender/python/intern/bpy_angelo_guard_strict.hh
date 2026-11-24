/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * "Strict script protection" (Ruling 17, latency first): the one switch every confinement layer with a measurable
 * loss against Angelo's own speed reads; OFF by default. See bpy_angelo_guard_strict.cc.
 */

#include <Python.h>

/** Whether "Strict script protection" is on: a relaxed atomic load, readable from any thread with or without the GIL
 * and from inside Python's allocator hook (no Python, no lock). */
bool angelo_strict_script_protection();

namespace blender::angelo_guard {

/** `_angelo_guard.sync_strict_script_protection()` (C reads the saved preference itself),
 * `_angelo_guard.set_strict_script_protection(on)` (off only as the sync allows it: the person on its own control,
 * a preference read they started, or a test harness) and
 * `_angelo_guard.strict_script_protection() -> bool`, added to the guard's module (bpy_angelo_guard_confine.cc); the
 * first two refused while confined: a script can't turn protection off. */
extern PyMethodDef strict_methods[];

}  // namespace blender::angelo_guard
