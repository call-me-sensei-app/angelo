/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "bpy_angelo_read_traversal_const.hh"

#include <cstring>
#include <memory>
#include <tuple>
#include <utility>
#include <vector>

#include "BLI_listbase.h"
#include "BLI_utildefines.h"

#include "BKE_action.hh"
#include "BKE_armature.hh"
#include "BKE_compute_contexts.hh"
#include "BKE_global.hh"
#include "BKE_object_types.hh"

#include "DNA_action_types.h"
#include "DNA_armature_types.h"
#include "DNA_layer_types.h"
#include "DNA_modifier_types.h"
#include "DNA_object_types.h"
#include "DNA_scene_types.h"

#include "MOD_nodes.hh"
#include "NOD_eval_log.hh"

#include "RNA_access.hh"
#include "RNA_prototypes.hh"
#include "intern/rna_internal_types.hh"

namespace blender::angelo_read_traversal {

static bool named(const PropertyRNA &property, const char *identifier)
{
  return property.identifier && std::strcmp(property.identifier, identifier) == 0;
}

static bool manual(const PropertyRNA &property, RNAReadCallbackRole role, const char *symbol)
{
  const RNAReadCallbackSource source = RNA_property_read_callback_source(&property, role);
  return source.state == RNAReadCallbackState::MATCHED &&
         source.origin == RNAReadCallbackOrigin::MANUAL && source.source_file &&
         source.source_identifier && std::strcmp(source.source_file, "rna_layer.cc") == 0 &&
         std::strcmp(source.source_identifier, symbol) == 0;
}

static bool manual_in(const PropertyRNA &property,
                      RNAReadCallbackRole role,
                      const char *file,
                      const char *symbol)
{
  const RNAReadCallbackSource source = RNA_property_read_callback_source(&property, role);
  return source.state == RNAReadCallbackState::MATCHED &&
         source.origin == RNAReadCallbackOrigin::MANUAL && source.source_file &&
         source.source_identifier && std::strcmp(source.source_file, file) == 0 &&
         std::strcmp(source.source_identifier, symbol) == 0;
}

static bool generated(const PropertyRNA &property, RNAReadCallbackRole role)
{
  const RNAReadCallbackSource source = RNA_property_read_callback_source(&property, role);
  return source.state == RNAReadCallbackState::MATCHED &&
         (source.origin == RNAReadCallbackOrigin::GENERATED_DNA ||
          (role == RNAReadCallbackRole::COLLECTION_END &&
           source.origin == RNAReadCallbackOrigin::GENERATED_NOOP));
}

static bool absent(const PropertyRNA &property, RNAReadCallbackRole role)
{
  return RNA_property_read_callback_source(&property, role).state == RNAReadCallbackState::ABSENT;
}

static bool iterator_slot(const PropertyRNA &property,
                          RNAReadCallbackRole role,
                          const char *symbol)
{
  return generated(property, role) || manual(property, role, symbol);
}

static bool ordinary_lookup(const PropertyRNA &property, RNAReadCallbackRole role)
{
  const RNAReadCallbackSource source = RNA_property_read_callback_source(&property, role);
  return source.state == RNAReadCallbackState::ABSENT ||
         (source.state == RNAReadCallbackState::MATCHED &&
          source.origin == RNAReadCallbackOrigin::GENERATED_LOOKUP);
}

Family pointer_family(const StructRNA &type, const PropertyRNA &property)
{
  if (property.type != PROP_POINTER || (property.flag & PROP_IDPROPERTY) ||
      !absent(property, RNAReadCallbackRole::POINTER_TYPE))
  {
    return NONE;
  }
  const StructRNA *fixed = static_cast<const PointerPropertyRNA &>(property).pointer_type;
  if (&type == RNA_ViewLayer) {
    if (fixed == RNA_LayerCollection && named(property, "layer_collection") &&
        generated(property, RNAReadCallbackRole::GET))
    {
      return VIEW_LAYER_ROOT;
    }
    if (fixed == RNA_LayerCollection && named(property, "active_layer_collection") &&
        manual(property, RNAReadCallbackRole::GET, "rna_ViewLayer_active_layer_collection_get"))
    {
      return VIEW_LAYER_ACTIVE_COLLECTION;
    }
  }
  if (&type == RNA_LayerObjects && fixed == RNA_Object && named(property, "active") &&
      manual(property, RNAReadCallbackRole::GET, "rna_LayerObjects_active_object_get"))
  {
    return VIEW_LAYER_ACTIVE_OBJECT;
  }
  if (&type == RNA_PoseBone && fixed == RNA_Bone && named(property, "bone") &&
      manual_in(property, RNAReadCallbackRole::GET, "rna_pose.cc", "rna_PoseChannel_bone_get"))
  {
    return POSE_BONE;
  }
  return NONE;
}

Family collection_family(const StructRNA &type, const PropertyRNA &property)
{
  if (property.type != PROP_COLLECTION || (property.flag & PROP_IDPROPERTY)) {
    return NONE;
  }
  const CollectionPropertyRNA &collection = static_cast<const CollectionPropertyRNA &>(property);
  const bool list_next = iterator_slot(
      property, RNAReadCallbackRole::COLLECTION_NEXT, "rna_iterator_listbase_next");
  const bool list_end = iterator_slot(
      property, RNAReadCallbackRole::COLLECTION_END, "rna_iterator_listbase_end");
  const bool ordinary_length = absent(property, RNAReadCallbackRole::COLLECTION_LENGTH) ||
                               generated(property, RNAReadCallbackRole::COLLECTION_LENGTH);
  if (&type == RNA_LayerCollection && collection.item_type == RNA_LayerCollection &&
      named(property, "children") && list_next && list_end && ordinary_length &&
      manual(property, RNAReadCallbackRole::COLLECTION_BEGIN, "rna_LayerCollection_children_begin") &&
      iterator_slot(property, RNAReadCallbackRole::COLLECTION_GET, "rna_iterator_listbase_get") &&
      manual(property, RNAReadCallbackRole::COLLECTION_LOOKUP_INT,
             "rna_LayerCollection_children_lookupint") &&
      manual(property, RNAReadCallbackRole::COLLECTION_LOOKUP_STRING,
             "rna_LayerCollection_children_lookupstring"))
  {
    return LAYER_CHILDREN;
  }
  if (collection.item_type == RNA_Object && list_next && list_end && ordinary_length &&
      manual(property, RNAReadCallbackRole::COLLECTION_GET, "rna_ViewLayer_objects_get") &&
      ordinary_lookup(property, RNAReadCallbackRole::COLLECTION_LOOKUP_INT) &&
      ordinary_lookup(property, RNAReadCallbackRole::COLLECTION_LOOKUP_STRING))
  {
    if (&type == RNA_ViewLayer && named(property, "objects") &&
        generated(property, RNAReadCallbackRole::COLLECTION_BEGIN))
    {
      return VIEW_LAYER_OBJECTS;
    }
    if (&type == RNA_LayerObjects && named(property, "selected") &&
        manual(property, RNAReadCallbackRole::COLLECTION_BEGIN, "rna_LayerObjects_selected_begin"))
    {
      return VIEW_LAYER_SELECTED_OBJECTS;
    }
  }
  if (&type == RNA_NodesModifier && collection.item_type == RNA_NodesModifierWarning &&
      named(property, "node_warnings") &&
      manual_in(property, RNAReadCallbackRole::COLLECTION_BEGIN, "rna_modifier.cc",
                "rna_NodesModifier_node_warnings_iterator_begin") &&
      manual_in(property, RNAReadCallbackRole::COLLECTION_NEXT, "rna_modifier.cc",
                "rna_NodesModifier_node_warnings_iterator_next") &&
      (absent(property, RNAReadCallbackRole::COLLECTION_END) ||
       generated(property, RNAReadCallbackRole::COLLECTION_END)) &&
      manual_in(property, RNAReadCallbackRole::COLLECTION_GET, "rna_modifier.cc",
                "rna_NodesModifier_node_warnings_iterator_get") &&
      manual_in(property, RNAReadCallbackRole::COLLECTION_LENGTH, "rna_modifier.cc",
                "rna_NodesModifier_node_warnings_length") &&
      ordinary_lookup(property, RNAReadCallbackRole::COLLECTION_LOOKUP_INT) &&
      ordinary_lookup(property, RNAReadCallbackRole::COLLECTION_LOOKUP_STRING))
  {
    return MODIFIER_WARNINGS;
  }
  return NONE;
}

bool catalogued(const PropertyRNA &property)
{
  if (property.flag & PROP_IDPROPERTY) {
    return false;
  }
  for (const auto &[file, symbol, role] : {
           std::tuple{"rna_layer.cc", "rna_ViewLayer_active_layer_collection_get", RNAReadCallbackRole::GET},
           std::tuple{"rna_layer.cc", "rna_LayerObjects_active_object_get", RNAReadCallbackRole::GET},
           std::tuple{"rna_layer.cc", "rna_LayerCollection_children_begin", RNAReadCallbackRole::COLLECTION_BEGIN},
           std::tuple{"rna_layer.cc", "rna_LayerCollection_children_lookupint", RNAReadCallbackRole::COLLECTION_LOOKUP_INT},
           std::tuple{"rna_layer.cc", "rna_LayerCollection_children_lookupstring", RNAReadCallbackRole::COLLECTION_LOOKUP_STRING},
           std::tuple{"rna_layer.cc", "rna_ViewLayer_objects_get", RNAReadCallbackRole::COLLECTION_GET},
           std::tuple{"rna_layer.cc", "rna_LayerObjects_selected_begin", RNAReadCallbackRole::COLLECTION_BEGIN},
           std::tuple{"rna_pose.cc", "rna_PoseChannel_bone_get", RNAReadCallbackRole::GET},
           std::tuple{"rna_modifier.cc", "rna_NodesModifier_node_warnings_iterator_begin", RNAReadCallbackRole::COLLECTION_BEGIN},
           std::tuple{"rna_modifier.cc", "rna_NodesModifier_node_warnings_length", RNAReadCallbackRole::COLLECTION_LENGTH}})
  {
    if (manual_in(property, role, file, symbol)) {
      return true;
    }
  }
  return false;
}

bool is_view_layer_family(const Family family)
{
  return ELEM(family,
              VIEW_LAYER_ROOT,
              VIEW_LAYER_ACTIVE_COLLECTION,
              VIEW_LAYER_ACTIVE_OBJECT,
              LAYER_CHILDREN,
              VIEW_LAYER_OBJECTS,
              VIEW_LAYER_SELECTED_OBJECTS);
}

struct Scope::Impl {
  struct WarningState {
    const NodesModifierData *source;
    const Object *object;
    Vector<nodes::eval_log::NodeWarning> warnings;
  };
  std::vector<std::unique_ptr<WarningState>> warnings;
};

struct ScopeAccess {
  using Impl = Scope::Impl;
  static Impl &impl(Scope &scope)
  {
    return *scope.impl_;
  }

