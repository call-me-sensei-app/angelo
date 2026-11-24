/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Remote names and the confinement's own lookups (bpy_angelo_guard_confine_remote.hh). No Python: callable from any
 * thread.
 */

#include "bpy_angelo_guard_confine_remote.hh"
#include "bpy_angelo_guard_confine_automount.hh"
#include "bpy_angelo_guard_strict.hh"

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cwctype>
#include <memory>
#include <future>
#include <memory>
#include <mutex>
#include <thread>
#include <vector>

#ifdef _WIN32
#  ifndef NOMINMAX
#    define NOMINMAX
#  endif
#  include <windows.h>

#  include <lm.h> /* NetUseEnum (netapi32). */
#endif

namespace blender::angelo_guard {

namespace {

bool separator(const wchar_t c)
{
  return c == L'\\' || c == L'/';
}

bool ascii_alpha(const wchar_t c)
{
  return (c >= L'a' && c <= L'z') || (c >= L'A' && c <= L'Z');
}

/** Whether `path` starts with `prefix`, any separator matching any separator and ASCII letters in any case. */
bool starts_with_folded(const std::wstring &path, const wchar_t *prefix)
{
  size_t i = 0;
  for (; prefix[i] != L'\0'; i++) {
    if (i >= path.size()) {
      return false;
    }
    const wchar_t a = path[i], b = prefix[i];
    if (separator(b) ? !separator(a) : (towlower(a) != towlower(b))) {
      return false;
    }
  }
  return true;
}

/** The next component of `path` from `at` (up to a separator or the end); `at` moves past it and its separator. */
std::wstring component(const std::wstring &path, size_t &at)
{
  const size_t begin = at;
  while (at < path.size() && !separator(path[at])) {
    at++;
  }
  std::wstring part = path.substr(begin, at - begin);
  if (at < path.size()) {
    at++;
  }
  return part;
}

std::wstring ascii_lower(std::wstring text)
{
  for (wchar_t &c : text) {
    if (c >= L'A' && c <= L'Z') {
      c = wchar_t(c - L'A' + L'a');
    }
  }
  return text;
}

std::mutex &shares_lock()
{
  static std::mutex lock;
  return lock;
}

std::vector<std::wstring> &connected_shares()
{
  static std::vector<std::wstring> shares;
  return shares;
}

std::atomic<uint64_t> opened{0};
/* Whether a script region began in this process: roots named after it note no share (`note_connected_root`). */
std::atomic<bool> region_began{false};

}  // namespace

bool windows_names_remote(const std::wstring &path)
{
  if (path.size() < 2 || !separator(path[0]) || !separator(path[1])) {
    /* `\??\` (the NT object namespace) starts with one separator: only a drive after it is local. */
    if (path.size() >= 4 && separator(path[0]) && path[1] == L'?' && path[2] == L'?' && separator(path[3])) {
      return !(path.size() >= 6 && ascii_alpha(path[4]) && path[5] == L':');
    }
    return false;
  }
  /* `\\?\C:\...` and `\\.\C:\...` are local drives; every other double-separator name is a host or a device. */
  if (path.size() >= 6 && (path[2] == L'?' || path[2] == L'.') && separator(path[3]) && ascii_alpha(path[4]) &&
      path[5] == L':')
  {
    return false;
  }
  return true;
}

std::wstring windows_share_of(const std::wstring &path)
{
  if (!windows_names_remote(path)) {
    return {};
  }
  size_t at = 0;
  if (starts_with_folded(path, L"\\\\?\\UNC\\") || starts_with_folded(path, L"\\\\.\\UNC\\") ||
      starts_with_folded(path, L"\\??\\UNC\\"))
  {
    at = 8; /* Each prefix is eight characters. */
  }
  else if (path.size() > 2 && (path[2] == L'?' || path[2] == L'.') && (path.size() == 3 || separator(path[3]))) {
    return {}; /* A device (`\\.\pipe\x`, `\\?\GLOBALROOT\...`, a volume GUID): never a share. */
  }
  else {
    at = 2;
  }
  const std::wstring server = component(path, at);
  const std::wstring share = component(path, at);
  for (const std::wstring &part : {server, share}) {
    if (part.empty() || part == L"." || part == L".." || part == L"?") {
      return {};
    }
  }
  return ascii_lower(server) + L"\\" + ascii_lower(share);
}

#ifdef _WIN32
namespace {
/** One of Windows' connections now (NetUseEnum, level 1): its share, whether a drive letter is mapped to it, its status. */
struct Use {
  std::wstring share;
  bool mapped = false;
  bool ok = false;
};

std::vector<Use> read_uses()
{
  std::vector<Use> uses;
  LPBYTE buffer = nullptr;
  DWORD read = 0, total = 0, resume = 0;
  if (NetUseEnum(nullptr, 1, &buffer, MAX_PREFERRED_LENGTH, &read, &total, &resume) == NERR_Success && buffer) {
    const USE_INFO_1 *listed = reinterpret_cast<const USE_INFO_1 *>(buffer);
    for (DWORD i = 0; i < read; i++) {
      if (listed[i].ui1_remote == nullptr) {
        continue;
      }
      Use use;
      use.share = windows_share_of(listed[i].ui1_remote);
      use.mapped = listed[i].ui1_local != nullptr && listed[i].ui1_local[0] != L'\0';
      use.ok = listed[i].ui1_status == USE_OK;
      if (!use.share.empty()) {
        uses.push_back(std::move(use));
      }
    }
  }
  if (buffer) {
    NetApiBufferFree(buffer);
  }
  return uses;
}

std::mutex uses_lock;
std::shared_ptr<const std::vector<Use>> uses_listed;
ULONGLONG uses_read_at = 0;
std::vector<std::wstring> deviceless_at_start; /* Deviceless and OK when the first region began. */

/** Windows' connections, read at most once a second; asked outside the lock (the workstation service may be slow). */
std::shared_ptr<const std::vector<Use>> uses_now()
{
  const ULONGLONG now = GetTickCount64();
  {
    std::lock_guard guard(uses_lock);
    if (uses_listed && now - uses_read_at <= 1000) {
      return uses_listed;
    }
  }
  auto fresh = std::make_shared<const std::vector<Use>>(read_uses());
  std::lock_guard guard(uses_lock);
  uses_listed = fresh;
  uses_read_at = now;
  return fresh;
}
}  // namespace

/** Whether `share` (`server\share`, lower case) is one of the person's connections now (break-it-confine-reparse-
 * b7d499c head-7 MUST-4): a drive letter mapped to it (their own setting, whatever its status), or a deviceless
 * connection that is OK now and was already listed when the first script region began. A connection made later
 * may be one a script caused indirectly (Angelo opening a path it left in data, Explorer reading a planted
 * desktop.ini), so it never counts; nothing here is noted for good, every answer is read again. */
static bool windows_connected_now(const std::wstring &share)
{
  const std::shared_ptr<const std::vector<Use>> uses = uses_now();
  const bool after_start = region_began.load(std::memory_order_acquire);
  for (const Use &use : *uses) {
    if (use.share != share) {
      continue;
    }
    if (use.mapped) {
      return true;
    }
    if (use.ok) {
      if (!after_start) {
        return true;
      }
      std::lock_guard guard(uses_lock);
      if (std::find(deviceless_at_start.begin(), deviceless_at_start.end(), share) != deviceless_at_start.end()) {
        return true;
      }
    }
  }
  return false;
}

/** The deviceless connections OK before the first document loads (or as the first region begins, where nothing took
 * them earlier): the only ones that count later. */
static std::atomic<bool> uses_at_start_taken{false};
static void windows_uses_at_start()
{
  if (uses_at_start_taken.exchange(true)) {
    return;
  }
  std::vector<std::wstring> deviceless;
  for (const Use &use : *uses_now()) {
    if (!use.mapped && use.ok) {
      deviceless.push_back(use.share);
    }
  }
  std::lock_guard guard(uses_lock);
  deviceless_at_start = std::move(deviceless);
}
#endif

namespace {
struct NetworkRefusal {
  std::string path;
  std::wstring share;
};
thread_local NetworkRefusal network_refusal;

void network_refusal_note(const std::wstring &share)
{
  if (network_refusal.share.empty() && !share.empty()) {
    network_refusal.share = L"\\\\" + share;
  }
}
}  // namespace

void network_refusal_begin(const std::string &path)
{
  network_refusal.path = path;
  network_refusal.share.clear();
}

std::string network_refusal_share(const char *path)
{
  std::string share;
#ifdef _WIN32
  if (path != nullptr && network_refusal.path == path && !network_refusal.share.empty()) {
    const std::wstring &wide = network_refusal.share;
    const int size = WideCharToMultiByte(CP_UTF8, 0, wide.data(), int(wide.size()), nullptr, 0, nullptr, nullptr);
    if (size > 0) {
      share.resize(size_t(size));
      WideCharToMultiByte(CP_UTF8, 0, wide.data(), int(wide.size()), share.data(), size, nullptr, nullptr);
    }
  }
#else
  (void)path;
#endif
  network_refusal = NetworkRefusal();
  return share;
}

bool lookup_may_open(const std::wstring &path)
{
  if (!windows_names_remote(path)) {
    return true;
  }
  const std::wstring share = windows_share_of(path);
  if (share.empty()) {
    return false;
  }
  {
    std::lock_guard guard(shares_lock());
    const std::vector<std::wstring> &shares = connected_shares();
    if (std::find(shares.begin(), shares.end(), share) != shares.end()) {
      return true;
    }
  }
#ifdef _WIN32
  /* A share Windows has connected now (the person opened it: a mapped drive, a folder of theirs linked to it, Explorer)
   * is one no script chose to reach: looking a name up there makes no new connection
   * (break-it-confine-reparse-b7d499c S3 residual; Ruling 11). */
  if (windows_connected_now(share)) {
    return true; /* Asked again each time: never noted for good (MUST-4). */
  }
#endif
  network_refusal_note(share);
  return false;
}

void note_connected(const std::wstring &name)
{
  const std::wstring share = windows_share_of(name);
  if (share.empty()) {
    return;
  }
  std::lock_guard guard(shares_lock());
  std::vector<std::wstring> &shares = connected_shares();
  if (std::find(shares.begin(), shares.end(), share) == shares.end()) {
    shares.push_back(share);
  }
}

bool utf8_strictly_valid(const std::string &text)
{
  /* Unicode's well-formed UTF-8 (The Unicode Standard, table 3-7): no stray continuation byte, no truncated sequence,
   * no overlong form, no encoded surrogate (U+D800..U+DFFF), nothing above U+10FFFF. */
  const auto *bytes = reinterpret_cast<const unsigned char *>(text.data());
  const size_t size = text.size();
  for (size_t i = 0; i < size;) {
    const unsigned char c = bytes[i];
    size_t extra = 0;
    unsigned char low = 0x80, high = 0xBF; /* The second byte's range. */
    if (c < 0x80) {
      i++;
      continue;
    }
    if (c >= 0xC2 && c <= 0xDF) {
      extra = 1;
    }
    else if (c >= 0xE0 && c <= 0xEF) {
      extra = 2;
      low = c == 0xE0 ? 0xA0 : 0x80;
      high = c == 0xED ? 0x9F : 0xBF; /* ED A0..BF would be a surrogate. */
    }
    else if (c >= 0xF0 && c <= 0xF4) {
      extra = 3;
      low = c == 0xF0 ? 0x90 : 0x80;
      high = c == 0xF4 ? 0x8F : 0xBF;
    }
    else {
      return false; /* A continuation byte or C0, C1, F5..FF as a lead. */
    }
    if (i + extra >= size) {
      return false; /* Truncated. */
    }
    if (bytes[i + 1] < low || bytes[i + 1] > high) {
      return false;
    }
    for (size_t k = 2; k <= extra; k++) {
      if (bytes[i + k] < 0x80 || bytes[i + k] > 0xBF) {
        return false;
      }
    }
    i += extra + 1;
  }
  return true;
}

static std::atomic<bool> walk_always{false};

bool strict_lookups()
{
  return walk_always.load(std::memory_order_relaxed) || angelo_strict_script_protection();
}

void lookups_walk_always(const bool always)
{
  walk_always.store(always, std::memory_order_relaxed);
}

#ifdef _WIN32
bool utf8_to_wide(const std::string &utf8, std::wstring &out)
{
  out.clear();
  const std::string text(utf8.c_str()); /* What a C call is given: up to the first NUL. */
  if (text.empty()) {
    return true;
  }
  if (!utf8_strictly_valid(text)) {
    return false; /* Another decoder would read it as another name (Blender's drops what it can't decode). */
  }
  const int size = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(), int(text.size()), nullptr, 0);
  if (size <= 0) {
    return false;
  }
  out.assign(size_t(size), L'\0');
  if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(), int(text.size()), out.data(), size) != size) {
    out.clear();
    return false;
  }
  return true;
}

