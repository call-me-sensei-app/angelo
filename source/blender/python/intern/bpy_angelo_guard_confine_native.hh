/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * The native-code modules the confinement refuses on import (doc/angelo/script_confinement.rst): their own list,
 * classified against every bundled extension module by test_blender_confinement_escapes (native_inventory.py).
 */

namespace blender::angelo_guard {

/** Modules that open files or run code natively with no entry point to judge: refused on import while confined
 * (test_blender_confinement_escapes inventories every bundled extension module against this list). dbm and gdbm
 * open databases by path in C; readline reads and writes history and init files; Tk and curses load native
 * extensions and terminal databases; the `_interpreters` family runs code in interpreters no audit hook confines;
 * `_lsprof` installs a profiler. The pipeline libraries (USD, OpenImageIO, OpenVDB, MaterialX, OpenColorIO,
 * OpenEXR, OpenTimelineIO, OSL queries, Audaspace, Mantaflow) are gated per entry point instead
 * (bpy_angelo_guard_confine_wrap_pipeline.cc). */
inline constexpr const char *NATIVE_FILE_MODULES[] = {
    "_dbm",           "_gdbm",           "readline",      "_tkinter", "tkinter", "_curses",
    "_curses_panel",  "_interpreters",   "_interpchannels", "_interpqueues", "_lsprof",
    "winsound", /* Windows: `PlaySound` reads any file natively. */
};

}  // namespace blender::angelo_guard
