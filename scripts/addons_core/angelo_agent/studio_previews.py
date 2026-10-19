# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Preview IDs are disposable; Undo/file load can invalidate cached RNA handles."""


def preview_alive(entry, images):
    if not entry:
        return False
    try:
        return images.get(entry[0].name) == entry[0]
    except (ReferenceError, RuntimeError):
        return False


def release_preview(entry, images):
    if preview_alive(entry, images):
        try:
            images.remove(entry[0])
        except (ReferenceError, RuntimeError):
            pass
