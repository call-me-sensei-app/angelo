#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The macOS release: a built Angelo.app to a signed, notarized and stapled disk image.

Runbook: doc/angelo/releasing.rst. From the repository root, after ``build_release_macos.sh``::

    python3 scripts/angelo/sign_notarize_macos.py --app ../build-angelo-release/bin/Angelo.app --out dist/macos \\
        --identity "Developer ID Application: <Organization> (<TEAMID>)" --notary-profile <profile>

    # Dry run with no certificate and no Apple account: ad-hoc signature, no notarization.
    python3 scripts/angelo/sign_notarize_macos.py --app ... --out ... --identity - --skip-notarize \\
        --allow-no-update-key --launch-check

Steps, each refusing rather than continuing on anything unexpected:

1. **Stage.** Clone the app into ``--out`` (the build folder is never changed) and drop what must not be
   sealed: ``.pyc`` files Python wrote (an older build that ran wrote some; the build's own precompiled
   library and scripts, ``release_checks.is_build_bytecode``, stay), ``.DS_Store``, and the bundled Python's
   build-only ``python.o`` and ``libpython*.a``.
2. **Check.** Inventory every Mach-O file. Refuse an object file, a library loaded by an absolute path
   outside the system, an absolute ``LC_RPATH``, a symbolic link leaving the bundle, a nested bundle type
   this pipeline does not sign, an app whose identifier or version is not this source's, (unless
   ``--allow-no-update-key``) an ``update_keys.json`` without an active key, and (unless
   ``--allow-no-agent-runtime``) an agent runtime that is missing, incomplete or joined by anything else in
   ``Contents/Helpers`` (``release_checks.agent_runtime_problems``), or an app without the release marker
   that makes it refuse another Codex (``release_checks.release_marker_problems``).
3. **Sign inside out.** Every loose Mach-O file, deepest first: dylibs and Python modules as they are,
   standalone executables (the bundled ``python3.13``) with ``release/darwin/entitlements.plist``, the
   agent runtime's two executables (``Contents/Helpers/codex`` and ``codex-code-mode-host``, by path)
   with ``release/darwin/agent_runtime_entitlements.plist``; then the Quick Look extension with its sandbox
   entitlements; then the app. Hardened runtime and a secure timestamp on every signature (an ad-hoc dry
   run cannot be timestamped).
4. **Verify.** ``codesign --verify --deep --strict``, every file's flags, one team for every file, the
   entitlements of the app and of every file signed with entitlements exactly as its file says, and
   ``spctl`` (which refuses until notarization).
