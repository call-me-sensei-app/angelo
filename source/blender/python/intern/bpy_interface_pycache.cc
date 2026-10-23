/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * Python's bytecode cache lives in a per-user folder, never inside the installed application.
 *
 * Without this, the first import of every bundled module writes `__pycache__/<name>.pyc` next to
 * its source: inside a macOS bundle that breaks the code signature's sealed resources ("Angelo is
 * damaged"), and inside a Windows install folder it leaves files Windows Installer does not own.
 * Where the folder is not writable (a protected bundle, Program Files) Python silently compiles
 * every module again at every launch instead.
 *
 * The cache is `<user caches>/pycache/<build hash>` (macOS `~/Library/Caches/Angelo/...`,
 * Windows `%LOCALAPPDATA%\Angelo\Cache\...`): Python mirrors each source's absolute path below it,
 * so installs in different folders never share a file, and keying by build means an updated app
 * never reads another build's bytecode. `angelo_agent.pycache_retention` removes old builds' folders.
 * See doc/angelo/releasing.rst, "Nothing is written inside the app".
 */

#include <Python.h>

#include <cstdlib>
#include <optional>
#include <string>
#include <vector>

#include "BLI_path_utils.hh"
#include "BLI_string.h"
#include "BLI_utildefines.h"

#include "BKE_appdir.hh"
#include "BKE_blender_version.h"

#include "bpy_interface_pycache.hh"

