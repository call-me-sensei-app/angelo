/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The link walk (bpy_angelo_guard_confine_links.hh) against a made-up file system, on every system: each rule of
 * break-it-confine-reparse-b7d499c (M1 targets, M2 inputs, M3 the real path, M4 streams and errors, M5 the cache,
 * S4 the caps, S5 the reparse buffer, the substring NIT). The real Windows probe is
 * bpy_angelo_guard_confine_reparse_win_test.cc.
 */

#include "testing/testing.h"

#include <map>
#include <set>
#include <string>
#include <vector>

#include "bpy_angelo_guard_confine_links.hh"

namespace blender::angelo_guard::links::tests {

/** A file system: each path (in `\\?\` form) and what a probe finds there; anything else is missing. */
struct FakeFs {
  std::map<std::wstring, Probe> entries;
  std::set<std::wstring> connected; /* Shares, `\\host\share`, lower case. */
  std::vector<std::wstring> probed;
  /* Drive roots (`\\?\S:\`) that stand for another place: `\??\` text (a `subst`), or empty (can't be told). */
  std::map<std::wstring, std::wstring> drives;

  void folder(const std::wstring &path)
  {
    Probe found;
    found.kind = Probe::Kind::Plain;
    found.folder = true;
    entries[path] = found;
  }
  void link(const std::wstring &path, const std::wstring &target, bool relative = false)
  {
    Probe found;
    found.kind = Probe::Kind::Link;
    found.folder = true;
    found.target = target;
    found.relative = relative;
    entries[path] = found;
  }
  void unreadable(const std::wstring &path)
  {
    entries[path] = Probe(); /* Refuse. */
  }
  Walker walker()
  {
    Walker walker;
    walker.probe = [this](const std::wstring &path) {
      probed.push_back(path);
      const auto found = entries.find(path);
      if (found == entries.end()) {
        Probe missing;
        missing.kind = Probe::Kind::Missing;
        return missing;
      }
      return found->second;
    };
    walker.share_ok = [this](const std::wstring &share) {
      std::wstring lower = share;
      for (wchar_t &c : lower) {
        c = (c >= L'A' && c <= L'Z') ? wchar_t(c - L'A' + L'a') : c;
      }
      return connected.count(lower) != 0;
    };
    if (!drives.empty()) {
      walker.drive_root = [this](const std::wstring &root) {
        const auto found = drives.find(root);
        if (found == drives.end()) {
          Rooted self;
          self.type = Rooted::Type::Drive;
          self.root = root;
          return self;
        }
        return found->second.empty() ? Rooted() : split_target(found->second, false);
      };
    }
    return walker;
  }
  Verdict walk(const std::wstring &path, bool follow_last = true)
  {
    Walker w = walker();
    return links::walk(path, follow_last, w);
  }
};

/* A folder `C:\a` with a link `remote` to a share nobody connected. */
static FakeFs with_remote(const std::wstring &target = L"\\??\\UNC\\host\\s")
{
  FakeFs fs;
  fs.folder(L"\\\\?\\C:\\a");
  fs.link(L"\\\\?\\C:\\a\\remote", target);
  return fs;
}

TEST(ConfinementLinkWalk, a_link_to_an_unconnected_share_is_the_network_and_a_connected_one_is_walked)
{
  FakeFs fs = with_remote();
  EXPECT_EQ(fs.walk(L"C:\\a\\remote\\x.txt"), Verdict::Network);
  EXPECT_EQ(fs.walk(L"C:\\a\\plain\\x.txt"), Verdict::Safe);
  fs.connected.insert(L"\\\\host\\s");
  fs.link(L"\\\\?\\UNC\\host\\s\\x.txt", L"\\??\\UNC\\other\\t"); /* A link on the connected share itself. */
  EXPECT_EQ(fs.walk(L"C:\\a\\remote\\x.txt"), Verdict::Network);
  EXPECT_EQ(fs.walk(L"C:\\a\\remote\\y.txt"), Verdict::Safe);
}

TEST(ConfinementLinkWalk, only_a_drive_a_unc_or_a_volume_target_is_followed)
{
  /* M1: every other substitute name, the network's in other spellings among them, is refused. */
  for (const wchar_t *target : {L"\\??\\Global\\UNC\\host\\s", L"\\GLOBAL??\\UNC\\host\\s", L"\\DosDevices\\UNC\\host\\s",
                                L"\\Device\\HarddiskVolume2\\x", L"\\??\\GLOBALROOT\\Device\\Mup\\host\\s",
                                L"\\Device\\Mup\\host\\s", L"C:\\x", L"\\??\\pipe\\x", L"\\??\\UNC\\host",
                                L"\\??\\Volume{1}x", L""})
  {
    EXPECT_EQ(with_remote(target).walk(L"C:\\a\\remote\\x.txt"), Verdict::Unreadable)
        << std::string(target, target + wcslen(target));
  }
  /* A junction to a volume by its GUID, whose own folder links to a share: walked into, so found. */
  FakeFs fs = with_remote(L"\\??\\Volume{0f1e2d3c-0000-0000-0000-000000000000}\\rest");
  fs.link(L"\\\\?\\Volume{0f1e2d3c-0000-0000-0000-000000000000}\\rest", L"\\??\\UNC\\host\\s");
  EXPECT_EQ(fs.walk(L"C:\\a\\remote\\x.txt"), Verdict::Network);
  /* A drive target, walked on. */
  FakeFs local = with_remote(L"\\??\\D:\\data");
  local.folder(L"\\\\?\\D:\\data");
  EXPECT_EQ(local.walk(L"C:\\a\\remote\\x.txt"), Verdict::Safe);
  EXPECT_EQ(local.probed.back(), L"\\\\?\\D:\\data\\x.txt");
}

TEST(ConfinementLinkWalk, every_spelling_of_a_drive_is_walked_and_other_devices_are_refused)
{
  /* M2: `\\.\C:` is the drive as `\\?\C:` is; any other device form fails closed. */
  for (const wchar_t *spelling : {L"C:\\a\\remote\\x", L"\\\\?\\C:\\a\\remote\\x", L"\\\\.\\C:\\a\\remote\\x"}) {
    EXPECT_EQ(with_remote().walk(spelling), Verdict::Network);
  }
  for (const wchar_t *device : {L"\\\\.\\pipe\\x", L"\\\\?\\GLOBALROOT\\Device\\Mup\\h\\s", L"\\\\.\\PhysicalDrive0",
                                L"\\\\.\\Volume{0f1e2d3c-0000}\\x", L"\\\\?\\C:\\a\\..\\remote\\x", L"relative\\x",
                                L"\\??\\C:\\a", L"\\\\host", L""})
  {
    EXPECT_EQ(with_remote().walk(device), Verdict::Unreadable) << std::string(device, device + wcslen(device));
  }
}

TEST(ConfinementLinkWalk, a_relative_target_applies_to_the_real_folder)
{
  /* M3 (git core.symlinks): J is a junction to sub\a\b; rel in it is `..\..\evil`; sub\evil links to a share. The
   * text parent of rel is C:\J, whose `..\..` is C:\; its real folder is C:\sub\a\b, whose `..\..` is C:\sub. */
  FakeFs fs;
  fs.link(L"\\\\?\\C:\\J", L"\\??\\C:\\sub\\a\\b");
  for (const wchar_t *folder : {L"\\\\?\\C:\\sub", L"\\\\?\\C:\\sub\\a", L"\\\\?\\C:\\sub\\a\\b"}) {
    fs.folder(folder);
  }
  fs.link(L"\\\\?\\C:\\sub\\a\\b\\rel", L"..\\..\\evil", true);
  fs.link(L"\\\\?\\C:\\sub\\evil", L"\\??\\UNC\\host\\s");
  EXPECT_EQ(fs.walk(L"C:\\J\\rel\\x.txt"), Verdict::Network);
  /* MUST-A: `..` after a name in a target. Windows reads `J2\..\evil` by its text (C:\repo\evil, a link to a
   * share); the walk would follow J2 to deep\deeper and find C:\repo\deep missing. Refused, whatever J2 is. */
  FakeFs text = with_remote();
  text.folder(L"\\\\?\\C:\\repo");
  text.link(L"\\\\?\\C:\\repo\\rel", L"J2\\..\\evil", true);
  text.link(L"\\\\?\\C:\\repo\\J2", L"deep\\deeper", true);
  text.link(L"\\\\?\\C:\\repo\\evil", L"\\??\\UNC\\host\\s");
  EXPECT_EQ(text.walk(L"C:\\repo\\rel\\x.txt"), Verdict::Unreadable);
  for (const auto &[target, relative] : std::vector<std::pair<std::wstring, bool>>{
           {L"nothere\\..\\remote", true},
           {L".\\a\\..\\b", true},
           {L"a\\.\\..", true},
           {L"\\??\\C:\\a\\..\\b", false},
           {L"\\??\\C:\\..\\b", false},
           {L"\\??\\UNC\\host\\s\\a\\..\\b", false}})
  {
    FakeFs dots = with_remote();
    dots.link(L"\\\\?\\C:\\a\\l", target, relative);
    EXPECT_EQ(dots.walk(L"C:\\a\\l\\x.txt"), Verdict::Unreadable) << std::string(target.begin(), target.end());
  }
  /* Leading `..` (and `.`) in a relative target are walked: each is the real folder's parent in both readings. */
  FakeFs leading = with_remote();
  leading.folder(L"\\\\?\\C:\\a");
  leading.folder(L"\\\\?\\C:\\a\\shared");
  leading.link(L"\\\\?\\C:\\a\\b\\l", L".\\..\\shared", true);
  leading.folder(L"\\\\?\\C:\\a\\b");
  EXPECT_EQ(leading.walk(L"C:\\a\\b\\l\\x.txt"), Verdict::Safe);
  /* A relative target never names a root, a drive or a stream. */
  for (const wchar_t *target : {L"\\x", L"C:x", L"remote:s"}) {
    FakeFs bad = with_remote();
    bad.link(L"\\\\?\\C:\\a\\l", target, true);
    EXPECT_EQ(bad.walk(L"C:\\a\\l\\x.txt"), Verdict::Unreadable);
  }
}

TEST(ConfinementLinkWalk, a_dot_dot_past_its_root_is_refused)
{
  /* Head-7 MUST-A': above a drive's root Windows goes on into the object namespace, where `UNC\host\s` or
   * `GLOBALROOT\Device\Mup\...` is a share; the walk refused nothing there before (it clamped at the root). */
  for (const auto &[link, target] : std::vector<std::pair<std::wstring, std::wstring>>{
           {L"\\\\?\\C:\\l", L"..\\UNC\\host\\s"},
           {L"\\\\?\\C:\\a\\l", L"..\\..\\GLOBALROOT\\Device\\Mup\\host\\s"},
           {L"\\\\?\\C:\\a\\l", L"..\\..\\x"}})
  {
    FakeFs fs = with_remote();
    fs.link(link, target, true);
    EXPECT_EQ(fs.walk(link.substr(4) + L"\\f.txt"), Verdict::Unreadable) << std::string(target.begin(), target.end());
  }
  /* Up to the root and down again is walked. */
  FakeFs within = with_remote();
  within.link(L"\\\\?\\C:\\a\\l", L"..\\a", true);
  EXPECT_EQ(within.walk(L"C:\\a\\l\\f.txt"), Verdict::Safe);
  within.link(L"\\\\?\\C:\\a\\up", L"..\\a\\remote", true);
  EXPECT_EQ(within.walk(L"C:\\a\\up\\f.txt"), Verdict::Network);
}

TEST(ConfinementLinkWalk, a_substituted_drive_is_walked_as_what_it_stands_for)
{
  /* Head-7 MUST-A': `subst S: \\host\s` (a share nobody connected), `subst T: C:\a` (a folder holding the link to
   * one), a drive that can't be told, a loop of substitutions; and a link's target on a substituted drive. */
  FakeFs fs = with_remote();
  fs.drives[L"\\\\?\\S:\\"] = L"\\??\\UNC\\host\\s";
  fs.drives[L"\\\\?\\T:\\"] = L"\\??\\C:\\a";
  fs.drives[L"\\\\?\\U:\\"] = L"";
  fs.drives[L"\\\\?\\V:\\"] = L"\\??\\W:\\";
  fs.drives[L"\\\\?\\W:\\"] = L"\\??\\V:\\";
  EXPECT_EQ(fs.walk(L"S:\\x.txt"), Verdict::Network);
  EXPECT_EQ(fs.walk(L"T:\\remote\\x.txt"), Verdict::Network);
  EXPECT_EQ(fs.walk(L"T:\\x.txt"), Verdict::Safe);
  EXPECT_EQ(fs.walk(L"U:\\x.txt"), Verdict::Unreadable);
  EXPECT_EQ(fs.walk(L"V:\\x.txt"), Verdict::Unreadable);
  EXPECT_EQ(fs.walk(L"D:\\x.txt"), Verdict::Safe); /* A drive that is itself. */
  fs.link(L"\\\\?\\C:\\a\\to-s", L"\\??\\S:\\");
  EXPECT_EQ(fs.walk(L"C:\\a\\to-s\\x.txt"), Verdict::Network);
  fs.connected.insert(L"\\\\host\\s");
  EXPECT_EQ(fs.walk(L"S:\\x.txt"), Verdict::Safe); /* Connected: walked. */
}

TEST(ConfinementLinkWalk, only_reparse_tags_known_to_keep_their_data_are_plain_folders)
{
  /* Head-10/11 re-read SHOULD: a cloud placeholder (each of its variants), dedup and WOF keep their data; DFS's
   * referral, ProjFS, a container placeholder or a tag yet unknown don't, so a folder with one is refused. */
  for (const uint32_t tag : {0x9000001Au, 0x9000101Au, 0x9000F01Au, 0x80000013u, 0x80000017u}) {
    EXPECT_TRUE(reparse_tag_keeps_data(tag)) << tag;
  }
  for (const uint32_t tag : {0x8000000Au, 0x9000001Cu, 0x80000018u, 0x8000001Bu, 0x00000000u, 0x9000001Bu}) {
    EXPECT_FALSE(reparse_tag_keeps_data(tag)) << tag;
  }
}

TEST(ConfinementLinkWalk, past_a_link_one_look_can_say_the_rest_is_plain)
{
  /* Windows cost read: after a link, the rest of the path is asked once (rest_plain) instead of a probe per part. */
  FakeFs fs = with_remote();
  fs.folder(L"\\\\?\\C:\\b");
  fs.link(L"\\\\?\\C:\\a\\l", L"\\??\\C:\\b");
  Walker walker = fs.walker();
  std::vector<std::wstring> asked;
  walker.rest_plain = [&](const std::wstring &folder, const std::deque<std::wstring> &rest, bool) {
    std::wstring joined = folder;
    for (const std::wstring &part : rest) {
      joined += L"|" + part;
    }
    asked.push_back(joined);
    return true;
  };
  EXPECT_EQ(links::walk(L"C:\\a\\l\\c\\d\\x.txt", true, walker), Verdict::Safe);
  ASSERT_EQ(asked.size(), size_t(1));
  /* An absolute target restarts at its root, its own parts still to walk: the same path, looked at once. */
  EXPECT_EQ(asked[0], L"\\\\?\\C:\\|b|c|d|x.txt");
  EXPECT_EQ(fs.probed.size(), size_t(2));             /* C:\a and the link: nothing past it. */
  /* When the look can't say so (a reparse point further on), the walk goes on and still finds a link to a share. */
  FakeFs deeper = with_remote();
  deeper.folder(L"\\\\?\\C:\\b");
  deeper.link(L"\\\\?\\C:\\a\\l", L"\\??\\C:\\b");
  deeper.link(L"\\\\?\\C:\\b\\share", L"\\??\\UNC\\host\\s");
  Walker walk_on = deeper.walker();
  walk_on.rest_plain = [](const std::wstring &, const std::deque<std::wstring> &, bool) { return false; };
  EXPECT_EQ(links::walk(L"C:\\a\\l\\share\\x.txt", true, walk_on), Verdict::Network);
}

TEST(ConfinementLinkWalk, a_stream_is_its_file_and_anything_unreadable_is_refused)
{
  /* M4. */
  EXPECT_EQ(with_remote().walk(L"C:\\a\\remote:x"), Verdict::Network);
  EXPECT_EQ(with_remote().walk(L"C:\\a\\remote::$DATA"), Verdict::Network);
  EXPECT_EQ(with_remote().walk(L"C:\\a:s\\remote\\x"), Verdict::Unreadable);
  FakeFs fs = with_remote();
  fs.unreadable(L"\\\\?\\C:\\a\\denied");
  EXPECT_EQ(fs.walk(L"C:\\a\\denied\\x"), Verdict::Unreadable);
  /* Nothing below a missing name is probed or followed. */
  FakeFs missing = with_remote();
  EXPECT_EQ(missing.walk(L"C:\\a\\none\\remote\\x"), Verdict::Safe);
  EXPECT_EQ(missing.probed.back(), L"\\\\?\\C:\\a\\none");
  /* The last component, not followed (an open that doesn't follow it). */
  EXPECT_EQ(with_remote().walk(L"C:\\a\\remote", false), Verdict::Safe);
  EXPECT_EQ(with_remote().walk(L"C:\\a\\remote", true), Verdict::Network);
  /* Every probe is in `\\?\` form, so a long path is probed whole. */
  FakeFs deep;
  std::wstring path = L"C:";
  for (int i = 0; i < 40; i++) {
    path += L"\\folder-with-a-long-name-" + std::to_wstring(i);
    deep.folder(L"\\\\?\\" + path);
  }
  deep.link(L"\\\\?\\" + path + L"\\remote", L"\\??\\UNC\\host\\s");
  EXPECT_GT(path.size(), size_t(260));
  EXPECT_EQ(deep.walk(path + L"\\remote\\x"), Verdict::Network);
}

TEST(ConfinementLinkWalk, probes_and_links_are_capped)
{
  /* S4: a loop, a chain longer than any real one, a path of more components than the budget: refused. */
  FakeFs loop;
  loop.link(L"\\\\?\\C:\\l", L"\\??\\C:\\l");
  EXPECT_EQ(loop.walk(L"C:\\l\\x"), Verdict::Unreadable);
  FakeFs chain;
  for (int i = 0; i < 40; i++) {
    chain.link(L"\\\\?\\C:\\c" + std::to_wstring(i), L"\\??\\C:\\c" + std::to_wstring(i + 1));
  }
  EXPECT_EQ(chain.walk(L"C:\\c0\\x"), Verdict::Unreadable);
  FakeFs wide;
  std::wstring path = L"C:";
  for (int i = 0; i < 70; i++) {
    path += L"\\d";
    wide.folder(L"\\\\?\\" + path);
  }
  Walker w = wide.walker();
  EXPECT_EQ(walk(path + L"\\x", true, w), Verdict::Unreadable);
  EXPECT_EQ(w.probes, w.probe_budget + 1);
}

TEST(ConfinementLinkWalk, the_cache_keeps_folders_by_their_exact_text)
{
  /* M5: only folders found plain, by their exact text (a case-sensitive folder is another name). */
  FakeFs fs;
  fs.folder(L"\\\\?\\C:\\a");
  Probe file;
  file.kind = Probe::Kind::Plain;
  fs.entries[L"\\\\?\\C:\\a\\x.txt"] = file;
  std::unordered_set<std::wstring> cache;
  Walker w = fs.walker();
  w.plain_folders = &cache;
  EXPECT_EQ(walk(L"C:\\a\\x.txt", true, w), Verdict::Safe);
  EXPECT_EQ(cache, (std::unordered_set<std::wstring>{L"\\\\?\\C:\\a"}));
  /* While the cache stands, a folder turned into a link is not probed again: why every judged move, link, make or
   * remove and every region's start drops it (reparse.cc's generation; the Windows test renames one). */
  fs.link(L"\\\\?\\C:\\a", L"\\??\\UNC\\host\\s");
  EXPECT_EQ(walk(L"C:\\a\\x.txt", true, w), Verdict::Safe);
  /* With the cheap look on a hit (as the Windows walk has), the change is seen without clearing anything. */
  w.still_plain = [&fs](const std::wstring &folder) {
    const auto found = fs.entries.find(folder);
    return found != fs.entries.end() && found->second.kind == Probe::Kind::Plain && found->second.folder;
  };
  EXPECT_EQ(walk(L"C:\\a\\x.txt", true, w), Verdict::Network);
  w.still_plain = nullptr;
  cache.clear();
  EXPECT_EQ(walk(L"C:\\a\\x.txt", true, w), Verdict::Network);
  EXPECT_EQ(walk(L"C:\\A\\x.txt", true, w), Verdict::Safe); /* Another name here: the made-up file system is exact. */
}

TEST(ConfinementLinkWalk, a_name_containing_a_device_word_is_an_ordinary_name)
{
  /* The substring NIT: `redirector` or `mup` inside a folder's name decides nothing. */
  FakeFs fs;
  fs.folder(L"\\\\?\\C:\\projects");
  fs.folder(L"\\\\?\\C:\\projects\\redirector-kit");
  EXPECT_EQ(fs.walk(L"C:\\projects\\redirector-kit\\device-mup.txt"), Verdict::Safe);
}

static std::vector<unsigned char> reparse_buffer(uint32_t tag, const std::wstring &name, uint32_t flags,
                                                 int offset_extra = 0, int length_extra = 0)
{
  const bool symlink = tag == 0xA000000Cu;
  const size_t header = symlink ? 20 : 16;
  std::vector<unsigned char> data(header + name.size() * 2 + 2, 0);
  auto put16 = [&](size_t at, uint32_t value) {
    data[at] = uint8_t(value);
    data[at + 1] = uint8_t(value >> 8);
  };
  put16(0, tag & 0xFFFF);
  put16(2, tag >> 16);
  put16(4, uint32_t(data.size() - 8));
  put16(8, uint32_t(0 + offset_extra));
  put16(10, uint32_t(name.size() * 2 + length_extra));
  if (symlink) {
    put16(16, flags & 0xFFFF);
  }
  for (size_t i = 0; i < name.size(); i++) {
    put16(header + i * 2, uint32_t(name[i]));
  }
  return data;
}

TEST(ConfinementLinkWalk, a_reparse_buffer_is_read_only_within_what_was_returned)
{
  /* S5: every offset and length against the returned size; a crafted one is refused, never read past. */
  std::wstring target;
  bool relative = true;
  std::vector<unsigned char> link = reparse_buffer(0xA000000Cu, L"\\??\\UNC\\host\\s", 0);
  ASSERT_TRUE(parse_reparse(link.data(), link.size(), target, relative));
  EXPECT_EQ(target, L"\\??\\UNC\\host\\s");
  EXPECT_FALSE(relative);
  std::vector<unsigned char> rel = reparse_buffer(0xA000000Cu, L"..\\x", 1);
  ASSERT_TRUE(parse_reparse(rel.data(), rel.size(), target, relative));
  EXPECT_TRUE(relative);
  std::vector<unsigned char> mount = reparse_buffer(0xA0000003u, L"\\??\\C:\\x", 0);
  ASSERT_TRUE(parse_reparse(mount.data(), mount.size(), target, relative));
  EXPECT_EQ(target, L"\\??\\C:\\x");
  EXPECT_FALSE(relative);
  for (const std::vector<unsigned char> &bad :
       {reparse_buffer(0xA000000Cu, L"\\??\\C:\\x", 0, 4000), reparse_buffer(0xA000000Cu, L"\\??\\C:\\x", 0, 0, 4000),
        reparse_buffer(0xA000000Cu, L"\\??\\C:\\x", 0, 1), reparse_buffer(0xA000000Cu, L"\\??\\C:\\x", 0, 0, 1),
        reparse_buffer(0x8000001Bu, L"\\??\\C:\\x", 0)})
  {
    EXPECT_FALSE(parse_reparse(bad.data(), bad.size(), target, relative));
  }
  /* Shorter than its own header or its stated length. */
  EXPECT_FALSE(parse_reparse(link.data(), 7, target, relative));
  EXPECT_FALSE(parse_reparse(link.data(), link.size() - 4, target, relative));
  EXPECT_FALSE(parse_reparse(nullptr, 0, target, relative));
}

}  // namespace blender::angelo_guard::links::tests