/** The checked wide name of `path`, or false when a lookup may not touch it: not strictly valid UTF-8, or its text or
 * full name (text only: `GetFullPathNameW`) is a remote name on a share nobody connected. */
/** `follow_last`: whether the call follows a link that is the name's last component (`GetFileAttributesW` doesn't:
 * it describes the link itself). */
bool confined_now(); /* bpy_angelo_guard_confine.cc. */

/** Whether the lookups walk the links on the way: only with "Strict script protection" on, and then only for a
 * confined thread (Ruling 17: the walk costs a confined lookup measurably against Angelo's own speed, so it is never
 * on by default, and it costs nothing while no script runs). Otherwise a lookup keeps 2.0.1's check by the name's
 * text, its full name included. Unconfined, a link on the way is the person's own (no confined code makes one). Also
 * while Angelo resolves its own roots before the first region (`RootLinksNoted`): the walk is what notes the share a
 * person's linked folder names, so it stays usable either way. The tests turn the walk on for every thread. */
static bool walks_links()
{
  return walk_always.load(std::memory_order_relaxed) || root_links_being_noted() ||
         (angelo_strict_script_protection() && confined_now());
}


static bool touchable(const std::string &path, std::wstring &wide, const bool follow_last = true)
{
  if (!utf8_to_wide(path, wide)) {
    return false;
  }
  if (wide.empty()) {
    return true; /* Names nothing: the call fails by itself. */
  }
  if (!lookup_may_open(wide)) {
    return false;
  }
  const DWORD needed = GetFullPathNameW(wide.c_str(), 0, nullptr, nullptr); /* Sized exactly, no 32K buffer. */
  if (needed == 0) {
    return false;
  }
  std::wstring full(size_t(needed), L'\0');
  const DWORD length = GetFullPathNameW(wide.c_str(), needed, full.data(), nullptr);
  if (length == 0 || length >= needed) {
    return false;
  }
  full.resize(length);
  /* The full name's text, as 2.0.1 checked it; then, with Strict script protection, the links on the way: the call
   * below follows every one of them (break-it-confine-reparse-b7d499c S7: the stat-family helpers were text-only). */
  if (!lookup_may_open(full)) {
    return false;
  }
  return !walks_links() || links_walk(full, follow_last) == links::Verdict::Safe;
}
#endif

