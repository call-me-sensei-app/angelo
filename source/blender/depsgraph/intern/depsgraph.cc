/* SPDX-FileCopyrightText: 2013 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup depsgraph
 *
 * Core routines for how the Depsgraph works.
 */

#include "intern/depsgraph.hh" /* own include */

#include <cstring>
#include <memory>
#include <utility>
#include <type_traits>

#include "BLI_utildefines.h"
#include "BLI_threads.h"

#include "BKE_global.hh"
#include "BKE_idtype.hh"
#include "BKE_scene.hh"
#include "BKE_undo_system.hh"
#include "BKE_main.hh"
#include "DNA_object_types.h"
#include "WM_api.hh"
#include "WM_types.hh"

#include "DEG_depsgraph.hh"
#include "DEG_depsgraph_debug.hh"
#include "DEG_depsgraph_query.hh"
#include "DEG_depsgraph_build.hh"
#include "intern/eval/deg_eval_visibility.h"

#include "intern/depsgraph_physics.hh"
#include "intern/depsgraph_registry.hh"
#include "intern/depsgraph_relation.hh"

#include "intern/eval/deg_eval_copy_on_write.h"

#include "intern/node/deg_node.hh"
#include "intern/node/deg_node_component.hh"
#include "intern/node/deg_node_factory.hh"
#include "intern/node/deg_node_id.hh"
#include "intern/node/deg_node_operation.hh"
#include "intern/node/deg_node_time.hh"

