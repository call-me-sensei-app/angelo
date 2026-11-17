# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A script that replaces the open file is never run on a standing consent: the person sees its card.

A script may load, revert or recover a file, or read the startup file (``script_document``). In the person's own
window that replaces the document they have open, unsaved work included, and Blender's Undo cannot take it back;
only the request's snapshot holds what was there. Trust scripts for this chat and Always allow scripts are consents
to scripts in general, given before this script existed, so a script that names one of those operators asks, as a
script that names preferences or Angelo's own add-on does (``trusted_scripts.consent_excluded``). A worker Blender
already refuses the same scripts by the same check over the code (``worker_script.file_load``, its ``FILE_LOADS``).

The card says why it asks, and says the truth: a script that would replace the open file gets its own line (it
does not refer to preferences or to Angelo's add-on, which is what the other always-ask scripts' line says), in
every consent the person can have given, through a real ``Controller`` and its own consent rule.

The names are a check over the code, as the preferences rule is: a script that hides the name still ends as
``DOCUMENT_REPLACED`` (``test_blender_script_document_replaced``). Appending or linking data is not a load.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).
import angelo_guard_twin  # noqa: E402  C's authority, as the app's (strict: no scope but the one a test opens).

from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import always_allow, approval_wait, auto_reasons, managed_policy  # noqa: E402
from angelo_agent import trusted_scripts, worker_script  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.managed_policy import Policy  # noqa: E402

# How a script can name the operator: called, called by its name, through an alias, or looked up from a string.
SPELLINGS = ("import bpy\nbpy.ops.wm.{name}()\n",
             "import bpy\nbpy.ops.wm.{name}('EXEC_DEFAULT')\n",
             "import bpy\nrun = bpy.ops.wm.{name}\nrun()\n",
             "import bpy\ngetattr(bpy.ops.wm, '{name}')()\n",
             "import bpy\nwith bpy.context.temp_override(window=bpy.context.window):\n    bpy.ops.wm.{name}()\n")
# Scripts that replace nothing: edits, a save, and data appended or linked from another file.
PLAIN = ("import bpy\nbpy.ops.mesh.primitive_cube_add()\n",
         "import bpy\nbpy.ops.wm.save_mainfile()\n",
         "import bpy\nbpy.ops.wm.append(filepath='//props.blend/Object/Chair', directory='//props.blend/Object/', "
         "filename='Chair')\n",
         "import bpy\nwith bpy.data.libraries.load('//props.blend', link=True) as (source, target):\n"
         "    target.objects = source.objects\n",
         "import bpy\nbpy.data.objects['Cube'].location.x = 2.0\n")


def call(code):
    return NS(name="angelo_execute_python", arguments={"code": code})


class FileLoadsAlwaysAsk(unittest.TestCase):
    def test_every_file_replacing_operator_asks_in_every_spelling(self):
        self.assertGreaterEqual(len(worker_script.FILE_LOADS), 6)
        for name in worker_script.FILE_LOADS:
            for spelling in SPELLINGS:
                code = spelling.format(name=name)
                with self.subTest(name=name, code=code):
                    self.assertEqual(worker_script.file_load({"code": code}), name)   # The class the worker refuses.
                    self.assertTrue(trusted_scripts.consent_excluded(call(code)))

    def test_a_script_that_replaces_nothing_keeps_its_consent(self):
        for code in PLAIN:
            with self.subTest(code=code):
                self.assertIsNone(worker_script.file_load({"code": code}))
                self.assertFalse(trusted_scripts.consent_excluded(call(code)))

    def test_the_card_offers_no_standing_consent_for_one(self):
        """``offers_always_allow`` and ``script_consent_kind`` read the same rule, so neither can widen it."""
        source = Path(trusted_scripts.__file__).read_text(encoding="utf-8")
        for function in ("script_consent_kind", "offers_always_allow"):
            body = source.split("def %s(" % function, 1)[1].split("\n    def ", 1)[0]
            with self.subTest(function=function):
                self.assertIn("consent_excluded(call)", body)


class TheCardSaysWhy(unittest.TestCase):
    def reasons(self, scripts):
        """``{(consent, code): (runs unasked, the card's (msgid, fields))}`` for each script in each consent."""
        managed_policy.reset(Policy())
        self.addCleanup(managed_policy.reset)
        always_allow.detach()
        self.addCleanup(always_allow.detach)
        found = {}
        for consent in ("none", "always", "chat"):
            with tempfile.TemporaryDirectory() as directory:
                controller = Controller(directory)
                try:
                    controller.busy = True
                    controller.set_document(("isolated",))
                    controller.request_checkpoint = "9cf4e420-dcdb-4f45-bc8a-d9d439ce9aac"
                    angelo_guard_twin.harness(always_allow.set_enabled, consent == "always")   # The person's choices.
                    angelo_guard_twin.harness(controller.set_trusted_scripts, consent == "chat")   # Trust scripts for this chat, as its UI does.
                    for index, code in enumerate(scripts):
                        identity = "script-%d" % index
                        controller.request_tool(identity, "angelo_execute_python",
                                                {"description": "A step", "code": code}, lambda result: None)
                        asked = controller.pending[controller.state["id"] + "/" + identity]
                        found[consent, code] = (controller._automatic_script(asked),
                                                approval_wait.auto_reason(controller, asked))
                finally:
                    always_allow.set_enabled(False)
                    controller.close()
        return found

    def test_a_file_replacing_script_asks_in_every_consent_and_its_card_says_so(self):
        scripts = [SPELLINGS[index % len(SPELLINGS)].format(name=name)
                   for index, name in enumerate(worker_script.FILE_LOADS)]
        line = auto_reasons.LINES[auto_reasons.SCRIPT_FILE_LOAD]
        self.assertNotEqual(line, auto_reasons.LINES[auto_reasons.SCRIPT_EXCLUDED])
        self.assertNotIn("preferences", line)
        for (consent, code), (runs, reason) in self.reasons(scripts).items():
            with self.subTest(consent=consent, code=code):
                self.assertFalse(runs)
                self.assertEqual(reason, (line, {}))

    def test_the_other_scripts_keep_their_lines(self):
        """Preferences and the add-on keep their line (a script naming both a file load and preferences too: the
        older rule's line stands); a script that replaces nothing has none once scripts are allowed."""
        excluded = ["bpy.context.preferences.view.ui_scale = 2", "import angelo_agent",
                    "import bpy\nbpy.context.preferences.view.ui_scale = 2\nbpy.ops.wm.read_homefile()\n"]
        found = self.reasons(excluded + list(PLAIN))
        for (consent, code), (runs, reason) in found.items():
            with self.subTest(consent=consent, code=code):
                if code in excluded:
                    self.assertFalse(runs)
                    self.assertEqual(reason, (auto_reasons.LINES[auto_reasons.SCRIPT_EXCLUDED], {}))
                else:
                    self.assertEqual(runs, consent != "none")
                    self.assertIsNone(reason)


if __name__ == "__main__":
    unittest.main()
