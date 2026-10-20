#!/usr/bin/env python3
"""Checks mediascan against the fixture pages and the answers in expected.json."""
import json
import os
import unittest

import mediascan

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "https://fixtures.example.test/playlist-test-videos/"


class FixturePagesTest(unittest.TestCase):
    def test_every_page_matches_expected(self):
        with open(os.path.join(ROOT, "expected.json")) as f:
            pages = json.load(f)["pages"]
        for expected in pages:
            with self.subTest(page=expected["path"]):
                with open(os.path.join(ROOT, expected["path"]), encoding="utf-8") as f:
                    got = mediascan.scan(BASE + expected["path"], f.read())
                for field in ("status", "evidence", "kind", "durationSeconds", "title"):
                    if field in expected:
                        self.assertEqual(expected[field], got[field], field)
                if "playUrl" in expected:
                    want = expected["playUrl"]
                    if want == "@page":
                        want = BASE + expected["path"]
                    elif not want.startswith("https://"):
                        want = BASE + want
                    self.assertEqual(want, got["playUrl"])

    def test_direct_media_url(self):
        info = mediascan.direct_media_info("https://example.test/a/clip.mp4?x=1")
        self.assertEqual(("VIDEO", "DIRECT_MEDIA_URL", "MP4"), (info["status"], info["evidence"], info["kind"]))
        self.assertIsNone(mediascan.direct_media_info("https://example.test/wiki/File:Clip.webm"))
        self.assertIsNone(mediascan.direct_media_info("https://example.test/story.html"))

    def test_http_references_are_not_playable(self):
        page = '<meta property="og:video" content="http://example.test/a.mp4">'
        self.assertEqual("NO_VIDEO", mediascan.scan("https://example.test/s", page)["status"])

    def test_durations(self):
        self.assertEqual(253, mediascan.parse_duration("PT4M13S"))
        self.assertEqual(90, mediascan.parse_duration("90"))
        self.assertEqual(0, mediascan.parse_duration("soon"))


if __name__ == "__main__":
    unittest.main()
