# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The agent runtime's model-catalog listings start, and nothing near them does (``agent_runtime.catalog_listing``).

Found by the Windows Carbon gate on 2.1.0 (96fc02ef7a6): every cold start of a connection on the Gemini wire (Gemini 4
Carbon and Neon) was refused in about half a second ("Angelo starts its agent runtime only in the ways it was built
for"), 13 of the gate's 30 failures. The connection's launch lists the wire's built-in catalog (``endpoint_catalog``)
with ``codex -c <a throwaway provider on the wire> debug models``, which the start check (``allowed_command``) did not
admit; the refusal was then cached as "no catalog", so a second launch within ``RETRY_SECONDS`` ran without one. The
tests here go through ``child_process``, the start every launch uses, never a fake starter. Nothing is billed or sent.

Red on 96fc02ef7a6 (3 errors, 2 failures): the Gemini listing refused by ``check_command``, by a cold start through
``child_process.run`` and from a listing folder; and with a start refused, the first ``bundled`` raised but the second,
within ``RETRY_SECONDS``, raised nothing and returned ``{}`` (a launch without a catalog) while ``_cache`` held the
refusal as a failed listing.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import agent_runtime, child_process, endpoint_catalog, private_roots  # noqa: E402
from angelo_agent.protocol import RpcError  # noqa: E402
from host_test_policy import environment_skip  # noqa: E402

PROGRAM = "/x/codex"
LISTED = {"models": [{"slug": "gemini-4-carbon"}]}


def pinned_wires():
    """Every wire a listing can be built for: Codex's own (``None`` and its name) and each the pin lists."""
    return [None, agent_runtime.DEFAULT_WIRE, *agent_runtime.PIN.get("wire_apis", ())]


class Case(unittest.TestCase):
    """Each host's own rules (no OS is faked: ``os.name`` and ``sys.platform`` are process-wide, and a faked one made
    pathlib build POSIX paths on Windows). ``started`` stubs the start each OS takes (``child_process.run``)."""

    def setUp(self):
        patch = mock.patch.object(agent_runtime, "is_agent_runtime", return_value=True)
        patch.start()
        self.addCleanup(patch.stop)
        endpoint_catalog._cache.clear()
        self.addCleanup(endpoint_catalog._cache.clear)

    def started(self, side_effect):
        """Stub both starts ``child_process.run`` takes, for this test: ``subprocess.run`` (POSIX), ``_run_windows``."""
        for patch in (mock.patch.object(child_process.subprocess, "run", side_effect=side_effect),
                      mock.patch.object(child_process, "_run_windows", side_effect=side_effect)):
            patch.start()
            self.addCleanup(patch.stop)

    def listing_home(self):
        """A listing folder as ``endpoint_catalog`` makes one (``private_roots``, Angelo's scratch root)."""
        home = private_roots.allocate(endpoint_catalog.LISTING_PREFIX)
        self.addCleanup(shutil.rmtree, home, True)
        return str(home)


class Admitted(Case):
    def test_the_pin_lists_a_wire_of_its_own(self):
        self.assertIn("gemini", agent_runtime.PIN.get("wire_apis", ()), "else nothing here tests a wire listing")

    def test_every_listing_endpoint_catalog_builds_passes_the_check(self):
        for wire in pinned_wires():
            with self.subTest(wire=wire):
                agent_runtime.check_command(endpoint_catalog._listing(PROGRAM, wire))
                agent_runtime.check_start(endpoint_catalog._listing(PROGRAM, wire),
                                          {"env": {"CODEX_HOME": self.listing_home()}})

    def test_a_cold_gemini_listing_starts_through_child_process(self):
        """The cold start the gate refused: the first listing of the wire's catalog, through child_process.run."""
        ran = []

        def run(argv, **kwargs):
            ran.append((list(argv), kwargs["env"]["CODEX_HOME"]))
            return SimpleNamespace(returncode=0, stdout=json.dumps(LISTED))
        self.started(run)
        with tempfile.TemporaryDirectory() as profile, \
                mock.patch.object(endpoint_catalog, "_signature", return_value=(PROGRAM, 1, 1)), \
                mock.patch.object(endpoint_catalog.owned_paths, "require_runnable", side_effect=lambda path: path):
            found = endpoint_catalog.bundled(PROGRAM, profile, wire="gemini")
        self.assertEqual(list(found), ["gemini-4-carbon"])
        self.assertEqual(ran[0][0], endpoint_catalog._listing(PROGRAM, "gemini"))
        self.assertTrue(Path(ran[0][1]).name.startswith(endpoint_catalog.LISTING_PREFIX))

    def test_a_refused_start_is_never_cached_as_no_catalog(self):
        """A refusal is said at every launch, never turned into a launch without a catalog (``RETRY_SECONDS``)."""
        self.started(AssertionError("started"))
        with tempfile.TemporaryDirectory() as profile, \
                mock.patch.object(endpoint_catalog, "_signature", return_value=(PROGRAM, 1, 1)), \
                mock.patch.object(endpoint_catalog.owned_paths, "require_runnable", side_effect=lambda path: path), \
                mock.patch.object(agent_runtime, "allowed_command", return_value=False):
            for attempt in range(2):
                with self.subTest(attempt=attempt), self.assertRaises(RpcError):
                    endpoint_catalog.bundled(PROGRAM, profile, wire="gemini")
        self.assertEqual(endpoint_catalog._cache, {})


class Refused(Case):
    def test_nothing_near_a_listing_starts(self):
        """Only the exact command lines ``catalog_listing`` builds for a pinned wire: no other provider, wire, setting
        or argument (``-c`` is never a passthrough)."""
        listing = endpoint_catalog._listing(PROGRAM, "gemini")
        provider = endpoint_catalog.CATALOG_PROVIDER
        refused = (endpoint_catalog._listing(PROGRAM, "unpinned_wire"),
                   [arg.replace(provider, "elsewhere") for arg in listing],
                   listing[:1] + ["-c", 'model_catalog_json="/tmp/x.json"'] + listing[1:],
                   listing[:-2] + ["-c", 'model="x"'] + listing[-2:],
                   listing + ["--bundled"], listing[:-1], [PROGRAM, "debug", "models"],
                   [PROGRAM, "-c", 'model_provider="%s"' % provider, "debug", "models"],
                   listing[:-2] + ["exec", "hi"], listing[:-2] + ["app-server", agent_runtime.IGNORE_USER_CONFIG])
        for argv in refused:
            with self.subTest(argv=argv[1:]), self.assertRaises(RpcError):
                agent_runtime.check_command(argv)

    def test_a_wire_listing_reads_no_persons_configuration(self):
        """``debug models`` has no ``--ignore-user-config``: a wire listing starts only with a listing folder of
        Angelo's own as its one CODEX_HOME, never the person's profile or the process environment's."""
        listing = endpoint_catalog._listing(PROGRAM, "gemini")
        outside = tempfile.mkdtemp(prefix=endpoint_catalog.LISTING_PREFIX)
        self.assertNotEqual(Path(outside).parent.resolve(), private_roots.scratch_root().resolve())
        self.addCleanup(shutil.rmtree, outside, True)
        homes = ({"CODEX_HOME": str(Path.home() / ".codex")}, {}, {"CODEX_HOME": outside})
        for env in homes:
            with self.subTest(env=env), self.assertRaises(RpcError):
                agent_runtime.check_start(listing, {"env": env})
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(Path.home() / ".codex")}), self.assertRaises(RpcError):
            agent_runtime.check_start(listing, {})
        agent_runtime.check_start(listing, {"env": {"CODEX_HOME": self.listing_home()}})


