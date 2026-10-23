/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * Where Python keeps its bytecode cache: never inside the installed application.
 */

#pragma once

#include <Python.h>

#include <string>

namespace blender {

/**
 * The environment variable naming the bytecode cache folder of this process for the bundled
 * interpreter's child processes (`multiprocessing`, a script started with `sys.executable`).
 * `scripts/site/sitecustomize.py` reads it; it is unset while bytecode writing is off.
 */
#define BPY_PYCACHE_PREFIX_ENV "ANGELO_PYCACHE_PREFIX"

/**
 * Before #Py_InitializeFromConfig: send bytecode to a per-user cache folder keyed by the build
 * (`<caches>/pycache/<build hash>`), so a launch never writes `__pycache__` into the installed
 * application (a macOS bundle's sealed resources, a Windows install folder). An explicit absolute
 * `PYTHONPYCACHEPREFIX` chooses the folder (a relative one turns writing off) and an explicit
 * `PYTHONDONTWRITEBYTECODE` turns writing off, as for a standalone Python, although Blender's isolated
 * configuration ignores the environment otherwise. A folder whose bytecode would land inside the
 * installed application (inside it, or `/` or another ancestor mirroring into it) turns writing off.
 */
void bpy_pycache_config_apply(PyConfig *config);

/**
 * The interpreter options a child Python needs for the same cache (`-X pycache_prefix=...`, or
 * `-B` while writing is off), for #bpy.app.python_args. Empty before #bpy_pycache_config_apply.
 */
void bpy_pycache_python_args(std::string &r_option, std::string &r_value);

}  // namespace blender
