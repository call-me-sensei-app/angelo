/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "testing/testing.h"

#include <string>

#include "BKE_report.hh"

#include "BLI_listbase_iterator.hh"
#include "BLI_threads.h"

#include "WM_angelo_job_reports.hh"

namespace blender::wm::angelo::tests {

/** A report list that stores every type, as a job's or an operator's does. */
struct TestReports {
  ReportList list;
  TestReports()
  {
    BKE_reports_init(&list, RPT_STORE);
    BKE_report_store_level_set(&list, RPT_DEBUG);
  }
  ~TestReports()
  {
    BKE_reports_free(&list);
  }
};

static JobReportsCapture capture_of(ReportList &list)
{
  JobReportsCapture capture;
  for (const Report &report : list.list) {
    capture.add(report);
  }
  return capture;
}

class JobReportsTest : public ::testing::Test {
 protected:
  void SetUp() override
  {
    /* The store is main-thread only (asserted): make this thread the main one. */
    BLI_threadapi_init();
    job_reports_clear();
  }
  void TearDown() override
  {
    job_reports_clear();
  }
};

TEST_F(JobReportsTest, KeepsOnlyErrorsAndWarningsInOrder)
{
  TestReports reports;
  BKE_report(&reports.list, RPT_INFO, "Saved");
  BKE_report(&reports.list, RPT_WARNING, "No UVs");
  BKE_report(&reports.list, RPT_ERROR_INVALID_INPUT, "Bad input");
  BKE_report(&reports.list, RPT_OPERATOR, "bpy.ops.object.bake()");
  BKE_report(&reports.list, RPT_ERROR, "No camera");
  const JobReportsCapture capture = capture_of(reports.list);
  ASSERT_EQ(capture.reports().size(), 3);
  EXPECT_EQ(capture.reports()[0].type, RPT_WARNING);
  EXPECT_EQ(capture.reports()[0].text, "No UVs");
  /* Every error kind is an ERROR. */
  EXPECT_EQ(capture.reports()[1].type, RPT_ERROR);
  EXPECT_EQ(capture.reports()[1].text, "Bad input");
  EXPECT_EQ(capture.reports()[2].text, "No camera");
}

TEST_F(JobReportsTest, PrefersTheUntranslatedText)
{
  TestReports reports;
  BKE_report_translated(&reports.list, RPT_ERROR, "カメラがありません", "No camera found in scene");
  BKE_report_translated(&reports.list, RPT_ERROR, "Same text", "Same text");
  BKE_report_translated(&reports.list, RPT_WARNING, "Only a message", nullptr);
  const Report *first = static_cast<const Report *>(reports.list.list.first);
  EXPECT_STREQ(first->message, "カメラがありません");
  EXPECT_STREQ(first->message_untranslated, "No camera found in scene");
  EXPECT_EQ(first->next->message_untranslated, nullptr); /* The same text stores none. */
  const JobReportsCapture capture = capture_of(reports.list);
  ASSERT_EQ(capture.reports().size(), 3);
  EXPECT_EQ(capture.reports()[0].text, "No camera found in scene");
  EXPECT_EQ(capture.reports()[1].text, "Same text");
  EXPECT_EQ(capture.reports()[2].text, "Only a message");
}

TEST_F(JobReportsTest, AFullRunDropsItsOldestWarningFirst)
{
  TestReports reports;
  BKE_report(&reports.list, RPT_ERROR, "error 0");
  for (int i = 0; i < job_reports_per_run; i++) {
    BKE_reportf(&reports.list, RPT_WARNING, "warning %d", i);
  }
  BKE_report(&reports.list, RPT_ERROR, "the error that ended it");
  const JobReportsCapture capture = capture_of(reports.list);
  ASSERT_EQ(capture.reports().size(), job_reports_per_run);
  EXPECT_EQ(capture.reports().first().text, "error 0");
  EXPECT_EQ(capture.reports()[1].text, "warning 2");
  EXPECT_EQ(capture.reports().last().text, "the error that ended it");

  /* Errors only: the first stays (often the cause), the second gives way, the latest are kept. */
  TestReports errors;
  for (int i = 0; i < job_reports_per_run + 2; i++) {
    BKE_reportf(&errors.list, RPT_ERROR, "error %d", i);
  }
  const JobReportsCapture only_errors = capture_of(errors.list);
  ASSERT_EQ(only_errors.reports().size(), job_reports_per_run);
  EXPECT_EQ(only_errors.reports()[0].text, "error 0");
  EXPECT_EQ(only_errors.reports()[1].text, "error 3");
  EXPECT_EQ(only_errors.reports().last().text, "error 9");
}

TEST_F(JobReportsTest, ALateWarningNeverDisplacesAnError)
{
  TestReports reports;
  for (int i = 0; i < job_reports_per_run; i++) {
    BKE_reportf(&reports.list, RPT_ERROR, "error %d", i);
  }
  BKE_report(&reports.list, RPT_WARNING, "a late warning");
  const JobReportsCapture capture = capture_of(reports.list);
  ASSERT_EQ(capture.reports().size(), job_reports_per_run);
  for (const JobReport &report : capture.reports()) {
    EXPECT_EQ(report.type, RPT_ERROR);
  }
  EXPECT_EQ(capture.reports().first().text, "error 0");
  EXPECT_EQ(capture.reports().last().text, "error 7");
}

TEST_F(JobReportsTest, ClipsByCodePointNeverInsideOne)
{
  TestReports reports;
  std::string ascii(job_report_max_chars + 40, 'a');
  std::string wide;
  for (int i = 0; i < job_report_max_chars + 5; i++) {
    wide += "\xE3\x81\x82"; /* U+3042, three bytes. */
  }
  BKE_report(&reports.list, RPT_ERROR, ascii.c_str());
  BKE_report(&reports.list, RPT_ERROR, wide.c_str());
  const JobReportsCapture capture = capture_of(reports.list);
  EXPECT_EQ(capture.reports()[0].text.size(), job_report_max_chars);
  EXPECT_EQ(capture.reports()[1].text.size(), size_t(job_report_max_chars) * 3);
  EXPECT_EQ(capture.reports()[1].text.substr(0, 3), "\xE3\x81\x82");
}

TEST_F(JobReportsTest, ReadingConsumesAndOtherJobsKeepTheirOwn)
{
  TestReports render, bake;
  BKE_report(&render.list, RPT_ERROR, "No camera");
  BKE_report(&bake.list, RPT_ERROR, "No UVs");
  /* Two jobs end in the same tick: each keeps its own. */
  const JobReportsCapture render_capture = capture_of(render.list);
  const JobReportsCapture bake_capture = capture_of(bake.list);
  job_reports_store(1, 100, &render_capture);
  job_reports_store(2, 100, &bake_capture);
  job_reports_store(1, 200, &bake_capture);

  Vector<JobReport> got = job_reports_take(1, 100);
  ASSERT_EQ(got.size(), 1);
  EXPECT_EQ(got[0].text, "No camera");
  EXPECT_TRUE(job_reports_take(1, 100).is_empty()); /* Consumed. */
  EXPECT_EQ(job_reports_take(2, 100)[0].text, "No UVs");
  EXPECT_EQ(job_reports_take(1, 200)[0].text, "No UVs");
  EXPECT_TRUE(job_reports_take(3, 100).is_empty());
}

TEST_F(JobReportsTest, AnEndWithoutReportsReplacesTheEarlierRun)
{
  TestReports first;
  BKE_report(&first.list, RPT_ERROR, "first run failed");
  const JobReportsCapture first_capture = capture_of(first.list);
  job_reports_store(1, 7, &first_capture);
  /* The same job's next run ends cleanly: a reader never gets the earlier run's reports. */
  job_reports_store(1, 7, nullptr);
  EXPECT_TRUE(job_reports_take(1, 7).is_empty());
  const JobReportsCapture empty;
  job_reports_store(1, 7, &first_capture);
  job_reports_store(1, 7, &empty);
  EXPECT_TRUE(job_reports_take(1, 7).is_empty());
}

TEST_F(JobReportsTest, KeepsAtMostTheNewestRecords)
{
  TestReports reports;
  BKE_report(&reports.list, RPT_ERROR, "failed");
  const JobReportsCapture capture = capture_of(reports.list);
  for (int i = 0; i < job_reports_records + 1; i++) {
    job_reports_store(1, uint32_t(i), &capture);
  }
  EXPECT_TRUE(job_reports_take(1, 0).is_empty()); /* The oldest was dropped. */
  for (int i = 1; i < job_reports_records + 1; i++) {
    EXPECT_EQ(job_reports_take(1, uint32_t(i)).size(), 1);
  }
  job_reports_store(1, 3, &capture);
  job_reports_clear();
  EXPECT_TRUE(job_reports_take(1, 3).is_empty());
}

static int count_of(const ReportList &list)
{
  int count = 0;
  for (const Report *report = static_cast<const Report *>(list.list.first); report;
       report = report->next)
  {
    count++;
  }
  return count;
}

static void count_moved(const Report & /*report*/, void *count)
{
  (*static_cast<int *>(count))++;
}

TEST_F(JobReportsTest, MoveWithCallbackSeesExactlyTheMovedReports)
{
  TestReports source, destination;
  BKE_report(&destination.list, RPT_ERROR, "already there");
  BKE_report(&source.list, RPT_WARNING, "one");
  BKE_report(&source.list, RPT_ERROR, "two");
  int count = 0;
  BKE_reports_move_to_reports_ex(&destination.list, &source.list, count_moved, &count);
  EXPECT_EQ(count, 2);
  EXPECT_TRUE(source.list.list.is_empty());
  EXPECT_EQ(count_of(destination.list), 3);
  BKE_reports_move_to_reports_ex(&destination.list, &source.list, nullptr, nullptr);
  EXPECT_EQ(count_of(destination.list), 3);
}

}  // namespace blender::wm::angelo::tests
