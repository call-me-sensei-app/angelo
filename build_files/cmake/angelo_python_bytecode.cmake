# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
#
# SPDX-License-Identifier: GPL-2.0-or-later

# Nothing is written inside the installed app (doc/angelo/releasing.rst, "Nothing is written inside
# the app"). Angelo's own interpreter keeps its bytecode in a per-user cache folder
# (`source/blender/python/intern/bpy_interface_pycache.cc`), and the bundled `sitecustomize.py` sends
# a child interpreter's there too, or writes none. A child that bypasses `sitecustomize` (pip's
# isolated build, a `sitecustomize` on PYTHONPATH, a venv, `-S`, `-W`) would still write next to the
# sources, so this install step precompiles the whole bundled library and Blender's scripts with the
# installed interpreter (`build_files/utils/angelo_python_bytecode.py`): checked hash-based .pyc that
# such a child reads and never rewrites. Unchanged modules are skipped on an incremental install.
#
# Include after `angelo_identity.cmake`; `source/creator/CMakeLists.txt` calls
# `angelo_install_python_bytecode()` after the bundled Python, its packages and the scripts are installed.

set(ANGELO_PYTHON_BYTECODE_SCRIPT "${CMAKE_SOURCE_DIR}/build_files/utils/angelo_python_bytecode.py")

# `angelo_install_python_bytecode(<python executable relative to the install prefix>)`.
function(angelo_install_python_bytecode python_relative)
  install(
    CODE "\
set(_angelo_root \"\${CMAKE_INSTALL_PREFIX}\")\n\
if(DEFINED ENV{DESTDIR})\n\
  set(_angelo_root \"\$ENV{DESTDIR}\${_angelo_root}\")\n\
endif()\n\
set(_angelo_python \"\${_angelo_root}/${python_relative}\")\n\
if(NOT EXISTS \"\${_angelo_python}\")\n\
  message(STATUS \"Angelo bytecode: no bundled Python at \${_angelo_python}, skipped\")\n\
else()\n\
  execute_process(\n\
    COMMAND \"\${_angelo_python}\" -I -B \"${ANGELO_PYTHON_BYTECODE_SCRIPT}\"\n\
    RESULT_VARIABLE _angelo_result\n\
  )\n\
  if(NOT _angelo_result EQUAL 0)\n\
    message(FATAL_ERROR \"Angelo bytecode: \${_angelo_python} failed (\${_angelo_result})\")\n\
  endif()\n\
endif()\n\
"
  )
endfunction()
