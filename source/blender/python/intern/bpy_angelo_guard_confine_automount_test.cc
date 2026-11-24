/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The automounter's folders of maps of any host (bpy_angelo_guard_confine_automount.hh), on every system: master maps
 * and mount tables as macOS and Linux write them (break-it-confine-reparse-b7d499c S6), what is already mounted
 * (S6 MUST-1) and the snapshot kept until something changes (S6 MUST-2).
 */

#include "testing/testing.h"

#include <chrono>
#include <cstdio>
#include <map>
#include <set>
#include <string>
#include <thread>
#include <vector>

#include "bpy_angelo_guard_confine_automount.hh"

namespace blender::angelo_guard::automount::tests {

/** A pretend system: executables, file texts and `*.autofs` folders. */
static Probe probe_of(std::set<std::string> programs,
                      std::map<std::string, std::string> files = {},
                      std::map<std::string, std::vector<std::string>> folders = {})
{
  Probe probe;
  probe.is_program = [programs](const std::string &path) { return programs.count(path) != 0; };
  probe.text = [files](const std::string &path) {
    const auto found = files.find(path);
    return found == files.end() ? std::string() : found->second;
  };
  probe.autofs_files = [folders](const std::string &folder) {
    const auto found = folders.find(folder);
    return found == folders.end() ? std::vector<std::string>() : found->second;
  };
  return probe;
}

static const Probe programs = probe_of({"/etc/auto.net", "/etc/auto.smb", "/usr/local/bin/any-map"});

TEST(ConfinementAutomount, a_master_map_gives_the_folders_of_maps_of_any_host)
{
  /* macOS's default (no -hosts: /net is commented out) and one with it; Linux's, with a program map by path, by
   * name and by type, file maps, direct maps, includes and options. */
  EXPECT_EQ(any_host_points_from_master("#\n# Automounter master map\n#\n+auto_master\t\t# Use directory service\n"
                                        "#/net\t\t\t-hosts\t\t-nobrowse,hidefromfinder,nosuid\n"
                                        "/home\t\t\tauto_home\t-nobrowse,hidefromfinder\n"
                                        "/Network/Servers\t-fstab\n/-\t\t\t-static\n",
                                        programs),
            std::vector<std::string>{});
  EXPECT_EQ(any_host_points_from_master("/net -hosts -nobrowse,nosuid\n/home auto_home\n", programs),
            std::vector<std::string>{"/net"});
  EXPECT_EQ(any_host_points_from_master("/misc /etc/auto.misc\n/net /etc/auto.net --timeout=60\n"
                                        "/smb auto.smb\n/cifs program:/usr/local/bin/any-map\n"
                                        "/nfs file:/etc/auto.net\n/ldap ldap:ou=auto.home,dc=example\n"
                                        "/- /etc/auto.direct\n+dir:/etc/auto.master.d\n/hosts/ -hosts\n",
                                        programs),
            (std::vector<std::string>{"/net", "/smb", "/cifs", "/hosts"}));
}

TEST(ConfinementAutomount, a_typed_program_line_and_included_masters_are_read)
{
  /* `program,sun:` (a type with a format), and program maps named relatively or by type inside `+dir:` and file
   * includes, which the master itself does not list. A name include (`+auto.master`) can't be read here. */
  const Probe probe = probe_of(
      {"/etc/auto.jobs"},
      {{"/etc/auto.master.d/jobs.autofs", "/jobs auto.jobs\n"},
       {"/etc/auto.master.d/show.autofs", "/show program:/opt/studio/show-map\n"},
       {"/etc/site.master", "/assets program,sun:/opt/studio/assets\n+/etc/site.master\n"}},
      {{"/etc/auto.master.d", {"/etc/auto.master.d/jobs.autofs", "/etc/auto.master.d/show.autofs"}}});
  EXPECT_EQ(any_host_points_from_master("/x program,sun:/usr/local/bin/any-map\n+dir:/etc/auto.master.d\n"
                                        "+/etc/site.master\n+auto.master\n",
                                        probe),
            (std::vector<std::string>{"/x", "/jobs", "/show", "/assets"}));
}

TEST(ConfinementAutomount, a_wildcard_file_map_whose_host_is_the_key_is_any_host)
{
  /* `* ... &:/` (NFS) and `* ://&/` (CIFS) mount whatever host the key names; a fixed server, or a local folder, is
   * the administrator's choice. A file map typed `file:` is read the same way. */
  const Probe probe = probe_of({}, {{"/etc/auto.any", "# hosts\n*\t-fstype=nfs,rw\t&:/export\n"},
                                    {"/etc/auto.cifs", "* -fstype=cifs ://&/share\n"},
                                    {"/etc/auto.fixed", "* -fstype=nfs server:/export/&\n"},
                                    {"/etc/auto_home", "+auto_home\n* /System/Volumes/Data/Users/&\n"}});
  EXPECT_EQ(any_host_points_from_master("/a auto.any\n/c file:/etc/auto.cifs\n/f /etc/auto.fixed\n/home auto_home\n",
                                        probe),
            (std::vector<std::string>{"/a", "/c"}));
  EXPECT_FALSE(any_host_map("file:/etc/auto.net", programs)); /* `file:` says so even of an executable. */
}

TEST(ConfinementAutomount, the_mount_table_gives_them_where_they_are_mounted)
{
  const std::vector<Mount> mac = {{"/dev/disk3s1s1", "/", "apfs"},
                                  {"map auto_home", "/System/Volumes/Data/home", "autofs"},
                                  {"map -hosts", "/System/Volumes/Data/net", "autofs"},
                                  {"map -fstab", "/System/Volumes/Data/Network/Servers", "autofs"}};
  EXPECT_EQ(any_host_points_from_mounts(mac, programs), std::vector<std::string>{"/System/Volumes/Data/net"});
  /* Linux: a program map by path, by name and by type; a file map; systemd's own automounts. */
  const std::vector<Mount> linux = {{"-hosts", "/net", "autofs"},
                                    {"/etc/auto.net", "/n", "autofs"},
                                    {"auto.smb", "/smb", "autofs"},
                                    {"program:/usr/local/bin/any-map", "/p", "autofs"},
                                    {"/etc/auto.misc", "/misc", "autofs"},
                                    {"systemd-1", "/proc/sys/fs/binfmt_misc", "autofs"},
                                    {"server:/export", "/net/server/export", "nfs4"}};
  EXPECT_EQ(any_host_points_from_mounts(linux, programs), (std::vector<std::string>{"/net", "/n", "/smb", "/p"}));
}

TEST(ConfinementAutomount, a_name_inside_such_a_folder_is_remote_and_the_folder_itself_is_not)
{
  const std::vector<std::string> points = {"/net", "/System/Volumes/Data/net"};
  for (const char *inside : {"/net/host", "/net/host/share/x", "/net//host", "/System/Volumes/Data/net/h/x"}) {
    EXPECT_TRUE(inside_any(inside, points, false)) << inside;
  }
  for (const char *outside : {"/net", "/net/", "/network/x", "/netx/h", "/home/me", "/", "net/host", ""}) {
    EXPECT_FALSE(inside_any(outside, points, false)) << outside;
  }
  EXPECT_TRUE(inside_any("/NET/host", points, true));    /* macOS: case ignored. */
  EXPECT_FALSE(inside_any("/NET/host", points, false));  /* Linux: another folder. */
}

TEST(ConfinementAutomount, a_key_already_mounted_stays_usable_and_an_unmounted_one_is_refused)
{
  /* S6 MUST-1. Linux: the person opened /net/nas (its export mounted, and the trigger -hosts sets below it); a studio
   * mounts /jobs/show through a program map with a fixed server. */
  const Table linux = table_from({"/net", "/jobs", "/net/nas/export"}, {},
                                 {"/net", "/net/nas/export", "/jobs", "/jobs/show"});
  EXPECT_EQ(linux.points, (std::vector<std::string>{"/net", "/jobs"})); /* The trigger is part of its key. */
  for (const char *usable : {"/net/nas", "/net/nas/export/shot/plate.exr", "/jobs/show", "/jobs/show/seq/a.usd",
                             "/jobs", "/net", "/home/me/x"})
  {
    EXPECT_FALSE(names_an_unmounted_key(usable, linux, false)) << usable;
  }
  for (const char *refused : {"/net/other", "/net/other/x", "/net//other", "/jobs/new", "/jobs/new/x", "/net/nasx",
                              "/net/nas/../other", "/net/nas/./x", "/jobs/show/../new"})
  {
    EXPECT_TRUE(names_an_unmounted_key(refused, linux, false)) << refused;
  }
  /* macOS: /net is a link to the folder the mount table names; a mount under either spelling counts for both. */
  const Table mac = table_from({"/net", "/System/Volumes/Data/net"}, {{"/net", "/System/Volumes/Data/net"}},
                               {"/System/Volumes/Data/net", "/System/Volumes/Data/net/nas/export"});
  for (const char *usable : {"/net/nas/export/x", "/NET/NAS/export/x", "/System/Volumes/Data/net/nas/export/x"}) {
    EXPECT_FALSE(names_an_unmounted_key(usable, mac, true)) << usable;
  }
  EXPECT_TRUE(names_an_unmounted_key("/net/other", mac, true));
  EXPECT_TRUE(names_an_unmounted_key("/System/Volumes/Data/net/other/x", mac, true));
}

TEST(ConfinementAutomount, angelos_own_roots_under_a_mounted_program_map_resolve)
{
  /* A person's project root on a studio share (/show/<name> under a program map) is mounted when Angelo names it:
   * every part of the root and below it is usable, so resolve() finds it; a sibling not mounted is still remote. */
  const Table table = table_from({"/show"}, {}, {"/show", "/show/alpha"});
  for (const char *part : {"/show", "/show/alpha", "/show/alpha/assets", "/show/alpha/assets/hero.blend"}) {
    EXPECT_FALSE(names_an_unmounted_key(part, table, false)) << part;
  }
  EXPECT_TRUE(names_an_unmounted_key("/show/beta/assets", table, false));
}

TEST(ConfinementAutomount, a_key_mounted_after_the_first_region_is_refused_unless_a_root_holds_it)
{
  /* S6 re-read MUST: a mount that appears once scripts may have run (Angelo's own lazy load of an image path a
   * script set, another process) is not a connection the person made. The baseline taken at the first region's
   * start, and the person's roots, are. Before the first region, whatever is mounted counts. */
  const std::vector<Mount> live = {{"-hosts", "/net", "autofs"},
                                   {"nas:/export", "/net/nas/export", "nfs4"},
                                   {"attacker:/x", "/net/attacker/x", "nfs4"},
                                   {"filer:/jobs/show", "/jobs/show", "nfs4"}};
  const std::vector<std::string> baseline = {"/", "/net", "/net/nas/export"};
  const std::vector<std::string> roots = {"/jobs/show/project"};
  const std::vector<std::string> points = {"/net", "/jobs"};
  const Table after = table_from(points, {}, connected_folders(&baseline, live, roots));
  EXPECT_FALSE(names_an_unmounted_key("/net/nas/export/plate.exr", after, false)); /* The baseline's. */
  EXPECT_TRUE(names_an_unmounted_key("/net/attacker/x/loot", after, false));       /* Mounted later. */
  EXPECT_FALSE(names_an_unmounted_key("/jobs/show/project/a.usd", after, false));  /* Under a root. */
  EXPECT_FALSE(names_an_unmounted_key("/jobs/show/other/a.usd", after, false)); /* Same key as the root. */
  EXPECT_TRUE(names_an_unmounted_key("/jobs/new/a.usd", after, false));
  const Table before = table_from(points, {}, connected_folders(nullptr, live, roots));
  EXPECT_FALSE(names_an_unmounted_key("/net/attacker/x/loot", before, false)); /* No script ran yet. */
  EXPECT_TRUE(names_an_unmounted_key("/net/other", before, false));
}

TEST(ConfinementAutomount, the_linux_mount_table_is_read_whole)
{
  /* A line longer than any fixed buffer (an overlay's lowerdir list) is one mount, not phantom entries made of its
   * pieces; escaped spaces and tabs in a name are decoded. */
  std::string lower;
  for (int i = 0; i < 400; i++) {
    lower += (i ? ":" : "") + std::string("/var/lib/docker/overlay2/l/LAYER") + std::to_string(i);
  }
  const std::string text = "overlay / overlay rw,lowerdir=" + lower + ",upperdir=/u,workdir=/w 0 0\n" +
                           "-hosts /net autofs rw,fd=5 0 0\n" +
                           "nas:/my\\040export /net/nas/my\\040export nfs4 rw 0 0\n" + "short line\n";
  const std::vector<Mount> mounts = mounts_from_proc(text);
  ASSERT_EQ(mounts.size(), size_t(3));
  EXPECT_EQ(mounts[0].point, "/");
  EXPECT_EQ(mounts[0].type, "overlay");
  EXPECT_EQ(mounts[1].source, "-hosts");
  EXPECT_EQ(mounts[2].source, "nas:/my export");
  EXPECT_EQ(mounts[2].point, "/net/nas/my export");
}

#ifndef _WIN32
TEST(ConfinementAutomount, the_snapshot_is_kept_until_something_changes)
{
  /* S6 MUST-2: a region's start reads nothing again when no map file or mount changed, and refreshing from many
   * threads at once is safe. The per-call cost is printed for the record. */
  refresh(); /* The first region's start: the baseline is taken. */
  const std::shared_ptr<const Table> first = current_table();
  refresh();
  EXPECT_EQ(current_table().get(), first.get());
  std::vector<std::thread> threads;
  for (int t = 0; t < 4; t++) {
    threads.emplace_back([] {
      for (int i = 0; i < 200; i++) {
        refresh();
        EXPECT_FALSE(current_table()->points.empty()); /* `/net` always. */
      }
    });
  }
  for (std::thread &thread : threads) {
    thread.join();
  }
  constexpr int calls = 2000;
  const auto start = std::chrono::steady_clock::now();
  for (int i = 0; i < calls; i++) {
    refresh();
  }
  const double us = std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now() - start).count();
  printf("automount refresh: %.2f us per call (%d calls)\n", us / calls, calls);
}
#endif

}  // namespace blender::angelo_guard::automount::tests