std::optional<std::string> unconnected_remote_spelling(const std::string &folder)
{
#ifdef _WIN32
  std::wstring wide;
  if (!utf8_to_wide(folder, wide) || wide.empty()) {
    return std::nullopt;
  }
  std::wstring full(32768, L'\0');
  const DWORD length = GetFullPathNameW(wide.c_str(), DWORD(full.size()), full.data(), nullptr); /* Text only. */
  if (length == 0 || length >= full.size()) {
    return std::nullopt;
  }
  full.resize(length);
  const std::wstring share = windows_share_of(full);
  if (share.empty() || lookup_may_open(full)) {
    return std::nullopt; /* Not a share, or a connected one (resolve answers for it). */
  }
  for (wchar_t &c : full) {
    c = c == L'/' ? L'\\' : c;
  }
  size_t at = 2; /* After `\\` of `\\server\share`. */
  for (const wchar_t *prefix : {L"\\\\?\\UNC\\", L"\\\\.\\UNC\\", L"\\??\\UNC\\"}) {
    if (full.size() >= 8 && _wcsnicmp(full.c_str(), prefix, 8) == 0) {
      at = 8;
    }
  }
  std::wstring spelled = L"\\\\" + full.substr(at);
  while (spelled.size() > 2 && spelled.back() == L'\\') {
    spelled.pop_back();
  }
  const int size = WideCharToMultiByte(
      CP_UTF8, WC_ERR_INVALID_CHARS, spelled.data(), int(spelled.size()), nullptr, 0, nullptr, nullptr);
  if (size <= 0) {
    return std::nullopt;
  }
  std::string result(size_t(size), '\0');
  WideCharToMultiByte(
      CP_UTF8, WC_ERR_INVALID_CHARS, spelled.data(), int(spelled.size()), result.data(), size, nullptr, nullptr);
  return result;
#else
  (void)folder;
  return std::nullopt;
#endif
}