5. **Launch check** (``--launch-check``, ``release_launch_checks.py``). A clone of the signed app starts
   headless under the hardened runtime, calls through ctypes, imports NumPy and renders an OSL material
   with Cycles (OSL's LLVM JIT is what needs ``allow-unsigned-executable-memory``); the clone's signed
   agent runtime reports the pinned version and writes its app-server schema with ``cacheAffinityKey``.
   Then the seal check: another clone, started through Launch Services as Finder starts it (headless
   with the add-on and both kinds of child Python, then a window launch unless ``--unstyled-dmg`` says
   there is no desktop session), must be unchanged and still pass ``codesign --verify --deep --strict``.
6. **Notarize and staple the app.** A ``ditto`` zip of the signed app goes to ``xcrun notarytool submit
   --keychain-profile <profile> --wait``; the ticket is stapled to the app itself and validated, and
   ``spctl`` must accept it. An app copied out of the image then opens offline on its own ticket.
7. **Disk image** (``dmg_macos.py``). The stapled app, the Applications link and the window styled by
   ``release/darwin/dmg_window.applescript.in`` through Finder, its stored layout read back and checked
   (``--unstyled-dmg`` for a session without Finder automation), compressed, checked mounted, then signed.
8. **Notarize, staple, validate the image.** The same submission for the image, then ``stapler staple``
   and ``stapler validate``; ``spctl`` must accept the image, and the app inside it must carry its ticket.

``angelo-macos-release.json`` in ``--out`` records every step. The identity (``--identity`` or
``ANGELO_SIGN_IDENTITY``) is a certificate name or SHA-1 from the login keychain, and the notary profile
(``--notary-profile`` or ``ANGELO_NOTARY_PROFILE``) is the name given to ``xcrun notarytool
store-credentials``. This tool never reads, prints or stores a password, key or token.

Standard library only (Python 3.9 or newer, as Xcode's ``python3``).
"""

import argparse
import hashlib
import json
import os
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import dmg_macos  # noqa: E402
import macho  # noqa: E402
import release_checks  # noqa: E402
import release_downloads  # noqa: E402
import release_launch_checks  # noqa: E402

IDENTITY = json.loads((ROOT / "scripts/modules/angelo_identity.json").read_text(encoding="utf-8"))
APP_ENTITLEMENTS = ROOT / "release/darwin/entitlements.plist"
APPEX_ENTITLEMENTS = ROOT / "release/darwin/thumbnailer_entitlements.plist"
# The agent runtime's executables only (codex, codex-code-mode-host), by path: V8's JIT, nothing more.
AGENT_RUNTIME_ENTITLEMENTS = ROOT / "release/darwin/agent_runtime_entitlements.plist"
AGENT_RUNTIME = release_checks.agent_runtime_pin()["platforms"]["macos"]
# What each kind of signature carries (``codesign_command``); libraries carry none.
ENTITLEMENTS = {"executable": APP_ENTITLEMENTS, "app": APP_ENTITLEMENTS, "appex": APPEX_ENTITLEMENTS,
                "agent_runtime": AGENT_RUNTIME_ENTITLEMENTS}
RECEIPT = "angelo-macos-release.json"
SIGNED_BUNDLES = (".appex",)   # Nested bundle types this pipeline signs; anything else is refused.
NESTED_BUNDLES = (".app", ".appex", ".framework", ".bundle", ".xpc", ".plugin", ".kext", ".systemextension")


ReleaseError = release_checks.CheckError


class Runner:
    """Runs the signing tools. Tests replace it with a recording fake; every command is logged."""

    def __init__(self, log=None):
        self.log = log if log is not None else []

    def __call__(self, argv, check=True, timeout=None, env=None):
        self.log.append(list(argv))
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
        if check and result.returncode != 0:
            raise ReleaseError("%s failed (%d): %s" % (" ".join(argv[:3]), result.returncode,
                                                       (result.stderr or result.stdout).strip()[-1500:]))
        return result


# -- stage and inventory --------------------------------------------------------------------------------------

def is_build_only(folder, name):
    """The bundled Python's build leftovers (``config-*/python.o`` and ``libpython*.a``): nothing at run time
    reads them and an object file cannot be signed. Current builds no longer install them
    (source/creator/CMakeLists.txt, ``install_dir``); older builds still carry them."""
    return os.path.basename(folder).startswith("config-") and (
        name == "python.o" or (name.startswith("libpython") and name.endswith(".a")))


def stage(app, out, runner):
    """Clone ``app`` into ``out`` and drop caches and build-only files; returns (staged app, removed paths)."""
    target = out / app.name
    if target.exists():
        raise ReleaseError("%s exists; use an empty --out folder for each release" % target)
    out.mkdir(parents=True, exist_ok=True)
    runner(["ditto", str(app), str(target)])
    removed = []
    for folder, dirs, files in os.walk(target, topdown=True):
        for name in list(dirs):
            if name == "__pycache__":
                release_checks.drop_runtime_bytecode(os.path.join(folder, name), removed)
                dirs.remove(name)
        for name in files:
            path = os.path.join(folder, name)
            if name == ".DS_Store" or name.endswith(".pyc") or is_build_only(folder, name):
                os.remove(path)
                removed.append(path)
    return target, [str(Path(p).relative_to(target)) for p in removed]


def inventory(app):
    """``(code, links)``: every Mach-O file with its ``MachO``, and every symbolic link with its target."""
    code, links = {}, {}
    for folder, dirs, files in os.walk(app):
        for name in dirs + files:
            path = Path(folder, name)
            if path.is_symlink():
                links[path] = os.readlink(path)
        for name in files:
            path = Path(folder, name)
            if not path.is_symlink():
                try:
                    info = macho.read(path)
                except (ValueError, struct.error) as error:
                    raise ReleaseError("%s is a malformed Mach-O file: %s" % (path, error)) from None
                if info is not None:
                    code[path] = info
    return code, links


def bundle_problems(app, code, links):
    """Every reason this bundle cannot be signed for release, as text (empty: none found)."""
    problems = []
    root = app.resolve()
    for path, target in sorted(links.items()):
        resolved = (path.parent / target).resolve()
        if os.path.isabs(target) or root not in resolved.parents:
            problems.append("%s links outside the bundle (%s)" % (path.relative_to(app), target))
    for folder, dirs, _files in os.walk(app):
        for name in dirs:
            nested = Path(folder, name)
            if nested.suffix in NESTED_BUNDLES and nested.suffix not in SIGNED_BUNDLES:
                problems.append("%s is a nested %s bundle this pipeline does not sign" % (nested.relative_to(app),
                                                                                            nested.suffix))
    for path, info in sorted(code.items()):
        relative = path.relative_to(app)
        if info.file_type not in ("executable", "dylib", "bundle"):
            problems.append("%s is a Mach-O %s file, which cannot be signed or shipped" % (relative, info.file_type))
        for name in info.dylibs:
            if not (macho.is_system_path(name) or macho.is_relative_path(name)):
                problems.append("%s loads %s by an absolute path outside the system" % (relative, name))
        for name in info.rpaths:
            if not macho.is_relative_path(name):
                problems.append("%s has the absolute LC_RPATH %s" % (relative, name))
    return problems


def bundle_executable(bundle):
    info = plistlib.loads((bundle / "Contents/Info.plist").read_bytes())
    return bundle / "Contents/MacOS" / info["CFBundleExecutable"], info


def identity_problems(app, info):
    """The bundle must be this source's product and version, so a stale build is never signed."""
    expected = {"CFBundleIdentifier": IDENTITY["bundle"]["macos_id"],
                "CFBundleShortVersionString": IDENTITY["version"]["label"],
                "CFBundleExecutable": IDENTITY["bundle"]["macos_executable"]}
    return ["Info.plist %s is %r; this source says %r" % (key, info.get(key), value)
            for key, value in expected.items() if info.get(key) != value]


# -- signing -----------------------------------------------------------------------------------------------

def agent_runtime_paths(app):
    """The agent runtime's executables in ``app`` (``Contents/Helpers``), pinned by name, whether present or not."""
    folder = release_checks.agent_runtime_folder(app, "macos")
    return {folder / name for name in AGENT_RUNTIME["required"] + AGENT_RUNTIME["optional"]}


def signing_plan(app, code):
    """``[(path, kind)]`` in signing order: loose code deepest first, then nested bundles, then the app.

    ``kind`` is ``library`` (dylibs and Python modules), ``executable`` (a standalone program such as the
    bundled ``python3.13``), ``agent_runtime`` (the agent runtime's executables, by path), ``appex`` or
    ``app``. A bundle's own main executable is signed with its bundle.
    """
    bundles = sorted({p for p in app.rglob("*") if p.is_dir() and p.suffix in SIGNED_BUNDLES},
                     key=lambda p: (-len(p.parts), str(p)))
    mains = {bundle_executable(b)[0] for b in bundles + [app]}
    runtime = agent_runtime_paths(app)
    loose = [(p, "agent_runtime" if p in runtime else "executable" if info.file_type == "executable" else "library")
             for p, info in code.items() if p not in mains]
    loose.sort(key=lambda item: (-len(item[0].parts), str(item[0])))
    return loose + [(b, b.suffix.lstrip(".")) for b in bundles] + [(app, "app")]


def codesign_command(path, kind, identity, keychain=None):
    argv = ["codesign", "--force", "--sign", identity, "--options", "runtime",
            "--timestamp=none" if identity == "-" else "--timestamp"]
    if keychain:
        argv += ["--keychain", keychain]
    if kind in ENTITLEMENTS:
        argv += ["--entitlements", str(ENTITLEMENTS[kind])]
    return argv + [str(path)]


def check_identity(identity, keychain, runner):
    """A release identity must be one "Developer ID Application" certificate with its key in the keychain."""
    if identity == "-":
        return
    argv = ["security", "find-identity", "-v", "-p", "codesigning"] + ([keychain] if keychain else [])
    lines = [line for line in runner(argv).stdout.splitlines() if '"' in line]
    matches = [line for line in lines if identity in line]
    if len(matches) != 1:
        raise ReleaseError("%d valid code-signing identities match %r; name exactly one (security find-identity "
                           "-v -p codesigning lists them)" % (len(matches), identity))
    if '"Developer ID Application: ' not in matches[0]:
        raise ReleaseError("%r is not a Developer ID Application identity; Gatekeeper accepts only those "
                           "outside the App Store" % identity)


def signature_details(path, runner):
    """``codesign -dv`` fields of one signature (Authority is repeated, so it is a list)."""
    fields = {"Authority": []}
    for line in runner(["codesign", "-dv", "--verbose=4", str(path)]).stderr.splitlines():
        key, _, value = line.partition("=")
        if key == "Authority":
            fields["Authority"].append(value)
        elif key in ("Identifier", "TeamIdentifier", "Timestamp", "Signature", "Runtime Version"):
            fields[key] = value
        elif key == "CodeDirectory v":
            fields["flags"] = value.split("flags=", 1)[-1].split(" ", 1)[0]
    return fields


def signed_entitlements(path, runner):
    """The entitlements ``path``'s signature carries (an empty dict for none)."""
    return plistlib.loads(runner(["codesign", "-d", "--entitlements", "-", "--xml", str(path)]).stdout.encode()
                          or plistlib.dumps({}))


def verify_signatures(app, plan, identity, runner):
    """Every signature in the plan, then the bundle as Gatekeeper's deep check sees it."""
    teams, report = set(), {"files": len(plan)}
    for path, kind in plan:
        runner(["codesign", "--verify", "--strict", "--verbose=2", str(path)])
        details = signature_details(path, runner)
        if "runtime" not in details.get("flags", ""):
            raise ReleaseError("%s is signed without the hardened runtime (%s)" % (path, details.get("flags")))
        if kind in ENTITLEMENTS and kind != "app":  # The app's own are checked below, after the deep check.
            if signed_entitlements(path, runner) != plistlib.loads(ENTITLEMENTS[kind].read_bytes()):
                raise ReleaseError("%s's entitlements differ from %s" % (path, ENTITLEMENTS[kind].name))
        if identity != "-":
            if not details["Authority"] or not details["Authority"][0].startswith("Developer ID Application: "):
                raise ReleaseError("%s is not signed by a Developer ID Application identity" % path)
            if not details.get("Timestamp"):
                raise ReleaseError("%s has no secure timestamp" % path)
            teams.add(details.get("TeamIdentifier"))
    if identity != "-" and len(teams) != 1:
        raise ReleaseError("the bundle is signed by more than one team: %s" % sorted(teams))
    runner(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)])
    signed = signed_entitlements(app, runner)
    wanted = plistlib.loads(APP_ENTITLEMENTS.read_bytes())
    if signed != wanted:
        raise ReleaseError("the app's entitlements %s differ from %s" % (sorted(signed), APP_ENTITLEMENTS.name))
    report["team"] = teams.pop() if teams else None
    report["entitlements"] = sorted(wanted)
    runtime = [path for path, kind in plan if kind == "agent_runtime"]
    report["agent_runtime"] = {"files": sorted(str(path.relative_to(app)) for path in runtime),
                               "entitlements": sorted(plistlib.loads(AGENT_RUNTIME_ENTITLEMENTS.read_bytes()))}
    report["app"] = signature_details(app, runner)
    gatekeeper = runner(["spctl", "--assess", "--type", "execute", "-vv", str(app)], check=False)
    report["spctl_before_notarization"] = (gatekeeper.stderr or gatekeeper.stdout).strip().splitlines()[-2:]
    return report


