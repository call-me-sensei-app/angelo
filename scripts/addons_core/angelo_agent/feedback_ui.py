# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Send feedback: the chat bar's + menu rows that open the website's feedback form (doc/angelo/app_identity.rst).

Report a bug… and Request a feature… open ``urls.feedback`` in the browser with the version, build, system and (a
bug) graphics card filled in (``url_prefill_runtime.feedback_details``), in the link's fragment: the form shows each
one, editable, and nothing leaves the computer until the person sends it. Never a scene, file name or path. Help >
Report a Bug / Request a Feature open the same form (``wm.url_open_preset`` BUG / FEATURE).

Costs nothing until clicked: two registry rows and one operator.
"""

import bpy
from bpy.props import EnumProperty
from bpy.types import Operator

from .ui_text import n_

FEEDBACK = n_("Feedback", "Angelo")   # Not Blender's "Feedback" (an IK solver term in its catalogues).
FEATURE_IDS = ("report_bug", "request_feature")


class ANGELO_OT_send_feedback(Operator):
    bl_idname = "angelo.send_feedback"
    bl_label = "Send Feedback"
    bl_description = ("Open Angelo's feedback form on angelo.studio in your browser, with your Angelo version and "
                      "system filled in. Nothing is sent until you send the form")

    kind: EnumProperty(name="Kind", items=(
        ("BUG", "Bug Report", "Something doesn't work as it should"),
        ("FEATURE", "Feature Request", "Something Angelo should do"),
    ), default="BUG")

    def execute(self, context):
        from _bpy_internal.system_info.url_prefill_runtime import url_from_blender
        from . import update_card
        update_card.open_link(url_from_blender(self.kind.lower()))
        return {'FINISHED'}


def _destinations():
    """The + menu rows (chat_destinations): Angelo's main navigation, in Director and Manual, lead and worker chats."""
    from . import chat_destinations as cd
    op = ANGELO_OT_send_feedback.bl_idname
    return (cd.Feature("report_bug", n_("Report a bug…"),
                       n_("Opens the form on angelo.studio, with your Angelo version and system filled in"), glyph="feedback", order=90,
                       section=FEEDBACK, operator=op, properties=(("kind", "BUG"),), placement="footer",
                       owner="feedback_ui"),
            cd.Feature("request_feature", n_("Request a feature…"), n_("Opens the form on angelo.studio: tell us what Angelo should do next"),
                       glyph="feedback", order=91, section=FEEDBACK, operator=op, properties=(("kind", "FEATURE"),),
                       placement="footer", owner="feedback_ui"))


def register():
    from . import chat_destinations
    bpy.utils.register_class(ANGELO_OT_send_feedback)
    for feature in _destinations():
        chat_destinations.register(feature)


def unregister():
    from . import chat_destinations
    for feature_id in FEATURE_IDS:
        chat_destinations.unregister(feature_id)
    bpy.utils.unregister_class(ANGELO_OT_send_feedback)
