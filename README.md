# Transcript Grabber

Paste an Instagram or YouTube link, or drop in a video file, and get what was said, as a text file. Everything runs on your Mac: no transcription service, no account, no cost per video.

Made with ❤️ by [Kyle Kesterson](https://www.demystified.ai). MIT licensed: use it, change it, make your own.

Want to learn how to build apps like this, or AI agents? Join the [Demystified AI Build Lab](https://www.skool.com/demystified-ai-build-room-5414) on Skool. Free live builds every week.

## Install

You need an Apple Silicon Mac and [Homebrew](https://brew.sh). Homebrew installs Apple's Command Line Tools, which is all the build needs; full Xcode is not required.

```bash
git clone https://github.com/freakngenius/transcript-grabber.git
cd transcript-grabber
brew install uv ffmpeg whisper-cpp
./build.sh
```

`build.sh` compiles the app, draws its icon and installs **Transcript Grabber** in /Applications. On first start the app downloads the Whisper model (1.6 GB) once, into `~/.cache/transcript-grabber/`. If a copy of `ggml-large-v3-turbo.bin` is already on your Mac, it clones that instead (an instant APFS clone that takes no extra space).

## Use it

1. Open **Transcript Grabber** from Applications, Launchpad or Spotlight.
2. Copy a link: in Instagram, tap Share, then Copy link; on YouTube, tap Share, then Copy; on the web, copy the address bar.
3. Paste into the box, or drag a link onto the window. It starts on its own.
   Or drop a video or audio file (.mov, .mp4, .m4a, .mp3, .wav and more) anywhere on the window, or click **Choose a video file**.
4. Click **Copy text**, or **Save .txt…** to pick where the file goes. Turn on **Timestamps** for `[00:12]` markers.

It takes Instagram reels and posts, and YouTube videos and Shorts (`youtube.com/watch`, `youtu.be` and `/shorts/` links). TikTok, X and other sites that yt-dlp supports work too. It does one video at a time, so channel, playlist and search pages are refused.

Files you drop never leave your Mac. The app reads a temporary copy, deletes it when the job ends, and never changes your original. Files can be up to 10 GB.

On an M5 Max, a one-minute reel takes about 5 seconds and a 30-minute YouTube video about 35. Older Macs take longer.

The saved file starts with a short header (title or caption, creator, link, length, language, date), then the transcript.

The View menu has **Open in Browser** (the same app in your browser) and **Show Log** (for when something fails). Quitting the app (Command-Q or closing the window) stops everything.

## Grab a whole profile (Mac app)

Paste an Instagram profile link, like `https://www.instagram.com/nasa/`, choose how many of the latest videos to grab (1 to 100), and click **Grab transcripts**.

1. A small window opens on the account's Reels tab and scrolls until it has that many links.
2. Each video is transcribed and saved as its own .txt file in `Downloads/Transcript Grabber/<account>/`, named by date and caption, like `2026-09-13 Today's flyover in Pittsburgh [DdPsDCWRT-u].txt`.
3. `<account> - all transcripts.txt` in the same folder holds every transcript in date order, ready to paste into a chat model.

Run it again later to pick up new videos: ones already saved are skipped. **Stop** ends a run early, and everything saved so far stays.

- Without a login, Instagram shows a few dozen reels before it asks you to log in. For more, use View > Instagram Login once; the app remembers it. The login is only used to scroll the Reels tab. The videos themselves are fetched without it.
- Videos are fetched 3 to 6 seconds apart, and a run stops if Instagram starts refusing. Grabbing hundreds of videos in a row can still get an account rate-limited, so keep runs modest.
- macOS asks once for permission to save in Downloads. Click Allow.

Without the app, you can paste several post links at once instead. They are transcribed and saved the same way.

## How it works

1. The app starts a small local server (`app.py`) with uv and shows its page in a window. The server only answers your own Mac.
2. For a link, yt-dlp downloads only the audio track into a temp folder, about 1 MB a minute. For a dropped file, the page hands the file to that local server, which saves a temporary copy.
3. ffmpeg converts it, and whisper.cpp transcribes it with the large-v3-turbo model on the Mac's GPU. YouTube's own captions are not used.
4. The temp folder, audio included, is deleted when the job ends. Only the text stays.

The app updates yt-dlp each time it opens, because Instagram and YouTube change often and yt-dlp follows within days. If links stop working, quit and reopen the app. YouTube sometimes refuses a download with a 403 error; the app retries once on its own. If YouTube links keep failing, install a JavaScript runtime that yt-dlp can use for YouTube: `brew install deno`.

## Without the app

From the repo folder:

```bash
uv run app.py                                       # web page at http://127.0.0.1:3232
uv run app.py https://youtu.be/XXXX                 # prints the transcript, saves to ~/Downloads
uv run app.py ~/Movies/interview.mov               # files work too
```

| Variable | Default | What it does |
| --- | --- | --- |
| `PORT` | `3232` | Port for the web page |
| `WHISPER_LANGUAGE` | `auto` | Spoken language; set `en` to skip detection |
| `WHISPER_MODEL` | large-v3-turbo | Path to a different whisper.cpp model |
| `COOKIES_FROM_BROWSER` | off | See Private videos |

## Private videos

Public posts and videos work without logging in. For private Instagram posts you can see, age-restricted YouTube videos, or when a site limits requests, let the app use your browser's login:

```bash
defaults write local.transcriptgrabber CookiesFromBrowser chrome
```

Quit and reopen the app. macOS asks once for Keychain access to Chrome's cookies. Requests then come from your account, so keep the volume low: sites can flag accounts that fetch a lot. To turn it off: `defaults delete local.transcriptgrabber CookiesFromBrowser`. Without the app, the same setting is `COOKIES_FROM_BROWSER=chrome uv run app.py`.

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
- Profile grabbing works for public accounts only.
- Not affiliated with Instagram, Meta, YouTube or Google. Their terms limit automated downloading; this is built for personal, occasional use. Only transcribe videos you have the right to use.

## License

MIT. See [LICENSE](LICENSE).
