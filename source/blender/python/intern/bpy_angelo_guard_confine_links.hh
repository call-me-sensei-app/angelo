/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * Where a Windows path really leads through the links on the way, decided without following any of them
 * (doc/angelo/script_confinement.rst, "Network"). Opening a path is the connection when a symbolic link, junction or
 * mount point on the way names a share nobody connected (the system follows every link, so a judge that opened the
 * path to learn where it leads would already have connected: the iso-win probe on a544c4b4b4d, SMB 0 -> 2).
 *
 * The walk is pure: it asks a probe for each component (opened without following it, reparse data read) and keeps a
 * real, resolved prefix, so a link's target, relative or not, is followed into the real path and never through a link
 * not yet resolved (break-it-confine-reparse-b7d499c M3). The Windows probe is in bpy_angelo_guard_confine_reparse.cc;
 * tests give their own, on every system. Names are Win32 wide paths; every probe is in `\\?\` form.
 */

#include <cstddef>
#include <cstdint>
#include <deque>
#include <functional>
#include <string>
#include <unordered_set>
#include <vector>

namespace blender::angelo_guard::links {

/** What a probe found at a path (one component, not followed). */
struct Probe {
  enum class Kind {
    Missing, /* Not there (and nothing below it): only the file system's "not found" answers. */
    Plain,   /* A file or folder that names no other place (a reparse point that keeps its own data is plain). */
    Link,    /* A symbolic link or junction or mount point: `target` is its raw substitute name. */
    Refuse,  /* Can't be told: any other error, unreadable reparse data, a name-surrogate tag of another kind. */
  };
  Kind kind = Kind::Refuse;
  bool folder = false;
  std::wstring target;
  bool relative = false; /* A symbolic link whose target is relative to its folder. */
};

/** The walk's answer for a path. */
enum class Verdict {
  Safe,       /* No link on the way leads to a share nobody connected. */
  Network,    /* A link (or the path) leads to a share nobody connected. */
  Unreadable, /* Can't be told (a form no rule admits, an error, too many probes or links): refuse it. */
};

/** A path's root and the components after it. */
struct Rooted {
  enum class Type { Drive, Unc, Volume, Bad } type = Type::Bad;
  std::wstring root;  /* `\\?\C:\`, `\\?\UNC\host\share\`, `\\?\Volume{G}\`. */
  std::wstring share; /* For a UNC root: `\\host\share`, for `lookup_may_open`. */
  std::vector<std::wstring> rest;
};

/**
 * A full Win32 path (GetFullPathNameW's) by its root: `C:\...`, `\\?\C:\...` and `\\.\C:\...` (the same drive, M2),
 * `\\host\share\...` and `\\?\UNC\host\share\...`, `\\?\Volume{G}\...`. Every other device form (`\\.\pipe\`,
 * `\\?\GLOBALROOT\`, `\\.\PhysicalDrive0`) and a `.` or `..` component in a literal `\\?\` name: Bad.
 */
Rooted split_input(const std::wstring &full);

/**
 * A link's raw substitute name, strictly (M1): `\??\X:\rest` is a local drive; `\??\UNC\host\share\rest` is a share;
 * `\??\Volume{G}\rest` is walked as `\\?\Volume{G}\rest`; a relative symbolic link's target is components to apply to
 * its real folder (Type Drive with an empty root); anything else (`\??\Global\`, `\GLOBAL??\`, `\DosDevices\`,
 * `\Device\...`, `\??\GLOBALROOT`, a pipe): Bad.
 */
Rooted split_target(const std::wstring &raw, bool relative);

/** A component without its stream (`name:stream`, `name::$DATA`): the file a stream belongs to. */
std::wstring without_stream(const std::wstring &component);

/**
 * The substitute name in a reparse buffer (FSCTL_GET_REPARSE_POINT's REPARSE_DATA_BUFFER) of `returned` bytes, for a
 * symbolic link (`tag` 0xA000000C) or mount point (0xA0000003), every offset and length checked against what was
 * returned (S5). False when it doesn't fit, is odd-sized, or the tag is another.
 */
bool parse_reparse(const unsigned char *data, size_t returned, std::wstring &target, bool &relative);

/**
 * Whether a reparse tag that isn't a name surrogate keeps the file's or folder's own data where it is (head-10/11
 * re-read SHOULD): a cloud placeholder (`IO_REPARSE_TAG_CLOUD` and its sixteen variants), a deduplicated file
 * (`IO_REPARSE_TAG_DEDUP`), a compressed one (`IO_REPARSE_TAG_WOF`). A FOLDER with any other such tag (DFS's
 * referral, a ProjFS or container placeholder, a tag yet unknown) leads somewhere a provider decides: refused.
 */
bool reparse_tag_keeps_data(uint32_t tag);

/** How a walk asks about the file system and about shares. */
struct Walker {
  std::function<Probe(const std::wstring &extended)> probe;
  /** Whether a share (`\\host\share`) may be touched: connected (or noted now, for Angelo's own roots). */
  std::function<bool(const std::wstring &share)> share_ok;
  /** What a drive root (`\\?\X:\`) stands for (head-7 MUST-A'): itself (a volume, or a drive letter mapped to a
   * share: the person's own setting), another place (a `subst` drive is `\??\` text: a folder on another drive, or a
   * share), or Bad (can't be told). Null: every drive is itself (the made-up file systems' tests). */
  std::function<Rooted(const std::wstring &root)> drive_root;
  /** After a link is resolved: whether everything from `folder` (the real prefix walked so far) through `rest` holds
   * no reparse point at all, told by one look (Windows: one open with OBJ_DONT_REPARSE), so the walk can stop there,
   * Safe. False: walked on. Null: always walked (the made-up file systems' tests). */
  std::function<bool(const std::wstring &folder, const std::deque<std::wstring> &rest, bool follow_last)> rest_plain;
  int probe_budget = 64; /* Probes per walk, as POSIX resolve caps its components (S4). */
  int link_limit = 32;   /* Links per walk: a loop, or a chain longer than any real one. */
  /** Folders already found plain (exact text, folders only: M5), or null. */
  std::unordered_set<std::wstring> *plain_folders = nullptr;
  /** Whether a folder found plain before still is (a cheap look that follows nothing: Windows' attributes, no reparse
   * point, still a folder); a hit that isn't is probed again, so a swap made since by anyone, another process too, is
   * seen. Null: every hit trusted (the made-up file systems' tests). */
  std::function<bool(const std::wstring &folder)> still_plain;
  int probes = 0; /* Out: probes made. */
};

/** Where `full` (a full Win32 path) leads: its last component followed only when `follow_last`. */
Verdict walk(const std::wstring &full, bool follow_last, Walker &walker);

}  // namespace blender::angelo_guard::links
