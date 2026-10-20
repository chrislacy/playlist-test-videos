"""Decide from a page's HTML whether the page holds a video.

Follows the rules of the Playlist Leanback page scanner in Brave Core, so a feed built
with it lists the articles the app is likely to accept. The app probes each article again
when it plays, so the app's answer is the one that counts. test_mediascan.py runs this
over the fixture pages and checks the answers in expected.json.

Like the app, it reads the HTML without running script, so video that script builds after
the page loads is not seen.
"""
import html.parser
import json
import re
from urllib.parse import parse_qs, urljoin, urlsplit

MAX_CHARS = 1 << 20

# Lower index wins when two ways to play a video are equally good.
EVIDENCE_ORDER = [
    "DIRECT_MEDIA_URL",
    "SCHEMA_VIDEO_OBJECT",
    "OG_VIDEO",
    "OG_TYPE_VIDEO",
    "TWITTER_PLAYER",
    "KNOWN_PROVIDER_IFRAME",
    "VIDEO_TAG",
    "OEMBED_PROVIDER",
]

VIDEO_PROVIDER_HOSTS = (
    "player.vimeo.com",
    "dailymotion.com",
    "geo.dailymotion.com",
    "player.twitch.tv",
    "rumble.com",
    "streamable.com",
    "fast.wistia.net",
    "players.brightcove.net",
    "content.jwplatform.com",
    "cdn.jwplayer.com",
    "facebook.com",
)
OEMBED_VIDEO_HOSTS = ("youtube.com", "vimeo.com", "dailymotion.com")
SKIPPED_TAGS = ("noscript", "template", "style")

YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]{11}")
ISO_DURATION = re.compile(
    r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?", re.IGNORECASE
)


def parse_duration(text):
    """Seconds from an ISO 8601 duration ("PT4M13S") or a count of seconds; 0 otherwise."""
    if not text:
        return 0
    text = text.strip()
    if text.isdigit():
        return int(text)
    m = ISO_DURATION.fullmatch(text)
    if not m:
        return 0
    days, hours, minutes, seconds = m.groups()
    return int(
        int(days or 0) * 86400
        + int(hours or 0) * 3600
        + int(minutes or 0) * 60
        + float(seconds or 0)
    )


def host_of(url):
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def path_of(url):
    try:
        return urlsplit(url).path
    except ValueError:
        return ""


def resolve_https(base, reference):
    """Resolves a reference against the page, keeping only HTTPS results."""
    if not isinstance(reference, str) or not reference.strip():
        return None
    try:
        resolved = urlsplit(urljoin(base, reference.strip()))
    except ValueError:
        return None
    if resolved.scheme.lower() != "https" or not resolved.hostname:
        return None
    return resolved.geturl()


def query_param(url, name):
    try:
        values = parse_qs(urlsplit(url).query).get(name)
    except ValueError:
        return None
    return values[0] if values else None


def direct_media_kind(url):
    """MP4, WEB (webm, HLS) for a URL that is a playable media file; otherwise None."""
    if not url or not host_of(url):
        return None
    path = path_of(url).lower()
    # "File:Clip.webm" on a wiki is a page about a file, not the file.
    if ":" in path.rsplit("/", 1)[-1]:
        return None
    if path.endswith((".mp4", ".m4v")):
        return "MP4"
    if path.endswith((".webm", ".m3u8")):
        return "WEB"
    return None


def youtube_video_id(url):
    host = host_of(url)
    if host is None:
        return None
    path = path_of(url)
    video_id = None
    if host == "youtu.be":
        video_id = path[1:] if path.startswith("/") else path
    elif host in ("youtube.com", "m.youtube.com", "youtube-nocookie.com"):
        if path.startswith("/embed/"):
            video_id = path[len("/embed/"):]
        elif path.startswith("/v/"):
            video_id = path[len("/v/"):]
        elif path == "/watch":
            video_id = query_param(url, "v")
    if video_id is None:
        return None
    video_id = video_id.split("/", 1)[0]
    if video_id == "videoseries" or not YOUTUBE_ID.fullmatch(video_id):
        return None
    return video_id


class Embed:
    def __init__(self, kind="WEB", play_url=None, thumbnail=None):
        self.kind = kind
        self.play_url = play_url  # None means the article itself is what to load
        self.thumbnail = thumbnail


