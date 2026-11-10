/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/* Angelo: the fixture shared by the upstream query's GTests (DEG_angelo_upstream.hh): a rig, its IK
 * target, a custom-shape widget and a skinned mesh, on graphs Blender builds from them. */

#pragma once

#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <mutex>
#include <thread>

#include "ANIM_action.hh"
#include "ANIM_fcurve.hh"

#include "BKE_action.hh"
#include "BKE_anim_data.hh"
#include "BKE_armature.hh"
#include "BKE_cachefile.hh"
#include "BKE_collection.hh"
#include "BKE_constraint.h"
#include "BKE_deform.hh"
#include "BKE_fcurve.hh"
#include "BKE_fcurve_driver.h"
#include "BKE_gtest_base.hh"
#include "BKE_layer.hh"
#include "BKE_lib_id.hh"
#include "BKE_main.hh"
#include "BKE_mesh.hh"
#include "BKE_modifier.hh"
#include "BKE_object.hh"
#include "BKE_scene.hh"
#include "BKE_wm_runtime.hh"

#include "BLI_listbase.h"
#include "BLI_map.hh"
#include "BLI_set.hh"
#include "BLI_string.h"

#include "DNA_action_types.h"
#include "DNA_anim_types.h"
#include "DNA_armature_types.h"
#include "DNA_cachefile_types.h"
#include "DNA_collection_types.h"
#include "DNA_constraint_types.h"
#include "DNA_layer_types.h"
#include "DNA_mesh_types.h"
#include "DNA_modifier_types.h"
#include "DNA_object_types.h"
#include "DNA_scene_types.h"
#include "DNA_windowmanager_types.h"

#include "DEG_angelo_upstream.hh"
#include "DEG_depsgraph_build.hh"

#include "intern/depsgraph.hh"
#include "intern/depsgraph_relation.hh"
#include "intern/eval/deg_eval_copy_on_write.h"
#include "intern/node/deg_node.hh"
#include "intern/node/deg_node_component.hh"
#include "intern/node/deg_node_id.hh"
#include "intern/node/deg_node_operation.hh"

#include "testing/testing.h"

namespace blender::deg::tests {

struct Upstream {
  DEGUpstreamStatus status;
  Map<uint, Set<int>> types_by_uid;
  Map<uint, int> id_type_by_uid;
  /** Node types of records that are not IDs (type code zero). */
  Set<int> sources;

  bool has(const ID *id) const
  {
    if (id == nullptr) {
      return false;
    }
    return types_by_uid.contains(id->session_uid);
  }
  bool has(const ID *id, const NodeType type) const
  {
    return has(id) && types_by_uid.lookup(id->session_uid).contains(int(type));
  }
  bool depends_on_time() const
  {
    return has_source(NodeType::TIMESOURCE);
  }
  bool has_source(const NodeType type) const
  {
    return sources.contains(int(type));
  }
  bool is_empty() const
  {
    return types_by_uid.is_empty() && sources.is_empty();
  }
};

class DegAngeloUpstreamTest : public bke::BlenderGTestBase {
 public:
  Main *bmain = nullptr;
  Scene *scene = nullptr;
  ViewLayer *view_layer = nullptr;
  Object *body = nullptr;
  Object *rig = nullptr;
  Object *target = nullptr;
  Object *widget = nullptr;
  ArmatureModifierData *body_armature = nullptr;

  void SetUp() override
  {
    bmain = BKE_main_new();
    scene = BKE_id_new<Scene>(bmain, "Scene");
    view_layer = static_cast<ViewLayer *>(scene->view_layers.first);
    /* A two-bone rig whose tip has an IK constraint on a mesh object and a custom-shape widget,
     * and a mesh deformed by it through an Armature modifier. */
    bArmature *armature = BKE_armature_add(bmain, "Armature");
    Bone *root_bone = MEM_new<Bone>("Bone");
    STRNCPY(root_bone->name, "Root");
    root_bone->tail[2] = 1.0f;
    BLI_addtail(&armature->bonebase, root_bone);
    Bone *tip_bone = MEM_new<Bone>("Bone");
    STRNCPY(tip_bone->name, "Tip");
    tip_bone->head[2] = 1.0f;
    tip_bone->tail[2] = 2.0f;
    tip_bone->parent = root_bone;
    BLI_addtail(&root_bone->childbase, tip_bone);
    rig = add_object(OB_ARMATURE, "Rig", &armature->id);
    BKE_pose_ensure(bmain, rig, armature, false);
    target = add_object(OB_MESH, "Target", &BKE_id_new<Mesh>(bmain, "Target mesh")->id);
    widget = add_object(OB_EMPTY, "Widget", nullptr);
    bPoseChannel *tip = BKE_pose_channel_find_name(rig->pose, "Tip");
    tip->custom = widget;
    bConstraint *ik = BKE_constraint_add_for_pose(rig, tip, "IK", CONSTRAINT_TYPE_KINEMATIC);
    static_cast<bKinematicConstraint *>(ik->data)->tar = target;
    static_cast<bKinematicConstraint *>(ik->data)->rootbone = 2;
    body = add_object(OB_MESH, "Body", &BKE_id_new<Mesh>(bmain, "Body mesh")->id);
    body_armature = add_modifier(body, eModifierType_Armature);
    body_armature->object = rig;
    /* A default vertex-group-only modifier on a mesh without matching groups reads no pose.
     * Use envelope deformation so this fixture actually depends on the complete rig pose. */
    body_armature->deformflag = ARM_DEF_ENVELOPE;
  }

