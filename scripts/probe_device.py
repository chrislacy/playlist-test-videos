#!/usr/bin/env python3
"""Probe every page of a published copy of this repository on a device.

Needs the pages on an HTTPS host (for example GitHub Pages) and a debug build of
Brave with Playlist Leanback installed. It starts the Playlist activity with the
PROBE_URLS extra, reads the probe's log lines, and compares each with
expected.json.

  scripts/probe_device.py --base https://USER.github.io/playlist-test-videos/ \
      [--serial emulator-5554] [--package com.brave.browser_default]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

ACTIVITY = "org.chromium.chrome.browser.playlist.leanback.PlaylistLeanbackActivity"
EXTRA = "org.chromium.chrome.browser.playlist.leanback.PROBE_URLS"
LINE = re.compile(
    r"PlaylistLeanbackProbe: (?P<url>\S+) -> (?P<status>[A-Z_]+) (?P<evidence>[A-Z_]+) "
    r"(?P<kind>[A-Z_]+) play=(?P<play>\S+) dur=(?P<dur>\d+) title=(?P<title>.*?) reason=(?P<reason>.*)$"
)


def adb(serial, *args):
    cmd = ["adb"] + (["-s", serial] if serial else []) + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, help="HTTPS URL of the published repository root")
    parser.add_argument("--serial")
    parser.add_argument("--package", default="com.brave.browser_default")
    parser.add_argument("--wait", type=int, default=60, help="seconds to wait for results")
    args = parser.parse_args()
    if not args.base.startswith("https://"):
        sys.exit("--base must be an https URL: the probe only fetches HTTPS pages")
    base = args.base.rstrip("/") + "/"

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    expected = json.load(open(os.path.join(root, "expected.json")))["pages"]
    # A fresh query keeps an earlier run's cached answer from being reused.
    stamp = str(int(time.time()))
    urls = {base + e["path"] + "?run=" + stamp: e for e in expected}

    adb(args.serial, "shell", "am", "force-stop", args.package)
    adb(args.serial, "logcat", "-c")
    adb(args.serial, "shell", "am", "start", "-n", f"{args.package}/{ACTIVITY}",
        "--es", EXTRA, "'" + " ".join(urls) + "'")

    results = {}
    deadline = time.time() + args.wait
    while time.time() < deadline and len(results) < len(urls):
        time.sleep(2)
        for line in adb(args.serial, "logcat", "-d").splitlines():
            m = LINE.search(line)
            if m and m["url"] in urls:
                results[m["url"]] = m

    failures = 0
    for url, e in urls.items():
        m = results.get(url)
        if m is None:
            print(f"MISSING  {e['path']}")
            failures += 1
            continue
        want_play = e.get("playUrl")
        if want_play == "@page":
            want_play = base + e["path"]
        elif want_play and not want_play.startswith("http"):
            want_play = base + want_play
        problems = []
        for field in ("status", "evidence", "kind"):
            if field in e and e[field] != m[field]:
                problems.append(f"{field} {m[field]} != {e[field]}")
        got_play = m["play"].split("?run=")[0]
        if want_play and got_play != want_play:
            problems.append(f"playUrl {got_play} != {want_play}")
        if problems:
            failures += 1
        print(("FAIL     " if problems else "ok       ") + e["path"] + ("  " + "; ".join(problems) if problems else ""))
    print(f"{len(urls) - failures}/{len(urls)} pages as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