void note_connected_root(const std::string &name)
{
  if (region_began.load(std::memory_order_relaxed)) {
    return; /* After a script ran, a root's inputs may hold what it appended to Angelo's own lists. */
  }
#ifdef _WIN32
  std::wstring wide;
  if (utf8_to_wide(name, wide)) {
    note_connected(wide);
  }
#else
  automount::note_root(name); /* Its automounted key stays connected after the first region (S6 re-read MUST). */
#endif
}

bool names_a_remote_file(const std::string &path)
{
#ifdef _WIN32
  /* Decoded once, strictly, as every lookup decodes it: a name another decoder would read differently (one that
   * drops invalid bytes would turn `\x80\\host\s\x` into `\\host\s\x`) is treated as remote, refused by its text. */
  std::wstring wide;
  if (!utf8_to_wide(path, wide)) {
    return true;
  }
  if (!windows_names_remote(wide)) {
    return false;
  }
  network_refusal_note(windows_share_of(wide)); /* By its text: refused whether connected or not. */
  return true;
#else
  /* Under a folder of the automounter's maps of any host (`/net` where `-hosts` is mounted, a program map): even a
   * stat there mounts, so connects to, the host a name asks for (break-it-confine-reparse-b7d499c S6). A key mounted
   * before the first region, or holding one of the person's roots, is connected: usable (S6 MUST-1, its re-read). */
#  ifdef __APPLE__
  constexpr bool fold = true; /* macOS volumes ignore case by default: `/NET/h` is `/net/h`. */
#  else
  constexpr bool fold = false;
#  endif
  return automount::names_an_unmounted_key(path, *automount::current_table(), fold);
#endif
}

