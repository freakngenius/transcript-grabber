# /// script
# requires-python = ">=3.11"
# dependencies = ["yt-dlp[default]"]
# ///
"""Transcript Grabber: paste a video link or drop a video file, get the transcript as a text file.

    uv run app.py              web app on http://127.0.0.1:3232
    uv run app.py URL|FILE …   command line; saves .txt files to ~/Downloads

yt-dlp downloads the audio track only, into a temp folder. whisper.cpp
transcribes it on this Mac. The temp folder is deleted when the job ends.
The Mac app in mac/ runs this server and shows the page in its own window.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

HERE = Path(__file__).resolve().parent
DEFAULT_PORT = int(os.environ.get("PORT", "3232"))
LANGUAGE = os.environ.get("WHISPER_LANGUAGE", "auto")
THREADS = str(min(8, os.cpu_count() or 4))
MODEL_NAME = "ggml-large-v3-turbo.bin"
MODEL_URL = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{MODEL_NAME}"
MODEL_DIR = Path.home() / ".cache" / "transcript-grabber"
MAX_VIDEOS = 10  # a carousel post can hold several videos
MAX_UPLOAD = 10 * 1024**3  # 10 GB per dropped file
MAX_BATCH = 100  # videos per batch
BATCH_PAUSE = (3.0, 6.0)  # seconds between videos, a person-like pace that avoids rate limits
SAVE_ROOT = Path(os.environ.get("TRANSCRIPTS_DIR") or Path.home() / "Downloads" / "Transcript Grabber").expanduser()
MIN_WORDS = 5

LANGUAGES = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German", "it": "Italian",
    "pt": "Portuguese", "nl": "Dutch", "ru": "Russian", "uk": "Ukrainian", "pl": "Polish",
    "tr": "Turkish", "ar": "Arabic", "he": "Hebrew", "hi": "Hindi", "ja": "Japanese",
    "ko": "Korean", "zh": "Chinese", "id": "Indonesian", "vi": "Vietnamese", "sv": "Swedish",
}


class Failure(Exception):
    """An error whose message is written for the user and shown as is."""


# ---------------------------------------------------------------- tools

@dataclass(frozen=True)
class Tools:
    ffmpeg: str
    ffprobe: str
    whisper: str
    model: Path

    @classmethod
    def load(cls) -> Tools:
        return cls(
            ffmpeg=need("ffmpeg", "brew install ffmpeg"),
            ffprobe=need("ffprobe", "brew install ffmpeg"),
            whisper=need("whisper-cli", "brew install whisper-cpp"),
            model=find_model(),
        )


def need(name: str, install: str) -> str:
    # A Finder launch gets a short PATH, so look in Homebrew's folders too.
    for path in (shutil.which(name), f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"):
        if path and os.access(path, os.X_OK):
            return path
    raise Failure(f"{name} is not installed. Run: {install}")


def find_model() -> Path:
    override = os.environ.get("WHISPER_MODEL")
    if override:
        path = Path(override).expanduser()
        if not path.is_file():
            raise Failure(f"WHISPER_MODEL points to a missing file: {path}")
        return path
    target = MODEL_DIR / MODEL_NAME
    if target.is_file():
        return target
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    existing = spotlight(MODEL_NAME)
    if existing:
        print(f"Copying the Whisper model from {existing}", file=sys.stderr)
        # cp -c makes an APFS clone: instant, and it takes no extra disk space.
        if subprocess.run(["cp", "-c", str(existing), str(part)]).returncode == 0:
            part.replace(target)
            return target
    try:
        download(MODEL_URL, part)
    except OSError as err:
        raise Failure(f"Could not download the Whisper model: {err}") from None
    part.replace(target)
    return target


def spotlight(name: str) -> Path | None:
    try:
        found = subprocess.run(["mdfind", "-name", name], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in found.splitlines():
        path = Path(line)
        # A real large-v3-turbo file is 1.6 GB; skip partial downloads.
        if path.name == name and path.is_file() and path.stat().st_size > 1_000_000_000:
            return path
    return None


def download(url: str, dest: Path) -> None:
    print(f"Downloading the Whisper model (1.6 GB) to {dest.parent}", file=sys.stderr)
    with urllib.request.urlopen(url, timeout=30) as resp, open(dest, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done, shown = 0, -1
        while chunk := resp.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            percent = done * 100 // total if total else 0
            if percent // 10 != shown:
                shown = percent // 10
                print(f"  {percent}%", file=sys.stderr)
    if total and done != total:
        raise OSError("the download ended early; start the app again to retry")


# ---------------------------------------------------------------- links and metadata

URL_RE = re.compile(r"https?://[^\s<>\"']+")
BARE_INSTAGRAM_RE = re.compile(r"(?:www\.)?instagram\.com/[^\s<>\"']+")
GENERIC_TITLE_RE = re.compile(r"^(video|post|reel) by ", re.I)
LOGIN_HINTS = ("login", "log in", "sign in", "empty media response", "rate-limit", "rate limit", "unreachable", "private")
SITE_NAMES = {"instagram.com": "Instagram", "youtube.com": "YouTube", "youtu.be": "YouTube",
              "tiktok.com": "TikTok", "x.com": "X", "twitter.com": "X", "facebook.com": "Facebook"}


def pick_url(text: str) -> str:
    """Find the link in pasted text, which is often a caption plus a URL."""
    text = text or ""
    match = URL_RE.search(text) or BARE_INSTAGRAM_RE.search(text)
    if not match:
        raise Failure("Paste a link that starts with https://")
    url = match.group(0).rstrip(".,;)]")
    if not url.startswith("http"):
        url = "https://" + url
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.endswith("instagram.com"):
        # A profile link would make yt-dlp fetch every post on the account.
        if not re.search(r"/(p|reels?|tv|share)/", parsed.path):
            raise Failure("Paste a link to one post or reel, not a profile.")
        # The igsh query tags who shared the link; keep it out of saved files.
        url = parsed._replace(query="", fragment="").geturl()
    # A channel, playlist or search page would make yt-dlp fetch many videos.
    if host.endswith("youtube.com") and not re.match(r"/(watch/?$|shorts/|live/|embed/|v/)", parsed.path):
        raise Failure("Paste a link to one video, not a channel or playlist.")
    return url


def site_name(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    for domain, name in SITE_NAMES.items():
        if host == domain or host.endswith("." + domain):
            return name
    return host.removeprefix("www.") or "The site"


def explain(message: str, site: str = "Instagram") -> str:
    """Turn a yt-dlp error into one plain sentence."""
    lines = message.strip().splitlines()
    line = re.sub(r"^ERROR:\s*", "", lines[0] if lines else "")
    line = re.sub(r"^\[[^\]]+\]\s*", "", line)  # extractor name, like [Instagram]
    line = re.sub(r"^[\w-]+:\s+", "", line)  # post ID
    low = line.lower()
    if "unsupported url" in low:
        return "This link is not a video page the app can read."
    if any(hint in low for hint in LOGIN_HINTS):
        return (f"{site} would not share this video without a login. It may be private or "
                f"age-restricted, or {site} is limiting requests. Try again in a minute. "
                "For private videos, see Private posts in the README.")
    if "404" in low:
        return "This post was not found. It may have been deleted."
    if "no video" in low or "no formats" in low or "requested format is not available" in low:
        return "This post has no video."
    return f"{line.rstrip('.') or 'Download failed'}. If links keep failing, restart the app to update yt-dlp."


def title_of(meta: dict) -> str:
    title = str(meta.get("title") or "").strip()
    if not title or GENERIC_TITLE_RE.match(title):
        caption = str(meta.get("description") or "").strip()
        title = caption.splitlines()[0].strip() if caption else ""
    return title if len(title) <= 100 else title[:99].rstrip() + "…"


def creator_of(meta: dict) -> str:
    name = str(meta.get("uploader") or meta.get("channel") or "").strip()
    handle = str(meta.get("channel") or meta.get("uploader_id") or "").strip().lstrip("@")
    if handle and not handle.isdigit() and handle.lower() != name.lower():
        return f"{name} (@{handle})" if name else f"@{handle}"
    return name


def filename_for(meta: dict) -> str:
    who = meta.get("channel") or meta.get("uploader_id") or meta.get("uploader") or "video"
    stem = re.sub(r"[^A-Za-z0-9_]+", "-", f"{who}-{meta.get('id') or 'transcript'}").strip("-")
    return f"{stem[:80] or 'transcript'}.txt"


def local_file(arg: str) -> Path | None:
    """The command line takes files as well as links."""
    if "://" in arg:
        return None
    path = Path(arg).expanduser()
    return path if path.is_file() else None


def file_txt_name(name: str) -> str:
    stem = re.sub(r"[^\w-]+", "-", Path(name).stem).strip("-")
    return f"{stem[:80] or 'transcript'}.txt"


def save_upload(stream, length: int, dest: Path) -> Path:
    """Copy a dropped file from the request body to disk in 1 MB chunks."""
    if length <= 0:
        raise Failure("That file is empty.")
    if length > MAX_UPLOAD:
        raise Failure(f"That file is over {MAX_UPLOAD // 1024**3} GB.")
    if shutil.disk_usage(dest.parent).free < length + 1024**3:
        raise Failure("There is not enough free disk space to read that file.")
    remaining = length
    with open(dest, "wb") as out:
        while remaining:
            chunk = stream.read(min(1 << 20, remaining))
            if not chunk:
                raise Failure("The file stopped arriving before it was complete.")
            out.write(chunk)
            remaining -= len(chunk)
    return dest


POST_ID_RES = (
    re.compile(r"instagram\.com/(?:[^/?#]+/)?(?:p|reels?|tv)/([A-Za-z0-9_-]+)"),
    re.compile(r"youtube\.com/(?:watch\?(?:[^#]*&)?v=|shorts/|live/|embed/)([A-Za-z0-9_-]{6,})"),
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{6,})"),
)


def post_id(url: str) -> str | None:
    """The video's ID from its link, used to name saved files and to skip repeats."""
    for pattern in POST_ID_RES:
        match = pattern.search(url)
        if match:
            return match.group(1)
    return None


