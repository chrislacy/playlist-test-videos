# Playlist test pages

Small, synthetic pages and videos for testing whether a web page holds a playable video, as the Playlist Leanback prototype in Brave Core decides when it is given a news article URL. Nothing here is real news, and every page is written to show one shape of markup.

## What is here

- `pages/` holds 21 pages, one per shape: no video, a video named by Open Graph, Twitter card or schema.org data, YouTube, Vimeo and TED embeds, a `<video>` element, and the near misses that must not count (a decorative looping clip, a generic oEmbed link, markup inside comments and scripts, a playlist embed, an audio widget, video that script builds after load).
- `media/` holds a 6 second `clip.mp4` (H.264 and AAC), `clip.webm` (VP9 and Opus), a three segment HLS stream in `media/hls/`, and `poster.jpg`. They were generated with ffmpeg from its test sources, so they carry no third-party content.
- `expected.json` states the right answer for each page: the status, the evidence found, the kind of player, and what to load to play it. A field that is absent is not checked.
- `index.html` links every page.

Pages use relative URLs, so the repository works under any base path, including a GitHub Pages project site.

## News feed

`feed.xml` is an RSS feed of real, current articles whose pages hold a video, for trying the Playlist TV app against live content. `scripts/build_feed.py` builds it: it reads the RSS or Atom feeds listed in `feed-sources.json` (Techmeme, The Verge, TechCrunch and Ars Technica articles, and the YouTube channels of The Verge, Marques Brownlee, WIRED, TechCrunch, Engadget and Linus Tech Tips), follows each entry to its article, scans the article's HTML for video, and keeps the ones that have one.

    scripts/build_feed.py            # update feed.xml
    scripts/build_feed.py --dry-run  # show what each article scored; write nothing

- The feed holds titles and links only, never article text. Each item carries the evidence found (`category`), the article's image (`media:thumbnail`), and the video file (`enclosure`) when the page names one.
- Techmeme's own `<link>` is a Techmeme page, so that source sets `"linkFrom": "description"` to take the article from the entry's description.
- Links are made HTTPS and lose tracking parameters. An article whose URL carries a credential (a gifted-article token) is skipped, because the feed is public.
- Each run adds new finds to the ones already in `feed.xml`, drops items older than `--max-age-days` (30), and keeps the newest `maxItems` of them (18 in `feed-sources.json`; `--max-items` overrides). About one news article in twenty holds a video, so the YouTube channel feeds, where every entry is a video, supply most of the feed. A source's `maxEntries` limits how many of its newest entries are looked at, and YouTube Shorts are skipped because vertical clips look poor on a TV.
- The scan follows the app's scanner (`scripts/mediascan.py`), but the app probes each article again when it plays, and its answer is the one that counts. News sites that block non-browser clients, or put the video behind a login, are not listed.
- Add a source by adding an entry to `feed-sources.json`.

## Checks

The scripts' own tests need no network and no device: `cd scripts && python3 -m unittest discover -p 'test_*.py'`. `test_mediascan.py` runs the scan over the fixture pages and compares the answers with `expected.json`.

Offline, against the real scanner in Brave Core (no device, no network):

    PLAYLIST_TEST_VIDEOS_DIR=$PWD \
      bcore test android --checkout <name> brave_junit_tests \
      --filter='*PlaylistLeanbackFixturePagesTest*' --gn=enable_playlist_leanback:true

Without `PLAYLIST_TEST_VIDEOS_DIR` that test is skipped.

On a device, after publishing the repository to an HTTPS host such as GitHub Pages:

    scripts/probe_device.py --base https://USER.github.io/playlist-test-videos/ --serial emulator-5554

This runs the whole path (network fetch in the browser process, then the scan) and prints one line per page. The probe only fetches HTTPS pages, so a local HTTP server cannot be used for it.

To look at the pages in a desktop browser:

    scripts/serve.py 8000

## Adding a page

Add the page under `pages/`, add its entry to `expected.json`, and run the offline check. For a bug found on a real site, reduce the markup to the smallest page that shows it, add the page with the answer it should give, and watch the check fail before changing the scanner.

## Licence

CC0. Do what you like with it.
