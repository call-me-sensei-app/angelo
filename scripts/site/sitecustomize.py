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


def _angelo_bytecode_folder(version_folder):
    """Bytecode never goes inside the installed application (doc/angelo/releasing.rst, "Nothing is written
    inside the app"): a ``__pycache__`` there breaks a macOS bundle's signature.

    Angelo's own interpreter chooses its cache folder before Python starts
    (``source/blender/python/intern/bpy_interface_pycache.cc``, which refuses one inside the app) and names it
    in ``ANGELO_PYCACHE_PREFIX``. That folder wins over any other prefix (``-X pycache_prefix``, a person's
    ``PYTHONPYCACHEPREFIX``). Without one, a run of this interpreter keeps a person's own absolute prefix, and
    otherwise writes no bytecode. A folder is refused (no bytecode) when it is relative (to a working folder
    that can be inside the app), would put bytecode inside the app (inside it, or ``/`` or another ancestor
    whose mirror of the app's sources lands inside it), or cannot be written.

    The decision is also exported for every child process before any can start: ``PYTHONPYCACHEPREFIX`` set
    to the absolute folder, or ``PYTHONDONTWRITEBYTECODE=1`` with no prefix. A child reads it while its own
    interpreter initializes, before it imports anything, so the modules it imports before ``site`` (the
    codec of a non-UTF-8 locale or ``PYTHONIOENCODING``, ``warnings`` for ``-W`` or ``PYTHONWARNINGS``) and a
    child that never runs this module (pip's isolated build, ``-S``, another ``sitecustomize`` first) write
    there under ``-O``/``-OO`` too. A child that cannot see it (a replaced environment, ``-E``/``-I``) reads the
    startup modules' shipped ``opt-N`` bytecode (``angelo_python_bytecode.precompile_optimized``).
    """
    def chosen():
        if sys.flags.dont_write_bytecode or os.environ.get("PYTHONDONTWRITEBYTECODE"):
            return None
        prefix = os.environ.get("ANGELO_PYCACHE_PREFIX") or sys.pycache_prefix
        if not prefix or not os.path.isabs(prefix) or inside_app(prefix) or not writable(prefix):
            return None
        return prefix

    def spellings(path):
        """``path`` as written (absolute) and resolved, compared as the file system compares names."""
        for spelled in (os.path.abspath(path), os.path.realpath(path)):
            spelled = os.path.normcase(spelled)
            yield os.path.join(spelled.lower() if sys.platform == "darwin" else spelled, "")

    def inside_app(prefix):
        # The install folder; on macOS the whole bundle around <name>.app/Contents/Resources/<version>. Both
        # spellings: Python mirrors the path as written (a SUBST or mapped drive, a link such as /var), not the
        # resolved one.
        apps = set()
        for version in spellings(version_folder):
            folder = os.path.dirname(os.path.dirname(version))
            bundle = os.path.dirname(os.path.dirname(folder))
            apps.add(os.path.join(bundle if bundle.endswith(".app") else folder, ""))

        def inside(path):
            return any(spelled.startswith(app) for spelled in spellings(path) for app in apps)

        # Python mirrors a source's absolute path, without its drive, below the prefix (cache_from_source).
        mirrors = [os.path.join(prefix, (app[2:] if app[1:2] == ":" else app).lstrip("\\/")) for app in apps]
        return inside(prefix) or any(inside(mirror) for mirror in mirrors)

    def writable(prefix):
        folder = prefix
        while not os.path.isdir(folder):    # Python creates the missing folders as it writes.
            parent = os.path.dirname(folder)
            if parent == folder or os.path.lexists(folder):
                return False
            folder = parent
        return os.access(folder, os.W_OK | os.X_OK)

    prefix = chosen()
    if prefix is None:
        sys.pycache_prefix = None
        sys.dont_write_bytecode = True
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        os.environ.pop("PYTHONPYCACHEPREFIX", None)
        os.environ.pop("ANGELO_PYCACHE_PREFIX", None)
    else:
        sys.pycache_prefix = prefix
        os.environ["PYTHONPYCACHEPREFIX"] = prefix


