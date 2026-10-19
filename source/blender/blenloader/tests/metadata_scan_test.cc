/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include <algorithm>
#include <array>
#include <cstdio>
#include <cstring>
#include <memory>
#include <string>
#include <vector>

#include <zlib.h>
#include <zstd.h>

#ifdef WIN32
#  include <io.h>
#else
#  include <unistd.h>
#endif

#include "DNA_ID.h"
#include "DNA_genfile.h"
#include "DNA_sdna_types.h"

#include "BKE_idtype.hh"

#include "BLO_core_bhead.hh"
#include "BLO_metadata_scan.hh"

#include "../intern/metadata_scan_reader.hh"

#include "testing/testing.h"

namespace blender::tests {
namespace {

using Bytes = std::vector<char>;

static void append(Bytes &bytes, const void *data, size_t size)
{
  const auto *first = static_cast<const char *>(data);
  bytes.insert(bytes.end(), first, first + size);
}

static void block(Bytes &bytes, int code, int sdna, const void *data, size_t size)
{
  const LargeBHead8 header{code, sdna, 0, int64_t(size), 1};
  append(bytes, &header, sizeof(header));
  if (size) {
    append(bytes, data, size);
  }
}

static BLOMetadataScanLimits test_limits()
{
  /* Fixture policy only; production limits require representative-file measurements. */
  return {64 * 1024 * 1024,
          64 * 1024 * 1024,
          100000,
          16 * 1024 * 1024,
          2 * 1024 * 1024,
          32768,
          32 * 1024 * 1024,
          16 * 1024 * 1024,
          4096};
}

static BLOMetadataScanRequest test_request(std::string list = {}, uint64_t offset = 0)
{
  return {std::move(list), offset, 2, 2, 2};
}

class TemporaryDescriptor {
  FILE *file_;

 public:
  explicit TemporaryDescriptor(const Bytes &bytes) : file_(std::tmpfile())
  {
    if (file_) {
      EXPECT_EQ(std::fwrite(bytes.data(), 1, bytes.size(), file_), bytes.size());
      EXPECT_EQ(std::fflush(file_), 0);
      EXPECT_EQ(std::fseek(file_, 3, SEEK_SET), 0);
    }
  }
  ~TemporaryDescriptor()
  {
    if (file_) {
      std::fclose(file_);
    }
  }
  int get() const
  {
#ifdef WIN32
    return file_ ? _fileno(file_) : -1;
#else
    return file_ ? fileno(file_) : -1;
#endif
  }
  long position() const
  {
    return file_ ? std::ftell(file_) : -1;
  }
};

static BLOMetadataScanResult scan(const Bytes &bytes,
                                  const BLOMetadataScanRequest &request = test_request(),
                                  const BLOMetadataScanLimits &limits = test_limits())
{
  TemporaryDescriptor file(bytes);
  EXPECT_GE(file.get(), 0);
  return BLO_metadata_scan_from_descriptor(file.get(), request, limits);
}

static const BLOMetadataScanType *objects(const BLOMetadataScanResult &result)
{
  const auto found = std::find_if(result.types.begin(), result.types.end(), [](const auto &row) {
    return row.list_name == "objects";
  });
  return found == result.types.end() ? nullptr : &*found;
}

static Bytes gzip(const Bytes &bytes)
{
  z_stream stream{};
  EXPECT_EQ(deflateInit2(&stream, 6, Z_DEFLATED, 16 + MAX_WBITS, 8, Z_DEFAULT_STRATEGY), Z_OK);
  Bytes encoded(compressBound(bytes.size()) + 64);
  stream.next_in = reinterpret_cast<Bytef *>(const_cast<char *>(bytes.data()));
  stream.avail_in = uInt(bytes.size());
  stream.next_out = reinterpret_cast<Bytef *>(encoded.data());
  stream.avail_out = uInt(encoded.size());
  EXPECT_EQ(deflate(&stream, Z_FINISH), Z_STREAM_END);
  encoded.resize(stream.total_out);
  EXPECT_EQ(deflateEnd(&stream), Z_OK);
  return encoded;
}

static Bytes zstd(const Bytes &bytes)
{
  Bytes encoded(ZSTD_compressBound(bytes.size()));
  const size_t size = ZSTD_compress(encoded.data(), encoded.size(), bytes.data(), bytes.size(), 3);
  EXPECT_FALSE(ZSTD_isError(size));
  if (ZSTD_isError(size)) {
    return {};
  }
  encoded.resize(size);
  return encoded;
}

static void u32(Bytes &bytes, uint32_t value)
{
  for (int i = 0; i < 4; i++) {
    bytes.push_back(char((value >> (8 * i)) & 255));
  }
}

/** Real seekable-format table, including frame byte counts and the seekable footer. */
static Bytes seekable_zstd(const Bytes &bytes)
{
  const size_t split = bytes.size() / 2;
  const Bytes first = zstd(Bytes(bytes.begin(), bytes.begin() + split));
  const Bytes second = zstd(Bytes(bytes.begin() + split, bytes.end()));
  Bytes encoded = first;
  append(encoded, second.data(), second.size());
  u32(encoded, 0x184d2a5e);
  u32(encoded, 25); /* Two 8-byte entries and the 9-byte footer. */
  u32(encoded, uint32_t(first.size()));
  u32(encoded, uint32_t(split));
  u32(encoded, uint32_t(second.size()));
  u32(encoded, uint32_t(bytes.size() - split));
  u32(encoded, 2);
  encoded.push_back(0);
  u32(encoded, 0x8f92eab1);
  return encoded;
}

class MetadataScan : public testing::Test {
 protected:
  SDNA *dna_ = nullptr;

