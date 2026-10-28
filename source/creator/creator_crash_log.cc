/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup creator
 *
 * The two-stage crash log (see `creator_crash_log.hh`). Everything named `minimal` here runs
 * after a crash with no lock and no allocation; everything else is best-effort.
 */

#ifndef WITH_PYTHON_MODULE

#  include <cerrno>
#  include <cstdint>
#  include <cstring>
#  include <mutex>

#  ifdef WIN32
#    include <windows.h>
#  else
#    include <fcntl.h>
#    include <unistd.h>
#    if defined(HAVE_EXECINFO_H)
#      include <execinfo.h>
#    endif
#    if defined(__APPLE__)
#      include <sys/ucontext.h>
#    endif
#  endif

#  include "BLI_fileops.h"
#  include "BLI_listbase_iterator.hh"
#  include "BLI_path_utils.hh"
#  include "BLI_string.h"
#  include "BLI_system.h"

#  include "BKE_blender.hh" /* #BKE_blender_globals_crash_path_get. */
#  include "BKE_blender_version.h"
#  include "BKE_global.hh"
#  include "BKE_main.hh"
#  include "BKE_report.hh"
#  include "BKE_wm_runtime.hh"

#  ifdef WITH_PYTHON
#    include "BPY_extern_python.hh" /* #BPY_python_backtrace. */
#  endif

#  include "creator_crash_log.hh"
#  include "creator_intern.h"

