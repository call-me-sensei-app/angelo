# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The MCP launcher's headless session: the one child it owns for the file the app opened.

``angelo_open_file`` starts a child (``Angelo --background``, ``headless_main``) on the file, or reuses it for the
same file; opening another file stops it first, so a session never runs more than one. Each call goes to the child
over its pipes (``headless_pipe``). A child that exited after its idle time, or died, is started again on the next
call: on the session's output file once an edit was saved there, else on the source, and that call's result says
to observe again. Every call is audited in ``<state>/mcp/headless-audit.jsonl`` (metadata and an argument digest,
never arguments or results). Pure standard library and no ``bpy`` (doc/angelo/external_mcp.rst, "Headless").
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import external_cli_protocol as protocol
from . import headless_files, private_roots
from . import headless_preferences as preferences
from .headless_pipe import ChildProcess

# The child's environment: what Blender and the OS need, nothing that redirects scripts, state or add-ons.
KEEP_ENVIRONMENT = ("PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "TZ", "LANG", "XDG_RUNTIME_DIR", "DISPLAY",
                    "WAYLAND_DISPLAY", "CFFIXED_USER_HOME")
PRIVATE_PREFIX = private_roots.HEADLESS_PREFIX   # headless_main checks the name; sweep_leftovers removes dead children's.
POLICY_RESTARTED = ("Angelo started its headless session again on {name} to apply a change to this app's access or "
                    "an organization's policy; identifiers from before are unknown. Observe the scene again.")
IN_FLIGHT_ENDED = "The headless Angelo ended while this call was running."
IN_FLIGHT_STOPPED = ("Angelo stopped the headless session while this call was running, to apply a change to this "
                     "app's access or a policy at once (only a change that just adds access waits for running calls).")
APPLY_AFTER_RUNNING = ("This app was just given more access. Angelo applies it once the call that is running now "
                       "finishes, so nothing running is stopped; retry in a moment.")
RESTARTED = ("Angelo started its headless session again on {name} (the previous one ended after {minutes} idle "
             "minutes or stopped); identifiers from before are unknown. Observe the scene again.")
SUPPORTED_PLATFORMS = ("darwin", "linux")


class HeadlessUnavailable(RuntimeError):
    def __init__(self, message, code="unavailable"):
        super().__init__(message)
        self.code = code


def angelo_binary(executable=None, platform=None):
    """Angelo's own executable, found from the bundled Python that runs the launcher (``<app>/.../python/bin``)."""
    executable = Path(executable or sys.executable).resolve()
    platform = platform or sys.platform
    if platform == "darwin":
        contents = next((parent for parent in executable.parents if parent.name == "Contents"), None)
        folder = contents / "MacOS" if contents else None
        names = ("Angelo",)
    else:
        folder = executable.parents[3] if len(executable.parents) > 3 else None
        names = ("angelo", "Angelo")
    for name in names:
        candidate = folder / name if folder else None
        if candidate is not None and candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise HeadlessUnavailable("Angelo's executable was not found next to this launcher's Python")


APPROVAL_RANK = {"READ_ONLY": 0, "ASK": 1, "AUTO_BOUNDED": 2, None: 2}   # None: the chat default (Approve for me).


def _switches_only_open(old, new, default_on, default_off, opens, closes):
    """Every switch named like ``opens`` only turned on and every one named like ``closes`` only turned off; any
    other field unchanged."""
    for key in set(old) | set(new):
        before, after = old.get(key), new.get(key)
        if key.startswith(opens):
            before, after = old.get(key, default_on), new.get(key, default_on)
            if before and not after:
                return False
        elif key.startswith(closes):
            before, after = old.get(key, default_off), new.get(key, default_off)
            if after and not before:
                return False
        elif before != after:
            return False
    return True


