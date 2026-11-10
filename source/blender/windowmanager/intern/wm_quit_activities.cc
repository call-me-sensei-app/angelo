/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup wm
 *
 * Running work that quitting, or reading another file, would end: a third source for the close
 * confirmation beside unsaved data, modified images and asset catalogs (#wm_close_file_dialog).
 * Process-wide like the asset catalogs' state, so it survives a file read that replaces the window
 * manager; emptied explicitly once another document replaced this one (#WM_quit_activities_clear,
 * called right before the load-post handlers; a failed read keeps it). Insertion-ordered, so the
 * dialog lists activities in a stable order.
 */

#include <cstdint>
#include <string>
#include <utility>

#include "BLI_assert.h"
#include "BLI_threads.h"
#include "BLI_vector.hh"

#include "WM_api.hh"

namespace blender {

static Vector<std::pair<std::string, std::string>> &activities()
{
  static Vector<std::pair<std::string, std::string>> list;
  return list;
}

static bool key_alnum(const char c)
{
  return (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9');
}

/** 1 to #WM_QUIT_ACTIVITY_KEY_MAX bytes of `[a-z0-9._-]`, starting with a letter or digit. */
static bool valid_key(const StringRef key)
{
  if (key.is_empty() || key.size() > WM_QUIT_ACTIVITY_KEY_MAX || !key_alnum(key[0])) {
    return false;
  }
  for (const char c : key) {
    if (!(key_alnum(c) || c == '.' || c == '_' || c == '-')) {
      return false;
    }
  }
  return true;
}

/** A code point that must never be drawn in a line of the dialog: C0 and C1 controls (line breaks included: one
 * activity is one line), DEL, and the bidirectional controls that could reorder the dialog's text. */
static bool forbidden_code_point(const uint32_t c)
{
  return c < 0x20 || (c >= 0x7F && c <= 0x9F) || c == 0x061C || c == 0x200E || c == 0x200F ||
         (c >= 0x202A && c <= 0x202E) || (c >= 0x2066 && c <= 0x2069) || c == 0x2028 || c == 0x2029;
}

/** Whether a code point draws nothing on its own: Unicode's White_Space and Default_Ignorable_Code_Point
 * properties (PropList.txt, DerivedCoreProperties.txt): spaces, format characters such as U+FEFF, U+2060
 * and the joiners, variation selectors and the Hangul fillers. Allowed inside a message (a ZWNJ inside a
 * Persian word), never as all of it. */
static bool invisible_code_point(const uint32_t c)
{
  static const uint32_t ranges[][2] = {
      /* White_Space. */
      {0x0009, 0x000D}, {0x0020, 0x0020}, {0x0085, 0x0085}, {0x00A0, 0x00A0}, {0x1680, 0x1680},
      {0x2000, 0x200A}, {0x2028, 0x2029}, {0x202F, 0x202F}, {0x205F, 0x205F}, {0x3000, 0x3000},
      /* Default_Ignorable_Code_Point. */
      {0x00AD, 0x00AD}, {0x034F, 0x034F}, {0x061C, 0x061C}, {0x115F, 0x1160}, {0x17B4, 0x17B5},
      {0x180B, 0x180F}, {0x200B, 0x200F}, {0x202A, 0x202E}, {0x2060, 0x206F}, {0x3164, 0x3164},
      {0xFE00, 0xFE0F}, {0xFEFF, 0xFEFF}, {0xFFA0, 0xFFA0}, {0xFFF0, 0xFFF8}, {0x1BCA0, 0x1BCA3},
      {0x1D173, 0x1D17A}, {0xE0000, 0xE0FFF},
  };
  for (const auto &range : ranges) {
    if (c >= range[0] && c <= range[1]) {
      return true;
    }
  }
  return false;
}

/** Valid UTF-8 of drawable code points with at least one that draws something. */
static bool valid_message(const StringRef message)
{
  bool visible = false;
  const unsigned char *s = reinterpret_cast<const unsigned char *>(message.data());
  const int64_t size = message.size();
  for (int64_t i = 0; i < size;) {
    const unsigned char lead = s[i];
    int extra;
    uint32_t c;
    if (lead < 0x80) {
      extra = 0;
      c = lead;
    }
    else if ((lead & 0xE0) == 0xC0) {
      extra = 1;
      c = lead & 0x1F;
    }
    else if ((lead & 0xF0) == 0xE0) {
      extra = 2;
      c = lead & 0x0F;
    }
    else if ((lead & 0xF8) == 0xF0) {
      extra = 3;
      c = lead & 0x07;
    }
    else {
      return false;
    }
    if (i + extra >= size) {
      return false; /* A sequence cut short by the end. */
    }
    for (int k = 1; k <= extra; k++) {
      if ((s[i + k] & 0xC0) != 0x80) {
        return false;
      }
      c = (c << 6) | (s[i + k] & 0x3F);
    }
    static const uint32_t smallest[4] = {0, 0x80, 0x800, 0x10000};
    if (c < smallest[extra] || c > 0x10FFFF || (c >= 0xD800 && c <= 0xDFFF) ||
        forbidden_code_point(c))
    {
      return false;
    }
    if (!invisible_code_point(c)) {
      visible = true;
    }
    i += extra + 1;
  }
  return visible;
}

bool WM_quit_activity_set(const StringRef key, const StringRef message)
{
  BLI_assert(BLI_thread_is_main());
  if (!valid_key(key) || message.size() > WM_QUIT_ACTIVITY_MESSAGE_MAX ||
      (!message.is_empty() && !valid_message(message)))
  {
    return false;
  }
  Vector<std::pair<std::string, std::string>> &list = activities();
  int64_t index = -1;
  for (const int64_t i : list.index_range()) {
    if (StringRef(list[i].first) == key) {
      index = i;
      break;
    }
  }
  if (message.is_empty()) {
    if (index != -1) {
      list.remove(index);
    }
    return true;
  }
  if (index != -1) {
    list[index].second = std::string(message);
    return true;
  }
  if (list.size() >= WM_QUIT_ACTIVITIES_MAX) {
    return false;
  }
  list.append({std::string(key), std::string(message)});
  return true;
}

bool WM_quit_activities_any()
{
  BLI_assert(BLI_thread_is_main());
  return !activities().is_empty();
}

void WM_quit_activities_foreach(const FunctionRef<void(StringRef key, StringRef message)> fn)
{
  BLI_assert(BLI_thread_is_main());
  /* A copy: a callback may change the list (it never should, but the dialog must not crash). */
  const Vector<std::pair<std::string, std::string>> list = activities();
  for (const std::pair<std::string, std::string> &item : list) {
    fn(item.first, item.second);
  }
}

void WM_quit_activities_clear()
{
  BLI_assert(BLI_thread_is_main());
  activities().clear();
}

}  // namespace blender
