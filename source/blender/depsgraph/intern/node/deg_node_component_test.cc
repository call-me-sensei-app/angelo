/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "BKE_gtest_base.hh"

#include "DNA_ID.h"
#include "DNA_scene_types.h"

#include "intern/depsgraph.hh"
#include "intern/node/deg_node_component.hh"

#include "testing/testing.h"

namespace blender::deg::tests {

class DegComponentNodeTest : public bke::BlenderGTestBase {
 protected:
  ID original{};
  IDNode id_node{};
  ComponentNode component;

  void SetUp() override
  {
    id_node.id_orig = &original;
    id_node.id_cow = &original;
    component.owner = &id_node;
    component.type = NodeType::PARAMETERS;
  }
};

TEST_F(DegComponentNodeTest, empty)
{
  EXPECT_EQ(component.find_operation(OperationCode::DRIVER), nullptr);
  EXPECT_EQ(component.get_entry_operation(), nullptr);
  EXPECT_EQ(component.get_exit_operation(), nullptr);
  component.finalize_build(nullptr);
  EXPECT_TRUE(component.operations.is_empty());
  EXPECT_EQ(component.find_operation(OperationCode::DRIVER), nullptr);
}

TEST_F(DegComponentNodeTest, single_operation)
{
  int calls = 0;
  OperationNode *node = component.add_operation(
      [&](blender::Depsgraph *) { ++calls; }, OperationCode::DRIVER, "location", 2);
  EXPECT_EQ(node->owner, &component);
  EXPECT_EQ(component.find_operation(OperationCode::DRIVER, "location", 2), node);
  EXPECT_EQ(component.find_operation(OperationCode::DRIVER, "location", 1), nullptr);
  EXPECT_EQ(component.find_operation(OperationCode::DRIVER, "scale", 2), nullptr);
  EXPECT_EQ(component.get_entry_operation(), node);
  EXPECT_EQ(component.get_exit_operation(), node);
  component.finalize_build(nullptr);
  ASSERT_EQ(component.operations.size(), 1);
  EXPECT_EQ(component.operations[0], node);
  EXPECT_EQ(component.find_operation(OperationCode::DRIVER, "location", 2), node);
  node->evaluate(nullptr);
  EXPECT_EQ(calls, 1);
}

TEST_F(DegComponentNodeTest, multi_operation_lookup_and_order)
{
  Map<ComponentNode::OperationIDKey, OperationNode *> reference;
  for (const int i : IndexRange(32)) {
    const std::string name = "long_property_name_to_exercise_owned_key_storage_" +
                             std::to_string(i / 2);
    OperationNode *node = component.add_operation(nullptr, OperationCode::DRIVER, name, i % 2);
    reference.add({OperationCode::DRIVER, node->name, i % 2}, node);
    EXPECT_EQ(component.find_operation(OperationCode::DRIVER, name, i % 2), node);
  }
  EXPECT_EQ(component.get_entry_operation(), nullptr);
  EXPECT_EQ(component.get_exit_operation(), nullptr);
  for (const auto item : reference.items()) {
    EXPECT_EQ(component.find_operation(item.key), item.value);
  }
  component.finalize_build(nullptr);
  ASSERT_EQ(component.operations.size(), reference.size());
  int index = 0;
  for (OperationNode *node : reference.values()) {
    EXPECT_EQ(component.operations[index++], node);
    EXPECT_EQ(component.find_operation(node->opcode, node->name, node->name_tag), node);
  }
}

TEST_F(DegComponentNodeTest, inferred_endpoints_survive_growth)
{
  OperationNode *first = component.add_operation(nullptr, OperationCode::PARAMETERS_ENTRY);
  EXPECT_EQ(component.get_entry_operation(), first);
  EXPECT_EQ(component.get_exit_operation(), first);
  component.add_operation(nullptr, OperationCode::PARAMETERS_EXIT);
  EXPECT_EQ(component.get_entry_operation(), first);
  EXPECT_EQ(component.get_exit_operation(), first);
  component.finalize_build(nullptr);
  EXPECT_EQ(component.get_entry_operation(), first);
  EXPECT_EQ(component.get_exit_operation(), first);
}

TEST_F(DegComponentNodeTest, explicit_endpoints_survive_growth)
{
  OperationNode *first = component.add_operation(nullptr, OperationCode::PARAMETERS_ENTRY);
  first->set_as_entry();
  OperationNode *last = component.add_operation(nullptr, OperationCode::PARAMETERS_EXIT);
  last->set_as_exit();
  component.finalize_build(nullptr);
  EXPECT_EQ(component.get_entry_operation(), first);
  EXPECT_EQ(component.get_exit_operation(), last);
}

TEST_F(DegComponentNodeTest, tagging_before_and_after_finalization)
{
  Scene scene{};
  Depsgraph graph(nullptr, &scene, nullptr, DAG_EVAL_VIEWPORT);
  OperationNode *first = component.add_operation(nullptr, OperationCode::VISIBILITY);
  graph.need_update_nodes_visibility = false;
  component.tag_update(&graph, DEG_UPDATE_SOURCE_USER_EDIT);
  EXPECT_TRUE(graph.entry_tags.contains(first));
  EXPECT_TRUE(first->flag & DEPSOP_FLAG_USER_MODIFIED);
  EXPECT_TRUE(graph.need_update_nodes_visibility);
  OperationNode *second = component.add_operation(nullptr, OperationCode::DRIVER, "location");
  graph.entry_tags.clear();
  component.tag_update(&graph, DEG_UPDATE_SOURCE_RELATIONS);
  EXPECT_EQ(graph.entry_tags.size(), 2);
  EXPECT_TRUE(second->flag & DEPSOP_FLAG_NEEDS_UPDATE);
  component.finalize_build(nullptr);
  graph.entry_tags.clear();
  component.tag_update(&graph, DEG_UPDATE_SOURCE_USER_EDIT);
  EXPECT_EQ(graph.entry_tags.size(), 2);
  EXPECT_TRUE(graph.entry_tags.contains(first));
  EXPECT_TRUE(graph.entry_tags.contains(second));
  EXPECT_TRUE(second->flag & DEPSOP_FLAG_USER_MODIFIED);
}

TEST_F(DegComponentNodeTest, clear_before_and_after_finalization)
{
  component.add_operation(nullptr, OperationCode::DRIVER, "first");
  component.clear_operations();
  EXPECT_EQ(component.find_operation(OperationCode::DRIVER, "first"), nullptr);
  component.add_operation(nullptr, OperationCode::DRIVER, "second");
  component.add_operation(nullptr, OperationCode::DRIVER, "third");
  component.clear_operations();
  EXPECT_EQ(component.find_operation(OperationCode::DRIVER, "second"), nullptr);
  EXPECT_EQ(component.find_operation(OperationCode::DRIVER, "third"), nullptr);
  component.add_operation(nullptr, OperationCode::DRIVER, "last");
  component.finalize_build(nullptr);
  component.clear_operations();
  EXPECT_TRUE(component.operations.is_empty());
  EXPECT_EQ(component.find_operation(OperationCode::DRIVER, "last"), nullptr);
}

}  // namespace blender::deg::tests
