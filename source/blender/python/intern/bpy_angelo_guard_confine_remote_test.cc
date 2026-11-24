/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Remote names never reach the system through the confinement's own lookups (`bpy_angelo_guard_confine_remote`,
 * BREAKIT review-confine-fixes-3cb71ae002d MUST-1): the grammar and the connected shares on every system, and on
 * Windows that no lookup a script's name can reach (the judge, `trusted`, `agent_made`, `script_wrote`, `forget`,
 * identities, final and followed paths, links, a file's facts) passes an unconnected share's name to `CreateFileW`.
 */

#include "testing/testing.h"

#ifdef _WIN32
#  ifndef NOMINMAX
#    define NOMINMAX
#  endif
#  include <windows.h>
#endif

#include <cstdio>
#include <cwchar>
#include <string>

#include "bpy_angelo_guard_confine_paths.hh"
#include "bpy_angelo_guard_confine_remote.hh"
#include "bpy_angelo_guard_confine_vouches.hh"
#include "bpy_angelo_guard_confine_winpath.hh"

namespace blender::angelo_guard::tests {

TEST(ConfinementRemoteLookups, the_grammar_knows_a_remote_name_on_every_system)
{
  for (const wchar_t *remote : {L"\\\\host\\share\\x", L"//host/share/x", L"\\\\?\\UNC\\host\\share\\x",
                                L"\\??\\UNC\\host\\share\\x", L"\\\\.\\UNC\\host\\x", L"\\\\host@SSL\\DavWWWRoot\\x",
                                L"\\\\.\\pipe\\name", L"\\\\?\\GLOBALROOT\\Device\\x", L"/\\host\\x",
                                L"\\??\\Volume{0}\\x"})
  {
    EXPECT_TRUE(windows_names_remote(remote)) << std::string(remote, remote + wcslen(remote));
  }
  for (const wchar_t *local :
       {L"C:\\x", L"c:/x", L"\\\\?\\C:\\x", L"\\\\.\\D:\\x", L"\\??\\C:\\x", L"relative\\x", L"\\x", L""})
  {
    EXPECT_FALSE(windows_names_remote(local)) << std::string(local, local + wcslen(local));
  }
}

TEST(ConfinementRemoteLookups, a_share_is_named_the_same_way_in_every_spelling)
{
  for (const wchar_t *spelling : {L"\\\\Host\\Share\\x", L"//HOST/share/x/y", L"\\\\?\\UNC\\host\\SHARE\\x",
                                  L"\\??\\UNC\\host\\share", L"\\\\.\\unc\\host\\share\\"})
  {
    EXPECT_EQ(windows_share_of(spelling), std::wstring(L"host\\share"));
  }
  for (const wchar_t *none : {L"C:\\x", L"\\\\.\\pipe\\name", L"\\\\?\\GLOBALROOT\\Device\\x", L"\\\\host",
                              L"\\\\host\\", L"\\\\\\share\\x", L"\\\\..\\share", L"\\\\host\\.\\x", L"\\??\\X\\y"})
  {
    EXPECT_TRUE(windows_share_of(none).empty()) << std::string(none, none + wcslen(none));
  }
}

TEST(ConfinementRemoteLookups, only_strictly_valid_utf8_is_a_name)
{
  for (const char *valid : {"", "C:\\x", "/tmp/caf\xC3\xA9", "\xE2\x82\xAC", "\xF0\x9F\x98\x80", "\xED\x9F\xBF",
                            "\xEE\x80\x80", "\xF4\x8F\xBF\xBF"})
  {
    EXPECT_TRUE(utf8_strictly_valid(valid)) << valid;
  }
  /* A stray continuation, an encoded surrogate (high and low), a truncated sequence, overlong forms, C0/C1/F5+ leads,
   * above U+10FFFF: each is a name another decoder reads differently (review-confine-fixes-7a1bcdb652d MUST-1). */
  for (const char *invalid : {"\x80\\\\host\\s\\x", "\xED\xB2\x80\\\\host\\s\\x", "\xED\xA0\x80", "\xE2\x82",
                              "\xC0\xAF", "\xE0\x80\xAF", "\xF0\x80\x80\xAF", "\xC1\xBF", "\xF5\x80\x80\x80",
                              "\xF4\x90\x80\x80", "x\xFF"})
  {
    EXPECT_FALSE(utf8_strictly_valid(invalid)) << invalid;
  }
}

TEST(ConfinementRemoteLookups, only_a_connected_share_may_be_looked_up)
{
  /* Names no other test uses: the connected set is process-wide and add-only. */
  EXPECT_TRUE(lookup_may_open(L"C:\\work\\x"));
  EXPECT_FALSE(lookup_may_open(L"\\\\angelo-test-a.invalid\\proj\\x"));
  note_connected(L"\\\\?\\UNC\\Angelo-Test-A.invalid\\Proj\\scene.blend"); /* As the system names a mapped drive. */
  EXPECT_TRUE(lookup_may_open(L"\\\\angelo-test-a.invalid\\proj\\textures\\x.png"));
  EXPECT_TRUE(lookup_may_open(L"//ANGELO-TEST-A.INVALID/PROJ/x"));
  EXPECT_FALSE(lookup_may_open(L"\\\\angelo-test-a.invalid\\other\\x")); /* Another share on that host. */
  EXPECT_FALSE(lookup_may_open(L"\\\\angelo-test-b.invalid\\proj\\x"));
  note_connected(L"\\\\.\\pipe\\angelo-test"); /* A device is never a share: nothing is noted. */
  EXPECT_FALSE(lookup_may_open(L"\\\\.\\pipe\\angelo-test"));
}

TEST(ConfinementRemoteLookups, a_document_the_person_opens_on_a_share_makes_it_theirs)
{
  /* Coordinator's ruling on head 10: a UNC project the person opens after a script ran keeps working; one a script
   * (or anything while a thread is confined) opens notes nothing. Each run its own names (add-only). */
  static int run = 0;
  const std::string tag = std::to_string(run++);
  const std::string theirs = "\\\\angelo-test-p" + tag + ".invalid\\proj";
  const std::string scripts = "\\\\angelo-test-q" + tag + ".invalid\\proj";
  auto wide = [](const std::string &text) { return std::wstring(text.begin(), text.end()); };
  EXPECT_FALSE(note_person_document(scripts + "\\scene.blend", false));
  EXPECT_FALSE(lookup_may_open(wide(scripts + "\\tex\\a.png")));
  EXPECT_FALSE(note_person_document("C:\\local\\scene.blend", true)); /* A local document: nothing to note. */
#ifdef _WIN32
  EXPECT_TRUE(note_person_document(theirs + "\\scene.blend", true));
  EXPECT_TRUE(lookup_may_open(wide(theirs + "\\tex\\a.png")));
#else
  EXPECT_FALSE(note_person_document(theirs + "\\scene.blend", true)); /* POSIX shares are mounts (automount.hh). */
#endif
}

TEST(ConfinementRemoteLookups, a_refusal_names_the_share_it_reaches)
{
  /* Re-read MUST-B: a link below a project folder names its share nowhere in the path, so the refusal says which share
   * it is (and how to make it usable). The text only, and only for the path that was judged. */
  network_refusal_begin("C:\\p\\textures\\x.png");
  EXPECT_FALSE(lookup_may_open(L"\\\\?\\UNC\\Angelo-Test-F.invalid\\Tex\\x.png"));
  EXPECT_FALSE(lookup_may_open(L"\\\\angelo-test-g.invalid\\other")); /* The first refused share is the one reached. */
#ifdef _WIN32
  EXPECT_EQ(network_refusal_share("C:\\p\\textures\\x.png"), "\\\\angelo-test-f.invalid\\tex");
#else
  EXPECT_EQ(network_refusal_share("C:\\p\\textures\\x.png"), ""); /* Only Windows refuses by a share. */
#endif
  EXPECT_EQ(network_refusal_share("C:\\p\\textures\\x.png"), ""); /* Read once. */
  network_refusal_begin("C:\\p\\a.png");
  EXPECT_FALSE(lookup_may_open(L"\\\\angelo-test-h.invalid\\s\\a.png"));
  EXPECT_EQ(network_refusal_share("C:\\p\\other.png"), ""); /* Another path's refusal: nothing added. */
  EXPECT_EQ(network_refusal_share("C:\\p\\a.png"), "");     /* And it was let go. */
}

#ifdef _WIN32
TEST(ConfinementRemoteLookups, no_lookup_opens_an_unconnected_share)
{
  /* `.invalid` never resolves; were it opened, the count below would say so before any connection mattered. */
  /* The last two are not UTF-8 (a stray continuation byte, an encoded surrogate): a decoder that drops them would
   * hand the system `\\angelo-probe.invalid\s\x` (review-confine-fixes-7a1bcdb652d MUST-1). */
  const char *probes[] = {"\\\\angelo-probe.invalid\\s\\x.py",
                          "//angelo-probe.invalid/s/x.py",
                          "\\\\?\\UNC\\angelo-probe.invalid\\s\\x.py",
                          "\\\\angelo-probe.invalid@SSL\\DavWWWRoot\\x.py",
                          "\x80\\\\angelo-probe.invalid\\s\\x",
                          "\xED\xB2\x80\\\\angelo-probe.invalid\\s\\x"};
  Folders folders;
  const uint64_t before = lookups_opened();
  for (const char *probe : probes) {
    EXPECT_FALSE(resolve(probe).has_value()) << probe;
    EXPECT_EQ(folders.judge(probe, false), Refusal::Network) << probe;
    EXPECT_EQ(folders.judge(probe, true), Refusal::Network) << probe;
    EXPECT_FALSE(folders.trusted(probe)) << probe;
    EXPECT_FALSE(folders.agent_made(probe)) << probe;
    EXPECT_TRUE(folders.script_wrote(probe)) << probe; /* Fail closed: not the person's. */
    EXPECT_EQ(folders.forget(probe), -2) << probe;
    EXPECT_FALSE(folder_identity(probe).has_value()) << probe;
    EXPECT_FALSE(entry_identity(probe).has_value()) << probe;
    EXPECT_TRUE(final_path(probe).empty()) << probe;
    EXPECT_TRUE(followed_path(probe).empty()) << probe;
    EXPECT_TRUE(reparse_in_any_component(probe)) << probe; /* Can't be examined: fail closed. */
    EXPECT_TRUE(last_component_links(probe, -1)) << probe;
    EXPECT_FALSE(vouches::facts_of(probe).exists) << probe;
    BLI_stat_t info;
    EXPECT_NE(lookup_stat(probe, &info), 0) << probe;
    EXPECT_FALSE(lookup_is_dir(probe)) << probe;
    EXPECT_FALSE(lookup_is_file(probe)) << probe;
    EXPECT_EQ(lookup_fopen_read(probe), nullptr) << probe;
    EXPECT_FALSE(lookup_may_touch(probe)) << probe;
  }
  EXPECT_EQ(lookups_opened(), before);
}

/* The only test that sets the process-wide "a region began" flag, and every check that depends on it, in order, so
 * the tests can run shuffled or filtered (review-confine-fixes-4d9daef113a NIT-3). Each run uses its own names (the
 * connected set is process-wide and add-only); under --gtest_repeat a later run finds the flag already set and checks
 * only what holds after a region. */
TEST(ConfinementRemoteLookups, roots_before_and_after_the_first_region)
{
  static int run = 0;
  const std::string tag = std::to_string(run++);
  auto wide = [](const std::string &text) { return std::wstring(text.begin(), text.end()); };

  /* Before any region, Angelo's root names its share (review-confine-fixes-eddf9bcda75 SHOULD-1). */
  const std::string early = "\\\\angelo-test-c" + tag + ".invalid\\profiles";
  note_connected_root(early + "\\x");
  const bool before_a_region = lookup_may_open(wide(early + "\\y"));
  if (!before_a_region) {
    std::printf("[ note ] a region already began in this process (a repeat): only the after-region checks run\n");
  }
#ifdef _WIN32
  if (before_a_region) {
    /* A person's folder that is a link to a share they use (`mklink /D C:\Assets \\nas\assets`), added as one of
     * Angelo's roots before the first region: the share is noted as connected, so it keeps working
     * (break-it-confine-reparse-b7d499c S3). Nothing can connect to the `.invalid` host. */
    wchar_t temp[MAX_PATH + 1];
    const DWORD length = GetTempPathW(MAX_PATH, temp);
    const std::wstring link = std::wstring(temp, length) + L"angelo-person-link-" + wide(tag) + L"-" +
                              std::to_wstring(GetCurrentProcessId());
    const std::wstring assets = wide("\\\\angelo-test-n" + tag + ".invalid\\assets");
    if (length > 0 && CreateSymbolicLinkW(link.c_str(), assets.c_str(),
                                          SYMBOLIC_LINK_FLAG_DIRECTORY | SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE))
    {
      const int size = WideCharToMultiByte(CP_UTF8, 0, link.data(), int(link.size()), nullptr, 0, nullptr, nullptr);
      std::string utf8(size_t(size > 0 ? size : 0), '\0');
      WideCharToMultiByte(CP_UTF8, 0, link.data(), int(link.size()), utf8.data(), size, nullptr, nullptr);
      Roots person;
      person.add(utf8, true);
      EXPECT_TRUE(lookup_may_open(assets + L"\\x.png"));
      RemoveDirectoryW(link.c_str());
    }
    else {
      std::printf("[ note ] no symbolic link could be made here: the person's linked share is not checked\n");
    }
  }
#endif
  remote_region_began();

  /* After one, a root's inputs may hold what a script appended to Angelo's own lists: its share is not noted. */
  const std::string planted = "\\\\angelo-test-d" + tag + ".invalid\\planted";
  note_connected_root(planted + "\\x");
  EXPECT_FALSE(lookup_may_open(wide(planted + "\\y")));
  if (before_a_region) {
    EXPECT_TRUE(lookup_may_open(wide(early + "\\y"))); /* Noted earlier: kept. */
  }

  /* A refusing root on a share nobody connected is held by its text, in any UNC spelling, so a key that resolves onto
   * it later (a mapped drive) is refused; an admitting one drops it (review-confine-fixes-7a1bcdb652d SHOULD-2). Only
   * by the same spelling of the server (paths.hh). Nothing is looked up. */
  const uint64_t before = lookups_opened();
  const std::string server = "angelo-test-e" + tag + ".invalid";
  Roots refusing, admitting;
  refusing.add("//" + server + "/S/scripts/", true);
  refusing.add("\\\\?\\UNC\\" + server + "\\S\\state", true);
  admitting.add("//" + server + "/S/scripts/", false);
  EXPECT_EQ(refusing.size(), size_t(2));
  EXPECT_EQ(admitting.size(), size_t(0));
  EXPECT_TRUE(refusing.holds("\\\\" + server + "\\s\\scripts\\startup\\x.py", {}));
  EXPECT_TRUE(refusing.holds("\\\\" + server + "\\s\\state\\keys\\k", {}));
  EXPECT_FALSE(refusing.holds("\\\\" + server + "\\s\\other\\x.py", {}));
  Families families;
  families.add("//" + server + "/S/home", "angelo-worker-");
  EXPECT_TRUE(families.holds("\\\\" + server + "\\s\\home\\angelo-worker-1\\k"));
  EXPECT_FALSE(families.holds("\\\\" + server + "\\s\\home\\other\\k"));
  EXPECT_EQ(lookups_opened(), before);
}
#endif

}  // namespace blender::angelo_guard::tests