class HomeRules(Case):
    """The break-it review of f3671ebce01: the listing folder is judged as Codex reads it (MAJOR-1: the exact name on
    POSIX; NIT-1: where it really is, never a link's name and parent; NIT-2: an absolute path, never one resolved
    against Angelo's working folder rather than the child's)."""

    listing = staticmethod(lambda: endpoint_catalog._listing(PROGRAM, "gemini"))

    def refused(self, env):
        with self.assertRaises(RpcError):
            agent_runtime.check_start(self.listing(), {"env": env})

    def test_codex_home_is_read_by_its_exact_name_on_posix(self):
        """Codex reads CODEX_HOME and nothing else: ``codex_home`` alone leaves it on ~/.codex (the person's). The
        POSIX rule on every host; the start check itself where this host is POSIX (Windows reads any case)."""
        from angelo_agent import runtime_listing
        self.assertFalse(runtime_listing.listing_home({"env": {"codex_home": self.listing_home()}}, windows=False))
        self.assertTrue(runtime_listing.listing_home({"env": {"CODEX_HOME": self.listing_home(),
                                                              "codex_home": str(Path.home() / ".codex")}},
                                                     windows=False))
        if sys.platform != "win32":
            self.refused({"codex_home": self.listing_home()})
            agent_runtime.check_start(self.listing(), {"env": {"CODEX_HOME": self.listing_home(),
                                                               "codex_home": str(Path.home() / ".codex")}})

    def test_windows_reads_its_environment_in_any_case_and_one_home_only(self):
        from angelo_agent import runtime_listing
        self.assertTrue(runtime_listing.listing_home({"env": {"codex_home": self.listing_home()}}, windows=True))
        self.assertFalse(runtime_listing.listing_home({"env": {"CODEX_HOME": self.listing_home(),
                                                               "Codex_Home": self.listing_home()}}, windows=True))

    def test_a_link_in_the_scratch_root_is_no_listing_folder(self):
        root, elsewhere = private_roots.scratch_root(), tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, elsewhere, True)
        for index, target in enumerate((elsewhere, self.listing_home())):
            link = os.path.join(root, "%slink-%d-%s" % (endpoint_catalog.LISTING_PREFIX, index, os.getpid()))
            try:
                if os.name == "nt":
                    import _winapi   # A junction: what a Windows user without the symlink privilege can make.
                    _winapi.CreateJunction(target, link)
                    self.addCleanup(os.rmdir, link)
                else:
                    os.symlink(target, link, target_is_directory=True)
                    self.addCleanup(os.unlink, link)
            except (OSError, ImportError, AttributeError) as error:
                self.skipTest(environment_skip("this host makes no folder link here: %s" % error))
            with self.subTest(target=target):
                self.refused({"CODEX_HOME": link})

    def test_a_relative_home_is_never_a_listing_folder(self):
        home = self.listing_home()
        try:
            relative = os.path.relpath(home)
        except ValueError:   # Another drive (Windows): no relative spelling to try.
            self.skipTest(environment_skip("the scratch root is on another drive than the working folder"))
        self.assertTrue(os.path.isdir(relative))
        self.refused({"CODEX_HOME": relative})
        self.refused({"CODEX_HOME": os.path.join(os.curdir, relative)})


