# Transcript Grabber

A Mac app: paste an Instagram or YouTube link (or any other yt-dlp link), or drop a video or audio file, and get the transcript as a .txt file. README.md has usage.

## How it works
- `app.py` is one uv script with a PEP 723 header, so its dependencies live in uv's cache, not in the repo. Do not add a pyproject.toml or .venv here. yt-dlp downloads the audio track only into a `TemporaryDirectory`, ffmpeg converts it to 16 kHz mono WAV, and Homebrew `whisper-cli` (whisper.cpp on Metal) transcribes it with ggml-large-v3-turbo. `/api/transcribe` (links) and `/api/transcribe-file` (dropped files) both stream NDJSON progress events to `index.html` through `Handler.stream()`. `run_job()` (links) and `run_file_job()` (files) both end in `finish_job()`. The command line takes links or file paths (`local_file()`).
- `mac/main.swift` is the whole Mac app: an AppKit window with a WKWebView. It starts `uv run --upgrade-package yt-dlp app.py --no-open --port 3232 --exit-with-stdin`, waits for `/api/ping` to answer `transcript-grabber`, then loads the page. It reuses a server that already answers the ping.
- `build.sh` compiles the Swift file, draws the icon with `mac/make_icon.swift`, bundles copies of `app.py` and `index.html`, signs ad hoc, and installs `Transcript Grabber.app` to /Applications. Edits to those files reach the app only after `./build.sh`.
- `/api/batch` runs `run_batch()`: one `run_job()` per link, each transcript saved as `{date} {title} [{id}].txt` under `SAVE_ROOT/<label>/` (`~/Downloads/Transcript Grabber`, or `TRANSCRIPTS_DIR`), plus `<label> - all transcripts.txt` rebuilt from every `*].txt`. The bracketed ID is how repeats are skipped.
- Every single link and dropped file is saved by `autosave()` into `SAVE_ROOT/<handle>/` (dropped files into `Files/`), named like batch files; the same video again replaces its old file. A failed save comes back as `save_error` in the result and never loses the transcript.
- `mac/ReelCollector.swift` holds `ReelCollector`, which lists reel links from an account's Reels tab in a small WebKit window, and `InstagramLoginWindow` (View > Instagram Login). Both use the default web data store, so a login made once is reused. The page asks for links with the `profile` message and gets them back through `window.igProfileLinks()`.
- Names that must agree: the app and bundle name `Transcript Grabber`, bundle ID `local.transcriptgrabber` (also the `defaults` domain), the ping ID `transcript-grabber` in app.py and main.swift, the model folder `~/.cache/transcript-grabber/`, and the log `~/Library/Logs/Transcript Grabber.log`.

## Design
The UI follows the Kesterson Collage design system by Kyle Kesterson (Claude Design export dated 2026-09-03). Its tokens are copied into the `:root` block of index.html.
- Ink and paper are the only page backgrounds. Yellow and accent red do the signalling. Zero border radius, 2px ink borders, hard offset shadows with no blur, torn edge between bands, cards tilted 1 to 2.6deg.
- Bricolage Grotesque for display, Work Sans for body, from Google Fonts; without internet they fall back to system sans. Section heads are lowercase. No icons and no emoji in UI copy.
- Deliberate deviations: the transcript sheet never tilts or hover-tilts, because people read and select text in it. The ink band's dot texture uses paper-coloured dots at 10%, because the token's dark dots vanish on ink.
- One deliberate exception: the ❤️ in the header byline. The system says no emoji; Kyle asked for it.
- The status pages in `page()` in main.swift and the icon in make_icon.swift use the same palette. The window is forced to dark appearance so the title bar sits on ink.
- Page copy names both Instagram and YouTube. Numbers in it come from real runs (see below).

## Non-negotiables
- The server binds to 127.0.0.1 only. Every request passes `Handler.allowed()`: the Host check blocks DNS rebinding and the Origin check blocks other sites. POST accepts only `application/json`, so cross-site forms fail. `GuardTests` covers this; it fails when the guard is removed.
- The server must not outlive the app. The app holds the server's stdin pipe, and `--exit-with-stdin` exits when that pipe closes, so a crash or `pkill` cannot orphan it.
- The native `save` and `copy` message handlers answer only pages from 127.0.0.1. Saving goes through NSSavePanel, which grants file access without a Downloads permission prompt.
- One video per job. `pick_url()` refuses Instagram profiles and YouTube channel, playlist and search pages, which would make yt-dlp fetch many videos. `noplaylist` handles `watch?v=…&list=…`.
- Media never persists. Everything stays inside the job's temp folder. A dropped file arrives as the raw request body, `save_upload()` writes it into a temp folder in 1 MB chunks (10 GB cap, free-space check first), and the user's original file is never touched.
- `/api/transcribe-file` accepts only `application/octet-stream`; like JSON, a cross-site form cannot send it and a cross-site fetch needs a preflight that never passes. Only the base name of the URL-encoded `X-Filename` header is kept, and it is used for display and the .txt name only.
- Errors the user sees are `Failure` messages in plain English. Raw yt-dlp errors go through `explain()`, which names the site from `site_name()`.
- No silent empties: zero words is an error, and fewer than 5 words shows a notice.
- Instagram links lose their query string (`igsh` tags who shared the link), in `pick_url()` and in `pickUrl()` in index.html.
- Batch downloads never use the Instagram login; only the collector window browses as the user. Keep `BATCH_PAUSE` (3 to 6 s between videos), the stop after two login refusals in a row, `MAX_BATCH` (100) and skip-by-ID.
- Build outside cloud-synced folders (build.sh uses a temp folder): Dropbox's extended attributes break code signatures.

