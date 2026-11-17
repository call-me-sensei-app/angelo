/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup GHOST
 */

#pragma once

#include <X11/Xlib.h>

/**
 * `XCloseDisplay`, without libX11 freeing stale bytes of its recycled event-queue entries as
 * event data (GHOST_XlibCloseDisplay.cc says how). Nothing may read events from the display
 * afterwards, as after `XCloseDisplay`.
 */
void GHOST_XlibCloseDisplay(Display *display);
