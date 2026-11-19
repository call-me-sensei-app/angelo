Agent scripts run confined
==========================

While any agent script runs, Angelo enforces at run time, never by reading the script's text, that it can't change
Angelo's private state or step outside Angelo. This covers every tool that runs a script (``angelo_execute_python``, a worker's script) and every review mode, Always allow scripts
and Trust scripts for this chat included.

The maintainer's rulings:

* 2026-11-14: "close it fully".
* 2026-11-15: "C guard in 2.0.0", and "Deny network to scripts".

The operator and authority side is :doc:`grant_operators`; this document is the runtime side.

How it works
------------

The confinement lives in C, in ``_bpy._angelo_guard``, next to the authority lane's ledger
(``source/blender/python/intern/bpy_angelo_guard*.cc``). Python code that runs in the same interpreter can rewrite
any Python object: module names, closure cells, ``__globals__``, instance attributes. So nothing the confinement
decides with lives in Python.

* **The audit hook** (PEP 578) is a C callback (``bpy_angelo_guard_confine_hook.cc``), registered with
  ``PySys_AddAuditHook`` before Python starts (``BPY_python_start``). It can never be removed.
  * Until the first agent script runs in a process, it returns after one comparison per event.
  * After that, only the events it judges are looked up and checked: files, programs, native code, the network,
    introspection, thread starts and changes to installed classes.
* **Confined is the thread's state, held in C.** A thread is confined in any of these cases:
  * its ``thread_local`` depth is above zero: inside ``run_confined``, inside a trampoline, or in a job a confined
    thread started;
  * it ran a script outside ``run_confined`` (fail closed);
  * a C walk of its frames finds agent code. That is code compiled under ``<angelo-reviewed-script>`` or
    ``<bpy driver>``, or code a confined thread compiled or ran, tainted by object (``builtins.compile`` is
    wrapped, and the ``exec`` event taints what runs), never by a file name the script picks
    (``bpy_angelo_guard_confine_provenance``). The taint table is held in C, by weak reference.
* **Code can't be made to look installed.** While confined, ``compile`` under a ``<frozen …>`` name is refused,
  and under an installed file's name only the import system's own compile of that file's exact bytes passes.
  ``code.replace``/``CodeType`` keep an agent name, or change an installed name only from installed code
  (``types.coroutine``). ``marshal.loads`` and ``_imp._fix_co_filename`` run only inside importlib's own bytecode
  loader (its code object held in C), on an installed ``.pyc``'s exact bytes or beside a module that is not
  installed, and in ``_imp.get_frozen_object`` for a module Python ships frozen (its bytes compiled into the binary:
  ``runpy``, ``zipimport``, ``os``; bytes a caller passes stay judged).
* **Regions and provenance.** A confined stretch of a thread (``run_confined``, a trampoline, a confined work item,
  a confining descriptor) is a region (``bpy_angelo_guard_confine_region``). While one runs, the object allocator
  records every block that thread allocates (``bpy_angelo_guard_confine_record``), and with it who made it: the
  script's own code (the innermost Python code running was agent code) or installed code, on the script's behalf or
  not. Both are facts C holds, read from the thread's frame chain without running Python; where a block was
  allocated more than once in a region, its latest allocation (the object living there) decides. When the outermost region
  ends, still confined, what it made and left alive is made safe ("Work that outlives the script"). A self-test at
  install proves the object layout this relies on; if it fails, no script runs. If the frame chain can't be read,
  everything a region made counts as the script's.
* **Running a script** (``run_confined(source, filename, globals, call_id, compiler)``):
  * The text is digested in C (SHA-256, ``bpy_angelo_guard_sha256``).
  * It runs only when the authority admits that exact text for that call (``BPY_angelo_guard_authority_admits``:
    an approval ticket or a standing consent held in C).
  * It is compiled by Angelo's own compiler (the parent-inverse rewrite), registered once at install and checked
    by identity in C (``set_compiler``), or in C; then run confined.
  * Still confined, the executor's read-back turns the result into plain data (``finish``), the namespace is
    cleared and its garbage collected. Angelo never touches a script's object after the script.
  * While it runs, C journals every change to a watched module namespace (dict watchers,
    ``bpy_angelo_guard_confine_journal``). After it, the journal is undone and the sweep puts back or confines
    what the script left elsewhere ("Work that outlives the script" below). Both sweeps are Angelo's functions,
    held only by C.
  * A run from inside agent code is refused.
* **The module exposes no state.** ``_bpy._angelo_guard`` holds only functions, two types (``ScriptConfined``,
  ``Deferred``) and constant strings.
  * Its type refuses ``setattr`` and ``delattr``, and its ``__dict__`` is a copy.
  * Every attribute is served from a C-held copy, so ``types.ModuleType.__setattr__`` or a ``__dict__`` write
    changes nothing Angelo calls.
  * Both types are immutable.
  * ``test_blender_confinement_attacks`` walks ``dir()`` and ``__dict__`` and tries every way to change them.
* **One signal.** These read ``confined()`` first:
  * ``grant_guard.script_running()`` (operators, widening settings);
  * ``grant_guard.inside_agent_code()`` (the entries);
  * the authority's ticket minting.
* **The Python stand-in.** ``script_confinement``, ``script_confinement_hook`` and their siblings implement the
  same rules in Python. They run only where ``_bpy._angelo_guard`` is absent (the host tests, a build without it),
  and they can be undone by a script written to walk closures.

What a confined script is refused
---------------------------------

Each refusal raises ``ScriptConfined`` (a ``PermissionError``) inside the script, reading "reason (what)", with
``reason`` and ``what`` on the exception. The agent reads the English. The person's interface shows the reason
translated (``activity_text.friendly_error``; every catalogue has it):

* "An agent script can't open Angelo's private settings, keys, approvals or connection records."
* "An agent script can't change what Angelo loads at startup."
* "An agent script can't start other programs. Ask the person to run it, or to approve it as its own step outside
  the script."
* "An agent script can't load native code."
* "An agent script can't use the network. Downloads go through Angelo's download tool."
* "An agent script can't change files Angelo's tools saved for it to read. Write a copy somewhere else."
* "An agent script can't make code run when a file opens."
* "An agent script can't change how Angelo checks scripts."
* "An agent script can't run this operator: it opens another program, installs code or saves preferences."
* "An agent script can't change Preferences. Ask the person to change them."
* "An agent script can't write a driver expression that runs Python. A simple expression (math on the driver's
  variables) works."
* "An agent script can't set a style module: its Python runs when the scene renders."
* "An agent script can't change how Angelo's process runs: its limits, priority, identity or what runs at fork."
* "Not while an agent script is running: only your own click in Angelo can do this." (a grant)

