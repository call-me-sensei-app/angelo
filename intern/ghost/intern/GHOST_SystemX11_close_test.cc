/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Quitting closes the X display without freeing what libX11 only takes for event data.
 *
 * libX11 (1.8.7 and its current master) recycles event-queue entries through a free list
 * (`Display::qfree`), and `_XEnq` writes an incoming event's type into a recycled entry before it
 * decides to drop the event: a GenericEvent from an extension with no Xlib converter (Present's,
 * for a window that has just closed) goes back on the free list typed GenericEvent, with the
 * previous event's bytes in the rest. At `XCloseDisplay`, `_XFreeDisplayStructure` walks that
 * free list (not the queue) and frees the `data` of every entry that reads as a cookie of an
 * extension with a converter: a stale window id whose low seven bits match XInput's opcode was
 * enough. Blender then aborted on quit in `free()`, inside `XCloseDisplay` (seen on Xvfb with
 * Mesa after a second window, the Preferences, had closed).
 *
 * The tests leave such an entry on a real system's display, on the free list or still queued (the
 * sync inside `XCloseDisplay` discards the queue onto the free list), and quit. Each quits in a
 * child process (a death test, re-executed rather than forked so no X connection is shared): an
 * abort there fails the test with its signal instead of ending the test program.
 */

#include "testing/testing.h"

#include <cstdlib>
#include <stdexcept>
#include <string>

#include "GHOST_SystemX11.hh"

/* After GHOST's headers: libX11's internal layout (#Display::qfree, #_XQEvent). */
#include <X11/Xlibint.h>

namespace ghost_x11_close::tests {

/* Bytes that are not a heap block, as a stale entry's are. */
static char not_heap_memory[64];

/** Why this machine cannot run the tests (they skip), or "" when it can. A set DISPLAY that does
 * not open fails instead: a dead X server must not pass as a machine without one. */
static std::string why_not_here()
{
  const char *name = std::getenv("DISPLAY");
  if (name == nullptr || name[0] == '\0') {
    return "DISPLAY is not set: no X server to quit from";
  }
  GHOST_SystemX11 *system = nullptr;
  try {
    system = new GHOST_SystemX11();
  }
  catch (const std::runtime_error &error) {
    ADD_FAILURE() << "DISPLAY=" << name << " is set but the X display does not open: " << error.what();
    return "the X display does not open";
  }
  Display *display = system->getXDisplay();
  int opcode = 0, first_event = 0, first_error = 0;
  std::string why;
  if (!XQueryExtension(display, "XInputExtension", &opcode, &first_event, &first_error)) {
    why = "this X server has no XInput extension, so no event converter makes a stale entry a cookie";
  }
  else if (display->generic_event_vec[opcode & 0x7F] == nullptr) {
    why = "libXi registered no event converter for XInput on this display";
  }
  delete system;
  return why;
}

/** Opens a real system, puts an entry whose bytes read as an XInput cookie (data no heap block) on
 * its free list or at the end of its queue, and quits: exits 0 unless the close aborts. */
static void quit_with_a_cookie_lookalike(const bool queued)
{
  GHOST_SystemX11 *system = new GHOST_SystemX11();
  Display *display = system->getXDisplay();
  int opcode = 0, first_event = 0, first_error = 0;
  XQueryExtension(display, "XInputExtension", &opcode, &first_event, &first_error);
  _XQEvent *entry = static_cast<_XQEvent *>(calloc(1, sizeof(_XQEvent)));
  entry->event.xcookie.type = GenericEvent;
  entry->event.xcookie.extension = opcode;
  entry->event.xcookie.data = not_heap_memory;
  if (queued) {
    entry->qserial_num = display->next_event_serial_num++;
    if (display->tail) {
      display->tail->next = entry;
    }
    else {
      display->head = entry;
    }
    display->tail = entry;
    display->qlen++;
  }
  else {
    entry->next = display->qfree;
    display->qfree = entry;
  }
  delete system; /* Closes the display: aborted in `free()` before the fix. */
  std::exit(0);
}

TEST(ghost_x11, quit_with_a_stale_cookie_on_the_free_list)
{
  const std::string why = why_not_here();
  if (HasFailure()) {
    return;
  }
  if (!why.empty()) {
    GTEST_SKIP() << why;
  }
  GTEST_FLAG_SET(death_test_style, "threadsafe");
  EXPECT_EXIT(quit_with_a_cookie_lookalike(false), testing::ExitedWithCode(0), "");
}

/* The sync inside #XCloseDisplay discards the queue onto the free list before the walk: an entry
 * still queued then is one too, so clearing the free list beforehand is not enough. */
TEST(ghost_x11, quit_with_a_stale_cookie_still_queued)
{
  const std::string why = why_not_here();
  if (HasFailure()) {
    return;
  }
  if (!why.empty()) {
    GTEST_SKIP() << why;
  }
  GTEST_FLAG_SET(death_test_style, "threadsafe");
  EXPECT_EXIT(quit_with_a_cookie_lookalike(true), testing::ExitedWithCode(0), "");
}

}  // namespace ghost_x11_close::tests
