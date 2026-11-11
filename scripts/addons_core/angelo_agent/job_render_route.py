# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which agent renders run in a child Angelo, and the memory a render may use (``job_render_child`` runs them).

Contract: doc/angelo/long_jobs.rst, "Renders in a separate process".

* ``route``: an agent's RENDER_STILL or RENDER_ANIMATION on Cycles on a GPU backend in ``CHILD_BACKENDS`` (where a
  render in the app was measured to hold up the interface) whose estimate is at least ``MIN_ESTIMATE_S``, or
  without an estimate whose work is at least ``MIN_WORK``, runs in a child. Looks (RENDER_PREVIEW), region renders,
  other engines and the person's own F12 stay in the application. So does a render the copy cannot reproduce
  (``process_state``), and its record says why: add-on or script Python it would run (the child starts with factory
  settings and auto-execution off), image pixels not saved yet, driver Python, and simulation state a copy does not
  carry (Geometry Nodes simulations and bakes, point caches neither baked into the file nor external, fluids), in
  every scene the render evaluates.
* ``projected_mb``: a render's expected peak from the last finished render of its scene on its device, kept on the
  record (local). Memory was measured to follow the scene's geometry, not its size (16.5 MiB per megapixel up to
  Cycles' tile, nothing per sample): a projection cannot foresee a shortage the last render did not show, so it never
  refuses a render (break-it review round 2: a refusal could only fire at the margin, on renders that fit). The
  child's watchdog enforces memory instead (``memory_low``).
* ``footprint_mb``, ``memory_low``, ``reserve_mb``: what the child's watchdog reads while it renders.
"""

import os
import sys

KINDS = frozenset({"RENDER_STILL", "RENDER_ANIMATION"})
# Cycles backends whose GPU render inside the app holds up the interface, measured (long_jobs.rst, "Renders in a
# separate process"): Metal, where the app's frames wait behind the render's GPU work (Mac 1, 2026-11-04: 4-7 frames of
# 67-126 ms every second in the app, none over 45 ms from a child). On a T4 (OptiX, OpenGL or Vulkan interface) the
# app's frames were unaffected and a child cost 1.6-3.3 s per render, so OptiX and CUDA stay in the app; HIP and
# oneAPI are not measured and stay as they were.
CHILD_BACKENDS = frozenset({"Metal"})
MIN_ESTIMATE_S = 20.0      # Measured on Metal: a child costs 0.8-2.2 s more, about a tenth of a render this long
                           # (long_jobs.rst, "Renders in a separate process", Cost).
MIN_WORK = 6000.0          # Without an estimate, megapixel-samples x frames x views (render_device_facts.work): at the
                           # fastest light-scene rate measured (M4 Pro, ~306 a second) at least MIN_ESTIMATE_S (same).
ENV = "RENDER_PROCESS"     # ANGELO_RENDER_PROCESS: "child" or "app" decides the route (tests, measurements).

PER_PIXEL_BYTES = 16       # Measured: a render's peak grows by one float RGBA film pixel (T4, 2026-11-04: 16.5 MiB
                           # per megapixel, the same at 16 and 128 samples); more passes cost more (long_jobs.rst).
TILE_PIXELS = 2048 * 2048  # Cycles' default tile: the film buffers above it are tiled, so memory stops growing.

_peaks = {}                # (device, scene uid) -> (peak MB, pixels) of the last finished render (``observe``).


# ---- Memory ------------------------------------------------------------------------------------------------------

def _windows_memory():
    """``(total, available)`` physical memory in bytes (GlobalMemoryStatusEx), or None."""
    import ctypes

    class Status(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
            (name, ctypes.c_ulonglong) for name in ("total", "free", "page_total", "page_free", "virtual_total",
                                                    "virtual_free", "extended")]
    status = Status(length=ctypes.sizeof(Status))
    return (status.total, status.free) if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)) else None


def physical_mb():
    """This computer's memory in MB, or None."""
    try:
        if os.name == "nt":
            found = _windows_memory()
            return None if found is None else found[0] // 1048576
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // 1048576
    except (OSError, ValueError, AttributeError):
        return None


