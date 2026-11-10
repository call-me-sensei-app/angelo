# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No test process sees a real provider login unless it opts in (stdlib only; importing it changes nothing).

``suite_isolation`` is the per-file entry that applies it: every Blender suite (``test_blender_*``, ``benchmark_blender_*``, ``stress_blender_*``), every host test
(``test_native_*``) and the billed harness's in-app drivers import ``suite_isolation`` first (``PREAMBLE_FILES``;
pinned by ``test_native_suite_isolation``), so the isolation lives in the per-file entry, not only in ``run_native_suites.py``.
A suite started by hand, ``Angelo --background --python tests/python/angelo_agent/test_blender_x.py`` with the
person's own HOME, once ran the app's bundled Codex on the maintainer's main ChatGPT sign-in (2026-11-05).

``ensure`` (run by that import), unless the environment already satisfies it (``satisfied``: what ``run_native_suites.py`` and an earlier
relaunch set), the process gets a fresh profile (``isolated_environment``): HOME, Cocoa's CFFIXED_USER_HOME, Windows'
USERPROFILE/APPDATA/LOCALAPPDATA and the XDG folders inside a new folder (inside ``ANGELO_QA_DIR`` when set, else the
temporary folder); ``CODEX_HOME`` and ``CLAUDE_CONFIG_DIR`` inside it too, so neither CLI finds a sign-in or a Keychain
item keyed to the person's own profile; ``ANGELO_DISABLE_KEYCHAIN=1`` (Angelo's own key store);
``ANGELO_AGENT_RUNTIME=off`` (the stand-in CLIs, never the bundled runtime); and every provider credential variable
removed (``credential``). Inside Blender the app has already started (and registered Angelo, which loads connections
and checks native sign-ins) under the caller's environment, so the suite is not run there: the process starts the same
command again with the isolated environment and exits with its status (``relaunch``). A host process (plain Python)
changes its own environment in place before anything else runs.

Opt-ins, each explicit and named:

* ``ANGELO_TEST_REAL_PROVIDERS=eval-profile:<path>``: only the billed eval harness sets it (``eval_live_agent``
  derives it from its validated connection), and the LIVE suites take it from the caller. ``<path>`` is the dedicated
  profile the run bills (``.../codex-profile/<uuid>`` signed in to the evaluation account,
  ``.../claude-profile/<uuid>``, or the dedicated connection store an API-key or team run uses in place); never the
  person's ``~/.codex`` or ``~/.claude``, never empty, never a folder holding the home (``opt_in_profile``). Before the
  process runs, every account it can bill is read and must be the expected one (``provider_accounts``): the opted-in
  profile's (a Codex profile's id_token claims against the evaluation account; a Claude profile's ``.claude.json``
  against the account the opt-in names, ``<path>|account=<email or account UUID>``, required for any Claude profile)
  and that of every subscription
  connection in its connection store (``ANGELO_CONNECTIONS_DIR``; an empty one when the caller names none, so the
  person's own store is never reached). HOME stays the caller's (the login Keychain);
  ``CODEX_HOME``/``CLAUDE_CONFIG_DIR`` are pinned to the profile and an empty folder, so a connection that reuses a
  native sign-in finds none but the evaluation-account profile.
* ``ANGELO_QA_LIVE_KEYCHAIN=1`` (``test_blender_keychain_live``): HOME stays the caller's for its test-only login
  Keychain items; provider profiles stay isolated and the runtime off.

A malformed or refused opt-in stops the process (``SystemExit``) rather than running isolated without saying so.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import provider_accounts

OPT_IN = "ANGELO_TEST_REAL_PROVIDERS"
OPT_IN_KIND = "eval-profile"
KEYCHAIN_OPT_IN = "ANGELO_QA_LIVE_KEYCHAIN"
# The isolated home every provider location of the process lies in; MARKER_FILE in it says this module (or the native
# runner) made it for a test, so a hand-set HOME elsewhere is never taken for an isolated one.
MARKER = "ANGELO_TEST_ISOLATED"
MARKER_FILE = ".angelo-test-profile"
PROFILE_ENV = {"codex-profile": "CODEX_HOME", "claude-profile": "CLAUDE_CONFIG_DIR"}
SIGN_IN_FILE = {"codex-profile": "auth.json"}   # Only its id_token's claims are read (provider_accounts).
ACCOUNT_FIELD = "|account="   # The expected Claude account (email or UUID), after the path (provider_accounts).
# The app's connection store override, in its lookup order (product_env.names("CONNECTIONS_DIR"); pinned by
# test_native_provider_accounts): under an opt-in, every subscription in that store is checked too.
STORE_ENV = ("ANGELO_CONNECTIONS_DIR", "SENSEI_CONNECTIONS_DIR", "TOONLAB_CONNECTIONS_DIR")
# The person's own sign-in locations under a home folder (Claude Code keeps ~/.claude.json beside ~/.claude).
NATIVE_LOGINS = (".codex", ".claude", ".claude.json")
POSIX_HOME = ("HOME", "CFFIXED_USER_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME")
WINDOWS_HOME = ("USERPROFILE", "APPDATA", "LOCALAPPDATA")
# Windows' expanduser falls back to these when USERPROFILE is unset: they name the real profile, so they go.
WINDOWS_HOME_PARTS = ("HOMEDRIVE", "HOMEPATH")
RELAUNCHED = "ANGELO_TEST_RELAUNCHED"   # Set in a relaunched suite: a second relaunch would loop.
PYCACHE = "angelo-test-pycache"   # A hand-started suite's bytecode, shared like run_native_suites' PYCACHE_FOLDER.

# Provider credentials (provider_accounts, the one list: a Claude profile's settings may set none either).
CREDENTIALS, CREDENTIAL_SUFFIXES = provider_accounts.CREDENTIALS, provider_accounts.CREDENTIAL_SUFFIXES
credential = provider_accounts.credential


# The files whose first statements must be the entry (``suite_isolation``): every Blender suite and host test, and the
# in-app drivers of the billed harness (eval_live_agent, parallel_live_trial).
PREAMBLE_PATTERNS = ("test_blender_*.py", "benchmark_blender_*.py", "stress_blender_*.py", "test_native_*.py")
PREAMBLE_DRIVERS = ("eval_live_agent_session.py", "parallel_live_session.py")


# The entry itself, exactly as each file carries it: a host test's folder is on sys.path however it is started; Blender's
# --python adds none, so a Blender file puts its own folder there first.
HOST_ENTRY = ("import suite_isolation  # noqa: F401  First: this test never sees a real provider login "
              "(provider_isolation.py).\n")
BLENDER_ENTRY = ("import os, sys  # noqa: E401  The suite's own folder, for its first import:\n"
                 "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
                 "import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).\n")


def entry_for(path):
    """The entry ``path`` must start with."""
    return HOST_ENTRY if Path(path).name.startswith("test_native_") else BLENDER_ENTRY


def preamble_files(directory):
    """Every file in ``directory`` that must import ``suite_isolation`` first, sorted."""
    directory = Path(directory)
    found = {path for pattern in PREAMBLE_PATTERNS for path in directory.glob(pattern)}
    found.update(directory / name for name in PREAMBLE_DRIVERS)
    return sorted(found)


class Refusal(Exception):
    """An opt-in that names no usable dedicated profile."""


def real_home():
    """The account's own home folder, from the system rather than the environment (which a test may have changed)."""
    if os.name == "nt":
        import ctypes
        buffer = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0028, None, 0, buffer) == 0:   # CSIDL_PROFILE
            return Path(buffer.value)
        return Path(os.path.expanduser("~"))
    import pwd
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def _norm(path):
    return os.path.normcase(os.path.realpath(str(path)))


