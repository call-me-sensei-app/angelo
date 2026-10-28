/* SPDX-FileCopyrightText: 2023 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup creator
 */

#ifndef WITH_PYTHON_MODULE

#  include <atomic>
#  include <cerrno>
#  include <cstdlib>
#  include <string>

#  if defined(__linux__) && defined(__GNUC__)
#    ifndef _GNU_SOURCE
#      define _GNU_SOURCE
#    endif
#    include <cfenv>
#  endif

#  if (defined(__APPLE__) && (defined(__i386__) || defined(__x86_64__)))
#    define OSX_SSE_FPE
#    include <xmmintrin.h>
#  endif

#  ifdef WIN32
#    include <float.h>
#    include <windows.h>

#    include "BLI_winstuff.h"

#    include "GPU_platform.hh"
#  endif

#  include "BLI_fileops.h"
#  include "BLI_path_utils.hh"
#  include "BLI_string.h"
#  include "BLI_system.h"
#  include BLI_SYSTEM_PID_H

#  include "BKE_appdir.hh" /* #BKE_tempdir_session_purge. */
#  include "BKE_blender.hh"
#  include "BKE_blender_version.h"
#  include "BKE_global.hh"
#  include "BKE_main.hh"

#  include <csignal>

#  ifndef WIN32
#    include <pthread.h>
#    include <sys/mman.h>
#    include <unistd.h>
#  endif

#  include "creator_crash_log.hh"
#  include "creator_intern.h" /* Own include. */

