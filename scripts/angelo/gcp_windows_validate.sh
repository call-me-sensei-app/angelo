#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Run the Windows identity validation on a throwaway Google Compute Engine machine, unattended.
#
#   scripts/angelo/gcp_windows_validate.sh up       create everything and start the run
#   scripts/angelo/gcp_windows_validate.sh redo     send what changed since the last upload (redo --full: all of HEAD) and run the
#                                                   build and validate stage again (only the changed files are rebuilt)
#   scripts/angelo/gcp_windows_validate.sh status   what stage is it in, is it finished
#   scripts/angelo/gcp_windows_validate.sh logs     print the newest logs from the bucket
#   scripts/angelo/gcp_windows_validate.sh fetch    download results (and the MSI) into ./windows-validation
#   scripts/angelo/gcp_windows_validate.sh stop     power the VM off (it keeps its disk; `redo` starts it again)
#   scripts/angelo/gcp_windows_validate.sh down     delete the VM, network, bucket and service account
#
# The full-feature build (blender_release with every GPU device and its kernels, BlendThumb.dll, the MSI, then the
# BACKGROUND native suites on it; windows_release_validate.ps1):
#   scripts/angelo/gcp_windows_validate.sh toolkits FILE...   upload GPU toolkit installers (CUDA Toolkit .exe, OptiX SDK
#                                                             .zip or .exe, AMD HIP SDK .exe) that you downloaded yourself
#   scripts/angelo/gcp_windows_validate.sh agent-runtime DIR  upload the Windows agent runtime (DIR as for ANGELO_AGENT_RUNTIME_DIR:
#                                                             codex.exe, codex-code-mode-host.exe and the sandbox helpers),
#                                                             checked here first; the release stage bundles it
#   scripts/angelo/gcp_windows_validate.sh release-build      send HEAD, run only the 'release' stage, then wait-off
#   scripts/angelo/gcp_windows_validate.sh wait-off           wait for the run's end, then confirm the VM is powered off
#                                                             (powering it off itself if it is not); exit 1 if it can't,
#                                                             or if the run failed (its stage exit code, or FAILED)
#   ANGELO_GCP_RELEASE_ALLOW_MISSING=hip+hiprt   build without the named parts (cuda, optix, hip, hiprt, oneapi, agent-runtime);
#                                                the manifest then says the build is not release-equivalent
#
# Validation work on a held machine (ANGELO_GCP_HOLD_MINUTES > 0 on `redo`: it runs remote commands before powering off):
#   scripts/angelo/gcp_windows_validate.sh send FILE.ps1 [gui]   run a PowerShell script there (gui: in the desktop session)
#   scripts/angelo/gcp_windows_validate.sh sync                  send what changed since the last upload, without a restart
#   scripts/angelo/gcp_windows_validate.sh out [N]               the newest N command outputs (default 3)
#   scripts/angelo/gcp_windows_validate.sh release [gui]         stop waiting for commands; the run continues and ends
#
# The final measurement runs on Windows (doc/angelo/final_runs.rst; the commands are documented in this script); each waits for the stage, then confirms the VM stopped:
#   scripts/angelo/gcp_windows_validate.sh bench prep            release build, install, freeze; wait for the RDP sign-in
#   scripts/angelo/gcp_windows_validate.sh bench dry             every unbilled check of the campaign (no model request)
#   scripts/angelo/gcp_windows_validate.sh bench run --authorize-billed-runs   BILLED: run or resume the campaign
#   scripts/angelo/gcp_windows_validate.sh bench wait            re-attach: wait-off (the run's end, then the VM off)
#   (ANGELO_GCP_DRY_RUN=1: print what `bench` would send and start, and change nothing)
#   scripts/angelo/gcp_windows_validate.sh clone NAME    another bench machine from the image $ANGELO_GCP_NAME-bench-base
#                                                        (its own bucket; no sign-in); ANGELO_GCP_DRY_RUN=1 prints it
#   scripts/angelo/gcp_windows_validate.sh oneshot-sources DIR   upload the one-shot tests' source pictures (checked
#                                                        against the tests' SHA-256 pins) to the private bucket only
#
# Needs a signed-in gcloud (the account must be able to create these resources), ANGELO_GCP_PROJECT (your
# Google Cloud project), ANGELO_GCP_ZONE (its zone) and ANGELO_GCP_NAME (the VM's name; the network, firewall
# rule, service account and default bucket are named after it). Nothing is pushed anywhere: the source travels as an archive of HEAD plus the
# small set of Git LFS files the build needs, through a private bucket only the VM's service
# account can read. The VM sits in its own network with no inbound rule except IAP's RDP range, so
# nothing is reachable from the internet. It powers itself off when a run ends, and at the latest after ANGELO_GCP_MAX_HOURS (default 8) of one run (stopped, never deleted).
# A running VM is billed per hour and its 300 GB disk while stopped; `down` deletes everything.
set -euo pipefail

