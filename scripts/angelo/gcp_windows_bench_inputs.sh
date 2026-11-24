# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
#
# A bench request's named inputs (sourced by gcp_windows_validate.sh's bench stage; needs BUCKET and EXTRA_META): each
# is validated and written to the machine's metadata on every send, empty when unset, so an earlier send's never
# carries over. A refused input exits the launcher with 2, as the stage itself does.

# A session command in place of final_runs.py (windows_final_runs.ps1 -Session): a gs:// folder in this VM's bucket
# holding session.ps1.
local session_input="${ANGELO_BENCH_SESSION_INPUT:-}"
[[ -z "$session_input" || "$session_input" == "gs://$BUCKET/"* ]] || { echo "ANGELO_BENCH_SESSION_INPUT must be a folder in gs://$BUCKET/" >&2; exit 2; }
[[ "$session_input" != *,* && "$session_input" != *=* ]] || { echo "ANGELO_BENCH_SESSION_INPUT holds no , or =" >&2; exit 2; }
EXTRA_META+=",angelo-bench-session-input=$session_input"
# An API-key gate's Secret Manager secret NAME (bench_api_key.py), in the bench prefix, never echoed; sent always.
local gemini_secret="${ANGELO_BENCH_GEMINI_SECRET_NAME:-}"; [[ -z "$gemini_secret" || "$gemini_secret" =~ ^angelo-bench-[A-Za-z0-9_-]{1,242}$ ]] || { echo "ANGELO_BENCH_GEMINI_SECRET_NAME is not an angelo-bench-... secret name (not shown: it may be a key)" >&2; exit 2; }; EXTRA_META+=",angelo-bench-gemini-secret=$gemini_secret"
