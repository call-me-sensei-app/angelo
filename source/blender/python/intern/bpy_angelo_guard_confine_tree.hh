/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * Whether a folder a confined script moves holds a link (break-it-confine-reparse-b7d499c head-7 M6, the reviewer's
 * general fix; doc/angelo/script_confinement.rst, "Network"). No confined code makes a link (hook.cc `link_refused`)
 * or moves one; a move of a folder that holds one at any depth would put that link at a new path, where an open
 * judged before the move, a read Blender makes later (an image's pixels, a UDIM tile, a cache, a sequence) or a
 * native library's own open could go through it. So such a move is refused, and no lock is held between a judge
 * and its open. The walk follows nothing; it fails closed when it can't read a folder or the tree is larger than
 * `MOVE_TREE_LIMIT` entries.
 */

#include <cstddef>
#include <string>

namespace blender::angelo_guard {

/** Entries (files and folders) a moved folder may hold and still be checked; above it the move is refused. */
constexpr size_t MOVE_TREE_LIMIT = 100000;

enum class TreeVerdict {
  Plain,      /* Not a folder (a file, nothing there), or a folder with no link inside. */
  Link,       /* The path, or something inside it, is a symbolic link, junction, mount point or another file system. */
  TooLarge,   /* More than the limit of entries: not checked, refused. */
  Unreadable, /* A folder inside can't be read: refused. */
  Network,    /* The path itself leads to the network (`network_lookup`): nothing was walked. */
};

/**
 * What a move of `path` (UTF-8; relative to `dir_fd` when that is not -1, POSIX only) would carry. Windows: a
 * reparse point whose tag is a name surrogate (a symbolic link, junction, mount point, WSL link) is a link; one that
 * keeps its own data (a cloud placeholder, a deduplicated file) is not. POSIX: a symbolic link, or a folder on
 * another device (a mount inside the tree). The path is asked of the network check first (`network_lookup`, its last
 * component not followed), so nothing is looked up through a link to a share or the automounter on the way; nothing
 * the walk then finds inside is followed. `entries` (optional) gets the number of entries walked.
 */
TreeVerdict move_tree_verdict(const std::string &path, int dir_fd, size_t limit = MOVE_TREE_LIMIT,
                              size_t *entries = nullptr);

/** The refusal's text for a verdict, or null for `Plain`. */
const char *move_tree_refusal(TreeVerdict verdict);

}  // namespace blender::angelo_guard