bool note_person_document(const std::string &path, const bool unconfined)
{
#ifdef _WIN32
  std::wstring wide;
  if (!unconfined || !utf8_to_wide(path, wide) || !windows_names_remote(wide) || windows_share_of(wide).empty()) {
    return false;
  }
  note_connected(wide);
  return true;
#else
  (void)path;
  (void)unconfined;
  return false;
#endif
}

bool remote_region_has_begun()
{
  return region_began.load(std::memory_order_relaxed);
}

/* The start snapshot runs on its own thread (Ruling 17: nothing costs the person anything while no script runs;
 * NetUseEnum asks the workstation service, which can be slow), and the first script region waits for it. Detached
 * (a thread no one joins would end the process at its exit); a future says when it is done. */
static std::mutex start_lock;
static std::shared_future<void> start_done;

static void start_snapshot()
{
#ifdef _WIN32
  windows_uses_at_start();
#else
  /* The automounter's baseline (its first refresh takes it) before the startup file loads, as Windows' list is: a
   * mount the startup file's own paths trigger never counts as the person's (head-10/11 re-read SHOULD). */
  automount::refresh();
#endif
}

void remote_connections_at_start()
{
  std::lock_guard guard(start_lock);
  if (start_done.valid() || region_began.load(std::memory_order_acquire)) {
    return;
  }
  auto promise = std::make_shared<std::promise<void>>();
  start_done = promise->get_future().share();
  std::thread([promise]() {
    start_snapshot();
    promise->set_value();
  }).detach();
}

/** The start snapshot is complete before the first region sees the flag, and before any document is read. */
static void start_snapshot_wait()
{
  std::shared_future<void> done;
  {
    std::lock_guard guard(start_lock);
    done = start_done;
  }
  if (done.valid()) {
    done.wait();
  }
}

void remote_connections_ready()
{
  start_snapshot_wait();
}

