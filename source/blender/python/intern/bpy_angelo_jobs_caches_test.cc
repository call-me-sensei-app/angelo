/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "testing/testing.h"

#include <string>
#include <utility>
#include <vector>

#include "bpy_angelo_jobs_caches.hh"

#include "BKE_gtest_base.hh"
#include "BKE_main.hh"

#include "BLI_listbase.h"
#include "BLI_string.h"

#include "DNA_dynamicpaint_types.h"
#include "DNA_fluid_types.h"
#include "DNA_modifier_types.h"
#include "DNA_object_force_types.h"
#include "DNA_object_types.h"
#include "DNA_particle_types.h"
#include "DNA_pointcache_types.h"
#include "DNA_rigidbody_types.h"
#include "DNA_scene_types.h"

namespace blender::angelo_jobs_caches::tests {

/** The owners `angelo_disk_cache_owners` reports for a Main whose lists hold stack DNA (never freed). */
class DiskCacheOwnersTest : public bke::BlenderGTestBase {
 protected:
  Main *bmain = nullptr;

  void SetUp() override
  {
    bmain = BKE_main_new();
  }

  void TearDown() override
  {
    BLI_listbase_clear(&bmain->objects);   /* Stack DNA: nothing for the Main to free. */
    BLI_listbase_clear(&bmain->scenes);
    BKE_main_free(bmain);
  }

  std::vector<std::pair<const ID *, std::string>> owners() const
  {
    std::vector<std::pair<const ID *, std::string>> found;
    angelo_disk_cache_owners(*bmain, [&](ID &owner, const ModifierData *md) {
      found.emplace_back(&owner, md != nullptr ? md->name : "<none>");
    });
    return found;
  }

  void add(Object &ob, const char *name)
  {
    STRNCPY(ob.id.name, name);
    BLI_addtail(&bmain->objects, &ob);
  }

