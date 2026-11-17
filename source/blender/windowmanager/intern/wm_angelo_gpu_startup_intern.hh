/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup wm
 *
 * Angelo: the record and the rule behind `WM_angelo_gpu_startup.hh` (`wm_angelo_gpu_startup.cc`).
 * Named apart from that public header by more than case: on a file system that ignores case an
 * include of one from this folder would find the other (`test_native_header_names.py`).
 */

#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

#ifndef WIN32
#  include <pthread.h>
#endif

#include "BLI_string_ref.hh"

namespace blender::wm::angelo {

/** What the record says about the last launch that set out to draw with Vulkan. */
enum class GPUStartupRecord : int8_t {
  /** No record, or an emptied one: no such launch, or it presented a frame or exited by itself. */
  None,
  /** Written by a launch before it touched the GPU: it is initializing the GPU, or is in its main
   * loop and has not presented a frame. A death here is counted. */
  Starting,
  /** The same launch while its start-up scripts and add-ons run. A death here is theirs. */
  StartingScripts,
  /** That launch's crash handler ran while a death was counted. Its process may still be alive
   * (the Windows crash dialog), but it no longer holds the record. */
  Crashed,
  /** A later launch found #Starting or #Crashed left behind: Vulkan did not start here. */
  Failed,
};

struct GPUStartupRecordData {
  GPUStartupRecord record = GPUStartupRecord::None;
  /** The process that wrote a starting record, for a person reading the file. */
  int64_t pid = 0;
};

/** What a launch does with the record it found. */
enum class GPUStartupAction : int8_t { Keep, Clear, WriteStarting, WriteFailed };

struct GPUStartupPlan {
  /** Draw this launch with OpenGL. */
  bool use_fallback = false;
  /** Tell the person: this is the launch that learned Vulkan did not start. */
  bool announce = false;
  /** The record says, or now says, that Vulkan failed: a preference file read later in this
   * launch does not bring Vulkan back. */
  bool failed = false;
  GPUStartupAction action = GPUStartupAction::Keep;
};

/** What a launch does with the record it found and holds: \a found is its content (whoever wrote
 * it no longer holds it), \a prefers_vulkan what the preferences ask for. */
GPUStartupPlan gpu_startup_plan(GPUStartupRecord found, bool prefers_vulkan);

/** What a launch does with a record it could read but not take (\a peeked): the file is held past
 * the wait, or cannot be opened for writing. A record that says crashed or failed is never held by
 * a live launch (one that watches rewrites it first), so whoever holds it (a child that outlived
 * its launch, another program) the evidence stands: OpenGL, and nothing is written. A starting
 * record says nothing here: its launch may be alive. */
GPUStartupPlan gpu_startup_plan_unheld(GPUStartupRecord peeked, bool prefers_vulkan);

/** The record as the text of its file, and back. Text that is not a record reads as no record. */
std::string gpu_startup_record_text(const GPUStartupRecordData &data);
GPUStartupRecordData gpu_startup_record_from_text(StringRef text);

/** The text of #GPUStartupRecord::Crashed with its line end, as the crash handler writes it. */
inline constexpr char GPU_STARTUP_TEXT_CRASHED[] = "vulkan crashed\n";
inline constexpr size_t GPU_STARTUP_TEXT_CRASHED_SIZE = sizeof(GPU_STARTUP_TEXT_CRASHED) - 1;

/** What the crash handler makes the record's whole content. */
struct GPUStartupCrashText {
  const char *text;
  size_t length;
};

/** A crash while start-up scripts run is the scripts' only on the main thread, where scripts and
 * add-ons run: the record is emptied. On any other thread it may be the GPU's (Vulkan's submission
 * thread and the driver's own threads work on while scripts run), so it counts like a crash at any
 * other time before the first frame. */
constexpr GPUStartupCrashText gpu_startup_crash_text(const bool in_scripts,
                                                     const bool on_main_thread)
{
  return (in_scripts && on_main_thread) ?
             GPUStartupCrashText{"", 0} :
             GPUStartupCrashText{GPU_STARTUP_TEXT_CRASHED, GPU_STARTUP_TEXT_CRASHED_SIZE};
}

/** A thread, noted so that a crash handler can ask whether it runs on it: both functions are only
 * what a handler may call. A thread never noted is not the current one. */
struct GPUStartupThread {
#ifdef WIN32
  unsigned long id = 0;
#else
  pthread_t thread{};
#endif
  bool noted = false;
};
GPUStartupThread gpu_startup_thread_current();
bool gpu_startup_thread_is_current(const GPUStartupThread &thread);

/** The record's file, opened for this process alone. */
struct GPUStartupFile {
#ifdef WIN32
  void *handle = nullptr;
#else
  int descriptor = -1;
#endif
  bool is_open() const;
};

enum class GPUStartupOpen : int8_t {
  /** This process holds the file; no other process can until it is closed or this process ends,
   * however it ends. */
  Opened,
  /** Another process holds it. */
  Held,
  /** It could not be opened for another reason: nothing is known. */
  Unknown,
};

/** Open \a filepath for this process alone, creating it empty when it is missing. */
GPUStartupOpen gpu_startup_file_open(const char *filepath, GPUStartupFile &r_file);
/** The record in an opened file (at most the first 64 bytes are read). */
GPUStartupRecordData gpu_startup_file_read(const GPUStartupFile &file);
/** Make \a data the file's whole content; #GPUStartupRecord::None empties it. */
bool gpu_startup_file_write(const GPUStartupFile &file, const GPUStartupRecordData &data);
/** Make \a length bytes of \a text the file's whole content. Only system calls on the open
 * file: also for the crash handler. */
bool gpu_startup_file_write_raw(const GPUStartupFile &file, const char *text, size_t length);
void gpu_startup_file_close(GPUStartupFile &file);
/** The record in \a filepath without holding it, for a file another process holds (no record
 * when it cannot be read). */
GPUStartupRecordData gpu_startup_file_peek(const char *filepath);

}  // namespace blender::wm::angelo