def folder_name(text: str) -> str:
    name = re.sub(r'[/\\:*?"<>|\x00-\x1f]+', " ", text).strip().strip(".").strip()
    return re.sub(r"\s+", " ", name)[:60] or "Batch"


def batch_file_name(result: dict, fallback_id: str) -> str:
    # Date first so the folder sorts oldest to newest; the ID in brackets finds repeats.
    title = folder_name(result.get("title") or "") if result.get("title") else ""
    if len(title) > 50:
        title = title[:50].rsplit(" ", 1)[0]  # end on a whole word
    ident = result.get("id") or fallback_id
    published = result.get("published") or "undated"
    return f"{published} {title} [{ident}].txt" if title else f"{published} [{ident}].txt"


def already_saved(folder: Path, ident: str) -> Path | None:
    return next((f for f in folder.glob("*.txt") if f.name.endswith(f"[{ident}].txt")), None)


def write_transcript(folder: Path, result: dict, fallback_id: str) -> Path:
    """Save one transcript. A newer copy of the same video replaces the older file."""
    name = result.get("filename") or "transcript.txt" if result.get("kind") == "file" else batch_file_name(result, fallback_id)
    folder.mkdir(parents=True, exist_ok=True)
    ident = result.get("id")
    if ident:
        for old in folder.glob("*.txt"):
            if old.name.endswith(f"[{ident}].txt") and old.name != name:
                old.unlink()
    path = folder / name
    path.write_text(f"{result['header']}\n\n{result['plain']}\n", encoding="utf-8")
    return path


