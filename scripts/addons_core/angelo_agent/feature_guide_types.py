# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The ``Guide`` entry of the agent's feature guide (``feature_guide``) and the placeholders its examples use, shared by
the modules that hold guide entries (``feature_guide``, ``feature_guide_scene``). Model-visible English. No Blender."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Guide:
    id: str
    name: str
    what: str           # What it does, one or two sentences.
    reach: str          # Where the person finds it, by the labels they see (English; relayed in their language).
    agent: str          # How the agent turns it on or uses it.
    menu: tuple = ()    # chat_destinations feature ids that reach it.
    doc: str = ""       # Its contract: doc/angelo/<doc>.rst.
    requires: tuple = ()   # Operator idnames that must be registered for the entry to be offered.
    suggest: str = ""   # When to recommend it unasked.
    heading: str = ""   # Its section in the people's guide (doc/angelo/features.rst); not sent to the model.
    examples: tuple = ()   # Calls as the agent makes them: {"tool", "arguments"} or {"operation"} of a program;
    # test_native_feature_guide validates each with the tool's own validator, so the guide can't teach a refused call.


U = "123e4567-e89b-42d3-a456-426614174000"   # Example ids and handles: placeholders the note says to replace.
H = "scn:" + "a" * 32 + ":1"
