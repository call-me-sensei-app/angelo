/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * \ingroup bke
 *
 * Identifiers that preferences, keymaps and .blend files saved before the
 * 2026-10-07 product rename may contain, and their Angelo successors. The full
 * list of retained pre-rename names is "Legacy names" in
 * `doc/angelo/native_editor_build.rst`. New code never uses the old names.
 */

namespace blender {

struct UserDef;

/**
 * The current idname of an operator or menu saved under its pre-rename idname,
 * in the stored `PREFIX_OT_name` form (`SCULPT_OT_...`, `..._MT_...`), or null.
 * Only the exact names the product shipped are mapped.
 */
const char *BKE_angelo_legacy_idname(const char *idname);

/** The current Console language for a pre-rename AI editor language, or null. */
const char *BKE_angelo_legacy_console_language(const char *language);

/**
 * Rename pre-rename identifiers in freshly read preferences: the enabled add-on
 * module (keeping its preferences; an existing current entry wins), user keymap
 * items and diff items, and Quick Favorites operators, menus and properties.
 * Idempotent, so it runs on every preferences read.
 */
void BKE_angelo_legacy_userdef_update(UserDef *userdef);

}  // namespace blender