1. **Angelo's private folders, for reading and writing alike.** These are every folder in ``private_roots``' registry:
   the state folder, the account store, native CLI profiles and runtime homes, and the worker, headless and scratch
   families. They hold Always allow scripts, approvals and trust lists, external-app clients, keys and access,
   endpoint and local-model records, audit files and chats.

   These routes to a file are checked (what is not is listed under "What is not covered"):

   * Python's ``open``, ``os`` and ``shutil`` operations, ``sqlite3``'s own file and ``glob``;
   * a descriptor Angelo already holds open: ``os.read``, ``write``, ``pread``, ``sendfile``, ``splice``,
     ``ftruncate`` and their siblings are wrapped in C and judge the file the descriptor has open (a socket's is
     the network);
   * the native file entry points of modules loaded before any script: ``_cycles.osl_compile`` and ``blf.load``
     judge their paths, ``ssl.SSLContext``'s certificate loaders too, and ``faulthandler``'s writers are refused;
   * Windows' raw handles: ``_winapi.CreateFile`` is judged as a write (``ReadFile`` reads its handle with no
     descriptor to judge), ``_overlapped.ConnectPipe`` (any local service's named pipe) and
     ``_winapi.CreateNamedPipe`` are refused, and ``winsound`` (``PlaySound`` reads any file) is refused on import;
     a pipe the process makes for itself (``multiprocessing.connection.Pipe``, every pool's change notifier) is
     judged by its arguments, whoever calls: a fresh name (``FILE_FLAG_FIRST_PIPE_INSTANCE``) in the local
     namespace (``\\.\pipe\``), one instance, and one ``CreateFile`` of exactly that name by the same thread;
     a remote or ``\\?\UNC\`` name, a listener for other processes and a pipe that already exists stay refused;
   * a named semaphore a confined thread makes (a thread pool's lock) is unlinked at once, as under the fork start
     method (``script_confinement_blender.unshared_semaphores``), so it never registers with ``multiprocessing``'s
     resource tracker, a helper process (where the default start method is spawn: macOS); no helper process starts
     for confined code, and a process pool stays refused;
   * SQLite: no database can be attached to a confined thread's connection (``ATTACH``, ``VACUUM INTO``), its
     ``setlimit`` is refused, a ``vfs=`` name and extension loading are refused;
   * the pipeline libraries (USD, OpenImageIO, OpenVDB, MaterialX, OpenColorIO, OpenEXR, OpenTimelineIO, OSL
     queries, Audaspace, Mantaflow) at every file entry point, and USD's own composition through a confined asset
     resolver (:doc:`script_confinement_pipeline`);
   * modules with no file entry point to judge (``NATIVE_FILE_MODULES``: dbm, readline, Tk, curses, the
     subinterpreter modules and ``_lsprof``) are refused on import, however they are imported;
   * ``mmap`` of a descriptor, and ``os.open`` with a folder descriptor (wrapped in C, because the event does not
     carry it);
   * Blender's own file operations (``BLI_open``, ``BLI_fopen``, ``BLI_gzopen``, rename, delete, copy, touch and
     folder creation, through ``BLI_fileops_gate_set``) and image saves (``IMB_save_image``);
   * every RNA file or folder path a confined script sets or passes to an RNA function: a text's or image's file,
     the render output, a cache folder, ``bpy.data.libraries.write``. Blender's own writers are covered by the path
     they will use;
   * every path property of a confined operator call, a file browser's name list joined to its folder, and the
     file ``text.save``, ``image.save`` and a still or animation render write from context.

   Paths are resolved as the system resolves them, then compared by key and by folder identity
   (``bpy_angelo_guard_confine_paths``, with its gtest):

   * through links, junctions and ``..``;
   * a relative path against the working folder or a descriptor's folder;
   * ``/dev/fd/N`` and ``/proc/self/fd/N`` as the file that descriptor has open;
   * 8.3 names through the Windows final path;
   * case on macOS and Windows.

   A path that can't be resolved is refused. A new link into or above a private folder is refused.

   **A script makes no link.** What Angelo's own code later writes through a link lands where it points (a junction
   or symbolic link in a folder Angelo writes, aimed at the person's Documents), so confined code makes none: a
   symbolic link or junction anywhere (``os.symlink``, ``_winapi.CreateJunction``, and what calls them, such as
   ``shutil.copytree(symlinks=True)`` or a tar member), and no hard link to, or move of, something that is itself a
   link (``os.link``, ``os.rename``/``os.replace``, ``shutil.move``). Links the person made are read as before. On
   Windows, C's write record resolves a path through ``bpy_angelo_guard_confine_winpath`` (with its gtest): its final
   spelling from the longest prefix that exists, opened without following its last component, and whether a name
   surrogate reparse point (a junction, mount point, symbolic or WSL link; not a OneDrive placeholder) lies on the
   way.

   **The chat's own workspace is open.** Each chat's default workspace lies inside the private state folder. A
   script uses it as the file tools do (``files.FileWorkspace(own=)`` opens it through
   ``script_confinement.open_folder``, C ``add_open_roots``). The deepest folder decides. Inside the workspace, these
   stay private:

   * a private folder at or inside it;
   * a temporary-folder family;
   * a folder below it named like a native CLI profile (``claude-profile``, ``codex-profile``).

   Its chat folder and everything beside it stay private. Only the workspace's own spelling opens it: another
   spelling of it is judged as the private folder, so it fails closed.

2. **Startup folders, for changes.** These are Blender's user config (``userpref.blend``, ``startup.blend``), the
   user script, add-on, preset and extension folders, the script directories in Preferences, and the app's own
   folders. A script may read them. Python's bytecode cache (``PYTHONPYCACHEPREFIX``, set by a release) is one too: what
   Python loads for installed modules, so scripts can't change it, and an installed source's cached bytecode there
   is accepted, byte for byte, as beside its source.

   So are the places the operating system or a shell runs later on its own (``os_startup``): shell startup files,
   macOS ``LaunchAgents``, the XDG autostart and systemd user folders, the Windows Startup folder, and the user's
   Python site-packages (``usercustomize`` and ``.pth`` files run in every Python). Changes to the Windows registry,
   whose Run keys start programs at login, are refused; reads stay. Angelo's updates folder (``<cache>/updates``,
   ``update_ready.directory``) is refused for changes and never trusted: what Restart to update runs is staged
   there (the helper script ``sh`` or ``cmd`` reads as it runs, the Windows installer, ``ready.json``).

   **Tool-output folders, for changes, never trusted.** Angelo's own tools save files there for scripts to read,
   outside the confinement: the downloads of an unsaved document (``download_folders.unsaved_base``,
   ``<Blender's temporary folder>/angelo-downloads``), and the settings import's backup
   (``settings-import-backup``, whose Undo puts files back into startup folders). A script reads, lists and imports them, and is refused any
   change with its own refusal ("write a copy somewhere else"). They are never trusted: code imported from there is
   the agent's, not installed code.

3. **Grants.** These are refused at the operator call in C:

   * every operator the grant guard lists (``add_denied_operators``);
   * operators that open another program or a URL;
   * operators that install code;
   * operators that save, read or reset preferences or the startup file;
   * operators that reload every add-on: ``read_factory_settings``, and ``read_homefile`` naming an app template
     (both run ``addon_utils.reset_all()``). The add-ons' re-run module bodies are no import's own definition, so
     the region's end would put the old classes and handlers back and leave every add-on broken until a restart;
     a plain ``read_homefile`` or ``open_mainfile`` reloads no add-on and ends as ``DOCUMENT_REPLACED``;
   * quitting Blender.

   An agent script cannot change Angelo's grant ledger in either direction: C refuses its ``set()`` and ``remove()``,
   so it can neither give nor withdraw an approval, an approval mode or a trust, its own chat's or another's (the
   Fusion audit, 2026-11-15, G2). The function behind each grant is sealed in C (``_angelo_guard.seal``): what an
   entry holds runs only from the entry's own code, which judges the call first (G3); a script can't read an
   installed function's closure either (below). Python that is not agent code, Angelo's own or an add-on's, keeps
   Blender's full privileges until the helper process (2.1 or 2.2; :doc:`grant_operators`, "Residuals").

4. **Other programs.** Every audited start is refused: ``subprocess``, ``os.system``, ``exec*``, ``spawn*``,
   ``posix_spawn``, ``fork``, ``forkpty``, ``startfile``, ``_winapi.CreateProcess`` and ``webbrowser``. Other
   processes can't be reached either: ``_winapi.OpenProcess``, ``TerminateProcess`` and WMI queries (``_wmi``,
   which read every process's command line) are refused.
   ``_posixsubprocess.fork_exec``, which Python 3.13 does not audit, is wrapped in C.

   **Compatibility:** a script that needs another program is refused. The person runs it, or approves it as its own
   step.

5. **Native code.** Every ``ctypes`` event (a load, a call of a foreign function loaded earlier, memory access) is
   refused, as is importing an extension module from outside the app's own folders.

   **Compatibility:** these work differently in a script, by design:

   * ``cProfile`` and ``profile`` (``_lsprof``), ``readline``, ``dbm``, ``tkinter`` and ``curses``, and Python's
     subinterpreters (``_interpreters``) are refused on import: each opens files or runs code where no check can
     see it;
   * native plugins a script points a library at are refused: OpenImageIO's ``plugin_searchpath`` and USD's
     ``Plug.Registry().RegisterPlugins``. Plugins installed with the app, or registered by the person, load as
     usual;
   * ``PyOpenColorIO.SetCurrentConfig(config)`` makes a copy of the configuration current, one that reads its LUTs
     through the confinement's check: the same colors, but not the same object.

6. **The network.** Every socket event (connect, bind, resolve, send) is refused, along with ``urllib``,
   ``http.client`` and the mail and FTP clients. ``socket.socketpair`` (a connected pair inside the process, which
   ``asyncio``'s event loop wakes itself through) is allowed with the socket events it raises itself. Downloads go through an Angelo tool that runs outside the script,
   with its own approval.

7. **Taking the guard off.** All of these are refused:

   * ``sys.addaudithook``, ``settrace``, ``setprofile``, the async-generator hooks and every ``sys.monitoring``
     tool or callback;
   * ``sys._current_frames``;
   * ``gc.get_objects``, ``get_referrers`` and ``get_referents``;
   * making functions from code (``FunctionType``), and making code that looks installed (above);
   * changing an installed function's ``__code__``, ``__defaults__`` or ``__kwdefaults__`` from the agent's own
     frame (a script's own functions stay free: ``namedtuple`` defaults);
   * re-initializing a loaded built-in module, which would hand back the originals the wrappers replaced. The
     originals live only in C, and every other reference to them (a ``from`` import, a ``__wrapped__``) is replaced;
   * reading an installed function's ``__closure__`` (its cells), by the script or any helper it calls
     (``inspect.getclosurevars``): installed code keeps what a script must not call directly there. A script's own
     functions' closures stay readable. An installed function's ``__defaults__``, ``__kwdefaults__`` and
     ``__globals__`` stay readable (``inspect.signature`` and ``typing`` need them), so an original kept there is
     reachable; calling it runs confined like any installed code the script calls, and the grant entries' originals
     are sealed in C whatever holds them (above).

8. **Preferences and the keymap.** Every write is refused in C (``bpy_rna.cc``): a property, an item or slice of
   one, ``foreach_set``, a collection's ``add``/``remove``/``move``, and every function of Preferences, an add-on's
   preferences or a key configuration (``autoexec_paths.new``, ``keymap_items.new``). A pointer is Preferences when
   its data lies in ``U`` or Preferences, add-on preferences or a key configuration is it or any of its ancestors.
   Every ``preferences.*`` operator and the keymap operators are refused too. Preferences are the person's
   configuration and Auto-Save Preferences writes them at quit, several decide what code runs ("Auto Run Python
   Scripts", script directories, extension repositories), and a keymap item can bind a key to running a file: so a
   script asks the person instead. Restoring them at the script's end was rejected: it would silently undo what a
   script meant, where a refusal tells it why.

9. **Python in the project.** Blender runs Python from file data in three places: a text set to run at load
   ("Register", ``use_module``), a scripted driver's expression and a Freestyle style module. Setting
   ``use_module``, a driver expression that needs Python (or making a driver scripted over one), a style module's
   script, or switching one on is refused (``bpy_angelo_guard_confine_carriers.cc``). A driver expression needs
   Python when Blender's simple-expression evaluator can't take it, whatever its variables are called: math on
   variables, numbers and the evaluator's functions works, in any order of adding the variables. A driver's Python
   always runs confined, wherever it came from (its code is ``<bpy driver>``, and its first evaluation arms the
   checks, so a file opened before any script ran is covered). Python that enters the project from confined code
   by any route (``bpy.data.libraries.load``, the Append and Link operators, an asset drop, a paste), in a region or
   from a generator the script left and resumed later, is made inert on entry by the same rule, in
   ``BKE_blendfile_link`` and ``BKE_blendfile_append`` (``blendfile_link_append_angelo.cc``): an appended text stops
   running at load, an appended driver keeps its expression but not its Python (in the data-block and in every ID
   embedded in it, such as a material's, world's or light's node tree or a scene's compositing tree, found by the ID
   walk rather than a list of where they live: ``BKE_angelo_foreach_animdata``; the end-of-script sweep walks the
   same way), an appended style module is switched off, and a linked text that runs at load or a linked scene with a
   style module on is removed and reads as not loaded (its code would come back from the library file at every
   open).

   **Python a file carries runs only as the person trusted it** (``bpy_angelo_autorun_trust.cc``). The rules above
   act on routes; this one acts where the code runs, so it holds for every route, named or not (open, recover,
   revert, link, append, relocate, reload, undo, an asset drop, a library's own texts at the next open, a generator
   the script left). A registered text runs when a file opens, and a style module runs at render, only if the
   person trusted that exact content: its SHA-256 digest is in the person's list
   (``<config>/angelo_trusted_scripts.txt``, in the user config folder a confined script can read but never
   write). Anything else is held back the way Blender holds back a file's scripts with auto-run off, and named in
   the auto-run warning. Only the person's own actions add to the list, never a confined thread's:

   * **Allow Execution** in that warning trusts the content it held back;
   * turning **Register** on (refused to a confined script) trusts the text's content then (Rigify's rig UI);
   * the person's own **save** trusts each registered text, and each style module switched on, that no file and
     no agent script brought or changed. Blender's text code keeps that as ``TXT_ANGELO_UNTRUSTED``, run-time only
     and never written. It is set when a text is read from any file (a .blend, undo, a library, a .py loaded or
     reloaded) unless its digest is trusted, and by any edit while confined (``txt_make_dirty``, which every
     editing path calls, and ``TextLine.body``); only the person's trust clears it.

   A style module the person makes in the session runs as before. "Allow Execution" trusts what the warning named
   when it was shown, so a script opening another file before the person answers can't change what they trust.

10. **The process's state** (``bpy_angelo_guard_confine_process.cc``, ``_signals.cc``). Resource limits, priority,
    CPU affinity and scheduling, the process's identity and session, ``chroot`` and namespaces are refused: they
    can't be undone, or every program Angelo starts inherits them; so are what delivers a signal after the script
    (``signal.alarm``, ``signal.setitimer``), ``signal.siginterrupt`` (it can't be read back), a disposition Python
    did not set (a native handler ``signal.signal`` could never restore), ``faulthandler``'s switches (Angelo's crash
    reports), ``gc.freeze``/``unfreeze`` (the regions' own) and every change to ``sys.monitoring`` (the
    confinement's own tool). The environment (``os.putenv``, ``os.unsetenv``, ``os.environ``: what every program
    Angelo starts inherits, ``LD_PRELOAD`` and ``TZ`` included, and what ``requests`` reads for a proxy), the working
    folder (``os.chdir``, ``os.fchdir``: every relative path Angelo opens) and the file-creation mask are refused to
    confined code inside a region or not (``process_change_refused``), whatever installed code the script steers
    into making the change (an installed module's top level run with ``runpy``'s ``init_globals``, a rebound
    builtin an import calls): no exemption while a confined frame is on the stack. The refusal says what to do
    instead (absolute paths; a setting passed to the code that needs it; ``os.chmod``). ``os.environ`` and
    ``os.environb`` are views of the C environment (``bpy_angelo_guard_confine_environ.cc``): no copy of their own
    (Python's ``_data``) that could differ from what programs inherit, no attribute or type to replace, and a
    rebinding of ``os.environ`` goes back with the journal. The locale and the interpreter's process-wide settings
    (``gc``, the recursion limit, the switch interval, the integer-digit limit, the dynamic-loading flags, the thread
    stack size) are put back when a region ends, and refused to confined code outside one. A callback given to
    ``os.register_at_fork`` runs confined, an import's own included.

11. **Code a script wrote never runs as Angelo's** (``bpy_angelo_guard_confine_hook.cc``). What a confined thread
    changes is recorded (``bpy_angelo_guard_confine_changes.cc``) in ``angelo_agent_changed.txt`` in the user config
    folder (a confined thread can read it but never write it), so a file a script wrote in an earlier session is
    still known as its own; tool output counts too. The record holds **files, never ancestry** (guard piece 7b: a
    project folder a script made had made the person's own add-on in it unloadable for good):

    * a file a script wrote, and a path something was moved or linked to. A folder counts as the script's, with
      everything in it, only when a script moved or linked it, or wrote more than 256 entries directly into it: the
      folder is then recorded whole (compaction), unless one of Angelo's folders lies in it (a home folder holds the
      state folder). A folder made, an entry removed, a mode, owner, time or flag changed: never recorded, as they
      carry no code (still judged as changes by the path rules);
    * each file a script wrote or moved, by identity (device and inode, or volume and file index) and content
      digest (SHA-256 of the whole file up to 128 KiB, else of its size and its first and last 64 KiB), taken when
      the region that wrote it ends, and again at a later region's end when its size or modification time changed
      (a handle the script kept open and wrote through in a later step: such a write has no event); a file
      unchanged for 32 region ends stops being watched (at most 256 are). An unconfined load is refused when its path, or its identity with the same
      size, or its content matches: Angelo's own rename or copy of a script's file elsewhere is still the script's,
      in that session and later ones. Identity and content are judged outside installed folders only: scripts
      can't write there, so what is there arrived by an install, and installed code is installed whatever its
      bytes. An empty file holds no code and is never recorded by content;
    * bounded: at most 16,384 path records (past it the deepest folder above the new path that can be recorded
      whole is; when none can, the change is refused) and the 8,192 newest content records.

    A load is judged on the real path (links followed, the file system's own case). The record fails closed: a
    change that can't be added to it is refused (saying so), and while it can't be read, or what a region wrote
    can't be added, Angelo loads code only from its installed folders. At a session's first read, path records whose
    path no longer exists are dropped, unless its volume is away (the first existing folder above it is on another
    device than the record says: a removable or network drive), and the file is rewritten whole (a new file put in
    place, never half-written); content records stay. A script making a path again records it again. Another
    session's additions are read again when the file changes (a load looks at most every 200 ms; a change recorded
    here looks first). **Preferences > Forget Agent Changes in a Folder…**
    (``agent_changes_forget``; a grant: refused to an agent script and to Angelo's automatic decisions, and its C door
    ``forget_changes`` refuses any caller outside the person's own grant scope) forgets the path records in that
    folder and above it, and the files now in it by identity (a link in it is not followed), so the person's
    reviewed code there loads as theirs; a copy of the same content elsewhere still counts as the script's, and a
    script writing one of those files again makes it the script's again. Python loads every module's source or bytecode, and
    every zip it imports from, through ``io.open_code``; the confinement's hook refuses an unconfined load of such a
    file, and an extension module or ``ctypes`` library from one. So data a script leaves in the import system
    (``sys.path``, ``sys.path_importer_cache``, ``sys.meta_path`` entries) can steer a later import of Angelo's to its
    file, and the import fails instead of running it. A confined thread imports its own files as before (its code is
    confined). A module Python ships frozen is the binary's own code and loads anywhere. The import system's steering
    state a script rebinds (``sys.path``, ``meta_path``, ``path_hooks``, ``path_importer_cache``, ``pycache_prefix``,
    ``dont_write_bytecode``) goes back exactly at the region's end, and the sweep puts back what a script changed
    inside them (entries, and a cached finder's ``path``).

Work that outlives the script
-----------------------------

* **Confined wherever and whenever it runs.** The guard, the entries and ticket minting refuse there too:

  * a function the script defined, and code it compiled (by object);
  * a thread it starts: the target gets the C trampoline ``Deferred``;
  * a ``bpy.app.timers``, ``bpy.msgbus``, draw-handler or ``bpy.props`` callback (update, get, set, poll, items);
  * an ``atexit``, thread-exit (``threading._register_atexit``), signal, codec-search or codec error callable, and a
    ``weakref.finalize``'s function (installed code too: the arguments are the script's), which also never runs at
    interpreter exit; each is confined as it is registered, because the registry wraps or records it where no later
    pass could reach (the thread-exit list holds a lambda of its own). ``finalize._registry`` is judged at the sink:
    every record a region put there, whatever made it, is settled at its end (``_exitfunc`` and ``__call__`` take any
    object with ``atexit``, ``func`` and ``args``): a genuine one (exactly ``_Info``, under exactly a ``finalize``)
    has its function trampolined and never runs at exit, and anything else (a namespace, a subclass whose ``atexit``
    is a property, a key of a subclass with its own ``__call__``) is removed; a record the region found and changed
    in place (its function, arguments, ``atexit``) gets every field back;
  * a job (``WM_jobs_start``): its thread inherits the depth.
  * Every trampoline entry checks the headroom left on its thread's native stack and raises ``RecursionError``
    before it runs out: a callable the script left can re-enter itself through C alone (a ``__hash__`` that is an
    ``lru_cache`` keyed on the object), and CPython 3.13 counts recursion, not stack bytes.

* **Work it left suspended runs as a region** (``bpy_angelo_guard_confine_resume.cc``). A generator, coroutine or
  async generator a region made and left alive is resumed, not called, so no trampoline can wrap it: whatever holds
  it (Angelo's code, an event loop, a thread) can run its next step after the region ended. Each one is noted when
  the region ends, and a step of it that starts outside a region runs inside one, entered when the step starts and
  left when it yields, returns or unwinds, on whatever thread runs it, with everything a region's end does: the
  journal (rebindings, registries such as ``copyreg.dispatch_table``), the leftovers pass below (a
  ``weakref.finalize``, a ``threading._register_atexit`` callable, a codec error handler), the process's state,
  Python it brought into the project, ``sys.modules``. The events come from ``sys.monitoring`` under the
  confinement's own tool id, set only on those generators' code (throw and unwind events are process-wide and return
  at once unless a step was entered on the thread). Its own leftovers are noted at its end in turn.

* **What a region made and left alive**, by provenance (findings in the result's ``confinement_restored``):

  * a class the script made (its code made it, or a class statement whose body is its code, through any
    metaclass: ``ABCMeta``, ``EnumType``): every callable or descriptor in it that is not installed code becomes
    ``ConfinedAttr``, so its dunders (``__del__``, ``__repr__``, ``__eq__``, ``__hash__``…), properties and methods
    run confined whoever reaches them. Installed code there keeps its identity: installed functions, C descriptors,
    classes, and an ``lru_cache`` judged by the function it holds in C (never its settable ``__wrapped__``). A
    partial or any object whose class reads what it holds (``partialmethod(exec, …)``) is the script's choice and
    is confined;
  * a class installed code made (an import, ``namedtuple``, ``enum``'s functional API, a pybind11 module) keeps
    what it holds (``lru_cache`` and ``cached_property`` methods, ``Protocol`` and ``enum`` attributes, native
    methods): only the script's code in it is confined, and what the script set on it afterwards goes back with the
    journal;
  * a weak reference it made with a callback (``weakref.finalize`` too): the callback becomes the trampoline, unless
    it is installed code (the standard library's own cleanups). A ``weakref.finalize`` it made never runs at
    interpreter exit, where nothing could confine it;
  * an object it made that holds a callable (a dict, list, set, cell, an instance's attributes or slots, a
    finalizer's record): the callable becomes the trampoline;
  * a callable it made, or a tuple of them, that older objects hold (an attribute of an object Angelo holds, a
    list): replaced there by its trampoline, through one pass over what refers to them.
  * Only callables whose effect the script chose are replaced: its code (agent functions, and methods,
    ``lru_cache``\ s, partials and wrappers around them), and what its own code made from installed parts (a method
    of an object it made, a partial or operator caller it made). What installed code made on the script's behalf from
    installed parts and keeps in its own state stays as it is: ``importlib.metadata``'s cached bound methods, the
    ``re``, ``fnmatch``, ``typing`` and ``functools`` caches, objects of installed classes (a ``typing`` alias, a
    weak reference in CPython's own subclass registry). ``test_blender_confinement_escapes`` runs a matrix of the
    standard library's caches and memos twice confined, then unconfined, and checks none is left confined.
  * A finalizer can run inside a garbage collection and start a region. C knows when a collection runs (a
    ``gc.callbacks`` entry it puts back if removed): a region started then neither freezes nor scans, and its scan
    waits for the next region that ends outside one. The scan itself runs with automatic collection paused.

* **Put back or wrapped when it ends** (the sweep; findings in the result's ``confinement_restored``):

  * handler and ``driver_namespace`` entries it added are wrapped;
  * a text it set to run at load stops running at load;
  * a widening Angelo setting goes back;
  * ``sys.path`` and the importers keep only installed entries;
  * the environment, the builtins, the interpreter's hooks and replaced ``sys.modules`` entries go back. Under
    ``os.environ``, C puts back the process's state when the last confined region ends: the C environment (every
    difference native code or an installed import made; a script's own ``os.putenv``/``os.unsetenv`` is refused),
    the working folder and the file-creation mask an installed import changed, the locale (noted by wrappers at the
    first confined change),
    and the interpreter's process-wide settings (garbage collection and its thresholds, the recursion limit, the
    switch interval, the integer-digit limit, the dynamic-loading flags, the thread stack size), signal dispositions
    and the wakeup descriptor (on the main thread: at once, or the next time it runs Python), the default socket
    timeout, the warning filters and the time zone ``time.tzset`` read; the signal mask of the thread the region ran
    on goes back when that thread's region ends (programs started from it inherit it);
  * ``sys.modules`` is as the region found it, name by name: what a name held goes back (replaced or removed), and a
    name it gained stays only if the import system loaded it from installed parts under its own name, from the file
    (or built-in or frozen module) that name resolves to as the installed finders resolve it, worked out on the file
    system alone, with no finder, hook or descriptor run (``sys.path`` as the region found it, then installed folders
    added since, or the package's ``__path__``; in each, the package directory's ``__init__`` before a module file,
    by extension, source and bytecode suffix; namespace packages by their places) (an import of installed code,
    whoever asked for it). What was loaded is judged by the loader that reads the body, never by the spec's origin
    (any text its maker gave): a wrapper's held loader (``importlib.util.LazyLoader``), a file loader's own path and
    the one name it loads, the interpreter's built-in and frozen importers themselves, or a finder the region found
    on ``sys.meta_path`` loading what it found (``six.moves``), while that finder's own name maps hold what they held
    when the region began (copied then, compared by identity: a name a script added in place is dropped); any other
    loader is dropped. A module object the
    script made, one it
    registered itself (``module_from_spec``, ``sys.modules[name] = m``, ``exec_module``: names it set before the body
    ran would run as Angelo's), one a loader of its own made (a finder it put on ``sys.meta_path``), another installed
    file loaded under the name (``_bootstrap._load`` of ``pickle``'s file as ``plistlib``, directly or through a
    finder of its own), a ``None`` that blocks an import, and an installed module under another's name are gone
    (Angelo's next import loads the module whole), so
    nothing Angelo imports later resolves to what the script put there. A change
    a generator the script left makes later, outside a region, is settled by the same rule the next time the main
    thread runs Python;
  * the import system's search state and the interpreter's exit list are as each outermost region found them,
    whatever kind of region it is (a run, or a step of a generator the script left, which has no sweep):
    ``sys.path``, ``sys.meta_path`` and ``sys.path_hooks`` hold what they held, in its order, then only installed
    entries added since; a replaced ``sys.path_importer_cache`` entry goes back and a new one stays only if it is an
    installed kind, and every cached ``FileFinder`` lists its folder again at its next import (the sweep clears its
    cached mtime at the script's start and end, calling no finder: a folder whose mtime did not move, seen on Windows,
    hid a module made while Angelo runs; ``test_native_sweep_new_modules``); ``threading._threading_atexits`` keeps
    only trampolines and installed code added since; each loaded
    package's ``__path__`` (or a namespace package's places) holds what it held (the packages, and any module whose
    namespace the journal doesn't watch, are found again only when ``sys.modules`` or a ``__path__`` changed, as the
    journal's dict watcher sees, so a region copies a few dozen lists rather than walking every loaded module);
    logging is as it was (below, "Angelo's own I/O"). Lists changed in place while the region settled are settled
    again at its very end;
  * nothing of the script's runs unjournaled while a region settles: what the settling itself does calls no Python
    (no finder, hook, descriptor or ``__getattr__``), and what the script's code changes there anyway (an object's
    ``__del__`` as the undo releases it, a key's ``__hash__``) is journaled and undone before the region ends;
  * Python it brought into the project is made inert (main thread): a driver expression that needs Python and
    wasn't there when the region started keeps its expression but not its Python ("Python restricted for
    security" until the person edits the expression), a style module it set is switched off, and linked data it
    brought in that carries a text that runs at load or a style module is removed. A .blend written while confined
    is written the same way, and a save is refused while such linked data is in the file;
  * every function, class, module or UPPER_CASE constant rebound in any loaded module goes back, and every name
    removed comes back. Module-level dicts (registries such as ``grant_guard.ENTRIES``) are journaled too: what
    replaces code goes back, a new non-installed callable goes, a removed entry that held code comes back;
  * a class's attributes: ``type.__setattr__`` is replaced before Python starts, so every change to a class the
    script didn't make is journaled and put back exactly. On Blender's RNA types what a script registers (a
    property) stays and what replaces code goes back;
  * a text set to run when a file opens: setting it is refused, and a .blend written while confined (code the
    script left and resumed later included: every gate asks the same ``confined_now()``) keeps a text's
    run-at-load only if it was so, with the same content, when the region started (appended texts too). The
    journal sees the namespace itself,
    so the route doesn't matter: attribute or item assignment, ``update``, ``exec`` into the namespace, a
    function's ``__globals__``, ``clear``, or a module swapped in ``sys.modules``;
  * a module's namespace is as the region found it, data included: whatever a confined region changed there (a
    module's opener, a default context, a flag, a cache) goes back, unless it is that module's own definition (below),
    what the import system itself adds as it loads (``__builtins__``, the interpreter's own; a package's attribute for
    a submodule it keeps, which goes when that submodule is dropped), or Angelo's own add-on code changing its own
    package with no frame of the script's running (its tool state, kept unless the value is one the script made); a
    script's write there, or Angelo's or installed code's it called with what it supplied, goes back;
  * a module the script imports first is watched from the moment it enters ``sys.modules``. A rebinding made in
    it later goes back. What an installed import does to its own package is that module's definition and is kept:
    any change to a module or class of its own top-level package made while the import system loads an installed
    module for the first time (its top level run by ``_load_unlocked``, and what that calls, reached from the running
    frame with no agent frame on the way: a package patching its own C++ classes from a sibling module,
    OpenTimelineIO's ``_core_utils``, USD's ``Tf.PrepareModule``; ``enum._convert_``; a class's
    ``__init_subclass__``), a Blender RNA type (registering on them is its API), and an extension module's native
    initialization, and a module changing its own namespace or classes with its own code. A change to another
    package's module or class goes back, whatever made it. Never a script's
    own frames, an import hook or loader it left (an import whose spec or loader is a kind the script made, or holds
    code of its own, is the script's), or a
    body a script ran itself (``runpy``, ``exec_module``, a fresh copy under a name it chose, a reload), where it
    could have seeded the namespace: a top level it steered into changing another module or class
    (``functools.partial(setattr, json, 'loads', eval)`` as a name the top level calls) is journaled and put back like
    the script's own change, a builtin included. And once a callable that is not plain installed code has been put
    into installed code in a region (a rebound builtin, a patched function, through ``unittest.mock`` or not; in a
    module-level registry such as ``encodings._cache``, by the script itself or as its code), no installed module's
    top level or native initialization runs for the rest of it: refused where it would run (an import statement,
    ``importlib``, a loader called directly), naming what the script placed ("import what it needs first, then
    change things"), since a top level could call what the script chose; the import system removes the module again.
    Ordinary work seeds nothing: an installed function placed by installed code (``logging.captureWarnings``), a
    module's builtin, a weak reference, a module changing its own namespace or classes with its own code
    (``typing.__getattr__`` caching ``typing.Match``), and the interpreter's own hooks in ``sys`` (``excepthook``,
    ``displayhook``, ``unraisablehook``, ``breakpointhook``: each has its original as ``sys.__excepthook__`` and so
    on; the interpreter calls them, an import doesn't). (Instance attributes of installed objects are not watched;
    what a top level reaches through one can change only that import's own package.) A multi-phase extension
    module's C code filling its namespace in ``_imp.exec_dynamic``/``exec_builtin`` counts too, and so does a module
    Python ships frozen (``<frozen __hello__>``: its code is the binary's own). Code such an import compiles as it loads
    (``@dataclass``'s generated ``__init__``, ``namedtuple``'s ``__new__``) is that module's too, never the script's.
    A definition whose value is evidently the script's code (an installed package that loads a file the script
    wrote) is not kept; a value the script planted elsewhere that the module copied (``os.path.join`` rebound, then a
    module imported that runs ``from os.path import join``) gets the original;
  * a script can't change the watch list (``watch_modules`` is refused while confined), and no watcher can be
    removed without native code.

Compatibility
-------------

* **Asked once:** a registered text in a file the person opens with auto-run on (Rigify's rig UI in a rig made
  before this version, a script in a downloaded file) is held back until they choose Allow Execution in the
  auto-run warning, once per content; it runs as before afterwards. A text they register, edit and save in Angelo,
  and a style module they make, need nothing. A background run (``-b -y``) runs only trusted content: a pipeline
  that relies on a file's registered texts trusts them once from the app.

* **Unchanged:**

  * files outside the private and startup folders;
  * temporary files, threads, dataclasses, logging and tracebacks;
  * installed extension modules other than the native file openers, and Blender's own operators;
  * ``sqlite3`` on a file the script may use, without attached databases;
  * ``asyncio``, ``unittest.mock``, ``namedtuple`` defaults, ``types.coroutine``;
  * registering operators and properties on Blender's types.

* **Refused:**

  * the network;
  * other programs;
  * a timer the script unregisters itself by the original function. It was registered as its confined form;
    ``is_registered`` and ``unregister`` with the original find nothing.

* **Angelo's own code is confined while a script runs.** That includes the handlers a script's own load or save
  fires. Their private-file work is refused, and the refusal is printed. Lifting the confinement for Angelo's
  handlers was rejected: a handler a script triggers can't be told apart from one it calls itself.

Angelo's own I/O
----------------

A script's changes to the process's shared Python state are put back when it ends (namespaces, data included;
classes; the import system's search state), but Angelo's own requests carry its credentials, so they also don't
trust that state when they run:

* **TLS.** Angelo's verifying context (``team_http.tls_context``: its bundled roots, the managed policy's extra
  roots, the distrust list) is built once, unconfined, before the first agent script (or outside one), never from
  inside a script, and fingerprinted (verification, hostname checks, flags, options, protocol range, key log file,
  socket classes, its roots). Every request checks the fingerprint, and that what it is built from (``ssl``'s context
  classes and factories, ``urllib.request``'s opener and handlers, ``http.client``'s connections, ``socket``'s
  connect: ``net_integrity``) is still what the add-on found at registration. Any difference refuses the request
  with a TLS error that says Angelo's network security settings were changed in this session and to restart Angelo:
  it fails closed, never rebuilding from what changed. Angelo never uses ``urllib``'s global opener.
* **Logging.** C puts back the root logger, Angelo's own loggers and those of the libraries its network I/O goes
  through (``urllib3``, ``websocket``, ``asyncio``, ``requests``, ``httpx``, ``httpcore`` and the like, whose records
  carry its traffic) exactly at the end of every region, a step of a
  generator the script left included (``bpy_angelo_guard_confine_logging.cc``): each logger's own attributes
  (handlers, level, filters, parent, propagation, disabled), each of its handlers' level, filters, formatter and
  stream, the manager's logger class, record factory and ``disable``, a replaced entry of Angelo's in the logger
  table, and ``logging._handlerList``; a logger of Angelo's made meanwhile keeps no handler, and the level caches are
  cleared. A handler a script added never receives Angelo's records. (Without the native guard a Python sweep does
  the same around each script.)
* **Credentials go only to a console Angelo named.** A team account's requests go to the console its own code named
  outside any script (``net_integrity.allow_origin``, an immutable set); a different address on the account object
  refuses the request. ``urllib.request``'s proxy lookups (``getproxies``, ``proxy_bypass``) are among what is
  captured at registration. These checks run where scripts run, in the app (the native guard); outside it a caller
  that swaps them (a test's fence, a tool's own proxy) is no script's doing. The TLS fingerprint holds the hostname
  checks (``hostname_checks_common_name``), the security level and each root by its bytes; a context may gain only
  roots from the system's certificate folder (which OpenSSL loads as a handshake needs them). After each handshake
  the verified chain must end at a root the context was built with (its CA roots and every certificate in the
  managed policy's extra-roots file, CA or not: an organization may trust a server's own certificate) or one from
  that folder, so a root a script added (``load_verify_locations``, a ``capath``) vouches for nothing. The WebFetch
  hook runs in its own interpreter and builds its own context.

Angelo's own code
-----------------

Angelo's unconfined code later acts on what its objects hold: folders it removes at exit, the socket it unlinks,
the address its credentials go to. The journal sees rebindings, not changes inside a container. So the guarantee is
at the sinks: Angelo's destructive steps, its signals and its credentialed requests are checked against facts C
holds, whatever its Python state says. On top of that, the code around a script is closed to it:

* **Frames and imports** (``bpy_angelo_guard_confine_opaque.cc``). A frame a script may read is its own or one it
  called (walking out from the frame, the script's comes before any of Angelo's). Any other frame (Angelo's code,
  what Angelo's code called on the script's behalf, what called the script) is opaque to it: its ``f_globals`` is a
  stand-in holding only its module's name, its ``f_locals`` is empty, and no ``f_back`` leads to it
  (``sys._getframe``, a traceback's or a generator's frames). The namespace of Angelo's code that runs the script is
  not reachable that way; tracebacks and a library's own frame lookups (USD's) still work. Angelo's package
  (``angelo_agent``) can't be imported by name from a script (``import``, ``__import__``,
  ``importlib.import_module``). An agent caller is one whose nearest frame, walking out,
  is the script's before it is Angelo's (a frame whose globals are a namespace of Angelo's package): Angelo's code
  the script calls (``kit``) reads frames and imports as always; a helper the script calls (``traceback``) gets
  what the script would.
* **Cleanups by C's record** (``bpy_angelo_guard_confine_owned.cc``, ``owned_paths``). A file or folder Angelo makes
  (a private folder, the CLI socket and its endpoint record, the engine link's record) is recorded in C by its
  identity (device and inode; on Windows the volume serial and file index, never by its path, and never through a
  link or junction), with a descriptor held on it where the system allows, so the identity can't pass to another file. While
  a script runs, only a path inside Angelo's private folders made since the script began is recorded (its birth time;
  a private folder ``private_roots.allocate`` makes is made by C itself and recorded at once, ``make_owned_dir``, so a
  file system that keeps no birth time loses nothing; a ``TemporaryDirectory`` there is left in place). A cleanup (at
  exit, a temporary folder's removal, the stage bundles' folders) removes only a recorded path still naming that
  file, whatever its Python state names; a ``TemporaryDirectory`` removes the folder it made (its finalizer's name).
  Every destructive step an exit or cleanup path reaches goes that way (piece 9 (ii); ``test_native_destructive_sinks``
  reads the add-on's source from its ``atexit``, ``weakref.finalize`` and cleanup methods and fails on one that
  doesn't, or isn't listed with why its target can't come from a script). Folders Angelo made are removed with
  ``owned_paths.remove_tree``; a file Angelo keeps and saves over or removes (the goals and usage records, a chat's
  pending messages) goes through ``owned_replace``, which replaces or removes an existing file only while it is on
  C's record, records the file again after each save, and records a store's file as Angelo opens it only when C
  keeps it inside Angelo's private folders; the headless working copy is removed only while it is the copy the
  launcher made; a checkpoint store's entry is one name inside its anchored folder (``entry_name``); a chat lease
  closes only the lease file's own descriptor; and Windows' ``TerminateProcess`` of a sign-in child's console
  members reaches only this process's descendants (``process_ours``).
* **Every write by C's facts** (``bpy_angelo_guard_confine_writable.cc``, ``owned_paths.may_write``/
  ``require_writable``; piece 10). Not only cleanups: every write, replace or removal Angelo's own code makes to a
  path that comes from Python state (a new file's creation included: one planted at a name from state could be
  code an autostart folder runs), whatever runs it (an operator the person runs, a timer, a job's completion, an
  engine link's handler, a thread, an exit path), and Blender's own writers it calls (``save_as_mainfile``, an
  exporter, an image's save, ``libraries.write``, and the writers of Blender's own Python modules handed a path:
  ``_bpy._angelo_saved.write_only``, ``imbuf.write``, audaspace's ``Sound.write``), asks C first. The parallel
  merge's two such writes need no check of their own, by construction: its privacy check's copy is the literal
  ``check.blend`` in a folder the check itself makes (``private_roots.TemporaryDirectory``), and a sound packed again
  as FLAC is written into a scratch folder ``require_writable`` checked, under ``sound-`` and the sound's own last
  name in word characters and ``-`` only (``worker_merge_media._flac_stem``): never a separator, ``..``, a drive, a
  stream or a Windows device name, whatever a script set the sound's path to. C allows a path inside Angelo's private folders
  (resolved first: a link there leads nowhere else), inside a file or folder Angelo made or adopted (owned.cc, by
  identity), or one the person named (``bpy_angelo_guard_confine_vouches.cc``; the review of piece 10). Nothing
  named is kept past the process, and nothing is wider than what was named:

  - **a file** Angelo's code vouched for where it entered from the person or a program they connected (a settings
    file Connect found, an engine asset the editor named, a worker's part, the CLI Angelo found, the headless
    child's source, working copy and continued output and each output it claims), never from a confined thread nor
    with a script's frame on the stack. A folder is never vouched for the session;
  - **one call's** file, or folder of Blender's own the call writes into whole (a settings import, its Undo), for
    that call only (``vouched_during``);
  - **the person's pick** in a file browser that saves (any operator whose browser saves: ``check_existing`` on by
    default; never an open, import or link) or that an Angelo operator opened, recorded by the window manager (never
    while a script runs, for a confirm a script drove, or in a browser a script's invoke opened: its handler is
    tagged and ``fileselect_add`` is refused to a script), for that operator's run only, and only for an unconfined
    caller: a handler or timer a script left, running inside that operator, an agent's modal, or a writer whose job
    began in a script region never writes over it. The window manager drops it when the operator's execute returns,
    when the browser is cancelled, and when its window refuses to close;
  - **a path the person typed** (or browsed to) in a file or folder property (a render's output, an export folder):
    that file, or files directly in that folder, for the writer that reads that property only (C's writers pass
    the property they read), until anything else sets the property (Python's set forgets it);
  - **a folder named before any script region** (``register_writable``): Blender's cache updates folder, a library
    folder the environment names.

  The open document's folder is never writable to Angelo's writers: a script chooses where the document is saved.
  It anchors only the file tools' own descriptors (``checkpoint_filesystem``, ``document_folder``), which write
  inside it as the person's file tools. Angelo's tools' own folders in it (``assets/downloads``, an engine
  project's ``.angelo``) are made by C part by part below that anchor (``make_dir_beside``), or adopted by identity
  when an earlier session made them (never through a link, never a folder a script made). A temporary or output beside a named file is made by C
  (``create_beside``: exclusively, recorded as Angelo's) and renamed onto it. A file with other names (hard links)
  is written only when it is the very file (by identity) Angelo made or the person named, unless it lies in
  Angelo's sealed private folders: a script could link a file of the person's into a folder Angelo writes. Every
  Blender writer operator (one whose file browser saves: ``check_existing`` on by default) that Angelo's own code
  calls is checked at the operator call, for every path property it is given; saving the open document over itself
  stays allowed.

  **Output paths in data: the data decides, not the place.** Blender's writers build an output path from data
  when they write (a render's pattern, its views' suffixes, a bake's and a cache's names, a node's folder and file
  name, an image's or a text's file), long after any call was judged, and unconfined. A confined thread may still
  point a path anywhere it may read (relinking a texture is ordinary agent work; an operator's own arguments are
  judged by what the operator does). Instead, what it sets is tagged where it is set
  (``bpy_angelo_guard_confine_tagged.cc``): every property, from Python or from C on its thread
  (``RNA_property_*_set``; any type, no list), by its data-block's session id, the data's RNA type (and each type it
  refines) and the property, so a strip's file is not the render's and a node's name is not a File Output item's;
  and every data-block a confined thread makes or copies, whose data is all the script's. Data a confined open, append or link
  reads is the script's only when its .blend is script-made (in C's change record, or where Angelo's writers may
  write); a person's own project a script opens stays the person's, as if they had opened it: who authored the
  data decides, not who pressed open. A writer judges a write when any input its path is built from is tagged, its owner is the
  script's, or its job started inside a script region (``BPY_angelo_guard_script_set``,
  ``_script_originated``; the writers' call sites: ``bpy_angelo_guard_confine_writers.cc``), and only then: a
  person's job on the person's own data is never judged, whatever the agent does meanwhile. It then asks
  ``BPY_angelo_guard_output_allowed`` with the path it is about to write and the property it read it from: a place
  C allows, what the person typed into that very property (a typed pattern covers its own expansions: ``#`` frames,
  ``{name}`` values, and frame digits and an extension after its last part, never another name or folder), or a NEW
  file below the folder of the document the person last opened or saved themselves (never one a script's turn
  opened or saved) or the folder of a file the person named, which C makes there (each missing
  folder too, following no link, named as written) and records as Angelo's, so its own later frames are rewritten,
  never an existing file of the person's. A string that is no path (a name, a view's suffix) may be joined into
  one, so a confined thread can't set one that leads out of where it is joined (a ``..`` part, a leading separator,
  a drive, a share); a ``/`` inside a name only goes down, and its write is judged anyway. ``Text.filepath`` is a
  file path property. Records last for the process. A templated output Angelo's code renders is judged by what it
  expands to now. Angelo's own folders in such places (a document's ``assets/downloads``, an engine project's
  ``.angelo``) are made by C too (``make_dir_beside``: fixed names below an accepted anchor, each part made
  following no link, or an existing real folder no script made adopted). A script can't change an open file
  browser's settings (``params``: its folder and file name) or the operator it answers for, call any of its
  functions (its active file, its selection: what a multi-file confirm answers for), nor run any operator with an
  open file browser as its context (a context override); it can still move the browser's folder through its
  bookmark, system and recent folder lists (see "Known limits in 2.0.0"). A browser a script-started operator's
  modal opens is the script's (refused, and tagged, by the one test popups use). A name with a parent folder (``..``) is refused, and a folder named whole may not be the file
  system's root, a top-level folder, the home folder or one above it. A path whose last part is a link is never
  writable, and before any script region began (startup's migrations and carry-over) or in a process with no
  confinement (a plain child) C allows any path: no script could
  have changed state then. A shared save (``shared_replace``, ``studio_state.atomic_json``) asks for its target
  itself; a cleanup asks ``may_write`` and skips what C refuses (it never raises). ``test_native_write_sinks`` reads
  every function of the add-on (``write_sinks``), classifies each target (fixed, a fresh unique name in a fixed or
  created folder, a parameter followed to its callers, or Python state: a unique name in a folder from state is
  state). Fixed is a literal or immutable value bound once and never rebound, or a path from a fixed source; a
  dynamic dispatch onto a sink is listed with why (``DYNAMIC``) or followed (a callable default, ``x or sink``, a
  choice in place, a thread target); folders made, modes, owners and times, archives, and ``os.open`` flags held in
  a name are sinks. It reads the same under any hash seed, and fails on a state target its function doesn't check
  or list with
  why (the startup carry-over, Undo of a settings import, a child's own private folder, saving the open document to
  its own path, and the others named there).
* **Tracers** (``bpy_angelo_guard_confine_trace_hooks.cc``). ``threading.settrace``/``setprofile`` (or a write to
  ``threading._trace_hook``/``_profile_hook``) only store a hook that every thread ``threading`` starts installs in
  itself, outside any region. C keeps the values Angelo's own code last gave the two names, puts them back at every
  thread start and at a region's end, and refuses a new thread's ``sys.settrace``/``setprofile`` from
  ``Thread._bootstrap_inner`` while the name holds another value (a hook stored between a start and the thread's
  first step). ``sys.settrace``/``setprofile`` themselves stay refused to a script, and ``frame.clear()`` on a frame
  it may not read (an Angelo generator's) is refused.
* **Callbacks with other threads' data** (``bpy_angelo_guard_confine_callbacks.cc``). ``sys.excepthook``,
  ``sys.unraisablehook``, ``threading.excepthook`` and an asyncio loop's task factory and exception handler are
  handed what another thread does (its exception and traceback, every coroutine a loop runs and its result). A
  script's value there, even one that runs no code of its own (``list.append`` on its list), never sees Angelo's:
  C keeps the value Angelo's own code last gave each slot (the dict watcher for the module names; for a loop, the C
  data descriptor each of ``BaseEventLoop``'s two names becomes once ``asyncio`` is loaded, installed at a region's
  start), and a caller that isn't confined calls only that value or Python's default. The interpreter's call of a
  ``sys`` hook is refused at its audit event after Angelo's own has run; each thread's ``_invoke_excepthook``
  becomes C's once a script sets ``threading.excepthook`` (every live thread then, every thread that starts while it
  holds the script's value); a loop's descriptor returns Angelo's value to code that isn't confined and the latest
  to confined code (an instance's namespace never shadows it), so asyncio's own methods run unchanged. A loop's
  slots are no module's names, so this also covers the time after the script: no journal puts
  a loop's attributes back. A script's own thread, error and loop reach what it set.
* **Signals.** A script signals nothing (``os.kill``, ``os.killpg``). Angelo's own code signals only itself and its
  descendants (the system's parent links, read in C), never every process or its own group, whatever PID its state
  holds; asking whether a process exists (signal 0) is free.
* **Programs Angelo starts unconfined, and what they read.** A model runtime's program is Angelo's agent runtime,
  the file the person chose in Preferences, or the CLI found by its fixed name, read at each launch
  (``cli_runtime.chosen``: a module binding the main thread refreshes from Preferences, which the journal puts back);
  never a path held on a provider or in its settings, which live as long as the chat. Its environment is built again
  for every start, never kept from an earlier one. Its profile folder (``CODEX_HOME``, ``CLAUDE_CONFIG_DIR``: the
  configuration there names programs the runtime runs, and its sign-in) must lie in Angelo's private folders, asked
  of C before anything is made in it (``storage.private_profile``). A custom endpoint's key program is read from the
  machine policy when it runs (``endpoint_keys.policy_command``), never from the endpoint Angelo holds, and one a
  script wrote is never run. Every launch also asks C (``owned_paths.require_runnable``).

Writers that read their path from data
--------------------------------------

Blender's writers build the path they write from data, at the moment they write: a render's output pattern with its
templates (``{scene_name}``, ``{camera_name}``), frame numbers, extension and view suffixes; a File Output node's folder,
file name and items; a cache's folder and name; a bake's folder and the names of the materials and objects it bakes;
the file an image or a text names when it is saved. An operator's path is judged once, when it is called, and much of
that data is no path property at all, so a script that set the data (before a job, or while one runs: a timer, handler
or generator step it left) would choose where Angelo's unconfined writer writes (the delta review of piece 10 and
its re-read of f9f24cc6ffd, round 3: "the data decides, not the place").

* **Each writer declares its inputs.** Every such writer names the data its path is built from: properties of data
  in data-blocks, each with the RNA type that defines it (``BLI_angelo_writers.hh``: ``angelo_writers::Input``; the
  render, File Output, multires and geometry-nodes bake lists are in ``BKE_angelo_writer_inputs.hh``). The first
  input is the tag: the property the path itself is read from. A declared type and property no RNA type has is
  judged on every write (over-judging, logged), never let through. A mesh's external multires displacements (written
  by every save and autosave), Save Sequence's frames and a packed bake's unpacking (which also removes the bake's
  folders) are writers like the others. ``c_write_sinks`` lists every place C and C++ open a file for writing:
  each asks a gate before it writes, in its function or its named caller, or says why its path is never from data
  (``test_native_c_write_sinks``).
* **When a write is judged.** When a confined script set any input, or the data-block holding it was made, copied,
  appended or linked inside a script region (``BPY_angelo_guard_script_set``, the v200 lane's record of what scripts
  set), or when the writer's job began inside a script region (on a thread running agent code, or while a region
  ran: a script's own render, bake or viewport render, and every thread its job hands work to). Otherwise nothing
  changes: a person's render or bake of their own data writes everything while the agent works, whatever regions run
  meanwhile, and data a script set elsewhere (a relinked texture) never touches their outputs. A WM job's run begins
  where its thread starts (``WM_jobs_start``); a writer run synchronously begins its own; the viewport render holds one
  for its run and puts it on every step's thread. A task a thread pushes to a task pool runs under the pusher's job,
  and a task pushed with no job runs with none, whichever thread runs it (``task_pool.cc``), so the depsgraph's evaluation during a bake and a render's write pool write for the job that
  made them, and a person's job is never judged because an agent's job runs beside it. A write whose job is unknown
  (agent code writing for itself, a thread no job made) is judged by its inputs alone; a path agent code passed
  itself was judged where it passed it, and the file gate judges that write.
* **What a judged write must be.** One Angelo's output may take for the property it was read from
  (``BPY_angelo_guard_output_allowed`` with the tag): Angelo's own folders, what it made, what the person typed into
  that very property, or a new file below an anchor C accepts. The tag names the data the person edits: a writer
  that runs on an evaluated copy (a cache, a fluid domain, a Dynamic Paint surface, a proxy build's copy of a strip)
  gives the original's, where their typed value is kept. A folder a writer fills without asking per file is judged by
  ``BPY_angelo_guard_folder_allowed``; Mantaflow writes its own files below a fluid cache's folder, so that folder is
  what is judged. Refused, the file is not written, the job reports the refusal's text
  and stops where the writer stops on a failed write.
* **Cost.** A write whose inputs nobody tagged costs one lookup per input (a hash set by the data-block's session
  uid and the property's name) and no file system call; the path is resolved only for a judged write.
* **The writers that ask, and their inputs:**

  ===============================================  ================================================================
  Writer                                           Inputs (the tag first)
  ===============================================  ================================================================
  render frames and stills, placeholders,          scene ``filepath``, ``use_file_extension``, ``file_format``,
  movies (``pipeline.cc``, ``image_save.cc``,      ``media_type``, ``format``, views' ``file_suffix``,
  ``movie_write.cc``), the viewport render         ``use_multiview``, ``views_format``; with templates the scene's
  (``render_opengl.cc``)                           ``name``, ``camera``, resolution and frame rate, and the
                                                   ``name`` of its camera and of every camera a marker binds
  File Output node (``FileOutput::save``)          node ``directory``, ``file_name``, ``name``, item names, format;
                                                   the scene's view suffixes and template values
  point caches: frames, clears, renames            cache ``filepath``, ``name``, ``index``, ``use_external``,
  (``pointcache.cc``)                              ``use_disk_cache``, ``use_library_path``, the owner's ``name``
  geometry-nodes bakes: meta, blobs, removal       modifier ``bake_directory``, bake ``directory``,
  (``object_bake_simulation.cc``,                  ``use_custom_path``, ``bake_target``, names
  ``bake_items_serialize.cc``)
  fluid cache folders (``fluid.cc``,               domain ``cache_directory`` and file formats
  ``physics_fluid.cc``)
  ocean bake frames (``ocean.cc``)                 modifier ``filepath``
  Dynamic Paint sequences (``dynamicpaint.cc``)    surface ``image_output_path``, ``output_name_a``,
                                                   ``output_name_b``, ``image_fileformat``
  external bake images (``object_bake_api.cc``)    scene bake ``filepath``, ``use_split_materials``,
                                                   ``use_automatic_name``, the object's and material's ``name``
  sequencer proxies, image and movie               proxy ``directory`` and ``filepath``, the strip's folder and
  (``proxy.cc``, ``movie_proxy_indexer.cc``)       file names, the editing's proxy storage
  clip proxies (``movieclip.cc``,                  clip proxy ``directory``, ``filepath``, ``use_proxy_custom_directory``
  ``clip_ops.cc``)
  image save, Save All, the close dialog's         image ``filepath``, ``filepath_raw``, ``source``, ``name``, the
  "save modified images" (``BKE_image_save``)      scene's view suffixes
  text save (``text_ops.cc``)                      text ``filepath``, ``name``
  unpack to original or local files                the data-block's ``filepath``, ``filepath_raw``, ``name``
  (``packedFile.cc``)
  ===============================================  ================================================================

  FFmpeg opens its files itself, past Blender's file operations: the movie writer and the proxy builder ask the
  confinement's file gate before ``avio_open`` as Blender's own file operations do.
* **Writers that need no gate.** Exporter jobs (Alembic, USD), sound mixdown and every operator whose path is its own
  property write that property's value, judged at the call. The sequencer's disk cache and autosave write to folders
  from Preferences, which scripts can't change. No bundled Python code writes a file from data in a handler, so none
  needs a Python-callable gate. How that was found: every module under ``scripts/`` (``addons_core``, ``startup``,
  ``modules``, ``freestyle``) that adds a ``bpy.app.handlers`` callback, by a list's ``append``/``insert`` or through
  ``getattr(bpy.app.handlers, ...)``, was read for file writes (``open`` for writing, ``write_bytes``/``write_text``,
  ``shutil`` copies and moves, ``os.replace``/``rename``, ``Image.save``/``save_render``, save and export operators).
  Of the sixteen outside Angelo's add-on, none writes a file in its callback (``addon_utils``' extension-repository
  callbacks write only into folders from Preferences). The Freestyle SVG exporter is an extension, not bundled, and
  Angelo's own add-on is read by ``test_native_write_sinks``. Bundled Python that writes outside a handler writes its
  operator's own path property (judged at the call) or saves through ``Image.save``/``save_render``, which run
  ``BKE_image_save``'s gate with the image's inputs; ``image.project_edit`` names its file after an image and an
  object but always picks a name no file has. A Python writer an add-on the person installs runs in a handler as
  their own code.

What is not covered (residual risk)
-----------------------------------

* **Secrets Angelo holds in memory leave through the script's output.** A script can read what Angelo's Python
  holds (a provider key loaded from its record, an access token, a local endpoint's key) and print or return it:
  the result goes to the model provider as the tool result. Refusing the network does not stop this. It is
  inherent while keys live in the Blender process; the helper-process design moves them out
  (helper-process-design-2026-11-15).
* **Objects shared in place.** The journal sees namespaces, classes, registries and the lists named above; an
  installed object's own attributes and contents (a third-party add-on's settings object, an instance's list) are not
  watched, so a script can change them in place, in the same process. Angelo's modules, classes and instances stay
  reachable too (``sys.modules``, a registered operator class, a bound method in a handler list, a thread's target),
  and so does the state they hold: ``threading.enumerate()`` hands a script Angelo's running threads, their targets
  and the arguments they were started with (v200-piece7b's finding), and a value it rebinds in place during its
  region reaches what Angelo's threads read then, not only what they return. That state can't make Angelo write, replace or delete a file outside its own
  folders, what it made and what was vouched for, signal other programs, or send its credentials elsewhere, from any
  entry point: the sinks check C's facts ("Every write by C's facts"). Inside a folder Angelo made or adopted (an
  engine project's working folder, the downloads folder, the updates folder) a script that changed Angelo's state
  can aim one of Angelo's own writes at another file in that same folder, never at one with another name elsewhere.
  It can make Angelo misbehave or show wrong state until it restarts. Full isolation (agent scripts in a helper
  process, where nothing of
  Angelo's is in reach) is planned for 2.1.0.
* **Writers judged by their data (above).** Mantaflow writes its own files below the cache folder: the folder is
  judged, not each file. Python writers (an add-on's code in a render handler, such as the Freestyle SVG exporter) are
  not Blender's C writers and are not asked. Only RNA property sets are recorded (with the data-blocks a region
  made); the routes this leaves open are listed under "Known limits in 2.0.0" below. A writer thread no job made and
  no task pushed (a raw thread a library starts) writes with no job: its writes are judged by their inputs only.
  Python writers an add-on the person installed runs (a render handler of theirs) are not asked.
* **Angelo's code acting on what a script gave it.** Angelo's code a script calls (``kit``) may look up its own state
  by a name the script supplied; it acts as Angelo's code does, with Angelo's checks.
* **Import resolution the path finder doesn't do.** A module a script's region gained through a loader other than
  the path finder's, the interpreter's, or a finder the region found on ``sys.meta_path`` loading it itself (a zip
  import, setuptools' ``distutils`` shim, an editable install's finder) doesn't resolve on the file system without
  running that finder: it is dropped at the region's end and imported again, outside the script, when next needed.
* **Native code already loaded**: hostile code in an extension module loaded before the script. (The pipeline
  libraries are gated whenever they load, before a script or during one.)
* **Libraries' own I/O for paths no gate saw** (OpenVDB, Alembic, ffmpeg writers inside Blender). Their paths come
  from operator or RNA properties, which are checked; USD's go through the confined resolver.
* **An existing callable placed in an object Angelo holds**: a builtin such as ``exec`` assigned to an attribute of
  an Angelo object (not a module, a registry or a class: those are journaled) stays, and runs with the arguments
  Angelo gives it. Script-made callables there are confined (above).
* **Installed code's own objects built from a script's values**: a callable installed code made on the script's
  behalf (a cache's bound method over an object it built from the script's arguments) stays unconfined. It runs
  installed code with those values, as any installed function the script calls does; script code it holds is
  confined.
* **Data a script changes in place** inside an Angelo object it reaches without a module (see "Objects shared in
  place" above): grant decisions read the C ledger (:doc:`grant_operators`), and what Angelo destroys, signals or
  sends its credentials to is checked against C's facts ("Angelo's own code"; every write, replace and removal by
  "Every write by C's facts"), but other behavior can follow the changed data until restart.
* **Defaults and attributes of Angelo's functions.** A script reading ``__kwdefaults__`` of a function that isn't
  its own gets a copy (changing a keyword default in place reaches nothing; setting it is judged as any attribute);
  a mutable value inside a ``__defaults__`` tuple, or another attribute an Angelo function holds, is shared in place
  ("Objects shared in place" above).
* **A GPU-less machine.** The escape checks render and composite on the CPU (Cycles, and the compositor, whose own
  default device is the GPU): on a Windows machine with no GPU, a background render that started a GPU context (a
  Workbench render on 9dcfb883ebb, a GPU composite on d106e84e26d) ended the process. GPU-less
  machines are out of scope by policy; 2.1.0's capability gate is where that crash is handled.
* **Where C's write check stops.** On Windows a path is named as the file system spells it, every junction and
  link above resolved (``winpath.cc``), and a path with a junction or link in any component, or naming an NTFS
  stream (``name:stream``), is refused outside Angelo's sealed private folders; a confined script makes no link or
  junction at all. Every judge reads a Windows path in the one spelling Win32 opens (``windows_plain``): a
  ``\\?\`` path counts only when its plain spelling is the very same file (no trailing dot or space, no ``.`` or
  ``..``), and NT (``\??\``), device, root-relative and drive-relative spellings are refused; the confinement's own
  gate refuses ``\??\`` too. Keys compare case-insensitively (NTFS' default): in a folder made case-sensitive,
  two names differing only in case count as one. A store's held folder
  (the checkpoint store's and a job's staging descriptors) is checked when anchored: an object a script reached
  could hold another descriptor in its place. Code outside Angelo's package (another add-on) writes as it always
  did. The write audit reads the source: a target reached through dynamic dispatch it can't follow is not checked.
* **A script-made callable held where it can't be replaced** (a tuple or another immutable holder Angelo keeps):
  counted in ``confinement_restored``.
* **Work a script left suspended** runs each later step as a region (above). If ``sys.monitoring`` has no free tool
  id (a debugger and a profiler both attached), it is closed at the region's end instead (it can't run again). An
  installed generator a script left (``os.walk``) is not watched: its steps run installed code (next item but two).
* **While a script runs, Angelo's other threads see its in-region changes**: a rebinding in an installed module is
  journaled and goes back when the region ends, not before. Code the script put there runs confined whoever calls
  it, but what it returns is used by the caller (a rebound function that returns a proxy address, read by a request
  Angelo makes on another thread during the script).
* **Data a script leaves in installed modules**: by the journal's rule, data a script sets stays (a new value that
  is not code), and so do entries it adds in place to a list Angelo's Python holds (the warning filters inside a
  region; a ``gc.callbacks`` entry, whose callable runs confined when it is the script's). The import system's
  state is the exception (item 11): rebound, it goes back exactly; changed in place, the sweep puts it back at a
  script's end, but a step of suspended work skips the sweep, so in-place changes can remain until the next
  script's end. What such state can steer an import to is refused when it is a file a script wrote, in this
  session or an earlier one, or a rename or copy of one; a file a person later replaces at a path a script wrote
  stays refused to Angelo's own imports, until the person removes it or forgets the folder in Preferences.
* **The record of what scripts wrote is bounded**: a script's file whose content record was among the oldest past
  the newest 8,192 is known by its path only, and a copy of it elsewhere is not. A file the person writes with
  exactly the content of one a script wrote (outside installed folders) is the script's until the person forgets
  that folder. A file a script wrote that another thread of Angelo's renames while the script's region still
  runs is known by its old path only (identity and content are taken when the region ends).
* **A library that sets an environment variable as it loads is refused in a script** (USD's ``usdGenSchema``, a
  schema generator: its variable would change USD's prim definitions for the whole process). The refusal names it a
  developer tool to run outside Angelo; such tools are not scene work. (``usdGenSchema`` also needs ``jinja2``,
  which Angelo doesn't ship.)
* **The environment view reads the process's environment with the interpreter lock held**; native code that sets a
  variable on another thread at the same moment races the read, as it races CPython's own use of it.
* **A ``tempfile.TemporaryDirectory`` a script makes** is removed when it is collected (confined), not at interpreter
  exit: its finalizer, like every finalizer a script registers, never runs at exit.
* **Installed callables with a script's arguments**: a callable installed code made on the script's behalf and keeps
  in its own state (a bound method of an object it built from the script's values) runs unconfined with those
  values, as any installed function the script calls does. What the script itself made from installed parts (a
  ``partial``, a ``weakref.finalize``, an ``atexit`` or thread-exit callable) is confined at the region's end.
* **Process state another thread of Angelo's changes while a script runs** is put back with the script's at its end.
* **Preferences**: a script can't change any, by design (a GPU device, a theme). It asks the person.
* **A driver's Python in a linked library** stays in that library file; it runs confined wherever it runs.
* **Drivers' Python is not judged by the trust list.** It always runs confined, whoever wrote it and however it
  arrived (its code is ``<bpy driver>``, and its first evaluation arms the checks), so it can do nothing a script
  can't; and driver Python is common in rigs, where asking for every expression would break files that work today.
  A driver a script set or brought in keeps its expression but not its Python until the person edits it (above).
* **The person's consent is theirs to give**: a script can open a file whose scripts are held back, so the auto-run
  warning appears; what the person allows there runs as theirs.
* **Trusted content runs as trusted**, whatever file it is in and whatever data it reads: a text the person
  trusted that executes another text's content (or a file's) runs that too. The list holds digests, not files.
* **Library overrides** (``BKE_blendfile_override``) make local copies of data already linked through the link
  function (entry rules above): a text can't be overridden, a style module's text stays linked (the trust list
  judges it at render), and an overridden driver's Python runs confined.
* **A local pipe a script makes for itself** (one instance, a fresh name) could be connected to by another local
  program in the moment before the process connects to it; the process's own connection then fails.
* **Undo** restores texts through the same read (their mark is judged again by digest) and runs no Python itself;
  the background suite exercises the read and the execution gate, not the undo key in the UI. An **asset drop**
  goes through the same append and link functions as ``bpy.data.libraries.load``, which the suite drives.
* **OSL shader scripts** in a project are not Python; Cycles compiles them through a gated path entry.
* **The downloads of an unsaved document** are a tool-output folder: read, not changed, by scripts
  (``tool_output_folders``; registering it fails loudly rather than leaving it out).
* **Threads that already exist**: Angelo's own pools and consumers run only ``guard_work`` items
  (``test_native_guard_work`` inventories them). An add-on's own pools are full trust.
* **Hard links another program made** to a private file.
* **A module a script reloads** gets back what it held at the region's end where the reload replaced code (a reload
  defines nothing: the script can seed the namespace it runs in), and so does a namespace a script had the import
  system's internals run an installed module's file into.
* **The Python stand-in** (host tests, a build without the C module) implements the path, program, network and
  journal rules, not provenance: its closures can be walked.

Known limits in 2.0.0
---------------------

2.0.0 confines what an agent script does directly: its own writes, links, child processes and picks, and the paths it
sets through RNA properties, which Blender's writers judge when they write (above). A few *indirect* routes remain,
where a script prepares data and a write the person starts later (a render, a bake, a save, a copy) writes the file,
unconfined; stock Blender has no confinement at all. The maintainer chose to ship 2.0.0 with these documented (the
fourth and fifth re-reads of piece 10, 2026-11-17). 2.1.0 runs agent scripts in a helper process, out of Angelo's
process, changing Blender's data only through requests C records in full; its design must show how each one below is
closed (the 2.1.0 backlog lists them). Until then, review what an agent changed (output paths, image and cache paths,
names, File Output nodes) before you render, bake or save a document an agent edited.

Routes a script can use itself (closed in 2.1.0):

* **With no Angelo window open, a script can overwrite your documents.** When an external app has Angelo run a
  script headless (no window), the script can write over your original document, the working copy Angelo edits and
  a resumed session's output through Blender's own writers whose path it sets (an image save, an unpack of packed
  data). These files are named for Angelo's own saves, and 2.0.0 does not yet tell those saves apart from a
  script's writers.
* **After you connect an AI app, a script can rewrite that app's MCP settings file.** Once Connect has written an
  app's settings (``~/.codex/config.toml``, Claude's or Cursor's MCP settings), an agent script can rewrite that
  file the same way, for example to add a server the app then starts. Disconnect, or check the file, if an agent
  ran scripts you did not review.
* **Other files Angelo vouched for its own use, the same way.** An engine asset whose editor opened it in Angelo
  (``engine_link_assets``) can be overwritten by a script's writers in the session that vouched it. The coding CLI
  Angelo found and runs (Codex, Claude Code: ``cli_runtime.find_executable``) is not: it is recorded to start only
  (``owned_paths.vouch_program``, C's ``Scope::Run``: its real file through any link, never a write vouch, never
  dropped by narrowing or the oldest-first trim). By that record C starts it only while it is that very file,
  unchanged since it was recorded (identity, size and its times), so a write that reached it some other way, and a
  file a script made, is refused until Angelo restarts (the break-it reviews of 29b385a6645 and 4be92cfb84f). A
  program in an installed folder or a folder on PATH starts by those rules, with no such check; gltfpack, for the
  web export's KTX2 textures, is recorded the same way.

Routes that need your later write:

* **Changes that are not RNA property sets are not tagged.** Only a property set on a confined thread tags data.
  RNA *functions* and operators change data without it: ``ID.rename()``, ``image.replace`` (an image's file and
  source), ``file.find_missing_files``, collection functions (``new``, ``remove``, ``move``), ``foreach_set``, ID
  custom properties, ``marker.camera_bind``, and ``driver_add``/``keyframe_insert`` (values the animation system
  writes later). A writer whose path is built from such data does not judge the write, so your later render, bake or
  image save can write where the script steered it, over an existing file too. Rule 4 (no ``..``, leading
  separator, drive or share in a name) applies to property sets only, not to ``ID.rename()``.
* **Tags do not travel with the data.** They last for the running session, on the data-block a script changed.
  After you save and reopen, revert or recover the file, or copy the data (Full Copy, duplicate, node copy and
  paste, append from your saved file), the values a script set count as yours.
* **A path you typed with a template accepts the template's values.** An output path you typed covers its own
  expansions: each ``{name}`` any one part, the last part extra digits and an extension. A script that renames the
  scene (``contract.pdf`` with file extensions off), sets a view's suffix or a project variable chooses which of
  your files it names. Project variables (``bpy.data.project`` variables, ``{blend_dir_lib}`` from a library's
  path) are not tagged at all.
* **Which File Output node runs, and its own name, are not inputs.** A script can unmute or link one of your
  dormant File Output nodes or switch the scene's compositor node group, and rename the node a ``{node_name}``
  template uses: your next render writes there.
* **Some data is tagged under another type than the writer asks about.** A set through ``ImagePackedFile.filepath``
  (unpack to the original location writes there) or through ``PointCacheItem`` (``point_cache.point_caches[i]``,
  the same cache) is not seen by those writers. A rigid-body world's cache is set through its scene while the cache
  names an object: a script-set external disk cache there is not judged (your bake can clear files named like it).
* **Asset library files.** Save Brush or Pose Asset writes, and Delete Asset deletes, the file an editable asset
  library's ``Library.filepath`` names, ungated: a script that repoints it chooses which file your save overwrites
  or your delete removes.
* **Writers not yet inventoried.** ``c_write_sinks`` scans the common file-writing calls only: ``.blend`` writes
  (``BLI_open_replacing``), renames and deletes, OpenEXR and OpenImageIO writes, the render texture cache
  (``blender_tx`` folders beside images), Cycles' compiled shaders beside their sources and the callers of the
  helpers it lists are not checked site by site.
* **Angelo's own writes below a folder it may write** are checked by folder: a file name from Angelo's state
  joined below it (a Windows device name, a stream) is not checked as a name.
* **A file browser's folder lists.** A script can set a browser's bookmark, system and recent folder entries
  (``bookmarks_active`` and the others move the open browser's folder; ``FileBrowserFSMenuEntry.path`` edits a
  bookmark), so your next confirm may land in a folder it chose.

* **A Save As path an agent filled in.** A file browser's path is the person's pick once they confirm it: an
  agent that set an operator's path before the browser opened (a property's default, the last-used path) chooses
  what the person confirms without reading it.
* **A geometry-nodes bake's default folder.** The object's and modifier's names are inputs only while the modifier
  has no bake folder; a bake or unpack that falls back to its default node folder otherwise is not judged by them.

Usability limits:

* **An image or text an agent loaded from your folder counts as the agent's.** Saving it to its own file is
  refused; Save As and pick the path (your pick in a file browser is yours).
* **After an agent opens your project, "render beside my file" works only after your own save** (or your own open):
  a document a script opened is no create-beside anchor until then.
* **A refused multires file on a mesh an agent made.** After Save External, each later save of the document whose
  external write is refused keeps the displacements inline in the .blend instead (nothing is lost), with only a
  line in the log saying so.
* **Animation evaluated while a script runs** on the main thread can mark your data as set by the script (an
  evaluated copy shares its original's id), so a later save or render of that data may be refused.

Cost and races: each confined property set takes one lock and records a string per base type (not yet measured
on set-heavy scripts; ``benchmark_confinement`` measures the common cases), and a writer's first check of a declared
input looks its type up in RNA from the job's thread, which can race an add-on registering on the main thread.

Results
-------

A script result still records ``execution_mode="trusted_unconfined"`` and ``confined=false`` (``trusted_scripts``):
these fields say the script is not sandboxed from the person's files and scene. ``confinement_restored`` lists what
its end put back.

Tests
-----

* ``test_native_script_confinement``: every attack class, in a fresh interpreter, against the Python stand-in and
  Angelo's real private-folder registry. It also pins that every native gate is wired.
* ``test_blender_confinement_attacks``: the same attacks against the C confinement in the app, and the module's
  surface.
* ``test_blender_script_confinement``: operators, timers, draw handlers, handlers, driver functions and load-time
  texts in the app, Angelo's access afterwards, and the cost.
* ``test_blender_confinement_escapes``: every trigger that runs code later × every carrier (break-it 2026-11-15),
  each payload reporting ``confined()``; class attributes, forged definitions, SQLite, saved load-time texts, held
  descriptors, the OS startup locations; ordinary code that must still run (``namedtuple`` defaults, ``asyncio``,
  ``unittest.mock``, a thread pool, sqlite, operator and property registration). Its inventory classifies every
  extension module the app bundles and every module built into it, and fails on one nobody classified.
* gtests: ``bpy_angelo_guard_confine_paths_test`` (aliases, descriptors, case, families) and
  ``bpy_angelo_guard_sha256_test`` (FIPS 180-4 vectors).

Cost
----

Measured at b7501fe4ac0 in the app on Linux (angelo-linux-build, not a shipping build), three arms alternated (the
candidate without the guard, the previous guard 72a40bf97f6, this guard), 4 rounds of 30 timed scripts after 5
untimed ones (10 after 2 for the two heavy workloads), approval outside the timing (``benchmark_confinement.py``).
Other lanes' work kept the load at 5–10 throughout; the arms alternate, so it fell on all three. Median / p95:

* ``x = 1`` (n=120): 1.40 / 1.42 ms with the guard, 1.37 / 1.40 ms with 72a40bf97f6, 0.01 / 0.01 ms without. About
  1.4 ms per script, fixed: the region's freeze, journal and leftover scan, the process-state and project snapshots,
  the sweeps, the digest.
* Twenty bevelled cubes with a material (n=120): 39.2 / 41.1 ms; 39.7 / 41.2 ms with 72a40bf97f6; 37.1 / 39.2 ms
  without (+6% median).
* A million-item list and a 200,000-entry dict (n=40), the allocator recorder's worst case: 104.9 / 105.7 ms;
  109.2 / 110.4 ms with 72a40bf97f6; 95.5 / 98.3 ms without (+9.8% median, +7.6% p95). Recording who made each
  block costs nothing measurable over 72a40bf97f6 (a per-thread cache of the running code's verdict).
* A 200,000-vertex bmesh grid, every vertex moved (n=40): 154.7 / 163.0 ms; 155.0 / 159.1 ms with 72a40bf97f6;
  152.6 / 162.3 ms without (+1.4% median).
* An ``open()`` with no script running: about 16 µs, as without the guard; while confined about 62 µs.

Suspended work as regions and code provenance (Devin break-it Phase C review, 2026-11-16, angelo-linux-build, not a
shipping build), measured against the previous guard ee5e77f8e11, four ABAB rounds (``benchmark_confinement.py``;
load 1–4): per-script medians unchanged within noise (``x = 1`` 1.45 vs 1.56 ms, the modeling script 39.4 vs
39.5 ms, the allocation script 105.0 vs 108.1 ms, bmesh 155.1 vs 156.5 ms; generator- and coroutine-heavy scripts
14.9 vs 15.1 ms and 7.96 vs 7.96 ms; ``import numpy`` 1.53 vs 1.63 ms). New costs: a step of a generator a script
left, resumed outside it, about 0.03 ms (a region per step; nothing before); and ``io.open_code`` for Angelo's own
imports once a script has run, about 38 µs instead of 9 µs (the path judged against what scripts wrote).

Definitions judged by the import's own parts, the persisted record of what scripts change and the coordinator's
re-read of pieces 2+3 (2026-11-16, angelo-linux-build, not a shipping build), against the previous guard
c0b6d0a0486, four ABAB rounds (``benchmark_confinement.py``; load 4–11 from another lane's runs, alternating, so on
both): medians unchanged within noise (``x = 1`` 1.455 vs 1.454 ms, the modeling script 39.64 vs 39.66 ms, the
allocation script 103.1 vs 105.3 ms, bmesh 153.5 vs 154.7 ms, generators 14.66 vs 14.78 ms, coroutines 7.88 vs
7.94 ms, ``import numpy`` 1.53 vs 1.53 ms, a leftover generator step 0.030 vs 0.031 ms, ``io.open_code`` 38.8 vs
38.4 µs).

The record of what scripts wrote by file, identity and content (guard piece 7b, 2026-11-16, angelo-lane-upstream, not
a shipping build), against 8fefa49f222 built the same way, six ABAB rounds (``benchmark_confinement.py``; load 1–11,
alternating): medians unchanged within noise (``x = 1`` 1.464 vs 1.432 ms, the modeling script 39.11 vs 38.96 ms, the
allocation script 104.5 vs 106.2 ms, bmesh 153.9 vs 153.6 ms, generators 14.82 vs 14.40 ms, coroutines 7.80 vs
7.81 ms, ``import numpy`` 1.53 vs 1.50 ms, a leftover generator step 0.030 vs 0.030 ms, Angelo's own
``io.open_code`` 42.5 vs 42.9 µs); window start 3.25–3.28 s vs 3.26–3.27 s. What it adds by design: a script that
writes one small file 1.62 vs 1.62 ms, a hundred files 12.4 vs 12.5 ms, a 4 MB file 2.56 vs 3.05 ms (its digest
reads 128 KiB at the region's end); Angelo loading a person's file outside installed folders 33.8 vs 36.0 µs, and
70 µs when a file a script wrote has its exact size (the digest).

Gained modules by their loader, logging restored at every region end, Angelo's package and frames closed to scripts,
and its cleanups, signals and credentials checked against C's records (guard piece 9 with 7b, 2026-11-16,
angelo-linux-build, not a shipping build), at dcad1e85c85 against piece 8 (3a91cc89f76) built the same way, four ABAB
rounds (``benchmark_confinement.py``; load 6–14 from another lane's runs, alternating, so on both): medians unchanged
within noise (``x = 1`` 1.499 vs 1.483 ms, the modeling script 39.76 vs 39.65 ms, the allocation script 104.6 vs
105.4 ms, bmesh 151.9 vs 155.2 ms, generators 15.04 vs 14.74 ms, coroutines 7.95 vs 7.99 ms, ``import numpy`` 1.57
vs 1.55 ms, Angelo's own ``io.open_code`` 39.0 vs 38.5 µs); on the piece's first build (the region change below
doesn't touch them), window start 3.30–3.32 s vs 3.29–3.32 s and importing the add-on 148.5 vs 147.9 ms. A step of a generator a script left costs 0.054 vs 0.037 ms (three runs of 30,000 steps:
0.055–0.058 vs 0.038–0.039 ms): each region now also copies every loaded package's search list and Angelo's
logging state, and settles both twice. The first build of this piece copied the search lists by walking all 1,334
loaded modules every region (0.21 ms a step, ``x = 1`` 1.67 ms); the packages are now found again only when
``sys.modules`` or a ``__path__`` changes.

Window start: the install replaces every other reference to a wrapped entry point in one walk over the loaded
modules for all of them (it walked them once per entry point, about 0.9 s of window start on Linux). Measured on
angelo-linux-build (not a shipping build), the previous guard c0b6d0a0486, this one, and the same build with the
install compiled out (CMake ``ANGELO_GUARD_DIAG_NO_INSTALL``, a diagnostic option never set in a release:
``test_native_guard_diag_define``; such a build refuses every agent script, a release configure refuses the option,
and every packaging step refuses a binary with its marker), alternated: ``install_wrappers`` 882–892 ms → 8–14 ms (5 rounds each, the same
241 references replaced); the window's first frame under callgrind 8.78e10 → 7.80e10 instructions, 0.36% over the
build without the install (7.77e10), ``guard_install_wrappers`` itself 8.21e9 → 2.39e7; wall time to the first
frame under Xvfb 4.19 s (all three rounds) → 3.30–4.22 s, 3.22–3.82 s without the install.

The reviewer's third read (2026-11-16: names resolved to their installed origin, ``finalize`` records judged at the
sink, seeding counted only for placements an import could call, definitions in the import's own package), against
8fefa49f222, four ABAB rounds on angelo-linux-build (load 1–10, alternating): medians within noise (``x = 1``
1.464 vs 1.456 ms, the modeling script 39.56 vs 39.49 ms, the allocation script 104.9 vs 103.4 ms, bmesh 154.6 vs
153.5 ms, generators 14.78 vs 14.71 ms, coroutines 7.90 vs 7.84 ms, ``import numpy`` 1.53 vs 1.52 ms, a leftover
generator step 0.031 vs 0.030 ms, ``io.open_code`` 38.5 vs 38.8 µs); wall time to the first frame under Xvfb
3.26–3.28 s vs 3.25–3.31 s (the install unchanged).

The reviewer's fourth read (2026-11-16: nothing of the script's runs unjournaled as a region settles, origins
resolved on the file system, ``finalize`` records judged field by field, namespaces put back data included, the
import system's search state and the exit list per region, Angelo's own I/O checked at use), against ae171b3278a,
four ABAB rounds on angelo-linux-build (load 1–12, alternating): medians within noise (``x = 1`` 1.48 vs 1.46 ms,
the modeling script 39.55 vs 39.73 ms, the allocation script 104.3 vs 105.2 ms, bmesh 156.2 vs 154.8 ms, generators
14.61 vs 15.16 ms, coroutines 7.98 vs 7.91 ms, ``import numpy`` 1.55 vs 1.54 ms, ``io.open_code`` 38.6 vs 38.5 µs);
a step of a generator the script left 0.038 vs 0.031 ms (the per-region snapshot of the import system's search state);
a confined first import of seven large standard-library packages (42 modules gained, six launches each, alternated)
148 vs 133 ms, about 0.36 ms per module gained (each resolved on the file system and its namespace journaled, data
included); wall time to the first frame under Xvfb 3.28–3.31 s vs 3.27–3.30 s.
