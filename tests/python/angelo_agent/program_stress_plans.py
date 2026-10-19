# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Recorded long-session plans for ``test_blender_program_stress.py`` (no Blender import).

The plans the toolset-21 gate recorded for the long task set
(``eval_live_tasks.build_long_session_tasks``), including the two shapes that
failed it:

* moves through ``angelo_set_transform``, whose ``object`` was the cube's name,
  its ``o<n>`` handle (the failure, from a Sonnet run) or a long handle, and
  through a program;
* "Change the bevel modifier" decided like a rebased Opus context: with no
  memory, only from the message. The
  ``observed`` variant configures the Bevel the observation lists, and applies
  the recorded ``bevel-and-shade`` recipe when it lists none (the failure);
  the ``recorded`` variant always applies that recipe, as the model did, and
  corrects itself from the host's refusal when there is one.

Plans are data, never invented beyond these recorded shapes. A plan that
names a value reads it from the prompt (the task's numbers, the observation's
handle and modifiers, the refusal's modifier name), as the model did, and
leaves programs' bindings to the host's inference from that observation, as
toolset-21 plans did.
"""

import json
import re

EVAL_OBJECT = "Eval Cube"
MOVE_VARIANTS = ("name", "short_handle", "long_handle", "program")
CHANGE_VARIANTS = ("observed", "recorded")
_NUMBER = r"(-?[0-9]+(?:\.[0-9]+)?)"
_MOVE = re.compile(r"Move 'Eval Cube' to location \(%s, %s, %s\)" % ((_NUMBER,) * 3))
_SCALE = re.compile(r"dimensions are exactly %s m along X, %s m along Y and %s m along Z" % ((_NUMBER,) * 3))
_BEVEL = re.compile(r"width of %s m and ([0-9]+) segments" % _NUMBER)
_MATERIAL = re.compile(r"material named '([^']+)' with base colour \(%s, %s, %s, 1\), roughness %s and metallic %s"
                       % ((_NUMBER,) * 5))
_CONFIGURE = re.compile(r"modifier_configure of '([^']+)'")
STOPPED = "The host stopped your last plan"


def observed_entry(text, name=EVAL_OBJECT):
    """The observation entry for ``name`` in a prompt (whole or delta), or None."""
    for line in text.splitlines():
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        for entry in value.get("objects") or () if isinstance(value, dict) else ():
            if isinstance(entry, dict) and entry.get("name") == name:
                return entry
    return None


def numbers(match):
    return [float(value) for value in match.groups()]


class LongSession:
    """Plans for one cycle; ``replay`` is the suite's Replay (live long handle and revision)."""

    def __init__(self, replay, cycle):
        self.replay = replay
        self.move = MOVE_VARIANTS[cycle % len(MOVE_VARIANTS)]
        self.change = CHANGE_VARIANTS[(cycle // len(MOVE_VARIANTS)) % len(CHANGE_VARIANTS)]
        self.recipe_bevel = cycle % 3 == 2  # The 5-task runs added the first bevel with the recipe.

    def target(self, text):
        """The cube's handle as this message's observation shows it, else the live long handle."""
        return (observed_entry(text) or {}).get("handle") or self.replay.handle

    def program(self, text, kind, parameters):
        return {"tool": "angelo_scene_program", "arguments": {"mode": "APPLY", "operations": [
            {"kind": kind, "target": self.target(text), "parameters": parameters}]}}

    def recipe(self, text, recipe, parameters):
        return {"tool": "angelo_apply_recipe", "arguments": {"recipe": recipe, "target": self.target(text),
                                                            "mode": "APPLY", "parameters": parameters}}

    def plan(self, text):
        prompt = text.split("\n", 1)[0]
        if prompt.startswith(STOPPED):
            return self.correction(text)
        if prompt.startswith("What are the current dimensions"):
            return {"reply": "Answered from the observation.", "operations": []}
        match = _MOVE.search(prompt)
        if match:
            return {"operations": [self.move_operation(text, numbers(match))]}
        match = _SCALE.search(prompt)
        if match:
            size = numbers(match)
            plan = self.program(text, "transform", {"mode": "SET", "scale": [value / 2 for value in size]})
            plan["arguments"]["expected_checks"] = [{"kind": "dimensions", "target": self.target(text),
                                                     "expected": size, "tolerance": 0.0001, "space": "WORLD"}]
            return {"operations": [plan]}
        match = _BEVEL.search(prompt)
        if match and prompt.startswith("Add a non-destructive bevel"):
            width, segments = float(match.group(1)), int(match.group(2))
            if self.recipe_bevel:
                return {"operations": [self.recipe(text, "bevel-and-shade", {"width": width, "segments": segments})]}
            return {"operations": [self.program(text, "modifier_add", {
                "type": "BEVEL", "name": "Bevel", "settings": {"width": width, "segments": segments}})]}
        if match and prompt.startswith("Change the bevel modifier"):
            width, segments = float(match.group(1)), int(match.group(2))
            bevels = [name for name, kind in ((observed_entry(text) or {}).get("modifiers") or {}).items()
                      if kind == "BEVEL"]
            if self.change == "observed" and bevels:
                return {"operations": [self.configure(text, bevels[0], width, segments)]}
            self.pending_change = (width, segments)
            return {"operations": [self.recipe(text, "bevel-and-shade", {"width": width, "segments": segments})]}
        match = _MATERIAL.search(prompt)
        if match:
            red, green, blue, roughness, metallic = (float(value) for value in match.groups()[1:])
            plan = self.program(text, "material_principled", {"name": match.group(1), "inputs": {
                "Base Color": [red, green, blue, 1], "Roughness": roughness, "Metallic": metallic}})
            plan["arguments"]["expected_checks"] = [{"kind": "material_assignment", "target": self.target(text),
                                                     "material": match.group(1), "all_faces": True}]
            return {"operations": [plan]}
        return None

    def move_operation(self, text, location):
        if self.move == "program":
            return self.program(text, "transform", {"mode": "SET", "location": location})
        target = {"name": EVAL_OBJECT, "long_handle": self.replay.handle,
                  "short_handle": (observed_entry(text) or {}).get("handle", EVAL_OBJECT)}[self.move]
        return {"tool": "angelo_set_transform", "arguments": {"object": target, "location": location}}

    def configure(self, text, modifier, width, segments):
        return self.program(text, "modifier_configure", {"modifier": modifier, "type": "BEVEL",
                                                         "settings": {"width": width, "segments": segments}})

    def correction(self, text):
        """The corrected plan a refusal asks for: configure the Bevel it names, or move by name."""
        named = _CONFIGURE.search(text)
        if named and getattr(self, "pending_change", None):
            return {"operations": [self.configure(text, named.group(1), *self.pending_change)]}
        match = _MOVE.search(getattr(self.replay, "last_prompt", ""))
        if match and "name or handle" in text:
            return {"operations": [{"tool": "angelo_set_transform", "arguments": {
                "object": EVAL_OBJECT, "location": numbers(match)}}]}
        return {"reply": "No recorded correction for this refusal.", "operations": []}
