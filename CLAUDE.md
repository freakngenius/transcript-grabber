# IG Transcript

A Mac app: paste an Instagram (or other yt-dlp) video link, get the transcript as a .txt file. README.md has usage.

## How it works
- `app.py` is one uv script with a PEP 723 header, so its dependencies live in uv's cache, not in the repo. Do not add a pyproject.toml or .venv here. yt-dlp downloads the audio track only into a `TemporaryDirectory`, ffmpeg converts it to 16 kHz mono WAV, and Homebrew `whisper-cli` (whisper.cpp on Metal) transcribes it with ggml-large-v3-turbo. `/api/transcribe` streams NDJSON progress events to `index.html`. The command-line mode runs the same `run_job()`.
- `mac/main.swift` is the whole Mac app: an AppKit window with a WKWebView. It starts `uv run --upgrade-package yt-dlp app.py --no-open --port 3232 --exit-with-stdin`, waits for `/api/ping`, then loads the page. It reuses a server that already answers the ping.
- `build.sh` compiles the Swift file, draws the icon with `mac/make_icon.swift`, bundles copies of `app.py` and `index.html`, signs ad hoc, and installs to /Applications. Edits to those files reach the app only after `./build.sh`.

## Design
The UI follows the Kesterson Collage design system by Kyle Kesterson (Claude Design export dated 2026-09-03). Its tokens are copied into the `:root` block of index.html.
- Ink and paper are the only page backgrounds. Yellow and accent red do the signalling. Zero border radius, 2px ink borders, hard offset shadows with no blur, torn edge between bands, cards tilted 1 to 2.6deg.
- Bricolage Grotesque for display, Work Sans for body, from Google Fonts; without internet they fall back to system sans. Section heads are lowercase. No icons and no emoji in UI copy.
- Deliberate deviations: the transcript sheet never tilts or hover-tilts, because people read and select text in it. The ink band's dot texture uses paper-coloured dots at 10%, because the token's dark dots vanish on ink.
- One deliberate exception: the ❤️ in the header byline. The system says no emoji; Kyle asked for it.
- The status pages in `page()` in main.swift and the icon in make_icon.swift use the same palette. The window is forced to dark appearance so the title bar sits on ink.

## Non-negotiables
- The server binds to 127.0.0.1 only. Every request passes `Handler.allowed()`: the Host check blocks DNS rebinding and the Origin check blocks other sites. POST accepts only `application/json`, so cross-site forms fail. `GuardTests` covers this; it fails when the guard is removed.
- The server must not outlive the app. The app holds the server's stdin pipe, and `--exit-with-stdin` exits when that pipe closes, so a crash or `pkill` cannot orphan it.
- The native `save` and `copy` message handlers answer only pages from 127.0.0.1. Saving goes through NSSavePanel, which grants file access without a Downloads permission prompt.
- Media never persists. Everything stays inside the job's temp folder.
- Errors the user sees are `Failure` messages in plain English. Raw yt-dlp errors go through `explain()`.
- No silent empties: zero words is an error, and fewer than 5 words shows a notice.
- Instagram links lose their query string (`igsh` tags who shared the link), in `pick_url()` and in `pickUrl()` in index.html.
- Build outside cloud-synced folders (build.sh uses a temp folder): Dropbox's extended attributes break code signatures.

## Seams that bite
- The Swift file compiles with `-parse-as-library` because of `@main`, and with `-swift-version 5`. Top-level code there is not main-actor isolated.
- Without the Edit menu, Command-V never reaches the web view. Keep Cut, Copy, Paste and Select All in `buildMenu()`.
- Apps opened from Finder get a short PATH. The app prepends /opt/homebrew/bin, and `need()` in app.py also checks Homebrew folders.
- uv exits 2 when PyPI is unreachable; only then does the app retry with `--offline`.

## Decisions with evidence (2026-09-26)
- Mac app, not a hosted URL: whisper.cpp runs free on the Mac's GPU, and Instagram serves logged-out requests from a home connection but tends to block cloud servers.
- No VAD. On real NASA reels, Silero VAD dropped a sentence spoken over crowd noise, invented a line on a music-only reel, and lost casing and punctuation. `-sns` without VAD gave the best output.
- Public reels work logged out with yt-dlp 2026.08.19, and reels expose an audio-only DASH stream, so `bestaudio` downloads about 1 MB per minute. Old reels can report `has_audio: false`; those fail with the no-sound message.
- The model is cloned (`cp -c`) into `~/.cache/ig-transcript/` from any copy Spotlight finds, else downloaded from Hugging Face. `WHISPER_MODEL` overrides it.
- macOS 26 shows the drawn squircle icon as is (no gray backing plate). Checked with `NSWorkspace.icon(forFile:)`, for both the first icon and the Collage one.

## Verify
- `python3 -B -m unittest -v` must exit 0. Capture exit codes explicitly, not through a pipe.
- `./build.sh` must exit 0, then `codesign --verify --deep --strict "/Applications/IG Transcript.app"`.
- End to end: open the app, then in a browser at http://127.0.0.1:3232 paste https://www.instagram.com/nasa/reel/DdPsDCWRT-u/ and expect about 95 words.
- Server lifetime: `( sleep 5 ) | uv run app.py --no-open --port 3299 --exit-with-stdin` must stop answering `/api/ping` once the pipe closes.
- `.claude/launch.json` has an `ig-transcript` config on port 3232 for the browser-only mode.
