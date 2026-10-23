#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Release check: every trust store the app ships is current and holds no distrusted key.

Angelo's TLS trusts roots it ships, not only the operating system's: the bundled certifi file (Python's
``SSL_CERT_FILE``), pip's vendored copy of it, and the webpki-roots set compiled into the bundled Codex. A
stale copy keeps a root the root programs have withdrawn (certifi 2025.04.26 shipped in 1.0.4 with emSign ECC
Root CA - G3, whose intermediates' keys were compromised in October 2026). So a release refuses::

    python3 scripts/angelo/release_checks.py trust-store <app or install folder> [--offline]

* any key ``scripts/addons_core/angelo_agent/distrusted_keys.json`` lists, found in a PEM certificate of any
  shipped file, or as raw key bytes beside its certificate's subject name in any shipped file (a compiled-in trust
  anchor such as webpki-roots'; the key alone is a deny list, such as rustls-webpki's, and passes);
* a certifi copy (``certifi/__init__.py``, vendored ones included) older than the floor
  ``build_files/cmake/angelo_python_packages.json`` sets, or a pinned package installed at another version;
* an agent runtime pin (``agent_runtime.json``) that does not declare its webpki-roots version, or declares
  one older than the floor;
* an expired known exception (``trust_store_exceptions.json``: one exact file, one key, one runtime version,
  decided with a reason and an end); a live one is printed, never silently passed;
* online (the default): a pinned package (certifi, pip) or the declared webpki-roots older than the newest
  stable release on PyPI or crates.io. A failed lookup is a problem, never a pass; ``--offline`` skips only
  this part and says so.

Standard library only; no bpy. ``fetch-wheels --dest <folder>`` downloads the pinned wheels (hash-checked), so
the host tests can check the exact bytes a build installs.
"""

import argparse
import base64
import hashlib
import json
import mmap
import os
import re
import ssl
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import tls_distrust  # noqa: E402  (standard library only)

PINS = ROOT / "build_files/cmake/angelo_python_packages.json"
AGENT_RUNTIME_PIN = ROOT / "scripts/addons_core/angelo_agent/agent_runtime.json"
EXCEPTIONS = Path(__file__).resolve().with_name("trust_store_exceptions.json")
PEM_SUFFIXES = (".pem", ".crt", ".cer")
PEM_CERTIFICATE = re.compile(rb"-----BEGIN CERTIFICATE-----\s+([A-Za-z0-9+/=\s]+?)-----END CERTIFICATE-----")
CERTIFI_VERSION = re.compile(r"""^__version__\s*=\s*["']([0-9.]+)["']""", re.M)


def version_key(text):
    return tuple(int(part) for part in re.findall(r"\d+", text))


def pins(path=PINS):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def distrusted_keys(path=tls_distrust.LIST):
    """``[(name, spki sha256, raw key bytes, subject Name contents)]`` of every distrusted key."""
    keys = []
    for entry in tls_distrust.entries(path):
        spki = base64.b64decode(entry["spki"])
        if hashlib.sha256(spki).hexdigest() != entry["spki_sha256"]:
            raise ValueError("distrusted_keys.json: %s's spki does not match its digest" % entry["name"])
        keys.append((entry["name"], entry["spki_sha256"], tls_distrust.key_bits(spki),
                     base64.b64decode(entry["subject"])))
    return keys


def pem_certificates(data):
    return [base64.b64decode(re.sub(rb"\s+", b"", match.group(1))) for match in PEM_CERTIFICATE.finditer(data)]


def _files(root):
    for folder, _, names in os.walk(root):
        for name in names:
            path = os.path.join(folder, name)
            if os.path.isfile(path) and not os.path.islink(path):
                yield path


def exceptions(path=EXCEPTIONS):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)["exceptions"]


def runtime_version(root):
    return shipped_runtime_pin(root).get("version")


def key_problems(root, keys=None, allowances=None):
    return key_scan(root, keys, allowances)[0]


