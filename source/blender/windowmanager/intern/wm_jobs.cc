/* SPDX-FileCopyrightText: 2009 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup wm
 *
 * Threaded job manager (high level job access).
 */

#include <cstring>

#include "DNA_ID.h"
#include "DNA_windowmanager_types.h"

#include "MEM_guardedalloc.h"

#include "BLI_build_config.h"
#include "BLI_listbase.h"
#include "BLI_string.h"
#include "BLI_threads.h"
#include "BLI_time.h"
#include "BLI_utildefines.h"

#if OS_WINDOWS
#  include "BLI_winstuff.h"
#endif

#include "BKE_context.hh"
#include "BKE_global.hh"
#include "BKE_report.hh"
#include "BKE_undo_system.hh"

#include "SEQ_prefetch.hh"

#include "WM_angelo_job_reports.hh"
#include "WM_api.hh"
#include "WM_types.hh"
#include "wm.hh"

#ifdef WITH_PYTHON
#  include "BPY_extern.hh"
#endif
#include "wm_event_types.hh"

namespace blender {

/*
 * Add new job
 * - register in WM
 * - configure callbacks
 *
 * Start or re-run job
 * - if job running
 *   - signal job to end
 *   - add timer notifier to verify when it has ended, to start it
 * - else
 *   - start job
 *   - add timer notifier to handle progress
 *
 * Stop job
 * - signal job to end
 * on end, job will tag itself as sleeping
 *
 * Remove job
 * - signal job to end
 * on end, job will remove itself
 *
 * When job is done:
 * - it puts timer to sleep (or removes?)
 */

struct ThreadSlot;

struct wmJob {
  wmJob *next, *prev;

  /** Job originating from, keep track of this when deleting windows. */
  wmWindow *win;

  /** Should store entirely owned context, for start, update, free. */
  void *customdata;
  /**
   * To prevent cpu overhead, use this one which only gets called when job really starts.
   * Executed in main thread.
   */
  void (*initjob)(void *);
  /**
   * This performs the actual parallel work.
   * Executed in worker thread(s).
   */
  wm_jobs_start_callback startjob;
  /**
   * Called if thread defines so (see `do_update` flag), and max once per timer step.
   * Executed in main thread.
   */
  void (*update)(void *);
  /**
   * Optional, called for each timer step while the job is running. Can be used to send messages to
   * the running job. For example, online asset library loading uses this to get status updates
   * from the downloader to the loading job, like that it's done downloading some files that are
   * now ready to be processed.
   *
   * Should be used for messaging to the job only, must _not_ be used to modify the running job,
   * like changing the timer, replacing the custom data pointer, etc.
   *
   * Executed in the main thread.
   */
  void (*timer_step)(void *);
  /**
   * Free callback (typically for customdata).
   * Executed in main thread.
   */
  void (*free)(void *);
  /**
   * Called when job is stopped.
   * Executed in main thread.
   */
  void (*endjob)(void *);
  /**
   * Called when job is stopped normally, i.e. by simply completing the startjob function.
   * Executed in main thread.
   */
  void (*completed)(void *);
  /**
   * Called when job is stopped abnormally, i.e. when stop=true but ready=false.
   * Executed in main thread.
   */
  void (*canceled)(void *);

  /** Running jobs each have their own timer. */
  double time_step;
  wmTimer *wt;
  /** Only start job after specified time delay. */
  double start_delay_time;
  /** The notifier event timers should send. */
  uint note, endnote;

  /* Internal. */
  const void *owner;
  eWM_JobFlag flag;
  bool suspended, running, ready;
  eWM_JobType job_type;

  /** Data shared with the worker code, so can be accessed and edited from several threads. */
  wmJobWorkerStatus worker_status;

  /** For display in header, identification. */
  char name[128];

  /** Once running, we store this separately. */
  void *run_customdata;
  void (*run_free)(void *);

  /** We use BLI_threads api, but per job only 1 thread runs. */
  ListBaseT<ThreadSlot> threads;

  double start_time;

  /**
   * Ticket mutex for main thread locking while some job accesses
   * data that the main thread might modify at the same time.
   */
  TicketMutex *main_thread_mutex;

