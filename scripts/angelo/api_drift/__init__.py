# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The generated table of Blender Python names that Blender renamed or removed since 3.6.

Scripts keep writing names from the Blender their author (often a model) learned. ``dump.py`` records the
API one Blender release exposes; ``table.py`` compares the dumps of every release from 3.6 to the target
with Blender's own file-versioning renames and a short cited overlay (``curated.json``), and writes the one
table that Angelo's three uses read: the error sentence a failed name gains (C++), the refusal before a
script runs, and the note after it runs. See doc/angelo/agent_execution.rst, "Removed Blender API".
"""
