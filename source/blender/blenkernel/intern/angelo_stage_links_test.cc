/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "testing/testing.h"

#include <tuple>

#include "DNA_material_types.h"
#include "DNA_mesh_types.h"
#include "DNA_collection_types.h"
#include "DNA_object_types.h"
#include "DNA_scene_types.h"

#include "BKE_angelo_stage_links.hh"
#include "BKE_global.hh"
#include "BKE_gtest_base.hh"
#include "BKE_idprop.hh"
#include "BKE_lib_query.hh"
#include "BKE_lib_id.hh"
#include "BKE_main.hh"
#include "BKE_material.hh"
#include "BKE_mesh.h"
#include "BKE_scene.hh"

namespace blender::bke::tests {

class AngeloStageLinksTest : public BlenderGTestBase {
 protected:
  Main *bmain = nullptr;

  void SetUp() override
  {
    bmain = BKE_main_new();
    G.main = bmain;
  }

  void TearDown() override
  {
    BKE_main_free(bmain);
    G.main = nullptr;
  }

  Mesh *mesh(const char *name)
  {
    return BKE_mesh_add(bmain, name);
  }

  static void hold(ID &holder, const char *key, ID *value)
  {
    IDP_AddToGroup(IDP_EnsureProperties(&holder), idprop::create(key, value).release());
  }