namespace blender {

namespace deg {

Depsgraph::Depsgraph(Main *bmain, Scene *scene, ViewLayer *view_layer, eEvaluationMode mode)
    : time_source(nullptr),
      has_animated_visibility(false),
      need_update_relations(true),
      need_update_nodes_visibility(true),
      need_tag_id_on_graph_visibility_update(true),
      need_tag_id_on_graph_visibility_time_update(false),
      bmain(bmain),
      scene(scene),
      view_layer(view_layer),
      mode(mode),
      frame(BKE_scene_frame_get(scene)),
      ctime(BKE_scene_ctime_get(scene)),
      scene_cow(nullptr),
      is_active(false),
      use_visibility_optimization(true),
      is_evaluating(false),
      is_render_pipeline_depsgraph(false),
      use_editors_update(false),
      physics_relations_effector(nullptr),
      update_count(0),
      sync_writeback(DEG_EVALUATE_SYNC_WRITEBACK_NO)
{
  BLI_spin_init(&lock);
  memset(id_type_updated, 0, sizeof(id_type_updated));
  memset(id_type_updated_backup, 0, sizeof(id_type_updated_backup));
  memset(id_type_exist, 0, sizeof(id_type_exist));
  memset(physics_relations_collision, 0, sizeof(physics_relations_collision));

  add_time_source();
}

Depsgraph::~Depsgraph()
{
  clear_id_nodes();
  delete time_source;
  BLI_spin_end(&lock);
}

/* Node Management ---------------------------- */

TimeSourceNode *Depsgraph::add_time_source()
{
  if (time_source == nullptr) {
    DepsNodeFactory *factory = type_get_factory(NodeType::TIMESOURCE);
    time_source = static_cast<TimeSourceNode *>(factory->create_node(nullptr, "", "Time Source"));
  }
  return time_source;
}

TimeSourceNode *Depsgraph::find_time_source() const
{
  return time_source;
}

void Depsgraph::tag_time_source()
{
  time_source->tag_update(this, DEG_UPDATE_SOURCE_TIME);
}

IDNode *Depsgraph::find_id_node(const ID *id) const
{
  return id_hash.lookup_default(id, nullptr);
}

IDNode *Depsgraph::add_id_node(ID *id, ID *id_cow_hint)
{
  BLI_assert((id->tag & ID_TAG_COPIED_ON_EVAL) == 0);
  IDNode *id_node = find_id_node(id);
  if (!id_node) {
    DepsNodeFactory *factory = type_get_factory(NodeType::ID_REF);
    id_node = static_cast<IDNode *>(factory->create_node(id, "", id->name));
    id_node->init_copy_on_write(id_cow_hint);
    /* Register node in ID hash.
     *
     * NOTE: We address ID nodes by the original ID pointer they are
     * referencing to. */
    id_hash.add_new(id, id_node);
    id_nodes.append(id_node);

    id_type_exist[BKE_idtype_idcode_to_index(GS(id->name))] = 1;
  }
  return id_node;
}

template<typename FilterFunc>
static void clear_id_nodes_conditional(Depsgraph::IDDepsNodes *id_nodes, const FilterFunc &filter)
{
  for (IDNode *id_node : *id_nodes) {
    if (id_node->id_cow == nullptr) {
      /* This means builder "stole" ownership of the evaluated
       * datablock for its own dirty needs. */
      continue;
    }
    if (id_node->id_cow == id_node->id_orig) {
      /* Evaluated copy is not needed for this ID type.
       *
       * NOTE: Is important to not de-reference the original datablock here because it might be
       * freed already (happens during main database free when some IDs are freed prior to a
       * scene). */
      continue;
    }
    if (!deg_eval_copy_is_expanded(id_node->id_cow)) {
      continue;
    }
    const ID_Type id_type = GS(id_node->id_cow->name);
    if (filter(id_type)) {
      id_node->destroy();
    }
  }
}

void Depsgraph::clear_id_nodes()
{
  /* Free memory used by ID nodes. */

  /* Stupid workaround to ensure we free IDs in a proper order. */
  clear_id_nodes_conditional(&id_nodes, [](ID_Type id_type) { return id_type == ID_SCE; });
  clear_id_nodes_conditional(&id_nodes, [](ID_Type id_type) { return id_type != ID_PA; });

  for (IDNode *id_node : id_nodes) {
    delete id_node;
  }
  /* Clear containers. */
  id_hash.clear();
  id_nodes.clear();
  /* Clear physics relation caches. */
  clear_physics_relations(this);

  light_linking_cache.clear();
}

Relation *Depsgraph::add_new_relation(Node *from, Node *to, const char *description, int flags)
{
  Relation *rel = nullptr;
  if (flags & RELATION_CHECK_BEFORE_ADD) {
    rel = check_nodes_connected(from, to, description);
  }
  if (rel != nullptr) {
    rel->flag |= flags;
    return rel;
  }

#ifndef NDEBUG
  if (from->type == NodeType::OPERATION && to->type == NodeType::OPERATION) {
    OperationNode *operation_from = static_cast<OperationNode *>(from);
    OperationNode *operation_to = static_cast<OperationNode *>(to);
    BLI_assert(operation_to->owner->type != NodeType::COPY_ON_EVAL ||
               operation_from->owner->type == NodeType::COPY_ON_EVAL);
  }
#endif

  /* Create new relation, and add it to the graph. The type must be trivially destructible for
   * `.release()` to be okay. If it weren't, we could store the relations with #destruct_ptr on
   * either the `inlinks` or `outlinks`. But since so many #Relation structs are allocated, it's
   * probably better for it be a simple type anyway. */
  static_assert(std::is_trivially_destructible_v<Relation>);
  rel = this->build_allocator.construct<Relation>(from, to, description).release();
  from->outlinks.append(rel);
  to->inlinks.append(rel);
  rel->flag |= flags;
  return rel;
}

Relation *Depsgraph::check_nodes_connected(const Node *from,
                                           const Node *to,
                                           const char *description)
{
  for (Relation *rel : from->outlinks) {
    BLI_assert(rel->from == from);
    if (rel->to != to) {
      continue;
    }
    if (description != nullptr && !STREQ(rel->name, description)) {
      continue;
    }
    return rel;
  }
  return nullptr;
}

/* Low level tagging -------------------------------------- */

void Depsgraph::add_entry_tag(OperationNode *node)
{
  /* Sanity check. */
  if (node == nullptr) {
    return;
  }
  /* Add to graph-level set of directly modified nodes to start searching
   * from.
   * NOTE: this is necessary since we have several thousand nodes to play
   * with. */
  entry_tags.add(node);
}

void Depsgraph::clear_all_nodes()
{
  clear_id_nodes();
  delete time_source;
  time_source = nullptr;
  /* Memory used by the build allocator is now unused. Rebuild it from scratch. */
  std::destroy_at(&this->build_allocator);
  new (&this->build_allocator) LinearAllocator<>();
}

ID *Depsgraph::get_cow_id(const ID *id_orig) const
{
  IDNode *id_node = find_id_node(id_orig);
  if (id_node == nullptr) {
    /* This function is used from places where we expect ID to be either
     * already a copy-on-evaluation version or have a corresponding copy-on-evaluation
     * version.
     *
     * We try to enforce that in debug builds, for release we play a bit
     * safer game here. */
    if ((id_orig->tag & ID_TAG_COPIED_ON_EVAL) == 0) {
      /* TODO(sergey): This is nice sanity check to have, but it fails
       * in following situations:
       *
       * - Material has link to texture, which is not needed by new
       *   shading system and hence can be ignored at construction.
       * - Object or mesh has material at a slot which is not used (for
       *   example, object has material slot by materials are set to
       *   object data). */
      // BLI_assert_msg(0, "Request for non-existing copy-on-evaluation ID");
    }
    return const_cast<ID *>(id_orig);
  }
  return id_node->id_cow;
}

}  // namespace deg

/* **************** */
/* Public Graph API */

Depsgraph *DEG_graph_new(Main *bmain, Scene *scene, ViewLayer *view_layer, eEvaluationMode mode)
{
  deg::Depsgraph *deg_depsgraph = new deg::Depsgraph(bmain, scene, view_layer, mode);
  deg::register_graph(deg_depsgraph);
  return reinterpret_cast<Depsgraph *>(deg_depsgraph);
}

void DEG_graph_replace_owners(Depsgraph *depsgraph,
                              Main *bmain,
                              Scene *scene,
                              ViewLayer *view_layer)
{
  deg::Depsgraph *deg_graph = reinterpret_cast<deg::Depsgraph *>(depsgraph);

  const bool do_update_register = deg_graph->bmain != bmain;
  if (do_update_register && deg_graph->bmain != nullptr) {
    deg::unregister_graph(deg_graph);
  }

  deg_graph->bmain = bmain;
  deg_graph->scene = scene;
  deg_graph->view_layer = view_layer;

  if (do_update_register) {
    deg::register_graph(deg_graph);
  }
}

void DEG_graph_free(Depsgraph *graph)
{
  if (graph == nullptr) {
    return;
  }
  using deg::Depsgraph;
  deg::Depsgraph *deg_depsgraph = reinterpret_cast<deg::Depsgraph *>(graph);
  deg::unregister_graph(deg_depsgraph);
  delete deg_depsgraph;
}

bool DEG_is_evaluating(const Depsgraph *depsgraph)
{
  const deg::Depsgraph *deg_graph = reinterpret_cast<const deg::Depsgraph *>(depsgraph);
  return deg_graph->is_evaluating;
}

bool DEG_is_active(const Depsgraph *depsgraph)
{
  if (depsgraph == nullptr) {
    /* Happens for such cases as work object in what_does_obaction(),
     * and sine render pipeline parts. Shouldn't really be accepting
     * nullptr depsgraph, but is quite hard to get proper one in those
     * cases. */
    return false;
  }
  const deg::Depsgraph *deg_graph = reinterpret_cast<const deg::Depsgraph *>(depsgraph);
  return deg_graph->is_active;
}

void DEG_make_active(Depsgraph *depsgraph)
{
  deg::Depsgraph *deg_graph = reinterpret_cast<deg::Depsgraph *>(depsgraph);
  deg_graph->is_active = true;
  /* TODO(sergey): Copy data from evaluated state to original. */
}

void DEG_make_inactive(Depsgraph *depsgraph)
{
  deg::Depsgraph *deg_graph = reinterpret_cast<deg::Depsgraph *>(depsgraph);
  deg_graph->is_active = false;
}

void DEG_disable_visibility_optimization(Depsgraph *depsgraph)
{
  deg::Depsgraph *deg_graph = reinterpret_cast<deg::Depsgraph *>(depsgraph);
  deg_graph->use_visibility_optimization = false;
}

/* C's call-local observer distinguishes an observed main-thread conflict from fatal
 * worker/unwitnessed access. False prohibits any Main/graph traversal, not callback bodies. */
bool BKE_checkpoint_callback_observation_safe(const Main *main);

struct DEGCheckpointRuntimeScope {
  Main *main;
  void *operation_user = nullptr;
  bool (*operation_hook)(void *, Main *) = nullptr;
};
static DEGCheckpointRuntimeScope *checkpoint_runtime_scope = nullptr;

bool DEG_checkpoint_runtime_is_active(Main *main)
{
  return BLI_thread_is_main() && checkpoint_runtime_scope &&
         checkpoint_runtime_scope->main == main && main == G_MAIN &&
         BKE_undosys_restore_phase() == UndoRestorePhase::Checkpoint &&
         BKE_undosys_restore_protects_main(main);
}

bool DEG_checkpoint_runtime_observation_allowed(Main *main)
{
  if (!DEG_checkpoint_runtime_is_active(main) || !BKE_checkpoint_callback_observation_safe(main)) { return false; }
  for (const wmWindowManager &wm : main->wm) {
    if (WM_jobs_has_pending_or_running(&wm)) { return false; }
  }
  return DEG_foreach_registered_graph(main, [&](const DEGRegisteredGraph &row) {
    const auto *graph = reinterpret_cast<const deg::Depsgraph *>(row.depsgraph);
    return !row.is_evaluating || graph->checkpoint_pool_has_no_workers;
  });
}

bool DEG_checkpoint_runtime_set_operation_hook(DEGCheckpointRuntimeScope *scope, void *user,
                                                bool (*hook)(void *, Main *))
{
  if (!scope || checkpoint_runtime_scope != scope || !DEG_checkpoint_runtime_is_active(scope->main) ||
      !DEG_checkpoint_runtime_observation_allowed(scope->main) || scope->operation_hook) { return false; }
  scope->operation_user = user; scope->operation_hook = hook;
  return true;
}

bool DEG_checkpoint_runtime_operation_boundary(Main *main)
{
  if (!DEG_checkpoint_runtime_is_active(main)) { return true; }
  if (!DEG_checkpoint_runtime_observation_allowed(main)) { return false; }
  const auto *scope = checkpoint_runtime_scope;
  return !scope->operation_hook || scope->operation_hook(scope->operation_user, main);
}

DEGCheckpointRuntimeScope *DEG_checkpoint_runtime_begin(Main *main)
{
  if (!main || !BLI_thread_is_main() || checkpoint_runtime_scope || main != G_MAIN ||
      BKE_undosys_restore_phase() != UndoRestorePhase::Checkpoint ||
      !BKE_undosys_restore_protects_main(main)) { return nullptr; }
  for (const wmWindowManager &wm : main->wm) {
    if (WM_jobs_has_pending_or_running(&wm)) { return nullptr; }
  }
  if (!DEG_foreach_registered_graph(main, [](const DEGRegisteredGraph &row) {
        return !row.is_evaluating;
      })) { return nullptr; }
  checkpoint_runtime_scope = new DEGCheckpointRuntimeScope{main};
  return checkpoint_runtime_scope;
}

void DEG_checkpoint_runtime_end(DEGCheckpointRuntimeScope *scope)
{
  if (!scope || !BLI_thread_is_main() || checkpoint_runtime_scope != scope) { return; }
  checkpoint_runtime_scope = nullptr;
  delete scope;
}

bool DEG_checkpoint_foreach_evaluated_ID(const Depsgraph *depsgraph,
                                        FunctionRef<bool(const DEGEvaluatedID &)> callback)
{
  if (!depsgraph || !BLI_thread_is_main()) { return false; }
  const auto &graph = *reinterpret_cast<const deg::Depsgraph *>(depsgraph);
  if (!DEG_checkpoint_runtime_observation_allowed(graph.bmain) ||
      (graph.is_evaluating && !graph.checkpoint_pool_has_no_workers)) { return false; }
  for (const deg::IDNode *node : graph.id_nodes) {
    if (!deg::deg_eval_copy_is_needed(node->id_type) || !node->id_cow ||
        node->id_cow == node->id_orig || !deg::deg_eval_copy_is_expanded(node->id_cow)) { continue; }
    if (!callback({node->id_cow, node->id_orig_session_uid, node->id_type})) { return false; }
  }
  return true;
}

struct DEGCheckpointMaterializeScope {
  Main *main;
  Depsgraph *graph;
  bool visibility;
  uint64_t cookie;
  Vector<std::pair<uint32_t, bool>> built_visibility;
};

DEGCheckpointMaterializeScope *DEG_checkpoint_materialize_begin(Depsgraph *depsgraph,
                                                               const Span<uint32_t> objects)
{
  if (!depsgraph || !BLI_thread_is_main()) { return nullptr; }
  auto *graph = reinterpret_cast<deg::Depsgraph *>(depsgraph);
  if (!DEG_checkpoint_runtime_observation_allowed(graph->bmain) || graph->is_evaluating ||
      !graph->checkpoint_visible_objects.is_empty()) { return nullptr; }
  auto scope = std::make_unique<DEGCheckpointMaterializeScope>();
  scope->main = graph->bmain; scope->graph = depsgraph;
  scope->visibility = graph->use_visibility_optimization;
  static uint64_t cookie = 0;
  scope->cookie = ++cookie;
  graph->checkpoint_materialize_cookie = scope->cookie;
  /* Admit hidden bases only during relation construction. Restore the saved eligibility
   * profile before evaluation, so disabled modifiers are never activated by this scope. */
  graph->use_visibility_optimization = false;
  DEG_graph_tag_relations_update(depsgraph);
  DEG_graph_relations_update(depsgraph);
  graph->use_visibility_optimization = scope->visibility;
  for (const uint32_t uid : objects) {
    graph->checkpoint_visible_objects.add(uid);
    for (Object &object : scope->main->objects) {
      if (object.id.session_uid == uid) {
        DEG_id_tag_update_ex(scope->main, &object.id, ID_RECALC_GEOMETRY);
        break;
      }
    }
  }
  for (deg::IDNode *node : graph->id_nodes) {
    if (node->id_type == ID_OB && graph->checkpoint_visible_objects.contains(node->id_orig_session_uid)) {
      scope->built_visibility.append({node->id_orig_session_uid, node->is_visible_on_build});
      node->is_visible_on_build = true;
    }
  }
  graph->need_update_nodes_visibility = true;
  deg::deg_graph_flush_visibility_flags_if_needed(graph);
  return scope.release();
}

bool DEG_checkpoint_materialize_end(DEGCheckpointMaterializeScope *scope)
{
  if (!scope) { return false; }
  std::unique_ptr<DEGCheckpointMaterializeScope> owned(scope);
  if (!BLI_thread_is_main() || scope->main != G_MAIN ||
      !DEG_checkpoint_runtime_is_active(scope->main)) { return false; }
  bool found = false;
  DEG_foreach_registered_graph(scope->main, [&](const DEGRegisteredGraph &row) {
    found |= row.depsgraph == scope->graph; return true;
  });
  if (!found) { return false; }
  auto *graph = reinterpret_cast<deg::Depsgraph *>(scope->graph);
  if (graph->is_evaluating || graph->checkpoint_materialize_cookie != scope->cookie) { return false; }
  graph->checkpoint_materialize_cookie = 0;
  graph->checkpoint_visible_objects.clear();
  /* The temporary build flag was restored before evaluation. A callback changing the
   * profile now owns that edit; do not erase it on the failure path. */
  const bool profile_unchanged = graph->use_visibility_optimization == scope->visibility;
  for (deg::IDNode *node : graph->id_nodes) {
    for (const auto &row : scope->built_visibility) {
      if (node->id_type == ID_OB && node->id_orig_session_uid == row.first) {
        node->is_visible_on_build = row.second;
        if (node->id_cow && node->id_cow != node->id_orig &&
            deg::deg_eval_copy_is_expanded(node->id_cow)) {
          deg::deg_evaluate_object_node_visibility(scope->graph, node);
        }
      }
    }
  }
  deg::deg_graph_flush_visibility_flags_if_needed(graph);
  return profile_unchanged && !graph->checkpoint_evaluation_aborted;
}

bool DEG_checkpoint_visibility_optimization_get(const Depsgraph *depsgraph, bool &r_enabled)
{
  if (!depsgraph || !BLI_thread_is_main()) { return false; }
  const auto *graph = reinterpret_cast<const deg::Depsgraph *>(depsgraph);
  if (graph->is_evaluating || graph->bmain != G_MAIN ||
      BKE_undosys_restore_phase() != UndoRestorePhase::Checkpoint ||
      !BKE_undosys_restore_protects_main(graph->bmain)) { return false; }
  r_enabled = graph->use_visibility_optimization;
  return true;
}

bool DEG_checkpoint_visibility_optimization_set(Depsgraph *depsgraph, const bool enabled)
{
  if (!depsgraph || !BLI_thread_is_main()) { return false; }
  auto *graph = reinterpret_cast<deg::Depsgraph *>(depsgraph);
  if (graph->is_evaluating || graph->bmain != G_MAIN ||
      BKE_undosys_restore_phase() != UndoRestorePhase::Checkpoint ||
      !BKE_undosys_restore_protects_main(graph->bmain)) { return false; }
  graph->use_visibility_optimization = enabled;
  return true;
}

uint64_t DEG_get_update_count(const Depsgraph *depsgraph)
{
  const deg::Depsgraph *deg_graph = reinterpret_cast<const deg::Depsgraph *>(depsgraph);
  return deg_graph->update_count;
}

}  // namespace blender