namespace blender {

/* The crash path runs once per process (the handlers' re-entry guard), so its buffers are static:
 * nothing is allocated after a crash and a stack overflow leaves them reachable. */
static char crash_header[512];
static char crash_filepath_partial[FILE_MAX + 16];

void crash_log_init()
{
#  ifndef BUILD_DATE
  SNPRINTF(crash_header, "# " BLEND_VERSION_FMT ", Unknown revision\n", BLEND_VERSION_ARG);
#  else
  SNPRINTF(crash_header,
           "# " BLEND_VERSION_FMT ", Commit date: %s %s, Hash %s\n",
           BLEND_VERSION_ARG,
           build_commit_date,
           build_commit_time,
           build_hash);
#  endif
#  if !defined(WIN32) && defined(HAVE_EXECINFO_H)
  /* The first #backtrace may load the unwinder (glibc loads `libgcc_s`, allocating): do it now. */
  void *frames[1];
  backtrace(frames, 1);
#  endif
}

/** `filepath` + ".partial": the full record's side file, never matched by `*.crash.txt`. */
static bool crash_partial_path(const char *filepath)
{
  const size_t len = strlen(filepath);
  if (len + sizeof(".partial") > sizeof(crash_filepath_partial)) {
    return false;
  }
  memcpy(crash_filepath_partial, filepath, len);
  memcpy(crash_filepath_partial + len, ".partial", sizeof(".partial"));
  return true;
}

void crash_log_write_reports(FILE *fp)
{
  /* Might be called after WM/Main exit, so needs to be careful about nullptr-checking before
   * de-referencing. */
  wmWindowManager *wm = G_MAIN ? static_cast<wmWindowManager *>(G_MAIN->wm.first) : nullptr;
  if (wm == nullptr || wm->runtime == nullptr || wm->runtime->reports.lock == nullptr) {
    return;
  }
  ReportList &reports = wm->runtime->reports;
  /* Never wait: the crashed thread, or a thread stopped by the crash, may hold the lock. */
  if (!reports.lock->try_lock()) {
    fputs("# Info reports skipped: their lock is held\n", fp);
    return;
  }
  for (Report &report : reports.list) {
    fprintf(fp, "%s  # %s\n", report.message, report.typestr);
  }
  reports.lock->unlock();
}

#  ifndef WIN32

/* -------------------------------------------------------------------- */
/** \name POSIX: the minimal record
 *
 * Only `write(2)`, `open(2)` and #backtrace_symbols_fd (no allocation), with text formatted into
 * static buffers by hand (`snprintf` is not async-signal-safe).
 * \{ */

static constexpr int CRASH_FRAMES_MAX = 128;
static void *crash_frames[CRASH_FRAMES_MAX];
static char crash_signal_line[192];

static void crash_out(int fd, const char *text)
{
  size_t len = strlen(text);
  while (len > 0) {
    const ssize_t written = write(fd, text, len);
    if (written < 0 && errno == EINTR) {
      continue;
    }
    if (written <= 0) {
      return;
    }
    text += written;
    len -= size_t(written);
  }
}

/** Append `text` to `buf` (of `buf_size`, NUL-terminated), truncating. */
static void crash_append(char *buf, size_t buf_size, const char *text)
{
  size_t len = strlen(buf);
  while (*text && len + 1 < buf_size) {
    buf[len++] = *text++;
  }
  buf[len] = '\0';
}

/** Append `value` in hexadecimal ("0x", at least `digits` digits) or decimal (`digits` 0). */
static void crash_append_number(char *buf, size_t buf_size, uintptr_t value, int digits)
{
  const uintptr_t base = digits ? 16 : 10;
  char text[32];
  char *p = text + sizeof(text) - 1;
  *p = '\0';
  do {
    *--p = "0123456789abcdef"[value % base];
    value /= base;
    digits--;
  } while ((value != 0 || digits > 0) && p > text + 2);
  if (base == 16) {
    *--p = 'x';
    *--p = '0';
  }
  crash_append(buf, buf_size, p);
}

static void crash_append_int(char *buf, size_t buf_size, long value)
{
  if (value < 0) {
    crash_append(buf, buf_size, "-");
    crash_append_number(buf, buf_size, uintptr_t(-(value + 1)) + 1, 0);
    return;
  }
  crash_append_number(buf, buf_size, uintptr_t(value), 0);
}

static const char *crash_signal_name(int signum)
{
  switch (signum) {
    case SIGSEGV:
      return "SIGSEGV";
    case SIGBUS:
      return "SIGBUS";
    case SIGILL:
      return "SIGILL";
    case SIGFPE:
      return "SIGFPE";
    case SIGTRAP:
      return "SIGTRAP";
    case SIGABRT:
      return "SIGABRT";
  }
  return "signal";
}

/** "# Signal 11 (SIGSEGV), code 2, fault address 0x...": what crashed, before the stack. */
static void crash_signal_line_build(int signum, const siginfo_t *info)
{
  char *line = crash_signal_line;
  const size_t size = sizeof(crash_signal_line);
  line[0] = '\0';
  crash_append(line, size, "# Signal ");
  crash_append_int(line, size, signum);
  crash_append(line, size, " (");
  crash_append(line, size, crash_signal_name(signum));
  crash_append(line, size, ")");
  if (info) {
    crash_append(line, size, ", code ");
    crash_append_int(line, size, info->si_code);
    crash_append(line, size, ", fault address ");
    crash_append_number(line, size, uintptr_t(info->si_addr), 2 * sizeof(uintptr_t));
  }
  crash_append(line, size, "\n");
}

/** The crashed thread's stack, the faulting instruction first. */
static int crash_frames_capture(const void *ucontext)
{
  int num = 0;
#    if defined(__APPLE__)
  /* macOS' #backtrace starts above the faulting function when it has no frame of its own (a
   * leaf), so name the faulting instruction from the interrupted context first. */
  if (ucontext) {
    const ucontext_t *uc = static_cast<const ucontext_t *>(ucontext);
#      if defined(__arm64__) || defined(__aarch64__)
    crash_frames[num++] = reinterpret_cast<void *>(uc->uc_mcontext->__ss.__pc);
#      elif defined(__x86_64__)
    crash_frames[num++] = reinterpret_cast<void *>(uc->uc_mcontext->__ss.__rip);
#      endif
  }
#    else
  (void)ucontext; /* glibc's #backtrace unwinds through the signal frame itself. */
#    endif
#    if defined(HAVE_EXECINFO_H)
  num += backtrace(crash_frames + num, CRASH_FRAMES_MAX - num);
#    endif
  return num;
}

static void crash_frames_write(int fd, int frames_num)
{
#    if defined(HAVE_EXECINFO_H)
  backtrace_symbols_fd(crash_frames, frames_num, fd);
#    else
  (void)fd;
  (void)frames_num;
#    endif
}

/**
 * Header and signal first (no stack walk can delay them), then the stack. Returns the number of
 * frames, which the full record writes again.
 */
static int crash_write_minimal(int fd, const void *ucontext)
{
  crash_out(fd, crash_header);
  crash_out(fd, crash_signal_line);
  crash_out(fd, "\n# backtrace\n");
  const int frames_num = crash_frames_capture(ucontext);
  crash_frames_write(fd, frames_num);
  return frames_num;
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name POSIX: the full record (best-effort)
 * \{ */

/**
 * Header, signal, Info reports, then the stack and the Python stack: the layout `crash_log.py`
 * reads (the header line, then everything after the last `# backtrace`). Written to a side file
 * that replaces the minimal record only when complete, so a stage that faults or blocks here
 * leaves the minimal record intact.
 */
static void crash_write_full(const char *filepath, int frames_num)
{
  if (!crash_partial_path(filepath)) {
    return;
  }
  FILE *fp = BLI_fopen(crash_filepath_partial, "wb");
  if (fp == nullptr) {
    return;
  }
  fputs(crash_header, fp);
  fputs(crash_signal_line, fp);
  crash_log_write_reports(fp);
  fputs("\n# backtrace\n", fp);
  fflush(fp);
  crash_frames_write(fileno(fp), frames_num);
#    ifdef WITH_PYTHON
  BPY_python_backtrace(fp);
#    endif
  const bool complete = (fflush(fp) == 0) && !ferror(fp);
  fclose(fp);
  if (complete) {
    BLI_rename_overwrite(crash_filepath_partial, filepath);
  }
  else {
    unlink(crash_filepath_partial);
  }
}

/** \} */

void crash_log_write_posix(int signum, const siginfo_t *info, const void *ucontext)
{
  static char filepath[FILE_MAX];
  crash_signal_line_build(signum, info);

  if (app_state.signal.use_console_crash_handler) {
    const int frames_num = crash_write_minimal(STDERR_FILENO, ucontext);
    /* The extras last, so a held `stderr` lock cannot hide the stack; the stack again after the
     * reports, so no report text follows the last `# backtrace` (as in the full record). */
    crash_log_write_reports(stderr);
    fputs("\n# backtrace\n", stderr);
    fflush(stderr);
    crash_frames_write(STDERR_FILENO, frames_num);
#    ifdef WITH_PYTHON
    BPY_python_backtrace(stderr);
#    endif
    fflush(stderr);
    return;
  }

  BKE_blender_globals_crash_path_get(filepath);
  const int fd = open(filepath, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
  if (fd == -1) {
    crash_out(STDERR_FILENO, "Unable to save '");
    crash_out(STDERR_FILENO, filepath);
    crash_out(STDERR_FILENO, "', falling back to console\n");
    crash_write_minimal(STDERR_FILENO, ucontext);
    return;
  }
  const int frames_num = crash_write_minimal(fd, ucontext);
  close(fd);
  /* After the record: a full `stdout` pipe nobody reads cannot keep it from the file. */
  crash_out(STDOUT_FILENO, "Writing: ");
  crash_out(STDOUT_FILENO, filepath);
  crash_out(STDOUT_FILENO, "\n");

  crash_write_full(filepath, frames_num);
}

#  else /* WIN32 */

/* -------------------------------------------------------------------- */
/** \name Windows
 *
 * The filter runs on the crashed thread, which may hold the heap, CRT or loader lock: the minimal
 * record uses only Win32 file calls and static buffers (little stack: an overflow leaves little);
 * the rest is bounded by the watchdog. `os_info` is null for an abort.
 * \{ */

static wchar_t crash_filepath_utf16[FILE_MAX];

static void crash_out_win(HANDLE handle, const char *text)
{
  DWORD written = 0;
  WriteFile(handle, text, DWORD(strlen(text)), &written, nullptr);
}

static void crash_write_minimal_win(HANDLE handle, const void *os_info)
{
  crash_out_win(handle, crash_header);
  if (os_info == nullptr) {
    crash_out_win(handle, "# Signal 22 (SIGABRT)\n");
  }
  crash_out_win(handle, "\n# backtrace\n");
  BLI_windows_exception_write_record(handle, os_info);
}

/**
 * Header, Info reports, then the exception record (none for an abort), the stack walk (of the
 * calling thread for an abort) and the Python stack.
 */
static void crash_write_full_win(FILE *fp, const void *os_info)
{
  fputs(crash_header, fp);
  crash_log_write_reports(fp);
  fputs("\n# backtrace\n", fp);
  BLI_system_backtrace_with_os_info(fp, os_info);
#    ifdef WITH_PYTHON
  BPY_python_backtrace(fp);
#    endif
}

void crash_log_write_windows(const char *filepath, const void *os_info, const bool minimal_only)
{
  const HANDLE console = GetStdHandle(STD_ERROR_HANDLE);
  if (app_state.signal.use_console_crash_handler) {
    crash_write_minimal_win(console, os_info);
    if (!minimal_only) {
      crash_write_full_win(stderr, os_info);
      fflush(stderr);
    }
    return;
  }

  HANDLE file = INVALID_HANDLE_VALUE;
  if (MultiByteToWideChar(CP_UTF8, 0, filepath, -1, crash_filepath_utf16, FILE_MAX) > 0) {
    file = CreateFileW(crash_filepath_utf16,
                       GENERIC_WRITE,
                       FILE_SHARE_READ,
                       nullptr,
                       CREATE_ALWAYS,
                       FILE_ATTRIBUTE_NORMAL,
                       nullptr);
  }
  if (file == INVALID_HANDLE_VALUE) {
    crash_out_win(console, "Unable to save '");
    crash_out_win(console, filepath);
    crash_out_win(console, "', falling back to console\n");
    crash_write_minimal_win(console, os_info);
    return;
  }
  crash_write_minimal_win(file, os_info);
  CloseHandle(file);
  /* After the record: a full `stdout` pipe nobody reads cannot keep it from the file. */
  const HANDLE out = GetStdHandle(STD_OUTPUT_HANDLE);
  crash_out_win(out, "Writing: ");
  crash_out_win(out, filepath);
  crash_out_win(out, "\n");
  if (minimal_only || !crash_partial_path(filepath)) {
    return;
  }

  /* The full record replaces the minimal one only when complete (see #crash_write_full). */
  FILE *fp = BLI_fopen(crash_filepath_partial, "wb");
  if (fp == nullptr) {
    return;
  }
  crash_write_full_win(fp, os_info);
  const bool complete = (fflush(fp) == 0) && !ferror(fp);
  fclose(fp);
  if (complete) {
    BLI_rename_overwrite(crash_filepath_partial, filepath);
  }
  else {
    BLI_delete(crash_filepath_partial, false, false);
  }
}

/** \} */

#  endif /* WIN32 */

}  // namespace blender

#endif /* WITH_PYTHON_MODULE */
