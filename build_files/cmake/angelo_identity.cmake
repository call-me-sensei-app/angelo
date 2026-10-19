# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
#
# SPDX-License-Identifier: GPL-2.0-or-later

# Angelo's application identity for the build.
#
# Reads `scripts/modules/angelo_identity.json` (the single source, also read by
# `scripts/modules/angelo_identity.py`; see `doc/angelo/app_identity.rst`) and
# exposes it as:
#
# - `ANGELO_*` CMake variables for packaging, bundle metadata and templates.
# - Global compile definitions for the few C/C++ sources and resource scripts
#   that need a product string (folder names, executable names, AUMID, ...).
#
# Include after `get_blender_version()`. Changing the JSON re-runs CMake.

if(NOT DEFINED BLENDER_VERSION OR NOT DEFINED BLENDER_VERSION_MAJOR)
  message(FATAL_ERROR "angelo_identity.cmake must be included after get_blender_version()")
endif()

set(ANGELO_IDENTITY_JSON_FILE "${CMAKE_SOURCE_DIR}/scripts/modules/angelo_identity.json")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${ANGELO_IDENTITY_JSON_FILE}")
file(READ "${ANGELO_IDENTITY_JSON_FILE}" _angelo_json)

# `_angelo_get(<variable> <member>...)`: a JSON member as a CMake string.
# A missing member is a configure-time error, never an empty identity value.
macro(_angelo_get variable)
  string(JSON ${variable} ERROR_VARIABLE _angelo_error GET "${_angelo_json}" ${ARGN})
  if(_angelo_error)
    message(FATAL_ERROR "angelo_identity.json: cannot read '${ARGN}': ${_angelo_error}")
  endif()
  # Values travel through compile definitions and configured templates: a ';' would split
  # a definition into two arguments, and quotes or backslashes would need escaping.
  if("${${variable}}" MATCHES "[;\"\\\\]")
    message(FATAL_ERROR "angelo_identity.json: '${ARGN}' must not contain ';', '\"' or '\\': '${${variable}}'")
  endif()
endmacro()

string(TIMESTAMP ANGELO_YEAR "%Y")

# Product and vendor.
_angelo_get(ANGELO_PRODUCT_NAME product name)
_angelo_get(ANGELO_PRODUCT_SLUG product slug)
_angelo_get(ANGELO_PRODUCT_TAGLINE product tagline)
_angelo_get(ANGELO_PRODUCT_DESCRIPTION product description)
_angelo_get(ANGELO_BASED_ON_NAME product based_on)
_angelo_get(ANGELO_COPYRIGHT product copyright)
string(REPLACE "{year}" "${ANGELO_YEAR}" ANGELO_COPYRIGHT "${ANGELO_COPYRIGHT}")
_angelo_get(ANGELO_VENDOR_NAME vendor name)
_angelo_get(ANGELO_CONTACT_EMAIL vendor contact_email)

# Angelo's own product version (`major.minor.patch`), independent of the Blender version it is
# based on. Blender's version plumbing (`BLENDER_VERSION`: file compatibility, add-on versions,
# the `5.2` config subfolders) is untouched; the Blender base is only reported next to ours.
_angelo_get(ANGELO_APP_VERSION version label)
if(NOT ANGELO_APP_VERSION MATCHES "^([0-9]+)\\.([0-9]+)\\.([0-9]+)$")
  message(FATAL_ERROR "angelo_identity.json: version.label must look like 1.0.0, got '${ANGELO_APP_VERSION}'")
endif()
set(ANGELO_APP_VERSION_MAJOR ${CMAKE_MATCH_1})
set(ANGELO_APP_VERSION_MINOR ${CMAKE_MATCH_2})
set(ANGELO_APP_VERSION_PATCH ${CMAKE_MATCH_3})
# What people read: "1.0" for 1.0.0, "1.0.1" for a patch release.
if(ANGELO_APP_VERSION_PATCH EQUAL 0)
  set(ANGELO_APP_VERSION_DISPLAY "${ANGELO_APP_VERSION_MAJOR}.${ANGELO_APP_VERSION_MINOR}")
else()
  set(ANGELO_APP_VERSION_DISPLAY "${ANGELO_APP_VERSION}")
endif()
# The Blender release this build is based on (for example 5.2.2).
set(ANGELO_BLENDER_BASE_VERSION "${BLENDER_VERSION}.${BLENDER_VERSION_PATCH}")

# Per-user and per-session names shared by every platform.
_angelo_get(ANGELO_USER_DIR_NAME paths user_dir_name)
_angelo_get(ANGELO_USER_DIR_NAME_UNIX paths user_dir_name_unix)
_angelo_get(ANGELO_CRASH_FILE_NAME paths crash_file)
_angelo_get(ANGELO_TEMP_PREFIX paths temp_prefix)

