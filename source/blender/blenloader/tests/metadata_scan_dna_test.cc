/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <string>
#include <vector>

#include "DNA_genfile.h"
#include "testing/testing.h"

#include "../intern/metadata_scan_dna.hh"

namespace blender::blo::tests {
namespace {

struct Field {
  int16_t type;
  int16_t name;
};

struct Structure {
  int16_t type;
  std::vector<Field> fields;
};

struct Schema {
  std::vector<std::string> names = {"*first", "*last", "value"};
  std::vector<std::string> types = {"void", "int", "ListBase", "Payload"};
  std::vector<int16_t> sizes = {0, 4, 16, 4};
  std::vector<Structure> structures = {{2, {{0, 0}, {0, 1}}}, {3, {{1, 2}}}};
};

struct EncodedSchema {
  std::vector<std::byte> bytes;
  std::vector<size_t> names;
  std::vector<size_t> structures;
  size_t name_count;
  size_t type_count;
  size_t structure_count;
};

template<typename T> void append(std::vector<std::byte> &bytes, const T value)
{
  const size_t offset = bytes.size();
  bytes.resize(offset + sizeof(T));
  std::memcpy(bytes.data() + offset, &value, sizeof(T));
}

void marker(std::vector<std::byte> &bytes, const char *value)
{
  for (int index = 0; index < 4; index++) {
    append(bytes, value[index]);
  }
}

void string(std::vector<std::byte> &bytes, const std::string &value)
{
  for (const char character : value) {
    append(bytes, character);
  }
  append(bytes, '\0');
}

void align_four(std::vector<std::byte> &bytes)
{
  bytes.resize((bytes.size() + 3) & ~size_t(3));
}

EncodedSchema encode(const Schema &schema)
{
  EncodedSchema result;
  auto &bytes = result.bytes;
  marker(bytes, "SDNA");
  marker(bytes, "NAME");
  result.name_count = bytes.size();
  append(bytes, int32_t(schema.names.size()));
  for (const std::string &name : schema.names) {
    result.names.push_back(bytes.size());
    string(bytes, name);
  }
  align_four(bytes);
  marker(bytes, "TYPE");
  result.type_count = bytes.size();
  append(bytes, int32_t(schema.types.size()));
  for (const std::string &type : schema.types) {
    string(bytes, type);
  }
  align_four(bytes);
  marker(bytes, "TLEN");
  for (const int16_t size : schema.sizes) {
    append(bytes, size);
  }
  align_four(bytes);
  marker(bytes, "STRC");
  result.structure_count = bytes.size();
  append(bytes, int32_t(schema.structures.size()));
  for (const Structure &structure : schema.structures) {
    result.structures.push_back(bytes.size());
    append(bytes, structure.type);
    append(bytes, int16_t(structure.fields.size()));
    for (const Field field : structure.fields) {
      append(bytes, field.type);
      append(bytes, field.name);
    }
  }
  return result;
}

template<typename T> void overwrite(EncodedSchema &schema, const size_t offset, const T value)
{
  ASSERT_LE(offset + sizeof(T), schema.bytes.size());
  std::memcpy(schema.bytes.data() + offset, &value, sizeof(T));
}

Span<std::byte> view(const EncodedSchema &schema)
{
  return {schema.bytes.data(), int64_t(schema.bytes.size())};
}

void expect_invalid(const EncodedSchema &schema, const uint32_t budget = 100000)
{
  bool limit = true;
  EXPECT_FALSE(metadata_scan_validate_dna(view(schema), budget, &limit));
  EXPECT_FALSE(limit);
}

Schema gravity_schema()
{
  Schema schema;
  schema.names = {"*first", "*last", "value", "gravity", "Cvi", "[3]"};
  schema.types = {"char", "uchar", "short", "ushort", "int", "long", "ulong", "float",
                  "double", "void", "ListBase", "ClothSimSettings"};
  schema.sizes = {1, 1, 2, 2, 4, 4, 4, 4, 8, 0, 16, 28};
  schema.structures = {{10, {{9, 0}, {9, 1}}},
                       {11, {{4, 2}, {4, 2}, {4, 2}, {7, 4}, {7, 3}, {7, 5}}}};
  return schema;
}

}  // namespace

TEST(MetadataScanDNA, CurrentBuildSchemaAndTruncationBoundaries)
{
  const Span<std::byte> data(reinterpret_cast<const std::byte *>(DNAstr), DNAlen);
  bool limit = true;
  EXPECT_TRUE(metadata_scan_validate_dna(data, 100000, &limit));
  EXPECT_FALSE(limit);
  /* Full-schema parsing at every byte would make this test quadratic in Blender's DNA size. */
  for (int64_t size = 1; size < data.size(); size *= 2) {
    EXPECT_FALSE(metadata_scan_validate_dna(data.take_front(size), 100000));
  }
  for (int64_t size = std::max<int64_t>(0, data.size() - 8); size < data.size(); size++) {
    EXPECT_FALSE(metadata_scan_validate_dna(data.take_front(size), 100000));
  }
}

TEST(MetadataScanDNA, EveryTruncationOfValidSchemaIsRejected)
{
  const EncodedSchema schema = encode(Schema{});
  ASSERT_TRUE(metadata_scan_validate_dna(view(schema), 4));
  for (int64_t size = 0; size < int64_t(schema.bytes.size()); size++) {
    SCOPED_TRACE(size);
    bool limit = true;
    EXPECT_FALSE(metadata_scan_validate_dna(view(schema).take_front(size), 100000, &limit));
    EXPECT_FALSE(limit);
  }
}

TEST(MetadataScanDNA, PositiveBudgetLimitsAreDistinctFromMalformedCounts)
{
  const EncodedSchema valid = encode(Schema{});
  for (const uint32_t budget : {0, 1, 2, 3}) {
    bool limit = false;
    EXPECT_FALSE(metadata_scan_validate_dna(view(valid), budget, &limit));
    EXPECT_TRUE(limit);
  }
  for (const size_t offset : {valid.name_count, valid.type_count, valid.structure_count}) {
    for (const int32_t count : {0, -1, 32769, std::numeric_limits<int32_t>::max()}) {
      SCOPED_TRACE(count);
      EncodedSchema invalid = valid;
      overwrite(invalid, offset, count);
      expect_invalid(invalid);
    }
  }
  /* Even a tiny budget must not classify a native-representation overflow as a policy limit. */
  EncodedSchema invalid = valid;
  overwrite(invalid, valid.name_count, int32_t(32769));
  expect_invalid(invalid, 0);
}

TEST(MetadataScanDNA, AggregateFieldBudgetIsEnforced)
{
  Schema schema;
  schema.names = {"*first", "*last", "a", "b"};
  schema.types.push_back("OtherPayload");
  schema.sizes = {0, 4, 16, 8, 8};
  schema.structures[1].fields = {{1, 2}, {1, 3}};
  schema.structures.push_back({4, {{1, 2}, {1, 3}}});
  const EncodedSchema encoded = encode(schema);
  bool limit = false;
  EXPECT_FALSE(metadata_scan_validate_dna(view(encoded), 5, &limit));
  EXPECT_TRUE(limit);
  EXPECT_TRUE(metadata_scan_validate_dna(view(encoded), 6, &limit));
  EXPECT_FALSE(limit);
}

TEST(MetadataScanDNA, SignedIndicesAndFieldCountsAreChecked)
{
  const EncodedSchema valid = encode(Schema{});
  for (const size_t offset : {valid.structures[1], valid.structures[1] + 4,
                             valid.structures[1] + 6})
  {
    for (const int16_t value : {int16_t(-1), std::numeric_limits<int16_t>::max()}) {
      EncodedSchema invalid = valid;
      overwrite(invalid, offset, value);
      expect_invalid(invalid);
    }
  }
  for (const int16_t count : {int16_t(-1), std::numeric_limits<int16_t>::max()}) {
    EncodedSchema invalid = valid;
    overwrite(invalid, valid.structures[1] + 2, count);
    expect_invalid(invalid);
  }
}

TEST(MetadataScanDNA, InvalidMarkersUnterminatedNamesAndTrailingDataAreRejected)
{
  EncodedSchema invalid = encode(Schema{});
  invalid.bytes[0] = std::byte{0};
  expect_invalid(invalid);
  invalid = encode(Schema{});
  std::fill(invalid.bytes.begin() + invalid.names[0], invalid.bytes.end(), std::byte{'x'});
  expect_invalid(invalid);
  invalid = encode(Schema{});
  invalid.bytes.push_back(std::byte{0});
  expect_invalid(invalid);
}

TEST(MetadataScanDNA, ArrayArithmeticIncludesDigitsOutsideBrackets)
{
  for (const char *name : {"value2147483648", "v2147483647x9", "value[32768]",
                           "value[256][256]", "value[0]", "value[]", "value[-1]", "value[2",
                           "value2]", "value[2]]", "value[2x3]"})
  {
    SCOPED_TRACE(name);
    Schema schema;
    schema.names[2] = name;
    expect_invalid(encode(schema));
  }
  Schema valid;
  valid.names[2] = "value2147483647";
  EXPECT_TRUE(metadata_scan_validate_dna(view(encode(valid)), 100000));
  valid.names[2] = "value[2][3]";
  valid.sizes[3] = 24;
  EXPECT_TRUE(metadata_scan_validate_dna(view(encode(valid)), 100000));
}

TEST(MetadataScanDNA, ListBaseRequiresExactTwoPointerShape)
{
  for (const int16_t size : {int16_t(8), int16_t(16)}) {
    Schema schema;
    schema.sizes[2] = size;
    EXPECT_TRUE(metadata_scan_validate_dna(view(encode(schema)), 100000));
  }
  for (const int16_t size : {int16_t(0), int16_t(9), int16_t(17)}) {
    Schema schema;
    schema.sizes[2] = size;
    expect_invalid(encode(schema));
  }
  Schema schema;
  schema.names[0] = "first";
  expect_invalid(encode(schema));
  schema.names[0] = "*first[2]";
  expect_invalid(encode(schema));
  schema = Schema{};
  schema.structures[0].fields.pop_back();
  expect_invalid(encode(schema));
  schema = Schema{};
  schema.types[2] = "NotListBase";
  expect_invalid(encode(schema));
}

TEST(MetadataScanDNA, PrimitiveWidthsAndStructSumsMustMatch)
{
  for (const int16_t size : {int16_t(-1), int16_t(0), int16_t(2), int16_t(8)}) {
    Schema schema;
    schema.sizes[1] = size;
    expect_invalid(encode(schema));
  }
  for (const int16_t size : {int16_t(0), int16_t(3), int16_t(5)}) {
    Schema schema;
    schema.sizes[3] = size;
    expect_invalid(encode(schema));
  }
  Schema schema;
  schema.structures[1].fields[0].type = 0;
  expect_invalid(encode(schema));
}

TEST(MetadataScanDNA, OpaquePointersRawDataAndLegacyLongRemainSupported)
{
  Schema schema;
  schema.types.push_back("Opaque");
  schema.sizes.push_back(0);
  schema.names[2] = "(*callback)()";
  schema.sizes[3] = 8;
  schema.structures[1].fields[0].type = 4;
  EXPECT_TRUE(metadata_scan_validate_dna(view(encode(schema)), 100000));
  schema.types.push_back("raw_data");
  schema.sizes.push_back(0);
  schema.structures.push_back({5, {}});
  EXPECT_TRUE(metadata_scan_validate_dna(view(encode(schema)), 100000));
  schema.types.push_back("long");
  schema.sizes.push_back(8);
  schema.names[2] = "value";
  schema.structures[1].fields[0].type = 6;
  EXPECT_TRUE(metadata_scan_validate_dna(view(encode(schema)), 100000));
}

TEST(MetadataScanDNA, DuplicateDefinitionsAndCanonicalAliasesAreUnambiguous)
{
  Schema schema;
  schema.structures.push_back(schema.structures[1]);
  expect_invalid(encode(schema));
  schema = Schema{};
  schema.types[3] = "Screen";
  schema.types.push_back("bScreen");
  schema.sizes.push_back(4);
  /* Old-name references without another definition do not enter the native structure hash. */
  EXPECT_TRUE(metadata_scan_validate_dna(view(encode(schema)), 100000));
  schema.structures.push_back({4, {{1, 2}}});
  expect_invalid(encode(schema));
}

TEST(MetadataScanDNA, GravityCorrectionRequiresTheLegacyVoidSlotAndFifthMember)
{
  Schema schema = gravity_schema();
  const EncodedSchema valid = encode(schema);
  const std::vector<std::byte> original = valid.bytes;
  EXPECT_TRUE(metadata_scan_validate_dna(view(valid), 100000));
  EXPECT_EQ(valid.bytes, original);
  schema.structures[1].fields.resize(4);
  schema.sizes[11] = 16;
  expect_invalid(encode(schema));
  schema = gravity_schema();
  schema.types[9] = "OtherType";
  expect_invalid(encode(schema));
  schema = gravity_schema();
  schema.sizes[9] = 4;
  expect_invalid(encode(schema));
}

}  // namespace blender::blo::tests
