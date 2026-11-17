/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup wm
 *
 * Angelo: the record of a Vulkan start (`wm_angelo_gpu_startup.cc` says what it is for): the rule
 * a launch follows with the record it finds, the record's text, and its file, which one process at
 * a time holds.
 */

#include <cerrno>
#include <cstdlib>

#ifdef WIN32
#  include <windows.h>

#  include "utfconv.hh"
#else
#  include <fcntl.h>
#  include <sys/file.h>
#  include <unistd.h>
#endif

#include "BLI_string_ref.hh"

#include "wm_angelo_gpu_startup_intern.hh"

namespace blender::wm::angelo {

static const char TEXT_STARTING[] = "vulkan starting ";
static const char TEXT_SCRIPTS[] = " scripts";
/* With its line end, like #GPU_STARTUP_TEXT_CRASHED. */
static const char TEXT_FAILED[] = "vulkan failed\n";
/** A record is one short line; more is not read. */
static const int RECORD_SIZE_MAX = 64;

/* -------------------------------------------------------------------- */
/** \name The Rule
 * \{ */

GPUStartupPlan gpu_startup_plan(const GPUStartupRecord found, const bool prefers_vulkan)
{
  GPUStartupPlan plan;
  switch (found) {
    case GPUStartupRecord::None:
      plan.action = prefers_vulkan ? GPUStartupAction::WriteStarting : GPUStartupAction::Keep;
      break;
    case GPUStartupRecord::StartingScripts:
      /* That launch ended while its scripts ran: nothing about the GPU. */
      plan.action = prefers_vulkan ? GPUStartupAction::WriteStarting : GPUStartupAction::Clear;
      break;
    case GPUStartupRecord::Starting:
    case GPUStartupRecord::Crashed:
      /* That launch died before its first frame. The evidence is kept whatever the preferences
       * say now (the person may have started once with OpenGL forced and changed them). */
      plan.action = GPUStartupAction::WriteFailed;
      plan.failed = true;
      plan.use_fallback = prefers_vulkan;
      plan.announce = prefers_vulkan;
      break;
    case GPUStartupRecord::Failed:
      plan.action = GPUStartupAction::Keep;
      plan.failed = true;
      plan.use_fallback = prefers_vulkan;
      break;
  }
  return plan;
}

GPUStartupPlan gpu_startup_plan_unheld(const GPUStartupRecord peeked, const bool prefers_vulkan)
{
  if (peeked != GPUStartupRecord::Crashed && peeked != GPUStartupRecord::Failed) {
    return {};
  }
  GPUStartupPlan plan = gpu_startup_plan(peeked, prefers_vulkan);
  plan.action = GPUStartupAction::Keep;
  return plan;
}

GPUStartupThread gpu_startup_thread_current()
{
  GPUStartupThread thread;
#ifdef WIN32
  thread.id = GetCurrentThreadId();
#else
  thread.thread = pthread_self();
#endif
  thread.noted = true;
  return thread;
}

bool gpu_startup_thread_is_current(const GPUStartupThread &thread)
{
#ifdef WIN32
  return thread.noted && thread.id == GetCurrentThreadId();
#else
  return thread.noted && pthread_equal(thread.thread, pthread_self()) != 0;
#endif
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name The Record's Text
 * \{ */

std::string gpu_startup_record_text(const GPUStartupRecordData &data)
{
  switch (data.record) {
    case GPUStartupRecord::None:
      return "";
    case GPUStartupRecord::Starting:
      return TEXT_STARTING + std::to_string(data.pid) + "\n";
    case GPUStartupRecord::StartingScripts:
      return TEXT_STARTING + std::to_string(data.pid) + TEXT_SCRIPTS + "\n";
    case GPUStartupRecord::Crashed:
      return GPU_STARTUP_TEXT_CRASHED;
    case GPUStartupRecord::Failed:
      return TEXT_FAILED;
  }
  return "";
}

GPUStartupRecordData gpu_startup_record_from_text(StringRef text)
{
  text = text.trim();
  if (text == StringRef(TEXT_FAILED).trim()) {
    return {GPUStartupRecord::Failed, 0};
  }
  if (text == StringRef(GPU_STARTUP_TEXT_CRASHED).trim()) {
    return {GPUStartupRecord::Crashed, 0};
  }
  if (text.startswith(TEXT_STARTING)) {
    StringRef rest = text.drop_prefix(StringRef(TEXT_STARTING).size());
    GPUStartupRecord record = GPUStartupRecord::Starting;
    if (rest.endswith(TEXT_SCRIPTS)) {
      rest = rest.drop_suffix(StringRef(TEXT_SCRIPTS).size());
      record = GPUStartupRecord::StartingScripts;
    }
    const std::string digits = rest;
    if (!digits.empty() && digits.size() <= 18 &&
        digits.find_first_not_of("0123456789") == std::string::npos)
    {
      return {record, std::strtoll(digits.c_str(), nullptr, 10)};
    }
  }
  return {};
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name The Record's File
 * \{ */

bool GPUStartupFile::is_open() const
{
#ifdef WIN32
  return handle != nullptr;
#else
  return descriptor != -1;
#endif
}

GPUStartupOpen gpu_startup_file_open(const char *filepath, GPUStartupFile &r_file)
{
#ifdef WIN32
  /* Others may read the record; none may open it for writing or remove it while this handle is
   * open. A handle made this way is not inherited by child processes. */
  HANDLE handle = INVALID_HANDLE_VALUE;
  DWORD error = ERROR_SUCCESS;
  UTF16_ENCODE(filepath);
  handle = CreateFileW(filepath_16,
                       GENERIC_READ | GENERIC_WRITE,
                       FILE_SHARE_READ,
                       nullptr,
                       OPEN_ALWAYS,
                       FILE_ATTRIBUTE_NORMAL,
                       nullptr);
  if (handle == INVALID_HANDLE_VALUE) {
    error = GetLastError();
  }
  UTF16_UN_ENCODE(filepath);
  if (handle == INVALID_HANDLE_VALUE) {
    return (error == ERROR_SHARING_VIOLATION) ? GPUStartupOpen::Held : GPUStartupOpen::Unknown;
  }
  r_file.handle = handle;
  return GPUStartupOpen::Opened;
#else
  /* Opened for writing: where `flock` is emulated with byte-range locks (NFS), an exclusive lock
   * needs that. */
  const int descriptor = open(filepath, O_RDWR | O_CREAT | O_CLOEXEC, 0644);
  if (descriptor == -1) {
    return GPUStartupOpen::Unknown;
  }
  if (flock(descriptor, LOCK_EX | LOCK_NB) != 0) {
    const bool held = (errno == EWOULDBLOCK);
    close(descriptor);
    return held ? GPUStartupOpen::Held : GPUStartupOpen::Unknown;
  }
  r_file.descriptor = descriptor;
  return GPUStartupOpen::Opened;
#endif
}

bool gpu_startup_file_write_raw(const GPUStartupFile &file, const char *text, const size_t length)
{
#ifdef WIN32
  HANDLE handle = HANDLE(file.handle);
  DWORD written = 0;
  if (SetFilePointer(handle, 0, nullptr, FILE_BEGIN) == INVALID_SET_FILE_POINTER) {
    return false;
  }
  if (length != 0 &&
      (!WriteFile(handle, text, DWORD(length), &written, nullptr) || written != DWORD(length)))
  {
    return false;
  }
  return SetEndOfFile(handle) != 0;
#else
  if (length != 0 && pwrite(file.descriptor, text, length, 0) != ssize_t(length)) {
    return false;
  }
  return ftruncate(file.descriptor, off_t(length)) == 0;
#endif
}

GPUStartupRecordData gpu_startup_file_read(const GPUStartupFile &file)
{
  char text[RECORD_SIZE_MAX];
#ifdef WIN32
  HANDLE handle = HANDLE(file.handle);
  DWORD size = 0;
  if (SetFilePointer(handle, 0, nullptr, FILE_BEGIN) == INVALID_SET_FILE_POINTER ||
      !ReadFile(handle, text, DWORD(sizeof(text)), &size, nullptr))
  {
    return {};
  }
#else
  const ssize_t size = pread(file.descriptor, text, sizeof(text), 0);
  if (size < 0) {
    return {};
  }
#endif
  return gpu_startup_record_from_text(StringRef(text, int64_t(size)));
}

bool gpu_startup_file_write(const GPUStartupFile &file, const GPUStartupRecordData &data)
{
  const std::string text = gpu_startup_record_text(data);
  return gpu_startup_file_write_raw(file, text.data(), text.size());
}

void gpu_startup_file_close(GPUStartupFile &file)
{
#ifdef WIN32
  if (file.handle != nullptr) {
    CloseHandle(HANDLE(file.handle));
    file.handle = nullptr;
  }
#else
  if (file.descriptor != -1) {
    close(file.descriptor);
    file.descriptor = -1;
  }
#endif
}

GPUStartupRecordData gpu_startup_file_peek(const char *filepath)
{
  GPUStartupFile file;
#ifdef WIN32
  HANDLE handle = INVALID_HANDLE_VALUE;
  UTF16_ENCODE(filepath);
  handle = CreateFileW(filepath_16,
                       GENERIC_READ,
                       FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                       nullptr,
                       OPEN_EXISTING,
                       FILE_ATTRIBUTE_NORMAL,
                       nullptr);
  UTF16_UN_ENCODE(filepath);
  if (handle == INVALID_HANDLE_VALUE) {
    return {};
  }
  file.handle = handle;
#else
  file.descriptor = open(filepath, O_RDONLY | O_CLOEXEC);
  if (file.descriptor == -1) {
    return {};
  }
#endif
  const GPUStartupRecordData data = gpu_startup_file_read(file);
  gpu_startup_file_close(file);
  return data;
}

/** \} */

}  // namespace blender::wm::angelo
