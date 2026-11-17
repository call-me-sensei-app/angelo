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

enum class Scope { Session, Call, Pick, Typed, Root };

/** What a path is now, without following a link at its last part: a regular file's identity and link count. */
struct Facts {
  bool exists = false;
  bool directory = false;
  bool link = false; /* A symbolic link, or on Windows any reparse point (a junction included). */
  uint64_t links = 0;
  uint64_t device = 0;
  uint64_t number[2] = {0, 0};
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
/** Whether a file entry names the program at `key`. */
bool program_named(const std::string &key, const Facts &now);
void forget(const std::string &key, bool folder, bool calls_only);
void forget_scope(Scope scope);
void forget_tag(const std::string &tag);
size_t count();

}  // namespace blender::angelo_guard::vouches