  static ID *held(ID &holder, const char *key)
  {
    const IDProperty *property = IDP_GetPropertyFromGroup(holder.properties, key);
    return property ? IDP_ID_get(property) : nullptr;
  }
};

static Vector<AngeloStageRelinkSlot> last_journal;

static bool relink(Main &bmain,
                   Span<ID *> ids,
                   Span<std::pair<ID *, ID *>> pairs,
                   Vector<ID *> &outside,
                   std::string &error)
{
  return BKE_angelo_stage_relink(bmain, ids, pairs, outside, last_journal, error);
}

/* A non-user pointer (a texture space mesh) and a user pointer (an ID property) both move, with
 * exact user counts, and only in the IDs asked for. */
TEST_F(AngeloStageLinksTest, every_pointer_kind_moves_only_in_the_given_ids)
{
  Mesh *staged = mesh("staged");
  Mesh *person = mesh("person");
  Mesh *live = mesh("live");
  Mesh *copy = mesh("copy");
  staged->texcomesh = live;
  person->texcomesh = live;
  hold(staged->id, "held", &live->id);
  hold(person->id, "held", &live->id);
  const int live_users = live->id.us;
  const int copy_users = copy->id.us;

  Vector<ID *> outside;
  std::string error;
  const std::pair<ID *, ID *> pair = {&live->id, &copy->id};
  ASSERT_TRUE(relink(*bmain, {&staged->id}, {pair}, outside, error)) << error;

  EXPECT_EQ(staged->texcomesh, copy);
  EXPECT_EQ(held(staged->id, "held"), &copy->id);
  /* The person's mesh was not asked for: untouched. */
  EXPECT_EQ(person->texcomesh, live);
  EXPECT_EQ(held(person->id, "held"), &live->id);
  /* The ID property is a user; the texture space mesh is not. */
  EXPECT_EQ(live->id.us, live_users - 1);
  EXPECT_EQ(copy->id.us, copy_users + 1);
  EXPECT_EQ(outside.size(), 1);
  EXPECT_EQ(outside[0], &copy->id);
}

/* What stays outside is reported once each; links between the given IDs are not outside. */
TEST_F(AngeloStageLinksTest, outside_lists_each_external_id_once)
{
  Mesh *a = mesh("a");
  Mesh *b = mesh("b");
  Mesh *external = mesh("external");
  Material *material = BKE_material_add(bmain, "material");
  a->texcomesh = b;
  b->texcomesh = external;
  hold(a->id, "one", &external->id);
  hold(a->id, "two", &material->id);
  hold(b->id, "three", &material->id);

  Vector<ID *> outside;
  std::string error;
  ASSERT_TRUE(relink(*bmain, {&a->id, &b->id, &a->id}, {}, outside, error)) << error;
  EXPECT_EQ(outside.size(), 2);
  EXPECT_TRUE(outside.contains(&external->id));
  EXPECT_TRUE(outside.contains(&material->id));
  EXPECT_FALSE(outside.contains(&b->id));
}

/* A material's embedded node tree is the material's own: never outside, never a holder. */
TEST_F(AngeloStageLinksTest, embedded_data_is_part_of_its_owner)
{
  Material *material = BKE_material_add(bmain, "material");
  ID *tree = reinterpret_cast<ID *>(material->nodetree);
  ASSERT_NE(tree, nullptr);
  Vector<ID *> outside;
  std::string error;
  ASSERT_TRUE(relink(*bmain, {&material->id}, {}, outside, error)) << error;
  EXPECT_FALSE(outside.contains(tree));
  EXPECT_FALSE(outside.contains(&material->id));
  /* Never a holder of its own, and never relinked as a pair. */
  EXPECT_FALSE(relink(*bmain, {tree}, {}, outside, error));
  EXPECT_FALSE(error.empty());
  Material *other = BKE_material_add(bmain, "other");
  const std::pair<ID *, ID *> embedded = {tree, reinterpret_cast<ID *>(other->nodetree)};
  EXPECT_FALSE(relink(*bmain, {&material->id}, {embedded}, outside, error));
  EXPECT_EQ(reinterpret_cast<ID *>(material->nodetree), tree);
}

/* An invalid request writes nothing. */
TEST_F(AngeloStageLinksTest, invalid_requests_write_nothing)
{
  Mesh *staged = mesh("staged");
  Mesh *live = mesh("live");
  Mesh *copy = mesh("copy");
  Material *material = BKE_material_add(bmain, "material");
  staged->texcomesh = live;
  Vector<ID *> outside;
  std::string error;

  const std::pair<ID *, ID *> good = {&live->id, &copy->id};
  const std::pair<ID *, ID *> mixed = {&live->id, &material->id};
  EXPECT_FALSE(relink(*bmain, {&staged->id}, {good, mixed}, outside, error));
  EXPECT_EQ(staged->texcomesh, live);

  const std::pair<ID *, ID *> same = {&live->id, &live->id};
  EXPECT_FALSE(relink(*bmain, {&staged->id}, {same}, outside, error));
  const std::pair<ID *, ID *> twice = {&live->id, &staged->id};
  EXPECT_FALSE(relink(*bmain, {&staged->id}, {good, twice}, outside, error));
  EXPECT_EQ(staged->texcomesh, live);
  EXPECT_FALSE(relink(*bmain, {nullptr}, {good}, outside, error));
  EXPECT_EQ(staged->texcomesh, live);
}

/* A stand-in is a new, empty local ID of the same type with no users; other types have none. */
TEST_F(AngeloStageLinksTest, stand_ins_are_empty_ids_of_the_same_type)
{
  Collection *rocks = BKE_id_new<Collection>(bmain, "Rocks");
  ID *stand_in = BKE_angelo_stage_stand_in(*bmain, rocks->id);
  ASSERT_NE(stand_in, nullptr);
  EXPECT_NE(stand_in, &rocks->id);
  EXPECT_EQ(GS(stand_in->name), ID_GR);
  EXPECT_EQ(stand_in->us, 0);

  /* A holder's link moves to the stand-in and back, and the live collection is never written. */
  Mesh *holder = mesh("holder");
  hold(holder->id, "rocks", &rocks->id);
  Vector<ID *> outside;
  std::string error;
  const std::pair<ID *, ID *> away = {&rocks->id, stand_in};
  ASSERT_TRUE(relink(*bmain, {&holder->id}, {away}, outside, error)) << error;
  EXPECT_EQ(held(holder->id, "rocks"), stand_in);
  const std::pair<ID *, ID *> back = {stand_in, &rocks->id};
  ASSERT_TRUE(relink(*bmain, {&holder->id}, {back}, outside, error)) << error;
  EXPECT_EQ(held(holder->id, "rocks"), &rocks->id);

  Scene *scene = BKE_scene_add(bmain, "Scene");
  EXPECT_EQ(BKE_angelo_stage_stand_in(*bmain, scene->id), nullptr);
  EXPECT_NE(BKE_angelo_stage_stand_in(*bmain, holder->id), nullptr);
  Material *material = BKE_material_add(bmain, "material");
  const ID &tree = *reinterpret_cast<ID *>(material->nodetree);
  EXPECT_EQ(BKE_angelo_stage_stand_in(*bmain, tree), nullptr);
}

/* links() is exactly foreach_ID_link's row set, flags included (a revision check relies on it). */
TEST_F(AngeloStageLinksTest, links_are_exactly_the_foreach_id_link_rows)
{
  Mesh *a = mesh("a");
  Mesh *b = mesh("b");
  Material *material = BKE_material_add(bmain, "material");
  a->texcomesh = b;
  hold(a->id, "material", &material->id);
  hold(material->id, "mesh", &b->id);
  using Row = std::tuple<ID *, ID *, ID *, int>;
  Vector<Row> expected, actual;
  for (ID *id : {&a->id, &material->id}) {
    BKE_library_foreach_ID_link(
        bmain,
        id,
        [&](LibraryIDLinkCallbackData *data) {
          if (*data->id_pointer) {
            expected.append(
                {data->owner_id, data->self_id, *data->id_pointer, int(data->cb_flag)});
          }
          return IDWALK_RET_NOP;
        },
        nullptr,
        IDWALK_NOP);
  }
  std::string error;
  const auto record = [&](ID *o, ID *h, ID *t, int f) { actual.append({o, h, t, f}); };
  ASSERT_TRUE(BKE_angelo_stage_links(*bmain, {&a->id, &material->id}, record, error));
  EXPECT_EQ(actual, expected);
  bool texture_mesh = false, embedded_tree = false;
  for (const Row &row : actual) {
    texture_mesh |= std::get<0>(row) == &a->id && std::get<2>(row) == &b->id &&
                    !(std::get<3>(row) & IDWALK_CB_USER);
    embedded_tree |= std::get<2>(row) == reinterpret_cast<ID *>(material->nodetree) &&
                     (std::get<3>(row) & IDWALK_CB_EMBEDDED);
  }
  EXPECT_TRUE(texture_mesh) << "a non-user pointer is a row";
  EXPECT_TRUE(embedded_tree) << "an owned embedded ID is a row, flagged embedded";
}

/* The walk is fresh: a relations cache built before a change never answers it. */
TEST_F(AngeloStageLinksTest, a_stale_relations_cache_never_answers)
{
  Mesh *a = mesh("a");
  Mesh *b = mesh("b");
  Mesh *c = mesh("c");
  a->texcomesh = b;
  BKE_main_relations_create(bmain, 0);
  a->texcomesh = c; /* After the cache was built. */
  Vector<ID *> outside;
  std::string error;
  ASSERT_TRUE(relink(*bmain, {&a->id}, {}, outside, error)) << error;
  BKE_main_relations_free(bmain);
  EXPECT_TRUE(outside.contains(&c->id));
  EXPECT_FALSE(outside.contains(&b->id));
}

/* The journal restores exactly the slots relink changed, and never a slot changed since. */
TEST_F(AngeloStageLinksTest, unrelink_restores_exactly_what_relink_changed)
{
  Mesh *staged = mesh("staged");
  Mesh *live = mesh("live");
  Mesh *copy = mesh("copy");
  Mesh *later = mesh("later");
  staged->texcomesh = live;
  hold(staged->id, "held", &live->id);
  hold(staged->id, "kept", &copy->id); /* Already the copy: not a slot relink changes. */
  const int live_users = live->id.us, copy_users = copy->id.us;
  Vector<ID *> outside;
  Vector<AngeloStageRelinkSlot> journal;
  std::string error;
  const std::pair<ID *, ID *> pair = {&live->id, &copy->id};
  ASSERT_TRUE(BKE_angelo_stage_relink(*bmain, {&staged->id}, {pair}, outside, journal, error));
  EXPECT_EQ(journal.size(), 2);
  staged->texcomesh = later; /* Changed since the relink: never restored. */
  int skipped = 0;
  EXPECT_EQ(BKE_angelo_stage_unrelink(*bmain, journal, skipped), 1);
  EXPECT_EQ(skipped, 1);
  EXPECT_EQ(staged->texcomesh, later);
  EXPECT_EQ(held(staged->id, "held"), &live->id);
  EXPECT_EQ(held(staged->id, "kept"), &copy->id) << "a slot relink did not change stays";
  EXPECT_EQ(live->id.us, live_users);
  EXPECT_EQ(copy->id.us, copy_users);
}

/* An ID outside Main is refused before anything is written. */
TEST_F(AngeloStageLinksTest, ids_outside_main_are_refused)
{
  Mesh *staged = mesh("staged");
  Mesh *live = mesh("live");
  Mesh *stray = BKE_id_new_nomain<Mesh>("stray");
  staged->texcomesh = live;
  Vector<ID *> outside;
  std::string error;
  const std::pair<ID *, ID *> pair = {&live->id, &stray->id};
  EXPECT_FALSE(relink(*bmain, {&staged->id}, {pair}, outside, error));
  EXPECT_EQ(staged->texcomesh, live);
  BKE_id_free(nullptr, stray);
}

/* reach lists embedded IDs and what holds animation, and stops at the limit. */
TEST_F(AngeloStageLinksTest, reach_sees_embedded_ids_and_animation)
{
  Mesh *a = mesh("a");
  Mesh *b = mesh("b");
  Material *material = BKE_material_add(bmain, "material");
  a->texcomesh = b;
  hold(b->id, "material", &material->id);
  Vector<ID *> reached, animated;
  std::string error;
  ASSERT_TRUE(BKE_angelo_stage_reach(*bmain, {&a->id}, {}, 100, reached, animated, error));
  EXPECT_TRUE(reached.contains(&b->id));
  EXPECT_TRUE(reached.contains(&material->id));
  EXPECT_TRUE(reached.contains(reinterpret_cast<ID *>(material->nodetree)));
  EXPECT_TRUE(animated.is_empty());
  EXPECT_FALSE(BKE_angelo_stage_reach(*bmain, {&a->id}, {}, 1, reached, animated, error));
  ASSERT_TRUE(BKE_angelo_stage_reach(*bmain, {&a->id}, {&b->id}, 100, reached, animated, error));
  EXPECT_FALSE(reached.contains(&material->id)) << "an excluded ID is never entered";
}

/* A plain copy is local, never an override, with no users. */
TEST_F(AngeloStageLinksTest, plain_copies_are_local_with_no_users)
{
  Mesh *live = mesh("live");
  ID *copy = BKE_angelo_stage_copy_plain(*bmain, live->id);
  ASSERT_NE(copy, nullptr);
  EXPECT_EQ(copy->lib, nullptr);
  EXPECT_FALSE(ID_IS_OVERRIDE_LIBRARY(copy));
  EXPECT_EQ(copy->us, 0);
  Material *material = BKE_material_add(bmain, "material");
  const ID &tree = *reinterpret_cast<ID *>(material->nodetree);
  EXPECT_EQ(BKE_angelo_stage_copy_plain(*bmain, tree), nullptr);
}


/* A stage scene is held unless its hold is exactly 0; any other scene never is (scene.cc returns
 * before rebuilding or evaluating a held stage's graph). */
TEST_F(AngeloStageLinksTest, a_stage_scene_is_held_unless_released)
{
  Scene *person = BKE_scene_add(bmain, "Scene");
  Scene *stage = BKE_scene_add(bmain, "Stage");
  IDP_AddToGroup(IDP_EnsureProperties(&stage->id),
                 idprop::create("_angelo_program_stage", StringRefNull("abcd")).release());
  EXPECT_FALSE(BKE_angelo_stage_held(*person));
  EXPECT_TRUE(BKE_angelo_stage_held(*stage));
  IDP_AddToGroup(IDP_EnsureProperties(&stage->id), idprop::create("_angelo_stage_hold", int32_t(0)).release());
  EXPECT_FALSE(BKE_angelo_stage_held(*stage));
  /* One release lets exactly one pass evaluate: taking it holds the stage again. */
  EXPECT_FALSE(BKE_angelo_stage_take_hold(*stage));
  EXPECT_TRUE(BKE_angelo_stage_held(*stage));
  EXPECT_TRUE(BKE_angelo_stage_take_hold(*stage));
  EXPECT_FALSE(BKE_angelo_stage_take_hold(*person));
  IDP_AddToGroup(IDP_EnsureProperties(&person->id), idprop::create("_angelo_stage_hold", int32_t(0)).release());
  EXPECT_FALSE(BKE_angelo_stage_held(*person));
}

/* Every ID made after a reading of the newest session UID has a greater one. */
TEST_F(AngeloStageLinksTest, ids_made_later_have_greater_session_uids)
{
  const uint32_t before = BKE_lib_libblock_session_uid_last();
  Mesh *made = mesh("Made");
  EXPECT_GT(made->id.session_uid, before);
  EXPECT_EQ(BKE_lib_libblock_session_uid_last(), made->id.session_uid);
}

}  // namespace blender::bke::tests
