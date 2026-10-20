#!/usr/bin/env python3
"""Checks the feed builder's parsing, link choice, and output without any network."""
import datetime
import os
import tempfile
import unittest

import build_feed

TECHMEME = """<?xml version="1.0"?><rss version="2.0"><channel><title>Techmeme</title>
<item><title>With image</title><link>https://www.techmeme.com/1#a</link>
<description><![CDATA[<A HREF="https://news.example/story-1"><IMG SRC="http://x/i.jpg"></A>
<P><A HREF="https://www.techmeme.com/1#a">pml</A> Author / <A HREF="http://news.example/">News</A>:<BR>
<B><A HREF="https://news.example/story-1">With image</A></B></P>]]></description>
<pubDate>Tue, 20 Oct 2026 02:15:01 -0400</pubDate></item>
<item><title>No image</title><link>https://www.techmeme.com/2#a</link>
<description><![CDATA[<P><A HREF="https://www.techmeme.com/2#a">pml</A> Author / <A HREF="https://news.example/">News</A>:<BR>
<B><A HREF="http://news.example/story-2?utm_source=x&amp;id=7">No image</A></B></P>]]></description>
<pubDate>Tue, 20 Oct 2026 01:00:00 -0400</pubDate></item></channel></rss>"""

SOURCE = {"name": "Techmeme", "url": "https://www.techmeme.com/feed.xml", "linkFrom": "description"}


class ArticleLinkTest(unittest.TestCase):
    def test_description_names_the_article(self):
        first, second = build_feed.parse_entries(TECHMEME)
        self.assertEqual("https://news.example/story-1", build_feed.article_link(first, SOURCE))
        self.assertEqual("https://news.example/story-2?id=7", build_feed.article_link(second, SOURCE))

    def test_link_is_used_without_link_from(self):
        entry = {"link": "http://news.example/a?utm_medium=x", "description": ""}
        self.assertEqual("https://news.example/a", build_feed.article_link(entry, {"url": "https://x/"}))

    def test_youtube_shorts_are_skipped(self):
        source = {"url": "https://www.youtube.com/feeds/videos.xml?channel_id=x"}
        short = {"link": "https://www.youtube.com/shorts/abcdefghijk", "description": ""}
        watch = {"link": "https://www.youtube.com/watch?v=abcdefghijk", "description": ""}
        self.assertIsNone(build_feed.article_link(short, source))
        self.assertEqual(watch["link"], build_feed.article_link(watch, source))

    def test_atom_entries_are_read(self):
        atom = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Clip</title>'
                '<link rel="alternate" href="https://www.youtube.com/watch?v=abcdefghijk"/>'
                '<published>2026-10-19T21:52:27+00:00</published></entry></feed>')
        (entry,) = build_feed.parse_entries(atom)
        self.assertEqual("https://www.youtube.com/watch?v=abcdefghijk", entry["link"])
        self.assertEqual("Clip", entry["title"])
        self.assertEqual(21, entry["published"].hour)

    def test_credentials_in_a_url_drop_the_article(self):
        for url in ("https://news.example/a?accessToken=abc", "https://news.example/a?sig=1"):
            self.assertIsNone(build_feed.clean_article_url(url))

    def test_tracking_parameters_and_fragment_are_removed(self):
        self.assertEqual("https://news.example/a?p=2",
                         build_feed.clean_article_url("https://news.example/a?p=2&st=9&reflink=r#top"))


class FeedFileTest(unittest.TestCase):
    def test_written_feed_reads_back_unchanged(self):
        item = {
            "title": "A story", "link": "https://news.example/s",
            "published": datetime.datetime(2026, 10, 20, 6, 0, tzinfo=datetime.timezone.utc),
            "source_name": "Techmeme", "source_url": "https://www.techmeme.com/feed.xml",
            "evidence": "OG_VIDEO", "enclosure": ("https://news.example/c.mp4", "video/mp4"),
            "thumbnail": "https://news.example/t.jpg", "duration": 2471,
        }
        config = {"title": "T", "link": "https://x/", "description": "D", "feedUrl": "https://x/feed.xml"}
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "feed.xml")
            build_feed.write_feed(path, config, [item])
            self.assertEqual([item], build_feed.read_feed(path))
            with open(path) as f:
                first = f.read()
            build_feed.write_feed(path, config, build_feed.read_feed(path))
            with open(path) as f:
                self.assertEqual(first, f.read())


if __name__ == "__main__":
    unittest.main()
