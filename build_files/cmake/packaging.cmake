# SPDX-FileCopyrightText: 2011-2022 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

string(TIMESTAMP CURRENT_YEAR "%Y")

# Product, vendor and contact come from the application identity
# (`scripts/modules/angelo_identity.json`, loaded by `angelo_identity.cmake`).
set(PROJECT_DESCRIPTION  "${ANGELO_PRODUCT_DESCRIPTION}")
set(PROJECT_COPYRIGHT    "${ANGELO_COPYRIGHT}")
set(PROJECT_CONTACT      "${ANGELO_CONTACT_EMAIL}")
set(PROJECT_VENDOR       "${ANGELO_VENDOR_NAME}")

# Packages carry Angelo's own version, not Blender's.
set(MAJOR_VERSION ${ANGELO_APP_VERSION_MAJOR})
set(MINOR_VERSION ${ANGELO_APP_VERSION_MINOR})
set(PATCH_VERSION ${ANGELO_APP_VERSION_PATCH})

set(CPACK_SYSTEM_NAME ${CMAKE_SYSTEM_NAME})
# The project is still called "Blender" in CMake; installers and packages are not.
set(CPACK_PACKAGE_NAME "${ANGELO_PRODUCT_NAME}")
set(CPACK_PACKAGE_DESCRIPTION ${PROJECT_DESCRIPTION})
set(CPACK_PACKAGE_VENDOR ${PROJECT_VENDOR})
set(CPACK_PACKAGE_CONTACT ${PROJECT_CONTACT})
set(CPACK_RESOURCE_FILE_LICENSE "${CMAKE_SOURCE_DIR}/COPYING")
set(CPACK_PACKAGE_INSTALL_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}")
set(CPACK_PACKAGE_VERSION_MAJOR "${MAJOR_VERSION}")
set(CPACK_PACKAGE_VERSION_MINOR "${MINOR_VERSION}")
set(CPACK_PACKAGE_VERSION_PATCH "${PATCH_VERSION}")


# Get the build revision, note that this can get out-of-sync, so for packaging run cmake first.
set(MY_WC_HASH "unknown")
if(EXISTS ${CMAKE_SOURCE_DIR}/.git/)
  find_package(Git)
  if(GIT_FOUND)
    # message(STATUS "Found Git: ${GIT_EXECUTABLE}")
    execute_process(
      COMMAND git rev-parse --short=12 HEAD
      WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
      OUTPUT_VARIABLE MY_WC_HASH
      OUTPUT_STRIP_TRAILING_WHITESPACE
      ERROR_QUIET
    )
  endif()
endif()
set(BUILD_REV ${MY_WC_HASH})
unset(MY_WC_HASH)


# Force Package Name: Angelo-<version>-<macos|windows|linux>-<arm64|x64> (.dmg, .msi, ...).
execute_process(COMMAND date "+%Y%m%d" OUTPUT_VARIABLE CPACK_DATE OUTPUT_STRIP_TRAILING_WHITESPACE)
set(PROJECT_NAME_LOWER "${ANGELO_PRODUCT_SLUG}")
if(APPLE)
  set(ANGELO_PACKAGE_OS macos)
elseif(WIN32)
  set(ANGELO_PACKAGE_OS windows)
else()
  set(ANGELO_PACKAGE_OS linux)
endif()
string(TOLOWER "${CMAKE_SYSTEM_PROCESSOR}" _angelo_processor)
if(_angelo_processor MATCHES "^(arm64|aarch64)$")
  set(PACKAGE_ARCH arm64)
elseif(_angelo_processor MATCHES "^(x86_64|amd64|x64)$")
  set(PACKAGE_ARCH x64)
else()
  set(PACKAGE_ARCH ${_angelo_processor})
endif()
unset(_angelo_processor)

if(CPACK_OVERRIDE_PACKAGENAME)
  set(CPACK_PACKAGE_FILE_NAME ${CPACK_OVERRIDE_PACKAGENAME}-${PACKAGE_ARCH})
else()
  set(CPACK_PACKAGE_FILE_NAME ${ANGELO_PRODUCT_NAME}-${ANGELO_APP_VERSION}-${ANGELO_PACKAGE_OS}-${PACKAGE_ARCH})
endif()

if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
  # RPM packages
  include(build_files/cmake/RpmBuild.cmake)
  if(RPMBUILD_FOUND)
    set(CPACK_GENERATOR "RPM")
    set(CPACK_RPM_PACKAGE_RELEASE "git${CPACK_DATE}.${BUILD_REV}")
    set(CPACK_SET_DESTDIR "true")
    set(CPACK_PACKAGE_DESCRIPTION_SUMMARY "${PROJECT_DESCRIPTION}")
    set(CPACK_PACKAGE_RELOCATABLE "false")
    set(CPACK_RPM_PACKAGE_LICENSE "GPLv2+ and Apache 2.0")
    set(CPACK_RPM_PACKAGE_GROUP "Amusements/Multimedia")
    set(CPACK_RPM_USER_BINARY_SPECFILE "${CMAKE_SOURCE_DIR}/build_files/package_spec/rpm/blender.spec.in")
  endif()
endif()