class WireNames(Case):
    """The break-it review of f3671ebce01, NIT-5: a pinned wire's name goes into a TOML string of a command line, so
    one not spelled as a wire (``runtime_listing.WIRE_NAME``) is never offered, listed or admitted."""

    BAD = ('gemini" -c model="x', "Gemini", "gemini\n", "1wire", "")

    def test_a_wire_not_spelled_as_one_is_never_offered_or_admitted(self):
        with mock.patch.dict(agent_runtime.PIN, {"wire_apis": ["gemini", *self.BAD]}):
            self.assertTrue(agent_runtime.supports_wire("gemini"))
            for wire in self.BAD:
                with self.subTest(wire=wire):
                    self.assertFalse(agent_runtime.supports_wire(wire))
                    name = endpoint_catalog.CATALOG_PROVIDER
                    argv = [PROGRAM, "-c", 'model_provider="%s"' % name, "-c",
                            'model_providers.%s.name="%s"' % (name, name), "-c",
                            'model_providers.%s.wire_api="%s"' % (name, wire), "debug", "models"]
                    if wire:
                        with self.assertRaises(RpcError):
                            agent_runtime.check_command(argv)
                        with self.assertRaises(ValueError):
                            endpoint_catalog._listing(PROGRAM, wire)

    def test_the_pin_and_the_release_check_spell_wires_alike(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/angelo"))
        self.addCleanup(sys.path.remove, sys.path[0])
        import runtime_wires
        from angelo_agent import runtime_listing
        self.assertEqual(runtime_wires.WIRE_NAME.pattern, runtime_listing.WIRE_NAME.pattern)
        for wire in agent_runtime.PIN.get("wire_apis", ()):
            with self.subTest(wire=wire):
                self.assertTrue(runtime_listing.valid_wire(wire))

    def test_the_release_check_refuses_a_pin_whose_wire_is_not_spelled_as_one(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/angelo"))
        self.addCleanup(sys.path.remove, sys.path[0])
        import runtime_wires
        ran = []
        problems = runtime_wires.wire_problems(Path("codex"), {"wire_apis": ['gemini" -c model="x']},
                                               lambda argv, **kwargs: ran.append(argv), {})
        self.assertEqual(ran, [], "never run")
        self.assertEqual(len(problems), 1)
        self.assertIn("not spelled as one", problems[0])


if __name__ == "__main__":
    unittest.main()
