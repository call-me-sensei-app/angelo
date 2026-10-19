/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "bpy_angelo_read_scalar_const.hh"
#include "bpy_angelo_read_geometry_const.hh"

#include <algorithm>
#include <climits>
#include <cstdio>
#include <functional>
#include <new>
#include <utility>
#include <cstring>
#include <unordered_map>

#include "ANIM_action.hh"

#include "BLI_expr_pylike_eval.h"
#include "BLI_bounds.hh"
#include "BLI_math_base.h"
#include "BLI_math_geom.h"
#include "BLI_math_matrix.h"
#include "BLI_math_matrix.hh"
#include "BLI_math_rotation.h"
#include "BLI_math_vector.h"
#include "BLI_listbase.h"
#include "BLI_span.hh"
#include "BLI_string.h"
#include "BLI_string_utf8.h"
#include "BLI_threads.h"
#include "BLI_utildefines.h"

#include "BKE_action.hh"
#include "BKE_anim_path.h"
#include "BKE_armature.hh"
#include "BKE_attribute_storage.hh"
#include "BKE_constraint.h"
#include "BKE_data_transfer.h"
#include "BKE_deform.hh"
#include "BKE_curve.hh"
#include "BKE_customdata.hh"
#include "BKE_displist.h"
#include "BKE_editmesh.hh"
#include "BKE_editmesh_cache.hh"
#include "BKE_fcurve.hh"
#include "BKE_mesh.hh"
#include "BKE_mesh_types.hh"
#include "BKE_layer.hh"
#include "BKE_main.hh"
#include "BKE_material.hh"
#include "BKE_node.hh"
#include "BKE_node_enum.hh"
#include "BKE_object.hh"
#include "BKE_object_types.hh"
#include "BKE_particle.h"
#include "BKE_pointcache.h"
#include "BKE_paint_types.hh"
#include "BKE_scene.hh"


#include "DNA_anim_types.h"
#include "DNA_action_types.h"
#include "DNA_armature_types.h"
#include "DNA_brush_types.h"
#include "DNA_color_types.h"
#include "DNA_constraint_types.h"
#include "DNA_curve_types.h"
#include "DNA_fluid_types.h"
#include "DNA_gpencil_legacy_types.h"
#include "DNA_lattice_types.h"
#include "DNA_mesh_types.h"
#include "DNA_modifier_types.h"
#include "DNA_movieclip_types.h"
#include "DNA_material_types.h"
#include "DNA_meshdata_types.h"
#include "DNA_node_types.h"
#include "DNA_object_types.h"
#include "DNA_scene_types.h"
#include "DNA_sequence_types.h"
#include "DNA_space_types.h"

#include "DEG_depsgraph_query.hh"

#include "IMB_imbuf_types.hh"
#include "IMB_colormanagement.hh"
#include "MOV_read.hh"
#include "RE_pipeline.h"
#include "SEQ_sequencer.hh"
#include "SEQ_utils.hh"

#include "RNA_access.hh"
#include "RNA_enum_types.hh"
#include "RNA_prototypes.hh"
#include "intern/rna_internal_types.hh"

#ifdef WITH_FLUID
#  include "manta_fluid_API.h"
#endif