def autosave(result: dict) -> dict:
    """Save a finished transcript under SAVE_ROOT without being asked, and note where."""
    label = "Files" if result.get("kind") == "file" else (result.get("handle") or "Links")
    folder = SAVE_ROOT / folder_name(str(label))
    try:
        path = write_transcript(folder, result, result.get("id") or "video")
        if (folder / f"{folder.name} - all transcripts.txt").exists():
            rebuild_combined(folder)  # keep a batch folder's combined file current
    except OSError as err:
        result["save_error"] = (f"Could not save into {folder}: {err.strerror}. On a Mac, allow Transcript Grabber "
                                "in System Settings > Privacy & Security > Files and Folders.")
        return result
    result["saved"] = str(path)
    return result


def rebuild_combined(folder: Path) -> Path:
    """One file with every transcript in the folder, handy to paste into a chat model."""
    combined = folder / f"{folder.name} - all transcripts.txt"
    parts = [f.read_text(encoding="utf-8").strip() for f in sorted(folder.glob("*].txt"))]
    combined.write_text(("\n\n" + "-" * 40 + "\n\n").join(parts) + "\n", encoding="utf-8")
    return combined


# ---------------------------------------------------------------- text

TAG_RE = re.compile(r"\[[^\]]*\]|\*[^*]*\*|[♪♫]+")


