/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

namespace blender {

struct ConsoleLine;
struct SpaceConsole;

/* Main-thread, runtime-only input access. Also works while the coding buffer is
 * hidden behind another Console subtype. Never switches or initializes an editor. */
ConsoleLine *ED_console_coding_agent_input(SpaceConsole *console);

}  // namespace blender
