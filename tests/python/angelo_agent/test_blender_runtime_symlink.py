# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A CLI runtime reached through a symbolic link still starts after an agent script ran, in the real app with the
real guard (2.0.1 MCP acceptance run, v210-plan-first: every new Claude start failed after any script).

Claude Code's official installer makes ``~/.local/bin/claude`` a link to ``~/.local/share/claude/versions/X``
(Homebrew and npm links do the same). ``find_executable`` vouched the name it found, which C refuses for a link, and
after any script region ``may_run`` resolves the real file, which nothing had vouched and whose folder is not on PATH:
a new chat, a reconnect or a settings restart failed until Angelo restarted, and the refusal read as "its version
could not be read". Here, with a stand-in CLI behind a link on PATH and as the person's Preferences pick:

* found, a script region runs, then the version probe (the exact call that failed) starts it and reads its version;
* a refused start says it was refused and what to do, never "too old" or "its version could not be read";
* the vouch is to run only (the break-it review of 29b385a6645): Angelo's writer never writes the real file where a
  script's data pointed (an image's file), and a file changed in place after a script ran never runs, even when it
  is found again or unvouched first (the re-review of 4be92cfb84f, S-1), and its refusal says to restart Angelo
  (S-2);
* an update that retargets the link to a new version starts; a program a script wrote over (scripts may write files
  outside Angelo's folders), or a link a script retargeted, never does, and says a script changed it.

The stand-ins are real programs on every system: a shell script on macOS and Linux, and on Windows an ``.exe``
(pip's console-script launcher with the version script appended, as ``pip install`` makes one), since Windows finds
only names with a PATHEXT extension and can't start a shell script. Windows needs no privilege for the links here
with Developer Mode on; where it can't make one, the suite fails saying ``[environment]`` (never a silent pass).
A start searches only the suite's folders (``isolate_search``): never a CLI the host has installed, which a release
VM has on PATH, and a program found outside them fails ``every_start_found_only_the_suite_s_programs``.

Run only in a disposable instance: ``--background --factory-startup --disable-autoexec``. No provider or network.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import io
import json
import platform
import stat
import tempfile
import traceback
import zipfile
from pathlib import Path

import addon_utils
import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-runtime-symlink-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("ANGELO_STATE_DIR", str(OUT / "state"))
os.environ.setdefault("ANGELO_CONNECTIONS_DIR", str(OUT / "connections"))
os.environ["ANGELO_DISABLE_KEYCHAIN"] = "1"
REPORT = {"success": False, "checks": {}}
STAND_IN = "#!/bin/sh\nif [ \"$1\" = \"--version\" ]; then echo \"%s (Claude Code)\"; fi\n"
WINDOWS = os.name == "nt"
EXE = ".exe" if WINDOWS else ""   # A name Windows finds (PATHEXT) and starts.
WINDOWS_MAIN = "import sys\nif sys.argv[1:2] == ['--version']:\n    print('%s (Claude Code)')\n"


def check(name, ok, detail=None):
    REPORT["checks"][name] = bool(ok)
    REPORT.setdefault("details", {})[name] = str(detail)[:600]


def run(source, **names):
    from angelo_agent import script_confinement, script_errors
    from confinement_attacks_admit import admit
    namespace = {"__name__": "__angelo_script__", "bpy": bpy, **names}
    admit("runtime-symlink", source)
    try:
        _, result = script_confinement.run_confined(
            source, namespace, None, "runtime-symlink", finish=lambda ns: script_errors.read_back(ns.get("result")))
        return {"refused": None, "result": result}
    except script_confinement.ScriptConfined as error:   # "said": the reason and what it refused, as the agent reads.
        return {"refused": script_confinement.reason_of(error), "said": str(error)[:300]}
    except Exception as error:   # noqa: BLE001 - the detail says what broke.
        return {"refused": None, "error": repr(error)[:300]}


def windows_program(version):
    """An .exe that prints ``version`` as Claude Code does: pip's launcher, the Python to run and a zip with the
    script, the layout ``pip install`` gives a console script (distlib ``ScriptMaker._write_script``)."""
    from pip._vendor import distlib
    launcher = "t64-arm.exe" if platform.machine().upper() == "ARM64" else "t64.exe"
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as written:
        written.writestr("__main__.py", WINDOWS_MAIN % version)
    shebang = b'#!"' + os.fsencode(sys.executable) + b'"\n'   # Blender's own Python (sys.executable).
    return Path(distlib.__file__).with_name(launcher).read_bytes() + shebang + archive.getvalue()


def program(path, version):
    """A runnable stand-in CLI at ``path`` (``.exe`` added on Windows) that reports ``version``."""
    path = path.with_name(path.name + EXE)
    path.parent.mkdir(parents=True, exist_ok=True)
    if WINDOWS:
        path.write_bytes(windows_program(version))
    else:
        path.write_text(STAND_IN % version)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def make_link(link, target):
    try:
        link.symlink_to(target)
    except OSError as error:   # Windows without Developer Mode or the privilege: the suite can't test links.
        raise RuntimeError("[environment] this host can't make a symbolic link: %r" % error) from error


def probe(executable):
    """The version probe a new Claude runtime start runs first (claude_transport.require_supported_runtime)."""
    from angelo_agent import claude_transport
    try:
        return {"version": list(claude_transport.require_supported_runtime(executable, dict(os.environ)))}
    except Exception as error:   # noqa: BLE001 - the message is what the person reads.
        return {"refused": str(error)}


FOUND_OUTSIDE = []   # Every program a start found outside the suite's folders: the isolation leaked.


def isolate_search(own):
    """Make the suite's folders ``own`` the only places a start searches, so ``find`` never reaches a CLI the host
    has installed (a Windows release VM has its own claude.EXE on PATH): PATH is only ``own``, and the fixed folders,
    the login shell's PATH and Windows' saved PATH find nothing. Git Bash, which Claude Code needs on Windows and
    ``find_executable`` looks up on PATH, is named first, as the person's own setting would."""
    import shutil
    from angelo_agent import cli_runtime, cli_windows
    if WINDOWS and not os.environ.get("CLAUDE_CODE_GIT_BASH_PATH"):
        bash = cli_windows.git_bash(os.environ, shutil.which)
        if bash:
            os.environ["CLAUDE_CODE_GIT_BASH_PATH"] = bash
    os.environ["PATH"] = os.pathsep.join(str(folder) for folder in own)   # Also the C environment (putenv).
    cli_runtime.known_directories = lambda environ=None, home=None: []
    cli_runtime.login_shell_path = lambda timeout=None: ""
    cli_windows.known_directories = lambda environ, home: []
    cli_windows.registry_path = lambda read=None: []


def find(root):
    """The CLI as a new runtime start finds it (on PATH), or None when nothing runnable is found. A program found
    outside the suite's ``root`` is noted (``FOUND_OUTSIDE``) and never returned: no check judges the host's own."""
    from angelo_agent import cli_runtime
    try:
        found = cli_runtime.find_executable("claude")
    except Exception:   # noqa: BLE001 - not found (a file replaced without its exec bit): nothing to start.
        return None
    inside = os.path.normcase(os.path.abspath(found)).startswith(os.path.normcase(str(root)) + os.sep)
    if not inside:
        FOUND_OUTSIDE.append(found)
    return found if inside else None


def main():
    from angelo_agent import cli_runtime, owned_paths
    root = Path(tempfile.mkdtemp(prefix="angelo-runtime-link-"))   # Not inside any installed or private folder.
    real = program(root / "share/claude/versions/2.1.300", "2.1.300")
    link = root / ("bin/claude" + EXE)
    link.parent.mkdir(parents=True, exist_ok=True)
    make_link(link, real)
    isolate_search([link.parent])   # On PATH as ~/.local/bin is, and the only place a start looks.

    found = cli_runtime.find_executable("claude")
    check("the_link_on_path_is_found", Path(found) == link, found)
    picked = program(root / "picked/versions/2.1.301", "2.1.301")
    pick = root / ("picked/claude" + EXE)
    make_link(pick, picked)
    chosen = cli_runtime.find_executable("claude", str(pick))   # The person's Preferences pick, through a link.
    check("the_preferences_pick_is_found", Path(chosen) == pick, chosen)

    outcome = run("result = 1\n")   # Any agent script: confinement is armed from here on.
    check("a_script_ran", outcome.get("refused") is None and "error" not in outcome, outcome)

    check("after_a_script_the_found_runtime_starts", probe(found) == {"version": [2, 1, 300]}, probe(found))
    check("after_a_script_the_picked_runtime_starts", probe(chosen) == {"version": [2, 1, 301]}, probe(chosen))
    check("may_run_allows_the_found_link", owned_paths.may_run(found), found)

    # A file changed in place after a script ran never runs, even when found again or unvouched first (the re-review
    # of 4be92cfb84f, S-1: narrowing is anyone's and never drops a program's record), and says to restart (S-2).
    with open(real, "a") as handle:   # Changed in place under the same name (same inode, new size and ctime).
        handle.write("# changed\n")
    check("a_program_changed_in_place_is_refused", not owned_paths.may_run(found), found)
    again = find(root)
    check("found_again_it_stays_refused", not owned_paths.may_run(again) and "refused" in probe(again), probe(again))
    import _bpy
    for path in (real, link):
        _bpy._angelo_guard.unvouch_path(str(path))
    unvouched = find(root)
    refused = probe(unvouched)
    check("unvouched_and_found_again_it_stays_refused", not owned_paths.may_run(unvouched) and "refused" in refused,
          refused)
    message = refused.get("refused", "")
    check("a_changed_program_says_restart_angelo",
          "changed on disk" in message and "Restart Angelo" in message and "reinstall" not in message.lower(), message)

    # The update flow of a versioned install: the installer retargets the link to versions/Y, then a start finds it.
    update = program(root / "share/claude/versions/2.1.310", "2.1.310")
    link.unlink()
    make_link(link, update)
    updated = find(root)
    check("an_update_to_a_new_version_starts", probe(updated) == {"version": [2, 1, 310]}, probe(updated))

    # The review of 29b385a6645, MUST-1 and SHOULD-1: the program vouch never widens writing. The route it widened is
    # Angelo's own (unconfined) writer writing a path a script put in data: an image whose file the script pointed at
    # the running program, saved later by Angelo, is judged by may_write, which a run-only record never satisfies.
    before = update.read_bytes()
    tagged = run("import bpy\nimg = bpy.data.images.new('payload', 4, 4)\nimg.filepath_raw = TARGET\n"
                 "img.file_format = 'PNG'\nresult = img.name\n", TARGET=str(update))
    name = (tagged.get("result") or {}).get("result")
    try:
        bpy.data.images[name].save()
        saved = "saved"
    except Exception as error:   # noqa: BLE001 - the refusal is the expected outcome.
        saved = repr(error)[:300]
    check("angelo_never_writes_the_program_where_a_script_pointed", name and update.read_bytes() == before,
          {"script": tagged, "save": saved})
    # C's change record may hold a path a script only named (a path property) as the script's: then it never runs,
    # and its refusal says so (fail closed; the coordinator's ruling for 2.0.1). Either way, never a wrong reason.
    pointed = probe(find(root))
    REPORT["pointed_program_still_runs"] = pointed
    check("a_program_a_script_pointed_at_runs_or_says_why", pointed == {"version": [2, 1, 310]}
          or "wrote or named" in pointed.get("refused", ""), pointed)

    # Another update (the installer retargets the link again): found, it starts.
    fresh = program(root / "share/claude/versions/2.1.320", "2.1.320")
    link.unlink()
    make_link(link, fresh)
    check("a_second_update_starts", probe(find(root)) == {"version": [2, 1, 320]}, probe(find(root)))

    # A script writes over the program itself (a file outside Angelo's folders, which scripts may write): C's change
    # record refuses it from then on, found again or not, and the refusal says a script changed it.
    written = run("import bpy\nbpy.ops.wm.save_as_mainfile(filepath=TARGET, copy=True)\nresult = 'saved'\n",
                  TARGET=str(fresh))
    after = find(root)
    refused = probe(after or str(link))
    check("a_program_a_script_wrote_never_runs",
          not owned_paths.may_run(str(link)) and "refused" in refused, {"script": written, "probe": refused})
    message = refused.get("refused", "")
    check("its_refusal_says_a_script_wrote_it", "agent script wrote or named" in message
          and "Forget Agent Changes in a Folder" in message, message)
    # The same start judged by C's rules alone: the script wrote the link's target, never the link, and the link's
    # folder is on PATH, which admits a program by itself; C's change record follows the link (``resolve``), so the
    # link counts as the script's too, and may_run refuses it and the CLI found again.
    on_path = os.path.normcase(str(link.parent)) in map(os.path.normcase, os.environ["PATH"].split(os.pathsep))
    behind = os.path.normcase(os.path.realpath(link)) == os.path.normcase(os.path.realpath(fresh))
    check("a_script_written_target_behind_a_path_link_is_refused",
          on_path and behind and not owned_paths.may_run(str(link)) and not owned_paths.may_run(str(after or link)),
          {"on_path": on_path, "behind": behind, "link_script_made": owned_paths.script_made(str(link))})

    # A script retargets the link to a program of its own; the next start finds it as real launches do. Either line
    # holds: confinement refuses the retarget (no confined script makes a link: hook.cc ``link_refused``), or the
    # start through the retargeted link is refused. It fails only when the link moved and the start ran.
    other = program(root / "elsewhere/claude-other", "2.1.302")   # Not vouched, not on PATH.
    retarget = run("import os\nos.remove(LINK)\nos.symlink(OTHER, LINK)\nresult = 'retargeted'\n",
                   LINK=str(link), OTHER=str(other))
    moved = os.path.lexists(link) and os.path.realpath(link) == os.path.realpath(other)
    start = probe(find(root) or str(link))
    start_refused = "refused" in start and not owned_paths.may_run(str(link))
    held = ("the retarget was refused" if not moved and retarget.get("refused") else
            "the start was refused" if moved and start_refused else
            "the link moved and the start ran" if moved else "the script neither moved the link nor was refused")
    check("a_link_a_script_retargeted_is_refused", held in ("the retarget was refused", "the start was refused"),
          {"held": held, "script": retarget, "moved": moved, "link_left": os.path.lexists(link), "probe": start})
    check("every_start_found_only_the_suite_s_programs", not FOUND_OUTSIDE, FOUND_OUTSIDE)
    refused = probe(str(other))   # A program Angelo starts by none of its rules.
    check("an_unknown_program_is_refused", "refused" in refused and not owned_paths.may_run(str(other)), refused)
    message = refused.get("refused", "")
    check("a_refusal_is_never_reported_as_too_old",
          "version could not be read" not in message and "or newer" not in message, message)
    check("a_refusal_says_what_happened_and_what_to_do", "starts only" in message and "choose" in message.lower(),
          message)

try:
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    main()
    REPORT["success"] = bool(REPORT["checks"]) and all(REPORT["checks"].values())
except Exception:   # noqa: BLE001 - the receipt says why.
    REPORT["error"] = traceback.format_exc()
(OUT / "runtime-symlink.json").write_text(json.dumps(REPORT, indent=2, default=str))
print(json.dumps(REPORT, default=str)[:6000])
if REPORT["success"]:
    print("ANGELO_RUNTIME_SYMLINK_OK")
sys.exit(0 if REPORT["success"] else 1)