def fmt_duration(seconds: float | None) -> str:
    total = int(round(seconds or 0))
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def fmt_clock(seconds: float) -> str:
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", TAG_RE.sub(" ", text)).strip()


def drop_loops(segments: list[dict], keep: int = 2) -> list[dict]:
    """Whisper can repeat one line over music or silence. Keep two in a row at most."""
    out: list[dict] = []
    for seg in segments:
        if len(out) >= keep and all(prev["text"] == seg["text"] for prev in out[-keep:]):
            continue
        out.append(seg)
    return out


def segments_from(whisper_json: dict) -> list[dict]:
    segments = []
    for item in whisper_json.get("transcription") or []:
        text = clean(item.get("text") or "")
        if text:
            offsets = item.get("offsets") or {}
            segments.append({"start": offsets.get("from", 0) / 1000, "end": offsets.get("to", 0) / 1000, "text": text})
    return drop_loops(segments)


def ends_sentence(text: str) -> bool:
    return text.rstrip("\"'”’)").endswith((".", "?", "!", "…"))


def plain_text(segments: list[dict], para_chars: int = 600, pause: float = 2.0) -> str:
    paragraphs: list[list[str]] = [[]]
    size, last_end = 0, None
    for seg in segments:
        current = paragraphs[-1]
        long_pause = last_end is not None and seg["start"] - last_end >= pause
        if current and (long_pause or (size >= para_chars and ends_sentence(current[-1]))):
            current = []
            paragraphs.append(current)
            size = 0
        current.append(seg["text"])
        size += len(seg["text"]) + 1
        last_end = seg["end"]
    return "\n\n".join(" ".join(p) for p in paragraphs if p)


def timed_text(segments: list[dict]) -> str:
    return "\n".join(f"[{fmt_clock(seg['start'])}] {seg['text']}" for seg in segments)


def compose(parts: list[list[dict]], render) -> str:
    if len(parts) == 1:
        return render(parts[0])
    return "\n\n".join(f"Video {n}\n{render(part) or '(no speech)'}" for n, part in enumerate(parts, 1))


# ---------------------------------------------------------------- pipeline

def run(cmd: list[str], failure: str, timeout: int = 3600) -> None:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Failure(f"{failure} It took longer than {timeout // 60} minutes.") from None
    if proc.returncode != 0:
        print(f"{Path(cmd[0]).name} exited {proc.returncode}:\n{proc.stderr[-2000:]}", file=sys.stderr)
        raise Failure(f"{failure} See the log for details.")


def probe(path: Path, tools: Tools, *args: str) -> str:
    proc = subprocess.run([tools.ffprobe, "-v", "error", *args, "-of", "csv=p=0", str(path)],
                          capture_output=True, text=True, errors="replace", timeout=60)
    return proc.stdout.strip()


