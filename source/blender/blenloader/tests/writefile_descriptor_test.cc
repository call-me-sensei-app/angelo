/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <filesystem>
#include <limits>
#include <memory>
#include <numeric>
#include <string>
#include <vector>

#include <fcntl.h>
#ifdef WIN32
#  include <io.h>
#else
#  include <unistd.h>
#endif

#include "DNA_scene_types.h"
#include "DNA_userdef_types.h"

#include "BLI_fileops.h"
#include "BLI_filereader.h"
#include "BLI_listbase.h"
#include "BLI_path_utils.hh"
#include "BLI_string.h"
#include "BLI_tempfile.h"

#include "BKE_global.hh"
#include "BKE_idprop.hh"
#include "BKE_lib_id.hh"
#include "BKE_main.hh"
#include "BKE_report.hh"
#include "BKE_scene.hh"

#include "BLO_core_bhead.hh"
#include "BLO_core_blend_header.hh"
#include "BLO_readfile.hh"
#include "BLO_writefile.hh"

#include "blendfile_loading_base_test.h"

namespace blender {
namespace {

#ifdef WIN32
constexpr int binary_flag = O_BINARY;
#else
constexpr int binary_flag = 0;
#endif

static int64_t descriptor_write(const int descriptor, const void *buffer, const size_t size)
{
#ifdef WIN32
  return _write(descriptor, buffer, unsigned(size));
#else
  return ::write(descriptor, buffer, size);
#endif
}

static int descriptor_close(const int descriptor)
{
#ifdef WIN32
  return _close(descriptor);
#else
  return ::close(descriptor);
#endif
}

enum class WriteFault { None, Short, Zero, Negative };

/** A call-local observer that always puts accepted bytes into the actual borrowed descriptor. */
struct WriteProbe {
  int descriptor = -1;
  WriteFault fault = WriteFault::None;
  size_t fault_at = std::numeric_limits<size_t>::max();
  bool failed = false;
  int calls_after_failure = 0;
  int after_stream_calls = 0;
  std::vector<size_t> requests;
  std::string accepted;

  static int64_t write(int descriptor, const void *buffer, size_t size, void *user_data)
  {
    WriteProbe &probe = *static_cast<WriteProbe *>(user_data);
    EXPECT_EQ(descriptor, probe.descriptor);
    if (probe.failed) {
      probe.calls_after_failure++;
      errno = EIO;
      return -1;
    }
    const size_t call = probe.requests.size();
    probe.requests.push_back(size);
    size_t accepted_size = size;
    if (call == probe.fault_at) {
      probe.failed = true;
      switch (probe.fault) {
        case WriteFault::Short:
          EXPECT_GT(size, 1u);
          accepted_size = std::min(size_t(7), size - 1);
          break;
        case WriteFault::Zero:
          errno = EDOM;
          return 0;
        case WriteFault::Negative:
          errno = ENOSPC;
          return -1;
        case WriteFault::None:
          ADD_FAILURE() << "A fault index requires an injected fault";
          return -1;
      }
    }
    const int64_t written = descriptor_write(descriptor, buffer, accepted_size);
    EXPECT_EQ(written, int64_t(accepted_size));
    if (written > 0) {
      probe.accepted.append(static_cast<const char *>(buffer), size_t(written));
    }
    if (probe.failed) {
      /* A positive short write must not be diagnosed using this unrelated errno. */
      errno = EDOM;
    }
    return written;
  }

  static void after_stream(void *user_data)
  {
    WriteProbe &probe = *static_cast<WriteProbe *>(user_data);
    probe.after_stream_calls++;
    errno = ERANGE;
  }

  BlendFileDescriptorTestHooks hooks()
  {
    return {write, this, after_stream};
  }
};

class WritefileDescriptorTest : public BlendfileLoadingBaseTest {
 protected:
  Main *main_ = nullptr;
  Scene *scene_ = nullptr;
  IDProperty *marker_ = nullptr;
  ReportList reports_{};
  std::string directory_;
  std::vector<int> descriptors_;
  std::vector<BlendFileData *> loaded_;
  int saved_versions_ = 0;
  int saved_fileflags_ = 0;
  std::string saved_last_blend_;
  static constexpr int payload_count = 3 * 1024 * 1024 / sizeof(int) + 17;

