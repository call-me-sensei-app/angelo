/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include <memory>
#include <thread>

#include "BKE_gtest_base.hh"
#include "BKE_layer.hh"
#include "BKE_lib_id.hh"
#include "BKE_main.hh"

#include "DNA_image_types.h"
#include "DNA_layer_types.h"
#include "DNA_mesh_types.h"
#include "DNA_object_types.h"
#include "DNA_scene_types.h"

#include "DEG_depsgraph_query.hh"

#include "intern/depsgraph.hh"
#include "intern/eval/deg_eval_copy_on_write.h"
#include "intern/node/deg_node_id.hh"

#include "testing/testing.h"

namespace blender::deg::tests {

class DegQueryForeachTest : public bke::BlenderGTestBase {};

using MainPtr = std::unique_ptr<Main, decltype(&BKE_main_free)>;
using GraphPtr = std::unique_ptr<blender::Depsgraph, decltype(&DEG_graph_free)>;

static Depsgraph &internal_graph(const GraphPtr &graph)
{
  return *reinterpret_cast<Depsgraph *>(graph.get());
}

TEST_F(DegQueryForeachTest, all_registered_graphs_and_metadata)
{
  MainPtr bmain(BKE_main_new(), BKE_main_free);
  MainPtr other_main(BKE_main_new(), BKE_main_free);
  Scene *scene = BKE_id_new<Scene>(bmain.get(), "Scene");
  Scene *other_scene = BKE_id_new<Scene>(other_main.get(), "Other scene");
  ViewLayer *layer = static_cast<ViewLayer *>(scene->view_layers.first);
  ASSERT_NE(layer, nullptr);
  GraphPtr first(DEG_graph_new(bmain.get(), scene, layer, DAG_EVAL_VIEWPORT), DEG_graph_free);
  GraphPtr second(DEG_graph_new(bmain.get(), scene, layer, DAG_EVAL_RENDER), DEG_graph_free);
  GraphPtr empty(DEG_graph_new(bmain.get(), scene, nullptr, DAG_EVAL_VIEWPORT), DEG_graph_free);
  GraphPtr foreign(DEG_graph_new(other_main.get(), other_scene, nullptr, DAG_EVAL_VIEWPORT),
                   DEG_graph_free);
  Depsgraph &source = internal_graph(second);
  source.frame = 12.25f;
  source.ctime = 24.5f;
  source.is_evaluating = true;
  source.is_active = true;
  source.use_visibility_optimization = false;
  source.is_render_pipeline_depsgraph = true;
  source.use_editors_update = true;
  source.need_update_relations = true;
  source.need_update_nodes_visibility = false;
  source.sync_writeback = DEG_EVALUATE_SYNC_WRITEBACK_YES;
  source.debug.flags = 5;
  source.update_count = 17;

  Vector<DEGRegisteredGraph> found;
  EXPECT_TRUE(DEG_foreach_registered_graph(bmain.get(), [&](const DEGRegisteredGraph &info) {
    found.append(info);
    return true;
  }));
  ASSERT_EQ(found.size(), 3);
  EXPECT_EQ(found[0].depsgraph, first.get());
  EXPECT_EQ(found[1].depsgraph, second.get());
  EXPECT_EQ(found[2].depsgraph, empty.get());
  EXPECT_EQ(found[2].view_layer, nullptr);
  const DEGRegisteredGraph &info = found[1];
  EXPECT_EQ(info.source_main, bmain.get());
  EXPECT_EQ(info.scene, scene);
  EXPECT_EQ(info.view_layer, layer);
  EXPECT_EQ(info.scene_original_session_uid, 0);
  EXPECT_EQ(info.mode, DAG_EVAL_RENDER);
  EXPECT_EQ(info.frame, 12.25f);
  EXPECT_EQ(info.ctime, 24.5f);
  EXPECT_TRUE(info.is_evaluating);
  EXPECT_TRUE(info.is_active);
  EXPECT_FALSE(info.use_visibility_optimization);
  EXPECT_TRUE(info.is_render_pipeline);
  EXPECT_TRUE(info.use_editors_update);
  EXPECT_TRUE(info.need_update_relations);
  EXPECT_FALSE(info.need_update_nodes_visibility);
  EXPECT_FALSE(info.has_pending_updates);
  EXPECT_EQ(info.sync_writeback, DEG_EVALUATE_SYNC_WRITEBACK_YES);
  EXPECT_EQ(info.debug_flags, 5);
  EXPECT_EQ(info.update_count, 17);
  EXPECT_TRUE(source.is_evaluating);
  EXPECT_EQ(source.update_count, 17);
  EXPECT_TRUE(source.id_nodes.is_empty());
  EXPECT_EQ(source.scene, scene);
  EXPECT_EQ(source.view_layer, layer);

  /* Owner replacement moves registry membership; neither query touches the other Main's graphs. */
  DEG_graph_replace_owners(foreign.get(), bmain.get(), scene, layer);
  int count = 0;
  EXPECT_TRUE(DEG_foreach_registered_graph(other_main.get(), [&](const DEGRegisteredGraph &) {
    ++count;
    return true;
  }));
  EXPECT_EQ(count, 0);
  EXPECT_TRUE(DEG_foreach_registered_graph(bmain.get(), [&](const DEGRegisteredGraph &) {
    ++count;
    return true;
  }));
  EXPECT_EQ(count, 4);
}

TEST_F(DegQueryForeachTest, stale_owner_addresses_are_not_dereferenced)
{
  MainPtr bmain(BKE_main_new(), BKE_main_free);
  Scene *scene = BKE_id_new<Scene>(bmain.get(), "Scene");
  Scene *other_scene = BKE_id_new<Scene>(bmain.get(), "Other scene");
  ViewLayer *layer = static_cast<ViewLayer *>(scene->view_layers.first);
  GraphPtr graph(DEG_graph_new(bmain.get(), scene, layer, DAG_EVAL_VIEWPORT), DEG_graph_free);
  Depsgraph &source = internal_graph(graph);
  Scene *stale_scene = reinterpret_cast<Scene *>(uintptr_t(1));
  ViewLayer *stale_layer = reinterpret_cast<ViewLayer *>(uintptr_t(1));
  source.scene = stale_scene;
  source.view_layer = stale_layer;
  EXPECT_TRUE(DEG_foreach_registered_graph(bmain.get(), [&](const DEGRegisteredGraph &info) {
    EXPECT_EQ(info.scene, nullptr);
    EXPECT_EQ(info.view_layer, nullptr);
    EXPECT_EQ(info.scene_original_session_uid, 0);
    return true;
  }));
  EXPECT_EQ(source.scene, stale_scene);
  EXPECT_EQ(source.view_layer, stale_layer);
  source.scene = scene;
  EXPECT_TRUE(DEG_foreach_registered_graph(bmain.get(), [&](const DEGRegisteredGraph &info) {
    EXPECT_EQ(info.scene, scene);
    EXPECT_EQ(info.view_layer, nullptr);
    return true;
  }));
  source.view_layer = static_cast<ViewLayer *>(other_scene->view_layers.first);
  EXPECT_TRUE(DEG_foreach_registered_graph(bmain.get(), [&](const DEGRegisteredGraph &info) {
    EXPECT_EQ(info.scene, scene);
    EXPECT_EQ(info.view_layer, nullptr);
    return true;
  }));
  source.view_layer = layer;
}

TEST_F(DegQueryForeachTest, primary_scene_cached_identity_does_not_follow_live_address)
{
  MainPtr bmain(BKE_main_new(), BKE_main_free);
  Scene *scene = BKE_id_new<Scene>(bmain.get(), "Primary scene");
  Scene *secondary_scene = BKE_id_new<Scene>(bmain.get(), "Secondary scene");
  ASSERT_TRUE(BKE_view_layer_synced_ensure(
      *bmain, scene, static_cast<ViewLayer *>(scene->view_layers.first)));
  ASSERT_TRUE(BKE_view_layer_synced_ensure(
      *bmain, secondary_scene, static_cast<ViewLayer *>(secondary_scene->view_layers.first)));
  GraphPtr graph(DEG_graph_new(bmain.get(),
                               scene,
                               static_cast<ViewLayer *>(scene->view_layers.first),
                               DAG_EVAL_VIEWPORT),
                 DEG_graph_free);
  Depsgraph &source = internal_graph(graph);
  auto check_identity = [&](const uint expected_uid, const Scene *expected_scene) {
    int calls = 0;
    EXPECT_TRUE(DEG_foreach_registered_graph(bmain.get(), [&](const DEGRegisteredGraph &info) {
      ++calls;
      EXPECT_EQ(info.scene, expected_scene);
      EXPECT_EQ(info.scene_original_session_uid, expected_uid);
      return true;
    }));
    EXPECT_EQ(calls, 1);
  };
  check_identity(0, scene);
  IDNode *secondary_node = source.add_id_node(&secondary_scene->id);
  secondary_node->linked_state = DEG_ID_LINKED_VIA_SET;
  deg_update_eval_copy_datablock(&source, secondary_node);
  ASSERT_TRUE(deg_eval_copy_is_expanded(secondary_node->id_cow));
  /* An expanded secondary Scene cannot stand in for the graph's primary input. */
  check_identity(0, scene);
  IDNode *primary_node = source.add_id_node(&scene->id);
  primary_node->linked_state = DEG_ID_LINKED_DIRECTLY;
  check_identity(0, scene);
  deg_update_eval_copy_datablock(&source, primary_node);
  ASSERT_TRUE(deg_eval_copy_is_expanded(primary_node->id_cow));
  const uint primary_uid = scene->id.session_uid;
  const uint stale_uid = secondary_scene->id.session_uid;
  ASSERT_NE(primary_uid, stale_uid);
  source.scene_cow = id_cast<Scene *>(secondary_node->id_cow);
  check_identity(0, scene);
  source.scene_cow = id_cast<Scene *>(primary_node->id_cow);
  check_identity(primary_uid, scene);

  /* Model an old graph node surviving address reuse: pointer membership still passes, but the
   * returned historical UID must differ from the live Scene's identity. */
  primary_node->id_orig_session_uid = stale_uid;
  check_identity(stale_uid, scene);
  EXPECT_EQ(scene->id.session_uid, primary_uid);
  EXPECT_EQ(primary_node->id_orig_session_uid, stale_uid);
  EXPECT_EQ(primary_node->id_orig, &scene->id);
  primary_node->id_orig_session_uid = primary_uid;

  source.is_evaluating = true;
  check_identity(0, scene);
  source.is_evaluating = false;
  primary_node->id_type = ID_OB;
  check_identity(0, scene);
  primary_node->id_type = ID_SCE;
  source.scene = reinterpret_cast<Scene *>(uintptr_t(1));
  check_identity(0, nullptr);
  EXPECT_EQ(source.scene, reinterpret_cast<Scene *>(uintptr_t(1)));
  source.scene = scene;
  check_identity(primary_uid, scene);
}

TEST_F(DegQueryForeachTest, expanded_ids_are_unfiltered_and_use_cached_identity)
{
  MainPtr bmain(BKE_main_new(), BKE_main_free);
  Scene *scene = BKE_id_new<Scene>(bmain.get(), "Scene");
  Object *object = BKE_id_new<Object>(bmain.get(), "Object");
  Mesh *mesh = BKE_id_new<Mesh>(bmain.get(), "Mesh");
  object->type = OB_MESH;
  object->data = &mesh->id;
  Object *unexpanded = BKE_id_new<Object>(bmain.get(), "Unexpanded");
  Object *absent = BKE_id_new<Object>(bmain.get(), "Absent copy");
  Object *borrowed = BKE_id_new<Object>(bmain.get(), "Borrowed original");
  Image *image = BKE_id_new<Image>(bmain.get(), "Image");
  GraphPtr graph(DEG_graph_new(bmain.get(), scene, nullptr, DAG_EVAL_VIEWPORT), DEG_graph_free);
  Depsgraph &source = internal_graph(graph);
  IDNode *object_node = source.add_id_node(&object->id);
  IDNode *mesh_node = source.add_id_node(&mesh->id);
  source.add_id_node(&unexpanded->id);
  IDNode *absent_node = source.add_id_node(&absent->id);
  IDNode *borrowed_node = source.add_id_node(&borrowed->id);
  IDNode *image_node = source.add_id_node(&image->id);
  ID *mesh_eval = deg_update_eval_copy_datablock(&source, mesh_node);
  ID *object_eval = deg_update_eval_copy_datablock(&source, object_node);
  ASSERT_TRUE(deg_eval_copy_is_expanded(object_eval));
  ASSERT_TRUE(deg_eval_copy_is_expanded(mesh_eval));
  ASSERT_EQ(id_cast<Object *>(object_eval)->data, mesh_eval);
  object_node->is_visible_on_build = false;
  object_node->is_enabled_on_eval = false;
  object_node->visible_components_mask = 0;
  object_eval->recalc = ID_RECALC_GEOMETRY;
  const uint cached_uid = object_node->id_orig_session_uid;
  object_eval->session_uid = cached_uid + 1;

  /* Poison both paths commonly used to recover original identity. The query must use neither. */
  ID *stale_id = reinterpret_cast<ID *>(uintptr_t(1));
  ID *saved_eval_original = object_eval->orig_id;
  ID *saved_absent_copy = absent_node->id_cow;
  ID *saved_borrowed_copy = borrowed_node->id_cow;
  object_node->id_orig = stale_id;
  object_eval->orig_id = stale_id;
  absent_node->id_cow = nullptr;
  borrowed_node->id_cow = &borrowed->id;
  image_node->id_orig = stale_id;
  image_node->id_cow = stale_id;
  Vector<DEGEvaluatedID> found;
  EXPECT_TRUE(DEG_foreach_evaluated_ID(graph.get(), [&](const DEGEvaluatedID &info) {
    found.append(info);
    return true;
  }));
  EXPECT_EQ(found.size(), 2);
  if (found.size() == 2) {
    EXPECT_EQ(found[0].evaluated_id, object_eval);
    EXPECT_EQ(found[0].original_session_uid, cached_uid);
    EXPECT_EQ(found[0].original_type, ID_OB);
    EXPECT_EQ(found[1].evaluated_id, mesh_eval);
    EXPECT_EQ(found[1].original_session_uid, mesh->id.session_uid);
    EXPECT_EQ(found[1].original_type, ID_ME);
  }
  EXPECT_EQ(object_node->id_orig, stale_id);
  EXPECT_EQ(object_eval->orig_id, stale_id);
  EXPECT_EQ(object_eval->session_uid, cached_uid + 1);
  EXPECT_EQ(object_eval->recalc, ID_RECALC_GEOMETRY);
  EXPECT_FALSE(object_node->is_visible_on_build);
  EXPECT_FALSE(object_node->is_enabled_on_eval);
  EXPECT_EQ(object_node->visible_components_mask, 0);
  EXPECT_EQ(absent_node->id_cow, nullptr);
  EXPECT_EQ(borrowed_node->id_cow, &borrowed->id);
  EXPECT_EQ(image_node->id_cow, stale_id);
  EXPECT_EQ(object->id.session_uid, cached_uid);
  EXPECT_EQ(object->data, &mesh->id);
  EXPECT_EQ(id_cast<Object *>(object_eval)->data, mesh_eval);

  object_node->id_orig = &object->id;
  object_eval->orig_id = saved_eval_original;
  absent_node->id_cow = saved_absent_copy;
  borrowed_node->id_cow = saved_borrowed_copy;
  image_node->id_orig = &image->id;
  image_node->id_cow = &image->id;
}

TEST_F(DegQueryForeachTest, refusal_and_early_termination)
{
  MainPtr bmain(BKE_main_new(), BKE_main_free);
  Scene *scene = BKE_id_new<Scene>(bmain.get(), "Scene");
  GraphPtr graph(DEG_graph_new(bmain.get(), scene, nullptr, DAG_EVAL_VIEWPORT), DEG_graph_free);
  GraphPtr second(DEG_graph_new(bmain.get(), scene, nullptr, DAG_EVAL_VIEWPORT), DEG_graph_free);
  Depsgraph &source = internal_graph(graph);
  Object *object = BKE_id_new<Object>(bmain.get(), "Object");
  object->type = OB_EMPTY;
  Mesh *mesh = BKE_id_new<Mesh>(bmain.get(), "Mesh");
  deg_update_eval_copy_datablock(&source, source.add_id_node(&object->id));
  deg_update_eval_copy_datablock(&source, source.add_id_node(&mesh->id));
  int graph_calls = 0;
  int id_calls = 0;
  auto stop_graph = [&](const DEGRegisteredGraph &) {
    ++graph_calls;
    return false;
  };
  auto stop_id = [&](const DEGEvaluatedID &) {
    ++id_calls;
    return false;
  };
  EXPECT_FALSE(DEG_foreach_registered_graph(bmain.get(), stop_graph));
  EXPECT_EQ(graph_calls, 1);
  EXPECT_FALSE(DEG_foreach_evaluated_ID(graph.get(), stop_id));
  EXPECT_EQ(id_calls, 1);
  graph_calls = id_calls = 0;
  source.is_evaluating = true;
  EXPECT_FALSE(DEG_foreach_evaluated_ID(graph.get(), stop_id));
  EXPECT_EQ(id_calls, 0);
  EXPECT_TRUE(source.is_evaluating);
  source.is_evaluating = false;
  EXPECT_FALSE(DEG_foreach_registered_graph(nullptr, stop_graph));
  EXPECT_FALSE(DEG_foreach_evaluated_ID(nullptr, stop_id));
  bool graphs_result = true;
  bool ids_result = true;
  std::thread worker([&]() {
    graphs_result = DEG_foreach_registered_graph(bmain.get(), stop_graph);
    ids_result = DEG_foreach_evaluated_ID(graph.get(), stop_id);
  });
  worker.join();
  EXPECT_FALSE(graphs_result);
  EXPECT_FALSE(ids_result);
  EXPECT_EQ(graph_calls, 0);
  EXPECT_EQ(id_calls, 0);
}

}  // namespace blender::deg::tests