def stream_types(path: Path, tools: Tools) -> set[str]:
    """The kinds of stream in a file, like {"video", "audio"}; empty if ffmpeg cannot read it."""
    return set(probe(path, tools, "-show_entries", "stream=codec_type").split())


def audio_seconds(path: Path, tools: Tools) -> float:
    try:
        return float(probe(path, tools, "-show_entries", "format=duration"))
    except ValueError:
        return 0.0


def fetch_audio(url: str, folder: Path) -> tuple[dict, list[Path]]:
    import yt_dlp  # imported here so the tests run without it

    options = {
        # Reels carry a separate audio-only stream; posts without one fall back to the whole file.
        "format": "bestaudio/best",
        "outtmpl": str(folder / "%(autonumber)03d-%(id)s.%(ext)s"),
        "noplaylist": True,
        "playlistend": MAX_VIDEOS,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 20,
        "retries": 3,
    }
    browser = os.environ.get("COOKIES_FROM_BROWSER")
    if browser:
        options["cookiesfrombrowser"] = (browser,)
    for attempt in (1, 2):
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
            break
        except yt_dlp.utils.DownloadError as err:
            # YouTube sometimes refuses a fresh media URL with 403; a new extraction usually works.
            if attempt == 1 and "HTTP Error 403" in str(err):
                for leftover in folder.iterdir():
                    leftover.unlink()
                continue
            raise Failure(explain(str(err), site_name(url))) from None
    if not info:
        raise Failure("No video found at that link.")
    entries = [e for e in (info.get("entries") or [info]) if e]
    files = [Path(d["filepath"]) for e in entries for d in e.get("requested_downloads") or [] if d.get("filepath")]
    files = [f for f in files if f.is_file()] or sorted(f for f in folder.iterdir() if f.is_file())
    if not files:
        raise Failure("No video found at that link.")
    # A carousel's own record can lack the creator, so fill gaps from its first video.
    meta = {**entries[0], **{k: v for k, v in info.items() if v not in (None, "", [])}}
    meta.pop("entries", None)
    return meta, files


def transcribe(audio: Path, base: Path, tools: Tools) -> tuple[list[dict], str]:
    wav = base.with_name(base.name + ".wav")
    run([tools.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(audio),
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)], "Could not read the audio.", timeout=600)
    # No VAD: on real reels, Silero VAD dropped a line spoken over crowd noise and
    # invented one on a music-only clip. -sns suppresses tags like *music*.
    run([tools.whisper, "-m", str(tools.model), "-f", str(wav), "-l", LANGUAGE, "-t", THREADS,
         "-sns", "-np", "-oj", "-of", str(base)], "Transcription failed.")
    # Multibyte characters can split across tokens, so decode leniently.
    data = json.loads(base.with_name(base.name + ".json").read_bytes().decode("utf-8", "replace"))
    return segments_from(data), str((data.get("result") or {}).get("language") or "")


def run_job(raw: str, tools: Tools, emit) -> dict:
    """Transcribe a link: yt-dlp fetches the audio, then finish_job() takes over."""
    url = pick_url(raw)
    host = (urlparse(url).hostname or "the link").removeprefix("www.")
    emit({"stage": "fetching", "message": f"Fetching audio from {host}…"})
    with tempfile.TemporaryDirectory(prefix="transcript-grabber-") as tmp:
        folder = Path(tmp)
        meta, files = fetch_audio(url, folder)
        return finish_job(files, folder, tools, emit, kind="link", site=host, title=title_of(meta),
                          creator=creator_of(meta), source=meta.get("webpage_url") or url,
                          filename=filename_for(meta), ident=meta.get("id"), published=published_on(meta),
                          handle=meta.get("channel") or meta.get("uploader_id") or meta.get("uploader") or host)


def published_on(meta: dict) -> str | None:
    day = str(meta.get("upload_date") or "")
    if re.fullmatch(r"\d{8}", day):
        return f"{day[:4]}-{day[4:6]}-{day[6:]}"
    if meta.get("timestamp"):
        return dt.datetime.fromtimestamp(meta["timestamp"]).date().isoformat()  # local time, not UTC
    return None