  void TearDown() override
  {
    BKE_main_free(bmain);
  }

  Object *add_object(const ObjectType type, const char *name, ID *data)
  {
    Object *object = BKE_object_add_only_object(bmain, type, name);
    object->data = data;
    BKE_collection_object_add(bmain, scene->master_collection, object);
    return object;
  }

  template<typename T = ArmatureModifierData> T *add_modifier(Object *object, const int type)
  {
    ModifierData *modifier = BKE_modifier_new(type);
    BLI_addtail(&object->modifiers, modifier);
    return reinterpret_cast<T *>(modifier);
  }

  /** The view layer's registered graph with built relations, never evaluated. */
  Depsgraph &build_graph()
  {
    BKE_view_layer_synced_ensure(*bmain, scene, view_layer);
    blender::Depsgraph *graph = BKE_scene_ensure_depsgraph(bmain, scene, view_layer);
    DEG_graph_relations_update(graph);
    Depsgraph &deg_graph = *reinterpret_cast<Depsgraph *>(graph);
    expect_visibility_relations_stay_in_visibility(deg_graph);
    return deg_graph;
  }

  /* What makes skipping VISIBILITY sound, on every graph these tests build: a relation leaving a
   * visibility component only ever enters another one, so no content flows through it. */
  static void expect_visibility_relations_stay_in_visibility(const Depsgraph &graph)
  {
    for (const OperationNode *op_node : graph.operations) {
      for (const Relation *rel : op_node->inlinks) {
        if (rel->from->get_class() != NodeClass::OPERATION) {
          continue;
        }
        const auto *from = static_cast<const OperationNode *>(rel->from);
        if (from->owner->type == NodeType::VISIBILITY) {
          EXPECT_EQ(op_node->owner->type, NodeType::VISIBILITY) << rel->name;
        }
      }
    }
  }

  /** A driver on the rig's root bone location with the given expression and no variables. */
  ChannelDriver *add_rig_driver(const char *expression)
  {
    AnimData *adt = BKE_animdata_ensure_id(&rig->id);
    FCurve *fcu = BKE_fcurve_create();
    fcu->rna_path = BLI_strdup("pose.bones[\"Root\"].location");
    fcu->driver = MEM_new<ChannelDriver>("ChannelDriver");
    fcu->driver->type = DRIVER_TYPE_PYTHON;
    STRNCPY(fcu->driver->expression, expression);
    BLI_addtail(&adt->drivers, fcu);
    return fcu->driver;
  }

  /** A single-property variable `var` reading `path` on `id`. */
  static void add_variable(ChannelDriver *driver, ID *id, const char *path)
  {
    DriverVar *dvar = driver_add_new_variable(driver);
    driver_change_variable_type(dvar, DVAR_TYPE_SINGLE_PROP);
    STRNCPY(dvar->name, "var");
    dvar->targets[0].idtype = GS(id->name);
    dvar->targets[0].id = id;
    dvar->targets[0].rna_path = BLI_strdup(path);
  }

  Upstream up(const ID *id) const;
};


/* Every walk runs under a watchdog: a traversal that never ends aborts the test binary. */
template<typename Fn> inline void within_watchdog(const Fn &fn)
{
  std::mutex mutex;
  std::condition_variable finished;
  bool done = false;
  std::thread watchdog([&]() {
    std::unique_lock lock(mutex);
    if (!finished.wait_for(lock, std::chrono::seconds(30), [&]() { return done; })) {
      fprintf(stderr, "The upstream walk did not finish within 30 seconds\n");
      std::abort();
    }
  });
  fn();
  {
    std::lock_guard lock(mutex);
    done = true;
  }
  finished.notify_one();
  watchdog.join();
}

inline Upstream upstream(const Main *bmain,
                         const Scene *scene,
                         const ViewLayer *view_layer,
                         const ID *id)
{
  Upstream result{};
  within_watchdog([&]() {
    result.status = DEG_angelo_foreach_upstream(
        bmain, scene, view_layer, id, [&](const DEGUpstreamRecord &record) {
          if (record.id_type == 0) {
            EXPECT_EQ(record.session_uid, 0u);
            result.sources.add(record.node_type);
            return;
          }
          EXPECT_NE(record.session_uid, 0u);
          result.types_by_uid.lookup_or_add_default(record.session_uid).add(record.node_type);
          result.id_type_by_uid.add_overwrite(record.session_uid, record.id_type);
        });
  });
  return result;
}

inline Upstream DegAngeloUpstreamTest::up(const ID *id) const
{
  return upstream(bmain, scene, view_layer, id);
}

}  // namespace blender::deg::tests
