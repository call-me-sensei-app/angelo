#!/bin/zsh
# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
# Voxel remesh memory/time measurement behind doc/angelo/voxel_remesh_guard.rst: one remesh per background
# process, niced, appended to results.jsonl in the current folder. Stops when free memory drops below half.
#   ANGELO_APP=/path/to/Angelo.app/Contents/MacOS/Angelo run.zsh cube:4000000:op:default sphere:1000000:mod:default ...
# Each spec is shape:estimated_vertices:op|mod:default|all|none (see measure_one.py). macOS only (memory_pressure).
HERE=${0:A:h}
APP=${ANGELO_APP:?set ANGELO_APP to the app's executable}
for spec in "$@"; do
  set -- ${(s/:/)spec}
  free=$(memory_pressure | awk -F': ' '/free percentage/ {gsub("%","",$2); print $2}')
  if (( free < 50 )); then echo "STOP low memory free=$free" >> run.log; exit 4; fi
  line=$(ANGELO_DISABLE_KEYCHAIN=1 nice -n 19 taskpolicy -b /usr/bin/perl -e "alarm shift; exec @ARGV" 900 $APP \
    --background --factory-startup --python-exit-code 1 --python $HERE/measure_one.py -- $1 $2 $3 $4 2>>stderr.log \
    | grep '^VOXREC ' | sed 's/^VOXREC //')
  echo "$(date +%T) $spec -> ${line:-FAILED}" >> run.log
  [[ -n $line ]] && echo $line >> results.jsonl
done
echo DONE >> run.log
