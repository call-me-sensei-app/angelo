/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** The checkpoint writer's content binding (CheckpointWriteContext::content): what it covers, and
 * that a same-size rewrite which keeps the file's stamp is still told apart by it. */

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <filesystem>
#include <string>
#include <vector>

#include <fcntl.h>
#include <sys/stat.h>
#ifdef WIN32
#  include <io.h>
#else
#  include <unistd.h>
#endif

#include "DNA_scene_types.h"

#include "BLI_fileops.h"
#include "BLI_hash_xxh3.hh"
#include "BLI_path_utils.hh"
#include "BLI_string.h"
#include "BLI_tempfile.h"

#include "BKE_idprop.hh"
#include "BKE_main.hh"
#include "BKE_report.hh"
#include "BKE_scene.hh"

#include "BLO_writefile.hh"

#include "blendfile_loading_base_test.h"
#include "intern/checkpoint_write_context.hh"

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

static bool accept_ids(Main &, Span<ID *>, const CheckpointWriteTokenLookup &, ReportList *, void *)
{
  return true;
}

/** A descriptor stand-in: passes buffers through, or alters, shortens or refuses one of them. */
struct Descriptor {
  enum class Fault { None, AlterFirstByte, Short, Zero, Negative };
  int descriptor = -1;
  Fault fault = Fault::None;
  size_t fault_at = 0;
  size_t calls = 0;
  /** Sizes of the buffers the descriptor accepted in full. */
  std::vector<size_t> accepted;
  /** Runs after the stream, as another writer would between native's write and a later reader. */
  void (*after_stream_action)(Descriptor &) = nullptr;

  static int64_t write(int descriptor, const void *buffer, size_t size, void *user_data)
  {
    Descriptor &self = *static_cast<Descriptor *>(user_data);
    const size_t call = self.calls++;
    if (call == self.fault_at) {
      switch (self.fault) {
        case Fault::None:
          break;
        case Fault::AlterFirstByte: {
          /* The descriptor stores other bytes than it was given, and still reports them all. */
          std::string altered(static_cast<const char *>(buffer), size);
          altered[0] = char(altered[0] ^ 0x5a);
          const int64_t written = descriptor_write(descriptor, altered.data(), size);
          if (written == int64_t(size)) {
            self.accepted.push_back(size);
          }
          return written;
        }
        case Fault::Short: {
          const int64_t written = descriptor_write(descriptor, buffer, std::max<size_t>(1, size / 2));
          return written;
        }
        case Fault::Zero:
          return 0;
        case Fault::Negative:
          errno = ENOSPC;
          return -1;
      }
    }
    const int64_t written = descriptor_write(descriptor, buffer, size);
    if (written == int64_t(size)) {
      self.accepted.push_back(size);
    }
    return written;
  }

  static void after_stream(void *user_data)
  {
    Descriptor &self = *static_cast<Descriptor *>(user_data);
    if (self.after_stream_action) {
      self.after_stream_action(self);
    }
  }

  BlendFileDescriptorTestHooks hooks()
  {
    return {write, this, after_stream};
  }
};

class WritefileCheckpointBindingTest : public BlendfileLoadingBaseTest {
 protected:
  Main *main_ = nullptr;
  ReportList reports_{};
  std::string directory_;
  std::vector<int> descriptors_;

