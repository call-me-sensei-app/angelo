/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Code that runs when a file opens (doc/angelo/script_confinement.rst, "Work that outlives the script"). A confined
 * script may not set a text's "Register" (`use_module`, refused in bpy_rna.cc). A text it appends from another file
 * may already carry it, so when the outermost region on the main thread starts, the load-time texts are noted (each
 * datablock's session UID and its content). A .blend written while a thread is confined keeps a text's run-at-load
 * only if that text was so, with that content, when the region started; the live text is cleared at the script's
 * end by the sweep.
 */

#include <Python.h>

#include <cstdint>
#include <cstring>
#include <utility>
#include <vector>

#include "BLI_threads.hh"

#include "BKE_global.hh"
#include "BKE_main.hh"

#include "DNA_text_types.h"

#include "bpy_angelo_guard_confine_intern.hh"

namespace blender::angelo_guard {

static std::vector<std::pair<uint32_t, uint64_t>> load_time; /* Main thread only. */

static uint64_t content_of(const Text *text)
{
  uint64_t hash = 1469598103934665603ull; /* FNV-1a over every line and its end. */
  for (const TextLine &line : text->lines) {
    for (const char *c = line.line; c && *c; c++) {
      hash = (hash ^ uint8_t(*c)) * 1099511628211ull;
    }
    hash = (hash ^ '\n') * 1099511628211ull;
  }
  return hash;
}

uint64_t text_content(const void *text)
{
  return content_of(static_cast<const Text *>(text));
}

void region_text_snapshot()
{
  if (!BLI_thread_is_main() || G_MAIN == nullptr) {
    return;
  }
  load_time.clear();
  for (Text &text : G_MAIN->texts) {
    if (text.flags & TXT_ISSCRIPT) {
      load_time.emplace_back(text.id.session_uid, content_of(&text));
    }
  }
}

bool load_time_kept(const void *pointer)
{
  if (!confined_now()) {
    return true;
  }
  const Text *text = static_cast<const Text *>(pointer);
  const std::pair<uint32_t, uint64_t> now{text->id.session_uid, content_of(text)};
  for (const auto &noted : load_time) {
    if (noted == now) {
      return true;
    }
  }
  return false;
}

}  // namespace blender::angelo_guard