# -- notarization ----------------------------------------------------------------------------------------------

def submit(path, profile, keychain, work, runner):
    """Submit to Apple's notary service and wait; the accepted answer, or ReleaseError with Apple's log."""
    extra = ["--keychain", keychain] if keychain else []
    submitted = runner(["xcrun", "notarytool", "submit", str(path), "--keychain-profile", profile, "--wait",
                        "--output-format", "json"] + extra, check=False, timeout=3 * 3600)
    try:
        answer = json.loads(submitted.stdout)
    except ValueError:
        raise ReleaseError("notarytool gave no result: %s" % (submitted.stderr or submitted.stdout).strip()[-800:])
    if answer.get("status") != "Accepted":
        if answer.get("id"):
            log = work / "notary-log.json"
            runner(["xcrun", "notarytool", "log", answer["id"], "--keychain-profile", profile, str(log)] + extra,
                   check=False)
            raise ReleaseError("notarization %s: %s; Apple's log is in %s" % (answer.get("status"), answer.get(
                "message", ""), log))
        raise ReleaseError("notarization failed: %s" % (answer.get("message") or submitted.stderr.strip()[-800:]))
    return answer


def notarize_app(app, profile, keychain, work, runner):
    """Notarize the signed app through a zip and staple the ticket to the app itself, so a copy dragged
    out of the disk image is verified offline too; returns the submission's id and status."""
    archive = work / (app.stem + "-notarize.zip")
    runner(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(archive)])
    answer = submit(archive, profile, keychain, work, runner)
    archive.unlink()
    runner(["xcrun", "stapler", "staple", str(app)])
    runner(["xcrun", "stapler", "validate", str(app)])
    assessed = runner(["spctl", "--assess", "--type", "execute", "-vv", str(app)], check=False)
    verdict = (assessed.stderr or assessed.stdout).strip()
    if "Notarized Developer ID" not in verdict:
        raise ReleaseError("Gatekeeper does not see a notarized app: %s" % verdict)
    return {"id": answer.get("id"), "status": "Accepted", "stapled": True, "gatekeeper": verdict.splitlines()}