def inside(path, root):
    """Whether ``path`` is ``root`` or lies within it, after resolving links (macOS /var is /private/var)."""
    path, root = _norm(path), _norm(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def homes(environ):
    """The person's home folders a test must never take a sign-in from: the account's own and the caller's."""
    found = {real_home()}
    for name in ("HOME", "USERPROFILE"):
        if environ.get(name) and not environ.get(MARKER):
            found.add(Path(environ[name]))
    return found


def opt_in_profile(environ, home_folders=None):
    """``(family, path)`` of a valid ``OPT_IN``, ``None`` when it is unset; raises ``Refusal`` when it is invalid.

    ``family`` is ``codex-profile`` or ``claude-profile`` for a sign-in profile, ``None`` for a dedicated connection
    store (an API-key or team run in place). The profile must be signed in to the expected account
    (``opt_in_account``).
    """
    found = _opt_in(environ, home_folders)
    return None if found is None else found[:2]


def opt_in_account(environ, home_folders=None):
    """What may be shown of the opted-in profile's account (``provider_accounts.summary``: an account id's first 8
    characters, an email's domain), ``None`` when ``OPT_IN`` is unset; raises ``Refusal`` when it is invalid."""
    found = _opt_in(environ, home_folders)
    return None if found is None else found[2]


def shown_account(value):
    """``opt_in_account`` of the ``OPT_IN`` value ``value`` (a billed harness's CONNECTION line)."""
    return opt_in_account({OPT_IN: value})


def claude_expected(environ):
    """The expected Claude account: the opt-in's ``|account=`` field, else ``CLAUDE_ACCOUNT_ENV`` (the harnesses
    outside the opt-in); None when neither names one."""
    value = environ.get(OPT_IN) or ""
    field = value.partition(":")[2].partition(ACCOUNT_FIELD)[2].strip()
    return field or (environ.get(provider_accounts.CLAUDE_ACCOUNT_ENV) or "").strip() or None


def check_billed(profiles=(), stores=(), environ=None):
    """For a billed harness outside the opt-in, before it starts anything: every ``(family, path)`` profile it launches
    a CLI with is dedicated (``_dedicated``) and signed in to its expected account, and every connection store it hands
    the app is checked as an opted-in process's (``verify_store``). Raises ``Refusal``."""
    environ = os.environ if environ is None else environ
    for family, path in profiles:
        _dedicated(Path(path).expanduser().absolute(), homes(environ), "%s profile" % family)
        try:
            provider_accounts.verify(family, Path(path).expanduser(), claude_expected(environ))
        except provider_accounts.AccountRefusal as error:
            raise Refusal("%s %s: %s" % (family, path, error)) from None
    for store in stores:
        verify_store(store, environ)


def without_credentials(environ):
    """``environ`` without a provider credential (``credential``): for a billed harness's child environment."""
    return {name: value for name, value in environ.items() if not credential(name)}


LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


def checked(family, path, fake=None):
    """The absolute ``path`` to launch a billed CLI with, after ``check_billed`` of exactly that folder, so each launch
    checks the folder it uses. ``fake``: an unbilled dry run's model endpoint, which must be local (then no account is
    read: the run's profile is a fresh one)."""
    path = str(Path(path).expanduser().absolute())
    if fake:
        from urllib.parse import urlsplit
        if urlsplit(str(fake)).hostname not in LOOPBACK:
            raise Refusal("a dry run's model endpoint must be local, not %s" % urlsplit(str(fake)).hostname)
        return path
    check_billed([(family, path)])
    return path


def store_of(environ):
    """The connection store ``environ`` names (``STORE_ENV``), None when it names none."""
    return next((environ[name] for name in STORE_ENV if environ.get(name)), None)


def verify_store(store, environ):
    """``provider_accounts.store_accounts`` of ``store`` with the opt-in's expected Claude account: for the store a
    billed process uses and any further one a LIVE suite bills (``test_blender_cross_provider_live``); raises
    ``Refusal``."""
    try:
        return provider_accounts.store_accounts(Path(store).expanduser(), claude_expected(environ))
    except provider_accounts.AccountRefusal as error:
        raise Refusal("the connection store %s: %s" % (store, error)) from None


def _opt_in(environ, home_folders=None):
    value = environ.get(OPT_IN)
    if value is None:
        return None
    kind, separator, text = value.partition(":")
    text, field, expected = text.partition(ACCOUNT_FIELD)
    if field and not expected.strip():
        raise Refusal("%s names an empty expected account" % OPT_IN)
    if "|" in text:   # A misspelt field: never echoed, it may hold an email.
        raise Refusal("%s's only field after the path is %s<email or account UUID>" % (OPT_IN, ACCOUNT_FIELD))
    if kind != OPT_IN_KIND or not separator or not text.strip():   # Never the value: its |account= is an email.
        raise Refusal("%s must be %s:<absolute path of the evaluation-account profile>, not %r"
                      % (OPT_IN, OPT_IN_KIND, text))
    path = Path(text)
    if path.parent.name in PROFILE_ENV and not _is_uuid(path.name):
        raise Refusal("%s names a %s folder that is not a connection's (<uuid>): %s" % (OPT_IN, path.parent.name, text))
    if not path.is_absolute():
        raise Refusal("%s names a relative path: %s" % (OPT_IN, text))
    if not path.is_dir():
        raise Refusal("%s names no folder: %s" % (OPT_IN, text))
    _dedicated(path, homes(environ) if home_folders is None else home_folders, OPT_IN)
    family = path.parent.name if _is_uuid(path.name) and path.parent.name in PROFILE_ENV else None
    sign_in = SIGN_IN_FILE.get(family)
    if sign_in and not (path / sign_in).is_file():
        raise Refusal("%s is not signed in (run: CODEX_HOME=%s codex login)" % (OPT_IN, path))
    try:   # The account it reaches, read now: a folder can have been re-signed into another account.
        account = provider_accounts.verify(family, path, claude_expected(environ))
    except provider_accounts.AccountRefusal as error:
        raise Refusal("%s %s: %s" % (OPT_IN, path, error)) from None
    return family, str(path), account


def _dedicated(path, home_folders, what):
    for home in home_folders:
        if inside(home, path):
            raise Refusal("%s must be a dedicated profile, never a folder holding the home %s" % (what, home))
        for login in NATIVE_LOGINS:
            if inside(path, Path(home) / login):
                raise Refusal("%s must be a dedicated profile, never the person's own %s" % (what, Path(home) / login))


def _is_uuid(text):
    try:
        return str(uuid.UUID(text)) == text.lower()
    except ValueError:
        return False


def _home_folders(home):
    home = Path(home)
    return {"HOME": home, "CFFIXED_USER_HOME": home, "USERPROFILE": home, "APPDATA": home / "AppData" / "Roaming",
            "LOCALAPPDATA": home / "AppData" / "Local", "XDG_CONFIG_HOME": home / ".config",
            "XDG_CACHE_HOME": home / ".cache", "XDG_DATA_HOME": home / ".local" / "share",
            "XDG_STATE_HOME": home / ".local" / "state"}


def isolated_environment(environ, home, keep_home=False):
    """``environ`` with no provider login: credentials removed, the home folders (unless ``keep_home``) and both
    runtimes' profiles inside ``home``, Angelo's key store and bundled runtime off."""
    env = {name: value for name, value in environ.items() if not credential(name)}
    home = Path(home)
    if not keep_home:
        for name in WINDOWS_HOME_PARTS:
            env.pop(name, None)
        env.update({name: str(path) for name, path in _home_folders(home).items()})
    env.update({MARKER: str(home), "CODEX_HOME": str(home / ".codex"), "CLAUDE_CONFIG_DIR": str(home / ".claude"),
                "ANGELO_DISABLE_KEYCHAIN": "1", "ANGELO_AGENT_RUNTIME": "off"})
    return env


def real_provider_environment(environ, family, profile, home):
    """``environ`` for the opted-in billed run: its own home folders, credentials removed, the opted-in profile as its
    runtime's profile and an empty one inside ``home`` for the other (so a reused native sign-in finds nothing)."""
    env = {name: value for name, value in environ.items() if not credential(name)}
    home = Path(home)
    env.update({MARKER: str(home), "CODEX_HOME": str(home / ".codex"), "CLAUDE_CONFIG_DIR": str(home / ".claude")})
    if family in PROFILE_ENV:
        env[PROFILE_ENV[family]] = str(profile)
    if store_of(env) is None:   # Never the person's own store (HOME is theirs): an empty one.
        env[STORE_ENV[0]] = str(home / "connections")
    return env


def connection_opt_in(connection, environ=None):
    """A billed run's ``OPT_IN`` value from its connection (``eval_live_agent.isolated_connection``, ``team_connection``
    or a kept returning-user copy): ``eval-profile:`` and the connection's dedicated profile (``--codex-home``'s
    evaluation-account Codex profile, a Claude connection's own ``claude-profile/<id>``, or the dedicated store an
    API-key or team run uses in place). A Claude profile's expected account comes from
    ``provider_accounts.CLAUDE_ACCOUNT_ENV`` in ``environ`` (refused when absent); a Codex profile's is the evaluation
    account (``provider_accounts.CODEX_ACCOUNT``).

    The only opt-in a test process gets to a real provider login, so it never falls back to the person's own sign-in:
    a connection that reuses the native ``~/.codex`` or ``~/.claude`` (on the maintainer's machines, the main account)
    is refused (``Refusal``), as is any profile ``opt_in_profile`` refuses, a profile signed in to another account
    included. Local reads only: nothing is sent, so the harness's ``--preflight-only`` is its dry check.
    """
    profile = connection.get("in_place") or connection.get("profile")
    if connection.get("reuse_native") or not profile:
        raise Refusal("Connection %s reuses the person's own native sign-in (~/.codex or ~/.claude: the main account). "
                      "Billed runs use a dedicated profile: --codex-home with the evaluation-account Codex profile, "
                      "or a connection with its own profile" % connection.get("id"))
    value = "%s:%s" % (OPT_IN_KIND, Path(profile).absolute())
    expected = (os.environ if environ is None else environ).get(provider_accounts.CLAUDE_ACCOUNT_ENV, "").strip()
    if Path(profile).parent.name == "claude-profile" and not expected:
        raise Refusal("A billed Claude run needs its expected account: set %s to the account's email or UUID"
                      % provider_accounts.CLAUDE_ACCOUNT_ENV)
    if expected:   # Any Claude profile the run reaches (its own, or a subscription in a store it uses in place).
        value += ACCOUNT_FIELD + expected
    opt_in_profile({OPT_IN: value})
    return value


def opt_in_environment(environ, value, home):
    """The environment a billed harness gives an in-app driver that opts in with ``value`` (``OPT_IN``'s value):
    validated (``Refusal``), with ``home`` prepared as its empty-profile folder, so the driver's ``suite_isolation``
    finds it satisfied and never relaunches (a relaunch would add a second start-up to a timed trial)."""
    probe = dict(environ, **{OPT_IN: value})
    probe.pop(MARKER, None)
    family, profile = opt_in_profile(probe)
    env = real_provider_environment(probe, family, profile, prepare(home))
    if not satisfied(env):
        raise Refusal("the opted-in environment for %s is not a satisfied one (its folder %s)" % (profile, home))
    return env


def prepare(home):
    """Create an isolated home and mark it as one."""
    home = Path(home)
    for path in [home, home / ".codex", home / ".claude"] + list(_home_folders(home).values()):
        path.mkdir(parents=True, exist_ok=True)
    (home / MARKER_FILE).write_text("A test's isolated profile (tests/python/angelo_agent/suite_isolation.py).\n",
                                    encoding="utf-8")
    return home


def _marked(environ):
    marker = environ.get(MARKER)
    return Path(marker) if marker and (Path(marker) / MARKER_FILE).is_file() else None


def satisfied(environ, home_folders=None):
    """Whether ``environ`` already gives a test no real provider login (or exactly the opted-in one).

    Raises ``Refusal`` for an invalid opt-in."""
    if any(credential(name) for name in environ):
        return False
    marker = _marked(environ)
    if marker is None:
        return False
    people = homes({}) if home_folders is None else home_folders
    if any(inside(home, marker) or any(inside(marker, Path(home) / login) for login in NATIVE_LOGINS)
           for home in people):
        return False
    pins = {"CODEX_HOME": marker / ".codex", "CLAUDE_CONFIG_DIR": marker / ".claude"}
    profile = opt_in_profile(environ, people)
    if profile is not None:
        family, path = profile
        if family in PROFILE_ENV:
            pins[PROFILE_ENV[family]] = Path(path)
        if store_of(environ) is None:
            return False
        verify_store(store_of(environ), environ)
        return all(environ.get(name) and _norm(environ[name]) == _norm(path) for name, path in pins.items())
    # Angelo's own worker or headless child (worker_process.worker_environment, ANGELO_HEADLESS) of an isolated suite
    # carries the marker but not the pins or the runtime switch: its logins default under its HOME, checked below.
    child = environ.get("ANGELO_HEADLESS") == "1"
    if not all((child and name not in environ) or (environ.get(name) and _norm(environ[name]) == _norm(path))
               for name, path in pins.items()):
        return False
    if environ.get("ANGELO_DISABLE_KEYCHAIN") != "1" or (environ.get("ANGELO_AGENT_RUNTIME") != "off" and not child):
        return False
    if environ.get(KEYCHAIN_OPT_IN) == "1":
        return True
    names = POSIX_HOME[:1] + (WINDOWS_HOME if os.name == "nt" else ())
    return all(environ.get(name) and inside(environ[name], marker) for name in names)


# Modules only Blender's own Python has built in (its inittab). ``_bpy`` itself is made at startup and put in
# sys.modules without being built in; a host test's stand-in ``bpy`` or ``_bpy`` is neither.
BLENDER_BUILTINS = frozenset({"_bpy_path", "_bpy_hydra", "_bpy"})


def in_blender():
    """Whether this is Blender's Python."""
    return not BLENDER_BUILTINS.isdisjoint(sys.builtin_module_names)


def new_home(environ):
    """A new isolated home: inside the suite's ``ANGELO_QA_DIR`` when set (kept as evidence), else a temporary folder
    removed when the process ends. Returns ``(home, remove_afterwards)``."""
    qa = environ.get("ANGELO_QA_DIR")
    if qa:
        Path(qa).mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix="test-profile-", dir=qa)) / "home", False
    return Path(tempfile.mkdtemp(prefix="angelo-test-profile-")) / "home", True


