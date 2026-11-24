#!/bin/bash
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Angelo's update installer for Linux (doc/angelo/updates_and_reports.rst, "Installing updates").
#
# Angelo copies this file into the update's own folder and starts it with /bin/bash --noprofile --norc, in a new
# session, just before it quits; it never runs from inside the folder it replaces. Every input is an environment
# variable; the rest of the environment is the one Angelo was started with, which the relaunched app keeps:
#
#   ANGELO_UPDATE_PID         Angelo's process: nothing changes until it has exited
#   ANGELO_UPDATE_ROOT        the installed release folder (.../Angelo-<version>-linux-x64, under any name)
#   ANGELO_UPDATE_EXECUTABLE  the executable's file name, for the log
#   ANGELO_UPDATE_PROGRAM     the executable's path inside the folder: another process running it is another copy
#                             of this Angelo, whose files the swap would move away under it
#   ANGELO_UPDATE_NEW         the new release folder, already checked against its signed manifest
#                             (update_stage_linux.stage) and its fingerprint checked again before this started
#   ANGELO_UPDATE_DIR         this update's folder: ready marker, result, log, the previous folder
#   ANGELO_UPDATE_VERSION     the new version, for the result
#   ANGELO_UPDATE_RELAUNCH    the executable's path inside the folder: start it afterwards (the new one, or the old
#                             one when anything failed); empty: start nothing
#   ANGELO_UPDATE_OPEN        a file the relaunched app opens (may be empty)
#   ANGELO_UPDATE_KEPT        where the two-rename fallback keeps the previous folder, beside the install (translated)
#   ANGELO_UPDATE_NOTE        the fallback's recovery note beside the install, and ANGELO_UPDATE_NOTE_TEXT its text
#
# One installer at a time (helper.lock). The swap is one atomic exchange of the two folders (renameat2
# RENAME_EXCHANGE, through the installed release's own Python, else the system's); the old release then moves into
# this update's folder (or beside the install on another file system). Where no exchange is possible, two renames:
# first a note beside the install ("<name> - update interrupted.txt") and the old folder renamed to a visible
# "<name> (previous)" beside it, so a stop between the renames leaves a findable app and how to restore it; a failed
# second rename moves it back. A new folder staged on another file system is copied beside the installed one first and compared with the
# checked stage byte for byte (diff), so what is renamed into place is what was checked. The previous folder stays
# until the new version's first launch removes it. result.txt says what happened; Angelo reads it at its next launch.

set -u
saved_path=${PATH:-/usr/bin:/bin}
PATH=/usr/bin:/bin:/usr/sbin:/sbin   # The system's tools only; the relaunched app gets the person's PATH back.
dir=$ANGELO_UPDATE_DIR
root=$ANGELO_UPDATE_ROOT
new=$ANGELO_UPDATE_NEW
log="$dir/install.log"

say() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$log"; }
result() {
    printf 'status=%s\nstep=%s\nversion=%s\n' "$1" "$2" "$ANGELO_UPDATE_VERSION" > "$dir/result.tmp" &&
        mv -f "$dir/result.tmp" "$dir/result.txt"
}
device() { stat -c %d "$1"; }
identity() { stat -L -c %d:%i "$1" 2>/dev/null; }   # The file itself, links followed.
relaunch() {
    [ -n "${ANGELO_UPDATE_RELAUNCH:-}" ] || return 0
    case "/$ANGELO_UPDATE_RELAUNCH/" in */../*|*/./*|//*) say "not relaunched: unusable path"; return 0 ;; esac
    target="$root/$ANGELO_UPDATE_RELAUNCH"
    [ -f "$target" ] && [ -x "$target" ] || { say "not relaunched: $target is not a program"; return 0; }
    set -- "$target"
    [ -n "${ANGELO_UPDATE_OPEN:-}" ] && set -- "$@" "$ANGELO_UPDATE_OPEN"
    (
        PATH=$saved_path
        for variable in $(compgen -e); do   # Every input of this script, whatever its name: none is the app's.
            case $variable in ANGELO_UPDATE_*) unset "$variable" ;; esac
        done
        cd "${HOME:-/}" 2>/dev/null || cd /
        exec "$@" < /dev/null > /dev/null 2>&1   # Already in this script's own session, apart from Angelo's.
    ) &
}
finish() {   # finish <status> <step>: record, relaunch, end
    say "$1 at $2"
    result "$1" "$2"
    relaunch
    [ "$1" = installed ] && exit 0
    exit 1
}

mkdir "$dir/helper.lock" 2>/dev/null || exit 0   # Another installer runs for this update.
# Readiness is not authorization. Only a complete native committed-exit frame
# followed by EOF authorizes the existing parent-exit/install path below.
nonce=${ANGELO_UPDATE_INVOCATION:-}
case "$nonce" in *[!0-9a-f]*|'') exit 1 ;; esac
[ "${#nonce}" = 32 ] || exit 1
started() {   # The process's start time (field 22 of /proc/<pid>/stat, after its parenthesised name).
    sed 's/.*) //' "/proc/$1/stat" 2>/dev/null | awk '{print $20}'
}
angelo_started=$(started "$ANGELO_UPDATE_PID")   # Now, while Angelo provably runs and waits for this script.
printf '%s\n' "$nonce" > "$dir/helper.ready" || exit 1
grant=''
IFS= read -r -t 600 -n 96 grant || { result aborted authorization; exit 1; }
[ "$grant" = "ANGELO_UPDATE_COMMIT:$nonce" ] || { result aborted authorization; exit 1; }
# Native owns the sole writer before commit. A live/extra writer is not EOF.
extra=''
IFS= read -r -t 2 -n 1 extra
read_status=$?
[ "$read_status" = 1 ] && [ -z "$extra" ] || { result aborted authorization; exit 1; }
exec 0<&-
say "waiting for process $ANGELO_UPDATE_PID ($ANGELO_UPDATE_EXECUTABLE) to quit"
alive() {   # An exited process its parent has not reaped yet (a zombie) counts as gone; so does a reused number.
    [ -d "/proc/$1" ] || return 1
    case "$(sed -n 's/^State:[[:space:]]*\(.\).*/\1/p' "/proc/$1/status" 2>/dev/null)" in
        Z|X|'') return 1 ;;
    esac
    [ -z "$angelo_started" ] || [ "$(started "$1")" = "$angelo_started" ]
}
waited=0
while alive "$ANGELO_UPDATE_PID"; do
    waited=$((waited + 1))
    if [ "$waited" -gt 1200 ]; then   # Ten minutes: Angelo did not quit; it is still open, so nothing relaunches.
        say "aborted at wait"; result aborted wait; exit 1
    fi
    sleep 0.5
done