def notarize(image, profile, keychain, work, runner):
    """Submit the image, wait, staple and validate; returns the submission's id and status."""
    answer = submit(image, profile, keychain, work, runner)
    runner(["xcrun", "stapler", "staple", str(image)])
    return {"id": answer.get("id"), "status": "Accepted", "gatekeeper": check_notarized_image(image, runner)}


def check_notarized_image(image, runner):
    """Gatekeeper's verdict on a disk image whose stapled ticket validates and which it accepts as notarized
    Developer ID, as lines; ReleaseError otherwise. ``notarize`` ends with it, and ``publish_installers.py``
    refuses to publish any other image."""
    runner(["xcrun", "stapler", "validate", str(image)])
    assessed = runner(["spctl", "--assess", "--type", "open", "--context", "context:primary-signature", "-vv",
                       str(image)])
    verdict = (assessed.stderr or assessed.stdout).strip()
    if "Notarized Developer ID" not in verdict:
        raise ReleaseError("Gatekeeper does not see a notarized image: %s" % verdict)
    return verdict.splitlines()


# -- command line ----------------------------------------------------------------------------------------------

def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def source_commit():
    result = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                            env=dict(os.environ, GIT_LFS_SKIP_SMUDGE="1"))
    return result.stdout.strip() or None


def release(arguments, runner, platform=sys.platform):
    app, out = Path(arguments.app).resolve(), Path(arguments.out).resolve()
    identity = arguments.identity
    notarizing = not arguments.skip_notarize and not arguments.no_dmg
    if not identity:
        raise ReleaseError("give --identity (or ANGELO_SIGN_IDENTITY); '-' is the ad-hoc dry run")
    if identity == "-" and notarizing:
        raise ReleaseError("an ad-hoc signature cannot be notarized; the dry run needs --skip-notarize")
    if notarizing and arguments.allow_no_update_key:
        raise ReleaseError("a notarized image is a release, and a release must trust the update key; "
                           "--allow-no-update-key is for rehearsals that stop before notarization")
    if notarizing and arguments.allow_no_agent_runtime:
        raise ReleaseError("a notarized image is a release, and a release bundles the agent runtime; "
                           "--allow-no-agent-runtime is for rehearsals that stop before notarization")
    if notarizing and not arguments.notary_profile:
        raise ReleaseError("give --notary-profile (or ANGELO_NOTARY_PROFILE): the name used with "
                           "'xcrun notarytool store-credentials'")
    if platform != "darwin":
        raise ReleaseError("macOS signing runs on macOS")
    if not (app / "Contents/Info.plist").is_file():
        raise ReleaseError("%s is not an app bundle" % app)
    receipt = {"kind": "angelo-macos-release", "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "source_commit": source_commit(), "built_app": str(app), "identity": identity,
               "dry_run": identity == "-", "notarized": False}
    _main, info = bundle_executable(app)
    problems = identity_problems(app, info)
    problems += release_checks.update_key_problems(app, allow_none=arguments.allow_no_update_key)
    runtime = release_checks.agent_runtime_folder(app, "macos")
    if runtime.exists() or not arguments.allow_no_agent_runtime:
        problems += release_checks.agent_runtime_problems(runtime, "macos", exclusive=True)
    if not arguments.allow_no_agent_runtime:
        problems += release_checks.release_marker_problems(app, "macos")
    problems += ["%s is a Git LFS pointer, not its content" % path for path in release_checks.lfs_pointer_files(app)]
    if problems:
        raise ReleaseError("refusing to sign:\n  " + "\n  ".join(problems))
    check_identity(identity, arguments.keychain, runner)
    staged, removed = stage(app, out, runner)
    receipt.update(staged_app=str(staged), removed_before_signing=removed)
    code, links = inventory(staged)
    problems = bundle_problems(staged, code, links)
    if problems:
        raise ReleaseError("the staged bundle cannot be signed:\n  " + "\n  ".join(problems))
    plan = signing_plan(staged, code)
    print("Signing %d files inside out, then %s" % (len(plan) - 1, staged.name))
    for path, kind in plan:
        runner(codesign_command(path, kind, identity, arguments.keychain))
    receipt["signing"] = verify_signatures(staged, plan, identity, runner)
    if arguments.launch_check:
        receipt["launch_check"] = release_launch_checks.launch_check(staged, runner)
        receipt["seal_check"] = release_launch_checks.seal_check(staged, runner, gui=not arguments.unstyled_dmg)
    if notarizing:
        with tempfile.TemporaryDirectory(prefix="angelo-notarize-") as folder:
            receipt["app_notarization"] = notarize_app(staged, arguments.notary_profile, arguments.keychain,
                                                       Path(folder), runner)
    if not arguments.no_dmg:
        # Under /tmp, never the per-user temporary folder: the window background's alias in .DS_Store records the
        # path of the writable image it was styled on (doc/angelo/releasing.rst, "No builder paths").
        with tempfile.TemporaryDirectory(prefix="angelo-dmg-", dir=dmg_macos.WORK_ROOT) as folder:
            image, layout = dmg_macos.build(staged, out, Path(folder), IDENTITY, not arguments.unstyled_dmg, runner)
            sign = ["codesign", "--force", "--sign", identity, "--timestamp=none" if identity == "-" else "--timestamp"]
            runner(sign + (["--keychain", arguments.keychain] if arguments.keychain else []) + [str(image)])
            runner(["codesign", "--verify", "--strict", "--verbose=2", str(image)])
            receipt["dmg"] = {"name": image.name, "styled": not arguments.unstyled_dmg, "window": layout}
            if notarizing:
                receipt["notarization"] = notarize(image, arguments.notary_profile, arguments.keychain, Path(folder),
                                                   runner)
                receipt["dmg"]["app_gatekeeper"] = dmg_macos.check_contents(
                    image, staged.name, not arguments.unstyled_dmg, Path(folder), runner, gatekeeper=True)
                receipt["notarized"] = True
            receipt["dmg"].update(bytes=image.stat().st_size, sha256=sha256(image))
            receipt["feed_download"] = "macos-arm64=%s=%s" % (
                release_downloads.url(IDENTITY["version"]["label"], image.name), image)
    receipt["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    receipt["commands"] = len(runner.log)
    (out / RECEIPT).write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def main(argv=None, runner=None, platform=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--app", required=True, help="the built Angelo.app (never changed)")
    parser.add_argument("--out", required=True, help="a new folder for the signed app, the image and the receipt")
    parser.add_argument("--identity", default=os.environ.get("ANGELO_SIGN_IDENTITY", ""),
                        help="Developer ID Application certificate name or SHA-1; '-' signs ad hoc (dry run)")
    parser.add_argument("--notary-profile", default=os.environ.get("ANGELO_NOTARY_PROFILE", ""),
                        help="the profile name given to 'xcrun notarytool store-credentials'")
    parser.add_argument("--keychain", default=os.environ.get("ANGELO_SIGN_KEYCHAIN", ""),
                        help="a keychain other than the default search list")
    parser.add_argument("--skip-notarize", action="store_true", help="stop after signing the image")
    parser.add_argument("--no-dmg", action="store_true", help="sign and verify the app only")
    parser.add_argument("--unstyled-dmg", action="store_true", help="no Finder window styling (no GUI session)")
    parser.add_argument("--launch-check", action="store_true",
                        help="start the signed app before packaging; a launch must leave it unchanged (seal check)")
    parser.add_argument("--allow-no-update-key", action="store_true",
                        help="sign a build whose update_keys.json is empty (rehearsals only)")
    parser.add_argument("--allow-no-agent-runtime", action="store_true",
                        help="sign a build without the agent runtime in Contents/Helpers (rehearsals only)")
    arguments = parser.parse_args(argv)
    try:
        receipt = release(arguments, runner or Runner(), platform or sys.platform)
    except (ReleaseError, OSError, subprocess.TimeoutExpired) as error:
        print("sign_notarize_macos: %s" % error, file=sys.stderr)
        return 1
    print(json.dumps({key: receipt[key] for key in ("dry_run", "notarized", "dmg", "feed_download")
                      if key in receipt}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