def run_file_job(path: Path, name: str, tools: Tools, emit) -> dict:
    """Transcribe a video or audio file already on this Mac. The file itself is never changed."""
    emit({"stage": "reading", "message": f"Reading {name}…"})
    with tempfile.TemporaryDirectory(prefix="transcript-grabber-") as tmp:
        return finish_job([path], Path(tmp), tools, emit, kind="file", site="Local file", title=Path(name).stem,
                          creator="", source=name, filename=file_txt_name(name))


def run_batch(links: list[str], label: str, tools: Tools, emit, pause: tuple[float, float] = BATCH_PAUSE) -> dict:
    """Transcribe many links in a row and save each one, plus a combined file, into one folder."""
    links = links[:MAX_BATCH]
    folder = SAVE_ROOT / folder_name(label)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as err:
        raise Failure(f"Could not create {folder}: {err.strerror}.") from None
    total = len(links)
    emit({"stage": "batch", "total": total, "folder": str(folder)})
    saved = skipped = failed = login_refusals = 0
    stopped = None
    for index, link in enumerate(links, 1):
        ident = post_id(link)
        existing = already_saved(folder, ident) if ident else None
        if existing:
            skipped += 1
            emit({"stage": "item", "index": index, "state": "skipped", "url": link, "title": existing.stem,
                  "reason": "Already saved"})
            continue
        prefix = f"{index} of {total}"
        try:
            result = run_job(link, tools, lambda event: emit(
                {**event, "stage": "progress", "index": index, "message": f"{prefix} · {event['message']}"}))
        except Failure as err:
            failed += 1
            emit({"stage": "item", "index": index, "state": "failed", "url": link, "reason": str(err)})
            login_refusals = login_refusals + 1 if "without a login" in str(err) else 0
            if login_refusals >= 2:
                stopped = "Instagram started asking for a login, so the batch stopped. Try again in a while."
                break
        else:
            login_refusals = 0
            try:
                name = write_transcript(folder, result, ident or f"video{index}").name
            except OSError as err:
                raise Failure(f"Could not save into {folder}: {err.strerror}. On a Mac, allow Transcript Grabber "
                              "in System Settings > Privacy & Security > Files and Folders.") from None
            saved += 1
            emit({"stage": "item", "index": index, "state": "saved", "url": link, "title": result["title"] or name,
                  "words": result["words"], "file": name})
        if index < total:
            time.sleep(random.uniform(*pause))
    combined = rebuild_combined(folder) if saved or skipped else None
    return {"stage": "batch-done", "saved": saved, "skipped": skipped, "failed": failed, "folder": str(folder),
            "combined": combined.name if combined else None, "stopped": stopped}


def finish_job(files: list[Path], folder: Path, tools: Tools, emit, *, kind: str, site: str,
               title: str, creator: str, source: str, filename: str,
               ident: str | None = None, published: str | None = None, handle: str | None = None) -> dict:
    kinds = [stream_types(f, tools) for f in files]
    voiced = [f for f, found in zip(files, kinds) if "audio" in found]
    if not voiced:
        if not any(kinds):
            raise Failure("That file is not a video or audio file.")
        raise Failure("This video has no sound, so there is nothing to transcribe.")
    seconds = sum(audio_seconds(f, tools) for f in voiced)
    emit({"stage": "transcribing", "message": f"Transcribing {fmt_duration(seconds)} of audio…"})
    parts, language = [], ""
    for n, f in enumerate(files, 1):
        if f in voiced:
            segments, detected = transcribe(f, folder / f"{n:03d}", tools)
            parts.append(segments)
            language = language or detected
        else:
            parts.append([])

    words = sum(len(seg["text"].split()) for part in parts for seg in part)
    if words == 0:
        raise Failure("No speech found. This video may be music only.")
    language = LANGUAGES.get(language, language)
    facts = [fmt_duration(seconds), language, f"transcribed {dt.date.today().isoformat()}"]
    header = "\n".join(line for line in (title, creator, source, " · ".join(f for f in facts if f)) if line)
    return {
        "stage": "done",
        "kind": kind,
        "id": ident,
        "published": published,
        "handle": handle,
        "site": site,
        "title": title,
        "creator": creator,
        "source": source,
        "seconds": seconds,
        "duration_label": fmt_duration(seconds),
        "language": language,
        "words": words,
        "notice": "Very little speech found. This video may be mostly music." if words < MIN_WORDS else None,
        "filename": filename,
        "header": header,
        "plain": compose(parts, plain_text),
        "timed": compose(parts, timed_text),
    }