namespace blender::angelo_read_scalar {
namespace {

using Role = RNAReadCallbackRole;
constexpr int64_t array_prefix_limit = 64;

bool source_is(const PropertyRNA &property,
               const Role role,
               const char *file,
               const char *symbol)
{
  const RNAReadCallbackSource source = RNA_property_read_callback_source(&property, role);
  return source.state == RNAReadCallbackState::MATCHED &&
         source.origin == RNAReadCallbackOrigin::MANUAL && source.source_file &&
         source.source_identifier && STREQ(source.source_file, file) &&
         STREQ(source.source_identifier, symbol);
}

bool absent(const PropertyRNA &property, const Role role)
{
  const RNAReadCallbackState state = RNA_property_read_callback_source(&property, role).state;
  return state == RNAReadCallbackState::ABSENT || state == RNAReadCallbackState::NOT_APPLICABLE;
}

bool declaration_is_native(const PropertyRNA &property)
{
  return !RNA_property_is_idprop(&property) && !(property.flag & PROP_IDPROPERTY) &&
         !(property.flag_internal & PROP_INTERN_RUNTIME);
}

bool derives(const StructRNA &type, const StructRNA *base)
{
  for (const StructRNA *current = &type; current; current = current->base) {
    if (current == base) {
      return true;
    }
  }
  return false;
}

struct Binding {
  Family family;
  const StructRNA *owner;
  const char *property;
  PropertyType type;
  Role role;
  const char *file;
  const char *symbol;
  /* Strings and dynamic arrays have another selected read whose identity must also close. */
  const char *length = nullptr;
  int fixed_array_length = 0;
  PropertySubType subtype = PROP_NONE;
};

Span<Binding> scalar_bindings()
{
  static const Binding bindings[] = {
      {RENDER_THREADS, RNA_RenderSettings, "threads", PROP_INT, Role::GET, "rna_scene.cc", "rna_RenderSettings_threads_get"},
      {RENDER_PASS_RECT, RNA_RenderPass, "rect", PROP_FLOAT, Role::GET_ARRAY, "rna_render.cc", "rna_RenderPass_rect_get", "rna_RenderPass_rect_get_length"},
      {DRIVER_SIMPLE_EXPRESSION, RNA_Driver, "is_simple_expression", PROP_BOOLEAN, Role::GET, "rna_fcurve.cc", "rna_ChannelDriver_is_simple_expression_get"},
      {POSE_MATRIX_BASIS, RNA_PoseBone, "matrix_basis", PROP_FLOAT, Role::GET_ARRAY, "rna_pose.cc", "rna_PoseChannel_matrix_basis_get", nullptr, 16, PROP_MATRIX},
      {POSE_IN_IK_CHAIN, RNA_PoseBone, "is_in_ik_chain", PROP_BOOLEAN, Role::GET, "rna_pose.cc", "rna_PoseChannel_has_ik_get"},
      {ACTION_FRAME_RANGE, RNA_Action, "frame_range", PROP_FLOAT, Role::GET_ARRAY, "rna_action.cc", "rna_Action_frame_range_get", nullptr, 2},
      {ACTION_CURVE_FRAME_RANGE, RNA_Action, "curve_frame_range", PROP_FLOAT, Role::GET_ARRAY, "rna_action.cc", "rna_Action_curve_frame_range_get", nullptr, 2},
      {NODE_CRYPTOMATTE_MATTE, RNA_CompositorNodeCryptomatte, "matte_id", PROP_STRING, Role::GET, "rna_nodetree.cc", "rna_NodeCryptomatte_matte_get", "rna_NodeCryptomatte_matte_length"},
      {NODE_CRYPTOMATTE_MATTE, RNA_CompositorNodeCryptomatteV2, "matte_id", PROP_STRING, Role::GET, "rna_nodetree.cc", "rna_NodeCryptomatte_matte_get", "rna_NodeCryptomatte_matte_length"},
#ifdef WITH_FLUID
      {FLUID_DENSITY, RNA_FluidDomainSettings, "density_grid", PROP_FLOAT, Role::GET_ARRAY, "rna_fluid.cc", "rna_FluidModifier_density_grid_get", "rna_FluidModifier_grid_get_length"},
      {FLUID_VELOCITY, RNA_FluidDomainSettings, "velocity_grid", PROP_FLOAT, Role::GET_ARRAY, "rna_fluid.cc", "rna_FluidModifier_velocity_grid_get", "rna_FluidModifier_velocity_grid_get_length"},
      {FLUID_FLAME, RNA_FluidDomainSettings, "flame_grid", PROP_FLOAT, Role::GET_ARRAY, "rna_fluid.cc", "rna_FluidModifier_flame_grid_get", "rna_FluidModifier_grid_get_length"},
      {FLUID_COLOR, RNA_FluidDomainSettings, "color_grid", PROP_FLOAT, Role::GET_ARRAY, "rna_fluid.cc", "rna_FluidModifier_color_grid_get", "rna_FluidModifier_color_grid_get_length"},
      {FLUID_HEAT, RNA_FluidDomainSettings, "heat_grid", PROP_FLOAT, Role::GET_ARRAY, "rna_fluid.cc", "rna_FluidModifier_heat_grid_get", "rna_FluidModifier_heat_grid_get_length"},
      {FLUID_TEMPERATURE, RNA_FluidDomainSettings, "temperature_grid", PROP_FLOAT, Role::GET_ARRAY, "rna_fluid.cc", "rna_FluidModifier_temperature_grid_get", "rna_FluidModifier_grid_get_length"},
#endif
      {NODE_SOCKET_MENU_ITEMS, RNA_NodeSocketMenu, "default_value", PROP_ENUM, Role::ENUM_ITEMS, "rna_node_socket.cc", "RNA_node_socket_menu_itemf"},
      {NODE_INPUT_MENU_ITEMS, RNA_FunctionNodeInputMenu, "value", PROP_ENUM, Role::ENUM_ITEMS, "rna_nodetree.cc", "rna_NodeInputMenu_menu_itemf"},
      {ANNOTATION_LAYER_ITEMS, RNA_AnnotationLayers, "active_note", PROP_ENUM, Role::ENUM_ITEMS, "rna_annotations.cc", "rna_annotation_active_layer_itemf"},
      {ACTION_SLOT_ID_TYPE_ITEMS, RNA_ActionSlot, "target_id_type", PROP_ENUM, Role::ENUM_ITEMS, "rna_action.cc", "rna_ActionSlot_target_id_type_itemf"},
      {OBJECT_MATRIX_LOCAL, RNA_Object, "matrix_local", PROP_FLOAT, Role::GET_ARRAY, "rna_object.cc", "rna_Object_matrix_local_get", nullptr, 16, PROP_MATRIX},
      {OBJECT_BOUND_BOX, RNA_Object, "bound_box", PROP_FLOAT, Role::GET_ARRAY, "rna_object.cc", "rna_Object_boundbox_get", nullptr, 24},
      {OBJECT_DIMENSIONS, RNA_Object, "dimensions", PROP_FLOAT, Role::GET_ARRAY, "rna_object.cc", "rna_Object_dimensions_get", nullptr, 3, PROP_XYZ_LENGTH},
      {COLORSPACE_INDEX, RNA_ColorManagedInputColorspaceSettings, "name", PROP_ENUM, Role::GET, "rna_color.cc", "rna_ColorManagedColorspaceSettings_colorspace_get"},
      {COLORSPACE_INDEX, RNA_ColorManagedSequencerColorspaceSettings, "name", PROP_ENUM, Role::GET, "rna_color.cc", "rna_ColorManagedColorspaceSettings_colorspace_get"},
      {NODE_FROM_COLORSPACE, RNA_CompositorNodeConvertColorSpace, "from_color_space", PROP_ENUM, Role::GET, "rna_nodetree.cc", "rna_NodeConvertColorSpace_from_color_space_get"},
      {NODE_TO_COLORSPACE, RNA_CompositorNodeConvertColorSpace, "to_color_space", PROP_ENUM, Role::GET, "rna_nodetree.cc", "rna_NodeConvertColorSpace_to_color_space_get"},
      {PARTICLE_DUPLI_WEIGHT_NAME, RNA_ParticleDupliWeight, "name", PROP_STRING, Role::GET, "rna_particle.cc", "rna_ParticleDupliWeight_name_get", "rna_ParticleDupliWeight_name_length"},
      {PARTICLE_TARGET_NAME, RNA_ParticleTarget, "name", PROP_STRING, Role::GET, "rna_particle.cc", "rna_ParticleTarget_name_get", "rna_ParticleTarget_name_length"},
      {PARTICLE_EDIT_EDITABLE, RNA_ParticleEdit, "is_editable", PROP_BOOLEAN, Role::GET, "rna_sculpt_paint.cc", "rna_ParticleEdit_editable_get"},
      {PARTICLE_EDIT_HAIR, RNA_ParticleEdit, "is_hair", PROP_BOOLEAN, Role::GET, "rna_sculpt_paint.cc", "rna_ParticleEdit_hair_get"},
      {STRIP_DURATION, RNA_Strip, "duration", PROP_INT, Role::GET, "rna_sequencer.cc", "rna_Strip_duration_get", nullptr, 0, PROP_TIME},
      {STRIP_DURATION, RNA_Strip, "frame_final_duration", PROP_INT, Role::GET, "rna_sequencer.cc", "rna_Strip_duration_get", nullptr, 0, PROP_TIME},
      {STRIP_CONTENT_DURATION, RNA_Strip, "content_duration", PROP_INT, Role::GET, "rna_sequencer.cc", "rna_Strip_content_duration_get", nullptr, 0, PROP_TIME},
      {STRIP_CONTENT_DURATION, RNA_Strip, "frame_duration", PROP_INT, Role::GET, "rna_sequencer.cc", "rna_Strip_content_duration_get", nullptr, 0, PROP_TIME},
      {STRIP_CONTENT_END, RNA_Strip, "content_end", PROP_INT, Role::GET, "rna_sequencer.cc", "rna_Strip_content_end_get", nullptr, 0, PROP_TIME},
      {STRIP_LEFT_HANDLE, RNA_Strip, "left_handle", PROP_INT, Role::GET, "rna_sequencer.cc", "rna_Strip_left_handle_get", nullptr, 0, PROP_TIME},
      {STRIP_LEFT_HANDLE, RNA_Strip, "frame_final_start", PROP_INT, Role::GET, "rna_sequencer.cc", "rna_Strip_left_handle_get", nullptr, 0, PROP_TIME},
      {STRIP_RIGHT_HANDLE, RNA_Strip, "right_handle", PROP_INT, Role::GET, "rna_sequencer.cc", "rna_Strip_right_handle_get", nullptr, 0, PROP_TIME},
      {STRIP_RIGHT_HANDLE, RNA_Strip, "frame_final_end", PROP_INT, Role::GET, "rna_sequencer.cc", "rna_Strip_right_handle_get", nullptr, 0, PROP_TIME},
      {STRIP_RETIMING_KEY_FRAME, RNA_RetimingKey, "timeline_frame", PROP_INT, Role::GET, "rna_sequencer.cc", "rna_Strip_retiming_key_frame_get"},
      {STRIP_RETIMING_SELECTED, RNA_SequenceEditor, "selected_retiming_keys", PROP_BOOLEAN, Role::GET, "rna_sequencer.cc", "rna_SequenceEditor_selected_retiming_key_get"},
      {STRIP_LOCK, RNA_Strip, "lock", PROP_BOOLEAN, Role::GET, "rna_sequencer.cc", "rna_Strip_lock_get"},
      {PARTICLE_HAIR_LOCATION, RNA_ParticleHairKey, "co", PROP_FLOAT, Role::GET_ARRAY, "rna_particle.cc", "rna_ParticleHairKey_location_object_get", nullptr, 3, PROP_TRANSLATION},
      {POINTCACHE_INFO, RNA_PointCache, "info", PROP_STRING, Role::STRING_LENGTH, "rna_object_force.cc", "rna_Cache_info_length"},
      {POINTCACHE_ACTIVE_INDEX, RNA_PointCaches, "active_index", PROP_INT, Role::GET, "rna_object_force.cc", "rna_Cache_active_point_cache_index_get", nullptr, 0, PROP_UNSIGNED},
      {STRIP_FPS, RNA_MovieStrip, "fps", PROP_FLOAT, Role::GET, "rna_sequencer.cc", "rna_Strip_fps_get"},
      {STRIP_FPS, RNA_MovieClipStrip, "fps", PROP_FLOAT, Role::GET, "rna_sequencer.cc", "rna_Strip_fps_get"},
      {STRIP_FPS, RNA_SceneStrip, "fps", PROP_FLOAT, Role::GET, "rna_sequencer.cc", "rna_Strip_fps_get"},
      {COLOR_VIEW_ITEMS, RNA_ColorManagedViewSettings, "view_transform", PROP_ENUM, Role::ENUM_ITEMS, "rna_color.cc", "rna_ColorManagedViewSettings_view_transform_itemf"},
      {DATA_TRANSFER_SOURCE_ITEMS, RNA_DataTransferModifier, "layers_vgroup_select_src", PROP_ENUM, Role::ENUM_ITEMS, "rna_modifier.cc", "rna_DataTransferModifier_layers_select_src_itemf"},
      {DATA_TRANSFER_SOURCE_ITEMS, RNA_DataTransferModifier, "layers_uv_select_src", PROP_ENUM, Role::ENUM_ITEMS, "rna_modifier.cc", "rna_DataTransferModifier_layers_select_src_itemf"},
      {DATA_TRANSFER_SOURCE_ITEMS, RNA_DataTransferModifier, "layers_vcol_vert_select_src", PROP_ENUM, Role::ENUM_ITEMS, "rna_modifier.cc", "rna_DataTransferModifier_layers_select_src_itemf"},
      {DATA_TRANSFER_SOURCE_ITEMS, RNA_DataTransferModifier, "layers_vcol_loop_select_src", PROP_ENUM, Role::ENUM_ITEMS, "rna_modifier.cc", "rna_DataTransferModifier_layers_select_src_itemf"},
      {DATA_TRANSFER_DESTINATION_ITEMS, RNA_DataTransferModifier, "layers_vgroup_select_dst", PROP_ENUM, Role::ENUM_ITEMS, "rna_modifier.cc", "rna_DataTransferModifier_layers_select_dst_itemf"},
      {DATA_TRANSFER_DESTINATION_ITEMS, RNA_DataTransferModifier, "layers_uv_select_dst", PROP_ENUM, Role::ENUM_ITEMS, "rna_modifier.cc", "rna_DataTransferModifier_layers_select_dst_itemf"},
      {DATA_TRANSFER_DESTINATION_ITEMS, RNA_DataTransferModifier, "layers_vcol_vert_select_dst", PROP_ENUM, Role::ENUM_ITEMS, "rna_modifier.cc", "rna_DataTransferModifier_layers_select_dst_itemf"},
      {DATA_TRANSFER_DESTINATION_ITEMS, RNA_DataTransferModifier, "layers_vcol_loop_select_dst", PROP_ENUM, Role::ENUM_ITEMS, "rna_modifier.cc", "rna_DataTransferModifier_layers_select_dst_itemf"},
      {NODE_IMPLICIT_SOCKET_TYPE, RNA_NodeImplicitConversion, "data_type", PROP_ENUM, Role::GET, "rna_nodetree.cc", "rna_NodeImplicitConversion_data_type_get"},
      {NODE_INTERFACE_SOCKET_TYPE, RNA_NodeTreeInterfaceSocket, "socket_type", PROP_ENUM, Role::GET, "rna_node_tree_interface.cc", "rna_NodeTreeInterfaceSocket_socket_type_get"},
      {NODE_IMPLICIT_SOCKET_TYPE_ITEMS, RNA_NodeImplicitConversion, "data_type", PROP_ENUM, Role::ENUM_ITEMS, "rna_nodetree.cc", "rna_NodeImplicitConversion_data_type_itemf"},
      {NODE_INTERFACE_SOCKET_TYPE_ITEMS, RNA_NodeTreeInterfaceSocket, "socket_type", PROP_ENUM, Role::ENUM_ITEMS, "rna_node_tree_interface.cc", "rna_NodeTreeInterfaceSocket_socket_type_itemf"},
      {COLORSPACE_ITEMS, RNA_ColorManagedInputColorspaceSettings, "name", PROP_ENUM, Role::ENUM_ITEMS, "rna_color.cc", "rna_ColorManagedColorspaceSettings_colorspace_itemf"},
      {COLORSPACE_ITEMS, RNA_ColorManagedSequencerColorspaceSettings, "name", PROP_ENUM, Role::ENUM_ITEMS, "rna_color.cc", "rna_ColorManagedColorspaceSettings_colorspace_itemf"},
      {COLORSPACE_ITEMS, RNA_CompositorNodeConvertColorSpace, "from_color_space", PROP_ENUM, Role::ENUM_ITEMS, "rna_nodetree.cc", "rna_NodeConvertColorSpace_color_space_itemf"},
      {COLORSPACE_ITEMS, RNA_CompositorNodeConvertColorSpace, "to_color_space", PROP_ENUM, Role::ENUM_ITEMS, "rna_nodetree.cc", "rna_NodeConvertColorSpace_color_space_itemf"},
      {NODE_ITEM_SOCKET_TYPE_ITEMS, RNA_RepeatItem, "socket_type", PROP_ENUM, Role::ENUM_ITEMS, "rna_nodetree.cc", "rna_Node_ItemArray_socket_type_itemf<RepeatItemsAccessor>"},
      {NODE_ITEM_SOCKET_TYPE_ITEMS, RNA_NodeGeometryViewerItem, "socket_type", PROP_ENUM, Role::ENUM_ITEMS, "rna_nodetree.cc", "rna_Node_ItemArray_socket_type_itemf<GeoViewerItemsAccessor>"},
      {NODE_ITEM_SOCKET_TYPE_ITEMS, RNA_NodeClosureInputItem, "socket_type", PROP_ENUM, Role::ENUM_ITEMS, "rna_nodetree.cc", "rna_Node_ItemArray_socket_type_itemf<ClosureInputItemsAccessor>"},
      {NODE_ITEM_SOCKET_TYPE_ITEMS, RNA_NodeClosureOutputItem, "socket_type", PROP_ENUM, Role::ENUM_ITEMS, "rna_nodetree.cc", "rna_Node_ItemArray_socket_type_itemf<ClosureOutputItemsAccessor>"},
      {NODE_ITEM_SOCKET_TYPE_ITEMS, RNA_NodeEvaluateClosureInputItem, "socket_type", PROP_ENUM, Role::ENUM_ITEMS, "rna_nodetree.cc", "rna_Node_ItemArray_socket_type_itemf<EvaluateClosureInputItemsAccessor>"},
      {NODE_ITEM_SOCKET_TYPE_ITEMS, RNA_NodeEvaluateClosureOutputItem, "socket_type", PROP_ENUM, Role::ENUM_ITEMS, "rna_nodetree.cc", "rna_Node_ItemArray_socket_type_itemf<EvaluateClosureOutputItemsAccessor>"},
      {NODE_ITEM_SOCKET_TYPE_ITEMS, RNA_NodeCombineBundleItem, "socket_type", PROP_ENUM, Role::ENUM_ITEMS, "rna_nodetree.cc", "rna_Node_ItemArray_socket_type_itemf<CombineBundleItemsAccessor>"},
      {NODE_ITEM_SOCKET_TYPE_ITEMS, RNA_NodeSeparateBundleItem, "socket_type", PROP_ENUM, Role::ENUM_ITEMS, "rna_nodetree.cc", "rna_Node_ItemArray_socket_type_itemf<SeparateBundleItemsAccessor>"},
      {NODE_ITEM_SOCKET_TYPE_ITEMS, RNA_GeometryNodeClosureToListItem, "socket_type", PROP_ENUM, Role::ENUM_ITEMS, "rna_nodetree.cc", "rna_Node_ItemArray_socket_type_itemf<ClosureToListItemsAccessor>"},
  };
  return Span<Binding>(bindings, ARRAY_SIZE(bindings));
}

const Binding *binding_for(const StructRNA &type, const PropertyRNA &property, const bool items)
{
  if (!declaration_is_native(property) || !property.identifier) {
    return nullptr;
  }
  for (const Binding &binding : scalar_bindings()) {
    if (items != (binding.role == Role::ENUM_ITEMS) || !derives(type, binding.owner) ||
        property.type != binding.type || property.subtype != binding.subtype ||
        !STREQ(property.identifier, binding.property) ||
        !source_is(property, binding.role, binding.file, binding.symbol))
    {
      continue;
    }
    if (items) {
      return &binding;
    }
    if (binding.role == Role::STRING_LENGTH) {
      const RNAReadCallbackSource getter = RNA_property_read_callback_source(&property, Role::GET);
      if (getter.state != RNAReadCallbackState::MATCHED ||
          getter.origin != RNAReadCallbackOrigin::GENERATED_DNA || !getter.source_file ||
          !STREQ(getter.source_file, binding.file))
      {
        continue;
      }
    }
    const bool array = binding.role == Role::GET_ARRAY;
    /* An installed transform would alter the result after the selected getter. */
    if (!absent(property, array ? Role::GET_ARRAY_TRANSFORM : Role::GET_TRANSFORM)) {
      continue;
    }
    if (binding.length) {
      const Role role = binding.type == PROP_STRING ? Role::STRING_LENGTH : Role::ARRAY_LENGTH;
      if (!source_is(property, role, binding.file, binding.length)) {
        continue;
      }
    }
    else if (!absent(property, Role::ARRAY_LENGTH) ||
             property.totarraylength != binding.fixed_array_length)
    {
      continue;
    }
    if (array != (property.getlength || property.totarraylength)) {
      continue;
    }
    return &binding;
  }
  return nullptr;
}

Value invalid()
{
  Value result;
  result.status = Status::INVALID_STORAGE;
  return result;
}

template<typename T> Value scalar(T value)
{
  Value result;
  result.status = Status::OK;
  result.data = std::move(value);
  return result;
}

Value float_prefix(const float *values, const int64_t length)
{
  if (length < 0) {
    return invalid();
  }
  Value result;
  result.status = Status::OK;
  result.actual_length = length;
  std::vector<float> copied(std::min(length, array_prefix_limit), 0.0f);
  if (values) {
    std::copy_n(values, copied.size(), copied.data());
  }
  result.data = std::move(copied);
  return result;
}

/* RNA lengths are ints. Refuse malformed/overflowing layout before any pointer arithmetic. */
bool checked_product(const int *dimensions, const int count, int64_t &total)
{
  total = 1;
  for (int i = 0; i < count; i++) {
    if (dimensions[i] < 0 || (dimensions[i] && total > INT_MAX / dimensions[i])) {
      return false;
    }
    total *= dimensions[i];
  }
  return true;
}

Value driver_simple_expression(const ChannelDriver &driver)
{
  /* The cached expression takes precedence even if the driver type has subsequently changed. */
  if (driver.expr_simple) {
    return scalar(BLI_expr_pylike_is_valid(driver.expr_simple));
  }
  if (driver.type != DRIVER_TYPE_PYTHON) {
    return scalar(false);
  }
  std::vector<const char *> names = {"frame"};
  for (const DriverVar &variable : driver.variables) {
    if (names.size() >= INT_MAX) {
      return invalid();
    }
    names.push_back(variable.name);
  }
  /* Parsing allocates native request-local operations. Do not evaluate or publish a cache. */
  ExprPyLike_Parsed *parsed = BLI_expr_pylike_parse(driver.expression, names.data(), names.size());
  const bool valid = BLI_expr_pylike_is_valid(parsed);
  BLI_expr_pylike_free(parsed);
  return scalar(valid);
}

Value action_frame_range(const bAction &source, const bool curve_range)
{
  const animrig::Action &action = source.wrap();
  if (!curve_range && (action.flag & ACT_FRAME_RANGE)) {
    const float range[2] = {action.frame_start, action.frame_end};
    return float_prefix(range, 2);
  }
  float minimum = 999999999.0f, maximum = -999999999.0f;
  bool found = false;
  /* Same traversal order and duplicates as legacy::fcurves_all, without its whole pointer Vector. */
  for (const animrig::Layer *layer : action.layers()) {
    for (const animrig::Strip *strip : layer->strips()) {
      switch (strip->type()) {
        case animrig::Strip::Type::Keyframe: {
          const animrig::StripKeyframeData &data = strip->data<animrig::StripKeyframeData>(action);
          for (const animrig::Channelbag *bag : data.channelbags()) {
            for (const FCurve *curve : bag->fcurves()) {
              if (curve->totvert) {
                float curve_min, curve_max;
                BKE_fcurve_calc_range(curve, &curve_min, &curve_max, false);
                minimum = min_ff(minimum, curve_min);
                maximum = max_ff(maximum, curve_max);
                found = true;
              }
            }
          }
          break;
        }
      }
    }
  }
  const float range[2] = {found ? max_ff(minimum, MINAFRAMEF) : 0.0f,
                          found ? min_ff(maximum, MAXFRAMEF) : 0.0f};
  return float_prefix(range, 2);
}

Value cryptomatte_matte(const bNode &node)
{
  const auto *storage = static_cast<const NodeCryptomatte *>(node.storage);
  if (!storage) {
    return invalid();
  }
  std::string matte;
  for (const CryptomatteEntry &entry : storage->entries) {
    if (!matte.empty()) {
      matte += ',';
    }
    if (entry.name[0]) {
      matte.append(entry.name, strnlen(entry.name, sizeof(entry.name)));
    }
    else {
      char encoded[64];
      const int length = std::snprintf(encoded, sizeof(encoded), "<%.9g>", entry.encoded_hash);
      if (length < 0 || length >= int(sizeof(encoded))) {
        return invalid();
      }
      matte.append(encoded, length);
    }
  }
  /* Original bytes are retained; the core applies its established string prefix policy. Memory
   * here scales with the resulting text, not with a second whole DynStr/getcstring intermediate. */
  return scalar(std::move(matte));
}

Status from_geometry(const angelo_read_geometry::Status status)
{
  switch (status) {
    case angelo_read_geometry::Status::OK: return Status::OK;
    case angelo_read_geometry::Status::INVALID_STORAGE: return Status::INVALID_STORAGE;
    case angelo_read_geometry::Status::RESOURCE_LIMIT: return Status::RESOURCE_LIMIT;
    case angelo_read_geometry::Status::NOT_EVALUATED: return Status::NOT_EVALUATED;
    case angelo_read_geometry::Status::LOADS_FILE: return Status::LOADS_FILE;
    case angelo_read_geometry::Status::UNSUPPORTED: return Status::UNSUPPORTED;
  }
  return Status::INVALID_STORAGE;
}

/* Keeps the reason a helper refused with. */
Value failed(const Status status)
{
  Value result;
  result.status = status;
  return result;
}

Status evaluated_mesh_for_read(angelo_read_geometry::Scope &scope,
                               const Object &object,
                               const Mesh *&result)
{
  return from_geometry(angelo_read_geometry::object_evaluated_mesh_for_read(scope, object, result));
}

/* Output-local counterpart of object.cc's give_parvert. Live BMesh index tables are never
 * ensured: a dirty table is represented by its canonical iteration order instead. */
Status parent_vertex(angelo_read_geometry::Scope &geometry,
                     const Object &parent,
                     const int index,
                     const bool evaluated_indices,
                     float vec[3])
{
  zero_v3(vec);
  if (index < 0 || !parent.runtime) {
    return Status::INVALID_STORAGE;
  }
  if (parent.type == OB_MESH) {
    if (!parent.data || GS(parent.data->name) != ID_ME) { return Status::INVALID_STORAGE; }
    const auto &mesh = *reinterpret_cast<const Mesh *>(parent.data);
    if (!mesh.runtime) {
      return Status::INVALID_STORAGE;
    }
    const BMEditMesh *edit = mesh.runtime->edit_mesh.get();
    const Mesh *evaluated = edit ? BKE_object_get_editmesh_eval_final(&parent) : nullptr;
    if (!edit) {
      const Status status = evaluated_mesh_for_read(geometry, parent, evaluated);
      if (status != Status::OK) { return status; }
    }
    if (!evaluated) {
      /* Canonical parent getter leaves the initialized zero coordinate in this domain. */
      return Status::OK;
    }
    if (!evaluated->runtime) {
      return Status::INVALID_STORAGE;
    }
    if (edit && evaluated->runtime->wrapper_type == ME_WRAPPER_TYPE_BMESH) {
      if (!edit->bm) {
        return Status::INVALID_STORAGE;
      }
      if (index >= edit->bm->totvert) {
        return Status::OK;
      }
      if (evaluated->runtime->edit_data &&
          !evaluated->runtime->edit_data->vert_positions.is_empty())
      {
        const auto &positions = evaluated->runtime->edit_data->vert_positions;
        if (index >= positions.size()) {
          return Status::INVALID_STORAGE;
        }
        copy_v3_v3(vec, positions[index]);
      }
      else if (!(edit->bm->elem_table_dirty & BM_VERT) && edit->bm->vtable) {
        copy_v3_v3(vec, edit->bm->vtable[index]->co);
      }
      else {
        BMIter iterator;
        BMVert *vertex;
        int current_index = 0;
        BM_ITER_MESH (vertex, &iterator, edit->bm, BM_VERTS_OF_MESH) {
          if (current_index++ == index) {
            copy_v3_v3(vec, vertex->co);
            return Status::OK;
          }
        }
        return Status::INVALID_STORAGE;
      }
      return Status::OK;
    }
    const Span<float3> positions = evaluated->vert_positions();
    if (positions.size() != evaluated->verts_num) {
      return Status::INVALID_STORAGE;
    }
    if (evaluated_indices && CustomData_has_layer(&evaluated->vert_data, CD_ORIGINDEX)) {
      const auto *original_indices = static_cast<const int *>(
          CustomData_get_layer(&evaluated->vert_data, CD_ORIGINDEX));
      if (!original_indices) {
        return Status::INVALID_STORAGE;
      }
      int count = 0;
      for (const int vertex : positions.index_range()) {
        if (original_indices[vertex] == index) {
          add_v3_v3(vec, positions[vertex]);
          count++;
        }
      }
      if (count) {
        mul_v3_fl(vec, 1.0f / count);
      }
    }
    else if (index < positions.size()) {
      copy_v3_v3(vec, positions[index]);
    }
  }
  else if (ELEM(parent.type, OB_CURVES_LEGACY, OB_SURF)) {
    if ((!parent.runtime->curve_cache || !parent.runtime->curve_cache->deformed_nurbs.first) &&
        (!parent.data || GS(parent.data->name) != ID_CU_LEGACY))
    { return Status::INVALID_STORAGE; }
    const ListBaseT<Nurb> *nurbs = parent.runtime->curve_cache &&
                                         parent.runtime->curve_cache->deformed_nurbs.first ?
                                     &parent.runtime->curve_cache->deformed_nurbs :
                                     BKE_curve_nurbs_get_for_read(
                                         reinterpret_cast<const Curve *>(parent.data));
    int64_t offset = 0;
    for (const Nurb &nurb : *nurbs) {
      const int dimensions[2] = {nurb.pntsu, nurb.type == CU_BEZIER ? 1 : nurb.pntsv};
      int64_t count;
      if (!checked_product(dimensions, 2, count)) {
        return Status::INVALID_STORAGE;
      }
      if (index - offset < count) {
        const int64_t local = index - offset;
        if (nurb.type == CU_BEZIER) {
          if (!nurb.bezt) {
            return Status::INVALID_STORAGE;
          }
          copy_v3_v3(vec, nurb.bezt[local].vec[1]);
        }
        else {
          if (!nurb.bp) {
            return Status::INVALID_STORAGE;
          }
          copy_v3_v3(vec, nurb.bp[local].vec);
        }
        break;
      }
      offset += count;
    }
  }
  else if (parent.type == OB_LATTICE) {
    if (!parent.data || GS(parent.data->name) != ID_LT) { return Status::INVALID_STORAGE; }
    const auto *lattice = reinterpret_cast<const Lattice *>(parent.data);
    const DispList *deformed = nullptr;
    if (parent.runtime->curve_cache) {
      for (const DispList &display : parent.runtime->curve_cache->disp) {
        if (display.type == DL_VERTS) {
          deformed = &display;
          break;
        }
      }
    }
    if (lattice->editlatt) {
      lattice = lattice->editlatt->latt;
    }
    if (!lattice) {
      return Status::INVALID_STORAGE;
    }
    const int dimensions[3] = {lattice->pntsu, lattice->pntsv, lattice->pntsw};
    int64_t count;
    if (!checked_product(dimensions, 3, count) || (deformed && deformed->nr != count)) {
      return Status::INVALID_STORAGE;
    }
    if (index < count) {
      if (deformed && deformed->verts) {
        copy_v3_v3(vec, deformed->verts + index * 3);
      }
      else if (lattice->def) {
        copy_v3_v3(vec, lattice->def[index].vec);
      }
      else {
        return Status::INVALID_STORAGE;
      }
    }
  }
  return Status::OK;
}

bool parent_curve(const Object &object, const Object &parent, float matrix[4][4])
{
  const auto &curve = *reinterpret_cast<const Curve *>(parent.data);
  if (!parent.runtime->curve_cache || !parent.runtime->curve_cache->anim_path_accum_length) {
    return false;
  }
  float time = curve.pathlen ? curve.ctime / curve.pathlen : curve.ctime;
  if (curve.flag & CU_PATH_CLAMP) {
    CLAMP(time, 0.0f, 1.0f);
  }
  unit_m4(matrix);
  float position[4], rotation[4], radius;
  if (BKE_where_on_path(&parent,
                        time,
                        position,
                        nullptr,
                        curve.flag & CU_FOLLOW ? rotation : nullptr,
                        &radius,
                        nullptr))
  {
    if (curve.flag & CU_FOLLOW) {
      quat_apply_track(rotation,
                       std::clamp<short>(object.trackflag, 0, 5),
                       std::clamp<short>(object.upflag, 0, 2));
      normalize_qt(rotation);
      quat_to_mat4(matrix, rotation);
    }
    if (curve.flag & CU_PATH_RADIUS) {
      float scale[4][4], scaled[4][4];
      scale_m4_fl(scale, radius);
      mul_m4_m4m4(scaled, scale, matrix);
      copy_m4_m4(matrix, scaled);
    }
    copy_v3_v3(matrix[3], position);
  }
  /* Canonical ob_parcurve returns true after the cache checks, even if sampling failed. */
  return true;
}

#ifdef WITH_FLUID
class FluidReadLock {
 private:
  ThreadRWMutex *mutex_;

