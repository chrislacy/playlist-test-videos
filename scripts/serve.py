#!/usr/bin/env python3
"""Serve this repository over HTTP for browsing the pages on a desktop.

The Playlist probe only fetches HTTPS pages, so a device check needs the pages on
an HTTPS host such as GitHub Pages. This server is for looking at the pages.
"""
import http.server
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".m3u8": "application/vnd.apple.mpegurl",
        ".ts": "video/mp2t",
        ".webm": "video/webm",
        ".mp4": "video/mp4",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"Serving {ROOT} on http://localhost:{port}/")
    http.server.ThreadingHTTPServer(("", port), Handler).serve_forever()
