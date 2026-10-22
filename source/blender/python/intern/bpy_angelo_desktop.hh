/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * Desktop notifications for Angelo's background jobs: the operating system's own
 * notification, under the application's name and icon, when a long job ends.
 * macOS uses the UserNotifications framework (`bpy_angelo_desktop_apple.mm`);
 * other platforms report "unsupported" (`bpy_angelo_desktop.cc`) and the add-on
 * uses its other means (`desktop_os.flash_taskbar`).
 *
 * Every function runs on the main thread and returns at once. The framework answers
 * permission and delivery on its own threads; those answers only update a stored
 * state that the next call reads.
 */

#pragma once

namespace blender {

/**
 * Whether this process may post notifications: "authorized", "denied",
 * "not_determined" (never asked), "unavailable" (not a signed application bundle,
 * a headless build) or "unsupported" (no implementation on this platform).
 * The answer is the last one the system gave; it is refreshed on every call.
 */
const char *angelo_desktop_permission();

/** Ask for permission once. The system shows its question only the first time. */
void angelo_desktop_request_permission();

/**
 * Post a notification with `identifier` (a later one with the same identifier
 * replaces it), `title` and `body` (UTF-8). Returns "posted", or the permission
 * state that kept it from being posted.
 */
const char *angelo_desktop_notify(const char *identifier, const char *title, const char *body);

/** Bounce the Dock icon once when the application is not in front ("unsupported" elsewhere). */
const char *angelo_desktop_attention();

}  // namespace blender