  void SetUp() override
  {
    saved_versions_ = U.versions;
    saved_fileflags_ = G.fileflags;
    saved_last_blend_ = G.filepath_last_blend;
    BKE_reports_init(&reports_, RPT_STORE | RPT_PRINT_HANDLED_BY_OWNER);

    char temporary[FILE_MAX];
    BLI_temp_directory_path_get(temporary, sizeof(temporary));
    const auto nonce = std::chrono::steady_clock::now().time_since_epoch().count();
    for (int attempt = 0; attempt < 100 && directory_.empty(); attempt++) {
      char candidate[FILE_MAX];
      const std::string name = "blender_descriptor_test_" + std::to_string(nonce) + "_" +
                               std::to_string(attempt);
      BLI_path_join(candidate, sizeof(candidate), temporary, name.c_str());
      std::error_code error;
      if (std::filesystem::create_directory(
              std::filesystem::path(std::u8string(candidate, candidate + strlen(candidate))),
              error))
      {
        directory_ = candidate;
      }
    }
    ASSERT_FALSE(directory_.empty());
    main_ = BKE_main_new();
    ASSERT_NE(main_, nullptr);
    ASSERT_NE(main_->lock, nullptr);
    const std::string original = path("artist-original.blend");
    STRNCPY(main_->filepath, original.c_str());
    scene_ = BKE_scene_add(main_, "DescriptorFixture");
    ASSERT_NE(scene_, nullptr);
    scene_->r.cfra = 37;
    scene_->r.xsch = 321;
    scene_->r.ysch = 123;
    IDProperty *properties = IDP_EnsureProperties(&scene_->id);
    marker_ = IDP_NewInt(13579, "descriptor_marker");
    ASSERT_TRUE(IDP_AddToGroup(properties, marker_));
    ASSERT_TRUE(IDP_AddToGroup(properties,
                               IDP_NewString("retained descriptor payload", "descriptor_text")));
  }

  void TearDown() override
  {
    for (BlendFileData *loaded : loaded_) {
      BLO_blendfiledata_free(loaded);
    }
    if (main_) {
      BKE_main_free(main_);
    }
    BKE_reports_free(&reports_);
    for (const int descriptor : descriptors_) {
      EXPECT_EQ(descriptor_close(descriptor), 0);
    }
    if (!directory_.empty()) {
      /* Only remove the directory this fixture actually created, never a colliding candidate. */
      std::error_code error;
      std::filesystem::remove_all(
          std::filesystem::path(std::u8string(directory_.begin(), directory_.end())), error);
      EXPECT_FALSE(bool(error)) << error.message();
    }
    U.versions = saved_versions_;
    G.fileflags = saved_fileflags_;
    STRNCPY(G.filepath_last_blend, saved_last_blend_.c_str());
    BlendfileLoadingBaseTest::TearDown();
  }

  std::string path(const char *name) const
  {
    char result[FILE_MAX];
    BLI_path_join(result, sizeof(result), directory_.c_str(), name);
    return result;
  }

  static BlendFileWriteParams params()
  {
    BlendFileWriteParams result{};
    result.use_save_as_copy = true;
    return result;
  }

  int open_owned(const std::string &filepath, const int flags = O_RDWR | O_CREAT | O_EXCL)
  {
    const int descriptor = BLI_open(filepath.c_str(), flags | binary_flag, 0600);
    EXPECT_GE(descriptor, 0) << filepath << ": " << strerror(errno);
    if (descriptor >= 0) {
      descriptors_.push_back(descriptor);
    }
    return descriptor;
  }

  void close_owned(const int descriptor)
  {
    const auto found = std::find(descriptors_.begin(), descriptors_.end(), descriptor);
    ASSERT_NE(found, descriptors_.end());
    EXPECT_EQ(descriptor_close(descriptor), 0);
    descriptors_.erase(found);
  }

  void put_file(const std::string &filepath, const std::string &bytes)
  {
    const int descriptor = open_owned(filepath, O_WRONLY | O_CREAT | O_TRUNC);
    ASSERT_GE(descriptor, 0);
    EXPECT_EQ(descriptor_write(descriptor, bytes.data(), bytes.size()), int64_t(bytes.size()));
    close_owned(descriptor);
  }

  std::string descriptor_bytes(const int descriptor)
  {
    BLI_stat_t info{};
    if (BLI_fstat(descriptor, &info) != 0 || info.st_size < 0 || info.st_size > 32 * 1024 * 1024) {
      ADD_FAILURE() << "Expected an open descriptor with bounded fixture output";
      return {};
    }
    const int64_t saved_offset = BLI_lseek(descriptor, 0, SEEK_CUR);
    EXPECT_GE(saved_offset, 0);
    EXPECT_EQ(BLI_lseek(descriptor, 0, SEEK_SET), 0);
    std::string result(size_t(info.st_size), '\0');
    EXPECT_EQ(BLI_read(descriptor, result.data(), result.size()), int64_t(result.size()));
    EXPECT_EQ(BLI_lseek(descriptor, saved_offset, SEEK_SET), saved_offset);
    return result;
  }

