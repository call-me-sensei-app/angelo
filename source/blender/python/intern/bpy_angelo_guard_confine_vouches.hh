/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The record of paths the person named for Angelo's own code to write (bpy_angelo_guard_confine_vouches.cc).
 * No Python here: the gtests use it directly.
 */

#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

namespace blender::angelo_guard::vouches {

/** RUN: a program Angelo found or the person chose, to start only (`add_program`): never a write vouch. */
enum class Scope { Session, Call, Pick, Typed, Root, Run };

/** What a path is now, without following a link at its last part: a regular file's identity and link count. */
struct Facts {
  bool exists = false;
  bool directory = false;
  bool link = false; /* A symbolic link, or on Windows any reparse point (a junction included). */
  uint64_t links = 0;
  uint64_t device = 0;
  uint64_t number[2] = {0, 0};
  uint64_t size = 0;      /* Its content's length, */
  int64_t modified = 0;   /* when that content was last written (ns), */
  int64_t changed = 0;    /* and when the file last changed at all (ns; Windows: its change time). */
};

Facts facts_of(const std::string &path);
/** Record `key` (a resolved key, writable.cc's `key_of`); `facts`: the file's now, its identity kept. */
bool add(const std::string &key, const Facts &facts, bool folder, bool recursive, Scope scope, const std::string &tag);
/** Whether an entry covers `key`: that file by its name, or one inside a folder named for it. Never a TYPED entry:
 * those count only for the writer that reads their property (`matches_typed`). A PICK entry only with `picks`
 * (an unconfined caller: writable.cc). */
bool matches(const std::string &key, const Facts &now, bool picks = true);
/** Whether the person typed `key` (that file, or its folder) into the property `tag` names. */
bool matches_typed(const std::string &key, const std::string &tag);
/** Whether a file named for the session lies directly in `folder` (a resolved key), or anywhere below it. */
bool file_named_in(const std::string &folder, bool at_any_depth);
/** Whether `now` is the very file (by identity) a file entry named. */
bool identity_named(const Facts &now);
/** Whether a file entry names the program at `key` and `now` is that very file, unchanged since it was named
 * (identity, size, modified and changed times): a program rewritten in place or replaced never runs on it. */
bool program_named(const std::string &key, const Facts &now);
/** Record the program at `key` to start only (Scope::Run): never matched by `matches` or `identity_named`, so it
 * never widens what a writer may write. With `keep_captured` (a script region has run), an entry already there keeps
 * the facts it captured: a program changed since is refused (false), never blessed by being found again. A record is
 * never forgotten or trimmed in this process; past PROGRAMS_KEPT a new program is refused (false). */
bool add_program(const std::string &key, const Facts &facts, bool keep_captured);
/** Whether a program recorded at `key` (Scope::Run) is no longer the file it captured: what its refusal says. */
bool program_changed(const std::string &key, const Facts &now);
/** Forget a named path (narrowing): never a program's record (Scope::Run), which only a new process starts afresh. */
void forget(const std::string &key, bool folder, bool calls_only);
void forget_scope(Scope scope);
void forget_tag(const std::string &tag);
size_t count();

}  // namespace blender::angelo_guard::vouches