PROJECT="${ANGELO_GCP_PROJECT:?set ANGELO_GCP_PROJECT}"
ZONE="${ANGELO_GCP_ZONE:?set ANGELO_GCP_ZONE to a Compute Engine zone with T4 GPUs (for example us-central1-a)}"
REGION="${ZONE%-*}"
NAME="${ANGELO_GCP_NAME:?set ANGELO_GCP_NAME to the VM name to create or reuse (for example angelo-validate)}"
# Official stock Blender installer for the side-by-side checks (a ~350 MB download on the VM).
STOCK_URL="${ANGELO_GCP_STOCK_BLENDER_URL:-https://download.blender.org/release/Blender5.2/blender-5.2.2-windows-x64.msi}"
STOP_WHEN_DONE="${ANGELO_GCP_STOP_WHEN_DONE:-1}"   # 1 = the VM powers itself off when a run ends
STAGES="${ANGELO_GCP_STAGES:-identity+teams}"   # which checks a run does, joined by +: identity, teams, thumbs, agent, gui
HOLD="${ANGELO_GCP_HOLD_MINUTES:-0}"            # > 0: run remote commands until this many idle minutes (or `release`)
ALLOW_MISSING="${ANGELO_GCP_RELEASE_ALLOW_MISSING:-}"   # release stage: GPU parts it may build without, joined by +
GPU="${ANGELO_GCP_GPU:-1}"                    # 1 = T4 virtual workstation (GUI checks), 0 = none
MACHINE="${ANGELO_GCP_MACHINE:-n1-standard-16}"
MAX_HOURS="${ANGELO_GCP_MAX_HOURS:-8}"
BUCKET="${ANGELO_GCP_BUCKET:-$PROJECT-$NAME}"
# bench: the hours one stage may run (below the VM's own --max-run-duration), the MCP side's stock Blender (5.2.2).
BENCH_HOURS="${ANGELO_GCP_BENCH_HOURS:-$((MAX_HOURS - 1))}"
BENCH_STOCK_URL="${ANGELO_BENCH_STOCK_URL:-https://download.blender.org/release/Blender5.2/blender-5.2.2-windows-x64.zip}"
EXTRA_META=""
SA="$NAME-build"
SA_EMAIL="$SA@$PROJECT.iam.gserviceaccount.com"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${ANGELO_GCP_OUT:-$PWD/windows-validation}"

g() { gcloud --project="$PROJECT" "$@"; }
# macOS tar adds an AppleDouble "._name" entry for every file with extended attributes; on Windows those became
# hundreds of stray files (._*.png beside each MSIX asset, ._*.dat among the icons) that tests and packaging picked up.
export COPYFILE_DISABLE=1
# git archive runs the Git LFS smudge filter: without this it downloads every large file (metered) instead of
# archiving its pointer. The large files the build needs travel separately (angelo-lfs.tar.gz).
export GIT_LFS_SKIP_SMUDGE=1

# The Git LFS files that travel as real files (angelo-lfs.tar.gz) beside the archive of HEAD, whose LFS files are
# pointers: every folder the build installs or packs from. A shipped file outside these folders would install as its
# pointer (the agent add-on's images did), and the release build refuses one.
LFS_DIRS='assets|release/datafiles|release/windows|release/branding|scripts'

# Refuse to send a large-file pointer in place of a file the build installs: run 'git lfs checkout' (local objects) first.
need_real_files() {
  local pointers
  pointers="$(cd "$ROOT" && git lfs ls-files | grep -E " - ($LFS_DIRS)/" | sed 's/^[0-9a-f]* - /  /' || true)"
  [[ -z "$pointers" ]] || { printf 'Git LFS pointers in place of files the build ships; run git lfs checkout (from the local cache) first:\n%s\n' "$pointers" >&2; exit 1; }
}

# A digest of the large files the build needs (their ids and the sizes in this checkout, so a pointer differs from the file).
lfs_digest() {
  ( cd "$ROOT" && git lfs ls-files -l | grep -E " [*-] ($LFS_DIRS)/" | while read -r oid _ path; do
      printf '%s %s\n' "$oid" "$(wc -c < "$path" 2>/dev/null || echo 0)"; done | shasum -a 256 | cut -c1-16 )
}