def memory_low(total, reserve):
    """``(low, free MB or None)``: whether the system is about to run out of memory. macOS: its memory pressure is
    critical (``kern.memorystatus_vm_pressure_level`` 4; its free percentage counts compressible memory as used and
    reads low under ordinary compression, break-it review round 2); Windows and Linux: available physical memory
    under ``reserve``. ``(False, None)`` when it cannot be read."""
    try:
        if sys.platform == "darwin":
            import ctypes
            libc = ctypes.CDLL("/usr/lib/libc.dylib")

            def read(name):
                value, size = ctypes.c_int(0), ctypes.c_size_t(ctypes.sizeof(ctypes.c_int))
                ok = libc.sysctlbyname(name, ctypes.byref(value), ctypes.byref(size), None, 0) == 0
                return value.value if ok else None
            level, percent = read(b"kern.memorystatus_vm_pressure_level"), read(b"kern.memorystatus_level")
            free = None if percent is None or not total else total * percent // 100
            return level == 4, free
        if os.name == "nt":
            found = _windows_memory()
            free = None if found is None else found[1] // 1048576
        else:
            free = None
            with open("/proc/meminfo", encoding="ascii") as info:
                for line in info:
                    if line.startswith("MemAvailable:"):
                        free = int(line.split()[1]) // 1024
        return (free is not None and free < reserve), free
    except (OSError, ValueError, AttributeError):
        return False, None


