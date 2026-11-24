/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * macOS and Linux: the automounter's folders that mount any host a name asks for (doc/angelo/script_confinement.rst,
 * "Network"; break-it-confine-reparse-b7d499c S6). Under such a folder even a stat of `host` mounts, so connects to,
 * `host`: the `-hosts` map (`/net` where it is configured), a program map (an executable that turns any key into a
 * location) and a file map whose wildcard entry takes its host from the key (`* &:/export`). Their folders are read
 * from the automounter's master maps (`/etc/auto_master`; `/etc/auto.master`, `/etc/auto.master.d/` and what they
 * include) and from the mount table, which also gives the real spelling (macOS mounts `/net` at
 * `/System/Volumes/Data/net`). A map that names its servers (`auto_home`, `-fstab`, a plain file map) is the
 * administrator's choice of host, not a name's, and stays as it is.
 *
 * Only a name whose key is not connected is remote. Connected means mounted before the first script region (the
 * baseline taken at its start), or holding one of the person's roots (noted, like Windows' shares, only before the
 * first region): a studio's `/jobs/show` and the person's own `/net/nas`. A key mounted later stays remote, whoever
 * mounted it: a script can make Angelo's own unconfined code mount one (an image path it set, loaded later), and
 * another process can too (S6 MUST-1 and its re-read). Before the first region no script has run, so whatever is
 * mounted counts.
 *
 * The parsers are pure, so they are tested on every system. The snapshot is kept until the maps' files or the mount
 * table change: each region's start stats the files the maps were read from and takes the mount table's identity,
 * and reads again only then (S6 MUST-2). A new table is built outside the lock, then swapped in, so a slow file system
 * never holds a lookup on another thread.
 */

#include <functional>
#include <memory>
#include <string>
#include <vector>

namespace blender::angelo_guard::automount {

/** One mounted file system: its source (for autofs, the map: `-hosts`, `map -hosts`, `/etc/auto.net`), where it is
 * mounted and its type. */
struct Mount {
  std::string source, point, type;
};

/** What the classifier may ask of the system: whether a map file is executable (a program map), a file's text
 * (a file map's entries, an included master map; empty when it can't be read) and a folder's `*.autofs` files
 * (a `+dir:` include). */
struct Probe {
  std::function<bool(const std::string &)> is_program;
  std::function<std::string(const std::string &)> text;
  std::function<std::vector<std::string>(const std::string &)> autofs_files;
};

/** Adds `point` (trailing slashes dropped) to `points` once: an absolute folder other than `/`. */
void add_point(std::vector<std::string> &points, const std::string &point);

/** Whether a map (a master line's map field, or an autofs mount's source) mounts any host a key names: `-hosts`, a
 * program map (`program:x`, `program,sun:x`, or an executable by path or by its name in /etc), or a file map with a
 * wildcard entry whose host is the key (`* -fstype=nfs &:/export`, `* ://&/share`). */
bool any_host_map(const std::string &map, const Probe &probe);

/** The folders a master map's text gives to maps of any host, following its includes (`+dir:/folder`, `+/file`,
 * `+file:/file`; a name, `+auto_master`, is the directory service's and can't be read). Comments, direct maps (`/-`)
 * and maps that name their servers are left out. */
std::vector<std::string> any_host_points_from_master(const std::string &text, const Probe &probe);

/** The folders the mount table gives to maps of any host: autofs mounts whose map `any_host_map` says so. */
std::vector<std::string> any_host_points_from_mounts(const std::vector<Mount> &mounts, const Probe &probe);

/** Whether `path` lies strictly inside one of `points` (component by component: `/net` holds `/net/h`, not
 * `/network`; the folder itself, the automounter's list, mounts nothing). `fold`: ASCII case folded (macOS). */
bool inside_any(const std::string &path, const std::vector<std::string> &points, bool fold);

/** The folders of maps of any host (`points`, each by every spelling: a link at one and its target) and the connected
 * folders (`connected`, also by every spelling under those folders). */
struct Table {
  std::vector<std::string> points, connected;
};

/** The folders that count as connected: before the first region (`baseline` empty) every live mount point; from then
 * on only the baseline's. The person's `roots` always. */
std::vector<std::string> connected_folders(const std::vector<std::string> *baseline,
                                           const std::vector<Mount> &live,
                                           const std::vector<std::string> &roots);

/** The table `points` and `connected` give: each point by the spellings `aliases` gives (link, target), each
 * connected folder under a point also by the point's other spellings. */
Table table_from(const std::vector<std::string> &points,
                 const std::vector<std::pair<std::string, std::string>> &aliases,
                 const std::vector<std::string> &connected);

/** Whether `path` would make the automounter connect: inside a folder of `table.points` at a key (`/net/<host>`,
 * `/jobs/<show>`) with no connected folder at or under it. */
bool names_an_unmounted_key(const std::string &path, const Table &table, bool fold);

/** Linux's `/proc/self/mounts` text as mounts: whole lines of any length, `\040`-style escapes decoded. */
std::vector<Mount> mounts_from_proc(const std::string &text);

/** This system's table: `/net` always (b7d499c's text rule), then the master maps and the mount table, each folder
 * also by the target of a link at it. Read on first use, then again when `refresh` sees the maps' files or the mount
 * table change, or a root was noted. A snapshot: safe to read on any thread while another refreshes. */
std::shared_ptr<const Table> current_table();
/** At each outermost region's start; the first call takes the baseline. */
void refresh();
/** One of the person's roots (`note_connected_root`: only before the first region). */
void note_root(const std::string &path);

}  // namespace blender::angelo_guard::automount