def relaunch(env, argv=None, binary=None, call=subprocess.call):
    """Run this Blender's command line again with ``env``; returns its exit status (0-255)."""
    if binary is None:
        import bpy
        binary = bpy.app.binary_path
    command = [str(binary)] + list(sys.argv[1:] if argv is None else argv[1:])
    sys.stdout.flush()
    sys.stderr.flush()
    code = call(command, env=env)
    return code if 0 <= code < 256 else 1


def ensure(environ=None):
    """Isolate this process (see the module docstring). Returns ``"kept"`` (already isolated or opted in) or
    ``"isolated"``; inside Blender an isolated start relaunches and never returns."""
    environ = os.environ if environ is None else environ
    try:
        if satisfied(environ):
            return "kept"
        profile = opt_in_profile(environ)
    except Refusal as error:
        raise SystemExit("suite_isolation: refused: %s" % error) from None
    if in_blender() and environ.get(RELAUNCHED) == "1":
        raise SystemExit("suite_isolation: a relaunched suite still lacks its isolated profile; not relaunching again")
    home, remove = new_home(environ)
    prepare(home)
    if profile is not None:
        env = real_provider_environment(environ, profile[0], profile[1], home)
        try:
            verify_store(store_of(env), env)
        except Refusal as error:
            raise SystemExit("suite_isolation: refused: %s" % error) from None
    else:
        env = isolated_environment(environ, home, keep_home=environ.get(KEYCHAIN_OPT_IN) == "1")
    if in_blender():
        env[RELAUNCHED] = "1"
        env.setdefault("PYTHONPYCACHEPREFIX", str(Path(tempfile.gettempdir()) / PYCACHE))
        print("suite_isolation: started outside run_native_suites.py with the caller's profile; running it again with "
              "the isolated profile %s (no real provider login)" % home, flush=True)
        code = relaunch(env)
        if remove:
            shutil.rmtree(home.parent, ignore_errors=True)
        os._exit(code)
    for name in [name for name in environ if name not in env]:
        del environ[name]
    environ.update(env)
    if remove:
        import atexit
        atexit.register(shutil.rmtree, str(home.parent), True)
    return "isolated"