def only_more_access(old, new):
    """Whether ``new`` authority (levels, approval, organization policy, effective machine policy) only adds access to
    ``old``: levels a superset, approval no stricter, policy switches only opened. Anything else, a mixed change
    included, counts as stricter (break-it re-check 2, FR3-L2)."""
    (levels, approval, organization, machine), (levels2, approval2, organization2, machine2) = old, new
    if not set(levels2) >= set(levels) or APPROVAL_RANK.get(approval2, 0) < APPROVAL_RANK.get(approval, 0):
        return False
    if organization2 is not None:
        if organization is None:
            return False   # An organization's policy where there was none.
        drop = lambda policy: {k: v for k, v in policy.items() if k != "adminContact"}  # noqa: E731
        if not _switches_only_open(drop(organization), drop(organization2), True, False, "allow", "require"):
            return False
    if machine != machine2:
        if machine is None or machine2 is None:
            return False
        names = lambda policy: {k: v for k, v in vars(policy).items()  # noqa: E731
                                if k not in {"keys", "sources", "problems", "organization_keys", "admin_contact"}}
        if not _switches_only_open(names(machine), names(machine2), True, False, "allow_", ("require_", "disable_")):
            return False
    return True


WORKING_ON_SOURCE = ("This file's folder is read-only, so this session works on the file itself instead of a copy "
                     "next to it; if one of the person's add-ons or an approved script writes it, the next result says "
                     "so.")
CHILD_OUTPUT = "child-output.txt"   # In the child's private folder, removed with it.


def scrub(text, private=None):
    """``text`` with the private folder, the home folder and the user name replaced (never in an app's result)."""
    import getpass
    for folder in {str(private), os.path.realpath(private)} if private is not None else ():
        text = text.replace(folder, "<private>")
    home = os.path.expanduser("~")
    for folder in {home, os.path.realpath(home)} if home and home != "~" else ():
        text = text.replace(folder, "~")
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 - no user name: nothing to replace.
        user = ""
    if len(user) > 2:
        text = text.replace(user, "<user>")
    return text


def effective(organization):
    """The fields of an organization's policy that change what a child may do (not its admin contact): a change in
    anything else never restarts a child (break-it review M3)."""
    if not organization:
        return None
    return tuple(sorted((key, value) for key, value in organization.items() if key != "adminContact"))


def child_environment(environ, private, preferences_used=False):
    """What the child inherits; ``preferences_used``: its config folder is the private copy (headless_preferences)."""
    kept = {name: environ[name] for name in KEEP_ENVIRONMENT if name in environ}
    # A bounded non-secret QA owner survives a private headless child, so the
    # outer test slot can clean it up without inheriting routing or credentials.
    owner = environ.get("ANGELO_QA_PROCESS_OWNER", "")
    if isinstance(owner, str) and len(owner) == 32 and all(char in "0123456789abcdef" for char in owner):
        kept["ANGELO_QA_PROCESS_OWNER"] = owner
    kept.update({name: value for name, value in environ.items() if name.startswith("LC_")})
    # A test policy file (managed_policy, lowest priority) reaches the child too, so the policy a test sets is the
    # one the child enforces; shipped policies are read by both processes from the system anyway.
    from . import product_env
    value, variable = product_env.lookup("POLICY_FILE", environ)
    if value:
        kept[variable] = value
    kept.update(ANGELO_HEADLESS="1", ANGELO_STATE_DIR=str(private / "state"),
                ANGELO_CONNECTIONS_DIR=str(private / "connections"), ANGELO_DISABLE_DEFAULT_CHAT="1",
                ANGELO_DISABLE_LOCAL_CLI="1", ANGELO_DISABLE_KEYCHAIN="1")
    if preferences_used:
        kept["BLENDER_USER_CONFIG"] = str(private / preferences.CONFIG)
    return kept


