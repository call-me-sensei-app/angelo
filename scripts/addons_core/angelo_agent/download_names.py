# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which names a download may let the proxy resolve (doc/angelo/downloads.rst, "Through a proxy"). No bpy.

Where only the proxy can resolve outside names (a locked-down office network), ``download_fetch`` sends a name it
could not look up to the proxy as a name. The proxy then finds the address, which Angelo cannot check, so this is
allowed only for a name that cannot be internal:

* never a single label (``intranet``), an address in any spelling (``10.1``, ``2130706433``, ``::1``) or a name whose
  last label is a number;
* never a special-use or private-use suffix: IANA's special-use domains (``localhost``, ``local``, ``test``,
  ``invalid``, ``example``, ``onion``, ``alt``, ``home.arpa`` and all of ``arpa``) and the suffixes networks use for
  their own hosts (``internal``, ``lan``, ``corp``, ``home``, ``intranet``, ``private``, ``localdomain``);
* never this machine's DNS search domains, which name its own network's hosts: ``/etc/resolv.conf`` (``search`` and
  ``domain``), macOS's ``scutil --dns``, Windows' TCP/IP and DNS client policy registry values. Read at most once a
  minute; when they cannot be read, no name goes to the proxy (fail closed).
"""

import ipaddress
import re
import socket
import subprocess
import sys
import time

SPECIAL = ("localhost", "local", "test", "invalid", "example", "onion", "alt", "arpa",   # IANA special-use (and .arpa).
           "internal", "lan", "corp", "home", "intranet", "private", "localdomain")      # Networks' own hosts.
CACHE_SECONDS = 60.0
_CACHE = {}
_SCUTIL = re.compile(r"^\s*(?:search domain\[\d+\]|domain)\s*:\s*(\S+)\s*$", re.MULTILINE)


def _clean(names):
    found = []
    for name in names:
        name = (name or "").strip().rstrip(".").lower()
        if name and name not in found:
            found.append(name)
    return tuple(found)


def from_resolv_conf(text):
    """The ``search`` and ``domain`` names of a resolv.conf, in order."""
    names = []
    for line in text.splitlines():
        words = line.split("#", 1)[0].split()
        if words and words[0] in ("search", "domain"):
            names += words[1:]
    return _clean(names)


def from_scutil(text):
    """The search domains and domains of ``scutil --dns``, in order."""
    return _clean(_SCUTIL.findall(text))


def from_windows(values):
    """The domains of Windows registry values (``SearchList`` is comma-separated)."""
    return _clean(part for value in values if value for part in str(value).split(","))


def _windows_values():
    import winreg
    keys = [r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters",
            r"SOFTWARE\Policies\Microsoft\Windows NT\DNSClient"]
    interfaces = r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, interfaces) as root:
            keys += [interfaces + "\\" + winreg.EnumKey(root, index) for index in range(winreg.QueryInfoKey(root)[0])]
    except OSError:
        pass
    values = []
    for key in keys:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as handle:
                for name in ("SearchList", "Domain", "DhcpDomain", "NV Domain"):
                    try:
                        values.append(winreg.QueryValueEx(handle, name)[0])
                    except OSError:
                        pass
        except FileNotFoundError:
            pass
    return values


def _read():
    if sys.platform == "win32":
        return from_windows(_windows_values())
    found = ()
    try:
        with open("/etc/resolv.conf", encoding="utf-8", errors="replace") as handle:
            found = from_resolv_conf(handle.read())
    except FileNotFoundError:
        pass
    if sys.platform == "darwin":
        text = subprocess.run(["/usr/sbin/scutil", "--dns"], capture_output=True, text=True, timeout=5,
                              check=True).stdout
        found = _clean(found + from_scutil(text))
    host = socket.gethostname().rstrip(".").lower()
    return _clean(found + ((host.split(".", 1)[1],) if "." in host else ()))


def search_domains():
    """This machine's DNS search domains, or None when they cannot be read (then no name goes to the proxy)."""
    now = time.monotonic()
    if _CACHE and now - _CACHE["at"] < CACHE_SECONDS:
        return _CACHE["domains"]
    try:
        domains = _read()
    except (OSError, subprocess.SubprocessError, ValueError):
        domains = None
    _CACHE.update(at=now, domains=domains)
    return domains


def _address(name):
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    try:
        socket.inet_aton(name)   # Every spelling a resolver reads as an IPv4 address (10.1, 2130706433, 0x7f.1).
        return True
    except (OSError, UnicodeError):
        return False


def proxy_may_resolve(host, domains):
    """(allowed, why): whether ``host``, which this computer could not look up, may go to the proxy as a name."""
    if domains is None:
        return False, "search_domains_unknown"
    name = host.strip().rstrip(".").lower()
    if _address(name):
        return False, "address"
    if "." not in name:
        return False, "single_label"
    if name.rsplit(".", 1)[1].isdigit():
        return False, "numeric_label"
    if any(name == suffix or name.endswith("." + suffix) for suffix in SPECIAL):
        return False, "special_suffix"
    if any(name == domain or name.endswith("." + domain) for domain in domains):
        return False, "search_domain"
    return True, ""
