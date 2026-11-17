/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Python a file carries runs only as the person trusted it (doc/angelo/script_confinement.rst, "Python a file
 * carries"). A text set to run when a file opens ("Register") and a Freestyle style module run unconfined, as the
 * person's own code; so whatever route brought them into the project (open, recover, revert, link, append, relocate,
 * reload, undo, an asset drop, a generator a script left), they run only when the person trusted that exact content:
 * its SHA-256 digest is in the person's list, which only the person's own actions add to:
 *
 * - "Allow Execution" in the auto-run warning, for the content held back when that warning was shown
 *   (#BPY_angelo_autorun_warning_shown, #BPY_angelo_autorun_consent);
 * - turning "Register" on (refused to a confined script), for the text's content then;
 * - their own save (never a confined thread's), for each registered text no file and no agent script brought or
 *   changed (#TXT_ANGELO_UNTRUSTED, kept by blenkernel's text.cc).
 *
 * Anything else is held back the way Blender holds back a file's scripts when auto-run is off: not run, named in the
 * warning. The list lives in the user's config folder, which a confined script can read but never write. Drivers'
 * Python is not judged here: it always runs confined (bpy_driver.cc).
 */

#include <Python.h>

#include <cstdio>
#include <cstring>
#include <mutex>
#include <string>
#include <unordered_set>
#include <vector>

#include "BLI_fileops.hh"
#include "BLI_listbase_iterator.hh"
#include "BLI_path_utils.hh"

#include "BKE_appdir.hh"
#include "BKE_main.hh"

#include "DNA_freestyle_types.h"
#include "DNA_layer_types.h"
#include "DNA_scene_types.h"
#include "DNA_text_types.h"

#include "BPY_extern.hh"

#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_sha256.hh"

namespace blender {

namespace angelo_guard {

static std::mutex mutex; /* The list is read while files load, on whatever thread loads them. */
static std::unordered_set<std::string> trusted;
static bool loaded = false;
static std::vector<std::string> blocked; /* What the last load held back (main thread). */
/* What the warning the person saw named: a later load (a script opening another file before they answer) can't
 * swap what their "Allow Execution" trusts. */
static std::vector<std::string> shown;

static constexpr const char *STORE = "angelo_trusted_scripts.txt";

static std::string digest_of(const Text *text)
{
  std::string content;
  for (const TextLine &line : text->lines) {
    if (line.line != nullptr) {
      content.append(line.line);
    }
    content.push_back('\n');
  }
  uint8_t digest[32];
  sha256(content.data(), content.size(), digest);
  static const char hex[] = "0123456789abcdef";
  std::string text_digest(64, '0');
  for (int i = 0; i < 32; i++) {
    text_digest[2 * i] = hex[digest[i] >> 4];
    text_digest[2 * i + 1] = hex[digest[i] & 15];
  }
  return text_digest;
}

static std::string store_path(const bool create)
{
  const std::optional<std::string> folder = create ? BKE_appdir_folder_id_create(BLENDER_USER_CONFIG, nullptr) :
                                                     BKE_appdir_folder_id(BLENDER_USER_CONFIG, nullptr);
  if (!folder) {
    return std::string();
  }
  char path[FILE_MAX];
  BLI_path_join(path, sizeof(path), folder->c_str(), STORE);
  return path;
}

/** Under the lock. */
static void load()
{
  if (loaded) {
    return;
  }
  loaded = true;
  const std::string path = store_path(false);
  FILE *file = path.empty() ? nullptr : BLI_fopen(path.c_str(), "rb");
  if (file == nullptr) {
    return;
  }
  char line[128];
  while (fgets(line, sizeof(line), file)) {
    size_t size = strlen(line);
    while (size > 0 && (line[size - 1] == '\n' || line[size - 1] == '\r')) {
      line[--size] = '\0';
    }
    if (size == 64) {
      trusted.insert(line);
    }
  }
  fclose(file);
}

static bool digest_trusted(const std::string &digest)
{
  std::lock_guard<std::mutex> lock(mutex);
  load();
  return trusted.count(digest) != 0;
}

/** Under the lock: add, and append to the person's list. */
static void add(const std::string &digest)
{
  load();
  if (!trusted.insert(digest).second) {
    return;
  }
  const std::string path = store_path(true);
  if (FILE *file = path.empty() ? nullptr : BLI_fopen(path.c_str(), "ab")) {
    fprintf(file, "%s\n", digest.c_str());
    fclose(file);
  }
}

bool autorun_trusted(const void *text)
{
  return digest_trusted(digest_of(static_cast<const Text *>(text)));
}

void autorun_trust(void *pointer)
{
  if (confined_now()) {
    return; /* Never an agent script's. */
  }
  Text *text = static_cast<Text *>(pointer);
  const std::string digest = digest_of(text);
  {
    std::lock_guard<std::mutex> lock(mutex);
    add(digest);
  }
  text->flags &= ~TXT_ANGELO_UNTRUSTED;
}

}  // namespace angelo_guard

using namespace angelo_guard;

bool BPY_angelo_autorun_trusted(const Text *text)
{
  return autorun_trusted(text);
}

void BPY_angelo_autorun_blocked_reset()
{
  blocked.clear();
}

void BPY_angelo_autorun_blocked_add(const Text *text)
{
  blocked.push_back(digest_of(text));
}

void BPY_angelo_autorun_warning_shown()
{
  shown = blocked;
}

void BPY_angelo_autorun_consent()
{
  if (confined_now()) {
    return;
  }
  std::lock_guard<std::mutex> lock(mutex);
  for (const std::string &digest : shown) {
    add(digest);
  }
  shown.clear();
}

bool BPY_angelo_style_module_trusted(const Text *text)
{
  return !(text->flags & TXT_ANGELO_UNTRUSTED) || autorun_trusted(text);
}

void BPY_angelo_autorun_trust_saved(Main *bmain)
{
  if (bmain == nullptr || confined_now()) {
    return;
  }
  std::lock_guard<std::mutex> lock(mutex);
  for (Text &text : bmain->texts) {
    if ((text.flags & TXT_ISSCRIPT) && !(text.flags & TXT_ANGELO_UNTRUSTED)) {
      add(digest_of(&text));
    }
  }
  for (Scene &scene : bmain->scenes) {
    for (ViewLayer &view_layer : scene.view_layers) {
      for (FreestyleModuleConfig &module : view_layer.freestyle_config.modules) {
        if (module.script != nullptr && module.is_displayed && !(module.script->flags & TXT_ANGELO_UNTRUSTED)) {
          add(digest_of(module.script));
        }
      }
    }
  }
}

}  // namespace blender
