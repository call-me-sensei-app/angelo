/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup blenloader
 *
 * Bounded preflight for the native SDNA decoder used by the metadata scanner.
 */

#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <string_view>
#include <vector>

#include "BLI_span.hh"

namespace blender::blo {
namespace metadata_scan_dna_detail {

class Cursor {
 private:
  Span<std::byte> data_;
  size_t offset_ = 0;

 public:
  explicit Cursor(const Span<std::byte> data) : data_(data) {}

  size_t remaining() const
  {
    return size_t(data_.size()) - offset_;
  }

  template<typename T> bool read(T &value)
  {
    if (remaining() < sizeof(T)) {
      return false;
    }
    /* The native decoder accepts native-endian SDNA. Avoid its alignment assumptions here. */
    std::memcpy(&value, data_.data() + offset_, sizeof(T));
    offset_ += sizeof(T);
    return true;
  }

  bool tag(const std::string_view value)
  {
    if (remaining() < value.size() ||
        std::memcmp(data_.data() + offset_, value.data(), value.size()) != 0)
    {
      return false;
    }
    offset_ += value.size();
    return true;
  }

  bool align_four()
  {
    const size_t padding = (4 - offset_ % 4) % 4;
    if (remaining() < padding) {
      return false;
    }
    offset_ += padding;
    return true;
  }

  bool count(uint32_t &value, const uint32_t max_entries, bool *r_limit_exceeded)
  {
    int32_t signed_value;
    if (!read(signed_value) || signed_value <= 0 ||
        uint32_t(signed_value) > uint32_t(std::numeric_limits<int16_t>::max()) + 1 ||
        size_t(signed_value) > remaining())
    {
      return false;
    }
    if (uint32_t(signed_value) > max_entries) {
      if (r_limit_exceeded) {
        *r_limit_exceeded = true;
      }
      return false;
    }
    value = uint32_t(signed_value);
    return true;
  }

