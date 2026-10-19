#!/bin/sh
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Angelo's update installer for macOS (doc/angelo/updates_and_reports.rst, "Installing updates").
#
# Angelo copies this file out of its bundle into the update's own folder and starts it with /bin/sh just
# before it quits; it never runs from inside the bundle it replaces. Every input is an environment variable:
#
#   ANGELO_UPDATE_PID         Angelo's process: nothing changes until it has exited
#   ANGELO_UPDATE_APP         the installed bundle (.../Angelo.app)
#   ANGELO_UPDATE_EXECUTABLE  its executable's name (Contents/MacOS/<name>), to find other running copies
#   ANGELO_UPDATE_NEW         the new bundle, already verified (signature, bundle id, version)
#   ANGELO_UPDATE_REQUIREMENT the code requirement it must still meet: checked again right before the swap, with
#                             the version, so a stage changed after Angelo verified it is never installed (Angelo
#                             always passes one; only the host tests, which have no codesign, leave it empty)
#   ANGELO_UPDATE_GATEKEEPER  1: Gatekeeper approves the running app (a notarized release), so it must approve the
#                             new one too (spctl), before the swap and again once it is in place
#   ANGELO_UPDATE_DIR         this update's folder: ready marker, result, log, the previous bundle
#   ANGELO_UPDATE_VERSION     the new version, for the result
#   ANGELO_UPDATE_RELAUNCH    1: open the app afterwards (the new one, or the old one when anything failed)
#   ANGELO_UPDATE_OPEN        a file the relaunched app opens (may be empty)
#   ANGELO_UPDATE_ENV         NAME=VALUE lines the relaunched app gets (open --env), may be empty
#
# One installer at a time (helper.lock). The swap is two renames: the installed bundle moves into this folder (or beside itself on another volume),
# the new one takes its place; a failed second rename moves the old one back, and so does a new bundle that
# fails its check once in place. The previous bundle stays here until the new version's first launch removes
# it. result.txt says what happened; Angelo reads it at its next launch.
#
# Nothing macOS would call damaged is ever registered or opened: a bundle is registered with Launch Services
# and relaunched only after codesign --verify --deep --strict (and spctl, when required) passed on it.

set -u
dir=$ANGELO_UPDATE_DIR
app=$ANGELO_UPDATE_APP
new=$ANGELO_UPDATE_NEW
log="$dir/install.log"

say() { printf '%s %s\n' "$(/bin/date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$log"; }
result() {
    printf 'status=%s\nstep=%s\nversion=%s\n' "$1" "$2" "$ANGELO_UPDATE_VERSION" > "$dir/result.tmp" &&
        /bin/mv -f "$dir/result.tmp" "$dir/result.txt"
}
if /usr/bin/stat -f %d / >/dev/null 2>&1; then   # BSD stat (macOS); GNU stat only for the host tests.
    device() { /usr/bin/stat -f %d "$1"; }
else
    device() { /usr/bin/stat -c %d "$1"; }
fi
checked=""
launchable() {   # launchable <bundle>: a sealed signature, and Gatekeeper's approval when the running app has it.
    [ "$checked" = "$1" ] && return 0
    [ -x /usr/bin/codesign ] || return 1
    /usr/bin/codesign --verify --deep --strict "$1" >> "$log" 2>&1 || return 1
    if [ "${ANGELO_UPDATE_GATEKEEPER:-0}" = 1 ]; then
        /usr/sbin/spctl --assess --type execute "$1" >> "$log" 2>&1 || return 1
    fi
    checked=$1
}
relaunch() {
    [ "${ANGELO_UPDATE_RELAUNCH:-0}" = 1 ] || return 0
    launchable "$app" || { say "not relaunched: $app fails its signature check"; return 0; }
    set -- -n "$app"
    if [ -n "${ANGELO_UPDATE_ENV:-}" ]; then
        while IFS= read -r pair; do
            [ -n "$pair" ] && set -- "$@" --env "$pair"
        done <<EOF
$ANGELO_UPDATE_ENV
EOF
    fi
    [ -n "${ANGELO_UPDATE_OPEN:-}" ] && set -- "$@" --args "$ANGELO_UPDATE_OPEN"
    /usr/bin/open "$@" >> "$log" 2>&1 || say "relaunch failed"
}
finish() {   # finish <status> <step>: record, relaunch, end
    say "$1 at $2"
    result "$1" "$2"
    relaunch
    [ "$1" = installed ] && exit 0
    exit 1
}

