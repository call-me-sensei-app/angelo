# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The reviewer's seventh read (piece 10's re-review, 2026-11-16), its NITs, for test_blender_confinement_escapes.
Each check failed before its fix and judges the class, not the route.

A callback Python or a library calls with another thread's data never reaches a value the script stored when the
caller is Angelo's (not confined): ``threading.excepthook`` (a thread's uncaught exception, for threads started
before and after the script stored it), ``sys.unraisablehook`` (an exception in a finalizer), ``sys.excepthook`` (an
error the interpreter prints), and an asyncio loop's task factory and exception handler (set through the loop's
methods or as attributes; during the script and after it). The script's own thread and loop still reach what it set.
Each error is reported by Angelo's own hook instead (Python's default: written to ``sys.stderr``, the loop's to the
``asyncio`` logger), captured here so the suite's log holds no traceback the runner would count.
"""

import contextlib
import io
import json
import logging
import sys
import threading

from host_waits import CAP_S   # A completion wait's cap: a hang ends it, never a slow machine.

_WAIT = "GO.set()\nFINISHED.wait(%s)\n" % CAP_S


def _while_the_script_waits(run, source, angelo, **names):
    """Runs `source` (which sets GO, then waits for FINISHED) while an Angelo thread runs `angelo` once GO is set."""
    go, finished = threading.Event(), threading.Event()

    def helper():
        try:
            if go.wait(CAP_S):
                angelo()
        finally:
            finished.set()

    thread = threading.Thread(target=helper, daemon=True)
    thread.start()
    outcome = run(source, GO=go, FINISHED=finished, **names)
    thread.join(CAP_S)
    return outcome


@contextlib.contextmanager
def _reported():
    """What Angelo's own hooks report meanwhile (``sys.stderr``, the ``asyncio`` logger), kept from the suite's log."""
    stderr, sys.stderr = sys.stderr, io.StringIO()
    records = []

    class Keep(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("asyncio")
    keep, propagate = Keep(), logger.propagate
    logger.addHandler(keep)
    logger.propagate = False
    try:
        yield sys.stderr, records
    finally:
        logger.removeHandler(keep)
        logger.propagate = propagate
        sys.stderr = stderr


def thread_errors(run, check, reported):
    """A thread of Angelo's that fails while the script runs (one started before it stored a hook, one after) reaches
    Angelo's ``threading.excepthook``; a thread the script starts reaches the script's."""
    held, raised = {}, []
    release = threading.Event()

    def fails(label):
        raised.append(label)
        raise ValueError("Angelo's error (%s)" % label)

    def waits_then_fails():
        release.wait(CAP_S)
        fails("before")

    earlier = threading.Thread(target=waits_then_fails, daemon=True)
    earlier.start()

    def angelo():
        release.set()
        earlier.join(CAP_S)
        later = threading.Thread(target=fails, args=("after",), daemon=True)
        later.start()
        later.join(CAP_S)

    outcome = _while_the_script_waits(
        run, "import threading\nthreading.excepthook = HELD.setdefault('errors', []).append\n" + _WAIT, angelo,
        HELD=held)
    angelos = [str(args.exc_value) for args in held.get("errors", ())]
    own = run("import threading\nseen = []\nthreading.excepthook = seen.append\n"
              "def mine():\n    raise ValueError('the script\\'s own')\n"
              "t = threading.Thread(target=mine)\nt.start()\nt.join(%s)\n"
              "result = [str(a.exc_value) for a in seen]\n" % CAP_S)
    text = reported[0].getvalue()
    told = all("Angelo's error (%s)" % label in text for label in ("before", "after"))
    check("thread_errors_reach_no_hook_a_script_set",
          sorted(raised) == ["after", "before"] and not angelos and told
          and "the script's own" in str(own.get("result")),
          (outcome, raised, angelos, told, own))


class _Noisy:
    def __del__(self):
        raise ValueError("Angelo's unraisable error")


def unraisable_errors(run, check, reported):
    """A finalizer of Angelo's that fails on its thread while the script runs reaches Angelo's
    ``sys.unraisablehook``."""
    held = {}

    def angelo():
        noisy = _Noisy()
        del noisy

    outcome = _while_the_script_waits(
        run, "import sys\nsys.unraisablehook = HELD.setdefault('errors', []).append\n" + _WAIT, angelo, HELD=held)
    seen = [str(getattr(args, "exc_value", args)) for args in held.get("errors", ())]
    told = "Angelo's unraisable error" in reported[0].getvalue()
    check("unraisable_errors_reach_no_hook_a_script_set", not seen and told, (outcome, seen, told))


def printed_errors(run, check, reported):
    """An error the interpreter prints on a thread of Angelo's (``PyErr_Print``) while the script runs reaches
    Angelo's ``sys.excepthook``."""
    held, printed = {}, []

    def angelo():
        import ctypes
        printed.append(ctypes.pythonapi.PyRun_SimpleString(b"raise ValueError(\"Angelo's printed error\")"))

    outcome = _while_the_script_waits(
        run, "import sys\nsys.excepthook = lambda *a: HELD.setdefault('errors', []).append(a)\n" + _WAIT, angelo,
        HELD=held)
    seen = [str(args[1]) for args in held.get("errors", ())]
    told = "Angelo's printed error" in reported[0].getvalue()
    check("printed_errors_reach_no_hook_a_script_set", printed == [-1] and not seen and told,
          (outcome, printed, seen, told))


