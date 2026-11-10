/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup wm
 *
 * Angelo: the store of ended job runs' own reports (`WM_angelo_job_reports.hh`). File-static and
 * main-thread only: job threads never reach it (they write their own report lists, which the main
 * thread copies from while it moves them).
 */

#include <cstring>

#include "BLI_assert.h"
#include "BLI_string_utf8.h"
#include "BLI_threads.h"

#include "WM_angelo_job_reports.hh"

namespace blender::wm::angelo {

struct JobReportsRecord {
  int job_type;
  uint32_t owner_session_uid;
  Vector<JobReport> reports;
};

/** Oldest first. A function-local static: built on first use, emptied at exit (#job_reports_clear). */
static Vector<JobReportsRecord> &records()
{
  static Vector<JobReportsRecord> stored;
  return stored;
}

bool job_report_type_kept(const int type)
{
  return (type & (RPT_ERROR_ALL | RPT_WARNING_ALL)) != 0;
}

/** `text` cut to #job_report_max_chars code points, never inside a UTF-8 sequence. */
static std::string clipped(const char *text)
{
  const size_t len = strlen(text);
  const int end = BLI_str_utf8_offset_from_index(text, len, job_report_max_chars);
  return std::string(text, size_t(end));
}

void JobReportsCapture::add(const Report &report)
{
  if (!job_report_type_kept(report.type)) {
    return;
  }
  const eReportType type = (report.type & RPT_ERROR_ALL) ? RPT_ERROR : RPT_WARNING;
  if (reports_.size() == job_reports_per_run) {
    /* Full. A WARNING never displaces an ERROR: the oldest WARNING gives way, or else a new
     * WARNING is not kept. A new ERROR among ERRORs only: the first stays (often the cause) and
     * the second gives way, so the latest (often what ended the job) are kept too. */
    int64_t drop = -1;
    for (const int64_t i : reports_.index_range()) {
      if (reports_[i].type == RPT_WARNING) {
        drop = i;
        break;
      }
    }
    if (drop == -1) {
      if (type == RPT_WARNING) {
        return;
      }
      drop = job_reports_per_run > 1 ? 1 : 0;
    }
    reports_.remove(drop);
  }
  const char *text = report.message_untranslated ? report.message_untranslated : report.message;
  reports_.append({type, clipped(text ? text : "")});
}

static int64_t record_find(const int job_type, const uint32_t owner_session_uid)
{
  const Vector<JobReportsRecord> &stored = records();
  for (const int64_t i : stored.index_range()) {
    if (stored[i].job_type == job_type && stored[i].owner_session_uid == owner_session_uid) {
      return i;
    }
  }
  return -1;
}

void job_reports_store(const int job_type,
                       const uint32_t owner_session_uid,
                       const JobReportsCapture *capture)
{
  BLI_assert(BLI_thread_is_main());
  Vector<JobReportsRecord> &stored = records();
  const int64_t earlier = record_find(job_type, owner_session_uid);
  if (earlier != -1) {
    stored.remove(earlier);
  }
  if (capture == nullptr || capture->is_empty()) {
    return;
  }
  if (stored.size() == job_reports_records) {
    stored.remove(0);
  }
  stored.append({job_type, owner_session_uid, Vector<JobReport>(capture->reports())});
}

Vector<JobReport> job_reports_take(const int job_type, const uint32_t owner_session_uid)
{
  BLI_assert(BLI_thread_is_main());
  Vector<JobReportsRecord> &stored = records();
  const int64_t found = record_find(job_type, owner_session_uid);
  if (found == -1) {
    return {};
  }
  Vector<JobReport> reports = std::move(stored[found].reports);
  stored.remove(found);
  return reports;
}

void job_reports_clear()
{
  records().clear_and_shrink();
}

}  // namespace blender::wm::angelo
