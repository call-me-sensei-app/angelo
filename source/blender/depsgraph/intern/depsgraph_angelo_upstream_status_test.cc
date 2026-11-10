/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/* Angelo: the upstream query's refusals and that reading changes nothing. */

#include "intern/depsgraph_angelo_upstream_test_fixture.hh"

namespace blender::deg::tests {

TEST_F(DegAngeloUpstreamTest, reads_leave_graph_and_originals_unchanged)
{
  Depsgraph &graph = build_graph();
  auto snapshot = [&]() {
    Vector<int64_t> state = {graph.need_update_relations,
                             graph.is_active,
                             graph.id_nodes.size(),
                             graph.operations.size(),
                             graph.entry_tags.size(),
                             int64_t(graph.update_count),
                             int64_t(rig->pose->flag),
                             int64_t(reinterpret_cast<bArmature *>(rig->data)->flag)};
    for (const IDNode *node : graph.id_nodes) {
      state.append(node->id_cow == node->id_orig || deg_eval_copy_is_expanded(node->id_cow));
      state.append(node->id_cow->recalc);
    }
    for (const OperationNode *op_node : graph.operations) {
      state.append(op_node->flag);
    }
    ID *id;
    FOREACH_MAIN_ID_BEGIN (bmain, id) {
      state.append(id->recalc);
      state.append(id->tag);
    }
    FOREACH_MAIN_ID_END;
    for (const bPoseChannel &pchan : rig->pose->chanbase) {
      state.append(pchan.flag);
      state.append(pchan.constflag);
    }
    return state;
  };
  const Vector<int64_t> before = snapshot();
  for (int i = 0; i < 3; i++) {
    for (const ID *root : {&body->id, &rig->id, &target->id, &widget->id}) {
      EXPECT_EQ(up(root).status, DEGUpstreamStatus::Ok);
    }
  }
  EXPECT_EQ(before, snapshot());

  /* Stale relations are a status: nothing is rebuilt. */
  DEG_graph_tag_relations_update(reinterpret_cast<blender::Depsgraph *>(&graph));
  const Vector<int64_t> stale = snapshot();
  const Upstream found = up(&body->id);
  EXPECT_EQ(found.status, DEGUpstreamStatus::RelationsStale);
  EXPECT_TRUE(found.is_empty());
  EXPECT_EQ(stale, snapshot());
  EXPECT_TRUE(graph.need_update_relations);
}

TEST_F(DegAngeloUpstreamTest, refusals_never_create_or_read_a_graph)
{
  /* No graph yet: the look-up creates none. */
  EXPECT_EQ(up(&body->id).status, DEGUpstreamStatus::NoGraph);
  EXPECT_EQ(BKE_scene_get_depsgraph(scene, view_layer), nullptr);

  /* An object in an excluded collection that nothing uses has no node. */
  Collection *excluded = BKE_collection_add(bmain, scene->master_collection, "Excluded");
  Object *outside = BKE_object_add_only_object(bmain, OB_EMPTY, "Outside");
  BKE_collection_object_add(bmain, excluded, outside);
  BKE_view_layer_synced_ensure(*bmain, scene, view_layer);
  BKE_layer_collection_first_from_scene_collection(view_layer, excluded)->flag |=
      LAYER_COLLECTION_EXCLUDE;
  BKE_view_layer_need_resync_tag(view_layer);
  Depsgraph &graph = build_graph();
  EXPECT_EQ(up(&outside->id).status, DEGUpstreamStatus::NotInGraph);

  graph.is_evaluating = true;
  EXPECT_EQ(up(&body->id).status, DEGUpstreamStatus::Evaluating);
  graph.is_evaluating = false;

  /* A bake evaluating this graph on a job thread holds the interface locked. */
  auto wm = std::make_unique<wmWindowManager>();
  bke::WindowManagerRuntime wm_runtime;
  wm->runtime = &wm_runtime;
  BLI_addtail(&bmain->wm, wm.get());
  wm_runtime.is_interface_locked = true;
  EXPECT_EQ(up(&body->id).status, DEGUpstreamStatus::Evaluating);
  wm_runtime.is_interface_locked = false;
  EXPECT_EQ(up(&body->id).status, DEGUpstreamStatus::Ok);
  BLI_remlink(&bmain->wm, wm.get());

  /* Inside a materialize scope the relations admit hidden bases. After it, a graph whose profile
   * changed without a rebuild holds a superset of nodes with the same edges: still listed. */
  graph.checkpoint_materialize_cookie = 1;
  EXPECT_EQ(up(&body->id).status, DEGUpstreamStatus::RelationsStale);
  graph.checkpoint_materialize_cookie = 0;
  graph.use_visibility_optimization = !graph.use_visibility_optimization;
  EXPECT_EQ(up(&body->id).status, DEGUpstreamStatus::Ok);
  graph.use_visibility_optimization = !graph.use_visibility_optimization;

  /* A node built for an earlier ID at the same address does not describe this one. */
  IDNode *node = graph.find_id_node(&body->id);
  node->id_orig_session_uid += 1;
  EXPECT_EQ(up(&body->id).status, DEGUpstreamStatus::RelationsStale);
  node->id_orig_session_uid -= 1;

  DEGUpstreamStatus off_main = DEGUpstreamStatus::Ok;
  int calls = 0;
  std::thread worker([&]() {
    off_main = DEG_angelo_foreach_upstream(
        bmain, scene, view_layer, &body->id, [&](const DEGUpstreamRecord &) { calls++; });
  });
  worker.join();
  EXPECT_EQ(off_main, DEGUpstreamStatus::NotMainThread);
  EXPECT_EQ(calls, 0);
  EXPECT_EQ(up(&body->id).status, DEGUpstreamStatus::Ok);
}

TEST_F(DegAngeloUpstreamTest, operation_bound_refuses_instead_of_truncating)
{
  build_graph();
  const blender::Depsgraph *graph = BKE_scene_get_depsgraph(scene, view_layer);
  int full = 0;
  within_watchdog([&]() {
    EXPECT_EQ(DEG_foreach_ancestor_component(
                  graph, &body->id, [&](const DEGUpstreamRecord &) { full++; }),
              DEGUpstreamStatus::Ok);
    EXPECT_EQ(DEG_foreach_ancestor_component(graph, &body->id, [](const DEGUpstreamRecord &) {}, 2),
              DEGUpstreamStatus::TooLarge);
  });
  EXPECT_GT(full, 0);
}

}  // namespace blender::deg::tests
