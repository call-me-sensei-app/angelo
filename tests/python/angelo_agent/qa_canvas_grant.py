# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A grant control on one of Angelo's own canvases (Media Studio, the Flows bar) clicked as the person clicks it, and
the click no person made refused (GUI suites, 2.0.0).

A canvas runs a click that grants (Generate, Enhance, a workflow's run, Run) in the grant scope of the person's press
on it: C mints that press's ticket from the event Blender delivered (``grant_guard.ticketed``,
doc/angelo/grant_operators.rst). A suite that calls the canvas's input handler straight from Python (a hand-built
event, ``qa_operator.stand_in``) makes no press C saw, so the grant refuses it: ``refused`` keeps that as a check. A
suite grants with ``press``: the pointer moves onto the control and presses through the window (``qa_input``), and
the suite reads the effect a phase later (``qa_input.after_clicks``).
"""


def rect_of(hits, action, value=None):
    """The rect ``(x, y, w, h)`` (region pixels) of the drawn control ``action`` (with ``value``, if given)."""
    return next(rect for rect, found, arg in hits if found == action and (value is None or arg == value))


def press(window, region, hits, action, value=None):
    """The person's press on a drawn control, through ``window``: C mints its ticket, the canvas runs it in that
    scope. Unmirrored layouts (the suites run in English)."""
    import qa_input
    qa_input.window_click(window, region, rect_of(hits, action, value), action)


def refused(state, scripted):
    """Whether ``scripted()`` (a click no person made) was refused by the grant guard: Studio turns the refusal into
    its notice (``studio_state.refusable``), a canvas without one raises it. The notice is cleared after."""
    from angelo_agent import grant_guard
    from angelo_agent.ui_text import rpt_
    state["notice"] = None
    try:
        scripted()
        said = str(state.get("notice") or "")
    except RuntimeError as error:
        said = str(error)
    state["notice"] = None
    return rpt_(grant_guard.NOT_YOURS) in said


def dialog_open():
    """Whether an operator's confirmation dialog is drawn now (``_angelo_jobs.close_dialog_texts``)."""
    from _bpy import _angelo_jobs
    return bool(_angelo_jobs.close_dialog_texts())
