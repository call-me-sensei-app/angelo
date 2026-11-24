# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How often a live session page reads a picture that never becomes whole, on a REAL clock (not a host test: a probe).

VM CI on 0ce7c3a7bf74 (Linux) read such a picture 217 times in ten minutes in
``test_native_session_page_pending``, whose harness moves a simulated clock through minutes in a fraction of a real
second while ``change_clock`` judges the file's real stamp against the real wall clock. This probe runs the same
scenario (one listed picture never whole, a page open, the chat changing every 0.5 s, ``Pages.pump`` every 0.1 s) with
``time.monotonic`` and real sleeps, so the two clocks agree, and prints the reads. Run against any checkout's
``scripts/addons_core`` (``--addons``), so one commit's live-page code can be compared with another's.

``--volume`` makes ``angelo_change_clock`` judge every stamp as one kind of volume, so Linux's verdicts can be seen on
a Mac (whose APFS is fine-grained and settles at once): ``fine`` (APFS), ``linux`` (ext4 and the other disk file
systems, a 16 ms tick), ``unknown`` (a share, FUSE, FAT: 2 s either side of the clock) or ``never`` (a verdict that
never settles: the bound must hold for any clock).

    python3.13 probe_live_page_waits.py --addons <checkout>/scripts/addons_core --volume linux --seconds 90

Prints one JSON line: reads, locates, publishes, what the page shows, and the code's commit when given.
"""

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

WHOLE = b"\x89PNG\r\n\x1a\n" + b"pixels" + b"\x00\x00\x00\x00IEND\xaeB`\x82"
HALF = WHOLE[:14]   # A capture reserved and never finished: never a whole picture.


class Site:
    def __init__(self, marks=None):
        self.url, self.viewers, self.published, self.closed = "http://127.0.0.1:1/s/p/", 0, [], False
        self.scene_feed, self.pictures = None, {}
        from angelo_agent.session_page_site import _whole
        self._whole = _whole

    def publish(self, state):
        self.published.append({key: list(state.get(key) or []) for key in ("pictures", "unavailable")})

    def publish_picture(self, identity, data):
        if not self._whole(data):
            return False
        self.pictures[identity] = data
        return True

    def has_picture(self, identity):
        return identity in self.pictures

    def close(self):
        self.closed = True


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--addons", required=True, help="a checkout's scripts/addons_core")
    parser.add_argument("--volume", choices=("fine", "linux", "unknown", "never"), required=True)
    parser.add_argument("--seconds", type=float, default=90.0, help="real seconds to run (give-up is at 60 s)")
    parser.add_argument("--commit", default="", help="the checkout's commit, echoed in the result")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.addons).resolve()))
    from angelo_agent import change_clock, reference_images, session_page, session_page_pending as pending
    volumes = sys.modules["angelo_change_clock_volumes"]
    kinds = {"fine": volumes.FINE, "linux": volumes.LINUX_DISK, "unknown": volumes.UNKNOWN}

    folder = Path(tempfile.mkdtemp(prefix="probe-live-page-"))
    (folder / "view-1.png").write_bytes(HALF)
    reads, locates = [], []
    real_read = reference_images.read

    def attached_image(identity):
        locates.append(identity)
        return folder / (identity + ".png")
    controller = SimpleNamespace(state={"id": "c1", "messages": []}, version=1, references=SimpleNamespace(
        attached_image=attached_image, image_limit=5 * 1024 * 1024))
    def read(store, handle):   # The one read of a picture's bytes.
        reads.append(1)
        return real_read(store, handle)
    patches = [mock.patch.object(reference_images, "read", read)]
    if args.volume == "never":
        patches.append(mock.patch.object(change_clock, "settled", lambda witness, *a, **k: False))
    else:
        patches.append(mock.patch.object(change_clock._posix, "volume", lambda device, stamp: kinds[args.volume]))
    for patch in patches:
        patch.start()
    try:
        pages = session_page.Pages(
            make_site=Site, clock=time.monotonic, retry=pending.retry,
            build=lambda c, entry: pending.shown(c, entry, {"pictures": ["view-1"]}))
        pages.link(controller, "probe")
        site = pages.site("c1")
        site.viewers = 1
        start = time.monotonic()
        tick = 0
        while time.monotonic() - start < args.seconds:
            if tick % 5 == 0:
                controller.version += 1   # The chat changes every 0.5 s: a build each time the page may publish.
            pages.pump(lambda identity: controller)
            tick += 1
            time.sleep(0.1)
        last = site.published[-1] if site.published else {}
        print(json.dumps({"commit": args.commit, "volume": args.volume, "seconds": round(time.monotonic() - start, 1),
                          "reads": len(reads), "locates": len(locates), "publishes": len(site.published),
                          "pictures": last.get("pictures"), "unavailable": last.get("unavailable")}))
    finally:
        for patch in patches:
            patch.stop()
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
