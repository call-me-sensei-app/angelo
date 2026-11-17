/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * What Angelo's own unconfined code may destroy or signal, decided from facts C holds, never from Python state a
 * script could have changed in place (doc/angelo/script_confinement.rst, "Angelo's own I/O").
 *
 * - Files and folders: `own_path` records the identity of a file or folder Angelo just made (a private folder, a
 *   socket, an endpoint record), holding a descriptor on it where the system allows, so its identity can't be given
 *   to another file while it is held. Outside a script anything is recorded; while one runs (Angelo's code called
 *   by it), only a path inside Angelo's private folders, where a script can write nothing, made since the script's
 *   region began (its birth time), so the path can't be one the script chose for something already there. Angelo's cleanups (at exit, a temporary folder's removal)
 *   remove a path only when `owned_path` says it is one of those, still at that path.
 * - Signals: outside a script, `os.kill` and `os.killpg` reach only this process and its descendants (the system's
 *   own parent links, read without running Python), and never every process (`-1`) or the caller's group (`0`).
 *   A confined thread signals nothing.
 */

#include <Python.h>

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <string>
#include <vector>

#include <ctime>
#include <random>

#ifdef _WIN32
#  include <windows.h>
#  include <sddl.h>
#  include <tlhelp32.h>
#else
#  include <fcntl.h>
#  include <sys/stat.h>
#  include <unistd.h>
#  ifdef __APPLE__
#    include <sys/sysctl.h>
#  endif
#endif

#include "BLI_file_time.hh"
#include "BLI_path_utils.hh"
#include "BLI_string.hh"

#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_paths.hh"

namespace blender::angelo_guard {

namespace {

struct Owned {
#ifdef _WIN32
  std::string path;    /* Normalized and lower-case (what `forget` names). */
  uint64_t volume = 0;
  uint64_t index[2] = {0, 0}; /* The volume's serial and the 128-bit file ID (unique on ReFS too): its identity. */
#else
  dev_t dev = 0;
  ino_t ino = 0;
  int fd = -1; /* Held where the system allows (Linux: any kind, `O_PATH`; elsewhere a folder or a file). */
#endif
};

std::mutex owned_lock;
std::vector<Owned> owned;
thread_local struct timespec region_began = {0, 0}; /* This thread's outermost region's start (wall clock). */
struct timespec first_region = {0, 0}; /* The process's first region's start (wall clock); set once. */
std::once_flag first_region_set;

/** Whether the file or folder at `path` was made at or after this thread's outermost region began; false when the
 * system can't say. */
/** Whether `path` (not followed) was born at or after `since`; `unknown` when the file system keeps no birth time. */
bool born_since(const std::string &path, const struct timespec &since, bool unknown)
{
#if defined(__linux__)
  struct statx info;
  if (statx(AT_FDCWD, path.c_str(), AT_SYMLINK_NOFOLLOW, STATX_BTIME, &info) != 0) {
    return false;
  }
  if (!(info.stx_mask & STATX_BTIME)) {
    return unknown;
  }
  return info.stx_btime.tv_sec > since.tv_sec ||
         (info.stx_btime.tv_sec == since.tv_sec && info.stx_btime.tv_nsec >= since.tv_nsec);
#elif defined(__APPLE__)
  struct stat info;
  if (lstat(path.c_str(), &info) != 0) {
    return false;
  }
  return info.st_birthtimespec.tv_sec > since.tv_sec ||
         (info.st_birthtimespec.tv_sec == since.tv_sec &&
          info.st_birthtimespec.tv_nsec >= since.tv_nsec);
#elif defined(_WIN32)
  WIN32_FILE_ATTRIBUTE_DATA data;
  int size = MultiByteToWideChar(CP_UTF8, 0, path.c_str(), -1, nullptr, 0);
  std::wstring wide(size > 0 ? size : 1, L'\0');
  if (size <= 0 || MultiByteToWideChar(CP_UTF8, 0, path.c_str(), -1, wide.data(), size) <= 0 ||
      !GetFileAttributesExW(wide.c_str(), GetFileExInfoStandard, &data))
  {
    return false;
  }
  ULARGE_INTEGER created;
  created.LowPart = data.ftCreationTime.dwLowDateTime;
  created.HighPart = data.ftCreationTime.dwHighDateTime;
  return blender::file_time::nanoseconds_from_filetime(created.QuadPart) >=
         blender::file_time::nanoseconds_saturated(since.tv_sec, since.tv_nsec);
#else
  (void)path;
  (void)since;
  return unknown;
#endif
}

bool born_in_region(const std::string &path)
{
  return born_since(path, region_began, false);
}

bool text_argument(PyObject *arg, std::string &out)
{
  PyObject *bytes = nullptr;
  if (!PyUnicode_FSConverter(arg, &bytes)) {
    PyErr_Clear();
    return false;
  }
  out = PyBytes_AS_STRING(bytes);
  Py_DECREF(bytes);
  return !out.empty();
}

#ifdef _WIN32
/** A file's or folder's identity (volume serial, 128-bit file ID: unique on ReFS and Dev Drives too, the 64-bit index
 * where the file system has no other), its own: a link or junction (a name-surrogate reparse point) is never
 * followed, it is refused; other reparse points (a cloud placeholder, a deduplicated file) are files like any. False
 * when it can't be opened. Opened for no access, sharing read, write and delete. */
bool win_identity(const std::string &path, uint64_t &volume, uint64_t index[2])
{
  const int size = MultiByteToWideChar(CP_UTF8, 0, path.c_str(), -1, nullptr, 0);
  std::wstring wide(size > 0 ? size : 1, L'\0');
  if (size <= 0 || MultiByteToWideChar(CP_UTF8, 0, path.c_str(), -1, wide.data(), size) <= 0) {
    return false;
  }
  HANDLE handle = CreateFileW(wide.c_str(),
                              0,
                              FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                              nullptr,
                              OPEN_EXISTING,
                              FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
                              nullptr);
  if (handle == INVALID_HANDLE_VALUE) {
    return false;
  }
  bool known = false, link = false;
  FILE_ATTRIBUTE_TAG_INFO tag;
  if (GetFileInformationByHandleEx(handle, FileAttributeTagInfo, &tag, sizeof(tag))) {
    link = (tag.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) && IsReparseTagNameSurrogate(tag.ReparseTag);
  }
  FILE_ID_INFO id;
  if (!link && GetFileInformationByHandleEx(handle, FileIdInfo, &id, sizeof(id))) {
    volume = id.VolumeSerialNumber;
    memcpy(index, id.FileId.Identifier, sizeof(id.FileId.Identifier));
    known = true;
  }
  BY_HANDLE_FILE_INFORMATION info;
  if (!link && !known && GetFileInformationByHandle(handle, &info)) {
    volume = info.dwVolumeSerialNumber;
    index[0] = (uint64_t(info.nFileIndexHigh) << 32) | info.nFileIndexLow;
    index[1] = 0;
    known = true;
  }
  CloseHandle(handle);
  return known && !link;
}

std::string key_of(const std::string &path)
{
  char normalized[FILE_MAX];
  STRNCPY(normalized, path.c_str());
  BLI_path_normalize(normalized);
  std::string key = normalized;
  for (char &c : key) {
    c = (c == '/') ? '\\' : char(tolower((unsigned char)c));
  }
  while (key.size() > 3 && key.back() == '\\') {
    key.pop_back();
  }
  return key;
}
#else
bool held_matches(const Owned &record)
{
  if (record.fd < 0) {
    return true; /* Nothing held (a socket outside Linux): the identity alone. */
  }
  struct stat info;
  if (fstat(record.fd, &info) != 0 || info.st_dev != record.dev || info.st_ino != record.ino) {
    return false;
  }
#  ifdef __linux__
  if (info.st_nlink == 0) {
    return false; /* Removed: the held descriptor keeps its number from another file, but it is gone. */
  }
#  endif
  return true;
}
#endif

bool record_path(const std::string &path)
{
#ifdef _WIN32
  uint64_t volume, index[2];
  if (!win_identity(path, volume, index)) {
    return false; /* Gone, unreadable, or a link: nothing recorded (its cleanup leaves it). */
  }
  const std::string key = key_of(path);
  std::lock_guard<std::mutex> guard(owned_lock);
  for (const Owned &record : owned) {
    if (record.volume == volume && record.index[0] == index[0] && record.index[1] == index[1]) {
      return true;
    }
  }
  owned.push_back(Owned{key, volume, {index[0], index[1]}});
  return true;
#else
  struct stat info;
  if (lstat(path.c_str(), &info) != 0 || S_ISLNK(info.st_mode)) {
    return false;
  }
  int fd = -1;
#  ifdef __linux__
  fd = open(path.c_str(), O_PATH | O_NOFOLLOW | O_CLOEXEC);
#  else
  if (S_ISDIR(info.st_mode)) {
    fd = open(path.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
  }
  else if (S_ISREG(info.st_mode)) {
    fd = open(path.c_str(), O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
  }
#  endif
  struct stat opened;
  if (fd >= 0 && (fstat(fd, &opened) != 0 || opened.st_dev != info.st_dev || opened.st_ino != info.st_ino)) {
    close(fd);
    return false; /* Replaced between the two looks. */
  }
  std::lock_guard<std::mutex> guard(owned_lock);
  for (const Owned &record : owned) {
    if (record.dev == info.st_dev && record.ino == info.st_ino && held_matches(record)) {
      if (fd >= 0) {
        close(fd);
      }
      return true;
    }
  }
  owned.push_back(Owned{info.st_dev, info.st_ino, fd});
  return true;
#endif
}

bool path_owned(const std::string &path)
{
#ifdef _WIN32
  uint64_t volume, index[2];
  if (!win_identity(path, volume, index)) {
    return false; /* A link or junction put in its place is never Angelo's. */
  }
  std::lock_guard<std::mutex> guard(owned_lock);
  for (const Owned &record : owned) {
    if (record.volume == volume && record.index[0] == index[0] && record.index[1] == index[1]) {
      return true;
    }
  }
  return false;
#else
  struct stat info;
  if (lstat(path.c_str(), &info) != 0 || S_ISLNK(info.st_mode)) {
    return false;
  }
  std::lock_guard<std::mutex> guard(owned_lock);
  for (const Owned &record : owned) {
    if (record.dev == info.st_dev && record.ino == info.st_ino) {
      return held_matches(record);
    }
  }
  return false;
#endif
}

void forget_path(const std::string &path)
{
#ifdef _WIN32
  uint64_t volume = 0, index[2] = {0, 0};
  const bool identified = win_identity(path, volume, index);
  const std::string key = key_of(path);
  std::lock_guard<std::mutex> guard(owned_lock);
  for (auto it = owned.begin(); it != owned.end(); ++it) {
    if ((identified && it->volume == volume && it->index[0] == index[0] && it->index[1] == index[1]) ||
        it->path == key)
    {
      owned.erase(it);
      return;
    }
  }
#else
  std::lock_guard<std::mutex> guard(owned_lock);
  struct stat info;
  if (lstat(path.c_str(), &info) != 0) {
    return;
  }
  for (auto it = owned.begin(); it != owned.end(); ++it) {
    if (it->dev == info.st_dev && it->ino == info.st_ino) {
      if (it->fd >= 0) {
        close(it->fd);
      }
      owned.erase(it);
      return;
    }
  }
#endif
}

/** The parent of `pid`, from the system's own process table; -1 when unknown. */
long long parent_of(long long pid)
{
#if defined(__linux__)
  char name[64];
  snprintf(name, sizeof(name), "/proc/%lld/stat", pid);
  FILE *file = fopen(name, "re");
  if (file == nullptr) {
    return -1;
  }
  char buffer[1024];
  const size_t size = fread(buffer, 1, sizeof(buffer) - 1, file);
  fclose(file);
  buffer[size] = '\0';
  const char *close_paren = strrchr(buffer, ')'); /* The command name may hold anything, ')' included. */
  long long parent = -1;
  char state = 0;
  if (close_paren == nullptr || sscanf(close_paren + 1, " %c %lld", &state, &parent) != 2) {
    return -1;
  }
  return parent;
#elif defined(__APPLE__)
  struct kinfo_proc info;
  size_t size = sizeof(info);
  int request[4] = {CTL_KERN, KERN_PROC, KERN_PROC_PID, int(pid)};
  if (sysctl(request, 4, &info, &size, nullptr, 0) != 0 || size == 0) {
    return -1;
  }
  return info.kp_eproc.e_ppid;
#elif defined(_WIN32)
  HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
  if (snapshot == INVALID_HANDLE_VALUE) {
    return -1;
  }
  PROCESSENTRY32 entry;
  entry.dwSize = sizeof(entry);
  long long parent = -1;
  for (BOOL more = Process32First(snapshot, &entry); more; more = Process32Next(snapshot, &entry)) {
    if ((long long)entry.th32ProcessID == pid) {
      parent = entry.th32ParentProcessID;
      break;
    }
  }
  CloseHandle(snapshot);
  return parent;
#else
  (void)pid;
  return -1;
#endif
}

long long this_process()
{
#ifdef _WIN32
  return (long long)GetCurrentProcessId();
#else
  return (long long)getpid();
#endif
}

/** Whether `pid` is this process or one of its descendants. */
bool ours(long long pid)
{
  const long long self = this_process();
  for (int hops = 0; hops < 64 && pid > 1; hops++) {
    if (pid == self) {
      return true;
    }
    const long long parent = parent_of(pid);
    if (parent == pid) {
      return false;
    }
    pid = parent;
  }
  return false;
}

/** Outside a script, anything; while one runs, only what is inside Angelo's private folders (refused to it). */
bool may_record(const std::string &path)
{
  if (!confined_now() && !frames_confined()) {
    return true;
  }
  return folders().judge(path.c_str(), true) == Refusal::Private && born_in_region(path);
}

}  // namespace

bool owned_or_inside_owned(const std::string &key)
{
  std::string current = key;
  while (!current.empty()) {
    if (path_owned(current)) {
      return true;
    }
#ifdef _WIN32
    const size_t cut = current.find_last_of("\\/");
#else
    const size_t cut = current.rfind('/');
#endif
    if (cut == std::string::npos || cut == 0 || (cut == 2 && current[1] == ':')) {
      break;
    }
    current.resize(cut);
  }
  return false;
}

bool owned_exactly(const std::string &path)
{
  return path_owned(path);
}

bool record_owned(const std::string &path)
{
  return record_path(path);
}

void owned_region_began()
{
  writable_region_began();
  /* File systems stamp with a coarse clock (Linux: the last tick), which can read earlier than this precise one:
   * 20 ms earlier still counts as made in the region (a script can make nothing in Angelo's private folders). */
  timespec_get(&region_began, TIME_UTC);
  region_began.tv_nsec -= 20000000L;
  if (region_began.tv_nsec < 0) {
    region_began.tv_nsec += 1000000000L;
    region_began.tv_sec -= 1;
  }
  std::call_once(first_region_set, []() { first_region = region_began; });
}

bool born_since_first_region(const std::string &path)
{
  if (first_region.tv_sec == 0 && first_region.tv_nsec == 0) {
    return false; /* No script region began in this process yet. */
  }
  return born_since(path, first_region, true); /* No birth time kept: it may be a script's (fail closed). */
}

int signal_refused(const char *event, PyObject *args, bool confined)
{
  if (confined) {
    return refuse(PROGRAMS, event); /* Other processes, and Angelo's own: a script signals nothing. */
  }
  if (PyTuple_GET_SIZE(args) < 2) {
    return 0;
  }
  const long long target = PyLong_AsLongLong(PyTuple_GET_ITEM(args, 0));
  const long long number = PyLong_AsLongLong(PyTuple_GET_ITEM(args, 1));
  if (PyErr_Occurred()) {
    PyErr_Clear();
    return 0; /* The call itself fails on such arguments. */
  }
  const bool group = strcmp(event, "os.killpg") == 0;
  if (number == 0 && target > 0) {
    return 0; /* Whether a process exists: nothing is delivered. */
  }
  const long long leader = group ? target : (target < 0 ? -target : target);
  if (leader > 1 && ours(leader)) {
    return 0;
  }
  PyErr_Format(PyExc_PermissionError,
               "Angelo signals only processes it started (%s of %lld refused: not this process or one of its "
               "descendants)",
               event, target);
  return -1;
}

static PyObject *guard_own_path(PyObject * /*self*/, PyObject *arg)
{
  std::string path;
  if (!text_argument(arg, path) || !may_record(path)) {
    Py_RETURN_FALSE; /* Nothing recorded: its cleanup leaves it in place. */
  }
  return PyBool_FromLong(record_path(path));
}

/** `make_owned_dir(parent, prefix)`: a new folder `<parent>/<prefix><random>`, made by C inside Angelo's private
 * folders and recorded at once: fresh by construction, so no birth time is needed (a file system that keeps none
 * still has Angelo's folders removed by its cleanups). Its path, or None when it can't be made there. */
static PyObject *guard_make_owned_dir(PyObject * /*self*/, PyObject *args)
{
  PyObject *parent_arg, *prefix_arg;
  if (!PyArg_ParseTuple(args, "OO:make_owned_dir", &parent_arg, &prefix_arg)) {
    return nullptr;
  }
  std::string parent, prefix;
  if (script_calling()) {
    Py_RETURN_NONE; /* A script's own call: nothing is made in Angelo's folders for it (Angelo's code it calls may). */
  }
  if (!text_argument(parent_arg, parent) || !text_argument(prefix_arg, prefix) ||
      prefix.find_first_of("/\\") != std::string::npos || prefix == "." || prefix == "..")
  {
    Py_RETURN_NONE;
  }
  const std::string probe = parent + "/" + prefix + "x";
  if (folders().judge(probe, true) != Refusal::Private) {
    Py_RETURN_NONE; /* Only inside Angelo's private folders, where a script makes nothing. */
  }
  std::string made;
  Py_BEGIN_ALLOW_THREADS;
#ifdef _WIN32
  /* Owner-only, as Python's own `tempfile.mkdtemp` makes it on Windows (CPython gh-118486): the parent's
   * permissions are not inherited. */
  PSECURITY_DESCRIPTOR descriptor = nullptr;
  ConvertStringSecurityDescriptorToSecurityDescriptorW(
      L"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;OW)", SDDL_REVISION_1, &descriptor, nullptr);
  SECURITY_ATTRIBUTES attributes = {sizeof(attributes), descriptor, FALSE};
  for (int attempt = 0; attempt < 32 && made.empty() && descriptor != nullptr; attempt++) {
    thread_local std::random_device source; /* Unique, not secret: no script writes in a private folder. */
    char suffix[17];
    snprintf(suffix, sizeof(suffix), "%08x%08x", unsigned(source()), unsigned(source()));
    const std::string candidate = parent + "\\" + prefix + suffix;
    const int size = MultiByteToWideChar(CP_UTF8, 0, candidate.c_str(), -1, nullptr, 0);
    std::wstring wide(size > 0 ? size : 1, L'\0');
    if (size > 0 && MultiByteToWideChar(CP_UTF8, 0, candidate.c_str(), -1, wide.data(), size) > 0 &&
        CreateDirectoryW(wide.c_str(), &attributes))
    {
      made = candidate;
    }
    else if (GetLastError() != ERROR_ALREADY_EXISTS) {
      break; /* Another failure than a name taken: trying other names won't help. */
    }
  }
  if (descriptor != nullptr) {
    LocalFree(descriptor);
  }
#else
  std::string pattern = parent + "/" + prefix + "XXXXXXXX";
  std::vector<char> buffer(pattern.begin(), pattern.end());
  buffer.push_back('\0');
  if (mkdtemp(buffer.data()) != nullptr) {
    made = buffer.data();
  }
#endif
  Py_END_ALLOW_THREADS;
  if (made.empty() || !record_path(made)) {
    Py_RETURN_NONE;
  }
  return PyUnicode_DecodeFSDefault(made.c_str());
}

static PyObject *guard_owned_path(PyObject * /*self*/, PyObject *arg)
{
  std::string path;
  if (!text_argument(arg, path)) {
    Py_RETURN_FALSE;
  }
  return PyBool_FromLong(path_owned(path));
}

static PyObject *guard_disown_path(PyObject * /*self*/, PyObject *arg)
{
  std::string path;
  if (text_argument(arg, path) && may_record(path)) {
    forget_path(path);
  }
  Py_RETURN_NONE;
}

static PyObject *guard_process_ours(PyObject * /*self*/, PyObject *arg)
{
  const long long pid = PyLong_AsLongLong(arg);
  if (PyErr_Occurred()) {
    PyErr_Clear();
    Py_RETURN_FALSE;
  }
  return PyBool_FromLong(pid > 1 && ours(pid));
}

PyMethodDef owned_methods[] = {
    {"own_path", guard_own_path, METH_O,
     "own_path(path) -> bool: record a file or folder Angelo just made (nothing while a script runs)."},
    {"make_owned_dir", guard_make_owned_dir, METH_VARARGS,
     "make_owned_dir(parent, prefix) -> str | None: a new folder Angelo's private folders hold, made and recorded by "
     "C at once."},
    {"owned_path", guard_owned_path, METH_O,
     "owned_path(path) -> bool: whether the path names a file or folder Angelo recorded, still that one."},
    {"disown_path", guard_disown_path, METH_O, "disown_path(path): forget a record (nothing while a script runs)."},
    {"process_ours", guard_process_ours, METH_O,
     "process_ours(pid) -> bool: whether the process is this one or one of its descendants."},
    {nullptr, nullptr, 0, nullptr},
};

}  // namespace blender::angelo_guard
