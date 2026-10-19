/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * Private observations for the existing read-tool owner. These helpers return copies of bounded
 * values, never writable mesh or IDProperty storage. Stored ID pointers use ordinary live RNA
 * wrappers; document authority, path admission and custom-computed-property policy remain in the
 * Python controller, which emits only its existing filtered JSON references.
 */

#include <Python.h>

#include "MEM_guardedalloc.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstddef>
#include <climits>
#include <functional>
#include <unordered_set>
#include <string_view>
#include <unordered_map>
#include <memory>
#include <mutex>
#include <optional>
#include <map>
#include <set>
#include <string>
#include <tuple>
#include <type_traits>
#include <vector>
#include <cstring>

#include "BLI_span.hh"
#include "BLI_threads.h"
#include "BLI_time.h"
#include "BLI_utildefines.h"
#include "BLI_listbase.h"

#include "BKE_file_handler.hh"
#include "BKE_attribute.h"
#include "BKE_attribute.hh"
#include "BKE_attribute_storage.hh"
#include "BKE_global.hh"
#include "BKE_main.hh"
#include "BKE_material.hh"
#include "BKE_node_legacy_types.hh"
#include "BKE_node_runtime.hh"
#include "BKE_lib_id.hh"
#include "BKE_mesh.hh"
#include "BKE_mesh_wrapper.hh"
#include "BKE_subdiv.hh"
#include "BKE_mesh_types.hh"
#include "BKE_subdiv_modifier.hh"
#include "BKE_context.hh"
#include "BKE_idprop.hh"
#include "BKE_image.hh"
#include "BKE_layer.hh"

#include "DNA_image_types.h"
#include "DNA_layer_types.h"
#include "DNA_collection_types.h"
#include "DNA_material_types.h"
#include "DNA_world_types.h"
#include "DNA_light_types.h"
#include "DNA_texture_types.h"
#include "DNA_modifier_types.h"
#include "DNA_constraint_types.h"
#include "DNA_anim_types.h"
#include "DNA_anim_enums.h"
#include "DNA_object_types.h"
#include "DNA_object_force_types.h"
#include "DNA_mesh_types.h"
#include "DNA_meshdata_types.h"
#include "DNA_node_types.h"
#include "DNA_scene_types.h"
#include "DNA_sequence_types.h"
#include "DNA_rigidbody_types.h"
#include "DNA_windowmanager_types.h"

#include "RNA_access.hh"
#include "RNA_enum_types.hh"
#include "RNA_prototypes.hh"
#include "WM_api.hh"
#include "WM_types.hh"

#include "bpy_angelo_metadata.hh"
#include "bpy_angelo_read.hh"
#include "bpy_angelo_read_geometry_const.hh"
#include "bpy_angelo_read_scalar_const.hh"
#include "bpy_angelo_read_traversal_const.hh"
#include "bpy_angelo_read_words.hh"
#include "bpy_capi_utils.hh"
#include "bpy_props.hh"
#include "bpy_rna.hh"

namespace blender {

/* Existing source-bound locator, implemented in makesrna/intern/rna_ID.cc. */
extern IDProperty **rna_ID_system_idprops(PointerRNA *ptr);

static constexpr int read_items_max = 64;
static constexpr int read_string_bytes_max = 1024;

static bool require_main_thread()
{
  if (!BLI_thread_is_main()) {
    PyErr_SetString(PyExc_RuntimeError, "Native data reads require Blender's main thread");
    return false;
  }
  return true;
}

static const PointerRNA *live_pointer(PyObject *object, const StructRNA *type, bool require_id)
{
  /* Check validity before inspecting the type; an invalidated wrapper may have no type. */
  if (!BPy_StructRNA_Check(object)) {
    PyErr_SetString(PyExc_TypeError, "Expected live Blender RNA");
    return nullptr;
  }
  if (pyrna_struct_validity_check(reinterpret_cast<BPy_StructRNA *>(object)) == -1) {
    return nullptr;
  }
  const PointerRNA *ptr = pyrna_struct_as_ptr(object, type);
  if (ptr == nullptr) {
    return nullptr;
  }
  if (ptr->data == nullptr || (require_id && ptr->owner_id == nullptr)) {
    PyErr_SetString(PyExc_TypeError, "Expected live ID-owned Blender RNA");
    return nullptr;
  }
  if (ptr->owner_id &&
      (ptr->owner_id->orig_id != nullptr ||
       (ptr->owner_id->tag & (ID_TAG_COPIED_ON_EVAL | ID_TAG_COPIED_ON_EVAL_FINAL_RESULT))))
  {
    PyErr_SetString(PyExc_ValueError, "Evaluated data is not supported by native data reads");
    return nullptr;
  }
  return ptr;
}

static PyObject *read_vertex_groups(PyObject * /*self*/, PyObject *args)
{
  PyObject *object;
  int vertex_index, offset, limit;
  if (!require_main_thread() ||
      !PyArg_ParseTuple(args, "Oiii:vertex_groups", &object, &vertex_index, &offset, &limit))
  {
    return nullptr;
  }
  const PointerRNA *ptr = live_pointer(object, RNA_Mesh, true);
  if (ptr == nullptr) {
    return nullptr;
  }
  const Mesh &mesh = *static_cast<const Mesh *>(ptr->data);
  if (vertex_index < 0 || vertex_index >= mesh.verts_num || offset < 0 || limit < 0 ||
      limit > read_items_max)
  {
    PyErr_SetString(PyExc_ValueError, "Invalid vertex index or page (offset >= 0, limit 0..64)");
    return nullptr;
  }

  /* The const accessor neither creates the layer nor detaches shared deform storage. */
  const Span<MDeformVert> vertices = mesh.deform_verts();
  const MDeformVert *vertex = vertices.is_empty() ? nullptr : &vertices[vertex_index];
  if (vertex && (vertex->totweight < 0 || (vertex->totweight && vertex->dw == nullptr))) {
    PyErr_SetString(PyExc_ValueError, "Invalid vertex-group storage");
    return nullptr;
  }
  const int length = vertex ? vertex->totweight : 0;
  const int count = offset < length ? std::min(limit, length - offset) : 0;
  PyObject *items = PyList_New(count);
  if (items == nullptr) {
    return nullptr;
  }
  for (int index = 0; index < count; index++) {
    const MDeformWeight &weight = vertex->dw[offset + index];
    PyObject *item = Py_BuildValue("(if)", weight.def_nr, weight.weight);
    if (item == nullptr) {
      Py_DECREF(items);
      return nullptr;
    }
    PyList_SET_ITEM(items, index, item);
  }
  return Py_BuildValue("{s:i,s:N,s:O}",
                       "length", length, "items", items,
                       "storage_present", vertices.is_empty() ? Py_False : Py_True);
}

static PyObject *read_image_tile_metadata(PyObject * /*self*/, PyObject *args)
{
  PyObject *object;
  int tile_number, frame = 0, view = 0;
  if (!require_main_thread() ||
      !PyArg_ParseTuple(args, "Oi|ii:image_tile_metadata", &object, &tile_number, &frame, &view))
  {
    return nullptr;
  }
  const PointerRNA *ptr = live_pointer(object, RNA_Image, true);
  if (ptr == nullptr) {
    return nullptr;
  }
  int size[2], channels;
  if (!BKE_image_cached_tile_metadata(
          static_cast<Image *>(ptr->data), tile_number, frame, view, size, &channels))
  {
    Py_RETURN_NONE;
  }
  return Py_BuildValue("{s:[ii],s:i}", "size", size[0], size[1], "channels", channels);
}

static PyObject *new_pair(PyObject *first, PyObject *second)
{
  PyObject *pair = (first && second) ? PyList_New(2) : nullptr;
  if (pair == nullptr) {
    Py_XDECREF(first);
    Py_XDECREF(second);
    return nullptr;
  }
  PyList_SET_ITEM(pair, 0, first);
  PyList_SET_ITEM(pair, 1, second);
  return pair;
}

static PyObject *read_image_metadata(PyObject *self, PyObject *object)
{
  namespace words = angelo_read_words;
  if (!require_main_thread()) {
    return nullptr;
  }
  const PointerRNA *ptr = live_pointer(object, RNA_Image, true);
  if (ptr == nullptr) {
    return nullptr;
  }
  ImageReadMetadata metadata;
  if (!BKE_image_cached_metadata(static_cast<Image *>(ptr->data), &metadata)) {
    Py_RETURN_NONE;
  }
  PyObject *record = _PyDict_NewPresized(metadata.has_buffer ? 7 : 1);
  if (record == nullptr) {
    return nullptr;
  }
  const bool built =
      (!metadata.has_buffer ||
       (words::set_item(
            record,
            self,
            words::SIZE,
            new_pair(PyLong_FromLong(metadata.size[0]), PyLong_FromLong(metadata.size[1]))) &&
        words::set_item(record, self, words::CHANNELS, PyLong_FromLong(metadata.channels)) &&
        words::set_item(record, self, words::DEPTH, PyLong_FromLong(metadata.depth)) &&
        words::set_item(record, self, words::IS_FLOAT, PyBool_FromLong(metadata.is_float)) &&
        words::set_item(record,
                        self,
                        words::RESOLUTION,
                        new_pair(PyFloat_FromDouble(metadata.resolution[0]),
                                 PyFloat_FromDouble(metadata.resolution[1]))) &&
        words::set_item(
            record, self, words::FILE_FORMAT, PyLong_FromLong(metadata.file_format)))) &&
      words::set_item(
          record, self, words::FRAME_DURATION, PyLong_FromLong(metadata.frame_duration));
  if (!built) {
    Py_DECREF(record);
    return nullptr;
  }
  return record;
}

static const char *storage_type_name(int type)
{
  switch (type) {
    case IDP_BOOLEAN:
      return "BOOLEAN";
    case IDP_INT:
      return "INT";
    case IDP_FLOAT:
      return "FLOAT";
    case IDP_DOUBLE:
      return "DOUBLE";
    case IDP_STRING:
      return "STRING";
    case IDP_ARRAY:
      return "ARRAY";
    case IDP_GROUP:
      return "GROUP";
    case IDP_ID:
      return "ID";
    case IDP_IDPARRAY:
      return "IDPARRAY";
    default:
      return "UNKNOWN";
  }
}

static bool storage_array_valid(const IDProperty &property)
{
  return property.len >= 0 && property.totallen >= property.len &&
         (property.len == 0 || property.data.pointer != nullptr);
}

static bool storage_group_valid(const IDProperty &property)
{
  return property.len >= 0 && (property.data.children_map ?
                                   property.data.children_map->children.size() == property.len :
                                   property.len == 0);
}

static PyObject *read_property_info(PyObject * /*self*/, PyObject *object)
{
  if (!require_main_thread()) {
    return nullptr;
  }
  const PointerRNA *ptr = live_pointer(object, RNA_Property, false);
  if (ptr == nullptr) {
    return nullptr;
  }
  PropertyRNA *property = static_cast<PropertyRNA *>(ptr->data);
  PyObject *result = Py_BuildValue(
      "{s:O,s:O,s:O,s:O}",
      "runtime",
      RNA_property_is_runtime(property) ? Py_True : Py_False,
      "uses_idprop_storage",
      (RNA_property_flag(property) & PROP_IDPROPERTY) ? Py_True : Py_False,
      "has_python_getter",
      BPY_rna_property_has_python_getter(property) ? Py_True : Py_False,
      "has_python_enum_items",
      BPY_rna_property_has_python_enum_items(property) ? Py_True : Py_False);
  if (result && !RNA_property_is_idprop(property) && RNA_property_type(property) == PROP_ENUM) {
    /* Reading RNA EnumProperty.default can convert through dynamic enum items. Return the
     * declaration's numeric value so the caller can map it with enum_items_static instead. */
    const int value = RNA_property_enum_get_declared_default(property);
    PyObject *value_object = PyLong_FromLong(value);
    if (value_object == nullptr || PyDict_SetItemString(result, "enum_default", value_object) < 0)
    {
      Py_XDECREF(value_object);
      Py_DECREF(result);
      return nullptr;
    }
    Py_DECREF(value_object);
  }
  return result;
}

/**
 * Address reader ABI (ADDRESS_READ_VERSION 2), used by the add-on's data_read_access for every
 * automatic read. The helpers above (vertex groups, image metadata, property info) keep their own
 * contract for the add-on's view sources and tests. stored_property, which read raw system storage
 * for the old controller, was retired in 1.0: automatic reads use stored_member here.
 */
namespace address_read {

/* Whether a C string is one of the given literals (no std::string per comparison). */
static bool one_of(const char *text, std::initializer_list<const char *> options)
{
  for (const char *option : options) {
    if (STREQ(text, option)) {
      return true;
    }
  }
  return false;
}


/* Owned transport has no RNA pointers and no Python references. Encoding is a separate phase
 * after the resolver's stack (including every iterator guard) has unwound. */
struct Value {
  enum class Kind { Null, Boolean, Integer, Float, String, Tuple, Dict } kind = Kind::Null;
  bool boolean = false;
  bool utf8_prefix = false;
  int64_t integer = 0;
  double number = 0;
  std::string text;
  bool unicode_storage = false;
  std::u32string unicode;
  std::vector<Value> sequence;
  std::vector<std::pair<std::string, Value>> fields;

