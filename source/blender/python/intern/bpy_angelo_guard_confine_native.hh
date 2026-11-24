/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * The native-code modules the confinement refuses on import (doc/angelo/script_confinement.rst): their own list,
 * classified against every bundled extension module by test_blender_confinement_escapes (native_inventory.py).
 */

#include <string_view>

namespace blender::angelo_guard {

/** Modules that open files or run code natively with no entry point to judge: refused on import while confined
 * (test_blender_confinement_escapes inventories every bundled extension module against this list). dbm and gdbm
 * open databases by path in C; readline reads and writes history and init files; Tk and curses load native
 * extensions and terminal databases; the `_interpreters` family runs code in interpreters no audit hook confines;
 * `_lsprof` installs a profiler. The pipeline libraries (USD, OpenImageIO, OpenVDB, MaterialX, OpenColorIO,
 * OpenEXR, OpenTimelineIO, OSL queries, Audaspace, Mantaflow) are gated per entry point instead
 * (bpy_angelo_guard_confine_wrap_pipeline.cc). */
inline constexpr const char *NATIVE_FILE_MODULES[] = {
    "_dbm",           "_gdbm",           "readline",      "_tkinter", "tkinter", "_curses",
    "_curses_panel",  "_interpreters",   "_interpchannels", "_interpqueues", "_lsprof",
    "winsound", /* Windows: `PlaySound` reads any file natively. */
};

/** The sub-interpreter family: any of them made, in any context, has every hot path ask for good (audit hook). */
inline constexpr const char *INTERPRETER_MODULES[] = {
    "_interpreters", "_interpchannels", "_interpqueues", "_xxsubinterpreters", "_xxinterpchannels",
    "concurrent.interpreters"};

/** A module file's base name up to its first ".": `_dbm` for `.../lib-dynload/_dbm.cpython-313-darwin.so`, `_lsprof`
 * for `...\_lsprof.pyd`. */
inline std::string_view module_file_stem(const std::string_view origin)
{
  const size_t slash = origin.find_last_of("/\\");
  const std::string_view base = slash == std::string_view::npos ? origin : origin.substr(slash + 1);
  const size_t dot = base.find('.');
  return dot == std::string_view::npos ? base : base.substr(0, dot);
}

/** Whether `origin` names an extension module: "built-in", or a file ending in an extension suffix (".so", ".pyd"). */
inline bool extension_origin(const std::string_view origin)
{
  const auto ends = [&](const std::string_view suffix) {
    return origin.size() >= suffix.size() && origin.substr(origin.size() - suffix.size()) == suffix;
  };
  return origin == "built-in" || ends(".so") || ends(".pyd");
}

/** Whether a module named `name`, made from the file `origin` (empty when unknown), is one of `list`: by its full
 * name or as a submodule of it, always; and, for an extension module (`extension`: one `_imp` makes, which CPython
 * loads from any file and initializes by its last name, `PyInit_<last>`), by its last component and by its file's base
 * name too, so `x._interpreters` from `_interpreters.so` is `_interpreters` (the perf re-read of f3b1e5f, MUST-1).
 * A pure-Python module is matched by its full name only: the bundled `_pyrepl.readline` is not `readline` (the
 * re-read of 502fd7f, NIT-1). A dotted entry (`concurrent.interpreters`, pure Python) matches by its full name only. */
template<size_t N>
inline bool module_is_one_of(const std::string_view name,
                             const std::string_view origin,
                             const bool extension,
                             const char *const (&list)[N])
{
  const size_t dot = name.rfind('.');
  const std::string_view last = dot == std::string_view::npos ? name : name.substr(dot + 1);
  const std::string_view stem = origin.empty() ? std::string_view() : module_file_stem(origin);
  for (const char *entry : list) {
    const std::string_view module(entry);
    if (name == module ||
        (name.size() > module.size() && name.substr(0, module.size()) == module && name[module.size()] == '.'))
    {
      return true;
    }
    if (extension && module.find('.') == std::string_view::npos &&
        (last == module || (!stem.empty() && stem == module)))
    {
      return true;
    }
  }
  return false;
}

}  // namespace blender::angelo_guard
