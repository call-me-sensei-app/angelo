/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Where a path really leads (`bpy_angelo_guard_confine_winpath`). On Windows: the final spelling of a path (real
 * case, separators, `..`, a missing tail kept as named), what a junction on the way does to it, and which
 * components name another place. Elsewhere: the names are unchanged and only a symbolic link as the last component
 * counts.
 */

#include "testing/testing.h"

#include <string>
#include <vector>

#ifdef _WIN32
#  ifndef NOMINMAX
#    define NOMINMAX
#  endif
#  include <windows.h>

#  include <winioctl.h>

#  include <algorithm>
#  include <cstddef>
#  include <vector>
#else
#  include <cstdio>
#  include <cstdlib>
#  include <fcntl.h>
#  include <sys/stat.h>
#  include <unistd.h>
#endif

#include "bpy_angelo_guard_confine_winpath.hh"

namespace blender::angelo_guard::tests {

/** Win32's documented full-path normalization ("File path formats on Windows"), for the spelling rules on every
 * system: `.` and `..` segments resolved, a segment's single trailing period dropped, and the last segment's trailing
 * periods and spaces dropped. Windows' own run of the rules (`windows_plain`) uses GetFullPathNameW itself. */
static std::string documented_full_path(const std::string &path)
{
  const size_t root = path.rfind("\\\\", 0) == 0 ? path.find('\\', path.find('\\', 2) + 1) : 2;
  const std::string head = path.substr(0, root);
  std::vector<std::string> parts;
  size_t at = root + 1;
  while (at <= path.size()) {
    size_t end = path.find('\\', at);
    end = end == std::string::npos ? path.size() : end;
    std::string part = path.substr(at, end - at);
    const bool last = end == path.size();
    if (part == "..") {
      if (!parts.empty()) {
        parts.pop_back();
      }
    }
    else if (part != "." && !part.empty()) {
      if (last) {
        while (!part.empty() && (part.back() == '.' || part.back() == ' ')) {
          part.pop_back();
        }
      }
      else if (part.size() >= 2 && part.back() == '.' && part[part.size() - 2] != '.') {
        part.pop_back();
      }
      parts.push_back(part);
    }
    at = end + 1;
  }
  std::string full = head;
  for (const std::string &part : parts) {
    full += "\\" + part;
  }
  return parts.empty() ? head + "\\" : full;
}

static std::string plain(const std::string &path)
{
  return windows_plain_with(path, documented_full_path);
}

/* The re-read of piece 10 (MUST-2): `\\?\` folds only to the very file its plain spelling names. */
TEST(WindowsPlainTest, LongFormFoldsOnlyWhenThePlainSpellingIsTheSameFile)
{
  EXPECT_EQ(plain("\\\\?\\C:\\Docs\\export.glb"), "C:\\Docs\\export.glb");
  EXPECT_EQ(plain("\\\\?\\UNC\\server\\share\\out\\x.png"), "\\\\server\\share\\out\\x.png");
  EXPECT_EQ(plain("\\\\?\\C:\\Docs\\export.glb."), "");          /* Literal `export.glb.`: plain is export.glb. */
  EXPECT_EQ(plain("\\\\?\\C:\\Docs\\export.glb "), "");          /* A trailing space. */
  EXPECT_EQ(plain("\\\\?\\C:\\Users\\p\\AngeloOut.\\x.blend"), ""); /* A sibling folder `AngeloOut.`. */
  EXPECT_EQ(plain("\\\\?\\C:\\Docs\\.\\x"), "");
  EXPECT_EQ(plain("\\\\?\\C:\\Docs\\..\\x"), "");
  EXPECT_EQ(plain("\\\\?\\C:\\Docs/x"), ""); /* `/` is a name's character in the long form. */
  EXPECT_EQ(plain("\\\\?\\UNC\\server"), "");
  EXPECT_EQ(plain("\\\\?\\GLOBALROOT\\Device\\HarddiskVolume3\\x"), "");
  EXPECT_EQ(plain("\\\\?\\Volume{0b1a52a3-0000-0000-0000-000000000000}\\x"), "");
}

/* The re-read of piece 10 (SHOULD-1): spellings Win32 opens elsewhere than a judge reads them are refused. */
TEST(WindowsPlainTest, NtDeviceRootAndDriveRelativeSpellingsAreRefused)
{
  EXPECT_EQ(plain("\\??\\C:\\Users\\p\\x"), "");
  EXPECT_EQ(plain("/?" "?/C:/Users/p/x"), ""); /* Split: `??/` is a trigraph. */
  EXPECT_EQ(plain("\\\\.\\C:\\x"), "");
  EXPECT_EQ(plain("//./pipe/x"), "");
  EXPECT_EQ(plain("//?/C:/x"), "");
  EXPECT_EQ(plain("\\Users\\p\\x"), "");
  EXPECT_EQ(plain("C:x.blend"), "");
  EXPECT_EQ(plain("C:"), "");
  EXPECT_EQ(plain("\\\\server"), "");
  EXPECT_EQ(plain(""), "");
}

TEST(WindowsPlainTest, DriveShareAndRelativePathsKeepTheirSpelling)
{
  EXPECT_EQ(plain("C:/Docs/x.png"), "C:\\Docs\\x.png");
  EXPECT_EQ(plain("c:\\docs\\x.png"), "c:\\docs\\x.png");
  EXPECT_EQ(plain("\\\\server\\share\\x"), "\\\\server\\share\\x");
  EXPECT_EQ(plain("renders\\x.png"), "renders\\x.png");
}

#ifdef _WIN32

namespace {

std::wstring wide(const std::string &utf8)
{
  const int size = MultiByteToWideChar(CP_UTF8, 0, utf8.data(), int(utf8.size()), nullptr, 0);
  std::wstring result(size_t(std::max(size, 0)), L'\0');
  MultiByteToWideChar(CP_UTF8, 0, utf8.data(), int(utf8.size()), result.data(), size);
  return result;
}

std::string narrow(const std::wstring &text)
{
  const int size = WideCharToMultiByte(CP_UTF8, 0, text.data(), int(text.size()), nullptr, 0, nullptr, nullptr);
  std::string result(size_t(std::max(size, 0)), '\0');
  WideCharToMultiByte(CP_UTF8, 0, text.data(), int(text.size()), result.data(), size, nullptr, nullptr);
  return result;
}

std::string lower(std::string text)
{
  for (char &c : text) {
    if (c >= 'A' && c <= 'Z') {
      c = char(c - 'A' + 'a'); /* ASCII only: other bytes are parts of UTF-8 sequences. */
    }
  }
  return text;
}

/** The layout of a mount point's reparse data (ntifs.h, not in the SDK's user-mode headers). */
struct MountPoint {
  DWORD tag;
  WORD data_length;
  WORD reserved;
  WORD substitute_offset;
  WORD substitute_length;
  WORD print_offset;
  WORD print_length;
  WCHAR buffer[1];
};

/** Makes `link` (a new, empty folder) a junction to `target`, as `_winapi.CreateJunction` does. */
bool make_junction(const std::wstring &link, const std::wstring &target)
{
  if (!CreateDirectoryW(link.c_str(), nullptr)) {
    return false;
  }
  HANDLE handle = CreateFileW(link.c_str(),
                              GENERIC_WRITE,
                              0,
                              nullptr,
                              OPEN_EXISTING,
                              FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
                              nullptr);
  if (handle == INVALID_HANDLE_VALUE) {
    return false;
  }
  const std::wstring substitute = L"\\??\\" + target;
  const size_t names = (substitute.size() + 1 + target.size() + 1) * sizeof(WCHAR);
  std::vector<char> bytes(offsetof(MountPoint, buffer) + names, 0);
  MountPoint *point = reinterpret_cast<MountPoint *>(bytes.data());
  point->tag = IO_REPARSE_TAG_MOUNT_POINT;
  point->data_length = WORD(bytes.size() - offsetof(MountPoint, substitute_offset));
  point->substitute_offset = 0;
  point->substitute_length = WORD(substitute.size() * sizeof(WCHAR));
  point->print_offset = WORD((substitute.size() + 1) * sizeof(WCHAR));
  point->print_length = WORD(target.size() * sizeof(WCHAR));
  std::copy(substitute.begin(), substitute.end(), point->buffer);
  std::copy(target.begin(), target.end(), point->buffer + substitute.size() + 1);
  DWORD returned = 0;
  const BOOL made = DeviceIoControl(
      handle, FSCTL_SET_REPARSE_POINT, bytes.data(), DWORD(bytes.size()), nullptr, 0, &returned, nullptr);
  CloseHandle(handle);
  return made != FALSE;
}

class WinPathTest : public testing::Test {
 protected:
  std::wstring base_; /* A fresh folder in the temporary folder, in its final spelling. */

