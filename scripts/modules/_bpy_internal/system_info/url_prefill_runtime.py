# SPDX-FileCopyrightText: 2019-2023 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

# Keep the information collected in this script synchronized with `startup.py`.

__all__ = (
    "feedback_details",
    "url_from_blender",
)


def _system():
    """The operating system, its version and the CPU architecture: ``macOS 26.1 (arm64)``. No user or computer name."""
    import platform
    name = platform.system()
    if name == "Darwin":
        name, version = "macOS", platform.mac_ver()[0]
    elif name == "Windows":
        version = platform.version()
    else:
        version = platform.release()
    text = "{:s} {:s} ({:s})".format(name, version, platform.machine()).replace("  ", " ")

    # Windowing Environment (include when dynamically selectable).
    # This lets us know if WAYLAND/X11 is in use.
    from _bpy import _ghost_backend
    ghost_backend = _ghost_backend()
    if ghost_backend not in {'NONE', 'DEFAULT'}:
        text += ", {:s} UI".format(ghost_backend)
    return text


def _gpu():
    import gpu
    text = "{:s} {:s} {:s}".format(
        gpu.platform.renderer_get(),
        gpu.platform.vendor_get(),
        gpu.platform.version_get(),
    )
    gpu_backend = gpu.platform.backend_type_get()
    if gpu_backend not in {'NONE', 'UNKNOWN', 'METAL'}:
        text += " {:s} Backend".format(gpu_backend.title())
    return text


def feedback_details(kind):
    """What Angelo fills in on its feedback form (``angelo_identity.FEEDBACK_FIELDS``): the version, the build
    (date and hash; never the source branch), the system and, for a bug, the graphics card. Nothing about the
    person, their scene or their files."""
    import angelo_identity
    import bpy

    details = {
        "version": "{:s} {:s} ({:s} {:s})".format(
            angelo_identity.PRODUCT_NAME,
            angelo_identity.APP_VERSION,
            angelo_identity.BASED_ON,
            bpy.app.version_string,
        ),
        "build": "{:s} {:s}, {:s}".format(
            bpy.app.build_commit_date.decode('utf-8', 'replace'),
            bpy.app.build_commit_time.decode('utf-8', 'replace'),
            bpy.app.build_hash.decode('ascii', 'replace')[:11],
        ),
        "os": _system(),
    }
    if kind == "bug":
        details["gpu"] = _gpu()
    return details


def url_from_blender(kind="bug"):
    """Angelo's feedback form (``feedback`` in `angelo_identity.json`) for ``kind`` ("bug" or "feature"), with
    ``feedback_details`` in the link's fragment: the website shows them on the form, and they leave the computer
    only when the person sends it."""
    import angelo_identity
    return angelo_identity.feedback_url(kind, **feedback_details(kind))