def _angelo_shipped_bytecode(version_folder):
    """Modules the app ships load from the bytecode it ships with them (doc/angelo/releasing.rst, "Nothing is
    written inside the app").

    The install step precompiles every module of the bundled library and Blender's scripts as checked
    hash-based ``.pyc`` next to its source (``build_files/utils/angelo_python_bytecode.py``). A bytecode folder
    (``sys.pycache_prefix``) makes Python look only there, so without this hook those files were never read:
    each launch with an empty or unwritable folder (a new home or a sandbox, ``PYTHONDONTWRITEBYTECODE``, the
    first launch of every build) compiled every module it imported again, about 1.1 s more CPU than stock
    Blender for ``-b --factory-startup``. For a source inside ``version_folder`` the loader (Python's own
    ``SourceFileLoader``, with a ``get_code`` of its own) reads the shipped file when its hash matches the
    source; anything else (an edited source, a person's add-on) goes to Python's own ``get_code`` and its
    bytecode folder, so nothing is ever written next to a shipped source.
    """
    import _imp
    import importlib.machinery as machinery
    import importlib.util
    import marshal
    import types

    cache_tag = sys.implementation.cache_tag
    # Under -O or -OO Python reads <name>.<tag>.opt-N.pyc; the shipped <name>.<tag>.pyc would keep the asserts and
    # docstrings it strips, so this loader serves only an unoptimized interpreter (Angelo's own is one).
    if cache_tag is None or sys.flags.optimize:
        return
    root = os.path.normcase(os.path.join(os.path.realpath(version_folder), ""))
    magic = importlib.util.MAGIC_NUMBER
    source_hash = importlib.util.source_hash

    python_get_code = machinery.SourceFileLoader.get_code

    def get_code(self, fullname):
        """``get_code`` of one loader for a source inside the app: the shipped file when its hash matches."""
        source_path = self.get_filename(fullname)
        folder, name = os.path.split(source_path)
        if name.endswith(".py"):
            try:
                data = self.get_data(os.path.join(folder, "__pycache__", "%s.%s.pyc" % (name[:-3], cache_tag)))
            except OSError:
                data = b""
            # PEP 552 flags 0b11: hash-based and checked, the only kind the install step writes.
            if data[:4] == magic and data[4:8] == b"\x03\x00\x00\x00":
                if data[8:16] == source_hash(self.get_data(source_path)):
                    try:
                        code = marshal.loads(memoryview(data)[16:])
                    except (EOFError, ValueError, TypeError):
                        code = None
                    if isinstance(code, types.CodeType):
                        # The build records paths relative to the app; tracebacks and linecache need the
                        # source's real path, as Python's own loader gives every .pyc it reads.
                        _imp._fix_co_filename(code, source_path)
                        return code
        return python_get_code(self, fullname)

    def source_loader(fullname, path):
        """The loader for a source inside the app: exactly Python's ``SourceFileLoader``, never a subclass.

        Code that dispatches on a loader's exact type must see Python's own (distlib's resource finders: pip
        died on Windows with "Unable to locate finder for 'pip._vendor.distlib'" when it was a subclass; also
        ``pkg_resources``, ``importlib.resources``, ``pkgutil``, ``inspect``). ``FileFinder`` only calls its
        loader with the module's name and path, so this factory hands it a plain instance whose own
        ``get_code`` reads the shipped file first. A pickled loader (a pickled module spec) comes back as a plain
        one: the bound method pickles by name, so it unpickles as the class's own ``get_code``.
        """
        loader = machinery.SourceFileLoader(fullname, path)
        loader.get_code = types.MethodType(get_code, loader)
        return loader

    shipped_finder = machinery.FileFinder.path_hook(
        (machinery.ExtensionFileLoader, machinery.EXTENSION_SUFFIXES),
        (source_loader, machinery.SOURCE_SUFFIXES),
        (machinery.SourcelessFileLoader, machinery.BYTECODE_SUFFIXES),
    )

    def inside(path):
        return os.path.normcase(os.path.join(os.path.realpath(path or os.getcwd()), "")).startswith(root)

    def shipped_path_hook(path):
        if not inside(path):
            raise ImportError("not a folder the app ships", path=path)
        return shipped_finder(path)

    sys.path_hooks.insert(0, shipped_path_hook)
    for path in [path for path in sys.path_importer_cache if isinstance(path, str) and inside(path)]:
        del sys.path_importer_cache[path]


# Not for Blender as a Python module: there the interpreter is the person's own.
if os.path.basename(__file__) != "bpy_site_customize.py":
    _angelo_bytecode_folder(os.path.dirname(sys.prefix))
    _angelo_shipped_bytecode(os.path.dirname(sys.prefix))
del _angelo_bytecode_folder, _angelo_shipped_bytecode


def _angelo_tls_distrust(version_folder):
    """Every TLS connection of this interpreter refuses compromised CA keys (``angelo_agent/tls_distrust.py``):
    Blender's own process from its first ``import ssl`` (``-b`` runs, add-ons registered before Angelo's), and
    every child that runs the bundled Python (extension sync and downloads, ``multiprocessing`` workers).

    ``ssl`` costs about 20 ms to import, so nothing is imported here: a one-shot import hook installs the check
    right after ``ssl`` first loads. A missing or failing module only prints a line; startup goes on.
    """
    module_path = os.path.join(version_folder, "scripts", "addons_core", "angelo_agent", "tls_distrust.py")
    if not os.path.isfile(module_path):
        return

    def install():
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("_angelo_tls_distrust", module_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            module.install()
        except Exception as error:
            sys.stderr.write("Angelo: the TLS distrust check is not installed: %s: %s\n" % (type(error).__name__, error))

    if "ssl" in sys.modules:
        install()
        return

    class AfterSSL:
        @staticmethod
        def find_spec(name, path=None, target=None):
            if name != "ssl":
                return None
            sys.meta_path.remove(AfterSSL)
            import importlib.machinery
            spec = importlib.machinery.PathFinder.find_spec(name, path)
            if spec is None or spec.loader is None:
                return spec
            exec_module = spec.loader.exec_module

            def exec_and_install(module):
                exec_module(module)
                install()
            spec.loader.exec_module = exec_and_install
            return spec

    sys.meta_path.insert(0, AfterSSL)


if os.path.basename(__file__) != "bpy_site_customize.py":
    _angelo_tls_distrust(os.path.dirname(sys.prefix))
del _angelo_tls_distrust

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
