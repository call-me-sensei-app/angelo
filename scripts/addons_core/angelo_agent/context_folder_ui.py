# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""**Folder as context…**: the + menu footer's way to attach a folder to the chat (``context_folder``).

The operator opens Blender's file browser on folders; the person's pick joins this chat's folders
(``context_folder_host.attach``, up to ``context_folder.MAX_FOLDERS``). The first is the working folder, where Angelo
may write text files; the rest are read-only reference folders. The chat shows one card per folder naming it and its
role, with **Make working folder** on a reference folder and **Remove** while it is still attached
(``chat_notices``). A worker's chat, which works on a worker's copy, refuses it.
"""

from pathlib import Path

import bpy
from bpy.types import Operator

from . import grant_guard
from .context_folder_host import ATTACHED
from .ui_text import n_, notice, rpt_

TITLE = n_("Folder as context…")
WHY = n_("Let Angelo read your folders and write notes only in the working one")
NOT_HERE = n_("A worker's chat can't have a folder of its own")
NOT_A_FOLDER = n_("Choose a folder")
PRIVATE_FOLDER = n_("Angelo can't read that folder: it holds keys, credentials or a repository's internals. Choose "
                    "a project folder instead")
TOO_WIDE_FOLDER = n_("That folder is too wide to read: your home folder and whole drives hold private files. Choose "
                     "a project folder inside it")
UNCHECKED_FOLDER = n_("Angelo can't open that folder to check it. Choose a project folder Angelo can open")
TOO_MANY_FOLDERS = n_("A chat can have {count} folders. Remove one from its card first")
ALREADY_ATTACHED = n_("That folder is attached to this chat already")
OVERLAPS = n_("That folder is inside a folder this chat has, or holds one. Choose a separate folder")
NOT_READY = n_("Angelo couldn't load this chat's folders just now. Try again in a moment")


class ANGELO_OT_context_folder(Operator):
    bl_idname = "angelo.context_folder"
    bl_label = "Folder as Context"
    bl_description = ("Let the agent use a folder of yours in this chat: it reads it, and writes text files only in "
                      "the working folder")
    bl_options = {'INTERNAL'}
    grant_browse = True   # Its invoke opens a file browser: the person's pick holds consent longer (grant_guard).

    directory: bpy.props.StringProperty(subtype='DIR_PATH', options={'SKIP_SAVE'})
    filter_folder: bpy.props.BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})

    def consent_target(self, context):
        """Nothing beyond its own field (the folder the person picks) and the chat, which the dialog binds already."""
        return None

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from . import context_folder, context_folder_host, ui
        controller = ui.get_controller()
        if getattr(controller, "remote_executor", None) is not None:
            self.report({'WARNING'}, rpt_(NOT_HERE))
            return {'CANCELLED'}
        folder = Path(self.directory.rstrip("/\\") or ".")
        try:
            folder_id = context_folder_host.attach(controller, folder)
        except context_folder.TooMany:
            self.report({'WARNING'}, rpt_(TOO_MANY_FOLDERS, count=context_folder.MAX_FOLDERS))
            return {'CANCELLED'}
        except context_folder.AlreadyAttached:
            self.report({'WARNING'}, rpt_(ALREADY_ATTACHED))
            return {'CANCELLED'}
        except context_folder.Overlaps:
            self.report({'WARNING'}, rpt_(OVERLAPS))
            return {'CANCELLED'}
        except PermissionError:   # The grants weren't in C yet (context_folder_grants.NOT_READY), or C refused.
            self.report({'WARNING'}, rpt_(NOT_READY))
            return {'CANCELLED'}
        except context_folder.FolderRefused as refused:   # Whoever picked it (the 2.1.0 break-it review, F-1).
            self.report({'WARNING'}, rpt_({context_folder.PRIVATE: PRIVATE_FOLDER,
                                           context_folder.UNCHECKED: UNCHECKED_FOLDER}.get(refused.reason,
                                                                                         TOO_WIDE_FOLDER)))
            return {'CANCELLED'}
        except (OSError, ValueError):
            self.report({'WARNING'}, rpt_(NOT_A_FOLDER))
            return {'CANCELLED'}
        controller.emit("message", {"role": "system", "notice": True, "context_folder": folder.name,
                                    "context_folder_id": folder_id, **notice(ATTACHED, name=folder.name)})
        controller.version += 1
        return {'FINISHED'}


grant_guard.guard(ANGELO_OT_context_folder)   # Lets the agent read a folder: only the person's pick grants (grant_guard).


def register():
    from . import chat_destinations
    bpy.utils.register_class(ANGELO_OT_context_folder)
    chat_destinations.register(chat_destinations.Feature(   # The chat bar's + menu: Angelo's main navigation.
        "context_folder", TITLE, WHY, glyph="feedback", order=87, operator=ANGELO_OT_context_folder.bl_idname,
        placement="footer", lead_only=True, owner="context_folder_ui"))


def unregister():
    from . import chat_destinations
    chat_destinations.unregister("context_folder")
    bpy.utils.unregister_class(ANGELO_OT_context_folder)
