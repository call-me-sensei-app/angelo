#!/bin/bash
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
#
# sign_remote.sh check|probe|files|msi --certificate FILE --job NAME --out DIR [--files DIR | --msi FILE] [--commit REV]
#
# One signing job on the Windows signing VM (doc/angelo/releasing.rst, "Or SSL.com eSigner"), run by the release owner
# with a gcloud that may start that VM and set its metadata (the signer account itself cannot):
#   files  DIR holds the key files at their paths under the app's folder (Angelo.exe, Angelo-launcher.exe,
#          agent-runtime/codex.exe); the VM signs each one and returns them (DIR/files/...) for the build's Presigned pass.
#   msi    FILE is the MSI that pass packed; the VM signs it as a file (nothing in it runs) and returns it (DIR/<name>).
#   probe  a new VM's or certificate's first test: one signing of sign_windows.ps1's own probe script, checked.
#   check  no signing: the secrets are read and eSigner lists the credential (free; proves a code or VM change).
# What the VM runs and signs is attested in its metadata, never by the bucket alone: angelo-sign-code (the SHA-256s of
# the signing code at REV, uploaded to gs://<bucket>/code/<sha256>) and angelo-sign-job-sha256 (job.json's). Inputs go to
# gs://<bucket>/input/<job>/<sha256>. The VM claims the job before spending (signed/<job>/CLAIM, created only if absent),
# signs, powers off; this waits until it is TERMINATED, removes the job from its metadata, fetches signed/<job>/ and
# prints what it cost (the ledger). Refuses a VM that is not TERMINATED and a job name used before.
#
# Settings (names, not secrets): ANGELO_SIGN_PROJECT (angelo-signing), ANGELO_SIGN_VM (win-sign),
# ANGELO_SIGN_ZONE (us-central1-a), ANGELO_SIGN_BUCKET (gs://<project>-win-sign),
# ANGELO_SIGN_ESIGNER_USERNAME, ANGELO_SIGN_ESIGNER_CREDENTIAL_ID, ANGELO_SIGN_ESIGNER_SECRET_PROJECT
# (default: the project), ANGELO_SIGN_WAIT_HOURS (4). ANGELO_SIGN_DRY_RUN=1 prints what it would send and start.
set -euo pipefail

usage() { sed -n '5,14p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }
kind=${1:-}; [[ "$kind" == check || "$kind" == probe || "$kind" == files || "$kind" == msi ]] || usage; shift
files="" msi="" certificate="" job="" out="" rev=HEAD
while (( $# )); do
  case "$1" in
    --files) files=$2; shift 2 ;;
    --msi) msi=$2; shift 2 ;;
    --certificate) certificate=$2; shift 2 ;;
    --job) job=$2; shift 2 ;;
    --out) out=$2; shift 2 ;;
    --commit) rev=$2; shift 2 ;;
    *) usage ;;
  esac
done
[[ -f "$certificate" && -n "$out" ]] || usage
[[ "$kind" != files || -d "$files" ]] || { echo "files needs --files DIR" >&2; exit 2; }
[[ "$kind" != msi || -f "$msi" ]] || { echo "msi needs --msi FILE" >&2; exit 2; }
[[ "$job" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$ ]] || { echo "--job must be a plain name" >&2; exit 2; }
# A fresh output folder: a sync into an old one would keep files of another job.
[[ ! -e "$out" || -z "$(ls -A "$out" 2>/dev/null)" ]] || { echo "--out $out must be new or empty" >&2; exit 2; }

