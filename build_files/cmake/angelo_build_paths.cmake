# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
#
# SPDX-License-Identifier: GPL-2.0-or-later

# No path of the machine that built Angelo may be embedded in what it ships (doc/angelo/releasing.rst,
# "No builder paths"). Compilers write source paths into `__FILE__` (asserts, logging, library headers
# such as USD's), debug information and the linker's object-file map; generated files (OSL bytecode,
# Python bytecode) record theirs too. This maps every such prefix to a neutral name:
#
#   the source tree     -> angelo          the build folder   -> build
#   LIBDIR              -> lib             the builder's home -> ~
#
# so a release built in any folder by anyone embeds the same paths. `release_checks.py builder-paths`
# scans the installed bundle and fails on any personal or machine path that is left.
#
# `ANGELO_MAP_BUILD_PATHS` (default ON) can be turned off for a local debugging build whose debugger
# should find the sources without a source map. Include after the platform files (they set LIBDIR).

option(ANGELO_MAP_BUILD_PATHS "Map source, build, library and home paths to neutral names in what is built" ON)
mark_as_advanced(ANGELO_MAP_BUILD_PATHS)

# `ANGELO_BUILD_PATH_MAP`: "<from>=<to>" pairs, broadest first, so a compiler that applies the last
# matching option (GCC, Clang) maps the most specific prefix; one that applied the first would still
# never leave the home folder in. Generated-file rewriters (`angelo_map_paths.cmake`) read the same list,
# most specific first.
set(ANGELO_BUILD_PATH_MAP "")
if(ANGELO_MAP_BUILD_PATHS)
  set(_angelo_map_sources)
  if(DEFINED ENV{HOME} AND NOT "$ENV{HOME}" STREQUAL "")
    list(APPEND _angelo_map_sources "$ENV{HOME}=~")
  endif()
  if(DEFINED ENV{USERPROFILE} AND NOT "$ENV{USERPROFILE}" STREQUAL "")
    list(APPEND _angelo_map_sources "$ENV{USERPROFILE}=~")
  endif()
  foreach(_angelo_pair
      "${CMAKE_SOURCE_DIR}=angelo"
      "${CMAKE_BINARY_DIR}=build"
      "${LIBDIR}=lib")
    string(REPLACE "=" ";" _angelo_parts "${_angelo_pair}")
    list(GET _angelo_parts 0 _angelo_from)
    list(GET _angelo_parts 1 _angelo_to)
    if(NOT "${_angelo_from}" STREQUAL "")
      list(APPEND _angelo_map_sources "${_angelo_from}=${_angelo_to}")
      # A symlinked or worktree path reaches the compiler resolved as well.
      get_filename_component(_angelo_real "${_angelo_from}" REALPATH)
      if(NOT "${_angelo_real}" STREQUAL "${_angelo_from}")
        list(APPEND _angelo_map_sources "${_angelo_real}=${_angelo_to}")
      endif()
    endif()
  endforeach()
  set(ANGELO_BUILD_PATH_MAP "${_angelo_map_sources}")

  foreach(_angelo_pair IN LISTS ANGELO_BUILD_PATH_MAP)
    string(REPLACE "=" ";" _angelo_parts "${_angelo_pair}")
    list(GET _angelo_parts 0 _angelo_from)
    list(GET _angelo_parts 1 _angelo_to)
    if(MSVC AND NOT CMAKE_C_COMPILER_ID MATCHES "Clang")
      # `__FILE__` (debug information goes to the .pdb, which is never shipped): the prefix is trimmed. MSVC
      # compares it case-insensitively; give it with both separators, as CMake and Ninja pass either, and
      # without a trailing one (a quoted argument ending in a backslash would escape its closing quote).
      file(TO_NATIVE_PATH "${_angelo_from}" _angelo_native)
      add_compile_options("/d1trimfile:${_angelo_native}" "/d1trimfile:${_angelo_from}")
    elseif(MSVC)
      add_compile_options("/clang:-ffile-prefix-map=${_angelo_from}=${_angelo_to}")  # clang-cl
    else()
      # `__FILE__`, `__builtin_FILE()`, debug information and coverage paths.
      add_compile_options("-ffile-prefix-map=${_angelo_from}=${_angelo_to}")
    endif()
  endforeach()

  if(MSVC)
    # The executable's debug directory names its .pdb by file name only, not the build folder.
    add_link_options("/PDBALTPATH:%_PDB%")
  elseif(APPLE)
    # ld64 records each object file's path for debuggers (N_OSO), static-library members included (LIBDIR's
    # libpython and the rest). It takes one prefix: the deepest folder holding both the build folder and
    # LIBDIR. Should that be "/", the builder's name stays in and release_checks.py builder-paths refuses it.
    set(_angelo_oso "${CMAKE_BINARY_DIR}")
    foreach(_angelo_other "${LIBDIR}")
      get_filename_component(_angelo_other "${_angelo_other}" REALPATH)
      get_filename_component(_angelo_oso "${_angelo_oso}" REALPATH)
      while(TRUE)
        string(FIND "${_angelo_other}/" "${_angelo_oso}/" _angelo_at)
        if(_angelo_at EQUAL 0 OR "${_angelo_oso}" STREQUAL "/")
          break()
        endif()
        get_filename_component(_angelo_oso "${_angelo_oso}" DIRECTORY)
      endwhile()
    endforeach()
    if("${_angelo_oso}" STREQUAL "/")
      set(_angelo_oso "")
    endif()
    add_link_options("LINKER:-oso_prefix,${_angelo_oso}/")
    unset(_angelo_oso)
    unset(_angelo_other)
    unset(_angelo_at)
  endif()
  unset(_angelo_map_sources)
  unset(_angelo_pair)
  unset(_angelo_parts)
  unset(_angelo_from)
  unset(_angelo_to)
  unset(_angelo_real)
  unset(_angelo_native)
endif()

# `angelo_map_generated_paths(<file>)`: a COMMAND list that rewrites the mapped prefixes in a generated
# text file in place (for add_custom_command), or nothing when mapping is off.
function(angelo_map_generated_paths out_var file)
  if(ANGELO_MAP_BUILD_PATHS AND ANGELO_BUILD_PATH_MAP)
    string(REPLACE ";" "::" _map "${ANGELO_BUILD_PATH_MAP}")
    set(${out_var}
      COMMAND ${CMAKE_COMMAND} "-DANGELO_FILE=${file}" "-DANGELO_MAP=${_map}"
              -P "${CMAKE_SOURCE_DIR}/build_files/cmake/angelo_map_paths.cmake"
      PARENT_SCOPE)
  else()
    set(${out_var} "" PARENT_SCOPE)
  endif()
endfunction()