def child_command(binary, script, source, client, app, levels, resume=None, idle=None, approval=None,
                  private=None, organization=None, approvals=None, preferences_code=None, source_identity=None,
                  working=None):
    """``preferences_code``: ``headless_preferences.YOURS`` (no factory startup), else why factory settings."""
    startup = [] if preferences_code == preferences.YOURS else ["--factory-startup"]
    command = [str(binary), "--background"] + startup + ["--disable-autoexec", "--offline-mode",
               "--python-exit-code", "1", "--python", str(script), "--",
               "--in", "{in}", "--out", "{out}", "--file", str(source), "--client", client, "--app", app,
               "--levels", ",".join(levels)]
    if preferences_code is not None:
        command += ["--preferences", preferences_code]
    if source_identity is not None:   # The child's baseline is the launcher's, from before it started.
        command += ["--source-identity", json.dumps(list(source_identity))]
    if working is not None:   # The copy Blender opens instead of the source (headless_working_copy).
        command += ["--working", str(working)]
    if approval is not None:
        command += ["--approval", approval]
    if private is not None:
        command += ["--private", str(private)]
    if organization:
        # The organization's app policy the window recorded (external_clients.read_organization): applied as there.
        command += ["--organization", json.dumps(organization, sort_keys=True, separators=(",", ":"))]
    if approvals is not None and "python" in levels:
        # The person's state folder, where their script file approvals are (script_files): read, never written.
        command += ["--approvals", str(approvals)]
    if resume is not None:
        command += ["--resume", str(resume)]
    if idle is not None:
        command += ["--idle", repr(float(idle))]
    return command


def window_approval(state_directory, client):
    """The person's approval choice for this app's External conversation in the window, read without writing.

    None when the app has no conversation yet (the chat default applies); ``ASK`` when its file cannot be read
    (never a weaker mode than the person may have chosen).
    """
    from .chat_settings import migrate_saved_approval
    from .external_requests import ClientRegistry
    from . import session_journal
    identity = ClientRegistry(state_directory).load().get(client)
    if identity is None:
        return None
    path = Path(state_directory) / "sessions" / (identity + ".json")
    try:
        raw = session_journal.read(path)[0]
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return "ASK"
    options, _version = migrate_saved_approval(raw.get("options") if isinstance(raw, dict) else None,
                                               raw.get("approval_version") if isinstance(raw, dict) else None,
                                               external=True)
    approval = options.get("approval") if isinstance(options, dict) else None
    return approval if approval in {"AUTO_BOUNDED", "ASK", "READ_ONLY"} else None


def sweep_leftovers(temporary=None):
    """Remove private folders of children that died without removing their own (``headless_working_copy``)."""
    from .headless_working_copy import sweep
    sweep(temporary, PRIVATE_PREFIX)


