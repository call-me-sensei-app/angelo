# SPDX-FileCopyrightText: 2022-2023 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

# Make shared libraries needed by modules available in standalone Python binary.

import sys
import os

# Path to Blender shared libraries.
shared_lib_dirname = "blender.shared" if sys.platform == "win32" else "lib"

if os.path.basename(__file__) == "bpy_site_customize.py":
    # Blender as Python Module.
    is_python = True
    shared_lib_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    # On Windows no subdirectory is used.
    if sys.platform != "win32":
        shared_lib_dir = os.path.join(shared_lib_dir, shared_lib_dirname)
else:
    exe_dir, exe_file = os.path.split(sys.executable)
    is_python = exe_file.startswith("python")
    if is_python:
        # Python executable bundled with Blender.
        shared_lib_dir = os.path.abspath(os.path.join(exe_dir, "..", "..", "..", shared_lib_dirname))
    else:
        # Blender executable.
        shared_lib_dir = os.path.abspath(os.path.join(exe_dir, shared_lib_dirname))


def _angelo_bytecode_folder():
    """Bytecode never goes inside the installed application (doc/angelo/releasing.rst, "Nothing is written
    inside the app"): a ``__pycache__`` there breaks a macOS bundle's signature.

    Angelo's own interpreter chooses its cache folder before Python starts
    (``source/blender/python/intern/bpy_interface_pycache.cc``) and names it in ``ANGELO_PYCACHE_PREFIX`` for
    its children: a ``multiprocessing`` worker or a script started with ``sys.executable`` then shares that
    folder. Any other run of this interpreter writes no bytecode. An absolute prefix or ``-B`` already chosen
    (``-X pycache_prefix``, ``PYTHONPYCACHEPREFIX``, ``PYTHONDONTWRITEBYTECODE``) is kept; a relative prefix
    is relative to the working folder, which can be inside the app, so it writes none. Everything this
    interpreter ships is precompiled (``build_files/utils/angelo_python_bytecode.py``), for a child that never
    runs this module (``-S``, another ``sitecustomize`` first on its path, pip's isolated builds).
    """
    if sys.flags.dont_write_bytecode:
        return
    if sys.pycache_prefix is not None:
        if not os.path.isabs(sys.pycache_prefix):
            sys.pycache_prefix = None
            sys.dont_write_bytecode = True
        return
    prefix = os.environ.get("ANGELO_PYCACHE_PREFIX", "")
    if os.path.isabs(prefix):
        sys.pycache_prefix = prefix
    else:
        sys.dont_write_bytecode = True


# Not for Blender as a Python module: there the interpreter is the person's own.
if os.path.basename(__file__) != "bpy_site_customize.py":
    _angelo_bytecode_folder()
del _angelo_bytecode_folder

if sys.platform == "win32":
    # Directory for extensions to find DLLs.
    if is_python:
        os.add_dll_directory(shared_lib_dir)

    # Directory for USD extension to find DLLs.
    import_paths = os.getenv("PXR_USD_WINDOWS_DLL_PATH")
    if import_paths is None:
        os.environ["PXR_USD_WINDOWS_DLL_PATH"] = shared_lib_dir

    # OIIO will by default add all paths from the path variable to add_dll_directory
    # problem there is that those folders will be searched before ours and versions of
    # some DLL files may be found that are not blenders and may not even be the right version
    # causing compatibility issues.
    os.environ["OIIO_LOAD_DLLS_FROM_PATH"] = "0"

# MaterialX libraries, append if already specified.
materialx_libs_dir = os.path.abspath(os.path.join(shared_lib_dir, "materialx", "libraries"))
materialx_libs_env = os.getenv("MATERIALX_SEARCH_PATH")
if materialx_libs_env is None:
    os.environ["MATERIALX_SEARCH_PATH"] = materialx_libs_dir
else:
    os.environ["MATERIALX_SEARCH_PATH"] = materialx_libs_env + os.pathsep + materialx_libs_dir

materialx_libs_env = os.getenv("PXR_MTLX_STDLIB_SEARCH_PATHS")
if materialx_libs_env is None:
    os.environ["PXR_MTLX_STDLIB_SEARCH_PATHS"] = materialx_libs_dir
else:
    os.environ["PXR_MTLX_STDLIB_SEARCH_PATHS"] = materialx_libs_env + os.pathsep + materialx_libs_dir


def register():
    # To make this work as a startup script for Blender as a Python module.
    pass
