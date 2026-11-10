# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The window's records for processes that run without it, with a reader or another program holding them: the
launcher's ``mcp/`` records (``external_clients``, ``external_records``, ``launcher_records``) and the
organization's offline policy (``team_org_policy``). Split from ``test_native_shared_replace_callers`` (its
``Case``: ``ntfs_sharing`` applies Windows' sharing rules); each fails closed and never comes back looser.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import os
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_shared_replace_callers import Case  # noqa: E402
from angelo_agent import (external_clients, external_records, launcher_records, shared_replace,  # noqa: E402
                          team_org_policy)


class ExternalClientsTests(Case):
    def test_an_app_connects_while_the_launcher_reads_the_registry(self):
        registry = external_clients.AppRegistry(self.folder)
        registry.connect("codex")
        results = self.during_read(registry.path, lambda: registry.connect("cursor"))
        external_clients.AppRegistry(self.folder).load()   # The MCP launcher's read (read_regular).
        self.assert_landed(results)
        self.assertEqual(sorted(registry.load()), ["codex", "cursor"])

    def test_a_record_for_the_launcher_lands_while_it_reads_one(self):
        external_clients.write_access(self.folder, True)
        path = self.folder / "mcp" / external_clients.ACCESS_FILE
        results = self.during_read(path, lambda: external_clients.write_access(self.folder, False))
        external_clients.read_access(self.folder)   # The launcher's read: headless follows the switch.
        self.assert_landed(results)
        self.assertIs(external_clients.read_access(self.folder), False, "turned off, never left on")

    def test_a_switch_turned_off_but_refused_reads_as_off_until_it_lands(self):
        """The launcher never reads the record's older value while the window still owes it (fails closed)."""
        external_clients.write_access(self.folder, True)
        external_clients.write_organization(self.folder, None)
        path = self.folder / "mcp" / external_clients.ACCESS_FILE
        held = self.sharing.hold(path)
        with self.assertRaises(PermissionError):
            external_clients.write_access(self.folder, False)
        self.assertIsNone(external_clients.read_access(self.folder), "off (never recorded), never the old on")
        organization = self.folder / "mcp" / external_clients.ORGANIZATION_FILE
        self.sharing.hold(organization)
        with self.assertRaises(PermissionError):
            external_clients.write_organization(self.folder, {"allowLocalCliAccess": False})
        self.assertEqual(external_clients.read_organization(self.folder), external_clients.org_policy(
            external_clients.STRICTEST), "the strictest policy, never the old 'none'")
        self.assertTrue(external_clients.organization_recorded(self.folder), "never 'open Angelo once'")
        self.sharing.let_go_all()
        external_clients.write_access(self.folder, False)
        self.assertIs(external_clients.read_access(self.folder), False)
        self.assertEqual([p.name for p in path.parent.iterdir() if p.name.endswith(external_clients.OWED)],
                         [m.name for m in launcher_records.markers(path.parent, external_clients.ORGANIZATION_FILE)],
                         "a record that lands, unmarked")
        self.assertEqual(launcher_records.markers(path.parent, external_clients.ACCESS_FILE), [])

    def test_a_record_another_window_left_owed_is_written_again_never_looser(self):
        now = [100.0]
        external_clients.write_access(self.folder, True)
        path = self.folder / "mcp" / external_clients.ACCESS_FILE
        seen = []
        real = shared_replace.replace

        def replacing(source, target, *args, **kwargs):   # While the record is written, the launcher fails closed.
            seen.append(external_clients.read_access(self.folder))
            return real(source, target, *args, **kwargs)
        held = self.sharing.hold(path)
        with patch.object(shared_replace, "replace", replacing), self.assertRaises(PermissionError):
            external_clients.write_access(self.folder, False)   # Window B turns it off: refused.
        self.sharing.let_go(held)
        self.assertEqual(seen, [None])
        (marker,) = launcher_records.markers(path.parent, external_clients.ACCESS_FILE)
        window = external_records.WindowRecords()   # Window A recorded "on" earlier and nothing changed since.
        window.env = SimpleNamespace(state_directory=lambda: self.folder, access_requested=lambda: True)
        window._recorded_access = True
        clock = patch.object(external_records, "time", SimpleNamespace(monotonic=lambda: now[0], time=time.time))
        with clock:
            window.record_access()   # B still trying (its marker was just touched): never written over.
        self.assertTrue(marker.exists())
        self.assertIsNone(external_clients.read_access(self.folder), "fails closed while B owes it")
        old = time.time() - launcher_records.STALE
        os.utime(marker, (old, old))   # B stopped trying (it quit, or stalls).
        now[0] += shared_replace.RETRY_SECONDS
        with clock:
            window.record_access()
        self.assertIs(external_clients.read_access(self.folder), False, "the stricter of A's on and B's off")
        self.assertEqual(launcher_records.markers(path.parent, external_clients.ACCESS_FILE), [])
        self.assertIs(window._recorded_access, True, "A keeps its own value: written again only when it changes")

    def test_a_takeover_never_loosens_the_record_against_the_window_still_open(self):
        mcp = self.folder / "mcp"
        external_clients.write_access(self.folder, False)
        held = self.sharing.hold(mcp / external_clients.ACCESS_FILE)
        with self.assertRaises(PermissionError):   # A dead window had turned it on (a newer change), refused.
            external_clients.write_access(self.folder, True, intent=time.time())
        self.sharing.let_go(held)
        claimed = launcher_records.claim_stale(mcp, external_clients.ACCESS_FILE, now=time.time() + 60)
        external_clients.write_access(self.folder, False, claimed=claimed)   # The only window left shows off.
        self.assertIs(external_clients.read_access(self.folder), False)
        external_clients.write_organization(self.folder, {"allowLocalCliAccess": False})
        held = self.sharing.hold(mcp / external_clients.ORGANIZATION_FILE)
        with self.assertRaises(PermissionError):   # A dead window signed out (no organization), refused.
            external_clients.write_organization(self.folder, None, intent=time.time())
        self.sharing.let_go(held)
        claimed = launcher_records.claim_stale(mcp, external_clients.ORGANIZATION_FILE, now=time.time() + 60)
        external_clients.write_organization(self.folder, {"allowLocalCliAccess": False}, claimed=claimed)
        self.assertFalse(external_clients.read_organization(self.folder)["allowLocalCliAccess"], "its policy stays")

    def test_a_takeover_writes_the_newest_change_never_an_older_looser_one(self):
        lr, ec = launcher_records, external_clients

        def access(allowed, intent):
            return {"version": 1, "allow_local_cli": allowed, "intent": intent}
        strictest = access(False, 0.0)
        newest = lambda own, others: lr.newest(own, others, lr.stricter_access, strictest)["allow_local_cli"]
        self.assertFalse(newest(access(True, 0.0), [access(True, 5.0), access(False, 10.0)]),
                         "a live window's newer off, already on disk, outweighs an older on")
        self.assertTrue(newest(access(True, 20.0), [access(False, 5.0)]), "the person's latest change wins")
        self.assertFalse(newest(access(True, 5.0), [access(False, 5.0)]), "a tie: the stricter")
        self.assertFalse(newest(access(True, 20.0), [None]), "one that can't be read: the strictest")
        merge = lambda first, second: lr.stricter_policy(first, second, ec.org_policy, ec.STRICTEST)["organization"]
        loose = {"allowPersonalConnections": True, "allowLocalCliAccess": True, "requireAskForEdits": False}
        tight = {"allowPersonalConnections": False, "allowLocalCliAccess": True, "requireAskForEdits": True}
        self.assertIsNone(merge({"organization": None}, {"organization": None}), "none only when both say so")
        self.assertEqual(merge({"organization": None}, {"organization": tight}), ec.org_policy(tight))
        both = merge({"organization": loose}, {"organization": tight})
        self.assertFalse(both["allowPersonalConnections"])
        self.assertTrue(both["requireAskForEdits"] and both["allowLocalCliAccess"])
        self.assertFalse(merge({"organization": loose}, {})["allowLocalCliAccess"], "unreadable: the strictest")

    def test_a_record_held_for_good_is_tried_again_after_a_pause_never_at_every_tick(self):
        """The pump records at every tick (5 to 100 ms): a refused record costs its retries once per pause."""
        switch, now = [True], [100.0]
        host = external_records.WindowRecords()
        host.env = SimpleNamespace(state_directory=lambda: self.folder, access_requested=lambda: switch[0])
        host.record_access()
        path = self.folder / "mcp" / external_clients.ACCESS_FILE
        held = self.sharing.hold(path)
        switch[0] = False
        clock = patch.object(external_records, "time", SimpleNamespace(monotonic=lambda: now[0], time=time.time))
        with clock, patch("builtins.print") as said:
            for _tick in range(20):
                host.record_access()
                now[0] += 0.1
            self.assertEqual(said.call_count, 1, "said once")
            self.assertEqual(self.sharing.pauses, [shared_replace.PAUSE * attempt for attempt in range(
                1, shared_replace.ATTEMPTS)], "one replace's retries in 2 s of ticks, never more")
            self.sharing.let_go(held)
            now[0] += shared_replace.RETRY_SECONDS
            host.record_access()
        self.assertIs(external_clients.read_access(self.folder), False, "written once free")
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()), [external_clients.ACCESS_FILE], "no part left")


