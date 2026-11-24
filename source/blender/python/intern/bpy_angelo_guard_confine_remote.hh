/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * Remote names and the confinement's own file-system lookups (doc/angelo/script_confinement.rst, "Network").
 *
 * On Windows opening a name is itself a network effect when the name is remote: `CreateFileW` on `\\host\share\x`
 * looks `host` up and connects to it over SMB or WebDAV, sending the person's NTLM hash, before any verdict. The
 * confinement looks names up to judge them (where a path really leads, whose a file is, which installed file a code
 * object names, whether a module a region gained resolves where it claims), many of them chosen by a script: a file
 * name, a compile's or a code object's file name, a package's `__path__` as the script left it. So every lookup it
 * makes goes through `open_for_lookup` (a handle) or the stat-family helpers below (`lookup_stat`, `lookup_is_dir`,
 * `lookup_is_file`, `lookup_fopen_read`), which never pass the system a remote name on a share nobody connected yet
 * (BREAKIT review-confine-fixes-3cb71ae002d MUST-1: refusing remote text in the judge alone left the other lookups
 * open; review-confine-fixes-eddf9bcda75 MUST-1: Blender's stat wrappers at a region's end too). Calls that remain
 * direct are listed with their reasons in `test_native_remote_lookups`.
 *
 * A share counts as connected when Angelo named it itself before any script ran (a root folder: a redirected
 * profile, a project on a share; after the first region a root's inputs may include what a script appended to
 * Angelo's own lists, so later roots note nothing) or when the system named it for a handle already open (a mapped
 * drive letter resolves to its `\\server\share`). Neither is a host a script chose. The judge still refuses every
 * remote name a script passes, by its text (`names_a_remote_file`), whatever the share.
 */

#include <cstdint>
#include <cstdio>
#include <optional>
#include <string>

#include "BLI_fileops.hh"

#include "bpy_angelo_guard_confine_links.hh"