void remote_region_began()
{
  if (!region_began.load(std::memory_order_acquire)) {
    start_snapshot_wait(); /* What WM_init started, complete before any script sees the flag. */
#ifdef _WIN32
    windows_uses_at_start(); /* Before the flag (MUST-4); a no-op when the start snapshot took it. */
#endif
  }
  links_region_began();
  automount::refresh(); /* Reads again only when the maps' files or the mount table changed (S6 MUST-2). */
  region_began.store(true, std::memory_order_release);
}

bool lookup_may_touch(const std::string &path)
{
#ifdef _WIN32
  std::wstring wide;
  return touchable(path, wide);
#else
  (void)path;
  return true;
#endif
}

/* Each helper checks a name and, on Windows, hands the system that very wide string: no second decoder. */

int lookup_stat(const std::string &path, BLI_stat_t *info)
{
#ifdef _WIN32
  std::wstring wide;
  if (!touchable(path, wide) || wide.empty()) {
    return -1;
  }
  opened.fetch_add(1, std::memory_order_relaxed);
  return BLI_wstat(wide.c_str(), info);
#else
  if (!lookup_may_touch(path)) {
    return -1;
  }
  opened.fetch_add(1, std::memory_order_relaxed);
  return BLI_stat(path.c_str(), info);
#endif
}

bool lookup_is_dir(const std::string &path)
{
#ifdef _WIN32
  std::wstring wide;
  if (!touchable(path, wide, false) || wide.empty()) {
    return false;
  }
  opened.fetch_add(1, std::memory_order_relaxed);
  const DWORD attributes = GetFileAttributesW(wide.c_str()); /* As `BLI_is_dir` asks. */
  return attributes != INVALID_FILE_ATTRIBUTES && (attributes & FILE_ATTRIBUTE_DIRECTORY);
#else
  if (!lookup_may_touch(path)) {
    return false;
  }
  opened.fetch_add(1, std::memory_order_relaxed);
  return BLI_is_dir(path.c_str());
#endif
}

bool lookup_is_file(const std::string &path)
{
#ifdef _WIN32
  std::wstring wide;
  if (!touchable(path, wide, false) || wide.empty()) {
    return false;
  }
  opened.fetch_add(1, std::memory_order_relaxed);
  const DWORD attributes = GetFileAttributesW(wide.c_str()); /* As `BLI_is_file` asks. */
  return attributes != INVALID_FILE_ATTRIBUTES && !(attributes & FILE_ATTRIBUTE_DIRECTORY);
#else
  if (!lookup_may_touch(path)) {
    return false;
  }
  opened.fetch_add(1, std::memory_order_relaxed);
  return BLI_is_file(path.c_str());
#endif
}

FILE *lookup_fopen_read(const std::string &path)
{
#ifdef _WIN32
  std::wstring wide;
  if (!touchable(path, wide) || wide.empty()) {
    errno = ENOENT;
    return nullptr;
  }
  opened.fetch_add(1, std::memory_order_relaxed);
  return _wfopen(wide.c_str(), L"rb");
#else
  if (!lookup_may_touch(path)) {
    errno = ENOENT;
    return nullptr;
  }
  opened.fetch_add(1, std::memory_order_relaxed);
  return BLI_fopen(path.c_str(), "rb");
#endif
}

#ifdef _WIN32
void *open_for_lookup(const std::wstring &path, const bool follow_last)
{
  if (path.empty() || !lookup_may_open(path)) { /* Empty: a name that did not decode (`utf8_to_wide`). */
    SetLastError(ERROR_BAD_NETPATH);
    return INVALID_HANDLE_VALUE;
  }
  if (walks_links() && links_walk(path, follow_last) != links::Verdict::Safe) {
    /* A link on the way leads to a share nobody connected, or can't be told: opening the path would follow it. */
    SetLastError(ERROR_BAD_NETPATH);
    return INVALID_HANDLE_VALUE;
  }
  opened.fetch_add(1, std::memory_order_relaxed);
  return CreateFileW(path.c_str(),
                     0,
                     FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                     nullptr,
                     OPEN_EXISTING,
                     FILE_FLAG_BACKUP_SEMANTICS | (follow_last ? 0 : FILE_FLAG_OPEN_REPARSE_POINT),
                     nullptr);
}
#endif

uint64_t lookups_opened()
{
  return opened.load(std::memory_order_relaxed);
}

}  // namespace blender::angelo_guard