  static Impl::WarningState *warning(Scope &scope, const PointerRNA &owner)
  {
    if (!owner.data || !owner.owner_id || GS(owner.owner_id->name) != ID_OB || !G_MAIN) {
      return nullptr;
    }
    const auto *object = reinterpret_cast<const Object *>(owner.owner_id);
    const auto *modifier = static_cast<const NodesModifierData *>(owner.data);
    bool member = false;
    for (const ModifierData &candidate : object->modifiers) {
      member |= &candidate == &modifier->modifier;
    }
    if (!member || modifier->modifier.type != eModifierType_Nodes || !modifier->runtime) {
      return nullptr;
    }
    Impl &state = impl(scope);
    for (const std::unique_ptr<Impl::WarningState> &entry : state.warnings) {
      if (entry->source == modifier && entry->object == object) {
        return entry.get();
      }
    }
    auto entry = std::make_unique<Impl::WarningState>();
    entry->source = modifier;
    entry->object = object;
    if (modifier->runtime->eval_log) {
      bke::DataBlockComputeContext data_block_context{nullptr, object->id};
      bke::ModifierComputeContext modifier_context{&data_block_context, *modifier};
      entry->warnings = modifier->runtime->eval_log->copy_node_warnings_for_read(
          modifier_context.hash(), *modifier);
    }
    state.warnings.push_back(std::move(entry));
    return state.warnings.back().get();
  }
};

Scope::Scope() : impl_(std::make_unique<Impl>()) {}
Scope::~Scope() = default;

static Status pose_bone(const PointerRNA &owner, PointerRNA &result)
{
  if (!owner.owner_id || !owner.data || GS(owner.owner_id->name) != ID_OB) {
    return Status::INVALID_STORAGE;
  }
  const Object &object = *reinterpret_cast<const Object *>(owner.owner_id);
  if (object.type != OB_ARMATURE || !object.data || GS(object.data->name) != ID_AR ||
      !object.pose || !object.runtime)
  {
    return Status::INVALID_STORAGE;
  }
  const bArmature &armature = *reinterpret_cast<const bArmature *>(object.data);
  const auto *channel = static_cast<const bPoseChannel *>(owner.data);
  bool member = false;
  for (const bPoseChannel &candidate : object.pose->chanbase) {
    member |= &candidate == channel;
  }
  if (!member || !armature.runtime) {
    return Status::INVALID_STORAGE;
  }
  const Bone *bone = nullptr;
  const bool array_valid = armature.runtime->is_bones_array_valid();
  const bool rebuild = !array_valid ||
                       object.runtime->pose_bones_generation_count !=
                           armature.runtime->bones_generation_count;
  int64_t index = channel->runtime.bone_index;
  if (rebuild) {
    /* Mirror every assignment in rebuild_pose_bone_indices, including its actual name/hash
     * selector and last assignment precedence, without touching either index or generation. */
    index = BONE_INDEX_UNKNOWN;
    BKE_armature_foreach_bone(armature, [&](const int position, const Bone &candidate) {
      if (BKE_pose_channel_find_name(object.pose, candidate.name) == channel) {
        index = position;
        bone = &candidate;
      }
    });
  }
  if (array_valid && index != BONE_INDEX_UNKNOWN && !armature.bonebase.is_empty()) {
    /* Canonical pose-index rebuild does not replace an already valid armature array. */
    if (index < 0 || index >= armature.runtime->bones.size()) {
      return Status::INVALID_STORAGE;
    }
    const Bone *cached = armature.runtime->bones[index];
    bone = nullptr;
    BKE_armature_foreach_bone(armature, [&](const int /*index*/, const Bone &candidate) {
      if (&candidate == cached) {
        bone = &candidate;
      }
    });
    if (cached && !bone) {
      return Status::INVALID_STORAGE;
    }
  }
  if (bone) {
    PointerRNA parent = owner;
    parent.owner_id = const_cast<ID *>(&armature.id);
    result = PointerRNA(parent.owner_id, RNA_Bone, const_cast<Bone *>(bone), parent);
  }
  return Status::OK;
}

Status pointer(Scope & /*scope*/,
               Family family,
               const PointerRNA &owner,
               const PropertyRNA &property,
               PointerRNA &result)
{
  result = {};
  if (!owner.type || family == NONE || pointer_family(*owner.type, property) != family) {
    return Status::UNSUPPORTED;
  }
  if (family == POSE_BONE) {
    return pose_bone(owner, result);
  }
  /* View-layer families run their built-in getter under the core's synchronization check. */
  return Status::UNSUPPORTED;
}

template<typename Visitor>
static Status visit_entries(Scope &scope,
                            Family family,
                            const PointerRNA &owner,
                            const Visitor &visit)
{
  if (family == MODIFIER_WARNINGS) {
    ScopeAccess::Impl::WarningState *state = ScopeAccess::warning(scope, owner);
    if (!state) {
      return Status::INVALID_STORAGE;
    }
    for (nodes::eval_log::NodeWarning &warning : state->warnings) {
      if (!visit(RNA_NodesModifierWarning, &warning, owner.owner_id, false)) {
        break;
      }
    }
    return Status::OK;
  }
  return Status::UNSUPPORTED;
}

Status length(Scope &scope,
              Family family,
              const PointerRNA &owner,
              const PropertyRNA &property,
              int64_t &result)
{
  result = 0;
  if (!owner.type || family == NONE || collection_family(*owner.type, property) != family) {
    return Status::UNSUPPORTED;
  }
  int64_t count = 0;
  const Status status = visit_entries(
      scope, family, owner, [&](StructRNA *, void *, ID *, bool) {
        ++count;
        return true;
      });
  if (status == Status::OK) {
    result = count;
  }
  return status;
}

Status item(Scope &scope,
            Family family,
            const PointerRNA &owner,
            const PropertyRNA &property,
            int64_t index,
            Item &result)
{
  result = {};
  if (!owner.type || family == NONE || collection_family(*owner.type, property) != family) {
    return Status::UNSUPPORTED;
  }
  if (index < 0) {
    return Status::INVALID_STORAGE;
  }
  int64_t position = 0;
  bool found = false;
  const Status status = visit_entries(
      scope, family, owner, [&](StructRNA *type, void *data, ID *id, bool is_id) {
        if (position++ != index) {
          return true;
        }
        result.pointer = is_id ? PointerRNA(id, type, data) : PointerRNA(id, type, data, owner);
        result.family = family;
        result.index = index;
        found = true;
        return false;
      });
  if (status != Status::OK) {
    return status;
  }
  if (!found) {
    return Status::INVALID_STORAGE;
  }
  return Status::OK;
}

}  // namespace blender::angelo_read_traversal
