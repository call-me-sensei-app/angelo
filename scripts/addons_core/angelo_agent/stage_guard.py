# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every evaluation of a stage is held natively and released only for one a check just passed (doc/angelo/
agent_execution.rst, "Links between staged copies"; B1).

A stage scene carries ``HOLD``: anything but 0 makes Blender's view-layer update and frame change return before
building or evaluating its graph, and each pass consumes a release (``BKE_angelo_stage_take_hold``, scene.cc).
``guarded`` (every evaluation the add-on starts) checks, releases, evaluates, holds again and runs the tripwire;
``_before`` (``depsgraph_update_pre`` / ``frame_change_pre``) releases any other evaluation only after a full check
passes on the main thread; ``_observed`` holds the graph it built to that check. A refusal or failure leaves the
stage held and waits for ``audit`` (the next check, a suspension, commit's last check).
"""

from types import SimpleNamespace

from .program_jobs import ProgramError
from .program_dependencies import _bpy

_LAST = {}   # Stage scene marker -> the Router of its last complete check; dropped by ``forget``.
_VIOLATIONS = {}   # Stage scene marker -> (text, code) a stopped or unchecked evaluation left for ``audit``.
_FRESH = set()   # Stage scene markers inside ``guarded``: evaluations its own check just approved.
HOLD = "_angelo_stage_hold"   # On a stage scene: anything but 0 holds every evaluation (BKE_angelo_stage_held).
STOPPED = ("{reason} (Found when staging was about to evaluate this edit: that evaluation was stopped, so nothing of "
           "the person's changed, and nothing was committed.)")


def _marker(stage):
    from .legacy_names import PROGRAM_STAGE
    scene = getattr(stage, "scene", None)
    return scene.get(PROGRAM_STAGE[0]) if scene is not None else None


def hold(scene, held=True):
    """Hold (or release, for one approved evaluation) every evaluation of a stage scene natively (scene.cc)."""
    scene[HOLD] = 1 if held else 0


def audit(stage):
    """Raise what a stopped or unchecked evaluation of the stage left since its last check, and hold the graph as
    it stands to that check (its allowance plus what the stage owns now)."""
    marker = _marker(stage)
    violation = _VIOLATIONS.pop(marker, None)
    if violation is not None:
        raise ProgramError(violation[0], code=violation[1])
    last = _LAST.get(marker)
    if last is not None:
        _held(stage.view_layer.depsgraph, last)


def _held(depsgraph, last):
    from . import stage_routes
    from . import stage_links
    if depsgraph is None:
        return   # Never evaluated: nothing was built.
    blocks = stage_links._blocks(last.stage)
    owned = {block.as_pointer() for block in blocks}
    stage_routes._tripwire(depsgraph, SimpleNamespace(blocks=blocks, allowed=last.allowed | owned))


def _main_thread():
    import threading
    return threading.current_thread() is threading.main_thread()


def _before(scene, *_args):
    """``depsgraph_update_pre`` / ``frame_change_pre`` of a stage scene: Blender rebuilds and evaluates its graph
    next unless it is held (it is, outside ``guarded``). Release it for this one evaluation only when a check of
    every link and path passes; otherwise it stays held, Blender returns without building or evaluating anything,
    and the refusal waits for the stage's next ``audit``. Off the main thread scene.cc holds it whatever this does."""
    from . import stage_routes
    marker = None
    try:
        from .legacy_names import PROGRAM_STAGE
        marker = scene.get(PROGRAM_STAGE[0]) if scene is not None else None
        if marker is None or not _main_thread():
            return   # Not a stage; or a job thread: whatever the hold is, scene.cc consumes no more than it.
        hold(scene)   # Held unless this call releases it: never a release left over from before.
        if marker in _FRESH:
            _FRESH.discard(marker)   # One-shot: ``guarded``'s check covers its first evaluation only.
            hold(scene, False)
            return
        last = _LAST.get(marker)
        if last is None or marker in _VIOLATIONS:
            return   # Held: nothing checked it yet, or a refusal or tripwire is pending.
        try:
            router = stage_routes._routes(last.stage, last)
        except ProgramError as refusal:
            _VIOLATIONS.setdefault(marker, (STOPPED.format(reason=str(refusal)), refusal.code or 'INTERNAL_ERROR',
                                             False))   # Stopped: nothing was evaluated.
            return
        _LAST[marker] = router
        hold(scene, False)
    except Exception as error:   # Held: whatever failed, nothing is evaluated.
        if marker is not None:
            _VIOLATIONS.setdefault(marker, (STOPPED.format(
                reason="A staging check could not run (%s)." % type(error).__name__), 'INTERNAL_ERROR', False))


def _observed(scene, depsgraph=None, *_args):
    """``depsgraph_update_post`` / ``frame_change_post``: hold the stage again after an evaluation ``_before``
    released, and hold what it built to the last check (a violation waits for ``audit``)."""
    marker = None
    try:
        from .legacy_names import PROGRAM_STAGE
        marker = scene.get(PROGRAM_STAGE[0]) if scene is not None else None
        if marker is None or marker in _FRESH or not _main_thread():
            return
        hold(scene)
        last = _LAST.get(marker)
        if last is not None and marker not in _VIOLATIONS:
            _held(depsgraph, last)
    except ProgramError as violation:
        _VIOLATIONS[marker] = (str(violation), 'INTERNAL_ERROR', True)   # Built: the person's data may have changed.
    except (ReferenceError, AttributeError):
        pass   # A stage being freed: its own cleanup forgets it.


def _watch():
    handlers = getattr(getattr(_bpy(), "app", None), "handlers", None)
    if handlers is None:
        return
    for chain, handler in ((handlers.depsgraph_update_pre, _before), (handlers.frame_change_pre, _before),
                           (handlers.depsgraph_update_post, _observed), (handlers.frame_change_post, _observed)):
        if chain and chain[-1] is handler:
            continue
        if handler in chain:   # Last in its chain: no handler added since runs between the check and the pass.
            chain.remove(handler)
        chain.append(handler)


def forget(stage):
    """Drop the stage's allowance; returns a pending violation (text, code, whether anything was built) or None."""
    marker = _marker(stage)
    _LAST.pop(marker, None)
    _FRESH.discard(marker)
    return _VIOLATIONS.pop(marker, None)


def disarm(stage):
    """After commit's last check: raise what is pending, then nothing releases the stage again (it stays held)."""
    audit(stage)
    forget(stage)


def guarded(stage, evaluate):
    """``evaluate()`` the stage after ``check``, released for exactly that call, held again whatever happens, then
    the ``tripwire`` over what it built. Every stage evaluation the add-on starts goes through here."""
    from . import stage_routes
    router = stage_routes.check(stage)
    marker = _marker(stage)
    _FRESH.add(marker)
    hold(stage.scene, False)
    try:
        result = evaluate()
    except Exception:
        _FRESH.discard(marker)
        hold(stage.scene)
        try:   # The evaluation's own failure is reported; what it built is still held to the check.
            stage_routes.tripwire(stage, router)
        except ProgramError as violation:
            _VIOLATIONS.setdefault(marker, (str(violation), 'INTERNAL_ERROR', True))
        except Exception:
            pass
        raise
    finally:
        _FRESH.discard(marker)
        hold(stage.scene)
    stage_routes.tripwire(stage, router)
    return result