  void SetUp() override
  {
    BKE_idtype_init();
    dna_ = DNA_sdna_from_data(DNAstr, DNAlen, true, false, nullptr);
    ASSERT_NE(dna_, nullptr);
  }
  void TearDown() override
  {
    DNA_sdna_free(dna_);
  }

  Bytes fixture(bool geometry = false, const Bytes *custom_dna = nullptr)
  {
    const char header[] = "BLENDER17-01v0520";
    Bytes bytes(header, header + sizeof(header) - 1);
    const char subversion[] = "  42";
    block(bytes, BLO_CODE_GLOB, 0, subversion, 4);
    const int id_struct = DNA_struct_find_index_without_alias(dna_, "ID");
    for (int i = 0; i < 6; i++) {
      ID id{};
      std::snprintf(id.name, sizeof(id.name), "OBItem%d", i);
      if (i == 2) {
        id.flag |= ID_FLAG_LINKED_AND_PACKED;
      }
      if (i == 3) {
        std::memset(id.name, 'x', sizeof(id.name));
      }
      block(bytes, ID_OB, id_struct, &id, sizeof(id));
    }
    Library library{};
    std::strcpy(library.id.name, "LILibrary");
    std::strcpy(library.filepath, "//dependency.blend");
    block(bytes, ID_LI, DNA_struct_find_index_without_alias(dna_, "Library"), &library,
          sizeof(library));
    if (geometry) {
      const Bytes discarded(2 * 1024 * 1024, 0);
      block(bytes, BLO_CODE_DATA, 0, discarded.data(), discarded.size());
    }
    block(bytes, BLO_CODE_DNA1, 0, custom_dna ? custom_dna->data() :
                                               reinterpret_cast<const char *>(DNAstr),
          custom_dna ? custom_dna->size() : size_t(DNAlen));
    block(bytes, BLO_CODE_ENDB, 0, nullptr, 0);
    return bytes;
  }
};

TEST_F(MetadataScan, SummaryMatchesNativeFilteringAndReverseFileOrder)
{
  const auto result = scan(fixture());
  ASSERT_EQ(result.status, BLOMetadataScanStatus::OK) << result.message;
  ASSERT_NE(objects(result), nullptr);
  EXPECT_EQ(objects(result)->total, 4);
  EXPECT_EQ(objects(result)->names, (std::vector<std::string>{"Item5", "Item4"}));
  EXPECT_EQ(result.library_paths, (std::vector<std::string>{"//dependency.blend"}));
  EXPECT_EQ(result.file_version, 520);
  EXPECT_EQ(result.subversion, 42);
  EXPECT_EQ(result.passes, 2);
}

TEST_F(MetadataScan, DeepPagesPreserveFilteredOrderWithoutRetainingSkippedNames)
{
  const auto first = scan(fixture(), test_request("objects"));
  const auto later = scan(fixture(), test_request("objects", 1));
  const auto empty = scan(fixture(), test_request("objects", UINT64_MAX));
  ASSERT_EQ(first.status, BLOMetadataScanStatus::OK);
  ASSERT_EQ(later.status, BLOMetadataScanStatus::OK);
  ASSERT_EQ(empty.status, BLOMetadataScanStatus::OK);
  ASSERT_NE(objects(first), nullptr);
  ASSERT_NE(objects(later), nullptr);
  ASSERT_NE(objects(empty), nullptr);
  EXPECT_EQ(first.passes, 2);
  EXPECT_EQ(later.passes, 3);
  EXPECT_EQ(objects(later)->names, (std::vector<std::string>{"Item4", "Item1"}));
  EXPECT_TRUE(objects(empty)->names.empty());
  EXPECT_GT(later.decoded_bytes, first.decoded_bytes);
}

TEST_F(MetadataScan, PlainGzipAndBothZstandardFormsHaveIdenticalMetadata)
{
  const Bytes plain = fixture(true);
  for (const Bytes &encoded : {plain, gzip(plain), zstd(plain), seekable_zstd(plain)}) {
    const auto result = scan(encoded, test_request("objects", 2));
    ASSERT_EQ(result.status, BLOMetadataScanStatus::OK) << result.message;
    ASSERT_NE(objects(result), nullptr);
    EXPECT_EQ(objects(result)->total, 4);
    EXPECT_EQ(objects(result)->names, (std::vector<std::string>{"Item1", "Item0"}));
    EXPECT_EQ(result.passes, 3);
  }
}

TEST_F(MetadataScan, PlainReadWindowsServeBlocksAcrossTheirBorders)
{
  /* Plain files are read in 64 KiB windows. Thousands of small blocks, as a rigged scene writes
   * one per weighted vertex, with ID blocks placed so that headers, names and the window borders
   * cross: every codec must report the same names. */
  constexpr int64_t window = 64 * 1024;
  const char header[] = "BLENDER17-01v0520";
  Bytes bytes(header, header + sizeof(header) - 1);
  block(bytes, BLO_CODE_GLOB, 0, "  42", 4);
  const int id_struct = DNA_struct_find_index_without_alias(dna_, "ID");
  /* A DATA block ending exactly at `end`, which must leave room for its own header. */
  const auto data_until = [&](const int64_t end) {
    const int64_t payload = end - int64_t(bytes.size()) - int64_t(sizeof(LargeBHead8));
    ASSERT_GE(payload, 0);
    const Bytes zeros(size_t(payload), 0);
    block(bytes, BLO_CODE_DATA, 0, zeros.data(), zeros.size());
  };
  std::vector<std::string> expected;
  const auto object = [&](const int number) {
    ID id{};
    std::snprintf(id.name, sizeof(id.name), "OBWeighted%05d", number);
    block(bytes, ID_OB, id_struct, &id, sizeof(id));
    expected.insert(expected.begin(), id.name + 2);
  };
  for (int border = 1; border <= 6; border++) {
    for (int i = 0; i < 2000; i++) {
      const int weights[3] = {i, border, i + border};
      block(bytes, BLO_CODE_DATA, 0, weights, sizeof(int) * (1 + i % 3));
    }
    /* The next border with room for a DATA block before the ID block. */
    const int64_t bhead = int64_t(sizeof(LargeBHead8));
    const int64_t next = ((int64_t(bytes.size()) + 3 * bhead) / window + 1) * window;
    /* The ID block's header crosses the border, ends 4 bytes before it (its name crosses), or
     * ends exactly at it. */
    const int64_t starts[3] = {next - bhead / 2, next - bhead - 4, next - bhead};
    data_until(starts[border % 3]);
    object(border);
  }
  block(bytes, BLO_CODE_DNA1, 0, DNAstr, size_t(DNAlen));
  block(bytes, BLO_CODE_ENDB, 0, nullptr, 0);
  BLOMetadataScanRequest request = test_request("objects", 0);
  request.limit = 64;
  for (const Bytes &encoded : {bytes, gzip(bytes), zstd(bytes)}) {
    const auto result = scan(encoded, request);
    ASSERT_EQ(result.status, BLOMetadataScanStatus::OK) << result.message;
    ASSERT_NE(objects(result), nullptr);
    EXPECT_EQ(objects(result)->total, expected.size());
    EXPECT_EQ(objects(result)->names, expected);
    EXPECT_EQ(result.passes, 2);
  }
}

TEST_F(MetadataScan, TruncatedCompressedFooterAndSkippableTableAreRejected)
{
  for (Bytes encoded : {gzip(fixture()), zstd(fixture()), seekable_zstd(fixture())}) {
    encoded.pop_back();
    const auto result = scan(encoded);
    EXPECT_EQ(result.status, BLOMetadataScanStatus::INVALID);
    EXPECT_TRUE(result.types.empty());
    EXPECT_TRUE(result.library_paths.empty());
    EXPECT_EQ(result.file_version, 0);
    EXPECT_EQ(result.subversion, 0);
  }
}

TEST_F(MetadataScan, WorkBlockDnaResultAndDecoderLimitsFailClosed)
{
  for (int setting = 0; setting < 8; setting++) {
    auto limits = test_limits();
    switch (setting) {
      case 0: limits.decoded_bytes = 32; break;
      case 1: limits.input_bytes = 8; break;
      case 2: limits.block_count = 1; break;
      case 3: limits.block_bytes = 32; break;
      case 4: limits.dna_bytes = 32; break;
      case 5: limits.dna_entries = 1; break;
      case 6: limits.result_names = 1; break;
      case 7: limits.decoder_bytes = 1; break;
    }
    const auto result = scan(setting == 7 ? zstd(fixture()) : fixture(), test_request(), limits);
    EXPECT_EQ(result.status, BLOMetadataScanStatus::LIMIT_EXCEEDED) << setting;
    EXPECT_TRUE(result.types.empty());
  }
}

TEST_F(MetadataScan, FooterValidationAcceptsExactDecodedBudget)
{
  for (const Bytes &encoded : {gzip(fixture()), zstd(fixture()), seekable_zstd(fixture())}) {
    const auto measured = scan(encoded);
    ASSERT_EQ(measured.status, BLOMetadataScanStatus::OK) << measured.message;
    auto limits = test_limits();
    limits.decoded_bytes = measured.decoded_bytes;
    const auto exact = scan(encoded, test_request(), limits);
    EXPECT_EQ(exact.status, BLOMetadataScanStatus::OK) << exact.message;
    EXPECT_EQ(exact.decoded_bytes, measured.decoded_bytes);
  }
}

TEST_F(MetadataScan, FooterOverflowProbeReportsOnlyOneExtraByteAndNoMetadata)
{
  Bytes encoded = zstd(fixture());
  const auto measured = scan(encoded);
  ASSERT_EQ(measured.status, BLOMetadataScanStatus::OK);
  const Bytes extra_frame = zstd(Bytes{'x', 'y'});
  append(encoded, extra_frame.data(), extra_frame.size());
  auto limits = test_limits();
  limits.decoded_bytes = measured.decoded_bytes;
  const auto result = scan(encoded, test_request(), limits);
  EXPECT_EQ(result.status, BLOMetadataScanStatus::LIMIT_EXCEEDED);
  EXPECT_EQ(result.decoded_bytes, limits.decoded_bytes + 1);
  EXPECT_EQ(result.file_version, 0);
  EXPECT_TRUE(result.types.empty());
  EXPECT_TRUE(result.library_paths.empty());
}

TEST_F(MetadataScan, MissingIdSchemaIsInvalidWithoutNativeOffsetLookup)
{
  const int id_index = DNA_struct_find_index_without_alias(dna_, "ID");
  const char *name = dna_->types[dna_->structs[id_index]->type_index];
  Bytes changed(reinterpret_cast<const char *>(DNAstr),
                reinterpret_cast<const char *>(DNAstr) + DNAlen);
  changed[size_t(name - dna_->data)] = 'Z';
  EXPECT_EQ(scan(fixture(false, &changed)).status, BLOMetadataScanStatus::INVALID);
}

TEST_F(MetadataScan, OversizedBlockAndZstandardWindowDeclarationsAreBounded)
{
  Bytes huge_block = fixture();
  const int64_t huge_length = INT64_MAX;
  std::memcpy(huge_block.data() + 17 + offsetof(LargeBHead8, len), &huge_length,
              sizeof(huge_length));
  EXPECT_EQ(scan(huge_block).status, BLOMetadataScanStatus::LIMIT_EXCEEDED);
  auto limits = test_limits();
  limits.zstd_window_bytes = 1024;
  EXPECT_EQ(scan(zstd(fixture(true)), test_request(), limits).status,
            BLOMetadataScanStatus::LIMIT_EXCEEDED);
}

TEST_F(MetadataScan, HostileSeekTableDeclarationDoesNotAllocateItsClaimedLength)
{
  Bytes encoded = zstd(fixture());
  u32(encoded, 0x184d2a5e);
  u32(encoded, UINT32_MAX);
  const auto result = scan(encoded);
  EXPECT_EQ(result.status, BLOMetadataScanStatus::INVALID);
  EXPECT_TRUE(result.types.empty());
  EXPECT_LT(result.input_bytes, uint64_t(encoded.size()) * 3);
}

TEST_F(MetadataScan, BorrowedDescriptorStaysOpenAndCursorIsRestored)
{
  TemporaryDescriptor file(gzip(fixture(true)));
  ASSERT_GE(file.get(), 0);
  const auto result = BLO_metadata_scan_from_descriptor(file.get(), test_request(), test_limits());
  ASSERT_EQ(result.status, BLOMetadataScanStatus::OK) << result.message;
  EXPECT_EQ(file.position(), 3);
  const auto again = BLO_metadata_scan_from_descriptor(file.get(), test_request(), test_limits());
  EXPECT_EQ(again.status, BLOMetadataScanStatus::OK);
}

TEST_F(MetadataScan, CancellationClearsPartialResultsAndKeepsDescriptorOwnedByCaller)
{
  TemporaryDescriptor file(zstd(fixture(true)));
  int checks = 0;
  const auto cancelled = [](void *opaque) { return ++*static_cast<int *>(opaque) > 12; };
  const auto result = BLO_metadata_scan_from_descriptor(file.get(), test_request(), test_limits(),
                                                       cancelled, &checks);
  EXPECT_EQ(result.status, BLOMetadataScanStatus::CANCELLED);
  EXPECT_TRUE(result.types.empty());
  EXPECT_TRUE(result.library_paths.empty());
  EXPECT_EQ(file.position(), 3);
  EXPECT_EQ(BLO_metadata_scan_from_descriptor(file.get(), test_request(), test_limits()).status,
            BLOMetadataScanStatus::OK);
}

TEST_F(MetadataScan, CancellationAtExhaustedBudgetPreventsFooterProbe)
{
  Bytes encoded = zstd(Bytes{'x'});
  const Bytes trailing_frame = zstd(Bytes{});
  append(encoded, trailing_frame.data(), trailing_frame.size());
  TemporaryDescriptor file(encoded);
  auto limits = test_limits();
  limits.decoded_bytes = 1;
  BLOMetadataScanResult result;
  result.status = BLOMetadataScanStatus::OK;
  bool stop = false;
  const auto cancelled = [](void *opaque) { return *static_cast<bool *>(opaque); };
  blo::MetadataScanBudget budget{limits, result, cancelled, &stop};
  {
    blo::MetadataScanReader reader(file.get(), encoded.size(), budget);
    ASSERT_TRUE(reader.initialize());
    char decoded;
    ASSERT_TRUE(reader.read_exact(&decoded, 1));
    EXPECT_EQ(decoded, 'x');
    stop = true;
    EXPECT_FALSE(reader.finish());
    EXPECT_EQ(result.status, BLOMetadataScanStatus::CANCELLED);
    EXPECT_EQ(result.decoded_bytes, 1);
  }
  EXPECT_EQ(file.position(), 3);
}

}  // namespace
}  // namespace blender::tests
