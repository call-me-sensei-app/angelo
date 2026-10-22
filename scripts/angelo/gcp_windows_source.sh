# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
#
# The source a Windows build machine receives (sourced by gcp_windows_validate.sh; needs ROOT, BUCKET and g): archives
# of HEAD through the private bucket, never a push. The machine extracts them with scripts/angelo/source_tree.py,
# which travels beside them, so every name arrives as its exact UTF-8 path (Windows tar.exe garbled non-ASCII names).
#
# Which commit the machine has is known only from the machine: input/confirmed-commit, which it writes after proving
# its tree is that commit (source_send.py). A delta is computed from that commit and nothing else; with none, the
# send is all of HEAD. The launcher writes input/pending-commit (what it is sending), never the machine's state:
# twice a marker the launcher wrote before the machine had the commit outlived a cut-off send, and the next send was
# an empty delta from it. Each send is its own folder, input/send-<nonce>/, finished by send.json, and the machine
# is told its nonce only once every file is up, so neither a cut-off send nor a later one can mix into it.
#
# Path lists are written with core.quotePath=false: a non-ASCII name travels as its UTF-8 bytes, not as git's octal
# escapes, which name no file (a deletion or a delta file with such a name was silently missed). git still quotes a
# name holding a quote, a backslash or a control character; no Windows file name can hold one. The delta archive's
# pathspecs are literal, so a name with *, ? or [ is that file, not a pattern.
#
# One send is one commit: the caller resolves HEAD once and every step below takes that commit, never HEAD again. A
# send that read HEAD at each step packed a commit made during it while send.json and the metadata named the one
# before (the 2026-10-20 11:18 send named bfcf73f4296 and packed 4416202f87d). The Git LFS files and the sendable
# check read the working tree, so the send is refused when HEAD or a tracked file changes before send.json is up.

# The Git LFS files that travel as real files (angelo-lfs.tar.gz) beside the archive of HEAD, whose LFS files are
# pointers: every folder the build installs or packs from. A shipped file outside these folders would install as its
# pointer (the agent add-on's images did), and the release build refuses one.
LFS_DIRS='assets|release/datafiles|release/windows|release/branding|scripts'
SOURCE_TOOLS=("$ROOT/scripts/angelo/source_tree.py" "$ROOT/scripts/angelo/source_extract.py"
              "$ROOT/scripts/angelo/source_send.py" "$ROOT/scripts/angelo/shipped_sources.py"
              "$ROOT/scripts/angelo/windows_source_send.ps1")

# Refuse to send a large-file pointer in place of a file the build installs: run 'git lfs checkout' (local objects) first.
need_real_files() {
  local pointers
  pointers="$(cd "$ROOT" && git lfs ls-files | grep -E " - ($LFS_DIRS)/" | sed 's/^[0-9a-f]* - /  /' || true)"
  [[ -z "$pointers" ]] || { printf 'Git LFS pointers in place of files the build ships; run git lfs checkout (from the local cache) first:\n%s\n' "$pointers" >&2; exit 1; }
}

# The commit the machine last proved its tree to be (it alone writes input/confirmed-commit), when this checkout has
# it; empty otherwise, which makes the next send all of HEAD.
confirmed_commit() {
  local sha
  sha="$(g storage cat "gs://$BUCKET/input/confirmed-commit" 2>/dev/null | tr -d '[:space:]' || true)"
  if [[ "$sha" =~ ^[0-9a-f]{40}([0-9a-f]{24})?$ ]] && git cat-file -e "$sha^{commit}" 2>/dev/null; then echo "$sha"; fi
}