_LOOP_SCRIPTS = {
    "methods": "LOOP.set_task_factory(factory)\nLOOP.set_exception_handler(handler)\n",
    "attributes": "LOOP._task_factory = factory\nLOOP._exception_handler = handler\n",
}
_LOOP_CALLBACKS = (
    "import asyncio\n"
    "def factory(loop, coro, **kwargs):\n"
    "    HELD.setdefault('tasks', []).append(coro)\n"
    "    return asyncio.Task(coro, loop=loop, **kwargs)\n"
    "def handler(loop, context):\n"
    "    HELD.setdefault('errors', []).append(context)\n"
)


def _fails():
    raise ValueError("Angelo's loop error")


async def _angelo_work():
    return "Angelo's result"


def loop_callbacks(run, check, reported):
    """A loop of Angelo's (its own thread) creates tasks and reports an error while the script runs and after it
    ends: neither reaches the factory or handler the script set. The script's own loop uses its own."""
    import asyncio
    results = {}
    for name, setter in _LOOP_SCRIPTS.items():
        loop = asyncio.new_event_loop()
        runner = threading.Thread(target=loop.run_forever, daemon=True)
        runner.start()
        done = []

        def angelo():
            done.append(asyncio.run_coroutine_threadsafe(_angelo_work(), loop).result(CAP_S))
            loop.call_soon_threadsafe(_fails)
            asyncio.run_coroutine_threadsafe(asyncio.sleep(0.05), loop).result(CAP_S)

        held_during, held_after = {}, {}
        during = _while_the_script_waits(run, _LOOP_CALLBACKS + setter + _WAIT, angelo, HELD=held_during, LOOP=loop)
        after = run(_LOOP_CALLBACKS + setter, HELD=held_after, LOOP=loop)
        angelo()   # Once the script has ended.
        loop.call_soon_threadsafe(loop.stop)
        runner.join(CAP_S)
        loop.close()
        results[name] = (during.get("refused"), during.get("error"), after.get("refused"), after.get("error"),
                         len(done), sorted(held_during), sorted(held_after))
    own = run(_LOOP_CALLBACKS + "async def main():\n    return 1\n"
              "async def outer():\n    asyncio.get_running_loop().set_task_factory(factory)\n"
              "    return await asyncio.get_running_loop().create_task(main())\n"
              "HELD = {}\nresult = [asyncio.run(outer()), len(HELD.get('tasks', ()))]\n", HELD={})
    clean = all(count == 2 and not during and not after
                for _r, _e, _ar, _ae, count, during, after in results.values())
    # The script's own loop: its main task and `asyncio.run`'s own shutdown tasks go through its factory.
    own_tasks = json.loads(own.get("result") or "{}").get("result") or [0, 0]
    told = sum(1 for record in reported[1] if record.exc_info and "Angelo's loop error" in str(record.exc_info[1]))
    check("loop_callbacks_never_see_angelos_work", clean and told == 4 and own_tasks[0] == 1 and own_tasks[1] >= 1,
          (results, told, own))


def run_all(run, check):
    for each in (thread_errors, unraisable_errors, printed_errors, loop_callbacks):
        with _reported() as reported:
            each(run, check, reported)