# Another copy of this same app still running would have its files moved away under it.
running_from() {   # Is any process executing the file $1? (/proc/<pid>/exe is the file the kernel runs.)
    wanted=$(identity "$1") || return 1
    for exe in /proc/[0-9]*/exe; do
        [ "$(identity "$exe")" = "$wanted" ] && return 0
    done
    return 1
}
case "/${ANGELO_UPDATE_PROGRAM:-}/" in */../*|*/./*|//*) finish failed missing_app ;; esac
if running_from "$root/$ANGELO_UPDATE_PROGRAM"; then
    finish aborted another_instance
fi
[ -d "$new" ] && [ ! -L "$new" ] && [ -f "$new/angelo-release-manifest.json" ] || finish failed missing_update
[ -d "$root" ] && [ ! -L "$root" ] && [ -f "$root/angelo-release-manifest.json" ] || finish failed missing_app

parent=$(dirname "$root")
name=$(basename "$root")
if [ "$(device "$new")" != "$(device "$parent")" ]; then
    # Staged on another file system: copy it beside the installed folder first, so the swap is still two renames,
    # and compare the copy with the checked stage (contents, links, types) before it is used.
    copy="$parent/.$name.update-$$"
    rm -rf "$copy"
    if ! cp -a --no-preserve=ownership "$new" "$copy" >> "$log" 2>&1 ||
            ! diff -r --no-dereference -q "$new" "$copy" >> "$log" 2>&1; then
        rm -rf "$copy"
        finish failed copy
    fi
    new=$copy
fi
python3_in() {   # python3_in A B: the first Python 3 of the two releases (or the system's); 1 when none.
    for py in "$1"/*/python/bin/python3 "$1"/*/python/bin/python3.[0-9]* "$2"/*/python/bin/python3.[0-9]* /usr/bin/python3; do
        [ -f "$py" ] && [ -x "$py" ] && { printf '%s\n' "$py"; return 0; }
    done
    return 1
}
exchange() {   # exchange A B: one atomic renameat2(RENAME_EXCHANGE) (glibc 2.28+, Linux 3.15+); 1 where unavailable.
    [ "${ANGELO_UPDATE_TEST_NO_EXCHANGE:-}" = 1 ] && return 1   # The host tests' way to run the fallback.
    py=$(python3_in "$1" "$2") || return 1
    "$py" -I -c 'import ctypes, os, sys
libc = ctypes.CDLL(None, use_errno=True)
if libc.renameat2(-100, os.fsencode(sys.argv[1]), -100, os.fsencode(sys.argv[2]), 2) != 0:
    sys.exit("renameat2: " + os.strerror(ctypes.get_errno()))' "$1" "$2" >> "$log" 2>&1
}
create_note() {   # create_note PATH < TEXT: a new file only (O_EXCL), never opened through a link, FIFO or device.
    py=$(python3_in "$new" "$root") || return 1
    "$py" -I -c 'import os, sys
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644)
with os.fdopen(fd, "wb") as note:
    note.write(sys.stdin.buffer.read())' "$1" 2>> "$log"
}
if exchange "$new" "$root"; then
    # One step: the person's path never stands empty. The old release is now where the new one was staged.
    old=$new
    if [ "$(device "$old")" = "$(device "$dir")" ]; then previous="$dir/previous"; else previous="$parent/.$name.previous-$$"; fi
    rm -rf "$previous"
    mv -T "$old" "$previous" >> "$log" 2>&1 || previous=$old
else
    # Two renames (no exchange on this file system: NFS, for one). A stop between them would leave the person's path
    # empty, so first the previous folder gets a visible name beside the install and a note says where it is and
    # how to put it back; the result says "interrupted" until the swap is done.
    # Angelo passes the kept folder, the note and its text in the person's language (update_card.linux_recovery);
    # the English below is only for a caller that passes none.
    previous=${ANGELO_UPDATE_KEPT:-"$parent/$name (previous)"}
    note=${ANGELO_UPDATE_NOTE:-"$parent/$name - update interrupted.txt"}
    [ "$(dirname "$previous")" = "$parent" ] && [ "$(dirname "$note")" = "$parent" ] || finish failed note
    [ -e "$previous" ] || [ -L "$previous" ] && finish failed note
    # The note is created, never written through a file, link or FIFO someone placed at its name: an existing name
    # of any kind refuses it (O_EXCL), so there is no check-then-open window.
    if [ -n "${ANGELO_UPDATE_NOTE_TEXT:-}" ]; then
        printf '%s' "$ANGELO_UPDATE_NOTE_TEXT" | create_note "$note" || finish failed note
    else
        printf '%s\n' "Angelo was being updated to $ANGELO_UPDATE_VERSION when the update stopped." \
            "Your previous Angelo is in: $previous" \
            "To use it again, rename that folder back to: $root" | create_note "$note" || finish failed note
    fi
    result failed interrupted
    printf '%s\n' "$previous" > "$dir/previous.txt"
    mv -T "$root" "$previous" >> "$log" 2>&1 || { rm -f "$note"; finish failed move_old; }
    [ "${ANGELO_UPDATE_TEST_STOP_BETWEEN:-}" = 1 ] && exit 9   # The host tests' stand-in for a stop right here.
    if ! mv -T "$new" "$root" >> "$log" 2>&1; then
        if mv -T "$previous" "$root" >> "$log" 2>&1; then rm -f "$note"; else
            say "could not put the previous folder back: it is at $previous"; fi
        finish failed move_new
    fi
    rm -f "$note"
fi
touch "$root"
printf '%s\n' "$previous" > "$dir/previous.txt"
finish installed swap
