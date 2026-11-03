/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * Opening the bundled meshoptimizer library: see `bpy_angelo_meshopt.hh` for the version contract.
 *
 * The library is looked for only where each platform's build installs it beside the executable (macOS
 * `Contents/Resources/lib`, portable Linux `lib`, Windows `blender.shared` or the executable's folder). Only
 * Linux then tries its plain name through the system linker's own search, for a build against the system
 * library; macOS and Windows never search by name (Windows would also search the current folder and PATH).
 * It is opened once, never closed, and only when an Angelo call first needs it, so nothing is loaded while
 * the tools go unused.
 * Only macOS has been run; the Linux and Windows paths follow those platforms' install rules in
 * `source/creator/CMakeLists.txt` and are unverified.
 */

#include <atomic>
#include <iterator>
#include <mutex>
#include <string>
#include <vector>

#ifdef _WIN32
#  ifndef WIN32_LEAN_AND_MEAN
#    define WIN32_LEAN_AND_MEAN
#  endif
#  include <windows.h>
#else
#  include <dlfcn.h>
#endif

#include "BKE_appdir.hh"

#include "bpy_angelo_meshopt.hh"

namespace blender::angelo_meshopt {


namespace {

struct Loaded {
  Status status;
#ifdef ANGELO_MESHOPT_SIMPLIFY
  Api api{};
#endif
};

#ifdef ANGELO_MESHOPT_SIMPLIFY

/** Exports first added in meshoptimizer 1.1: present means the library is 1.1 or later (see the header). */
const char *const version_evidence[] = {"meshopt_decodeMeshlet", "meshopt_opacityMapMeasure"};

#  ifdef _WIN32
using Handle = HMODULE;

Handle open_library(const std::string &path)
{
  const int size = MultiByteToWideChar(CP_UTF8, 0, path.c_str(), -1, nullptr, 0);
  std::wstring wide(size_t(size > 0 ? size : 1), L'\0');
  MultiByteToWideChar(CP_UTF8, 0, path.c_str(), -1, wide.data(), size);
  /* A full path: its own folder, then the system's default folders, for the library's dependencies. */
  return LoadLibraryExW(
      wide.c_str(), nullptr, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
}

void *find_symbol(Handle handle, const char *name)
{
  return reinterpret_cast<void *>(GetProcAddress(handle, name));
}

std::string loaded_path(Handle handle, const char * /*symbol*/)
{
  wchar_t buffer[4096];
  const DWORD length = GetModuleFileNameW(handle, buffer, DWORD(std::size(buffer)));
  if (length == 0 || length >= std::size(buffer)) {
    return "";
  }
  const int size = WideCharToMultiByte(CP_UTF8, 0, buffer, int(length), nullptr, 0, nullptr, nullptr);
  std::string path(size_t(size), '\0');
  WideCharToMultiByte(CP_UTF8, 0, buffer, int(length), path.data(), size, nullptr, nullptr);
  return path;
}

std::vector<std::string> candidates()
{
  const std::string dir = BKE_appdir_program_dir();
#    ifdef NDEBUG
  return {dir + "\\blender.shared\\meshoptimizer.dll", dir + "\\meshoptimizer.dll"};
#    else /* A debug build installs the library's debug variant (`source/creator/CMakeLists.txt`). */
  return {dir + "\\blender.shared\\meshoptimizer_d.dll", dir + "\\blender.shared\\meshoptimizer.dll"};
#    endif
}
#  else
using Handle = void *;

Handle open_library(const std::string &path)
{
  return dlopen(path.c_str(), RTLD_NOW | RTLD_LOCAL);
}

void *find_symbol(Handle handle, const char *name)
{
  return dlsym(handle, name);
}

std::string loaded_path(Handle handle, const char *symbol)
{
  Dl_info info{};
  void *address = dlsym(handle, symbol);
  if (address == nullptr || dladdr(address, &info) == 0 || info.dli_fname == nullptr) {
    return "";
  }
  return info.dli_fname;
}

std::vector<std::string> candidates()
{
  const std::string dir = BKE_appdir_program_dir();
#    ifdef __APPLE__
  return {dir + "/../Resources/lib/libmeshoptimizer.dylib"};
#    else
  return {dir + "/lib/libmeshoptimizer.so", "libmeshoptimizer.so"};
#    endif
}
#  endif

/** Resolve one entry point into `slot`, typed by the slot itself; false when the library lacks it. */
template<typename Function> bool bind(Handle handle, const char *name, Function &slot)
{
  slot = reinterpret_cast<Function>(find_symbol(handle, name));
  return slot != nullptr;
}

void load(Loaded &loaded)
{
  loaded.status.header_version = MESHOPTIMIZER_VERSION;
  Handle handle = nullptr;
  for (const std::string &path : candidates()) {
    handle = open_library(path);
    if (handle != nullptr) {
      break;
    }
  }
  if (handle == nullptr) {
    loaded.status.reason = "The meshoptimizer library is missing from this installation";
    return;
  }
  loaded.status.library_path = loaded_path(handle, "meshopt_simplify");
  bool evidence = false;
  for (const char *name : version_evidence) {
    evidence = evidence || find_symbol(handle, name) != nullptr;
  }
  if (!evidence) {
    loaded.status.reason =
        "The meshoptimizer library found is older than 1.1 (it lacks meshopt_decodeMeshlet and "
        "meshopt_opacityMapMeasure), so its simplifier's signatures may differ: nothing was called";
    return;
  }
  Api &api = loaded.api;
  const bool bound = bind(handle, "meshopt_simplify", api.simplify) &&
                     bind(handle, "meshopt_simplifyWithAttributes", api.simplify_with_attributes) &&
                     bind(handle, "meshopt_simplifyWithUpdate", api.simplify_with_update) &&
                     bind(handle, "meshopt_simplifySloppy", api.simplify_sloppy) &&
                     bind(handle, "meshopt_simplifyScale", api.simplify_scale) &&
                     bind(handle, "meshopt_generateVertexRemapMulti", api.generate_vertex_remap_multi);
  if (!bound) {
    loaded.status.reason = "The meshoptimizer library found lacks a simplifier entry point";
    return;
  }
  loaded.status.available = true;
}

#endif /* ANGELO_MESHOPT_SIMPLIFY */

std::atomic<bool> load_attempted = false;

Loaded &loaded_once()
{
  static Loaded loaded;
  static std::once_flag once;
  std::call_once(once, [] {
    load_attempted = true;
#if defined(ANGELO_MESHOPT_SIMPLIFY)
    load(loaded);
#elif defined(WITH_MESHOPTIMIZER)
    loaded.status.header_version = MESHOPTIMIZER_VERSION;
    loaded.status.reason = "This build was made against meshoptimizer " + std::to_string(MESHOPTIMIZER_VERSION) +
                           " (major * 1000 + minor * 10 + patch); the simplifier needs 1.x from 1.1";
#else
    loaded.status.reason = "This build of Angelo was made without meshoptimizer";
#endif
  });
  return loaded;
}

}  // namespace

const Api *api()
{
#ifdef ANGELO_MESHOPT_SIMPLIFY
  Loaded &loaded = loaded_once();
  return loaded.status.available ? &loaded.api : nullptr;
#else
  return nullptr;
#endif
}

Status status()
{
  return loaded_once().status;
}

bool attempted()
{
  return load_attempted;
}

}  // namespace blender::angelo_meshopt
