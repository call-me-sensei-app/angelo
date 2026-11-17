/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * Where a path really is, for the confinement (doc/angelo/script_confinement.rst). No Python and no GIL: the file
 * gate calls it from any thread.
 *
 * A path is resolved the way the system resolves it: relative to the working folder or to the folder a descriptor
 * names, through every symbolic link, junction and `..` as far as it exists, `/dev/fd/N` and `/proc/self/fd/N` as the
 * file that descriptor has open. It is then compared with each root by key (the resolved spelling, folded to
 * lower case on macOS and Windows, whose file systems ignore case) and by folder identity (device and inode, or
 * volume and file index), so a firmlink, a hard-linked folder, a short name or another spelling of a root folder is
 * still that folder.
 */

#include <cstdint>
#include <cstdio>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "bpy_angelo_guard_confine_changes.hh"

namespace blender::angelo_guard {

/** A folder's identity: (device, inode) on POSIX, (volume serial, file index) on Windows. */
using Identity = std::pair<uint64_t, uint64_t>;

/** The real path `path` names, or nothing when it cannot be resolved (refuse it). `dir_fd` >= 0: a relative path is
 * relative to that descriptor's folder. `path` is UTF-8. */
std::optional<std::string> resolve(const std::string &path, int dir_fd = -1);
/** The path of the file a descriptor has open, or nothing (a pipe, a socket, an unknown descriptor). */
std::optional<std::string> descriptor_path(int descriptor);
/** The resolved path's key: lower case on macOS and Windows. */
std::string key(const std::string &resolved);
/** The identities of every existing folder from `resolved` up to the root, nearest first. */
std::vector<Identity> ancestors(const std::string &resolved);
/** A folder's identity, or nothing when it does not exist or is not a folder. */
std::optional<Identity> folder_identity(const std::string &path);
/** Any existing entry's identity (a file or a folder), or nothing. */
std::optional<Identity> entry_identity(const std::string &path);
/** Angelo's own file access, past Blender's file gate (the confinement's own record): UTF-8 paths everywhere. */
FILE *open_ungated(const std::string &path, const char *mode);
/** Atomically puts `from` at `to`, replacing it. */
bool replace_ungated(const std::string &from, const std::string &to);

/** A set of folders, add-only. Thread-safe. */
class Roots {
 public:
  void add(const std::string &folder);
  /** Whether `resolved` is one of these folders or inside one (`chain`: its `ancestors`). */
  bool holds(const std::string &resolved, const std::vector<Identity> &chain) const;
  /** Whether `resolved` is a folder that holds one of these (a link to it reaches them). */
  bool above(const std::string &resolved) const;
  /** The key of the deepest of these folders that holds `resolved` by its spelling (never by identity: another
   * spelling of the folder is not it), or nothing. */
  std::optional<std::string> deepest(const std::string &resolved) const;
  /** Whether one of these folders is the folder `outer` (a key) or lies inside it, and holds `resolved`: by its
   * spelling, or by an identity in `chain` that is not in `outer_chain` (the identities strictly above `outer`). */
  bool inside_of(const std::string &resolved,
                 const std::string &outer,
                 const std::vector<Identity> &chain,
                 const std::vector<Identity> &outer_chain) const;
  size_t size() const;

 private:
  std::vector<std::string> keys_;
  std::vector<Identity> ids_;
};

/** Folder names that stay private anywhere below an open folder (the native CLI profile families). Add-only.
 * Thread-safe. */
class Names {
 public:
  void add(const std::string &name);
  /** Whether a folder between the folder `outer` (a key) and `resolved`, or `resolved` itself, has one of these
   * names. */
  bool below(const std::string &resolved, const std::string &outer) const;

 private:
  std::vector<std::string> names_;
};

/** Folders named `<prefix>*` directly inside a parent folder. Add-only. Thread-safe. */
class Families {
 public:
  void add(const std::string &parent, const std::string &prefix);
  bool holds(const std::string &resolved) const;

 private:
  std::vector<std::pair<std::string, std::string>> pairs_;
};

/** Why a confined thread is refused `path`: none, a private root (any access), a startup root (a change), or a
 * tool-output root (a change: files Angelo's own tools wrote there for scripts to read, such as downloads). */
enum class Refusal { None, Private, Startup, ToolOutput };

/** The confinement's folders: private (never read or written), startup (never changed), tool output (never
 * changed; never trusted), open (inside a private folder, yet used freely: the chat's default workspace, as the file
 * tools use it; below it a private folder inside it, a family or a folder with a private name stays private),
 * families, trusted (whose files are installed code). The deepest folder decides. One process-wide instance
 * (`folders()`). */
struct Folders {
  Roots private_roots;
  Roots startup_roots;
  Roots tool_roots;
  Roots open_roots;
  Names open_private_names;
  Roots trusted_roots;
  Families families;

  /** `write`: any change, and `change` what it does (what the record keeps: bpy_angelo_guard_confine_changes.hh);
   * `link_target`: the target of a new link (it may not reach into or above a root). */
  Refusal judge(const std::string &path,
                bool write,
                int dir_fd = -1,
                bool link_target = false,
                Change change = Change::Content) const;
  bool trusted(const std::string &path) const;
  /** Whether code at `path` is the agent's, whoever loads it (bpy_angelo_guard_confine_hook.cc): a file a confined
   * thread wrote, moved or linked, or in a folder it moved, linked or filled (`changes_made`); outside installed
   * folders also a file with the identity or content of one it wrote or moved; or tool output. */
  bool agent_made(const std::string &path) const;
  /** Whether a confined script authored the file at `path` (changes_wrote: by identity and path, never by bytes). */
  bool script_wrote(const std::string &path) const;
  /** Whether the folder `folder_key` may be recorded whole: none of these roots lies in it. */
  bool compactable(const std::string &folder_key) const;
  /** The person forgets what scripts changed in `folder` (`changes_forget`); -2: not a folder. */
  int forget(const std::string &folder) const;
};

Folders &folders();
/** Set by the last `judge` on this thread when it refused a change only because it couldn't be recorded. */
extern thread_local bool judged_unrecorded;

}  // namespace blender::angelo_guard
