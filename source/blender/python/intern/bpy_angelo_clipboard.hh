/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * A picture on the system clipboard, saved as a PNG file (`_bpy._angelo_clipboard`): what the
 * chat's composer attaches when an image is pasted (`angelo_agent/clipboard_image.py`).
 *
 * The clipboard is read through the window manager (`WM_clipboard_image_available`,
 * `WM_clipboard_image_get`), as Blender's own Image Editor paste (`IMAGE_OT_clipboard_paste`)
 * does: macOS, Windows and Wayland have a picture clipboard; X11 and background sessions have
 * none (an empty answer, never an error). Where the platform reads the picture's size from its
 * header (`WM_angelo_clipboard_image_size`: macOS, Windows, Wayland), a side past the limit is
 * refused before the picture is decoded. Each call asks the operating system, so callers ask only
 * when the person pastes, never per frame, draw or poll.
 */

#pragma once

#include <cstdint>

struct _object;

namespace blender {

struct ImBuf;

/** `_bpy._angelo_clipboard`: `image_available()` and `image_save(filepath, ...)`. */
_object *BPY_angelo_clipboard_module();

/**
 * Write \a ibuf's pixels to \a filepath (absolute) as an 8-bit PNG with PNG compression
 * \a compression (0 to 100, as #ImbFormatOptions::compress). A float-only buffer gets byte
 * pixels first (#IMB_byte_from_float). A picture whose every pixel is opaque is written without
 * an alpha channel. The PNG is encoded in memory first: \a filepath is opened only to write the
 * finished file, and a write that fails part way removes it. Changes \a ibuf's file type,
 * options and colour mode (and may add its byte pixels), never its picture; the caller still owns
 * and frees it. False when \a ibuf has no pixels or the file could not be written.
 */
bool angelo_clipboard_write_png(ImBuf *ibuf, const char *filepath, int compression);

/** What #angelo_clipboard_save did. */
enum class ClipboardSave {
  /** The file is written and within the limits. */
  Saved,
  /** A side is longer than `max_side`: nothing was encoded or written. */
  TooWide,
  /** Over `max_bytes` even at the strongest compression: nothing was written. */
  TooHeavy,
  /**
   * The file could not be written. A file this call started writing is removed; one it never
   * opened (a missing folder, no permission) is left as it was.
   */
  Failed,
  /** As #Failed, but the partly written file could not be removed: it is still at the path. */
  FailedFileLeft,
};

/** Whether a picture of \a width by \a height is refused by \a max_side (0: no limit). */
bool angelo_clipboard_side_refused(int width, int height, int max_side);

/**
 * What a save that ended in \a result leaves at \a filepath: a #ClipboardSave::Failed save that
 * \a started_writing the file removes it (it may be partly written); one that never opened it
 * leaves whatever is there, which it did not write. #ClipboardSave::FailedFileLeft when the
 * removal fails. Any other result is returned unchanged.
 */
ClipboardSave angelo_clipboard_settle(ClipboardSave result,
                                      bool started_writing,
                                      const char *filepath);

/**
 * Save \a ibuf for an attachment: refused before any encode when a side is longer than
 * \a max_side (0: no limit), encoded in memory at \a compression, and once more from the same
 * pixels at the strongest compression when that is larger than \a max_bytes (0: no limit). Only a
 * picture within the limits is written, so a refusal never touches \a filepath. Never throws: an
 * exception (an allocation failure on a huge picture) is #ClipboardSave::Failed.
 */
ClipboardSave angelo_clipboard_save(
    ImBuf *ibuf, const char *filepath, int compression, int max_side, int64_t max_bytes);

}  // namespace blender