  std::string file_bytes(const std::string &filepath)
  {
    const int descriptor = open_owned(filepath, O_RDONLY);
    if (descriptor < 0) {
      return {};
    }
    const std::string result = descriptor_bytes(descriptor);
    close_owned(descriptor);
    return result;
  }

  std::string errors() const
  {
    std::string result;
    for (const Report &report : reports_.list) {
      if (report.type & RPT_ERROR_ALL) {
        result += report.message;
        result += '\n';
      }
    }
    return result;
  }

  bool write_public(const int descriptor, const int flags = 0)
  {
    BKE_reports_clear(&reports_);
    const BlendFileWriteParams options = params();
    return BLO_write_file_to_descriptor(
        main_, path("logical.blend").c_str(), descriptor, flags, &options, &reports_);
  }

  bool write_probe(WriteProbe &probe)
  {
    BKE_reports_clear(&reports_);
    const BlendFileWriteParams options = params();
    return BLO_write_file_to_descriptor_for_test(main_,
                                                 path("logical.blend").c_str(),
                                                 probe.descriptor,
                                                 0,
                                                 &options,
                                                 &reports_,
                                                 probe.hooks());
  }

  static int payload_value(const int index)
  {
    return (index % 4093) - 2046;
  }

  void add_large_payload()
  {
    IDPropertyTemplate value{};
    value.array.type = IDP_INT;
    value.array.len = payload_count;
    IDProperty *payload = IDP_New(IDP_ARRAY, &value, "descriptor_payload");
    ASSERT_NE(payload, nullptr);
    ASSERT_TRUE(IDP_AddToGroup(IDP_EnsureProperties(&scene_->id), payload));
    for (int i = 0; i < payload_count; i++) {
      IDP_array_int_get(payload)[i] = payload_value(i);
    }
  }

  BlendFileData *read_back(const std::string &bytes)
  {
    EXPECT_EQ(bytes.substr(0, 7), "BLENDER");
    BlendFileData *loaded = BLO_read_from_memory(
        bytes.data(), int(bytes.size()), BLO_READ_SKIP_NONE, &reports_);
    EXPECT_NE(loaded, nullptr) << errors();
    if (loaded) {
      loaded_.push_back(loaded);
    }
    return loaded;
  }

  void expect_contents(BlendFileData *loaded, const bool large, const int marker = 13579)
  {
    ASSERT_NE(loaded, nullptr);
    ASSERT_NE(loaded->main, nullptr);
    Scene *scene = reinterpret_cast<Scene *>(
        BKE_libblock_find_name(loaded->main, ID_SCE, "DescriptorFixture"));
    ASSERT_NE(scene, nullptr);
    EXPECT_NE(scene, scene_);
    EXPECT_EQ(scene->r.cfra, 37);
    EXPECT_EQ(scene->r.xsch, 321);
    EXPECT_EQ(scene->r.ysch, 123);
    IDProperty *properties = IDP_GetProperties(&scene->id);
    ASSERT_NE(properties, nullptr);
    IDProperty *read_marker = IDP_GetPropertyFromGroup(properties, "descriptor_marker");
    ASSERT_NE(read_marker, nullptr);
    ASSERT_EQ(read_marker->type, IDP_INT);
    EXPECT_EQ(IDP_int_get(read_marker), marker);
    IDProperty *text = IDP_GetPropertyFromGroup(properties, "descriptor_text");
    ASSERT_NE(text, nullptr);
    ASSERT_EQ(text->type, IDP_STRING);
    EXPECT_STREQ(IDP_string_get(text), "retained descriptor payload");
    IDProperty *payload = IDP_GetPropertyFromGroup(properties, "descriptor_payload");
    if (!large) {
      EXPECT_EQ(payload, nullptr);
      return;
    }
    ASSERT_NE(payload, nullptr);
    ASSERT_EQ(payload->type, IDP_ARRAY);
    ASSERT_EQ(payload->subtype, IDP_INT);
    ASSERT_EQ(payload->len, payload_count);
    for (int i = 0; i < payload_count; i++) {
      ASSERT_EQ(IDP_array_int_get(payload)[i], payload_value(i)) << "payload index " << i;
    }
  }

  void create_sentinels()
  {
    put_file(path("logical.blend"), "logical path sentinel");
    put_file(path("logical.blend@"), "temporary path sentinel");
    put_file(path("logical.blend1"), "history one sentinel");
    put_file(path("logical.blend2"), "history two sentinel");
  }

  void expect_sentinels()
  {
    EXPECT_EQ(file_bytes(path("logical.blend")), "logical path sentinel");
    EXPECT_EQ(file_bytes(path("logical.blend@")), "temporary path sentinel");
    EXPECT_EQ(file_bytes(path("logical.blend1")), "history one sentinel");
    EXPECT_EQ(file_bytes(path("logical.blend2")), "history two sentinel");
  }

