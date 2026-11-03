/* SPDX-FileCopyrightText: 2023 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup bli
 */

#include <algorithm>
#include <climits>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "CLG_log.h"

#include "BLI_math_base.h"
#include "BLI_mutex.hh"
#include "BLI_string.h"
#include "BLI_system.h"

/* for backtrace and gethostname/GetComputerName */
#if defined(WIN32)
#  include <intrin.h>

#  include "BLI_winstuff.h"
#else
#  if defined(HAVE_EXECINFO_H)
#    include <execinfo.h>
#  endif
#  include <sys/resource.h>
#  include <unistd.h>
#endif
#if defined(__APPLE__)
#  include <mach/mach.h>
#  include <sys/sysctl.h>
#endif

namespace blender {

static CLG_LogRef LOG = {"system"};

int BLI_cpu_support_sse2()
{
#if defined(__x86_64__) || defined(_M_X64)
  /* x86_64 always has SSE2 instructions */
  return 1;
#elif defined(__GNUC__) && defined(i386)
  /* for GCC x86 we check cpuid */
  uint d;
  __asm__(
      "pushl %%ebx\n\t"
      "cpuid\n\t"
      "popl %%ebx\n\t"
      : "=d"(d)
      : "a"(1));
  return (d & 0x04000000) != 0;
#elif (defined(_MSC_VER) && defined(_M_IX86))
  /* also check cpuid for MSVC x86 */
  uint d;
  __asm {
    xor     eax, eax
    inc eax
    push ebx
    cpuid
    pop ebx
    mov d, edx
  }
  return (d & 0x04000000) != 0;
#else
  return 0;
#endif
}

/* Windows stack-walk lives in system_win32.cc */
#if !defined(_MSC_VER)
void BLI_system_backtrace_with_os_info(FILE *fp, const void * /*os_info*/)
{
  /* ----------------------- */
  /* If system as execinfo.h */
#  if defined(HAVE_EXECINFO_H)

#    define SIZE 100
  void *buffer[SIZE];
  int nptrs;
  char **strings;
  int i;

  /* Include a back-trace for good measure.
   *
   * NOTE: often values printed are addresses (no line numbers of function names),
   * this information can be expanded using `addr2line`, a utility is included to
   * conveniently run addr2line on the output generated here:
   *
   *   `./tools/utils/addr2line_backtrace.py --exe=/path/to/blender trace.txt`
   */
  nptrs = backtrace(buffer, SIZE);
  strings = backtrace_symbols(buffer, nptrs);
  for (i = 0; i < nptrs; i++) {
    fputs(strings[i], fp);
    fputc('\n', fp);
  }

  free(strings);
#    undef SIZE

#  else
  /* --------------------- */
  /* Non MSVC/Apple/Linux. */
  (void)fp;
#  endif
}
#endif
/* end BLI_system_backtrace_with_os_info */

void BLI_system_backtrace(FILE *fp)
{
  static Mutex mutex;
  std::scoped_lock lock(mutex);
  BLI_system_backtrace_with_os_info(fp, nullptr);
}

/* NOTE: The code for CPU brand string is adopted from Cycles. */

#if !defined(_WIN32) || defined(FREE_WINDOWS)
static void __cpuid(
    /* Cannot be const, because it is modified below.
     * NOLINTNEXTLINE: readability-non-const-parameter. */
    int data[4],
    int selector)
{
#  if defined(__x86_64__)
  asm("cpuid" : "=a"(data[0]), "=b"(data[1]), "=c"(data[2]), "=d"(data[3]) : "a"(selector));
#  elif defined(__i386__)
  asm("pushl %%ebx    \n\t"
      "cpuid          \n\t"
      "movl %%ebx, %1 \n\t"
      "popl %%ebx     \n\t"
      : "=a"(data[0]), "=r"(data[1]), "=c"(data[2]), "=d"(data[3])
      : "a"(selector)
      : "ebx");
#  else
  (void)selector;
  data[0] = data[1] = data[2] = data[3] = 0;
#  endif
}
#endif

char *BLI_cpu_brand_string()
{
#if !defined(_M_ARM64)
  char buf[49] = {0};
  int result[4] = {0};
  __cpuid(result, 0x80000000);
  if (result[0] >= int(0x80000004)) {
    __cpuid(reinterpret_cast<int *>(buf + 0), 0x80000002);
    __cpuid(reinterpret_cast<int *>(buf + 16), 0x80000003);
    __cpuid(reinterpret_cast<int *>(buf + 32), 0x80000004);
    char *brand = BLI_strdup(buf);
    /* TODO(sergey): Make it a bit more presentable by removing trademark. */
    return brand;
  }
#else
  /* No CPUID on ARM64, so we pull from the registry (on Windows) instead. */
  DWORD processorNameStringLength = 255;
  char processorNameString[255];
  if (RegGetValueA(HKEY_LOCAL_MACHINE,
                   "HARDWARE\\DESCRIPTION\\System\\CentralProcessor\\0",
                   "ProcessorNameString",
                   RRF_RT_REG_SZ,
                   nullptr,
                   &processorNameString,
                   &processorNameStringLength) == ERROR_SUCCESS)
  {
    return BLI_strdup(processorNameString);
  }
#endif
  return nullptr;
}

int BLI_cpu_support_sse42()
{
#if !defined(_M_ARM64)
  int result[4], num;
  __cpuid(result, 0);
  num = result[0];

  if (num >= 1) {
    __cpuid(result, 0x00000001);
    return (result[2] & (int(1) << 20)) != 0;
  }
#endif
  return 0;
}

void BLI_hostname_get(char *buffer, size_t buffer_maxncpy)
{
#ifndef WIN32
  if (gethostname(buffer, buffer_maxncpy - 1) < 0) {
    BLI_strncpy(buffer, "-unknown-", buffer_maxncpy);
  }
  /* When `gethostname()` truncates, it doesn't guarantee the trailing `\0`. */
  buffer[buffer_maxncpy - 1] = '\0';
#else
  DWORD buffer_size_in_out = buffer_maxncpy;
  if (!GetComputerName(buffer, &buffer_size_in_out)) {
    BLI_strncpy(buffer, "-unknown-", buffer_maxncpy);
  }
#endif
}

size_t BLI_system_memory_max_in_megabytes()
{
  /* Maximum addressable bytes on this platform.
   *
   * NOTE: Due to the shift arithmetic this is a half of the memory. */
  const size_t limit_bytes_half = size_t(1) << (sizeof(size_t[8]) - 1);
  /* Convert it to megabytes and return. */
  return (limit_bytes_half >> 20) * 2;
}

int BLI_system_memory_max_in_megabytes_int()
{
  const size_t limit_megabytes = BLI_system_memory_max_in_megabytes();
  /* NOTE: The result will fit into integer. */
  return int(min_zz(limit_megabytes, size_t(INT_MAX)));
}

size_t BLI_system_memory_physical_bytes()
{
#if defined(WIN32)
  MEMORYSTATUSEX status;
  status.dwLength = sizeof(status);
  return GlobalMemoryStatusEx(&status) ? size_t(status.ullTotalPhys) : 0;
#elif defined(__APPLE__)
  uint64_t bytes = 0;
  size_t length = sizeof(bytes);
  return sysctlbyname("hw.memsize", &bytes, &length, nullptr, 0) == 0 ? size_t(bytes) : 0;
#else
  const long pages = sysconf(_SC_PHYS_PAGES);
  const long page_size = sysconf(_SC_PAGE_SIZE);
  return (pages > 0 && page_size > 0) ? size_t(pages) * size_t(page_size) : 0;
#endif
}

#if defined(__linux__)
/** A "Key:   123 kB" line of /proc/meminfo, in bytes; 0 when absent. */
static size_t linux_meminfo_bytes(const char *key)
{
  FILE *file = fopen("/proc/meminfo", "r");
  if (file == nullptr) {
    return 0;
  }
  char line[256];
  size_t bytes = 0;
  const size_t key_len = strlen(key);
  while (fgets(line, sizeof(line), file)) {
    if (strncmp(line, key, key_len) == 0 && line[key_len] == ':') {
      bytes = size_t(strtoull(line + key_len + 1, nullptr, 10)) * 1024;
      break;
    }
  }
  fclose(file);
  return bytes;
}

/** The first number in a cgroup file ("max" and missing files give 0). */
static size_t linux_cgroup_bytes(const char *path)
{
  FILE *file = fopen(path, "r");
  if (file == nullptr) {
    return 0;
  }
  char text[64] = {0};
  const bool read = fgets(text, sizeof(text), file) != nullptr;
  fclose(file);
  return (read && text[0] >= '0' && text[0] <= '9') ? size_t(strtoull(text, nullptr, 10)) : 0;
}
#endif

size_t BLI_system_memory_available_bytes()
{
#if defined(WIN32)
  MEMORYSTATUSEX status;
  status.dwLength = sizeof(status);
  return GlobalMemoryStatusEx(&status) ? size_t(status.ullAvailPhys) : 0;
#elif defined(__APPLE__)
  vm_statistics64_data_t stats;
  mach_msg_type_number_t count = HOST_VM_INFO64_COUNT;
  vm_size_t page_size = 0;
  if (host_page_size(mach_host_self(), &page_size) != KERN_SUCCESS ||
      host_statistics64(mach_host_self(), HOST_VM_INFO64, host_info64_t(&stats), &count) !=
          KERN_SUCCESS)
  {
    return 0;
  }
  const size_t physical = BLI_system_memory_physical_bytes();
  const uint64_t app_pages = stats.internal_page_count > stats.purgeable_count ?
                                 stats.internal_page_count - stats.purgeable_count :
                                 0;
  const uint64_t used = (app_pages + stats.wire_count + stats.compressor_page_count) *
                        uint64_t(page_size);
  return physical > used ? size_t(physical - used) : 0;
#elif defined(__linux__)
  size_t available = linux_meminfo_bytes("MemAvailable");
  const size_t limit = linux_cgroup_bytes("/sys/fs/cgroup/memory.max");
  if (limit > 0) {
    const size_t current = linux_cgroup_bytes("/sys/fs/cgroup/memory.current");
    const size_t headroom = limit > current ? limit - current : 0;
    available = available > 0 ? std::min(available, headroom) : headroom;
  }
  return available;
#else
  return 0;
#endif
}

void BLI_system_max_open_files_ensure()
{
  /* The Windows maximum is documented as 8192. */
  constexpr int max_open_files = 8192;
  bool ok = true;

#if defined(WIN32)
  if (_getmaxstdio() < max_open_files) {
    ok = _setmaxstdio(max_open_files) == max_open_files;
  }
#else
  struct rlimit limit;
  ok = getrlimit(RLIMIT_NOFILE, &limit) == 0;
  if (ok && limit.rlim_cur < rlim_t(max_open_files)) {
    limit.rlim_cur = std::min(rlim_t(max_open_files), limit.rlim_max);
    ok = setrlimit(RLIMIT_NOFILE, &limit) == 0;
  }
#endif

  if (!ok) {
    CLOG_DEBUG(&LOG,
               "Failed to ensure max open files is at least %d: %s",
               max_open_files,
               strerror(errno));
  }
}

}  // namespace blender
