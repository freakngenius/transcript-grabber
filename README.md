# IG Transcript

Paste an Instagram video link and get what was said, as a text file. Everything runs on your Mac: no transcription service, no account, no cost per video.

Made with ❤️ by [Kyle Kesterson](https://www.demystified.ai). MIT licensed: use it, change it, make your own.

## Install

You need an Apple Silicon Mac with Xcode and [Homebrew](https://brew.sh).

```bash
git clone https://github.com/freakngenius/ig-transcript.git
cd ig-transcript
brew install uv ffmpeg whisper-cpp
./build.sh
```

`build.sh` compiles the app, draws its icon and installs **IG Transcript** in /Applications. On first start the app downloads the Whisper model (1.6 GB) once, into `~/.cache/ig-transcript/`. If a copy of `ggml-large-v3-turbo.bin` is already on your Mac, it clones that instead (an instant APFS clone that takes no extra space).

## Use it

1. Open **IG Transcript** from Applications, Launchpad or Spotlight.
2. In Instagram, tap Share, then Copy link. On the web, copy the address bar.
3. Paste into the box, or drag a link onto the window. It starts on its own.
4. Click **Copy text**, or **Save .txt…** to pick where the file goes. Turn on **Timestamps** for `[00:12]` markers.

A 1-minute reel takes about 5 seconds. Links from TikTok, YouTube, X and other sites that yt-dlp supports work too.

The saved file starts with a short header (caption, creator, link, length, language, date), then the transcript.

The View menu has **Open in Browser** (the same app in your browser) and **Show Log** (for when something fails). Quitting the app (Command-Q or closing the window) stops everything.

## How it works

1. The app starts a small local server (`app.py`) with uv and shows its page in a window. The server only answers your own Mac.
2. yt-dlp downloads only the audio track into a temp folder. A 1-minute reel is about 1 MB.
3. ffmpeg converts it, and whisper.cpp transcribes it with the large-v3-turbo model on the Mac's GPU.
4. The temp folder, audio included, is deleted when the job ends. Only the text stays.

The app updates yt-dlp each time it opens, because Instagram changes its site often and yt-dlp follows within days. If links stop working, quit and reopen the app.

## Without the app

From the repo folder:

```bash
uv run app.py                                       # web page at http://127.0.0.1:3232
uv run app.py https://www.instagram.com/reel/XXXX/  # prints the transcript, saves to ~/Downloads
```

| Variable | Default | What it does |
| --- | --- | --- |
| `PORT` | `3232` | Port for the web page |
| `WHISPER_LANGUAGE` | `auto` | Spoken language; set `en` to skip detection |
| `WHISPER_MODEL` | large-v3-turbo | Path to a different whisper.cpp model |
| `IG_COOKIES_FROM_BROWSER` | off | See Private posts |

## Private posts

Public posts work without logging in. For private posts you can see, or when Instagram limits requests, let the app use your browser's Instagram login:

```bash
defaults write local.igtranscript CookiesFromBrowser chrome
```

Quit and reopen the app. macOS asks once for Keychain access to Chrome's cookies. Requests then come from your account, so keep the volume low: Instagram can flag accounts that fetch a lot. To turn it off: `defaults delete local.igtranscript CookiesFromBrowser`. Without the app, the same setting is `IG_COOKIES_FROM_BROWSER=chrome uv run app.py`.

## Make it your own

- **Look:** the page is `index.html`. Its colours, fonts and shadows come from the tokens in the `:root` block, which follow Kyle's Kesterson Collage design system (ink and paper, torn edges, hard offset shadows, Bricolage Grotesque and Work Sans). Swap the tokens to rebrand it. The fonts load from Google Fonts, so without internet the page falls back to the system font.
- **Icon:** drawn in code by `mac/make_icon.swift`.
- **Byline:** in the header of `index.html`.

Run `./build.sh` after any change; the app bundles copies of `app.py` and `index.html`.

## Tests

```bash
python3 -B -m unittest -v
```

## Limits

- Videos with no speech (music only) return a notice instead of a transcript.
- Photos and silent videos have nothing to transcribe.
- Not affiliated with Instagram or Meta. Instagram's terms limit automated downloading; this is built for personal, occasional use. Only transcribe videos you have the right to use.

## License

MIT. See [LICENSE](LICENSE).