up() {
  cd "$ROOT"
  need_real_files
  [[ -z "$(git status --porcelain --untracked-files=no)" ]] || { echo "Commit first: the archive is HEAD." >&2; exit 1; }
  local work; work="$(mktemp -d)"
  echo "== archive HEAD ($(git rev-parse --short HEAD)) and the LFS subset"
  git archive --format=tar.gz -o "$work/angelo-src.tar.gz" HEAD
  git lfs ls-files -n | grep -E "^($LFS_DIRS)/" \
    | tar -czf "$work/angelo-lfs.tar.gz" -T -
  local libs_sha; libs_sha="$(git ls-tree HEAD lib/windows_x64 | awk '{print $3}')"
  ls -lh "$work"/*.tar.gz | awk '{print "   " $5 "  " $NF}'

  echo "== network (private: only IAP may reach RDP)"
  g compute networks describe "$NAME-net" >/dev/null 2>&1 || g compute networks create "$NAME-net" --subnet-mode=custom
  g compute networks subnets describe "$NAME-subnet" --region="$REGION" >/dev/null 2>&1 || \
    g compute networks subnets create "$NAME-subnet" --network="$NAME-net" --region="$REGION" --range=10.77.0.0/24
  g compute firewall-rules describe "$NAME-iap-rdp" >/dev/null 2>&1 || \
    g compute firewall-rules create "$NAME-iap-rdp" --network="$NAME-net" --direction=INGRESS \
      --allow=tcp:3389 --source-ranges=35.235.240.0/20

  echo "== bucket and service account (least privilege: this bucket only)"
  g storage buckets describe "gs://$BUCKET" >/dev/null 2>&1 || \
    g storage buckets create "gs://$BUCKET" --location="$REGION" --uniform-bucket-level-access --public-access-prevention
  g iam service-accounts describe "$SA_EMAIL" >/dev/null 2>&1 || g iam service-accounts create "$SA" --display-name="Angelo Windows validation"
  g storage buckets add-iam-policy-binding "gs://$BUCKET" --member="serviceAccount:$SA_EMAIL" --role=roles/storage.objectAdmin >/dev/null
  g storage cp "$work/angelo-src.tar.gz" "$work/angelo-lfs.tar.gz" "gs://$BUCKET/input/"
  printf '%s' "$(git rev-parse HEAD)" > "$work/uploaded-commit"
  g storage cp "$work/uploaded-commit" "gs://$BUCKET/input/uploaded-commit"
  g storage rm -r "gs://$BUCKET/result/" "gs://$BUCKET/logs/" 2>/dev/null || true

  echo "== VM"
  local accel=() meta="angelo-bucket=$BUCKET,angelo-src-commit=$(git rev-parse HEAD),angelo-libs-sha=$libs_sha,angelo-gpu=$GPU,angelo-stop-when-done=$STOP_WHEN_DONE"
  [[ "$GPU" == 1 ]] && accel=(--accelerator=type=nvidia-tesla-t4-vws,count=1)
  g compute instances create "$NAME" --zone="$ZONE" --machine-type="$MACHINE" "${accel[@]}" \
    --maintenance-policy=TERMINATE --image-family=windows-2022 --image-project=windows-cloud \
    --boot-disk-size=300GB --boot-disk-type=pd-ssd --network="$NAME-net" --subnet="$NAME-subnet" \
    --service-account="$SA_EMAIL" --scopes=storage-rw \
    --max-run-duration="${MAX_HOURS}h" --instance-termination-action=STOP \
    --metadata="$meta" --metadata-from-file="windows-startup-script-ps1=$ROOT/scripts/angelo/windows_cloud_bootstrap.ps1"
  rm -rf "$work"
  echo "Started. Watch with: $0 status   (the VM powers off when the run ends, or after ${MAX_HOURS}h; '$0 down' deletes everything)"
}

# What the machine has: the commit last uploaded (a text object next to the archives). Only files that changed
# since then are sent again, with their new times, so the incremental build recompiles just what depends on them;
# extracting all of HEAD would stamp every file with the commit time and rebuild the whole tree.
redo() {
  cd "$ROOT"
  need_real_files
  [[ -z "$(git status --porcelain --untracked-files=no)" ]] || { echo "Commit first: the archive is HEAD." >&2; exit 1; }
  local work head_sha base="" mode="full"
  work="$(mktemp -d)"; head_sha="$(git rev-parse HEAD)"
  if [[ "${2:-}" != "--full" ]]; then
    base="$(g storage cat "gs://$BUCKET/input/uploaded-commit" 2>/dev/null | tr -d '[:space:]' || true)"
    if [[ -n "$base" ]] && git cat-file -e "$base^{commit}" 2>/dev/null; then mode="delta"; fi
  fi
  if [[ "$mode" == "delta" ]]; then
    git lfs ls-files -n | sort > "$work/lfs-paths"
    git diff --no-renames --name-only --diff-filter=ACMRT "$base" HEAD | sort > "$work/changed"
    git diff --no-renames --name-only --diff-filter=D "$base" HEAD > "$work/angelo-deleted.txt"
    # A changed large file (LFS) travels as a pointer in the archive: harmless unless the build needs the real file
    # (the subset `up` sends separately); then send everything again.
    if comm -12 "$work/changed" "$work/lfs-paths" | grep -qE "^($LFS_DIRS)/"; then
      echo "A large file the build needs changed since the last upload; sending all of HEAD." >&2; mode="full"
    else
      comm -23 "$work/changed" "$work/lfs-paths" > "$work/changed.plain"; mv "$work/changed.plain" "$work/changed"
    fi
  fi
  if [[ "$mode" == "delta" ]]; then
    echo "== delta $(git rev-parse --short "$base")..$(git rev-parse --short HEAD): $(wc -l < "$work/changed" | tr -d ' ') changed, $(wc -l < "$work/angelo-deleted.txt" | tr -d ' ') deleted"
    : > "$work/angelo-delta.tar"
    if [[ -s "$work/changed" ]]; then
      local files=() f
      while IFS= read -r f; do files+=("$f"); done < "$work/changed"
      git archive --format=tar -o "$work/angelo-delta.tar" HEAD -- "${files[@]}"
    fi
    gzip -f "$work/angelo-delta.tar"
    git lfs ls-files -n | grep -E "^($LFS_DIRS)/" \
      | tar -czf "$work/angelo-lfs.tar.gz" -T -
    g storage cp "$work/angelo-delta.tar.gz" "$work/angelo-deleted.txt" "$work/angelo-lfs.tar.gz" "gs://$BUCKET/input/"
  else
    echo "== all of HEAD ($(git rev-parse --short HEAD))"
    git archive --format=tar.gz -o "$work/angelo-src.tar.gz" HEAD
    git lfs ls-files -n | grep -E "^($LFS_DIRS)/" \
      | tar -czf "$work/angelo-lfs.tar.gz" -T -
    g storage cp "$work/angelo-src.tar.gz" "$work/angelo-lfs.tar.gz" "gs://$BUCKET/input/"
  fi
  local commit_meta=",angelo-src-pack=0"
  if [[ "+$STAGES+" == *"+release+"* || "+$STAGES+" == *"+bench+"* || "${ANGELO_GCP_SRC_PACK:-0}" == 1 ]]; then
    # A release build (and a bench, whose harness must be its commit's: final_runs.py run refuses another tree)
    # needs the commit itself, so Blender's build info reads its true hash, branch and date (the
    # release stage refuses to package without them): the commit object and its tree, no history (about 90 MB),
    # which the bootstrap installs as a shallow commit under the extracted files.
    { git rev-parse HEAD; git rev-list --objects 'HEAD^{tree}' | cut -d' ' -f1; } | git pack-objects --stdout -q > "$work/angelo-commit.pack"
    g storage cp "$work/angelo-commit.pack" "gs://$BUCKET/input/angelo-commit.pack"
    commit_meta=",angelo-src-pack=1,angelo-src-branch=$(git rev-parse --abbrev-ref HEAD)"
  fi
  printf '%s' "$head_sha" > "$work/uploaded-commit"
  g storage cp "$work/uploaded-commit" "gs://$BUCKET/input/uploaded-commit"
  g storage rm -r "gs://$BUCKET/result/" 2>/dev/null || true
  g compute instances add-metadata "$NAME" --zone="$ZONE" \
    --metadata="angelo-src-nonce=$(date +%s),angelo-src-mode=$mode,angelo-src-commit=$head_sha,angelo-src-base=$base,angelo-stop-when-done=$STOP_WHEN_DONE,angelo-stages=$STAGES,angelo-hold-minutes=$HOLD,angelo-lfs-digest=$(lfs_digest),angelo-libs-sha=$(git ls-tree HEAD lib/windows_x64 | awk '{print $3}'),angelo-release-allow-missing=$ALLOW_MISSING${STOCK_URL:+,angelo-stock-blender-url=$STOCK_URL}$commit_meta$EXTRA_META" \
    --metadata-from-file="windows-startup-script-ps1=$ROOT/scripts/angelo/windows_cloud_bootstrap.ps1"
  if [[ "$(g compute instances describe "$NAME" --zone="$ZONE" --format='value(status)')" == "RUNNING" ]]; then
    g compute instances reset "$NAME" --zone="$ZONE"
  else
    g compute instances start "$NAME" --zone="$ZONE"
  fi
  rm -rf "$work"
  echo "Restarted; the validate stage runs again after the reboot. Watch with: $0 status"
}

status() {
  g compute instances describe "$NAME" --zone="$ZONE" --format="value(status)" 2>&1 | sed 's/^/VM: /' || true
  echo "--- result objects:"; g storage ls "gs://$BUCKET/result/" 2>&1 | sed 's#.*/##' || true
  echo "--- newest log lines:"; logs | tail -n 12
}

