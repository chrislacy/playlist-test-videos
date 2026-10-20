#!/usr/bin/env python3
"""Build feed.xml: the articles of some RSS or Atom feeds whose pages hold a video.

Reads feed-sources.json, follows each entry to its article, scans the article's HTML for
video (see mediascan.py), and writes the articles that have one as an RSS 2.0 feed. The
feed carries titles and links only, never article text. Articles found by an earlier run
stay in the feed until they are older than --max-age-days or pushed out by --max-items.

  scripts/build_feed.py [--sources feed-sources.json] [--out feed.xml] [--dry-run]

A source with "linkFrom": "description" names its article in the first link of the entry's
description that leaves the source's own site (an aggregator such as Techmeme links to
itself in <link>).
"""
import argparse
import concurrent.futures
import datetime
import email.utils
import html.parser
import json
import os
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

import mediascan

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USER_AGENT = "playlist-test-videos-feed-builder (+https://github.com/chrislacy/playlist-test-videos)"
MAX_BODY_BYTES = 1 << 20
MEDIA_NS = "http://search.yahoo.com/mrss/"
ATOM_NS = "http://www.w3.org/2005/Atom"
MEDIA_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm",
               ".m3u8": "application/vnd.apple.mpegurl"}

ET.register_namespace("media", MEDIA_NS)
ET.register_namespace("atom", ATOM_NS)


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != "https":
            raise urllib.error.URLError("redirect leaves HTTPS")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# No cookie handler: requests carry no cookies, as in the app.
_OPENER = urllib.request.build_opener(_HttpsOnlyRedirects)


def fetch(url, timeout):
    """(final_url, content_type, text) of an HTTPS page; raises on any failure."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with _OPENER.open(request, timeout=timeout) as response:
        body = response.read(MAX_BODY_BYTES)
        charset = response.headers.get_content_charset() or "utf-8"
        return (response.geturl(), response.headers.get_content_type(),
                body.decode(charset, errors="replace"))


def local(tag):
    return tag.rsplit("}", 1)[-1]


def child_text(element, name):
    for child in element:
        if local(child.tag) == name and child.text:
            return child.text.strip()
    return None


def parse_date(text):
    if not text:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def parse_entries(xml_text):
    """Entries of an RSS or Atom document as dicts of title, link, description, published."""
    root = ET.fromstring(xml_text)
    entries = []
    for element in root.iter():
        name = local(element.tag)
        if name not in ("item", "entry"):
            continue
        link = child_text(element, "link")
        if name == "entry":
            link = None
            for child in element:
                if local(child.tag) == "link" and child.get("rel", "alternate") == "alternate":
                    link = child.get("href")
                    break
        entries.append({
            "title": child_text(element, "title") or "",
            "link": link,
            "description": child_text(element, "description")
            or child_text(element, "summary") or child_text(element, "content") or "",
            "published": parse_date(
                child_text(element, "pubDate") or child_text(element, "published")
                or child_text(element, "updated")),
        })
    return entries


class _Anchors(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.hrefs.append(href)


TRACKING_PARAMS = {"st", "reflink", "ref", "fbclid", "gclid", "mc_cid", "mc_eid"}
SECRET_WORDS = ("token", "auth", "key", "sig", "session")


def clean_article_url(url):
    """The article URL as HTTPS without tracking parameters; None if it looks like it
    carries a credential (a gifted-article link), since the feed is public."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    kept = []
    for pair in parts.query.split("&") if parts.query else []:
        name = pair.split("=", 1)[0].lower()
        if any(word in name for word in SECRET_WORDS):
            return None
        if not name.startswith("utm_") and name not in TRACKING_PARAMS:
            kept.append(pair)
    return parts._replace(scheme="https", query="&".join(kept), fragment="").geturl()


def article_link(entry, source):
    """The URL of the article an entry is about, as HTTPS; None if there is none."""
    link = entry["link"]
    if source.get("linkFrom") == "description":
        own_hosts = {mediascan.host_of(link), mediascan.host_of(source["url"])}
        anchors = _Anchors()
        anchors.feed(entry["description"])
        # A publisher's homepage (its name beside the headline) is not the article.
        link = next((h for h in anchors.hrefs
                     if mediascan.host_of(h) not in own_hosts
                     and urlsplit(h).path not in ("", "/")), None)
    if not link:
        return None
    # Shorts are vertical clips of a minute or less, which look poor on a TV.
    if mediascan.host_of(link) == "youtube.com" and urlsplit(link).path.startswith("/shorts/"):
        return None
    return clean_article_url(link)


def inspect(url, timeout):
    """(info, reason): info is mediascan's answer, or None with the reason it is unknown."""
    info = mediascan.direct_media_info(url)
    if info:
        return info, None
    try:
        final_url, content_type, text = fetch(url, timeout)
    except (urllib.error.URLError, OSError, ValueError) as error:
        return None, str(getattr(error, "reason", error))
    if "html" not in content_type:
        return None, f"not a page ({content_type})"
    return mediascan.scan(final_url, text), None


