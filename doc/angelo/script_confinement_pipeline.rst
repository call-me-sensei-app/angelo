Pipeline libraries under the script confinement
================================================

Agent scripts use USD, OpenImageIO, OpenEXR, OpenVDB, MaterialX, OpenColorIO, OpenTimelineIO, OSL queries
(``oslquery``), Audaspace (``aud``) and Mantaflow (``manta``) on their own files. Every file one of these libraries
opens by a path is judged like Python's ``open`` (:doc:`script_confinement`): a private folder is refused for reading
and writing, a startup or tool-output folder for writing. The maintainer's rule: never trade usability, so these
modules are gated per entry point instead of refused on import.

Only modules with no entry point to judge stay refused on import while confined (``NATIVE_FILE_MODULES`` in
``bpy_angelo_guard_confine_native.hh``): ``_dbm``, ``_gdbm``, ``readline``, Tk, curses, the ``_interpreters``
family and ``_lsprof``.

USD: the confined resolver
--------------------------

USD reads layers that composition reaches (sublayers, references, payloads, a ``.mtlx`` referenced from a stage),
often on its worker threads, and Blender's own USD import and export share the same library as the ``pxr`` modules.
So the gate is USD's primary asset resolver (``source/blender/io/usd/usd_confined_resolver.hh``), the default
resolver with four entry points judged while any confined script runs:

* ``Resolve`` and ``ResolveForNewAsset``: a refused path does not resolve. Readers that open a resolved path
  themselves (usdMtlx reads a ``.mtlx`` on disk with MaterialX; the usdz writer maps a dependency's file) never see
  it, and the resolver's error carries the confinement's reason.
* ``OpenAsset`` and ``OpenAssetForWrite``: a resolved path a script builds directly (``Ar.ResolvedPath``) is judged
  too.
* Resolving a ``.mtlx`` judges every file its XIncludes reach, since MaterialX follows them outside Ar.

The resolver is declared by a resource plugin (``io/usd/plugin/blenderConfinedResolver``, installed beside USD's
plugins) and defined with its factory in Blender's code. The confinement makes USD's resolver at install, before
any script, naming the confined one as USD's preferred resolver (another primary resolver plugin in the environment
would otherwise win when its type name sorts first), so no environment a script sets later
(``PXR_AR_DISABLE_PLUGIN_RESOLVER``) decides which resolver USD uses.