 public:
  explicit FluidReadLock(void *mutex) : mutex_(static_cast<ThreadRWMutex *>(mutex))
  {
    BLI_rw_mutex_lock(mutex_, THREAD_LOCK_READ);
  }
  ~FluidReadLock()
  {
    BLI_rw_mutex_unlock(mutex_);
  }
};

Value fluid_prefix(const FluidDomainSettings &domain, const Family family)
{
  if (!domain.fluid_mutex) {
    return invalid();
  }
  /* The solver pointer, dimensions and every selected buffer are observed under this lock. */
  FluidReadLock lock(domain.fluid_mutex);
  if (!domain.fluid) {
    return float_prefix(nullptr, 0);
  }
  const bool noise = domain.flags & FLUID_DOMAIN_USE_NOISE;
  const bool low_resolution = family == FLUID_HEAT || family == FLUID_VELOCITY;
  int resolution[3];
  if (noise && !low_resolution) {
    manta_noise_get_res(domain.fluid, resolution);
  }
  else {
    std::copy_n(domain.res, 3, resolution);
  }
  int64_t cells;
  if (!checked_product(resolution, 3, cells)) {
    return invalid();
  }
  const float *density = nullptr;
  const float *heat = nullptr;
  const float *velocity[3] = {nullptr, nullptr, nullptr};
  if (family == FLUID_VELOCITY) {
    velocity[0] = manta_get_velocity_x(domain.fluid);
    velocity[1] = manta_get_velocity_y(domain.fluid);
    velocity[2] = manta_get_velocity_z(domain.fluid);
    if (!velocity[0] || !velocity[1] || !velocity[2]) {
      return float_prefix(nullptr, 0);
    }
  }
  else if (family == FLUID_HEAT) {
    heat = manta_smoke_get_heat(domain.fluid);
    if (!heat) {
      return float_prefix(nullptr, 0);
    }
  }
  else {
    density = noise ? manta_noise_get_density(domain.fluid) :
                      manta_smoke_get_density(domain.fluid);
    if (!density) {
      return float_prefix(nullptr, 0);
    }
  }
  const int components = family == FLUID_COLOR ? 4 : family == FLUID_VELOCITY ? 3 : 1;
  if (cells > INT_MAX / components) {
    return invalid();
  }
  const int64_t length = cells * components;
  const int64_t copied_length = std::min(length, array_prefix_limit);
  std::vector<float> copied(copied_length, 0.0f);
  const float *flame = nullptr;
  const float *color[3] = {nullptr, nullptr, nullptr};
  if (family == FLUID_FLAME || family == FLUID_TEMPERATURE) {
    flame = noise ? manta_noise_get_flame(domain.fluid) : manta_smoke_get_flame(domain.fluid);
  }
  if (family == FLUID_COLOR) {
    color[0] = noise ? manta_noise_get_color_r(domain.fluid) : manta_smoke_get_color_r(domain.fluid);
    color[1] = noise ? manta_noise_get_color_g(domain.fluid) : manta_smoke_get_color_g(domain.fluid);
    color[2] = noise ? manta_noise_get_color_b(domain.fluid) : manta_smoke_get_color_b(domain.fluid);
  }
  const bool has_colors = color[0] && color[1] && color[2];
  for (int64_t index = 0; index < copied_length; index++) {
    switch (family) {
      case FLUID_DENSITY:
        copied[index] = density[index];
        break;
      case FLUID_VELOCITY:
        copied[index] = velocity[index % 3][index / 3];
        break;
      case FLUID_HEAT:
        copied[index] = heat[index] * 0.5f;
        break;
      case FLUID_FLAME:
        copied[index] = flame ? flame[index] : 0.0f;
        break;
      case FLUID_TEMPERATURE:
        copied[index] = flame && flame[index] > 0.01f ?
                            domain.flame_ignition +
                                flame[index] * (domain.flame_max_temp - domain.flame_ignition) :
                            0.0f;
        break;
      case FLUID_COLOR: {
        const int component = index % 4;
        const int64_t cell = index / 4;
        copied[index] = has_colors ?
                            (component == 3 ? density[cell] : color[component][cell] * density[cell]) :
                            (component == 3 ? 1.0f : domain.active_color[component]);
        break;
      }
    }
  }
  Value result;
  result.status = Status::OK;
  result.actual_length = length;
  result.data = std::move(copied);
  return result;
}
#endif

void append_enum_items(EnumResult &result, const EnumPropertyItem *items)
{
  if (items) {
    for (const EnumPropertyItem *item = items; item->identifier; item++) {
      result.items.push_back({item->value, item->identifier, item->name ? item->name : ""});
    }
  }
}

void append_menu_items(EnumResult &result, const bNodeSocket *socket)
{
  if (!socket) {
    return;
  }
  if (socket->type != SOCK_MENU || !socket->default_value) {
    result.status = Status::INVALID_STORAGE;
    return;
  }
  const auto &menu = *static_cast<const bNodeSocketValueMenu *>(socket->default_value);
  if (menu.enum_items) {
    for (const bke::RuntimeNodeEnumItem &item : menu.enum_items->items) {
      result.items.push_back({item.identifier, item.name, item.name});
    }
  }
}

}  // namespace

struct Scope::Impl {
  std::unique_ptr<angelo_read_geometry::Scope> owned_geometry;
  angelo_read_geometry::Scope &geometry;

