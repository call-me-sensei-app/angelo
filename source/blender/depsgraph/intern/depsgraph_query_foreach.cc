/* SPDX-FileCopyrightText: 2017 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup depsgraph
 *
 * Implementation of Querying and Filtering API's
 */

#include <deque>

#include "BLI_threads.h"

#include "BKE_main.hh"

#include "DNA_layer_types.h"
#include "DNA_scene_types.h"

#include "DEG_depsgraph.hh"
#include "DEG_depsgraph_query.hh"

#include "intern/depsgraph.hh"
#include "intern/depsgraph_registry.hh"
#include "intern/depsgraph_relation.hh"
#include "intern/eval/deg_eval_copy_on_write.h"
#include "intern/node/deg_node.hh"
#include "intern/node/deg_node_component.hh"
#include "intern/node/deg_node_id.hh"
#include "intern/node/deg_node_operation.hh"

namespace blender {

/* ************************ DEG TRAVERSAL ********************* */

namespace deg {
namespace {

using TraversalQueue = std::deque<OperationNode *>;

using DEGForeachOperation = void (*)(OperationNode *, void *);

bool deg_foreach_needs_visit(const OperationNode *op_node, const int flags)
{
  if (flags & DEG_FOREACH_COMPONENT_IGNORE_TRANSFORM_SOLVERS) {
    if (op_node->opcode == OperationCode::RIGIDBODY_SIM) {
      return false;
    }
  }
  return true;
}

void deg_foreach_dependent_operation(const Depsgraph * /*graph*/,
                                     const IDNode *target_id_node,
                                     eDepsObjectComponentType source_component_type,
                                     int flags,
                                     DEGForeachOperation callback,
                                     void *user_data)
{
  if (target_id_node == nullptr) {
    /* TODO(sergey): Shall we inform or assert here about attempt to start
     * iterating over non-existing ID? */
    return;
  }
  /* Start with scheduling all operations from ID node. */
  TraversalQueue queue;
  Set<OperationNode *> scheduled;
  for (ComponentNode *comp_node : target_id_node->components.values()) {
    if (comp_node->type == NodeType::VISIBILITY) {
      /* Visibility component is only used internally. It is not to be reporting dependencies to
       * the outer world. */
      continue;
    }

    if (source_component_type != DEG_OB_COMP_ANY &&
        nodeTypeToObjectComponent(comp_node->type) != source_component_type)
    {
      continue;
    }
    for (OperationNode *op_node : comp_node->operations) {
      if (!deg_foreach_needs_visit(op_node, flags)) {
        continue;
      }
      queue.push_back(op_node);
      scheduled.add(op_node);
    }
  }
  /* Process the queue. */
  while (!queue.empty()) {
    /* get next operation node to process. */
    OperationNode *op_node = queue.front();
    queue.pop_front();
    for (;;) {
      callback(op_node, user_data);
      /* Schedule outgoing operation nodes. */
      if (op_node->outlinks.size() == 1) {
        OperationNode *to_node = static_cast<OperationNode *>(op_node->outlinks[0]->to);
        if (!scheduled.contains(to_node) && deg_foreach_needs_visit(to_node, flags)) {
          scheduled.add_new(to_node);
          op_node = to_node;
        }
        else {
          break;
        }
      }
      else {
        for (Relation *rel : op_node->outlinks) {
          OperationNode *to_node = static_cast<OperationNode *>(rel->to);
          if (!scheduled.contains(to_node) && deg_foreach_needs_visit(to_node, flags)) {
            queue.push_front(to_node);
            scheduled.add_new(to_node);
          }
        }
        break;
      }
    }
  }
}

struct ForeachIDComponentData {
  DEGForeachIDComponentCallback callback;
  IDNode *target_id_node;
  Set<ComponentNode *> visited;
};

void deg_foreach_dependent_component_callback(OperationNode *op_node, void *user_data_v)
{
  ForeachIDComponentData *user_data = reinterpret_cast<ForeachIDComponentData *>(user_data_v);
  ComponentNode *comp_node = op_node->owner;
  IDNode *id_node = comp_node->owner;
  if (id_node != user_data->target_id_node && !user_data->visited.contains(comp_node)) {
    user_data->callback(id_node->id_orig, nodeTypeToObjectComponent(comp_node->type));
    user_data->visited.add_new(comp_node);
  }
}

void deg_foreach_dependent_ID_component(const Depsgraph *graph,
                                        const ID *id,
                                        eDepsObjectComponentType source_component_type,
                                        int flags,
                                        DEGForeachIDComponentCallback callback)
{
  ForeachIDComponentData data;
  data.callback = callback;
  data.target_id_node = graph->find_id_node(id);
  deg_foreach_dependent_operation(graph,
                                  data.target_id_node,
                                  source_component_type,
                                  flags,
                                  deg_foreach_dependent_component_callback,
                                  &data);
}

struct ForeachIDData {
  DEGForeachIDCallback callback;
  IDNode *target_id_node;
  Set<IDNode *> visited;
};

void deg_foreach_dependent_ID_callback(OperationNode *op_node, void *user_data_v)
{
  ForeachIDData *user_data = reinterpret_cast<ForeachIDData *>(user_data_v);
  ComponentNode *comp_node = op_node->owner;
  IDNode *id_node = comp_node->owner;
  if (id_node != user_data->target_id_node && !user_data->visited.contains(id_node)) {
    user_data->callback(id_node->id_orig);
    user_data->visited.add_new(id_node);
  }
}

void deg_foreach_dependent_ID(const Depsgraph *graph, const ID *id, DEGForeachIDCallback callback)
{
  ForeachIDData data;
  data.callback = callback;
  data.target_id_node = graph->find_id_node(id);
  deg_foreach_dependent_operation(
      graph, data.target_id_node, DEG_OB_COMP_ANY, 0, deg_foreach_dependent_ID_callback, &data);
}

void deg_foreach_ancestor_ID(const Depsgraph *graph, const ID *id, DEGForeachIDCallback callback)
{
  /* Start with getting ID node from the graph. */
  IDNode *target_id_node = graph->find_id_node(id);
  if (target_id_node == nullptr) {
    /* TODO(sergey): Shall we inform or assert here about attempt to start
     * iterating over non-existing ID? */
    return;
  }
  /* Start with scheduling all operations from ID node. */
  TraversalQueue queue;
  Set<OperationNode *> scheduled;
  for (ComponentNode *comp_node : target_id_node->components.values()) {
    for (OperationNode *op_node : comp_node->operations) {
      queue.push_back(op_node);
      scheduled.add(op_node);
    }
  }
  Set<IDNode *> visited;
  visited.add_new(target_id_node);
  /* Process the queue. */
  while (!queue.empty()) {
    /* get next operation node to process. */
    OperationNode *op_node = queue.front();
    queue.pop_front();
    for (;;) {
      /* Check whether we need to inform callee about corresponding ID node. */
      ComponentNode *comp_node = op_node->owner;
      IDNode *id_node = comp_node->owner;
      if (!visited.contains(id_node)) {
        /* TODO(sergey): Is it orig or evaluated? */
        callback(id_node->id_orig);
        visited.add_new(id_node);
      }
      /* Schedule incoming operation nodes. */
      if (op_node->inlinks.size() == 1) {
        Node *from = op_node->inlinks[0]->from;
        if (from->get_class() == NodeClass::OPERATION) {
          OperationNode *from_node = static_cast<OperationNode *>(from);
          if (scheduled.add(from_node)) {
            op_node = from_node;
          }
          else {
            break;
          }
        }
      }
      else {
        for (Relation *rel : op_node->inlinks) {
          Node *from = rel->from;
          if (from->get_class() == NodeClass::OPERATION) {
            OperationNode *from_node = static_cast<OperationNode *>(from);
            if (scheduled.add(from_node)) {
              queue.push_front(from_node);
            }
          }
        }
        break;
      }
    }
  }
}

void deg_foreach_id(const Depsgraph *depsgraph, DEGForeachIDCallback callback)
{
  for (const IDNode *id_node : depsgraph->id_nodes) {
    callback(id_node->id_orig);
  }
}

}  // namespace
}  // namespace deg

void DEG_foreach_dependent_ID(const Depsgraph *depsgraph,
                              const ID *id,
                              DEGForeachIDCallback callback)
{
  deg::deg_foreach_dependent_ID(reinterpret_cast<const deg::Depsgraph *>(depsgraph), id, callback);
}

void DEG_foreach_dependent_ID_component(const Depsgraph *depsgraph,
                                        const ID *id,
                                        eDepsObjectComponentType source_component_type,
                                        int flags,
                                        DEGForeachIDComponentCallback callback)
{
  deg::deg_foreach_dependent_ID_component(reinterpret_cast<const deg::Depsgraph *>(depsgraph),
                                          id,
                                          source_component_type,
                                          flags,
                                          callback);
}

void DEG_foreach_ancestor_ID(const Depsgraph *depsgraph,
                             const ID *id,
                             DEGForeachIDCallback callback)
{
  deg::deg_foreach_ancestor_ID(reinterpret_cast<const deg::Depsgraph *>(depsgraph), id, callback);
}

void DEG_foreach_ID(const Depsgraph *depsgraph, DEGForeachIDCallback callback)
{
  deg::deg_foreach_id(reinterpret_cast<const deg::Depsgraph *>(depsgraph), callback);
}

bool DEG_foreach_registered_graph(Main *bmain,
                                  FunctionRef<bool(const DEGRegisteredGraph &)> callback)
{
  if (bmain == nullptr || !BLI_thread_is_main()) {
    return false;
  }
  for (const deg::Depsgraph *graph : deg::get_all_registered_graphs(bmain)) {
    DEGRegisteredGraph info{};
    info.depsgraph = reinterpret_cast<const Depsgraph *>(graph);
    info.source_main = graph->bmain;
    if (!graph->is_evaluating && graph->scene != nullptr) {
      /* The input address is only a hash key. Another Scene node in this graph cannot establish
       * the primary Scene's identity, and a live address match alone cannot exclude reuse. */
      const ID *scene_id = reinterpret_cast<const ID *>(graph->scene);
      const deg::IDNode *scene_node = graph->find_id_node(scene_id);
      if (scene_node != nullptr && scene_node->id_type == ID_SCE &&
          scene_node->id_orig == scene_id && scene_node->id_cow != nullptr &&
          scene_node->id_cow != scene_id &&
          scene_node->id_cow == reinterpret_cast<const ID *>(graph->scene_cow) &&
          deg::deg_eval_copy_is_expanded(scene_node->id_cow))
      {
        info.scene_original_session_uid = scene_node->id_orig_session_uid;
      }
    }
    /* A retained graph can still hold original pointers from before an Undo read. Only follow
     * live list members, even when reporting an unsupported or stale owner to the caller. */
    for (const Scene &scene : bmain->scenes) {
      if (&scene != graph->scene) {
        continue;
      }
      info.scene = &scene;
      for (const ViewLayer &view_layer : scene.view_layers) {
        if (&view_layer == graph->view_layer) {
          info.view_layer = &view_layer;
          break;
        }
      }
      break;
    }
    info.mode = graph->mode;
    info.frame = graph->frame;
    info.ctime = graph->ctime;
    info.is_evaluating = graph->is_evaluating;
    info.is_active = graph->is_active;
    info.use_visibility_optimization = graph->use_visibility_optimization;
    info.is_render_pipeline = graph->is_render_pipeline_depsgraph;
    info.use_editors_update = graph->use_editors_update;
    info.need_update_relations = graph->need_update_relations;
    info.need_update_nodes_visibility = graph->need_update_nodes_visibility;
    info.has_pending_updates = !graph->entry_tags.is_empty();
    info.sync_writeback = graph->sync_writeback;
    info.debug_flags = graph->debug.flags;
    info.update_count = graph->update_count;
    if (!callback(info)) {
      return false;
    }
  }
  return true;
}

bool DEG_foreach_evaluated_ID(const Depsgraph *depsgraph,
                              FunctionRef<bool(const DEGEvaluatedID &)> callback)
{
  if (depsgraph == nullptr || !BLI_thread_is_main()) {
    return false;
  }
  const deg::Depsgraph &graph = *reinterpret_cast<const deg::Depsgraph *>(depsgraph);
  if (graph.is_evaluating) {
    return false;
  }
  for (const deg::IDNode *node : graph.id_nodes) {
    /* Non-CoW IDs can already be freed originals. Decide from cached type before reading id_cow.
     * Pointer equality also excludes any borrowed original from the owned-copy inventory. */
    if (!deg::deg_eval_copy_is_needed(node->id_type) || node->id_cow == nullptr ||
        node->id_cow == node->id_orig || !deg::deg_eval_copy_is_expanded(node->id_cow))
    {
      continue;
    }
    if (!callback({node->id_cow, node->id_orig_session_uid, node->id_type})) {
      return false;
    }
  }
  return true;
}

}  // namespace blender
