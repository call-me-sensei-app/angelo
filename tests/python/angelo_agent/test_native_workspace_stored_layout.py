# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``workspace_edit.cc`` gives a duplicated workspace's stored layout only to that workspace (1.0.5).

A switch applied before the copy's own (queued earlier in the same pass) took the stored layout for the wrong
workspace and crashed ``ED_workspace_change`` on a null layout. The app's proof, crash on the unguarded code and pass
with the guard, is ``test_blender_workspace_stored_layout``; this pins the guard in the source.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "source/blender/editors/screen/workspace_edit.cc"


def function(source, name):
    start = re.search(r"^static [^\n]*\b%s\(" % name, source, re.M)
    if start is None:
        start = re.search(r"^bool %s\(" % name, source, re.M)
    if start is None:
        raise AssertionError("workspace_edit.cc has no %s (the stored-layout guard)" % name)
    body = source[start.start():]
    return body[:body.index("\n}\n") + 3]


class StoredLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text(encoding="utf-8")

    def test_the_stored_layout_is_used_only_by_the_workspace_that_owns_it(self):
        stored = function(self.source, "workspace_stored_layout_for")
        self.assertIn("workspace_owns_layout(workspace_new, stored)", stored)
        layout = function(self.source, "workspace_change_get_new_layout")
        self.assertIn("workspace_stored_layout_for(workspace_new, win)", layout)
        self.assertNotIn("layout_new = win->workspace_hook->temp_layout_store", layout, "never unchecked")

    def test_it_is_kept_for_the_copy_when_another_switch_comes_first(self):
        change = function(self.source, "ED_workspace_change")
        clear = change.index("temp_layout_store = nullptr")
        self.assertIn("if (workspace_stored_layout_for(workspace_new, win))", change[:clear],
                      "cleared only when this change used it")

    def test_ownership_walks_the_workspaces_own_layouts(self):
        owns = function(self.source, "workspace_owns_layout")
        self.assertIn("workspace->layouts.first", owns)
        self.assertIn("iter == layout", owns)


if __name__ == "__main__":
    unittest.main()
