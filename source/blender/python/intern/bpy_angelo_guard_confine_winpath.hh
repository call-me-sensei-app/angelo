/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * Where a Windows path really leads, for C's write record (doc/angelo/script_confinement.rst, "Every write by C's
 * facts"). A junction or symbolic link inside a folder Angelo may write would carry Angelo's own writes to whatever
 * it points at (a person's Documents), and a name compared by its spelling never sees it. No Python: callable from
 * any thread. On other systems the caller resolves with `realpath` and these change nothing.
 */

#include <functional>
#include <string>

namespace blender::angelo_guard {

/**
 * Windows' spelling rules, as one spelling Angelo judges a path by (the re-read of piece 10, MUST-2 and SHOULD-1): a
 * judge must check the very file Win32 opens for the same text. On every system, so the rules are tested everywhere;
 * `full` is Win32's full-path normalization (`GetFullPathNameW`: `/` to `\`, `.` and `..` resolved, a component's
 * trailing dots and spaces dropped), empty when it fails.
 *
 * - `\\?\X` (Win32 takes it literally, normalizing nothing): `X` (`C:\...`, or `\\server\share\...` for
 *   `\\?\UNC\server\share\...`) only when `full(X)` is `X` itself, so the plain spelling names the same file; else
 *   empty. A `/` in it is a name's character there: empty.
 * - `\??\` (Win32 passes it to the system as an NT path, while `GetFullPathNameW` reads it as root-relative), a
 *   device path (`\\.\`), a root-relative path (`\x`: the current drive's) and a drive-relative one (`C:x`: that
 *   drive's own current folder): empty.
 * - `C:\...` and `\\server\share\...`: as written, `/` as `\`. A relative path too (Win32 and the judges both read it
 *   against the process's current folder).
 *
 * Empty: refused.
 */
std::string windows_plain_with(const std::string &path, const std::function<std::string(const std::string &)> &full);

/** `windows_plain_with` Win32's own normalization on Windows; elsewhere `path` unchanged. */
std::string windows_plain(const std::string &path);

/**
 * Windows: the path as the file system spells it. The longest prefix that exists is opened (no access asked, every
 * sharing mode, `FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT`, so the last component of that prefix is
 * the link itself, never what it points at) and named by `GetFinalPathNameByHandleW`: drive letter, real case, long
 * names for 8.3 ones, and every link or junction above it resolved. The components that don't exist yet follow as
 * named. Relative paths, `/`, `.` and `..` are normalized first, as Win32 does before any file call. A UNC path comes
 * back as `\\server\share\...`, a drive path as `C:\...`, with no `\\?\` prefix.
 *
 * Empty when it can't be answered (an empty or invalid name, a device path such as `\\.\pipe\x` or `NUL`, a prefix
 * that exists but can't be opened, no name for the volume): the caller refuses. Elsewhere: `utf8` unchanged.
 */
std::string final_path(const std::string &utf8);

/**
 * Windows: whether any existing component of the path, the volume's root excepted, is a name surrogate reparse point
 * (a junction, a volume mount point, a symbolic link, a WSL link): something that names another place. Cloud-file
 * placeholders (OneDrive) and other reparse points that keep their own data are not. True too when a component that
 * exists can't be examined, or the name is invalid (fail closed). Components that don't exist yet are not links.
 * Elsewhere: false.
 */
bool reparse_in_any_component(const std::string &utf8);

/**
 * Whether the path's last component, not followed, names another place: a symbolic link (every system), or on
 * Windows a name surrogate reparse point as above. `dir_fd` (POSIX only; -1: none) is the folder a relative path is
 * in. False when it doesn't exist.
 */
bool last_component_links(const std::string &utf8, int dir_fd);

}  // namespace blender::angelo_guard