logs() {
  local tmp; tmp="$(mktemp -d)"
  g storage cp --quiet "gs://$BUCKET/logs/*" "$tmp/" 2>/dev/null || { echo "(no logs yet)"; return 0; }
  for f in $(ls -t "$tmp"); do echo "### $f"; tail -n "${ANGELO_GCP_LOG_LINES:-40}" "$tmp/$f"; done
  rm -rf "$tmp"
}

fetch() {
  mkdir -p "$OUT"; g storage cp --recursive "gs://$BUCKET/result/*" "gs://$BUCKET/logs/*" "$OUT/"
  echo "Downloaded to $OUT"; ls -lh "$OUT"
}

stop() { g compute instances stop "$NAME" --zone="$ZONE"; }

# Remote commands (windows_command_queue.ps1): only a machine started by `redo` with ANGELO_GCP_HOLD_MINUTES > 0 runs them.
send() {
  local file="${2:?send FILE.ps1 [gui]}" prefix="cmd"
  [[ "${3:-}" == "gui" ]] && prefix="cmd-gui"
  local name; name="$(date +%Y%m%d-%H%M%S)-$(basename "$file" .ps1)"
  g storage cp --quiet "$file" "gs://$BUCKET/$prefix/$name.ps1"
  echo "queued $prefix/$name; output: $0 out"
}

# What changed since the last upload, extracted over the machine's tree by a remote command (no restart, no build).
sync() {
  cd "$ROOT"
  [[ -z "$(git status --porcelain --untracked-files=no)" ]] || { echo "Commit first: the archive is HEAD." >&2; exit 1; }
  local work base head_sha; work="$(mktemp -d)"; head_sha="$(git rev-parse HEAD)"
  base="$(g storage cat "gs://$BUCKET/input/uploaded-commit" | tr -d '[:space:]')"
  git cat-file -e "$base^{commit}" 2>/dev/null || { echo "The uploaded commit $base is not here; use redo --full." >&2; exit 1; }
  git lfs ls-files -n | sort > "$work/lfs-paths"
  git diff --no-renames --name-only --diff-filter=ACMRT "$base" HEAD | sort > "$work/changed"
  if comm -12 "$work/changed" "$work/lfs-paths" | grep -qE "^($LFS_DIRS)/"; then
    echo "A large file the build needs changed; use redo." >&2; exit 1
  fi
  comm -23 "$work/changed" "$work/lfs-paths" > "$work/plain"
  git diff --no-renames --name-only --diff-filter=D "$base" HEAD > "$work/angelo-deleted.txt"
  : > "$work/angelo-delta.tar"
  if [[ -s "$work/plain" ]]; then
    local files=() f
    while IFS= read -r f; do files+=("$f"); done < "$work/plain"
    git archive --format=tar -o "$work/angelo-delta.tar" HEAD -- "${files[@]}"
  fi
  gzip -f "$work/angelo-delta.tar"
  cat > "$work/sync.ps1" <<PS1
\$ErrorActionPreference = "Stop"
\$gcloud = @("C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd", "C:\Program Files\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd") | Where-Object { Test-Path \$_ } | Select-Object -First 1
& \$gcloud storage cp --quiet "gs://$BUCKET/sync/angelo-delta.tar.gz" "gs://$BUCKET/sync/angelo-deleted.txt" C:\angelo\download
& tar -xzf C:\angelo\download\angelo-delta.tar.gz -C C:\angelo\src
foreach (\$gone in (Get-Content C:\angelo\download\angelo-deleted.txt)) { if (\$gone.Trim()) { Remove-Item (Join-Path C:\angelo\src \$gone.Trim()) -Force -ErrorAction SilentlyContinue } }
Set-Content C:\angelo\state\src.commit "$head_sha"
"synced $base..$head_sha"
PS1
  g storage cp --quiet "$work/angelo-delta.tar.gz" "$work/angelo-deleted.txt" "gs://$BUCKET/sync/"
  printf '%s' "$head_sha" > "$work/uploaded-commit"
  g storage cp --quiet "$work/uploaded-commit" "gs://$BUCKET/input/uploaded-commit"
  echo "== sync $(git rev-parse --short "$base")..$(git rev-parse --short HEAD): $(wc -l < "$work/plain" | tr -d ' ') changed"
  send send "$work/sync.ps1"
  rm -rf "$work"
}