# Mac Bundle
if(APPLE)
  set(CPACK_GENERATOR "DragNDrop")
  set(CPACK_DMG_VOLUME_NAME "${ANGELO_MACOS_DMG_VOLUME_NAME}")
  # Opt-in: the window is styled through Finder (AppleScript), which needs a GUI session
  # and the user's Automation approval, so it is off for unattended and CI package runs.
  option(ANGELO_DMG_STYLED "Style the DMG window with the Angelo background (needs Finder access)" OFF)
  if(ANGELO_DMG_STYLED)
    # CPack only copies the picture into the image (`.background/background.tif`); the setup
    # script has Finder show it and put the icons where the picture expects them.
    set(CPACK_DMG_BACKGROUND_IMAGE "${CMAKE_SOURCE_DIR}/release/darwin/background.tif")
    configure_file(
      ${CMAKE_SOURCE_DIR}/release/darwin/dmg_window.applescript.in
      ${CMAKE_BINARY_DIR}/release/darwin/dmg_window.applescript
      @ONLY
    )
    set(CPACK_DMG_DS_STORE_SETUP_SCRIPT "${CMAKE_BINARY_DIR}/release/darwin/dmg_window.applescript")
  endif()

  # Libraries are bundled directly
  set(CPACK_COMPONENT_LIBRARIES_HIDDEN TRUE)
endif()

if(WIN32)
  # One install folder and one fixed upgrade code: an Angelo MSI upgrades Angelo and can
  # never replace a stock Blender install (which uses its own folder and upgrade code).
  set(CPACK_PACKAGE_INSTALL_DIRECTORY "${ANGELO_WIN_INSTALL_FOLDER}")
  set(CPACK_PACKAGE_INSTALL_REGISTRY_KEY "${ANGELO_WIN_REGISTRY_KEY}")

  set(CPACK_NSIS_MUI_ICON ${CMAKE_SOURCE_DIR}/release/windows/icons/angelo.ico)
  set(CPACK_NSIS_COMPRESSOR "/SOLID lzma")

  # Even though we no longer display this, we still need to set it otherwise it'll throw an error
  # during the msi build.
  set(CPACK_RESOURCE_FILE_LICENSE ${CMAKE_SOURCE_DIR}/release/license/spdx/GPL-3.0-or-later.txt)
  set(CPACK_WIX_PRODUCT_ICON ${CMAKE_SOURCE_DIR}/release/windows/icons/angelo.ico)

  set(CPACK_WIX_UPGRADE_GUID "${ANGELO_WIN_MSI_UPGRADE_CODE}")
  # The template chooses the install context itself (a per-user default an administrator can make per-machine,
  # WIX.template.in), so CPack must not add an InstallScope of its own (CMake 3.29+).
  set(CPACK_WIX_INSTALL_SCOPE "NONE")

  # The WiX template names the product, links and launcher: fill it from the identity.
  configure_file(
    ${CMAKE_SOURCE_DIR}/release/windows/installer_wix/WIX.template.in
    ${CMAKE_BINARY_DIR}/release/windows/installer_wix/WIX.template
    @ONLY
  )
  set(CPACK_WIX_TEMPLATE ${CMAKE_BINARY_DIR}/release/windows/installer_wix/WIX.template)
  set(CPACK_WIX_UI_BANNER ${CMAKE_SOURCE_DIR}/release/windows/installer_wix/WIX_UI_BANNER.bmp)
  set(CPACK_WIX_UI_DIALOG ${CMAKE_SOURCE_DIR}/release/windows/installer_wix/WIX_UI_DIALOG.png)
  set(CPACK_WIX_EXTRA_SOURCES ${CMAKE_SOURCE_DIR}/release/windows/installer_wix/WixUI_Angelo.wxs)
  set(CPACK_WIX_UI_REF "WixUI_Angelo")
  set(CPACK_WIX_LIGHT_EXTRA_FLAGS -dcl:medium)

  # The MSIX manifest is filled in by the packaging step (its [BRACKETED] values); the
  # product, publisher, app id, executable and protocol names come from the identity.
  configure_file(
    ${CMAKE_SOURCE_DIR}/release/windows/msix/AppxManifest.xml.template.in
    ${CMAKE_BINARY_DIR}/release/windows/msix/AppxManifest.xml.template
    @ONLY
  )

  # Start menu and desktop shortcuts start the launcher.
  set(CPACK_PACKAGE_EXECUTABLES "${ANGELO_WIN_LAUNCHER_STEM}" "${ANGELO_PRODUCT_NAME}")
  set(CPACK_CREATE_DESKTOP_LINKS "${ANGELO_WIN_LAUNCHER_STEM}" "${ANGELO_PRODUCT_NAME}")
endif()

include(CPack)

# Target for build_archive.py script, to automatically pass along
# version, revision, platform, build directory
function(add_package_archive packagename extension)
  set(build_archive python ${CMAKE_SOURCE_DIR}/build_files/package_spec/build_archive.py)
  set(package_output ${CMAKE_BINARY_DIR}/release/${packagename}.${extension})

  add_custom_target(package_archive DEPENDS ${package_output})

  add_custom_command(
    OUTPUT ${package_output}
    COMMAND ${build_archive} ${packagename} ${extension} bin release
    WORKING_DIRECTORY ${CMAKE_BINARY_DIR}
  )
endfunction()

if(APPLE)
  add_package_archive(
    "${ANGELO_PRODUCT_SLUG}-${ANGELO_APP_VERSION}-${BUILD_REV}-OSX-${CMAKE_OSX_ARCHITECTURES}"
    "zip"
  )
elseif(UNIX)
  # platform name could be tweaked, to include glibc, and ensure processor is correct (i386 vs i686)
  string(TOLOWER ${CMAKE_SYSTEM_NAME} PACKAGE_SYSTEM_NAME)

  add_package_archive(
    "${ANGELO_PRODUCT_SLUG}-${ANGELO_APP_VERSION}-${BUILD_REV}-${PACKAGE_SYSTEM_NAME}-${CMAKE_SYSTEM_PROCESSOR}"
    "tar.xz"
  )
endif()

unset(MAJOR_VERSION)
unset(MINOR_VERSION)
unset(PATCH_VERSION)

unset(BUILD_REV)