  /** Verify that the retained fd remains writable at exactly the writer's ending offset. */
  void expect_retained(const int descriptor, const size_t size)
  {
    BLI_stat_t info{};
    ASSERT_EQ(BLI_fstat(descriptor, &info), 0);
    EXPECT_EQ(info.st_size, int64_t(size));
    EXPECT_EQ(BLI_lseek(descriptor, 0, SEEK_CUR), int64_t(size));
    EXPECT_EQ(descriptor_write(descriptor, "!", 1), 1);
    EXPECT_EQ(BLI_lseek(descriptor, 0, SEEK_CUR), int64_t(size + 1));
  }

  void expect_preflight_refused(const int descriptor,
                                const BlendFileWriteParams &options,
                                const int flags,
                                const char *logical,
                                const char *diagnostic)
  {
    scene_->id.flag |= ID_FLAG_CLIPBOARD_MARK;
    const std::string original_main_path = main_->filepath;
    BKE_reports_clear(&reports_);
    WriteProbe probe;
    probe.descriptor = descriptor;
    EXPECT_FALSE(BLO_write_file_to_descriptor_for_test(
        main_, logical, descriptor, flags, &options, &reports_, probe.hooks()));
    EXPECT_TRUE(scene_->id.flag & ID_FLAG_CLIPBOARD_MARK);
    EXPECT_EQ(main_->filepath, original_main_path);
    EXPECT_TRUE(probe.requests.empty());
    EXPECT_EQ(probe.after_stream_calls, 0);
    EXPECT_NE(errors().find(diagnostic), std::string::npos) << errors();
  }

  void expect_fault(const WriteProbe &reference,
                    const WriteFault fault,
                    const size_t fault_at,
                    const char *diagnostic)
  {
    ASSERT_LT(fault_at, reference.requests.size());
    const int descriptor = open_owned(path("fault.blend"));
    ASSERT_GE(descriptor, 0);
    const int sibling = open_owned(path("fault-sibling"));
    ASSERT_GE(sibling, 0);
    ASSERT_EQ(descriptor_write(sibling, "sibling", 7), 7);
#ifndef WIN32
    const int directory = ::open(directory_.c_str(), O_RDONLY);
    ASSERT_GE(directory, 0);
    descriptors_.push_back(directory);
#endif
    WriteProbe probe;
    probe.descriptor = descriptor;
    probe.fault = fault;
    probe.fault_at = fault_at;
    errno = EDOM;
    EXPECT_FALSE(write_probe(probe));
    ASSERT_TRUE(probe.failed);
    EXPECT_EQ(probe.requests.size(), fault_at + 1);
    EXPECT_EQ(probe.calls_after_failure, 0);
    EXPECT_EQ(probe.after_stream_calls, 1);
    ASSERT_EQ(probe.requests.size(), fault_at + 1);
    EXPECT_TRUE(
        std::equal(probe.requests.begin(), probe.requests.end(), reference.requests.begin()));
    const size_t before = std::accumulate(
        reference.requests.begin(), reference.requests.begin() + fault_at, size_t(0));
    const size_t accepted = before + (fault == WriteFault::Short ?
                                          std::min(size_t(7), reference.requests[fault_at] - 1) :
                                          0);
    EXPECT_EQ(probe.accepted.size(), accepted);
    const std::string actual = descriptor_bytes(descriptor);
    EXPECT_EQ(actual, probe.accepted);
    /* An independent successful serialization also establishes stream order and exact extent. */
    EXPECT_EQ(actual, reference.accepted.substr(0, accepted));
    EXPECT_NE(errors().find(diagnostic), std::string::npos) << errors();
    EXPECT_EQ(errors().find(strerror(ERANGE)), std::string::npos);
    if (fault != WriteFault::Negative) {
      EXPECT_EQ(errors().find(strerror(EDOM)), std::string::npos);
    }
    expect_sentinels();
    EXPECT_EQ(descriptor_bytes(sibling), "sibling");
    EXPECT_EQ(BLI_lseek(sibling, 0, SEEK_CUR), 7);
    EXPECT_EQ(descriptor_write(sibling, "!", 1), 1);
#ifndef WIN32
    BLI_stat_t info{};
    EXPECT_EQ(BLI_fstat(directory, &info), 0);
    EXPECT_TRUE(S_ISDIR(info.st_mode));
#endif
    expect_retained(descriptor, accepted);
  }

