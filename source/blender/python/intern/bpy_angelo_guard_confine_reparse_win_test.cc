/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Windows: links already in a folder, on a real file system, whose target is a share nobody connected, are never
 * followed by the judge or a lookup (break-it-confine-reparse-b7d499c S2). Written against the judge and
 * `open_for_lookup` only, so the fail-first branch (test/v2.1.0-confine-reparse-red, a544c4b4b4d's code) compiles it
 * and fails it. The targets are `.invalid` hosts: nothing can connect to them, and a refusal means nothing tried.
 * Symbolic links need the right to make them (an administrator, or Developer Mode): skipped with the reason otherwise;
 * junctions need no right.
 */

#include "testing/testing.h"

#ifdef _WIN32

#  ifndef NOMINMAX
#    define NOMINMAX
#  endif
#  include <windows.h>
#  include <lm.h>
#  pragma comment(lib, "netapi32.lib") /* NetUseEnum, also where the fail-first base doesn't link it. */
#  include <winioctl.h>

#  include <cstdio>
#  include <cstdlib>
#  include <string>
#  include <vector>

#  include "bpy_angelo_guard_confine_changes.hh"
#  include "bpy_angelo_guard_confine_paths.hh"
#  include "bpy_angelo_guard_confine_remote.hh"

namespace blender::angelo_guard::tests {

namespace {

const wchar_t *const SHARE = L"\\\\angelo-probe.invalid\\s";

std::string utf8(const std::wstring &wide)
{
  const int size = WideCharToMultiByte(CP_UTF8, 0, wide.data(), int(wide.size()), nullptr, 0, nullptr, nullptr);
  std::string out(size_t(size > 0 ? size : 0), '\0');
  if (size > 0) {
    WideCharToMultiByte(CP_UTF8, 0, wide.data(), int(wide.size()), out.data(), size, nullptr, nullptr);
  }
  return out;
}

bool symlink(const std::wstring &at, const std::wstring &target)
{
  return CreateSymbolicLinkW(
             at.c_str(), target.c_str(), SYMBOLIC_LINK_FLAG_DIRECTORY | SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE) !=
         0;
}

/** A junction (mount point) at the empty folder `at` to `nt_target` (`\??\C:\x` or `\??\Volume{G}\x`). */
bool junction(const std::wstring &at, const std::wstring &nt_target)
{
  if (!CreateDirectoryW(at.c_str(), nullptr)) {
    return false;
  }
  HANDLE handle = CreateFileW(at.c_str(), GENERIC_WRITE, 0, nullptr, OPEN_EXISTING,
                              FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, nullptr);
  if (handle == INVALID_HANDLE_VALUE) {
    return false;
  }
  const size_t names = (nt_target.size() + 1 + 1) * sizeof(wchar_t); /* Substitute, NUL, empty print name, NUL. */
  std::vector<unsigned char> data(8 + 8 + names, 0);
  auto put16 = [&](size_t at_byte, size_t value) {
    data[at_byte] = uint8_t(value);
    data[at_byte + 1] = uint8_t(value >> 8);
  };
  put16(0, IO_REPARSE_TAG_MOUNT_POINT & 0xFFFF);
  put16(2, IO_REPARSE_TAG_MOUNT_POINT >> 16);
  put16(4, data.size() - 8);
  put16(8, 0);                                      /* SubstituteNameOffset */
  put16(10, nt_target.size() * sizeof(wchar_t));    /* SubstituteNameLength */
  put16(12, (nt_target.size() + 1) * sizeof(wchar_t)); /* PrintNameOffset */
  put16(14, 0);                                     /* PrintNameLength */
  for (size_t i = 0; i < nt_target.size(); i++) {
    put16(16 + i * 2, nt_target[i]);
  }
  DWORD returned = 0;
  const bool made = DeviceIoControl(handle, FSCTL_SET_REPARSE_POINT, data.data(), DWORD(data.size()), nullptr, 0,
                                    &returned, nullptr) != 0;
  CloseHandle(handle);
  return made;
}

class ConfinementLinksOnDisk : public testing::Test {
 protected:
  std::wstring root_;
  Folders folders_; /* No roots: whatever it refuses here, the links decide. */

  void SetUp() override
  {
    lookups_walk_always(true); /* The walk runs only with Strict script protection, on a confined thread; these tests
                                * ask it on every thread. */
    wchar_t temp[MAX_PATH + 1];
    ASSERT_GT(GetTempPathW(MAX_PATH, temp), DWORD(0));
    /* A long name: the link's 8.3 name is then really another name. */
    root_ = std::wstring(temp) + L"angelo-links-on-disk-" + std::to_wstring(GetCurrentProcessId()) + L"-" +
            std::to_wstring(GetTickCount64());
    ASSERT_TRUE(CreateDirectoryW(root_.c_str(), nullptr));
    if (!symlink(root_ + L"\\remote-share-link-with-a-long-name", SHARE)) {
      const DWORD error = GetLastError();
      RemoveDirectoryW(root_.c_str());
      GTEST_SKIP() << "this account may not make symbolic links (error " << error << ")";
    }
  }

  void TearDown() override
  {
    lookups_walk_always(false);
    /* Links are removed as links (never followed), then the folders, deepest first. */
    std::wstring command = L"cmd /c rmdir /s /q \"" + root_ + L"\"";
    _wsystem(command.c_str());
  }

  std::wstring remote() const
  {
    return root_ + L"\\remote-share-link-with-a-long-name";
  }

  /** Whether `path` is refused (by the judge, for a read, and by a lookup), and never as allowed. */
  void expect_refused(const std::wstring &path, Refusal as)
  {
    /* The helpers failing proves nothing by itself (a `.invalid` host fails to resolve on any build): nothing may
     * have been handed to the system (re-read SHOULD). */
    const uint64_t before = lookups_opened();
    EXPECT_EQ(folders_.judge(utf8(path), false), as) << utf8(path);
    EXPECT_EQ(open_for_lookup(path, true), INVALID_HANDLE_VALUE) << utf8(path);
    /* The stat-family helpers ask the same walk (each follows every link it is given). */
    EXPECT_FALSE(lookup_may_touch(utf8(path))) << utf8(path);
    BLI_stat_t info;
    EXPECT_NE(lookup_stat(utf8(path), &info), 0) << utf8(path);
    EXPECT_EQ(lookups_opened(), before) << utf8(path);
  }
};

}  // namespace

TEST_F(ConfinementLinksOnDisk, a_dos_device_is_no_link)
{
  /* `os.devnull` and its spellings name the null device: nothing on the way to walk (build3 head 23). */
  const wchar_t *names[] = {L"nul", L"NUL", L"\\\\.\\nul", L"con", L"CONOUT$"};
  for (size_t i = 0; i < sizeof(names) / sizeof(names[0]); i++) {
    EXPECT_EQ(int(links_walk(names[i], true)), int(links::Verdict::Safe)) << "name " << i;
  }
}

TEST_F(ConfinementLinksOnDisk, links_to_an_unconnected_share_in_every_shape_are_the_network)
{
  const std::wstring link = remote();
  symlink(root_ + L"\\chain", link);    /* A local link to the link. */
  symlink(root_ + L"\\relative", L"remote-share-link-with-a-long-name");
  expect_refused(link + L"\\x.txt", Refusal::Network);
  expect_refused(root_ + L"\\chain\\x.txt", Refusal::Network);
  expect_refused(root_ + L"\\relative\\x.txt", Refusal::Network);
  expect_refused(link + L":x", Refusal::Network);       /* A stream of the link is the link. */
  expect_refused(link + L"::$DATA", Refusal::Network);
  expect_refused(L"\\\\.\\" + link + L"\\x.txt", Refusal::Network); /* The drive by its device name. */
  wchar_t shorter[MAX_PATH + 1];
  const DWORD length = GetShortPathNameW(link.c_str(), shorter, MAX_PATH);
  if (length > 0 && length < MAX_PATH && _wcsicmp(shorter, link.c_str()) != 0) {
    expect_refused(std::wstring(shorter) + L"\\x.txt", Refusal::Network);
  }
}

TEST_F(ConfinementLinksOnDisk, a_junction_and_dot_dot_behind_it_lead_to_the_real_folder)
{
  /* J is a junction to sub\a\b; rel in it is `..\..\evil`; sub\evil links to the share. The real parent of rel is
   * sub\a\b (so `..\..` is sub), its text parent J (whose `..\..` would be above root_). */
  const std::wstring sub = root_ + L"\\sub";
  CreateDirectoryW(sub.c_str(), nullptr);
  CreateDirectoryW((sub + L"\\a").c_str(), nullptr);
  CreateDirectoryW((sub + L"\\a\\b").c_str(), nullptr);
  ASSERT_TRUE(junction(root_ + L"\\J", L"\\??\\" + sub + L"\\a\\b"));
  ASSERT_TRUE(CreateSymbolicLinkW((sub + L"\\a\\b\\rel").c_str(), L"..\\..\\evil",
                                  SYMBOLIC_LINK_FLAG_DIRECTORY | SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE));
  ASSERT_TRUE(symlink(sub + L"\\evil", SHARE));
  expect_refused(root_ + L"\\J\\rel\\x.txt", Refusal::Network);
  /* A junction to a plain local folder is followed as before. */
  CreateDirectoryW((root_ + L"\\plain").c_str(), nullptr);
  ASSERT_TRUE(junction(root_ + L"\\to-plain", L"\\??\\" + root_ + L"\\plain"));
  EXPECT_EQ(folders_.judge(utf8(root_ + L"\\to-plain\\x.txt"), false), Refusal::None);
}

TEST_F(ConfinementLinksOnDisk, a_link_below_a_folder_to_a_share_windows_has_connected_works)
{
  /* Re-read MUST-B (a usability regression against a544c4b4b4d): `Project\Textures -> \\nas\tex` below a project
   * folder, the project opened after any script ran, while the person has the share connected. Runs where Windows has
   * a live connection (iso-win: `net use \\localhost\C$` first); says so and checks nothing otherwise. */
  std::wstring connected;
  LPBYTE buffer = nullptr;
  DWORD read = 0, total = 0, resume = 0;
  if (NetUseEnum(nullptr, 1, &buffer, MAX_PREFERRED_LENGTH, &read, &total, &resume) == NERR_Success && buffer) {
    const USE_INFO_1 *uses = reinterpret_cast<const USE_INFO_1 *>(buffer);
    for (DWORD i = 0; i < read && connected.empty(); i++) {
      if (uses[i].ui1_status == USE_OK && uses[i].ui1_remote != nullptr && uses[i].ui1_remote[0] == L'\\') {
        connected = uses[i].ui1_remote;
      }
    }
  }
  if (buffer) {
    NetApiBufferFree(buffer);
  }
  if (connected.empty()) {
    /* Where the run promised one (`net use` first, ANGELO_TEST_LIVE_SHARE=1), its absence fails (head-7 NIT). */
    const char *promised = std::getenv("ANGELO_TEST_LIVE_SHARE");
    ASSERT_FALSE(promised != nullptr && promised[0] == '1') << "ANGELO_TEST_LIVE_SHARE=1 but no live connection";
    std::printf("[ note ] Windows has no live share connection here: the connected-share link is not checked\n");
    return;
  }
  const std::wstring project = root_ + L"\\project";
  ASSERT_TRUE(CreateDirectoryW(project.c_str(), nullptr));
  ASSERT_TRUE(symlink(project + L"\\textures", connected));
  const std::wstring name = project + L"\\textures\\angelo-no-such-file.png";
  EXPECT_EQ(folders_.judge(utf8(name), false), Refusal::None) << utf8(connected);
  const uint64_t before = lookups_opened();
  BLI_stat_t info;
  lookup_stat(utf8(name), &info); /* Missing, but asked of the system: not refused. */
  EXPECT_EQ(lookups_opened(), before + 1) << utf8(connected);
}

TEST_F(ConfinementLinksOnDisk, a_dot_dot_past_the_root_and_a_substituted_drive_are_walked_as_windows_reads_them)
{
  /* Head-7 MUST-A': a relative target whose `..` climbs past the drive's root goes on into the object namespace
   * (`C:\..\UNC\host\s`): refused. A drive substituted onto a share nobody connected (`subst`, here
   * DefineDosDeviceW) is that share, and one substituted onto a folder holding the link reaches it. */
  std::wstring climb;
  for (size_t i = 0; i < 32; i++) {
    climb += L"..\\";
  }
  ASSERT_TRUE(CreateSymbolicLinkW((root_ + L"\\climb").c_str(), (climb + L"UNC\\angelo-probe.invalid\\s").c_str(),
                                  SYMBOLIC_LINK_FLAG_DIRECTORY | SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE));
  expect_refused(root_ + L"\\climb\\x.txt", Refusal::Private);
  wchar_t letter = 0;
  const DWORD used = GetLogicalDrives();
  for (wchar_t candidate = L'Z'; candidate > L'D'; candidate--) {
    if (!(used & (1u << (candidate - L'A')))) {
      letter = candidate;
      break;
    }
  }
  if (letter == 0) {
    std::printf("[ note ] no free drive letter: the substituted drives are not checked\n");
    return;
  }
  const wchar_t drive[3] = {letter, L':', L'\0'};
  /* What Windows says the letter stands for, and what the walk makes of a path on it (for the report). */
  auto describe = [&](const std::wstring &path) {
    wchar_t target[1024] = {};
    const DWORD length = QueryDosDeviceW(drive, target, 1024);
    std::printf("[ note ] %ls -> %ls (QueryDosDeviceW %lu, error %lu); walk(%ls) = %d\n", drive, target,
                (unsigned long)length, (unsigned long)(length ? 0 : GetLastError()), path.c_str(),
                int(links_walk(path, true)));
  };
  const std::wstring on_drive = std::wstring(drive) + L"\\x.txt";
  ASSERT_TRUE(DefineDosDeviceW(DDD_RAW_TARGET_PATH, drive, L"\\??\\UNC\\angelo-probe.invalid\\s"));
  links_region_began(); /* A new region: what the drive stood for before is asked again. */
  describe(on_drive);
  expect_refused(on_drive, Refusal::Network);
  DefineDosDeviceW(DDD_RAW_TARGET_PATH | DDD_REMOVE_DEFINITION | DDD_EXACT_MATCH_ON_REMOVE, drive,
                   L"\\??\\UNC\\angelo-probe.invalid\\s");
  ASSERT_TRUE(DefineDosDeviceW(0, drive, root_.c_str())); /* As `subst X: <root_>` does. */
  links_region_began();
  const std::wstring through = std::wstring(drive) + L"\\remote-share-link-with-a-long-name\\x.txt";
  describe(through);
  expect_refused(through, Refusal::Network);
  describe(std::wstring(drive) + L"\\plain-file.txt");
  EXPECT_EQ(folders_.judge(utf8(std::wstring(drive) + L"\\plain-file.txt"), false), Refusal::None);
  DefineDosDeviceW(DDD_REMOVE_DEFINITION, drive, nullptr);
  links_region_began();
}

TEST_F(ConfinementLinksOnDisk, dot_dot_after_a_name_in_a_target_is_refused)
{
  /* MUST-A: rel is `J2\..\evil`, J2 is `deep\deeper`, evil links to the share. Windows reads `J2\..` by its text
   * (repo\evil); the walk would follow J2 (repo\deep\deeper, missing). Refused, failing closed, either way. */
  const std::wstring repo = root_ + L"\\repo";
  CreateDirectoryW(repo.c_str(), nullptr);
  const DWORD flags = SYMBOLIC_LINK_FLAG_DIRECTORY | SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE;
  ASSERT_TRUE(CreateSymbolicLinkW((repo + L"\\rel").c_str(), L"J2\\..\\evil", flags));
  ASSERT_TRUE(CreateSymbolicLinkW((repo + L"\\J2").c_str(), L"deep\\deeper", flags));
  ASSERT_TRUE(symlink(repo + L"\\evil", SHARE));
  expect_refused(repo + L"\\rel\\x.txt", Refusal::Private);
  /* A leading `..` stays walked: a link to a sibling folder by `..\shared` is followed. */
  CreateDirectoryW((root_ + L"\\shared").c_str(), nullptr);
  ASSERT_TRUE(CreateSymbolicLinkW((repo + L"\\sibling").c_str(), L"..\\shared", flags));
  EXPECT_EQ(folders_.judge(utf8(repo + L"\\sibling\\x.txt"), false), Refusal::None);
}

TEST_F(ConfinementLinksOnDisk, a_volume_target_is_walked_and_other_device_targets_refused)
{
  /* A junction to this volume by its GUID, into a folder holding the link: found. */
  wchar_t volume[MAX_PATH + 1];
  const std::wstring drive = root_.substr(0, 3);
  ASSERT_TRUE(GetVolumeNameForVolumeMountPointW(drive.c_str(), volume, MAX_PATH));
  const std::wstring by_volume = std::wstring(volume) + root_.substr(3); /* `\\?\Volume{G}\...\root` */
  ASSERT_TRUE(junction(root_ + L"\\vol", L"\\??\\" + by_volume.substr(4)));
  expect_refused(root_ + L"\\vol\\remote-share-link-with-a-long-name\\x.txt", Refusal::Network);
  /* The network in a spelling the system accepts but no rule admits: refused, never followed. */
  if (CreateSymbolicLinkW((root_ + L"\\global").c_str(), L"\\\\?\\Global\\UNC\\angelo-probe.invalid\\s",
                          SYMBOLIC_LINK_FLAG_DIRECTORY | SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE))
  {
    EXPECT_NE(folders_.judge(utf8(root_ + L"\\global\\x.txt"), false), Refusal::None);
    EXPECT_EQ(open_for_lookup(root_ + L"\\global\\x.txt", true), INVALID_HANDLE_VALUE);
  }
}

TEST_F(ConfinementLinksOnDisk, a_long_path_through_a_link_is_walked_whole)
{
  std::wstring deep = L"\\\\?\\" + root_;
  while (deep.size() < 300) {
    deep += L"\\a-folder-with-a-long-name";
    ASSERT_TRUE(CreateDirectoryW(deep.c_str(), nullptr));
  }
  ASSERT_TRUE(symlink(deep + L"\\remote", SHARE));
  expect_refused(deep.substr(4) + L"\\remote\\x.txt", Refusal::Network);
}

TEST_F(ConfinementLinksOnDisk, a_judged_rename_drops_what_was_found_plain)
{
  /* A folder walked (found plain), then renamed away by a judged move and replaced by a link: the next judge sees the
   * link (break-it-confine-reparse-b7d499c M5). */
  const std::wstring moving = root_ + L"\\moving";
  ASSERT_TRUE(CreateDirectoryW(moving.c_str(), nullptr));
  EXPECT_EQ(folders_.judge(utf8(moving + L"\\x.txt"), false), Refusal::None);
  EXPECT_EQ(folders_.judge(utf8(moving), true, -1, false, Change::Moved), Refusal::None); /* The judged rename. */
  ASSERT_TRUE(MoveFileW(moving.c_str(), (root_ + L"\\moved").c_str()));
  ASSERT_TRUE(symlink(moving, SHARE));
  expect_refused(moving + L"\\x.txt", Refusal::Network);
  /* A rename nothing judged (another process's): seen too, by the walk's re-check of what it found plain before. */
  const std::wstring other = root_ + L"\\other";
  ASSERT_TRUE(CreateDirectoryW(other.c_str(), nullptr));
  EXPECT_EQ(folders_.judge(utf8(other + L"\\x.txt"), false), Refusal::None);
  ASSERT_TRUE(MoveFileW(other.c_str(), (root_ + L"\\other-moved").c_str()));
  ASSERT_TRUE(symlink(other, SHARE));
  expect_refused(other + L"\\x.txt", Refusal::Network);
}

TEST_F(ConfinementLinksOnDisk, ordinary_folders_and_names_are_untouched)
{
  const std::wstring kit = root_ + L"\\redirector-kit";
  ASSERT_TRUE(CreateDirectoryW(kit.c_str(), nullptr));
  EXPECT_EQ(folders_.judge(utf8(kit + L"\\mup-device.txt"), false), Refusal::None);
  CreateDirectoryW((root_ + L"\\local").c_str(), nullptr);
  ASSERT_TRUE(symlink(root_ + L"\\to-local", root_ + L"\\local"));
  EXPECT_EQ(folders_.judge(utf8(root_ + L"\\to-local\\x.txt"), false), Refusal::None);
}

}  // namespace blender::angelo_guard::tests

#endif