/bin/mkdir "$dir/helper.lock" 2>/dev/null || exit 0   # Another installer runs for this update.
: > "$dir/helper.ready"   # Angelo waits for this before it quits.
say "waiting for process $ANGELO_UPDATE_PID to quit"
alive() {   # An exited process its parent has not reaped yet (a zombie) counts as gone.
    /bin/kill -0 "$1" 2>/dev/null || return 1
    case "$(/bin/ps -o stat= -p "$1" 2>/dev/null)" in
        Z*) return 1 ;;
    esac
    return 0
}
waited=0
while alive "$ANGELO_UPDATE_PID"; do
    waited=$((waited + 1))
    if [ "$waited" -gt 1200 ]; then   # Ten minutes: Angelo did not quit; it is still open, so nothing relaunches.
        say "aborted at wait"; result aborted wait; exit 1
    fi
    /bin/sleep 0.5
done

# Another copy of this same app still running would have its files replaced under it.
running_from() {   # Is any process executing the file $1? (/proc only where it exists: the host tests on Linux.)
    if [ -L /proc/self/exe ]; then
        for exe in /proc/[0-9]*/exe; do
            [ "$(/usr/bin/readlink "$exe" 2>/dev/null)" = "$1" ] && return 0
        done
        return 1
    fi
    /bin/ps -axo comm= | /usr/bin/grep -Fqx -- "$1"
}
if running_from "$app/Contents/MacOS/$ANGELO_UPDATE_EXECUTABLE"; then
    finish aborted another_instance
fi
[ -d "$new/Contents" ] || finish failed missing_update
[ -d "$app/Contents" ] || finish failed missing_app
if [ -n "${ANGELO_UPDATE_REQUIREMENT:-}" ]; then
    /usr/bin/codesign --verify --deep --strict -R="$ANGELO_UPDATE_REQUIREMENT" "$new" >> "$log" 2>&1 ||
        finish failed signature
    found=$(/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$new/Contents/Info.plist" 2>/dev/null)
    [ "$found" = "$ANGELO_UPDATE_VERSION" ] || finish failed version
fi
if [ "${ANGELO_UPDATE_GATEKEEPER:-0}" = 1 ]; then
    /usr/sbin/spctl --assess --type execute "$new" >> "$log" 2>&1 || finish failed gatekeeper
fi
verified=""   # The bundle that just passed every check launchable makes, when Angelo asked for them.
[ -n "${ANGELO_UPDATE_REQUIREMENT:-}" ] && verified=$new

parent=$(/usr/bin/dirname "$app")
name=$(/usr/bin/basename "$app")
if [ "$(device "$new")" != "$(device "$parent")" ]; then
    # Staged on another volume: copy it beside the installed app first, so the swap is still two renames.
    copy="$parent/.$name.update-$$"
    /bin/rm -rf "$copy"
    /usr/bin/ditto "$new" "$copy" >> "$log" 2>&1 || { /bin/rm -rf "$copy"; finish failed copy; }
    new=$copy
fi
if [ "$(device "$dir")" = "$(device "$parent")" ]; then
    previous="$dir/previous.app"
else
    previous="$parent/.$name.previous-$$"
fi
/bin/rm -rf "$previous"
/bin/mv "$app" "$previous" >> "$log" 2>&1 || finish failed move_old
if ! /bin/mv "$new" "$app" >> "$log" 2>&1; then
    /bin/mv "$previous" "$app" >> "$log" 2>&1 || say "could not put the previous app back: it is at $previous"
    finish failed move_new
fi
/usr/bin/touch "$app"
# A rename changes no byte: the verified bundle is what is in place. A copy (another volume) is checked again
# where it will run, and anything wrong puts the previous app back.
[ -n "$verified" ] && [ "$verified" = "$new" ] && checked=$app
if [ -n "${ANGELO_UPDATE_REQUIREMENT:-}" ] && ! launchable "$app"; then
    rejected="$parent/.$name.rejected-$$"
    if /bin/mv "$app" "$rejected" >> "$log" 2>&1 && /bin/mv "$previous" "$app" >> "$log" 2>&1; then
        /bin/rm -rf "$rejected"
    else
        say "could not put the previous app back: it is at $previous"
    fi
    finish failed installed_check
fi
printf '%s\n' "$previous" > "$dir/previous.txt"
lsregister=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
if [ -x "$lsregister" ] && launchable "$app"; then
    "$lsregister" -f "$app" >> "$log" 2>&1
fi
# Launch Services follows a moved bundle: the previous app, now in this folder until the new version deletes it,
# must not answer for Angelo any more.
[ -x "$lsregister" ] && "$lsregister" -u "$previous" >> "$log" 2>&1
finish installed swap
