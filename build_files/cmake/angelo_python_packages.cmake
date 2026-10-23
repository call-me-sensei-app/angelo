# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
#
# SPDX-License-Identifier: GPL-2.0-or-later

# Pure-Python packages Angelo ships newer than the pinned precompiled libraries hold
# (doc/angelo/releasing.rst, "Bundled trust stores"): certifi, the root store Angelo's TLS uses,
# and pip, which vendors its own copy. `angelo_python_packages.json` pins each wheel (PyPI URL and
# SHA-256). Configuring downloads the wheels into the build folder, checked against that hash, or
# takes them from `ANGELO_PYTHON_WHEELS_DIR` (an offline build); a missing or mismatching wheel is a
# configure error, so no build ships the libraries' older copies (the release check,
# `scripts/angelo/trust_store.py`, refuses one that does). The install step runs
# `build_files/utils/angelo_python_packages.py` with the installed interpreter, which checks the
# hashes again and replaces the bundled copies.
#
# Include after `angelo_identity.cmake`; `source/creator/CMakeLists.txt` calls
# `angelo_install_python_packages()` after the bundled Python and its packages are installed and
# before `angelo_install_python_bytecode()`.

set(ANGELO_PYTHON_PACKAGES_PIN "${CMAKE_SOURCE_DIR}/build_files/cmake/angelo_python_packages.json")
set(ANGELO_PYTHON_PACKAGES_SCRIPT "${CMAKE_SOURCE_DIR}/build_files/utils/angelo_python_packages.py")
set(ANGELO_PYTHON_WHEELS_DIR "" CACHE PATH
  "Folder holding the wheels angelo_python_packages.json pins (an offline build); empty downloads them")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${ANGELO_PYTHON_PACKAGES_PIN}")

# `_angelo_python_wheels(<variable>)`: the folder holding every pinned wheel, each checked against its SHA-256.
function(_angelo_python_wheels variable)
  file(READ "${ANGELO_PYTHON_PACKAGES_PIN}" _json)
  string(JSON _count LENGTH "${_json}" packages)
  if(ANGELO_PYTHON_WHEELS_DIR)
    set(_folder "${ANGELO_PYTHON_WHEELS_DIR}")
  else()
    set(_folder "${CMAKE_BINARY_DIR}/angelo_python_wheels")
  endif()
  math(EXPR _last "${_count} - 1")
  foreach(_index RANGE ${_last})
    string(JSON _url GET "${_json}" packages ${_index} url)
    string(JSON _sha256 GET "${_json}" packages ${_index} sha256)
    get_filename_component(_name "${_url}" NAME)
    set(_wheel "${_folder}/${_name}")
    if(EXISTS "${_wheel}")
      file(SHA256 "${_wheel}" _actual)
    else()
      set(_actual "")
    endif()
    if(NOT _actual STREQUAL _sha256)
      if(ANGELO_PYTHON_WHEELS_DIR)
        message(FATAL_ERROR "Angelo packages: ${_wheel} is missing or does not match its pinned SHA-256 "
                            "(build_files/cmake/angelo_python_packages.json)")
      endif()
      file(DOWNLOAD "${_url}" "${_wheel}" EXPECTED_HASH SHA256=${_sha256} TLS_VERIFY ON STATUS _status)
      list(GET _status 0 _code)
      if(NOT _code EQUAL 0)
        file(REMOVE "${_wheel}")
        message(FATAL_ERROR "Angelo packages: cannot download ${_url} (${_status}). Put the wheels "
                            "angelo_python_packages.json pins in a folder and set ANGELO_PYTHON_WHEELS_DIR to it.")
      endif()
    endif()
  endforeach()
  set(${variable} "${_folder}" PARENT_SCOPE)
endfunction()

# `angelo_install_python_packages(<python executable relative to the install prefix>)`.
function(angelo_install_python_packages python_relative)
  _angelo_python_wheels(_wheels)
  install(
    CODE "\
set(_angelo_root \"\${CMAKE_INSTALL_PREFIX}\")\n\
if(DEFINED ENV{DESTDIR})\n\
  set(_angelo_root \"\$ENV{DESTDIR}\${_angelo_root}\")\n\
endif()\n\
set(_angelo_python \"\${_angelo_root}/${python_relative}\")\n\
if(NOT EXISTS \"\${_angelo_python}\")\n\
  message(FATAL_ERROR \"Angelo packages: no bundled Python at \${_angelo_python}; the bundle would keep the libraries' certifi and pip\")\n\
else()\n\
  execute_process(\n\
    COMMAND \"\${_angelo_python}\" -I -B \"${ANGELO_PYTHON_PACKAGES_SCRIPT}\" \"${ANGELO_PYTHON_PACKAGES_PIN}\" \"${_wheels}\"\n\
    RESULT_VARIABLE _angelo_result\n\
  )\n\
  if(NOT _angelo_result EQUAL 0)\n\
    message(FATAL_ERROR \"Angelo packages: \${_angelo_python} failed (\${_angelo_result})\")\n\
  endif()\n\
endif()\n\
"
  )
endfunction()
