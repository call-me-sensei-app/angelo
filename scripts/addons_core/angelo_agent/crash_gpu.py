# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""After a crash in the graphics driver while Angelo drew with Vulkan: the offer to restart with OpenGL.

Blender 5.3 moves every Windows x64 preference file to Vulkan, upgraders included, while Angelo 1.0.x drew with
OpenGL. A driver that crashes on Vulkan otherwise left a person a ``blender_startup_opengl.cmd`` to find. The
crash card (``crash_card``) offers **Restart with OpenGL** when ``offers_opengl``; a crash the card does not ask
about gets one notice with the same button (``crash_ui``). The choice is the one Preferences > System makes, so
the person can switch back there. Contract: doc/angelo/updates_and_reports.rst, "A crash in the graphics driver".

``offers_opengl`` needs no ``bpy``; ``restart_with_opengl`` takes it.
"""

import ntpath
import re
import subprocess
import sys

# The Vulkan loader and the graphics drivers' Vulkan and OpenGL modules (NVIDIA serves both from nvoglv).
DRIVER_IMAGE = re.compile(r"^(vulkan-1|nvoglv(32|64)|amdvlk(32|64)|atio6axx|atig6pxx|igvk(32|64)|ig\w*icd(32|64))"
                          r"\.dll$", re.IGNORECASE)
# Blender's own Vulkan backend and GHOST context, and the Vulkan API's entry points.
VULKAN_SYMBOL = re.compile(r"(::VK[A-Z]\w*|\bGHOST_ContextVK\b|\bvk[A-Z]\w+)")


def stopped_in_gpu(parsed):
    """Whether the crashed thread, or the module that faulted, is the Vulkan loader, a graphics driver or Blender's
    Vulkan backend."""
    frames = list(parsed.native)
    module = ntpath.basename(parsed.exception.get("module", "") or "")
    if module and DRIVER_IMAGE.match(module):
        return True
    return any(DRIVER_IMAGE.match(frame.get("image", "") or "") or VULKAN_SYMBOL.search(frame.get("symbol", "") or "")
               for frame in frames)


def offers_opengl(parsed, backend):
    """Whether to offer OpenGL: this launch draws with Vulkan (the backend that crashed, since the preference is
    unchanged), and the crash was in it."""
    return backend == "VULKAN" and stopped_in_gpu(parsed)


def choose_opengl(bpy):
    """OpenGL, as Preferences > System chooses it, saved: the next start draws with it."""
    bpy.context.preferences.system.gpu_backend = 'OPENGL'
    bpy.ops.wm.save_userpref()


def relaunch(bpy, popen=subprocess.Popen, platform=sys.platform):
    """Start Angelo again, detached, and quit this one once the card's own event has finished."""
    options = {"close_fds": True, "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
               "stderr": subprocess.DEVNULL}
    if platform == "win32":
        options["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0x8)
                                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200))
    else:
        options["start_new_session"] = True
    popen([bpy.app.binary_path], **options)

    def quit_this_one():
        bpy.ops.wm.quit_blender()
        return None

    bpy.app.timers.register(quit_this_one, first_interval=0.2)   # After the card's own event has finished.


def restart_with_opengl(bpy, popen=subprocess.Popen, platform=sys.platform):
    """Choose OpenGL as Preferences > System does, save it, start Angelo again and quit this one."""
    choose_opengl(bpy)
    relaunch(bpy, popen, platform)
