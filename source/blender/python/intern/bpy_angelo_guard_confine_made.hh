/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * The objects with no file (pipes, sockets, terminals, events) confined code made, by identity
 * (bpy_angelo_guard_confine_made.cc): a descriptor or handle naming no file is used by a confined thread only when its
 * object is one of these.
 */

#include <Python.h>

#include <cstdint>

namespace blender::angelo_guard {

/** Own the number `fd` (a descriptor) or `handle` (a Windows handle) for confined code, when confined. */
void note_made_descriptor(int fd);
void note_made_handle(intptr_t handle);
/** Whether `fd` or `handle` is a number confined code owns, still the same object (false for one that can't be told). */
bool made_descriptor(int fd);
bool made_handle(intptr_t handle);
/** Drop a number: it was closed, replaced, or passed to something that closes it unseen. */
void forget_descriptor(int fd);
/** Windows: the handle under descriptor `fd` while it is open; -1 otherwise (and elsewhere). */
intptr_t descriptor_handle(int fd);
void forget_handle(intptr_t handle);
/** Whether `fd` or `handle` is open at all (a closed one is the original's own error). */
bool descriptor_is_open(int fd);
bool handle_is_open(intptr_t handle);
/** The two sockets of `socket.socketpair()`'s result. */
void note_made_socket_pair(PyObject *pair);
/** Drop every one (the first region's start, the last's end). */
void made_objects_forget();
/** Whether this run tracks and enforces which numbers a script made: Strict script protection as the run's first
 * region found it (`ownership_run_began`), so a switch turned on mid-run takes effect from the next run (S10). */
bool ownership_tracked();
/** The first region of a run began: latch `ownership_tracked` from the switch. */
void ownership_run_began();
/** Wrap what makes them (`os.pipe`, `_winapi.CreatePipe`, ...): after the descriptor and handle judges. */
void install_makers();
/** What notes the numbers a script makes, and forgets them (FileIO.close, socket.detach): with Strict script
 * protection only (Head 20 read R-4), installed when it turns on; recorded, so turning it off removes them. */
void install_strict_makers();

}  // namespace blender::angelo_guard