# Into folder $1: angelo-src.tar.gz (all of HEAD, LFS files as pointers), angelo-lfs.tar.gz (the large files the
# build needs, real) and angelo-lfs-real.txt, their list: the machine's proof fails while any of them is a pointer
# (git status cannot tell, the LFS clean filter passes a pointer unchanged). The machine applies the large-file
# archive with every send and writes only what differs, so no digest decides whether a renamed or re-moded file
# arrives real.
pack_head() {
  git archive --format=tar.gz -o "$1/angelo-src.tar.gz" "$2"
  pack_lfs "$1"
}
pack_lfs() {
  git lfs ls-files -n | grep -E "^($LFS_DIRS)/" > "$1/angelo-lfs-real.txt"
  tar -czf "$1/angelo-lfs.tar.gz" -T "$1/angelo-lfs-real.txt"
}

# What changed from commit $1 to the sent commit $3, into folder $2: changed and angelo-deleted.txt. Every changed path
# travels, a large file as its pointer (the machine's index holds pointers; the large-file archive brings the real ones).
delta_lists() {
  local base="$1" work="$2" commit="$3"
  git -c core.quotePath=false diff --no-renames --name-only --diff-filter=ACMRT "$base" "$commit" > "$work/changed"
  git -c core.quotePath=false diff --no-renames --name-only --diff-filter=D "$base" "$commit" > "$work/angelo-deleted.txt"
  if [[ ! -s "$work/changed" && ! -s "$work/angelo-deleted.txt" ]] &&
     [[ "$(git rev-parse "$base^{tree}")" != "$(git rev-parse "$commit^{tree}")" ]]; then
    echo "The delta from $base is empty although its tree differs from HEAD's: not sending it." >&2; return 1
  fi
}

# Folder $1's angelo-delta.tar.gz: the files delta_lists listed in $1/changed, as the sent commit $2 holds them.
pack_delta() {
  local work="$1" commit="$2" files=() f
  : > "$work/angelo-delta.tar"
  if [[ -s "$work/changed" ]]; then
    while IFS= read -r f; do files+=("$f"); done < "$work/changed"
    git --literal-pathspecs archive --format=tar -o "$work/angelo-delta.tar" "$commit" -- "${files[@]}"
  fi
  gzip -f "$work/angelo-delta.tar"
}

# Folder $1's angelo-commit.pack: the sent commit $3's object and the objects of its tree the machine lacks (no
# history): all of them, or for a delta only those not in confirmed commit $2's tree, which the machine holds.
pack_commit() {
  local work="$1" base="${2:-}" commit="$3"
  git rev-list --objects "$commit^{tree}" | cut -d' ' -f1 | LC_ALL=C sort -u > "$work/objects"
  if [[ -n "$base" ]]; then
    git rev-list --objects "$base^{tree}" | cut -d' ' -f1 | LC_ALL=C sort -u > "$work/objects.base"
    LC_ALL=C comm -23 "$work/objects" "$work/objects.base" > "$work/objects.new"
    mv "$work/objects.new" "$work/objects"
  fi
  { echo "$commit"; cat "$work/objects"; } | git pack-objects --stdout -q > "$work/angelo-commit.pack"
}

# Refuse the send when HEAD is no longer the sent commit $1, or a tracked file differs from it: the Git LFS files and
# the sendable check came from the working tree, so they would not be that commit's.
same_commit_still() {
  local now; now="$(git rev-parse HEAD)"
  [[ "$now" == "$1" ]] || { echo "HEAD moved from $1 to $now during the send: not sending it; send again." >&2; return 1; }
  [[ -z "$(git status --porcelain --untracked-files=no)" ]] ||
    { echo "A tracked file changed during the send of $1: not sending it; commit, then send again." >&2; return 1; }
}