  static Value string(std::string text)
  {
    Value value;
    value.kind = Kind::String;
    value.text = std::move(text);
    return value;
  }
  static Value boolean_value(bool boolean)
  {
    Value value;
    value.kind = Kind::Boolean;
    value.boolean = boolean;
    return value;
  }
  static Value int_value(int64_t integer)
  {
    Value value;
    value.kind = Kind::Integer;
    value.integer = integer;
    return value;
  }
  static Value float_value(double number)
  {
    Value value;
    value.kind = Kind::Float;
    value.number = number;
    return value;
  }
  static Value tuple(std::vector<Value> sequence = {})
  {
    Value value;
    value.kind = Kind::Tuple;
    value.sequence = std::move(sequence);
    return value;
  }
  /* Field order carries no meaning: lookups are by name and Python dicts are unordered here. */
  static Value dict(std::vector<std::pair<std::string, Value>> fields)
  {
    Value value;
    value.kind = Kind::Dict;
    value.fields = std::move(fields);
    return value;
  }
  bool operator==(const Value &other) const
  {
    return kind == other.kind && boolean == other.boolean && utf8_prefix == other.utf8_prefix &&
           integer == other.integer &&
           number == other.number && text == other.text && unicode_storage == other.unicode_storage &&
           unicode == other.unicode && sequence == other.sequence &&
           fields == other.fields;
  }
};

using Field = std::pair<std::string, Value>;

/* A dict built by moving each field in. Value::dict copies every field (and so its whole subtree)
 * out of its initializer list; result records are nested several levels deep. */
template<typename... Fields> static Value moved_dict(Fields &&...fields)
{
  Value value;
  value.kind = Value::Kind::Dict;
  value.fields.reserve(sizeof...(fields));
  (value.fields.emplace_back(std::forward<Fields>(fields)), ...);
  return value;
}

template<typename... Items> static Value moved_tuple(Items &&...items)
{
  Value value;
  value.kind = Value::Kind::Tuple;
  value.sequence.reserve(sizeof...(items));
  (value.sequence.emplace_back(std::forward<Items>(items)), ...);
  return value;
}

/* Record field names are a small closed vocabulary: reuse one interned string per name instead of
 * creating and interning one per field. Returns a new reference. */
static PyObject *record_key(const std::string &key)
{
  static PyInterpreterState *owner = nullptr;
  static std::unordered_map<std::string, PyObject *> keys;
  /* A direct-mapped front for the map: a record's few dozen names rarely collide. Slots borrow
   * the map's references, which live as long as the interpreter. */
  struct Slot {
    std::string key;
    PyObject *name = nullptr;
  };
  static Slot slots[512];
  PyInterpreterState *current = PyInterpreterState_Get();
  if (owner != current) {
    keys.clear(); /* Another interpreter never sees the previous one's objects. */
    for (Slot &slot : slots) {
      slot = {};
    }
    owner = current;
  }
  const size_t size = key.size();
  const size_t mix = size ? (size_t(uint8_t(key[0])) * 131 + size_t(uint8_t(key[size - 1])) * 31 +
                             size_t(uint8_t(key[size / 2])) * 7 + size) :
                            0;
  Slot &slot = slots[mix & 511];
  if (slot.name && slot.key == key) {
    Py_INCREF(slot.name);
    return slot.name;
  }
  if (const auto found = keys.find(key); found != keys.end()) {
    slot = {key, found->second};
    Py_INCREF(found->second);
    return found->second;
  }
  PyObject *name = PyUnicode_InternFromString(key.c_str());
  if (name && keys.size() < 4096) {
    Py_INCREF(name); /* The cache's own reference, for the interpreter's lifetime. */
    keys.emplace(key, name);
    slot = {key, name};
  }
  return name;
}

static PyObject *encode(const Value &value)
{
  switch (value.kind) {
    case Value::Kind::Null:
      Py_RETURN_NONE;
    case Value::Kind::Boolean:
      return PyBool_FromLong(value.boolean);
    case Value::Kind::Integer:
      return PyLong_FromLongLong(value.integer);
    case Value::Kind::Float:
      return PyFloat_FromDouble(value.number);
    case Value::Kind::String: {
      if (value.unicode_storage) {
        return PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, value.unicode.data(),
                                         value.unicode.size());
      }
      /* Native prefixes may end mid-codepoint; preserve the existing bounded UTF-8 behavior. */
      Py_ssize_t consumed;
      return value.utf8_prefix ? PyUnicode_DecodeUTF8Stateful(
                                     value.text.data(), value.text.size(), "strict", &consumed) :
                                 PyUnicode_DecodeUTF8(value.text.data(), value.text.size(), "strict");
    }
    case Value::Kind::Tuple: {
      PyObject *result = PyTuple_New(value.sequence.size());
      if (!result) {
        return nullptr;
      }
      for (size_t index = 0; index < value.sequence.size(); index++) {
        PyObject *item = encode(value.sequence[index]);
        if (!item) {
          Py_DECREF(result);
          return nullptr;
        }
        PyTuple_SET_ITEM(result, index, item);
      }
      return result;
    }
    case Value::Kind::Dict: {
      PyObject *result = PyDict_New();
      if (!result) {
        return nullptr;
      }
      for (const auto &[key, field] : value.fields) {
        PyObject *item = encode(field);
        PyObject *name = item ? record_key(key) : nullptr;
        if (!name || PyDict_SetItem(result, name, item) < 0) {
          Py_XDECREF(name);
          Py_XDECREF(item);
          Py_DECREF(result);
          return nullptr;
        }
        Py_DECREF(name);
        Py_DECREF(item);
      }
      return result;
    }
  }
  BLI_assert_unreachable();
  return nullptr;
}

/* Input parsing runs before the native borrow phase. No coercion, descriptors, mapping hooks
 * or sequence iteration may occur. A closed schema is checked after this owned copy. */
static bool parse_value(PyObject *object, Value &value, int depth = 0)
{
  if (depth > 12) {
    PyErr_SetString(PyExc_ValueError, "Read transport nesting exceeds its closed schema");
    return false;
  }
  if (object == Py_None) {
    value = {};
  }
  else if (PyBool_Check(object)) {
    value = Value::boolean_value(object == Py_True);
  }
  else if (PyLong_CheckExact(object)) {
    value = Value::int_value(PyLong_AsLongLong(object));
    if (PyErr_Occurred()) {
      return false;
    }
  }
  else if (PyUnicode_CheckExact(object)) {
    Py_ssize_t length;
    const char *bytes = PyUnicode_AsUTF8AndSize(object, &length);
    if (!bytes) {
      return false;
    }
    if (memchr(bytes, '\0', length)) {
      PyErr_SetString(PyExc_ValueError, "Read transport strings cannot contain NUL");
      return false;
    }
    value = Value::string(std::string(bytes, length));
  }
  else if (PyTuple_CheckExact(object)) {
    value = Value::tuple();
    for (Py_ssize_t index = 0; index < PyTuple_GET_SIZE(object); index++) {
      Value item;
      if (!parse_value(PyTuple_GET_ITEM(object, index), item, depth + 1)) {
        return false;
      }
      value.sequence.push_back(std::move(item));
    }
  }
  else if (PyDict_CheckExact(object)) {
    value.kind = Value::Kind::Dict;
    Py_ssize_t position = 0;
    PyObject *key, *item;
    while (PyDict_Next(object, &position, &key, &item)) {
      Value owned_key, owned_item;
      if (!PyUnicode_CheckExact(key) || !parse_value(key, owned_key, depth + 1) ||
          !parse_value(item, owned_item, depth + 1))
      {
        if (!PyErr_Occurred()) {
          PyErr_SetString(PyExc_TypeError, "Read transport keys must be exact strings");
        }
        return false;
      }
      value.fields.emplace_back(std::move(owned_key.text), std::move(owned_item));
    }
    /* Dict insertion order is not projection authority. Normalize before comparing copies. */
    std::sort(value.fields.begin(), value.fields.end(), [](const auto &a, const auto &b) {
      return a.first < b.first;
    });
  }
  else {
    PyErr_SetString(PyExc_TypeError, "Read transport requires exact built-in values");
    return false;
  }
  return true;
}

struct Step {
  enum class Kind { Attribute, Index, Key } kind;
  std::string text;
  int64_t index = 0;
};
struct Address {
  bool data_root = false;
  std::vector<Step> parsed;
  std::vector<Step> expansion;
};

static Step::Kind address_step_kind(const Address &address, int index)
{
  return index < int(address.parsed.size()) ? address.parsed[index].kind :
                                              address.expansion[index - address.parsed.size()].kind;
}

[[maybe_unused]] static bool parse_address(PyObject *object, Address &address)
{
  /* Bound the address container before making any recursive owned transport copies. */
  bool shape = PyTuple_CheckExact(object) && PyTuple_GET_SIZE(object) == 3;
  if (shape) {
    for (int group = 1; group <= 2; group++) {
      PyObject *steps = PyTuple_GET_ITEM(object, group);
      shape = shape && PyTuple_CheckExact(steps) &&
              PyTuple_GET_SIZE(steps) <= (group == 1 ? 16 : 2);
      if (!shape) {
        break;
      }
      for (Py_ssize_t index = 0; index < PyTuple_GET_SIZE(steps); index++) {
        PyObject *step = PyTuple_GET_ITEM(steps, index);
        shape = PyTuple_CheckExact(step) && PyTuple_GET_SIZE(step) == 2;
        if (!shape) {
          break;
        }
      }
    }
  }
  if (!shape) {
    PyErr_SetString(PyExc_ValueError, "Invalid closed read address");
    return false;
  }
  Value owned;
  if (!parse_value(object, owned)) {
    return false;
  }
  const auto tuple_size = [](const Value &value, size_t size) {
    return value.kind == Value::Kind::Tuple && value.sequence.size() == size;
  };
  bool valid = tuple_size(owned, 3) && owned.sequence[0].kind == Value::Kind::String &&
               ELEM(owned.sequence[0].text, "scene", "data");
  if (valid) {
    address.data_root = owned.sequence[0].text == "data";
    for (int group = 1; group <= 2 && valid; group++) {
      const Value &steps = owned.sequence[group];
      valid = steps.kind == Value::Kind::Tuple && steps.sequence.size() <= (group == 1 ? 16 : 2);
      if (!valid) {
        break;
      }
      for (const Value &part : steps.sequence) {
        valid = tuple_size(part, 2) && part.sequence[0].kind == Value::Kind::String;
        if (!valid) {
          break;
        }
        const std::string &kind = part.sequence[0].text;
        const Value &argument = part.sequence[1];
        Step step;
        if (kind == "index") {
          valid = argument.kind == Value::Kind::Integer &&
                  (group == 1 ? argument.integer >= -9999999 && argument.integer <= 9999999 :
                                argument.integer >= 0 && argument.integer <= 10000063);
          step.kind = Step::Kind::Index;
          step.index = argument.integer;
        }
        else if (kind == "attribute" || (group == 1 && kind == "key")) {
          valid = argument.kind == Value::Kind::String && !argument.text.empty();
          step.kind = kind == "attribute" ? Step::Kind::Attribute : Step::Kind::Key;
          step.text = argument.text;
          if (valid && group == 1 && kind == "attribute") {
            valid = step.text.size() <= 64 && step.text.front() != '_' &&
                    ((step.text.front() >= 'A' && step.text.front() <= 'Z') ||
                     (step.text.front() >= 'a' && step.text.front() <= 'z'));
            for (char byte : step.text) {
              valid = valid && ((byte >= 'A' && byte <= 'Z') || (byte >= 'a' && byte <= 'z') ||
                                (byte >= '0' && byte <= '9') || byte == '_');
            }
          }
          if (valid && kind == "key") {
            /* Public parser owns Unicode printability; exact UTF-8 input was already validated.
             * The byte cap is deliberately not used as a Unicode character cap. */
            PyObject *original = PyTuple_GET_ITEM(PyTuple_GET_ITEM(object, group),
                                                (&part - steps.sequence.data()));
            PyObject *text = PyTuple_GET_ITEM(original, 1);
            const Py_ssize_t length = PyUnicode_GET_LENGTH(text);
            valid = length <= 255;
            for (Py_ssize_t index = 0; valid && index < length; index++) {
              valid = Py_UNICODE_ISPRINTABLE(PyUnicode_READ_CHAR(text, index));
            }
          }
        }
        else {
          valid = false;
        }
        if (!valid) {
          break;
        }
        (group == 1 ? address.parsed : address.expansion).push_back(std::move(step));
      }
    }
  }
  if (!valid) {
    PyErr_SetString(PyExc_ValueError, "Invalid closed read address");
  }
  return valid;
}

/* Context holds integers only. Never dereference a saved candidate to test whether it is live. */
struct Context {
  uintptr_t main = 0;
  uintptr_t manager = 0;
  unsigned int manager_uid = 0;
  uintptr_t window = 0;
  int window_id = 0;
  uintptr_t scene = 0;
  unsigned int scene_uid = 0;
  double deadline = 0;
  bool closed = false;
  enum class InitialExpiry { None, Document, Window, Scene } initial_expiry = InitialExpiry::None;
  /* Declarations keys this read computed, by (type, owner bits). A key only names a cached
   * description list; every use of a declaration is still admitted by its own fingerprint. */
  struct KeyHash {
    size_t operator()(const std::pair<const void *, uint64_t> &key) const
    {
      const uint64_t mixed = (uint64_t(uintptr_t(key.first)) * 0x9e3779b97f4a7c15ULL) ^
                             (key.second * 0xbf58476d1ce4e5b9ULL);
      return size_t(mixed ^ (mixed >> 29));
    }
  };
  mutable std::unordered_map<std::pair<const void *, uint64_t>, int64_t, KeyHash> declaration_keys;
};
static constexpr const char *capsule_name = "angelo.read_context.v1";

static const char *validate_context(const Context &context)
{
  if (context.closed) {
    return "CLOSED";
  }
  switch (context.initial_expiry) {
    case Context::InitialExpiry::Document:
      return "DOCUMENT";
    case Context::InitialExpiry::Window:
      return "WINDOW";
    case Context::InitialExpiry::Scene:
      return "SCENE";
    case Context::InitialExpiry::None:
      break;
  }
  Main *main = G_MAIN;
  if (!main || uintptr_t(main) != context.main) {
    return "DOCUMENT";
  }
  wmWindow *window = nullptr;
  for (wmWindowManager &manager : main->wm) {
    if (uintptr_t(&manager) != context.manager || manager.id.session_uid != context.manager_uid) {
      continue;
    }
    for (wmWindow &candidate : manager.windows) {
      if (uintptr_t(&candidate) == context.window && candidate.winid == context.window_id) {
        window = &candidate;
        break;
      }
    }
    break;
  }
  if (!window) {
    return "WINDOW";
  }
  for (Scene &scene : main->scenes) {
    if (uintptr_t(&scene) == context.scene && scene.id.session_uid == context.scene_uid) {
      return window->scene == &scene ? nullptr : "SCENE";
    }
  }
  return "SCENE";
}

static void destroy_context(PyObject *capsule)
{
  /* Capsule is created only here. Destruction touches owned memory, never Blender state. */
  delete static_cast<Context *>(PyCapsule_GetPointer(capsule, capsule_name));
}

[[maybe_unused]] static PyObject *begin(PyObject *window_object, PyObject *seconds)
{
  if (!require_main_thread()) {
    return nullptr;
  }
  if (!PyFloat_CheckExact(seconds)) {
    PyErr_SetString(PyExc_TypeError, "Read budget must be an exact float");
    return nullptr;
  }
  const double budget = PyFloat_AS_DOUBLE(seconds);
  if (!std::isfinite(budget) || budget <= 0 || budget > 2) {
    PyErr_SetString(PyExc_ValueError, "Read budget must be finite and in (0, 2]");
    return nullptr;
  }
  if (!BPy_StructRNA_Check(window_object)) {
    PyErr_SetString(PyExc_TypeError, "Read context requires a Window receiver");
    return nullptr;
  }
  uintptr_t candidate_address = 0;
  {
    const BPy_StructRNA *receiver = reinterpret_cast<BPy_StructRNA *>(window_object);
    if (receiver->ptr && receiver->ptr->type == RNA_Window) {
      candidate_address = uintptr_t(receiver->ptr->data);
    }
  }
  Context context;
  context.initial_expiry = G_MAIN ? Context::InitialExpiry::Window : Context::InitialExpiry::Document;
  {
    Main *main = G_MAIN;
    if (main && candidate_address) {
      for (wmWindowManager &manager : main->wm) {
        for (wmWindow &window : manager.windows) {
          if (uintptr_t(&window) != candidate_address) {
            continue;
          }
          context.initial_expiry = Context::InitialExpiry::Scene;
          for (Scene &scene : main->scenes) {
            if (window.scene == &scene) {
              context.initial_expiry = Context::InitialExpiry::None;
              context.main = uintptr_t(main);
              context.manager = uintptr_t(&manager);
              context.manager_uid = manager.id.session_uid;
              context.window = uintptr_t(&window);
              context.window_id = window.winid;
              context.scene = uintptr_t(&scene);
              context.scene_uid = scene.id.session_uid;
            }
          }
        }
      }
    }
  }
  context.deadline = BLI_time_now_seconds() + budget;
  std::unique_ptr<Context> owned = std::make_unique<Context>(context);
  PyObject *capsule = PyCapsule_New(owned.get(), capsule_name, destroy_context);
  if (capsule) {
    owned.release();
  }
  return capsule;
}

static Context *get_context(PyObject *capsule)
{
  if (!PyCapsule_CheckExact(capsule) || !PyCapsule_IsValid(capsule, capsule_name) ||
      PyCapsule_GetDestructor(capsule) != destroy_context) {
    PyErr_SetString(PyExc_TypeError, "Expected owned angelo.read_context.v1 capsule");
    return nullptr;
  }
  return static_cast<Context *>(PyCapsule_GetPointer(capsule, capsule_name));
}

[[maybe_unused]] static PyObject *check(PyObject *capsule)
{
  if (!require_main_thread()) {
    return nullptr;
  }
  const Context *context = get_context(capsule);
  if (!context) {
    return nullptr;
  }
  const char *reason = validate_context(*context);
  Value result = reason ? Value::dict({{"status", Value::string("EXPIRED")},
                                      {"reason", Value::string(reason)}}) :
                          Value::dict({{"status", Value::string("OK")},
                                       {"budget_exhausted", Value::boolean_value(
                                                                BLI_time_now_seconds() >=
                                                                context->deadline)}});
  return encode(result);
}

[[maybe_unused]] static PyObject *close(PyObject *capsule)
{
  Context *context = get_context(capsule);
  if (!context) {
    return nullptr;
  }
  context->closed = true;
  Py_RETURN_NONE;
}

static std::string enum_identifier(const EnumPropertyItem *items, int value)
{
  for (; items->identifier; items++) {
    if (items->value == value && items->identifier[0]) {
      return items->identifier;
    }
  }
  return {};
}

static Value type_info(const StructRNA *type)
{
  if (!type) {
    return {};
  }
  std::vector<Value> bases;
  for (const StructRNA *base = type->base; base && bases.size() < 15; base = base->base) {
    bases.push_back(Value::string(base->identifier));
  }
  return moved_dict(Field("identifier", Value::string(type->identifier)),
                    Field("bases", Value::tuple(std::move(bases))),
                    Field("name_property", type->nameproperty ?
                                               Value::string(type->nameproperty->identifier) :
                                               Value{}));
}

static Value type_key(const StructRNA *type)
{
  if (!type) {
    return {};
  }
  std::vector<Value> bases;
  for (const StructRNA *base = type->base; base && bases.size() < 15; base = base->base) {
    bases.push_back(Value::string(base->identifier));
  }
  return moved_tuple(Value::string(type->identifier), Value::tuple(std::move(bases)));
}

struct Owner {
  bool exists = false;
  std::string id_type;
  bool original = false;
  bool bound_scene = false;
  bool self = false;
};

static Value address_value(const Address &address)
{
  auto steps = [](const std::vector<Step> &source) {
    std::vector<Value> result;
    result.reserve(source.size());
    for (const Step &step : source) {
      const char *kind = step.kind == Step::Kind::Attribute ? "attribute" :
                         step.kind == Step::Kind::Index ? "index" : "key";
      result.push_back(moved_tuple(Value::string(kind), step.kind == Step::Kind::Index ?
                                                            Value::int_value(step.index) :
                                                            Value::string(step.text)));
    }
    return Value::tuple(std::move(result));
  };
  return moved_tuple(Value::string(address.data_root ? "data" : "scene"), steps(address.parsed),
                     steps(address.expansion));
}

/* This first route family is bound to rna_ID_system_idprops in rna_ID.cc. It returns the
 * existing ID::system_properties slot; no arbitrary StructRNA locator is ever called here.
 * Other native/generated locator families require their own source binding before activation. */
static bool id_storage_route(const StructRNA *type)
{
  return type->system_idproperties == rna_ID_system_idprops;
}

static Value property_description(const StructRNA *type,
                                  const PropertyRNA &property,
                                  const Owner &owner,
                                  bool data_root)
{
  const StructRNA *fixed_type = property.type == PROP_POINTER ?
                                    static_cast<const PointerPropertyRNA &>(property).pointer_type :
                                property.type == PROP_COLLECTION ?
                                    static_cast<const CollectionPropertyRNA &>(property).item_type :
                                    nullptr;
  const StructRNA *collection_type = property.type == PROP_COLLECTION ? property.srna : nullptr;
  Value dimensions = moved_tuple(Value::int_value(property.totarraylength), Value::int_value(0),
                                 Value::int_value(0));
  if (property.arraydimension > 1) {
    for (int index = 0; index < 3; index++) {
      dimensions.sequence[index] = Value::int_value(property.arraylength[index]);
    }
  }
  const bool storage = property.flag & PROP_IDPROPERTY;
  const bool locator = storage && owner.exists && id_storage_route(type);
  const char *route = locator ? "ID_SYSTEM_STORAGE" : "UNSUPPORTED";
  /* Moved, not copied (Value::dict copies its initializer list): one description per property. */
  Value routes = moved_dict(Field("value", Value::string(route)),
                            Field("length", Value::string("UNSUPPORTED")),
                            Field("refine", Value::string("UNSUPPORTED")),
                            Field("iterate", Value::string("UNSUPPORTED")),
                            Field("lookup_index", Value::string("UNSUPPORTED")),
                            Field("lookup_name", Value::string("UNSUPPORTED")),
                            Field("enum_value_items", Value::string("UNSUPPORTED")),
                            Field("enum_decl_items", Value::string("UNSUPPORTED")),
                            Field("storage_locator", Value::string(route)));
  Value owner_part = moved_dict(Field("type", type_key(type)),
                                Field("is_data_root", Value::boolean_value(data_root)),
                                Field("is_id", Value::boolean_value(type->flag & STRUCT_ID)),
                                Field("owner_exists", Value::boolean_value(owner.exists)),
                                Field("owner_id_type", owner.exists ? Value::string(owner.id_type) : Value{}),
                                Field("owner_is_original", Value::boolean_value(owner.original)),
                                Field("owner_is_bound_scene", Value::boolean_value(owner.bound_scene)),
                                Field("virtual_kind", Value::string("NONE")));
  Value declaration = moved_dict(
      Field("identifier", Value::string(property.identifier)),
      Field("type", Value::string(enum_identifier(rna_enum_property_type_items, property.type))),
      Field("subtype", Value::string(enum_identifier(rna_enum_property_subtype_items, property.subtype))),
      Field("name", Value::string(property.name ? property.name : "")),
      Field("is_runtime", Value::boolean_value(property.flag_internal & PROP_INTERN_RUNTIME)),
      Field("is_deprecated", Value::boolean_value(property.deprecated != nullptr)),
      Field("is_readonly", Value::boolean_value(!(property.flag & PROP_EDITABLE))),
      Field("is_enum_flag", Value::boolean_value(property.flag & PROP_ENUM_FLAG)),
      Field("array_length", Value::int_value(property.totarraylength)),
      Field("array_dimensions", std::move(dimensions)),
      Field("has_dynamic_length", Value::boolean_value(property.getlength != nullptr)),
      Field("fixed_type", type_key(fixed_type)),
      Field("collection_type", type_key(collection_type)),
      Field("uses_idprop_storage", Value::boolean_value(storage)),
      Field("has_python_getter", Value::boolean_value(BPY_rna_property_has_python_getter(
                                     const_cast<PropertyRNA *>(&property)))),
      Field("has_python_enum_items", Value::boolean_value(BPY_rna_property_has_python_enum_items(
                                         const_cast<PropertyRNA *>(&property)))));
  Value projection = moved_tuple(std::move(owner_part), std::move(declaration), std::move(routes));
  Value fixed_name;
  if (fixed_type && fixed_type->nameproperty) {
    const PropertyRNA &name = *fixed_type->nameproperty;
    fixed_name = moved_dict(
        Field("identifier", Value::string(name.identifier)),
        Field("type", Value::string(enum_identifier(rna_enum_property_type_items, name.type))),
        Field("subtype", Value::string(enum_identifier(rna_enum_property_subtype_items, name.subtype))));
  }
  return moved_dict(Field("projection", std::move(projection)),
                    Field("fixed_type", type_info(fixed_type)),
                    Field("collection_type", type_info(collection_type)),
                    Field("fixed_name", std::move(fixed_name)));
}

/* Mirrors rna_Struct_properties_begin/next inheritance order and builtin filtering directly.
 * The user-IDProperty tail from that iterator is intentionally outside the read grammar. */
static std::vector<const PropertyRNA *> declarations(const StructRNA *type)
{
  std::vector<const StructRNA *> hierarchy;
  for (const StructRNA *current = type; current; current = current->base) {
    hierarchy.push_back(current);
  }
  std::vector<const PropertyRNA *> result;
  for (auto current = hierarchy.rbegin(); current != hierarchy.rend(); ++current) {
    for (const PropertyRNA &property : (*current)->cont.properties) {
      if (!(property.flag_internal & PROP_INTERN_BUILTIN)) {
        result.push_back(&property);
      }
    }
  }
  return result;
}

[[maybe_unused]] static Value missing(const char *kind, int edge)
{
  return Value::dict({{"status", Value::string("MISSING")},
                      {"kind", Value::string(kind)}, {"edge", Value::int_value(edge)},
                      {"length", {}}, {"member", {}}});
}

/* Validate copied UTF-8 with Python's strict scalar-value rules. A deliberately bounded prefix
 * may end in an otherwise valid incomplete codepoint; trim only that final incomplete character.
 * Unlike general BLI string validators, embedded NUL remains a valid copied Unicode codepoint. */
static bool validate_owned_utf8(Value &value)
{
  const bool prefix = value.utf8_prefix;
  size_t index = 0;
  while (index < value.text.size()) {
    const unsigned char lead = value.text[index];
    if (lead < 0x80) {
      index++;
      continue;
    }
    const int width = lead >= 0xc2 && lead <= 0xdf ? 2 :
                      lead >= 0xe0 && lead <= 0xef ? 3 :
                      lead >= 0xf0 && lead <= 0xf4 ? 4 : 0;
    if (!width) {
      return false;
    }
    const size_t available = std::min<size_t>(width, value.text.size() - index);
    for (size_t tail = 1; tail < available; tail++) {
      const unsigned char byte = value.text[index + tail];
      if (byte < 0x80 || byte > 0xbf ||
          (tail == 1 && ((lead == 0xe0 && byte < 0xa0) ||
                        (lead == 0xed && byte >= 0xa0) ||
                        (lead == 0xf0 && byte < 0x90) ||
                        (lead == 0xf4 && byte >= 0x90))))
      {
        return false;
      }
    }
    if (available != size_t(width)) {
      if (!prefix) {
        return false;
      }
      value.text.resize(index);
      break;
    }
    index += width;
  }
  value.utf8_prefix = false;
  return true;
}

/* Raw storage copying is independent of transport allocation. The resolver must retain the
 * admitted owner/virtual-container lifetime until this returns, then end all borrows before encode.
 * Stored IDs need address-target resolution; this copy helper deliberately does not wrap them. */
[[maybe_unused]] static Value copy_storage(const IDProperty *property)
{
  const char *state = property ? "VALUE" : "ABSENT";
  Value value, subtype, length;
  if (property) {
    switch (property->type) {
      case IDP_BOOLEAN:
        value = Value::boolean_value(IDP_bool_get(property));
        break;
      case IDP_INT:
        value = Value::int_value(IDP_int_get(property));
        break;
      case IDP_FLOAT:
        value = Value::float_value(IDP_float_get(property));
        break;
      case IDP_DOUBLE:
        value = Value::float_value(IDP_double_get(property));
        break;
      case IDP_STRING:
        if (property->subtype != IDP_STRING_SUB_UTF8 || property->len < 1 ||
            property->totallen < property->len || !property->data.pointer)
        {
          state = "INVALID";
          break;
        }
        length = Value::int_value(property->len - 1);
        value = Value::string(std::string(IDP_string_get(property),
                                         std::min(property->len - 1, read_string_bytes_max)));
        value.utf8_prefix = property->len - 1 > read_string_bytes_max;
        if (!validate_owned_utf8(value)) {
          value = {};
          state = "INVALID";
        }
        break;
      case IDP_ARRAY: {
        if (!storage_array_valid(*property) ||
            !ELEM(property->subtype, IDP_BOOLEAN, IDP_INT, IDP_FLOAT, IDP_DOUBLE))
        {
          state = "INVALID";
          break;
        }
        subtype = Value::string(storage_type_name(property->subtype));
        length = Value::int_value(property->len);
        std::vector<Value> copied;
        for (int index = 0; index < std::min(property->len, read_items_max); index++) {
          switch (property->subtype) {
            case IDP_BOOLEAN:
              copied.push_back(Value::boolean_value(IDP_array_bool_get(property)[index]));
              break;
            case IDP_INT:
              copied.push_back(Value::int_value(IDP_array_int_get(property)[index]));
              break;
            case IDP_FLOAT:
              copied.push_back(Value::float_value(IDP_array_float_get(property)[index]));
              break;
            case IDP_DOUBLE:
              copied.push_back(Value::float_value(IDP_array_double_get(property)[index]));
              break;
          }
        }
        value = Value::tuple(std::move(copied));
        break;
      }
      case IDP_GROUP:
        state = storage_group_valid(*property) ? "GROUP" : "INVALID";
        break;
      case IDP_IDPARRAY:
        state = storage_array_valid(*property) ? "COLLECTION" : "INVALID";
        if (STREQ(state, "COLLECTION")) {
          length = Value::int_value(property->len);
        }
        break;
      case IDP_ID:
        /* A non-null ID cannot be represented safely without admitted fresh target resolution. */
        if (IDP_ID_get(property)) {
          return Value::dict({{"status", Value::string("UNAVAILABLE")},
                              {"code", Value::string("NATIVE_READER_REQUIRED")}});
        }
        break;
      default:
        state = "INVALID";
        break;
    }
  }
  return Value::dict({{"state", Value::string(state)},
                      {"type", Value::string(property ? storage_type_name(property->type) : "NONE")},
                      {"subtype", std::move(subtype)}, {"length", std::move(length)},
                      {"value", std::move(value)}, {"default", {}}, {"default_array", {}},
                      {"enum_default", {}}, {"enum_items_static", {}}});
}

[[maybe_unused]] static Value copy_weights(const Mesh &mesh,
                                           int vertex_index,
                                           int offset,
                                           int limit)
{
  /* The caller validates current owner/type/admission and page bounds before borrowing. */
  const Span<MDeformVert> vertices = mesh.deform_verts();
  const MDeformVert *vertex = vertices.is_empty() ? nullptr : &vertices[vertex_index];
  if (vertex && (vertex->totweight < 0 || (vertex->totweight && !vertex->dw))) {
    return Value::dict({{"status", Value::string("UNAVAILABLE")},
                        {"code", Value::string("INVALID_STORAGE")}});
  }
  int length = vertex ? vertex->totweight : 0;
  int count = offset < length ? std::min(limit, length - offset) : 0;
  std::vector<Value> copied;
  for (int index = 0; index < count; index++) {
    const MDeformWeight &weight = vertex->dw[offset + index];
    copied.push_back(Value::tuple({Value::int_value(weight.def_nr),
                                  Value::float_value(weight.weight)}));
  }
  return Value::dict({{"length", Value::int_value(length)},
                      {"items", Value::tuple(std::move(copied))},
                      {"storage_present", Value::boolean_value(!vertices.is_empty())}});
}

[[maybe_unused]] static Value copy_image_metadata(Image &image)
{
  ImageReadMetadata metadata;
  if (!BKE_image_cached_metadata(&image, &metadata)) {
    return {};
  }
  std::vector<std::pair<std::string, Value>> fields = {
      {"frame_duration", Value::int_value(metadata.frame_duration)}};
  if (metadata.has_buffer) {
    fields.emplace_back("size", Value::tuple({Value::int_value(metadata.size[0]),
                                              Value::int_value(metadata.size[1])}));
    fields.emplace_back("channels", Value::int_value(metadata.channels));
    fields.emplace_back("depth", Value::int_value(metadata.depth));
    fields.emplace_back("is_float", Value::boolean_value(metadata.is_float));
    fields.emplace_back("resolution", Value::tuple({Value::float_value(metadata.resolution[0]),
                                                    Value::float_value(metadata.resolution[1])}));
    fields.emplace_back("file_format", Value::int_value(metadata.file_format));
  }
  return Value::dict(std::move(fields));
}

[[maybe_unused]] static Value copy_tile_metadata(Image &image, int current_tile_number)
{
  int size[2], channels;
  if (!BKE_image_cached_tile_metadata(&image, current_tile_number, 0, 0, size, &channels)) {
    return {};
  }
  return Value::dict({{"size", Value::tuple({Value::int_value(size[0]), Value::int_value(size[1])})},
                      {"channels", Value::int_value(channels)}});
}

static Value &field(Value &value, const char *identifier)
{
  auto found = std::find_if(value.fields.begin(), value.fields.end(), [&](const auto &item) {
    return item.first == identifier;
  });
  BLI_assert(found != value.fields.end());
  return found->second;
}

/* Defaults are raw declaration data, never get_default/get_default_array callbacks.
 * This is called only after fresh terminal projection comparison for an ABSENT storage field. */
[[maybe_unused]] static void copy_absent_defaults(const PropertyRNA &property, Value &raw)
{
  BLI_assert(field(raw, "state").text == "ABSENT");
  Value scalar;
  std::vector<Value> array;
  const int count = std::min<int>(property.totarraylength, read_items_max);
  switch (property.type) {
    case PROP_BOOLEAN: {
      const BoolPropertyRNA &decl = static_cast<const BoolPropertyRNA &>(property);
      scalar = Value::boolean_value(decl.defaultvalue);
      for (int index = 0; index < count; index++) {
        array.push_back(Value::boolean_value(decl.defaultarray ? decl.defaultarray[index] :
                                                                decl.defaultvalue));
      }
      break;
    }
    case PROP_INT: {
      const IntPropertyRNA &decl = static_cast<const IntPropertyRNA &>(property);
      scalar = Value::int_value(decl.defaultvalue);
      for (int index = 0; index < count; index++) {
        array.push_back(Value::int_value(decl.defaultarray ? decl.defaultarray[index] :
                                                            decl.defaultvalue));
      }
      break;
    }
    case PROP_FLOAT: {
      const FloatPropertyRNA &decl = static_cast<const FloatPropertyRNA &>(property);
      scalar = Value::float_value(decl.defaultvalue);
      for (int index = 0; index < count; index++) {
        array.push_back(Value::float_value(decl.defaultarray ? decl.defaultarray[index] :
                                                              decl.defaultvalue));
      }
      break;
    }
    case PROP_STRING: {
      const char *text = static_cast<const StringPropertyRNA &>(property).defaultvalue;
      scalar = Value::string(text ? std::string(text, strnlen(text, read_string_bytes_max)) : "");
      scalar.utf8_prefix = text && scalar.text.size() == read_string_bytes_max;
      break;
    }
    case PROP_ENUM:
      field(raw, "enum_default") = Value::int_value(
          static_cast<const EnumPropertyRNA &>(property).defaultvalue);
      break;
    default:
      break;
  }
  if (property.totarraylength && ELEM(property.type, PROP_BOOLEAN, PROP_INT, PROP_FLOAT)) {
    field(raw, "default_array") = Value::tuple(std::move(array));
  }
  else {
    field(raw, "default") = std::move(scalar);
  }
}

/* Full static mapping is copied when admitted enum conversion needs it. The caller pages
 * display choices separately; a 64-choice display cap cannot truncate value/flag mapping. */
[[maybe_unused]] static Value copy_static_enum(const EnumPropertyRNA &property)
{
  std::vector<Value> items;
  if (property.item) {
    for (int index = 0; index < property.totitem; index++) {
      const EnumPropertyItem &item = property.item[index];
      if (!item.identifier) {
        break;
      }
      if (!item.identifier[0]) {
        continue;
      }
      items.push_back(Value::tuple({Value::string(item.identifier),
                                   Value::string(item.name ? item.name : ""),
                                   Value::int_value(item.value)}));
    }
  }
  return Value::tuple(std::move(items));
}

static const Value *find_field(const Value &value, const char *identifier)
{
  if (value.kind != Value::Kind::Dict) {
    return nullptr;
  }
  for (const auto &[key, field] : value.fields) {
    if (key == identifier) {
      return &field;
    }
  }
  return nullptr;
}

/* Admitted declarations by fingerprint: per attribute edge of the address, and collection name
 * declarations. A fingerprint grants nothing by itself; native compares it with the current one. */
struct Allowance {
  std::vector<std::pair<int, int64_t>> edges;
  std::vector<int64_t> names;
};

/* An exact non-negative Python int that fits a fingerprint. */
static bool exact_fingerprint(PyObject *object, int64_t &value)
{
  if (!PyLong_CheckExact(object)) {
    return false;
  }
  const long long parsed = PyLong_AsLongLong(object);
  if (parsed == -1 && PyErr_Occurred()) {
    PyErr_Clear();
    return false;
  }
  if (parsed < 0) {
    return false;
  }
  value = parsed;
  return true;
}

static bool parse_allowance(PyObject *object, const Address &address, Allowance &allowance)
{
  const int step_count = address.parsed.size() + address.expansion.size();
  bool valid = PyTuple_CheckExact(object) && PyTuple_GET_SIZE(object) == 2 &&
               PyTuple_CheckExact(PyTuple_GET_ITEM(object, 0)) &&
               PyTuple_CheckExact(PyTuple_GET_ITEM(object, 1));
  PyObject *edges = valid ? PyTuple_GET_ITEM(object, 0) : nullptr;
  PyObject *names = valid ? PyTuple_GET_ITEM(object, 1) : nullptr;
  valid = valid && PyTuple_GET_SIZE(edges) <= step_count && PyTuple_GET_SIZE(names) <= 4000;
  int previous = 0;
  for (Py_ssize_t index = 0; valid && index < PyTuple_GET_SIZE(edges); index++) {
    PyObject *entry = PyTuple_GET_ITEM(edges, index);
    int64_t count = 0, admitted = 0;
    valid = PyTuple_CheckExact(entry) && PyTuple_GET_SIZE(entry) == 2 &&
            exact_fingerprint(PyTuple_GET_ITEM(entry, 0), count) && count > previous &&
            count <= step_count && exact_fingerprint(PyTuple_GET_ITEM(entry, 1), admitted);
    if (!valid) {
      break;
    }
    previous = int(count);
    if (address_step_kind(address, previous - 1) != Step::Kind::Attribute) {
      valid = false;
      break;
    }
    allowance.edges.emplace_back(previous, admitted);
  }
  for (Py_ssize_t index = 0; valid && index < PyTuple_GET_SIZE(names); index++) {
    int64_t admitted = 0;
    valid = exact_fingerprint(PyTuple_GET_ITEM(names, index), admitted);
    if (valid) {
      allowance.names.push_back(admitted);
    }
  }
  if (!valid) {
    PyErr_SetString(PyExc_ValueError, "Invalid closed read allowance");
  }
  return valid;
}

static Value unavailable(const char *code)
{
  return moved_dict(Field("status", Value::string("UNAVAILABLE")), Field("code", Value::string(code)));
}

static Value success(const char *kind, Value payload)
{
  return moved_dict(Field("status", Value::string("OK")), Field("kind", Value::string(kind)),
                    Field("payload", std::move(payload)));
}

static Value admission(const Address &address, int edge, Value property)
{
  return Value::dict({{"status", Value::string("NEEDS_ADMISSION")},
                      {"address", address_value(address)}, {"edge", Value::int_value(edge)},
                      {"descriptions", Value::tuple({std::move(property)})}});
}

static Value stale(const Address &address, int edge)
{
  return Value::dict({{"status", Value::string("STALE_ADMISSION")},
                      {"address", address_value(address)}, {"edge", Value::int_value(edge)}});
}

/* How one declaration's value, length, items and choices are read. Route labels are part of the
 * copied projection that the Python caller hands back for admission; they never grant authority
 * by themselves (native recomputes them on every operation). See source_route_facts. */
struct RouteFacts {
  /* Precondition under which a guarded built-in callback runs; checked at every invocation. */
  const char *guard = "NONE";
  /* Refusal code of a catalogued callback that never runs (null when not withheld). */
  const char *withheld = nullptr;
  const char *value = "UNSUPPORTED";
  bool object_data_special = false;
  bool material_slot_special = false;
  bool active_material_special = false;
  bool texture_output_special = false;
  bool node_socket_node_special = false;
  int registry_properties_kind = 0;
  bool bake_node_special = false;
  int bounded_array_kind = 0;
  int socket_key_kind = 0;
  int owned_collection_kind = 0;
  angelo_read_scalar::Family scalar_value = angelo_read_scalar::NONE;
  angelo_read_scalar::Family scalar_enum = angelo_read_scalar::NONE;
  angelo_read_geometry::Family geometry_scalar = angelo_read_geometry::NONE;
  angelo_read_geometry::Family geometry_collection = angelo_read_geometry::NONE;
  angelo_read_traversal::Family traversal_pointer = angelo_read_traversal::NONE;
  angelo_read_traversal::Family traversal_collection = angelo_read_traversal::NONE;
  int mesh_collection_kind = 0;
  bool attribute_elements = false;
  int const_attribute_kind = 0;
  const char *length = "NONE";
  const char *refine = "NONE";
  const char *iterate = "NONE";
  const char *lookup_index = "NONE";
  const char *lookup_name = "NONE";
  const char *enum_value_items = "NONE";
  const char *enum_decl_items = "NONE";
  const char *storage_locator = "NONE";
};

static bool callback_absent(const PropertyRNA &property, RNAReadCallbackRole role)
{
  return RNA_property_read_callback_source(&property, role).state == RNAReadCallbackState::ABSENT;
}

static bool generated_callback(const PropertyRNA &property, RNAReadCallbackRole role)
{
  const RNAReadCallbackSource source = RNA_property_read_callback_source(&property, role);
  return source.state == RNAReadCallbackState::MATCHED &&
         ELEM(source.origin, RNAReadCallbackOrigin::GENERATED_DNA,
               RNAReadCallbackOrigin::GENERATED_NOOP);
}

static bool manual_callback(const PropertyRNA &property,
                            RNAReadCallbackRole role,
                            const char *file,
                            const char *identifier)
{
  const RNAReadCallbackSource source = RNA_property_read_callback_source(&property, role);
  return source.state == RNAReadCallbackState::MATCHED &&
         source.origin == RNAReadCallbackOrigin::MANUAL && source.source_file &&
         source.source_identifier && STREQ(source.source_file, file) &&
         STREQ(source.source_identifier, identifier);
}

/* Standard iterator protocol callbacks implemented in rna_access.cc. Some catalogued families
 * are identified by their manual begin/length callbacks together with this protocol. */
static bool ordinary_iterator_callback(const PropertyRNA &property, RNAReadCallbackRole role)
{
  if (generated_callback(property, role) || callback_absent(property, role)) {
    return true;
  }
  const RNAReadCallbackSource source = RNA_property_read_callback_source(&property, role);
  if (source.state != RNAReadCallbackState::MATCHED || source.origin != RNAReadCallbackOrigin::MANUAL ||
      !source.source_identifier)
  {
    return false;
  }
  return one_of(source.source_identifier, {"rna_iterator_listbase_next",
              "rna_iterator_listbase_end", "rna_iterator_listbase_get",
              "rna_iterator_array_next", "rna_iterator_array_end", "rna_iterator_array_get",
              "rna_iterator_array_dereference_get"});
}

static bool registered_type(const StructRNA *type)
{
  return type && std::any_of(RNA_blender_rna_get().structs.begin(),
                             RNA_blender_rna_get().structs.end(),
                             [&](const std::unique_ptr<StructRNA> &candidate) {
                               return candidate.get() == type;
                             });
}

/* Refine callbacks are compiled into Blender: Python registration reuses its base type's
 * function and cannot install one. A refiner is admitted when it is one of the compiled
 * declarations' own callbacks; anything else (a stale or foreign pointer) is not called. */
static bool known_refiner(const StructRNA *type)
{
  if (!type->refine) {
    return true;
  }
  static const std::unordered_set<StructRefineFunc> compiled = []() {
    std::unordered_set<StructRefineFunc> result;
    for (const std::unique_ptr<StructRNA> &candidate : RNA_blender_rna_get().structs) {
      if (candidate->refine && !(candidate->flag & STRUCT_RUNTIME) &&
          RNA_struct_read_callback_source(candidate.get(), RNAReadCallbackRole::REFINE).state ==
              RNAReadCallbackState::MATCHED)
      {
        result.insert(candidate->refine);
      }
    }
    return result;
  }();
  return compiled.contains(type->refine);
}

static bool refiner_storage_valid(const PointerRNA &pointer)
{
  if (RNA_struct_read_callback_matches_builtin(pointer.type, RNA_Node,
                                                RNAReadCallbackRole::REFINE))
  {
    return static_cast<const bNode *>(pointer.data)->typeinfo != nullptr;
  }
  if (RNA_struct_read_callback_matches_builtin(pointer.type, RNA_NodeTree,
                                                RNAReadCallbackRole::REFINE))
  {
    return static_cast<const bNodeTree *>(pointer.data)->typeinfo != nullptr;
  }
  if (RNA_struct_read_callback_matches_builtin(pointer.type, RNA_NodeSocket,
                                                RNAReadCallbackRole::REFINE))
  {
    return static_cast<const bNodeSocket *>(pointer.data)->typeinfo != nullptr;
  }
  return true;
}

static bool refine_checked(PointerRNA &pointer)
{
  if (pointer.data && (!registered_type(pointer.type))) {
    return false;
  }
  std::unordered_set<StructRNA *> visited;
  while (pointer.data && pointer.type && pointer.type->refine) {
    if (!registered_type(pointer.type) || !known_refiner(pointer.type) ||
        !refiner_storage_valid(pointer) || !visited.insert(pointer.type).second)
    {
      return false;
    }
    StructRNA *next = pointer.type->refine(&pointer);
    /* A stored extension pointer is not authority to dereference a removed/replaced RNA type. */
    if (!registered_type(next)) {
      return false;
    }
    if (next == pointer.type) {
      return true;
    }
    pointer.type = next;
  }
  return true;
}

/* Storage of Python-defined (add-on) properties on a struct: its system IDProperty slot, found
 * through the compiled locator callback the struct inherits. A replaced callback is not called. */
/* Changed whenever an operation starts or ends; a cache valid for one operation compares it. */
static uint64_t operation_serial = 1;

static bool source_storage_route(const StructRNA *type)
{
  if (!type || !type->system_idproperties) {
    return false;
  }
  /* One node's declarations ask about one type over and over within an operation. */
  static const StructRNA *cached_type = nullptr;
  static uint64_t cached_serial = 0;
  static bool cached = false;
  if (type == cached_type && cached_serial == operation_serial) {
    return cached;
  }
  cached_type = type;
  cached_serial = operation_serial;
  cached = false;
  /* The locator must be a compiled declaration's own source-recorded callback; a runtime type
   * or a derived declaration inherits it unchanged from such an ancestor. */
  const StructRNA *builtin = type;
  for (int depth = 0; builtin && depth < 128; depth++, builtin = builtin->base) {
    if (!(builtin->flag & STRUCT_RUNTIME) &&
        RNA_struct_read_callback_matches_builtin(
            type, builtin, RNAReadCallbackRole::SYSTEM_IDPROPERTIES))
    {
      cached = true;
      return true;
    }
  }
  return false;
}

static bool derives(const StructRNA *type, const StructRNA *base);

/**
 * Supplemental catalogue of built-in callbacks that are not replaced by a native family.
 * Every row is listed with its reason in doc/angelo/scene_intelligence.rst ("Catalogued
 * built-in getters"); keep the two in step. GUARD rows run the built-in callback only while the
 * guard holds (the callback then neither loads nor changes anything); WITHHELD rows never run.
 */
enum class Guard { NONE, VIEW_LAYERS_SYNCED, NODE_TOPOLOGY_CURRENT, CONTEXT_CURRENT };
struct CatalogueRow {
  const char *file;
  const char *symbol;
  RNAReadCallbackRole role;
  Guard guard;
  /* Refusal code when the row never runs (guard NONE). */
  const char *withheld;
};

static Span<CatalogueRow> supplemental_catalogue()
{
  using R = RNAReadCallbackRole;
  static const CatalogueRow rows[] = {
      /* Node topology cache: rebuilt by these getters when a tree changed. */
      {"rna_node_socket.cc", "rna_NodeSocket_label_get", R::GET, Guard::NODE_TOPOLOGY_CURRENT, nullptr},
      {"rna_node_socket.cc", "rna_NodeSocket_label_length", R::STRING_LENGTH, Guard::NODE_TOPOLOGY_CURRENT, nullptr},
      {"rna_node_socket.cc", "rna_NodeSocket_is_linked_get", R::GET, Guard::NODE_TOPOLOGY_CURRENT, nullptr},
      {"rna_node_socket.cc", "rna_NodeSocket_is_inactive_get", R::GET, Guard::NODE_TOPOLOGY_CURRENT, nullptr},
      {"rna_node_socket.cc", "rna_NodeSocket_is_icon_visible_get", R::GET, Guard::NODE_TOPOLOGY_CURRENT, nullptr},
      {"rna_nodetree.cc", "rna_NodeLink_is_hidden_get", R::GET, Guard::NODE_TOPOLOGY_CURRENT, nullptr},
      /* Editor context: these choices read the active object through the window's view layer,
       * which Blender resynchronizes (and may rename) when it is out of date. */
      {"rna_brush.cc", "rna_BrushTextureSlot_map_mode_itemf", R::ENUM_ITEMS, Guard::CONTEXT_CURRENT, nullptr},
      {"rna_brush.cc", "rna_Brush_direction_itemf", R::ENUM_ITEMS, Guard::CONTEXT_CURRENT, nullptr},
      {"rna_brush.cc", "rna_Brush_stroke_itemf", R::ENUM_ITEMS, Guard::CONTEXT_CURRENT, nullptr},
      {"rna_sculpt_paint.cc", "rna_ParticleEdit_tool_itemf", R::ENUM_ITEMS, Guard::CONTEXT_CURRENT, nullptr},
      {"rna_particle.cc", "rna_Particle_Material_itemf", R::ENUM_ITEMS, Guard::CONTEXT_CURRENT, nullptr},
      /* File loads. */
      {"rna_image.cc", "rna_Image_pixels_get", R::GET_ARRAY, Guard::NONE, "LOADS_FILE"},
      {"rna_image.cc", "rna_Image_pixels_get_length", R::ARRAY_LENGTH, Guard::NONE, "LOADS_FILE"},
      {"rna_movieclip.cc", "rna_MovieClip_fps_get", R::GET, Guard::NONE, "LOADS_FILE"},
      {"rna_text.cc", "rna_Text_modified_get", R::GET, Guard::NONE, "LOADS_FILE"},
      {"rna_volume.cc", "rna_Volume_grids_get", R::COLLECTION_GET, Guard::NONE, "LOADS_FILE"},
      {"rna_volume.cc", "rna_VolumeGrids_frame_filepath_get", R::GET, Guard::NONE, "LOADS_FILE"},
      {"rna_volume.cc", "rna_VolumeGrids_error_message_get", R::GET, Guard::NONE, "LOADS_FILE"},
      {"rna_cachefile.cc", "rna_CacheFile_active_layer_get", R::GET, Guard::NONE, "LOADS_FILE"},
      /* Writes: the slot's preview icon is created on first access. */
      {"rna_ID.cc", "rna_ImagePreview_icon_id_get", R::GET, Guard::NONE, "DENIED"},
  };
  return Span<CatalogueRow>(rows, ARRAY_SIZE(rows));
}

static const CatalogueRow *supplemental_row(const PropertyRNA &property)
{
  for (const CatalogueRow &row : supplemental_catalogue()) {
    if (manual_callback(property, row.role, row.file, row.symbol)) {
      return &row;
    }
  }
  return nullptr;
}

/* Core-owned replacements below are identified by these callbacks. A declaration that uses one
 * but no longer matches its replacement exactly is withheld instead of running the callback. */
static bool core_catalogued(const PropertyRNA &property)
{
  using R = RNAReadCallbackRole;
  for (const auto &[file, symbol, role] : {
           std::tuple{"rna_texture.cc", "rna_TextureSlot_output_node_get", R::GET},
           std::tuple{"rna_object.cc", "rna_MaterialSlot_name_get", R::GET},
           std::tuple{"rna_object.cc", "rna_MaterialSlot_material_get", R::GET},
           std::tuple{"rna_object.cc", "rna_Object_active_material_get", R::GET},
           std::tuple{"rna_object.cc", "rna_Object_data_get", R::GET},
           std::tuple{"rna_collection.cc", "rna_CollectionImport_import_properties_get", R::GET},
           std::tuple{"rna_collection.cc", "rna_CollectionExport_export_properties_get", R::GET},
           std::tuple{"rna_collection.cc", "rna_Collection_all_objects_begin", R::COLLECTION_BEGIN},
           std::tuple{"rna_modifier.cc", "rna_NodesModifierBake_node_get", R::GET},
           std::tuple{"rna_node_socket.cc", "rna_NodeSocket_node_get", R::GET},
           std::tuple{"rna_sequencer.cc", "rna_SequenceEditor_strips_all_begin", R::COLLECTION_BEGIN},
           std::tuple{"rna_node_tree_interface.cc", "rna_NodeTreeInterface_items_begin", R::COLLECTION_BEGIN},
           std::tuple{"rna_node_tree_interface.cc", "rna_NodeTreeInterface_items_length", R::COLLECTION_LENGTH},
           std::tuple{"rna_node_tree_interface.cc", "rna_NodeTreeInterface_items_lookup_int", R::COLLECTION_LOOKUP_INT},
           std::tuple{"rna_node_tree_interface.cc", "rna_NodeTreeInterface_items_lookup_string", R::COLLECTION_LOOKUP_STRING},
           std::tuple{"rna_mesh.cc", "rna_Attribute_data_begin", R::COLLECTION_BEGIN},
           std::tuple{"rna_attribute.cc", "rna_Attribute_data_begin", R::COLLECTION_BEGIN},
           std::tuple{"rna_mesh.cc", "rna_MeshVertex_groups_begin", R::COLLECTION_BEGIN},
           std::tuple{"rna_modifier.cc", "rna_HookModifier_vertex_indices_get", R::GET_ARRAY},
           std::tuple{"rna_constraint.cc", "rna_SplineIKConstraint_joint_bindings_get", R::GET_ARRAY},
           std::tuple{"rna_fcurve.cc", "rna_FModifierGenerator_coefficients_get", R::GET_ARRAY}})
  {
    if (manual_callback(property, role, file, symbol)) {
      return true;
    }
  }
  for (const char *name : {"vertices", "edges", "loops", "polygons"}) {
    const std::string prefix = std::string("rna_Mesh_") + name;
    if (manual_callback(property, R::COLLECTION_BEGIN, "rna_mesh.cc", (prefix + "_begin").c_str()) ||
        manual_callback(property, R::COLLECTION_LOOKUP_INT, "rna_mesh.cc",
                        (prefix + "_lookup_int").c_str()))
    {
      return true;
    }
  }
  return false;
}

static void builtin_route(const PropertyRNA &property, RouteFacts &facts)
{
  facts.length = property.getlength ? "SOURCE_LENGTH" : "NONE";
  switch (property.type) {
    case PROP_BOOLEAN:
    case PROP_INT:
    case PROP_FLOAT:
    case PROP_ENUM:
      facts.value = property.totarraylength || property.getlength ? "SOURCE_ARRAY" : "SOURCE_SCALAR";
      if (property.type == PROP_ENUM) {
        facts.enum_value_items = "SOURCE_ENUM_VALUE";
        facts.enum_decl_items = "SOURCE_ENUM_DECL";
      }
      break;
    case PROP_STRING:
      facts.value = "SOURCE_SCALAR";
      break;
    case PROP_POINTER:
      facts.value = "SOURCE_POINTER";
      facts.refine = "SOURCE_REFINE";
      break;
    case PROP_COLLECTION: {
      facts.value = facts.iterate = facts.lookup_index = facts.lookup_name = "SOURCE_COLLECTION";
      facts.length = "SOURCE_LENGTH";
      facts.refine = "SOURCE_REFINE";
      const CollectionPropertyRNA &collection = static_cast<const CollectionPropertyRNA &>(property);
      if (collection.item_type == RNA_NodeSocket) {
        if (manual_callback(property, RNAReadCallbackRole::COLLECTION_LOOKUP_STRING,
                            "rna_nodetree.cc", "rna_NodeInputs_lookup_string"))
        {
          facts.socket_key_kind = 1;
        }
        else if (manual_callback(property, RNAReadCallbackRole::COLLECTION_LOOKUP_STRING,
                                 "rna_nodetree.cc", "rna_NodeOutputs_lookup_string"))
        {
          facts.socket_key_kind = 2;
        }
      }
      break;
    }
  }
}

/**
 * The getter policy (doc/angelo/scene_intelligence.rst, "Getter policy"):
 *  - an add-on's (Python) property is read from storage or its declared default; its getter,
 *    transform and item callbacks never run;
 *  - a catalogued built-in getter (one that loads a file, changes data or a cache, or runs
 *    add-on code) is replaced by a native copy, runs under a guard that makes it inert, or is
 *    withheld with its reason;
 *  - every other built-in getter, iterator, choice list and refiner runs as for any RNA read.
 * Python attribute access, Python classes and their overrides are never involved: the caller
 * receives only copied values.
 */
static RouteFacts compute_source_route_facts(const StructRNA &type, const PropertyRNA &property)
{
  RouteFacts facts;
  if (RNA_property_is_idprop(&property)) {
    return facts;
  }
  const bool runtime = property.flag_internal & PROP_INTERN_RUNTIME;
  const bool raw = property.flag & PROP_IDPROPERTY ||
                   (runtime && BPY_rna_property_has_python_getter(const_cast<PropertyRNA *>(&property)));
  if (raw) {
    facts.storage_locator = id_storage_route(&type) ? "ID_SYSTEM_STORAGE" :
                            source_storage_route(&type) ? "SOURCE_STORAGE_LOCATOR" : "UNSUPPORTED";
    facts.value = facts.storage_locator;
    facts.length = property.getlength || property.totarraylength || property.type == PROP_COLLECTION ?
                       "VIRTUAL_STORAGE" : "NONE";
    facts.refine = "NONE";
    if (property.type == PROP_COLLECTION) {
      facts.iterate = facts.lookup_index = facts.lookup_name = "VIRTUAL_STORAGE";
    }
    if (property.type == PROP_ENUM) {
      facts.enum_value_items = facts.enum_decl_items = "VIRTUAL_STORAGE";
    }
    return facts;
  }
  if (runtime) {
    /* A runtime declaration without IDProperty storage or a Python getter is a native one
     * (compiled callbacks registered at run time); a Python enum item callback never runs. */
    if (BPY_rna_property_has_python_enum_items(const_cast<PropertyRNA *>(&property))) {
      facts.withheld = "COMPUTED_VALUE";
      return facts;
    }
    builtin_route(property, facts);
    return facts;
  }
  facts.scalar_value = angelo_read_scalar::value_family(type, property);
  facts.scalar_enum = property.type == PROP_ENUM ?
                          angelo_read_scalar::enum_family(type, property) : angelo_read_scalar::NONE;
  if (facts.scalar_value) {
    facts.value = property.getlength || property.totarraylength ? "SOURCE_ARRAY" : "SOURCE_SCALAR";
    facts.length = property.getlength ? "SOURCE_LENGTH" : "NONE";
    if (property.type == PROP_ENUM) {
      facts.enum_value_items = "SOURCE_ENUM_VALUE";
      facts.enum_decl_items = "SOURCE_ENUM_DECL";
    }
    return facts;
  }
  /* These source getters memcpy potentially large stored arrays. Preserve the native length
   * and selected source, but copy only the transport prefix instead of allocating N numbers. */
  for (const auto &[expected, file, getter, length, kind, numeric_type] : {
           std::tuple{RNA_HookModifier, "rna_modifier.cc", "rna_HookModifier_vertex_indices_get",
                      "rna_HookModifier_vertex_indices_get_length", 1, PROP_INT},
           std::tuple{RNA_SplineIKConstraint, "rna_constraint.cc", "rna_SplineIKConstraint_joint_bindings_get",
                      "rna_SplineIKConstraint_joint_bindings_get_length", 2, PROP_FLOAT},
           std::tuple{RNA_FModifierGenerator, "rna_fcurve.cc", "rna_FModifierGenerator_coefficients_get",
                      "rna_FModifierGenerator_coefficients_get_length", 3, PROP_FLOAT}})
  {
    if (derives(&type, expected) && property.type == numeric_type &&
        manual_callback(property, RNAReadCallbackRole::GET_ARRAY, file, getter) &&
        manual_callback(property, RNAReadCallbackRole::ARRAY_LENGTH, file, length) &&
        callback_absent(property, RNAReadCallbackRole::GET_ARRAY_EX) &&
        callback_absent(property, RNAReadCallbackRole::GET_ARRAY_TRANSFORM))
    {
      facts.bounded_array_kind = kind;
      facts.value = "SOURCE_ARRAY";
      facts.length = "SOURCE_LENGTH";
      return facts;
    }
  }
  facts.geometry_scalar = angelo_read_geometry::scalar_family(type, property);
  if (facts.geometry_scalar) {
    facts.value = property.getlength || property.totarraylength ? "SOURCE_ARRAY" : "SOURCE_SCALAR";
    facts.length = property.getlength ? "SOURCE_LENGTH" : "NONE";
    if (property.type == PROP_ENUM) {
      const bool static_items = callback_absent(property, RNAReadCallbackRole::ENUM_ITEMS);
      facts.enum_value_items = static_items ? "SOURCE_ENUM_VALUE" : "UNSUPPORTED";
      facts.enum_decl_items = static_items ? "SOURCE_ENUM_DECL" : "UNSUPPORTED";
    }
    return facts;
  }
  facts.geometry_collection = angelo_read_geometry::collection_family(type, property);
  if (facts.geometry_collection) {
    facts.value = "SOURCE_COLLECTION";
    facts.length = "SOURCE_LENGTH";
    facts.refine = "SOURCE_REFINE";
    if (facts.geometry_collection == angelo_read_geometry::ATTRIBUTE_DATA_LENGTH) {
      facts.attribute_elements = true;
      facts.iterate = facts.lookup_index = facts.lookup_name = "UNSUPPORTED";
    }
    else {
      facts.iterate = facts.lookup_index = facts.lookup_name = "SOURCE_COLLECTION";
    }
    return facts;
  }
  const angelo_read_traversal::Family traversal_pointer =
      angelo_read_traversal::pointer_family(type, property);
  if (traversal_pointer) {
    if (angelo_read_traversal::is_view_layer_family(traversal_pointer)) {
      builtin_route(property, facts);
      facts.guard = "VIEW_LAYERS_SYNCED";
    }
    else {
      facts.traversal_pointer = traversal_pointer;
      facts.value = "SOURCE_POINTER";
      facts.refine = "SOURCE_REFINE";
    }
    return facts;
  }
  const angelo_read_traversal::Family traversal_collection =
      angelo_read_traversal::collection_family(type, property);
  if (traversal_collection) {
    if (angelo_read_traversal::is_view_layer_family(traversal_collection)) {
      builtin_route(property, facts);
      facts.guard = "VIEW_LAYERS_SYNCED";
    }
    else {
      facts.traversal_collection = traversal_collection;
      facts.value = facts.iterate = facts.lookup_index = facts.lookup_name = "SOURCE_COLLECTION";
      facts.length = "SOURCE_LENGTH";
      facts.refine = "SOURCE_REFINE";
    }
    return facts;
  }
  if (property.type == PROP_ENUM && derives(&type, RNA_TextureSlot) &&
      manual_callback(property, RNAReadCallbackRole::GET,
                      "rna_texture.cc", "rna_TextureSlot_output_node_get") &&
      callback_absent(property, RNAReadCallbackRole::GET_TRANSFORM))
  {
    builtin_route(property, facts);
    facts.texture_output_special = true;
    return facts;
  }
  if (property.type == PROP_STRING && &type == RNA_MaterialSlot &&
      manual_callback(property, RNAReadCallbackRole::GET,
                      "rna_object.cc", "rna_MaterialSlot_name_get") &&
      manual_callback(property, RNAReadCallbackRole::STRING_LENGTH,
                      "rna_object.cc", "rna_MaterialSlot_name_length") &&
      callback_absent(property, RNAReadCallbackRole::GET_TRANSFORM))
  {
    facts.value = "SOURCE_SCALAR";
    facts.material_slot_special = true;
    return facts;
  }
  if (property.type == PROP_POINTER) {
    const StructRNA *fixed = static_cast<const PointerPropertyRNA &>(property).pointer_type;
    if ((&type == RNA_CollectionImport &&
         manual_callback(property, RNAReadCallbackRole::GET, "rna_collection.cc",
                         "rna_CollectionImport_import_properties_get")) ||
        (&type == RNA_CollectionExport &&
         manual_callback(property, RNAReadCallbackRole::GET, "rna_collection.cc",
                         "rna_CollectionExport_export_properties_get")))
    {
      facts.registry_properties_kind = &type == RNA_CollectionImport ? 1 : 2;
      facts.value = "SOURCE_POINTER";
      facts.refine = "SOURCE_REFINE";
      return facts;
    }
    if (&type == RNA_NodesModifierBake && fixed == RNA_Node &&
        manual_callback(property, RNAReadCallbackRole::GET,
                        "rna_modifier.cc", "rna_NodesModifierBake_node_get"))
    {
      facts.bake_node_special = true;
      facts.value = "SOURCE_POINTER";
      facts.refine = "SOURCE_REFINE";
      return facts;
    }
    if (derives(&type, RNA_NodeSocket) && fixed == RNA_Node &&
        manual_callback(property, RNAReadCallbackRole::GET,
                        "rna_node_socket.cc", "rna_NodeSocket_node_get"))
    {
      facts.value = "SOURCE_POINTER";
      facts.refine = "SOURCE_REFINE";
      facts.node_socket_node_special = true;
      return facts;
    }
    if (fixed == RNA_Material &&
        ((&type == RNA_MaterialSlot &&
          manual_callback(property, RNAReadCallbackRole::GET,
                          "rna_object.cc", "rna_MaterialSlot_material_get")) ||
         (&type == RNA_Object &&
          manual_callback(property, RNAReadCallbackRole::GET,
                          "rna_object.cc", "rna_Object_active_material_get"))))
    {
      facts.value = "SOURCE_POINTER";
      facts.refine = "SOURCE_REFINE";
      facts.material_slot_special = &type == RNA_MaterialSlot;
      facts.active_material_special = &type == RNA_Object;
      return facts;
    }
    if (type.identifier && STREQ(type.identifier, "Object") &&
        manual_callback(property, RNAReadCallbackRole::GET, "rna_object.cc", "rna_Object_data_get"))
    {
      facts.value = "SOURCE_POINTER";
      facts.object_data_special = true;
      facts.refine = "SOURCE_REFINE";
      return facts;
    }
  }
  if (property.type == PROP_COLLECTION) {
    const CollectionPropertyRNA &collection = static_cast<const CollectionPropertyRNA &>(property);
    const auto owned = [&](const int kind) {
      facts.owned_collection_kind = kind;
      facts.value = facts.iterate = facts.lookup_index = facts.lookup_name = "SOURCE_COLLECTION";
      facts.length = "SOURCE_LENGTH";
      facts.refine = "SOURCE_REFINE";
    };
    if (&type == RNA_SequenceEditor && collection.item_type == RNA_Strip &&
        manual_callback(property, RNAReadCallbackRole::COLLECTION_BEGIN, "rna_sequencer.cc",
                        "rna_SequenceEditor_strips_all_begin"))
    {
      owned(3);
      return facts;
    }
    if (&type == RNA_NodeTreeInterface && collection.item_type == RNA_NodeTreeInterfaceItem &&
        manual_callback(property, RNAReadCallbackRole::COLLECTION_BEGIN,
                        "rna_node_tree_interface.cc", "rna_NodeTreeInterface_items_begin"))
    {
      owned(1);
      return facts;
    }
    if (&type == RNA_Collection && collection.item_type == RNA_Object &&
        manual_callback(property, RNAReadCallbackRole::COLLECTION_BEGIN,
                        "rna_collection.cc", "rna_Collection_all_objects_begin"))
    {
      owned(2);
      return facts;
    }
    if (ELEM(&type, RNA_MeshUVLoopLayer, RNA_MeshLoopColorLayer) &&
        manual_callback(property, RNAReadCallbackRole::COLLECTION_BEGIN,
                        "rna_mesh.cc", "rna_Attribute_data_begin") &&
        manual_callback(property, RNAReadCallbackRole::COLLECTION_LENGTH,
                        "rna_mesh.cc", "rna_Attribute_data_length") &&
        ELEM(collection.item_type, RNA_MeshUVLoop, RNA_Float2AttributeValue, RNA_MeshLoopColor))
    {
      facts.const_attribute_kind = collection.item_type == RNA_MeshLoopColor ? 2 : 1;
      facts.value = facts.iterate = facts.lookup_index = "SOURCE_COLLECTION";
      facts.length = "SOURCE_LENGTH";
      facts.refine = facts.lookup_name = "NONE";
      return facts;
    }
    if (manual_callback(property, RNAReadCallbackRole::COLLECTION_LENGTH,
                        "rna_attribute.cc", "rna_Attribute_data_length"))
    {
      /* Length is pure; begin/get rewrite SINGLE storage and are never invoked for elements. */
      facts.value = "SOURCE_COLLECTION";
      facts.length = "SOURCE_LENGTH";
      facts.iterate = facts.lookup_index = facts.lookup_name = "UNSUPPORTED";
      facts.attribute_elements = true;
      return facts;
    }
    if (&type == RNA_Mesh) {
      int kind = 0;
      for (const auto &[name, expected, ordinal] : {
               std::tuple{"vertices", RNA_MeshVertex, 1},
               std::tuple{"edges", RNA_MeshEdge, 2},
               std::tuple{"loops", RNA_MeshLoop, 3},
               std::tuple{"polygons", RNA_MeshPolygon, 4}})
      {
        const std::string prefix = std::string("rna_Mesh_") + name;
        if (collection.item_type == expected &&
            manual_callback(property, RNAReadCallbackRole::COLLECTION_BEGIN,
                            "rna_mesh.cc", (prefix + "_begin").c_str()) &&
            manual_callback(property, RNAReadCallbackRole::COLLECTION_LENGTH,
                            "rna_mesh.cc", (prefix + "_length").c_str()) &&
            ordinary_iterator_callback(property, RNAReadCallbackRole::COLLECTION_GET) &&
            ordinary_iterator_callback(property, RNAReadCallbackRole::COLLECTION_NEXT) &&
            ordinary_iterator_callback(property, RNAReadCallbackRole::COLLECTION_END))
        {
          kind = ordinal;
        }
      }
      if (kind) {
        facts.mesh_collection_kind = kind;
        facts.value = facts.iterate = facts.lookup_index = "SOURCE_COLLECTION";
        facts.length = "SOURCE_LENGTH";
        facts.refine = "NONE";
        facts.lookup_name = "NONE";
        return facts;
      }
    }
  }
  if (facts.scalar_enum) {
    /* The value getter is an ordinary built-in; the choices come from the native family. */
    builtin_route(property, facts);
    return facts;
  }
  if (const CatalogueRow *row = supplemental_row(property)) {
    if (row->guard == Guard::NONE) {
      facts.withheld = row->withheld;
      return facts;
    }
    builtin_route(property, facts);
    facts.guard = row->guard == Guard::VIEW_LAYERS_SYNCED ? "VIEW_LAYERS_SYNCED" :
                  row->guard == Guard::NODE_TOPOLOGY_CURRENT ? "NODE_TOPOLOGY_CURRENT" :
                                                               "CONTEXT_CURRENT";
    return facts;
  }
  if (angelo_read_scalar::catalogued(property) || angelo_read_geometry::catalogued(property) ||
      angelo_read_traversal::catalogued(property) || core_catalogued(property))
  {
    /* A catalogued callback whose declaration no longer matches its replacement exactly. */
    facts.withheld = "NATIVE_READER_REQUIRED";
    return facts;
  }
  builtin_route(property, facts);
  return facts;
}

/* Route facts of compiled declarations on compiled types depend only on those static
 * declarations, so they are computed once per (type, property) for the process lifetime.
 * Runtime (add-on) types and declarations can be freed and are always recomputed. Main thread
 * only, like every read. */
static RouteFacts source_route_facts(const StructRNA &type, const PropertyRNA &property)
{
  if (RNA_property_is_idprop(&property) || (type.flag & STRUCT_RUNTIME) ||
      (property.flag_internal & PROP_INTERN_RUNTIME) || (property.flag & PROP_IDPROPERTY))
  {
    return compute_source_route_facts(type, property);
  }
  struct KeyHash {
    size_t operator()(const std::pair<const StructRNA *, const PropertyRNA *> &key) const
    {
      return std::hash<const void *>()(key.first) ^
             (std::hash<const void *>()(key.second) * 0x9e3779b97f4a7c15ULL);
    }
  };
  static std::unordered_map<std::pair<const StructRNA *, const PropertyRNA *>, RouteFacts, KeyHash>
      cache;
  const auto key = std::make_pair(&type, &property);
  if (const auto found = cache.find(key); found != cache.end()) {
    return found->second;
  }
  const RouteFacts facts = compute_source_route_facts(type, property);
  cache.emplace(key, facts);
  return facts;
}

struct OperationScope {
  struct Items { std::vector<PointerRNA> values; };
  /* Declared first, destroyed last: scalar/traversal borrows and selected children end
   * before geometry's owned mesh snapshots and managed-cache locks are released. */
  angelo_read_geometry::Scope geometry;
  angelo_read_scalar::Scope scalar;
  angelo_read_traversal::Scope traversal;

