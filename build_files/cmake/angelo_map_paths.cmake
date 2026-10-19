# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
#
# SPDX-License-Identifier: GPL-2.0-or-later

# cmake -DANGELO_FILE=<text file> -DANGELO_MAP="<from>=<to>::..." -P angelo_map_paths.cmake
#
# Rewrites every mapped prefix in a generated text file (for example the source and include paths oslc
# writes into OSL bytecode), longest prefix first so a folder inside another maps to its own name.
# The map is ANGELO_BUILD_PATH_MAP from angelo_build_paths.cmake. The file is written only if it changes.

if(NOT DEFINED ANGELO_FILE OR NOT DEFINED ANGELO_MAP)
  message(FATAL_ERROR "angelo_map_paths: ANGELO_FILE and ANGELO_MAP are required")
endif()
file(READ "${ANGELO_FILE}" _text)
set(_original "${_text}")
string(REPLACE "::" ";" _pairs "${ANGELO_MAP}")

# Longest <from> first.
set(_ordered)
while(_pairs)
  set(_best "")
  set(_best_length -1)
  foreach(_pair IN LISTS _pairs)
    string(FIND "${_pair}" "=" _split REVERSE)
    if(_split GREATER _best_length)
      set(_best "${_pair}")
      set(_best_length ${_split})
    endif()
  endforeach()
  list(APPEND _ordered "${_best}")
  list(REMOVE_ITEM _pairs "${_best}")
endwhile()

foreach(_pair IN LISTS _ordered)
  string(FIND "${_pair}" "=" _split REVERSE)
  string(SUBSTRING "${_pair}" 0 ${_split} _from)
  math(EXPR _after "${_split} + 1")
  string(SUBSTRING "${_pair}" ${_after} -1 _to)
  if(NOT "${_from}" STREQUAL "")
    string(REPLACE "${_from}" "${_to}" _text "${_text}")
  endif()
endforeach()

if(NOT "${_text}" STREQUAL "${_original}")
  file(WRITE "${ANGELO_FILE}" "${_text}")
endif()