## Seams that bite
- Command Line Tools are enough to build; full Xcode is not needed (checked with `DEVELOPER_DIR=/Library/Developer/CommandLineTools`).
- The Swift file compiles with `-parse-as-library` because of `@main`, and with `-swift-version 5`. Top-level code there is not main-actor isolated.
- A WKWebView ignores `<input type="file">` unless the UI delegate implements `runOpenPanelWith`; main.swift does, with an NSOpenPanel limited to movies and audio.
- The page must call `preventDefault()` on `dragover` and `drop`, or a WKWebView tries to open the dropped file itself.
- Without the Edit menu, Command-V never reaches the web view. Keep Cut, Copy, Paste and Select All in `buildMenu()`.
- Apps opened from Finder get a short PATH. The app prepends /opt/homebrew/bin, and `need()` in app.py also checks Homebrew folders. yt-dlp finds deno on that PATH for YouTube.
- In WebKit, `window.scrollTo` alone did not make Instagram load more reels; bringing the last reel into view (`scrollDown` in ReelCollector) does. The first grid can take several seconds to draw, so the collector waits up to 20 rounds for the first reels and stops after 6 quiet rounds once it has some.
- ~/Downloads is protected by macOS. The server is a child of the app, so its first batch save triggers a one-time Allow prompt; a refusal surfaces as the Privacy & Security error in `run_batch()`.
- `build.sh` compiles `mac/main.swift` and `mac/ReelCollector.swift` together.
- uv exits 2 when PyPI is unreachable; only then does the app retry with `--offline`.
- YouTube sometimes answers a fresh media URL with HTTP 403. `fetch_audio()` clears the temp folder and extracts once more; `FetchRetryTests` covers it with a stand-in yt_dlp module.

## Decisions with evidence (2026-09-26, M5 Max)
- Mac app, not a hosted URL: whisper.cpp runs free on the Mac's GPU, and Instagram serves logged-out requests from a home connection but tends to block cloud servers.
- No VAD. On real NASA reels, Silero VAD dropped a sentence spoken over crowd noise, invented a line on a music-only reel, and lost casing and punctuation. `-sns` without VAD gave the best output.
- Public reels work logged out with yt-dlp 2026.08.19, and reels expose an audio-only DASH stream, so `bestaudio` downloads about 1 MB per minute. Old reels can report `has_audio: false`; those fail with the no-sound message.
- YouTube works with the same pipeline: a 3m11s NASA video gave 403 words in 8 to 14 seconds end to end, and a 29m59s one gave 5,310 words in 34 seconds. It also worked with deno off the PATH. One Short failed once with 403 and downloaded fine on the next try, hence the retry.
- Profiles: yt-dlp's Instagram profile extractor is marked broken ("Unable to extract data"), it does not support `/reels/` URLs, and `api/v1/users/web_profile_info` answers 401 without a login. A real browser on the Reels tab lists 12 reels and loads about 4 more per scroll without a login; after about 36, Instagram sent the session to its login page. Hence the WebKit collector.
- Collector runs from a test harness (window off screen): 12 reels in 6 s; 24 in 6 s after the scrollIntoView change (12 before it). A real two-reel batch saved both in 12 s including one pause, and the rerun skipped both in 0 s.
- Whisper transcribes YouTube too; YouTube's own captions are not used, so every site gets the same quality and code path.
- The model is cloned (`cp -c`) into `~/.cache/transcript-grabber/` from any copy Spotlight finds, else downloaded from Hugging Face. `WHISPER_MODEL` overrides it.
- macOS 26 shows the drawn squircle icon as is (no gray backing plate). Checked with `NSWorkspace.icon(forFile:)`.

## Verify
- `python3 -B -m unittest -v` must exit 0. Capture exit codes explicitly, not through a pipe.
- `./build.sh` must exit 0, then `codesign --verify --deep --strict "/Applications/Transcript Grabber.app"`.
- End to end: open the app, then in a browser at http://127.0.0.1:3232 paste https://www.instagram.com/nasa/reel/DdPsDCWRT-u/ (about 95 words) and https://www.youtube.com/watch?v=RU-i523fflU (about 400 words).
- Files: build a clip from whisper.cpp's public-domain JFK sample (`ffmpeg -f lavfi -i color=c=black:s=320x240:r=10 -i /opt/homebrew/share/whisper-cpp/jfk.wav -shortest -c:v libx264 -c:a aac jfk.mp4`), then `uv run app.py jfk.mp4` should print 22 words. A text file renamed to .mov must fail with "That file is not a video or audio file."
- Collector: compile a small harness that calls `ReelCollector(username:count:beside:nil, visible: false).run()` together with `mac/ReelCollector.swift`, and check it returns links for `nasa`. Keep such runs rare; Instagram walls repeated logged-out visits.
- Batch without touching Downloads: `open --env TRANSCRIPTS_DIR=/tmp/somewhere -a "/Applications/Transcript Grabber.app"`, then paste two post links into the page at http://127.0.0.1:3232.
- Server lifetime: `( sleep 5 ) | uv run app.py --no-open --port 3299 --exit-with-stdin` must stop answering `/api/ping` once the pipe closes.
- `.claude/launch.json` has a `transcript-grabber` config on port 3232 and `transcript-grabber-dev` on 3233.