project=${ANGELO_SIGN_PROJECT:-angelo-signing}
vm=${ANGELO_SIGN_VM:-win-sign}
zone=${ANGELO_SIGN_ZONE:-us-central1-a}
bucket=${ANGELO_SIGN_BUCKET:-gs://$project-win-sign}
username=${ANGELO_SIGN_ESIGNER_USERNAME:?set ANGELO_SIGN_ESIGNER_USERNAME (the SSL.com user name)}
credential=${ANGELO_SIGN_ESIGNER_CREDENTIAL_ID:?set ANGELO_SIGN_ESIGNER_CREDENTIAL_ID}
secrets=${ANGELO_SIGN_ESIGNER_SECRET_PROJECT:-$project}
hours=${ANGELO_SIGN_WAIT_HOURS:-4}
dry=${ANGELO_SIGN_DRY_RUN:-0}
root=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
commit=$(git -C "$root" rev-parse --verify "$rev^{commit}")

run() { if [[ "$dry" == 1 ]]; then printf 'DRY RUN:'; printf ' %q' "$@"; echo; else "$@"; fi; }
status() { gcloud compute instances describe "$vm" --project="$project" --zone="$zone" --format='value(status)'; }

if [[ "$dry" != 1 ]]; then
  [[ "$(status)" == TERMINATED ]] || { echo "$vm is $(status), not TERMINATED: one job at a time" >&2; exit 1; }
  if gcloud storage ls "$bucket/input/$job/" >/dev/null 2>&1 || gcloud storage ls "$bucket/signed/$job/" >/dev/null 2>&1; then
    echo "job $job was used before: name a new one" >&2; exit 1
  fi
fi

stage=$(mktemp -d); trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/code" "$stage/input"
# The signing code at REV, by content.
: > "$stage/code.list"
for path in scripts/angelo/sign_windows.ps1 scripts/angelo/windows_pe_digest.ps1 \
            scripts/addons_core/angelo_agent/agent_runtime.json scripts/modules/angelo_identity.json; do
  git -C "$root" show "$commit:$path" > "$stage/code/tmp"
  sha=$(shasum -a 256 "$stage/code/tmp" | cut -c1-64)
  mv "$stage/code/tmp" "$stage/code/$sha"
  printf '%s %s\n' "$path" "$sha" >> "$stage/code.list"
done
cp "$certificate" "$stage/input/tmp"; certsha=$(shasum -a 256 "$stage/input/tmp" | cut -c1-64); mv "$stage/input/tmp" "$stage/input/$certsha"
: > "$stage/files.list"
if [[ "$kind" == files ]]; then
  # Only the key files the commit's identity and runtime pin name (the signing VM refuses anything else too).
  keys=$(git -C "$root" show "$commit:scripts/modules/angelo_identity.json" > "$stage/identity.json" && \
         git -C "$root" show "$commit:scripts/addons_core/angelo_agent/agent_runtime.json" > "$stage/runtime.json" && \
         python3 -I -c 'import json, sys
w = json.load(open(sys.argv[1]))["windows"]; p = json.load(open(sys.argv[2]))["platforms"]["windows"]
own = [n for n in p["required"] + p.get("optional", []) if n not in p.get("upstream_signed", [])]
print("\n".join([w["exe_stem"] + ".exe", w["exe_stem"] + w["launcher_suffix"] + ".exe"] + [p["folder"] + "/" + n for n in own]))' \
         "$stage/identity.json" "$stage/runtime.json")
  rm -f "$stage/identity.json" "$stage/runtime.json"
  while read -r rel; do
    grep -qxF -- "$rel" <<< "$keys" || { echo "'$rel' is not a key file (key files: $(echo $keys))" >&2; exit 2; }
    sha=$(shasum -a 256 "$files/$rel" | cut -c1-64)
    cp "$files/$rel" "$stage/input/$sha"
    printf '%s\t%s\n' "$rel" "$sha" >> "$stage/files.list"
  done < <(cd "$files" && find . -type f | sed 's|^\./||' | LC_ALL=C sort)
  [[ -s "$stage/files.list" ]] || { echo "$files holds no files" >&2; exit 2; }
fi
msisha=""
if [[ "$kind" == msi ]]; then
  [[ "$(basename "$msi")" =~ ^[A-Za-z0-9][A-Za-z0-9._\ -]{0,120}\.msi$ ]] || { echo "the MSI's file name must be plain" >&2; exit 2; }
  msisha=$(shasum -a 256 "$msi" | cut -c1-64); cp "$msi" "$stage/input/$msisha"
fi
python3 -I - "$stage" "$kind" "$certsha" "$(basename "${msi:-none}")" "$msisha" "$username" "$credential" "$secrets" "$commit" <<'EOF'
import hashlib, json, os, sys
stage, kind, certsha, msiname, msisha, username, credential, secrets, commit = sys.argv[1:]
code = dict(line.split() for line in open(os.path.join(stage, "code.list")))
files = dict(line.rstrip("\n").split("\t") for line in open(os.path.join(stage, "files.list")) if line.strip())
job = {"kind": kind, "commit": commit, "certificate": certsha, "files": files,
       "msi": {"name": msiname, "sha256": msisha} if kind == "msi" else None,
       "esigner": {"username": username, "credential_id": credential, "secret_project": secrets}}
data = (json.dumps(job, indent=1, sort_keys=True) + "\n").encode()
open(os.path.join(stage, "input", "job.json"), "wb").write(data)
open(os.path.join(stage, "code.json"), "w").write(json.dumps(code, sort_keys=True))
open(os.path.join(stage, "job.sha256"), "w").write(hashlib.sha256(data).hexdigest())
print(json.dumps({"kind": kind, "commit": commit, "files": sorted(files), "msi": job["msi"] and msiname}))
EOF

run gcloud storage cp --quiet "$stage/code/*" "$bucket/code/"
run gcloud storage cp --quiet "$stage/input/*" "$bucket/input/$job/"
run gcloud compute instances add-metadata "$vm" --project="$project" --zone="$zone" \
  --metadata="angelo-sign-job=$job,angelo-sign-job-sha256=$(cat "$stage/job.sha256")" \
  --metadata-from-file="angelo-sign-code=$stage/code.json"
run gcloud compute instances start "$vm" --project="$project" --zone="$zone"
[[ "$dry" == 1 ]] && exit 0

deadline=$(( $(date +%s) + hours * 3600 ))
until [[ "$(status)" == TERMINATED ]]; do
  if (( $(date +%s) > deadline )); then
    echo "$vm still runs after $hours h: stopping it" >&2
    gcloud compute instances stop "$vm" --project="$project" --zone="$zone" --quiet || true
    break
  fi
  sleep 30
done
# The job never runs again on a later start (its CLAIM also stops the VM from signing it twice).
gcloud compute instances remove-metadata "$vm" --project="$project" --zone="$zone" --keys=angelo-sign-job,angelo-sign-job-sha256
echo "$vm: $(status)"

mkdir -p "$out"
gcloud storage cp --quiet "$bucket/signed/$job/ledger.json" "$out/" 2>/dev/null || true
if gcloud storage ls "$bucket/signed/$job/FAILED" >/dev/null 2>&1; then
  echo "job $job FAILED: $(gcloud storage cat "$bucket/signed/$job/FAILED")" >&2
  [[ -f "$out/ledger.json" ]] && echo "eSigner calls (each may have cost signings): $(cat "$out/ledger.json")" >&2
  exit 1
fi
gcloud storage ls "$bucket/signed/$job/DONE" >/dev/null 2>&1 || { echo "job $job left no DONE" >&2; exit 1; }
gcloud storage rsync --recursive "$bucket/signed/$job" "$out"
python3 -I - "$out/sign-report.json" <<'EOF'
import json, sys
report = json.load(open(sys.argv[1]))
print("signed %d files (eSigner signings %s), vendor-signed kept %d, upstream kept %d"
      % (len(report.get("signed") or []), report.get("esigner_signings"), len(report.get("vendor_signed") or []),
         len(report.get("upstream_kept") or [])))
EOF
