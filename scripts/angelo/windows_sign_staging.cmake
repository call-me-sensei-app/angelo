# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
#
# SPDX-License-Identifier: GPL-2.0-or-later

# Signs the files CPack is about to pack: a CPack pre-build script (CPACK_PRE_BUILD_SCRIPTS, CMake 3.19+).
#
# CPack installs the build into its own staging folder and packs that, copying third-party DLLs and the
# Python modules from lib\windows_x64 again, so signing the build folder would not reach the MSI.
# build_release_windows.ps1 -Sign passes this file to cpack with -D CPACK_PRE_BUILD_SCRIPTS=... and the
# signing inputs in the environment (ANGELO_SIGN_* as sign_windows.ps1 reads them). Any failure, or a
# staging folder without the main executable, stops cpack before WiX packs an unsigned file.
#
#   ANGELO_SIGN_SCRIPT     sign_windows.ps1
#   ANGELO_SIGN_EXPECT     a file name the staging folder must contain (the main executable)
#   ANGELO_SIGN_REPORT     where sign_windows.ps1 writes its JSON report (optional)
#   ANGELO_POWERSHELL      the PowerShell to run it with (default: powershell, Windows PowerShell 5.1)

if(NOT DEFINED ENV{ANGELO_SIGN_SCRIPT} OR NOT EXISTS "$ENV{ANGELO_SIGN_SCRIPT}")
  message(FATAL_ERROR "windows_sign_staging.cmake: ANGELO_SIGN_SCRIPT must name sign_windows.ps1")
endif()
if(NOT DEFINED ENV{ANGELO_SIGN_EXPECT} OR "$ENV{ANGELO_SIGN_EXPECT}" STREQUAL "")
  message(FATAL_ERROR "windows_sign_staging.cmake: ANGELO_SIGN_EXPECT must name the main executable")
endif()

# The folder CPack installed into; the generators name it in one of these.
set(_angelo_stage "")
foreach(_angelo_candidate "${CPACK_TEMPORARY_DIRECTORY}" "${CPACK_TEMPORARY_INSTALL_DIRECTORY}")
  if(_angelo_stage STREQUAL "" AND NOT _angelo_candidate STREQUAL "" AND IS_DIRECTORY "${_angelo_candidate}")
    set(_angelo_stage "${_angelo_candidate}")
  endif()
endforeach()
if(_angelo_stage STREQUAL "")
  message(FATAL_ERROR "windows_sign_staging.cmake: CPack named no staging folder to sign")
endif()
file(GLOB_RECURSE _angelo_expected "${_angelo_stage}/*/$ENV{ANGELO_SIGN_EXPECT}" "${_angelo_stage}/$ENV{ANGELO_SIGN_EXPECT}")
if(NOT _angelo_expected)
  message(FATAL_ERROR "windows_sign_staging.cmake: ${_angelo_stage} has no $ENV{ANGELO_SIGN_EXPECT}; nothing was signed")
endif()

set(_angelo_powershell "powershell")
if(DEFINED ENV{ANGELO_POWERSHELL} AND NOT "$ENV{ANGELO_POWERSHELL}" STREQUAL "")
  set(_angelo_powershell "$ENV{ANGELO_POWERSHELL}")
endif()
set(_angelo_report "")
if(DEFINED ENV{ANGELO_SIGN_REPORT} AND NOT "$ENV{ANGELO_SIGN_REPORT}" STREQUAL "")
  set(_angelo_report -Report "$ENV{ANGELO_SIGN_REPORT}")
endif()

message(STATUS "Signing the staged files in ${_angelo_stage}")
execute_process(
  COMMAND "${_angelo_powershell}" -NoProfile -NonInteractive -ExecutionPolicy Bypass
          -File "$ENV{ANGELO_SIGN_SCRIPT}" -Mode Sign -Path "${_angelo_stage}" ${_angelo_report}
  RESULT_VARIABLE _angelo_code
)
if(NOT _angelo_code EQUAL 0)
  message(FATAL_ERROR "windows_sign_staging.cmake: signing the staged files failed (${_angelo_code})")
endif()
