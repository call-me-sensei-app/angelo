# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Machine inputs a native suite needs from outside the app under test: a Godot editor, pinned held-out models, a
hardware GPU, a window that can be captured.

A suite names its inputs in the manifest (``Suite.inputs``). Before planning, ``run_native_suites`` resolves each one
once per run: from its variable when the caller set it, else from what the machine or the checkout provides (Godot's
default install; the models ``godot_physics_e2e/heldout_assets.json`` pins, fetched and sha256-checked into a cache
beside the run). A resolved input reaches the suite as that variable. An input this machine cannot provide makes the
suite a skip whose reason says what is missing and how to provide it: never a failure that reads as a product defect
(1.0.10's runs reported the physics suite FAILED on every Mac with Godot installed, for want of the models), and
never a pass. Suites started by hand resolve their inputs the same way. Standard library only, and the product's
list of software renderers.

A hardware GPU is an input too (``hardware_gpu``): a capability of the machine, never its platform. A suite whose
checks are a GPU's own behaviour (``test_blender_view_warm`` times a compile-ahead draw against a synchronous
render, which a software rasterizer turns around) declares it. The runner asks the app under test once which
renderer it draws with, in the background (``probe_renderer``), unless ``ANGELO_QA_GPU_RENDERER`` names it; a
software renderer by the product's own list (``parallel_capability.SOFTWARE_RENDERERS``) or WARP, and an app that
cannot say, are skips with that reason: Angelo does not support machines without a GPU. With a hardware GPU the
suite runs and gets the renderer's name in that variable.