# Send commit $4 (the caller's one reading of HEAD, which its metadata names too) as input/send-$2/, a delta from
# confirmed commit $3 (empty: all of it), staging in folder $1. Every file goes up before send.json; the caller tells
# the machine the nonce only after this returns. Call it as a plain command: the caller's set -e stops the send at
# any failed step, and an || or if around the call would turn that off inside it.
send_source() {
  local work="$1" nonce="$2" base="$3" head="$4" mode files branch prefix="gs://$BUCKET/input/send-$2"
  [[ "$head" =~ ^[0-9a-f]{40}([0-9a-f]{24})?$ ]] || { echo "send_source needs the full commit it sends." >&2; exit 1; }
  same_commit_still "$head"
  need_real_files   # every send: up, redo (release-build, bench) and sync
  # A tracked symbolic link, or two paths differing only in letter case, could never be made exact on the machine.
  python3 -I -B "$ROOT/scripts/angelo/source_tree.py" sendable --tree "$ROOT" --rev "$head"
  branch="$(git rev-parse --abbrev-ref HEAD | sed 's/\\/\\\\/g; s/"/\\"/g')"
  printf '%s\n' "$head" > "$work/pending-commit"
  g storage cp --quiet "$work/pending-commit" "gs://$BUCKET/input/pending-commit"
  g storage rm -r --quiet "gs://$BUCKET/input/send-*" 2>/dev/null || true   # earlier sends, finished or cut off
  if [[ -n "$base" ]]; then
    mode=delta; delta_lists "$base" "$work" "$head"; pack_delta "$work" "$head"; pack_lfs "$work"
    files=("$work/angelo-delta.tar.gz" "$work/angelo-deleted.txt")
    echo "== delta $(git rev-parse --short "$base")..$(git rev-parse --short "$head") (the confirmed commit): $(wc -l < "$work/changed" | tr -d ' ') changed, $(wc -l < "$work/angelo-deleted.txt" | tr -d ' ') deleted"
  else
    mode=full; pack_head "$work" "$head"; files=("$work/angelo-src.tar.gz")
    echo "== all of HEAD ($(git rev-parse --short "$head")): no confirmed commit to send a delta from"
  fi
  pack_commit "$work" "$base" "$head"
  g storage cp --quiet "${files[@]}" "$work/angelo-lfs.tar.gz" "$work/angelo-lfs-real.txt" "$work/angelo-commit.pack" \
    "${SOURCE_TOOLS[@]}" "$prefix/"
  same_commit_still "$head"   # nothing names this send yet: no send.json, no nonce
  printf '{"commit": "%s", "mode": "%s", "base": "%s", "branch": "%s"}\n' "$head" "$mode" "$base" "$branch" > "$work/send.json"
  g storage cp --quiet "$work/send.json" "$prefix/send.json"
}

# What changed since the confirmed commit, received by the machine's tree from a remote command (no restart, no build).
sync() {
  cd "$ROOT"
  [[ -z "$(git status --porcelain --untracked-files=no)" ]] || { echo "Commit first: the archive is HEAD." >&2; exit 1; }
  local work base head_sha nonce; work="$(mktemp -d)"; head_sha="$(git rev-parse HEAD)"; nonce="$(date +%s)-$$"
  base="$(confirmed_commit)"
  [[ -n "$base" ]] || { echo "The machine has confirmed no commit this checkout has; use redo (all of HEAD)." >&2; exit 1; }
  send_source "$work" "$nonce" "$base" "$head_sha"
  cat > "$work/sync.ps1" <<PS1
\$ErrorActionPreference = "Stop"
\$gcloud = @("C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd", "C:\Program Files\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd") | Where-Object { Test-Path \$_ } | Select-Object -First 1
\$folder = "C:\angelo\download\send-$nonce"
Remove-Item \$folder -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force \$folder | Out-Null
& \$gcloud storage cp --quiet --recursive "gs://$BUCKET/input/send-$nonce/*" \$folder
. (Join-Path \$folder "windows_source_send.ps1")
Receive-Send \$folder "C:\angelo\src" \$gcloud "$BUCKET" "$head_sha"
"synced $base..$head_sha"
PS1
  # The commit under test for the checks that read it; the machine's own record (sent.commit) is what runs check.
  g compute instances add-metadata "$NAME" --zone="$ZONE" --metadata="angelo-src-commit=$head_sha"
  send send "$work/sync.ps1"
  rm -rf "$work"
}
