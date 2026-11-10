/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/* Angelo: what the upstream query lists, on graphs Blender builds from real scenes. */

#include "BKE_idprop.hh"

#include "intern/depsgraph_angelo_upstream_test_fixture.hh"

namespace blender::deg::tests {

/** A data-block custom property `name` on `holder` pointing at `pointee`. */
static IDProperty *add_pointer_property(ID *holder, const char *name, ID *pointee)
{
  IDProperty *prop = bke::idprop::create(name, pointee).release();
  IDP_AddToGroup(IDP_EnsureProperties(holder), prop);
  return prop;
}

TEST_F(DegAngeloUpstreamTest, rig_inputs_of_a_skinned_mesh)
{
  build_graph();
  const Upstream body_up = up(&body->id);
  ASSERT_EQ(body_up.status, DEGUpstreamStatus::Ok);
  EXPECT_TRUE(body_up.has(&body->id, NodeType::GEOMETRY));
  EXPECT_TRUE(body_up.has(body->data, NodeType::GEOMETRY));
  EXPECT_TRUE(body_up.has(&rig->id, NodeType::EVAL_POSE));
  EXPECT_TRUE(body_up.has(rig->data));
  /* The IK target is two hops away (mesh <- rig pose <- target transform). */
  EXPECT_TRUE(body_up.has(&target->id, NodeType::TRANSFORM));
  EXPECT_EQ(body_up.id_type_by_uid.lookup_default(target->id.session_uid, -1), int(ID_OB));
  EXPECT_EQ(body_up.id_type_by_uid.lookup_default(body->data->session_uid, -1), int(ID_ME));
  /* The IK solver also waits for the target's copy: listed one hop deep, never followed, so the
   * target's mesh, which only its copy order relates to, is not upstream. */
  EXPECT_TRUE(body_up.has(&target->id, NodeType::COPY_ON_EVAL));
  EXPECT_FALSE(body_up.has(target->data));
  /* The custom shape is linked by a visibility relation only. */
  EXPECT_FALSE(body_up.has(&widget->id));
  /* Every object reaches its collections' hierarchy, up to the Scene's. */
  EXPECT_TRUE(body_up.has(&scene->master_collection->id, NodeType::HIERARCHY));
  EXPECT_FALSE(body_up.depends_on_time());

  /* Direction: the mesh feeds neither its rig nor the target. */
  const Upstream rig_up = up(&rig->id);
  ASSERT_EQ(rig_up.status, DEGUpstreamStatus::Ok);
  EXPECT_TRUE(rig_up.has(&target->id, NodeType::TRANSFORM));
  EXPECT_FALSE(rig_up.has(&body->id));
  EXPECT_FALSE(rig_up.has(&widget->id));
  const Upstream target_up = up(&target->id);
  ASSERT_EQ(target_up.status, DEGUpstreamStatus::Ok);
  EXPECT_FALSE(target_up.has(&rig->id));
}

TEST_F(DegAngeloUpstreamTest, matching_vertex_group_reaches_pose_and_driver_inputs)
{
  /* The other Armature relation path reads only bones named by deform groups. */
  body_armature->deformflag = ARM_DEF_VGROUP;
  BKE_object_defgroup_new(body, "Tip");
  add_rig_driver("frame * 0.1");
  build_graph();
  const Upstream found = up(&body->id);
  ASSERT_EQ(found.status, DEGUpstreamStatus::Ok);
  EXPECT_TRUE(found.has(&rig->id, NodeType::BONE));
  EXPECT_TRUE(found.has(&rig->id, NodeType::EVAL_POSE));
  EXPECT_TRUE(found.has(rig->data));
  EXPECT_TRUE(found.has(&target->id, NodeType::TRANSFORM));
  EXPECT_TRUE(found.has(&rig->id, NodeType::PARAMETERS));
  EXPECT_TRUE(found.depends_on_time());
  EXPECT_FALSE(found.has(&widget->id));
}

TEST_F(DegAngeloUpstreamTest, vertex_group_modifier_without_groups_does_not_read_pose)
{
  /* The original fixture had this configuration: the modifier reads transforms, not bones.
   * The oracle must not invent a pose dependency just because an Armature modifier exists. */
  body_armature->deformflag = ARM_DEF_VGROUP;
  add_rig_driver("frame * 0.1");
  build_graph();
  const Upstream found = up(&body->id);
  ASSERT_EQ(found.status, DEGUpstreamStatus::Ok);
  EXPECT_TRUE(found.has(&rig->id, NodeType::TRANSFORM));
  EXPECT_FALSE(found.has(&rig->id, NodeType::EVAL_POSE));
  EXPECT_FALSE(found.has(&rig->id, NodeType::BONE));
  EXPECT_FALSE(found.has(rig->data));
  EXPECT_FALSE(found.has(&target->id));
  EXPECT_FALSE(found.depends_on_time());
  const Upstream rig_up = up(&rig->id);
  ASSERT_EQ(rig_up.status, DEGUpstreamStatus::Ok);
  EXPECT_TRUE(rig_up.has(&rig->id, NodeType::PARAMETERS));
  EXPECT_TRUE(rig_up.depends_on_time());
}

TEST_F(DegAngeloUpstreamTest, unmatched_vertex_group_does_not_read_pose)
{
  body_armature->deformflag = ARM_DEF_VGROUP;
  BKE_object_defgroup_new(body, "Unrelated group");
  add_rig_driver("frame * 0.1");
  build_graph();
  const Upstream found = up(&body->id);
  ASSERT_EQ(found.status, DEGUpstreamStatus::Ok);
  EXPECT_TRUE(found.has(&rig->id, NodeType::TRANSFORM));
  EXPECT_FALSE(found.has(&rig->id, NodeType::EVAL_POSE));
  EXPECT_FALSE(found.has(&rig->id, NodeType::BONE));
  EXPECT_FALSE(found.has(rig->data));
  EXPECT_FALSE(found.has(&target->id));
  EXPECT_FALSE(found.depends_on_time());
}

TEST_F(DegAngeloUpstreamTest, visibility_relations_are_never_followed)
{
  const Depsgraph &graph = build_graph();
  /* The widget's visibility feeds the armature data's: the one root where following visibility
   * would reach the widget. */
  const IDNode *widget_node = graph.find_id_node(&widget->id);
  const IDNode *armature_node = graph.find_id_node(rig->data);
  ASSERT_NE(widget_node, nullptr);
  ASSERT_NE(armature_node, nullptr);
  bool linked = false;
  for (const OperationNode *op_node : graph.operations) {
    for (const Relation *rel : op_node->inlinks) {
      if (rel->from->get_class() == NodeClass::OPERATION &&
          static_cast<const OperationNode *>(rel->from)->owner->owner == widget_node &&
          op_node->owner->owner == armature_node)
      {
        linked = true;
      }
    }
  }
  ASSERT_TRUE(linked);
  const Upstream armature_up = up(rig->data);
  ASSERT_EQ(armature_up.status, DEGUpstreamStatus::Ok);
  EXPECT_FALSE(armature_up.has(&widget->id));
}

TEST_F(DegAngeloUpstreamTest, frame_only_driver_without_an_action)
{
  /* The driver operation's only inlinks are the time source and its own copy: the case on which
   * a single-inlink shortcut never exits once copies are not followed. */
  add_rig_driver("frame * 0.1");
  build_graph();
  for (const ID *root : {&rig->id, &body->id}) {
    const Upstream found = up(root);
    ASSERT_EQ(found.status, DEGUpstreamStatus::Ok);
    EXPECT_TRUE(found.depends_on_time());
    EXPECT_TRUE(found.has(&rig->id, NodeType::PARAMETERS));
  }
  EXPECT_FALSE(up(&target->id).depends_on_time());
}

TEST_F(DegAngeloUpstreamTest, driver_variable_target_is_listed)
{
  ChannelDriver *driver = add_rig_driver("var");
  add_variable(driver, &target->id, "location[0]");
  build_graph();
  const Upstream found = up(&rig->id);
  ASSERT_EQ(found.status, DEGUpstreamStatus::Ok);
  EXPECT_TRUE(found.has(&target->id, NodeType::TRANSFORM));
  EXPECT_FALSE(found.depends_on_time());
}

TEST_F(DegAngeloUpstreamTest, driver_reading_its_owner_transform_reaches_the_owner_inputs)
{
  /* An object-level driver on the body writes a property of its mesh from the body's own
   * location. Blender builds no relation for a variable on the driver's own ID (it would be a
   * cycle for drivers of the object's own properties), so the walk must add the owner's
   * transform itself: the body's parent feeds its mesh through that driver. */
  body->parent = target;
  AnimData *adt = BKE_animdata_ensure_id(&body->id);
  FCurve *fcu = BKE_fcurve_create();
  fcu->rna_path = BLI_strdup("data.remesh_voxel_size");
  fcu->driver = MEM_new<ChannelDriver>("ChannelDriver");
  fcu->driver->type = DRIVER_TYPE_AVERAGE;
  BLI_addtail(&adt->drivers, fcu);
  DriverVar *dvar = driver_add_new_variable(fcu->driver);
  driver_change_variable_type(dvar, DVAR_TYPE_TRANSFORM_CHAN);
  dvar->targets[0].id = &body->id;
  dvar->targets[0].transChan = DTAR_TRANSCHAN_LOCX;
  build_graph();
  const Upstream found = up(body->data);
  ASSERT_EQ(found.status, DEGUpstreamStatus::Ok);
  EXPECT_TRUE(found.has(&body->id, NodeType::TRANSFORM));
  EXPECT_TRUE(found.has(&target->id, NodeType::TRANSFORM));
}

TEST_F(DegAngeloUpstreamTest, undeclared_driver_reads_refuse)
{
  /* A full Python expression can read anything; a variable path that does not resolve now has no
   * relation, yet evaluation resolves it again. Neither is listed as complete. */
  add_rig_driver("bpy.data.objects['Target'].location.x");
  build_graph();
  EXPECT_EQ(up(&rig->id).status, DEGUpstreamStatus::UndeclaredReads);
  EXPECT_EQ(up(&body->id).status, DEGUpstreamStatus::UndeclaredReads);
  EXPECT_EQ(up(&target->id).status, DEGUpstreamStatus::Ok);
}

TEST_F(DegAngeloUpstreamTest, unresolved_driver_path_refuses)
{
  ChannelDriver *driver = add_rig_driver("var");
  add_variable(driver, &target->id, "[\"not_yet_there\"]");
  build_graph();
  EXPECT_EQ(up(&rig->id).status, DEGUpstreamStatus::UndeclaredReads);
  EXPECT_EQ(up(&target->id).status, DEGUpstreamStatus::Ok);
}

TEST_F(DegAngeloUpstreamTest, action_animated_rig)
{
  animrig::Action &action = animrig::action_add(*bmain, "Action");
  ASSERT_TRUE(animrig::assign_action(&action, rig->id));
  animrig::action_fcurve_ensure(bmain, action, rig->id, {"location", 0});
  build_graph();
  const Upstream found = up(&body->id);
  ASSERT_EQ(found.status, DEGUpstreamStatus::Ok);
  EXPECT_TRUE(found.has(&action.id, NodeType::ANIMATION));
  EXPECT_TRUE(found.has(&rig->id, NodeType::ANIMATION));
  EXPECT_TRUE(found.depends_on_time());
}

TEST_F(DegAngeloUpstreamTest, time_dependent_modifier_and_cache_file)
{
  Object *wave = add_object(OB_MESH, "Wave", &BKE_id_new<Mesh>(bmain, "Wave mesh")->id);
  add_modifier(wave, eModifierType_Wave);
  Object *cached = add_object(OB_MESH, "Cached", &BKE_id_new<Mesh>(bmain, "Cached mesh")->id);
  CacheFile *cache_file = static_cast<CacheFile *>(BKE_cachefile_add(bmain, "Cache"));
  cache_file->is_sequence = true;
  add_modifier<MeshSeqCacheModifierData>(cached, eModifierType_MeshSequenceCache)->cache_file =
      cache_file;
  build_graph();
  const Upstream wave_up = up(&wave->id);
  ASSERT_EQ(wave_up.status, DEGUpstreamStatus::Ok);
  EXPECT_TRUE(wave_up.depends_on_time());
  const Upstream cached_up = up(&cached->id);
  ASSERT_EQ(cached_up.status, DEGUpstreamStatus::Ok);
  EXPECT_TRUE(cached_up.has(&cache_file->id, NodeType::CACHE));
  EXPECT_TRUE(cached_up.depends_on_time());
  EXPECT_FALSE(cached_up.has(&wave->id));
}

/* Relations follow what a driver path names when they are built. A write that re-points data the
 * path passes through without tagging the relations (C code, a Python collection move or removal,
 * a write from another thread) must not leave the old relation reported as complete. */
TEST_F(DegAngeloUpstreamTest, driver_path_re_pointed_without_a_tag_is_stale)
{
  Object *other = add_object(OB_EMPTY, "Other", nullptr);
  IDProperty *prop = add_pointer_property(&target->id, "pointer", &widget->id);
  add_variable(add_rig_driver("var"), &target->id, "[\"pointer\"].location[0]");
  Depsgraph &graph = build_graph();
  const Upstream before = up(&rig->id);
  ASSERT_EQ(before.status, DEGUpstreamStatus::Ok);
  EXPECT_TRUE(before.has(&widget->id, NodeType::TRANSFORM));
  EXPECT_FALSE(before.has(&other->id));

  IDP_AssignID(prop, &widget->id, 0);
  EXPECT_EQ(up(&rig->id).status, DEGUpstreamStatus::Ok) << "the same data-block again";

  IDP_AssignID(prop, &other->id, 0);
  ASSERT_FALSE(graph.need_update_relations);
  EXPECT_EQ(up(&rig->id).status, DEGUpstreamStatus::RelationsStale);
  EXPECT_EQ(up(&body->id).status, DEGUpstreamStatus::RelationsStale) << "downstream of the rig";
  EXPECT_EQ(up(&target->id).status, DEGUpstreamStatus::Ok) << "the driver is not upstream of it";

  DEG_relations_tag_update(bmain);
  build_graph();
  const Upstream after = up(&rig->id);
  ASSERT_EQ(after.status, DEGUpstreamStatus::Ok);
  EXPECT_TRUE(after.has(&other->id, NodeType::TRANSFORM));
  EXPECT_FALSE(after.has(&widget->id, NodeType::TRANSFORM));
}

TEST_F(DegAngeloUpstreamTest, driver_path_that_stops_resolving_without_a_tag_is_stale)
{
  IDProperty *prop = add_pointer_property(&target->id, "pointer", &widget->id);
  add_variable(add_rig_driver("var"), &target->id, "[\"pointer\"].location[0]");
  build_graph();
  ASSERT_EQ(up(&rig->id).status, DEGUpstreamStatus::Ok);
  IDP_FreeFromGroup(IDP_EnsureProperties(&target->id), prop);
  EXPECT_EQ(up(&rig->id).status, DEGUpstreamStatus::RelationsStale);
}

TEST_F(DegAngeloUpstreamTest, driver_path_through_its_own_target_re_pointed_is_stale)
{
  /* A path that leaves its target ID and comes back to it names the target at build time; the
   * check must not depend on the path naming another ID. */
  IDProperty *prop = add_pointer_property(&target->id, "pointer", &target->id);
  add_variable(add_rig_driver("var"), &target->id, "[\"pointer\"].location[0]");
  build_graph();
  const Upstream before = up(&rig->id);
  ASSERT_EQ(before.status, DEGUpstreamStatus::Ok);
  EXPECT_TRUE(before.has(&target->id, NodeType::TRANSFORM));
  IDP_AssignID(prop, &widget->id, 0);
  EXPECT_EQ(up(&rig->id).status, DEGUpstreamStatus::RelationsStale);
}

}  // namespace blender::deg::tests
