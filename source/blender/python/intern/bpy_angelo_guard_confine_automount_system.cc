/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * This system's automounter table (bpy_angelo_guard_confine_automount.hh): the reads (the maps' files are read and
 * stat'ed, the folders' links readlink'ed; the mount table is the kernel's list), the snapshot, the baseline the
 * first region takes, the person's roots and the change signal that keeps the snapshot until a map file or the mount
 * table changes (break-it-confine-reparse-b7d499c S6 MUST-2). A table is built outside the lock and swapped in.
 */

#include "bpy_angelo_guard_confine_automount.hh"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <fstream>
#include <mutex>
#include <sstream>

#ifndef _WIN32
#  include <dirent.h>
#  include <fcntl.h>
#  include <sys/stat.h>
#  include <unistd.h>
#  ifdef __APPLE__
#    include <sys/mount.h>
#    include <sys/param.h>
#  else
#    include <poll.h>
#  endif
#endif

namespace blender::angelo_guard::automount {

#ifndef _WIN32

namespace {

/** What a file looked like when read: a change to any of them reads the maps again. */
struct Stamp {
  std::string path;
  bool exists = false;
  int64_t mtime_s = 0, mtime_sub = 0; /* Compared as the system gives them: never scaled. */
  uint64_t inode = 0, size = 0;
  bool operator==(const Stamp &other) const
  {
    return exists == other.exists && mtime_s == other.mtime_s && mtime_sub == other.mtime_sub &&
           inode == other.inode && size == other.size;
  }
};

/** What `lock` guards: only copies and swaps happen under it, never a read of a file or of the mount table. */
struct State {
  std::shared_ptr<const Table> table;
  std::vector<Stamp> stamps;
  uint64_t mounts_seen = 0;
  bool has_baseline = false;
  std::vector<std::string> baseline; /* The mount points at the first region's start. */
  std::vector<std::string> roots;    /* The person's, noted before the first region. */
  bool roots_changed = false;
  uint64_t generation = 0; /* A swap only replaces the table it was built from. */
};

std::mutex lock;
State state;

Stamp stamp_of(const std::string &path)
{
  Stamp stamp{path};
  struct stat info;
  if (stat(path.c_str(), &info) == 0) {
#  ifdef __APPLE__
    stamp.mtime_s = int64_t(info.st_mtimespec.tv_sec);
    stamp.mtime_sub = int64_t(info.st_mtimespec.tv_nsec);
#  else
    stamp.mtime_s = int64_t(info.st_mtim.tv_sec);
    stamp.mtime_sub = int64_t(info.st_mtim.tv_nsec);
#  endif
    stamp.exists = true;
    stamp.inode = uint64_t(info.st_ino);
    stamp.size = uint64_t(info.st_size) ^ (uint64_t(info.st_mode) << 48);
  }
  return stamp;
}

/** The system probe: every file it reads is stamped (into `stamps`, the build's own), so a change is noticed. */
Probe noting_probe(std::vector<Stamp> &stamps)
{
  auto note = [&stamps](const std::string &path) {
    if (std::none_of(stamps.begin(), stamps.end(), [&](const Stamp &s) { return s.path == path; })) {
      stamps.push_back(stamp_of(path));
    }
  };
  Probe probe;
  probe.is_program = [note](const std::string &path) {
    note(path);
    struct stat info;
    return stat(path.c_str(), &info) == 0 && S_ISREG(info.st_mode) && (info.st_mode & 0111) != 0;
  };
  probe.text = [note](const std::string &path) {
    note(path);
    std::ifstream in(path);
    std::ostringstream text;
    text << in.rdbuf();
    return text.str();
  };
  probe.autofs_files = [note](const std::string &folder) {
    note(folder); /* A file added or removed changes the folder's stamp. */
    std::vector<std::string> files;
    if (DIR *dir = opendir(folder.c_str())) {
      while (struct dirent *entry = readdir(dir)) {
        const std::string name = entry->d_name;
        if (name.size() > 7 && name.compare(name.size() - 7, 7, ".autofs") == 0) {
          files.push_back(folder + "/" + name);
        }
      }
      closedir(dir);
    }
    std::sort(files.begin(), files.end());
    return files;
  };
  return probe;
}

uint64_t mix(uint64_t hash, const std::string &text)
{
  for (const char c : text) {
    hash = (hash ^ uint8_t(c)) * 1099511628211ull; /* FNV-1a. */
  }
  return (hash ^ 0xff) * 1099511628211ull;
}

/** The mount table, read into this call's own buffers (`getfsstat`; Linux: the whole file, `mounts_from_proc`). */
std::vector<Mount> mount_table()
{
#  ifdef __APPLE__
  std::vector<Mount> mounts;
  for (int tries = 0; tries < 3; tries++) {
    const int count = getfsstat(nullptr, 0, MNT_NOWAIT); /* The kernel's list: no file system is asked. */
    if (count <= 0) {
      break;
    }
    std::vector<struct statfs> entries(size_t(count) + 4);
    const int got = getfsstat(entries.data(), int(entries.size() * sizeof(struct statfs)), MNT_NOWAIT);
    if (got >= 0 && size_t(got) < entries.size()) {
      for (int i = 0; i < got; i++) {
        mounts.push_back({entries[i].f_mntfromname, entries[i].f_mntonname, entries[i].f_fstypename});
      }
      break;
    }
  }
  return mounts;
#  else
  std::ifstream in("/proc/self/mounts");
  std::ostringstream text;
  text << in.rdbuf();
  return mounts_from_proc(text.str());
#  endif
}

uint64_t identity(const std::vector<Mount> &mounts)
{
  uint64_t hash = 14695981039346656037ull;
  for (const Mount &mount : mounts) {
    hash = mix(mix(mix(hash, mount.source), mount.point), mount.type);
  }
  return hash;
}

#  ifndef __APPLE__
/** Linux: `/proc/self/mounts` polls with POLLPRI once the table changed since it was last read to the end. Its own
 * small lock: a poll and a drain, never a file system's wait. */
std::mutex signal_lock;
int mounts_file = -2;

bool mount_table_changed()
{
  std::lock_guard guard(signal_lock);
  if (mounts_file == -2) {
    mounts_file = open("/proc/self/mounts", O_RDONLY | O_CLOEXEC);
  }
  if (mounts_file < 0) {
    return true; /* No signal: read the table and compare its identity. */
  }
  struct pollfd entry{mounts_file, POLLPRI, 0};
  if (poll(&entry, 1, 0) <= 0 || !(entry.revents & (POLLPRI | POLLERR))) {
    return false;
  }
  char buffer[4096];
  lseek(mounts_file, 0, SEEK_SET);
  while (read(mounts_file, buffer, sizeof(buffer)) > 0) {
  }
  return true;
}
#  endif

/** A table built from scratch, with the stamps of what it read. Reads files: never under `lock`. */
struct Built {
  std::shared_ptr<const Table> table;
  std::vector<Stamp> stamps;
  uint64_t mounts_seen;
};

Built build(const std::vector<Mount> &mounts, const std::vector<std::string> *baseline,
            const std::vector<std::string> &roots)
{
  Built built;
  const Probe probe = noting_probe(built.stamps);
  std::vector<std::string> points = {"/net"};
  for (const char *master : {"/etc/auto_master", "/etc/auto.master"}) {
    for (const std::string &point : any_host_points_from_master(probe.text(master), probe)) {
      add_point(points, point);
    }
  }
  for (const std::string &file : probe.autofs_files("/etc/auto.master.d")) {
    for (const std::string &point : any_host_points_from_master(probe.text(file), probe)) {
      add_point(points, point);
    }
  }
  for (const std::string &point : any_host_points_from_mounts(mounts, probe)) {
    add_point(points, point);
  }
  /* A folder that is a link (macOS: `/net` to `/System/Volumes/Data/net`): its target too, read without following. */
  std::vector<std::pair<std::string, std::string>> aliases;
  const size_t named = points.size();
  for (size_t i = 0; i < named; i++) {
    char target[1024];
    const ssize_t length = readlink(points[i].c_str(), target, sizeof(target) - 1);
    if (length > 0 && target[0] == '/') {
      std::string to(target, size_t(length));
      while (to.size() > 1 && to.back() == '/') {
        to.pop_back();
      }
      add_point(points, to);
      aliases.emplace_back(points[i], to);
    }
  }
  built.table = std::make_shared<const Table>(
      table_from(points, aliases, connected_folders(baseline, mounts, roots)));
  built.mounts_seen = identity(mounts);
  return built;
}

/** The current table, built or checked again first. `region`: a region's start (the change signal is checked, and
 * the first one takes the baseline). Files and the mount table are read with `lock` free; only the swap holds it. */
std::shared_ptr<const Table> update(const bool region)
{
  State seen;
  std::shared_ptr<const Table> settled; /* A region's start with nothing of Angelo's own to add: checked cheaply. */
  {
    std::lock_guard guard(lock);
    if (state.table && !state.roots_changed && !region) {
      return state.table;
    }
    if (state.table && !state.roots_changed && state.has_baseline) {
      settled = state.table;
    }
    else {
      seen = state;
    }
  }
  /* A map the automounter takes on mounts its folder, so a new one shows in the mount table first. Linux asks the
   * table's change signal at every region's start (one poll); the maps' files (a stat each), and on macOS the mount
   * table (no signal: a copy of the kernel's list), are read again only after that signal or at most once a second:
   * per region they cost a script-left callback about 12 us (iso-linux on b55b844, Ruling 17). Nothing is copied
   * unless something is read again. */
  bool mounts_signalled = false;
  if (settled) {
#  ifndef __APPLE__
    mounts_signalled = mount_table_changed();
#  endif
    static std::atomic<int64_t> last_read{0};
    const int64_t now = std::chrono::duration_cast<std::chrono::milliseconds>(
                            std::chrono::steady_clock::now().time_since_epoch())
                            .count();
    if (!mounts_signalled && now - last_read.load(std::memory_order_relaxed) < 1000) {
      return settled;
    }
    last_read.store(now, std::memory_order_relaxed);
    std::lock_guard guard(lock);
    seen = state;
  }
  const bool take_baseline = region && !seen.has_baseline;
  bool changed = !seen.table || seen.roots_changed || take_baseline;
  std::vector<Mount> mounts;
  bool have_mounts = false;
  if (!changed) {
    changed = std::any_of(seen.stamps.begin(), seen.stamps.end(),
                          [](const Stamp &stamp) { return !(stamp_of(stamp.path) == stamp); });
#  ifdef __APPLE__
    mounts = mount_table(); /* One call: the kernel's list, copied into this call's buffer. */
    have_mounts = true;
    changed = changed || identity(mounts) != seen.mounts_seen;
#  else
    if (mounts_signalled || mount_table_changed()) {
      mounts = mount_table();
      have_mounts = true;
      changed = changed || identity(mounts) != seen.mounts_seen;
    }
#  endif
  }
#  ifndef __APPLE__
  else if (!seen.table) {
    mount_table_changed(); /* Opens the signal before the first read, so a later change is seen. */
  }
#  endif
  if (!changed) {
    return seen.table;
  }
  if (!have_mounts) {
    mounts = mount_table();
  }
  std::vector<std::string> baseline = seen.baseline;
  if (take_baseline) {
    baseline.clear();
    for (const Mount &mount : mounts) {
      add_point(baseline, mount.point);
    }
  }
  const bool has_baseline = seen.has_baseline || take_baseline;
  Built built = build(mounts, has_baseline ? &baseline : nullptr, seen.roots);
  std::lock_guard guard(lock);
  if (state.generation != seen.generation) {
    return state.table; /* Another thread swapped meanwhile; its table is as fresh, or will be on its next look. */
  }
  state.table = built.table;
  state.stamps = std::move(built.stamps);
  state.mounts_seen = built.mounts_seen;
  if (take_baseline) {
    state.has_baseline = true;
    state.baseline = std::move(baseline);
  }
  state.roots_changed = state.roots.size() != seen.roots.size(); /* A root noted meanwhile: built next time. */
  state.generation++;
  return state.table;
}

}  // namespace

std::shared_ptr<const Table> current_table()
{
  return update(false);
}

void refresh()
{
  update(true);
}

void note_root(const std::string &path)
{
  std::lock_guard guard(lock);
  if (state.has_baseline) {
    return; /* After the first region a root's inputs may hold what a script appended (as on Windows). */
  }
  if (std::find(state.roots.begin(), state.roots.end(), path) == state.roots.end()) {
    state.roots.push_back(path);
    state.roots_changed = true;
  }
}

#else

std::shared_ptr<const Table> current_table()
{
  static const auto none = std::make_shared<const Table>();
  return none;
}

void refresh() {}

void note_root(const std::string &path)
{
  (void)path;
}

#endif

}  // namespace blender::angelo_guard::automount
