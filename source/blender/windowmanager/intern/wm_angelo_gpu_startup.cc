/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup wm
 *
 * Angelo: a Vulkan start that died before its first frame is not tried again.
 *
 * Blender 5.3 draws with Vulkan by default on Windows x64 and Linux and moves every earlier
 * preference file to it. Blender falls back to OpenGL when a device says it cannot do what Vulkan
 * needs (#GPU_backend_type_selection_detect). A driver that says it can and then crashes while
 * Blender starts gets no fallback: every launch dies the same way before any interface exists
 * that could offer OpenGL (upstream #166510, older Intel graphics after a driver update; Angelo's
 * crash dialog offers OpenGL only at a launch that gets as far as drawing it).
 *
 * So a window launch that sets out to draw with Vulkan leaves a record in the user's
 * configuration folder before it touches the GPU and keeps the file open for itself alone until
 * a frame has been presented. A launch that finds a record whose launch is gone knows Vulkan did
 * not start on this machine: it draws with OpenGL, sets the preference to OpenGL (Preferences
 * then shows what is used, and the usual save on exit keeps it), shows Blender's own "Failed to
 * load using Vulkan, using OpenGL instead" alert (#wm_test_gpu_backend_fallback) once, and marks
 * the record failed.
 *
 * Which deaths count:
 * - While the GPU is initialized (from the first preferences read to Python's start) and in the
 *   main loop before the first presented frame: the record says `vulkan starting`, and a launch
 *   that ends then, however it ends, is a failed Vulkan start.
 * - Not while start-up scripts run, from Python's start to the main loop's: add-ons loading (a
 *   crashing third-party add-on is the safe start's to handle, a long first start of a new version
 *   is not the GPU's) and scripts given on the command line (automation that ends the process).
 *   The record says `vulkan starting ... scripts` then. A crash in that time is the scripts'
 *   only on the main thread, where scripts and add-ons run. On any other thread it counts:
 *   Vulkan's submission thread and the driver's own threads work on while scripts run. Missed are
 *   a driver crash on the main thread in exactly that time, a death there that is no crash, and
 *   a crash after the first frame (Angelo's crash dialog offers OpenGL at the next launch,
 *   `crash_gpu.py`).
 * - An exit Blender makes itself after the GPU is initialized, with any status, is never counted.
 *
 * Who is gone:
 * - The launch that wrote the record holds its file open so that no other process can open it
 *   for writing. The operating system ends that when the process ends, however it ends. No
 *   process id is compared, so an id used again by another process cannot hide a failed start.
 * - A crashing process may live on (the Windows crash dialog is open until the person closes it,
 *   and its Restart starts the next launch meanwhile). Its crash handler therefore rewrites the
 *   record to `vulkan crashed` (or empties it, for the scripts' own crash) and lets go of the
 *   file before the dialog (#WM_angelo_gpu_startup_crashed), as the safe start marks its own
 *   record.
 * - A launch that finds the file held by another process waits a bounded time for it
 *   (#HELD_WAIT_MS: a killed process's handles go in the system's own time), unless the record
 *   says that launch is running its scripts. Still held, or not openable for any other reason,
 *   the file is read without being taken. A record that says crashed or failed is never held by
 *   a live launch (one that watches rewrites it first), so it stands whoever holds the file (a
 *   child that outlived its launch, another program) or keeps it from being opened: OpenGL, and
 *   nothing is written. Any other content is no evidence: a launch that is still starting, and
 *   this launch goes on unwatched.
 * - A start that completed empties its record before it lets go of the file. On Windows it then
 *   removes the file, which a launch that took it meanwhile refuses. Elsewhere the empty file
 *   stays: removing a name asks no holder.
 *
 * Afterwards:
 * - While the record says failed, every preference file read (settings imported from an earlier
 *   version are moved to Vulkan by versioning; factory preferences) is turned to OpenGL, in the
 *   launch that learned of the failure and in every later one. Only the launch that learned of
 *   it shows the alert.
 * - Only setting the backend preference to Vulkan (Preferences > System, or a script) removes
 *   the record; the next launch then tries Vulkan, watched as before.
 * - `--gpu-backend` on the command line is never overridden and leaves no record. Background
 *   launches present no frame and are not watched. Only where both backends are built (Windows,
 *   Linux); macOS has no backend to fall back to.
 * - A child made with `fork()` and no `exec` before the first frame keeps the file held after
 *   its parent died (`O_CLOEXEC` covers only `exec`; an add-on's `multiprocessing` forks on
 *   Linux). A parent that crashed has marked the record first, and that is read past the hold. A
 *   parent that died without its crash handler leaves `starting` held by the child: unwatched
 *   launches until the child ends.
 */

#include <atomic>
#include <optional>
#include <string>

#ifdef WIN32
#  include <windows.h>
#else
#  include <unistd.h>
#endif

#include "BLI_fileops.hh"
#include "BLI_path_utils.hh"
#include "BLI_string_ref.hh"
#include "BLI_time.hh"
#include "BLI_utildefines.hh"

#include "DNA_userdef_types.h"

#include "BKE_appdir.hh"
#include "BKE_global.hh"

#include "CLG_log.h"

#include "WM_angelo_gpu_startup.hh"
#include "wm_angelo_gpu_startup_intern.hh"

namespace blender {

static CLG_LogRef LOG = {"wm.gpu_startup"};

namespace wm::angelo {

/* The rule applies where a launch can draw with either backend. */
#if defined(WITH_VULKAN_BACKEND) && defined(WITH_OPENGL_BACKEND)
static const bool HAS_FALLBACK_BACKEND = true;
#else
static const bool HAS_FALLBACK_BACKEND = false;
#endif

static const char *RECORD_FILE = "angelo-gpu-startup.txt";
/** How long a launch waits for a file another process holds, as the safe start does
 * (`angelo_start_records.HELD_WAIT`), and how often it looks. */
static const int HELD_WAIT_MS = 2500;
static const int HELD_POLL_MS = 100;

/* -------------------------------------------------------------------- */
/** \name This Launch
 * \{ */

static int64_t process_id()
{
#ifdef WIN32
  return int64_t(GetCurrentProcessId());
#else
  return int64_t(getpid());
#endif
}

/** This launch's part in the record. */
struct GPUStartupState {
  bool seen_first_read = false;
  /** This launch wrote a starting record, holds its file and has not presented a frame. Whoever
   * takes it back (the first frame, an exit, the crash handler on any thread) writes and closes
   * the file, and nobody else does. */
  std::atomic<bool> watching = false;
  /** Start-up scripts run: a death on the main thread now is theirs. */
  std::atomic<bool> in_scripts = false;
  /** The thread that reads the preferences: start-up scripts and add-ons run on it. */
  GPUStartupThread main_thread;
  /** The GPU is initialized: an exit Blender makes from here on is its own. */
  bool gpu_initialized = false;
  /** The record says failed: a preference file that asks for Vulkan is turned to OpenGL. */
  bool record_failed = false;
  GPUStartupFile file;
};
static GPUStartupState g_state;

static std::optional<std::string> record_filepath(const bool create)
{
  const std::optional<std::string> folder = create ?
                                                BKE_appdir_folder_id_create(BLENDER_USER_CONFIG,
                                                                            nullptr) :
                                                BKE_appdir_folder_id(BLENDER_USER_CONFIG, nullptr);
  if (!folder) {
    return std::nullopt;
  }
  char filepath[FILE_MAX];
  BLI_path_join(filepath, sizeof(filepath), folder->c_str(), RECORD_FILE);
  return std::string(filepath);
}

static void use_opengl()
{
  U.gpu_backend = USER_GPU_BACKEND_OPENGL;
  U.runtime.is_dirty = true;
}

/** Remove a record file that has been emptied and let go of. Left behind it reads as no record. */
static void remove_emptied(const char *filepath)
{
#ifdef WIN32
  /* A launch that took the file meanwhile keeps it: its hold refuses the delete. */
  if (BLI_exists(filepath) && BLI_delete(filepath, false, false) != 0) {
    CLOG_INFO(&LOG, "The emptied start-up record could not be removed: %s", filepath);
  }
#else
  /* Left in place: `unlink` asks no holder. A launch that took the file in the moment since it was
   * let go would lose its record's name, and its death before a frame would leave nothing. */
  UNUSED_VARS(filepath);
#endif
}

/** Draw this launch with OpenGL, as \a plan says, and tell the person or the log why. */
static void use_fallback(const GPUStartupPlan &plan)
{
  if (!plan.use_fallback) {
    return;
  }
  use_opengl();
  if (plan.announce) {
    CLOG_WARN(&LOG, "The last start with Vulkan ended before its first frame: using OpenGL");
    G.f |= G_FLAG_GPU_BACKEND_FALLBACK;
  }
  else {
    CLOG_WARN(&LOG,
              "Vulkan did not start here earlier: using OpenGL until Vulkan is chosen in the "
              "Preferences");
  }
}

static void first_preferences_read()
{
  const bool prefers_vulkan = (U.gpu_backend == USER_GPU_BACKEND_VULKAN);
  const std::optional<std::string> filepath = record_filepath(prefers_vulkan);
  if (!filepath || (!prefers_vulkan && !BLI_exists(filepath->c_str()))) {
    /* Nothing to watch and no record to read. */
    return;
  }
  GPUStartupFile file;
  GPUStartupOpen opened = gpu_startup_file_open(filepath->c_str(), file);
  /* Held: by a launch running its scripts (no evidence, whatever becomes of it), or by one that
   * may be ending, whose hold goes in the system's own time. */
  for (int waited = 0;
       opened == GPUStartupOpen::Held && waited < HELD_WAIT_MS &&
       gpu_startup_file_peek(filepath->c_str()).record != GPUStartupRecord::StartingScripts;
       waited += HELD_POLL_MS)
  {
    BLI_time_sleep_ms(HELD_POLL_MS);
    opened = gpu_startup_file_open(filepath->c_str(), file);
  }
  if (opened != GPUStartupOpen::Opened) {
    /* Not taken. What the file says can still be read, and a failure it tells of stands whoever
     * holds it; nothing is written. */
    const GPUStartupPlan plan = gpu_startup_plan_unheld(
        gpu_startup_file_peek(filepath->c_str()).record, prefers_vulkan);
    g_state.record_failed = plan.failed;
    use_fallback(plan);
    if (plan.failed) {
      CLOG_WARN(&LOG, "The start-up record could not be taken: it is left as it is");
    }
    else if (opened == GPUStartupOpen::Held) {
      CLOG_WARN(&LOG, "Another launch is still starting with Vulkan: this start is not watched");
    }
    else {
      CLOG_WARN(&LOG, "The start-up record cannot be opened: this start is not watched");
    }
    return;
  }

  const GPUStartupRecordData found = gpu_startup_file_read(file);
  const GPUStartupPlan plan = gpu_startup_plan(found.record, prefers_vulkan);
  g_state.record_failed = plan.failed;
  use_fallback(plan);

  switch (plan.action) {
    case GPUStartupAction::Keep:
      gpu_startup_file_close(file);
      if (found.record == GPUStartupRecord::None) {
        remove_emptied(filepath->c_str());
      }
      break;
    case GPUStartupAction::Clear:
      gpu_startup_file_write(file, {});
      gpu_startup_file_close(file);
      remove_emptied(filepath->c_str());
      break;
    case GPUStartupAction::WriteStarting:
      if (gpu_startup_file_write(file, {GPUStartupRecord::Starting, process_id()})) {
        g_state.file = file;
        g_state.main_thread = gpu_startup_thread_current();
        g_state.watching = true;
      }
      else {
        gpu_startup_file_close(file);
      }
      break;
    case GPUStartupAction::WriteFailed:
      if (!gpu_startup_file_write(file, {GPUStartupRecord::Failed, 0})) {
        CLOG_WARN(&LOG, "The failed Vulkan start could not be recorded: %s", filepath->c_str());
      }
      gpu_startup_file_close(file);
      break;
  }
}

/** This start is over and was no failure: empty the record, let go of it, remove it. */
static void stop_watching()
{
  if (!g_state.watching.exchange(false)) {
    return;
  }
  /* Emptied while it is still held: a launch that opens it next never reads a start that looks
   * abandoned. */
  gpu_startup_file_write(g_state.file, {});
  gpu_startup_file_close(g_state.file);
  if (const std::optional<std::string> filepath = record_filepath(false)) {
    remove_emptied(filepath->c_str());
  }
}

/** Say in the record whether start-up scripts run now. */
static void set_in_scripts(const bool in_scripts)
{
  g_state.in_scripts = in_scripts;
  if (g_state.watching) {
    gpu_startup_file_write(
        g_state.file,
        {in_scripts ? GPUStartupRecord::StartingScripts : GPUStartupRecord::Starting,
         process_id()});
  }
}

}  // namespace wm::angelo

/** \} */

/* -------------------------------------------------------------------- */
/** \name What Blender Reports
 * \{ */

void WM_angelo_gpu_startup_preferences_read(const bool backend_from_command_line)
{
  using namespace wm::angelo;
  if (!HAS_FALLBACK_BACKEND) {
    return;
  }
  if (!g_state.seen_first_read) {
    g_state.seen_first_read = true;
    if (!backend_from_command_line && !G.background) {
      first_preferences_read();
    }
    return;
  }
  /* A preference file read later (settings imported from an earlier version, factory
   * preferences): a file is not the person choosing Vulkan. */
  if (g_state.record_failed && U.gpu_backend == USER_GPU_BACKEND_VULKAN) {
    CLOG_WARN(&LOG, "Vulkan did not start here earlier: the preferences read now keep OpenGL");
    use_opengl();
  }
}

void WM_angelo_gpu_startup_scripts_begin()
{
  /* The GPU is initialized: from here an exit is Blender's own. */
  wm::angelo::g_state.gpu_initialized = true;
  wm::angelo::set_in_scripts(true);
}

void WM_angelo_gpu_startup_main_loop_begin()
{
  wm::angelo::set_in_scripts(false);
}

void WM_angelo_gpu_startup_frame_presented()
{
  wm::angelo::stop_watching();
}

void WM_angelo_gpu_startup_exit()
{
  /* An exit Blender makes itself once the GPU is initialized (the person quits, a script ends
   * the process with any status) is not the GPU failing. One before that is: no window could be
   * made, or the platform is not supported. */
  if (wm::angelo::g_state.gpu_initialized) {
    wm::angelo::stop_watching();
  }
}

void WM_angelo_gpu_startup_backend_chosen()
{
  using namespace wm::angelo;
  if (!HAS_FALLBACK_BACKEND || G.background || U.gpu_backend != USER_GPU_BACKEND_VULKAN) {
    return;
  }
  /* The person asks for Vulkan again: the next launch tries it, watched like any other. */
  g_state.record_failed = false;
  if (g_state.watching) {
    /* This launch holds the record of its own start, which is not a failed one. Opening the file
     * a second time would, where locks belong to the process (`flock` over NFS), drop this
     * launch's hold when that second descriptor is closed. */
    return;
  }
  const std::optional<std::string> filepath = record_filepath(false);
  if (!filepath || !BLI_exists(filepath->c_str())) {
    return;
  }
  GPUStartupFile file;
  if (gpu_startup_file_open(filepath->c_str(), file) != GPUStartupOpen::Opened) {
    CLOG_WARN(&LOG,
              "The record of the failed Vulkan start cannot be opened, so the next start uses "
              "OpenGL again: %s",
              filepath->c_str());
    return;
  }
  const GPUStartupRecord found = gpu_startup_file_read(file).record;
  const bool failed = ELEM(found, GPUStartupRecord::Failed, GPUStartupRecord::Crashed);
  if (failed && !gpu_startup_file_write(file, {})) {
    CLOG_WARN(&LOG,
              "The record of the failed Vulkan start cannot be cleared, so the next start uses "
              "OpenGL again: %s",
              filepath->c_str());
  }
  gpu_startup_file_close(file);
  if (failed || found == GPUStartupRecord::None) {
    remove_emptied(filepath->c_str());
  }
}

void WM_angelo_gpu_startup_crashed()
{
  using namespace wm::angelo;
  /* Called from the crash handler: system calls on what is already set up, nothing else. The
   * process may live on (the Windows crash dialog), so it lets go of the file here; what it
   * leaves says whether this death counts. */
  if (!g_state.watching.exchange(false)) {
    return;
  }
  const GPUStartupCrashText left = gpu_startup_crash_text(
      g_state.in_scripts, gpu_startup_thread_is_current(g_state.main_thread));
  gpu_startup_file_write_raw(g_state.file, left.text, left.length);
  gpu_startup_file_close(g_state.file);
}

/** \} */

}  // namespace blender