def reserve_mb(total):
    """Memory kept free for the system and the person's other work: a tenth, at least 2 GB."""
    return max(2048, total // 10)


def footprint_mb(pid):
    """A process's memory in MB as the system charges it (macOS: its physical footprint, GPU memory included;
    Windows: its private bytes; elsewhere its resident size), or None."""
    try:
        if sys.platform == "darwin":
            import ctypes
            info = (ctypes.c_uint64 * 40)()   # rusage_info_v2, larger than needed
            if ctypes.CDLL("/usr/lib/libproc.dylib").proc_pid_rusage(int(pid), 2, ctypes.byref(info)) != 0:
                return None
            return int(info[9] // 1048576)    # ri_phys_footprint: after the 16-byte uuid and seven counters
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                    (name, ctypes.c_size_t) for name in ("peak_ws", "ws", "peak_paged", "paged", "peak_nonpaged",
                                                         "nonpaged", "pagefile", "peak_pagefile", "private")]
            kernel = ctypes.WinDLL("kernel32")
            handle = kernel.OpenProcess(0x1000 | 0x0010, False, int(pid))   # QUERY_LIMITED_INFORMATION | VM_READ
            if not handle:
                return None
            try:
                counters = Counters(cb=ctypes.sizeof(Counters))
                ok = ctypes.WinDLL("psapi").GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
                return int(counters.private // 1048576) if ok else None
            finally:
                kernel.CloseHandle(handle)
        with open("/proc/%d/status" % int(pid), encoding="ascii") as status:
            for line in status:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, AttributeError):
        return None
    return None


def _pixels(facts):
    try:
        width, height = facts["resolution"]
        return int(width) * int(height)
    except (KeyError, TypeError, ValueError):
        return None


def observe(record):
    """Remember a finished Cycles render's peak memory per device and scene (job_ui: every ended job)."""
    detail = record.get("detail") or {}
    peak, pixels = detail.get("peak_memory_mb"), _pixels(detail)
    if (record.get("status") == "done" and detail.get("engine") == "CYCLES" and detail.get("device")
            and isinstance(peak, int) and peak > 0 and pixels):
        _peaks[(detail["device"], detail.get("scene_uid"))] = (peak, pixels)


def projected_mb(facts, scene_uid):
    """``(projected MB, last peak MB, its pixels)`` for a render of ``scene_uid`` on the device ``facts`` names, from
    the last finished render of the same scene there; None without one. Memory grows with the film's pixels up to
    Cycles' tile (and shrinks with fewer); geometry, textures and samples add nothing per pixel. ``PER_PIXEL_BYTES``
    is the least a pixel costs (the beauty pass alone): more passes cost more, so a projection only ever errs low,
    never into a refusal of a render that fits."""
    found = _peaks.get((facts.get("device"), scene_uid))
    pixels = _pixels(facts)
    if found is None or not pixels or scene_uid is None:
        return None
    peak, then = found
    grown = (min(pixels, TILE_PIXELS) - min(then, TILE_PIXELS)) * PER_PIXEL_BYTES // 1048576
    return max(1, peak + grown), peak, then


# ---- The route ---------------------------------------------------------------------------------------------------

def route(runner, local_state=None):
    """None when ``runner`` renders in a child; else why it renders in this application (English, local only).
    ``local_state``: the reasons ``process_state`` would give (tests)."""
    from . import product_env
    request, facts = runner.request, getattr(runner, "facts", None) or {}
    if request["kind"] not in KINDS or facts.get("engine") != "CYCLES" or request.get("engine"):
        return "not a Cycles still or animation"
    chosen = (product_env.get(ENV) or "").lower()
    if chosen == "app":
        return "ANGELO_RENDER_PROCESS=app"
    extensions = getattr(runner, "extensions", None)
    if extensions is not None and extensions.any:
        return "it runs add-on or script Python, which the child does not load"
    found = process_state(runner.scene) if local_state is None else local_state
    if found:
        return "it uses state that exists only in this process (" + "; ".join(found[:3]) + ")"
    if chosen == "child":
        return None
    backend = _backend(facts.get("device"))
    if backend not in CHILD_BACKENDS:
        return "it renders on %s, whose renders in the app measured no interface stall" % (backend or "the CPU")
    estimate = facts.get("estimate_s")
    if isinstance(estimate, (int, float)):
        return None if estimate >= MIN_ESTIMATE_S else "its estimate (%d s) is under %d s" % (estimate, MIN_ESTIMATE_S)
    from . import render_device_facts
    size = render_device_facts.work(facts)
    return None if size and size >= MIN_WORK else "no estimate, and its work is under %d megapixel-samples" % MIN_WORK


def _backend(device):
    """The Cycles backend a device text names, or None for the CPU: "GPU (Metal: Apple M4 Pro)" and "GPU (Metal: ...) +
    CPU" as ``render_device_facts`` words them, and "METAL" or "METAL+CPU" as ``--cycles-device`` gives them."""
    if not isinstance(device, str):
        return None
    if device.startswith("GPU ("):
        return device[len("GPU ("):].split(":", 1)[0].strip() or None
    from . import render_device
    return render_device.NAMES.get(device.split("+", 1)[0].strip().upper())


# ---- What a copy cannot carry ------------------------------------------------------------------------------------
# The class: anything a render of the scene reads that lives in this process and is not written into a .blend the
# child can load the same way. Each check is conservative: when unsure, the render stays in the application.

def process_state(scene):
    """Why a copy of ``scene`` would render something else, as short English phrases (none: it would not)."""
    import bpy
    from . import render_drivers, render_reach
    found = []
    dirty = [image for image in bpy.data.images
             if image.is_dirty and image.users and image.type not in {"RENDER_RESULT", "COMPOSITING"}]
    dirty = [image.name for image in _reached(bpy, dirty)]
    if dirty:   # Painted or baked pixels only in memory (packed or not): the copy writes the stale ones.
        found.append("image pixels not saved: " + ", ".join(sorted(dirty)[:3]))
    results = _reached(bpy, [image for image in bpy.data.images if image.type in {"RENDER_RESULT", "COMPOSITING"}])
    if results:   # A render or Viewer result used as a texture: it exists only in this process.
        found.append("a render result used as a texture: " + ", ".join(sorted(image.name for image in results)[:3]))
    handlers = _addon_handlers()
    if handlers:   # A bundled add-on's render handler: the child's factory settings do not load it.
        found.append("add-on handlers: " + ", ".join(handlers[:3]))
    try:   # Driver Python the render runs here; the child has no add-ons and auto-execution off.
        drivers = render_drivers.python_drivers(scene, lambda value: False)
    except Exception:   # noqa: BLE001 - unknown is never "none".
        drivers = [(None, "unreadable drivers")]
    if drivers:
        found.append("driver Python: " + ", ".join(label for _identity, label in drivers[:3]))
    for other in _scenes(scene, render_reach):
        found += ["%s in %s" % (why, other.name) for why in _caches(other)]
    return found


_NOT_RENDERED = ("Brush", "Palette", "Screen", "WorkSpace", "WindowManager")   # IDs that use images, never in a render.


def _reached(bpy, images):
    """The ``images`` something other than a brush or the interface uses (``bpy.data.user_map``): an unsaved stroke
    on a brush texture or an image open only in an editor never keeps a render in the app (review round 2, F9)."""
    if not images:
        return []
    users = bpy.data.user_map(subset=images)
    return [image for image in images
            if any(type(user).__name__ not in _NOT_RENDERED for user in users.get(image, ()))]


# The add-ons a --factory-startup child loads: Blender's factory list (BKE_blendfile_userdef_from_defaults, pinned by
# test_native_job_render_child_addons). Their handlers, Angelo code inside them included (io_scene_gltf2's
# angelo_physics), run in the child as here.
FACTORY_ADDONS = frozenset({"io_anim_bvh", "io_curve_svg", "io_mesh_uv_layout", "io_scene_fbx", "io_scene_gltf2",
                            "cycles", "pose_library", "bl_pkg"})
CHILD_MODULES = FACTORY_ADDONS | {"angelo_agent", "angelo_render_device"}


def _addon_handlers():
    """Render-time handlers of bundled add-ons the child does not load: it starts on factory settings, so the factory
    add-ons and Angelo's own modules run there too (``render_extensions`` counts only code that does not ship; review
    round 2, F8)."""
    from . import render_extensions
    import bpy
    lists = getattr(getattr(bpy, "app", None), "handlers", None)
    found = []
    for name in render_extensions.HANDLERS[render_extensions.RENDER] if lists is not None else ():
        for function in getattr(lists, name, ()):
            if render_extensions._hosted(function):
                continue
            module = (getattr(function, "__module__", None) or "").split(".", 1)[0]
            filename = render_extensions._code_file(function) or ""
            if module in CHILD_MODULES or "addons" not in filename.replace("\\", "/"):
                continue   # The factory add-ons, Angelo, and Blender's own startup scripts and modules run there too.
            found.append(render_extensions._label(function))
    return found


def _scenes(scene, render_reach):
    """The scenes a render of ``scene`` evaluates: those it renders (compositor, Sequencer), with background sets."""
    found = []
    for rendered in render_reach.rendered_scenes(scene):
        current, seen = rendered, 0
        while current is not None and current not in found and seen < 64:
            found.append(current)
            current, seen = current.background_set, seen + 1
    return found


def _cache_kept(cache):
    """Whether a point cache reads the same in the copy: baked into the file (memory), or external on disk."""
    return bool(getattr(cache, "use_external", False)) or (bool(getattr(cache, "is_baked", False))
                                                           and not getattr(cache, "use_disk_cache", False))


def _caches(scene):
    """Simulation state a copy loses: Geometry Nodes simulations and bakes, point caches that are not baked into the
    file or external (a disk cache's default folder is named after the .blend), fluids (their cache folders)."""
    found = []
    world = getattr(scene, "rigidbody_world", None)
    if world is not None and world.enabled and not _cache_kept(world.point_cache):
        found.append("rigid body cache")
    for obj in scene.objects:
        for modifier in getattr(obj, "modifiers", ()):
            kind = modifier.type
            if kind == "NODES" and _baking_tree(modifier.node_group, set()):
                found.append("Geometry Nodes simulation, bake or import on " + obj.name)
            elif kind == "FLUID" and getattr(modifier, "fluid_type", "") == "DOMAIN":
                found.append("fluid domain " + obj.name)
            elif kind in {"CLOTH", "SOFT_BODY"} and not _cache_kept(modifier.point_cache):
                found.append("cache of " + obj.name)
            elif kind == "DYNAMIC_PAINT" and getattr(modifier, "canvas_settings", None) is not None:
                if any(not _cache_kept(surface.point_cache) for surface in modifier.canvas_settings.canvas_surfaces):
                    found.append("dynamic paint cache of " + obj.name)
        for system in getattr(obj, "particle_systems", ()):
            settings = system.settings
            dynamic = settings.type != "HAIR" or getattr(system, "use_hair_dynamics", False)
            if dynamic and not _cache_kept(system.point_cache):
                found.append("particle cache of " + obj.name)
    return found


def _baking_tree(tree, seen):
    if tree is None or tree in seen:
        return False
    seen.add(tree)
    for node in tree.nodes:
        if node.bl_idname in {"GeometryNodeSimulationOutput", "GeometryNodeBake"} or node.bl_idname.startswith(
                "GeometryNodeImport"):   # An import node's relative path would be read beside the copy (F12).
            return True
        if getattr(node, "node_tree", None) is not None and _baking_tree(node.node_tree, seen):
            return True
    return False