# macOS bundle.
_angelo_get(ANGELO_MACOS_BUNDLE_ID bundle macos_id)
_angelo_get(ANGELO_MACOS_MIN_VERSION bundle macos_min_version)
if(NOT ANGELO_MACOS_MIN_VERSION MATCHES "^[0-9]+\\.[0-9]+$")
  message(FATAL_ERROR "angelo_identity.json: bundle.macos_min_version must look like 26.0")
endif()
_angelo_get(ANGELO_MACOS_EXECUTABLE bundle macos_executable)
_angelo_get(ANGELO_MACOS_THUMBNAILER_SUFFIX bundle macos_thumbnailer_suffix)
_angelo_get(ANGELO_MACOS_IMPORTED_DOCUMENT_UTI bundle macos_imported_document_uti)
_angelo_get(ANGELO_MACOS_DMG_VOLUME_NAME bundle macos_dmg_volume_name)
set(ANGELO_MACOS_THUMBNAILER_ID "${ANGELO_MACOS_BUNDLE_ID}.${ANGELO_MACOS_THUMBNAILER_SUFFIX}")
_angelo_get(ANGELO_LINUX_DESKTOP_ID bundle linux_desktop_id)

# Windows.
_angelo_get(ANGELO_WIN_EXE_STEM windows exe_stem)
_angelo_get(ANGELO_WIN_LAUNCHER_SUFFIX windows launcher_suffix)
_angelo_get(ANGELO_WIN_INSTALL_FOLDER windows install_folder)
_angelo_get(ANGELO_WIN_REGISTRY_KEY windows registry_key)
_angelo_get(ANGELO_WIN_AUMID windows app_user_model_id)
_angelo_get(ANGELO_WIN_MSI_UPGRADE_CODE windows msi_upgrade_code)
_angelo_get(ANGELO_WIN_THUMBNAIL_CLSID windows thumbnail_clsid)
_angelo_get(ANGELO_WIN_THUMBNAIL_HANDLER_NAME windows thumbnail_handler_name)
_angelo_get(ANGELO_WIN_MSIX_IDENTITY_NAME windows msix_identity_name)
_angelo_get(ANGELO_WIN_MSIX_APPLICATION_ID windows msix_application_id)
_angelo_get(ANGELO_WIN_URL_SCHEME windows url_scheme)
_angelo_get(ANGELO_WIN_INSTALL_COMPONENT windows install_component)
_angelo_get(_angelo_claim_default windows claim_blend_default)
if(_angelo_claim_default)
  set(ANGELO_WIN_CLAIM_BLEND_DEFAULT 1)
else()
  set(ANGELO_WIN_CLAIM_BLEND_DEFAULT 0)
endif()

set(ANGELO_WIN_EXE_NAME "${ANGELO_WIN_EXE_STEM}.exe")
set(ANGELO_WIN_LAUNCHER_STEM "${ANGELO_WIN_EXE_STEM}${ANGELO_WIN_LAUNCHER_SUFFIX}")
set(ANGELO_WIN_LAUNCHER_NAME "${ANGELO_WIN_LAUNCHER_STEM}.exe")

foreach(_guid ANGELO_WIN_MSI_UPGRADE_CODE ANGELO_WIN_THUMBNAIL_CLSID)
  # 8-4-4-4-12 hexadecimal digits (CMake regular expressions have no `{n}` counts).
  string(LENGTH "${${_guid}}" _guid_length)
  string(SUBSTRING "${${_guid}}" 8 1 _dash_1)
  string(SUBSTRING "${${_guid}}" 13 1 _dash_2)
  string(SUBSTRING "${${_guid}}" 18 1 _dash_3)
  string(SUBSTRING "${${_guid}}" 23 1 _dash_4)
  if(NOT _guid_length EQUAL 36 OR NOT "${${_guid}}" MATCHES "^[0-9A-Fa-f-]+$"
     OR NOT "${_dash_1}${_dash_2}${_dash_3}${_dash_4}" STREQUAL "----")
    message(FATAL_ERROR "angelo_identity.json: ${_guid} is not a GUID: '${${_guid}}'")
  endif()
  string(TOUPPER "${${_guid}}" ${_guid})
endforeach()

# CPack's WiX generator names a packaged file `CM_FP_<component>.<path>` with every
# character other than letters, digits, `_` and `.` replaced by `_`. The WiX
# template refers to the launcher by that id (to register/unregister the file type).
string(REGEX REPLACE "[^A-Za-z0-9_.]" "_" _angelo_launcher_wix_name "${ANGELO_WIN_LAUNCHER_NAME}")
set(ANGELO_WIN_LAUNCHER_WIX_FILE_ID "CM_FP_${ANGELO_WIN_INSTALL_COMPONENT}.${_angelo_launcher_wix_name}")
# The installer's (un)registration steps run the main executable, not the launcher: the launcher returns
# at once (it only waits for -b), so Windows Installer would remove the files under the still-starting
# program and the unregistration would never finish.
string(REGEX REPLACE "[^A-Za-z0-9_.]" "_" _angelo_exe_wix_name "${ANGELO_WIN_EXE_NAME}")
set(ANGELO_WIN_EXE_WIX_FILE_ID "CM_FP_${ANGELO_WIN_INSTALL_COMPONENT}.${_angelo_exe_wix_name}")

