/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup wm
 *
 * Angelo: a Vulkan start that died before its first frame is not tried again
 * (`wm_angelo_gpu_startup.cc`). The moments the rest of Blender reports.
 */

#pragma once

namespace blender {

/** Preferences were read: at startup, before the GPU backend is chosen, and at any later read.
 * \a backend_from_command_line: the backend was given with `--gpu-backend`, which is never
 * overridden. May change #UserDef.gpu_backend to OpenGL. */
void WM_angelo_gpu_startup_preferences_read(bool backend_from_command_line);
/** The GPU is initialized and Python is about to start: from here until the main loop begins,
 * scripts and add-ons run, and a death is theirs. */
void WM_angelo_gpu_startup_scripts_begin();
/** The main loop begins: every start-up script has run. */
void WM_angelo_gpu_startup_main_loop_begin();
/** A window's frame was presented. */
void WM_angelo_gpu_startup_frame_presented();
/** Blender is exiting through #WM_exit_ex (the person quits, a script exits, a failed start). */
void WM_angelo_gpu_startup_exit();
/** The backend preference was set through RNA (Preferences > System, or a script), after the
 * new value is stored. */
void WM_angelo_gpu_startup_backend_chosen();
/** This process is crashing. For the crash handler, before anything that keeps the process alive
 * (the Windows crash dialog): only system calls on state set up earlier, no allocation. */
void WM_angelo_gpu_startup_crashed();

}  // namespace blender