out() {
  local tmp; tmp="$(mktemp -d)"
  g storage cp --quiet "gs://$BUCKET/cmd-out/*" "$tmp/" 2>/dev/null || { echo "(no command output yet)"; return 0; }
  for f in $(ls "$tmp"/*.log | sort -r | head -n "${2:-3}"); do
    echo "### $(basename "$f") exit $(cat "$f.exit" 2>/dev/null | tr -d '[:space:]')"; tail -n "${ANGELO_GCP_LOG_LINES:-60}" "$f"
  done
  rm -rf "$tmp"
}

release() {
  local marker; marker="$(mktemp)"; echo release > "$marker"
  g storage cp --quiet "$marker" "gs://$BUCKET/$([[ "${2:-}" == "gui" ]] && echo cmd-gui || echo cmd)-release"; rm -f "$marker"
}

# GPU toolkit installers for the release stage. They are licensed downloads (NVIDIA's CUDA Toolkit and OptiX SDK, AMD's
# HIP SDK): whoever runs this downloaded them and accepted their terms; the machine installs what it finds here.
toolkits() {
  shift
  [[ $# -gt 0 ]] || { echo "toolkits FILE... (cuda_<version>_..._windows.exe, an OptiX SDK .zip or .exe, the AMD HIP SDK .exe)" >&2; exit 2; }
  local f
  for f in "$@"; do [[ -f "$f" ]] || { echo "$f is not a file" >&2; exit 1; }; done
  g storage cp "$@" "gs://$BUCKET/toolkits/"
  g storage ls -l "gs://$BUCKET/toolkits/"
}

# The Windows agent runtime for the release stage (doc/angelo/releasing.rst, "Agent runtime"): the folder a build machine
# names in ANGELO_AGENT_RUNTIME_DIR, exactly as on macOS. It is checked here (release_checks.py: the pinned files, x64
# executables, OpenAI's signature still on its unchanged helpers) and against its own SHA256SUMS when it has one; the
# pinned files and their sums then replace gs://<bucket>/agent-runtime/, and the machine checks the sums again and
# passes the folder to the build as ANGELO_AGENT_RUNTIME_DIR (windows_cloud_bootstrap.ps1).
agent_runtime() {
  local dir="${2:?agent-runtime DIR (the folder holding codex.exe, codex-code-mode-host.exe and the sandbox helpers)}"
  [[ -d "$dir" ]] || { echo "$dir is not a folder" >&2; exit 1; }
  python3 "$ROOT/scripts/angelo/release_checks.py" agent-runtime "$dir" --platform windows --no-run || exit 1
  if [[ -f "$dir/SHA256SUMS" ]]; then
    ( cd "$dir" && shasum -a 256 -c SHA256SUMS >/dev/null ) || { echo "$dir/SHA256SUMS does not match its files" >&2; exit 1; }
  fi
  local work name
  work="$(mktemp -d)"
  # Only the pinned files travel (agent_runtime.json, platforms.windows), with sums of exactly those.
  for name in $(python3 -c 'import json, sys
pin = json.load(open(sys.argv[1]))["platforms"]["windows"]
print(" ".join(pin["required"] + pin["optional"]))' "$ROOT/scripts/addons_core/angelo_agent/agent_runtime.json"); do
    if [[ -f "$dir/$name" ]]; then cp "$dir/$name" "$work/"; fi
  done
  ( cd "$work" && shasum -a 256 -- *.exe > SHA256SUMS )
  if [[ -f "$dir/PROVENANCE.txt" ]]; then cp "$dir/PROVENANCE.txt" "$work/"; fi
  g storage rm --quiet "gs://$BUCKET/agent-runtime/**" 2>/dev/null || true
  g storage cp "$work"/* "gs://$BUCKET/agent-runtime/"
  g storage ls -l "gs://$BUCKET/agent-runtime/"
  rm -rf "$work"
}

# The one-shot tests' source pictures (doc/angelo/final_runs.rst, "What a machine holds"): never in the repository and
# never public. Only a bucket with uniform access, public access prevention enforced and no allUsers or
# allAuthenticatedUsers binding receives them; DIR is checked against the tests' SHA-256 pins first
# (final_runs_sources.py) and only the pinned files travel. Prepare-Bench downloads and checks them again; a clone
# gets a copy in its own bucket.
oneshot_sources() {
  local dir="${2:?oneshot-sources DIR (the folder holding the pictures the one-shot tests pin)}" info policy files f
  [[ -d "$dir" ]] || { echo "$dir is not a folder" >&2; exit 1; }
  info="$(g storage buckets describe "gs://$BUCKET" --format=json)"
  policy="$(g storage buckets get-iam-policy "gs://$BUCKET" --format=json)"
  python3 -c 'import json, sys
bucket, policy = json.loads(sys.argv[1]), json.loads(sys.argv[2])
members = {m for binding in policy.get("bindings", []) for m in binding.get("members", [])}
problems = [p for p, bad in (("uniform bucket-level access is off", not bucket.get("uniform_bucket_level_access")),
                             ("public access prevention is not enforced",
                              bucket.get("public_access_prevention") != "enforced"),
                             ("the bucket grants allUsers or allAuthenticatedUsers",
                              bool(members & {"allUsers", "allAuthenticatedUsers"}))) if bad]
sys.exit("not private: " + "; ".join(problems) if problems else 0)' "$info" "$policy" || {
    echo "gs://$BUCKET is not private enough for the source pictures; nothing was uploaded." >&2; exit 1; }
  files="$(python3 "$ROOT/tests/python/angelo_agent/final_runs_sources.py" paths --dir "$dir")" || {
    echo "$dir does not hold the pinned pictures; nothing was uploaded." >&2; exit 1; }
  while IFS= read -r f; do
    g storage cp "$f" "gs://$BUCKET/bench/oneshot-sources/$(basename "$f")"
  done <<< "$files"
  g storage ls -l "gs://$BUCKET/bench/oneshot-sources/"
}

# The run's own result once it ended: DONE says "validate exit code: N" (the last failed stage's code, the bench stage's
# included; 0 when all passed), FAILED that the bootstrap itself failed.
run_result() {
  local text
  if [[ " $1 " == *" FAILED "* ]]; then
    text="$(g storage cat "gs://$BUCKET/result/FAILED" 2>/dev/null | head -n 1 | tr -d '\r' || true)"
    echo "FAILED (${text:-no reason readable})"; return
  fi
  text="$(g storage cat "gs://$BUCKET/result/DONE" 2>/dev/null | tr -d '\r' || true)"
  if [[ "$text" =~ exit\ code:\ *([0-9]+) ]]; then echo "${BASH_REMATCH[1]}"; else echo "unknown (${text:-DONE unreadable})"; fi
}

# A run ended with stage exit code 0 is a success; anything else (FAILED, a nonzero or unreadable code, no end) is not.
run_passed() {
  [[ -n "$1" ]] || return 1
  [[ "$2" == 0 ]] && return 0
  echo "The run FAILED: stage exit code $2 (details: $0 logs)" >&2; return 1
}

# Wait for the run to end, then confirm the VM is off: it must be powered off after every use (a stopped VM is not
# billed for compute). The VM
# stops itself when a run ends (angelo-stop-when-done) and at the latest after its maximum run time; if it is still
# running a while after the run's DONE or FAILED, or when the wait runs out, it is stopped from here and checked again.
# Exits 0 only when the run ended with stage exit code 0 and the VM is confirmed off: a failed stage never counts as
# done, nor as READY FOR SIGN-IN (told only when the stage is still waiting ANGELO_GCP_READY_SETTLE_SECONDS after
# its first SIGN-IN-READY, and taken back if the stage fails afterwards).
wait_off() {
  local deadline=$(( $(date +%s) + ${ANGELO_GCP_WAIT_HOURS:-$((MAX_HOURS + 1))} * 3600 )) grace="${ANGELO_GCP_OFF_GRACE_MINUTES:-15}"
  local ended="" result="" vm objects told="" ready_seen="" nap
  while :; do
    vm="$(g compute instances describe "$NAME" --zone="$ZONE" --format='value(status)' 2>/dev/null || echo UNKNOWN)"
    objects="$( (g storage ls "gs://$BUCKET/result/" 2>/dev/null || true) | sed 's#.*/##' | tr '\n' ' ')"
    nap="${ANGELO_GCP_POLL_SECONDS:-120}"
    if [[ -z "$ended" && ( " $objects " == *" DONE "* || " $objects " == *" FAILED "* ) ]]; then
      ended="$(date +%s)"; result="$(run_result "$objects")"
      echo "$(date '+%H:%M') run ended: $objects(stage exit code: $result)"
      if [[ "$told" == ready && "$result" != 0 ]]; then
        echo "NOT READY: the stage failed after READY FOR SIGN-IN (exit code $result) and the machine powers off; do not sign in." >&2
      fi
    fi
    if [[ -z "$ended" && -z "$told" && " $objects " == *" SIGNED-IN-KEPT "* ]]; then
      told=kept; echo "SIGNED IN ALREADY: the machine's earlier sign-ins are still there (checked by existence) and were kept; no sign-in needed."
    elif [[ -z "$ended" && -z "$told" && " $objects " == *" SIGN-IN-READY "* && "$vm" == RUNNING ]]; then
      # bench prep: the operator's turn to sign in, once a second look shows the stage still waiting for it.
      if [[ -z "$ready_seen" ]]; then
        ready_seen=1; nap="${ANGELO_GCP_READY_SETTLE_SECONDS:-60}"
      else
        told=ready; echo "READY FOR SIGN-IN: sign in to Claude Code and Codex on the machine over remote desktop (the stage waits for it)."
      fi
    fi
    case "$vm" in
      TERMINATED|STOPPED)
        [[ -n "$ended" ]] || echo "The VM stopped before the run ended (its maximum run time?): '$0 release-build' again continues the incremental build."
        echo "VM $NAME: $vm (confirmed powered off)"
        if run_passed "$ended" "$result"; then return 0; fi
        return 1 ;;
    esac
    if [[ -n "$ended" && $(( $(date +%s) - ended )) -gt $(( grace * 60 )) ]] || [[ $(date +%s) -gt $deadline ]]; then
      echo "$(date '+%H:%M') VM $NAME still $vm; stopping it"
      g compute instances stop "$NAME" --zone="$ZONE" || true
      vm="$(g compute instances describe "$NAME" --zone="$ZONE" --format='value(status)' 2>/dev/null || echo UNKNOWN)"
      if [[ "$vm" == TERMINATED || "$vm" == STOPPED ]]; then
        echo "VM $NAME: $vm (confirmed powered off)"
        if run_passed "$ended" "$result"; then return 0; fi
        return 1
      fi
      echo "VM $NAME is $vm and could not be confirmed off: stop it in the console now." >&2; return 1
    fi
    echo "$(date '+%H:%M') VM $vm; results: ${objects:-none yet}"
    sleep "$nap"
  done
}

