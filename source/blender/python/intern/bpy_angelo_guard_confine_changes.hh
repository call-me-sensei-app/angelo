/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * The record of what confined threads changed (doc/angelo/script_confinement.rst, "What scripts wrote"), on which
 * "Angelo never loads a script's file as its own" rests: kept across sessions in the user config folder
 * (`angelo_agent_changed.txt`). No Python and no GIL.
 *
 * It records FILES, never ancestry:
 * - a path a script wrote, moved or linked (a folder counts only when a script moved or linked it, or wrote more
 *   than `COMPACT_CHILDREN` entries into it: compaction, which also keeps the record bounded);
 * - the identity (device and inode, or volume and file index) and a content digest of each file a script wrote or
 *   moved, so a copy or a rename of it elsewhere is still the script's.
 * A folder made, a time or mode changed, a file removed: never recorded (they carry no code).
 */

#include <cstdint>
#include <functional>
#include <string>

namespace blender::angelo_guard {

/** What a confined change does to the path it names. */
enum class Change {
  Content,  /* Writes a file's bytes (open for writing, truncate, copy to, a native writer). */
  Moved,    /* Puts an existing file or folder at this path (rename, move). */
  Linked,   /* Makes a link at this path (hard link, symbolic link, junction). */
  Metadata, /* Makes or removes an entry, or changes its mode, owner, times or flags: nothing recorded. */
  Named,    /* A path a script named (an operator's file to read, a path property): recorded by its path only (code
             * read from it later is the script's), never its identity or content: naming the person's file is no
             * authorship of it (the coordinator's ruling on the re-read of f9f24cc6ffd). */
};

/** Above this many entries a script wrote, moved or linked directly into a folder, the folder is recorded whole. */
constexpr size_t COMPACT_CHILDREN = 256;
/** Path records kept at most; beyond it the deepest compactable folder of the new path is compacted. */
constexpr size_t MAX_PATHS = 16384;
/** Content records (identity, size, digest) kept at most, newest first. */
constexpr size_t MAX_CONTENT = 8192;
/** A load looks for another session's changes to the record at most this often (a change recorded here always
 * looks first): every import after a script pays a clock read, not a `stat`. */
constexpr int FRESH_MS = 200;
/** Files up to this size are digested whole; a bigger one by its size, first and last `EDGE_BYTES`. */
constexpr int64_t EDGE_BYTES = 64 * 1024;

/** The file the record is kept in (the app's install). Empty: in memory only (the C++ tests). */
void changes_record_set(const std::string &path);
/**
 * Note that a confined thread is about to change `resolved` (its key: `trimmed_key`). `compactable(folder_key)`:
 * whether a folder may be recorded whole (no Angelo root inside it, not a volume's root). False when the change
 * can't be recorded (the record unreadable or unwritable, or full with nothing to compact): refuse the change.
 */
bool changes_note(const std::string &path_key,
                  Change change,
                  const std::function<bool(const std::string &)> &compactable);
/** Record the identity and digest of what was noted since the last settle (a region's end; a load). */
void changes_settle();

enum class Made { No, Yes, Unknown /* The record can't be read: fail closed. */ };
/**
 * Whether code at `path_key` is a script's: its path or a recorded folder above it, or, when `by_content()` says
 * to look (asked only then: the common load costs no more), its identity or content equals a file a script wrote
 * or moved.
 */
Made changes_made(const std::string &path_key, const std::function<bool()> &by_content);
/**
 * Whether a script AUTHORED the file at `path_key` (whose its data is, for a .blend a script opens, appends or links:
 * the coordinator's ruling on the re-read of f9f24cc6ffd): this very file by identity (written or moved by a
 * script, wherever it is now), or its own recorded path (never only a folder above it) while the file there is still
 * the one written (its identity or content matches a write; replaced since by the person: not the script's). Never by bytes alone: a script's copy of
 * the person's project leaves the person's original theirs.
 */
Made changes_wrote(const std::string &path_key);
/**
 * The person forgets what scripts changed in the folder `folder_key`: path records in it or above it, and content
 * records of the files now in it. Returns how many records went, or -1 when the record can't be rewritten.
 */
int changes_forget(const std::string &folder_key);
/** Path and content records held now (tests). */
size_t changes_paths_count();
size_t changes_content_count();
/** Everything forgotten, in memory only (tests). */
void changes_reset();

}  // namespace blender::angelo_guard