  bool strings(std::vector<std::string_view> &values,
               const uint32_t max_entries,
               bool *r_limit_exceeded)
  {
    uint32_t count;
    if (!this->count(count, max_entries, r_limit_exceeded)) {
      return false;
    }
    values.reserve(count);
    for (uint32_t index = 0; index < count; index++) {
      const char *start = reinterpret_cast<const char *>(data_.data() + offset_);
      const char *end = static_cast<const char *>(std::memchr(start, '\0', remaining()));
      if (!end || end == start) {
        return false;
      }
      const size_t length = size_t(end - start);
      values.emplace_back(start, length);
      offset_ += length + 1;
    }
    return align_four();
  }
};

struct MemberShape {
  uint16_t array_size = 1;
  bool is_pointer = false;
};

inline bool member_shape(const std::string_view name, MemberShape &shape)
{
  shape.is_pointer = name.front() == '*' || name.substr(0, 2) == "(*";
  uint32_t current = 0;
  uint32_t product = 1;
  bool in_array = false;
  bool has_digit = false;
  for (const char character : name) {
    if (character >= '0' && character <= '9') {
      const uint32_t digit = uint32_t(character - '0');
      /* DNA_member_array_num accumulates digits outside brackets too, including identifiers. */
      if (current > (uint32_t(std::numeric_limits<int32_t>::max()) - digit) / 10) {
        return false;
      }
      current = current * 10 + digit;
      has_digit = true;
    }
    else if (character == '[') {
      if (in_array) {
        return false;
      }
      in_array = true;
      has_digit = false;
      current = 0;
    }
    else if (character == ']') {
      if (!in_array || !has_digit || current == 0 ||
          current > uint32_t(std::numeric_limits<int16_t>::max()) / product)
      {
        return false;
      }
      product *= current;
      in_array = false;
    }
    else if (in_array) {
      return false;
    }
  }
  if (in_array) {
    return false;
  }
  shape.array_size = uint16_t(product);
  return true;
}

inline std::string_view canonical_type(const std::string_view type)
{
  /* init_structDNA applies these legacy renames even when do_alias is false. */
  if (type == "bScreen") {
    return "Screen";
  }
  if (type == "Collection") {
    return "Group";
  }
  if (type == "CollectionObject") {
    return "GroupObject";
  }
  return type;
}

inline bool primitive_size(const std::string_view type, const int16_t size)
{
  if (type == "char" || type == "uchar" || type == "int8_t") {
    return size == 1;
  }
  if (type == "short" || type == "ushort") {
    return size == 2;
  }
  if (type == "int" || type == "float") {
    return size == 4;
  }
  if (type == "double" || type == "int64_t" || type == "uint64_t") {
    return size == 8;
  }
  if (type == "long" || type == "ulong") {
    return size == 4 || size == 8;
  }
  return type != "void" || size == 0;
}

}  // namespace metadata_scan_dna_detail

/**
 * Validate storage, arithmetic and traversal bounds before DNA_sdna_from_data.
 *
 * The caller must cap the DNA byte length, retain the validated bytes unchanged, and decode with
 * data_alloc=true and do_alias=false. The native decoder remains the field-offset authority. This
 * preflight does not authorize alias expansion, reconstruction, recursive pointer traversal, or
 * primitive conversion, which have additional invariants. The caller must also compare the decoded
 * pointer size to the file header and bound every resulting offset against its block payload.
 *
 * max_entries bounds each table and the aggregate number of struct fields. Table indices, field
 * counts, type sizes and cached array lengths must also fit the native signed-short representation.
 * r_limit_exceeded, if supplied, is cleared on entry and set when validation stops at an otherwise
 * representable count exceeding max_entries. It does not establish validity of the unexamined data.
 */
inline bool metadata_scan_validate_dna(const Span<std::byte> data,
                                       const uint32_t max_entries,
                                       bool *r_limit_exceeded = nullptr)
{
  using namespace metadata_scan_dna_detail;
  if (r_limit_exceeded) {
    *r_limit_exceeded = false;
  }
  if (data.is_empty() || data.size() > std::numeric_limits<int32_t>::max()) {
    return false;
  }
  Cursor cursor(data);
  std::vector<std::string_view> names;
  std::vector<std::string_view> types;
  if (!cursor.tag("SDNA") || !cursor.tag("NAME") ||
      !cursor.strings(names, max_entries, r_limit_exceeded) || !cursor.tag("TYPE") ||
      !cursor.strings(types, max_entries, r_limit_exceeded) || !cursor.tag("TLEN"))
  {
    return false;
  }

  std::vector<MemberShape> shapes(names.size());
  bool gravity_fix = false;
  for (size_t index = 0; index < names.size(); index++) {
    if (!member_shape(names[index], shapes[index])) {
      return false;
    }
    gravity_fix |= index != 0 && names[index - 1] == "Cvi" && names[index] == "[3]";
  }

  std::vector<int16_t> sizes(types.size());
  for (size_t index = 0; index < types.size(); index++) {
    if (!cursor.read(sizes[index]) || sizes[index] < 0 || !primitive_size(types[index], sizes[index]))
    {
      return false;
    }
    types[index] = canonical_type(types[index]);
  }
  uint32_t structs_num;
  if (!cursor.align_four() || !cursor.tag("STRC") ||
      !cursor.count(structs_num, max_entries, r_limit_exceeded))
  {
    return false;
  }
  const Cursor structs_start = cursor;
  std::vector<bool> defined(types.size(), false);
  std::vector<std::string_view> struct_names;
  struct_names.reserve(structs_num);
  uint32_t total_fields = 0;
  int pointer_size = 0;
  for (uint32_t index = 0; index < structs_num; index++) {
    int16_t type, fields;
    if (!cursor.read(type) || !cursor.read(fields) || type < 0 || size_t(type) >= types.size() ||
        fields < 0 || defined[type] || size_t(fields) > cursor.remaining() / 4)
    {
      return false;
    }
    if (uint32_t(fields) > max_entries - total_fields) {
      if (r_limit_exceeded) {
        *r_limit_exceeded = true;
      }
      return false;
    }
    defined[type] = true;
    struct_names.push_back(types[type]);
    total_fields += uint32_t(fields);
    const bool list_base = types[type] == "ListBase";
    if (list_base) {
      if (fields != 2 || (sizes[type] != 8 && sizes[type] != 16)) {
        return false;
      }
      pointer_size = sizes[type] / 2;
    }
    /* The decoder writes the fifth member's type, even when do_alias is false. */
    if (gravity_fix && types[type] == "ClothSimSettings" &&
        (fields < 5 || types.size() <= 9 || types[9] != "void" || sizes[9] != 0))
    {
      return false;
    }
    for (int field = 0; field < fields; field++) {
      int16_t field_type, name;
      if (!cursor.read(field_type) || !cursor.read(name) || field_type < 0 ||
          size_t(field_type) >= types.size() || name < 0 || size_t(name) >= names.size())
      {
        return false;
      }
      if (list_base && (!shapes[name].is_pointer || shapes[name].array_size != 1)) {
        return false;
      }
    }
  }
  if (pointer_size == 0 || cursor.remaining() != 0) {
    return false;
  }
  /* Only defined structures enter the native hash. Legacy references may retain old type names. */
  std::sort(struct_names.begin(), struct_names.end());
  if (std::adjacent_find(struct_names.begin(), struct_names.end()) != struct_names.end()) {
    return false;
  }

  /* A second bounded pass uses the now-known pointer width. No native offsets are computed here. */
  cursor = structs_start;
  for (uint32_t index = 0; index < structs_num; index++) {
    int16_t type, fields;
    if (!cursor.read(type) || !cursor.read(fields)) {
      return false;
    }
    uint32_t struct_size = 0;
    for (int field = 0; field < fields; field++) {
      int16_t field_type, name;
      if (!cursor.read(field_type) || !cursor.read(name)) {
        return false;
      }
      const bool patched_gravity = gravity_fix && types[type] == "ClothSimSettings" && field == 4;
      if (patched_gravity) {
        field_type = 9;
      }
      const MemberShape &shape = shapes[name];
      const uint32_t element_size = shape.is_pointer ? uint32_t(pointer_size) :
                                                     uint32_t(sizes[field_type]);
      /* Zero-size by-value fields are only used by the legacy gravity correction. */
      if (element_size == 0 && !patched_gravity) {
        return false;
      }
      const uint32_t field_size = element_size * shape.array_size;
      if (field_size > uint32_t(sizes[type]) - struct_size) {
        return false;
      }
      struct_size += field_size;
    }
    if (struct_size != uint32_t(sizes[type]) ||
        (struct_size == 0 && (types[type] != "raw_data" || fields != 0)))
    {
      return false;
    }
  }
  return true;
}

}  // namespace blender::blo
