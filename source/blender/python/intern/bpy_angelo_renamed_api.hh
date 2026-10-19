/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

namespace blender {

struct StructRNA;

/** How a script used a name that Blender renamed or removed. */
enum class BPyRenamedUse {
  /** A string given to an enum property (`sky_type = 'NISHITA'`). */
  EnumItem,
  /** An attribute read, written or deleted (`node.dust_density`). */
  Attribute,
  /** A string key of a collection property (`node.inputs['Specular']`). */
  CollectionKey,
};

/**
 * The sentence naming the replacement of `name`, a name Blender 4.x or 5.x renamed or removed,
 * when a script used it on `type` (or a subtype of it) as `use`; for `EnumItem` and
 * `CollectionKey`, in the property `property_id`. Null when the name is not listed.
 * Only error paths call this: it never makes a failed lookup succeed.
 */
const char *BPY_angelo_renamed_api_hint(BPyRenamedUse use,
                                        StructRNA *type,
                                        const char *property_id,
                                        const char *name);

/**
 * Call right after an attribute lookup on an instance of `type` failed. When the pending
 * exception is an `AttributeError` and `name` is listed, its message gains the replacement.
 * The exception object, its type and traceback are kept; its `obj` is set to None so that
 * Python's closest-spelling guess ("Did you mean: 'air_density'?") does not contradict the
 * replacement. Any other pending exception is left untouched.
 */
void BPY_angelo_renamed_api_attribute_error(StructRNA *type, const char *name);

}  // namespace blender
