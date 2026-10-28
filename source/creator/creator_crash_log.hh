/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup creator
 *
 * The crash log the crash handlers in `creator_signals.cc` write.
 *
 * A crash can happen with any lock held, the heap corrupted or the Python interpreter half way
 * through a call, so the log is written in two stages:
 *
 * - The **minimal record** (the version header, the signal or exception and the native stack) is
 *   written first, with system calls that take no lock and allocate nothing, straight to the file.
 * - The **full record** (the same plus the Info reports and the Python stack) is best-effort. It
 *   is written to a side file that replaces the minimal record only once it is complete; the
 *   reports are read only when their lock is free, Python only when this thread holds the GIL.
 *
 * The handlers bound both stages with a watchdog, so a stage that blocks ends the process instead
 * of hanging it, and a fault inside a stage takes the default action (`creator_signals.cc`).
 */

#pragma once

#ifndef WITH_PYTHON_MODULE

#  include <cstdio>

#  ifndef WIN32
#    include <csignal>
#  endif

namespace blender {

/** Seconds a crash handler may run before the watchdog ends the process. */
constexpr int CRASH_HANDLER_TIMEOUT_S = 10;

/**
 * Compute what the crash path must not compute after a crash (the version header).
 * Call once at startup, before installing the handlers.
 */
void crash_log_init();

#  ifndef WIN32
/**
 * Write the crash log for a POSIX signal: the minimal record, then the full record.
 * Called from the signal handler, at most once per process.
 */
void crash_log_write_posix(int signum, const siginfo_t *info, const void *ucontext);
#  else
/**
 * Write the crash log for a Windows exception (`os_info`, the `EXCEPTION_POINTERS`) or an abort
 * (`os_info` null): the minimal record, then (unless the stack overflowed) the full record.
 * Called from the unhandled-exception filter or the abort handler, at most once.
 */
void crash_log_write_windows(const char *filepath, const void *os_info, bool minimal_only);
#  endif

/**
 * Best-effort extras shared by both platforms: the Info reports (only when their lock is free).
 * Writes nothing when no window-manager exists.
 */
void crash_log_write_reports(FILE *fp);

}  // namespace blender

#endif /* WITH_PYTHON_MODULE */
