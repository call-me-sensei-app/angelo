/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/**
 * A point cache with no owning object (a rigid body world's) on every disk-file path of
 * `pointcache.cc`: it stays in memory and never names, writes or deletes a file of its own, while
 * an object's cache still writes `<hex of the object name>_<frame>_<index>.bphys` and an external
 * cache is read by its own name. Setting `use_disk_cache` on `scene.rigidbody_world.point_cache`
 * used to crash in #BKE_object_insert_ptcache with a null object (see #PTCacheID.owner_id).
 */

#include <algorithm>
#include <filesystem>
#include <fstream>
#include <random>
#include <string>
#include <vector>

#include "testing/testing.h"

#include "BKE_global.hh"
#include "BKE_gtest_base.hh"
#include "BKE_lib_id.hh"
#include "BKE_main.hh"
#include "BKE_object.hh"
#include "BKE_pointcache.h"
#include "BKE_softbody.h"
#include "BKE_rigidbody.h"
#include "BKE_scene.hh"
#include "BKE_undo_system.hh"
#include "BLO_readfile.hh"
#include "BLO_undofile.hh"
#include "BLO_writefile.hh"
#include "DNA_scene_types.h"

#include "BLI_fileops.h"
#include "BLI_listbase.h"
#include "BLI_path_utils.hh"
#include "BLI_string.h"

#include "DNA_object_force_types.h"
#include "DNA_object_types.h"
#include "DNA_pointcache_types.h"
#include "DNA_rigidbody_types.h"

#include "MEM_guardedalloc.h"

namespace blender::bke::tests {

namespace fs = std::filesystem;

class PointCacheOwnerlessTest : public BlenderGTestBase {
 public:
  Main *bmain = nullptr;
  Main *main_before = nullptr;
  std::string folder;
  RigidBodyWorld rbw;
  RigidBodyWorld_Shared rbw_shared;

  void SetUp() override
  {
    /* A folder of its own: ctest runs each test in its own process, possibly at once. */
    const std::string test_name = testing::UnitTest::GetInstance()->current_test_info()->name();
    const std::string nonce = std::to_string(std::random_device()());
    folder = (fs::temp_directory_path() / ("blender_ptcache_" + test_name + "_" + nonce)).string() +
             SEP_STR;
    BLI_delete(folder.c_str(), true, true);
    BLI_dir_create_recursive(folder.c_str());
    bmain = BKE_main_new();
    STRNCPY(bmain->filepath, (folder + "scene.blend").c_str());
    main_before = G_MAIN;
    G_MAIN = bmain;

    rbw.shared = &rbw_shared;
    rbw.numbodies = 2; /* No objects: every point writes empty data. */
    rbw_shared.pointcache = BKE_ptcache_add(&rbw_shared.ptcaches);
  }

  void TearDown() override
  {
    BKE_ptcache_free_list(&rbw_shared.ptcaches);
    G_MAIN = main_before;
    BKE_main_free(bmain);
    BLI_delete(folder.c_str(), true, true);
  }

  std::vector<std::string> bphys_files() const
  {
    std::vector<std::string> files;
    for (const fs::directory_entry &entry : fs::recursive_directory_iterator(folder)) {
      if (entry.path().extension() == ".bphys") {
        files.push_back(fs::relative(entry.path(), folder).generic_string());
      }
    }
    std::sort(files.begin(), files.end());
    return files;
  }

  /** A file another cache owns, in the folder where this file's caches live. */
  std::string plant_sibling_file(const char *name)
  {
    const std::string dir = folder + "blendcache_scene" + SEP_STR;
    BLI_dir_create_recursive(dir.c_str());
    std::ofstream(dir + name) << "other";
    return std::string("blendcache_scene/") + name;
  }

