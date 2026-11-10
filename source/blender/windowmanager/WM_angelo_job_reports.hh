/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * \ingroup wm
 *
 * Angelo: a job's own ERROR and WARNING reports, kept when the job ends so the agent reads why
 * that job failed rather than whatever else reached the Info log meanwhile (a person's operator,
 * another script) or a list a fluid bake cleared at its start.
 *
 * `wm_jobs.cc` gathers one #JobReportsCapture per run of the job types it keeps (the Scene-owned
 * types of `_bpy._angelo_jobs`) from the reports that move from that job's lists into the window
 * manager's, and from what remains in them when it ends; at its end it stores the capture under
 * (job type, owning Scene's `session_uid`). Everything here runs on the main thread only.
 *
 * Bounds: at most #job_reports_per_run reports per run (when full, a WARNING never displaces an
 * ERROR: the oldest WARNING gives way, or a new WARNING is not kept; among ERRORs only, the first
 * stays and the second gives way), each at most #job_report_max_chars code points (as Blender's
 * UTF-8 decoder counts them; `_angelo_jobs` clips again after decoding), and at most
 * #job_reports_records ended runs (the oldest is dropped). Stored reports are freed when read,
 * dropped, erased when the next run of the same job is asked for, or at exit; a run that ends
 * after the next run was asked for (a restart) is not stored.
 */

#include <cstdint>
#include <string>

#include "BKE_report.hh"

#include "BLI_vector.hh"

namespace blender::wm::angelo {

/** At most this many reports are kept for one job run. */
inline constexpr int job_reports_per_run = 8;
/** Each report's text is clipped to this many code points (never inside a UTF-8 sequence). */
inline constexpr int job_report_max_chars = 500;
/** At most this many ended runs keep their reports until read. */
inline constexpr int job_reports_records = 8;

/** One ERROR or WARNING of a job run, as a reader that must not depend on the language reads it. */
struct JobReport {
  /** #RPT_ERROR (any #RPT_ERROR_ALL kind) or #RPT_WARNING. */
  eReportType type;
  /** #Report::message_untranslated when Blender translated the report, else its message. */
  std::string text;
};

/** The ERROR and WARNING reports of one job run, in the order they were reported. */
class JobReportsCapture {
  Vector<JobReport> reports_;

 public:
  /** Keep `report` when it is an ERROR or WARNING (bounded, see the file comment). */
  void add(const Report &report);
  Span<JobReport> reports() const
  {
    return reports_;
  }
  bool is_empty() const
  {
    return reports_.is_empty();
  }
};

/** Whether this report type is kept (ERROR of any kind, or WARNING). */
bool job_report_type_kept(int type);

/**
 * Store the reports of the run of `job_type` owned by the Scene with `owner_session_uid` that has
 * just ended, replacing any earlier run's of the same job: also when `capture` is null or empty,
 * so a reader never gets an older run's reports.
 */
void job_reports_store(int job_type, uint32_t owner_session_uid, const JobReportsCapture *capture);

/** Remove and return that job's last run's reports, oldest first; empty when none were kept. */
Vector<JobReport> job_reports_take(int job_type, uint32_t owner_session_uid);

/** Drop every stored run's reports. */
void job_reports_clear();

}  // namespace blender::wm::angelo
