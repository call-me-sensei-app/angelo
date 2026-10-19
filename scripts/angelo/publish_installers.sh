#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Runs publish_installers.py with its R2 credentials read from the macOS Keychain.
#
#   ANGELO_KEYCHAIN_SERVICE=<service> scripts/angelo/publish_installers.sh --check
#   ANGELO_KEYCHAIN_SERVICE=<service> scripts/angelo/publish_installers.sh --version 1.0.0 --dmg ... --msi ...
#
# Each of R2_ACCOUNT_ID, R2_DOWNLOADS_ACCESS_KEY_ID and R2_DOWNLOADS_SECRET_ACCESS_KEY that is not already in the
# environment is read from the generic password whose service is $ANGELO_KEYCHAIN_SERVICE and whose account is the
# variable's name (security add-generic-password -s <service> -a <NAME> -w). The values go into this process's
# environment only: never onto a command line, into a file or onto the screen. --dry-run and --verify-only need no
# credentials, so they never open the Keychain. See doc/angelo/releasing.rst, "Publishing the installers".
set -euo pipefail
set +x   # Never trace: the trace would print the values.

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
needs_credentials=1
for argument in "$@"; do
    case "$argument" in
        --dry-run|--verify-only|-h|--help) needs_credentials=0 ;;
    esac
done

if [[ "$needs_credentials" == 1 ]]; then
    for name in R2_ACCOUNT_ID R2_DOWNLOADS_ACCESS_KEY_ID R2_DOWNLOADS_SECRET_ACCESS_KEY; do
        [[ -n "${!name:-}" ]] && continue
        if [[ -z "${ANGELO_KEYCHAIN_SERVICE:-}" ]]; then
            echo "publish_installers.sh: $name is not set; set ANGELO_KEYCHAIN_SERVICE to the Keychain service that" \
                 "holds it" >&2
            exit 1
        fi
        if ! value="$(security find-generic-password -s "$ANGELO_KEYCHAIN_SERVICE" -a "$name" -w 2>/dev/null)"; then
            echo "publish_installers.sh: no Keychain item for $name in service $ANGELO_KEYCHAIN_SERVICE" >&2
            exit 1
        fi
        export "$name=$value"
        unset value
    done
fi

exec python3 "$here/publish_installers.py" "$@"