# Every link as `ANGELO_URL_<KEY>` (keys starting with `_` are notes). Python reads the
# same table; CMake needs them for installer pages, package metadata and the crash dialog.
string(JSON _angelo_url_count LENGTH "${_angelo_json}" urls)
math(EXPR _angelo_url_last "${_angelo_url_count} - 1")
foreach(_i RANGE 0 ${_angelo_url_last})
  string(JSON _angelo_url_key MEMBER "${_angelo_json}" urls ${_i})
  if(NOT _angelo_url_key MATCHES "^_")
    _angelo_get(_angelo_url_value urls ${_angelo_url_key})
    string(TOUPPER "${_angelo_url_key}" _angelo_url_name)
    set(ANGELO_URL_${_angelo_url_name} "${_angelo_url_value}")
  endif()
endforeach()

# Windows app id, used for file registration and the taskbar group. Several
# modules need it, so there is no tidy way to share it other than a global define.
set(ANGELO_WIN_APPID "${ANGELO_WIN_AUMID}")
set(ANGELO_WIN_APPID_FRIENDLY_NAME "${ANGELO_PRODUCT_NAME}")

# Global definitions: only the stable names (product, base, folders, crash file, temp prefix). Every
# compile command carries them, so a value that changes with a pending decision (bundle id, links,
# vendor, version) must NOT be added here: it would recompile all of Blender. Those are scoped
# to the directories that use them (ANGELO_VERSION_DEFINITIONS, ANGELO_THUMBNAILER_DEFINITIONS) or
# are Windows-only; `test_native_app_identity.py` pins this.
add_definitions(
  -DANGELO_PRODUCT_NAME="${ANGELO_PRODUCT_NAME}"
  -DANGELO_BASED_ON_NAME="${ANGELO_BASED_ON_NAME}"
  -DANGELO_USER_DIR_NAME="${ANGELO_USER_DIR_NAME}"
  -DANGELO_USER_DIR_NAME_UNIX="${ANGELO_USER_DIR_NAME_UNIX}"
  -DANGELO_CRASH_FILE_NAME="${ANGELO_CRASH_FILE_NAME}"
  -DANGELO_TEMP_PREFIX="${ANGELO_TEMP_PREFIX}"
)

# Added by the few directories that display the version (windowmanager, space_info), not here:
# a change of global definitions would recompile everything.
set(ANGELO_VERSION_DEFINITIONS
  -DANGELO_APP_VERSION_DISPLAY="${ANGELO_APP_VERSION_DISPLAY}"
)

if(WIN32)
  add_definitions(
    # The app id is also the file-type ProgID and the taskbar identity.
    -DBLENDER_WIN_APPID="${ANGELO_WIN_APPID}"
    # The name shown in the taskbar and the "Open with" UI.
    -DBLENDER_WIN_APPID_FRIENDLY_NAME="${ANGELO_WIN_APPID_FRIENDLY_NAME}"
    -DANGELO_WIN_EXE_NAME="${ANGELO_WIN_EXE_NAME}"
    -DANGELO_WIN_LAUNCHER_NAME="${ANGELO_WIN_LAUNCHER_NAME}"
    -DANGELO_WIN_THUMBNAIL_CLSID_STR="{${ANGELO_WIN_THUMBNAIL_CLSID}}"
    -DANGELO_WIN_THUMBNAIL_HANDLER_NAME="${ANGELO_WIN_THUMBNAIL_HANDLER_NAME}"
    -DANGELO_WIN_CLAIM_BLEND_DEFAULT=${ANGELO_WIN_CLAIM_BLEND_DEFAULT}
    # Version resource of the executables.
    -DANGELO_VENDOR_NAME="${ANGELO_VENDOR_NAME}"
    -DANGELO_COPYRIGHT="${ANGELO_COPYRIGHT}"
    -DANGELO_URL_REPORT_BUG="${ANGELO_URL_REPORT_BUG}"
    -DANGELO_RC_COMMENTS="Based on ${ANGELO_BASED_ON_NAME} ${ANGELO_BLENDER_BASE_VERSION}"
  )
elseif(APPLE)
  # Added by source/blender/blendthumb only (it follows the bundle id, which is still pending).
  set(ANGELO_THUMBNAILER_DEFINITIONS
    -DANGELO_MACOS_THUMBNAILER_ID="${ANGELO_MACOS_THUMBNAILER_ID}"
  )
endif()

unset(_angelo_json)
unset(_angelo_url_count)
unset(_angelo_url_last)
unset(_angelo_url_key)
unset(_angelo_url_value)
unset(_angelo_url_name)
unset(_angelo_error)
unset(_angelo_claim_default)
unset(_angelo_launcher_wix_name)
unset(_angelo_exe_wix_name)
