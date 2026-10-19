/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 * SPDX-License-Identifier: GPL-2.0-or-later */
/* Included inside the sculpt namespace after the native pass implementation. */

static wmOperatorStatus angelo_sculpt_program_exec(bContext *C, wmOperator *op)
{
  using namespace io::serialize;
  char *json = RNA_string_get_alloc(op->ptr, "program", nullptr, 0, nullptr);
  std::istringstream stream(json);
  MEM_delete(json);
  JsonFormatter formatter;
  std::unique_ptr<Value> root = formatter.deserialize(stream);
  const ArrayValue *steps = root ? root->as_array_value() : nullptr;
  if (!steps || steps->elements().is_empty() || steps->elements().size() > 8) {
    BKE_report(op->reports, RPT_ERROR, "A sculpt program needs 1-8 bounded passes");
    return OPERATOR_CANCELLED;
  }
  wmOperatorType *type = WM_operatortype_find("SCULPT_OT_angelo_sculpt_pass", false);
  Vector<PointerRNA> properties;
  Vector<int> anchors;
  Vector<float3> anchor_positions;
  auto cleanup = [&]() {
    for (PointerRNA &ptr : properties) {
      WM_operator_properties_free(&ptr);
    }
  };
  auto set_enum = [&](PointerRNA &ptr, const char *name, StringRefNull value) {
    PropertyRNA *prop = RNA_struct_find_property(&ptr, name);
    int number;
    if (!RNA_property_enum_value(nullptr, &ptr, prop, value.c_str(), &number)) {
      return false;
    }
    RNA_property_enum_set(&ptr, prop, number);
    return true;
  };
  auto vector = [](const ArrayValue *array, float *out) {
    if (!array || array->elements().size() != 3) {
      return false;
    }
    for (int i = 0; i < 3; i++) {
      const Value &value = *array->elements()[i];
      if (const auto *number = value.as_double_value()) {
        out[i] = number->value();
      }
      else if (const auto *number = value.as_int_value()) {
        out[i] = number->value();
      }
      else {
        return false;
      }
      if (!std::isfinite(out[i])) {
        return false;
      }
    }
    return true;
  };
  int dabs = 0;
  bool valid = true;
  for (const auto &value : steps->elements()) {
    const DictionaryValue *step = value->as_dictionary_value();
    if (!step) { valid = false; break; }
    properties.append(WM_operator_properties_create_ptr(type));
    PointerRNA &ptr = properties.last();
    const int64_t anchor = step->lookup_int("anchor_vertex").value_or(-1);
    float3 initial(0);
    if (anchor < -1 || anchor >= 1000000 ||
        (anchor >= 0 && !vector(step->lookup_array("anchor_initial"), initial))) {
      valid = false; break;
    }
    anchors.append(int(anchor));
    anchor_positions.append(initial);
    for (const char *name : {"brush_type", "symmetry", "falloff", "mode"}) {
      auto item = step->lookup_str(name);
      valid &= item && set_enum(ptr, name, *item);
    }
    for (const char *name : {"radius", "strength"}) {
      auto number = step->lookup_double(name);
      valid &= number && std::isfinite(*number) && *number > 0 &&
               *number <= (STREQ(name, "radius") ? 10000 : 1);
      if (number) { RNA_float_set(&ptr, name, *number); }
    }
    auto front = step->lookup_bool("front_faces_only");
    valid &= front.has_value();
    RNA_boolean_set(&ptr, "front_faces_only", front.value_or(true));
    float normal[3];
    valid &= vector(step->lookup_array("surface_normal"), normal);
    if (!valid) { break; }
    RNA_float_set_array(&ptr, "surface_normal", normal);
    int flags = 0;
    if (const auto *mask = step->lookup_array("automasking")) {
      for (const auto &entry : mask->elements()) {
        const auto *text = entry->as_string_value();
        if (!text || !set_enum(ptr, "automasking", text->value())) { valid = false; break; }
        flags |= RNA_enum_get(&ptr, "automasking");
      }
    }
    RNA_enum_set(&ptr, "automasking", flags);
    const ArrayValue *stroke = step->lookup_array("stroke");
    if (!stroke || stroke->elements().is_empty() || (dabs += stroke->elements().size()) > 512) {
      valid = false; break;
    }
    for (const auto &entry : stroke->elements()) {
      const DictionaryValue *point = entry->as_dictionary_value();
      float location[3];
      if (!point || !vector(point->lookup_array("location"), location)) { valid = false; break; }
      PointerRNA item;
      RNA_collection_add(&ptr, "stroke", &item);
      RNA_float_set_array(&item, "location", location);
      for (const char *name : {"pressure", "size", "x_tilt", "y_tilt"}) {
        auto number = point->lookup_double(name);
        if (!number || !std::isfinite(*number) ||
            *number < (ELEM(StringRef(name), "x_tilt", "y_tilt") ? -1 : 0) ||
            *number > (STREQ(name, "size") ? 2 : 1)) { valid = false; break; }
        RNA_float_set(&item, name, *number);
      }
      auto start = point->lookup_bool("is_start");
      valid &= start.has_value();
      RNA_boolean_set(&item, "is_start", start.value_or(false));
    }
    if (!valid) { break; }
  }
  if (!valid) {
    cleanup();
    BKE_report(op->reports, RPT_ERROR, "Invalid sculpt program; no changes were applied");
    return OPERATOR_CANCELLED;
  }
  /* Every native pass must satisfy the same preflight before creating Undo or
   * mutating the first pass. This also covers direct bpy callers and Repeat. */
  for (PointerRNA &ptr : properties) {
    wmOperator child = {};
    child.type = type;
    child.ptr = &ptr;
    child.reports = op->reports;
    if (angelo_sculpt_pass_exec_impl(C, &child, true, true) != OPERATOR_FINISHED) {
      cleanup();
      return OPERATOR_CANCELLED;
    }
  }
  Object &object = *CTX_data_active_object(C);
  Depsgraph *depsgraph = CTX_data_ensure_evaluated_depsgraph(C);
  BKE_sculpt_update_object_for_edit(depsgraph, &object, false);
  /* Creating Multires mask storage may rebuild the evaluated PBVH. Do it once
   * before capturing any transaction nodes, never halfway through a program. */
  for (PointerRNA &ptr : properties) {
    if (RNA_enum_get(&ptr, "brush_type") == SCULPT_BRUSH_TYPE_MASK) {
      BKE_sculpt_mask_layers_ensure(depsgraph,
                                   CTX_data_main(C),
                                   &object,
                                   BKE_sculpt_multires_active(CTX_data_scene(C), &object));
      BKE_sculpt_update_object_for_edit(depsgraph, &object, false);
      break;
    }
  }
  undo::push_begin(*CTX_data_scene(C), object, op);
  undo::angelo_program_begin();
  bool finished = true;
  for (const int step_index : properties.index_range()) {
    PointerRNA &ptr = properties[step_index];
    if (G.is_break) { finished = false; break; }
    /* Project EACH pass onto the surface produced by the preceding pass. */
    const int brush = RNA_enum_get(&ptr, "brush_type");
    const bool anchored = ELEM(brush, SCULPT_BRUSH_TYPE_GRAB, SCULPT_BRUSH_TYPE_ELASTIC_DEFORM, SCULPT_BRUSH_TYPE_POSE);
    const float radius = RNA_float_get(&ptr, "radius");
    float3 offset(0);
    if (anchors[step_index] >= 0) {
      auto point = bke::angelo::surface_vertex(*depsgraph, object, anchors[step_index]);
      if (!point) { finished = false; break; }
      offset = *point - anchor_positions[step_index];
    }
    RNA_BEGIN (&ptr, item, "stroke") {
      float3 position;
      RNA_float_get_array(&item, "location", position);
      position += offset;
      RNA_float_set_array(&item, "location", position);
      if (!anchored || RNA_boolean_get(&item, "is_start")) {
        auto hit = bke::angelo::surface_query(*depsgraph, object, position, float3(0), radius * 0.5f);
        if (!hit) { finished = false; break; }
        RNA_float_set_array(&item, "location", hit->position);
      }
    }
    RNA_END;
    if (!finished) { break; }
    wmOperator child = {};
    child.type = type;
    child.ptr = &ptr;
    child.reports = op->reports;
    if (angelo_sculpt_pass_exec_impl(C, &child, true) != OPERATOR_FINISHED) {
      finished = false; break;
    }
  }
  undo::push_end(object);
  cleanup();
  if (!finished) {
    BKE_report(op->reports, RPT_ERROR, "Sculpt program stopped; inspect partial changes or undo the program");
    /* FINISHED retains the already committed sculpt Undo step. The error
     * report still raises to bpy/the host, which reports partial changes.
     * CANCELLED makes WM discard that step and can undo unrelated work. */
    return OPERATOR_FINISHED;
  }
  return OPERATOR_FINISHED;
}

void SCULPT_OT_angelo_sculpt_program(wmOperatorType *ot)
{
  ot->name = "Angelo Sculpt Program";
  ot->idname = "SCULPT_OT_angelo_sculpt_program";
  ot->description = "Execute up to eight native brush passes with one sculpt undo transaction";
  ot->exec = angelo_sculpt_program_exec;
  ot->poll = sculpt_mode_poll_view3d;
  ot->flag = OPTYPE_UNDO;
  RNA_def_string(ot->srna, "program", nullptr, 262144, "Program", "Validated native brush passes");
}