class HeadlessSession:
    """One app session's headless file and child (module docstring)."""

    def __init__(self, client, app, state_directory, *, binary=None, script=None, environ=None, idle=None,
                 child_type=ChildProcess, platform=None, config_directory=None):
        self.client, self.app = client, app
        self.config_directory = config_directory   # Whose preferences the child loads; found when None.
        self.preferences = None   # The running child's ready record "preferences" (which ones it uses, in words).
        self.working = None   # (path, identity) of the copy the child has open (headless_working_copy).
        self.state = Path(state_directory)
        self.binary, self.idle = binary, idle
        self.script = Path(script) if script else Path(__file__).resolve().parents[2] / "angelo" / "angelo_headless.py"
        self.environ = os.environ if environ is None else environ
        self.child_type, self.platform = child_type, platform or sys.platform
        self.source = self.source_identity = self.output = self.output_identity = None
        self.levels, self.approval, self.organization = (), None, None
        self.machine = None   # managed_policy.effective() of the machine policy the child started under.
        self.child = self._private = None
        # Which child (by start count) admitted each call still running: a later round of that call goes only to
        # that child; any other child never saw it, so it would run it again (break-it re-check FR2-L1).
        # A call whose child was stopped keeps why until its next round asks (``_lost``).
        self.generation, self._admitted, self._lost = 0, {}, {}
        self._lock = threading.Lock()
        self._swept = False

    # Opening -------------------------------------------------------------------------------------------------------

    def open(self, path, levels, approval=None, organization=None, machine=None):
        """Start (or reuse) the child on ``path``; returns the child's ready record. Raises HeadlessUnavailable."""
        if self.platform not in SUPPORTED_PLATFORMS:
            raise HeadlessUnavailable("Working without Angelo's window is available on macOS first; open the file "
                                      "in Angelo instead")
        try:
            source = headless_files.check_source(path)
        except headless_files.FileProblem as error:
            raise HeadlessUnavailable(str(error), code=error.code) from None
        with self._lock:
            if (self.child is not None and self.child.alive() and source == self.source
                    and (tuple(levels), approval, effective(organization), machine)
                    == (self.levels, self.approval, effective(self.organization), self.machine)
                    and headless_files.identity(source) == self.source_identity):
                return self.child.ready_value
            self._stop()
            self.source, self.levels, self.approval, self.organization = source, tuple(levels), approval, organization
            self.machine = machine
            self.output = self.output_identity = None
            self.source_identity = headless_files.identity(source)
            return self._start(resume=None)

    def close(self):
        with self._lock:
            self._stop()
            self.source = self.source_identity = self.output = self.output_identity = None

    def _start(self, resume):
        self.generation += 1
        binary = self.binary or angelo_binary(platform=self.platform)
        if not self._swept:
            self._swept = True
            sweep_leftovers()
        config = self.config_directory or preferences.person_config_directory(self.environ, self.platform)
        self._private = private_roots.allocate(PRIVATE_PREFIX)
        code = preferences.known_failure(config) or preferences.stage(config, self._private)
        child, ready = self._launch(binary, resume, code)
        if code == preferences.YOURS and preferences.failed(ready):
            # The person's preferences crashed, hung or broke the start, or Blender could not read them: once more
            # with factory settings, saying why, and straight to them while that preferences file is unchanged.
            child.close()
            self._remove_private()
            self._private = private_roots.allocate(PRIVATE_PREFIX)
            code = "not_blend" if (ready or {}).get("code") == preferences.NOT_READABLE else "start_failed"
            preferences.remember_failure(config, code)
            child, ready = self._launch(binary, resume, code)
        if not ready or not ready.get("ok"):
            child.close()
            output = [scrub(line, self._private) for line in getattr(child, "last_output", lambda: [])()]
            self._remove_private()
            reason = (ready or {}).get("error") or "it did not start"
            if not ready and output:
                # Why it never said ready, in its own words (a file the build did not ship names itself), for the
                # app and the launcher's log; personal paths and the user name never leave (release-105, 2026-10-23).
                reason += ". Its last output: " + " | ".join(output)
                print("Angelo: the headless child did not start; its last output:\n  " + "\n  ".join(output),
                      file=sys.stderr)
            raise HeadlessUnavailable("Angelo could not open the file without its window: " + str(reason),
                                      code=(ready or {}).get("code") or "unavailable")
        self.child = child
        self.preferences = dict(ready.get("preferences") or {}, working_on_source=bool(ready.get("working_on_source")))
        copy, identity = self._copy   # Only the copy the launcher made is ever removed (break-it L7); the child's
        self.working = (str(copy) if copy else None,   # record may update its identity, never its path.
                        ready.get("working_identity") if copy and ready.get("working")
                        and Path(ready["working"]) == Path(copy) and isinstance(ready.get("working_identity"), list)
                        else (list(identity) if identity else None))
        return ready

    def _launch(self, binary, resume, code):
        """Start one child with preferences ``code`` on a working copy the launcher made, which it removes again
        when the start fails; ``(child, ready record or None)``."""
        from . import headless_working_copy as working_copy
        copy = working_copy.claim(self.source) if resume is None else None
        identity = headless_files.identity(copy) if copy else None
        self._copy = (copy, identity)
        command = child_command(binary, self.script, self.source, self.client, self.app, self.levels,
                                resume=resume, idle=self.idle, approval=self.approval, private=self._private,
                                organization=self.organization, approvals=self.state, preferences_code=code,
                                source_identity=self.source_identity, working=copy)
        try:
            child = self.child_type(command, child_environment(self.environ, self._private, code == preferences.YOURS),
                                    stderr=self._private / CHILD_OUTPUT)
        except OSError as error:
            working_copy.discard(copy, self.source, identity)
            self._remove_private()
            raise HeadlessUnavailable("Angelo could not start without its window (%s)" % error) from None
        ready = preferences.wait_ready(child, self._private, code)
        if not ready or not ready.get("ok"):
            child.close()
            working_copy.discard(copy, self.source, identity)   # A crash, a hang or a kill leaves no copy.
        return child, ready

    def _stop(self, reason=IN_FLIGHT_ENDED):
        # Calls the stopped child admitted can only be answered "unknown" now, with why.
        self._lost.update((call_id, reason) for call_id in self._admitted)
        self._admitted.clear()
        if self.child is not None:
            self.child.close()
            self.child = None
        if self.output is None and self.working and self.working[0]:   # A crashed child's unedited copy.
            from .headless_working_copy import discard
            discard(Path(self.working[0]), self.source, self.working[1])
        self.working = None
        self._remove_private()

    def _remove_private(self):
        if self._private is not None:
            shutil.rmtree(self._private, ignore_errors=True)
            self._private = None

    # Calls ---------------------------------------------------------------------------------------------------------

    @property
    def opened(self):
        return self.source is not None

    def request(self, request, timeout, levels=None, approval=None, organization=None, machine=None, authority=None,
                in_flight=False, track=False):
        """The child's envelope for one request; ``(envelope, note)``.

        ``levels``, ``approval``, ``organization`` and ``machine`` (``managed_policy.effective`` of this computer's
        policy) are the app's authority as the person and policies set it now, or ``authority()`` returns them (or a
        refusal envelope) and is read while this session's lock is held, so no
        call runs on a read made before a slow start finished (break-it review S2). A change in an effective field
        restarts the child with it before this request, so a revoked level or a stricter policy holds from the next
        call. A child that ended is started again first, on the session's output only while it is still the file
        this session wrote.

        ``in_flight``: this request asks again about a call the child already has (the launcher's next round). It
        never restarts the child or sends the call again (a script must never run twice: break-it review, fresh
        round); a child that ended meanwhile answers ``unavailable`` with the outcome unknown. ``track``: the caller
        asks again until the call is answered and then calls ``forget`` (``Server.call``), so a pending answer marks
        the call as running in this child.
        """
        note, refused = None, None
        call_id = (request.get("args") or {}).get("id")
        with self._lock:
            if in_flight and call_id in self._lost:
                return protocol.envelope("unavailable", self._lost.pop(call_id)), None
            if self.source is None:
                return protocol.envelope("unavailable", "No file is open; call angelo_open_file"), None
            if authority is not None:
                granted = authority()
                if not isinstance(granted, tuple):
                    refused = granted
                else:
                    levels, approval, organization, machine = granted
            if refused is None and in_flight:
                if (self.child is None or not self.child.alive()
                        or self._admitted.get(call_id) != self.generation):
                    self._admitted.pop(call_id, None)
                    return protocol.envelope("unavailable", IN_FLIGHT_ENDED), None
            changed = (refused is None and not in_flight and levels is not None
                       and (tuple(levels), approval, effective(organization), machine)
                       != (self.levels, self.approval, effective(self.organization), self.machine))
            running = self.child is not None and self.child.alive() and self.generation in self._admitted.values()
            if changed and running and only_more_access((self.levels, self.approval, self.organization, self.machine),
                                                        (levels, approval, organization, machine)):
                # Only more access: never stop a running call for it (re-check FR2-L2). A stricter change still
                # applies at once below, and the running call is told it was stopped (IN_FLIGHT_STOPPED).
                return protocol.envelope("busy", APPLY_AFTER_RUNNING), None
            if refused is not None:
                self._stop(IN_FLIGHT_STOPPED)   # Access ended: at once, and a running call is told why.
            elif in_flight:
                pass
            elif changed or self.child is None or not self.child.alive():
                if headless_files.identity(self.source) != self.source_identity:
                    return protocol.envelope("stale", "%s changed on disk since it was opened. Call "
                                             "angelo_open_file again to load it." % self.source.name), None
                resume = None
                if self.output is not None:
                    if headless_files.identity(self.output) != self.output_identity:
                        return protocol.envelope("stale", "%s, where this session's edits are saved, changed on "
                                                 "disk. Open it (or the source) again with angelo_open_file."
                                                 % self.output.name), None
                    resume = self.output
                ended = not changed
                self._stop(IN_FLIGHT_STOPPED if changed else IN_FLIGHT_ENDED)
                if changed:
                    self.levels, self.approval, self.organization = tuple(levels), approval, organization
                    self.machine = machine
                try:
                    self._start(resume=resume)
                except HeadlessUnavailable as error:
                    return protocol.envelope("unavailable", str(error)), None
                if ended:
                    note = RESTARTED.format(name=(resume or self.source).name, minutes=round((self.idle or 600) / 60))
                else:
                    note = POLICY_RESTARTED.format(name=(resume or self.source).name)
            child, generation = self.child, self.generation
            if track and call_id and not in_flight:
                # Running from the moment it is sent, not from its first "pending" (up to a round later): a change
                # that only adds access never stops it even then (re-check 2, FR3-L1).
                self._admitted[call_id] = generation
        if refused is not None:
            self.close()   # Access ended: the session ends with it ("Turning this off closes the connection").
            return refused, None
        envelope = child.request(request, timeout)
        code = envelope.get("code") if isinstance(envelope, dict) else None
        with self._lock:
            if code == "pending" and call_id and track:
                self._admitted[call_id] = generation
            elif not (code == "busy" and in_flight):
                # Answered, gone, or never admitted (a first round's busy): its rounds are over. A later round's busy:
                # it is still running.
                self._admitted.pop(call_id, None)
        record = envelope.get("headless_file") if isinstance(envelope, dict) else None
        if isinstance(record, dict) and record.get("output") and isinstance(record.get("output_identity"), list):
            self.output, self.output_identity = Path(record["output"]), tuple(record["output_identity"])
        if (isinstance(record, dict) and record.get("working") and isinstance(record.get("working_identity"), list)
                and self.working and self.working[0] and Path(record["working"]) == Path(self.working[0])):
            self.working = (self.working[0], record["working_identity"])   # Its identity after a save, same path.
        return envelope, note

    def forget(self, call_id):
        """``call_id`` is over for its caller (answered, cancelled or past its deadline): never running here again."""
        with self._lock:
            self._admitted.pop(call_id, None)
            self._lost.pop(call_id, None)

    def status(self):
        alive = self.child is not None and self.child.alive()
        return {"file": str(self.source) if self.source else None, "output": str(self.output) if self.output else None,
                "running": alive, "levels": [level for level in self.levels if level in ("read", "edit")],
                "approval": self.approval or "AUTO_BOUNDED",
                "preferences": (self.preferences or {}).get("note") if alive else None,
                "working_on_source": WORKING_ON_SOURCE if alive and (self.preferences or {}).get("working_on_source")
                else None,
                "changed_on_disk": bool(self.source and headless_files.identity(self.source) != self.source_identity)}

    def audit(self, tool, arguments, code):
        """One line per call; never arguments or results."""
        from .external_requests import AuditLog
        directory = self.state / "mcp"
        try:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError:
            return
        digest = hashlib.sha256(json.dumps(arguments, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        AuditLog(directory / "headless-audit.jsonl").record(
            client=self.client, tool=tool, arguments_sha256=digest, code=code, at=round(time.time(), 3),
            file=self.source.name if self.source else None, output=self.output.name if self.output else None)