  void SetUp() override
  {
    wchar_t temp[MAX_PATH + 1];
    ASSERT_GT(GetTempPathW(MAX_PATH + 1, temp), 0u);
    base_ = std::wstring(temp) + L"angelo_winpath_" + std::to_wstring(GetCurrentProcessId());
    ASSERT_TRUE(CreateDirectoryW(base_.c_str(), nullptr) || GetLastError() == ERROR_ALREADY_EXISTS);
    const std::string spelled = final_path(narrow(base_));
    ASSERT_FALSE(spelled.empty());
    base_ = wide(spelled);
  }

  void TearDown() override
  {
    /* Links first (removing one removes the link, never its target's files). */
    DeleteFileW((base_ + L"\\file-link.txt").c_str());
    RemoveDirectoryW((base_ + L"\\link").c_str());
    DeleteFileW((base_ + L"\\target\\file.txt").c_str());
    RemoveDirectoryW((base_ + L"\\target").c_str());
    RemoveDirectoryW((base_ + L"\\Plain").c_str());
    RemoveDirectoryW(base_.c_str());
  }

  std::string at(const wchar_t *name) const
  {
    return narrow(base_ + L"\\" + name);
  }
};

}  // namespace

TEST_F(WinPathTest, ExistingFolderKeepsItsRealSpellingWhateverTheCaseAndSeparators)
{
  ASSERT_TRUE(CreateDirectoryW((base_ + L"\\Plain").c_str(), nullptr));
  std::string asked = lower(at(L"Plain"));
  std::replace(asked.begin(), asked.end(), '\\', '/');
  EXPECT_EQ(final_path(asked), at(L"Plain"));
  EXPECT_EQ(final_path(at(L"Plain\\.\\..\\Plain\\")), at(L"Plain"));
  EXPECT_FALSE(reparse_in_any_component(at(L"Plain")));
  EXPECT_FALSE(last_component_links(at(L"Plain"), -1));
}

TEST_F(WinPathTest, MissingTailFollowsTheLongestExistingPrefixAsNamed)
{
  ASSERT_TRUE(CreateDirectoryW((base_ + L"\\Plain").c_str(), nullptr));
  EXPECT_EQ(final_path(lower(at(L"Plain")) + "\\New Folder\\File.TXT"), at(L"Plain\\New Folder\\File.TXT"));
  EXPECT_FALSE(reparse_in_any_component(at(L"Plain\\New Folder\\File.TXT")));
}

TEST_F(WinPathTest, JunctionOnTheWayIsResolvedAndCounted)
{
  const std::wstring target = base_ + L"\\target";
  ASSERT_TRUE(CreateDirectoryW(target.c_str(), nullptr));
  HANDLE file = CreateFileW(
      (target + L"\\file.txt").c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
  ASSERT_NE(file, INVALID_HANDLE_VALUE);
  CloseHandle(file);
  ASSERT_TRUE(make_junction(base_ + L"\\link", target));

  /* Through the junction to a file that exists: the file's own place. */
  EXPECT_EQ(lower(final_path(at(L"link\\file.txt"))), lower(at(L"target\\file.txt")));
  /* To a file that doesn't exist yet: the junction itself is the last existing component, never followed. */
  EXPECT_EQ(lower(final_path(at(L"link\\new.txt"))), lower(at(L"link\\new.txt")));
  /* Either way the junction is on the way. */
  EXPECT_TRUE(reparse_in_any_component(at(L"link\\file.txt")));
  EXPECT_TRUE(reparse_in_any_component(at(L"link\\new.txt")));
  EXPECT_TRUE(reparse_in_any_component(at(L"link")));
  EXPECT_TRUE(last_component_links(at(L"link"), -1));
  EXPECT_FALSE(reparse_in_any_component(at(L"target\\file.txt")));
  EXPECT_FALSE(last_component_links(at(L"target"), -1));
  EXPECT_FALSE(last_component_links(at(L"link\\file.txt"), -1));
}

TEST_F(WinPathTest, FollowedPathFollowsTheLastLinkToo)
{
  /* What a program start opens (may_run's `resolved_program`): the real file, as `vouch_program` records it. */
  const std::wstring target = base_ + L"\\target";
  ASSERT_TRUE(CreateDirectoryW(target.c_str(), nullptr));
  HANDLE file = CreateFileW(
      (target + L"\\file.txt").c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
  ASSERT_NE(file, INVALID_HANDLE_VALUE);
  CloseHandle(file);
  ASSERT_TRUE(make_junction(base_ + L"\\link", target));

  EXPECT_EQ(lower(final_path(at(L"link"))), lower(at(L"link"))); /* Not followed: the link itself. */
  EXPECT_EQ(lower(followed_path(at(L"link"))), lower(at(L"target")));
  EXPECT_EQ(lower(followed_path(lower(at(L"link\\file.txt")))), lower(at(L"target\\file.txt")));
  EXPECT_EQ(followed_path(at(L"target\\file.txt")), final_path(at(L"target\\file.txt")));
  EXPECT_EQ(followed_path(at(L"target\\missing.exe")), "");
  EXPECT_EQ(followed_path(""), "");
  EXPECT_EQ(followed_path("NUL"), "");

  /* A file symbolic link (an installer's or a person's link to the program). */
  const std::wstring link = base_ + L"\\file-link.txt";
  if (!CreateSymbolicLinkW(
          link.c_str(), (target + L"\\file.txt").c_str(), SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE))
  {
    GTEST_SKIP() << "[environment] this host can't make a file symbolic link (Developer Mode off, no privilege)";
  }
  EXPECT_TRUE(last_component_links(at(L"file-link.txt"), -1));
  EXPECT_EQ(lower(final_path(at(L"file-link.txt"))), lower(at(L"file-link.txt")));
  EXPECT_EQ(lower(followed_path(at(L"file-link.txt"))), lower(at(L"target\\file.txt")));
}

TEST_F(WinPathTest, DevicesAndInvalidNamesAnswerNothing)
{
  EXPECT_EQ(final_path(""), "");
  EXPECT_EQ(final_path("NUL"), "");
  EXPECT_EQ(final_path("\\\\.\\pipe\\angelo"), "");
  EXPECT_EQ(final_path(std::string("C:\\a\0b", 6)), "");
  EXPECT_TRUE(reparse_in_any_component(""));
  EXPECT_TRUE(reparse_in_any_component("\\\\.\\pipe\\angelo"));
}

TEST_F(WinPathTest, MissingVolumeAnswersNothing)
{
  /* A drive letter with no volume (the last free one, if any). */
  const DWORD drives = GetLogicalDrives();
  for (char letter = 'Z'; letter > 'D'; letter--) {
    if (!(drives & (1u << (letter - 'A')))) {
      EXPECT_EQ(final_path(std::string(1, letter) + ":\\angelo\\file.txt"), "");
      return;
    }
  }
}

#else

TEST(WinPathTest, ElsewhereNamesAreUnchangedAndOnlyALastLinkCounts)
{
  EXPECT_EQ(final_path("/tmp/../tmp/Some File"), "/tmp/../tmp/Some File");
  EXPECT_EQ(followed_path("/tmp/../tmp/Some File"), "/tmp/../tmp/Some File"); /* POSIX: realpath. */
  EXPECT_FALSE(reparse_in_any_component("/tmp/anything"));

  char folder[] = "/tmp/angelo_winpath_XXXXXX";
  ASSERT_NE(mkdtemp(folder), nullptr);
  const std::string base = folder;
  const std::string file = base + "/file.txt";
  const std::string link = base + "/link";
  FILE *handle = fopen(file.c_str(), "w");
  ASSERT_NE(handle, nullptr);
  fclose(handle);
  ASSERT_EQ(symlink(file.c_str(), link.c_str()), 0);
  EXPECT_TRUE(last_component_links(link, -1));
  EXPECT_FALSE(last_component_links(file, -1));
  EXPECT_FALSE(last_component_links(base + "/missing", -1));
  const int descriptor = open(base.c_str(), O_RDONLY);
  ASSERT_GE(descriptor, 0);
  EXPECT_TRUE(last_component_links("link", descriptor));
  EXPECT_FALSE(last_component_links("file.txt", descriptor));
  close(descriptor);
  unlink(link.c_str());
  unlink(file.c_str());
  rmdir(base.c_str());
}

#endif

}  // namespace blender::angelo_guard::tests