def read_feed(path):
    """Items of an earlier feed.xml, as the dicts build_item writes."""
    if not os.path.exists(path):
        return []
    items = []
    for item in ET.parse(path).getroot().iter("item"):
        source = item.find("source")
        enclosure = item.find("enclosure")
        thumbnail = item.find(f"{{{MEDIA_NS}}}thumbnail")
        items.append({
            "title": child_text(item, "title") or "",
            "link": child_text(item, "link"),
            "published": parse_date(child_text(item, "pubDate")),
            "source_name": source.text if source is not None else None,
            "source_url": source.get("url") if source is not None else None,
            "evidence": child_text(item, "category"),
            "enclosure": (enclosure.get("url"), enclosure.get("type"))
            if enclosure is not None else None,
            "thumbnail": thumbnail.get("url") if thumbnail is not None else None,
        })
    return [i for i in items if i["link"]]


def build_item(entry, source, article, info):
    path = mediascan.path_of(info["playUrl"] or "").lower()
    enclosure = None
    if info["kind"] in ("MP4", "WEB") and info["playUrl"] != article:
        suffix = os.path.splitext(path)[1]
        if suffix in MEDIA_TYPES:
            enclosure = (info["playUrl"], MEDIA_TYPES[suffix])
    return {
        "title": entry["title"],
        "link": article,
        "published": entry["published"] or datetime.datetime.now(datetime.timezone.utc),
        "source_name": source["name"],
        "source_url": source["url"],
        "evidence": info["evidence"],
        "enclosure": enclosure,
        "thumbnail": info["thumbnail"],
    }


def write_feed(path, config, items):
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = config["title"]
    ET.SubElement(channel, "link").text = config["link"]
    ET.SubElement(channel, "description").text = config["description"]
    ET.SubElement(channel, "language").text = "en"
    # The newest item's date, so rebuilding with nothing new leaves the file unchanged.
    newest = max((i["published"] for i in items), default=datetime.datetime.fromtimestamp(
        0, datetime.timezone.utc))
    ET.SubElement(channel, "lastBuildDate").text = email.utils.format_datetime(newest)
    ET.SubElement(channel, f"{{{ATOM_NS}}}link", {
        "href": config["feedUrl"], "rel": "self", "type": "application/rss+xml"})
    for item in items:
        element = ET.SubElement(channel, "item")
        ET.SubElement(element, "title").text = item["title"]
        ET.SubElement(element, "link").text = item["link"]
        ET.SubElement(element, "guid", {"isPermaLink": "true"}).text = item["link"]
        ET.SubElement(element, "pubDate").text = email.utils.format_datetime(item["published"])
        if item["source_name"]:
            ET.SubElement(element, "source", {"url": item["source_url"] or ""}).text = (
                item["source_name"])
        if item["evidence"]:
            ET.SubElement(element, "category").text = item["evidence"]
        if item["enclosure"]:
            ET.SubElement(element, "enclosure", {
                "url": item["enclosure"][0], "type": item["enclosure"][1], "length": "0"})
        if item["thumbnail"]:
            ET.SubElement(element, f"{{{MEDIA_NS}}}thumbnail", {"url": item["thumbnail"]})
    ET.indent(rss)
    temporary = path + ".tmp"
    ET.ElementTree(rss).write(temporary, encoding="utf-8", xml_declaration=True)
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--sources", default=os.path.join(ROOT, "feed-sources.json"))
    parser.add_argument("--out", default=os.path.join(ROOT, "feed.xml"))
    parser.add_argument("--max-items", type=int, help="default: maxItems in the sources file, else 50")
    parser.add_argument("--max-age-days", type=int, default=30)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--dry-run", action="store_true", help="print results; do not write")
    args = parser.parse_args()

    with open(args.sources) as f:
        config = json.load(f)
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(days=args.max_age_days)
    kept = {i["link"]: i for i in read_feed(args.out) if i["published"] and i["published"] >= cutoff}

    jobs = {}
    for source in config["sources"]:
        try:
            _, _, text = fetch(source["url"], args.timeout)
            entries = parse_entries(text)
        except (urllib.error.URLError, OSError, ValueError, ET.ParseError) as error:
            print(f"SOURCE FAILED  {source['name']}: {error}", file=sys.stderr)
            continue
        linked = 0
        for entry in entries:
            if linked >= source.get("maxEntries", len(entries)):
                break
            article = article_link(entry, source)
            if article and article not in kept and article not in jobs:
                jobs[article] = (entry, source)
                linked += 1
        print(f"{source['name']}: {len(entries)} entries, {linked} new articles to inspect")

    found = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(inspect, url, args.timeout): url for url in jobs}
        for future in concurrent.futures.as_completed(futures):
            url = futures[future]
            entry, source = jobs[url]
            info, reason = future.result()
            if info is None:
                print(f"unreachable  {url}  ({reason})")
            elif info["status"] != "VIDEO":
                print(f"no video     {url}")
            else:
                found += 1
                print(f"VIDEO        {url}  {info['evidence']} {info['kind']}")
                kept[url] = build_item(entry, source, url, info)

    max_items = args.max_items or config.get("maxItems", 50)
    items = sorted(kept.values(), key=lambda i: i["published"], reverse=True)[:max_items]
    print(f"{found} new with video; {len(items)} in the feed")
    if not args.dry_run:
        write_feed(args.out, config, items)
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