  static void add_modifier(Object &ob, ModifierData &md, ModifierType type, const char *name)
  {
    md.type = type;
    STRNCPY(md.name, name);
    BLI_addtail(&ob.modifiers, &md);
  }
};

TEST_F(DiskCacheOwnersTest, PointCachesOnDiskAndUnbakedOnly)
{
  Object disk{}, memory{}, baked{}, external{};
  ClothModifierData c1{}, c2{}, c3{}, c4{};
  PointCache k1{}, k2{}, k3{}, k4{};
  k1.flag = PTCACHE_DISK_CACHE;
  k2.flag = ePointCache_Flag(0);
  k3.flag = PTCACHE_DISK_CACHE | PTCACHE_BAKED;
  k4.flag = PTCACHE_EXTERNAL;
  c1.point_cache = &k1;
  c2.point_cache = &k2;
  c3.point_cache = &k3;
  c4.point_cache = &k4;
  add(disk, "OBdisk");
  add(memory, "OBmemory");
  add(baked, "OBbaked");
  add(external, "OBexternal");
  add_modifier(disk, c1.modifier, eModifierType_Cloth, "Cloth");
  add_modifier(memory, c2.modifier, eModifierType_Cloth, "Cloth");
  add_modifier(baked, c3.modifier, eModifierType_Cloth, "Cloth");
  add_modifier(external, c4.modifier, eModifierType_Cloth, "Cloth");
  const auto found = owners();
  ASSERT_EQ(found.size(), 2);
  EXPECT_EQ(found[0], std::make_pair(static_cast<const ID *>(&disk.id), std::string("Cloth")));
  EXPECT_EQ(found[1], std::make_pair(static_cast<const ID *>(&external.id), std::string("Cloth")));
}

TEST_F(DiskCacheOwnersTest, SoftBodyParticlesAndDynamicPaint)
{
  Object ob{};
  add(ob, "OBsim");
  /* Soft body: its cache lives on the object, not the modifier. */
  SoftBody sb{};
  SoftBody_Shared sb_shared{};
  PointCache sb_cache{};
  sb_cache.flag = PTCACHE_DISK_CACHE;
  sb_shared.pointcache = &sb_cache;
  sb.shared = &sb_shared;
  ob.soft = &sb;
  ModifierData softbody{};
  add_modifier(ob, softbody, eModifierType_Softbody, "Softbody");
  /* Particles: an emitter and hair dynamics step; plain hair does not. */
  ParticleSettings emitter{}, hair{};
  emitter.type = PART_EMITTER;
  hair.type = PART_HAIR;
  ParticleSystem p1{}, p2{}, p3{};
  PointCache pc1{}, pc2{}, pc3{};
  pc1.flag = pc2.flag = pc3.flag = PTCACHE_DISK_CACHE;
  p1.part = &emitter;
  p1.pointcache = &pc1;
  p2.part = &hair;
  p2.pointcache = &pc2;
  p3.part = &hair;
  p3.pointcache = &pc3;
  p3.flag |= PSYS_HAIR_DYNAMICS;
  ParticleSystemModifierData m1{}, m2{}, m3{};
  m1.psys = &p1;
  m2.psys = &p2;
  m3.psys = &p3;
  add_modifier(ob, m1.modifier, eModifierType_ParticleSystem, "Emitter");
  add_modifier(ob, m2.modifier, eModifierType_ParticleSystem, "Hair");
  add_modifier(ob, m3.modifier, eModifierType_ParticleSystem, "Dynamic hair");
  /* Dynamic paint: a canvas with one surface on disk; a brush has no cache. */
  DynamicPaintCanvasSettings canvas{};
  DynamicPaintSurface surface{}, other{};
  PointCache s1{}, s2{};
  s1.flag = ePointCache_Flag(0);
  s2.flag = PTCACHE_DISK_CACHE;
  surface.pointcache = &s1;
  other.pointcache = &s2;
  BLI_addtail(&canvas.surfaces, &surface);
  BLI_addtail(&canvas.surfaces, &other);
  DynamicPaintModifierData paint{}, brush{};
  paint.type = MOD_DYNAMICPAINT_TYPE_CANVAS;
  paint.canvas = &canvas;
  brush.type = MOD_DYNAMICPAINT_TYPE_BRUSH;
  add_modifier(ob, paint.modifier, eModifierType_DynamicPaint, "Paint");
  add_modifier(ob, brush.modifier, eModifierType_DynamicPaint, "Brush");
  std::vector<std::string> names;
  for (const auto &[owner, name] : owners()) {
    EXPECT_EQ(owner, &ob.id);
    names.push_back(name);
  }
  EXPECT_EQ(names, (std::vector<std::string>{"Softbody", "Emitter", "Dynamic hair", "Paint"}));
}

TEST_F(DiskCacheOwnersTest, FluidDomainsUnbakedOrReplay)
{
  Object unbaked{}, baked_all{}, baked_replay{}, flow{};
  add(unbaked, "OBunbaked");
  add(baked_all, "OBbaked_all");
  add(baked_replay, "OBbaked_replay");
  add(flow, "OBflow");
  FluidDomainSettings d1{}, d2{}, d3{};
  d1.cache_type = FLUID_DOMAIN_CACHE_ALL;
  d2.cache_type = FLUID_DOMAIN_CACHE_ALL;
  d2.cache_flag = FLUID_DOMAIN_BAKED_DATA;
  d3.cache_type = FLUID_DOMAIN_CACHE_REPLAY;
  d3.cache_flag = FLUID_DOMAIN_BAKED_DATA;   /* Replay still bakes a frame its cache lacks. */
  FluidModifierData f1{}, f2{}, f3{}, f4{};
  f1.type = f2.type = f3.type = MOD_FLUID_TYPE_DOMAIN;
  f1.domain = &d1;
  f2.domain = &d2;
  f3.domain = &d3;
  f4.type = MOD_FLUID_TYPE_FLOW;
  add_modifier(unbaked, f1.modifier, eModifierType_Fluid, "Fluid");
  add_modifier(baked_all, f2.modifier, eModifierType_Fluid, "Fluid");
  add_modifier(baked_replay, f3.modifier, eModifierType_Fluid, "Fluid");
  add_modifier(flow, f4.modifier, eModifierType_Fluid, "Fluid");
  std::vector<const ID *> found;
  for (const auto &[owner, name] : owners()) {
    found.push_back(owner);
  }
  EXPECT_EQ(found, (std::vector<const ID *>{&unbaked.id, &baked_replay.id}));
}

TEST_F(DiskCacheOwnersTest, RigidBodyWorldIsReportedWithItsSceneOnlyWhenExternal)
{
  /* The world's cache has no owner object and no Disk Cache files of its own: only an External one
   * counts, reported with its scene; nothing but the scene and DNA is read, never a path made. */
  Scene flagged_disk{}, in_memory{}, external{}, external_baked{}, no_world{};
  RigidBodyWorld w1{}, w2{}, w3{}, w4{};
  RigidBodyWorld_Shared s1{}, s2{}, s3{}, s4{};
  PointCache k1{}, k2{}, k3{}, k4{};
  k1.flag = PTCACHE_DISK_CACHE;   /* Stays in memory (`ptcache_uses_disk`). */
  k2.flag = ePointCache_Flag(0);
  k3.flag = PTCACHE_EXTERNAL;
  k4.flag = PTCACHE_EXTERNAL | PTCACHE_BAKED;
  s1.pointcache = &k1;
  s2.pointcache = &k2;
  s3.pointcache = &k3;
  s4.pointcache = &k4;
  w1.shared = &s1;
  w2.shared = &s2;
  w3.shared = &s3;
  w4.shared = &s4;
  flagged_disk.rigidbody_world = &w1;
  in_memory.rigidbody_world = &w2;
  external.rigidbody_world = &w3;
  external_baked.rigidbody_world = &w4;
  for (Scene *scene : {&flagged_disk, &in_memory, &external, &external_baked, &no_world}) {
    BLI_addtail(&bmain->scenes, scene);
  }
  const auto found = owners();
  ASSERT_EQ(found.size(), 1);
  EXPECT_EQ(found[0], std::make_pair(static_cast<const ID *>(&external.id), std::string("<none>")));
}

TEST_F(DiskCacheOwnersTest, LinkedObjectsCountAndNothingElseDoes)
{
  Library library{};
  Object linked{}, plain{};
  linked.id.lib = &library;
  add(linked, "OBlinked");
  add(plain, "OBplain");
  ClothModifierData cloth{};
  PointCache cache{};
  cache.flag = PTCACHE_DISK_CACHE;
  cloth.point_cache = &cache;
  add_modifier(linked, cloth.modifier, eModifierType_Cloth, "Cloth");
  ModifierData bevel{};
  add_modifier(plain, bevel, eModifierType_Bevel, "Bevel");
  const auto found = owners();
  ASSERT_EQ(found.size(), 1);
  EXPECT_EQ(found[0].first, &linked.id);
}

}  // namespace blender::angelo_jobs_caches::tests
