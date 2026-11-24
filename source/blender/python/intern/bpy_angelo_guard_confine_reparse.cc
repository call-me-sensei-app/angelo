/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Windows: the link walk's probe and its process-wide state (bpy_angelo_guard_confine_links.hh is the walk itself,
 * pure and tested on every system). Each component is opened in `\\?\` form with FILE_FLAG_OPEN_REPARSE_POINT (never
 * followed) and FILE_READ_ATTRIBUTES; a symbolic link's or mount point's substitute name is read with
 * FSCTL_GET_REPARSE_POINT and parsed with every offset checked against what was returned. Only "not found" stops a
 * walk; any other error, unreadable data, or a name-surrogate tag of another kind refuses
 * (break-it-confine-reparse-b7d499c M4, S5). Folders found plain are kept per thread, by their exact text, until the
 * process-wide generation moves: at each region's start and at every judged move, link, make or remove (M5).
 */

#include "bpy_angelo_guard_confine_links.hh"
#include "bpy_angelo_guard_confine_remote.hh"

#include <atomic>

#ifdef _WIN32
#  ifndef NOMINMAX
#    define NOMINMAX
#  endif
#  include <windows.h>
#  include <winioctl.h>

#  include <cwchar>
#  include <cwctype>
#  include <memory>
#  include <unordered_map>
#endif

namespace blender::angelo_guard {

namespace {

std::atomic<uint64_t> generation{1};

}  // namespace

void links_region_began()
{
  generation.fetch_add(1, std::memory_order_acq_rel);
}

void links_changed()
{
  generation.fetch_add(1, std::memory_order_acq_rel);
}

#ifdef _WIN32

namespace {

/* While Angelo resolves its own roots before the first region: a share a link on the way names is the person's (a
 * folder of theirs linked to a share they use: `mklink /D C:\Assets \\nas\assets`), noted as connected
 * (break-it-confine-reparse-b7d499c S3). */
thread_local int noting_root_links = 0;

struct Cache {
  uint64_t seen = 0;
  std::unordered_set<std::wstring> folders;
  std::unordered_map<wchar_t, std::wstring> drives; /* A drive letter's first DOS device target, this region. */
};
thread_local Cache cache;

/** A drive root's (`\\?\\X:\\`) first DOS device target (QueryDosDeviceW), kept for the region once Windows answers;
 * empty when it names none (asked again next time). */
std::wstring drive_device(const std::wstring &root)
{
  if (root.size() < 6) {
    return {};
  }
  const wchar_t letter = wchar_t(std::towupper(root[4]));
  if (const auto found = cache.drives.find(letter); found != cache.drives.end()) {
    return found->second;
  }
  const wchar_t name[3] = {letter, L':', L'\0'};
  std::wstring asked(1024, L'\0');
  const DWORD length = QueryDosDeviceW(name, asked.data(), DWORD(asked.size()));
  asked.resize(length == 0 ? 0 : wcsnlen(asked.c_str(), asked.size()));
  if (!asked.empty()) {
    cache.drives.emplace(letter, asked);
  }
  return asked;
}

/** What a drive root stands for (head-7 MUST-A'): `\??\` text (a `subst` drive: a folder on another drive, or a
 * share) is walked as that place; a device (a volume, or a drive letter mapped to a share: `\Device\...Redirector`,
 * the person's own setting) is the drive itself; a letter Windows can't name is refused. */
links::Rooted drive_root(const std::wstring &root)
{
  links::Rooted self;
  self.type = links::Rooted::Type::Drive;
  self.root = root;
  if (root.size() < 6) {
    return links::Rooted();
  }
  const std::wstring target = drive_device(root);
  if (target.empty()) {
    /* No such drive (yet), or its device can't be read: refused now, and asked again next time, so a drive the person
     * maps while a region runs works at once (only an answer is kept for the region). */
    return links::Rooted();
  }
  if (target.rfind(L"\\??\\", 0) == 0) {
    return links::split_target(target, false);
  }
  return self;
}

/** GetFullPathNameW (text only), sized exactly: no 32K buffer per call (S4). Empty when it fails. */
std::wstring full_path(const std::wstring &path)
{
  if (path.empty()) {
    return std::wstring();
  }
  const DWORD needed = GetFullPathNameW(path.c_str(), 0, nullptr, nullptr);
  if (needed == 0) {
    return std::wstring();
  }
  std::wstring full(size_t(needed), L'\0');
  const DWORD length = GetFullPathNameW(path.c_str(), needed, full.data(), nullptr);
  if (length == 0 || length >= needed) {
    return std::wstring();
  }
  full.resize(length);
  return full;
}

/** One component, opened without following it. */
links::Probe probe_component(const std::wstring &extended)
{
  links::Probe found;
  HANDLE handle = CreateFileW(extended.c_str(),
                              FILE_READ_ATTRIBUTES,
                              FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                              nullptr,
                              OPEN_EXISTING,
                              FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
                              nullptr);
  if (handle == INVALID_HANDLE_VALUE) {
    const DWORD error = GetLastError();
    found.kind = (error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND) ? links::Probe::Kind::Missing :
                                                                                    links::Probe::Kind::Refuse;
    return found;
  }
  FILE_ATTRIBUTE_TAG_INFO tag = {};
  if (!GetFileInformationByHandleEx(handle, FileAttributeTagInfo, &tag, sizeof(tag))) {
    CloseHandle(handle);
    return found; /* Refuse: a component that can't be examined. */
  }
  found.folder = (tag.FileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
  if (!(tag.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)) {
    found.kind = links::Probe::Kind::Plain;
  }
  else if (!IsReparseTagNameSurrogate(tag.ReparseTag)) {
    /* Not a link. A file keeps its data whatever its tag; a folder only with a tag known to (a cloud placeholder,
     * dedup, WOF): DFS's referral or a provider's placeholder leads where the provider decides (re-read SHOULD). */
    if (!found.folder || links::reparse_tag_keeps_data(tag.ReparseTag)) {
      found.kind = links::Probe::Kind::Plain;
    }
  }
  else if (tag.ReparseTag == IO_REPARSE_TAG_SYMLINK || tag.ReparseTag == IO_REPARSE_TAG_MOUNT_POINT) {
    /* Not zero-filled: parse_reparse reads only what `returned` covers. */
    std::unique_ptr<unsigned char[]> buffer(new unsigned char[MAXIMUM_REPARSE_DATA_BUFFER_SIZE]);
    DWORD returned = 0;
    if (DeviceIoControl(handle,
                        FSCTL_GET_REPARSE_POINT,
                        nullptr,
                        0,
                        buffer.get(),
                        DWORD(MAXIMUM_REPARSE_DATA_BUFFER_SIZE),
                        &returned,
                        nullptr) &&
        links::parse_reparse(buffer.get(), size_t(returned), found.target, found.relative))
    {
      found.kind = links::Probe::Kind::Link;
    }
  }
  /* Any other name surrogate (a WSL link, an AF_UNIX socket, a tag yet unknown): Refuse, as winpath.cc does. */
  CloseHandle(handle);
  return found;
}

}  // namespace

namespace {

/* NtOpenFile's own structures (winternl.h's layout), so nothing else of the native API is pulled in. */
struct NtName {
  USHORT length;
  USHORT maximum;
  PWSTR buffer;
};
struct NtAttributes {
  ULONG length;
  HANDLE root;
  NtName *name;
  ULONG attributes;
  PVOID security;
  PVOID quality;
};
struct NtStatusBlock {
  union {
    LONG status;
    PVOID pointer;
  };
  ULONG_PTR information;
};
using NtOpenFileFunction = LONG(NTAPI *)(HANDLE *, ACCESS_MASK, NtAttributes *, NtStatusBlock *, ULONG, ULONG);

/** NtOpenFile, called so that even a fault inside it is only a failed look (the walk then decides): no C++ object
 * lives in this frame, so structured exception handling may guard it. */
LONG nt_open_guarded(const NtOpenFileFunction function,
                     HANDLE *handle,
                     NtAttributes *attributes,
                     NtStatusBlock *status,
                     const ULONG options)
{
#  ifdef _MSC_VER
  __try {
    return function(handle,
                    FILE_READ_ATTRIBUTES | SYNCHRONIZE,
                    attributes,
                    status,
                    FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                    options);
  }
  __except (EXCEPTION_EXECUTE_HANDLER) {
    *handle = nullptr;
    return LONG(0xC0000005); /* STATUS_ACCESS_VIOLATION: a failed look, walked. */
  }
#  else
  return function(handle,
                  FILE_READ_ATTRIBUTES | SYNCHRONIZE,
                  attributes,
                  status,
                  FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                  options);
#  endif
}

NtOpenFileFunction nt_open_file()
{
  static const NtOpenFileFunction function = []() -> NtOpenFileFunction {
    const HMODULE ntdll = GetModuleHandleW(L"ntdll.dll");
    return ntdll ? reinterpret_cast<NtOpenFileFunction>(GetProcAddress(ntdll, "NtOpenFile")) : nullptr;
  }();
  return function;
}

/**
 * Whether `start` (a local drive's path: its root and components) holds no reparse point on the way, told by ONE
 * open of the whole name with OBJ_DONT_REPARSE over the volume's device path (`\Device\HarddiskVolumeN\...`, the
 * drive's DOS device; a `\??\` drive is a symbolic link object, which the flag would refuse too), instead of one probe
 * per component (Windows cost read: the walk was ~4.3x the baseline at 12 folders, quadratic in depth). True only
 * when the open succeeds, or stops at a missing name or a file after folders that held no reparse point: nothing past
 * it exists, so nothing is followed. Any reparse point on the way (STATUS_REPARSE_POINT_ENCOUNTERED), any other
 * answer, a drive that is no plain device (a `subst`, a share by `\??\` text), or a system without the flag: false,
 * and the component walk decides as before. `follow_last` false: the last component's own reparse point is not
 * followed (FILE_OPEN_REPARSE_POINT), as the walk wouldn't.
 */
bool no_reparse_on_the_way(const links::Rooted &start, const bool follow_last)
{
  /* Only `\\?\\X:\\` (a drive letter): anything else, an empty root (a relative target's) included, is walked. */
  if (start.type != links::Rooted::Type::Drive || start.root.size() != 7 || start.root.compare(0, 4, L"\\\\?\\") != 0 ||
      !std::iswalpha(start.root[4]) || start.root[5] != L':')
  {
    return false;
  }
  const NtOpenFileFunction nt_open = nt_open_file();
  const std::wstring device = drive_device(start.root);
  if (nt_open == nullptr || device.size() <= 8 || device.rfind(L"\\Device\\", 0) != 0 ||
      device.find(L'\0') != std::wstring::npos)
  {
    return false; /* No ntdll entry, or a drive that is no plain device: walked. */
  }
  std::wstring name = device;
  for (size_t i = 0; i < start.rest.size(); i++) {
    const std::wstring &part = start.rest[i];
    if (part == L"." || part == L".." || (i + 1 < start.rest.size() && part.find(L':') != std::wstring::npos)) {
      return false; /* A dot name, or a stream on the way (the walk refuses it): walked. */
    }
    name += L"\\" + part;
  }
  if (start.rest.empty()) {
    name += L"\\";
  }
  if (name.size() * sizeof(wchar_t) >= 0xFFFF) {
    return false; /* Longer than a native name holds: walked. */
  }
  constexpr ULONG case_insensitive = 0x00000040, dont_reparse = 0x00001000;
  constexpr ULONG for_backup = 0x00004000, synchronous = 0x00000020, open_reparse_point = 0x00200000;
  constexpr LONG name_not_found = LONG(0xC0000034), path_not_found = LONG(0xC000003A),
                 not_a_directory = LONG(0xC0000103);
  NtName text = {USHORT(name.size() * sizeof(wchar_t)), USHORT(name.size() * sizeof(wchar_t)), name.data()};
  NtAttributes attributes = {sizeof(NtAttributes), nullptr, &text, case_insensitive | dont_reparse, nullptr, nullptr};
  NtStatusBlock status = {};
  HANDLE handle = nullptr;
  const LONG result = nt_open_guarded(
      nt_open, &handle, &attributes, &status, for_backup | synchronous | (follow_last ? 0 : open_reparse_point));
  if (result >= 0) {
    if (handle != nullptr && handle != INVALID_HANDLE_VALUE) {
      CloseHandle(handle);
    }
    return true;
  }
  return result == name_not_found || result == path_not_found || result == not_a_directory;
}

}  // namespace

/** Whether `full` (a full Win32 name) is the null or the console device in the `\\.\` namespace: `NUL`, `CON`,
 * `CONIN$`, `CONOUT$` (what `nul` and `os.devnull` name). No file and no link: nothing on the way to walk (build3 head
 * 23: `os.open(os.devnull)` was refused as a link Angelo can't tell). Not `PRN`, `AUX`, `COM*` or `LPT*`: those can be
 * mapped to a network printer or redefined (`DefineDosDevice`), so they take the normal checks (the Head 21 read's
 * ESC-1). */
bool dos_device(const std::wstring &full)
{
  static const std::wstring prefix = L"\\\\.\\";
  if (full.size() <= prefix.size() || full.compare(0, prefix.size(), prefix) != 0) {
    return false;
  }
  std::wstring name = full.substr(prefix.size());
  if (name.find_first_of(L"\\/") != std::wstring::npos) {
    return false;
  }
  for (wchar_t &c : name) {
    c = wchar_t(towupper(c));
  }
  for (const wchar_t *device : {L"NUL", L"CON", L"CONIN$", L"CONOUT$"}) {
    if (name == device) {
      return true;
    }
  }
  return false;
}

links::Verdict links_walk(const std::wstring &path, const bool follow_last)
{
  const std::wstring full = full_path(path);
  if (full.empty()) {
    return links::Verdict::Unreadable;
  }
  if (dos_device(full)) {
    return links::Verdict::Safe;
  }
  const uint64_t now = generation.load(std::memory_order_acquire);
  if (cache.seen != now || cache.folders.size() > 4096) {
    cache.folders.clear();
    cache.drives.clear();
    cache.seen = now;
  }
  if (no_reparse_on_the_way(links::split_input(full), follow_last)) {
    return links::Verdict::Safe; /* One open, no reparse point on the way: nothing to follow. */
  }
  links::Walker walker;
  walker.probe = probe_component;
  walker.share_ok = [](const std::wstring &share) {
    if (noting_root_links > 0) {
      note_connected(share);
    }
    return lookup_may_open(share);
  };
  walker.drive_root = drive_root;
  walker.rest_plain = [](const std::wstring &folder, const std::deque<std::wstring> &rest, const bool follow) {
    /* The real prefix (a local drive's, `\\?\\X:\\...`) and what is left, as one path: the same one-open look. */
    links::Rooted at = links::split_input(folder);
    at.rest.insert(at.rest.end(), rest.begin(), rest.end());
    return no_reparse_on_the_way(at, follow);
  };
  walker.plain_folders = &cache.folders;
  walker.still_plain = [](const std::wstring &folder) {
    /* The entry itself, not followed (a reparse point's own attributes); the folders above it were checked in this
     * same walk (break-it-confine-reparse-b7d499c (4): a swap by another process between walks is seen). */
    const DWORD attributes = GetFileAttributesW(folder.c_str());
    return attributes != INVALID_FILE_ATTRIBUTES && (attributes & FILE_ATTRIBUTE_DIRECTORY) &&
           !(attributes & FILE_ATTRIBUTE_REPARSE_POINT);
  };
  return links::walk(full, follow_last, walker);
}

bool root_links_being_noted()
{
  return noting_root_links > 0;
}

RootLinksNoted::RootLinksNoted() : active_(!remote_region_has_begun())
{
  noting_root_links += active_ ? 1 : 0;
}

RootLinksNoted::~RootLinksNoted()
{
  noting_root_links -= active_ ? 1 : 0;
}

#else

RootLinksNoted::RootLinksNoted() : active_(false) {}

RootLinksNoted::~RootLinksNoted() {}

#endif

}  // namespace blender::angelo_guard
