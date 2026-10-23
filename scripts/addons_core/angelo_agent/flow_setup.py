# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a Generation Flow step still waits for, in the interface's words: setup is not a failure.

A freshly opened template said "Can't run" in red before the person had done anything (1.0.5), and after an account
and a model were chosen it still did ("'Image': pick an image"), as did a new node from the Add menu. A step that only
lacks an account or a model (``flow_graph.connection_needs``), or something nobody has filled in yet
(``flow_graph``'s ``setup``: an image, a prompt, a link, or the same for a step before it) is drawn neutrally with
what to do next. Red stays for a failed run and for a block the person's own choices caused (a model of the wrong
kind, too many references, a missing library image, a broken recipe).
"""

from .ui_text import n_

SETUP_LABELS = {"connect": n_("Connect an account to run"), "reconnect": n_("Connect an account to run"),
                "choose": n_("Choose a model to run")}
SETUP_TEXT = {"prompt": n_("Add a prompt or link a Text node to run"),
              "prompt_or_reference": n_("Add a prompt or a reference image to run"),
              "image": n_("Pick an image to run"), "link": n_("Link a Generate 3D step to place its model"),
              "upstream": n_("Set up the steps before this one to run"), "recipe": n_("Choose a flow to run")}
# A run the person stopped is not a failure: said neutrally. Both said in the interface's words, never the agent's
# reason ("the last attempt failed; running again is a new paid generation").
STOPPED_TEXT = n_("Stopped before it finished. Running it again starts a new paid generation.")
FAILED_TEXT = n_("The last attempt failed. Running it again starts a new paid generation.")
def default_names():
    """The step names Angelo itself gives (a template's steps, a new node's type name): interface words, shown in the
    interface's language. Any other name is the person's own and is shown as typed."""
    global _defaults
    if _defaults is None:
        from .flow_starters import STARTERS
        try:
            from .flow_nodes import NODES
        except ImportError:   # No Blender (host tests): the templates' names only.
            NODES = ()
        _defaults = frozenset({node["id"] for entry in STARTERS for node in entry["flow"]["nodes"]}
                              | {cls.bl_label for cls in NODES})
    return _defaults


_defaults = None
# A new node's type name as its step name, in Angelo's context so Angelo's catalogue wins over Blender's own word for
# the same English ("Text", "Image"): test_native_flow_setup requires one for every node type and template step.
NODE_STEP_NAMES = (n_("Image", "Angelo"), n_("Scene Capture", "Angelo"), n_("Text", "Angelo"),
                   n_("Generate Image", "Angelo"), n_("Generate 3D", "Angelo"), n_("Place in Scene", "Angelo"),
                   n_("Subflow", "Angelo"))


def step_label(name):
    """A step's name as shown (never as stored: the name is the step's identity for the agent and its outputs). A
    default name is translated, in Angelo's context first (iface_), so Blender's sense of the same English never wins;
    Blender's ``.001`` suffix stays; a recipe's ``<use>/<step>`` is shown part by part; a typed name stays as typed."""
    from .ui_text import iface_

    def one(part):
        base, dot, number = part.rpartition(".")
        base, suffix = (base, "." + number) if dot and number.isdigit() and len(number) == 3 else (part, "")
        return (iface_(base) if base in default_names() else base) + suffix
    return "/".join(one(part) for part in name.split("/"))


def waiting_text(waits):
    """A waiting step's one line: the step it waits for by name, or how many (1.0.5: "Waiting for inputs" then
    "waits for '3D model'" said it twice). ``waits`` are step names, which are the person's and never translated."""
    from .ui_text import iface_, plural_
    if len(waits) == 1:
        return iface_("Waiting for '{step}'", step=step_label(waits[0]))
    return plural_(len(waits), "Waiting for {count} step", "Waiting for {count} steps")


# flow_host.LABELS, drawn through Blender's own label translation, marked here so they reach the catalogues.
STATE_WORDS = (n_("Some variants ready"), n_("Not generated yet"), n_("Out of date"), n_("Waiting for inputs"),
               n_("Can't run"))


def setup_need(tree, name):
    """The account or model a step waits for (flow_graph.connection_needs: connect, reconnect or choose), or None."""
    from . import flow_host
    from .flow_graph import need_for
    needs = (flow_host._cache.get(tree.name) or {}).get("needs") or {}
    return need_for(needs, name)