class OrganizationPolicyTests(Case):
    LOOSE, TIGHT = {"allowPersonalConnections": True}, {"allowPersonalConnections": False}

    def account(self, policy, signed_in=True):
        view = SimpleNamespace(signed_in=signed_in, status="signed_in" if signed_in else "offline", org_policy=policy)
        return SimpleNamespace(console_url="https://console.example", view=lambda: view)

    def test_a_tightened_policy_refused_for_good_is_never_looser_at_the_next_offline_launch(self):
        account = self.account(self.LOOSE)
        org = team_org_policy.OrganizationPolicy(account, self.folder)
        org.remember(account.view())
        held = self.sharing.hold(org.path)
        tight = self.account(self.TIGHT)
        org.remember(tight.view())   # Refused past its retries: owed, and kept in PENDING meanwhile.
        offline = team_org_policy.OrganizationPolicy(self.account(None, signed_in=False), self.folder)
        self.assertFalse(offline.current()["allowPersonalConnections"], "the tightened policy, never the old file's")
        self.sharing.let_go(held)
        org.save_owed()   # The account closing: the last try lands, and PENDING goes.
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), [team_org_policy.FILE])
        again = team_org_policy.OrganizationPolicy(self.account(None, signed_in=False), self.folder)
        self.assertFalse(again.current()["allowPersonalConnections"])

    def test_a_refused_save_is_tried_again_from_the_pump_after_its_pause(self):
        now = [100.0]
        clock = patch.object(team_org_policy, "time", SimpleNamespace(monotonic=lambda: now[0], time=time.time))
        account = self.account(self.TIGHT)
        Path(self.folder, team_org_policy.FILE).write_text("{}")
        with clock:
            org = team_org_policy.OrganizationPolicy(account, self.folder)
            held = self.sharing.hold(org.path)
            org.remember(account.view())   # Refused past its retries: owed.
            self.sharing.let_go(held)
            org.retry_due()
            self.assertEqual(Path(org.path).read_text(), "{}", "not before its pause: nothing tried")
            now[0] += shared_replace.RETRY_SECONDS
            org.retry_due()
        self.assertIsNone(org.unsaved)
        self.assertFalse(json.loads(Path(org.path).read_text())["policy"]["allowPersonalConnections"])
        pump = Path(team_org_policy.__file__).with_name("team_account_ui.py").read_text(encoding="utf-8")
        early = pump.index("if (view.version, key) == _applied:")
        self.assertLess(early, pump.index("_org_policy.retry_due()"), "the pump retries it even when nothing changed")
        self.assertLess(pump.index("_org_policy.retry_due()"), pump.index("_applied = (view.version, key)"))

    def test_a_save_is_stamped_later_than_another_angelos_even_after_the_clock_steps_back(self):
        tight, loose = self.account(self.TIGHT), self.account(self.LOOSE)
        first = team_org_policy.OrganizationPolicy(loose, self.folder)   # This Angelo, started earlier.
        other = team_org_policy.OrganizationPolicy(tight, self.folder)
        other.remember(tight.view())   # Another Angelo saves the tightened policy.
        held = self.sharing.hold(first.path)
        stepped_back = SimpleNamespace(monotonic=time.monotonic, time=lambda: other.stamped - 3600.0)
        with patch.object(team_org_policy, "time", stepped_back):
            first.remember(loose.view())   # The admin loosened it since; refused: PENDING.
        self.sharing.let_go(held)
        self.assertGreater(first.stamped, other.stamped)
        offline = team_org_policy.OrganizationPolicy(self.account(None, signed_in=False), self.folder)
        self.assertTrue(offline.current()["allowPersonalConnections"], "the newest save applies, whatever the clock")

    def test_a_pending_policy_older_than_the_file_never_wins(self):
        loose, tight = self.account(self.LOOSE), self.account(self.TIGHT)
        org = team_org_policy.OrganizationPolicy(loose, self.folder)
        org.remember(loose.view())
        held = self.sharing.hold(org.path)
        org.remember(tight.view())   # Refused: PENDING says tight.
        self.sharing.let_go(held)
        pending = self.sharing.hold(org.pending)   # PENDING can't go (a scan holds it); the file is written.
        org.remember(loose.view())
        self.sharing.let_go(pending)
        offline = team_org_policy.OrganizationPolicy(self.account(None, signed_in=False), self.folder)
        self.assertTrue(offline.current()["allowPersonalConnections"], "the newer file's policy, not PENDING's")


if __name__ == "__main__":
    unittest.main()
