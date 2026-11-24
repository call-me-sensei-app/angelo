# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Descriptors and handles by number, and what a script leaves on its thread (confinement_lookup_cases, from
test_blender_script_confinement; break-it-confine-reparse head-12/14 reads, the coordinator's tracking ruling):
Angelo's own private file's descriptor, and a pipe made before the region (as Angelo's to its agent runtime), are
refused; the script's own pipes, sockets, multiprocessing and asyncio pipes, and print/logging, work; Windows' name
lookups judge names and pipes; an async generator hook a script set runs confined after its region.
Run only inside Blender, from the suite.
"""

import os
import shutil
import sys
import tempfile

import host_waits


def descriptors(run, check, PRIVATE, PROCESS, KEY):
    """A descriptor of Angelo's own private file, by number: closing, replacing, seeking or changing it is refused
    (a later open would reuse its number, or Angelo would read the script's file as its own); closing a range is
    refused; the script's own descriptors still close; a new session on a terminal is refused."""
    with open(KEY, "rb") as held, tempfile.TemporaryDirectory(prefix="angelo-descriptor-cases-") as folder:
        angelo = held.fileno()
        for label, source in (("close", "os.close(FD)"), ("dup2", "os.dup2(MINE, FD)"),
                              ("lseek", "os.lseek(FD, 0, 2)"), ("set_inheritable", "os.set_inheritable(FD, True)"),
                              ("closerange", "os.closerange(FD, FD + 1)")):
            outcome = run("import os\nMINE = os.open(OWN, os.O_RDWR | os.O_CREAT)\ntry:\n    " + source +
                          "\nfinally:\n    os.close(MINE)\n", FD=angelo, OWN=os.path.join(folder, label))
            check("descriptor_refused_%s" % label, outcome.get("refused") == PRIVATE, outcome)
        check("descriptor_still_angelos", os.fstat(angelo).st_size == os.path.getsize(KEY), angelo)
        outcome = run("import os\nfd = os.open(OWN, os.O_RDWR | os.O_CREAT)\nos.lseek(fd, 0, 0)\nos.close(fd)\n",
                      OWN=os.path.join(folder, "own"))
        check("descriptor_own_still_closes", outcome.get("refused") is None and "error" not in outcome, outcome)
        if hasattr(os, "login_tty"):
            outcome = run("import os\nos.login_tty(FD)", FD=angelo)
            check("descriptor_refused_login_tty", outcome.get("refused") == PROCESS, outcome)


def made_objects(run, check, PRIVATE):
    """A descriptor or handle that names no file is used only when confined code made its object (the coordinator's
    ruling: track): the script's own os.pipe, a dup of it, socket.socketpair, multiprocessing's Pipe and an asyncio
    read pipe work; print, sys.stderr and logging to the console work; a pipe made before the region (as Angelo's to
    its agent runtime) is refused for a write, fcntl and a dup2 onto it."""
    own = [("pipe", "r, w = os.pipe()\nos.write(w, b'x')\nassert os.read(r, 1) == b'x'\nos.close(r)\nos.close(w)\n"),
           ("dup", "r, w = os.pipe()\nw2 = os.dup(w)\nos.write(w2, b'x')\nassert os.read(r, 1) == b'x'\n"
                   "for fd in (r, w, w2):\n    os.close(fd)\n"),
           ("socketpair", "import socket\na, b = socket.socketpair()\n" +
                          ("a.sendall(b'x')\nassert b.recv(1) == b'x'\n" if sys.platform == "win32" else   # Handles.
                           "os.write(a.fileno(), b'x')\nassert os.read(b.fileno(), 1) == b'x'\n") +
                          "a.close()\nb.close()\n"),
           ("multiprocessing_pipe", "from multiprocessing.connection import Pipe\nfor duplex in (True, False):\n"
                                    "    a, b = Pipe(duplex)\n    b.send('x')\n    assert a.recv() == 'x'\n"
                                    "    a.close()\n    b.close()\n"),
           ("asyncio_pipe", "import asyncio\nasync def main():\n    r, w = os.pipe()\n"
                            "    loop = asyncio.get_running_loop()\n    reader = asyncio.StreamReader()\n"
                            "    transport, _ = await loop.connect_read_pipe(\n"
                            "        lambda: asyncio.StreamReaderProtocol(reader), os.fdopen(r, 'rb', 0))\n"
                            "    os.write(w, b'x')\n    data = await reader.read(1)\n    transport.close()\n"
                            "    os.close(w)\n    return data\nassert asyncio.run(main()) == b'x'\n"),
           ("print", "import sys, logging\nprint('angelo-made-objects')\nsys.stderr.write('angelo-made-objects\\n')\n"
                     "logging.getLogger('angelo-made-objects').warning('angelo-made-objects')\n")]
    if sys.platform == "win32":
        own = [("winapi_pipe", "import _winapi\nr, w = _winapi.CreatePipe(None, 0)\n_winapi.WriteFile(w, b'x')\n"
                               "assert _winapi.ReadFile(r, 1)[0] == b'x'\n_winapi.CloseHandle(r)\n"
                               "_winapi.CloseHandle(w)\n")] + own[:4] + own[5:]
    for label, source in own:
        outcome = run("import os\n" + source)
        check("made_own_%s_works" % label, outcome.get("refused") is None and "error" not in outcome, outcome)
    # A pipe this process made before the region: what the script may never write into or change.
    r, w = os.pipe()
    try:
        # The dup2's source is a pipe of the script's own, so the check reaches the dup2 onto Angelo's number on every
        # platform (build3 head 23: os.devnull was refused first, on Windows).
        for label, source in (("write", "os.write(W, b'injected')"), ("dup2", "m, mw = os.pipe()\n"
                                                                    "try:\n    os.dup2(m, W)\nfinally:\n"
                                                                    "    os.close(m)\n    os.close(mw)\n"),
                              ("close", "os.close(W)")) + ((("fcntl", "import fcntl\nfcntl.fcntl(W, fcntl.F_GETFL)"),)
                                                           if sys.platform != "win32" else ()):
            outcome = run("import os\n" + source, W=w)
            check("made_angelos_pipe_refused_%s" % label, outcome.get("refused") == PRIVATE, outcome)
        os.write(w, b"ok")
        check("made_angelos_pipe_intact", os.read(r, 2) == b"ok")
    finally:
        os.close(r)
        os.close(w)
    if sys.platform == "win32":
        import _winapi
        hr, hw = _winapi.CreatePipe(None, 0)
        try:
            outcome = run("import _winapi\n_winapi.WriteFile(H, b'injected')", H=hw)
            check("made_angelos_handle_refused_write", outcome.get("refused") == PRIVATE, outcome)
            outcome = run("import _overlapped\n_overlapped.Overlapped().WriteFile(H, b'injected')", H=hw)
            check("made_angelos_handle_refused_overlapped_write", outcome.get("refused") == PRIVATE, outcome)
        finally:
            _winapi.CloseHandle(hr)
            _winapi.CloseHandle(hw)


class _AngeloThread:
    """A thread of Angelo's own (started before the region, so never confined) that makes what it is asked to while a
    script runs: the script asks through events and gets the number back."""

    def __init__(self, make):
        import threading
        self.make, self.ask, self.made, self.number, self.stop = make, threading.Event(), threading.Event(), None, False
        self.held = []
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop:
            if self.ask.wait(0.1):   # window: the worker's look at its stop flag between asks; work comes by `ask`
                self.ask.clear()
                self.held.append(self.make())
                self.number = self.held[-1][-1] if isinstance(self.held[-1], tuple) else self.held[-1]
                self.made.set()

    def close(self):
        self.stop = True
        self.thread.join(host_waits.CAP_S)
        for item in self.held:
            for fd in item if isinstance(item, tuple) else (item,):
                try:
                    os.close(fd)
                except OSError:
                    pass


def owned_numbers(run, check, PRIVATE):
    """Head-16 read MUST-1/2: a number is the script's only while it is the one a maker handed it and nothing has closed
    or passed it on since. Angelo's own thread makes a pipe (or an eventfd) while the script runs, after the script
    made and closed its own (directly, and through a file object that closed it unseen by os.close), so the number,
    and on macOS the pipe's identity, is often the same: the script's write into it is refused. The script's own pipe,
    multiprocessing Pipe and socketpair reach their end (nothing of Angelo's holds them open)."""
    cases = [("pipe_after_close", os.pipe,
              "for _ in range(64):\n    r, w = os.pipe()\n    os.close(r)\n    os.close(w)\n"),
             ("pipe_after_file_close", os.pipe,
              "for _ in range(64):\n    r, w = os.pipe()\n    os.close(r)\n    os.fdopen(w, 'wb').close()\n")]
    if hasattr(os, "eventfd"):
        cases.append(("eventfd", lambda: os.eventfd(0), "mine = os.eventfd(0)\nos.eventfd_write(mine, 1)\n"
                                                         "assert os.eventfd_read(mine) == 1\n"))
    for label, make, before in cases:
        angelo = _AngeloThread(make)
        try:
            held = {}
            outcome = run("import os\n" + before + "ASK.set()\nassert MADE.wait(CAP)\nN = HELD['n'] = THREAD.number\n"
                          "attempts = (('write', lambda: os.write(N, b'injected')), ('close', lambda: os.close(N)),\n"
                          "            ('lseek', lambda: os.lseek(N, 0, 0)))\n"
                          "for name, attempt in attempts:\n"
                          "    try:\n        attempt()\n        HELD.setdefault('passed', []).append(name)\n"
                          "    except PermissionError as error:\n"
                          "        HELD.setdefault('refused', []).append(getattr(error, 'reason', repr(error)))\n"
                          "    except OSError:\n        HELD.setdefault('os_error', []).append(name)\n",
                          ASK=angelo.ask, MADE=angelo.made, THREAD=angelo, HELD=held, CAP=host_waits.CAP_S)
            check("owned_number_reuse_refused_%s" % label, "error" not in outcome and not held.get("passed") and
                  held.get("refused") and set(held["refused"]) == {PRIVATE}, (outcome, held))
        finally:
            angelo.close()
    own = [("pipe_eof", "r, w = os.pipe()\nos.write(w, b'x')\nos.close(w)\nassert os.read(r, 9) == b'x'\n"
                        "assert os.read(r, 9) == b''\nos.close(r)\n"),
           ("multiprocessing_eof", "from multiprocessing.connection import Pipe\na, b = Pipe()\nb.send('x')\n"
                                   "b.close()\nassert a.recv() == 'x'\ntry:\n    a.recv()\n    raise AssertionError\n"
                                   "except EOFError:\n    pass\na.close()\n"),
           ("socketpair_eof", "import socket\na, b = socket.socketpair()\nb.sendall(b'x')\nb.close()\n"
                              "assert a.recv(9) == b'x'\nassert a.recv(9) == b''\na.close()\n")]
    for label, source in own:
        outcome = run("import os\n" + source)
        check("made_own_%s" % label, outcome.get("refused") is None and "error" not in outcome, outcome)


def owned_starts(run, check, PROGRAMS, PRIVATE):
    """Ownership never starts a program (build3 6c49a17: multiprocessing's Windows spawn makes its own pipe a
    descriptor, then starts its child): with the script's own pipe, every start is refused, whichever route passes
    the pipe, and its child (which would leave a mark) never runs. A program start is refused as one; on Windows a
    Popen that redirects one stream duplicates Angelo's own standard handles for the others first (subprocess's
    _make_inheritable), refused as Angelo's (build3 8be1419), still before anything starts. The script's own handle
    made a descriptor still works; a handle Angelo made before the region, made a descriptor by number, is refused."""
    folder = tempfile.mkdtemp(prefix="angelo-owned-starts-")
    child, mark = os.path.join(folder, "child.py"), os.path.join(folder, "ran")
    with open(child, "w", encoding="utf-8") as handle:
        handle.write("open(%r, 'w').close()\n" % mark)
    starts = [("popen_stdin", "import subprocess\nsubprocess.Popen([sys.executable, '-I', CHILD], stdin=r)\n"),
              ("multiprocessing_spawn", "import multiprocessing\n"
                                        "multiprocessing.get_context('spawn').Process(target=print).start()\n")]
    if sys.platform == "win32":
        starts += [("winapi_create_process", "import _winapi\n_winapi.CreateProcess(None, '\"%s\" -I \"%s\"' % "
                                             "(sys.executable, CHILD), None, None, True, 0, None, None, None)\n"),
                   ("open_osfhandle_then_start", "import _winapi, msvcrt, subprocess\n"
                                                 "hr, hw = _winapi.CreatePipe(None, 0)\n"
                                                 "fd = msvcrt.open_osfhandle(hw, 0)\n"
                                                 "subprocess.Popen([sys.executable, '-I', CHILD], stdout=fd)\n")]
    else:
        starts += [("popen_pass_fds", "import subprocess\n"
                                      "subprocess.Popen([sys.executable, '-I', CHILD], pass_fds=(r,))\n"),
                   ("posix_spawn_dup2", "os.posix_spawn(sys.executable, [sys.executable, '-I', CHILD], os.environ,\n"
                                        "               file_actions=[(os.POSIX_SPAWN_DUP2, w, 1)])\n")]
    redirects = {"popen_stdin", "open_osfhandle_then_start"} if sys.platform == "win32" else set()
    for label, source in starts:
        outcome = run("import os, sys\nr, w = os.pipe()\ntry:\n" + "".join("    " + line + "\n" for line in
                      source.splitlines()) + "finally:\n    os.close(r)\n    os.close(w)\n", CHILD=child)
        allowed = {PROGRAMS, PRIVATE} if label in redirects else {PROGRAMS}
        check("owned_start_refused_%s" % label, outcome.get("refused") in allowed and not os.path.exists(mark),
              (outcome, os.path.exists(mark)))
    shutil.rmtree(folder, ignore_errors=True)
    if sys.platform != "win32":
        return
    import _winapi
    outcome = run("import _winapi, msvcrt\nr, w = _winapi.CreatePipe(None, 0)\nfd = msvcrt.open_osfhandle(w, 0)\n"
                  "with open(fd, 'wb', closefd=True) as pipe:\n    pipe.write(b'x')\n"
                  "assert _winapi.ReadFile(r, 1)[0] == b'x'\n_winapi.CloseHandle(r)\n")
    check("open_osfhandle_own_pipe_works", outcome.get("refused") is None and "error" not in outcome, outcome)
    angelo_read, angelo_write = _winapi.CreatePipe(None, 0)
    try:
        outcome = run("import msvcrt\nmsvcrt.open_osfhandle(H, 0)\n", H=angelo_write)
        check("open_osfhandle_angelos_refused", outcome.get("refused") == PRIVATE, outcome)
    finally:
        _winapi.CloseHandle(angelo_read)
        _winapi.CloseHandle(angelo_write)


def asyncgen_hooks(run, check, PRIVATE, KEY):
    """CPython's `sys.set_asyncgen_hook_*` events are classified, not refused (asyncio sets them in every loop): a hook
    a confined script sets is agent code, so when Angelo's own code later first iterates an async generator on that
    thread, after the region, the hook runs confined (its read of a private file refused) or not at all."""
    old = sys.get_asyncgen_hooks()
    held = {}
    try:
        outcome = run("import sys\ndef hook(agen):\n    try:\n        open(KEY).read()\n        HELD['read'] = True\n"
                      "    except Exception as error:\n"
                      "        HELD['refused'] = getattr(error, 'reason', repr(error))\n"
                      "sys.set_asyncgen_hooks(firstiter=hook)\n", KEY=KEY, HELD=held)
        check("asyncgen_hook_set_by_a_script", outcome.get("refused") is None and "error" not in outcome, outcome)

        async def generator():
            yield 1

        started = generator()
        step = started.asend(None)   # The first iteration: the thread's firstiter hook is called here.
        step.close()
        started.aclose().close()
        check("asyncgen_hook_after_region_confined", "read" not in held and held.get("refused") in (None, PRIVATE),
              held)
    finally:
        sys.set_asyncgen_hooks(*old)


def windows_names(run, check, NETWORK, PRIVATE):
    """Windows: a long or short name of a private folder's file, a wildcard, and another host's pipe are refused;
    this machine's pipe is waited for as before."""
    outcome = run("import _winapi, os\n_winapi.GetLongPathName(os.path.join(STATE, 'keys'))")
    check("winapi_long_name_private_refused", outcome.get("refused") == PRIVATE, outcome)
    outcome = run("import nt\nnt._findfirstfile(os.path.join(STATE, '*'))")
    check("findfirstfile_wildcard_refused", outcome.get("refused") == PRIVATE or "AttributeError" in str(
        outcome.get("error")), outcome)
    outcome = run("import _winapi\n_winapi.WaitNamedPipe(PIPE, 0)", PIPE=r"\\angelo-probe.invalid\pipe\x")
    check("winapi_remote_pipe_refused", outcome.get("refused") == NETWORK, outcome)
    outcome = run("import _winapi\ntry:\n    _winapi.WaitNamedPipe(PIPE, 0)\nexcept OSError:\n    pass\n",
                  PIPE=r"\\.\pipe\angelo-probe-absent")
    check("winapi_local_pipe_allowed", outcome.get("refused") is None and "error" not in outcome, outcome)


def devnull(run, check, label):
    """The null device opens for a confined script, read and written, by os.open and open() (on Windows `nul`: a DOS
    device, no file and no link; build3 head 23 refused it), with Strict script protection on and off."""
    outcome = run("import os\nfd = os.open(os.devnull, os.O_RDWR)\nos.write(fd, b'x')\nos.close(fd)\n"
                  "with open(os.devnull, 'w') as out:\n    out.write('x')\n")
    check("devnull_opens_%s" % label, outcome.get("refused") is None and "error" not in outcome, outcome)


def run_descriptor_cases(run, check, NETWORK, KEY):
    from angelo_agent import script_confinement
    from confinement_link_cases import strict_protection
    # Which numbers a script made is tracked with Strict script protection only (Head 20 read R-4, Ruling 17): off, a
    # descriptor is judged as in 2.0.1 (its file, if any).
    with strict_protection():
        descriptors(run, check, script_confinement.PRIVATE, script_confinement.PROCESS, KEY)
        made_objects(run, check, script_confinement.PRIVATE)
        owned_numbers(run, check, script_confinement.PRIVATE)
        owned_starts(run, check, script_confinement.PROGRAMS, script_confinement.PRIVATE)
        devnull(run, check, "strict")
    devnull(run, check, "default")
    asyncgen_hooks(run, check, script_confinement.PRIVATE, KEY)
    if sys.platform == "win32":
        windows_names(run, check, NETWORK, script_confinement.PRIVATE)
