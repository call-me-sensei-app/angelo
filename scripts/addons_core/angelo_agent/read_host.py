# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The live read tools on the agent's executor: one data path, and a .blend file as a file.

``BlenderExecutor`` hands both tools here on Blender's main thread. The file
tool takes the chat's selected file workspace from the controller, the same
``files.FileWorkspace`` the file tools use, so it can reach nothing they cannot.
"""


def execute(controller, window, name, arguments):
    if name == "angelo_read_data":
        from . import data_read
        return data_read.execute(window, arguments)
    from . import blend_file
    return blend_file.execute(window, arguments, getattr(controller, "files", None))