def key_scan(root, keys=None, allowances=None):
    """``(problems, accepted)``: every shipped PEM certificate or compiled-in trust anchor of a distrusted key below
    ``root``, and the findings a known exception (``trust_store_exceptions.json``) accepts: the exact file content,
    the key, while the bundled runtime is the exception's version."""
    keys = distrusted_keys() if keys is None else keys
    if allowances is None:
        version = runtime_version(root)
        allowances = [entry for entry in exceptions() if entry["runtime_version"] == version]
    digests = {digest: name for name, digest, _, _ in keys}
    problems, accepted = [], []
    for path in _files(root):
        relative = os.path.relpath(path, root)
        try:
            size = os.path.getsize(path)
            if not size:
                continue
            with open(path, "rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
                # A trust anchor (webpki-roots), like any copy of the certificate, holds the subject name and the key;
                # a deny list (rustls-webpki's DISTRUSTED_SPKIS, the fix itself) holds the key alone.
                for name, _, bits, subject in keys:
                    if data.find(bits) >= 0 and data.find(subject) >= 0:
                        finding = "%s holds a trust anchor or certificate for %s (distrusted)" % (relative, name)
                        allowance = _allowance(allowances, path, name)
                        if allowance:
                            accepted.append("%s: known exception until %s" % (finding, allowance["until"]))
                        else:
                            problems.append(finding)
                if path.lower().endswith(PEM_SUFFIXES) or b"-----BEGIN CERTIFICATE-----" in data[:1 << 16]:
                    for certificate in pem_certificates(data[:]):
                        try:
                            digest = tls_distrust.spki_sha256(certificate)
                        except (ValueError, IndexError):
                            continue
                        if digest in digests:
                            problems.append("%s holds a certificate for %s (distrusted)" % (relative, digests[digest]))
        except (OSError, ValueError) as error:
            problems.append("%s cannot be read: %s" % (relative, error))
    return sorted(set(problems)), sorted(set(accepted))


def _allowance(allowances, path, key):
    candidates = [entry for entry in allowances if entry["file"] == os.path.basename(path) and entry["key"] == key]
    if not candidates:
        return None
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return next((entry for entry in candidates if entry["sha256"] == digest), None)


def certifi_copies(root):
    """``[(relative folder, version)]`` of every certifi package below ``root`` (vendored copies too)."""
    found = []
    for path in _files(root):
        if os.path.basename(path) == "__init__.py" and os.path.basename(os.path.dirname(path)) == "certifi":
            with open(path, encoding="utf-8", errors="replace") as handle:
                match = CERTIFI_VERSION.search(handle.read())
            found.append((os.path.relpath(os.path.dirname(path), root), match.group(1) if match else "unknown"))
    return sorted(found)


def installed_versions(root, name):
    wanted = re.sub(r"[-_.]+", "_", name).lower()
    versions = []
    for folder, directories, _ in os.walk(root):
        for directory in directories:
            if directory.endswith(".dist-info"):
                project, _, version = directory[:-len(".dist-info")].rpartition("-")
                if re.sub(r"[-_.]+", "_", project).lower() == wanted:
                    versions.append((os.path.relpath(os.path.join(folder, directory), root), version))
    return sorted(versions)


def _urlopen(target, timeout):
    """``urlopen`` that verifies with certifi when Python has no roots of its own (Angelo's bundled Python)."""
    context = ssl.create_default_context()
    if not context.cert_store_stats().get("x509_ca"):
        try:
            import certifi
            context.load_verify_locations(cafile=certifi.where())
        except (ImportError, OSError, ssl.SSLError):
            pass   # The lookup then fails, and a failed lookup is a problem.
    return urllib.request.urlopen(target, timeout=timeout, context=context)


def newest_pypi(name, opener=_urlopen):
    with opener("https://pypi.org/pypi/%s/json" % name, timeout=30) as response:
        return json.load(response)["info"]["version"]


def newest_crate(name, opener=_urlopen):
    request = urllib.request.Request("https://crates.io/api/v1/crates/%s" % name,
                                     headers={"User-Agent": "angelo-release-check (trust_store.py)"})
    with opener(request, timeout=30) as response:
        return json.load(response)["crate"]["max_stable_version"]


def shipped_runtime_pin(root):
    """The agent runtime pin the bundle below ``root`` ships (the source's when the bundle has none)."""
    for path in _files(root):
        if path.replace(os.sep, "/").endswith("addons_core/angelo_agent/agent_runtime.json"):
            break
    else:
        path = AGENT_RUNTIME_PIN
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def version_problems(root, pin=None, runtime_pin=None, newest=None):
    """Floors, pinned versions and (with ``newest``: name -> newest version, or an exception) freshness."""
    pin = pins() if pin is None else pin
    runtime_pin = shipped_runtime_pin(root) if runtime_pin is None else runtime_pin
    floors = pin["floors"]
    problems = ["the exception for %s (Codex %s) has expired: the bundled runtime is %s; remove it from %s"
                % (entry["file"], entry["runtime_version"], runtime_pin.get("version"), EXCEPTIONS.name)
                for entry in exceptions() if entry["runtime_version"] != runtime_pin.get("version")]
    copies = certifi_copies(root)
    if not copies:
        problems.append("no certifi package found below %s" % root)
    for folder, version in copies:
        if version == "unknown" or version_key(version) < version_key(floors["certifi"]):
            problems.append("%s is certifi %s, older than the floor %s" % (folder, version, floors["certifi"]))
    for package in pin["packages"]:
        found = installed_versions(root, package["name"])
        if not found:
            problems.append("%s is not installed" % package["name"])
        for folder, version in found:
            if version != package["version"]:
                problems.append("%s is %s %s, not the pinned %s" % (folder, package["name"], version,
                                                                     package["version"]))
    declared = (runtime_pin.get("trust_roots") or {}).get("webpki-roots")
    if not declared:
        problems.append("agent_runtime.json declares no trust_roots webpki-roots version for the bundled Codex")
    elif version_key(declared) < version_key(floors["webpki-roots"]):
        problems.append("the bundled Codex has webpki-roots %s, older than the floor %s"
                        % (declared, floors["webpki-roots"]))
    for name, latest in sorted((newest or {}).items()):
        if isinstance(latest, Exception):
            problems.append("the newest %s release could not be looked up (%s); rerun online, or --offline "
                            "to check the floors only" % (name, latest))
            continue
        current = declared if name == "webpki-roots" else next(
            (package["version"] for package in pin["packages"] if package["name"] == name), None)
        if current and version_key(current) < version_key(latest):
            problems.append("%s %s is bundled but %s is published: bump the pin" % (name, current, latest))
    return problems


def newest_releases(pin=None):
    pin = pins() if pin is None else pin
    found = {}
    for package in pin["packages"]:
        try:
            found[package["name"]] = newest_pypi(package["name"])
        except (OSError, ValueError, KeyError) as error:
            found[package["name"]] = error
    try:
        found["webpki-roots"] = newest_crate("webpki-roots")
    except (OSError, ValueError, KeyError) as error:
        found["webpki-roots"] = error
    return found


def problems(root, offline=False):
    """``(problems, accepted known exceptions)``."""
    found, accepted = key_scan(root)
    return found + version_problems(root, newest=None if offline else newest_releases()), accepted


def fetch_wheels(dest, pin=None, opener=_urlopen):
    """Download every pinned wheel into ``dest``, each checked against its SHA-256; returns the paths."""
    pin = pins() if pin is None else pin
    os.makedirs(dest, exist_ok=True)
    paths = []
    for package in pin["packages"]:
        path = os.path.join(dest, package["url"].rsplit("/", 1)[-1])
        if not os.path.isfile(path) or hashlib.sha256(Path(path).read_bytes()).hexdigest() != package["sha256"]:
            with opener(package["url"], timeout=60) as response:
                data = response.read()
            if hashlib.sha256(data).hexdigest() != package["sha256"]:
                raise ValueError("%s does not match its pinned SHA-256" % package["url"])
            Path(path).write_bytes(data)
        paths.append(path)
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(description="Release check: current trust stores, no distrusted key.")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="check an installed app or folder")
    check.add_argument("install")
    check.add_argument("--offline", action="store_true", help="skip the newest-release lookups (floors only)")
    fetch = commands.add_parser("fetch-wheels", help="download the pinned wheels, hash-checked")
    fetch.add_argument("--dest", required=True)
    arguments = parser.parse_args(argv)
    if arguments.command == "fetch-wheels":
        for path in fetch_wheels(arguments.dest):
            print(path)
        return 0
    found, accepted = problems(arguments.install, offline=arguments.offline)
    for line in accepted:
        print("trust store: %s" % line)
    for problem in found:
        print("trust store: %s" % problem, file=sys.stderr)
    if not found:
        print("trust store: %s ships no distrusted key; certifi %s%s" % (
            arguments.install, ", ".join(version for _, version in certifi_copies(arguments.install)),
            " (floors only, offline)" if arguments.offline else ", every pin the newest release"))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