  void make_reference(WriteProbe &reference, const bool large)
  {
    if (large) {
      add_large_payload();
    }
    create_sentinels();
    /* Run the public entry first so existing serializer preparation has already settled. */
    const int control = open_owned(path("public-control.blend"));
    ASSERT_GE(control, 0);
    ASSERT_TRUE(write_public(control)) << errors();
    const std::string public_bytes = descriptor_bytes(control);
    expect_contents(read_back(public_bytes), large);
    reference.descriptor = open_owned(path("reference.blend"));
    ASSERT_GE(reference.descriptor, 0);
    ASSERT_TRUE(write_probe(reference)) << errors();
    EXPECT_EQ(reference.accepted, public_bytes);
    EXPECT_EQ(descriptor_bytes(reference.descriptor), public_bytes);
    EXPECT_EQ(reference.after_stream_calls, 1);
  }

  /** Identify the actual last buffered call, using the format-aware reader for block boundaries.
   */
  void expect_final_dna_flush(const WriteProbe &reference)
  {
    ASSERT_FALSE(reference.requests.empty());
    auto close_reader = [](FileReader *reader) { reader->close(reader); };
    std::unique_ptr<FileReader, decltype(close_reader)> reader(
        BLI_filereader_new_memory(reference.accepted.data(), reference.accepted.size()),
        close_reader);
    ASSERT_NE(reader, nullptr);
    const BlenderHeaderVariant decoded_header = BLO_readfile_blender_header_decode(reader.get());
    ASSERT_TRUE(std::holds_alternative<BlenderHeader>(decoded_header));
    const BlenderHeader header = std::get<BlenderHeader>(decoded_header);
    int previous_code = 0;
    size_t dna_offset = std::numeric_limits<size_t>::max();
    const size_t final_start = reference.accepted.size() - reference.requests.back();
    while (size_t(reader->offset) < reference.accepted.size()) {
      const size_t start = size_t(reader->offset);
      const std::optional<BHead> block = BLO_readfile_read_bhead(reader.get(),
                                                                 header.bhead_type());
      ASSERT_TRUE(block.has_value());
      ASSERT_GE(block->len, 0);
      if (block->code == BLO_CODE_DNA1) {
        dna_offset = start;
        EXPECT_GT(block->len, 0);
      }
      if (block->code == BLO_CODE_ENDB) {
        EXPECT_EQ(previous_code, BLO_CODE_DNA1);
        EXPECT_EQ(block->len, 0);
        EXPECT_EQ(block->nr, 0);
        EXPECT_EQ(block->old, nullptr);
        EXPECT_EQ(block->SDNAnr, 0);
        EXPECT_EQ(size_t(reader->offset), reference.accepted.size());
        /* IDs are flushed before DNA1. Even if DNA spans buffers, mywrite_end flushes the
         * remaining DNA tail (if any) and the complete ENDB header in this last request. */
        EXPECT_NE(dna_offset, std::numeric_limits<size_t>::max());
        EXPECT_GE(final_start, dna_offset);
        EXPECT_GE(start, final_start);
        size_t end_header_size = 0;
        switch (header.bhead_type()) {
          case BHeadType::BHead4:
            end_header_size = sizeof(BHead4);
            break;
          case BHeadType::SmallBHead8:
            end_header_size = sizeof(SmallBHead8);
            break;
          case BHeadType::LargeBHead8:
            end_header_size = sizeof(LargeBHead8);
            break;
        }
        EXPECT_EQ(size_t(reader->offset) - start, end_header_size);
        return;
      }
      ASSERT_LE(uint64_t(block->len), reference.accepted.size() - size_t(reader->offset));
      const int64_t next = reader->offset + block->len;
      ASSERT_EQ(reader->seek(reader.get(), block->len, SEEK_CUR), next);
      previous_code = block->code;
    }
    FAIL() << "Successful stream must end with its actual ENDB block";
  }
};

TEST_F(WritefileDescriptorTest, PublicStreamReadsBackLargePayloadAndRetainsCallerOwnership)
{
  add_large_payload();
  create_sentinels();
  const std::string original_main_path = main_->filepath;
  Main *global_main = G.main;
  const int descriptor = open_owned(path("owned-base.blend"));
  ASSERT_GE(descriptor, 0);
  const int sibling = open_owned(path("sibling"));
  ASSERT_GE(sibling, 0);
  ASSERT_EQ(descriptor_write(sibling, "sibling", 7), 7);
#ifndef WIN32
  const int directory = ::open(directory_.c_str(), O_RDONLY);
  ASSERT_GE(directory, 0);
  descriptors_.push_back(directory);
#endif
  ASSERT_TRUE(write_public(descriptor)) << errors();
  const std::string bytes = descriptor_bytes(descriptor);
  EXPECT_GT(bytes.size(), size_t(2 * 1024 * 1024));
  expect_contents(read_back(bytes), true);
  EXPECT_EQ(main_->filepath, original_main_path);
  EXPECT_EQ(G.main, global_main);
  EXPECT_EQ(G.filepath_last_blend, saved_last_blend_);
  expect_sentinels();
  EXPECT_EQ(descriptor_bytes(sibling), "sibling");
  EXPECT_EQ(BLI_lseek(sibling, 0, SEEK_CUR), 7);
  EXPECT_EQ(descriptor_write(sibling, "!", 1), 1);
#ifndef WIN32
  BLI_stat_t info{};
  EXPECT_EQ(BLI_fstat(directory, &info), 0);
  EXPECT_TRUE(S_ISDIR(info.st_mode));
#endif
  expect_retained(descriptor, bytes.size());
}

TEST_F(WritefileDescriptorTest, RecoveryFlagsKeepRawStreamAndOriginalPath)
{
  const int descriptor = open_owned(path("recovery-base.blend"));
  ASSERT_GE(descriptor, 0);
  G.fileflags = G_FILE_COMPRESS;
  ASSERT_TRUE(write_public(descriptor, G_FILE_RECOVER_WRITE)) << errors();
  const std::string bytes = descriptor_bytes(descriptor);
  G.fileflags |= G_FILE_RECOVER_READ;
  BlendFileData *loaded = read_back(bytes);
  ASSERT_NE(loaded, nullptr);
  expect_contents(loaded, false);
  EXPECT_EQ(loaded->fileflags, G_FILE_COMPRESS);
  EXPECT_STREQ(loaded->filepath, main_->filepath);
  expect_retained(descriptor, bytes.size());
}

TEST_F(WritefileDescriptorTest, IncompatibleOptionsRefuseBeforeMainPreparationOrWrites)
{
  const int descriptor = open_owned(path("options.blend"));
  ASSERT_GE(descriptor, 0);
  const std::string logical = path("logical.blend");
  BlendThumbnail thumbnail{};
  for (int option = 0; option < 11; option++) {
    SCOPED_TRACE(option);
    BlendFileWriteParams rejected = params();
    int flags = 0;
    switch (option) {
      case 0:
        rejected.remap_mode = BLO_WRITE_PATH_REMAP_RELATIVE;
        break;
      case 1:
        rejected.remap_mode = BLO_WRITE_PATH_REMAP_RELATIVE_ALL;
        break;
      case 2:
        rejected.remap_mode = BLO_WRITE_PATH_REMAP_ABSOLUTE;
        break;
      case 3:
        rejected.use_save_versions = true;
        break;
      case 4:
        rejected.use_save_as_copy = false;
        break;
      case 5:
        rejected.use_userdef = true;
        break;
      case 6:
        rejected.is_copypaste_buffer = true;
        break;
      case 7:
        rejected.thumb = &thumbnail;
        break;
      case 8:
        rejected.compression_min_size = 1;
        break;
      case 9:
        flags = G_FILE_COMPRESS;
        break;
      case 10:
        flags = G_FILE_COMPRESS;
        rejected.compression_min_size = 4096;
        break;
    }
    expect_preflight_refused(descriptor, rejected, flags, logical.c_str(), "uncompressed copy");
    EXPECT_TRUE(descriptor_bytes(descriptor).empty());
    EXPECT_EQ(BLI_lseek(descriptor, 0, SEEK_CUR), 0);
  }
  /* A positive control proves the marker is sensitive to entering real preparation. */
  ASSERT_TRUE(write_public(descriptor)) << errors();
  EXPECT_FALSE(scene_->id.flag & ID_FLAG_CLIPBOARD_MARK);
  expect_contents(read_back(descriptor_bytes(descriptor)), false);
}

TEST_F(WritefileDescriptorTest, RelativeEmptyAndNullLogicalPathsRefuseBeforePreparation)
{
  const int descriptor = open_owned(path("bad-path.blend"));
  ASSERT_GE(descriptor, 0);
  for (const char *logical :
       {"relative.blend", "//relative.blend", "", static_cast<const char *>(nullptr)})
  {
    expect_preflight_refused(descriptor, params(), 0, logical, "absolute path");
    EXPECT_TRUE(descriptor_bytes(descriptor).empty());
    EXPECT_EQ(BLI_lseek(descriptor, 0, SEEK_CUR), 0);
  }
}

TEST_F(WritefileDescriptorTest, NonemptyDescriptorIsNotTruncatedOrRepositioned)
{
  const int descriptor = open_owned(path("nonempty.blend"));
  ASSERT_GE(descriptor, 0);
  ASSERT_EQ(descriptor_write(descriptor, "existing bytes", 14), 14);
  ASSERT_EQ(BLI_lseek(descriptor, 0, SEEK_SET), 0);
  const std::string logical = path("logical.blend");
  expect_preflight_refused(descriptor, params(), 0, logical.c_str(), "empty regular file");
  EXPECT_EQ(descriptor_bytes(descriptor), "existing bytes");
  EXPECT_EQ(BLI_lseek(descriptor, 0, SEEK_CUR), 0);
}

TEST_F(WritefileDescriptorTest, EmptyDescriptorAtNonzeroOffsetIsNotRepositioned)
{
  const int descriptor = open_owned(path("offset.blend"));
  ASSERT_GE(descriptor, 0);
  ASSERT_EQ(BLI_lseek(descriptor, 19, SEEK_SET), 19);
  const std::string logical = path("logical.blend");
  expect_preflight_refused(descriptor, params(), 0, logical.c_str(), "offset zero");
  EXPECT_TRUE(descriptor_bytes(descriptor).empty());
  EXPECT_EQ(BLI_lseek(descriptor, 0, SEEK_CUR), 19);
}

TEST_F(WritefileDescriptorTest, InvalidDescriptorRefusesBeforePreparation)
{
  const std::string logical = path("logical.blend");
  expect_preflight_refused(-1, params(), 0, logical.c_str(), "Cannot inspect BASE descriptor");
}

#ifndef WIN32
TEST_F(WritefileDescriptorTest, DirectoryDescriptorRefusesAndRemainsOpen)
{
  const int descriptor = ::open(directory_.c_str(), O_RDONLY);
  ASSERT_GE(descriptor, 0);
  descriptors_.push_back(descriptor);
  const std::string logical = path("logical.blend");
  expect_preflight_refused(descriptor, params(), 0, logical.c_str(), "empty regular file");
  BLI_stat_t info{};
  EXPECT_EQ(BLI_fstat(descriptor, &info), 0);
  EXPECT_TRUE(S_ISDIR(info.st_mode));
}

TEST_F(WritefileDescriptorTest, PipeDescriptorRefusesWithoutConsumingOrClosingEitherEnd)
{
  int pipe_descriptors[2];
  ASSERT_EQ(::pipe(pipe_descriptors), 0);
  descriptors_.push_back(pipe_descriptors[0]);
  descriptors_.push_back(pipe_descriptors[1]);
  const std::string logical = path("logical.blend");
  expect_preflight_refused(
      pipe_descriptors[1], params(), 0, logical.c_str(), "empty regular file");
  BLI_stat_t info{};
  ASSERT_EQ(BLI_fstat(pipe_descriptors[0], &info), 0);
  EXPECT_TRUE(S_ISFIFO(info.st_mode));
  ASSERT_EQ(BLI_fstat(pipe_descriptors[1], &info), 0);
  EXPECT_TRUE(S_ISFIFO(info.st_mode));
  ASSERT_EQ(descriptor_write(pipe_descriptors[1], "pipe", 4), 4);
  char bytes[4];
  ASSERT_EQ(BLI_read(pipe_descriptors[0], bytes, sizeof(bytes)), 4);
  EXPECT_EQ(std::string(bytes, sizeof(bytes)), "pipe");
}
#endif

TEST_F(WritefileDescriptorTest, AssetGuardNamesLogicalPathBeforePreparation)
{
  const int descriptor = open_owned(path("asset.blend"));
  ASSERT_GE(descriptor, 0);
  ASSERT_FALSE(main_->is_asset_edit_file);
  const std::string logical = path("logical.blend");
  expect_preflight_refused(
      descriptor, params(), G_FILE_ASSET_EDIT_FILE, logical.c_str(), "asset system file");
  EXPECT_NE(errors().find(logical), std::string::npos);
  EXPECT_TRUE(descriptor_bytes(descriptor).empty());
  EXPECT_EQ(BLI_lseek(descriptor, 0, SEEK_CUR), 0);
}

TEST_F(WritefileDescriptorTest, ReadOnlyDescriptorFailsAfterPreparationAndRemainsOpen)
{
  create_sentinels();
  put_file(path("readonly.blend"), "");
  const int descriptor = open_owned(path("readonly.blend"), O_RDONLY);
  ASSERT_GE(descriptor, 0);
  scene_->id.flag |= ID_FLAG_CLIPBOARD_MARK;
  EXPECT_FALSE(write_public(descriptor));
  EXPECT_NE(errors().find(strerror(EBADF)), std::string::npos) << errors();
  EXPECT_FALSE(scene_->id.flag & ID_FLAG_CLIPBOARD_MARK);
  EXPECT_TRUE(descriptor_bytes(descriptor).empty());
  EXPECT_EQ(BLI_lseek(descriptor, 0, SEEK_CUR), 0);
  expect_sentinels();
}

TEST_F(WritefileDescriptorTest, PositiveShortWriteKeepsExactPrefixAndDistinctError)
{
  WriteProbe reference;
  make_reference(reference, false);
  expect_fault(reference, WriteFault::Short, 0, "short descriptor write");
}

TEST_F(WritefileDescriptorTest, ZeroWriteKeepsEmptyPrefixAndDistinctError)
{
  WriteProbe reference;
  make_reference(reference, false);
  expect_fault(reference, WriteFault::Zero, 0, "zero-byte descriptor write");
}

TEST_F(WritefileDescriptorTest, NegativeWritePreservesFirstErrnoAfterStreamCleanup)
{
  WriteProbe reference;
  make_reference(reference, false);
  expect_fault(reference, WriteFault::Negative, 0, strerror(ENOSPC));
}

TEST_F(WritefileDescriptorTest, MidStreamFailureRetainsExactReferencePrefixWithoutRetry)
{
  WriteProbe reference;
  make_reference(reference, true);
  ASSERT_GT(reference.requests.size(), 4u);
  const size_t fault_at = reference.requests.size() / 2;
  ASSERT_GT(fault_at, 0u);
  ASSERT_LT(fault_at, reference.requests.size() - 1);
  expect_fault(reference, WriteFault::Short, fault_at, "short descriptor write");
}

TEST_F(WritefileDescriptorTest, FinalBufferedDnaAndEndBlockFlushFailureIsReported)
{
  WriteProbe reference;
  make_reference(reference, true);
  ASSERT_GT(reference.requests.size(), 1u);
  expect_final_dna_flush(reference);
  expect_fault(reference, WriteFault::Negative, reference.requests.size() - 1, strerror(ENOSPC));
}

#ifndef WIN32
TEST_F(WritefileDescriptorTest, ReplacedLogicalPathDoesNotRedirectBorrowedDescriptor)
{
  const std::string logical = path("logical.blend");
  const int descriptor = open_owned(logical);
  ASSERT_GE(descriptor, 0);
  ASSERT_EQ(::unlink(logical.c_str()), 0);
  create_sentinels();
  ASSERT_TRUE(write_public(descriptor)) << errors();
  const std::string bytes = descriptor_bytes(descriptor);
  expect_contents(read_back(bytes), false);
  expect_sentinels();
  BLI_stat_t info{};
  ASSERT_EQ(BLI_fstat(descriptor, &info), 0);
  EXPECT_EQ(info.st_nlink, 0);
  expect_retained(descriptor, bytes.size());
}

TEST_F(WritefileDescriptorTest, UnlinkedBorrowedDescriptorFailurePreservesReplacementPath)
{
  const std::string logical = path("logical.blend");
  const int descriptor = open_owned(logical);
  ASSERT_GE(descriptor, 0);
  ASSERT_EQ(::unlink(logical.c_str()), 0);
  create_sentinels();
  WriteProbe probe;
  probe.descriptor = descriptor;
  probe.fault = WriteFault::Short;
  probe.fault_at = 0;
  EXPECT_FALSE(write_probe(probe));
  EXPECT_EQ(probe.requests.size(), 1u);
  EXPECT_EQ(probe.calls_after_failure, 0);
  EXPECT_EQ(descriptor_bytes(descriptor), probe.accepted);
  expect_sentinels();
  expect_retained(descriptor, probe.accepted.size());
}
#endif

TEST_F(WritefileDescriptorTest, OrdinaryPathWriterStillReadsBackAndRotatesSaveHistory)
{
  const std::string logical = path("ordinary.blend");
  BlendFileWriteParams options = params();
  options.use_save_versions = true;
  U.versions = 2;
  put_file(logical, "old base sentinel");
  put_file(logical + "1", "old history one sentinel");
  put_file(logical + "2", "old history two sentinel");
  put_file(logical + "@", "old temporary sentinel");
  ASSERT_TRUE(BLO_write_file(main_, logical.c_str(), 0, &options, &reports_)) << errors();
  EXPECT_EQ(file_bytes(logical + "1"), "old base sentinel");
  EXPECT_EQ(file_bytes(logical + "2"), "old history one sentinel");
  EXPECT_FALSE(BLI_exists((logical + "@").c_str()));
  const std::string first = file_bytes(logical);
  expect_contents(read_back(first), false);

  IDP_int_set(marker_, 24680);
  ASSERT_TRUE(BLO_write_file(main_, logical.c_str(), 0, &options, &reports_)) << errors();
  EXPECT_EQ(file_bytes(logical + "1"), first);
  EXPECT_EQ(file_bytes(logical + "2"), "old base sentinel");
  EXPECT_FALSE(BLI_exists((logical + "@").c_str()));
  expect_contents(read_back(file_bytes(logical)), false, 24680);
  expect_contents(read_back(file_bytes(logical + "1")), false, 13579);
}

}  // namespace
}  // namespace blender
