/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup GHOST
 *
 * Closing the X display without libX11 freeing stale bytes as event data.
 *
 * libX11 recycles event-queue entries through a free list (`Display::qfree`). `_XEnq` writes an
 * incoming event's type into a recycled entry before it decides whether to keep the event, so a
 * GenericEvent it drops (one from an extension with no Xlib converter, such as Present's for a
 * window being destroyed) returns to the free list typed GenericEvent, with the previous event's
 * bytes in the rest; an entry that held a real cookie keeps that cookie's freed data.
 * `XCloseDisplay` first syncs, which reads the events still arriving and discards the queue onto
 * the free list, then `_XFreeDisplayStructure` walks the free list and frees the `data` of every
 * entry that reads as a cookie: type GenericEvent and a converter registered in
 * `Display::generic_event_vec` for the extension byte (a stale window id whose low seven bits
 * matched XInput's opcode was enough). libX11 1.8.7, unchanged on its master as of 2026-11.
 *
 * Clearing the stale entries before the call is not enough, since the sync inside it makes new
 * ones. With no converter registered nothing reads as a cookie, so the walk frees nothing: the
 * cookies already handed out (`Display::cookiejar`) are still freed, and a real cookie still
 * queued at that moment leaks its data at exit at worst. Nothing reads events after this.
 *
 * Kept apart from GHOST_SystemX11.cc so libX11's internal header (macros included) reaches no
 * other file.
 */

#include "GHOST_XlibCloseDisplay.hh"

#include <X11/Xlibint.h>

void GHOST_XlibCloseDisplay(Display *display)
{
  LockDisplay(display);
  for (auto &converter : display->generic_event_vec) {
    converter = nullptr;
  }
  UnlockDisplay(display);
  XCloseDisplay(display);
}
