/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * `_bpy._angelo_jobs.disk_cache_owners()`: the caches a render or export may write files for while it
 * evaluates frames, outside the job's own output (a render's and the USD/Alembic exporters'
 * evaluation steps unbaked simulations, and Blender writes, resets and deletes their cache files:
 * `pointcache.cc`, `fluid.cc`). The Auto approval's step facts list them (`step_facts.py`,
 * doc/angelo/long_jobs.rst "What a job runs and simulates"); Python reaches the same set only by
 * walking every object's modifiers through RNA, about 5 instructions in every 10 a render job's
 * precondition spends on a 2,000-object scene, so this C walk replaces it where this build has it.
 *
 * The rule is the add-on's (`motion_guards.simulated` with the disk test of `step_facts._writers`);
 * a Blender suite compares the two owner sets on real data (`test_blender_auto_new_outputs`). DNA is
 * read directly: no `PTCacheID` is built, so the owner-less rigid-body world cache never reaches the
 * object-only path functions, and no file name is made. A rigid-body world's cache is reported with
 * its scene and no modifier, only when External (it has no Disk Cache files of its own).
 */

#include <Python.h>

#include "BLI_listbase.h"
#include "BLI_threads.h"

#include "BKE_global.hh"
#include "BKE_main.hh"

#include "DNA_dynamicpaint_types.h"
#include "DNA_fluid_types.h"
#include "DNA_modifier_types.h"
#include "DNA_object_force_types.h"
#include "DNA_object_types.h"
#include "DNA_particle_types.h"
#include "DNA_pointcache_types.h"
#include "DNA_rigidbody_types.h"
#include "DNA_scene_types.h"

#include "bpy_angelo_jobs_caches.hh"
#include "bpy_rna.hh"

namespace blender {

/** A point cache whose files Blender reads and writes while it evaluates: on disk, not baked. */
static bool writes_files(const PointCache *cache)
{
  return cache != nullptr && (cache->flag & (PTCACHE_DISK_CACHE | PTCACHE_EXTERNAL)) != 0 &&
         (cache->flag & PTCACHE_BAKED) == 0;
}

static bool modifier_writes_files(const Object &ob, const ModifierData &md)
{
  switch (ModifierType(md.type)) {
    case eModifierType_Cloth:
      return writes_files(reinterpret_cast<const ClothModifierData &>(md).point_cache);
    case eModifierType_Softbody:
      return ob.soft != nullptr && ob.soft->shared != nullptr &&
             writes_files(ob.soft->shared->pointcache);
    case eModifierType_ParticleSystem: {
      const ParticleSystem *psys = reinterpret_cast<const ParticleSystemModifierData &>(md).psys;
      if (psys == nullptr || psys->part == nullptr) {
        return false;
      }
      const bool steps = psys->part->type == PART_EMITTER || (psys->flag & PSYS_HAIR_DYNAMICS);
      return steps && writes_files(psys->pointcache);
    }
    case eModifierType_DynamicPaint: {
      const auto &pmd = reinterpret_cast<const DynamicPaintModifierData &>(md);
      if (pmd.type != MOD_DYNAMICPAINT_TYPE_CANVAS || pmd.canvas == nullptr) {
        return false;
      }
      for (const DynamicPaintSurface &surface : pmd.canvas->surfaces) {
        if (writes_files(surface.pointcache)) {
          return true;
        }
      }
      return false;
    }
    case eModifierType_Fluid: {
      const auto &fmd = reinterpret_cast<const FluidModifierData &>(md);
      if (fmd.type != MOD_FLUID_TYPE_DOMAIN || fmd.domain == nullptr) {
        return false;
      }
      return (fmd.domain->cache_flag & FLUID_DOMAIN_BAKED_DATA) == 0 ||
             fmd.domain->cache_type == FLUID_DOMAIN_CACHE_REPLAY;
    }
    default:
      return false;
  }
}

void angelo_disk_cache_owners(const Main &bmain,
                              FunctionRef<void(ID &owner, const ModifierData *modifier)> fn)
{
  for (Object &ob : bmain.objects) {
    for (const ModifierData &md : ob.modifiers) {
      if (modifier_writes_files(ob, md)) {
        fn(ob.id, &md);
      }
    }
  }
  /* A rigid-body world's cache has no files of its own: a Disk Cache flag on it stays in memory
   * (`ptcache_uses_disk`), and only an External cache is read from a folder. Its owner is the scene;
   * no `PTCacheID` is built for it, so its owner-less id never reaches a path function. */
  for (Scene &scene : bmain.scenes) {
    const RigidBodyWorld *rbw = scene.rigidbody_world;
    const PointCache *cache = rbw != nullptr && rbw->shared != nullptr ? rbw->shared->pointcache :
                                                                         nullptr;
    if (cache != nullptr && (cache->flag & PTCACHE_EXTERNAL) != 0 && (cache->flag & PTCACHE_BAKED) == 0)
    {
      fn(scene.id, nullptr);
    }
  }
}

static PyObject *caches_disk_cache_owners(PyObject * /*self*/, PyObject * /*args*/)
{
  if (!BLI_thread_is_main()) {
    PyErr_SetString(PyExc_RuntimeError, "disk_cache_owners requires Blender's main thread");
    return nullptr;
  }
  PyObject *found = PyList_New(0);
  if (found == nullptr) {
    return nullptr;
  }
  bool failed = false;
  angelo_disk_cache_owners(*G_MAIN, [&](ID &owner, const ModifierData *modifier) {
    if (failed) {
      return;
    }
    PyObject *item = Py_BuildValue(
        "(Ns)", pyrna_id_CreatePyObject(&owner), modifier != nullptr ? modifier->name : nullptr);
    if (item == nullptr || PyList_Append(found, item) < 0) {
      failed = true;
    }
    Py_XDECREF(item);
  });
  if (failed) {
    Py_DECREF(found);
    return nullptr;
  }
  return found;
}

static PyMethodDef methods[] = {
    {"disk_cache_owners",
     caches_disk_cache_owners,
     METH_NOARGS,
     "disk_cache_owners(): [(owner, modifier name or None)] for every cache an evaluation may write "
     "files for: an object's cloth, soft-body, emitter or hair-dynamics particle or dynamic-paint "
     "cache on disk (Disk Cache or External) and not baked, a fluid domain without baked data or in "
     "Replay mode, and a scene (no modifier) whose rigid-body world's cache is External and not baked. "
     "Main thread only."},
    {nullptr, nullptr, 0, nullptr},
};

PyMethodDef *BPY_angelo_jobs_caches_methods()
{
  return methods;
}

}  // namespace blender