def classify_embed(url):
    """What a URL that names a player or video service stands for, or None."""
    if not url:
        return None
    video_id = youtube_video_id(url)
    if video_id:
        return Embed(
            "YOUTUBE",
            f"https://www.youtube.com/watch?v={video_id}",
            f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
        )
    host = host_of(url)
    if host is None:
        return None
    path = path_of(url)
    if host == "embed.ted.com" and path.startswith("/talks/") and len(path) > 7:
        return Embed("TED", "https://www.ted.com" + path)
    for provider in VIDEO_PROVIDER_HOSTS:
        if host == provider or host.endswith("." + provider):
            if host == "facebook.com" and not path.startswith("/plugins/video"):
                continue
            if host == "rumble.com" and not path.startswith("/embed"):
                continue
            if host == "streamable.com" and not path.startswith("/e/"):
                continue
            if host == "dailymotion.com" and not path.startswith("/embed"):
                continue
            return Embed()
    return None


def is_oembed_video_host(url):
    host = host_of(url)
    if host is None:
        return False
    for provider in OEMBED_VIDEO_HOSTS:
        if host == provider or host.endswith("." + provider):
            return "oembed" in path_of(url)
    return False


def collapse_spaces(text):
    return " ".join(text.split())


class Candidate:
    def __init__(self, evidence):
        self.evidence = evidence
        self.kind = "WEB"
        self.play_url = None
        self.title = None
        self.thumbnail = None
        self.duration = 0

    def play_rank(self, article_url):
        """Lower is better: a YouTube page is the most reliable thing to load, then a file."""
        if self.kind == "YOUTUBE":
            return 0
        if self.kind == "TED":
            return 1
        if article_url != self.play_url:
            return 2 if self.kind == "MP4" else 3
        return 4


def no_video():
    return {
        "status": "NO_VIDEO",
        "evidence": None,
        "kind": None,
        "playUrl": None,
        "title": None,
        "thumbnail": None,
        "durationSeconds": 0,
    }


def direct_media_info(url):
    """The answer for a URL that is itself a video file, or None if it is not one."""
    kind = direct_media_kind(url)
    if kind is None:
        return None
    info = no_video()
    info.update(status="VIDEO", evidence="DIRECT_MEDIA_URL", kind=kind, playUrl=url)
    return info


