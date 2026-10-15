# Playlist test pages

Small, synthetic pages and videos for testing whether a web page holds a playable video, as the Playlist Leanback prototype in Brave Core decides when it is given a news article URL. Nothing here is real news, and every page is written to show one shape of markup.

## What is here

- `pages/` holds 21 pages, one per shape: no video, a video named by Open Graph, Twitter card or schema.org data, YouTube, Vimeo and TED embeds, a `<video>` element, and the near misses that must not count (a decorative looping clip, a generic oEmbed link, markup inside comments and scripts, a playlist embed, an audio widget, video that script builds after load).
- `media/` holds a 6 second `clip.mp4` (H.264 and AAC), `clip.webm` (VP9 and Opus), a three segment HLS stream in `media/hls/`, and `poster.jpg`. They were generated with ffmpeg from its test sources, so they carry no third-party content.
- `expected.json` states the right answer for each page: the status, the evidence found, the kind of player, and what to load to play it. A field that is absent is not checked.
- `index.html` links every page.

Pages use relative URLs, so the repository works under any base path, including a GitHub Pages project site.

## Checks

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