  OperationScope() : scalar(geometry) {}

  std::map<std::pair<uintptr_t, uintptr_t>, Items> collections;
  /* The bound window's scene and view layer, resolved without context callbacks. */
  angelo_read_scalar::ReadWindow window;
  bool membership_ready = false;
  std::unordered_map<const void *, ID *> membership;

  ID *borrow_mesh_cache(Mesh *mesh)
  {
    if (mesh) {
      membership.emplace(mesh, &mesh->id);
    }
    return mesh ? &mesh->id : nullptr;
  }

  ID *live(const void *candidate)
  {
    if (!candidate || !G_MAIN) {
      return nullptr;
    }
    if (!membership_ready) {
      for (ListBaseT<ID> *list : BKE_main_lists_get(*G_MAIN)) {
        for (ID &id : *list) {
          membership.emplace(&id, &id);
        }
      }
      const auto add = [&](void *pointer) {
        if (pointer) {
          membership.emplace(pointer, reinterpret_cast<ID *>(pointer));
        }
      };
      for (Material &id : G_MAIN->materials) { add(id.nodetree); }
      for (World &id : G_MAIN->worlds) { add(id.nodetree); }
      for (Light &id : G_MAIN->lights) { add(id.nodetree); }
      for (Tex &id : G_MAIN->textures) { add(id.nodetree); }
      for (Scene &id : G_MAIN->scenes) { add(id.master_collection); }
      membership_ready = true;
    }
    const auto found = membership.find(candidate);
    return found == membership.end() ? nullptr : found->second;
  }