class _Scan(html.parser.HTMLParser):
    def __init__(self, article_url):
        super().__init__(convert_charrefs=True)
        self.url = article_url
        self.candidates = []
        self.og_title = self.twitter_title = self.doc_title = None
        self.og_image = None
        self.og_duration = None
        self.og_type_video = False
        self.og_video_is_flash = False
        self.music_page = False
        self.microdata_video = False
        self.microdata_duration = None
        self.microdata_name = None
        self._skip_tag = None
        self._script = None
        self._script_is_json_ld = False
        self._title = None
        self._video_tag = None
        self._video_source = None
        self._video_mp4_source = None

    def handle_starttag(self, tag, attr_list):
        if self._skip_tag:
            return
        attrs = {}
        for name, value in attr_list:
            attrs.setdefault(name.lower(), "" if value is None else value)
        if tag in SKIPPED_TAGS:
            self._skip_tag = tag
        elif tag == "script":
            self._script = []
            self._script_is_json_ld = "ld+json" in attrs.get("type", "").lower()
        elif tag == "title":
            self._title = []
        elif tag == "meta":
            self._meta(attrs)
        elif tag == "link":
            self._link(attrs)
        elif tag == "iframe":
            self._iframe(attrs)
        elif tag == "video":
            self._finish_video()
            self._video_tag = attrs
        elif tag == "source":
            if self._video_tag is not None:
                self._video_source_tag(attrs)
        else:
            item_type = attrs.get("itemtype")
            if item_type and item_type.lower().endswith("/videoobject"):
                self.microdata_video = True

    def handle_endtag(self, tag):
        if self._skip_tag:
            if tag == self._skip_tag:
                self._skip_tag = None
            return
        if tag == "script":
            self._end_script()
        elif tag == "title":
            if self._title is not None and self.doc_title is None:
                self.doc_title = collapse_spaces("".join(self._title))
            self._title = None
        elif tag == "video":
            self._finish_video()

    def handle_data(self, data):
        if self._skip_tag:
            return
        if self._script is not None:
            self._script.append(data)
        elif self._title is not None:
            self._title.append(data)

    def _end_script(self):
        body = "".join(self._script or [])
        if self._script_is_json_ld:
            self._add_json_ld(body)
        self._script = None
        self._script_is_json_ld = False

    def _meta(self, attrs):
        key = attrs.get("property")
        if key is None:
            key = attrs.get("name")
        if key is None:
            key = attrs.get("itemprop")
        content = attrs.get("content")
        if key is None or content is None:
            return
        key = key.lower()
        if key == "og:title":
            if self.og_title is None:
                self.og_title = collapse_spaces(content)
        elif key == "twitter:title":
            if self.twitter_title is None:
                self.twitter_title = collapse_spaces(content)
        elif key in ("og:image", "og:image:secure_url"):
            if self.og_image is None:
                self.og_image = resolve_https(self.url, content)
        elif key == "og:type":
            og_type = content.strip().lower()
            if og_type.startswith("video"):
                self.og_type_video = True
            if og_type.startswith("music"):
                self.music_page = True
        elif key in ("og:video", "og:video:url", "og:video:secure_url"):
            self._add_og_video(content)
        elif key == "og:video:type":
            if "shockwave-flash" in content.lower():
                self.og_video_is_flash = True
        elif key in ("og:video:duration", "video:duration"):
            self.og_duration = content
        elif key == "twitter:player":
            self._add_player("TWITTER_PLAYER", content)
        elif key == "twitter:player:stream":
            self._add_stream("TWITTER_PLAYER", content)
        elif key == "duration":
            if self.microdata_duration is None:
                self.microdata_duration = content
        if attrs.get("itemprop", "").lower() == "name" and self.microdata_name is None:
            self.microdata_name = collapse_spaces(content)

    def _add_og_video(self, content):
        url = resolve_https(self.url, content)
        if url is None or path_of(url).lower().endswith(".swf"):
            return
        self._add_player("OG_VIDEO", content)

    def _add_player(self, evidence, reference):
        """A URL named as a player: a known service, a media file, or some page's own embed."""
        url = resolve_https(self.url, reference)
        if url is None:
            return
        candidate = Candidate(evidence)
        direct = direct_media_kind(url)
        embed = classify_embed(url)
        if embed is not None:
            candidate.kind = embed.kind
            candidate.play_url = embed.play_url or self.url
            candidate.thumbnail = embed.thumbnail
        elif direct is not None:
            candidate.kind = direct
            candidate.play_url = url
        else:
            candidate.play_url = self.url
        self.candidates.append(candidate)

    def _add_stream(self, evidence, reference):
        url = resolve_https(self.url, reference)
        direct = direct_media_kind(url)
        if direct is None:
            return
        candidate = Candidate(evidence)
        candidate.kind = direct
        candidate.play_url = url
        self.candidates.append(candidate)

    def _link(self, attrs):
        link_type, href, rel = attrs.get("type"), attrs.get("href"), attrs.get("rel")
        if link_type is None or href is None or rel is None:
            return
        if "alternate" not in rel.lower() or "oembed" not in link_type.lower():
            return
        url = resolve_https(self.url, href)
        if url is None or not is_oembed_video_host(url):
            return
        candidate = Candidate("OEMBED_PROVIDER")
        named = classify_embed(query_param(url, "url"))
        if named is not None and named.play_url:
            candidate.kind = named.kind
            candidate.play_url = named.play_url
            candidate.thumbnail = named.thumbnail
        else:
            candidate.play_url = self.url
        self.candidates.append(candidate)

    def _iframe(self, attrs):
        for name in ("src", "data-src", "data-lazy-src"):
            reference = attrs.get(name)
            if reference is None:
                continue
            embed = classify_embed(resolve_https(self.url, reference))
            if embed is None:
                continue
            candidate = Candidate("KNOWN_PROVIDER_IFRAME")
            candidate.kind = embed.kind
            candidate.play_url = embed.play_url or self.url
            candidate.thumbnail = embed.thumbnail
            self.candidates.append(candidate)
            return

    def _video_source_tag(self, attrs):
        url = resolve_https(self.url, attrs.get("src"))
        if url is None:
            return
        source_type = attrs.get("type")
        is_mp4 = direct_media_kind(url) == "MP4" or (
            source_type is not None and "mp4" in source_type.lower()
        )
        if self._video_source is None:
            self._video_source = url
        if is_mp4 and self._video_mp4_source is None:
            self._video_mp4_source = url

    def _finish_video(self):
        """Ends the open video element, if any, and records it unless it is decoration."""
        if self._video_tag is None:
            return
        tag = self._video_tag
        self._video_tag = None
        own = resolve_https(self.url, tag.get("src"))
        source = own if own is not None else self._video_source
        if own is None and self._video_mp4_source is not None:
            source = self._video_mp4_source
        self._video_source = None
        self._video_mp4_source = None

        # A silent looping clip without controls is page decoration, not something to play.
        if "loop" in tag and "muted" in tag and "controls" not in tag:
            return
        candidate = Candidate("VIDEO_TAG")
        if source is not None:
            candidate.kind = direct_media_kind(source) or "WEB"
            candidate.play_url = source
        else:
            candidate.play_url = self.url
        candidate.thumbnail = resolve_https(self.url, tag.get("poster"))
        self.candidates.append(candidate)

    def _add_json_ld(self, text):
        text = text.strip()
        if not text:
            return
        try:
            root = json.loads(text)
        except ValueError:
            return  # malformed structured data is common
        self._walk_json_ld(root, 0)

    def _walk_json_ld(self, node, depth):
        if depth > 8:
            return
        if isinstance(node, list):
            for child in node[:200]:
                self._walk_json_ld(child, depth + 1)
        elif isinstance(node, dict):
            if _has_type(node, "VideoObject"):
                self._add_video_object(node)
            for child in list(node.values())[:200]:
                if isinstance(child, (dict, list)):
                    self._walk_json_ld(child, depth + 1)

    def _add_video_object(self, obj):
        candidate = Candidate("SCHEMA_VIDEO_OBJECT")
        candidate.play_url = self.url
        embed = classify_embed(resolve_https(self.url, obj.get("embedUrl")))
        content_url = resolve_https(self.url, obj.get("contentUrl"))
        direct = direct_media_kind(content_url)
        if embed is not None and embed.play_url:
            candidate.kind = embed.kind
            candidate.play_url = embed.play_url
            candidate.thumbnail = embed.thumbnail
        elif direct is not None:
            candidate.kind = direct
            candidate.play_url = content_url
        name = obj.get("name")
        if isinstance(name, str) and name:
            candidate.title = collapse_spaces(name)
        duration = obj.get("duration")
        candidate.duration = parse_duration(duration if isinstance(duration, str) else "")
        thumbnail = self._json_ld_image(obj.get("thumbnailUrl"))
        if thumbnail is not None:
            candidate.thumbnail = thumbnail
        self.candidates.append(candidate)

    def _json_ld_image(self, value):
        if isinstance(value, str):
            return resolve_https(self.url, value)
        if isinstance(value, list):
            for item in value:
                url = self._json_ld_image(item)
                if url is not None:
                    return url
        elif isinstance(value, dict):
            return self._json_ld_image(value.get("url"))
        return None

    def result(self):
        if self._script is not None:
            self._end_script()
        candidates = self.candidates
        if self.microdata_video:
            candidate = Candidate("SCHEMA_VIDEO_OBJECT")
            candidate.play_url = self.url
            candidate.title = self.microdata_name
            candidate.duration = parse_duration(self.microdata_duration)
            candidates.append(candidate)
        if self.og_type_video:
            candidate = Candidate("OG_TYPE_VIDEO")
            candidate.play_url = self.url
            candidates.append(candidate)
        if self.og_video_is_flash:
            candidates[:] = [c for c in candidates if c.evidence != "OG_VIDEO"]
        if self.music_page:
            # Audio services offer a widget as the page's "video" player. Only a player that
            # is a known video service or a media file is video on such a page.
            candidates[:] = [
                c
                for c in candidates
                if not (
                    c.evidence in ("OG_VIDEO", "TWITTER_PLAYER") and c.play_rank(self.url) == 4
                )
            ]
        if not candidates:
            return no_video()

        best = candidates[0]
        for candidate in candidates[1:]:
            if self._better(candidate, best):
                best = candidate
        title, thumbnail, duration = best.title, best.thumbnail, best.duration
        for candidate in candidates:
            title = title or candidate.title
            duration = duration or candidate.duration
            thumbnail = thumbnail or candidate.thumbnail
        if duration == 0 and self.og_duration:
            duration = parse_duration(self.og_duration)
        title = title or self.og_title or self.twitter_title or self.doc_title
        if self.og_image is not None:
            derived = thumbnail is not None and thumbnail.startswith("https://i.ytimg.com/")
            if thumbnail is None or derived:
                thumbnail = self.og_image
        return {
            "status": "VIDEO",
            "evidence": best.evidence,
            "kind": best.kind,
            "playUrl": best.play_url,
            "title": collapse_spaces(title) if title else None,
            "thumbnail": thumbnail,
            "durationSeconds": duration,
        }

    def _better(self, a, b):
        rank_a, rank_b = a.play_rank(self.url), b.play_rank(self.url)
        if rank_a != rank_b:
            return rank_a < rank_b
        return EVIDENCE_ORDER.index(a.evidence) < EVIDENCE_ORDER.index(b.evidence)


def _has_type(obj, wanted):
    value = obj.get("@type")
    if isinstance(value, str):
        return value.lower() == wanted.lower()
    if isinstance(value, list):
        return any(isinstance(v, str) and v.lower() == wanted.lower() for v in value)
    return False


def scan(article_url, html_text):
    """What the page at article_url holds: a dict with status VIDEO or NO_VIDEO."""
    if not html_text:
        return no_video()
    scanner = _Scan(article_url)
    scanner.feed(html_text[:MAX_CHARS])
    scanner.close()
    return scanner.result()