A window that can be captured is one more (``window_capture``): a suite that judges what the person's window shows by
``screen.screenshot`` declares it. The runner opens the app under test once, in a window with a profile of its own,
and has it capture itself after a draw of the window it asks for, again until a picture is lit or
``CAPTURE_SECONDS`` have passed (``probe_window_capture``), unless ``ANGELO_QA_WINDOW_CAPTURE`` says (1 or 0). A
black picture is a skip only on a machine that draws with a software renderer, as the ``hardware_gpu`` probe names
it: a software OpenGL window on a virtual display is captured black, and Angelo does not support such machines.
Everything else is the input FAILING (``Failed``), and its suite an error of the run with the reason, never a skip:
a black picture where a hardware GPU draws, a capture that raised, an app that did not answer, a renderer that
could not be named. A broken screenshot or a window that never draws is a defect, not a property of the machine.
"""

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import gui_launch

HERE = Path(__file__).resolve().parent
GODOT_ENV = "ANGELO_QA_GODOT"
PHYSICS_ASSETS_ENV = "ANGELO_QA_PHYSICS_ASSETS"
# Where Godot's own installer puts the editor; the Godot suites are macOS-only (the editor-settings path they write).
DEFAULT_GODOT = {"darwin": "/Applications/Godot.app/Contents/MacOS/Godot"}
HELDOUT_MANIFEST = HERE / "godot_physics_e2e" / "heldout_assets.json"
FETCHER = HERE / "godot_physics_e2e" / "fetch_heldout_assets.py"
CACHE_FOLDER = "angelo-native-inputs"   # Beside the runs in the runner's output root, like its bytecode folder.
GPU_ENV = "ANGELO_QA_GPU_RENDERER"     # The renderer the app under test draws with, as Blender's gpu module names it.
BINARY_ENV = "ANGELO_BINARY"           # The app under test: the runner names it to the resolvers.
PROBE_MARK = "ANGELO_GPU_RENDERER\t"   # The probe's one line: this, the backend, a tab, the renderer.
PROBE_SECONDS = 180
PROBE = ("import gpu\ntry:\n    getattr(gpu, 'init', lambda: None)()\n"
         "    print(%r + gpu.platform.backend_type_get() + '\\t' + (gpu.platform.renderer_get() or ''), flush=True)\n"
         "except Exception as error:\n    print(%r + 'NONE\\t' + type(error).__name__, flush=True)\n"
         % (PROBE_MARK, PROBE_MARK))
_WARP = re.compile(r"\bwarp\b")   # Windows' software rasterizer, where a driver names it by that word alone.
CAPTURE_ENV = "ANGELO_QA_WINDOW_CAPTURE"   # 1 where screen.screenshot captures the app's window, 0 where it cannot.
CAPTURE_MARK = "ANGELO_WINDOW_CAPTURE\t"   # The probe's one line: this, 1, 0 or NONE, a tab, the size or the error.
CAPTURE_SECONDS = 30   # How long a window that is captured black is drawn and captured again before it is said so.
# Each capture follows a draw of the window the probe itself asks for (never a moment after start-up that a slow
# cold start could miss); a black one is taken again until one is lit or CAPTURE_SECONDS have passed.
CAPTURE_PROBE = (
    "import bpy, time\n"
    "began = []\n"
    "def capture():\n"
    "    began.append(time.monotonic())\n"
    "    try:\n"
    "        import numpy, os, tempfile\n"
    "        window = bpy.context.window_manager.windows[0]\n"
    "        area = window.screen.areas[0]\n"
    "        path = os.path.join(tempfile.mkdtemp(prefix='angelo-capture-'), 'window.png')\n"
    "        with bpy.context.temp_override(window=window, area=area, region=area.regions[0]):\n"
    "            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)\n"
    "            bpy.ops.screen.screenshot(filepath=path, check_existing=False)\n"
    "        image = bpy.data.images.load(path)\n"
    "        pixels = numpy.empty(image.size[0] * image.size[1] * 4, dtype=numpy.float32)\n"
    "        image.pixels.foreach_get(pixels)\n"
    "        lit, size = bool(pixels.reshape(-1, 4)[:, :3].any()), '%%dx%%d' %% tuple(image.size)\n"
    "        bpy.data.images.remove(image)\n"
    "        if not lit and time.monotonic() - began[0] < %d:\n"
    "            return 0.5\n"
    "        said = ('1' if lit else '0') + '\\t' + size\n"
    "    except Exception as error:\n"
    "        said = 'NONE\\t%%s: %%s' %% (type(error).__name__, ' '.join(str(error).split())[:160])\n"
    "    print(%r + said, flush=True)\n"
    "    bpy.ops.wm.quit_blender()\n"
    "bpy.app.timers.register(capture, first_interval=0.5)\n" % (CAPTURE_SECONDS, CAPTURE_MARK))


class Unavailable(Exception):
    """This machine cannot provide the input; the message says what is missing and how to provide it."""


class Failed(Exception):
    """The input could not be established, and that is no property of the machine: its probe broke, or found what
    only a defect explains. The suites that need it are errors of the run with this reason, never skips."""


def default_godot(platform=None):
    """The editor a Godot suite uses without ``ANGELO_QA_GODOT`` (it may not be installed)."""
    return DEFAULT_GODOT.get(platform or sys.platform, "")


def godot_editors(env, platform=None, isfile=os.path.isfile):
    """The Godot editors to run: ``ANGELO_QA_GODOT`` (several joined by the path separator), else the default."""
    value = env.get(GODOT_ENV, "")
    if value:
        editors = [item for item in value.split(os.pathsep) if item]
        missing = [item for item in editors if not isfile(item)]
        if missing:
            raise Unavailable("%s names no Godot editor at %s" % (GODOT_ENV, ", ".join(missing)))
        return editors
    default = default_godot(platform)
    if default and isfile(default):
        return [default]
    raise Unavailable("needs a Godot 4.4+ editor: set %s (several joined by %r)%s"
                      % (GODOT_ENV, os.pathsep, " or install one at " + default if default else ""))


def load_fetcher():
    spec = importlib.util.spec_from_file_location("fetch_heldout_assets", FETCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def physics_assets(env, cache_root, fetch=None):
    """A folder of held-out .glb models: ``ANGELO_QA_PHYSICS_ASSETS``, else the pinned set fetched into the cache."""
    value = env.get(PHYSICS_ASSETS_ENV, "")
    if value:
        if not list(Path(value).glob("*.glb")):
            raise Unavailable("%s=%s holds no .glb models" % (PHYSICS_ASSETS_ENV, value))
        return value
    pinned = HELDOUT_MANIFEST.read_bytes()
    # Named by the manifest's digest: a changed pin never reuses another pin's files.
    folder = Path(cache_root) / CACHE_FOLDER / ("heldout-physics-" + hashlib.sha256(pinned).hexdigest()[:12])
    fetch = fetch or load_fetcher().fetch
    try:
        fetch(json.loads(pinned), folder)   # Skips files already there with the pinned sha256.
    except (OSError, ValueError, SystemExit) as error:   # Offline, an HTTP error, or the fetcher's refusal.
        raise Unavailable("the held-out models of %s could not be fetched into %s (%s); set %s to a folder of them "
                          "(godot_physics_e2e/fetch_heldout_assets.py DEST)"
                          % (HELDOUT_MANIFEST.name, folder, error, PHYSICS_ASSETS_ENV)) from None
    return str(folder)


def software_renderer(name):
    """Whether a renderer of this name is software: no GPU draws. The names are the product's own
    (``parallel_capability.SOFTWARE_RENDERERS``), so the suites skip exactly what Angelo itself counts as no GPU."""
    try:
        from angelo_agent import parallel_capability
    except ImportError:   # Started where the add-ons are not on the path yet: this checkout's.
        sys.path.append(str(HERE.parents[2] / "scripts/addons_core"))
        from angelo_agent import parallel_capability
    lowered = name.lower()
    return any(soft in lowered for soft in parallel_capability.SOFTWARE_RENDERERS) or bool(_WARP.search(lowered))


def probe_renderer(binary, env, run=subprocess.run):
    """``(backend, renderer)`` as the app at ``binary`` names them from a background start; ``("NONE", why)`` when
    it has no GPU there. ``Unavailable`` when it did not say (it could not start, or took too long)."""
    command = [str(binary), "--background", "--factory-startup", "--disable-autoexec", "--python-expr", PROBE]
    try:
        done = run(command, env=env, capture_output=True, text=True, timeout=PROBE_SECONDS)
        said = [line for line in (done.stdout or "").splitlines() if line.startswith(PROBE_MARK)]
        status = "exit %s" % done.returncode
    except (OSError, subprocess.SubprocessError) as error:
        said, status = [], type(error).__name__
    if not said:
        raise Unavailable("the app under test did not say which renderer it draws with (%s); set %s to its name"
                          % (status, GPU_ENV))
    backend, _, renderer = said[-1][len(PROBE_MARK):].partition("\t")
    return backend.strip(), renderer.strip()


def renderer(env, probe=None):
    """The renderer the app under test draws with, by name: ``ANGELO_QA_GPU_RENDERER``, else the app is asked.
    ``Unavailable`` for an app that names none, or when there is no app to ask."""
    name = env.get(GPU_ENV, "")
    if not name:
        binary = env.get(BINARY_ENV, "")
        if not binary:
            raise Unavailable("needs a hardware GPU: set %s to the renderer the app draws with" % GPU_ENV)
        backend, name = (probe or probe_renderer)(binary, env)
        if not name or backend in ("", "NONE"):
            raise Unavailable("needs a hardware GPU; the app under test has no GPU renderer in the background (%s)"
                              % (name or backend or "none named"))
    return name


def hardware_gpu(env, cache_root, probe=None):
    """The hardware GPU the app under test draws with, by name (``renderer``). ``Unavailable`` for a software
    renderer, and for an app that names none: never taken for hardware."""
    name = renderer(env, probe)
    if software_renderer(name):
        raise Unavailable("needs a hardware GPU; this machine draws with a software renderer (%s), and Angelo does "
                          "not support machines without a GPU" % name)
    return name


def probe_window_capture(binary, env, cache_root, run=subprocess.run):
    """``(said, detail)`` from the app at ``binary`` opened in a window, with a profile and state folders of its own
    under ``cache_root``: ``("1", size)`` where ``screen.screenshot`` wrote a picture with a lit pixel, ``("0",
    size)`` where it wrote a black one, ``("NONE", why)`` where it could not capture. ``Unavailable`` when it did
    not say (it could not open a window, or took too long)."""
    box = Path(cache_root) / CACHE_FOLDER / "window-capture"
    own = {name: str(box / folder) for name, folder in (
        ("BLENDER_USER_RESOURCES", "user"), ("BLENDER_USER_CONFIG", "user/config"),
        ("BLENDER_USER_SCRIPTS", "user/scripts"), ("BLENDER_USER_DATAFILES", "user/datafiles"),
        ("BLENDER_USER_EXTENSIONS", "user/extensions"), ("ANGELO_STATE_DIR", "state"),
        ("ANGELO_CONNECTIONS_DIR", "accounts"), ("ANGELO_LIBRARY_DIR", "library"))}
    own.update(ANGELO_DISABLE_KEYCHAIN="1", ANGELO_AGENT_RUNTIME="off", ANGELO_DISABLE_INTRO="1",
               ANGELO_DISABLE_DEFAULT_CHAT="1")
    # The window a window suite gets (gui_launch.window_flags): behind the person's, its input isolated.
    command = ([str(binary)] + gui_launch.window_flags()
               + ["--factory-startup", "--disable-autoexec", "--python-expr", CAPTURE_PROBE])
    try:
        for folder in own.values():
            if os.sep in folder:
                Path(folder).mkdir(parents=True, exist_ok=True)
        done = run(command, env=dict(env, **own), capture_output=True, text=True, timeout=PROBE_SECONDS)
        said = [line for line in (done.stdout or "").splitlines() if line.startswith(CAPTURE_MARK)]
        status = "exit %s" % done.returncode
    except (OSError, subprocess.SubprocessError) as error:
        said, status = [], type(error).__name__
    if not said:
        raise Failed("the app under test did not say whether its window can be captured (%s): it opened no window, "
                     "ended first or took over %d s" % (status, PROBE_SECONDS))
    answer, _, detail = said[-1][len(CAPTURE_MARK):].partition("\t")
    return answer.strip(), detail.strip()


def window_capture(env, cache_root, probe=None, gpu_probe=None):
    """``"1"`` where ``screen.screenshot`` captures the window of the app under test: ``ANGELO_QA_WINDOW_CAPTURE``,
    else the app is asked. ``Unavailable`` only for a black picture on a machine that draws with a software renderer
    (``renderer``, as ``hardware_gpu`` names it). ``Failed`` for everything else that is not a lit picture: black
    where a hardware GPU draws or where the renderer cannot be named, a capture that raised, no answer, no app."""
    said, detail = env.get(CAPTURE_ENV, ""), "set by %s" % CAPTURE_ENV
    if not said:
        binary = env.get(BINARY_ENV, "")
        if not binary:
            raise Failed("needs a window screen.screenshot can capture, and there is no app to ask: set %s"
                         % BINARY_ENV)
        said, detail = (probe or probe_window_capture)(binary, env, cache_root)
    if said == "1":
        return "1"
    if said != "0":
        raise Failed("the app under test could not capture its own window with screen.screenshot (%s)"
                     % (detail or said))
    try:
        name = renderer(env, gpu_probe)
    except Unavailable as error:
        raise Failed("the window of the app under test is captured as a black picture (%s), and the renderer it "
                     "draws with could not be named, so that is not known to be a software renderer's (%s)"
                     % (detail, error)) from None
    if not software_renderer(name):
        raise Failed("the window of the app under test is captured as a black picture (%s) though it draws with a "
                     "hardware GPU (%s): the window did not draw, or screen.screenshot is broken" % (detail, name))
    raise Unavailable("needs a window screen.screenshot can capture; this one is captured as a black picture (%s) "
                      "on a software renderer (%s), as a software OpenGL window on a virtual display is"
                      % (detail, name))


# name: (the variable the suite reads, resolver(env, cache_root) -> value). Godot's value is the editors joined.
RESOLVERS = {
    "godot": (GODOT_ENV, lambda env, cache_root: os.pathsep.join(godot_editors(env))),
    "physics_heldout": (PHYSICS_ASSETS_ENV, physics_assets),
    "hardware_gpu": (GPU_ENV, hardware_gpu),
    "window_capture": (CAPTURE_ENV, window_capture),
}


def resolve(suites, env, cache_root, resolvers=None, failed=None):
    """``(values, unavailable)``: each suite's resolved variables by name, and why each other suite cannot run.

    Each input is resolved at most once; a suite that needs an unavailable input is not in ``values``. A suite whose
    input FAILED (``Failed``) is in neither: its reason is put in ``failed`` (a dict), for the caller to report as
    an error of the run. Without ``failed`` the failure is raised: it is never dropped, and never a skip.
    """
    resolvers = RESOLVERS if resolvers is None else resolvers
    resolved, values, unavailable = {}, {}, {}
    for suite in suites:
        found = {}
        for name in suite.inputs:
            if name not in resolved:
                variable, resolver = resolvers[name]
                try:
                    resolved[name] = ("ok", variable, resolver(env, cache_root))
                except Unavailable as error:
                    resolved[name] = ("unavailable", variable, "input %s unavailable: %s" % (name, error))
                except Failed as error:
                    if failed is None:
                        raise
                    resolved[name] = ("failed", variable, "input %s failed: %s" % (name, error))
            state, variable, value = resolved[name]
            if state != "ok":
                (unavailable if state == "unavailable" else failed)[suite.name] = value
                break
            found[variable] = value
        else:
            values[suite.name] = found
    return values, unavailable