  PTCacheID world_pid(Object *reached_through = nullptr)
  {
    PTCacheID pid;
    BKE_ptcache_id_from_rigidbody(&pid, reached_through, &rbw);
    return pid;
  }
};

TEST_F(PointCacheOwnerlessTest, disk_cache_toggle_keeps_the_world_cache_in_memory)
{
  PTCacheID pid = world_pid();
  ASSERT_EQ(pid.owner_id, nullptr);
  EXPECT_EQ(BKE_ptcache_write(&pid, 1), 1);
  ASSERT_EQ(BLI_listbase_count(&pid.cache->mem_cache), 1);

  /* What `use_disk_cache = True` does: the flag first, then the conversion. */
  pid.cache->flag |= PTCACHE_DISK_CACHE;
  BKE_ptcache_toggle_disk_cache(&pid);

  EXPECT_EQ(pid.cache->flag & PTCACHE_DISK_CACHE, 0);
  EXPECT_EQ(BLI_listbase_count(&pid.cache->mem_cache), 1);
  EXPECT_TRUE(BKE_ptcache_id_exist(&pid, 1));
  EXPECT_EQ(pid.cache->index, -1);
  EXPECT_TRUE(bphys_files().empty());
}

TEST_F(PointCacheOwnerlessTest, disk_flag_still_writes_reads_and_clears_memory)
{
  /* A disk flag the cache carries anyway (a file from elsewhere, an external cache switched off). */
  const std::string sibling = plant_sibling_file("_000001_00.bphys");
  const std::string object_file = plant_sibling_file("43756265_000001_00.bphys");
  PTCacheID pid = world_pid();
  pid.cache->flag |= PTCACHE_DISK_CACHE;

  EXPECT_EQ(BKE_ptcache_write(&pid, 1), 1);
  /* Evaluation uses the effective policy without normalizing shared configuration. */
  EXPECT_NE(pid.cache->flag & PTCACHE_DISK_CACHE, 0);
  pid.cache->flag |= PTCACHE_DISK_CACHE;
  EXPECT_EQ(BKE_ptcache_write(&pid, 2), 1);
  EXPECT_EQ(BLI_listbase_count(&pid.cache->mem_cache), 2);
  EXPECT_TRUE(BKE_ptcache_id_exist(&pid, 2));
  int startframe = 0, endframe = 0;
  pid.cache->flag |= PTCACHE_DISK_CACHE;
  BKE_ptcache_id_time(&pid, nullptr, 1.0f, &startframe, &endframe, nullptr);
  EXPECT_EQ(startframe, 1);
  EXPECT_NE(pid.cache->flag & PTCACHE_DISK_CACHE, 0);
  pid.cache->flag |= PTCACHE_DISK_CACHE;

  BKE_ptcache_id_clear(&pid, PTCACHE_CLEAR_AFTER, 1);
  EXPECT_FALSE(BKE_ptcache_id_exist(&pid, 2));
  BKE_ptcache_id_clear(&pid, PTCACHE_CLEAR_ALL, 0);
  EXPECT_FALSE(BKE_ptcache_id_exist(&pid, 1));
  EXPECT_TRUE(BLI_listbase_is_empty(&pid.cache->mem_cache));

  STRNCPY(pid.cache->name, "Rigid");
  BKE_ptcache_disk_cache_rename(&pid, "", "Rigid");
  BKE_ptcache_update_info(&pid);

  /* Not external: nothing of its own to load, and others' files are not taken for its frames. */
  pid.cache->flag &= ~PTCACHE_BAKED;
  BKE_ptcache_load_external(&pid);
  EXPECT_EQ(pid.cache->flag & PTCACHE_BAKED, 0);

  EXPECT_EQ(bphys_files(), (std::vector<std::string>{object_file, sibling}));
}

TEST_F(PointCacheOwnerlessTest, world_cache_reached_through_an_object_names_nothing_after_it)
{
  Object *cube = BKE_object_add_only_object(bmain, OB_EMPTY, "Cube");
  PTCacheID pid = world_pid(cube);
  ASSERT_EQ(pid.owner_id, &cube->id);
  pid.cache->flag |= PTCACHE_DISK_CACHE;

  EXPECT_EQ(BKE_ptcache_write(&pid, 1), 1);
  EXPECT_EQ(BLI_listbase_count(&pid.cache->mem_cache), 1);
  EXPECT_TRUE(BLI_listbase_is_empty(&cube->pc_ids));
  EXPECT_EQ(pid.cache->index, -1);
  EXPECT_TRUE(bphys_files().empty());
}

TEST_F(PointCacheOwnerlessTest, external_world_cache_is_read_by_its_own_name)
{
  const std::string dir = folder + "external" + SEP_STR;
  BLI_dir_create_recursive(dir.c_str());
  std::ofstream(dir + "Rigid_000001.bphys");
  std::ofstream(dir + "Rigid_000003.bphys");
  PTCacheID pid = world_pid();
  pid.cache->flag |= PTCACHE_EXTERNAL;
  STRNCPY(pid.cache->name, "Rigid");
  STRNCPY(pid.cache->path, dir.c_str());

  BKE_ptcache_load_external(&pid);

  EXPECT_EQ(pid.cache->startframe, 1);
  EXPECT_EQ(pid.cache->endframe, 3);
  EXPECT_NE(pid.cache->flag & PTCACHE_BAKED, 0);
  EXPECT_EQ(pid.cache->index, -1);
  EXPECT_TRUE(BKE_ptcache_id_exist(&pid, 3));
  EXPECT_FALSE(BKE_ptcache_id_exist(&pid, 2));
}

#ifdef WITH_BULLET
/* BKE_rigidbody_create_world is a null-returning stub without Bullet. The manually constructed
 * cache-policy tests above still run in that configuration. */
/* A carried disk flag must not depend on a time/write/info call before serialization.
 * Both ordinary Save and Undo's memfile writer exercise the scene-owned storage policy. */
TEST_F(PointCacheOwnerlessTest, immediate_save_and_undo_preserve_fileless_memory)
{
  Scene *scene = BKE_scene_add(bmain, "Cache persistence");
  scene->rigidbody_world = BKE_rigidbody_create_world(scene);
  scene->rigidbody_world->numbodies = 2;
  PTCacheID pid;
  BKE_ptcache_id_from_rigidbody(&pid, nullptr, scene->rigidbody_world);
  ASSERT_EQ(BKE_ptcache_write(&pid, 1), 1);
  ASSERT_EQ(BKE_ptcache_write(&pid, 2), 1);
  pid.cache->flag |= PTCACHE_BAKED | PTCACHE_DISK_CACHE;

  const auto check_loaded = [](BlendFileData *bfd) {
    ASSERT_NE(bfd, nullptr);
    const Scene *loaded_scene = static_cast<const Scene *>(bfd->main->scenes.first);
    ASSERT_NE(loaded_scene, nullptr);
    ASSERT_NE(loaded_scene->rigidbody_world, nullptr);
    const PointCache *cache = loaded_scene->rigidbody_world->shared->pointcache;
    EXPECT_EQ(cache->flag & PTCACHE_DISK_CACHE, 0);
    EXPECT_NE(cache->flag & PTCACHE_BAKED, 0);
    ASSERT_EQ(BLI_listbase_count(&cache->mem_cache), 2);
    EXPECT_EQ(static_cast<const PTCacheMem *>(cache->mem_cache.first)->frame, 1u);
    EXPECT_EQ(static_cast<const PTCacheMem *>(cache->mem_cache.last)->frame, 2u);
  };

  const std::string filepath = folder + "immediate.blend";
  BlendFileWriteParams params{};
  ASSERT_TRUE(BLO_write_file(bmain, filepath.c_str(), 0, &params, nullptr));
  EXPECT_NE(pid.cache->flag & PTCACHE_DISK_CACHE, 0); /* Save did not mutate shared state. */
  BlendFileReadReport read_reports{};
  BlendFileData *saved = BLO_read_from_file(filepath.c_str(), BLO_READ_SKIP_NONE, &read_reports);
  check_loaded(saved);
  if (saved) {
    BLO_blendfiledata_free(saved);
  }

  MemFile memfile{};
  const bool written = BLO_write_file_mem(bmain, nullptr, &memfile, 0);
  EXPECT_TRUE(written);
  EXPECT_NE(pid.cache->flag & PTCACHE_DISK_CACHE, 0);
  if (written) {
    /* Decode through Undo's reader without ID reuse hiding omitted serialized frames. */
    Main *oldmain = BKE_main_new();
    BlendFileReadParams read_params{};
    read_params.skip_flags = BLO_READ_SKIP_UNDO_OLD_MAIN;
    read_params.undo_direction = STEP_UNDO;
    BlendFileData *undo = BLO_read_from_memfile(
        oldmain, filepath.c_str(), &memfile, &read_params, nullptr);
    check_loaded(undo);
    if (undo) {
      BLO_blendfiledata_free(undo);
    }
    BKE_main_free(oldmain);
  }
  BLO_memfile_free(&memfile);
}
#endif /* WITH_BULLET */

/* Held out: an object's cache is named, written and toggled exactly as before. */
TEST_F(PointCacheOwnerlessTest, object_cache_still_writes_files_named_after_its_object)
{
  Object *sheet = BKE_object_add_only_object(bmain, OB_EMPTY, "Sheet");
  SoftBody soft;
  SoftBody_Shared soft_shared;
  soft.shared = &soft_shared;
  soft_shared.pointcache = BKE_ptcache_add(&soft_shared.ptcaches);
  soft.totpoint = 3;
  soft.bpoint = MEM_new_array_zeroed<BodyPoint>(3, __func__);
  sheet->soft = &soft;

  PTCacheID pid;
  BKE_ptcache_id_from_softbody(&pid, sheet, &soft);
  pid.cache->flag |= PTCACHE_DISK_CACHE;
  EXPECT_EQ(BKE_ptcache_write(&pid, 1), 1);

  EXPECT_EQ(bphys_files(), (std::vector<std::string>{"blendcache_scene/5368656574_000001_00.bphys"}));
  EXPECT_EQ(pid.cache->index, 0);
  EXPECT_EQ(BLI_listbase_count(&sheet->pc_ids), 1);
  EXPECT_TRUE(BKE_ptcache_id_exist(&pid, 1));

  /* Back to memory: the frame comes along and the file goes. Index 0 stays reserved, as upstream
   * keeps it (the toggle releases only a non-zero index). */
  pid.cache->flag &= ~PTCACHE_DISK_CACHE;
  BKE_ptcache_toggle_disk_cache(&pid);
  EXPECT_EQ(BLI_listbase_count(&pid.cache->mem_cache), 1);
  EXPECT_TRUE(bphys_files().empty());
  EXPECT_EQ(pid.cache->index, 0);
  EXPECT_EQ(BLI_listbase_count(&sheet->pc_ids), 1);

  sheet->soft = nullptr;
  MEM_delete(soft.bpoint);
  BKE_ptcache_free_list(&soft_shared.ptcaches);
}

}  // namespace blender::bke::tests