  /* Angelo: this job's own reports (#WM_angelo_job_reports.hh). Main thread only: the job's
   * callbacks get only their custom data and #worker_status, never these. */
  /** This job's type keeps its runs' reports; its owner is then a Scene. */
  bool angelo_keeps_reports;
  /** The owning Scene's #ID::session_uid, read when the job was made (never reused, kept across
   * memfile Undo). */
  uint32_t angelo_owner_session_uid;
  /** The operator report list this run writes on its thread (#WM_jobs_reports_attach), or null. */
  ReportList *angelo_attached_reports;
  /** The list attached while a run still runs (a restart): the next run's, set as its attached list
   * when that run is asked to start (#WM_jobs_start), like `customdata` becoming `run_customdata`. */
  ReportList *angelo_pending_reports;
  /** This run's ERROR and WARNING copies, made at the first one. */
  wm::angelo::JobReportsCapture *angelo_capture;
};

/**
 * Angelo: the job types whose runs keep their own reports: the table of `_bpy._angelo_jobs`
 * (`bpy_angelo_jobs.cc`), each made with a Scene as its owner.
 */
static bool wm_job_angelo_keeps_reports(const eWM_JobType job_type)
{
  return ELEM(job_type,
              WM_JOB_TYPE_RENDER,
              WM_JOB_TYPE_OBJECT_BAKE,
              WM_JOB_TYPE_POINTCACHE,
              WM_JOB_TYPE_BAKE_GEOMETRY_NODES,
              WM_JOB_TYPE_USD_EXPORT,
              WM_JOB_TYPE_ALEMBIC_EXPORT,
              WM_JOB_TYPE_OBJECT_SIM_FLUID);
}

/** Angelo: the job whose end callbacks run now (#wm_job_end), or null. Main thread only. */
static wmJob *angelo_ending_job = nullptr;

/**
 * Angelo: the job whose `startjob` runs on this thread (#do_job_thread), or null. A task the job
 * hands to other threads (a task pool) does not see it.
 */
static thread_local wmJob *angelo_thread_job = nullptr;

ReportList *wm_jobs_thread_reports()
{
  return angelo_thread_job ? angelo_thread_job->worker_status.reports : nullptr;
}

void wm_jobs_angelo_report_moved(const Report &report, void *wm_job_v)
{
  wmJob *wm_job = static_cast<wmJob *>(wm_job_v);
  if (!wm::angelo::job_report_type_kept(report.type)) {
    return;
  }
  if (wm_job->angelo_capture == nullptr) {
    wm_job->angelo_capture = MEM_new<wm::angelo::JobReportsCapture>(__func__);
  }
  wm_job->angelo_capture->add(report);
}

wmJob *wm_jobs_angelo_reports_owner(const wmWindowManager *wm, const ReportList *reports)
{
  if (wm == nullptr || !BLI_thread_is_main()) {
    return nullptr;
  }
  for (wmJob &wm_job : wm->runtime->jobs) {
    if (wm_job.angelo_keeps_reports &&
        (wm_job.worker_status.reports == reports || wm_job.angelo_attached_reports == reports))
    {
      return &wm_job;
    }
  }
  /* Reports its end callbacks put into the Info log (a fluid bake's #WM_global_reportf). */
  if (angelo_ending_job && angelo_ending_job->angelo_keeps_reports) {
    return angelo_ending_job;
  }
  return nullptr;
}

/**
 * Angelo: the jobs with an attached operator list, so an operator being freed finds them without
 * walking any window manager (one may be being freed itself). Main thread only.
 */
static Vector<wmJob *> &angelo_attached_jobs()
{
  static Vector<wmJob *> jobs;
  return jobs;
}

/** List `wm_job` in #angelo_attached_jobs exactly while it has an attached or pending list. */
static void wm_job_angelo_attached_update(wmJob *wm_job)
{
  Vector<wmJob *> &jobs = angelo_attached_jobs();
  const bool needed = wm_job->angelo_attached_reports || wm_job->angelo_pending_reports;
  const bool listed = jobs.contains(wm_job);
  if (needed && !listed) {
    jobs.append(wm_job);
  }
  else if (!needed && listed) {
    jobs.remove_first_occurrence_and_reorder(wm_job);
    if (jobs.is_empty()) {
      jobs.clear_and_shrink();
    }
  }
}

static void wm_job_angelo_reports_unattach(wmJob *wm_job)
{
  wm_job->angelo_attached_reports = nullptr;
  wm_job_angelo_attached_update(wm_job);
}

void wm_jobs_angelo_reports_detach(const ReportList *reports)
{
  if (reports == nullptr) {
    return;
  }
  for (wmJob *wm_job : Vector<wmJob *>(angelo_attached_jobs())) {
    if (wm_job->angelo_attached_reports == reports) {
      wm_job->angelo_attached_reports = nullptr;
    }
    if (wm_job->angelo_pending_reports == reports) {
      wm_job->angelo_pending_reports = nullptr;
    }
    wm_job_angelo_attached_update(wm_job);
  }
}

void WM_jobs_reports_attach(wmJob *wm_job, ReportList *reports)
{
  BLI_assert(BLI_thread_is_main());
  if (wm_job->running) {
    /* A restart (#WM_jobs_customdata_set on a running job): the running run keeps its own list,
     * this one is the next run's. */
    wm_job->angelo_pending_reports = reports;
  }
  else {
    wm_job->angelo_attached_reports = reports;
  }
  wm_job_angelo_attached_update(wm_job);
}

/**
 * Angelo: at a run's end, once its data is freed (`run_free`, which may still report: a viewport
 * render's write tasks), copy what is left in the operator's list (its `startjob` has returned),
 * then store the run's reports, replacing the previous run's, also when there are none. When a
 * new run was asked for meanwhile (\a restart_asked: `customdata` was set, read before a kill
 * frees it), this run is no longer the latest, so nothing is stored: its record was erased when it
 * started.
 */
static void wm_job_angelo_reports_end(wmJob *wm_job, const bool restart_asked)
{
  if (!wm_job->angelo_keeps_reports) {
    return;
  }
  if (ReportList *attached = wm_job->angelo_attached_reports) {
    BKE_reports_lock(attached);
    for (const Report &report : attached->list) {
      wm_jobs_angelo_report_moved(report, wm_job);
    }
    BKE_reports_unlock(attached);
    wm_job_angelo_reports_unattach(wm_job);
  }
  if (!restart_asked) {
    wm::angelo::job_reports_store(
        wm_job->job_type, wm_job->angelo_owner_session_uid, wm_job->angelo_capture);
  }
  MEM_delete(wm_job->angelo_capture);
  wm_job->angelo_capture = nullptr;
}

/* Main thread locking. */

void WM_job_main_thread_lock_acquire(wmJob *wm_job)
{
  BLI_ticket_mutex_lock(wm_job->main_thread_mutex);
}

void WM_job_main_thread_lock_release(wmJob *wm_job)
{
  BLI_ticket_mutex_unlock(wm_job->main_thread_mutex);
}

static void wm_job_main_thread_yield(wmJob *wm_job)
{
  /* Unlock and lock the ticket mutex. because it's a fair mutex any job that
   * is waiting to acquire the lock will get it first, before we can lock. */
  BLI_ticket_mutex_unlock(wm_job->main_thread_mutex);
  BLI_ticket_mutex_lock(wm_job->main_thread_mutex);
}

static void wm_jobs_update_qos(const wmWindowManager *wm)
{
  /* A QoS API is currently only available for Windows. */
#if OS_WINDOWS
  for (wmJob &wm_job : wm->runtime->jobs) {
    if (wm_job.flag & WM_JOB_PRIORITY) {
      BLI_windows_process_set_qos(QoSMode::HIGH, QoSPrecedence::JOB);
      return;
    }
  }

  BLI_windows_process_set_qos(QoSMode::DEFAULT, QoSPrecedence::JOB);
#else
  UNUSED_VARS(wm);
#endif
}
/**
 * Finds if type or owner, compare for it, otherwise any matching job.
 */
static wmJob *wm_job_find(const wmWindowManager *wm, const void *owner, const eWM_JobType job_type)
{
  if (owner && (job_type != WM_JOB_TYPE_ANY)) {
    for (wmJob &wm_job : wm->runtime->jobs) {
      if (wm_job.owner == owner && wm_job.job_type == job_type) {
        return &wm_job;
      }
    }
  }
  else if (owner) {
    for (wmJob &wm_job : wm->runtime->jobs) {
      if (wm_job.owner == owner) {
        return &wm_job;
      }
    }
  }
  else if (job_type != WM_JOB_TYPE_ANY) {
    for (wmJob &wm_job : wm->runtime->jobs) {
      if (wm_job.job_type == job_type) {
        return &wm_job;
      }
    }
  }

  return nullptr;
}

/* ******************* public API ***************** */

wmJob *WM_jobs_get(wmWindowManager *wm,
                   wmWindow *win,
                   const void *owner,
                   const char *name,
                   const eWM_JobFlag flag,
                   const eWM_JobType job_type)
{
  wmJob *wm_job = wm_job_find(wm, owner, job_type);

  if (wm_job == nullptr) {
    wm_job = MEM_new_zeroed<wmJob>("new job");

    BLI_addtail(&wm->runtime->jobs, wm_job);
    wm_job->win = win;
    wm_job->owner = owner;
    wm_job->flag = flag;
    wm_job->job_type = job_type;
    STRNCPY(wm_job->name, name);

    wm_job->main_thread_mutex = BLI_ticket_mutex_alloc();
    WM_job_main_thread_lock_acquire(wm_job);

    wm_job->worker_status.reports = MEM_new<ReportList>(__func__);
    BKE_reports_init(wm_job->worker_status.reports, RPT_STORE | RPT_PRINT);
    BKE_report_print_level_set(wm_job->worker_status.reports, RPT_WARNING);

    if (owner && wm_job_angelo_keeps_reports(job_type)) {
      const ID *owner_id = static_cast<const ID *>(owner);
      BLI_assert(GS(owner_id->name) == ID_SCE);
      wm_job->angelo_keeps_reports = true;
      wm_job->angelo_owner_session_uid = owner_id->session_uid;
    }

    wm_jobs_update_qos(wm);
  }
  /* Else: a running job, be careful. */

  /* Prevent creating a job with an invalid type. */
  BLI_assert(wm_job->job_type != WM_JOB_TYPE_ANY);

  return wm_job;
}

bool WM_jobs_test(const wmWindowManager *wm, const void *owner, int job_type)
{
  /* Job can be running or about to run (suspended). */
  for (wmJob &wm_job : wm->runtime->jobs) {
    if (wm_job.owner != owner) {
      continue;
    }

    if (!ELEM(job_type, WM_JOB_TYPE_ANY, wm_job.job_type)) {
      continue;
    }

    if ((wm_job.flag & WM_JOB_PROGRESS) && (wm_job.running || wm_job.suspended)) {
      return true;
    }
  }

  return false;
}

float WM_jobs_progress(const wmWindowManager *wm, const void *owner)
{
  const wmJob *wm_job = wm_job_find(wm, owner, WM_JOB_TYPE_ANY);

  if (wm_job && wm_job->flag & WM_JOB_PROGRESS) {
    return wm_job->worker_status.progress;
  }

  return 0.0;
}

float WM_jobs_progress_type(const wmWindowManager *wm, const void *owner, int job_type)
{
  const wmJob *wm_job = wm_job_find(wm, owner, eWM_JobType(job_type));

  if (wm_job && wm_job->flag & WM_JOB_PROGRESS) {
    return wm_job->worker_status.progress;
  }

  return 0.0;
}

static void wm_jobs_update_progress_bars(wmWindowManager *wm)
{
  float total_progress = 0.0f;
  float jobs_progress = 0;

  for (wmJob &wm_job : wm->runtime->jobs) {
    if (wm_job.threads.first && !wm_job.ready) {
      if (wm_job.flag & WM_JOB_PROGRESS) {
        /* Accumulate global progress for running jobs. */
        jobs_progress++;
        total_progress += wm_job.worker_status.progress;
      }
    }
  }

  /* If there are running jobs, set the global progress indicator. */
  if (jobs_progress > 0) {
    float progress = total_progress / jobs_progress;

    for (wmWindow &win : wm->windows) {
      WM_progress_set(&win, progress);
    }
  }
  else {
    for (wmWindow &win : wm->windows) {
      WM_progress_clear(&win);
    }
  }
}

double WM_jobs_starttime(const wmWindowManager *wm, const void *owner)
{
  const wmJob *wm_job = wm_job_find(wm, owner, WM_JOB_TYPE_ANY);

  if (wm_job && wm_job->flag & WM_JOB_PROGRESS) {
    return wm_job->start_time;
  }

  return 0;
}

const char *WM_jobs_name(const wmWindowManager *wm, const void *owner)
{
  wmJob *wm_job = wm_job_find(wm, owner, WM_JOB_TYPE_ANY);

  if (wm_job) {
    return wm_job->name;
  }

  return nullptr;
}

void *WM_jobs_customdata_from_type(wmWindowManager *wm, const void *owner, int job_type)
{
  wmJob *wm_job = wm_job_find(wm, owner, eWM_JobType(job_type));

  if (wm_job) {
    return WM_jobs_customdata_get(wm_job);
  }

  return nullptr;
}

bool WM_jobs_is_running(const wmJob *wm_job)
{
  return wm_job->running;
}

bool WM_jobs_is_stopped(const wmWindowManager *wm, const void *owner)
{
  wmJob *wm_job = wm_job_find(wm, owner, WM_JOB_TYPE_ANY);
  return wm_job ? wm_job->worker_status.stop : true; /* XXX to be redesigned properly. */
}

void *WM_jobs_customdata_get(wmJob *wm_job)
{
  if (!wm_job->customdata) {
    return wm_job->run_customdata;
  }
  return wm_job->customdata;
}

void WM_jobs_customdata_set(wmJob *wm_job, void *customdata, void (*free)(void *customdata))
{
  /* Pending job? just free. */
  if (wm_job->customdata) {
    wm_job->free(wm_job->customdata);
  }

  wm_job->customdata = customdata;
  wm_job->free = free;

  if (wm_job->running) {
    /* Signal job to end. */
    wm_job->worker_status.stop = true;
  }
}

void WM_jobs_timer(
    wmJob *wm_job, double time_step, uint note, uint endnote, void (*timer_step)(void *))
{
  wm_job->time_step = time_step;
  wm_job->note = note;
  wm_job->endnote = endnote;
  wm_job->timer_step = timer_step;
}

void WM_jobs_delay_start(wmJob *wm_job, double delay_time)
{
  wm_job->start_delay_time = delay_time;
}

void WM_jobs_callbacks(wmJob *wm_job,
                       wm_jobs_start_callback startjob,
                       void (*initjob)(void *),
                       void (*update)(void *),
                       void (*endjob)(void *))
{
  WM_jobs_callbacks_ex(wm_job, startjob, initjob, update, endjob, nullptr, nullptr);
}

void WM_jobs_callbacks_ex(wmJob *wm_job,
                          wm_jobs_start_callback startjob,
                          void (*initjob)(void *),
                          void (*update)(void *),
                          void (*endjob)(void *),
                          void (*completed)(void *),
                          void (*canceled)(void *))
{
  wm_job->startjob = startjob;
  wm_job->initjob = initjob;
  wm_job->update = update;
  wm_job->endjob = endjob;
  wm_job->completed = completed;
  wm_job->canceled = canceled;
}

static void wm_jobs_reports_update(wmWindowManager *wm, wmJob *wm_job)
{
  WM_reports_from_reports_move(wm, wm_job->worker_status.reports);
}

static void *do_job_thread(void *job_v)
{
  wmJob *wm_job = static_cast<wmJob *>(job_v);

  {
    /* Angelo: this thread runs `wm_job` until `startjob` returns (#wm_jobs_thread_reports). */
    struct ThreadJobScope {
      explicit ThreadJobScope(wmJob *job)
      {
        angelo_thread_job = job;
      }
      ~ThreadJobScope()
      {
        angelo_thread_job = nullptr;
      }
    } scope(wm_job);
    wm_job->startjob(wm_job->run_customdata, &wm_job->worker_status);
  }
  wm_job->ready = true;

  return nullptr;
}

/* Don't allow same startjob to be executed twice. */
static void wm_jobs_test_suspend_stop(wmWindowManager *wm, wmJob *test)
{
  if (test->job_type == WM_JOB_TYPE_RENDER_PREVIEW &&
      BKE_undosys_restore_phase() == UndoRestorePhase::Preparing)
  {
    /* Keep the normal timer retry. A later MemFile replacement cancels these previews
     * before their borrowed document and UI owners can be destroyed. */
    test->suspended = true;
    return;
  }
  bool suspend = false;

  /* Job added with suspend flag, we wait 1 timer step before activating it. */
  if (test->start_delay_time > 0.0) {
    suspend = true;
    test->start_delay_time = 0.0;
  }
  else {
    /* Check other jobs. */
    for (wmJob &wm_job : wm->runtime->jobs) {
      /* Obvious case, no test needed. */
      if (&wm_job == test || !wm_job.running) {
        continue;
      }

      /* If new job is not render, then check for same job type. */
      if (0 == (test->flag & WM_JOB_EXCL_RENDER)) {
        if (wm_job.job_type != test->job_type) {
          continue;
        }
      }

      /* If new job is render, any render job should be stopped. */
      if (test->flag & WM_JOB_EXCL_RENDER) {
        if (0 == (wm_job.flag & WM_JOB_EXCL_RENDER)) {
          continue;
        }
      }

      suspend = true;

      /* If this job has higher priority, stop others. */
      if (test->flag & WM_JOB_PRIORITY) {
        wm_job.worker_status.stop = true;
        // printf("job stopped: %s\n", wm_job->name);
      }
    }
  }

  /* Possible suspend ourselves, waiting for other jobs, or de-suspend. */
  test->suspended = suspend;
#if 0
  if (suspend) {
    printf("job suspended: %s\n", test->name);
  }
#endif
}

void WM_jobs_start(wmWindowManager *wm, wmJob *wm_job)
{
  if (wm_job->running) {
    /* Signal job to end and restart. */
    wm_job->worker_status.stop = true;
    // printf("job started a running job, ending... %s\n", wm_job->name);
  }
  else {

    if (wm_job->customdata && wm_job->startjob) {
      /* Angelo: a new run is asked for. The previous run's reports are no longer the latest, so a
       * reader never takes them for this run's (#WM_angelo_job_reports.hh; a running run's record
       * was erased when it started), and a list attached during a restart becomes this run's. */
      if (wm_job->angelo_keeps_reports) {
        wm::angelo::job_reports_store(wm_job->job_type, wm_job->angelo_owner_session_uid, nullptr);
      }
      if (ReportList *pending = wm_job->angelo_pending_reports) {
        wm_job->angelo_pending_reports = nullptr;
        wm_job->angelo_attached_reports = pending;
        wm_job_angelo_attached_update(wm_job);
      }
      const double time_step = (wm_job->start_delay_time > 0.0) ? wm_job->start_delay_time :
                                                                  wm_job->time_step;

      wm_jobs_test_suspend_stop(wm, wm_job);

      if (wm_job->suspended == false) {
        /* Copy to ensure proper free in end. */
        wm_job->run_customdata = wm_job->customdata;
        wm_job->run_free = wm_job->free;
        wm_job->free = nullptr;
        wm_job->customdata = nullptr;
        wm_job->running = true;

        if (wm_job->initjob) {
          wm_job->initjob(wm_job->run_customdata);
        }

        wm_job->worker_status.stop = false;
        wm_job->ready = false;
        wm_job->worker_status.progress = 0.0;

        // printf("job started: %s\n", wm_job->name);

        BLI_threadpool_init(&wm_job->threads, do_job_thread, 1);
        BLI_threadpool_insert(&wm_job->threads, wm_job);
      }

      /* Restarted job has timer already. */
      if (wm_job->wt && (wm_job->wt->time_step > time_step)) {
        WM_event_timer_remove(wm, wm_job->win, wm_job->wt);
        wm_job->wt = WM_event_timer_add(wm, wm_job->win, TIMERJOBS, time_step);
      }
      if (wm_job->wt == nullptr) {
        wm_job->wt = WM_event_timer_add(wm, wm_job->win, TIMERJOBS, time_step);
      }

      wm_job->start_time = BLI_time_now_seconds();
    }
    else {
      printf("job fails, not initialized\n");
    }
  }
}

static void wm_job_end(wmWindowManager *wm, wmJob *wm_job)
{
  BLI_assert_msg(BLI_thread_is_main(), "wm_job_end should only be called from the main thread");
  /* Angelo: reports the end callbacks move into the Info log are this job's
   * (#wm_jobs_angelo_reports_owner); the run's reports are stored once its data is freed
   * (#wm_job_free_run). Restored after: an end callback may end another job. */
  wmJob *ending_outer = angelo_ending_job;
  angelo_ending_job = wm_job;
  if (wm_job->endjob) {
    wm_job->endjob(wm_job->run_customdata);
  }

  /* Do the final callback based on whether the job was run to completion or not.
   * Not all jobs have the same way of signaling cancellation (i.e. rendering stops when
   * `G.is_break == true`, but doesn't set any wm_job properties to cancel the WM job). */
  const bool was_canceled = wm_job->worker_status.stop || G.is_break;
  void (*final_callback)(void *) = (wm_job->ready && !was_canceled) ? wm_job->completed :
                                                                      wm_job->canceled;
  if (final_callback) {
    final_callback(wm_job->run_customdata);
  }

  /* Ensure all reports have been moved to WM. */
  wm_jobs_reports_update(wm, wm_job);

  angelo_ending_job = ending_outer;
}

/**
 * Free a run's data as part of its end, then store the run's reports (#wm_job_angelo_reports_end):
 * \a run_free may still write to the job's own lists (a viewport render's write tasks report into
 * its attached operator list). Unlike the end callbacks, it is not the ending job: a report it
 * puts straight into the Info log (a Python frame-change handler) is not the job's.
 */
static void wm_job_free_run(wmWindowManager *wm, wmJob *wm_job, const bool restart_asked)
{
  if (wm_job->run_free) {
    wm_job->run_free(wm_job->run_customdata);
  }
  wm_jobs_reports_update(wm, wm_job);
  wm_job_angelo_reports_end(wm_job, restart_asked);
}

static void wm_job_free(wmWindowManager *wm, wmJob *wm_job)
{
  BLI_remlink(&wm->runtime->jobs, wm_job);
  WM_job_main_thread_lock_release(wm_job);
  BLI_ticket_mutex_free(wm_job->main_thread_mutex);

  BLI_assert(wm_job->worker_status.reports->list.is_empty());
  BKE_reports_free(wm_job->worker_status.reports);
  MEM_delete(wm_job->worker_status.reports);
  MEM_delete(wm_job->angelo_capture);
  wm_job->angelo_attached_reports = nullptr;
  wm_job->angelo_pending_reports = nullptr;
  wm_job_angelo_attached_update(wm_job);
  MEM_delete(wm_job);

  wm_jobs_update_qos(wm);
}

/* Stop job, end thread, free data completely. */
static void wm_jobs_kill_job(wmWindowManager *wm, wmJob *wm_job)
{
  bool update_progress = (wm_job->flag & WM_JOB_PROGRESS) != 0;

  if (wm_job->running) {
    /* Signal job to end. */
    wm_job->worker_status.stop = true;

    WM_job_main_thread_lock_release(wm_job);
    /* A kill reached from Python (a file read, an RNA function, an operator called from a script)
     * holds the GIL, while an ending job thread can need it: an icon preview freeing its depsgraph
     * invalidates Python references. Joining it with the GIL held never returns. */
#ifdef WITH_PYTHON
    BPy_BEGIN_ALLOW_THREADS;
#endif
    BLI_threadpool_end(&wm_job->threads);
#ifdef WITH_PYTHON
    BPy_END_ALLOW_THREADS;
#endif
    WM_job_main_thread_lock_acquire(wm_job);
    wm_job_end(wm, wm_job);
  }

  if (wm_job->wt) {
    WM_event_timer_remove(wm, wm_job->win, wm_job->wt);
  }
  /* Angelo: a new run was asked for (a restart), read before its data is freed. */
  const bool restart_asked = wm_job->customdata != nullptr;
  if (wm_job->customdata) {
    wm_job->free(wm_job->customdata);
  }
  if (wm_job->run_customdata) {
    wm_job_free_run(wm, wm_job, restart_asked);
  }

  /* Remove wm_job. */
  wm_job_free(wm, wm_job);

  /* Update progress bars in windows. */
  if (update_progress) {
    wm_jobs_update_progress_bars(wm);
  }
}

void WM_jobs_kill_all(wmWindowManager *wm)
{
  wmJob *wm_job;

  while ((wm_job = static_cast<wmJob *>(wm->runtime->jobs.first))) {
    wm_jobs_kill_job(wm, wm_job);
  }

  /* This job will be automatically restarted. */
  seq::prefetch_stop_all();
}

void WM_jobs_kill_all_except(wmWindowManager *wm, const void *owner)
{
  for (wmJob &wm_job : wm->runtime->jobs.items_mutable()) {
    if (wm_job.owner != owner) {
      wm_jobs_kill_job(wm, &wm_job);
    }
  }
}

void WM_jobs_kill_type(wmWindowManager *wm, const void *owner, int job_type)
{
  BLI_assert(job_type != WM_JOB_TYPE_ANY);

  for (wmJob &wm_job : wm->runtime->jobs.items_mutable()) {
    if (owner && wm_job.owner != owner) {
      continue;
    }

    if (wm_job.job_type == job_type) {
      wm_jobs_kill_job(wm, &wm_job);
    }
  }
}

void WM_jobs_kill_all_from_owner(wmWindowManager *wm, const void *owner)
{
  for (wmJob &wm_job : wm->runtime->jobs.items_mutable()) {
    if (wm_job.owner == owner) {
      wm_jobs_kill_job(wm, &wm_job);
    }
  }
}

void WM_jobs_stop_type(wmWindowManager *wm, const void *owner, eWM_JobType job_type)
{
  BLI_assert(job_type != WM_JOB_TYPE_ANY);

  for (wmJob &wm_job : wm->runtime->jobs) {
    if (owner && wm_job.owner != owner) {
      continue;
    }
    if (wm_job.job_type == job_type) {
      if (wm_job.running) {
        wm_job.worker_status.stop = true;
      }
    }
  }
}

void WM_jobs_stop_all_from_owner(wmWindowManager *wm, const void *owner)
{
  for (wmJob &wm_job : wm->runtime->jobs) {
    if (wm_job.owner == owner) {
      if (wm_job.running) {
        wm_job.worker_status.stop = true;
      }
    }
  }
}

void wm_jobs_timer_end(wmWindowManager *wm, wmTimer *wt)
{
  wmJob *wm_job = static_cast<wmJob *>(BLI_findptr(&wm->runtime->jobs, wt, offsetof(wmJob, wt)));
  if (wm_job) {
    wm_jobs_kill_job(wm, wm_job);
  }
}

static void wm_job_update(wmWindowManager *wm, wmJob &job)
{
  if (job.update) {
    job.update(job.run_customdata);
  }

  if (job.note) {
    WM_event_add_notifier_ex(wm, job.win, job.note, nullptr);
  }

  if (job.flag & WM_JOB_PROGRESS) {
    WM_event_add_notifier_ex(wm, job.win, NC_WM | ND_JOB, nullptr);
  }

  job.worker_status.do_update = false;
}

void wm_jobs_timer(wmWindowManager *wm, wmTimer *wt)
{
  wmJob *wm_job = static_cast<wmJob *>(BLI_findptr(&wm->runtime->jobs, wt, offsetof(wmJob, wt)));

  if (wm_job) {
    /* Running threads. */
    if (wm_job->threads.first) {
      /* Let threads get temporary lock over main thread if needed. */
      wm_job_main_thread_yield(wm_job);

      if (wm_job->timer_step) {
        wm_job->timer_step(wm_job->run_customdata);
      }

      if (wm_job->worker_status.do_update) {
        wm_job_update(wm, *wm_job);
      }
    }
    else if (wm_job->suspended) {
      WM_jobs_start(wm, wm_job);
    }

    /* Move pending reports generated by the worker thread to the WM main list. */
    if (wm_job) {
      wm_jobs_reports_update(wm, wm_job);
    }
  }

  /* Update progress bars in windows. */
  wm_jobs_update_progress_bars(wm);
}

void wm_jobs_handle_finished(const bContext *C)
{
  wmWindowManager *wm = CTX_wm_manager(C);
  for (wmJob &job : wm->runtime->jobs.items_reversed_mutable()) {
    if (!job.threads.first) {
      continue;
    }

    /* Let threads get temporary lock over main thread if needed. */
    wm_job_main_thread_yield(&job);

    /* Job not yet finished. */
    if (!job.ready) {
      continue;
    }

    wm_job_update(wm, job);
    wm_job_end(wm, &job);

    /* Free owned data. Angelo: then store the run's own reports. */
    wm_job_free_run(wm, &job, job.customdata != nullptr);
    job.run_customdata = nullptr;
    job.run_free = nullptr;

    if (G.debug & G_DEBUG_JOBS) {
      printf(
          "Job '%s' finished in %f seconds\n", job.name, BLI_time_now_seconds() - job.start_time);
    }

    job.running = false;

    WM_job_main_thread_lock_release(&job);
    BLI_threadpool_end(&job.threads);
    WM_job_main_thread_lock_acquire(&job);

    if (job.endnote) {
      WM_event_add_notifier_ex(wm, job.win, job.endnote, nullptr);
    }

    WM_event_add_notifier_ex(wm, job.win, NC_WM | ND_JOB, nullptr);

    /* New job added for wm_job? */
    if (job.customdata) {
      // printf("job restarted with new data %s\n", job.name);
      WM_jobs_start(wm, &job);
    }
    else {
      WM_event_timer_remove(wm, job.win, job.wt);
      job.wt = nullptr;

      /* Remove wm_job. */
      wm_job_free(wm, &job);
    }
  }

  /* Update progress bars in windows. */
  wm_jobs_update_progress_bars(wm);
}

bool WM_jobs_has_pending_or_running(const wmWindowManager *wm)
{
  for (const wmJob &job : wm->runtime->jobs) {
    if (job.running || job.customdata || job.run_customdata) {
      return true;
    }
  }
  return false;
}

bool WM_jobs_has_running(const wmWindowManager *wm)
{
  for (const wmJob &wm_job : wm->runtime->jobs) {
    if (wm_job.running) {
      return true;
    }
  }

  return false;
}

bool WM_jobs_has_running_type(const wmWindowManager *wm, int job_type)
{
  for (wmJob &wm_job : wm->runtime->jobs) {
    if (wm_job.running && wm_job.job_type == job_type) {
      return true;
    }
  }
  return false;
}

bool WM_jobs_has_running(const wmWindowManager *window_manager,
                         const void *owner,
                         const eWM_JobType type,
                         const eWM_JobFlag exclude_flags)
{
  for (const wmJob &job : window_manager->runtime->jobs) {
    if (owner && job.owner != owner) {
      continue;
    }

    if (!ELEM(type, WM_JOB_TYPE_ANY, job.job_type)) {
      continue;
    }

    if (job.flag & exclude_flags) {
      continue;
    }

    if (job.running || job.suspended) {
      return true;
    }
  }

  return false;
}

}  // namespace blender
