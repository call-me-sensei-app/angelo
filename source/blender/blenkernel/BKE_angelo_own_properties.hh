/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * \ingroup bke
 *
 * Angelo: which custom properties are Angelo's own saved records, never the person's data.
 *
 * Angelo saves records of its own as custom properties on the person's data (where a library or
 * team item came from, which character part an object is, a scene's merged parts, a render
 * region). An exporter that writes custom properties wrote every one of them into the file it
 * made, so a file handed to another tool carried the app's bookkeeping. USD and Alembic, which
 * write custom properties by default, ask this before they write one.
 *
 * The rule is the name: the two reserved prefixes, and exactly the pre-rename keys a .blend saved
 * before 2026-10-07 may still hold. The Python exporters (glTF, FBX) ask its twin,
 * `scripts/modules/angelo_own_properties.py`; `test_native_own_properties` keeps the two equal.
 */

#include <string_view>

namespace blender::bke::angelo {

/** True when the custom property `name` is one of Angelo's own saved records. */
inline bool is_own_property(const std::string_view name)
{
  using namespace std::string_view_literals;
  for (const std::string_view prefix : {"angelo_"sv, "_angelo_"sv}) {
    if (name.substr(0, prefix.size()) == prefix) {
      return true;
    }
  }
  for (const std::string_view legacy : {"sensei_director"sv,
                                        "sensei_manual_workspace"sv,
                                        "_sensei_native_graph_v1"sv,
                                        "_sensei_sculpt_regions_v1"sv,
                                        "_sensei_program_stage"sv})
  {
    if (name == legacy) {
      return true;
    }
  }
  return false;
}

}  // namespace blender::bke::angelo