  void SetUp() override
  {
    BKE_reports_init(&reports_, RPT_STORE | RPT_PRINT_HANDLED_BY_OWNER);
    char temporary[FILE_MAX];
    BLI_temp_directory_path_get(temporary, sizeof(temporary));
    const auto nonce = std::chrono::steady_clock::now().time_since_epoch().count();
    for (int i = 0; i < 100 && directory_.empty(); i++) {
      char candidate[FILE_MAX];
      const std::string name = "blender_checkpoint_binding_" + std::to_string(nonce) + "_" +
                               std::to_string(i);
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
    Scene *scene = BKE_scene_add(main_, "BindingFixture");
    ASSERT_NE(scene, nullptr);
    /* Several megabytes, so the stream spans many writer buffers (2 MiB buffer, 1 MiB chunks). */
    IDPropertyTemplate value{};
    value.array.type = IDP_INT;
    value.array.len = 3 * 1024 * 1024 / sizeof(int) + 17;
    IDProperty *payload = IDP_New(IDP_ARRAY, &value, "binding_payload");
    ASSERT_NE(payload, nullptr);
    ASSERT_TRUE(IDP_AddToGroup(IDP_EnsureProperties(&scene->id), payload));
    for (int i = 0; i < value.array.len; i++) {
      IDP_array_int_get(payload)[i] = int((uint32_t(i) * 2654435761u) >> 7);
    }
  }

  void TearDown() override
  {
    if (main_) {
      BKE_main_free(main_);
    }
    BKE_reports_free(&reports_);
    for (const int descriptor : descriptors_) {
#ifdef WIN32
      EXPECT_EQ(_close(descriptor), 0);
#else
      EXPECT_EQ(::close(descriptor), 0);
#endif
    }
    if (!directory_.empty()) {
      std::error_code error;
      std::filesystem::remove_all(
          std::filesystem::path(std::u8string(directory_.begin(), directory_.end())), error);
      EXPECT_FALSE(bool(error));
    }
    BlendfileLoadingBaseTest::TearDown();
  }

  std::string path(const char *name) const
  {
    char filepath[FILE_MAX];
    BLI_path_join(filepath, sizeof(filepath), directory_.c_str(), name);
    return filepath;
  }

  int open_base(const char *name)
  {
    const int descriptor = BLI_open(
        path(name).c_str(), O_RDWR | O_CREAT | O_EXCL | binary_flag, 0600);
    EXPECT_GE(descriptor, 0);
    if (descriptor >= 0) {
      descriptors_.push_back(descriptor);
    }
    return descriptor;
  }

  static std::string bytes(const int descriptor)
  {
    BLI_stat_t info{};
    EXPECT_EQ(BLI_fstat(descriptor, &info), 0);
    const int64_t offset = BLI_lseek(descriptor, 0, SEEK_CUR);
    EXPECT_EQ(BLI_lseek(descriptor, 0, SEEK_SET), 0);
    std::string result(size_t(info.st_size), '\0');
    EXPECT_EQ(BLI_read(descriptor, result.data(), result.size()), int64_t(result.size()));
    EXPECT_EQ(BLI_lseek(descriptor, offset, SEEK_SET), offset);
    return result;
  }

  static std::array<uint8_t, 16> xxh3(const std::string &data)
  {
    return BLI_hash_xxh3_128(
        Span<std::byte>(reinterpret_cast<const std::byte *>(data.data()), int64_t(data.size())));
  }

  CheckpointWriteContext context(XXH3_128Stream *content)
  {
    CheckpointWriteContext result;
    result.before_local_ids = accept_ids;
    result.after_local_ids = accept_ids;
    result.content = content;
    return result;
  }

  bool write(const int descriptor, XXH3_128Stream *content)
  {
    BlendFileWriteParams params{};
    params.use_save_as_copy = true;
    return BLO_write_checkpoint_to_descriptor(
        main_, path("logical.blend").c_str(), descriptor, 0, &params, &reports_, context(content));
  }

  bool write_through(Descriptor &probe, XXH3_128Stream *content)
  {
    BlendFileWriteParams params{};
    params.use_save_as_copy = true;
    return BLO_write_checkpoint_to_descriptor_for_test(main_,
                                                       path("logical.blend").c_str(),
                                                       probe.descriptor,
                                                       0,
                                                       &params,
                                                       &reports_,
                                                       context(content),
                                                       probe.hooks());
  }
};

TEST_F(WritefileCheckpointBindingTest, BindingIsTheXXH3OfTheFinalFile)
{
  const int descriptor = open_base("bound.blend");
  ASSERT_GE(descriptor, 0);
  XXH3_128Stream content;
  ASSERT_TRUE(write(descriptor, &content));
  const std::string written = bytes(descriptor);
  ASSERT_GT(written.size(), size_t(3 * 1024 * 1024));
  EXPECT_EQ(content.bytes(), uint64_t(written.size()));
  EXPECT_EQ(content.finalize(), xxh3(written));
}

TEST_F(WritefileCheckpointBindingTest, BindingNeverChangesTheBytesWritten)
{
  /* With the gate off (no stream) the writer produces the same file, and records no binding. */
  const int bound = open_base("with-binding.blend");
  const int unbound = open_base("without-binding.blend");
  ASSERT_GE(bound, 0);
  ASSERT_GE(unbound, 0);
  XXH3_128Stream content;
  ASSERT_TRUE(write(bound, &content));
  ASSERT_TRUE(write(unbound, nullptr));
  EXPECT_EQ(bytes(bound), bytes(unbound));
  EXPECT_EQ(content.bytes(), uint64_t(bytes(bound).size()));
}

TEST_F(WritefileCheckpointBindingTest, UsedStreamIsRefusedBeforeAnyByte)
{
  const int descriptor = open_base("used-stream.blend");
  ASSERT_GE(descriptor, 0);
  XXH3_128Stream content;
  content.update("x", 1);
  EXPECT_FALSE(write(descriptor, &content));
  EXPECT_TRUE(bytes(descriptor).empty());
  EXPECT_EQ(content.bytes(), 1u);
}

#ifndef WIN32
/** Another writer after native's last write: same size, one byte flipped, times put back. */
static void rewrite_same_size_and_restore_times(Descriptor &self)
{
  struct stat before{};
  ASSERT_EQ(fstat(self.descriptor, &before), 0);
  ASSERT_GT(before.st_size, 64);
  char byte = 0;
  ASSERT_EQ(pread(self.descriptor, &byte, 1, 64), 1);
  byte = char(byte ^ 0x01);
  ASSERT_EQ(pwrite(self.descriptor, &byte, 1, 64), 1);
#  ifdef __APPLE__
  const timespec times[2] = {before.st_atimespec, before.st_mtimespec};
#  else
  const timespec times[2] = {before.st_atim, before.st_mtim};
#  endif
  ASSERT_EQ(futimens(self.descriptor, times), 0);
}

TEST_F(WritefileCheckpointBindingTest, SameSizeRewriteKeepingTheStampChangesOnlyTheBinding)
{
  const int descriptor = open_base("rewritten.blend");
  ASSERT_GE(descriptor, 0);
  Descriptor probe;
  probe.descriptor = descriptor;
  XXH3_128Stream content;
  /* The stamp the writer's last write left, read inside the hook before the rewrite. */
  static struct stat stamped;
  probe.after_stream_action = [](Descriptor &self) {
    ASSERT_EQ(fstat(self.descriptor, &stamped), 0);
    rewrite_same_size_and_restore_times(self);
  };
  ASSERT_TRUE(write_through(probe, &content));
  struct stat now{};
  ASSERT_EQ(fstat(descriptor, &now), 0);
  /* What a stamp check compares (identity, size, modification time) is unchanged ... */
  EXPECT_EQ(now.st_dev, stamped.st_dev);
  EXPECT_EQ(now.st_ino, stamped.st_ino);
  EXPECT_EQ(now.st_size, stamped.st_size);
#  ifdef __APPLE__
  EXPECT_EQ(now.st_mtimespec.tv_sec, stamped.st_mtimespec.tv_sec);
  EXPECT_EQ(now.st_mtimespec.tv_nsec, stamped.st_mtimespec.tv_nsec);
#  else
  EXPECT_EQ(now.st_mtim.tv_sec, stamped.st_mtim.tv_sec);
  EXPECT_EQ(now.st_mtim.tv_nsec, stamped.st_mtim.tv_nsec);
#  endif
  /* ... but the bytes are not those written, and the binding says so. */
  const std::string current = bytes(descriptor);
  EXPECT_EQ(content.bytes(), uint64_t(current.size()));
  EXPECT_NE(content.finalize(), xxh3(current));
}
#endif

TEST_F(WritefileCheckpointBindingTest, DescriptorStoringOtherBytesIsAMismatch)
{
  const int descriptor = open_base("altered.blend");
  ASSERT_GE(descriptor, 0);
  Descriptor probe;
  probe.descriptor = descriptor;
  probe.fault = Descriptor::Fault::AlterFirstByte;
  probe.fault_at = 1;
  XXH3_128Stream content;
  ASSERT_TRUE(write_through(probe, &content));
  const std::string current = bytes(descriptor);
  EXPECT_EQ(content.bytes(), uint64_t(current.size()));
  EXPECT_NE(content.finalize(), xxh3(current));
}

TEST_F(WritefileCheckpointBindingTest, FailedWritesCoverOnlyBuffersAcceptedInFull)
{
  for (const Descriptor::Fault fault :
       {Descriptor::Fault::Short, Descriptor::Fault::Zero, Descriptor::Fault::Negative})
  {
    const std::string name = "fault-" + std::to_string(int(fault)) + ".blend";
    const int descriptor = open_base(name.c_str());
    ASSERT_GE(descriptor, 0);
    Descriptor probe;
    probe.descriptor = descriptor;
    probe.fault = fault;
    probe.fault_at = 1;
    XXH3_128Stream content;
    EXPECT_FALSE(write_through(probe, &content));
    uint64_t accepted = 0;
    for (const size_t size : probe.accepted) {
      accepted += size;
    }
    /* Never a partial buffer: the stream holds exactly the buffers written in full before. */
    ASSERT_EQ(probe.accepted.size(), 1u);
    EXPECT_EQ(content.bytes(), accepted);
    EXPECT_EQ(content.finalize(), xxh3(bytes(descriptor).substr(0, size_t(accepted))));
  }
}

}  // namespace
}  // namespace blender
