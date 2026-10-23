#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Checks every release step shares: Git LFS pointers, build bytecode, the shipped update keys and the agent runtime.

``sign_notarize_macos.py`` and ``source_release.py`` use them; ``sign_windows.ps1`` repeats the update-key
check in PowerShell and ``build_release_windows.ps1`` the agent-runtime check. Standard library only; no bpy.

The agent runtime (``agent_runtime_problems``) is the patched Codex build the app bundles, pinned by
``scripts/addons_core/angelo_agent/agent_runtime.json``. From the command line, before a release build::

    python3 scripts/angelo/release_checks.py agent-runtime <folder> [--platform macos|windows] [--no-run]

checks a folder meant for ``ANGELO_AGENT_RUNTIME_DIR``; it prints the problems and exits 1 when there are any.
``trust-store <bundle> [--offline]`` refuses a bundle whose trust stores (certifi, pip's copy, the Codex
webpki-roots) are older than the newest release or hold a distrusted key (``trust_store.py``; run on the built app
before signing). ``builder-paths <bundle or unpacked payload>`` refuses any builder path or credential in what ships
(``builder_paths.py``; every release stage runs it). ``windows-toolset --binary <Angelo.exe>`` refuses a Windows
build that clang-cl and lld-link did not make (``windows_toolset.py``; the Windows release build and stage run it).
``prompt-audit <recordings folder>... [--build COMMIT]`` refuses a release whose recordings (the gates' and
``prompt_route_probe.py``'s) show a route that lacks a fact meant for it, receives one it must not, or was not
recorded at all (``prompt_audit.py``, doc/angelo/prompt_audit.rst).
"""

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # builder_paths beside this file, also under python -I
from angelo_agent import update_feed  # noqa: E402  (standard library only)
from shipped_sources import LFS_POINTER, is_lfs_pointer  # noqa: E402,F401  (one owner; release_checks.* still works)
# Where the add-on's key list sits inside an installed tree, below the Blender version folder (5.2).
KEYS_IN_INSTALL = "scripts/addons_core/angelo_agent/update_keys.json"


IDENTITY = ROOT / "scripts/modules/angelo_identity.json"


def _identity_publisher(identity=IDENTITY):
    pin = json.loads(Path(identity).read_text(encoding="utf-8"))["windows"]["authenticode_publisher"]
    return pin["name"], pin["country"]


def identity_publisher_text(identity=IDENTITY):
    return "%s (%s)" % _identity_publisher(identity)


def authenticode_publisher_problems(subjects=(), files=(), identity=IDENTITY, signature=None):
    """Problems with signer ``subjects``, or with ``files``' Authenticode signatures, against
    ``windows.authenticode_publisher``, compared as the updater compares a downloaded installer with the running app
    (update_stage_win): a release whose certificate writes the name otherwise fails here, not in every user's next
    update. ``files`` are read the updater's way (update_stage_win.authenticode, Windows): each must be Valid."""
    from angelo_agent import update_stage_win  # standard library only (ctypes is used only on Windows calls)
    expected = update_stage_win.publisher_named(*_identity_publisher(identity))
    read = signature or update_stage_win.authenticode
    checked = [(repr(subject), "Valid", subject) for subject in subjects]
    for path in files:
        found = read(path)
        checked.append((str(path), found["status"], found["subject"]))
    problems = []
    for label, status, subject in checked:
        if status != "Valid":
            problems.append("%s: Authenticode status %s, not Valid" % (label, status))
            continue
        found = update_stage_win.publisher(subject)
        if found != expected:
            problems.append("%s names publisher %r, not angelo_identity.json's %r: Angelo installs signed by another "
                            "publisher refuse this release's updates" % (label, found, expected))
    return problems


def msi_signing_record_problems(installer, signing, files=(), identity=IDENTITY, signature=None):
    """Write ``installer``'s signing record (msi_signing.py) when the release really is ``signing``: signed means the
    publisher check passes for it and ``files``; unsigned means none of them carries a signature at all. Returns the
    problems (and writes nothing) otherwise."""
    import msi_signing  # noqa: E402  (beside this file; standard library only)
    from angelo_agent import update_stage_win
    read = signature or update_stage_win.authenticode
    paths = [installer] + list(files)
    if signing == "signed":
        problems = authenticode_publisher_problems(files=paths, identity=identity, signature=read)
    else:
        problems = ["%s: Authenticode status %s; an unsigned release carries no signature" % (path, status)
                    for path, status in ((path, read(path)["status"]) for path in paths) if status != "NotSigned"]
    if not problems:
        msi_signing.write(installer, signing)
    return problems


class CheckError(Exception):
    pass


def real_file(path):
    """``path`` when it holds real content; refuses a Git LFS pointer with the command that fixes it."""
    path = Path(path)
    with open(path, "rb") as handle:
        if is_lfs_pointer(handle.read(1024)):
            raise CheckError("%s is a Git LFS pointer; run 'git lfs checkout %s' (local objects only) first"
                             % (path, path.relative_to(ROOT) if ROOT in path.parents else path))
    return path


def lfs_pointer_files(install):
    """Every file under ``install`` that is a Git LFS pointer rather than its content, as relative paths.

    A build copies whatever the checkout holds, so a file Git LFS never checked out ships as a 130-byte text stub
    (the app templates' startup files did). Run on the installed bundle or folder before signing or packaging.
    """
    root = Path(install)
    found = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink() and path.stat().st_size < 1024:
            with open(path, "rb") as handle:
                if is_lfs_pointer(handle.read(1024)):
                    found.append(path.relative_to(root).as_posix())
    return found


# PEP 552: the 32-bit flags after a .pyc's magic number; bit 0 set means hash-based. The install step precompiles
# the bundled library and scripts as checked hash-based files (build_files/utils/angelo_python_bytecode.py).
# Python itself writes timestamp-based files (flags 0) unless it is rewriting a hash-based one.
PYC_HASH_BASED = 0b01


def is_build_bytecode(path):
    """True for a hash-based ``.pyc``: bytecode the build precompiled, which a release keeps."""
    if not str(path).endswith(".pyc"):
        return False
    try:
        with open(path, "rb") as handle:
            header = handle.read(8)
    except OSError:
        return False
    return len(header) == 8 and bool(int.from_bytes(header[4:8], "little") & PYC_HASH_BASED)


def drop_runtime_bytecode(cache, removed):
    """Empty the ``__pycache__`` folder ``cache`` of what Python wrote at run time, keeping the build's precompiled
    bytecode (``is_build_bytecode``); a folder left with nothing goes as a whole. Appends to ``removed``."""
    names = os.listdir(cache)
    kept = {name for name in names if is_build_bytecode(os.path.join(cache, name))}
    if not kept:
        shutil.rmtree(cache)
        removed.append(cache)
        return
    for name in sorted(set(names) - kept):
        path = os.path.join(cache, name)
        shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
        removed.append(path)


def shipped_key_files(install):
    """The ``update_keys.json`` files of an installed tree (a macOS bundle or a Windows install folder)."""
    install = Path(install)
    resources = install / "Contents/Resources" if (install / "Contents").is_dir() else install
    return sorted(resources.glob("*/" + KEYS_IN_INSTALL))


def update_key_problems(install, allow_none=False):
    """Why this build cannot ship as a release that finds updates (empty: it can).

    The key list must be present, valid JSON and hold at least one active Ed25519 key, exactly as the app
    loads it; ``allow_none`` accepts an empty list (rehearsals and dry runs only).
    """
    files = shipped_key_files(install)
    if len(files) != 1:
        return ["%d update_keys.json files in %s (expected 1)" % (len(files), install)]
    try:
        json.loads(files[0].read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return ["%s is not readable JSON: %s" % (files[0], error)]
    if update_feed.load_keys(files[0]) or allow_none:
        return []
    return ["%s has no active update-signing key, so this build could never find an update. Add the Cloud KMS "
            "key with 'sign_update_feed.py add-key' and rebuild (doc/angelo/releasing.rst), or pass "
            "--allow-no-update-key for a rehearsal" % files[0]]


# -- the agent runtime ---------------------------------------------------------------------------------------

AGENT_RUNTIME_PIN = ROOT / "scripts/addons_core/angelo_agent/agent_runtime.json"
# The thread requests that must accept ``cacheAffinityKey`` (app-server schema, ``v2/<name>.json``).
AGENT_RUNTIME_PARAMS = ("ThreadStartParams", "ThreadResumeParams", "ThreadForkParams")


def agent_runtime_pin():
    return json.loads(AGENT_RUNTIME_PIN.read_text(encoding="utf-8"))


def agent_runtime_folder(install, platform):
    """Where an installed app keeps its agent runtime: ``Angelo.app/Contents/Helpers`` or ``<install>/agent-runtime``."""
    return Path(install) / agent_runtime_pin()["platforms"][platform]["folder"]


# PE/COFF (Microsoft's "PE Format"): the machine type of x64, the PE32+ optional header, a DLL's flag, and the
# certificate table (data directory 4), where an Authenticode signature is embedded.
PE_MACHINE_AMD64 = 0x8664
PE32_PLUS = 0x20B
PE_FILE_DLL = 0x2000
PE_CERTIFICATE_TABLE = 4


def pe_info(path):
    """``(machine, is_dll, certificate_table_bytes)`` of a PE32+ file, or None when ``path`` is not one."""
    with open(path, "rb") as handle:
        head = handle.read(4096)
        if len(head) < 0x40 or head[:2] != b"MZ":
            return None
        offset = struct.unpack_from("<I", head, 0x3C)[0]
        if offset + 24 + 2 > len(head) or head[offset:offset + 4] != b"PE\0\0":
            return None
        machine, _sections, _stamp, _symbols, _count, optional_size, characteristics = struct.unpack_from(
            "<HHIIIHH", head, offset + 4)
        optional = offset + 24
        directory = optional + 112 + 8 * PE_CERTIFICATE_TABLE
        if struct.unpack_from("<H", head, optional)[0] != PE32_PLUS or directory + 8 > min(len(head), optional + optional_size):
            return None
        # The certificate table's "address" is a file offset, not a virtual address.
        start, size = struct.unpack_from("<II", head, directory)
        table = b""
        if start and size:
            handle.seek(start)
            table = handle.read(size)
        return machine, bool(characteristics & PE_FILE_DLL), table


def _code_problem(path, platform):
    """Why ``path`` is not an executable for ``platform`` ("" when it is one)."""
    if platform == "macos":
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import macho
        try:
            info = macho.read(path)
        except (ValueError, OSError) as error:
            return "%s is a malformed Mach-O file: %s" % (path.name, error)
        if info is None or info.file_type != "executable" or "arm64" not in info.archs:
            return "%s is not an arm64 Mach-O executable" % path.name
        if not os.access(path, os.X_OK):
            return "%s is not marked executable" % path.name
        return ""
    try:
        info = pe_info(path)
    except OSError as error:
        return "%s cannot be read: %s" % (path.name, error)
    if info is None or info[0] != PE_MACHINE_AMD64 or info[1]:
        return "%s is not an x64 Windows executable" % path.name
    return ""


def _upstream_signature_problems(folder, files):
    """OpenAI's unchanged release executables (``upstream_signed``) must still carry OpenAI's own Authenticode
    signature: an embedded certificate table that names ``upstream_signer``. Whether that signature is valid
    is Windows' to say (``sign_windows.ps1`` and ``build_release_windows.ps1`` ask it); here, a stand-in
    without one, or a file re-signed by someone else, is refused on any host."""
    problems = []
    signer = files.get("upstream_signer", "")
    for name in files.get("upstream_signed", ()):
        path = folder / name
        if not path.is_file():
            continue
        try:
            info = pe_info(path)
        except OSError:
            info = None
        table = info[2] if info else b""
        if not table or signer.encode("utf-8") not in table:
            problems.append("%s must carry %s's own signature (it is that release's unchanged file): take it from "
                            "Codex's release again, never signed or re-signed here" % (name, signer))
    return problems


def _run_problems(folder, files, pin, runner):
    """The pinned version and the cache-affinity parameter, asked of the binary itself (offline)."""
    codex = folder / files["required"][0]
    with tempfile.TemporaryDirectory(prefix="angelo-agent-runtime-") as home:
        env = dict(os.environ, CODEX_HOME=home, HOME=home, USERPROFILE=home)
        version = runner([str(codex), "--version"], check=False, timeout=120, env=env)
        reported = (version.stdout or "").split()
        if version.returncode != 0 or not reported or reported[-1] != pin["version"]:
            said = (version.stdout or version.stderr or "").strip()[:200]
            return ["%s --version exited %s saying %r; this source pins Codex %s" % (
                codex.name, version.returncode, said, pin["version"])]
        out = Path(home, "schema")
        schema = runner([str(codex), "app-server", "generate-json-schema", "--experimental", "--out", str(out)],
                        check=False, timeout=120, env=env)
        if schema.returncode != 0:
            return ["%s could not write its app-server schema: %s" % (codex.name, (schema.stderr or "").strip()[-300:])]
        problems = []
        for name in AGENT_RUNTIME_PARAMS:
            try:
                properties = json.loads((out / "v2" / (name + ".json")).read_text(encoding="utf-8"))["properties"]
            except (OSError, ValueError, KeyError, TypeError):
                properties = {}
            for feature in pin["features"]:
                if feature not in properties:
                    problems.append("%s's %s has no %s: this is not the patched build (stock Codex has the same "
                                    "version)" % (codex.name, name, feature))
        return problems


def agent_runtime_problems(folder, platform, runner=None, exclusive=False):
    """Why ``folder`` cannot ship as this platform's agent runtime (empty: it can).

    ``folder`` holds the runtime: an ``ANGELO_AGENT_RUNTIME_DIR``, or an installed app's
    (``agent_runtime_folder``); ``exclusive``: nothing but the pinned files may be in it (an installed app).
    Every required file must be an executable for the platform (arm64 Mach-O, or an x64 Windows PE
    executable), and on Windows the pin's ``upstream_signed`` files must carry OpenAI's own signature.
    ``runner(argv, check=False, timeout=..., env=...)`` runs the binaries when this host can: then
    ``codex --version`` must name the pinned version and the app-server schema must offer each pinned
    feature on thread/start, thread/resume and thread/fork. Both are offline; the runtime writes only to a
    temporary ``CODEX_HOME``.
    """
    pin = agent_runtime_pin()
    files = pin["platforms"][platform]
    folder = Path(folder)
    wanted = ", ".join(files["required"])
    if not folder.is_dir():
        return ["%s is missing: a release bundles Angelo's agent runtime (Codex %s: %s; doc/angelo/releasing.rst, "
                "\"Agent runtime\")" % (folder, pin["version"], wanted)]
    problems = []
    for name in files["required"]:
        path = folder / name
        if not path.is_file():
            problems.append("%s lacks %s (the agent runtime is %s)" % (folder, name, wanted))
        elif _code_problem(path, platform):
            problems.append(_code_problem(path, platform))
    for name in files["optional"]:
        if (folder / name).is_file() and _code_problem(folder / name, platform):
            problems.append(_code_problem(folder / name, platform))
    problems += _upstream_signature_problems(folder, files)
    if exclusive:
        known = set(files["required"]) | set(files["optional"])
        problems += ["%s holds %s, which is not part of the pinned agent runtime" % (folder, entry.name)
                     for entry in sorted(folder.iterdir()) if entry.name not in known]
    if runner is not None and not problems:
        problems += _run_problems(folder, files, pin, runner)
    return problems


def release_marker_problems(install, platform):
    """A release build's installed app must carry the pin's ``release_marker`` (``ANGELO_REQUIRE_AGENT_RUNTIME``):
    without it the app would accept another Codex in ``ANGELO_AGENT_RUNTIME``."""
    marker = Path(install) / agent_runtime_pin()["platforms"][platform]["release_marker"]
    if marker.is_file():
        return []
    return ["%s is missing: build with ANGELO_REQUIRE_AGENT_RUNTIME=ON (the release scripts do), so the app runs "
            "only its own agent runtime" % marker]


def _run(argv, check=False, timeout=None, env=None):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env, check=check)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Release checks shared by the release tools.")
    commands = parser.add_subparsers(dest="command", required=True)
    runtime = commands.add_parser("agent-runtime", help="check a folder meant for ANGELO_AGENT_RUNTIME_DIR")
    runtime.add_argument("folder")
    runtime.add_argument("--platform", choices=("macos", "windows"),
                         default="windows" if sys.platform == "win32" else "macos")
    runtime.add_argument("--no-run", action="store_true", help="do not run the binaries (another host's build)")
    pointers = commands.add_parser("lfs-pointers", help="refuse an installed bundle that ships Git LFS pointers")
    pointers.add_argument("install")
    clean = commands.add_parser("source-clean", help="refuse a source tree that is not exactly its commit")
    clean.add_argument("source")
    clean.add_argument("--commit", default=None, help="the commit the tree must be (the one sent to a build machine)")
    shipped = commands.add_parser("tracked-scripts", help="refuse a bundle that ships script files the commit lacks")
    shipped.add_argument("scripts", help="the bundle's (or unpacked payload's) scripts folder")
    shipped.add_argument("--source", required=True)
    shipped.add_argument("--rev", default="HEAD")
    shipped.add_argument("--libdir", default=None, help="the pinned precompiled libraries the build used")
    dated = commands.add_parser("release-dated", help="refuse to build a release whose own notes are undated")
    dated.add_argument("source")
    commands.add_parser("builder-paths", add_help=False,
                        help="refuse builder paths or credentials in a bundle or unpacked payload (builder_paths.py)")
    commands.add_parser("trust-store", add_help=False,
                        help="refuse a bundle whose trust stores are stale or hold a distrusted key (trust_store.py)")
    commands.add_parser("prompt-audit", add_help=False,
                        help="refuse gate recordings in which a route lacks a fact meant for it (prompt_audit.py)")
    signer = commands.add_parser("authenticode-publisher",
                                 help="refuse a signer whose publisher is not angelo_identity.json's (the updater's comparison)")
    signer.add_argument("--subject", action="append", default=[],
                        help="a signer certificate subject as Get-AuthenticodeSignature reports it (repeatable)")
    signer.add_argument("--file", action="append", default=[],
                        help="a signed file, read the updater's way (Windows; repeatable)")
    record = commands.add_parser("msi-signing-record",
                                 help="the release build's last gate: record whether its MSI is signed (msi_signing.py)")
    record.add_argument("--installer", required=True, help="the MSI")
    record.add_argument("--signing", required=True, choices=("unsigned", "signed"),
                        help="what this release build is (build_release_windows.ps1: -Sign or not)")
    record.add_argument("--file", action="append", default=[],
                        help="another file of the release held to the same state (the app the MSI installs)")
    same = commands.add_parser("msi-same",
                               help="refuse a signed MSI that holds anything but the packed MSI's streams (msi_compound.py)")
    same.add_argument("--packed", required=True, help="the MSI the build packed (unsigned)")
    same.add_argument("--signed", required=True, help="the signing VM's signed copy of it")
    commands.add_parser("windows-toolset", add_help=False,
                        help="refuse a Windows build that clang-cl and lld-link did not make (windows_toolset.py)")
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["prompt-audit"]:
        import prompt_audit  # noqa: E402  (beside this file; standard library only)
        return prompt_audit.main(["check", "--require-routes"] + argv[1:])   # every release route recorded
    if argv[:1] == ["builder-paths"]:
        import builder_paths  # noqa: E402  (beside this file, on sys.path above; standard library only)
        return builder_paths.main(argv[1:])
    if argv[:1] == ["trust-store"]:
        import trust_store  # noqa: E402  (beside this file; standard library only)
        return trust_store.main(["check"] + argv[1:])
    if argv[:1] == ["windows-toolset"]:
        import windows_toolset  # noqa: E402  (beside this file; standard library only)
        return windows_toolset.main(argv[1:])
    arguments = parser.parse_args(argv)
    if arguments.command == "msi-same":   # Read as data: nothing in either MSI is executed.
        import msi_compound
        try:
            problems = msi_compound.same_problems(arguments.packed, arguments.signed)
        except (OSError, msi_compound.CompoundFileError) as error:
            problems = ["the MSIs cannot be read as Compound Files: %s" % error]
        for problem in problems[:40]:
            print("msi-same: " + problem, file=sys.stderr)
        if not problems:
            print("msi-same: the signed MSI holds exactly the packed MSI's %d entries, and a signature"
                  % len(msi_compound.digests(arguments.packed)))
        return 1 if problems else 0
    if arguments.command == "msi-signing-record":
        problems = msi_signing_record_problems(arguments.installer, arguments.signing, arguments.file)
        for problem in problems:
            print(problem, file=sys.stderr)
        if not problems:
            print("msi-signing-record: %s is %s" % (Path(arguments.installer).name, arguments.signing))
        return 1 if problems else 0
    if arguments.command == "authenticode-publisher":
        if not arguments.subject and not arguments.file:
            parser.error("authenticode-publisher needs --subject or --file")
        problems = authenticode_publisher_problems(arguments.subject, arguments.file)
        for problem in problems:
            print(problem, file=sys.stderr)
        if not problems:
            print("authenticode-publisher: every signer is %s" % identity_publisher_text())
        return 1 if problems else 0
    if arguments.command in ("source-clean", "tracked-scripts"):
        import shipped_sources  # noqa: E402  (beside this file; standard library only)
        if arguments.command == "source-clean":
            problems = shipped_sources.source_clean(arguments.source, arguments.commit)
            label, ok = "source", "%s is exactly its commit" % arguments.source
        else:
            problems = ["shipped but not tracked at %s: %s" % (arguments.rev, rel) for rel in
                        shipped_sources.untracked_shipped(arguments.scripts, arguments.source, arguments.rev,
                                                          libdir=arguments.libdir)]
            label = "tracked scripts"
            ok = "every file in %s is tracked or built from a tracked source" % arguments.scripts
        for problem in problems:
            print("%s: %s" % (label, problem), file=sys.stderr)
        if not problems:
            print("%s: %s" % (label, ok))
        return 1 if problems else 0
    if arguments.command == "release-dated":
        import release_dated  # noqa: E402  (beside this file; standard library only)
        problems = release_dated.problems(arguments.source)
        for problem in problems:
            print("release notes: %s" % problem, file=sys.stderr)
        if not problems:
            print("release notes: %s is dated in CHANGELOG.md and the bundled release_notes.json"
                  % release_dated.version_label(arguments.source))
        return 1 if problems else 0
    if arguments.command == "lfs-pointers":
        found = lfs_pointer_files(arguments.install)
        for path in found:
            print("Git LFS pointer shipped: %s (run 'git lfs checkout' with local objects, then rebuild)" % path,
                  file=sys.stderr)
        if not found:
            print("lfs pointers: none in %s" % arguments.install)
        return 1 if found else 0
    problems = agent_runtime_problems(arguments.folder, arguments.platform, None if arguments.no_run else _run)
    for problem in problems:
        print("agent runtime: %s" % problem, file=sys.stderr)
    if not problems:
        print("agent runtime: %s holds Codex %s with %s" % (arguments.folder, agent_runtime_pin()["version"],
                                                            ", ".join(agent_runtime_pin()["features"])))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
