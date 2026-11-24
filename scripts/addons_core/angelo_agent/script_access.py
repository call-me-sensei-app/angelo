# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What an agent script can reach, said the same way wherever Angelo offers to run scripts without asking (2.1.0
break-it review A-8). The one place this is worded: the script isolation lanes update it here when what a script can
reach changes. No Blender here.

It makes no "can't" claim (re-reads of 1c2c4edaf48 and ccdfea52edb, M-1; the coordinator's ruling, 2026-11-20): the
confinement as it ships has routes around it, so it says plainly what scripts can do and asks for the person's own
judgement. ``CLAIMS`` holds a "can't" clause only with an in-app test that proves it (test_blender_confinement_attacks
and the like, never the host stand-in).

``LIMITS_URL`` opens the section at the top of doc/angelo/script_confinement.rst that names every route a script
still has (``ROUTES``, each a bold lead there; re-read of dd87b2d5aa3, S-2). ``SURFACES``: every control that lets
scripts run without asking, each showing ``SENTENCE`` and offering the link (the same re-read's S-1)."""

from .ui_text import n_

SENTENCE = n_("Scripts run with your access: they can read and change your scene and your files, including files "
              "other apps run or read. Only allow scripts you would run yourself.")
LIMIT = "including files other apps run or read"
CLAIMS = ()   # (clause, in-app test proving it): none the confinement guarantees as it ships.
# The confinement's known limits as published (the public source doc until a site page replaces it; coordinator,
# 2026-11-20).
LIMITS_ANCHOR = "what-agent-scripts-can-still-reach"
LIMITS_URL = "https://github.com/call-me-sensei-app/angelo/blob/main/doc/angelo/script_confinement.rst#" + LIMITS_ANCHOR
ROUTES = ("Your scene, and files your later writes make or remove", "Files other apps run or read",
          "Your documents, with no Angelo window open", "Secrets Angelo holds in memory",
          "Angelo's own running state, until it restarts", "Audio servers on Linux",
          "Network shares, through a path")
# (surface, module, the functions or classes that draw it, the grant points its press reaches as "module:function"):
# every control that lets scripts run without asking. test_native_script_access discovers every grant point (a call
# of set_trusted_scripts(True), a use of always_allow.set_enabled) and drawing of the Always allow scripts switch in
# the add-on and fails on one no surface lists (re-read of 70142a371dd, N-2).
SURFACES = (("script card", "chat_cards.py", ("always_allow_rows", "always_allow_button"),
             ("trusted_scripts.py:session_allow_script", "trusted_scripts_ui.py:always_allow_from_card")),
            ("Trust scripts for this chat", "trusted_scripts_ui.py", ("ANGELO_OT_trusted_scripts",),
             ("trusted_scripts_ui.py:execute",)),
            ("Approval Policy menu", "trusted_scripts_ui.py", ("ANGELO_MT_approval",), ()),
            ("Preferences toggle", "trusted_scripts_ui.py", ("draw_always_allow",),
             ("trusted_scripts_ui.py:always_allow_set",)),
            ("AI Providers toggle", "providers_page_model.py", ("_permissions",),
             ("trusted_scripts_ui.py:always_allow_set",)))
# Lines that restate a consent already given at a surface above, granting nothing: no sentence or link of their own
# (re-read of 70142a371dd, N-1): the chat's standing line (with Turn off) and the Approvals popover's footer.
REMINDERS = (("chat_cards.py", "standing_line"), ("chat_cards.py", "script_rule"))
LINK = n_("What scripts can reach")
