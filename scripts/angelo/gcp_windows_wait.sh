# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Watching a Windows machine's run to its end (sourced by gcp_windows_validate.sh; needs NAME, ZONE, BUCKET, STAGES,
# MAX_HOURS, HEARTBEAT_MINUTES, STALL_MINUTES, STALL_CONFIRM and g): the run's result, a release stage's failures, a
# hung run (the stall guard), and wait_off, which confirms the machine is powered off after every use and puts its
# maximum run time back to the default.

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

# The checks the release stage marked FAIL in result/release/release-results.json (uploaded when it ends, before any
# hold), joined by "; "; empty while there is none. Its MSI is then not this commit's build, so waiting on is pointless.
release_failures() {
  (g storage cat "gs://$BUCKET/result/release/release-results.json" 2>/dev/null || true) | python3 -I -c 'import json, sys
rows = json.loads(sys.stdin.buffer.read().decode("utf-8-sig").strip() or "[]")   # One check: an object, not a list.
print("; ".join(str(r.get("Check")) for r in (rows if isinstance(rows, list) else [rows])
                if isinstance(r, dict) and r.get("Status") == "FAIL"))' 2>/dev/null || true
}

# A run ended with stage exit code 0 is a success; anything else (FAILED, a nonzero or unreadable code, no end) is not.
run_passed() {
  [[ -n "$1" ]] || return 1
  [[ "$2" == 0 ]] && return 0
  echo "The run FAILED: stage exit code $2 (details: $0 logs)" >&2; return 1
}

# A hung run (heartbeat_stall): the heartbeat is older than HEARTBEAT_MINUTES (or absent that long after the send),
# or the output folders it reports have not changed for STALL_MINUTES. Prints why, with the machine's last log lines.
heartbeat_stall() {
  local since="$1" quiet="$2" text errors
  # A read that fails for any reason but the object's absence (a token expiry, the network) is unknown: "?".
  errors="$(mktemp)"
  if ! text="$(g storage cat "gs://$BUCKET/live/HEARTBEAT" 2>"$errors")" || [[ -z "$text" ]]; then
    if ! grep -q "matched no objects" "$errors"; then rm -f "$errors"; echo "?"; return; fi
    text=""
  fi
  text="$(printf '%s' "$text" | tr -d '\r')"
  rm -f "$errors"
  HEARTBEAT_TEXT="$text" python3 - "$since" "$HEARTBEAT_MINUTES" "$quiet" <<'PY'
import os, re, sys, time
since, beat, stall = int(sys.argv[1]), int(sys.argv[2]) * 60, int(sys.argv[3]) * 60
text = os.environ.get("HEARTBEAT_TEXT", "")
head = text.split("\n--", 1)[0]   # The fields, one per line (or on one line, as a heartbeat may write them).
found = {key: re.search(r"\b%s: (\S+)" % key, head) for key in ("at", "progress", "newest")}
fields = {key: match.group(1) for key, match in found.items() if match}
now = time.time()
try:
    at, progress = int(fields.get("at", "")), int(fields.get("progress", "0"))
except ValueError:
    at = progress = 0
if not at:
    if now - since > beat:
        print("no heartbeat %d minutes after the send" % ((now - since) // 60))
elif now - at > beat:
    print("heartbeat %d minutes old" % ((now - at) // 60))
elif stall and progress and now - max(progress, since) > stall:
    print("no output for %d minutes (newest %s)\n%s" % ((now - max(progress, since)) // 60, fields.get("newest", "?"),
                                                    text.split("\n--", 1)[-1].strip()))
PY
}

# Wait for the run to end, then confirm the VM is off: it must be powered off after every use (a stopped VM is not
# billed for compute). The VM
# stops itself when a run ends (angelo-stop-when-done) and at the latest after its maximum run time; if it is still
# running a while after the run's DONE or FAILED, or when the wait runs out, it is stopped from here and checked again.
# Exits 0 only when the run ended with stage exit code 0 and the VM is confirmed off: a failed stage never counts as
# done, nor as READY FOR SIGN-IN (told only when the stage is still waiting ANGELO_GCP_READY_SETTLE_SECONDS after
# its first SIGN-IN-READY, and taken back if the stage fails afterwards).
# A send sets the machine's maximum run time for its own run (redo); once the machine is off it goes back to the
# default (MAX_HOURS), so a later send from a sender without this step never inherits a short limit.
restore_limit() {
  local vm limit
  vm="$(g compute instances describe "$NAME" --zone="$ZONE" --format='value(status)' 2>/dev/null || echo UNKNOWN)"
  [[ "$vm" == TERMINATED || "$vm" == STOPPED ]] || return 0
  limit="$(g compute instances describe "$NAME" --zone="$ZONE" --format='value(scheduling.maxRunDuration.seconds)' 2>/dev/null || true)"
  [[ "$limit" == "$(( MAX_HOURS * 3600 ))" ]] && return 0
  g compute instances set-scheduling "$NAME" --zone="$ZONE" --max-run-duration="${MAX_HOURS}h" \
    --instance-termination-action=STOP >/dev/null 2>&1 || echo "VM $NAME: its maximum run time could not be put back to ${MAX_HOURS}h" >&2
}

wait_off() {
  local code=0
  wait_off_run || code=$?
  restore_limit
  return "$code"
}

wait_off_run() {
  local deadline=$(( $(date +%s) + ${ANGELO_GCP_WAIT_HOURS:-$((MAX_HOURS + 1))} * 3600 )) grace="${ANGELO_GCP_OFF_GRACE_MINUTES:-15}"
  local ended="" result="" vm objects told="" ready_seen="" nap failed stalled since="${ANGELO_GCP_RUN_SINCE:-$(date +%s)}" misses=0
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
    if [[ -z "$ended" ]] && failed="$(release_failures)" && [[ -n "$failed" ]]; then
      # Ends the wait now (a hold would only keep the machine up); the VM is stopped from here at the next look.
      ended="$(date +%s)"; result="release-results.json FAIL"; grace=0
      echo "$(date '+%H:%M') release stage FAILED: $failed (release-results.json); not waiting for the run's end" >&2
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
    stalled=""
    if [[ -z "$ended" && -z "$told" && "$vm" == RUNNING ]]; then
      stalled="$(heartbeat_stall "$since" "$([[ "$STAGES" == bench ]] && echo "$STALL_MINUTES" || echo 0)")"
      # One reading is not enough (a single failed read of the heartbeat): STALL_CONFIRM in a row stop the run.
      if [[ "$stalled" == "?" ]]; then stalled=""; echo "$(date '+%H:%M') heartbeat unreadable this poll" >&2
      elif [[ -n "$stalled" ]]; then misses=$(( ${misses:-0} + 1 )); else misses=0; fi
      [[ -z "$stalled" ]] || echo "$(date '+%H:%M') STALLED ($misses of $STALL_CONFIRM): $stalled" >&2
      (( ${misses:-0} >= STALL_CONFIRM )) || stalled=""
    fi
    if [[ -n "$ended" && $(( $(date +%s) - ended )) -gt $(( grace * 60 )) ]] || [[ $(date +%s) -gt $deadline ]] \
        || [[ -n "$stalled" ]]; then
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
