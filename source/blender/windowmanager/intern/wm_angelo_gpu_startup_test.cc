/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "testing/testing.h"

#include <string>
#include <thread>

#include "BLI_fileops.hh"
#include "BLI_path_utils.hh"
#include "BLI_tempfile.hh"

#include "wm_angelo_gpu_startup_intern.hh"

#ifdef WIN32
#  include <windows.h>
#else
#  include <unistd.h>
#endif

namespace blender::wm::angelo::tests {

using Record = GPUStartupRecord;
using Action = GPUStartupAction;

/** A file of this test process in the temporary folder, not there yet. */
static std::string test_filepath(const char *name)
{
  char folder[FILE_MAX];
  BLI_temp_directory_path_get(folder, sizeof(folder));
#ifdef WIN32
  const std::string process = std::to_string(GetCurrentProcessId());
#else
  const std::string process = std::to_string(getpid());
#endif
  const std::string file = std::string("angelo-gpu-startup-test-") + name + "-" + process;
  char filepath[FILE_MAX];
  BLI_path_join(filepath, sizeof(filepath), folder, file.c_str());
  if (BLI_exists(filepath)) {
    BLI_delete(filepath, false, false);
  }
  return filepath;
}

static void expect_plan(const Record found,
                        const bool prefers_vulkan,
                        const bool use_fallback,
                        const bool announce,
                        const bool failed,
                        const Action action)
{
  const GPUStartupPlan plan = gpu_startup_plan(found, prefers_vulkan);
  const std::string what = "record " + std::to_string(int(found)) + ", vulkan " +
                           std::to_string(prefers_vulkan);
  EXPECT_EQ(plan.use_fallback, use_fallback) << what;
  EXPECT_EQ(plan.announce, announce) << what;
  EXPECT_EQ(plan.failed, failed) << what;
  EXPECT_EQ(int(plan.action), int(action)) << what;
}

TEST(wm_angelo_gpu_startup, AVulkanLaunchLeavesARecordAndAnOpenGLLaunchDoesNot)
{
  expect_plan(Record::None, true, false, false, false, Action::WriteStarting);
  expect_plan(Record::None, false, false, false, false, Action::Keep);
}

TEST(wm_angelo_gpu_startup, AStartThatDiedBeforeItsFirstFrameFallsBackAndSaysSoOnce)
{
  /* Left by a process that ended, or marked by its crash handler. */
  for (const Record found : {Record::Starting, Record::Crashed}) {
    expect_plan(found, true, true, true, true, Action::WriteFailed);
  }
}

TEST(wm_angelo_gpu_startup, ADeathWhileStartupScriptsRanIsNotTheGPUs)
{
  /* An add-on that crashes as it loads, a script that ends the process, a first start of a new
   * version ended by force: the next launch starts with Vulkan again, watched. */
  expect_plan(Record::StartingScripts, true, false, false, false, Action::WriteStarting);
  expect_plan(Record::StartingScripts, false, false, false, false, Action::Clear);
}

TEST(wm_angelo_gpu_startup, AFailedStartKeepsOpenGLQuietlyWhilePreferencesAskForVulkan)
{
  /* Preferences never saved after the fallback, or a file read later that says Vulkan: OpenGL
   * again, and the person is not told again. */
  expect_plan(Record::Failed, true, true, false, true, Action::Keep);
}

TEST(wm_angelo_gpu_startup, TheEvidenceIsKeptWhilePreferencesAskForOpenGL)
{
  /* Nothing to fall back from and nothing to announce, but a file that says Vulkan read later
   * must not be tried. */
  for (const Record found : {Record::Starting, Record::Crashed}) {
    expect_plan(found, false, false, false, true, Action::WriteFailed);
  }
  expect_plan(Record::Failed, false, false, false, true, Action::Keep);
}

TEST(wm_angelo_gpu_startup, ARecordThatCannotBeTakenStillTellsOfAFailure)
{
  /* Held past the wait or not openable for writing, but readable. A record that says crashed or
   * failed has no live launch behind it: OpenGL, and nothing is written. */
  for (const bool prefers_vulkan : {true, false}) {
    for (const Record peeked : {Record::Crashed, Record::Failed}) {
      const GPUStartupPlan plan = gpu_startup_plan_unheld(peeked, prefers_vulkan);
      EXPECT_EQ(plan.use_fallback, prefers_vulkan);
      EXPECT_TRUE(plan.failed);
      /* Only the launch that learns of the crash tells the person. */
      EXPECT_EQ(plan.announce, prefers_vulkan && peeked == Record::Crashed);
      EXPECT_EQ(int(plan.action), int(Action::Keep));
    }
    /* A starting record may be a live launch's, and no record is no evidence. */
    for (const Record peeked : {Record::None, Record::Starting, Record::StartingScripts}) {
      const GPUStartupPlan plan = gpu_startup_plan_unheld(peeked, prefers_vulkan);
      EXPECT_FALSE(plan.use_fallback);
      EXPECT_FALSE(plan.failed);
      EXPECT_FALSE(plan.announce);
      EXPECT_EQ(int(plan.action), int(Action::Keep));
    }
  }
}

TEST(wm_angelo_gpu_startup, ACrashWhileScriptsRunIsTheirsOnlyOnTheMainThread)
{
  const std::string crashed(GPU_STARTUP_TEXT_CRASHED, GPU_STARTUP_TEXT_CRASHED_SIZE);
  const auto left = [](const bool in_scripts, const bool on_main_thread) {
    const GPUStartupCrashText text = gpu_startup_crash_text(in_scripts, on_main_thread);
    return std::string(text.text, text.length);
  };
  /* Scripts and add-ons run on the main thread: their crash says nothing about the GPU. */
  EXPECT_EQ(left(true, true), std::string());
  /* Vulkan's submission thread and the driver's threads work on while scripts run. */
  EXPECT_EQ(left(true, false), crashed);
  /* Outside the scripts every crash before the first frame counts. */
  EXPECT_EQ(left(false, true), crashed);
  EXPECT_EQ(left(false, false), crashed);
  EXPECT_EQ(int(gpu_startup_record_from_text(crashed).record), int(Record::Crashed));
}

TEST(wm_angelo_gpu_startup, AThreadIsToldFromAnother)
{
  const GPUStartupThread here = gpu_startup_thread_current();
  EXPECT_TRUE(gpu_startup_thread_is_current(here));
  bool there_is_here = true, there_is_there = false;
  std::thread other([&]() {
    there_is_here = gpu_startup_thread_is_current(here);
    there_is_there = gpu_startup_thread_is_current(gpu_startup_thread_current());
  });
  other.join();
  EXPECT_FALSE(there_is_here);
  EXPECT_TRUE(there_is_there);
  /* A thread never noted is nobody's: a crash is then never taken for the scripts'. */
  EXPECT_FALSE(gpu_startup_thread_is_current(GPUStartupThread()));
}

TEST(wm_angelo_gpu_startup, TheRecordsTextReadsBack)
{
  for (const Record record : {Record::Starting, Record::StartingScripts}) {
    const GPUStartupRecordData data = gpu_startup_record_from_text(
        gpu_startup_record_text({record, 4242}));
    EXPECT_EQ(int(data.record), int(record));
    EXPECT_EQ(data.pid, 4242);
  }
  for (const Record record : {Record::Crashed, Record::Failed}) {
    const GPUStartupRecordData data = gpu_startup_record_from_text(
        gpu_startup_record_text({record, 0}));
    EXPECT_EQ(int(data.record), int(record));
    EXPECT_EQ(data.pid, 0);
  }
  EXPECT_EQ(gpu_startup_record_text({}), "");
  EXPECT_EQ(gpu_startup_record_text({Record::Crashed, 0}), GPU_STARTUP_TEXT_CRASHED);
  EXPECT_EQ(gpu_startup_record_text({Record::Starting, 17}), "vulkan starting 17\n");
  EXPECT_EQ(gpu_startup_record_text({Record::StartingScripts, 17}),
            "vulkan starting 17 scripts\n");
  /* Line endings and spaces around the text do not matter. */
  EXPECT_EQ(int(gpu_startup_record_from_text("  vulkan failed\r\n").record), int(Record::Failed));
  EXPECT_EQ(int(gpu_startup_record_from_text("vulkan crashed\r\n").record), int(Record::Crashed));
  EXPECT_EQ(gpu_startup_record_from_text("vulkan starting 17\r\n").pid, 17);
}

TEST(wm_angelo_gpu_startup, TextThatIsNotARecordIsNoRecord)
{
  for (const char *text : {"",
                           "\n",
                           "vulkan",
                           "vulkan starting",
                           "vulkan starting ",
                           "vulkan starting -5",
                           "vulkan starting 12x",
                           "vulkan starting 1 2",
                           "vulkan starting scripts",
                           "vulkan starting  scripts",
                           "vulkan starting 12 scripts 3",
                           "vulkan starting 99999999999999999999999999",
                           "vulkan failed 3",
                           "vulkan crashed 3",
                           "opengl failed",
                           "VULKAN FAILED",
                           "vulkan failed\nvulkan starting 3"})
  {
    const GPUStartupRecordData data = gpu_startup_record_from_text(text);
    EXPECT_EQ(int(data.record), int(Record::None)) << "text: " << text;
    EXPECT_EQ(data.pid, 0) << "text: " << text;
  }
}

TEST(wm_angelo_gpu_startup, OneProcessAtATimeHoldsTheFile)
{
  const std::string filepath = test_filepath("held");
  GPUStartupFile file;
  ASSERT_EQ(int(gpu_startup_file_open(filepath.c_str(), file)), int(GPUStartupOpen::Opened));
  EXPECT_TRUE(file.is_open());
  /* Created empty: no record. */
  EXPECT_EQ(int(gpu_startup_file_read(file).record), int(Record::None));
  ASSERT_TRUE(gpu_startup_file_write(file, {Record::Starting, 77}));

  /* A second launch cannot open it, changes nothing, and can still see what it says. */
  GPUStartupFile other;
  EXPECT_EQ(int(gpu_startup_file_open(filepath.c_str(), other)), int(GPUStartupOpen::Held));
  EXPECT_FALSE(other.is_open());
  GPUStartupRecordData data = gpu_startup_file_peek(filepath.c_str());
  EXPECT_EQ(int(data.record), int(Record::Starting));
  EXPECT_EQ(data.pid, 77);

  /* The holder says its scripts run, and that they are over, in place. */
  ASSERT_TRUE(gpu_startup_file_write(file, {Record::StartingScripts, 77}));
  EXPECT_EQ(int(gpu_startup_file_peek(filepath.c_str()).record), int(Record::StartingScripts));
  ASSERT_TRUE(gpu_startup_file_write(file, {Record::Starting, 77}));
  EXPECT_EQ(int(gpu_startup_file_read(file).record), int(Record::Starting));

  /* Let go of (as the operating system does for a process that ended): the text stays, and the
   * next launch that opens the file reads it and can replace it. */
  gpu_startup_file_close(file);
  EXPECT_FALSE(file.is_open());
  gpu_startup_file_close(file); /* Twice is harmless. */
  ASSERT_EQ(int(gpu_startup_file_open(filepath.c_str(), other)), int(GPUStartupOpen::Opened));
  data = gpu_startup_file_read(other);
  EXPECT_EQ(int(data.record), int(Record::Starting));
  EXPECT_EQ(data.pid, 77);
  ASSERT_TRUE(gpu_startup_file_write(other, {Record::Failed, 0}));
  EXPECT_EQ(int(gpu_startup_file_read(other).record), int(Record::Failed));
  gpu_startup_file_close(other);
  EXPECT_EQ(int(gpu_startup_file_peek(filepath.c_str()).record), int(Record::Failed));
  BLI_delete(filepath.c_str(), false, false);
}

TEST(wm_angelo_gpu_startup, ACompletedStartEmptiesItsRecordBeforeItLetsGo)
{
  const std::string filepath = test_filepath("emptied");
  GPUStartupFile file;
  ASSERT_EQ(int(gpu_startup_file_open(filepath.c_str(), file)), int(GPUStartupOpen::Opened));
  ASSERT_TRUE(gpu_startup_file_write(file, {Record::Starting, 31337}));
  /* A shorter text replaces a longer one whole. */
  ASSERT_TRUE(gpu_startup_file_write(file, {Record::Failed, 0}));
  EXPECT_EQ(int(gpu_startup_file_read(file).record), int(Record::Failed));
  /* Emptied while still held: whoever opens it next finds no start that looks abandoned. */
  ASSERT_TRUE(gpu_startup_file_write(file, {}));
  EXPECT_EQ(int(gpu_startup_file_peek(filepath.c_str()).record), int(Record::None));
  gpu_startup_file_close(file);
  EXPECT_TRUE(BLI_exists(filepath.c_str()));
  EXPECT_EQ(int(gpu_startup_file_peek(filepath.c_str()).record), int(Record::None));
  BLI_delete(filepath.c_str(), false, false);
}

TEST(wm_angelo_gpu_startup, TheCrashHandlersWriteIsTheCrashedRecord)
{
  /* What the crash handler does with the file it holds: only the raw write, then it lets go. */
  const std::string filepath = test_filepath("crashed");
  GPUStartupFile file;
  ASSERT_EQ(int(gpu_startup_file_open(filepath.c_str(), file)), int(GPUStartupOpen::Opened));
  ASSERT_TRUE(gpu_startup_file_write(file, {Record::Starting, 123456789}));
  ASSERT_TRUE(
      gpu_startup_file_write_raw(file, GPU_STARTUP_TEXT_CRASHED, GPU_STARTUP_TEXT_CRASHED_SIZE));
  gpu_startup_file_close(file);
  EXPECT_EQ(int(gpu_startup_file_peek(filepath.c_str()).record), int(Record::Crashed));
  /* And while its scripts ran: emptied, so the death says nothing. */
  ASSERT_EQ(int(gpu_startup_file_open(filepath.c_str(), file)), int(GPUStartupOpen::Opened));
  ASSERT_TRUE(gpu_startup_file_write(file, {Record::StartingScripts, 123456789}));
  ASSERT_TRUE(gpu_startup_file_write_raw(file, "", 0));
  gpu_startup_file_close(file);
  EXPECT_EQ(int(gpu_startup_file_peek(filepath.c_str()).record), int(Record::None));
  BLI_delete(filepath.c_str(), false, false);
}

TEST(wm_angelo_gpu_startup, AFileThatCannotBeOpenedSaysNothing)
{
  /* A folder that does not exist: not held by anyone, and not a dead launch either. */
  const std::string filepath = test_filepath("missing-folder") + "/record.txt";
  GPUStartupFile file;
  EXPECT_EQ(int(gpu_startup_file_open(filepath.c_str(), file)), int(GPUStartupOpen::Unknown));
  EXPECT_FALSE(file.is_open());
  EXPECT_EQ(int(gpu_startup_file_peek(filepath.c_str()).record), int(Record::None));
}

TEST(wm_angelo_gpu_startup, OnlyTheStartOfALongFileIsRead)
{
  const std::string filepath = test_filepath("long");
  GPUStartupFile file;
  ASSERT_EQ(int(gpu_startup_file_open(filepath.c_str(), file)), int(GPUStartupOpen::Opened));
  const std::string text = "vulkan failed\n" + std::string(100000, 'x');
  ASSERT_TRUE(gpu_startup_file_write_raw(file, text.data(), text.size()));
  /* More than a record's one line: not a record. */
  EXPECT_EQ(int(gpu_startup_file_read(file).record), int(Record::None));
  gpu_startup_file_close(file);
  BLI_delete(filepath.c_str(), false, false);
}

}  // namespace blender::wm::angelo::tests