  /* Main and embedded ID membership (live_id_candidate) and the bound scene's first objects
   * (scene_contains_object), each built once per operation instead of once per lookup. */
  bool main_ready = false;
  std::unordered_set<const void *> main_ids;
  bool scene_objects_ready = false;
  std::unordered_set<const Object *> scene_objects;
};

/* The operation of the current resolve: every read runs on the main thread, one at a time. */
static OperationScope *current_operation = nullptr;
struct CurrentOperation {
  OperationScope *previous;
  explicit CurrentOperation(OperationScope *operation) : previous(current_operation)
  {
    current_operation = operation;
    operation_serial++;
  }
  ~CurrentOperation()
  {
    current_operation = previous;
    operation_serial++;
  }
};

/* All targets are stack-local native borrows. A Target must never be captured in a detached
 * result or retained across ABI calls. Collections retain the originating owner/property. */
struct Target {
  enum class Kind { Struct, Collection, Property, Null } kind = Kind::Struct;
  PointerRNA ptr;
  PropertyRNA *property = nullptr;
  const IDProperty *storage = nullptr;
  bool virtual_storage = false;
  const char *virtual_kind = "NONE";
  int weight_vertex = -1;
  const MDeformWeight *weight = nullptr;
  bool data_root = false;
  OperationScope *operation = nullptr;
  const bke::Attribute *attribute_source = nullptr;
  int attribute_element_index = -1;
  angelo_read_geometry::Item geometry_item;
  angelo_read_traversal::Item traversal_item;
};

static bool derives(const StructRNA *type, const StructRNA *base)
{
  for (; type; type = type->base) {
    if (type == base) {
      return true;
    }
  }
  return false;
}

/* Embedded IDs are reached only from current live Main owners' stored DNA links. No saved
 * candidate header is dereferenced to decide membership, and no RNA/IDType traversal callback runs. */
static ID *live_embedded_id_candidate(const void *candidate)
{
  auto same = [&](const void *embedded) -> ID * {
    return embedded && embedded == candidate ? reinterpret_cast<ID *>(const_cast<void *>(embedded)) :
                                              nullptr;
  };
  for (Material &owner : G_MAIN->materials) {
    if (ID *found = same(owner.nodetree)) {
      return found;
    }
  }
  for (World &owner : G_MAIN->worlds) {
    if (ID *found = same(owner.nodetree)) {
      return found;
    }
  }
  for (Light &owner : G_MAIN->lights) {
    if (ID *found = same(owner.nodetree)) {
      return found;
    }
  }
  for (Tex &owner : G_MAIN->textures) {
    if (ID *found = same(owner.nodetree)) {
      return found;
    }
  }
  for (Scene &owner : G_MAIN->scenes) {
    if (ID *found = same(owner.master_collection)) {
      return found;
    }
    /* Current compositing_node_group trees are ordinary Main IDs; old embedded compositor
     * trees are migrated before this reader can observe the current Main. */
  }
  return nullptr;
}

static ID *live_id_candidate(const void *candidate)
{
  if (!candidate || !G_MAIN) {
    return nullptr;
  }
  if (OperationScope *operation = current_operation) {
    if (!operation->main_ready) {
      for (ListBaseT<ID> *list : BKE_main_lists_get(*G_MAIN)) {
        for (ID &id : *list) {
          operation->main_ids.insert(&id);
        }
      }
      for (Material &owner : G_MAIN->materials) { operation->main_ids.insert(owner.nodetree); }
      for (World &owner : G_MAIN->worlds) { operation->main_ids.insert(owner.nodetree); }
      for (Light &owner : G_MAIN->lights) { operation->main_ids.insert(owner.nodetree); }
      for (Tex &owner : G_MAIN->textures) { operation->main_ids.insert(owner.nodetree); }
      for (Scene &owner : G_MAIN->scenes) { operation->main_ids.insert(owner.master_collection); }
      operation->main_ids.erase(nullptr);
      operation->main_ready = true;
    }
    return operation->main_ids.contains(candidate) ? static_cast<ID *>(const_cast<void *>(candidate)) :
                                                     nullptr;
  }
  for (ListBaseT<ID> *list : BKE_main_lists_get(*G_MAIN)) {
    for (ID &id : *list) {
      if (&id == candidate) {
        return &id;
      }
    }
  }
  return live_embedded_id_candidate(candidate);
}

static wmWindow *live_window(const Context &context)
{
  for (wmWindowManager &manager : G_MAIN->wm) {
    if (uintptr_t(&manager) != context.manager || manager.id.session_uid != context.manager_uid) {
      continue;
    }
    for (wmWindow &window : manager.windows) {
      if (uintptr_t(&window) == context.window && window.winid == context.window_id) {
        return &window;
      }
    }
  }
  return nullptr;
}

static Scene *live_scene(const Context &context)
{
  for (Scene &scene : G_MAIN->scenes) {
    if (uintptr_t(&scene) == context.scene && scene.id.session_uid == context.scene_uid) {
      return &scene;
    }
  }
  return nullptr;
}

static bool original_id(const ID &id)
{
  return !id.orig_id && !(id.tag & (ID_TAG_COPIED_ON_EVAL | ID_TAG_COPIED_ON_EVAL_FINAL_RESULT));
}

static Owner target_owner(const Context &context, const Target &target)
{
  Owner owner;
  if (target.ptr.owner_id) {
    /* Ordinary admitted native results own their pointers. Stored ID targets are separately
     * matched to Main before constructing this stack-local target. */
    owner.exists = true;
    owner.id_type = enum_identifier(rna_enum_id_type_items, GS(target.ptr.owner_id->name));
    owner.original = original_id(*target.ptr.owner_id);
    owner.bound_scene = uintptr_t(target.ptr.owner_id) == context.scene &&
                        target.ptr.owner_id->session_uid == context.scene_uid;
    /* The ID itself, not a struct that shares its address (Object.display is the Object). */
    owner.self = target.ptr.data == target.ptr.owner_id && !target.virtual_storage &&
                 target.ptr.type && (target.ptr.type->flag & STRUCT_ID);
  }
  return owner;
}

static const char *target_refusal(const Context &context, const Target &target)
{
  if (target.data_root) {
    return nullptr;
  }
  if (!target.ptr.owner_id) {
    return "OUT_OF_DOCUMENT";
  }
  const Owner owner = target_owner(context, target);
  if (!owner.original) {
    return "DENIED";
  }
  if (owner.id_type == "SCENE" && !owner.bound_scene) {
    return "OTHER_SCENE";
  }
  if (ELEM(owner.id_type, "SCREEN", "WINDOWMANAGER", "WORKSPACE", "TEXT", "LIBRARY")) {
    return "DENIED";
  }
  for (const StructRNA *type = target.ptr.type; type; type = type->base) {
    if (one_of(type->identifier, {"Depsgraph", "DepsgraphObjectInstance",
              "DepsgraphUpdate", "Struct", "Property", "Function", "ImagePreview",
              "PackedFile", "TextLine"}))
    {
      return "DENIED";
    }
  }
  if (!owner.self && ELEM(owner.id_type, "MOVIECLIP", "SOUND", "VOLUME", "CACHEFILE")) {
    return "DENIED";
  }
  return nullptr;
}

/* Every read callback of a declaration is generated DNA access (or absent). Such accessors read
 * the struct's own fields, which is safe for operation-private element copies too. */
static bool generated_reads_only(const PropertyRNA &property)
{
  using R = RNAReadCallbackRole;
  for (const R role : {R::GET, R::GET_ARRAY, R::GET_EX, R::GET_ARRAY_EX, R::GET_TRANSFORM,
                       R::GET_ARRAY_TRANSFORM, R::STRING_LENGTH, R::ARRAY_LENGTH, R::ENUM_ITEMS,
                       R::COLLECTION_BEGIN, R::COLLECTION_GET, R::COLLECTION_LENGTH})
  {
    const RNAReadCallbackSource source = RNA_property_read_callback_source(&property, role);
    if (source.state == RNAReadCallbackState::MATCHED &&
        source.origin != RNAReadCallbackOrigin::MANUAL)
    {
      continue;
    }
    if (ELEM(source.state, RNAReadCallbackState::ABSENT, RNAReadCallbackState::NOT_APPLICABLE)) {
      continue;
    }
    return false;
  }
  return true;
}

static RouteFacts routes_for(const Target &owner, const PropertyRNA &property)
{
  RouteFacts facts = source_route_facts(*owner.ptr.type, property);
  bool replaced = false;
  if (owner.virtual_storage) {
    facts.value = facts.storage_locator = "VIRTUAL_STORAGE";
    facts.length = property.type == PROP_COLLECTION || property.totarraylength ?
                       "VIRTUAL_STORAGE" : "NONE";
    facts.refine = "NONE";
    facts.iterate = facts.lookup_index = facts.lookup_name =
        property.type == PROP_COLLECTION ? "VIRTUAL_STORAGE" : "NONE";
    facts.enum_value_items = facts.enum_decl_items =
        property.type == PROP_ENUM ? "VIRTUAL_STORAGE" : "NONE";
    replaced = true;
  }
  if (owner.attribute_source && owner.ptr.type == RNA_MeshUVLoop &&
      manual_callback(property, RNAReadCallbackRole::GET,
                       "rna_mesh.cc", "rna_MeshUVLoop_pin_uv_get") &&
      callback_absent(property, RNAReadCallbackRole::GET_TRANSFORM))
  {
    /* Explicit const element route, never direct pin callback/cross-domain lazy adaptation. */
    facts.value = "SOURCE_SCALAR";
    replaced = true;
  }
  if (owner.weight && one_of(property.identifier, {"group", "weight"})) {
    facts.value = "WEIGHTS";
    replaced = true;
  }
  if (owner.ptr.type == RNA_MeshVertex && STREQ(property.identifier, "groups")) {
    facts.value = facts.length = facts.iterate = facts.lookup_index = "WEIGHTS";
    facts.refine = facts.lookup_name = "NONE";
    replaced = true;
  }
  if (!(property.flag & PROP_IDPROPERTY) &&
      !(property.flag_internal & PROP_INTERN_RUNTIME))
  {
    if (owner.ptr.type == RNA_Image &&
        one_of(property.identifier, {"size", "channels", "depth", "is_float",
              "resolution", "file_format", "frame_duration"}))
    {
      /* Cache-only copies; Blender's getters would load the image file. */
      facts.value = "IMAGE_CACHE";
      facts.length = "NONE";
      replaced = true;
    }
    if (owner.ptr.type == RNA_UDIMTile &&
        one_of(property.identifier, {"size", "channels"}))
    {
      facts.value = "IMAGE_TILE_CACHE";
      facts.length = "NONE";
      replaced = true;
    }
  }
  if (replaced) {
    facts.withheld = nullptr;
    facts.guard = "NONE";
  }
  else if (owner.geometry_item.pointer.data && !facts.geometry_scalar &&
           ELEM(owner.ptr.type, RNA_MeshVertex, RNA_MeshEdge, RNA_MeshLoop, RNA_MeshPolygon,
                RNA_MeshLoopTriangle, RNA_MeshNormalValue, RNA_ReadOnlyInteger,
                RNA_FloatVectorValueReadOnly) &&
           !generated_reads_only(property))
  {
    /* Elements a geometry replacement produced may live in the operation's private runtime;
     * a manual getter would compute their index against the artist's mesh instead. */
    facts.withheld = "NATIVE_READER_REQUIRED";
  }
  return facts;
}

/* Guards for catalogued built-in callbacks that are inert while their precondition holds. */

static bool scene_view_layers_synced(const Scene &scene)
{
  for (const ViewLayer &layer : scene.view_layers) {
    if (!BKE_view_layer_is_synced(layer)) {
      return false;
    }
  }
  return true;
}

/* The built-in view-layer getters call BKE_view_layer_synced_ensure, which only changes an
 * out-of-date layer. Every layer of the owning scene must be synchronized. */
static bool view_layers_synced(const Target &owner)
{
  const ID *id = owner.ptr.owner_id;
  return id && GS(id->name) == ID_SCE && scene_view_layers_synced(*reinterpret_cast<const Scene *>(id));
}

/* Socket and link getters rebuild a node tree's topology cache when it is stale. */
static bool node_topology_current(const Target &owner)
{
  const ID *id = owner.ptr.owner_id;
  if (!id || GS(id->name) != ID_NT) {
    return false;
  }
  const bNodeTree &tree = *reinterpret_cast<const bNodeTree *>(id);
  return tree.runtime && !tree.runtime->topology_cache_mutex.is_dirty();
}

/* Context-reading choice callbacks resolve the window's view layer (renaming the window's layer
 * when it no longer exists) and resynchronize it, and consult a Python context override. They
 * run only when none of that can happen: no override, every view layer synchronized and the
 * window's layer name resolving. */
static bool context_current(const bContext *C)
{
  if (!C || CTX_py_dict_get(C) || !G_MAIN) {
    return false;
  }
  for (const Scene &scene : G_MAIN->scenes) {
    if (!scene_view_layers_synced(scene)) {
      return false;
    }
  }
  if (const wmWindow *window = CTX_wm_window(C)) {
    return window->scene && BKE_view_layer_find(window->scene, window->view_layer_name);
  }
  return true;
}

static const char *route_refusal(const Target &owner, const RouteFacts &facts)
{
  if (facts.withheld) {
    return facts.withheld;
  }
  if (STREQ(facts.guard, "VIEW_LAYERS_SYNCED")) {
    return view_layers_synced(owner) ? nullptr : "VIEW_LAYER_OUT_OF_SYNC";
  }
  if (STREQ(facts.guard, "NODE_TOPOLOGY_CURRENT")) {
    return node_topology_current(owner) ? nullptr : "NOT_EVALUATED";
  }
  if (STREQ(facts.guard, "CONTEXT_CURRENT")) {
    return context_current(BPY_context_get()) ? nullptr : "VIEW_LAYER_OUT_OF_SYNC";
  }
  return nullptr;
}

/* Declaration fingerprints. A fingerprint stands for one property description: every input of
 * property_description and routes_for (declaration owner type, owner facts, virtual kind, element
 * traits, the declaration itself and its routes) is hashed, so a different description has a
 * different fingerprint. Python hands fingerprints back for admission instead of whole copied
 * projections; native always recomputes the current one and compares. A mismatch is STALE. */
struct Fingerprint {
  uint64_t state = 0xcbf29ce484222325ULL;
  void bytes(const void *data, size_t size)
  {
    const unsigned char *byte = static_cast<const unsigned char *>(data);
    for (size_t index = 0; index < size; index++) {
      state = (state ^ byte[index]) * 0x100000001b3ULL;
    }
  }
  void integer(int64_t value)
  {
    /* One multiply-xorshift round per word instead of eight byte rounds. */
    uint64_t mixed = (state ^ uint64_t(value)) * 0x9e3779b97f4a7c15ULL;
    state = (mixed ^ (mixed >> 29)) * 0xbf58476d1ce4e5b9ULL;
  }
  void flag(bool value)
  {
    const unsigned char byte = value ? 1 : 0;
    bytes(&byte, 1);
  }
  void text(const char *value)
  {
    flag(value != nullptr);
    if (value) {
      const size_t size = strlen(value);
      integer(int64_t(size));
      bytes(value, size);
    }
  }
  void type(const StructRNA *value)
  {
    flag(value != nullptr);
    if (value) {
      text(value->identifier);
      int count = 0;
      for (const StructRNA *base = value->base; base && count < 15; base = base->base, count++) {
        text(base->identifier);
      }
      integer(count);
      text(value->nameproperty ? value->nameproperty->identifier : nullptr);
    }
  }
  /* Non-negative and exactly representable as a Python int on every platform. */
  int64_t result() const
  {
    return int64_t(state & 0x3fffffffffffffffULL);
  }
};

static int virtual_kind_code(const char *kind)
{
  return STREQ(kind, "NONE") ? 0 : STREQ(kind, "STORED_GROUP") ? 1 :
         STREQ(kind, "STORED_COLLECTION_ITEM") ? 2 : STREQ(kind, "WEIGHT") ? 3 : 4;
}

static const StructRNA *fixed_type_of(const PropertyRNA &property)
{
  return property.type == PROP_POINTER ?
             static_cast<const PointerPropertyRNA &>(property).pointer_type :
         property.type == PROP_COLLECTION ?
             static_cast<const CollectionPropertyRNA &>(property).item_type :
             nullptr;
}

/* The owner part of a projection's fingerprint, shared by every declaration of one owner. */
static uint64_t owner_state(const Context &context, const Target &owner)
{
  Fingerprint hash;
  const Owner facts = target_owner(context, owner);
  hash.type(owner.ptr.type);
  hash.flag(owner.data_root);
  hash.flag(owner.ptr.type && (owner.ptr.type->flag & STRUCT_ID));
  hash.flag(facts.exists);
  hash.text(facts.exists ? facts.id_type.c_str() : nullptr);
  hash.flag(facts.original);
  hash.flag(facts.bound_scene);
  hash.text(owner.virtual_kind);
  return hash.state;
}

static int64_t compute_fingerprint_from(uint64_t state,
                                        const Target &owner,
                                        const PropertyRNA &property)
{
  Fingerprint hash;
  hash.state = state;
  /* Declaration part. */
  hash.text(property.identifier);
  hash.integer(property.type);
  hash.integer(property.subtype);
  hash.text(property.name ? property.name : "");
  hash.flag(property.flag_internal & PROP_INTERN_RUNTIME);
  hash.flag(property.deprecated != nullptr);
  hash.flag(property.flag & PROP_EDITABLE);
  hash.flag(property.flag & PROP_ENUM_FLAG);
  hash.integer(property.totarraylength);
  hash.integer(property.arraydimension);
  for (int index = 0; index < 3; index++) {
    hash.integer(property.arraydimension > 1 ? property.arraylength[index] : 0);
  }
  hash.flag(property.getlength != nullptr);
  const StructRNA *fixed = fixed_type_of(property);
  hash.type(fixed);
  if (fixed && fixed->nameproperty) {
    hash.integer(fixed->nameproperty->type);
    hash.integer(fixed->nameproperty->subtype);
  }
  hash.type(property.type == PROP_COLLECTION ? property.srna : nullptr);
  hash.flag(property.flag & PROP_IDPROPERTY);
  /* Hard limits are described with a single detailed member; readers keep them by fingerprint. */
  if (property.type == PROP_INT) {
    const IntPropertyRNA &declaration = static_cast<const IntPropertyRNA &>(property);
    hash.integer(declaration.hardmin);
    hash.integer(declaration.hardmax);
  }
  else if (property.type == PROP_FLOAT) {
    const FloatPropertyRNA &declaration = static_cast<const FloatPropertyRNA &>(property);
    hash.bytes(&declaration.hardmin, sizeof(declaration.hardmin));
    hash.bytes(&declaration.hardmax, sizeof(declaration.hardmax));
  }
  hash.flag(BPY_rna_property_has_python_getter(const_cast<PropertyRNA *>(&property)));
  hash.flag(BPY_rna_property_has_python_enum_items(const_cast<PropertyRNA *>(&property)));
  /* Route part. */
  const RouteFacts routes = routes_for(owner, property);
  for (const char *route : {routes.value, routes.length, routes.refine, routes.iterate,
                            routes.lookup_index, routes.lookup_name, routes.enum_value_items,
                            routes.enum_decl_items, routes.storage_locator})
  {
    hash.text(route);
  }
  return hash.result();
}

static int64_t compute_fingerprint(const Context &context,
                                   const Target &owner,
                                   const PropertyRNA &property)
{
  return compute_fingerprint_from(owner_state(context, owner), owner, property);
}

/* Compiled declarations on compiled types are static for the process lifetime, so their
 * fingerprint depends only on the owner facts and element traits, packed exactly into the key.
 * Runtime (add-on) types and declarations can be freed and are always recomputed. */
struct FingerprintKey {
  const StructRNA *type;
  const PropertyRNA *property;
  uint64_t bits;
  bool operator==(const FingerprintKey &other) const
  {
    return type == other.type && property == other.property && bits == other.bits;
  }
};
struct FingerprintKeyHash {
  size_t operator()(const FingerprintKey &key) const
  {
    /* Pointers and owner bits mixed with one multiply each; the high bits carry the mix. */
    uint64_t mixed = (uint64_t(uintptr_t(key.type)) * 0x9e3779b97f4a7c15ULL) ^
                     (uint64_t(uintptr_t(key.property)) * 0xbf58476d1ce4e5b9ULL) ^
                     (key.bits * 0x94d049bb133111ebULL);
    return size_t(mixed ^ (mixed >> 31) ^ (mixed >> 17));
  }
};

static bool compiled_declaration(const StructRNA *type, const PropertyRNA &property)
{
  if (!type || (type->flag & STRUCT_RUNTIME) || (property.flag_internal & PROP_INTERN_RUNTIME) ||
      (property.flag & PROP_IDPROPERTY))
  {
    return false;
  }
  const StructRNA *fixed = fixed_type_of(property);
  return !fixed || !(fixed->flag & STRUCT_RUNTIME);
}

/* Everything besides the type and the declaration that a compiled declaration's description
 * depends on: owner facts and element traits, packed exactly. */
static uint64_t owner_bits(const Context &context, const Target &owner)
{
  const ID *id = owner.ptr.owner_id;
  const bool bound_scene = id && uintptr_t(id) == context.scene && id->session_uid == context.scene_uid;
  return uint64_t(id ? GS(id->name) : 0) | (uint64_t(id != nullptr) << 16) |
         (uint64_t(id && original_id(*id)) << 17) | (uint64_t(bound_scene) << 18) |
         (uint64_t(owner.data_root) << 19) | (uint64_t(owner.virtual_storage) << 20) |
         (uint64_t(owner.attribute_source != nullptr) << 21) |
         (uint64_t(owner.weight != nullptr) << 22) |
         (uint64_t(owner.geometry_item.pointer.data != nullptr) << 23) |
         (uint64_t(virtual_kind_code(owner.virtual_kind)) << 24);
}

static int64_t fingerprint(const Context &context, const Target &owner, const PropertyRNA &property)
{
  if (!compiled_declaration(owner.ptr.type, property)) {
    return compute_fingerprint(context, owner, property);
  }
  const uint64_t bits = owner_bits(context, owner);
  static std::unordered_map<FingerprintKey, int64_t, FingerprintKeyHash> cache;
  /* A direct-mapped front for the map: a page asks for the same few hundred declarations. */
  struct Slot {
    FingerprintKey key{nullptr, nullptr, 0};
    int64_t value = 0;
  };
  static Slot slots[4096];
  const FingerprintKey key{owner.ptr.type, &property, bits};
  Slot &slot = slots[FingerprintKeyHash()(key) & 4095];
  if (slot.key == key && slot.key.property) {
    return slot.value;
  }
  if (const auto found = cache.find(key); found != cache.end()) {
    slot = {key, found->second};
    return found->second;
  }
  const int64_t computed = compute_fingerprint(context, owner, property);
  cache.emplace(key, computed);
  slot = {key, computed};
  return computed;
}

/* The declaration list of a node: its type's properties with this owner's facts and traits. A
 * compiled declaration's description is a function of (type, declaration, owner bits), all hashed
 * here, so its address stands for it; any other declaration contributes its full fingerprint. */
static int64_t compute_declarations_key(const Context &context, const Target &owner);

static int64_t declarations_key(const Context &context, const Target &owner)
{
  const std::pair<const void *, uint64_t> key(owner.ptr.type, owner_bits(context, owner));
  if (const auto found = context.declaration_keys.find(key); found != context.declaration_keys.end()) {
    return found->second;
  }
  const int64_t computed = compute_declarations_key(context, owner);
  if (context.declaration_keys.size() < 4096) {
    context.declaration_keys.emplace(key, computed);
  }
  return computed;
}

static int64_t compute_declarations_key(const Context &context, const Target &owner)
{
  Fingerprint hash;
  hash.flag(owner.data_root);
  hash.integer(int64_t(owner_bits(context, owner)));
  const StructRNA *type = owner.ptr.type;
  hash.integer(int64_t(uintptr_t(type)));
  for (const StructRNA *current = type; current; current = current->base) {
    hash.text(current->identifier);
  }
  if (!type) {
    return hash.result();
  }
  const StructRNA *hierarchy[32];
  int levels = 0;
  for (const StructRNA *current = type; current && levels < 32; current = current->base) {
    hierarchy[levels++] = current;
  }
  std::optional<uint64_t> state; /* The owner part, computed once if any declaration needs it. */
  for (int level = levels - 1; level >= 0; level--) {
    for (const PropertyRNA &property : hierarchy[level]->cont.properties) {
      if (property.flag_internal & PROP_INTERN_BUILTIN) {
        continue;
      }
      if (compiled_declaration(type, property)) {
        hash.integer(int64_t(uintptr_t(&property)));
      }
      else {
        if (!state) {
          state = owner_state(context, owner);
        }
        hash.flag(true);
        hash.integer(compute_fingerprint_from(*state, owner, property));
      }
    }
  }
  return hash.result();
}

static Value describe_property(const Context &context,
                               const Target &owner,
                               const PropertyRNA &property)
{
  Value description = property_description(owner.ptr.type, property,
                                           target_owner(context, owner), owner.data_root);
  description.fields.emplace_back("fingerprint", Value::int_value(fingerprint(context, owner, property)));
  Value &projection = field(description, "projection");
  field(projection.sequence[0], "virtual_kind") = Value::string(owner.virtual_kind);
  const RouteFacts facts = routes_for(owner, property);
  projection.sequence[2] = moved_dict(Field("value", Value::string(facts.value)),
                                      Field("length", Value::string(facts.length)),
                                      Field("refine", Value::string(facts.refine)),
                                      Field("iterate", Value::string(facts.iterate)),
                                      Field("lookup_index", Value::string(facts.lookup_index)),
                                      Field("lookup_name", Value::string(facts.lookup_name)),
                                      Field("enum_value_items", Value::string(facts.enum_value_items)),
                                      Field("enum_decl_items", Value::string(facts.enum_decl_items)),
                                      Field("storage_locator", Value::string(facts.storage_locator)));
  return description;
}

/* A node record is a tagged tuple: ("NODE", address, (type, bases, name property),
 * (exists, id type, original, bound scene, self), data root, ID, virtual kind, declarations key).
 * Positional fields copy and encode far cheaper than the dict records used for declarations. */
static Value node_value(const Context &context,
                        const Address &address,
                        const Target &target,
                        bool with_declarations = true,
                        std::optional<int64_t> known_key = std::nullopt)
{
  const StructRNA *type = target.ptr.type;
  std::vector<Value> bases;
  if (type) {
    for (const StructRNA *base = type->base; base && bases.size() < 15; base = base->base) {
      bases.push_back(Value::string(base->identifier));
    }
  }
  const Owner owner = target_owner(context, target);
  return moved_tuple(
      Value::string("NODE"), address_value(address),
      type ? moved_tuple(Value::string(type->identifier), Value::tuple(std::move(bases)),
                         type->nameproperty ? Value::string(type->nameproperty->identifier) : Value{}) :
             Value{},
      moved_tuple(Value::boolean_value(owner.exists),
                  owner.exists ? Value::string(owner.id_type) : Value{},
                  Value::boolean_value(owner.original), Value::boolean_value(owner.bound_scene),
                  Value::boolean_value(owner.self)),
      Value::boolean_value(target.data_root),
      Value::boolean_value(type && (type->flag & STRUCT_ID) && !target.virtual_storage),
      Value::string(target.virtual_kind),
      with_declarations ? Value::int_value(known_key ? *known_key : declarations_key(context, target)) :
                          Value{});
}

static Value missing_detail(const char *kind, int edge, Value length = {}, Value member = {})
{
  return Value::dict({{"status", Value::string("MISSING")}, {"kind", Value::string(kind)},
                      {"edge", Value::int_value(edge)}, {"length", std::move(length)},
                      {"member", std::move(member)}});
}

static const Step &address_step(const Address &address, int index)
{
  return index < int(address.parsed.size()) ? address.parsed[index] :
                                           address.expansion[index - address.parsed.size()];
}

static bool admit_edge(const Context &context,
                       const Address &address,
                       const Allowance &allowance,
                       int edge,
                       const Target &owner,
                       PropertyRNA &property,
                       Value &failure)
{
  const int64_t current = fingerprint(context, owner, property);
  for (const auto &[count, admitted] : allowance.edges) {
    if (count == edge + 1) {
      if (current != admitted) {
        failure = stale(address, edge);
        return false;
      }
      return true;
    }
  }
  failure = admission(address, edge, describe_property(context, owner, property));
  return false;
}

static const IDProperty *stored_member(const Target &owner,
                                       const PropertyRNA &property,
                                       bool &valid)
{
  const IDProperty *group = nullptr;
  if (owner.virtual_storage) {
    group = owner.storage;
  }
  else if (id_storage_route(owner.ptr.type) && owner.ptr.owner_id &&
           owner.ptr.owner_id == owner.ptr.data)
  {
    group = owner.ptr.owner_id->system_properties;
  }
  else if (source_storage_route(owner.ptr.type)) {
    PointerRNA pointer = owner.ptr;
    IDProperty **slot = pointer.type->system_idproperties(&pointer);
    group = slot ? *slot : nullptr;
  }
  else {
    valid = false;
    return nullptr;
  }
  valid = !group || (group->type == IDP_GROUP && storage_group_valid(*group));
  return valid && group ? IDP_GetPropertyFromGroup(group, property.identifier) : nullptr;
}

static bool storage_matches(const PropertyRNA &declaration, const IDProperty &stored)
{
  if (declaration.totarraylength || declaration.getlength) {
    return stored.type == IDP_ARRAY && storage_array_valid(stored) &&
           stored.len == int(declaration.totarraylength) &&
           (declaration.type == PROP_BOOLEAN ? ELEM(stored.subtype, IDP_BOOLEAN, IDP_INT) :
            declaration.type == PROP_INT ? stored.subtype == IDP_INT :
            declaration.type == PROP_FLOAT ? ELEM(stored.subtype, IDP_FLOAT, IDP_DOUBLE) : false);
  }
  switch (declaration.type) {
    case PROP_BOOLEAN:
      return ELEM(stored.type, IDP_BOOLEAN, IDP_INT);
    case PROP_INT:
    case PROP_ENUM:
      return stored.type == IDP_INT;
    case PROP_FLOAT:
      return ELEM(stored.type, IDP_FLOAT, IDP_DOUBLE);
    case PROP_STRING:
      return stored.type == IDP_STRING && stored.subtype == IDP_STRING_SUB_UTF8 && stored.len >= 1 &&
             stored.totallen >= stored.len && stored.data.pointer;
    case PROP_POINTER: {
      const StructRNA *fixed = static_cast<const PointerPropertyRNA &>(declaration).pointer_type;
      return fixed && ((fixed->flag & STRUCT_ID) ?
                           (stored.type == IDP_ID ||
                            (stored.type == IDP_GROUP && storage_group_valid(stored))) :
                           stored.type == IDP_GROUP && storage_group_valid(stored));
    }
    case PROP_COLLECTION:
      return stored.type == IDP_IDPARRAY && storage_array_valid(stored);
  }
  return false;
}

static bool storage_child(const Context & /*context*/,
                          const Target &owner,
                          PropertyRNA &property,
                          Target &child,
                          Value &failure)
{
  bool valid = true;
  const IDProperty *storage = stored_member(owner, property, valid);
  if (!valid || (storage && !storage_matches(property, *storage))) {
    failure = unavailable("INVALID_STORAGE");
    return false;
  }
  child = owner;
  child.property = &property;
  child.storage = storage;
  child.data_root = false;
  if (property.type == PROP_COLLECTION) {
    child.kind = Target::Kind::Collection;
    child.virtual_storage = true;
    return true;
  }
  const StructRNA *fixed = static_cast<const PointerPropertyRNA &>(property).pointer_type;
  if (fixed && (fixed->flag & STRUCT_ID)) {
    /* Ordinary RNA/legacy Access treat valid legacy GROUP ID storage as current null;
     * never reinterpret that group's union as an ID pointer or materialize a replacement. */
    ID *candidate = storage && storage->type == IDP_ID ? IDP_ID_get(storage) : nullptr;
    if (!candidate) {
      child = {};
  child.operation = owner.operation;
      child.kind = Target::Kind::Null;
      return true;
    }
    ID *live = live_id_candidate(candidate);
    if (!live || !original_id(*live)) {
      failure = unavailable("INVALID_STORAGE");
      return false;
    }
    StructRNA *actual = ID_code_to_RNA_type(GS(live->name));
    if (!derives(actual, fixed)) {
      failure = unavailable("INVALID_STORAGE");
      return false;
    }
    /* Each encountered ID refiner is independently admitted before invocation; no ordinary
     * pointer factory or cold Python class construction is used for stored IDs. */
    child = {};
  child.operation = owner.operation;
    child.ptr = PointerRNA(live, actual, live);
    if (!refine_checked(child.ptr)) {
      failure = unavailable("NATIVE_READER_REQUIRED");
      return false;
    }
    return true;
  }
  child.kind = Target::Kind::Struct;
  child.ptr.type = const_cast<StructRNA *>(fixed);
  child.ptr.data = const_cast<IDProperty *>(storage);
  child.virtual_storage = true;
  child.virtual_kind = "STORED_GROUP";
  child.property = nullptr;
  return fixed != nullptr;
}

static bool const_material_selection(const Target &owner,
                                     bool active,
                                     Material *&material,
                                     Value &failure)
{
  material = nullptr;
  ID *object_id = live_id_candidate(owner.ptr.owner_id);
  if (!object_id || GS(object_id->name) != ID_OB || !original_id(*object_id)) {
    failure = unavailable("DENIED");
    return false;
  }
  const Object &object = *reinterpret_cast<const Object *>(object_id);
  if (object.totcol < 0) {
    failure = unavailable("INVALID_STORAGE");
    return false;
  }
  int act = object.actcol;
  if (active) {
    if (object.totcol == 0) {
      return true;
    }
  }
  else {
    const uintptr_t index = uintptr_t(owner.ptr.data) - uintptr_t(object_id);
    if (index >= uint64_t(object.totcol)) {
      failure = unavailable("INVALID_STORAGE");
      return false;
    }
    act = int(index) + 1;
  }
  short expected = 0;
  switch (object.type) {
    case OB_MESH: expected = ID_ME; break;
    case OB_CURVES_LEGACY:
    case OB_FONT:
    case OB_SURF: expected = ID_CU_LEGACY; break;
    case OB_MBALL: expected = ID_MB; break;
    case OB_CURVES: expected = ID_CV; break;
    case OB_POINTCLOUD: expected = ID_PT; break;
    case OB_VOLUME: expected = ID_VO; break;
    case OB_GREASE_PENCIL: expected = ID_GP; break;
    default: return true; /* Original material count helper returns nullptr. */
  }
  ID *data = live_id_candidate(object.data);
  if (!data || GS(data->name) != expected || !original_id(*data)) {
    failure = unavailable("INVALID_STORAGE");
    return false;
  }
  /* These two helpers only select stored count/array field addresses (material.cc351-415).
   * Read the addresses locally; never pass them to assignment or repair helpers. */
  Object *borrowed = const_cast<Object *>(&object);
  const short *count = BKE_object_material_len_p(borrowed);
  if (!count || *count == 0) {
    return true;
  }
  if (*count < 0) {
    failure = unavailable("INVALID_STORAGE");
    return false;
  }
  const int slot = std::clamp(act - 1, 0, int(*count) - 1);
  const int effective_object_count = std::min(int(*count), int(object.totcol));
  if (slot < effective_object_count && object.matbits && object.matbits[slot]) {
    if (!object.mat) {
      failure = unavailable("INVALID_STORAGE");
      return false;
    }
    material = object.mat[slot];
  }
  else {
    Material ***array = BKE_object_material_array_p(borrowed);
    material = array && *array ? (*array)[slot] : nullptr;
  }
  if (material) {
    ID *live = live_id_candidate(material);
    if (!live || GS(live->name) != ID_MA || !original_id(*live)) {
      failure = unavailable("DENIED");
      return false;
    }
    material = reinterpret_cast<Material *>(live);
    if (object.type == OB_GREASE_PENCIL && !material->gp_style) {
      material = nullptr;
    }
  }
  return true;
}

static bool pure_material_pointer(const Target &owner,
                                  const RouteFacts &routes,
                                  Target &child,
                                  Value &failure)
{
  Material *material;
  if (!const_material_selection(owner, routes.active_material_special, material, failure)) {
    return false;
  }
  child = {};
  child.operation = owner.operation;
  if (!material) {
    child.kind = Target::Kind::Null;
    return true;
  }
  child.ptr = PointerRNA(&material->id, RNA_Material, material);
  if (!refine_checked(child.ptr)) {
    failure = unavailable("NATIVE_READER_REQUIRED");
    return false;
  }
  return true;
}

static const char *helper_failure_reason(const angelo_read_scalar::Status status)
{
  switch (status) {
    case angelo_read_scalar::Status::OK:
      return nullptr;
    case angelo_read_scalar::Status::INVALID_STORAGE:
      return "INVALID_STORAGE";
    case angelo_read_scalar::Status::RESOURCE_LIMIT:
      return "RESOURCE_LIMIT";
    case angelo_read_scalar::Status::UNSUPPORTED:
      return "NATIVE_READER_REQUIRED";
    case angelo_read_scalar::Status::EXTENSION_CODE:
      return "EXTENSION_CODE";
    case angelo_read_scalar::Status::NOT_EVALUATED:
      return "NOT_EVALUATED";
    case angelo_read_scalar::Status::LOADS_FILE:
      return "LOADS_FILE";
  }
  return "INVALID_STORAGE";
}

static const char *helper_failure_reason(const angelo_read_geometry::Status status)
{
  switch (status) {
    case angelo_read_geometry::Status::OK:
      return nullptr;
    case angelo_read_geometry::Status::INVALID_STORAGE:
      return "INVALID_STORAGE";
    case angelo_read_geometry::Status::UNSUPPORTED:
      return "NATIVE_READER_REQUIRED";
    case angelo_read_geometry::Status::NOT_EVALUATED:
      return "NOT_EVALUATED";
    case angelo_read_geometry::Status::LOADS_FILE:
      return "LOADS_FILE";
    case angelo_read_geometry::Status::RESOURCE_LIMIT:
      return "RESOURCE_LIMIT";
  }
  return "INVALID_STORAGE";
}

static const char *helper_failure_reason(const angelo_read_traversal::Status status)
{
  return status == angelo_read_traversal::Status::OK ? nullptr :
         status == angelo_read_traversal::Status::INVALID_STORAGE ? "INVALID_STORAGE" :
                                                                    "NATIVE_READER_REQUIRED";
}

static bool copy_source_string(const Target &owner,
                               PropertyRNA &property,
                               const RouteFacts &routes,
                               std::string &text,
                               Value &failure)
{
  if (routes.scalar_value) {
    if (!owner.operation) {
      failure = unavailable("INVALID_STORAGE");
      return false;
    }
    const auto copied = angelo_read_scalar::value(owner.operation->scalar,
        routes.scalar_value, owner.ptr, property);
    if (copied.status != angelo_read_scalar::Status::OK) {
      failure = unavailable(helper_failure_reason(copied.status));
      return false;
    }
    const std::string *string = std::get_if<std::string>(&copied.data);
    if (!string) {
      failure = unavailable("INVALID_STORAGE");
      return false;
    }
    text = *string;
  }
  else if (routes.material_slot_special) {
    Material *material;
    if (!const_material_selection(owner, false, material, failure)) {
      return false;
    }
    text = material ? material->id.name + 2 : "";
  }
  else {
    PointerRNA pointer = owner.ptr;
    text = RNA_property_string_get(&pointer, &property);
  }
  return true;
}

static bool pure_socket_node(const Target &socket, Target &child, Value &failure)
{
  ID *id = live_id_candidate(socket.ptr.owner_id);
  if (!id || GS(id->name) != ID_NT || !original_id(*id)) {
    failure = unavailable("DENIED");
    return false;
  }
  const bNodeTree &tree = *reinterpret_cast<const bNodeTree *>(id);
  for (const bNode &node : tree.nodes) {
    for (const ListBase *sockets : {&node.inputs, &node.outputs}) {
      for (const bNodeSocket *candidate = static_cast<const bNodeSocket *>(sockets->first);
           candidate; candidate = candidate->next) {
        if (candidate != socket.ptr.data) {
          continue;
        }
        child = {};
    child.operation = socket.operation;
        child.ptr = PointerRNA(id, RNA_Node, const_cast<bNode *>(&node));
        if (!refine_checked(child.ptr)) {
          failure = unavailable("NATIVE_READER_REQUIRED");
          return false;
        }
        return true;
      }
    }
  }
  /* Ordinary node_find_node assumes this membership; no topology-cache ensure is needed. */
  failure = unavailable("INVALID_STORAGE");
  return false;
}

static bool registry_properties(const Target &owner, const RouteFacts &facts,
                                Target &child, Value &failure)
{
  if (!owner.operation || !owner.ptr.data) {
    failure = unavailable("INVALID_STORAGE");
    return false;
  }
  ID *collection_id = owner.operation->live(owner.ptr.owner_id);
  if (!collection_id || GS(collection_id->name) != ID_GR || !original_id(*collection_id)) {
    failure = unavailable("INVALID_STORAGE");
    return false;
  }
  const Collection &collection = *reinterpret_cast<const Collection *>(collection_id);
  bool listed = facts.registry_properties_kind == 1 ? collection.importer == owner.ptr.data : false;
  if (facts.registry_properties_kind == 2) {
    for (const CollectionExport &record : collection.exporters) {
      listed |= &record == owner.ptr.data;
    }
  }
  if (!listed) {
    failure = unavailable("INVALID_STORAGE");
    return false;
  }
  const char *handler_name;
  IDProperty *group;
  if (facts.registry_properties_kind == 1) {
    const CollectionImport &record = *static_cast<const CollectionImport *>(owner.ptr.data);
    handler_name = record.fh_idname;
    group = record.import_properties;
  }
  else {
    const CollectionExport &record = *static_cast<const CollectionExport *>(owner.ptr.data);
    handler_name = record.fh_idname;
    group = record.export_properties;
  }
  if (!memchr(handler_name, 0, sizeof(CollectionExport::fh_idname))) {
    failure = unavailable("INVALID_STORAGE");
    return false;
  }
  if (group && (group->type != IDP_GROUP || !storage_group_valid(*group))) {
    failure = unavailable("INVALID_STORAGE");
    return false;
  }
  StructRNA *type = RNA_IDPropertyWrapPtr;
  /* Both registries already exist during a Blender Python read session. These lookups invoke
   * no operator or file-handler callbacks. quiet=true preserves target/miss without logging. */
  if (const bke::FileHandlerType *handler = bke::file_handler_find(handler_name)) {
    const char *operator_name = facts.registry_properties_kind == 1 ?
                                   handler->import_operator : handler->export_operator;
    if (!memchr(operator_name, 0, OP_MAX_TYPENAME)) {
      failure = unavailable("INVALID_STORAGE");
      return false;
    }
    if (const wmOperatorType *operator_type = WM_operatortype_find(operator_name, true)) {
      if (!registered_type(operator_type->srna) ||
          !derives(operator_type->srna, RNA_OperatorProperties))
      {
        failure = unavailable("INVALID_STORAGE");
        return false;
      }
      type = operator_type->srna;
    }
  }
  if (!registered_type(type) || !known_refiner(type)) {
    failure = unavailable("NATIVE_READER_REQUIRED");
    return false;
  }
  child = {};
  child.operation = owner.operation;
  child.ptr = PointerRNA(owner.ptr.owner_id, type, group);
  child.kind = group ? Target::Kind::Struct : Target::Kind::Null;
  if (group && !refine_checked(child.ptr)) {
    failure = unavailable("NATIVE_READER_REQUIRED");
    return false;
  }
  return true;
}

static bool pure_bake_node(const Target &owner, Target &child, Value &failure)
{
  ID *id = owner.operation ? owner.operation->live(owner.ptr.owner_id) : nullptr;
  if (!id || GS(id->name) != ID_OB || !original_id(*id) || !owner.ptr.data) {
    failure = unavailable("INVALID_STORAGE");
    return false;
  }
  const Object &object = *reinterpret_cast<const Object *>(id);
  const NodesModifierBake *bake = static_cast<const NodesModifierBake *>(owner.ptr.data);
  const NodesModifierData *modifier = nullptr;
  for (const ModifierData &candidate : object.modifiers) {
    if (candidate.type != eModifierType_Nodes) {
      continue;
    }
    const NodesModifierData &nodes = reinterpret_cast<const NodesModifierData &>(candidate);
    if (nodes.bakes_num < 0 || (nodes.bakes_num && !nodes.bakes)) {
      failure = unavailable("INVALID_STORAGE");
      return false;
    }
    for (int index = 0; index < nodes.bakes_num; index++) {
      if (&nodes.bakes[index] == bake) {
        modifier = &nodes;
        break;
      }
    }
    if (modifier) {
      break;
    }
  }
  if (!modifier) {
    failure = unavailable("INVALID_STORAGE");
    return false;
  }
  child = {};
  child.operation = owner.operation;
  child.kind = Target::Kind::Null;
  const bNodeTree *tree = modifier->node_group;
  int32_t nested_id = bake->id;
  std::set<std::pair<uintptr_t, int32_t>> visited;
  while (tree) {
    ID *tree_id = owner.operation->live(tree);
    if (!tree_id || GS(tree_id->name) != ID_NT || !original_id(*tree_id) ||
        !visited.emplace(uintptr_t(tree), nested_id).second)
    {
      failure = unavailable("INVALID_STORAGE");
      return false;
    }
    tree = reinterpret_cast<const bNodeTree *>(tree_id);
    if (!tree->runtime || tree->nested_node_refs_num < 0 ||
        (tree->nested_node_refs_num && !tree->nested_node_refs))
    {
      failure = unavailable("INVALID_STORAGE");
      return false;
    }
    const bNestedNodeRef *ref = tree->find_nested_node_ref(nested_id);
    if (!ref) {
      return true;
    }
    if (ref->path.node_id < 0) {
      failure = unavailable("INVALID_STORAGE");
      return false;
    }
    const bNode *node = tree->node_by_id(ref->path.node_id);
    if (!node) {
      return true;
    }
    if (!node->is_group()) {
      child.ptr = PointerRNA(tree_id, RNA_Node, const_cast<bNode *>(node));
      if (!node->typeinfo || !refine_checked(child.ptr)) {
        failure = unavailable("INVALID_STORAGE");
        return false;
      }
      child.kind = Target::Kind::Struct;
      return true;
    }
    tree = reinterpret_cast<const bNodeTree *>(node->id);
    nested_id = ref->path.id_in_node;
  }
  return true;
}

static bool pure_object_data(const Target &owner, Target &child, Value &failure)
{
  const Object &object = *static_cast<const Object *>(owner.ptr.data);
  ID *candidate = static_cast<ID *>(object.data);
  ID *live = live_id_candidate(candidate);
  if (!candidate) {
    child = {};
    child.operation = owner.operation;
    child.kind = Target::Kind::Null;
    return true;
  }
  if (!live || !original_id(*live)) {
    failure = unavailable("DENIED");
    return false;
  }
  if (object.type == OB_MESH) {
    if (GS(live->name) != ID_ME) {
      failure = unavailable("INVALID_STORAGE");
      return false;
    }
    const Mesh &mesh = *reinterpret_cast<const Mesh *>(live);
    if (!mesh.runtime || !owner.operation) {
      failure = unavailable("INVALID_STORAGE");
      return false;
    }
    /* RNA Object.data starts from ob.data. Blender's getter would compute a pending
     * subdivision wrapper here; the read reports it as not evaluated instead. */
    const Mesh *selected = nullptr;
    const angelo_read_geometry::Status status = angelo_read_geometry::mesh_subdivision_for_read(
        owner.operation->geometry, mesh, selected);
    if (const char *reason = helper_failure_reason(status)) {
      failure = unavailable(reason);
      return false;
    }
    if (!selected) {
      child = {};
      child.operation = owner.operation;
      child.kind = Target::Kind::Null;
      return true;
    }
    if (selected != &mesh) {
      /* Warm managed and cold detached results live through this operation's geometry Scope. */
      live = owner.operation->borrow_mesh_cache(const_cast<Mesh *>(selected));
      if (!live || !original_id(*live)) {
        failure = unavailable("DENIED");
        return false;
      }
    }
  }
  child = {};
  child.operation = owner.operation;
  child.ptr = PointerRNA(live, ID_code_to_RNA_type(GS(live->name)), live);
  if (!refine_checked(child.ptr)) {
    failure = unavailable("NATIVE_READER_REQUIRED");
    return false;
  }
  return true;
}

/* A selected temporary child stays under this guard through ALL recursive descent and copying.
 * No fallback lookup helper may return a pointer after closing its own iterator. */
class IteratorGuard {
 public:
  CollectionPropertyIterator iterator = {};
  IteratorGuard(PointerRNA &owner, PropertyRNA &property)
  {
    RNA_property_collection_begin(&owner, &property, &iterator);
  }
  ~IteratorGuard()
  {
    RNA_property_collection_end(&iterator);
  }
  IteratorGuard(const IteratorGuard &) = delete;
  IteratorGuard &operator=(const IteratorGuard &) = delete;
};

using Consume = std::function<Value(const Target &, int)>;
static Value replay(const Context &, const Address &, const Allowance &, int, const Target &,
                    const Consume &);

static bool owned_collection_items(const Target &collection,
                                   const RouteFacts &facts,
                                   const std::vector<PointerRNA> *&items)
{
  if (!collection.operation) {
    return false;
  }
  const auto key = std::make_pair(uintptr_t(collection.ptr.data), uintptr_t(collection.property));
  auto &cache = collection.operation->collections;
  if (const auto found = cache.find(key); found != cache.end()) {
    items = &found->second.values;
    return true;
  }
  OperationScope::Items owned;
  if (facts.owned_collection_kind == 1) {
    ID *id = collection.operation->live(collection.ptr.owner_id);
    if (!id || GS(id->name) != ID_NT || !original_id(*id)) {
      return false;
    }
    const bNodeTree &tree = *reinterpret_cast<const bNodeTree *>(id);
    if (tree.runtime) {
      bool valid = true;
      tree.tree_interface.foreach_item([&](const bNodeTreeInterfaceItem &item) {
        PointerRNA pointer(id, RNA_NodeTreeInterfaceItem,
                             const_cast<bNodeTreeInterfaceItem *>(&item), collection.ptr);
        valid = refine_checked(pointer);
        if (valid) {
          owned.values.push_back(std::move(pointer));
        }
        return valid;
      }, false);
      if (!valid) {
        return false;
      }
    }
  }
  else if (facts.owned_collection_kind == 2) {
    ID *root = collection.operation->live(collection.ptr.data);
    if (!root || GS(root->name) != ID_GR || !original_id(*root)) {
      return false;
    }
    std::vector<const Collection *> pending{reinterpret_cast<const Collection *>(root)};
    std::unordered_set<const Collection *> visited;
    std::unordered_set<const Object *> seen;
    while (!pending.empty()) {
      const Collection *candidate = pending.back();
      pending.pop_back();
      ID *id = collection.operation->live(candidate);
      if (!id || GS(id->name) != ID_GR || !original_id(*id)) {
        return false;
      }
      candidate = reinterpret_cast<const Collection *>(id);
      if (!visited.insert(candidate).second) {
        continue;
      }
      for (const CollectionObject &member : candidate->gobject) {
        ID *object = collection.operation->live(member.ob);
        if (!object || GS(object->name) != ID_OB || !original_id(*object)) {
          return false;
        }
        const Object *actual = reinterpret_cast<const Object *>(object);
        if (seen.insert(actual).second) {
          owned.values.emplace_back(object, RNA_Object, const_cast<Object *>(actual));
        }
      }
      /* Stack reverse insertion retains own-object order then stored children DFS order.
       * No instance_collection descent: ordinary all_objects uses with_instances=false. */
      std::vector<const Collection *> children;
      for (const CollectionChild &child : candidate->children) {
        children.push_back(child.collection);
      }
      pending.insert(pending.end(), children.rbegin(), children.rend());
    }
  }
  else if (facts.owned_collection_kind == 3) {
    ID *id = collection.operation->live(collection.ptr.owner_id);
    if (!id || GS(id->name) != ID_SCE || !original_id(*id)) {
      return false;
    }
    const Scene &scene = *reinterpret_cast<const Scene *>(id);
    if (!scene.ed || scene.ed != collection.ptr.data) {
      return false;
    }
    std::vector<const Strip *> pending;
    for (const Strip &strip : scene.ed->seqbase) {
      pending.push_back(&strip);
    }
    std::reverse(pending.begin(), pending.end());
    std::unordered_set<const Strip *> visited;
    while (!pending.empty()) {
      const Strip *strip = pending.back();
      pending.pop_back();
      if (!visited.insert(strip).second) {
        return false; /* Invalid cycle/shared-list storage cannot masquerade as a finite result. */
      }
      PointerRNA pointer(id, RNA_Strip, const_cast<Strip *>(strip), collection.ptr);
      if (!refine_checked(pointer)) {
        return false;
      }
      owned.values.push_back(std::move(pointer));
      if (strip->type == STRIP_TYPE_META) {
        std::vector<const Strip *> children;
        for (const Strip &child : strip->seqbase) {
          children.push_back(&child);
        }
        pending.insert(pending.end(), children.rbegin(), children.rend());
      }
    }
  }
  else {
    return false;
  }
  auto inserted = cache.emplace(key, std::move(owned));
  items = &inserted.first->second.values;
  return true;
}

static bool owned_collection_item(const Target &collection, int index, Target &item)
{
  const RouteFacts facts = routes_for(collection, *collection.property);
  const std::vector<PointerRNA> *items;
  if (!owned_collection_items(collection, facts, items) || index < 0 || index >= items->size()) {
    return false;
  }
  item = {};
  item.operation = collection.operation;
  item.ptr = (*items)[index];
  return true;
}

static bool const_attribute_source(const Target &collection,
                                   const RouteFacts &facts,
                                   const bke::Attribute *&attribute,
                                   int &length)
{
  if (!collection.ptr.owner_id || GS(collection.ptr.owner_id->name) != ID_ME) {
    return false;
  }
  const Mesh &mesh = *reinterpret_cast<const Mesh *>(collection.ptr.owner_id);
  length = mesh.runtime->edit_mesh ? 0 : mesh.corners_num;
  if (length < 0) {
    return false;
  }
  if (mesh.runtime->edit_mesh) {
    attribute = nullptr; /* Ordinary legacy element collection is empty in edit mode. */
    return true;
  }
  attribute = nullptr;
  for (const bke::Attribute &stored : mesh.attribute_storage.wrap()) {
    if (&stored == collection.ptr.data) {
      attribute = &stored;
      break;
    }
  }
  if (!attribute || attribute->domain() != bke::AttrDomain::Corner ||
      attribute->data_type() != (facts.const_attribute_kind == 1 ? bke::AttrType::Float2 :
                                                                  bke::AttrType::ColorByte))
  {
    return false;
  }
  if (const auto *data = std::get_if<bke::Attribute::ArrayData>(&attribute->data())) {
    return data->size == length && (length == 0 || data->data);
  }
  const auto *data = std::get_if<bke::Attribute::SingleData>(&attribute->data());
  return data && (length == 0 || data->value);
}

static bool const_attribute_item(const Target &collection, int index, Target &item)
{
  const RouteFacts facts = routes_for(collection, *collection.property);
  const bke::Attribute *attribute;
  int length;
  if (!facts.const_attribute_kind ||
      !const_attribute_source(collection, facts, attribute, length) ||
      index < 0 || index >= length)
  {
    return false;
  }
  const void *value;
  if (const auto *data = std::get_if<bke::Attribute::ArrayData>(&attribute->data())) {
    const size_t stride = facts.const_attribute_kind == 1 ? sizeof(float2) : sizeof(MLoopCol);
    value = static_cast<const std::byte *>(data->data) + size_t(index) * stride;
  }
  else {
    value = std::get<bke::Attribute::SingleData>(attribute->data()).value;
  }
  item = {};
  item.operation = collection.operation;
  item.ptr = PointerRNA(collection.ptr.owner_id,
                         static_cast<CollectionPropertyRNA *>(collection.property)->item_type,
                         const_cast<void *>(value), collection.ptr);
  item.attribute_source = attribute;
  item.attribute_element_index = index;
  item.geometry_item.pointer = item.ptr;
  item.geometry_item.index = index;
  item.geometry_item.attribute_source = attribute;
  return true;
}

static bool const_legacy_uv_pin(const Target &element, bool &value)
{
  const Mesh &mesh = *reinterpret_cast<const Mesh *>(element.ptr.owner_id);
  const bke::Attribute *selected = element.attribute_source;
  int index = element.attribute_element_index;
  if (!selected || index < 0 || index >= mesh.corners_num) {
    return false;
  }
  if (std::holds_alternative<bke::Attribute::ArrayData>(selected->data())) {
    /* Match ordinary reverse lookup's first containing float2 array, including shared arrays.
     * Integer address comparisons avoid subtraction between unrelated storage allocations. */
    selected = nullptr;
    const uintptr_t pointer = uintptr_t(element.ptr.data);
    for (const bke::Attribute &attribute : mesh.attribute_storage.wrap()) {
      if (attribute.domain() != bke::AttrDomain::Corner ||
          attribute.data_type() != bke::AttrType::Float2)
      {
        continue;
      }
      const auto *data = std::get_if<bke::Attribute::ArrayData>(&attribute.data());
      if (!data || !data->data || data->size < 0 || pointer < uintptr_t(data->data)) {
        continue;
      }
      const uintptr_t offset = (pointer - uintptr_t(data->data)) / sizeof(float2);
      if (offset < uint64_t(data->size)) {
        selected = &attribute;
        index = int(offset);
        break;
      }
    }
    if (!selected) {
      value = false;
      return true;
    }
  }
  /* SINGLE would become its own unique expanded array under the ordinary getter. Preserve
   * the requested logical element index without creating that array or losing its pin index. */
  if (selected->name().size() >= MAX_CUSTOMDATA_LAYER_NAME - 4) {
    return false;
  }
  char buffer[MAX_CUSTOMDATA_LAYER_NAME];
  const StringRef pin_name = BKE_uv_map_pin_name_get(selected->name(), buffer);
  const bke::Attribute *pins = mesh.attribute_storage.wrap().lookup(pin_name);
  if (!pins) {
    value = false;
    return true;
  }
  if (pins->domain() != bke::AttrDomain::Corner || pins->data_type() != bke::AttrType::Bool) {
    return false; /* Source-owned geometry helper must close domain/type adaptation. */
  }
  if (const auto *data = std::get_if<bke::Attribute::ArrayData>(&pins->data())) {
    if (!data->data || data->size != mesh.corners_num || index >= data->size) {
      return false;
    }
    value = static_cast<const bool *>(data->data)[index];
  }
  else {
    const auto *single = std::get_if<bke::Attribute::SingleData>(&pins->data());
    if (!single || !single->value) {
      return false;
    }
    value = *static_cast<const bool *>(single->value);
  }
  return true;
}

/* Helpers own their temporary geometry/log snapshots through OperationScope. Their selected
 * children are consumed recursively before that scope ends; no helper borrow enters transport. */

static bool helper_collection_item(const Target &collection, int index, Target &item,
                                   Value &failure)
{
  if (!collection.operation) {
    failure = unavailable("INVALID_STORAGE");
    return false;
  }
  const RouteFacts facts = routes_for(collection, *collection.property);
  item = {};
  item.operation = collection.operation;
  if (facts.geometry_collection) {
    const auto status = angelo_read_geometry::item(collection.operation->geometry,
        facts.geometry_collection, collection.ptr, *collection.property, index, item.geometry_item);
    if (const char *reason = helper_failure_reason(status)) {
      failure = unavailable(reason);
      return false;
    }
    item.ptr = item.geometry_item.pointer;
    item.attribute_source = item.geometry_item.attribute_source;
    item.attribute_element_index = int(item.geometry_item.index);
  }
  else if (facts.traversal_collection) {
    const auto status = angelo_read_traversal::item(collection.operation->traversal,
        facts.traversal_collection, collection.ptr, *collection.property, index, item.traversal_item);
    if (const char *reason = helper_failure_reason(status)) {
      failure = unavailable(reason);
      return false;
    }
    item.ptr = item.traversal_item.pointer;
  }
  else {
    failure = unavailable("NATIVE_READER_REQUIRED");
    return false;
  }
  if (item.ptr.data && !refine_checked(item.ptr)) {
    failure = unavailable("NATIVE_READER_REQUIRED");
    return false;
  }
  if (facts.geometry_collection) {
    item.geometry_item.pointer = item.ptr;
  }
  if (facts.traversal_collection) {
    item.traversal_item.pointer = item.ptr;
  }
  item.kind = item.ptr.data ? Target::Kind::Struct : Target::Kind::Null;
  return true;
}

/* Ordinary callbacks assume these valid-storage preconditions. Check before calling so
 * invalid stored owners cannot turn a read into an unchecked dereference. */
static bool pointer_storage_valid(const Target &owner, const PropertyRNA &property)
{
  if (manual_callback(property, RNAReadCallbackRole::GET,
                       "rna_modifier.cc", "rna_SoftBodyModifier_point_cache_get"))
  {
    ID *id = owner.operation ? owner.operation->live(owner.ptr.owner_id) : nullptr;
    if (!id || GS(id->name) != ID_OB || !original_id(*id)) {
      return false;
    }
    const Object &object = *reinterpret_cast<const Object *>(id);
    return object.soft && object.soft->shared;
  }
  if (manual_callback(property, RNAReadCallbackRole::GET,
                       "rna_rigidbody.cc", "rna_RigidBodyWorld_PointCache_get"))
  {
    return owner.ptr.data && static_cast<const RigidBodyWorld *>(owner.ptr.data)->shared;
  }
  if (manual_callback(property, RNAReadCallbackRole::GET,
                       "rna_sculpt_paint.cc", "rna_ParticleEdit_brush_get"))
  {
    if (!owner.ptr.data) {
      return false;
    }
    const ParticleEditSettings &settings = *static_cast<const ParticleEditSettings *>(owner.ptr.data);
    return settings.brushtype >= 0 && settings.brushtype < int(ARRAY_SIZE(settings.brush));
  }
  return true;
}

static bool helper_pointer(const Target &owner, const RouteFacts &facts,
                           Target &child, Value &failure)
{
  if (!owner.operation) {
    failure = unavailable("INVALID_STORAGE");
    return false;
  }
  child = {};
  child.operation = owner.operation;
  const auto status = angelo_read_traversal::pointer(owner.operation->traversal,
      facts.traversal_pointer, owner.ptr, *owner.property, child.ptr);
  if (const char *reason = helper_failure_reason(status)) {
    failure = unavailable(reason);
    return false;
  }
  if (child.ptr.data && !refine_checked(child.ptr)) {
    failure = unavailable("NATIVE_READER_REQUIRED");
    return false;
  }
  child.kind = child.ptr.data ? Target::Kind::Struct : Target::Kind::Null;
  return true;
}

static bool collection_length(const Target &target, int &length, const char **reason = nullptr)
{
  if (reason) {
    *reason = nullptr;
  }
  const RouteFacts current = routes_for(target, *target.property);
  if (current.geometry_collection || current.traversal_collection) {
    if (!target.operation) {
      return false;
    }
    int64_t native_length = -1;
    const char *failure = current.geometry_collection ?
        helper_failure_reason(angelo_read_geometry::length(target.operation->geometry,
          current.geometry_collection, target.ptr, *target.property, native_length)) :
        helper_failure_reason(angelo_read_traversal::length(target.operation->traversal,
          current.traversal_collection, target.ptr, *target.property, native_length));
    if (failure || native_length < 0 || native_length > INT_MAX) {
      if (reason) {
        *reason = failure ? failure : "INVALID_STORAGE";
      }
      return false;
    }
    length = int(native_length);
    return true;
  }
  if (current.owned_collection_kind) {
    const std::vector<PointerRNA> *items;
    if (!owned_collection_items(target, current, items) || items->size() > INT_MAX) {
      return false;
    }
    length = int(items->size());
    return true;
  }
  if (current.const_attribute_kind) {
    const bke::Attribute *attribute;
    return const_attribute_source(target, current, attribute, length);
  }
  if (current.mesh_collection_kind) {
    const Mesh &mesh = *static_cast<const Mesh *>(target.ptr.data);
    length = current.mesh_collection_kind == 1 ? mesh.verts_num :
             current.mesh_collection_kind == 2 ? mesh.edges_num :
             current.mesh_collection_kind == 3 ? mesh.corners_num : mesh.faces_num;
    return length >= 0;
  }
  if (target.weight_vertex >= 0) {
    const Mesh &mesh = *static_cast<const Mesh *>(target.ptr.owner_id ?
                                                     static_cast<const void *>(target.ptr.owner_id) :
                                                     nullptr);
    const Span<MDeformVert> weights = mesh.deform_verts();
    if (weights.is_empty()) {
      length = 0;
      return true;
    }
    const MDeformVert &vertex = weights[target.weight_vertex];
    if (vertex.totweight < 0 || (vertex.totweight && !vertex.dw)) {
      return false;
    }
    length = vertex.totweight;
    return true;
  }
  if (target.virtual_storage) {
    if (target.storage && (target.storage->type != IDP_IDPARRAY ||
                           !storage_array_valid(*target.storage)))
    {
      return false;
    }
    length = target.storage ? target.storage->len : 0;
    return true;
  }
  const RouteFacts facts = routes_for(target, *target.property);
  if (const char *refusal = route_refusal(target, facts)) {
    if (reason) {
      *reason = refusal;
    }
    return false;
  }
  if (!STREQ(facts.length, "SOURCE_LENGTH")) {
    return false;
  }
  PointerRNA owner = target.ptr;
  length = RNA_property_collection_length(&owner, target.property);
  return length >= 0;
}

static bool weight_collection(Target &target)
{
  if (target.ptr.type != RNA_MeshVertex || !target.ptr.owner_id ||
      GS(target.ptr.owner_id->name) != ID_ME)
  {
    return false;
  }
  const Mesh &mesh = *reinterpret_cast<const Mesh *>(target.ptr.owner_id);
  const Span<float3> positions = mesh.vert_positions();
  const uintptr_t pointer = uintptr_t(target.ptr.data), base = uintptr_t(positions.data());
  if (pointer < base || pointer - base >= positions.size_in_bytes() ||
      (pointer - base) % sizeof(float3))
  {
    return false;
  }
  target.weight_vertex = (pointer - base) / sizeof(float3);
  return true;
}

static bool const_geometry_item(const Target &collection, int index, Target &item)
{
  const RouteFacts facts = routes_for(collection, *collection.property);
  const Mesh &mesh = *static_cast<const Mesh *>(collection.ptr.data);
  const void *data = nullptr;
  StructRNA *type = nullptr;
  switch (facts.mesh_collection_kind) {
    case 1: {
      const Span<float3> values = mesh.vert_positions();
      if (index >= values.size()) {
        return false;
      }
      data = &values[index];
      type = RNA_MeshVertex;
      break;
    }
    case 2: {
      const Span<int2> values = mesh.edges();
      if (index >= values.size()) {
        return false;
      }
      data = &values[index];
      type = RNA_MeshEdge;
      break;
    }
    case 3: {
      const Span<int> values = mesh.corner_verts();
      if (index >= values.size()) {
        return false;
      }
      data = &values[index];
      type = RNA_MeshLoop;
      break;
    }
    case 4: {
      const Span<int> values = mesh.face_offsets();
      if (index >= mesh.faces_num || index >= values.size()) {
        return false;
      }
      data = &values[index];
      type = RNA_MeshPolygon;
      break;
    }
    default:
      return false;
  }
  item = {};
  item.operation = collection.operation;
  item.ptr = PointerRNA(collection.ptr.owner_id, type, const_cast<void *>(data), collection.ptr);
  return true;
}

static Value select_collection(const Context &context,
                                const Address &address,
                                const Allowance &allowance,
                                int edge,
                                const Target &collection,
                                const Consume &consume)
{
  const Step &step = address_step(address, edge);
  const RouteFacts current = routes_for(collection, *collection.property);
  if (current.attribute_elements) {
    return unavailable("DENIED");
  }
  if (!collection.virtual_storage && collection.weight_vertex < 0) {
    if (const char *reason = route_refusal(collection, current)) {
      return unavailable(reason);
    }
  }
  int length;
  const char *length_reason = nullptr;
  if (!collection_length(collection, length, &length_reason)) {
    return unavailable(length_reason ? length_reason :
                       collection.virtual_storage || collection.weight_vertex >= 0 || current.const_attribute_kind ?
                           "INVALID_STORAGE" : "NATIVE_READER_REQUIRED");
  }
  int64_t index = step.index;
  if (step.kind == Step::Kind::Index && index < 0) {
    index += length;
  }
  if (step.kind == Step::Kind::Index && (index < 0 || index >= length)) {
    return missing_detail("INDEX_RANGE", edge, Value::int_value(length));
  }
  const StructRNA *fixed = static_cast<CollectionPropertyRNA *>(collection.property)->item_type;
  if (step.kind == Step::Kind::Key &&
      (collection.weight_vertex >= 0 || !fixed ||
       (!fixed->nameproperty && current.owned_collection_kind != 1 && !current.socket_key_kind)))
  {
    return missing_detail("NO_NAMES", edge, Value::int_value(length), Value::string(step.text));
  }
  std::vector<Value> needed_names;
  std::vector<int64_t> needed_fingerprints;
  auto need_names = [&]() {
    return Value::dict({{"status", Value::string("NEEDS_ADMISSION")},
                        {"address", address_value(address)}, {"edge", Value::int_value(edge)},
                        {"descriptions", Value::tuple(needed_names)}});
  };
  auto consume_candidate = [&](const Target &candidate, int candidate_index,
                                 const char *field_name = nullptr,
                                 const char *physical = nullptr) -> Value {
    if (step.kind == Step::Kind::Index) {
      return candidate_index == index ? replay(context, address, allowance, edge + 1,
                                              candidate, consume) : Value{};
    }
    if (candidate.kind == Target::Kind::Null || !candidate.ptr.type ||
        (!field_name && !candidate.ptr.type->nameproperty))
    {
      return {};
    }
    if (const char *reason = target_refusal(context, candidate)) {
      return unavailable(reason);
    }
    PropertyRNA *name = field_name ? RNA_struct_type_find_property(candidate.ptr.type, field_name) :
                                    candidate.ptr.type->nameproperty;
    if (!name || name->type != PROP_STRING) {
      return unavailable("UNSUPPORTED_VALUE");
    }
    const int64_t current = fingerprint(context, candidate, *name);
    const bool admitted = std::find(allowance.names.begin(), allowance.names.end(), current) !=
                          allowance.names.end();
    if (!admitted) {
      if (std::find(needed_fingerprints.begin(), needed_fingerprints.end(), current) ==
          needed_fingerprints.end())
      {
        needed_fingerprints.push_back(current);
        needed_names.push_back(describe_property(context, candidate, *name));
      }
      /* Fetch no newly encountered name. Keep scanning metadata to batch heterogeneous types;
       * no iterator or target pointer survives the admission result and original selector retry. */
      return needed_names.size() >= read_items_max ? need_names() : Value{};
    }
    std::string candidate_name;
    const RouteFacts routes = routes_for(candidate, *name);
    if (physical) {
      /* Exact source-admitted lookup semantics compare stored canonical identifier/name, as
       * original native lookup does, after current actual declaration's policy admission. */
      candidate_name = physical;
    }
    else if (one_of(routes.value, {"ID_SYSTEM_STORAGE", "VIRTUAL_STORAGE", "SOURCE_STORAGE_LOCATOR"})) {
      bool valid = true;
      const IDProperty *stored = stored_member(candidate, *name, valid);
      if (!valid || (stored && !storage_matches(*name, *stored))) {
        return unavailable("INVALID_STORAGE");
      }
      if (stored) {
        if (stored->len - 1 != int(step.text.size())) {
          return {};
        }
        candidate_name.assign(IDP_string_get(stored), stored->len - 1);
      }
      else {
        const char *default_name = static_cast<StringPropertyRNA *>(name)->defaultvalue;
        candidate_name = default_name ? default_name : "";
      }
    }
    else if (STREQ(routes.value, "SOURCE_SCALAR") && name->type == PROP_STRING) {
      Value failure;
      if (!copy_source_string(candidate, *name, routes, candidate_name, failure)) {
        return failure;
      }
    }
    else {
      return unavailable("COMPUTED_VALUE");
    }
    if (candidate_name == step.text) {
      return needed_names.empty() ? replay(context, address, allowance, edge + 1, candidate, consume) :
                                   need_names();
    }
    return {};
  };
  if (current.socket_key_kind && step.kind == Step::Kind::Key) {
    const bNode &node = *static_cast<const bNode *>(collection.ptr.data);
    const ListBaseT<bNodeSocket> &sockets = current.socket_key_kind == 1 ? node.inputs : node.outputs;
    for (const bNodeSocket &socket : sockets) {
      if (!socket.is_available()) {
        continue;
      }
      if (!socket.typeinfo) {
        return unavailable("INVALID_STORAGE");
      }
      Target candidate;
      candidate.operation = collection.operation;
      candidate.ptr = PointerRNA(collection.ptr.owner_id, RNA_NodeSocket,
                                   const_cast<bNodeSocket *>(&socket), collection.ptr);
      if (!refine_checked(candidate.ptr)) {
        return unavailable("NATIVE_READER_REQUIRED");
      }
      for (const char *field : {"identifier", "name"}) {
        Value result = consume_candidate(candidate, 0, field, "");
        if (result.kind != Value::Kind::Null) {
          return result;
        }
      }
      if (BLI_time_now_seconds() >= context.deadline) {
        return needed_names.empty() ? Value::dict({{"status", Value::string("BUDGET")}}) : need_names();
      }
    }
    if (!needed_names.empty()) {
      return need_names();
    }
    const int first_pass = ELEM(node.type_legacy, SH_NODE_MIX, SH_NODE_MAP_RANGE) ? 1 : 0;
    for (int pass = first_pass; pass < 2; pass++) {
      int position = 0;
      for (const bNodeSocket &socket : sockets) {
        if (socket.is_available()) {
          if (!socket.typeinfo) {
            return unavailable("INVALID_STORAGE");
          }
          Target candidate;
          candidate.operation = collection.operation;
          candidate.ptr = PointerRNA(collection.ptr.owner_id, RNA_NodeSocket,
                                       const_cast<bNodeSocket *>(&socket), collection.ptr);
          if (!refine_checked(candidate.ptr)) {
            return unavailable("NATIVE_READER_REQUIRED");
          }
          Value result = consume_candidate(candidate, position, pass == 0 ? "identifier" : "name",
                                             pass == 0 ? socket.identifier : socket.name);
          if (result.kind != Value::Kind::Null) {
            return result;
          }
        }
        if (BLI_time_now_seconds() >= context.deadline) {
          return needed_names.empty() ? Value::dict({{"status", Value::string("BUDGET")}}) : need_names();
        }
        position++;
      }
    }
    return needed_names.empty() ? missing_detail("UNKNOWN_NAME", edge, Value::int_value(length),
                                                  Value::string(step.text)) : need_names();
  }
  if (current.owned_collection_kind == 1 && step.kind == Step::Kind::Key) {
    for (int position = 0; position < length; position++) {
      Target candidate;
      if (!owned_collection_item(collection, position, candidate)) {
        return unavailable("INVALID_STORAGE");
      }
      const auto &item = *static_cast<const bNodeTreeInterfaceItem *>(candidate.ptr.data);
      if (item.item_type == NodeTreeInterfaceItemType::Socket) {
        Value result = consume_candidate(candidate, position, "identifier", "");
        if (result.kind != Value::Kind::Null) {
          return result;
        }
      }
      if (item.item_type == NodeTreeInterfaceItemType::Socket ||
          item.item_type == NodeTreeInterfaceItemType::Panel) {
        Value result = consume_candidate(candidate, position, "name", "");
        if (result.kind != Value::Kind::Null) {
          return result;
        }
      }
      if (BLI_time_now_seconds() >= context.deadline) {
        return needed_names.empty() ? Value::dict({{"status", Value::string("BUDGET")}}) : need_names();
      }
    }
    if (!needed_names.empty()) {
      return need_names();
    }
    for (int pass = 0; pass < 2; pass++) {
      for (int position = 0; position < length; position++) {
        Target candidate;
        if (!owned_collection_item(collection, position, candidate)) {
          return unavailable("INVALID_STORAGE");
        }
        const auto &item = *static_cast<const bNodeTreeInterfaceItem *>(candidate.ptr.data);
        const char *physical = nullptr;
        if (item.item_type == NodeTreeInterfaceItemType::Socket) {
          const auto &socket = *reinterpret_cast<const bNodeTreeInterfaceSocket *>(&item);
          physical = pass == 0 ? socket.identifier : socket.name;
        }
        else if (pass == 1 && item.item_type == NodeTreeInterfaceItemType::Panel) {
          physical = reinterpret_cast<const bNodeTreeInterfacePanel *>(&item)->name;
        }
        else {
          continue;
        }
        Value result = consume_candidate(candidate, position, pass == 0 ? "identifier" : "name",
                                           physical ? physical : "");
        if (result.kind != Value::Kind::Null) {
          return result;
        }
        if (BLI_time_now_seconds() >= context.deadline) {
          return needed_names.empty() ? Value::dict({{"status", Value::string("BUDGET")}}) : need_names();
        }
      }
    }
    return needed_names.empty() ? missing_detail("UNKNOWN_NAME", edge, Value::int_value(length),
                                                  Value::string(step.text)) : need_names();
  }
  if (current.owned_collection_kind) {
    const int first = step.kind == Step::Kind::Index ? int(index) : 0;
    const int end = step.kind == Step::Kind::Index ? first + 1 : length;
    for (int position = first; position < end; position++) {
      Target candidate;
      if (!owned_collection_item(collection, position, candidate)) {
        return unavailable("INVALID_STORAGE");
      }
      Value result = consume_candidate(candidate, position);
      if (result.kind != Value::Kind::Null) {
        return result;
      }
      if (BLI_time_now_seconds() >= context.deadline) {
        return needed_names.empty() ? Value::dict({{"status", Value::string("BUDGET")}}) : need_names();
      }
    }
    return needed_names.empty() ? missing_detail(step.kind == Step::Kind::Key ? "UNKNOWN_NAME" :
                                                   "INDEX_RANGE", edge, Value::int_value(length),
                                                   step.kind == Step::Kind::Key ? Value::string(step.text) : Value{}) :
                                 need_names();
  }
  if (current.geometry_collection || current.traversal_collection) {
    const int first = step.kind == Step::Kind::Index ? int(index) : 0;
    const int end = step.kind == Step::Kind::Index ? first + 1 : length;
    for (int position = first; position < end; position++) {
      Target candidate;
      Value failure;
      if (!helper_collection_item(collection, position, candidate, failure)) {
        return failure;
      }
      Value result = consume_candidate(candidate, position);
      if (result.kind != Value::Kind::Null) {
        return result;
      }
      if (BLI_time_now_seconds() >= context.deadline) {
        return needed_names.empty() ? Value::dict({{"status", Value::string("BUDGET")}}) : need_names();
      }
    }
    return needed_names.empty() ? missing_detail("UNKNOWN_NAME", edge,
                         Value::int_value(length), Value::string(step.text)) : need_names();
  }
  if (current.const_attribute_kind) {
    if (step.kind != Step::Kind::Index) {
      return missing_detail("NO_NAMES", edge, Value::int_value(length), Value::string(step.text));
    }
    Target candidate;
    if (!const_attribute_item(collection, index, candidate)) {
      return unavailable("INVALID_STORAGE");
    }
    return consume_candidate(candidate, index);
  }
  if (current.mesh_collection_kind) {
    if (step.kind != Step::Kind::Index) {
      return missing_detail("NO_NAMES", edge, Value::int_value(length), Value::string(step.text));
    }
    Target candidate;
    if (!const_geometry_item(collection, index, candidate)) {
      return unavailable("INVALID_STORAGE");
    }
    return consume_candidate(candidate, index);
  }
  if (collection.virtual_storage || collection.weight_vertex >= 0) {
    const Span<MDeformVert> deform = collection.weight_vertex >= 0 ?
                                        reinterpret_cast<const Mesh *>(collection.ptr.owner_id)->deform_verts() :
                                        Span<MDeformVert>();
    const int first = step.kind == Step::Kind::Index ? int(index) : 0;
    const int end = step.kind == Step::Kind::Index ? first + 1 : std::min(length, 4000);
    for (int item = first; item < end; item++) {
      Target candidate = collection;
      candidate.kind = Target::Kind::Struct;
      candidate.property = nullptr;
      candidate.ptr.type = const_cast<StructRNA *>(fixed);
      if (collection.weight_vertex >= 0) {
        candidate.weight = &deform[collection.weight_vertex].dw[item];
        candidate.ptr.data = const_cast<MDeformWeight *>(candidate.weight);
        candidate.virtual_kind = "WEIGHT";
        candidate.virtual_storage = false;
      }
      else {
        candidate.storage = &IDP_property_array_get(collection.storage)[item];
        if (candidate.storage->type != IDP_GROUP || !storage_group_valid(*candidate.storage)) {
          return unavailable("INVALID_STORAGE");
        }
        candidate.ptr.data = const_cast<IDProperty *>(candidate.storage);
        candidate.virtual_kind = "STORED_COLLECTION_ITEM";
      }
      Value result = consume_candidate(candidate, item);
      if (result.kind != Value::Kind::Null) {
        return result;
      }
      if (BLI_time_now_seconds() >= context.deadline) {
        return needed_names.empty() ? Value::dict({{"status", Value::string("BUDGET")}}) : need_names();
      }
    }
  }
  else {
    const RouteFacts routes = routes_for(collection, *collection.property);
    if (!STREQ(routes.iterate, "SOURCE_COLLECTION") ||
        !one_of(routes.refine, {"NONE", "SOURCE_REFINE"}))
    {
      return unavailable("NATIVE_READER_REQUIRED");
    }
    PointerRNA owner = collection.ptr;
    IteratorGuard guard(owner, *collection.property);
    int item = 0;
    while (guard.iterator.valid) {
      Target candidate;
      candidate.operation = collection.operation;
      candidate.ptr = guard.iterator.ptr;
      candidate.kind = candidate.ptr.data ? Target::Kind::Struct : Target::Kind::Null;
      Value result = consume_candidate(candidate, item);
      if (result.kind != Value::Kind::Null) {
        return result; /* guard remains alive until selected descendant copying is complete. */
      }
      if (step.kind == Step::Kind::Index && item > index) {
        break;
      }
      /* The ordinary key lookup is not the4000-name suggestion scan. Search all current
       * candidates until match/end/deadline; only the legacy virtual storage lookup above
       * retains its own4000-entry semantics. Never claim UNKNOWN_NAME after a partial scan. */
      if (BLI_time_now_seconds() >= context.deadline) {
        return needed_names.empty() ? Value::dict({{"status", Value::string("BUDGET")}}) : need_names();
      }
      RNA_property_collection_next(&guard.iterator);
      item++;
    }
  }
  if (!needed_names.empty()) {
    return need_names();
  }
  return missing_detail(step.kind == Step::Kind::Key ? "UNKNOWN_NAME" : "INDEX_RANGE",
                         edge, Value::int_value(length),
                         step.kind == Step::Kind::Key ? Value::string(step.text) : Value{});
}

static Value replay(const Context &context,
                    const Address &address,
                    const Allowance &allowance,
                    int edge,
                    const Target &target,
                    const Consume &consume)
{
  const int count = address.parsed.size() + address.expansion.size();
  if (target.kind == Target::Kind::Null) {
    return edge == count ? consume(target, edge) : missing_detail("NULL", edge);
  }
  if (const char *reason = target_refusal(context, target)) {
    return unavailable(reason);
  }
  if (edge == count) {
    return consume(target, edge);
  }
  const Step &step = address_step(address, edge);
  if (target.kind == Target::Kind::Collection && step.kind != Step::Kind::Attribute) {
    return select_collection(context, address, allowance, edge, target, consume);
  }
  if (step.kind != Step::Kind::Attribute) {
    return missing_detail("NOT_A_COLLECTION", edge);
  }
  Target owner = target;
  if (owner.kind == Target::Kind::Collection) {
    if (!owner.property->srna) {
      return missing_detail("NOT_A_STRUCT", edge);
    }
    owner.ptr.type = owner.property->srna;
    owner.kind = Target::Kind::Struct;
    owner.property = nullptr;
  }
  if (!owner.ptr.type) {
    return missing_detail("NOT_A_STRUCT", edge);
  }
  PropertyRNA *property = RNA_struct_type_find_property(owner.ptr.type, step.text.c_str());
  if (!property) {
    return missing_detail("NOT_A_STRUCT", edge, {}, Value::string(step.text));
  }
  Value failure;
  if (!admit_edge(context, address, allowance, edge, owner, *property, failure)) {
    return failure;
  }
  Target child = owner;
  child.kind = Target::Kind::Property;
  child.property = property;
  if (edge + 1 == count) {
    return consume(child, edge + 1);
  }
  const RouteFacts routes = routes_for(owner, *property);
  if (!one_of(routes.value, {"ID_SYSTEM_STORAGE", "VIRTUAL_STORAGE", "SOURCE_STORAGE_LOCATOR"})) {
    if (const char *reason = route_refusal(owner, routes)) {
      return unavailable(reason);
    }
  }
  if (property->type == PROP_COLLECTION) {
    child.kind = Target::Kind::Collection;
    if (STREQ(routes.value, "WEIGHTS")) {
      if (!weight_collection(child)) {
        return unavailable("INVALID_STORAGE");
      }
    }
    else if (one_of(routes.value, {"ID_SYSTEM_STORAGE", "VIRTUAL_STORAGE", "SOURCE_STORAGE_LOCATOR"})) {
      if (!storage_child(context, owner, *property, child, failure)) {
        return failure;
      }
    }
    else if (!STREQ(routes.value, "SOURCE_COLLECTION")) {
      return unavailable("NATIVE_READER_REQUIRED");
    }
    return replay(context, address, allowance, edge + 1, child, consume);
  }
  if (property->type != PROP_POINTER) {
    return missing_detail("NOT_A_STRUCT", edge + 1);
  }
  if (one_of(routes.value, {"ID_SYSTEM_STORAGE", "VIRTUAL_STORAGE", "SOURCE_STORAGE_LOCATOR"})) {
    if (!storage_child(context, owner, *property, child, failure)) {
      return failure;
    }
  }
  else if (routes.registry_properties_kind) {
    if (!registry_properties(owner, routes, child, failure)) {
      return failure;
    }
  }
  else if (routes.bake_node_special) {
    if (!pure_bake_node(owner, child, failure)) {
      return failure;
    }
  }
  else if (routes.traversal_pointer) {
    Target declared = owner;
    declared.property = property;
    if (!helper_pointer(declared, routes, child, failure)) {
      return failure;
    }
  }
  else if (routes.material_slot_special || routes.active_material_special) {
    if (!pure_material_pointer(owner, routes, child, failure)) {
      return failure;
    }
  }
  else if (routes.node_socket_node_special) {
    if (!pure_socket_node(owner, child, failure)) {
      return failure;
    }
  }
  else if (routes.object_data_special) {
    if (!pure_object_data(owner, child, failure)) {
      return failure;
    }
  }
  else if (STREQ(routes.value, "SOURCE_POINTER") &&
           one_of(routes.refine, {"NONE", "SOURCE_REFINE"}))
  {
    if (!pointer_storage_valid(owner, *property)) {
      return unavailable("INVALID_STORAGE");
    }
    PointerRNA pointer = owner.ptr;
    child = {};
    child.operation = owner.operation;
    child.ptr = RNA_property_pointer_get_never_create(&pointer, property);
    child.kind = child.ptr.data ? Target::Kind::Struct : Target::Kind::Null;
  }
  else {
    return unavailable("NATIVE_READER_REQUIRED");
  }
  return replay(context, address, allowance, edge + 1, child, consume);
}

static Value resolve(const Context &context,
                     const Address &address,
                     const Allowance &allowance,
                     const Consume &consume)
{
  if (const char *reason = validate_context(context)) {
    return Value::dict({{"status", Value::string("EXPIRED")},
                        {"reason", Value::string(reason)}});
  }
  OperationScope operation;
  CurrentOperation current(&operation);
  Scene *bound_scene = live_scene(context);
  const wmWindow *bound_window = live_window(context);
  operation.window.main = G_MAIN;
  operation.window.scene = bound_scene;
  /* A missing layer name stays unresolved; the window's own lookup would rename the window. */
  operation.window.view_layer = bound_scene && bound_window ?
                                    BKE_view_layer_find(bound_scene, bound_window->view_layer_name) :
                                    nullptr;
  Target root;
  root.operation = &operation;
  if (address.data_root) {
    root.data_root = true;
    root.ptr = PointerRNA(nullptr, RNA_BlendData, G_MAIN);
  }
  else {
    root.ptr = PointerRNA(&bound_scene->id, RNA_Scene, bound_scene);
  }
  /* This call returns only detached Value. Every root/selected-child PointerRNA and iterator
   * guard is destroyed here before the ABI wrapper can allocate ANY Python result object. */
  return replay(context, address, allowance, 0, root, consume);
}

static bool materialize(const Context &context,
                        const Target &target,
                        Target &node,
                        Value &failure)
{
  node = target;
  if (target.kind != Target::Kind::Property) {
    return true;
  }
  PropertyRNA &property = *target.property;
  const RouteFacts routes = routes_for(target, property);
  if (!ELEM(property.type, PROP_POINTER, PROP_COLLECTION)) {
    return false;
  }
  if (one_of(routes.value, {"ID_SYSTEM_STORAGE", "VIRTUAL_STORAGE", "SOURCE_STORAGE_LOCATOR"})) {
    return storage_child(context, target, property, node, failure);
  }
  if (const char *reason = route_refusal(target, routes)) {
    failure = unavailable(reason);
    return false;
  }
  if (property.type == PROP_COLLECTION) {
    node.kind = Target::Kind::Collection;
    if (STREQ(routes.value, "WEIGHTS")) {
      if (!weight_collection(node)) {
        failure = unavailable("INVALID_STORAGE");
        return false;
      }
      return true;
    }
    if (STREQ(routes.value, "SOURCE_COLLECTION")) {
      return true;
    }
  }
  else if (routes.registry_properties_kind) {
    return registry_properties(target, routes, node, failure);
  }
  else if (routes.bake_node_special) {
    return pure_bake_node(target, node, failure);
  }
  else if (routes.traversal_pointer) {
    return helper_pointer(target, routes, node, failure);
  }
  else if (routes.material_slot_special || routes.active_material_special) {
    return pure_material_pointer(target, routes, node, failure);
  }
  else if (routes.node_socket_node_special) {
    return pure_socket_node(target, node, failure);
  }
  else if (routes.object_data_special) {
    return pure_object_data(target, node, failure);
  }
  else if (STREQ(routes.value, "SOURCE_POINTER") &&
           one_of(routes.refine, {"NONE", "SOURCE_REFINE"}))
  {
    if (!pointer_storage_valid(target, property)) {
      failure = unavailable("INVALID_STORAGE");
      return false;
    }
    PointerRNA owner = target.ptr;
    node = {};
    node.operation = target.operation;
    node.ptr = RNA_property_pointer_get_never_create(&owner, &property);
    node.kind = node.ptr.data ? Target::Kind::Struct : Target::Kind::Null;
    return true;
  }
  failure = unavailable("NATIVE_READER_REQUIRED");
  return false;
}

static Value describe_target(const Context &context,
                             const Address &address,
                             const Target &target,
                             int edge,
                             const std::string *member,
                             bool detail,
                             bool copy_declarations)
{
  Target node;
  Value failure;
  if (!materialize(context, target, node, failure)) {
    return failure.kind == Value::Kind::Null ? missing_detail("NOT_A_STRUCT", edge) : failure;
  }
  if (node.kind == Target::Kind::Null) {
    return missing_detail("NULL", edge);
  }
  Target declaration_owner = node;
  if (node.kind == Target::Kind::Collection) {
    declaration_owner.ptr.type = node.property->srna;
    declaration_owner.kind = Target::Kind::Struct;
    declaration_owner.property = nullptr;
  }
  const StructRNA *type = declaration_owner.ptr.type;
  /* The declaration list is built only when copied or listed: most descriptions name a key the
   * caller already holds. */
  std::vector<const PropertyRNA *> properties;
  if (member) {
    PropertyRNA *found = type ? RNA_struct_type_find_property(const_cast<StructRNA *>(type),
                                                              member->c_str()) : nullptr;
    std::vector<Value> names;
    if (!found && type) {
      properties = declarations(type);
      for (const PropertyRNA *property : properties) {
        names.push_back(Value::string(property->identifier));
      }
    }
    Value limits;
    if (detail && found && ELEM(found->type, PROP_INT, PROP_FLOAT)) {
      const double minimum = found->type == PROP_INT ?
                                 static_cast<IntPropertyRNA *>(found)->hardmin :
                                 static_cast<FloatPropertyRNA *>(found)->hardmin;
      const double maximum = found->type == PROP_INT ?
                                 static_cast<IntPropertyRNA *>(found)->hardmax :
                                 static_cast<FloatPropertyRNA *>(found)->hardmax;
      limits = Value::dict({{"hard_min", std::isfinite(minimum) ? Value::float_value(minimum) : Value{}},
                            {"hard_max", std::isfinite(maximum) ? Value::float_value(maximum) : Value{}}});
    }
    const bool function_exists = type && RNA_struct_find_function(const_cast<StructRNA *>(type),
                                                                  member->c_str());
    return success("MEMBER", moved_dict(
        Field("property", found ? describe_property(context, declaration_owner, *found) : Value{}),
        Field("function_exists", Value::boolean_value(function_exists)),
        Field("members", Value::tuple(std::move(names))),
        Field("members_total", Value::int_value(found ? 0 : properties.size())),
        Field("numeric_limits", std::move(limits))));
  }
  /* A caller that holds the descriptions behind this node's declarations key asks for none. */
  const int64_t key = declarations_key(context, declaration_owner);
  const bool cached = !copy_declarations;
  std::vector<Value> copied;
  if (!cached && type) {
    properties = declarations(type);
    copied.reserve(properties.size());
    for (const PropertyRNA *property : properties) {
      copied.push_back(describe_property(context, declaration_owner, *property));
    }
  }
  if (node.kind == Target::Kind::Collection) {
    return success("COLLECTION", moved_dict(
        Field("address", address_value(address)),
        Field("property", describe_property(context, node, *node.property)),
        Field("collection_type", type_info(node.property->srna)),
        Field("declarations", Value::int_value(key)),
        Field("properties", cached ? Value{} : Value::tuple(std::move(copied)))));
  }
  if (cached) {
    return success("STRUCT", moved_dict(Field("node", node_value(context, address, node, true, key)),
                                        Field("properties", Value{}), Field("bookkeeping", Value{}),
                                        Field("data_collections", Value{})));
  }
  std::vector<Value> bookkeeping, collections;
  if (node.data_root) {
    for (const PropertyRNA *property : properties) {
      if (property->type == PROP_COLLECTION) {
        const StructRNA *item = static_cast<const CollectionPropertyRNA *>(property)->item_type;
        if (item) {
          collections.push_back(Value::tuple({Value::string(item->identifier),
                                              Value::string(property->identifier)}));
        }
      }
    }
  }
  else {
    for (const PropertyRNA *property : declarations(RNA_ID)) {
      if (!STREQ(property->identifier, "name")) {
        bookkeeping.push_back(Value::string(property->identifier));
      }
    }
  }
  return success("STRUCT", moved_dict(Field("node", node_value(context, address, node, true, key)),
                                      Field("properties", Value::tuple(std::move(copied))),
                                      Field("bookkeeping", Value::tuple(std::move(bookkeeping))),
                                      Field("data_collections", Value::tuple(std::move(collections)))));
}

/* Only a declaration whose Python getter or item callback was not run says where its value came
 * from: without one, the stored or default value is the property's value. */
static Value annotations(const PropertyRNA &property, const char *source)
{
  const bool computed = BPY_rna_property_has_python_getter(const_cast<PropertyRNA *>(&property)) ||
                        BPY_rna_property_has_python_enum_items(const_cast<PropertyRNA *>(&property));
  return moved_dict(Field("value_source", computed && source ? Value::string(source) : Value{}),
                    Field("computed_getter", computed ? Value::string("not_run") : Value{}));
}

/* A copied value, kept in the compact form of a value page while the native phase runs:
 * ("OK", tag, value, value_source, computed_getter). Most values are page members; a single
 * fetch expands it to the documented VALUE record (expanded_value). */
static Value value_result(const char *tag, Value value, Value notes)
{
  Value source, getter;
  if (notes.kind == Value::Kind::Dict) {
    for (auto &[key, item] : notes.fields) {
      if (key == "value_source") {
        source = std::move(item);
      }
      else if (key == "computed_getter") {
        getter = std::move(item);
      }
    }
  }
  return moved_tuple(Value::string("OK"), Value::string(tag), std::move(value), std::move(source),
                     std::move(getter));
}

static bool compact_value(const Value &result)
{
  return result.kind == Value::Kind::Tuple && result.sequence.size() == 5 &&
         result.sequence[0].kind == Value::Kind::String && result.sequence[0].text == "OK";
}

/* The VALUE record of a compact value (anything else is returned unchanged). */
static Value expanded_value(Value result)
{
  if (!compact_value(result)) {
    return result;
  }
  std::vector<Value> &items = result.sequence;
  return success("VALUE", moved_dict(Field("tag", std::move(items[1])), Field("value", std::move(items[2])),
                                     Field("annotations", moved_dict(
                                                              Field("value_source", std::move(items[3])),
                                                              Field("computed_getter", std::move(items[4]))))));
}

static Value plain_annotations()
{
  return moved_dict(Field("value_source", Value{}), Field("computed_getter", Value{}));
}

static Value copy_raw_value(const Context &context,
                            const Address &address,
                            const Target &target)
{
  PropertyRNA &property = *target.property;
  bool valid = true;
  const IDProperty *stored = stored_member(target, property, valid);
  if (!valid || (stored && !storage_matches(property, *stored))) {
    return value_result("RAW", Value::dict({
        {"state", Value::string("INVALID")},
        {"type", Value::string(stored ? storage_type_name(stored->type) : "NONE")},
        {"subtype", {}}, {"length", {}}, {"value", {}}, {"default", {}},
        {"default_array", {}}, {"enum_default", {}}, {"enum_items_static", {}}}),
        annotations(property, stored ? "stored" : "default"));
  }
  Value raw;
  if (ELEM(property.type, PROP_POINTER, PROP_COLLECTION)) {
    Target child;
    Value failure;
    if (!storage_child(context, target, property, child, failure)) {
      return failure;
    }
    raw = Value::dict({
        {"state", Value::string(!stored ? "ABSENT" : property.type == PROP_COLLECTION ?
                                                     "COLLECTION" : stored->type == IDP_GROUP ?
                                                     "GROUP" : "VALUE")},
        {"type", Value::string(stored ? storage_type_name(stored->type) : "NONE")},
        {"subtype", {}},
        {"length", property.type == PROP_COLLECTION ?
                       Value::int_value(stored ? stored->len : 0) : Value{}},
        {"value", child.kind == Target::Kind::Null || child.kind == Target::Kind::Collection ?
                      Value{} : node_value(context, address, child)},
        {"default", {}}, {"default_array", {}}, {"enum_default", {}}, {"enum_items_static", {}}});
  }
  else {
    raw = copy_storage(stored);
    if (!stored) {
      copy_absent_defaults(property, raw);
    }
    if (property.type == PROP_ENUM) {
      field(raw, "enum_items_static") = copy_static_enum(static_cast<EnumPropertyRNA &>(property));
    }
  }
  return value_result("RAW", std::move(raw), annotations(property, stored ? "stored" : "default"));
}

template<typename Number, typename Declaration>
static std::vector<Value> copy_numeric_array(PointerRNA &owner,
                                            Declaration &declaration,
                                            int length,
                                            int retained)
{
  std::unique_ptr<Number[]> values = std::make_unique<Number[]>(length);
  if (declaration.getarray) {
    declaration.getarray(&owner, values.get());
  }
  else if (declaration.getarray_ex) {
    declaration.getarray_ex(&owner, &declaration, values.get());
  }
  else if (declaration.get_default_array && owner.data) {
    declaration.get_default_array(&owner, &declaration, values.get());
  }
  else {
    const int declared = declaration.totarraylength;
    for (int index = 0; index < length; index++) {
      values[index] = declaration.defaultarray && index < declared ? declaration.defaultarray[index] :
                                                                    declaration.defaultvalue;
    }
  }
  if (declaration.getarray_transform) {
    std::unique_ptr<Number[]> original = std::make_unique<Number[]>(length);
    std::copy_n(values.get(), length, original.get());
    /* These are full static native declarations (raw/runtime storage already took its own
     * nonrepairing route), so ordinary transform's is_set fact is true. */
    declaration.getarray_transform(&owner, &declaration, original.get(), true, values.get());
  }
  std::vector<Value> copied;
  copied.reserve(retained);
  for (int index = 0; index < retained; index++) {
    if constexpr (std::is_same_v<Number, bool>) {
      copied.push_back(Value::boolean_value(values[index]));
    }
    else if constexpr (std::is_integral_v<Number>) {
      copied.push_back(Value::int_value(values[index]));
    }
    else {
      copied.push_back(Value::float_value(values[index]));
    }
  }
  return copied;
}

static Value copy_array(PointerRNA &owner, PropertyRNA &property, const RouteFacts &routes)
{
  if (!STREQ(routes.value, "SOURCE_ARRAY") ||
      (property.getlength && !STREQ(routes.length, "SOURCE_LENGTH")))
  {
    return unavailable("NATIVE_READER_REQUIRED");
  }
  const int length = property.getlength ? RNA_property_array_length(&owner, &property) :
                                         int(property.totarraylength);
  if (length < 0) {
    return unavailable("INVALID_STORAGE");
  }
  const int retained = std::min(length, read_items_max);
  std::vector<Value> flat;
  /* Exactly one selected array getter/default source; never per-index whole-array fetches.
   * Numeric intermediate is N*sizeof(T), or2*N*sizeof(T) for an admitted native transform,
   * plus at most64 pre-reserved detached Values. Generated DNA getters may read their pure
   * generated length again internally; that is not another whole-array getter invocation. */
  switch (property.type) {
    case PROP_BOOLEAN:
      flat = copy_numeric_array<bool>(owner, static_cast<BoolPropertyRNA &>(property), length, retained);
      break;
    case PROP_INT:
      flat = copy_numeric_array<int>(owner, static_cast<IntPropertyRNA &>(property), length, retained);
      break;
    case PROP_FLOAT:
      flat = copy_numeric_array<float>(owner, static_cast<FloatPropertyRNA &>(property), length, retained);
      break;
    default:
      return unavailable("INVALID_STORAGE");
  }
  std::vector<Value> dimensions;
  for (int index = 0; index < 3; index++) {
    dimensions.push_back(Value::int_value(property.arraydimension > 1 ? property.arraylength[index] :
                                         index == 0 ? property.totarraylength : 0));
  }
  return value_result("ARRAY", Value::dict({{"flat", Value::tuple(std::move(flat))},
                                            {"declared_length", Value::int_value(property.totarraylength)},
                                            {"actual_length", Value::int_value(length)},
                                            {"dimensions", Value::tuple(std::move(dimensions))}}),
                      Value{});
}

class EnumItemsGuard {
 public:
  const EnumPropertyItem *items = nullptr;
  int length = 0;
  bool free = false;
  ~EnumItemsGuard()
  {
    if (free) {
      MEM_delete(items);
    }
  }
};

static bool const_texture_output(PointerRNA &owner, int &current, Value &failure)
{
  const MTex &slot = *static_cast<const MTex *>(owner.data);
  current = 0;
  if (!slot.tex) {
    return true;
  }
  ID *texture_id = live_id_candidate(slot.tex);
  if (!texture_id || GS(texture_id->name) != ID_TE || !original_id(*texture_id)) {
    failure = unavailable("DENIED");
    return false;
  }
  const Tex &texture = *reinterpret_cast<const Tex *>(texture_id);
  if (!texture.nodetree) {
    return true;
  }
  ID *tree_id = live_id_candidate(texture.nodetree);
  if (!tree_id || GS(tree_id->name) != ID_NT || !original_id(*tree_id)) {
    failure = unavailable("DENIED");
    return false;
  }
  const bNodeTree &tree = *reinterpret_cast<const bNodeTree *>(tree_id);
  for (const bNode &node : tree.nodes) {
    if (node.type_legacy == TEX_NODE_OUTPUT && node.custom1 == slot.which_output) {
      current = slot.which_output;
      break;
    }
  }
  /* No match returns0 without the original getter's which_output reset. */
  return true;
}

static Value copy_enum_mapping(const Target &target, PropertyRNA &property,
                               const RouteFacts &routes, int current)
{
  if (!STREQ(routes.enum_value_items, "SOURCE_ENUM_VALUE")) {
    return unavailable("NATIVE_READER_REQUIRED");
  }
  std::vector<angelo_read_scalar::EnumItem> items;
  if (routes.scalar_enum) {
    if (!target.operation) {
      return unavailable("INVALID_STORAGE");
    }
    auto copied = angelo_read_scalar::enum_items(target.operation->scalar,
        routes.scalar_enum, target.ptr, property, &target.operation->window, false);
    if (const char *reason = helper_failure_reason(copied.status)) {
      return unavailable(reason);
    }
    items = std::move(copied.items);
  }
  else {
    EnumItemsGuard guard;
    PointerRNA owner = target.ptr;
    /* Ordinary value conversion uses the bound context. While a Python context override is
     * active or a view layer is out of date, a context-reading choice callback could consult
     * Python or resynchronize data; the declaration's context-free choices are used instead.
     * (Catalogued context-reading callbacks are withheld by their guard before this point.) */
    bContext *C = BPY_context_get();
    RNA_property_enum_items(context_current(C) ? C : nullptr, &owner, &property,
                            &guard.items, &guard.length, &guard.free);
    if (guard.items) {
      items.reserve(guard.length);
      for (int index = 0; index < guard.length; index++) {
        const EnumPropertyItem &item = guard.items[index];
        items.push_back({item.value, item.identifier ? item.identifier : ""});
      }
    }
  }
  if (property.flag & PROP_ENUM_FLAG) {
    std::vector<Value> flags;
    for (const auto &item : items) {
      if (!item.identifier.empty() && (item.value & current)) {
        flags.push_back(Value::string(item.identifier));
      }
    }
    return value_result("ENUM_FLAGS", Value::tuple(std::move(flags)), Value{});
  }
  /* Headings and separators carry an empty identifier (and often value 0); like
   * RNA_enum_identifier, only named items name a value. */
  for (const auto &item : items) {
    if (item.value == current && !item.identifier.empty()) {
      return value_result("ENUM", Value::string(item.identifier), Value{});
    }
  }
  return value_result("ENUM", Value::string(""), Value{});
}

static Value copy_enum_value(const Target &target, PropertyRNA &property, const RouteFacts &routes)
{
  if (!STREQ(routes.value, "SOURCE_SCALAR")) {
    return unavailable("NATIVE_READER_REQUIRED");
  }
  PointerRNA owner = target.ptr;
  int current;
  if (routes.texture_output_special) {
    Value failure;
    if (!const_texture_output(owner, current, failure)) {
      return failure;
    }
  }
  else {
    current = RNA_property_enum_get(&owner, &property);
  }
  return copy_enum_mapping(target, property, routes, current);
}

static Value copy_stored_array_prefix(const Target &target, const RouteFacts &routes)
{
  const PropertyRNA &property = *target.property;
  const int *integers = nullptr;
  const float *numbers = nullptr;
  int length = 0;
  if (!target.ptr.data) {
    return unavailable("INVALID_STORAGE");
  }
  if (routes.bounded_array_kind == 1) {
    const HookModifierData &hook = *static_cast<const HookModifierData *>(target.ptr.data);
    if (hook.modifier.type != eModifierType_Hook) {
      return unavailable("INVALID_STORAGE");
    }
    integers = hook.indexar;
    length = integers ? hook.indexar_num : 0;
  }
  else if (routes.bounded_array_kind == 2) {
    const bConstraint &constraint = *static_cast<const bConstraint *>(target.ptr.data);
    if (constraint.type != CONSTRAINT_TYPE_SPLINEIK) {
      return unavailable("INVALID_STORAGE");
    }
    const bSplineIKConstraint *bindings = static_cast<const bSplineIKConstraint *>(constraint.data);
    length = bindings ? bindings->numpoints : 0;
    numbers = bindings ? bindings->points : nullptr;
  }
  else if (routes.bounded_array_kind == 3) {
    const FModifier &modifier = *static_cast<const FModifier *>(target.ptr.data);
    if (modifier.type != FMODIFIER_TYPE_GENERATOR) {
      return unavailable("INVALID_STORAGE");
    }
    const FMod_Generator *generator = static_cast<const FMod_Generator *>(modifier.data);
    length = generator ? generator->arraysize : 0;
    numbers = generator ? generator->coefficients : nullptr;
  }
  else {
    return unavailable("NATIVE_READER_REQUIRED");
  }
  if (length < 0 || (length && !integers && !numbers)) {
    return unavailable("INVALID_STORAGE");
  }
  std::vector<Value> flat;
  const int retained = std::min(length, read_items_max);
  flat.reserve(retained);
  for (int index = 0; index < retained; index++) {
    flat.push_back(integers ? Value::int_value(integers[index]) : Value::float_value(numbers[index]));
  }
  std::vector<Value> dimensions;
  for (int index = 0; index < 3; index++) {
    dimensions.push_back(Value::int_value(property.arraydimension > 1 ? property.arraylength[index] :
                                         index == 0 ? property.totarraylength : 0));
  }
  return value_result("ARRAY", Value::dict({{"flat", Value::tuple(std::move(flat))},
      {"declared_length", Value::int_value(property.totarraylength)},
      {"actual_length", Value::int_value(length)},
      {"dimensions", Value::tuple(std::move(dimensions))}}), Value{});
}

template<typename NativeValue>
static Value copy_owned_numeric_value(const Target &target, const RouteFacts &routes,
                                      const NativeValue &copied)
{
  PropertyRNA &property = *target.property;
  if (const char *reason = helper_failure_reason(copied.status)) {
    return unavailable(reason);
  }
  if (const bool *value = std::get_if<bool>(&copied.data)) {
    if (property.type != PROP_BOOLEAN || property.totarraylength || property.getlength) {
      return unavailable("INVALID_STORAGE");
    }
    return value_result("BOOLEAN", Value::boolean_value(*value), Value{});
  }
  if (const int *value = std::get_if<int>(&copied.data)) {
    if (!ELEM(property.type, PROP_INT, PROP_ENUM) || property.totarraylength || property.getlength) {
      return unavailable("INVALID_STORAGE");
    }
    if (property.type == PROP_ENUM) {
      return copy_enum_mapping(target, property, routes, *value);
    }
    return value_result("INT", Value::int_value(*value), Value{});
  }
  if (const float *value = std::get_if<float>(&copied.data)) {
    if (property.type != PROP_FLOAT || property.totarraylength || property.getlength) {
      return unavailable("INVALID_STORAGE");
    }
    return value_result("FLOAT", Value::float_value(*value), Value{});
  }
  const bool vector_type = property.type == PROP_BOOLEAN ?
                               std::holds_alternative<std::vector<bool>>(copied.data) :
                           property.type == PROP_INT ?
                               std::holds_alternative<std::vector<int>>(copied.data) :
                           property.type == PROP_FLOAT &&
                               std::holds_alternative<std::vector<float>>(copied.data);
  if (!vector_type || (!property.totarraylength && !property.getlength)) {
    return unavailable("INVALID_STORAGE");
  }
  std::vector<Value> flat;
  std::visit([&](const auto &numbers) {
    using T = std::decay_t<decltype(numbers)>;
    if constexpr (std::is_same_v<T, std::vector<bool>> ||
                  std::is_same_v<T, std::vector<int>> ||
                  std::is_same_v<T, std::vector<float>>)
    {
      const size_t retained = std::min<size_t>(numbers.size(), read_items_max);
      flat.reserve(retained);
      for (size_t index = 0; index < retained; index++) {
        if constexpr (std::is_same_v<T, std::vector<bool>>) {
          flat.push_back(Value::boolean_value(numbers[index]));
        }
        else if constexpr (std::is_same_v<T, std::vector<int>>) {
          flat.push_back(Value::int_value(numbers[index]));
        }
        else {
          flat.push_back(Value::float_value(numbers[index]));
        }
      }
    }
  }, copied.data);
  if (copied.actual_length < 0 ||
      size_t(std::min<int64_t>(copied.actual_length, read_items_max)) != flat.size())
  {
    return unavailable("INVALID_STORAGE");
  }
  std::vector<Value> dimensions;
  for (int index = 0; index < 3; index++) {
    dimensions.push_back(Value::int_value(property.arraydimension > 1 ? property.arraylength[index] :
                                         index == 0 ? property.totarraylength : 0));
  }
  return value_result("ARRAY", Value::dict({{"flat", Value::tuple(std::move(flat))},
      {"declared_length", Value::int_value(property.totarraylength)},
      {"actual_length", Value::int_value(copied.actual_length)},
      {"dimensions", Value::tuple(std::move(dimensions))}}), Value{});
}

static Value copy_geometry_value(const Target &target, const RouteFacts &routes)
{
  if (!target.operation) {
    return unavailable("INVALID_STORAGE");
  }
  const auto copied = angelo_read_geometry::value(target.operation->geometry,
      routes.geometry_scalar, target.ptr, *target.property,
      target.geometry_item.pointer.data ? &target.geometry_item : nullptr);
  return copy_owned_numeric_value(target, routes, copied);
}

static Value copy_scalar_value(const Target &target, const RouteFacts &routes)
{
  if (!target.operation) {
    return unavailable("INVALID_STORAGE");
  }
  const auto copied = angelo_read_scalar::value(target.operation->scalar,
      routes.scalar_value, target.ptr, *target.property);
  if (const char *reason = helper_failure_reason(copied.status)) {
    return unavailable(reason);
  }
  if (const std::string *string = std::get_if<std::string>(&copied.data)) {
    if (target.property->type != PROP_STRING) {
      return unavailable("INVALID_STORAGE");
    }
    const bool prefix = string->size() > read_string_bytes_max;
    Value text = Value::string(string->substr(0, read_string_bytes_max));
    text.utf8_prefix = prefix;
    return value_result("STRING", std::move(text), Value{});
  }
  return copy_owned_numeric_value(target, routes, copied);
}

static Value fetch_target(const Context &context,
                          const Address &address,
                          const Target &target,
                          int edge,
                          int64_t expected)
{
  if (target.kind != Target::Kind::Property || edge == 0) {
    return missing_detail("NOT_A_STRUCT", edge);
  }
  PropertyRNA &property = *target.property;
  if (fingerprint(context, target, property) != expected) {
    return stale(address, edge - 1);
  }
  const RouteFacts routes = routes_for(target, property);
  if (one_of(routes.value, {"ID_SYSTEM_STORAGE", "VIRTUAL_STORAGE", "SOURCE_STORAGE_LOCATOR"})) {
    return copy_raw_value(context, address, target);
  }
  if (const char *reason = route_refusal(target, routes)) {
    return unavailable(reason);
  }
  if (ELEM(property.type, PROP_POINTER, PROP_COLLECTION)) {
    Target child;
    Value failure;
    if (!materialize(context, target, child, failure)) {
      return failure;
    }
    if (child.kind == Target::Kind::Null) {
      return value_result("NULL", {}, Value{});
    }
    if (const char *reason = target_refusal(context, child)) {
      return unavailable(reason);
    }
    if (child.kind == Target::Kind::Collection) {
      int length;
      const char *length_reason = nullptr;
      if (!collection_length(child, length, &length_reason)) {
        return unavailable(length_reason ? length_reason :
                           child.weight_vertex >= 0 || routes.const_attribute_kind ?
                               "INVALID_STORAGE" : "NATIVE_READER_REQUIRED");
      }
      return value_result("COLLECTION", Value::dict({{"address", address_value(address)},
                                                     {"length", Value::int_value(length)}}),
                          Value{});
    }
    return value_result("STRUCT", node_value(context, address, child), Value{});
  }
  if (routes.scalar_value) {
    return copy_scalar_value(target, routes);
  }
  if (routes.bounded_array_kind) {
    return copy_stored_array_prefix(target, routes);
  }
  if (routes.geometry_scalar) {
    return copy_geometry_value(target, routes);
  }
  if (target.attribute_source && target.ptr.type == RNA_MeshUVLoop &&
      STREQ(property.identifier, "pin_uv") && STREQ(routes.value, "SOURCE_SCALAR"))
  {
    bool pin;
    return const_legacy_uv_pin(target, pin) ?
               value_result("BOOLEAN", Value::boolean_value(pin), Value{}) :
               unavailable("NATIVE_READER_REQUIRED");
  }
  if (target.weight) {
    if (STREQ(property.identifier, "group")) {
      return value_result("INT", Value::int_value(target.weight->def_nr), Value{});
    }
    if (STREQ(property.identifier, "weight")) {
      return value_result("FLOAT", Value::float_value(target.weight->weight), Value{});
    }
  }
  if (one_of(routes.value, {"IMAGE_CACHE", "IMAGE_TILE_CACHE"})) {
    if (!target.ptr.owner_id || GS(target.ptr.owner_id->name) != ID_IM) {
      return unavailable("INVALID_STORAGE");
    }
    Image &image = *reinterpret_cast<Image *>(target.ptr.owner_id);
    Value metadata = STREQ(routes.value, "IMAGE_CACHE") ? copy_image_metadata(image) :
                         copy_tile_metadata(image, static_cast<ImageTile *>(target.ptr.data)->tile_number);
    const Value *copied = find_field(metadata, property.identifier);
    if (!copied) {
      return unavailable("LOADS_FILE");
    }
    if (property.type == PROP_ENUM) {
      std::string mapped = enum_identifier(static_cast<EnumPropertyRNA &>(property).item,
                                           copied->integer);
      return mapped.empty() ? unavailable("COMPUTED_VALUE") :
                             value_result("ENUM", Value::string(std::move(mapped)), Value{});
    }
    if (copied->kind == Value::Kind::Tuple) {
      return value_result("ARRAY", Value::dict({{"flat", *copied},
                                                {"declared_length", Value::int_value(property.totarraylength)},
                                                {"actual_length", Value::int_value(copied->sequence.size())},
                                                {"dimensions", Value::tuple({Value::int_value(property.totarraylength),
                                                                             Value::int_value(0), Value::int_value(0)})}}),
                          Value{});
    }
    return value_result(property.type == PROP_BOOLEAN ? "BOOLEAN" : "INT", *copied,
                        Value{});
  }
  PointerRNA owner = target.ptr;
  if (property.totarraylength || property.getlength) {
    return copy_array(owner, property, routes);
  }
  if (property.type == PROP_ENUM) {
    return copy_enum_value(target, property, routes);
  }
  if (!STREQ(routes.value, "SOURCE_SCALAR")) {
    return unavailable("NATIVE_READER_REQUIRED");
  }
  switch (property.type) {
    case PROP_BOOLEAN:
      return value_result("BOOLEAN", Value::boolean_value(RNA_property_boolean_get(&owner, &property)),
                          Value{});
    case PROP_INT:
      return value_result("INT", Value::int_value(RNA_property_int_get(&owner, &property)),
                          Value{});
    case PROP_FLOAT:
      return value_result("FLOAT", Value::float_value(RNA_property_float_get(&owner, &property)),
                          Value{});
    case PROP_STRING: {
      std::string copied;
      Value failure;
      if (!copy_source_string(target, property, routes, copied, failure)) {
        return failure;
      }
      const bool prefix = copied.size() > read_string_bytes_max;
      copied.resize(std::min<size_t>(copied.size(), read_string_bytes_max));
      Value text = Value::string(std::move(copied));
      text.utf8_prefix = prefix;
      return value_result("STRING", std::move(text), Value{});
    }
    default:
      return unavailable("NATIVE_READER_REQUIRED");
  }
}

/* Copy heap type name storage without __name__ lookup, UTF-8 cache construction or type creation.
 * Encoding into a fresh exact Python string happens only after native borrows have ended. */
static bool stored_type_name(PyTypeObject *type, Value &copied)
{
  if (!type || !(type->tp_flags & Py_TPFLAGS_HEAPTYPE)) {
    return false;
  }
  PyObject *name = reinterpret_cast<PyHeapTypeObject *>(type)->ht_name;
  if (!name || !PyUnicode_Check(name)) {
    return false;
  }
  /* ht_name may be a str subclass accepted by type.__name__ assignment. Copy its stored
   * Unicode payload directly; no subclass protocol executes and transport remains exact. */
  copied = Value::string("");
  copied.unicode_storage = true;
  const int kind = PyUnicode_KIND(name);
  const void *data = PyUnicode_DATA(name);
  for (Py_ssize_t index = 0; index < PyUnicode_GET_LENGTH(name); index++) {
    copied.unicode.push_back(PyUnicode_READ(kind, data, index));
  }
  return true;
}

static bool object_stored_type_name(const ID &object, Value &copied)
{
  PyTypeObject *selected = nullptr;
  PyObject *instance = static_cast<PyObject *>(object.py_instance);
  if (instance && BPy_StructRNA_Check(instance) &&
      Py_TYPE(instance)->tp_basicsize >= Py_ssize_t(sizeof(BPy_StructRNA)))
  {
    const BPy_StructRNA *cached = reinterpret_cast<const BPy_StructRNA *>(instance);
    if (cached->ptr && cached->ptr->type == RNA_Object && cached->ptr->data == &object &&
        cached->ptr->owner_id == &object)
    {
      selected = Py_TYPE(instance);
    }
  }
  if (!selected) {
    selected = static_cast<PyTypeObject *>(RNA_Object->py_type);
  }
  return selected && PyType_Check(reinterpret_cast<PyObject *>(selected)) &&
         stored_type_name(selected, copied);
}

static bool scene_contains_object(const Context &context, const Object &object)
{
  Scene *scene = live_scene(context);
  if (!scene || !scene->master_collection) {
    return false;
  }
  OperationScope *operation = current_operation;
  if (operation && operation->scene_objects_ready) {
    return operation->scene_objects.contains(&object);
  }
  /* Match cache-fill ordering (own objects, then children; first unique encounter) without
   * building/reparing BKE_collection_object_cache_get's mutable cache. This set is same-call only. */
  std::unordered_set<const Collection *> live_collections;
  for (Collection &collection : G_MAIN->collections) {
    live_collections.insert(&collection);
  }
  live_collections.insert(scene->master_collection); /* Scene's original embedded root. */
  std::unordered_set<const Collection *> visited;
  std::unordered_set<const Object *> objects;
  std::vector<const Collection *> pending = {scene->master_collection};
  while (!pending.empty() && objects.size() < 20000) {
    const Collection *collection = pending.back();
    pending.pop_back();
    if (!live_collections.contains(collection) || !visited.insert(collection).second) {
      continue;
    }
    for (const CollectionObject &entry : collection->gobject) {
      if (!entry.ob || !objects.insert(entry.ob).second) {
        continue;
      }
      if (entry.ob == &object && !operation) {
        return true;
      }
      if (objects.size() >= 20000) {
        break;
      }
    }
    if (objects.size() >= 20000) {
      break;
    }
    std::vector<const Collection *> children;
    for (const CollectionChild &child : collection->children) {
      children.push_back(child.collection);
    }
    pending.insert(pending.end(), children.rbegin(), children.rend());
  }
  if (operation) {
    /* The same first (at most 20000) objects the scan matches against, kept for the operation. */
    operation->scene_objects = std::move(objects);
    operation->scene_objects_ready = true;
    return operation->scene_objects.contains(&object);
  }
  return false;
}

static Value object_identity_target(const Context &context,
                                     const Target &target,
                                     const char *relation)
{
  Target node;
  Value failure;
  if (!materialize(context, target, node, failure)) {
    return failure.kind == Value::Kind::Null ? success("OBJECT_IDENTITY", {}) : failure;
  }
  ID *candidate = STREQ(relation, "owner") ? node.ptr.owner_id :
                  node.kind == Target::Kind::Struct && node.ptr.type == RNA_Object &&
                          !node.virtual_storage ?
                      static_cast<ID *>(node.ptr.data) : nullptr;
  ID *live = live_id_candidate(candidate);
  if (!live || GS(live->name) != ID_OB || !original_id(*live) ||
      !scene_contains_object(context, *reinterpret_cast<Object *>(live)))
  {
    return success("OBJECT_IDENTITY", {});
  }
  Value name;
  if (!object_stored_type_name(*live, name)) {
    return unavailable("NATIVE_READER_REQUIRED");
  }
  return success("OBJECT_IDENTITY", Value::dict({{"type_name", std::move(name)},
                                                 {"session_uid", Value::int_value(live->session_uid)},
                                                 {"pointer", Value::int_value(uintptr_t(live))},
                                                 {"scene_uid", Value::int_value(context.scene_uid)}}));
}

static Value reference_target(const Context &context,
                               const Address &address,
                               const Target &target,
                               int edge,
                               const char *relation,
                               const std::vector<std::pair<std::string, int64_t>> &fields)
{
  Target node;
  Value failure;
  if (!materialize(context, target, node, failure)) {
    return failure.kind == Value::Kind::Null ? missing_detail("NOT_A_STRUCT", edge) : failure;
  }
  if (node.kind != Target::Kind::Struct) {
    return missing_detail(node.kind == Target::Kind::Null ? "NULL" : "NOT_A_STRUCT", edge);
  }
  if (STREQ(relation, "owner")) {
    ID *owner = node.ptr.owner_id;
    if (!owner || !original_id(*owner)) {
      return unavailable("OUT_OF_DOCUMENT");
    }
    StructRNA *type = ID_code_to_RNA_type(GS(owner->name));
    /* General owner-ID refinement joins the source-provenance owner; Object has no subtype. */
    OperationScope *operation = node.operation;
    node = {};
    node.operation = operation;
    node.ptr = PointerRNA(owner, type, owner);
    if (!refine_checked(node.ptr)) {
      return unavailable("NATIVE_READER_REQUIRED");
    }
  }
  if (const char *reason = target_refusal(context, node)) {
    return unavailable(reason);
  }
  std::vector<Value> copied;
  if (STREQ(relation, "owner")) {
    std::vector<Value> needed;
    for (const PropertyRNA *declaration : declarations(node.ptr.type)) {
      if (declaration == node.ptr.type->nameproperty || STREQ(declaration->identifier, "identifier") ||
          STREQ(declaration->identifier, "type") || STREQ(declaration->identifier, "enabled"))
      {
        if (!ELEM(declaration->type, PROP_STRING, PROP_ENUM, PROP_BOOLEAN) ||
            declaration->totarraylength || declaration->getlength)
        {
          continue;
        }
        const int64_t current = fingerprint(context, node, *declaration);
        const bool present = std::any_of(fields.begin(), fields.end(), [&](const auto &field) {
          return field.second == current;
        });
        if (!present) {
          needed.push_back(describe_property(context, node, *declaration));
        }
      }
    }
    if (!needed.empty()) {
      return Value::dict({{"status", Value::string("NEEDS_ADMISSION")},
                          {"address", address_value(address)}, {"edge", Value::int_value(edge)},
                          {"descriptions", Value::tuple(std::move(needed))}});
    }
  }
  for (const auto &[identifier, expected] : fields) {
    if (!(node.ptr.type->nameproperty && identifier == node.ptr.type->nameproperty->identifier) &&
        !ELEM(identifier, "identifier", "type", "enabled"))
    {
      return unavailable("DENIED");
    }
    PropertyRNA *property = RNA_struct_type_find_property(node.ptr.type, identifier.c_str());
    if (!property || fingerprint(context, node, *property) != expected) {
      return stale(address, edge);
    }
    Target value_target = node;
    value_target.kind = Target::Kind::Property;
    value_target.property = property;
    /* A reference field is an operation, not a synthetic path selector. Native copy runs inside
     * this same resolved node's lifetime and reports a failed label locally. */
    Value result = fetch_target(context, address, value_target, std::max(edge, 1), expected);
    Value tag, value, notes, withheld;
    if (compact_value(result)) {
      tag = std::move(result.sequence[1]);
      value = std::move(result.sequence[2]);
      notes = moved_dict(Field("value_source", std::move(result.sequence[3])),
                         Field("computed_getter", std::move(result.sequence[4])));
      if (tag.text == "RAW") {
        Value &raw = value;
        const std::string state = field(raw, "state").text;
        if (state == "INVALID") {
          withheld = Value::string("INVALID_STORAGE");
          tag = value = {};
        }
        else {
          Value raw_value = state == "ABSENT" ?
                                field(raw, property->type == PROP_ENUM ? "enum_default" : "default") :
                                field(raw, "value");
          if (property->type == PROP_ENUM) {
            const Value &choices = field(raw, "enum_items_static");
            bool found = false;
            for (const Value &choice : choices.sequence) {
              if (choice.sequence[2].integer == raw_value.integer) {
                raw_value = choice.sequence[0];
                found = true;
                break;
              }
            }
            if (!found || property->flag & PROP_ENUM_FLAG) {
              withheld = Value::string("COMPUTED_VALUE");
              tag = value = {};
            }
            else {
              tag = Value::string("ENUM");
              value = std::move(raw_value);
            }
          }
          else {
            tag = Value::string(property->type == PROP_STRING ? "STRING" : "BOOLEAN");
            value = property->type == PROP_BOOLEAN ?
                        Value::boolean_value(raw_value.kind == Value::Kind::Boolean ? raw_value.boolean :
                                                                                     raw_value.integer != 0) :
                        std::move(raw_value);
          }
        }
      }
    }
    else {
      notes = plain_annotations();
      const Value *code = find_field(result, "code");
      withheld = code ? *code : Value::string("UNSUPPORTED_VALUE");
    }
    copied.push_back(moved_dict(Field("identifier", Value::string(identifier)),
                                Field("tag", std::move(tag)), Field("value", std::move(value)),
                                Field("withheld", std::move(withheld)),
                                Field("annotations", std::move(notes))));
  }
  Value id_facts;
  if (node.ptr.data == node.ptr.owner_id && node.ptr.owner_id && !node.virtual_storage &&
      (node.ptr.type->flag & STRUCT_ID))
  {
    const ID &id = *node.ptr.owner_id;
    /* Stored ID identity is copied independently of owner-name wrappers. Fields above still
     * require admission for every displayed label; path/name screening remains Python-owned. */
    id_facts = Value::dict({{"name", Value::string(id.name + 2)},
                            {"id_type", Value::string(enum_identifier(rna_enum_id_type_items, GS(id.name)))},
                            {"embedded", Value::boolean_value(id.flag & ID_FLAG_EMBEDDED_DATA)},
                            {"linked", Value::boolean_value(id.lib != nullptr)}});
  }
  /* A reference's node is only labelled, never listed: its declarations key is not needed. */
  return success("REFERENCE", moved_dict(Field("node", node_value(context, address, node, false)),
                                         Field("id_facts", std::move(id_facts)),
                                         Field("fields", Value::tuple(std::move(copied)))));
}

static bool virtual_item(const Target &collection, int index, Target &item)
{
  const StructRNA *fixed = static_cast<CollectionPropertyRNA *>(collection.property)->item_type;
  if (!fixed) {
    return false;
  }
  item = collection;
  item.kind = Target::Kind::Struct;
  item.property = nullptr;
  item.ptr.type = const_cast<StructRNA *>(fixed);
  if (collection.weight_vertex >= 0) {
    const Mesh &mesh = *reinterpret_cast<const Mesh *>(collection.ptr.owner_id);
    const Span<MDeformVert> deform = mesh.deform_verts();
    item.weight = &deform[collection.weight_vertex].dw[index];
    item.ptr.data = const_cast<MDeformWeight *>(item.weight);
    item.virtual_kind = "WEIGHT";
    item.virtual_storage = false;
  }
  else {
    item.storage = &IDP_property_array_get(collection.storage)[index];
    if (item.storage->type != IDP_GROUP || !storage_group_valid(*item.storage)) {
      return false;
    }
    item.ptr.data = const_cast<IDProperty *>(item.storage);
    item.virtual_kind = "STORED_COLLECTION_ITEM";
  }
  return true;
}

static Value items_target(const Context &context,
                          const Address &address,
                          const Target &target,
                          int edge,
                          int offset,
                          int limit)
{
  Target collection;
  Value failure;
  if (!materialize(context, target, collection, failure)) {
    return failure.kind == Value::Kind::Null ? missing_detail("NOT_A_COLLECTION", edge) : failure;
  }
  if (collection.kind != Target::Kind::Collection) {
    return missing_detail("NOT_A_COLLECTION", edge);
  }
  const RouteFacts current = routes_for(collection, *collection.property);
  if (current.attribute_elements) {
    return unavailable("DENIED");
  }
  if (!collection.virtual_storage && collection.weight_vertex < 0) {
    if (const char *reason = route_refusal(collection, current)) {
      return unavailable(reason);
    }
  }
  int length;
  const char *length_reason = nullptr;
  if (!collection_length(collection, length, &length_reason)) {
    return unavailable(length_reason ? length_reason :
                       collection.virtual_storage || collection.weight_vertex >= 0 || current.const_attribute_kind ?
                           "INVALID_STORAGE" : "NATIVE_READER_REQUIRED");
  }
  const int count = offset < length ? std::min(limit, length - offset) : 0;
  std::vector<Value> copied;
  auto copy_item = [&](const Target &item, int index) {
    Address selected = address;
    selected.expansion.push_back({Step::Kind::Index, "", index});
    /* Depth2 addresses have room for item selection before their field expansion. */
    copied.push_back(moved_dict(Field("index", Value::int_value(index)),
                                Field("node", item.kind == Target::Kind::Null ?
                                                  Value{} :
                                                  node_value(context, selected, item))));
  };
  if (count && address.expansion.size() >= 2) {
    return unavailable("UNSUPPORTED_VALUE");
  }
  if (current.owned_collection_kind) {
    for (int index = offset; index < offset + count; index++) {
      Target item;
      if (!owned_collection_item(collection, index, item)) {
        return unavailable("INVALID_STORAGE");
      }
      copy_item(item, index);
      if (BLI_time_now_seconds() >= context.deadline) {
        break;
      }
    }
  }
  else if (current.geometry_collection || current.traversal_collection) {
    for (int index = offset; index < offset + count; index++) {
      Target item;
      if (!helper_collection_item(collection, index, item, failure)) {
        return failure;
      }
      copy_item(item, index);
      if (BLI_time_now_seconds() >= context.deadline) {
        break;
      }
    }
  }
  else if (current.const_attribute_kind) {
    for (int index = offset; index < offset + count; index++) {
      Target item;
      if (!const_attribute_item(collection, index, item)) {
        return unavailable("INVALID_STORAGE");
      }
      copy_item(item, index);
      if (BLI_time_now_seconds() >= context.deadline) {
        break;
      }
    }
  }
  else if (current.mesh_collection_kind) {
    for (int index = offset; index < offset + count; index++) {
      Target item;
      if (!const_geometry_item(collection, index, item)) {
        return unavailable("INVALID_STORAGE");
      }
      copy_item(item, index);
      if (BLI_time_now_seconds() >= context.deadline) {
        break;
      }
    }
  }
  else if (collection.virtual_storage || collection.weight_vertex >= 0) {
    for (int index = offset; index < offset + count; index++) {
      Target item;
      if (!virtual_item(collection, index, item)) {
        return unavailable("INVALID_STORAGE");
      }
      copy_item(item, index);
      if (BLI_time_now_seconds() >= context.deadline) {
        break;
      }
    }
  }
  else if (count) {
    const RouteFacts routes = routes_for(collection, *collection.property);
    if (!STREQ(routes.iterate, "SOURCE_COLLECTION") ||
        !one_of(routes.refine, {"NONE", "SOURCE_REFINE"}))
    {
      return unavailable("NATIVE_READER_REQUIRED");
    }
    PointerRNA owner = collection.ptr;
    IteratorGuard guard(owner, *collection.property);
    int index = 0;
    while (guard.iterator.valid && int(copied.size()) < count) {
      if (index >= offset) {
        Target item;
        item.operation = collection.operation;
        item.ptr = guard.iterator.ptr;
        item.kind = item.ptr.data ? Target::Kind::Struct : Target::Kind::Null;
        copy_item(item, index); /* No child pointer escapes the still-active iterator. */
      }
      if (BLI_time_now_seconds() >= context.deadline) {
        if (copied.empty()) {
          return Value::dict({{"status", Value::string("BUDGET")}});
        }
        break;
      }
      RNA_property_collection_next(&guard.iterator);
      index++;
    }
  }
  const int consumed = copied.size();
  return success("ITEMS", moved_dict(Field("length", Value::int_value(length)),
                                     Field("offset", Value::int_value(offset)),
                                     Field("items", Value::tuple(std::move(copied))),
                                     Field("more", Value::boolean_value(offset < length &&
                                                                       consumed < length - offset))));
}

static Value choices_target(const Context &context,
                            const Address &address,
                            const Target &target,
                            int edge,
                            int64_t expected,
                            int offset,
                            int limit)
{
  if (target.kind != Target::Kind::Property || target.property->type != PROP_ENUM || edge == 0) {
    return unavailable("UNSUPPORTED_VALUE");
  }
  PropertyRNA &property = *target.property;
  if (fingerprint(context, target, property) != expected) {
    return stale(address, edge - 1);
  }
  const RouteFacts routes = routes_for(target, property);
  EnumItemsGuard guard;
  if (property.flag_internal & PROP_INTERN_RUNTIME) {
    const EnumPropertyRNA &declaration = static_cast<EnumPropertyRNA &>(property);
    guard.items = declaration.item;
    guard.length = declaration.totitem;
  }
  else {
    if (const char *reason = route_refusal(target, routes)) {
      return unavailable(reason);
    }
    if (!STREQ(routes.enum_decl_items, "SOURCE_ENUM_DECL")) {
      return unavailable("NATIVE_READER_REQUIRED");
    }
    /* Match the metadata EnumProperty context (C=nullptr, ptr.data=the declaration), including
     * PROP_ENUM_NO_CONTEXT. It is distinct from ordinary value-conversion's bound context. */
    PointerRNA declaration(nullptr, RNA_EnumProperty, &property);
    RNA_property_enum_items_ex(nullptr, &declaration, &property, false,
                               &guard.items, &guard.length, &guard.free);
  }
  std::vector<Value> copied;
  const int length = guard.items ? guard.length : 0;
  const int count = offset < length ? std::min(limit, length - offset) : 0;
  for (int index = offset; index < offset + count; index++) {
    const EnumPropertyItem &item = guard.items[index];
    copied.push_back(Value::tuple({Value::string(item.identifier ? item.identifier : ""),
                                   Value::string(item.name ? item.name : ""),
                                   Value::int_value(item.value)}));
    if (BLI_time_now_seconds() >= context.deadline) {
      break;
    }
  }
  const int consumed = copied.size();
  return success("CHOICES", Value::dict({{"items", Value::tuple(std::move(copied))},
                                         {"length", Value::int_value(length)},
                                         {"offset", Value::int_value(offset)},
                                         {"more", Value::boolean_value(offset < length &&
                                                                         consumed < length - offset)}}));
}

/* ABI wrappers. No Python allocation/exception emission occurs until resolve and all scoped
 * native borrows end. */
struct Request {
  const Context *context = nullptr;
  Address address;
  Allowance allowance;
};

static bool parse_request(PyObject *capsule,
                          PyObject *address,
                          PyObject *allowance,
                          Request &request)
{
  if (!require_main_thread() || !parse_address(address, request.address) ||
      !parse_allowance(allowance, request.address, request.allowance))
  {
    return false;
  }
  request.context = get_context(capsule);
  return request.context != nullptr;
}

static bool exact_index(PyObject *object, int &value, int minimum, int maximum)
{
  if (!PyLong_CheckExact(object)) {
    PyErr_SetString(PyExc_TypeError, "Read page numbers must be exact integers");
    return false;
  }
  long long parsed = PyLong_AsLongLong(object);
  if (PyErr_Occurred()) {
    return false;
  }
  if (parsed < minimum || parsed > maximum) {
    PyErr_SetString(PyExc_ValueError, "Read page number is outside its bounds");
    return false;
  }
  value = parsed;
  return true;
}

static bool exact_relation(PyObject *object, std::string &relation)
{
  Value owned;
  if (!parse_value(object, owned)) {
    return false;
  }
  if (owned.kind != Value::Kind::String || !ELEM(owned.text, "self", "owner")) {
    PyErr_SetString(PyExc_ValueError, "Read relation must be self or owner");
    return false;
  }
  relation = std::move(owned.text);
  return true;
}

template<typename Function> static PyObject *allocation_guard(Function &&function)
{
  try {
    return function();
  }
  catch (const std::bad_alloc &) {
    /* C++ stack unwinding has released every iterator/borrow before Python raises. */
    return PyErr_NoMemory();
  }
}

static PyObject *abi_begin(PyObject * /*self*/, PyObject *args)
{
  return allocation_guard([&]() -> PyObject * {
    PyObject *window, *seconds;
    if (!PyArg_ParseTuple(args, "OO:begin", &window, &seconds)) {
      return nullptr;
    }
    return begin(window, seconds);
  });
}

/* (identifier, fingerprint) pairs naming admitted declarations of one node. */
static bool parse_members(PyObject *object,
                          size_t maximum,
                          std::vector<std::pair<std::string, int64_t>> &members)
{
  bool valid = PyTuple_CheckExact(object) && size_t(PyTuple_GET_SIZE(object)) <= maximum;
  /* Views of the request's own UTF-8 buffers, alive while it is parsed; sorted to find repeats. */
  std::vector<std::string_view> identifiers;
  if (valid) {
    identifiers.reserve(PyTuple_GET_SIZE(object));
    members.reserve(PyTuple_GET_SIZE(object));
  }
  for (Py_ssize_t index = 0; valid && index < PyTuple_GET_SIZE(object); index++) {
    PyObject *entry = PyTuple_GET_ITEM(object, index);
    int64_t expected = 0;
    valid = PyTuple_CheckExact(entry) && PyTuple_GET_SIZE(entry) == 2 &&
            PyUnicode_CheckExact(PyTuple_GET_ITEM(entry, 0)) &&
            exact_fingerprint(PyTuple_GET_ITEM(entry, 1), expected);
    if (!valid) {
      break;
    }
    Py_ssize_t length;
    const char *text = PyUnicode_AsUTF8AndSize(PyTuple_GET_ITEM(entry, 0), &length);
    if (!text) {
      PyErr_Clear();
      valid = false;
      break;
    }
    valid = length > 0 && length <= 256 && !memchr(text, '\0', length);
    if (valid) {
      identifiers.emplace_back(text, size_t(length));
      members.emplace_back(std::string(text, length), expected);
    }
  }
  if (valid) {
    std::sort(identifiers.begin(), identifiers.end());
    valid = std::adjacent_find(identifiers.begin(), identifiers.end()) == identifiers.end();
  }
  if (!valid) {
    PyErr_SetString(PyExc_ValueError, "Invalid or duplicate read members");
  }
  return valid;
}

static bool parse_expected(PyObject *object, int64_t &expected)
{
  if (!exact_fingerprint(object, expected)) {
    PyErr_SetString(PyExc_ValueError, "Expected declaration must be a fingerprint");
    return false;
  }
  return true;
}

static PyObject *abi_describe(PyObject * /*self*/, PyObject *args)
{
  return allocation_guard([&]() -> PyObject * {
    PyObject *capsule, *address, *allowance, *member, *detail = Py_False, *copy = Py_True;
    if (!PyArg_ParseTuple(args, "OOOO|OO:describe", &capsule, &address, &allowance, &member,
                          &detail, &copy))
    {
      return nullptr;
    }
    Value name;
    if (member != Py_None && (!parse_value(member, name) || name.kind != Value::Kind::String ||
                              name.text.empty()))
    {
      if (!PyErr_Occurred()) {
        PyErr_SetString(PyExc_TypeError, "Read member must be None or an exact identifier");
      }
      return nullptr;
    }
    if (!PyBool_Check(detail) || !PyBool_Check(copy)) {
      PyErr_SetString(PyExc_TypeError, "Read detail and declaration copying must be exact bools");
      return nullptr;
    }
    Request request;
    if (!parse_request(capsule, address, allowance, request)) {
      return nullptr;
    }
    Value copied = resolve(*request.context, request.address, request.allowance,
                           [&](const Target &target, int edge) {
      return describe_target(*request.context, request.address, target, edge,
                              member == Py_None ? nullptr : &name.text, detail == Py_True,
                              copy == Py_True);
    });
    return encode(copied);
  });
}

static PyObject *abi_fetch(PyObject * /*self*/, PyObject *args)
{
  return allocation_guard([&]() -> PyObject * {
    PyObject *capsule, *address, *allowance, *expected_object;
    if (!PyArg_ParseTuple(args, "OOOO:fetch", &capsule, &address, &allowance, &expected_object)) {
      return nullptr;
    }
    int64_t expected;
    if (!parse_expected(expected_object, expected)) {
      return nullptr;
    }
    Request request;
    if (!parse_request(capsule, address, allowance, request)) {
      return nullptr;
    }
    Value copied = resolve(*request.context, request.address, request.allowance,
                           [&](const Target &target, int edge) {
      return expanded_value(fetch_target(*request.context, request.address, target, edge, expected));
    });
    return encode(copied);
  });
}

/* A member's fetch result in the compact form of a value page: ("OK", tag, value, value_source,
 * computed_getter), ("UNAVAILABLE", code), or ("RETRY",) for anything the caller must fetch alone
 * (a changed declaration, a missing step, the deadline). */
static Value compact_member(Value result)
{
  if (compact_value(result)) {
    return result;
  }
  const Value *status = find_field(result, "status");
  if (status && status->text == "OK") {
    Value &payload = field(result, "payload");
    Value &notes = field(payload, "annotations");
    return moved_tuple(Value::string("OK"), std::move(field(payload, "tag")),
                       std::move(field(payload, "value")), std::move(field(notes, "value_source")),
                       std::move(field(notes, "computed_getter")));
  }
  if (status && status->text == "UNAVAILABLE") {
    return moved_tuple(Value::string("UNAVAILABLE"), std::move(field(result, "code")));
  }
  return moved_tuple(Value::string("RETRY"));
}

/* Steps from a batch's resolved address to one group or entry: at most two, an attribute step
 * admitted by its declaration fingerprint exactly like an allowance edge. */
struct RelativeStep {
  Step step;
  int64_t expected = 0;
};

static bool parse_relative_steps(PyObject *object, std::vector<RelativeStep> &steps)
{
  bool valid = PyTuple_CheckExact(object) && PyTuple_GET_SIZE(object) <= 2;
  for (Py_ssize_t index = 0; valid && index < PyTuple_GET_SIZE(object); index++) {
    PyObject *step = PyTuple_GET_ITEM(object, index);
    const Py_ssize_t size = PyTuple_CheckExact(step) ? PyTuple_GET_SIZE(step) : 0;
    PyObject *kind = size >= 2 ? PyTuple_GET_ITEM(step, 0) : nullptr;
    RelativeStep relative;
    if (kind && PyUnicode_CheckExact(kind) && PyUnicode_CompareWithASCIIString(kind, "attribute") == 0 &&
        size == 3 && PyUnicode_CheckExact(PyTuple_GET_ITEM(step, 1)))
    {
      Py_ssize_t length;
      const char *text = PyUnicode_AsUTF8AndSize(PyTuple_GET_ITEM(step, 1), &length);
      valid = text && length > 0 && length <= 256 && !memchr(text, '\0', length) &&
              exact_fingerprint(PyTuple_GET_ITEM(step, 2), relative.expected);
      if (valid) {
        relative.step = {Step::Kind::Attribute, std::string(text, length), 0};
      }
    }
    else if (kind && PyUnicode_CheckExact(kind) && PyUnicode_CompareWithASCIIString(kind, "index") == 0 &&
             size == 2)
    {
      int64_t position = 0;
      valid = exact_fingerprint(PyTuple_GET_ITEM(step, 1), position) && position <= 10000063;
      relative.step = {Step::Kind::Index, "", position};
    }
    else {
      valid = false;
    }
    if (PyErr_Occurred()) {
      PyErr_Clear();
      valid = false;
    }
    if (valid) {
      steps.push_back(std::move(relative));
    }
  }
  return valid;
}

/* The address and allowance of a group: the batch's own, extended by the relative steps. */
static void extend(const Address &base,
                   const Allowance &allowance,
                   const std::vector<RelativeStep> &steps,
                   Address &address,
                   Allowance &extended)
{
  address = base;
  extended = allowance;
  const int count = base.parsed.size() + base.expansion.size();
  for (size_t index = 0; index < steps.size(); index++) {
    address.expansion.push_back(steps[index].step);
    if (steps[index].step.kind == Step::Kind::Attribute) {
      extended.edges.emplace_back(count + int(index) + 1, steps[index].expected);
    }
  }
}

/* The admitted members of one struct, each fetched exactly as a single fetch of the struct's address
 * extended by that member: ("OK", entries) where each entry is compact_member's form. */
static Value fetch_members(const Context &context,
                           const Address &address,
                           const Target &target,
                           int edge,
                           const std::vector<std::pair<std::string, int64_t>> &members)
{
  Target node;
  Value failure;
  if (!materialize(context, target, node, failure)) {
    return failure.kind == Value::Kind::Null ? missing_detail("NOT_A_STRUCT", edge) : failure;
  }
  if (node.kind != Target::Kind::Struct || !node.ptr.type) {
    return missing_detail(node.kind == Target::Kind::Null ? "NULL" : "NOT_A_STRUCT", edge);
  }
  if (const char *reason = target_refusal(context, node)) {
    return unavailable(reason);
  }
  if (address.expansion.size() >= 2) {
    return unavailable("UNSUPPORTED_VALUE");
  }
  std::vector<Value> results;
  results.reserve(members.size());
  /* Several members are found in one pass over the declarations (RNA_struct_type_find_property
   * scans them once per name), in the same search order, so the first match wins as there. */
  std::vector<PropertyRNA *> found(members.size(), nullptr);
  const bool batched = members.size() > 4;
  if (batched) {
    std::vector<std::pair<std::string_view, size_t>> wanted;
    wanted.reserve(members.size());
    for (size_t index = 0; index < members.size(); index++) {
      wanted.emplace_back(members[index].first, index);
    }
    std::sort(wanted.begin(), wanted.end());
    size_t remaining = wanted.size();
    for (StructRNA *current = node.ptr.type; current && remaining; current = current->base) {
      for (PropertyRNA &property : current->cont.properties) {
        const std::string_view name(property.identifier);
        auto match = std::lower_bound(wanted.begin(), wanted.end(),
                                      std::make_pair(name, size_t(0)));
        if (match != wanted.end() && match->first == name && !found[match->second]) {
          found[match->second] = &property;
          if (--remaining == 0) {
            break;
          }
        }
      }
    }
  }
  Address member_address = address;
  member_address.expansion.push_back({Step::Kind::Attribute, std::string(), 0});
  for (size_t index = 0; index < members.size(); index++) {
    const auto &[identifier, expected] = members[index];
    PropertyRNA *property = nullptr;
    if (BLI_time_now_seconds() < context.deadline) {
      property = batched ? found[index] :
                           RNA_struct_type_find_property(node.ptr.type, identifier.c_str());
    }
    if (!property || fingerprint(context, node, *property) != expected) {
      results.push_back(moved_tuple(Value::string("RETRY")));
      continue;
    }
    member_address.expansion.back().text = identifier;
    Target child = node;
    child.kind = Target::Kind::Property;
    child.property = property;
    results.push_back(compact_member(fetch_target(context, member_address, child, edge + 1, expected)));
  }
  return moved_tuple(Value::string("OK"), Value::tuple(std::move(results)));
}

struct FetchGroup {
  std::vector<RelativeStep> steps;
  std::vector<std::pair<std::string, int64_t>> members;
};

/* One resolution of the batch's address, then each group: its node (the address itself, or the
 * address extended by the group's steps, replayed from there) and its members. A group whose node
 * does not resolve is ("RETRY",); the caller then reads it alone. */
static Value fetch_many_target(const Context &context,
                               const Address &address,
                               const Allowance &allowance,
                               const Target &target,
                               int edge,
                               const std::vector<FetchGroup> &groups)
{
  Target base;
  Value failure;
  if (!materialize(context, target, base, failure)) {
    return failure.kind == Value::Kind::Null ? missing_detail("NOT_A_STRUCT", edge) : failure;
  }
  std::vector<Value> results;
  results.reserve(groups.size());
  for (const FetchGroup &group : groups) {
    Value result;
    if (group.steps.empty()) {
      result = fetch_members(context, address, base, edge, group.members);
    }
    else {
      Address group_address;
      Allowance group_allowance;
      extend(address, allowance, group.steps, group_address, group_allowance);
      result = replay(context, group_address, group_allowance, edge, base,
                      [&](const Target &node, int node_edge) {
        return fetch_members(context, group_address, node, node_edge, group.members);
      });
    }
    results.push_back(result.kind == Value::Kind::Tuple ? std::move(result) :
                                                          moved_tuple(Value::string("RETRY")));
  }
  return success("VALUES", Value::tuple(std::move(results)));
}

static PyObject *abi_fetch_many(PyObject * /*self*/, PyObject *args)
{
  return allocation_guard([&]() -> PyObject * {
    PyObject *capsule, *address, *allowance, *groups_object;
    if (!PyArg_ParseTuple(args, "OOOO:fetch_many", &capsule, &address, &allowance, &groups_object)) {
      return nullptr;
    }
    std::vector<FetchGroup> groups;
    bool valid = PyTuple_CheckExact(groups_object) && PyTuple_GET_SIZE(groups_object) <= read_items_max;
    for (Py_ssize_t index = 0; valid && index < PyTuple_GET_SIZE(groups_object); index++) {
      PyObject *item = PyTuple_GET_ITEM(groups_object, index);
      FetchGroup group;
      valid = PyTuple_CheckExact(item) && PyTuple_GET_SIZE(item) == 2 &&
              parse_relative_steps(PyTuple_GET_ITEM(item, 0), group.steps) &&
              parse_members(PyTuple_GET_ITEM(item, 1), 256, group.members);
      if (valid) {
        groups.push_back(std::move(group));
      }
    }
    if (!valid) {
      if (!PyErr_Occurred()) {
        PyErr_SetString(PyExc_ValueError, "Invalid value page groups");
      }
      return nullptr;
    }
    Request request;
    if (!parse_request(capsule, address, allowance, request)) {
      return nullptr;
    }
    Value copied = resolve(*request.context, request.address, request.allowance,
                           [&](const Target &target, int edge) {
      return fetch_many_target(*request.context, request.address, request.allowance, target, edge,
                               groups);
    });
    return encode(copied);
  });
}

/* One reference of a reference page: a node reached from the batch's address by relative steps,
 * with the fields a single reference call would name. */
struct ReferenceEntry {
  std::vector<RelativeStep> steps;
  std::string relation;
  std::vector<std::pair<std::string, int64_t>> fields;
};

/* One resolution of the owner, then each entry exactly as a single reference (and, for an Object,
 * object identity) of the owner's address extended by the entry's steps. Per entry:
 * (reference result, object identity result or None). */
static Value reference_many_target(const Context &context,
                                   const Address &address,
                                   const Allowance &allowance,
                                   const Target &target,
                                   int edge,
                                   const std::vector<ReferenceEntry> &entries)
{
  Target owner;
  Value failure;
  if (!materialize(context, target, owner, failure)) {
    return failure.kind == Value::Kind::Null ? missing_detail("NOT_A_STRUCT", edge) : failure;
  }
  std::vector<Value> results;
  results.reserve(entries.size());
  for (const ReferenceEntry &entry : entries) {
    if (BLI_time_now_seconds() >= context.deadline) {
      results.push_back(moved_tuple(Value::dict({{"status", Value::string("BUDGET")}}), Value{}));
      continue;
    }
    Address entry_address;
    Allowance entry_allowance;
    extend(address, allowance, entry.steps, entry_address, entry_allowance);
    if (entry_address.expansion.size() > 2) {
      results.push_back(moved_tuple(unavailable("UNSUPPORTED_VALUE"), Value{}));
      continue;
    }
    Value identity;
    Value reference = replay(context, entry_address, entry_allowance, edge, owner,
                             [&](const Target &final_target, int final_edge) {
      Value result = reference_target(context, entry_address, final_target, final_edge,
                                      entry.relation.c_str(), entry.fields);
      const Value *status = find_field(result, "status");
      if (status && status->text == "OK") {
        const Value &facts = *find_field(*find_field(result, "payload"), "id_facts");
        const Value *id_type = find_field(facts, "id_type");
        if (id_type && id_type->text == "OBJECT") {
          identity = object_identity_target(context, final_target, entry.relation.c_str());
        }
      }
      return result;
    });
    results.push_back(moved_tuple(std::move(reference), std::move(identity)));
  }
  return success("REFERENCES", Value::tuple(std::move(results)));
}

static bool parse_reference_entries(PyObject *object, std::vector<ReferenceEntry> &entries)
{
  bool valid = PyTuple_CheckExact(object) && PyTuple_GET_SIZE(object) <= 4 * read_items_max;
  for (Py_ssize_t index = 0; valid && index < PyTuple_GET_SIZE(object); index++) {
    PyObject *item = PyTuple_GET_ITEM(object, index);
    ReferenceEntry entry;
    valid = PyTuple_CheckExact(item) && PyTuple_GET_SIZE(item) == 3 &&
            parse_relative_steps(PyTuple_GET_ITEM(item, 0), entry.steps) && !entry.steps.empty() &&
            exact_relation(PyTuple_GET_ITEM(item, 1), entry.relation) &&
            parse_members(PyTuple_GET_ITEM(item, 2), 4, entry.fields);
    if (valid) {
      entries.push_back(std::move(entry));
    }
  }
  if (!valid && !PyErr_Occurred()) {
    PyErr_SetString(PyExc_ValueError, "Invalid reference page entries");
  }
  return valid;
}

static PyObject *abi_reference_many(PyObject * /*self*/, PyObject *args)
{
  return allocation_guard([&]() -> PyObject * {
    PyObject *capsule, *address, *allowance, *entries_object;
    if (!PyArg_ParseTuple(args, "OOOO:reference_many", &capsule, &address, &allowance, &entries_object)) {
      return nullptr;
    }
    std::vector<ReferenceEntry> entries;
    if (!parse_reference_entries(entries_object, entries)) {
      return nullptr;
    }
    Request request;
    if (!parse_request(capsule, address, allowance, request)) {
      return nullptr;
    }
    Value copied = resolve(*request.context, request.address, request.allowance,
                           [&](const Target &target, int edge) {
      return reference_many_target(*request.context, request.address, request.allowance, target,
                                   edge, entries);
    });
    return encode(copied);
  });
}

static PyObject *abi_items(PyObject * /*self*/, PyObject *args)
{
  return allocation_guard([&]() -> PyObject * {
    PyObject *capsule, *address, *allowance, *offset_object, *limit_object;
    if (!PyArg_ParseTuple(args, "OOOOO:items", &capsule, &address, &allowance,
                          &offset_object, &limit_object))
    {
      return nullptr;
    }
    int offset, limit;
    if (!exact_index(offset_object, offset, 0, INT_MAX) ||
        !exact_index(limit_object, limit, 0, read_items_max))
    {
      return nullptr;
    }
    Request request;
    if (!parse_request(capsule, address, allowance, request)) {
      return nullptr;
    }
    Value copied = resolve(*request.context, request.address, request.allowance,
                           [&](const Target &target, int edge) {
      return items_target(*request.context, request.address, target, edge, offset, limit);
    });
    return encode(copied);
  });
}

static PyObject *abi_choices(PyObject * /*self*/, PyObject *args)
{
  return allocation_guard([&]() -> PyObject * {
    PyObject *capsule, *address, *allowance, *expected_object;
    PyObject *offset_object = nullptr, *limit_object = nullptr;
    if (!PyArg_ParseTuple(args, "OOOO|OO:choices", &capsule, &address, &allowance,
                          &expected_object, &offset_object, &limit_object))
    {
      return nullptr;
    }
    int offset = 0, limit = read_items_max;
    if ((offset_object && !exact_index(offset_object, offset, 0, INT_MAX)) ||
        (limit_object && !exact_index(limit_object, limit, 1, read_items_max)))
    {
      return nullptr;
    }
    int64_t expected;
    if (!parse_expected(expected_object, expected)) {
      return nullptr;
    }
    Request request;
    if (!parse_request(capsule, address, allowance, request)) {
      return nullptr;
    }
    Value copied = resolve(*request.context, request.address, request.allowance,
                           [&](const Target &target, int edge) {
      return choices_target(*request.context, request.address, target, edge, expected, offset, limit);
    });
    return encode(copied);
  });
}

static PyObject *abi_reference(PyObject * /*self*/, PyObject *args)
{
  return allocation_guard([&]() -> PyObject * {
    PyObject *capsule, *address, *allowance, *relation_object, *fields_object;
    if (!PyArg_ParseTuple(args, "OOOOO:reference", &capsule, &address, &allowance,
                          &relation_object, &fields_object))
    {
      return nullptr;
    }
    std::string relation;
    std::vector<std::pair<std::string, int64_t>> fields;
    if (!exact_relation(relation_object, relation) || !parse_members(fields_object, 4, fields)) {
      return nullptr;
    }
    Request request;
    if (!parse_request(capsule, address, allowance, request)) {
      return nullptr;
    }
    Value copied = resolve(*request.context, request.address, request.allowance,
                           [&](const Target &target, int edge) {
      return reference_target(*request.context, request.address, target, edge,
                               relation.c_str(), fields);
    });
    return encode(copied);
  });
}

static PyObject *abi_object_identity(PyObject * /*self*/, PyObject *args)
{
  return allocation_guard([&]() -> PyObject * {
    PyObject *capsule, *address, *allowance, *relation_object;
    if (!PyArg_ParseTuple(args, "OOOO:object_identity", &capsule, &address, &allowance,
                          &relation_object))
    {
      return nullptr;
    }
    std::string relation;
    if (!exact_relation(relation_object, relation)) {
      return nullptr;
    }
    Request request;
    if (!parse_request(capsule, address, allowance, request)) {
      return nullptr;
    }
    Value copied = resolve(*request.context, request.address, request.allowance,
                           [&](const Target &target, int /*edge*/) {
      return object_identity_target(*request.context, target, relation.c_str());
    });
    return encode(copied);
  });
}

static PyObject *abi_check(PyObject * /*self*/, PyObject *capsule)
{
  return allocation_guard([&]() { return check(capsule); });
}

static PyObject *abi_close(PyObject * /*self*/, PyObject *capsule)
{
  return close(capsule);
}

}  // namespace address_read