namespace blender {

#  if defined(__linux__) || defined(_WIN32) || defined(OSX_SSE_FPE)
/**
 * Set breakpoints here when running in debug mode, useful to catch floating point errors.
 */
static void sig_handle_fpe(int /*sig*/)
{
  fprintf(stderr, "debug: SIGFPE trapped\n");
}
#  endif

/* Handling `Ctrl-C` event in the console. */
static void sig_handle_blender_esc(int sig)
{
  /* Forces render loop to read queue, not sure if its needed. */
  G.is_break = true;

  if (sig == 2) {
    static int count = 0;
    if (count) {
      printf("\nBlender killed\n");
      exit(2);
    }
    printf("\nSent an internal break event. Press ^C again to kill Blender\n");
    count++;
  }
}

/* -------------------------------------------------------------------- */
/** \name Crash handlers
 *
 * A crash always writes its log and always ends the process (`creator_crash_log.hh` has the log's
 * two stages):
 * - One thread at a time: the first crash claims the handler with an atomic owner. A fault inside
 *   the handler (the owner crashing again) ends the process at once with the default action;
 *   another thread's crash waits for the owner to end the process. A `std::call_once` here would
 *   block forever on a fault inside the handler.
 * - A watchdog ends the process with the default action if the handler has not after
 *   #CRASH_HANDLER_TIMEOUT_S seconds (a lock held by a stopped thread, a corrupted heap).
 * - POSIX: installed with `sigaction` for every crash signal on an alternate stack, so a stack
 *   overflow still reaches the handler. With `SA_NODEFER`, a fault inside the handler re-enters
 *   it and ends the process: a blocked synchronous signal hangs the process on macOS.
 * - An abort is a crash too (an uncaught C++ exception, a heap check, a fatal Python error): with
 *   both the crash and the abort handler on, it writes the log the same way.
 * \{ */

#  ifndef WIN32

static std::atomic<int> crash_signum{0};
static std::atomic<pthread_t> crash_owner{};

/** End the process with the default action of `signum` (core dump, crash report, exit status). */
static void crash_terminate_default(int signum)
{
  struct sigaction action = {};
  action.sa_handler = SIG_DFL;
  sigemptyset(&action.sa_mask);
  sigaction(signum, &action, nullptr);
  sigset_t mask;
  sigemptyset(&mask);
  sigaddset(&mask, signum);
  pthread_sigmask(SIG_UNBLOCK, &mask, nullptr);
  raise(signum);
  /* Only reached for a signal whose default action does not end the process. */
  _exit(128 + signum);
}

/** Claim the crash handler for this thread; never returns when another crash claimed it. */
static void crash_handler_claim(int signum)
{
  int owner_signum = 0;
  if (crash_signum.compare_exchange_strong(owner_signum, signum)) {
    crash_owner.store(pthread_self());
    return;
  }
  if (pthread_equal(crash_owner.load(), pthread_self())) {
    /* A fault (or abort) inside the handler: what it wrote so far is the log; the process ends
     * with the signal that crashed it. */
    crash_terminate_default(owner_signum);
  }
  /* Another thread is writing the log: it ends the process, or the watchdog does. */
  for (;;) {
    pause();
  }
}

static void crash_watchdog_fn(int /*signum*/)
{
  crash_terminate_default(crash_signum.load());
}

/** End the process after #CRASH_HANDLER_TIMEOUT_S seconds, whatever the handler is blocked on. */
static void crash_watchdog_arm()
{
  struct sigaction action = {};
  action.sa_handler = crash_watchdog_fn;
  action.sa_flags = SA_ONSTACK;
  sigemptyset(&action.sa_mask);
  sigaction(SIGALRM, &action, nullptr);
  /* This thread at least can take it, even if every other thread blocks it. */
  sigset_t mask;
  sigemptyset(&mask);
  sigaddset(&mask, SIGALRM);
  pthread_sigmask(SIG_UNBLOCK, &mask, nullptr);
  alarm(CRASH_HANDLER_TIMEOUT_S);
}

static void sig_cleanup_and_terminate(int signum)
{
  /* Delete content of temp directory: last, best-effort, under the watchdog. */
  BKE_tempdir_session_purge();

  /* Really crash. */
  crash_terminate_default(signum);
}

/** The signals that mean the process crashed (SIGABRT too, when the abort handler is on). */
static const int crash_signals[] = {SIGSEGV, SIGBUS, SIGILL, SIGFPE, SIGTRAP};

static void sig_handle_abort(int signum);

static void sig_handle_crash_fn(int signum, siginfo_t *info, void *ucontext)
{
  if (signum == SIGABRT && info && info->si_pid != getpid()) {
    /* Sent by another process: not a crash of this one (upstream: purge and keep running). */
    sig_handle_abort(signum);
    return;
  }
  crash_handler_claim(signum);
  crash_watchdog_arm();
  /* A fault while the log is written must reach the handler at once (`abort()` blocks the other
   * signals on macOS, and a blocked synchronous signal hangs there). */
  sigset_t mask;
  sigemptyset(&mask);
  for (const int crash_signal : crash_signals) {
    sigaddset(&mask, crash_signal);
  }
  pthread_sigmask(SIG_UNBLOCK, &mask, nullptr);
  crash_log_write_posix(signum, info, ucontext);
  sig_cleanup_and_terminate(signum);
}

/**
 * Give the main thread its own stack for the handler (a stack overflow leaves none). An alternate
 * stack is per thread: a stack overflow on another thread still ends the process, without a log.
 */
static void crash_altstack_install()
{
  stack_t current = {};
  if (sigaltstack(nullptr, &current) == 0 && !(current.ss_flags & SS_DISABLE)) {
    return; /* Someone else's, keep it. */
  }
  const size_t page = size_t(sysconf(_SC_PAGESIZE));
  const size_t size = 256 * 1024;
  void *memory = mmap(nullptr, size + page, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
  if (memory == MAP_FAILED) {
    return;
  }
  /* A guard page below it, so overflowing the handler's own stack faults. */
  mprotect(memory, page, PROT_NONE);
  stack_t stack = {};
  stack.ss_sp = static_cast<char *>(memory) + page;
  stack.ss_size = size;
  if (sigaltstack(&stack, nullptr) != 0) {
    munmap(memory, size + page);
  }
}

static void crash_handler_install(int signum)
{
  struct sigaction previous = {};
  if (sigaction(signum, nullptr, &previous) != 0) {
    return;
  }
  if ((previous.sa_flags & SA_SIGINFO) || previous.sa_handler != SIG_DFL) {
    return; /* Installed on purpose (`--debug-fpe`, a sanitizer) or ignored: keep it. */
  }
  struct sigaction action = {};
  action.sa_sigaction = sig_handle_crash_fn;
  action.sa_flags = SA_SIGINFO | SA_ONSTACK | SA_NODEFER;
  sigemptyset(&action.sa_mask);
  sigaction(signum, &action, nullptr);
}

#  else /* WIN32 */

static std::atomic<DWORD> crash_owner_thread{0};
static std::atomic<DWORD> crash_exit_code{0};
static HANDLE crash_watchdog_arm_event = nullptr;
static HANDLE crash_watchdog_stand_down_event = nullptr;

/**
 * The watchdog is a thread started with the handler (starting one after a crash may need the
 * loader lock the crashed thread holds). Armed, it ends the process after
 * #CRASH_HANDLER_TIMEOUT_S seconds unless told to stand down (the crash dialog is up, waiting for
 * the person).
 */
static DWORD WINAPI crash_watchdog_main(LPVOID /*parameter*/)
{
  for (;;) {
    WaitForSingleObject(crash_watchdog_arm_event, INFINITE);
    if (WaitForSingleObject(crash_watchdog_stand_down_event, CRASH_HANDLER_TIMEOUT_S * 1000) ==
        WAIT_TIMEOUT)
    {
      TerminateProcess(GetCurrentProcess(), crash_exit_code.load());
    }
  }
}

static void crash_watchdog_start()
{
  crash_watchdog_arm_event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
  crash_watchdog_stand_down_event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
  if (crash_watchdog_arm_event && crash_watchdog_stand_down_event) {
    HANDLE thread = CreateThread(nullptr,
                                 64 * 1024,
                                 crash_watchdog_main,
                                 nullptr,
                                 STACK_SIZE_PARAM_IS_A_RESERVATION,
                                 nullptr);
    if (thread) {
      CloseHandle(thread);
    }
  }
}

static void crash_watchdog_arm()
{
  if (crash_watchdog_arm_event) {
    SetEvent(crash_watchdog_arm_event);
  }
}

static void crash_watchdog_stand_down()
{
  if (crash_watchdog_stand_down_event) {
    SetEvent(crash_watchdog_stand_down_event);
  }
}

/** Claim the crash handler for this thread; never returns when another crash claimed it. */
static void crash_handler_claim(const DWORD exit_code)
{
  const DWORD self = GetCurrentThreadId();
  DWORD owner = 0;
  if (!crash_owner_thread.compare_exchange_strong(owner, self)) {
    if (owner == self) {
      /* A fault (or abort) inside the handler: what it wrote so far is the log. */
      TerminateProcess(GetCurrentProcess(), crash_exit_code.load());
    }
    /* Another thread is writing the log: it ends the process, or the watchdog does. */
    Sleep(INFINITE);
  }
  crash_exit_code.store(exit_code);
  crash_watchdog_arm();
}

static void sig_cleanup_and_terminate(int signum)
{
  /* Delete content of temp directory. */
  BKE_tempdir_session_purge();

  /* Really crash. */
  signal(signum, SIG_DFL);
  TerminateProcess(GetCurrentProcess(), signum);
}

extern LONG WINAPI windows_exception_handler(EXCEPTION_POINTERS *ExceptionInfo)
{
  const DWORD code = ExceptionInfo->ExceptionRecord->ExceptionCode;
  crash_handler_claim(code);

  /* If this is a stack overflow then we can't walk the stack: only the minimal record (exception,
   * address, module) is written, from static buffers. */
  const bool stack_overflow = (code == EXCEPTION_STACK_OVERFLOW);
  static char filepath_crashlog[FILE_MAX];
  BKE_blender_globals_crash_path_get(filepath_crashlog);
  crash_log_write_windows(filepath_crashlog, ExceptionInfo, stack_overflow);
  BLI_windows_exception_print_message(ExceptionInfo);

  if (stack_overflow) {
    /* No stack left to purge the temporary folder with. */
    TerminateProcess(GetCurrentProcess(), SIGSEGV);
  }

  /* Disable popup in background mode to avoid blocking automation.
   * (e.g., when used by a render farm; see #142314). */
  if ((!G.background) && (!app_state.signal.use_console_crash_handler)) {
    std::string version;
#    ifndef BUILD_DATE
    const char *build_hash = G_MAIN ? G_MAIN->build_hash : "unknown";
    version = std::string(BKE_blender_version_string()) + ", hash: `" + build_hash + "`";
#    else
    version = std::string(BKE_blender_version_string()) + ", Commit date: " + build_commit_date +
              " " + build_commit_time + ", hash: `" + build_hash + "`";
#    endif

    /* The dialog gets its own budget: the watchdog starts again for it, stands down once it is up
     * (never while it cannot appear) and covers the clean-up after it closes. */
    crash_watchdog_stand_down();
    crash_watchdog_arm();
    BLI_windows_exception_show_dialog(filepath_crashlog,
                                      G.filepath_last_blend,
                                      GPU_platform_gpu_name(),
                                      version.c_str(),
                                      crash_watchdog_stand_down);
    crash_watchdog_arm();

    sig_cleanup_and_terminate(SIGSEGV);
  }

  /* Not the system's exit path: its DLL clean-up can wait on threads the crash stopped. */
  TerminateProcess(GetCurrentProcess(), code);
  return EXCEPTION_EXECUTE_HANDLER;
}

#  endif /* WIN32 */

/** \} */

static void sig_handle_abort(int /*signum*/)
{
#  ifdef WIN32
  if (app_state.signal.use_crash_handler) {
    /* The CRT would end the process after this handler without the exception filter (and through
     * Windows Error Reporting): log it here, one crash at a time, bounded by the watchdog, then
     * end with abort's exit code 3. */
    crash_handler_claim(3);
    static char filepath_crashlog[FILE_MAX];
    BKE_blender_globals_crash_path_get(filepath_crashlog);
    crash_log_write_windows(filepath_crashlog, nullptr, false);
    BKE_tempdir_session_purge();
    TerminateProcess(GetCurrentProcess(), 3);
  }
#  endif
  /* Delete content of temp directory. */
  BKE_tempdir_session_purge();
}

void main_signal_setup()
{
  if (app_state.signal.use_crash_handler) {
    crash_log_init();
#  ifdef WIN32
    crash_watchdog_start();
    SetUnhandledExceptionFilter(windows_exception_handler);
#  else
    /* After parsing arguments. */
    crash_altstack_install();
    for (const int signum : crash_signals) {
      crash_handler_install(signum);
    }
#  endif
  }

#  ifdef WIN32
  /* Prevent any error mode dialogs from hanging the application. */
  SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOALIGNMENTFAULTEXCEPT | SEM_NOGPFAULTERRORBOX |
               SEM_NOOPENFILEERRORBOX);
#  endif

  if (app_state.signal.use_abort_handler) {
#  ifndef WIN32
    if (app_state.signal.use_crash_handler) {
      crash_handler_install(SIGABRT); /* Logged like any crash, then ends the process. */
    }
    else
#  endif
    {
      signal(SIGABRT, sig_handle_abort);
    }
  }
}

void main_signal_setup_background()
{
  /* for all platforms, even windows has it! */
  BLI_assert(G.background);

  /* Support pressing `Ctrl-C` to close Blender in background-mode.
   * Useful to be able to cancel a render operation. */
  signal(SIGINT, sig_handle_blender_esc);
}

void main_signal_setup_fpe()
{
#  if defined(__linux__) || defined(_WIN32) || defined(OSX_SSE_FPE)
  /* Zealous but makes float issues a heck of a lot easier to find!
   * Set breakpoints on #sig_handle_fpe. */
  signal(SIGFPE, sig_handle_fpe);

#    if defined(__linux__) && defined(__GNUC__) && defined(HAVE_FEENABLEEXCEPT)
  feenableexcept(FE_DIVBYZERO | FE_INVALID | FE_OVERFLOW);
#    endif /* defined(__linux__) && defined(__GNUC__) */
#    if defined(OSX_SSE_FPE)
  /* OSX uses SSE for floating point by default, so here
   * use SSE instructions to throw floating point exceptions. */
  _MM_SET_EXCEPTION_MASK(_MM_MASK_MASK &
                         ~(_MM_MASK_OVERFLOW | _MM_MASK_INVALID | _MM_MASK_DIV_ZERO));
#    endif /* OSX_SSE_FPE */
#    if defined(_WIN32) && defined(_MSC_VER)
  /* Enables all floating-point exceptions. */
  _controlfp_s(nullptr, 0, _MCW_EM);
  /* Hide the ones we don't care about. */
  _controlfp_s(nullptr, _EM_DENORMAL | _EM_UNDERFLOW | _EM_INEXACT, _MCW_EM);
#    endif /* _WIN32 && _MSC_VER */
#  endif
}

}  // namespace blender

#endif /* WITH_PYTHON_MODULE */
