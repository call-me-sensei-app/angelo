# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which names a download may let the proxy resolve (``download_names``; doc/angelo/downloads.rst, "Through a proxy").

When this computer cannot look a name up and a proxy is set, the proxy may resolve it, but only a name that cannot be
internal: never a single label, an address in any spelling, a special-use or private-use suffix, or one of this
machine's DNS search domains (read from resolv.conf, scutil or the Windows registry). No network, Blender or bpy.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import download_names as names  # noqa: E402

SEARCH = ("corp.example.com", "lab.example.org")


class NameTests(unittest.TestCase):
    def test_public_names_may_be_resolved_by_the_proxy(self):
        for host in ("cdn.example.net", "textures.example.org", "a.b.c.example.co.uk", "files.example.com.",
                     "notcorp.example.com", "xn--bcher-kva.example.net"):
            with self.subTest(host=host):
                self.assertEqual(names.proxy_may_resolve(host, SEARCH), (True, ""))

    def test_a_name_that_may_be_internal_never_goes_to_the_proxy(self):
        for host, why in (("intranet", "single_label"), ("nas", "single_label"),
                          ("nas.local", "special_suffix"), ("NAS.LOCAL.", "special_suffix"),
                          ("vault.internal", "special_suffix"), ("printer.lan", "special_suffix"),
                          ("router.home.arpa", "special_suffix"), ("files.corp", "special_suffix"),
                          ("box.home", "special_suffix"), ("wiki.intranet", "special_suffix"),
                          ("db.private", "special_suffix"), ("x.localdomain", "special_suffix"),
                          ("app.localhost", "special_suffix"), ("x.test", "special_suffix"),
                          ("x.invalid", "special_suffix"), ("x.onion", "special_suffix"), ("x.alt", "special_suffix"),
                          ("5.0.0.10.in-addr.arpa", "special_suffix"), ("example", "single_label"),
                          ("build.corp.example.com", "search_domain"), ("corp.example.com", "search_domain"),
                          ("x.lab.example.org", "search_domain"), ("10.1", "address"), ("2130706433", "address"),
                          ("0x7f.1", "address"), ("192.168.1.1", "address"), ("::1", "address"),
                          ("fe80::1", "address"), ("host.123", "numeric_label")):
            with self.subTest(host=host):
                self.assertEqual(names.proxy_may_resolve(host, SEARCH), (False, why))


class SearchDomainTests(unittest.TestCase):
    def test_resolv_conf(self):
        text = "# search ignored.example\nnameserver 1.1.1.1\nsearch corp.example.com  lab.example.org\n" \
               "domain Office.Example.NET.\noptions ndots:2\n"
        self.assertEqual(names.from_resolv_conf(text), ("corp.example.com", "lab.example.org", "office.example.net"))

    def test_scutil(self):
        text = ("DNS configuration\n\nresolver #1\n  search domain[0] : corp.example.com\n"
                "  search domain[1] : lab.example.org\n  nameserver[0] : 10.0.0.1\nresolver #2\n"
                "  domain   : office.example.net\n  nameserver[0] : 10.0.0.2\n")
        self.assertEqual(names.from_scutil(text), ("corp.example.com", "lab.example.org", "office.example.net"))

    def test_windows_values(self):
        values = ["corp.example.com,lab.example.org", "", None, "Office.Example.NET", " spaced.example "]
        self.assertEqual(names.from_windows(values),
                         ("corp.example.com", "lab.example.org", "office.example.net", "spaced.example"))

    def test_the_machine_domains_are_read_once_a_minute(self):
        calls = []

        def read():
            calls.append(1)
            return ("corp.example.com",)
        names._CACHE.clear()
        clock = mock.patch.object(names.time, "monotonic", side_effect=[0, 30, 61])
        with mock.patch.object(names, "_read", read), clock:
            self.assertEqual(names.search_domains(), ("corp.example.com",))
            names.search_domains()
            names.search_domains()
        self.assertEqual(len(calls), 2)
        names._CACHE.clear()

    def test_this_machines_domains_are_read_for_real(self):
        # The platform's own source (resolv.conf, scutil, the Windows registry) on the machine the test runs on: it
        # answers names, never an error that would close the proxy route for everyone.
        found = names._read()
        self.assertIsInstance(found, tuple)
        self.assertTrue(all(isinstance(d, str) and d == d.lower() and d.strip(".") == d for d in found), found)

    def test_domains_that_cannot_be_read_allow_no_proxy_lookup(self):
        # Fail closed: without this machine's search domains, an internal name could reach the proxy.
        names._CACHE.clear()
        with mock.patch.object(names, "_read", side_effect=OSError("no access")):
            self.assertIsNone(names.search_domains())
        names._CACHE.clear()
        self.assertEqual(names.proxy_may_resolve("cdn.example.net", None), (False, "search_domains_unknown"))


if __name__ == "__main__":
    unittest.main()