# The full-feature build: only the 'release' stage, the machine powering itself off at the end, then wait_off.
release_build() {
  local vm
  vm="$(g compute instances describe "$NAME" --zone="$ZONE" --format='value(status)' 2>/dev/null || echo MISSING)"
  [[ "$vm" == TERMINATED || "$vm" == STOPPED ]] || { echo "The VM is $vm: another run may be using it. Wait for it to power off first." >&2; exit 1; }
  STAGES=release HOLD=0 STOP_WHEN_DONE=1 redo redo "${2:-}"
  wait_off
}

bench() {
  local mode="${2:-}" claude_version codex_version work only="${ANGELO_BENCH_ONLY:-}"
  case "$mode" in
    prep|dry) ;;
    run) [[ "${3:-}" == "--authorize-billed-runs" ]] || {
           echo "bench run sends BILLED model requests on the signed-in accounts: pass --authorize-billed-runs" >&2; exit 2; } ;;
    wait) wait_off; return ;;
    *) echo "usage: $0 bench prep|dry|run --authorize-billed-runs|wait" >&2; exit 2 ;;
  esac
  local runtime="${ANGELO_BENCH_MCP_RUNTIME:?set ANGELO_BENCH_MCP_RUNTIME to the frozen-runtime folder of the pilot}"
  # The CLI versions the Mac measures with; the VM installs exactly these (a missing version fails the stage): Claude
  # Code from npm at the Mac's version, stock Codex from its GitHub release at the agent runtime's pinned version.
  claude_version="${ANGELO_BENCH_CLAUDE_VERSION:-$("$HOME/.local/bin/claude" --version 2>/dev/null | awk '{print $1}')}"
  codex_version="${ANGELO_BENCH_CODEX_VERSION:-$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["version"])' "$ROOT/scripts/addons_core/angelo_agent/agent_runtime.json")}"
  # The candidate: an MSI (a file here, uploaded; or vm:<path> on the machine) installed and frozen there; without
  # one, prep builds this commit (the 'release' stage) and the bench freezes that build.
  local candidate="${ANGELO_BENCH_CANDIDATE_MSI:-}" candidate_meta="" candidate_sha="${ANGELO_BENCH_CANDIDATE_SHA256:-}"
  if [[ -n "$candidate" && "$candidate" != vm:* ]]; then
    [[ -f "$candidate" ]] || { echo "ANGELO_BENCH_CANDIDATE_MSI: $candidate is not a file" >&2; exit 2; }
    candidate_sha="$(shasum -a 256 "$candidate" | awk '{print $1}')"
    candidate_meta="gs://$BUCKET/bench/candidate/${candidate_sha:0:12}-$(basename "$candidate")"
  elif [[ -n "$candidate" ]]; then
    candidate_meta="${candidate#vm:}"
  fi
  [[ -n "$claude_version" && -n "$codex_version" ]] || {
    echo "Set ANGELO_BENCH_CLAUDE_VERSION and ANGELO_BENCH_CODEX_VERSION (the versions the Mac runs use)." >&2; exit 2; }
  [[ "$only" != *,* ]] || { echo "ANGELO_BENCH_ONLY: separate globs with spaces" >&2; exit 2; }
  work="$(mktemp -d)"
  python3 "$ROOT/tests/python/angelo_agent/final_runs_mcp.py" bundle --runtime "$runtime" --into "$work/mcp-runtime.tar.gz" >/dev/null
  # prep also brings the full-feature build to this commit (the 'release' stage, incremental) unless an MSI is the
  # candidate; the bench freezes the candidate.
  STAGES="$([[ "$mode" == prep && -z "$candidate" ]] && echo release+bench || echo bench)"; HOLD=0; STOP_WHEN_DONE=1
  EXTRA_META=",angelo-bench-mode=$mode,angelo-bench-claude-version=$claude_version,angelo-bench-codex-version=$codex_version"
  EXTRA_META+=",angelo-bench-hours=$BENCH_HOURS,angelo-bench-stock-url=$BENCH_STOCK_URL,angelo-bench-only=$only"
  EXTRA_META+=",angelo-bench-signin-minutes=${ANGELO_BENCH_SIGNIN_MINUTES:-60}"
  EXTRA_META+=",angelo-bench-candidate-msi=$candidate_meta,angelo-bench-candidate-sha256=$candidate_sha"
  if [[ "${ANGELO_GCP_DRY_RUN:-0}" == "1" ]]; then
    echo "DRY RUN: nothing is uploaded or started."
    echo "  VM $NAME in $ZONE (project $PROJECT), bucket gs://$BUCKET; stage bench, mode $mode"
    echo "  would upload: HEAD $(git -C "$ROOT" rev-parse --short HEAD) (as redo), $(ls -lh "$work/mcp-runtime.tar.gz" | awk '{print $5}') MCP runtime"
    echo "  metadata: angelo-stages=$STAGES${EXTRA_META//,/ }"
    echo "  then: wait-off (the run's DONE or FAILED, then confirm the VM is TERMINATED, stopping it if not)"
    rm -rf "$work"; return 0
  fi
  local vm
  vm="$(g compute instances describe "$NAME" --zone="$ZONE" --format='value(status)' 2>/dev/null || echo MISSING)"
  [[ "$vm" == TERMINATED || "$vm" == STOPPED ]] || { echo "The VM is $vm: another run may be using it. Wait for it to power off first." >&2; exit 1; }
  g storage cp "$work/mcp-runtime.tar.gz" "gs://$BUCKET/bench/mcp-runtime.tar.gz"
  if [[ "$candidate_meta" == gs://* ]]; then g storage cp --no-clobber "$candidate" "$candidate_meta"; fi
  rm -rf "$work"
  redo bench
  local code=0
  wait_off || code=1
  echo "Results: gcloud storage ls gs://$BUCKET/bench/  (logs: $0 logs)"
  return "$code"
}

# Another bench machine for a parallel model (doc/angelo/final_runs.rst, "Three machines"): an instance of the machine
# image $NAME-bench-base (made from this machine while stopped and before any sign-in, so it holds the tools, the MCP
# side and the frozen candidate but no credential), in the same zone and network, with its own bucket (the command
# queue, the inputs and the results must not mix), readable by this machine's service account, which the image keeps.
# The source tree's commit and the MCP bundle are copied over, so the next send is a delta and the bench stage finds
# its bundle. The new machine boots once, has nothing to do and powers itself off; it is left TERMINATED.
clone() {
  local new="${2:?clone NAME (for example angelo-bench-2)}" image="${ANGELO_GCP_IMAGE:-$NAME-bench-base}"
  local bucket="$PROJECT-$new"
  if [[ "${ANGELO_GCP_DRY_RUN:-0}" == "1" ]]; then
    echo "DRY RUN: nothing is created."
    echo "  bucket gs://$bucket ($REGION), objectAdmin for $SA_EMAIL"
    echo "  copy gs://$BUCKET/input/uploaded-commit, gs://$BUCKET/bench/mcp-runtime.tar.gz and gs://$BUCKET/bench/oneshot-sources/* into it"
    echo "  instance $new in $ZONE from machine image $image, metadata angelo-bucket=$bucket angelo-hold-minutes=0"
    echo "  then: confirm $new is TERMINATED (it powers itself off after its first boot)"
    return 0
  fi
  g compute machine-images describe "$image" >/dev/null || { echo "No machine image $image" >&2; exit 1; }
  g storage buckets describe "gs://$bucket" >/dev/null 2>&1 || \
    g storage buckets create "gs://$bucket" --location="$REGION" --uniform-bucket-level-access --public-access-prevention
  g storage buckets add-iam-policy-binding "gs://$bucket" --member="serviceAccount:$SA_EMAIL" --role=roles/storage.objectAdmin >/dev/null
  g storage cp "gs://$BUCKET/input/uploaded-commit" "gs://$bucket/input/uploaded-commit"
  g storage cp "gs://$BUCKET/bench/mcp-runtime.tar.gz" "gs://$bucket/bench/mcp-runtime.tar.gz"
  # The one-shot source pictures, bucket to bucket (both private; oneshot-sources): Prepare-Bench reads its own.
  g storage cp "gs://$BUCKET/bench/oneshot-sources/*" "gs://$bucket/bench/oneshot-sources/"
  g compute instances create "$new" --zone="$ZONE" --source-machine-image="$image" \
    --metadata="angelo-bucket=$bucket,angelo-hold-minutes=0,angelo-stop-when-done=1"
  ANGELO_GCP_NAME="$new" ANGELO_GCP_BUCKET="$bucket" ANGELO_GCP_WAIT_HOURS=1 "$0" wait-off || {
    g compute instances stop "$new" --zone="$ZONE"; }
  g compute instances describe "$new" --zone="$ZONE" --format='value(status)' | sed "s/^/$new: /"
}

down() {
  g compute instances delete "$NAME" --zone="$ZONE" --quiet 2>/dev/null || true
  g compute firewall-rules delete "$NAME-iap-rdp" --quiet 2>/dev/null || true
  g compute networks subnets delete "$NAME-subnet" --region="$REGION" --quiet 2>/dev/null || true
  g compute networks delete "$NAME-net" --quiet 2>/dev/null || true
  g storage rm -r "gs://$BUCKET" 2>/dev/null || true
  g iam service-accounts delete "$SA_EMAIL" --quiet 2>/dev/null || true
  echo "Deleted the VM, network, bucket and service account (anything already gone was skipped)."
}

case "${1:-}" in
  up) up ;; redo) redo "$@" ;; stop) stop ;; status) status ;; logs) logs ;; fetch) fetch ;; down) down ;;
  send) send "$@" ;; sync) sync ;; out) out "$@" ;; release) release "$@" ;; bench) bench "$@" ;;
  toolkits) toolkits "$@" ;; agent-runtime) agent_runtime "$@" ;; release-build) release_build "$@" ;; wait-off) wait_off ;;
  clone) clone "$@" ;;
  oneshot-sources) oneshot_sources "$@" ;;
  *) sed -n '2,53p' "$0"; exit 2 ;;
esac