# ---------------------------------------------------------------- web app

JOB_LOCK = threading.Lock()  # one transcription at a time; they share the GPU


class Handler(BaseHTTPRequestHandler):
    server_version = "TranscriptGrabber/1"

    def log_message(self, format: str, *args) -> None:
        pass  # keep the terminal for job output

    def allowed(self) -> bool:
        # The Host check blocks DNS rebinding; the Origin check blocks other sites.
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        origin = self.headers.get("Origin")
        return self.headers.get("Host") in hosts and (origin is None or origin in {f"http://{h}" for h in hosts})

    def reply(self, status: int, body: bytes, kind: str = "text/plain; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if not self.allowed():
            return self.reply(403, b"Forbidden")
        path = urlparse(self.path).path
        if path == "/":
            return self.reply(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/api/ping":
            return self.reply(200, b'{"app": "transcript-grabber"}', "application/json")
        self.reply(404, b"Not found")

    def do_POST(self) -> None:
        if not self.allowed():
            return self.reply(403, b"Forbidden")
        path = urlparse(self.path).path
        if path == "/api/transcribe":
            return self.transcribe_link()
        if path == "/api/transcribe-file":
            return self.transcribe_file()
        if path == "/api/batch":
            return self.transcribe_batch()
        self.reply(404, b"Not found")

    def transcribe_batch(self) -> None:
        if self.headers.get_content_type() != "application/json":
            return self.reply(415, b"Send JSON")
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= 400_000:
            return self.reply(400, b"Bad request")
        try:
            body = json.loads(self.rfile.read(length))
            links = [str(link) for link in body.get("links") or []]
            label = str(body.get("label") or "Batch")
        except (ValueError, AttributeError, TypeError):
            return self.reply(400, b"Bad request")
        if not 0 < len(links) <= MAX_BATCH:
            return self.reply(400, f"Send between 1 and {MAX_BATCH} links.".encode())
        self.stream(label, lambda emit: run_batch(links, label, self.server.tools, emit))

    def transcribe_link(self) -> None:
        # Only JSON is accepted, so a cross-site form cannot post here and a
        # cross-site fetch needs a CORS preflight that this server never grants.
        if self.headers.get_content_type() != "application/json":
            return self.reply(415, b"Send JSON")
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= 10_000:
            return self.reply(400, b"Bad request")
        try:
            url = str(json.loads(self.rfile.read(length)).get("url") or "")
        except (ValueError, AttributeError):
            return self.reply(400, b"Bad request")
        self.stream(url, lambda emit: autosave(run_job(url, self.server.tools, emit)))

    def transcribe_file(self) -> None:
        # The raw file is the body. Forms cannot send application/octet-stream, and a
        # cross-site fetch with it needs a CORS preflight, so other sites cannot upload.
        if self.headers.get_content_type() != "application/octet-stream":
            return self.reply(415, b"Send the file as application/octet-stream")
        length = int(self.headers.get("Content-Length") or 0)
        # Only the base name is kept, so a crafted name cannot point anywhere else.
        name = Path(unquote(self.headers.get("X-Filename") or "")).name or "video"
        suffix = re.sub(r"[^.a-z0-9]", "", Path(name).suffix.lower())[:8]
        with tempfile.TemporaryDirectory(prefix="transcript-grabber-upload-") as tmp:
            try:
                upload = save_upload(self.rfile, length, Path(tmp) / f"upload{suffix}")
            except Failure as err:
                self.close_connection = True  # do not read a body that was refused
                return self.reply(413, str(err).encode())
            self.stream(name, lambda emit: autosave(run_file_job(upload, name, self.server.tools, emit)))

    def stream(self, label: str, job) -> None:
        """Answer with NDJSON progress events while job(emit) runs, one job at a time."""
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()

        def emit(event: dict) -> None:
            self.wfile.write(json.dumps(event).encode() + b"\n")
            self.wfile.flush()

        try:
            if not JOB_LOCK.acquire(blocking=False):
                emit({"stage": "waiting", "message": "Waiting for the previous one to finish…"})
                JOB_LOCK.acquire()
            try:
                emit(job(emit))
            finally:
                JOB_LOCK.release()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the page was closed mid-job
        except Failure as err:
            print(f"{label}\n  {err}", file=sys.stderr)
            self.try_emit(emit, {"stage": "error", "message": str(err)})
        except Exception as err:
            traceback.print_exc()
            self.try_emit(emit, {"stage": "error", "message": f"Unexpected error: {err}"})

    @staticmethod
    def try_emit(emit, event: dict) -> None:
        try:
            emit(event)
        except OSError:
            pass


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, tools: Tools | None):
        super().__init__(("127.0.0.1", port), Handler)
        self.tools = tools


def already_running(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/ping", timeout=2) as resp:
            return json.load(resp).get("app") == "transcript-grabber"
    except (OSError, ValueError):
        return False


def serve(port: int, tools: Tools, open_browser: bool, exit_with_stdin: bool = False) -> int:
    url = f"http://127.0.0.1:{port}"
    try:
        server = Server(port, tools)
    except OSError:
        print(f"Port {port} is in use. Start with --port to pick another.", file=sys.stderr)
        return 1
    if exit_with_stdin:
        # The Mac app holds this pipe open. When the app quits or crashes, the
        # pipe closes and the server exits with it instead of lingering.
        threading.Thread(target=lambda: (sys.stdin.read(), os._exit(0)), daemon=True).start()
    print(f"Transcript Grabber is running at {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.6, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
    return 0


# ---------------------------------------------------------------- command line

def cli(items: list[str], tools: Tools, out_dir: Path) -> int:
    failures = 0
    for raw in items:
        say = lambda event: print(event["message"], file=sys.stderr)  # noqa: E731
        path = local_file(raw)
        try:
            result = run_file_job(path, path.name, tools, say) if path else run_job(raw, tools, say)
        except Failure as err:
            print(f"{raw}\n  {err}", file=sys.stderr)
            failures += 1
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / result["filename"]
        path.write_text(f"{result['header']}\n\n{result['plain']}\n", encoding="utf-8")
        if result["notice"]:
            print(result["notice"], file=sys.stderr)
        print(result["plain"])
        print(f"Saved {path}", file=sys.stderr)
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Paste a video link or drop a video file, get the transcript as text.")
    parser.add_argument("urls", nargs="*", help="links or video files to transcribe from the command line")
    parser.add_argument("--out", type=Path, default=Path.home() / "Downloads",
                        help="folder for .txt files in command-line mode (default: ~/Downloads)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-open", action="store_true", help="do not open the browser")
    parser.add_argument("--exit-with-stdin", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    url = f"http://127.0.0.1:{args.port}"
    if not args.urls and already_running(url):
        print(f"Transcript Grabber is already running at {url}")
        if not args.no_open:
            webbrowser.open(url)
        return 0
    try:
        tools = Tools.load()
    except Failure as err:
        print(err, file=sys.stderr)
        return 1
    if args.urls:
        return cli(args.urls, tools, args.out.expanduser())
    return serve(args.port, tools, open_browser=not args.no_open, exit_with_stdin=args.exit_with_stdin)


if __name__ == "__main__":
    sys.exit(main())
