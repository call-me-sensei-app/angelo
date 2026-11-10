/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

#include "BLI_function_ref.hh"

struct PyMethodDef;

namespace blender {

struct ID;
struct Main;
struct ModifierData;

/**
 * Every cache an evaluation of `bmain` may write files for, by owner: an object's modifier
 * (cloth, soft body, an emitter or hair-dynamics particle system, a dynamic-paint canvas) whose
 * point cache is on disk (Disk Cache or External) and not baked, a fluid domain without baked data
 * or in Replay mode (which bakes a frame its cache lacks), and a scene whose rigid-body world's
 * cache is External and not baked (it has no Disk Cache files of its own), reported with no
 * modifier. Reads DNA only: no `PTCacheID` is built, no file path is made, nothing is allocated.
 */
void angelo_disk_cache_owners(const Main &bmain,
                              FunctionRef<void(ID &owner, const ModifierData *modifier)> fn);

/** `_bpy._angelo_jobs.disk_cache_owners` (main thread only). */
PyMethodDef *BPY_angelo_jobs_caches_methods();

}  // namespace blender