namespace blender {

#ifdef BUILD_DATE
extern "C" char build_hash[];
#endif

/** The folder in use, empty when there is none (writing off, or Python's default location). */
static std::string pycache_prefix;
static bool pycache_write = true;
static bool pycache_configured = false;

/** A set, non-empty environment variable, copied (Windows' #BLI_getenv returns a shared buffer). */
static std::optional<std::string> explicit_value(const char *name)
{
  const char *value = BLI_getenv(name);
  if (value == nullptr || value[0] == '\0') {
    return std::nullopt;
  }
  return std::string(value);
}

/** The build's commit hash; a build without build information shares one folder per version. */
static std::string pycache_build_key()
{
#ifdef BUILD_DATE
  if (build_hash[0] != '\0' && !STREQ(build_hash, "unknown")) {
    return build_hash;
  }
#endif
  char key[32];
  SNPRINTF(key, "dev-%d.%d", BLENDER_VERSION / 100, BLENDER_VERSION % 100);
  return key;
}

/** `<user caches>/pycache/<build key>`, or empty when the platform gives no absolute folder. */
static std::string pycache_default_prefix()
{
  char caches[FILE_MAX];
  BKE_appdir_folder_caches(caches, sizeof(caches));
  /* Before the session's temporary folder exists, the fallback for a missing caches folder is a
   * relative path, which would put bytecode below the working directory: write none instead. */
  if (caches[0] == '\0' || !BLI_path_is_abs_from_cwd(caches)) {
    return "";
  }
  char prefix[FILE_MAX];
  BLI_path_join(prefix, sizeof(prefix), caches, "pycache", pycache_build_key().c_str());
  return prefix;
}

/**
 * `path` absolute and normalized, with symbolic links resolved in its longest existing leading
 * part (the rest may not exist yet: Python creates the cache folders as it writes), and lower-cased
 * where file names compare without case (macOS by default, Windows).
 */
static std::string pycache_resolved(const char *path)
{
  char resolved[FILE_MAX];
  STRNCPY(resolved, path);
  BLI_path_canonicalize_native(resolved, sizeof(resolved));
#ifndef _WIN32
  /* Resolve the longest existing ancestor, then re-append the missing tail. */
  std::string tail;
  char head[FILE_MAX];
  STRNCPY(head, resolved);
  while (true) {
    char real[PATH_MAX];
    if (realpath(head, real) != nullptr) {
      std::string joined = real;
      if (!tail.empty()) {
        if (joined.empty() || joined.back() != SEP) {
          joined += SEP;
        }
        joined += tail;
      }
      STRNCPY(resolved, joined.c_str());
      BLI_path_normalize_native(resolved);
      break;
    }
    const char *last = BLI_path_slash_rfind(head);
    if (last == nullptr || last == head) {
      break; /* Nothing of it exists (or only the root): keep the canonical spelling. */
    }
    const std::string leaf = last + 1;
    tail = tail.empty() ? leaf : leaf + SEP + tail;
    head[last - head] = '\0';
  }
#endif
#if defined(__APPLE__) || defined(_WIN32)
  BLI_str_tolower_ascii(resolved, sizeof(resolved));
#endif
  return resolved;
}

/**
 * The folders of the installed application that must never receive a file: the program's own
 * folder (on macOS the whole `<name>.app` bundle above `Contents/MacOS`, which is sealed) and the
 * bundled Python's folder (outside the program's folder on a system-wide Linux install).
 */
static std::vector<std::string> pycache_install_folders()
{
  std::vector<std::string> folders;
  char program[FILE_MAX];
  STRNCPY(program, BKE_appdir_program_dir());
#ifdef __APPLE__
  BLI_path_parent_dir(program); /* `Contents`. */
  BLI_path_parent_dir(program); /* `<name>.app`. */
#endif
  if (program[0] != '\0') {
    folders.push_back(pycache_resolved(program));
  }
  if (const std::optional<std::string> python = BKE_appdir_folder_id(BLENDER_SYSTEM_PYTHON,
                                                                     nullptr))
  {
    folders.push_back(pycache_resolved(python->c_str()));
  }
  return folders;
}

/**
 * Where Python puts bytecode for a source in `folder` with `prefix` as `sys.pycache_prefix`: the
 * folder's absolute path below the prefix, without its drive (`importlib`'s `cache_from_source`).
 */
static std::string pycache_mirror(const std::string &prefix, const std::string &folder)
{
  const char *rest = folder.c_str();
#ifdef _WIN32
  if (rest[0] != '\0' && rest[1] == ':') {
    rest += 2;
  }
#endif
  while (*rest == '/' || *rest == '\\') {
    rest++;
  }
  char mirror[FILE_MAX];
  BLI_path_join(mirror, sizeof(mirror), prefix.c_str(), rest);
  return mirror;
}

/**
 * True when `prefix` would put bytecode inside the installed application: the prefix itself is
 * inside it, or the application's sources mirror back into it (`/`, a drive root, any ancestor
 * whose mirror lands inside). Such a prefix is refused like a relative one.
 */
static bool pycache_prefix_inside_install(const std::string &prefix)
{
  const std::string resolved_prefix = pycache_resolved(prefix.c_str());
  for (const std::string &folder : pycache_install_folders()) {
    if (BLI_path_contains(folder.c_str(), resolved_prefix.c_str()) ||
        BLI_path_contains(folder.c_str(),
                          pycache_resolved(pycache_mirror(resolved_prefix, folder).c_str()).c_str()))
    {
      return true;
    }
  }
  return false;
}

void bpy_pycache_config_apply(PyConfig *config)
{
  pycache_configured = true;
  /* Explicit requests win. Blender's isolated configuration ignores PYTHON* variables, so these two
   * are read here: a person or tool that sets them expects what a standalone Python does. */
  if (explicit_value("PYTHONDONTWRITEBYTECODE")) {
    pycache_write = false;
  }
  if (std::optional<std::string> prefix = explicit_value("PYTHONPYCACHEPREFIX")) {
    if (BLI_path_is_abs_from_cwd(prefix->c_str())) {
      pycache_prefix = *prefix;
    }
    else {
      /* Relative to the working folder, which can be inside the installed application: write none. */
      pycache_write = false;
    }
  }
  else {
    pycache_prefix = pycache_default_prefix();
    if (pycache_prefix.empty()) {
      /* Python's default location is inside the installed application: never write there. */
      pycache_write = false;
    }
  }

  if (!pycache_prefix.empty() && pycache_prefix_inside_install(pycache_prefix)) {
    /* Absolute, but the bytecode would land inside the installed application (a prefix inside it,
     * `/` or another ancestor whose mirror of the sources lands inside it): write none, as for a
     * relative prefix. Children are told the same (no #BPY_PYCACHE_PREFIX_ENV). */
    pycache_prefix.clear();
    pycache_write = false;
  }

  if (!pycache_prefix.empty()) {
    /* An explicit prefix also overrides Python's own reading of PYTHONPYCACHEPREFIX when
     * `--python-use-system-env` enables the environment (the same value). */
    const PyStatus status = PyConfig_SetBytesString(
        config, &config->pycache_prefix, pycache_prefix.c_str());
    if (PyStatus_Exception(status)) {
      pycache_prefix.clear();
      pycache_write = false;
    }
  }
  if (!pycache_write) {
    config->write_bytecode = 0;
  }
  /* The bundled interpreter's children read it in `sitecustomize` (#BPY_PYCACHE_PREFIX_ENV). */
  BLI_setenv(BPY_PYCACHE_PREFIX_ENV,
             (pycache_write && !pycache_prefix.empty()) ? pycache_prefix.c_str() : nullptr);
}

void bpy_pycache_python_args(std::string &r_option, std::string &r_value)
{
  r_option.clear();
  r_value.clear();
  if (!pycache_configured) {
    return;
  }
  if (!pycache_write) {
    r_option = "-B";
  }
  else if (!pycache_prefix.empty()) {
    r_option = "-X";
    r_value = "pycache_prefix=" + pycache_prefix;
  }
}

}  // namespace blender
