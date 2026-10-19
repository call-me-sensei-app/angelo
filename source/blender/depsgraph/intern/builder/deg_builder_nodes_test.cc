/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include <memory>

#include "BKE_gtest_base.hh"
#include "BKE_lib_id.hh"
#include "BKE_main.hh"

#include "DNA_object_types.h"
#include "DNA_scene_types.h"

#include "intern/builder/deg_builder_cache.h"
#include "intern/builder/deg_builder_nodes.h"
#include "intern/builder/deg_builder_relations.h"
#include "intern/depsgraph.hh"
#include "intern/depsgraph_relation.hh"
#include "intern/eval/deg_eval_copy_on_write.h"
#include "intern/node/deg_node_component.hh"
#include "intern/node/deg_node_id.hh"

#include "testing/testing.h"

namespace blender::deg::tests {

class DegBuilderNodesTest : public bke::BlenderGTestBase {};

static void check_repeated_creation_and_rebuild_metadata(const bool expand_copy)
{
  std::unique_ptr<Main, decltype(&BKE_main_free)> bmain(BKE_main_new(), BKE_main_free);
  Object *object = BKE_id_new<Object>(bmain.get(), "Builder object");
  Scene scene{};
  Depsgraph graph(bmain.get(), &scene, nullptr, DAG_EVAL_VIEWPORT);
  DepsgraphBuilderCache cache;
  DEGCustomDataMeshMasks masks{};
  masks.vert_mask = 1;
  masks.loop_mask = 2;
  ID *expanded_copy = nullptr;
  {
    DepsgraphNodeBuilder builder(bmain.get(), &graph, &cache);
    builder.begin_build();
    IDNode *node = builder.add_id_node(&object->id);
    ID *evaluated = node->id_cow;
    ASSERT_NE(evaluated, nullptr);
    EXPECT_NE(evaluated, &object->id);
    EXPECT_EQ(node->previously_visible_components_mask, 0);
    EXPECT_EQ(node->previous_eval_flags, 0);
    EXPECT_EQ(node->previous_customdata_masks, DEGCustomDataMeshMasks{});
    for (int i = 0; i < 10; i++) {
      EXPECT_EQ(builder.add_id_node(&object->id), node);
      EXPECT_EQ(builder.ensure_cow_id(&object->id), evaluated);
    }
    EXPECT_EQ(graph.id_nodes.size(), 1);
    EXPECT_EQ(graph.operations.size(), 2);
    if (expand_copy) {
      object->loc[0] = 4.25f;
      expanded_copy = deg_update_eval_copy_datablock(&graph, node);
      ASSERT_TRUE(deg_eval_copy_is_expanded(expanded_copy));
      EXPECT_EQ(id_cast<Object *>(expanded_copy)->loc[0], 4.25f);
      EXPECT_TRUE(deg_validate_eval_copy_datablock(expanded_copy));
    }
    /* These are the previous graph's outputs, restored as metadata on the next build. */
    node->visible_components_mask = 7;
    node->eval_flags = 3;
    node->customdata_masks = masks;
  }
  {
    DepsgraphNodeBuilder builder(bmain.get(), &graph, &cache);
    builder.begin_build();
    IDNode *node = builder.add_id_node(&object->id);
    ID *evaluated = node->id_cow;
    if (expand_copy) {
      EXPECT_EQ(evaluated, expanded_copy);
      EXPECT_EQ(evaluated->orig_id, &object->id);
      EXPECT_EQ(id_cast<Object *>(evaluated)->loc[0], 4.25f);
      EXPECT_TRUE(deg_eval_copy_is_expanded(evaluated));
      EXPECT_TRUE(deg_validate_eval_copy_datablock(evaluated));
    }
    for (int i = 0; i < 10; i++) {
      EXPECT_EQ(builder.add_id_node(&object->id), node);
      EXPECT_EQ(builder.ensure_cow_id(&object->id), evaluated);
      EXPECT_EQ(node->previously_visible_components_mask, 7);
      EXPECT_EQ(node->previous_eval_flags, 3);
      EXPECT_EQ(node->previous_customdata_masks, masks);
    }
    EXPECT_EQ(graph.id_nodes.size(), 1);
    EXPECT_EQ(graph.operations.size(), 2);
  }
}

TEST_F(DegBuilderNodesTest, repeated_creation_and_rebuild_metadata)
{
  check_repeated_creation_and_rebuild_metadata(false);
}

TEST_F(DegBuilderNodesTest, expanded_copy_survives_rebuild)
{
  check_repeated_creation_and_rebuild_metadata(true);
}

TEST_F(DegBuilderNodesTest, copy_on_evaluation_relations)
{
  std::unique_ptr<Main, decltype(&BKE_main_free)> bmain(BKE_main_new(), BKE_main_free);
  Object *object = BKE_id_new<Object>(bmain.get(), "Relations object");
  object->type = OB_EMPTY;
  Scene scene{};
  Depsgraph graph(bmain.get(), &scene, nullptr, DAG_EVAL_VIEWPORT);
  DepsgraphBuilderCache cache;
  DepsgraphNodeBuilder nodes(bmain.get(), &graph, &cache);
  nodes.begin_build();
  IDNode *id_node = nodes.add_id_node(&object->id);
  OperationNode *copy = nodes.find_operation_node(
      &object->id, NodeType::COPY_ON_EVAL, OperationCode::COPY_ON_EVAL);
  OperationNode *visibility = nodes.find_operation_node(
      &object->id, NodeType::VISIBILITY, OperationCode::VISIBILITY);
  ASSERT_NE(copy, nullptr);
  ASSERT_NE(visibility, nullptr);
  nodes.add_component_node(&object->id, NodeType::CACHE); /* No operations. */
  OperationNode *single = nodes.add_operation_node(
      &object->id, NodeType::PARAMETERS, OperationCode::PARAMETERS_EVAL);
  OperationNode *entry = nodes.add_operation_node(
      &object->id, NodeType::TRANSFORM, OperationCode::TRANSFORM_INIT);
  entry->set_as_entry();
  OperationNode *child = nodes.add_operation_node(
      &object->id, NodeType::TRANSFORM, OperationCode::TRANSFORM_FINAL);
  OperationNode *dangling = nodes.add_operation_node(
      &object->id, NodeType::TRANSFORM, OperationCode::TRANSFORM_LOCAL);
  graph.add_new_relation(entry, child, "Same component dependency");
  graph.add_new_relation(visibility, dangling, "Other component dependency");
  DepsgraphRelationBuilder relations(bmain.get(), &graph, &cache);
  relations.build_copy_on_write_relations(id_node);
  for (OperationNode *target : {visibility, single, entry, dangling}) {
    const Relation *relation = graph.check_nodes_connected(
        copy, target, "Copy-on-Eval Dependency");
    ASSERT_NE(relation, nullptr);
    EXPECT_EQ(relation->flag, RELATION_FLAG_NO_FLUSH | RELATION_FLAG_GODMODE);
  }
  EXPECT_EQ(graph.check_nodes_connected(copy, child, nullptr), nullptr);
  EXPECT_EQ(copy->outlinks.size(), 4);
}

}  // namespace blender::deg::tests