  Impl() : owned_geometry(std::make_unique<angelo_read_geometry::Scope>()), geometry(*owned_geometry) {}
  explicit Impl(angelo_read_geometry::Scope &geometry) : geometry(geometry) {}

  /* Reproduce BKE_pose_ensure_bone_indices locally. A valid warm array/index retains precedence;
   * a stale/cold array rebuild resolves names with the same pure pose hash/list lookup. */
  struct PoseBones {
    Status status = Status::OK;
    std::unordered_map<const bPoseChannel *, const Bone *> bones;
  };
  std::unordered_map<const Object *, PoseBones> poses;

  PoseBones &pose_bones(const Object &object)
  {
    auto [iterator, inserted] = poses.try_emplace(&object);
    PoseBones &snapshot = iterator->second;
    if (!inserted) {
      return snapshot;
    }
    if (object.type != OB_ARMATURE || !object.data || !object.pose || !object.runtime ||
        GS(object.data->name) != ID_AR)
    {
      snapshot.status = Status::INVALID_STORAGE;
      return snapshot;
    }
    const auto &armature = *reinterpret_cast<const bArmature *>(object.data);
    if (!armature.runtime) {
      snapshot.status = Status::INVALID_STORAGE;
      return snapshot;
    }
    const bool warm = armature.runtime->is_bones_array_valid() &&
                      armature.runtime->bones_generation_count ==
                          object.runtime->pose_bones_generation_count;
    for (const bPoseChannel &channel : object.pose->chanbase) {
      const Bone *bone = nullptr;
      if (warm && channel.runtime.bone_index != BONE_INDEX_UNKNOWN) {
        const int64_t index = channel.runtime.bone_index;
        if (index < 0 || index >= armature.runtime->bones.size()) {
          snapshot.status = Status::INVALID_STORAGE;
          return snapshot;
        }
        bone = armature.runtime->bones[index];
      }
      snapshot.bones.emplace(&channel, bone);
    }
    if (!warm) {
      BKE_armature_foreach_bone(armature, [&](const int /*index*/, const Bone &bone) {
        if (const bPoseChannel *channel = BKE_pose_channel_find_name(object.pose, bone.name)) {
          snapshot.bones[channel] = &bone;
        }
      });
    }
    return snapshot;
  }
};

Scope::Scope() : impl_(std::make_unique<Impl>()) {}
Scope::Scope(angelo_read_geometry::Scope &geometry) : impl_(std::make_unique<Impl>(geometry)) {}
Scope::~Scope() = default;

bool catalogued(const PropertyRNA &property)
{
  if (!declaration_is_native(property)) {
    return false;
  }
  for (const Binding &binding : scalar_bindings()) {
    if (source_is(property, binding.role, binding.file, binding.symbol) ||
        (binding.length &&
         (source_is(property, Role::STRING_LENGTH, binding.file, binding.length) ||
          source_is(property, Role::ARRAY_LENGTH, binding.file, binding.length))))
    {
      return true;
    }
  }
  return false;
}

Family value_family(const StructRNA &owner_type, const PropertyRNA &property)
{
  const Binding *binding = binding_for(owner_type, property, false);
  return binding ? binding->family : NONE;
}

Family enum_family(const StructRNA &owner_type, const PropertyRNA &property)
{
  const Binding *binding = binding_for(owner_type, property, true);
  return binding ? binding->family : NONE;
}

Value value(Scope &scope,
            const Family family,
            const PointerRNA &owner,
            const PropertyRNA &property)
{
  if (!owner.type || family == NONE || value_family(*owner.type, property) != family) {
    return {};
  }
  if (!owner.data) {
    return invalid();
  }
  switch (family) {
    case POINTCACHE_INFO:
      /* Blender rebuilds this text by checking the cache files on disk and stores it on the
       * cache. The stored text may be stale, so the value is withheld rather than shown. */
      return failed(Status::LOADS_FILE);
    case POINTCACHE_ACTIVE_INDEX: {
      /* The point cache's position in its owner's cache list. Blender finds that list by
       * building every cache descriptor of the object, which fills collection object caches;
       * a list position is the number of preceding links. */
      int index = 0;
      for (const PointCache *previous = static_cast<const PointCache *>(owner.data)->prev;
           previous;
           previous = previous->prev)
      {
        if (++index > 1 << 20) {
          return invalid();
        }
      }
      return scalar(index);
    }
    case STRIP_FPS: {
      if (!owner.owner_id || GS(owner.owner_id->name) != ID_SCE) {
        return invalid();
      }
      const auto &strip = *static_cast<const Strip *>(owner.data);
      switch (strip.type) {
        case STRIP_TYPE_MOVIE:
          /* Blender reopens the movie file to answer this. */
          return failed(Status::LOADS_FILE);
        case STRIP_TYPE_MOVIECLIP: {
          const MovieClip *clip = strip.clip;
          if (!clip || clip->source != MCLIP_SRC_MOVIE) {
            return scalar(0.0f);
          }
          /* An already open clip answers from its reader; otherwise the file would be opened. */
          return clip->anim ? scalar(MOV_get_fps(clip->anim)) : failed(Status::LOADS_FILE);
        }
        case STRIP_TYPE_SCENE:
          return scalar(strip.scene ? float(strip.scene->r.frs_sec) / strip.scene->r.frs_sec_base :
                                      0.0f);
        default:
          return scalar(0.0f);
      }
    }
    case PARTICLE_HAIR_LOCATION: {
      if (!owner.owner_id || GS(owner.owner_id->name) != ID_OB) {
        return invalid();
      }
      const auto &object = *reinterpret_cast<const Object *>(owner.owner_id);
      const auto &key = *static_cast<const HairKey *>(owner.data);
      const uintptr_t key_address = reinterpret_cast<uintptr_t>(&key);
      for (const ModifierData &modifier : object.modifiers) {
        if (modifier.type != eModifierType_ParticleSystem) {
          continue;
        }
        const auto &system_modifier = *reinterpret_cast<const ParticleSystemModifierData *>(&modifier);
        const ParticleSystem *system = system_modifier.psys;
        if (!system_modifier.mesh_final || !system) {
          continue;
        }
        if (system->totpart < 0 || (system->totpart && !system->particles)) {
          return invalid();
        }
        for (int index = 0; index < system->totpart; index++) {
          const ParticleData &particle = system->particles[index];
          if (particle.totkey < 0 || (particle.totkey && !particle.hair)) {
            return invalid();
          }
          const uintptr_t first = reinterpret_cast<uintptr_t>(particle.hair);
          const uint64_t bytes = uint64_t(particle.totkey) * sizeof(HairKey);
          if (!particle.hair || key_address < first || key_address - first >= bytes) {
            continue;
          }
          const int64_t offset = (key_address - first) / sizeof(HairKey);
          if ((key_address - first) % sizeof(HairKey)) {
            return invalid();
          }
          const Mesh *deformed = system->flag & PSYS_HAIR_DYNAMICS ? system->hair_out_mesh : nullptr;
          if (deformed) {
            const Span<float3> positions = deformed->vert_positions();
            const int64_t vertex = particle.hair_index + offset;
            return vertex >= 0 && vertex < positions.size() ? float_prefix(&positions[vertex][0], 3) : invalid();
          }
          if (!system->part) {
            return invalid();
          }
          Span<float3> normals;
          if (system->part->from == PART_FROM_VOLUME) {
            const auto status = angelo_read_geometry::mesh_vertex_normals_for_read(
                scope.impl_->geometry, *system_modifier.mesh_final, normals);
            if (status != angelo_read_geometry::Status::OK) {
              return failed(from_geometry(status));
            }
          }
          float matrix[4][4];
          if (!psys_mat_hair_to_object_for_read(system_modifier.mesh_final, system->part->from,
                                               particle, normals, matrix)) {
            return invalid();
          }
          float position[3];
          copy_v3_v3(position, key.co);
          mul_m4_v3(matrix, position);
          return float_prefix(position, 3);
        }
      }
      return float_prefix(nullptr, 3);
    }
    case STRIP_LOCK: {
      if (!owner.owner_id || GS(owner.owner_id->name) != ID_SCE) {
        return invalid();
      }
      const auto &scene = *reinterpret_cast<const Scene *>(owner.owner_id);
      const auto &strip = *static_cast<const Strip *>(owner.data);
      if (!scene.ed || !strip.runtime) {
        return invalid();
      }
      const std::optional<const Strip *> parent = seq::lookup_meta_by_strip_for_read(*scene.ed, strip);
      if (!parent) {
        return invalid();
      }
      const ListBaseT<SeqTimelineChannel> &channels = *parent ? (*parent)->channels : scene.ed->channels;
      const auto *channel = static_cast<const SeqTimelineChannel *>(BLI_findlink(&channels, strip.channel));
      if (!channel) {
        return invalid();
      }
      return scalar(bool(strip.flag & SEQ_LOCK) ||
                    (channel->is_locked() &&
                     !flag_is_set(strip.runtime->flag, seq::StripRuntimeFlag::IgnoreChannelLock)));
    }
    case STRIP_DURATION:
    case STRIP_CONTENT_DURATION:
    case STRIP_CONTENT_END:
    case STRIP_LEFT_HANDLE:
    case STRIP_RIGHT_HANDLE:
    case STRIP_RETIMING_KEY_FRAME:
    case STRIP_RETIMING_SELECTED: {
      if (!owner.owner_id || GS(owner.owner_id->name) != ID_SCE) {
        return invalid();
      }
      const auto &scene = *reinterpret_cast<const Scene *>(owner.owner_id);
      if (family == STRIP_RETIMING_SELECTED) {
        if (!scene.ed) {
          return scalar(false);
        }
        /* Same current (not recursive) strip list and key flags as retiming_selection_get,
         * without allocating its whole selection map merely to ask whether it is empty. */
        for (const Strip &strip : *scene.ed->current_strips()) {
          if (strip.retiming_keys_num < 0 || (strip.retiming_keys_num && !strip.retiming_keys)) {
            return invalid();
          }
          for (int index = 0; index < strip.retiming_keys_num; index++) {
            if (strip.retiming_keys[index].flag & SEQ_KEY_SELECTED) {
              return scalar(true);
            }
          }
        }
        return scalar(false);
      }
      if (family == STRIP_RETIMING_KEY_FRAME) {
        if (!scene.ed) {
          return scalar(0);
        }
        struct Pending {
          const Strip *next;
          const Strip *parent;
        };
        /* query_all_strips_recursive is child-before-parent. Preserve that exact search order
         * with O(meta-depth) pointer storage instead of building its O(all-strips) VectorSet. */
        std::vector<Pending> pending = {{static_cast<const Strip *>(scene.ed->seqbase.first), nullptr}};
        const auto &key = *static_cast<const SeqRetimingKey *>(owner.data);
        const Strip *found = nullptr;
        bool malformed = false;
        const auto matches = [&](const Strip &strip) {
          const int count = strip.retiming_keys_num;
          if (count < 0 || (count && !strip.retiming_keys)) {
            malformed = true;
            return false;
          }
          if (!count) {
            return false;
          }
          const uintptr_t first = reinterpret_cast<uintptr_t>(strip.retiming_keys);
          const uintptr_t address = reinterpret_cast<uintptr_t>(&key);
          const uint64_t last_offset = uint64_t(count - 1) * sizeof(SeqRetimingKey);
          return address >= first && uint64_t(address - first) <= last_offset;
        };
        while (!pending.empty() && !found && !malformed) {
          Pending &frame = pending.back();
          if (!frame.next) {
            const Strip *parent = frame.parent;
            pending.pop_back();
            if (parent && matches(*parent)) {
              found = parent;
            }
            continue;
          }
          const Strip *strip = frame.next;
          frame.next = strip->next;
          if (strip->type == STRIP_TYPE_META) {
            pending.push_back({static_cast<const Strip *>(strip->seqbase.first), strip});
          }
          else if (matches(*strip)) {
            found = strip;
          }
        }
        if (malformed) {
          return invalid();
        }
        return scalar(found ? int(found->content_start() + key.strip_frame_index) : 0);
      }
      const auto &strip = *static_cast<const Strip *>(owner.data);
      /* These canonical const members are source-closed stored arithmetic/retiming reads.
       * They never open the media reader used by media_fps(). */
      switch (family) {
        case STRIP_DURATION:
          return scalar(strip.right_handle(&scene) - strip.left_handle());
        case STRIP_CONTENT_DURATION:
          return scalar(strip.length(&scene));
        case STRIP_CONTENT_END:
          return scalar(int(strip.content_end(&scene)));
        case STRIP_LEFT_HANDLE:
          return scalar(strip.left_handle());
        case STRIP_RIGHT_HANDLE:
          return scalar(strip.right_handle(&scene));
      }
      return {};
    }
    case PARTICLE_EDIT_EDITABLE:
    case PARTICLE_EDIT_HAIR: {
      const auto &settings = *static_cast<const ParticleEditSettings *>(owner.data);
      if (!settings.scene || (family == PARTICLE_EDIT_EDITABLE && !settings.object)) {
        return scalar(false);
      }
      bool editable, hair;
      if (!psys_edit_cache_for_read(*settings.scene, settings.object, editable, hair)) {
        return invalid();
      }
      return scalar(family == PARTICLE_EDIT_EDITABLE ? editable : hair);
    }
    case OBJECT_BOUND_BOX:
    case OBJECT_DIMENSIONS: {
      if (!owner.owner_id || GS(owner.owner_id->name) != ID_OB || owner.data != owner.owner_id) {
        return invalid();
      }
      const auto &object = *static_cast<const Object *>(owner.data);
      std::optional<Bounds<float3>> object_bounds;
      const auto status = angelo_read_geometry::object_bounds(
          scope.impl_->geometry, object,
          angelo_read_geometry::BoundsMode::EVALUATED_CACHE_THEN_DATA, object_bounds);
      if (status != angelo_read_geometry::Status::OK) {
        return failed(from_geometry(status));
      }
      if (family == OBJECT_DIMENSIONS) {
        const float3 dimensions = object_bounds ?
                                      math::to_scale(object.object_to_world()) *
                                          (object_bounds->max - object_bounds->min) : float3(0);
        return float_prefix(&dimensions[0], 3);
      }
      if (!object_bounds) {
        return float_prefix(nullptr, 24);
      }
      const auto corners = bounds::corners(*object_bounds);
      return float_prefix(&corners[0][0], 24);
    }
    case NODE_IMPLICIT_SOCKET_TYPE: {
      const auto &node = *static_cast<const bNode *>(owner.data);
      const auto *storage = static_cast<const NodeImplicitConversion *>(node.storage);
      if (!storage) {
        return invalid();
      }
      /* Current node_socket_type_find uses StringRef lookup without UString interning. */
      const bke::bNodeSocketType *type = bke::node_socket_type_find(storage->type_idname);
      return scalar(type ? type->type : SOCK_CUSTOM);
    }
    case NODE_INTERFACE_SOCKET_TYPE: {
      const auto &socket = *static_cast<const bNodeTreeInterfaceSocket *>(owner.data);
      const bke::bNodeSocketType *type = bke::node_socket_type_find(socket.socket_type);
      if (!type) {
        return scalar(-1);
      }
      if (type->type != SOCK_CUSTOM) {
        type = bke::node_socket_type_find_static(type->type, PROP_NONE);
        if (!type) {
          return invalid();
        }
      }
      const Span<bke::bNodeSocketType *> types = bke::node_socket_types_get();
      for (const int index : types.index_range()) {
        if (types[index] == type) {
          return scalar(index);
        }
      }
      return invalid();
    }
    case PARTICLE_DUPLI_WEIGHT_NAME: {
      if (!owner.owner_id || GS(owner.owner_id->name) != ID_PA) {
        return invalid();
      }
      const auto &settings = *reinterpret_cast<const ParticleSettings *>(owner.owner_id);
      const auto &weight = *static_cast<const ParticleDupliWeight *>(owner.data);
      const Object *object;
      if (!psys_dupli_weight_object_for_read(settings, weight, object)) {
        return invalid();
      }
      char name[MAX_ID_NAME + 64];
      if (object) {
        BLI_snprintf_utf8_rlen(name, sizeof(name), "%s: %i", object->id.name + 2, weight.count);
      }
      else {
        BLI_strncpy_utf8(name, "No object", sizeof(name));
      }
      return scalar(std::string(name));
    }
    case PARTICLE_TARGET_NAME: {
      const auto &target = *static_cast<const ParticleTarget *>(owner.data);
      if (target.flag & PTARGET_VALID) {
        const Object *object = target.ob;
        if (!object) {
          if (!owner.owner_id || GS(owner.owner_id->name) != ID_OB) {
            return invalid();
          }
          object = reinterpret_cast<const Object *>(owner.owner_id);
        }
        if (target.psys == INT_MIN) {
          return invalid();
        }
        const auto *system = static_cast<const ParticleSystem *>(
            BLI_findlink(&object->particlesystem, target.psys - 1));
        if (system) {
          char name[MAX_ID_NAME + MAX_ID_NAME + 64];
          if (target.ob) {
            BLI_snprintf_rlen(name, sizeof(name), "%s: %s", object->id.name + 2, system->name);
          }
          else {
            BLI_strncpy(name, system->name, sizeof(name));
          }
          return scalar(std::string(name));
        }
      }
      /* Model-visible text is never translated (doc/angelo/localization.rst). */
      return scalar(std::string("Invalid target!"));
    }
    case COLORSPACE_INDEX:
    case NODE_FROM_COLORSPACE:
    case NODE_TO_COLORSPACE: {
      const char *name;
      if (family == COLORSPACE_INDEX) {
        name = static_cast<const ColorManagedColorspaceSettings *>(owner.data)->name;
      }
      else {
        const auto &node = *static_cast<const bNode *>(owner.data);
        const auto *storage = static_cast<const NodeConvertColorSpace *>(node.storage);
        if (!storage) {
          return invalid();
        }
        name = family == NODE_FROM_COLORSPACE ? storage->from_color_space : storage->to_color_space;
      }
      int index;
      return IMB_colormanagement_colorspace_index_for_read(name, index) ? scalar(index) : Value{};
    }
    case OBJECT_MATRIX_LOCAL: {
      if (!owner.owner_id || GS(owner.owner_id->name) != ID_OB ||
          owner.data != owner.owner_id)
      {
        return invalid();
      }
      const auto &object = *static_cast<const Object *>(owner.data);
      if (!object.runtime) {
        return invalid();
      }
      float matrix[4][4];
      if (!object.parent) {
        return float_prefix(&object.object_to_world().ptr()[0][0], 16);
      }
      const Object &parent = *object.parent;
      if (!parent.runtime) {
        return invalid();
      }
      const bool evaluated_indices = !(object.transflag & OB_PARENT_USE_FINAL_INDICES);
      float parent_matrix[4][4], local[4][4];
      switch (object.partype & PARTYPE) {
        case PAROBJECT: {
          if (parent.type == OB_CURVES_LEGACY &&
              (!parent.data || GS(parent.data->name) != ID_CU_LEGACY))
          { return invalid(); }
          const bool path = parent.type == OB_CURVES_LEGACY &&
                            (reinterpret_cast<const Curve *>(parent.data)->flag & CU_PATH) &&
                            parent_curve(object, parent, local);
          if (path) {
            mul_m4_m4m4(parent_matrix, parent.object_to_world().ptr(), local);
          }
          else {
            copy_m4_m4(parent_matrix, parent.object_to_world().ptr());
          }
          break;
        }
        case PARBONE: {
          unit_m4(local);
          if (parent.type == OB_ARMATURE) {
            const bPoseChannel *channel = BKE_pose_channel_find_name(parent.pose, object.parsubstr);
            if (channel) {
              Scope::Impl::PoseBones &snapshot = scope.impl_->pose_bones(parent);
              const auto bone = snapshot.bones.find(channel);
              if (snapshot.status != Status::OK || bone == snapshot.bones.end()) {
                return invalid();
              }
              if (bone->second) {
                if (bone->second->flag & BONE_RELATIVE_PARENTING) {
                  copy_m4_m4(local, channel->chan_mat);
                }
                else {
                  copy_m4_m4(local, channel->pose_mat);
                  float offset[3];
                  copy_v3_v3(offset, local[1]);
                  mul_v3_fl(offset, bone->second->length * object.parent_bone_head_tail_factor);
                  add_v3_v3(local[3], offset);
                }
              }
            }
          }
          mul_m4_m4m4(parent_matrix, parent.object_to_world().ptr(), local);
          break;
        }
        case PARVERT1: {
          float vertex[3];
          const Status status = parent_vertex(scope.impl_->geometry, parent, object.par1, evaluated_indices, vertex);
          if (status != Status::OK) { return failed(status); }
          unit_m4(parent_matrix);
          mul_v3_m4v3(parent_matrix[3], parent.object_to_world().ptr(), vertex);
          break;
        }
        case PARVERT3: {
          unit_m4(local);
          if (OB_TYPE_SUPPORT_PARVERT(parent.type)) {
            float vertices[3][3];
            const int indices[3] = {object.par1, object.par2, object.par3};
            for (int i = 0; i < 3; i++) {
              const Status status = parent_vertex(scope.impl_->geometry, parent, indices[i], evaluated_indices, vertices[i]);
              if (status != Status::OK) { return failed(status); }
            }
            float quaternion[4], rotation[3][3];
            tri_to_quat(quaternion, vertices[0], vertices[1], vertices[2]);
            quat_to_mat3(rotation, quaternion);
            copy_m4_m3(local, rotation);
            mid_v3_v3v3v3(local[3], vertices[0], vertices[1], vertices[2]);
          }
          mul_m4_m4m4(parent_matrix, parent.object_to_world().ptr(), local);
          break;
        }
        case PARSKEL:
          copy_m4_m4(parent_matrix, parent.object_to_world().ptr());
          break;
        default:
          return invalid();
      }
      invert_m4(parent_matrix);
      mul_m4_m4m4(matrix, parent_matrix, object.object_to_world().ptr());
      return float_prefix(&matrix[0][0], 16);
    }
    case RENDER_THREADS: {
      const auto &render = *static_cast<const RenderData *>(owner.data);
      const int override = BLI_system_num_threads_override_get();
      if (override > 0) {
        return scalar(override);
      }
      return scalar(std::max(render.mode & R_FIXED_THREADS ? render.threads :
                                                                BLI_system_thread_count_for_read(),
                             1));
    }
    case RENDER_PASS_RECT: {
      const auto &pass = *static_cast<const RenderPass *>(owner.data);
      const int dimensions[3] = {pass.rectx, pass.recty, pass.channels};
      int64_t length;
      if (!checked_product(dimensions, 3, length)) {
        return invalid();
      }
      return float_prefix(pass.ibuf ? pass.ibuf->float_data() : nullptr, length);
    }
    case DRIVER_SIMPLE_EXPRESSION:
      return driver_simple_expression(*static_cast<const ChannelDriver *>(owner.data));
    case ACTION_FRAME_RANGE:
    case ACTION_CURVE_FRAME_RANGE:
      return action_frame_range(*static_cast<const bAction *>(owner.data),
                                family == ACTION_CURVE_FRAME_RANGE);
    case NODE_CRYPTOMATTE_MATTE:
      return cryptomatte_matte(*static_cast<const bNode *>(owner.data));
    case POSE_MATRIX_BASIS:
    case POSE_IN_IK_CHAIN: {
      if (!owner.owner_id || GS(owner.owner_id->name) != ID_OB) {
        return invalid();
      }
      const auto &object = *reinterpret_cast<const Object *>(owner.owner_id);
      const auto &channel = *static_cast<const bPoseChannel *>(owner.data);
      Scope::Impl::PoseBones &snapshot = scope.impl_->pose_bones(object);
      const auto found = snapshot.bones.find(&channel);
      if (snapshot.status != Status::OK || found == snapshot.bones.end()) {
        return invalid();
      }
      if (family == POSE_MATRIX_BASIS) {
        float matrix[4][4];
        BKE_pchan_to_mat4({&channel, found->second}, matrix);
        return float_prefix(&matrix[0][0], 16);
      }
      /* Explicit stack avoids C++ recursion depth proportional to an artist's bone hierarchy. */
      struct Pending {
        const bPoseChannel *channel;
        int level;
      };
      std::vector<Pending> pending = {{&channel, 0}};
      while (!pending.empty()) {
        const Pending current = pending.back();
        pending.pop_back();
        for (const bConstraint &constraint : current.channel->constraints) {
          if (constraint.type == CONSTRAINT_TYPE_KINEMATIC) {
            const auto *data = static_cast<const bKinematicConstraint *>(constraint.data);
            if (!data) {
              return invalid();
            }
            if ((data->rootbone == 0 || data->rootbone > current.level) &&
                !(data->flag & CONSTRAINT_IK_AUTO))
            {
              return scalar(true);
            }
          }
        }
        const auto bone = snapshot.bones.find(current.channel);
        if (bone == snapshot.bones.end() || !bone->second || current.level == INT_MAX) {
          return invalid();
        }
        for (const Bone &child : bone->second->childbase) {
          if (const bPoseChannel *child_channel = BKE_pose_channel_find_name(object.pose, child.name)) {
            pending.push_back({child_channel, current.level + 1});
          }
        }
      }
      return scalar(false);
    }
#ifdef WITH_FLUID
    case FLUID_DENSITY:
    case FLUID_VELOCITY:
    case FLUID_FLAME:
    case FLUID_COLOR:
    case FLUID_HEAT:
    case FLUID_TEMPERATURE:
      return fluid_prefix(*static_cast<const FluidDomainSettings *>(owner.data), family);
#endif
  }
  return {};
}

EnumResult enum_items(Scope &scope,
                      const Family family,
                      const PointerRNA &owner,
                      const PropertyRNA &property,
                      const ReadWindow *window,
                      const bool declaration)
{
  if (!owner.type || family == NONE || enum_family(*owner.type, property) != family) {
    return {};
  }
  EnumResult result;
  result.status = Status::OK;
  /* Declarations use the same null-owner EnumProperty pointer facts as rna_rna.cc, not the
   * original object's type/data. Retain the original owner only for source-family binding. */
  const bool no_context = (property.flag & PROP_ENUM_NO_CONTEXT) ||
                          (declaration ? (RNA_EnumProperty->flag & STRUCT_NO_CONTEXT_WITHOUT_OWNER_ID) :
                           ((owner.type->flag & STRUCT_NO_CONTEXT_WITHOUT_OWNER_ID) && !owner.owner_id));
  const ReadWindow *selected = declaration || no_context ? nullptr : window;
  /* RNA uses static declarations except the explicit NO_CONTEXT owner-independent case. */
  if (!selected && !no_context) {
    append_enum_items(result, static_cast<const EnumPropertyRNA &>(property).item);
    return result;
  }
  if (declaration && no_context && family != ACTION_SLOT_ID_TYPE_ITEMS && family != COLORSPACE_ITEMS) {
    /* These two source callbacks ignore ptr entirely. Never reinterpret &PropertyRNA as an
     * original socket/node/constraint when a different callback has NO_CONTEXT installed. */
    result.status = Status::UNSUPPORTED;
    return result;
  }
  switch (family) {
    case COLOR_VIEW_ITEMS: {
      const ColorManagedDisplaySettings *display = nullptr;
      const PointerRNA parent = owner.parent();
      if (parent.data && parent.type) {
        if (derives(*parent.type, RNA_Scene)) {
          display = &static_cast<const Scene *>(parent.data)->display_settings;
        }
        else if (derives(*parent.type, RNA_ImageFormatSettings)) {
          display = &static_cast<const ImageFormatData *>(parent.data)->display_settings;
        }
        else if (derives(*parent.type, RNA_CompositorNodeConvertToDisplay)) {
          const auto *node = static_cast<const bNode *>(parent.data);
          if (!node->storage) { result.status = Status::INVALID_STORAGE; return result; }
          display = &static_cast<const NodeConvertToDisplay *>(node->storage)->display_settings;
        }
      }
      if (!display && owner.owner_id && GS(owner.owner_id->name) == ID_SCE) {
        display = &reinterpret_cast<const Scene *>(owner.owner_id)->display_settings;
      }
      if (!display && selected && selected->scene) {
        /* The original callback falls back to the context scene: the bound window's scene. */
        display = &selected->scene->display_settings;
      }
      if (!display) { result.status = Status::INVALID_STORAGE; return result; }
      Vector<ColorManagedReadEnumItem> items;
      if (!IMB_colormanagement_view_items_for_read(display->display_device, items)) {
        result.status = Status::UNSUPPORTED;
        return result;
      }
      for (const ColorManagedReadEnumItem &item : items) {
        result.items.push_back({item.value, item.identifier, item.name});
      }
      return result;
    }
    case DATA_TRANSFER_SOURCE_ITEMS:
    case DATA_TRANSFER_DESTINATION_ITEMS: {
      if (!owner.data || !selected) { result.status = Status::INVALID_STORAGE; return result; }
      const auto &transfer = *static_cast<const DataTransferModifierData *>(owner.data);
      const bool source = family == DATA_TRANSFER_SOURCE_ITEMS;
      const char *identifier = property.identifier;
      const bool groups = STRPREFIX(identifier, "layers_vgroup_");
      const bool uv = STRPREFIX(identifier, "layers_uv_");
      const bool vertex_color = STRPREFIX(identifier, "layers_vcol_vert_");
      const int layer_index = groups ? DT_MULTILAYER_INDEX_MDEFORMVERT :
                              uv ? DT_MULTILAYER_INDEX_UV :
                              vertex_color ? DT_MULTILAYER_INDEX_VCOL_VERT : DT_MULTILAYER_INDEX_VCOL_LOOP;
      const auto add_value = [&](int value) {
        for (const EnumPropertyItem *item = static_cast<const EnumPropertyRNA &>(property).item;
             item && item->identifier; item++)
        {
          if (item->value == value) {
            result.items.push_back({item->value, item->identifier, item->name ? item->name : ""});
            break;
          }
        }
      };
      if (source) { add_value(DT_LAYERS_ALL_SRC); }
      else {
        add_value(DT_LAYERS_NAME_DST);
        add_value(DT_LAYERS_INDEX_DST);
        if (transfer.layers_select_src[layer_index] < 0) { return result; }
      }
      const Object *object = transfer.ob_source;
      if (!source) {
        /* The original callback lists the context's active object. Its active base is read
         * from the bound window's view layer, which must already be synchronized. */
        const ViewLayer *layer = selected->view_layer;
        if (!layer || !BKE_view_layer_is_synced(*layer)) {
          result.items.clear();
          result.status = Status::NOT_EVALUATED;
          return result;
        }
        object = layer->basact ? layer->basact->object : nullptr;
      }
      if (!object) { return result; }
      if (groups) {
        if (!object->data || !BKE_object_supports_vertex_groups(object)) {
          result.items.clear(); result.status = Status::INVALID_STORAGE; return result;
        }
        const ListBaseT<bDeformGroup> *list = BKE_object_defgroup_list(object);
        if (!list) { result.items.clear(); result.status = Status::INVALID_STORAGE; return result; }
        result.items.push_back({0, "", ""});
        int index = 0;
        for (const bDeformGroup &group : *list) { result.items.push_back({index++, group.name, group.name}); }
        return result;
      }
      const Mesh *mesh = nullptr;
      if (source) {
        /* The original callback evaluates the dependency graph first. A read never evaluates:
         * it lists the source's evaluated layers only when Blender has already evaluated them. */
        const Depsgraph *graph = selected->scene && selected->view_layer ?
                                     BKE_scene_get_depsgraph(selected->scene, selected->view_layer) :
                                     nullptr;
        if (!graph || !DEG_is_fully_evaluated(graph)) {
          result.items.clear();
          result.status = Status::NOT_EVALUATED;
          return result;
        }
        const Object *evaluated = DEG_get_evaluated(graph, object);
        if (!evaluated) { return result; }
        const Status status = evaluated_mesh_for_read(scope.impl_->geometry, *evaluated, mesh);
        if (status != Status::OK) { result.items.clear(); result.status = status; return result; }
      }
      else if (object->data) {
        if (object->type != OB_MESH || GS(object->data->name) != ID_ME) {
          result.items.clear(); result.status = Status::INVALID_STORAGE; return result;
        }
        mesh = id_cast<const Mesh *>(object->data);
      }
      if (!mesh) { return result; }
      if (!mesh->runtime) { result.items.clear(); result.status = Status::INVALID_STORAGE; return result; }
      if (uv) {
        result.items.push_back({0, "", ""});
        int index = 0;
        for (const StringRefNull name : mesh->uv_map_names()) {
          result.items.push_back({index++, name, name});
        }
      }
      else {
        const CustomData &data = vertex_color ? mesh->vert_data : mesh->corner_data;
        int index = 0;
        for (const eCustomDataType type : {CD_PROP_COLOR, CD_PROP_BYTE_COLOR}) {
          const int count = CustomData_number_of_layers(&data, type);
          result.items.push_back({0, "", ""});
          for (int layer = 0; layer < count; layer++) {
            const char *name = CustomData_get_layer_name(&data, type, layer);
            if (!name) { result.items.clear(); result.status = Status::INVALID_STORAGE; return result; }
            result.items.push_back({index++, name, name});
          }
        }
      }
      return result;
    }
    case COLORSPACE_ITEMS: {
      Vector<ColorManagedReadEnumItem> items;
      if (!IMB_colormanagement_colorspace_items_for_read(items)) {
        result.status = Status::UNSUPPORTED;
        return result;
      }
      for (const ColorManagedReadEnumItem &item : items) {
        result.items.push_back({item.value, item.identifier, item.name});
      }
      return result;
    }
    case NODE_ITEM_SOCKET_TYPE_ITEMS:
    case NODE_IMPLICIT_SOCKET_TYPE_ITEMS:
    case NODE_INTERFACE_SOCKET_TYPE_ITEMS: {
      if (!owner.owner_id || GS(owner.owner_id->name) != ID_NT) {
        result.status = Status::INVALID_STORAGE;
        return result;
      }
      const auto &tree = *reinterpret_cast<const bNodeTree *>(owner.owner_id);
      const bke::bNodeTreeType *tree_type = family == NODE_ITEM_SOCKET_TYPE_ITEMS ?
                                              bke::node_tree_type_find_builtin(tree.type) :
                                              tree.typeinfo;
      if (!tree_type) {
        if (family != NODE_ITEM_SOCKET_TYPE_ITEMS) {
          result.status = Status::INVALID_STORAGE;
        }
        return result;
      }
      /* An add-on node tree's socket rule is a Python method; it is never called here. */
      if (tree_type->valid_socket_type && tree_type->rna_ext.data) {
        result.status = Status::EXTENSION_CODE;
        return result;
      }
      const auto add_if_valid = [&](const bke::bNodeSocketType &type,
                                     const int value,
                                     const char *identifier,
                                     const char *name) {
        bool allowed = true;
        if (tree_type->valid_socket_type) {
          allowed = tree_type->valid_socket_type(const_cast<bke::bNodeTreeType *>(tree_type),
                                                 const_cast<bke::bNodeSocketType *>(&type));
        }
        else if (family == NODE_ITEM_SOCKET_TYPE_ITEMS) {
          allowed = false;
        }
        if (allowed && identifier) {
          result.items.push_back({value, identifier, name ? name : ""});
        }
      };
      if (family == NODE_INTERFACE_SOCKET_TYPE_ITEMS) {
        const Span<bke::bNodeSocketType *> types = bke::node_socket_types_get();
        for (const int index : types.index_range()) {
          const bke::bNodeSocketType &type = *types[index];
          /* Preserve the original's basic-type restriction, without asking for labels/icons. */
          if (type.type != SOCK_CUSTOM &&
              &type != bke::node_socket_type_find_static(type.type, PROP_NONE))
          {
            continue;
          }
          add_if_valid(type, index, type.idname.c_str(), bke::node_socket_type_label(type).c_str());
        }
      }
      else {
        for (const EnumPropertyItem *item = rna_enum_node_socket_data_type_items;
             item->identifier;
             item++)
        {
          const bke::bNodeSocketType *type = bke::node_socket_type_find_static(item->value);
          if (type) {
            add_if_valid(*type, item->value, item->identifier, item->name);
          }
        }
      }
      return result;
    }
    case ACTION_SLOT_ID_TYPE_ITEMS:
      append_enum_items(result, rna_enum_id_type_items);
      result.items.push_back({0, "UNSPECIFIED", "Unspecified"});
      return result;
    case NODE_SOCKET_MENU_ITEMS:
      if (!owner.data) {
        return result;
      }
      append_menu_items(result, static_cast<const bNodeSocket *>(owner.data));
      return result;
    case NODE_INPUT_MENU_ITEMS:
      if (owner.data) {
        const auto &node = *static_cast<const bNode *>(owner.data);
        append_menu_items(result, static_cast<const bNodeSocket *>(node.outputs.first));
      }
      return result;
    case ANNOTATION_LAYER_ITEMS: {
      /* The original callback lists layers only with a context; icons are not part of a read
       * (building them would create icon previews). */
      if (!selected || !owner.owner_id) {
        return result;
      }
      if (GS(owner.owner_id->name) != ID_GD_LEGACY) {
        result.status = Status::INVALID_STORAGE;
        return result;
      }
      const auto &annotation = *reinterpret_cast<const bGPdata *>(owner.owner_id);
      int index = 0;
      for (const bGPDlayer &layer : annotation.layers) {
        result.items.push_back({index, layer.info, layer.info});
        index++;
      }
      return result;
    }
  }
  result.status = Status::UNSUPPORTED;
  return result;
}

}  // namespace blender::angelo_read_scalar