namespace blender::angelo_guard {

/**
 * Windows' grammar, on every system so it is tested everywhere: whether `path` names a file on another host or a
 * device rather than a local file, by its text alone. Any name that starts with two separators (`\\host\share`,
 * `//host/share`, `\\?\UNC\...`, `\??\UNC\...`, `\\.\UNC\...`, `\\host@SSL\DavWWWRoot\...`, `\\.\pipe\...`,
 * `\\?\GLOBALROOT\...`), except the local drive forms `\\?\C:\...` and `\\.\C:\...`; and `\??\` followed by anything
 * but a drive.
 */
bool windows_names_remote(const std::wstring &path);

/**
 * Windows' grammar, on every system: the share a remote name opens, as `server\share` in ASCII lower case with `\`
 * separators, for `\\server\share\...`, `//server/share/...`, `\\?\UNC\server\share\...`, `\??\UNC\...` and
 * `\\.\UNC\...`. Empty for every other name: a local one, a pipe or other device, `GLOBALROOT`, a server without a
 * share, an empty server or share name, or `.` / `..` as either.
 */
std::wstring windows_share_of(const std::wstring &path);

/** Whether a lookup may open `path`: a local name, or a remote one on a connected share (above). */
bool lookup_may_open(const std::wstring &path);

/** For the refusal's text (break-it-confine-reparse-b7d499c re-read MUST-B): a judge of `path` begins on this thread,
 * and the share (`\\host\share`) the first refused remote name of that judge was on, as UTF-8, if the refusal
 * being written is of that same `path` (empty otherwise; read once). The text only: nothing is decided by it. */
void network_refusal_begin(const std::string &path);
std::string network_refusal_share(const char *path);

/** Records the share `name` is on, if any, as connected: a name the system gave an open handle. Add-only. */
void note_connected(const std::wstring &name);
/** As above for one of Angelo's root folders (UTF-8): noted only before the first script region, when no script can
 * have chosen it (the root setters refuse while confined, but a later root's inputs may hold what an earlier script
 * appended to Angelo's own lists). macOS and Linux: its automounted key, if any, stays connected
 * (bpy_angelo_guard_confine_automount.hh). */
void note_connected_root(const std::string &name);
/** The person opened the document `path` (UTF-8, Blender's own file read; coordinator's ruling on head 10): a share it
 * lies on is theirs, noted as connected, so a project they open from a UNC path after a script ran keeps working.
 * Only when `unconfined` (no confined thread anywhere, none on this stack: no script chose it, directly or by a
 * call it queued); nothing elsewhere than on Windows. Returns whether a share was noted (tests). */
bool note_person_document(const std::string &path, bool unconfined);
/** Windows' deviceless connections now are the ones that count later: taken once, before the first document loads
 * (WM_init), or at the first region where nothing took it. Nothing elsewhere. */
void remote_connections_at_start();
/** Waits for the start snapshot (no-op once taken): before any document is read, so a share that document's own paths
 * connect while it loads never counts as connected at start (Head 20 read R-8). */
void remote_connections_ready();
/** A script region began (each outermost region's start): from now on `note_connected_root` notes nothing. */
void remote_region_began();

/** Windows: whether `path` (UTF-8) names a remote file by its text (`windows_names_remote`), decoded by
 * `utf8_to_wide`; a name that doesn't decode strictly counts as remote (refused by its text). Elsewhere a leading `//`
 * is the local root; a name inside a folder of the automounter's maps of any host (`/net`, any `-hosts` or program
 * map's folder: even a stat there mounts, so connects to, the host; bpy_angelo_guard_confine_automount.hh) is
 * remote. */
bool names_a_remote_file(const std::string &path);

/**
 * Whether `text` is well-formed UTF-8 (Unicode's table 3-7: no stray or truncated sequence, no overlong form, no
 * encoded surrogate, nothing above U+10FFFF). On every system, so it is tested everywhere.
 */
bool utf8_strictly_valid(const std::string &text);

#ifdef _WIN32
/**
 * The confinement's one decoder of a UTF-8 name to the wide name Windows is given, up to the first NUL (what a C call
 * receives). False, with `out` empty, when the text is not strictly valid UTF-8: decoders differ on such text
 * (Blender's drops what it can't decode, so `\x80\\host\s\x` reads as `\\host\s\x`), so a name is checked
 * and used only in the one form this gives (review-confine-fixes-7a1bcdb652d MUST-1). True with `out` empty for an
 * empty name.
 */
bool utf8_to_wide(const std::string &utf8, std::wstring &out);
#endif

/**
 * Whether a lookup may touch `path` (UTF-8): on Windows, its text and its full name (`GetFullPathNameW`, so a
 * relative name under a share the process runs in counts too) are local or on a connected share. Elsewhere true.
 */
bool lookup_may_touch(const std::string &path);

/**
 * Windows: `folder` (UTF-8), when it is a remote name on a share nobody connected, spelled as a resolved key is
 * (`\\\\server\\share\\...` from any UNC form, `\\` separators, no trailing one), from its full name (text only:
 * nothing is looked up). Nothing for any other name, and elsewhere: for a refusing root `resolve` refused
 * (`Roots::add`).
 */
std::optional<std::string> unconnected_remote_spelling(const std::string &folder);

/** The stat-family lookups, each refused (as the call failing: -1, false, null) when `lookup_may_touch` refuses,
 * before anything reaches the system. On Windows each hands the system the very wide name it checked
 * (`BLI_wstat`, `GetFileAttributesW` as `BLI_is_dir`/`BLI_is_file` ask, `_wfopen`); elsewhere Blender's own call. A
 * call left at its own site after `lookup_may_touch` (a write the write inventory lists there) passes the same UTF-8
 * to Blender, whose decoder agrees with this one on the strictly valid UTF-8 `lookup_may_touch` alone admits. */
int lookup_stat(const std::string &path, BLI_stat_t *info);
bool lookup_is_dir(const std::string &path);
bool lookup_is_file(const std::string &path);
/** Read only (`rb`): a write is made at its own site, where the write inventory lists it (c_write_sinks.py), after
 * `lookup_may_touch`. On Windows it opens with `_wfopen`, so Blender's file gate (which `BLI_fopen` asks) is not
 * asked there, unlike POSIX: a caller that may run confined judges the name itself first, as the OpenColorIO proxy
 * does, or reads only names that are installed files (provenance). */
FILE *lookup_fopen_read(const std::string &path);

#ifdef _WIN32
/**
 * The confinement's only `CreateFileW` for a lookup: no access asked, every sharing mode, `OPEN_EXISTING`,
 * `FILE_FLAG_BACKUP_SEMANTICS` (folders too), and `FILE_FLAG_OPEN_REPARSE_POINT` unless `follow_last`. A name
 * `lookup_may_open` refuses is never passed to the system: `INVALID_HANDLE_VALUE` with `ERROR_BAD_NETPATH`, which
 * every caller treats as a lookup that failed (fail closed). The result is a `HANDLE`.
 */
void *open_for_lookup(const std::wstring &path, bool follow_last);
/** Where `path` leads through the symbolic links, junctions and mount points on the way (its last component too when
 * `follow_last`), decided without following any of them (bpy_angelo_guard_confine_links.hh; reparse.cc): Safe, Network
 * (a link leads to a share nobody connected) or Unreadable (refuse). */
links::Verdict links_walk(const std::wstring &path, bool follow_last);
/** Whether `full` (a full Win32 name) is the null or the console device in the `\\.\` namespace (`NUL`, `CON`,
 * `CONIN$`, `CONOUT$`): no file and no link; it resolves as itself (reparse.cc). Printers and ports take the normal
 * checks. */
bool dos_device(const std::wstring &full);
#endif
/** A script region began: what the link walk found plain is found again (reparse.cc). */
void links_region_began();
/** A judged move, link, make or remove: every thread's link-walk cache is dropped before its next walk (M5). */
void links_changed();
/** Whether a script region has begun in this process. */
bool remote_region_has_begun();
#ifdef _WIN32
/** Whether a `RootLinksNoted` is active on this thread: Angelo resolving its own roots before the first region, when
 * the lookups walk the links on the way whatever Strict script protection says, so a person's folder linked to a
 * share they use keeps that share connected (a one-time cost at startup, never a script's). */
bool root_links_being_noted();
#endif
/** While one lives on this thread, before the first region: a share a link on the way names is noted as connected
 * (Angelo resolving its own roots: a person's folder linked to a share they use). After the first region it does
 * nothing (a root's inputs may hold what a script appended). */
class RootLinksNoted {
 public:
  RootLinksNoted();
  ~RootLinksNoted();
  RootLinksNoted(const RootLinksNoted &) = delete;
  RootLinksNoted &operator=(const RootLinksNoted &) = delete;

 private:
  bool active_;
};

/** How many names `open_for_lookup` and the stat-family helpers have passed to the system: the tests' seam (a
 * refused name adds nothing). */
uint64_t lookups_opened();
/** The lookups and judges run their costly checks for every thread, not only a confined one, Strict script protection
 * or not (tests). */
void lookups_walk_always(bool always);
/** Whether a script's lookups and judges run the checks whose cost is measurable against Angelo's own speed (Ruling
 * 17): only with "Strict script protection" on, or for the tests. Those checks: Windows' walk of the links on the way
 * (`links_walk`), the stat family's and `os.DirEntry`'s network check (`network_lookup`, wrap_lookup*.cc), Blender's
 * own lookups for a script (`lookup_refused_by_gate`), and POSIX resolve's check of each component against the
 * automounter. Off, a judge keeps the name's text and its resolved path, and a lookup 2.0.1's checks. */
bool strict_lookups();

}  // namespace blender::angelo_guard