If the resolver is still not the confined one (its plugin missing from an install, ``PXR_AR_DISABLE_PLUGIN_RESOLVER``
in the person's environment), nothing USD does is judged, so no USD is a confined script's (fail closed): its modules
can't be made while confined; every ``pxr`` binding (one Boost.Python function type: functions, methods,
constructors, property accessors) refuses a confined frame, whichever module or object reached it; and Blender's own
USD entry points refuse while a confined region runs (``confined_resolver_refusal``: the USD import and export
operators, a USD cache file, which then opens nothing, and Hydra's engines).

USD worker threads hold no GIL and no confinement of their own, so while any confined region runs every asset USD
touches is judged, Angelo's own concurrent USD work included (it never touches private folders).

``pxr`` entry points gated in Python as well: the openers that name a file (``Usd.Stage.Open``/``CreateNew``/
``Export``, ``Sdf.Layer.FindOrOpen``/``CreateNew``/``Export``/``Import``, the zip files), so the refusal is the
confinement's own exception; and those that bypass Ar: ``Sdf.Layer._WriteDataFile``, ``Sdf.ZipFileWriter``
(``CreateNew``, ``AddFile``), ``UsdUtils.CreateNewUsdzPackage``, reports and traces written to a path,
``Tf.TouchFile``. ``Plug.Registry.RegisterPlugins`` is refused (it loads native code from the folder).

The other libraries
-------------------

``bpy_angelo_guard_confine_pipeline_table.cc`` lists every file entry point: where it is defined, how it is held (a
module function, a method, a static or class method, ``__init__`` or a static type's ``tp_new``) and which arguments
name files. Gates go in when the library's extension module is made, however it is imported (``importlib``
raises no ``import`` event for a module itself) and for modules Angelo or the person loaded before any script:
``_imp.create_dynamic``/``exec_dynamic`` and their builtin twins are wrapped on top of the confinement's own. The
originals stay in C. ``bpy_angelo_guard_confine_pipeline_special.cc`` handles the files a library finds by itself:

* **A name searched in folders** (OSL queries, OpenImageIO's ``ImageCache``/``TextureSystem`` and the shared cache
  ``ImageBuf`` uses, MaterialX's readers): the name, and the name in each folder, are judged; a search path
  attribute's folders are judged when set.
* **Plugin search paths** (OpenImageIO's ``plugin_searchpath``, as an attribute or an argument) load native code:
  refused when not empty.
* **MaterialX XIncludes**: a confined read's options hold the gated reader, so every included file is judged where
  MaterialX finds it; ``XmlReadOptions.readXIncludeFunction`` gives a confined script the gated reader too.
* **OpenColorIO LUTs** are read when a processor is made, through a configuration's search paths and working
  folder: a confined call's configurations (``getProcessor``, ``GetProcessorFromConfigs``, a ``Baker``,
  ``GroupTransform.write``, ``SetCurrentConfig``) are copies whose LUTs are read through a judging IO proxy
  (OpenColorIO's ``ConfigIOProxy``), so the path OpenColorIO resolved is judged before any byte is read.
  ``$OCIO`` is judged where a call reads it.
* **OpenImageIO's color operations** use OpenColorIO without the proxy: their configuration (a file, ``$OCIO`` or
  the built-in default) is made first in OpenColorIO's module with the proxy, and every transform it holds, or the
  LUT ``ociofiletransform`` names, is made once there, judged.
* **Mantaflow** publishes no signatures: every string argument of a save or load is judged as a path.

OpenTimelineIO's adapters and plugins are Python (audited by the hook); its core's JSON file functions are gated.

Tests
-----

* ``test_blender_confinement_pipeline`` (in the app): each library's ordinary work on a project folder (Blender's USD
  import and export, ``importlib``); private paths named directly refused; private files reached through
  composition, XIncludes, LUT search paths, a symbolic link, a relative path and usdz packaging never read; USD's
  resolver confined; every file-like entry point of the bundled libraries classified (``native_inventory.PIPELINE_*``,
  each gated one called with a private path).
* ``test_blender_confinement_usd_unconfined@no_plugin_resolver`` (in the app, USD on its default resolver): ``Ar``,
  an in-memory stage composing a private sublayer, a stage opened by path, the USD operators, a USD cache file and
  Hydra are refused to a confined script while Angelo's own USD works; a child Angelo whose environment declares
  another primary resolver plugin still uses the confined one.
* ``test_blender_confinement_escapes``: every bundled extension module classified; the refused list matches C.

Upstream issues seen while testing
----------------------------------

Not caused by the confinement; they reproduce in the app's bundled Python with Angelo off.

* **OpenImageIO 3.1: ``TextureSystem.imagespec()`` crashes** (segmentation fault) on an existing image. Repro:
  ``blender --background --factory-startup --python-expr "import OpenImageIO as o; buf = o.ImageBuf(o.ImageSpec(4,
  4, 3, 'float')); buf.write('/tmp/a.exr'); o.TextureSystem(False).imagespec('/tmp/a.exr')"``, or the same in
  ``<install>/5.3/python/bin/python3.13`` with the app's ``site-packages`` and ``lib`` on the path. The binding
  copies the ``ImageSpec`` the texture system returns (``py_texturesys.cpp``, ``imagespec``). Report upstream.
* **Mantaflow aborts when a solver is freed before its grids**: ``Manta::FluidSolver::~FluidSolver`` throws
  (``fluidsolver.cpp:33``) and the C++ runtime terminates. Repro: ``import manta; s = manta.Solver(name='x',
  gridSize=manta.vec3(8, 8, 8), dim=3); g = s.create(manta.RealGrid); del s`` then let ``g`` be freed (a script's
  namespace freed in that order does it). Free grids before their solver.

What is not covered
-------------------

* **A library's own I/O behind an entry point no inventory names**: the inventory lists entry points by name; a
  library update that adds a file entry point with an unusual name is caught only if its name says files.
* **A plugin a library loads on its own** from folders set before the script (an OpenImageIO plugin path the person
  set): plugin paths are refused to scripts, not audited when Angelo set them.