static PyMethodDef methods[] = {
    {"begin",
     address_read::abi_begin,
     METH_VARARGS,
     "Open a request-local detached reader context."},
    {"describe",
     address_read::abi_describe,
     METH_VARARGS,
     "Copy the current declaration at an address."},
    {"fetch", address_read::abi_fetch, METH_VARARGS, "Copy an admitted value."},
    {"fetch_many",
     address_read::abi_fetch_many,
     METH_VARARGS,
     "Copy the admitted values of several members of one struct."},
    {"reference_many",
     address_read::abi_reference_many,
     METH_VARARGS,
     "Copy references to several members or items of one struct or collection."},
    {"choices",
     address_read::abi_choices,
     METH_VARARGS,
     "Copy a page of an admitted enum declaration's choices."},
    {"items",
     address_read::abi_items,
     METH_VARARGS,
     "Copy a page of an admitted collection's items."},
    {"reference", address_read::abi_reference, METH_VARARGS, "Copy an admitted reference."},
    {"object_identity",
     address_read::abi_object_identity,
     METH_VARARGS,
     "Copy a fresh Object identity."},
    {"check", address_read::abi_check, METH_O, "Validate detached reader lifecycle and deadline."},
    {"close",
     address_read::abi_close,
     METH_O,
     "Close detached reader context without native access."},
    {"vertex_groups",
     read_vertex_groups,
     METH_VARARGS,
     "vertex_groups(mesh, vertex_index, offset, limit): copy at most 64 stored weights."},
    {"image_tile_metadata",
     read_image_tile_metadata,
     METH_VARARGS,
     "image_tile_metadata(image, tile_number, frame=0, view=0): exact cached size/channels or "
     "None."},
    {"image_metadata",
     read_image_metadata,
     METH_O,
     "image_metadata(image): exact default cached metadata or None; never loads an image."},
    {"property_info",
     read_property_info,
     METH_O,
     "property_info(property): runtime and Python read-callback flags without calling callbacks."},
    {nullptr, nullptr, 0, nullptr},
};

static PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    "_bpy._angelo_read",
    nullptr,
    /* The interned keys of image_metadata's record (bpy_angelo_read_words.hh). */
    angelo_read_words::state_size(),
    methods,
    nullptr,
    angelo_read_words::traverse_state,
    angelo_read_words::clear_state,
    angelo_read_words::free_state,
};

PyObject *BPY_angelo_read_module()
{
  PyObject *mod = PyModule_Create(&module);
  if (mod && !angelo_read_words::init(mod)) {
    Py_DECREF(mod);
    return nullptr;
  }
  if (mod && PyModule_AddFunctions(mod, BPY_angelo_metadata_methods()) < 0) {
    Py_DECREF(mod);
    return nullptr;
  }
  /* The address-read ABI the add-on's data_read_access requires; without it every live read
   * answers NATIVE_READER_REQUIRED. Bump together with that module when the ABI changes. */
  if (mod && PyModule_AddIntConstant(mod, "ADDRESS_READ_VERSION", 2) < 0) {
    Py_DECREF(mod);
    return nullptr;
  }
  return mod;
}

}  // namespace blender
