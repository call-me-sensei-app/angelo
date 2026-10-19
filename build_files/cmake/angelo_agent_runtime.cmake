# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
#
# SPDX-License-Identifier: GPL-2.0-or-later

# Angelo's agent runtime: the patched OpenAI Codex build the app bundles
# (doc/angelo/native_editor_build.rst, "Agent runtime"). Its pin,
# `scripts/addons_core/angelo_agent/agent_runtime.json`, names the version and,
# per platform, the folder inside the installed app and the files it holds;
# the add-on reads the same file to find and verify the runtime.
#
# - `ANGELO_AGENT_RUNTIME_DIR`: a folder holding this platform's runtime files,
#   built for the target (macOS arm64: `codex` and `codex-code-mode-host`).
#   They are installed under their upstream names, because Codex finds its
#   code-mode host next to its own executable. Empty: none is bundled and Codex
#   connections use the person's own Codex (development builds).
# - `ANGELO_REQUIRE_AGENT_RUNTIME`: ON for a release; configuring without the
#   folder, or with a required file missing, is then an error.
#   `build_release_macos.sh` and `build_release_windows.ps1` set it. The
#   install then also writes the pin's `release_marker`, which makes the app
#   accept only `off` in `ANGELO_AGENT_RUNTIME` (`agent_runtime.release_build`).
#
# Include after `angelo_identity.cmake`; `source/creator/CMakeLists.txt` calls
# `angelo_install_agent_runtime()` where it installs the app.

set(ANGELO_AGENT_RUNTIME_DIR "" CACHE PATH
  "Folder with this platform's agent runtime (Codex) files to bundle; empty bundles none")
option(ANGELO_REQUIRE_AGENT_RUNTIME "Fail the configure when the agent runtime is not bundled (releases)" OFF)

set(ANGELO_AGENT_RUNTIME_PIN "${CMAKE_SOURCE_DIR}/scripts/addons_core/angelo_agent/agent_runtime.json")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${ANGELO_AGENT_RUNTIME_PIN}")

# `_angelo_agent_runtime_get(<variable> <member>...)`: a member of the pin; a JSON array becomes a list.
function(_angelo_agent_runtime_get variable)
  file(READ "${ANGELO_AGENT_RUNTIME_PIN}" _json)
  string(JSON _type ERROR_VARIABLE _error TYPE "${_json}" ${ARGN})
  if(_error)
    message(FATAL_ERROR "agent_runtime.json: cannot read '${ARGN}': ${_error}")
  endif()
  if(_type STREQUAL "ARRAY")
    string(JSON _count LENGTH "${_json}" ${ARGN})
    set(_items)
    if(_count GREATER 0)
      math(EXPR _last "${_count} - 1")
      foreach(_index RANGE ${_last})
        string(JSON _item GET "${_json}" ${ARGN} ${_index})
        list(APPEND _items "${_item}")
      endforeach()
    endif()
    set(${variable} "${_items}" PARENT_SCOPE)
  else()
    string(JSON _value GET "${_json}" ${ARGN})
    set(${variable} "${_value}" PARENT_SCOPE)
  endif()
endfunction()

# `angelo_install_agent_runtime(<macos|windows> <root> [COMPONENT <component>])`:
# install the platform's required files from `ANGELO_AGENT_RUNTIME_DIR`, and its
# optional files that are present, as programs into the pin's folder under
# `<root>` (the app bundle on macOS, the install folder on Windows).
function(angelo_install_agent_runtime platform root)
  cmake_parse_arguments(ARG "" "COMPONENT" "" ${ARGN})
  _angelo_agent_runtime_get(_version version)
  _angelo_agent_runtime_get(_folder platforms ${platform} folder)
  set(destination "${root}/${_folder}")
  _angelo_agent_runtime_get(_required platforms ${platform} required)
  _angelo_agent_runtime_get(_optional platforms ${platform} optional)
  string(REPLACE ";" ", " _required_text "${_required}")
  if(NOT ANGELO_AGENT_RUNTIME_DIR)
    if(ANGELO_REQUIRE_AGENT_RUNTIME)
      message(FATAL_ERROR
        "A release bundles Angelo's agent runtime (Codex ${_version}): set ANGELO_AGENT_RUNTIME_DIR to the folder "
        "holding ${_required_text} for this platform (doc/angelo/releasing.rst, \"Agent runtime\")."
      )
    endif()
    message(STATUS
      "Agent runtime: none bundled (ANGELO_AGENT_RUNTIME_DIR is empty); Codex connections use the person's own Codex"
    )
    return()
  endif()
  if(NOT IS_DIRECTORY "${ANGELO_AGENT_RUNTIME_DIR}")
    message(FATAL_ERROR "ANGELO_AGENT_RUNTIME_DIR is not a folder: ${ANGELO_AGENT_RUNTIME_DIR}")
  endif()
  set(_files)
  set(_missing)
  foreach(_name ${_required})
    set(_path "${ANGELO_AGENT_RUNTIME_DIR}/${_name}")
    if(EXISTS "${_path}" AND NOT IS_DIRECTORY "${_path}")
      list(APPEND _files "${_path}")
    else()
      list(APPEND _missing "${_name}")
    endif()
  endforeach()
  if(_missing)
    string(REPLACE ";" ", " _missing_text "${_missing}")
    message(FATAL_ERROR
      "ANGELO_AGENT_RUNTIME_DIR (${ANGELO_AGENT_RUNTIME_DIR}) lacks ${_missing_text}: it must hold ${_required_text} "
      "built for this platform (Codex ${_version})."
    )
  endif()
  foreach(_name ${_optional})
    if(EXISTS "${ANGELO_AGENT_RUNTIME_DIR}/${_name}")
      list(APPEND _files "${ANGELO_AGENT_RUNTIME_DIR}/${_name}")
    endif()
  endforeach()
  set(_component)
  if(ARG_COMPONENT)
    set(_component COMPONENT ${ARG_COMPONENT})
  endif()
  install(
    PROGRAMS ${_files}
    DESTINATION "${destination}"
    ${_component}
  )
  if(ANGELO_REQUIRE_AGENT_RUNTIME)
    _angelo_agent_runtime_get(_marker platforms ${platform} release_marker)
    get_filename_component(_marker_folder "${_marker}" DIRECTORY)
    get_filename_component(_marker_name "${_marker}" NAME)
    file(WRITE "${CMAKE_BINARY_DIR}/${_marker_name}"
      "{\"release\": true, \"agent_runtime\": \"${_version}\"}\n"
    )
    install(
      FILES "${CMAKE_BINARY_DIR}/${_marker_name}"
      DESTINATION "${root}/${_marker_folder}"
      ${_component}
    )
  endif()
  message(STATUS "Agent runtime: Codex ${_version} from ${ANGELO_AGENT_RUNTIME_DIR} into ${destination}")
endfunction()
