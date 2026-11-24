<!-- SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd. -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Linux release keys (minisign form)

One `<key id>.pub` per key in `scripts/addons_core/angelo_agent/linux_release_keys.json`, written by
`scripts/angelo/sign_linux_release.py add-key` after the Cloud KMS key ceremony
(doc/angelo/updates_and_reports.rst, "Linux release signing"). Check a download with:

    minisign -Vm Angelo-<version>-linux-x64.tar.xz -p <key id>.pub

Empty until the ceremony: no Linux release has been signed yet.
