"""Unit tests. Standard library only: python3 -m unittest -v"""

import http.client
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

import app


def seg(start, text, length=2.0):
    return {"start": float(start), "end": float(start) + length, "text": text}


class TextTests(unittest.TestCase):
    def test_duration(self):
        self.assertEqual(app.fmt_duration(0), "0s")
        self.assertEqual(app.fmt_duration(54.4), "54s")
        self.assertEqual(app.fmt_duration(323), "5m23s")
        self.assertEqual(app.fmt_duration(3900), "1h05m")

    def test_clock(self):
        self.assertEqual(app.fmt_clock(12.7), "00:12")
        self.assertEqual(app.fmt_clock(3723), "1:02:03")

    def test_clean_strips_sound_tags(self):
        self.assertEqual(app.clean(" *music*  Hello [Music] there ♪ "), "Hello there")

    def test_repeated_lines_collapse_to_two(self):
        segments = [seg(i, "Thank you.") for i in range(6)] + [seg(6, "Bye.")]
        self.assertEqual([s["text"] for s in app.drop_loops(segments)], ["Thank you.", "Thank you.", "Bye."])

    def test_whisper_json(self):
        data = {"transcription": [
            {"offsets": {"from": 0, "to": 2500}, "text": " Hello there."},
            {"offsets": {"from": 2500, "to": 4000}, "text": " *music* "},
            {"offsets": {"from": 4000, "to": 6000}, "text": " Bye."},
        ]}
        self.assertEqual(app.segments_from(data), [seg(0, "Hello there.", 2.5), seg(4, "Bye.")])

    def test_paragraph_breaks_at_sentence_end_after_limit(self):
        segments = [seg(0, "One two."), seg(2, "Three four"), seg(4, "five."), seg(6, "Six.")]
        self.assertEqual(app.plain_text(segments, para_chars=10), "One two. Three four five.\n\nSix.")

    def test_paragraph_breaks_on_long_pause(self):
        segments = [seg(0, "Before"), seg(10, "after")]
        self.assertEqual(app.plain_text(segments), "Before\n\nafter")

    def test_timed(self):
        self.assertEqual(app.timed_text([seg(0, "Hi."), seg(75, "Later.")]), "[00:00] Hi.\n[01:15] Later.")

    def test_carousel_sections(self):
        text = app.compose([[seg(0, "First.")], []], app.plain_text)
        self.assertEqual(text, "Video 1\nFirst.\n\nVideo 2\n(no speech)")


class LinkTests(unittest.TestCase):
    def test_link_inside_shared_text_drops_share_tag(self):
        pasted = "Look at this https://www.instagram.com/reel/DdPsDCWRT-u/?igsh=abc."
        self.assertEqual(app.pick_url(pasted), "https://www.instagram.com/reel/DdPsDCWRT-u/")

    def test_query_kept_for_other_sites(self):
        self.assertEqual(app.pick_url("https://www.youtube.com/watch?v=abc"), "https://www.youtube.com/watch?v=abc")

    def test_link_without_scheme(self):
        self.assertEqual(app.pick_url("instagram.com/p/abc123/"), "https://instagram.com/p/abc123/")

    def test_profile_link_rejected(self):
        with self.assertRaises(app.Failure):
            app.pick_url("https://www.instagram.com/nasa/")

    def test_no_link(self):
        with self.assertRaises(app.Failure):
            app.pick_url("hello")

    def test_youtube_single_videos_pass(self):
        for link in ("https://www.youtube.com/watch?v=RU-i523fflU", "https://youtu.be/RU-i523fflU",
                     "https://www.youtube.com/shorts/myZ9kn9MIWQ", "https://m.youtube.com/watch?v=abc"):
            self.assertEqual(app.pick_url(link), link)

    def test_youtube_channels_and_playlists_rejected(self):
        for link in ("https://www.youtube.com/@NASA", "https://www.youtube.com/@NASA/shorts",
                     "https://www.youtube.com/playlist?list=PL1", "https://www.youtube.com/channel/UC123",
                     "https://www.youtube.com/results?search_query=nasa"):
            with self.assertRaises(app.Failure, msg=link):
                app.pick_url(link)

    def test_site_names(self):
        self.assertEqual(app.site_name("https://youtu.be/x"), "YouTube")
        self.assertEqual(app.site_name("https://m.youtube.com/watch?v=x"), "YouTube")
        self.assertEqual(app.site_name("https://www.instagram.com/p/x/"), "Instagram")
        self.assertEqual(app.site_name("https://vimeo.com/1"), "vimeo.com")

    def test_other_sites_pass_through(self):
        self.assertEqual(app.pick_url("https://www.tiktok.com/@a/video/1"), "https://www.tiktok.com/@a/video/1")


class MetadataTests(unittest.TestCase):
    def test_generic_title_falls_back_to_caption(self):
        meta = {"title": "Video by nasa", "description": "NASA is inbound to the NFL\n#space"}
        self.assertEqual(app.title_of(meta), "NASA is inbound to the NFL")

    def test_creator(self):
        self.assertEqual(app.creator_of({"uploader": "NASA", "channel": "nasa"}), "NASA")
        self.assertEqual(app.creator_of({"uploader": "Jane Doe", "channel": "janedoe"}), "Jane Doe (@janedoe)")
        self.assertEqual(app.creator_of({"uploader": "Jane", "uploader_id": "12345"}), "Jane")

    def test_filename(self):
        self.assertEqual(app.filename_for({"channel": "nasa", "id": "DdPsDCWRT-u"}), "nasa-DdPsDCWRT-u.txt")
        self.assertEqual(app.filename_for({"uploader": "Jane Doe!", "id": "a/b"}), "Jane-Doe-a-b.txt")

    def test_errors_explained(self):
        login = "ERROR: [Instagram] abc: Requested content is not available, rate-limit reached or login required"
        self.assertIn("without a login", app.explain(login))
        self.assertIn("without a login", app.explain("ERROR: [Instagram] abc: Instagram sent an empty media response. Check"))
        self.assertEqual(app.explain("ERROR: Unsupported URL: https://x.test/"), "This link is not a video page the app can read.")
        self.assertIn("deleted", app.explain("ERROR: [Instagram] abc: Unable to download webpage: HTTP Error 404: Not Found"))
        self.assertTrue(app.explain("ERROR: [Instagram] abc: Something odd.").startswith("Something odd."))
        age = "ERROR: [youtube] abc: Sign in to confirm your age. This video may be inappropriate for some users."
        self.assertTrue(app.explain(age, "YouTube").startswith("YouTube would not share this video without a login."))


def fake_yt_dlp(errors):
    """A stand-in yt_dlp module: each extraction raises the next queued error, then succeeds."""
    calls = []

    class DownloadError(Exception):
        pass

    class YoutubeDL:
        def __init__(self, options):
            self.folder = Path(options["outtmpl"]).parent

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download):
            calls.append(url)
            if errors:
                raise DownloadError(errors.pop(0))
            path = self.folder / "001-abc.webm"
            path.write_bytes(b"audio")
            return {"id": "abc", "requested_downloads": [{"filepath": str(path)}]}

    module = types.ModuleType("yt_dlp")
    module.YoutubeDL = YoutubeDL
    module.utils = types.SimpleNamespace(DownloadError=DownloadError)
    return module, calls


class FetchRetryTests(unittest.TestCase):
    """YouTube sometimes answers a fresh media URL with 403; one new extraction usually fixes it."""

    def test_403_retries_once(self):
        module, calls = fake_yt_dlp(["ERROR: unable to download video data: HTTP Error 403: Forbidden"])
        with mock.patch.dict(sys.modules, {"yt_dlp": module}), tempfile.TemporaryDirectory() as tmp:
            _, files = app.fetch_audio("https://youtu.be/abc", Path(tmp))
            names = [f.name for f in files]
        self.assertEqual(len(calls), 2)
        self.assertEqual(names, ["001-abc.webm"])

    def test_second_403_gives_up(self):
        forbidden = "ERROR: unable to download video data: HTTP Error 403: Forbidden"
        module, calls = fake_yt_dlp([forbidden, forbidden])
        with mock.patch.dict(sys.modules, {"yt_dlp": module}), tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(app.Failure):
                app.fetch_audio("https://youtu.be/abc", Path(tmp))
        self.assertEqual(len(calls), 2)

    def test_other_errors_do_not_retry(self):
        module, calls = fake_yt_dlp(["ERROR: [youtube] abc: Private video. Sign in if you've been granted access"])
        with mock.patch.dict(sys.modules, {"yt_dlp": module}), tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(app.Failure) as caught:
                app.fetch_audio("https://youtu.be/abc", Path(tmp))
        self.assertEqual(len(calls), 1)
        self.assertIn("YouTube would not share", str(caught.exception))


class FileTests(unittest.TestCase):
    def test_txt_name_keeps_unicode_and_drops_paths(self):
        self.assertEqual(app.file_txt_name("Entrevista año.mov"), "Entrevista-año.txt")
        self.assertEqual(app.file_txt_name("../../etc/passwd"), "passwd.txt")
        self.assertEqual(app.file_txt_name("clip.MOV"), "clip.txt")
        self.assertEqual(app.file_txt_name(""), "transcript.txt")

    def test_local_file_detection(self):
        with tempfile.NamedTemporaryFile(suffix=".mov") as handle:
            self.assertEqual(app.local_file(handle.name), Path(handle.name))
        self.assertIsNone(app.local_file("https://youtu.be/abc"))
        self.assertIsNone(app.local_file("/no/such/file.mov"))

    def test_upload_is_saved_in_full(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = app.save_upload(io.BytesIO(b"x" * 3_000_000), 3_000_000, Path(tmp) / "upload.mov")
            self.assertEqual(dest.stat().st_size, 3_000_000)

    def test_short_upload_fails(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(app.Failure):
            app.save_upload(io.BytesIO(b"abc"), 10, Path(tmp) / "upload.mov")

    def test_empty_upload_fails(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(app.Failure):
            app.save_upload(io.BytesIO(b""), 0, Path(tmp) / "upload.mov")

    def test_oversized_upload_refused_before_reading(self):
        stream = io.BytesIO(b"abc")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(app, "MAX_UPLOAD", 2):
            with self.assertRaises(app.Failure):
                app.save_upload(stream, 3, Path(tmp) / "upload.mov")
        self.assertEqual(stream.tell(), 0)


class GuardTests(unittest.TestCase):
    """The server spends compute and fetches URLs, so other sites must not reach it."""

    @classmethod
    def setUpClass(cls):
        cls.server = app.Server(0, None)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, method, path, headers, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        if body is not None:
            headers = {**headers, "Content-Length": str(len(body))}
        for name, value in headers.items():
            conn.putheader(name, value)
        conn.endheaders(body)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def local(self, **extra):
        return {"Host": f"127.0.0.1:{self.port}", **extra}

    def test_page_loads(self):
        status, body = self.request("GET", "/", self.local())
        self.assertEqual(status, 200)
        self.assertIn(b"What did they actually say?", body)

    def test_launcher_detects_running_app(self):
        self.assertTrue(app.already_running(f"http://127.0.0.1:{self.port}"))

    def test_rebound_host_blocked(self):
        status, _ = self.request("GET", "/", {"Host": f"attacker.example:{self.port}"})
        self.assertEqual(status, 403)

    def test_cross_site_post_blocked(self):
        body = json.dumps({"url": "https://www.instagram.com/reel/x/"}).encode()
        headers = self.local(Origin="https://attacker.example", **{"Content-Type": "application/json"})
        status, _ = self.request("POST", "/api/transcribe", headers, body)
        self.assertEqual(status, 403)

    def test_form_post_blocked(self):
        body = b"url=https://www.instagram.com/reel/x/"
        status, _ = self.request("POST", "/api/transcribe", self.local(**{"Content-Type": "application/x-www-form-urlencoded"}), body)
        self.assertEqual(status, 415)

    def test_file_upload_needs_octet_stream(self):
        status, _ = self.request("POST", "/api/transcribe-file", self.local(**{"Content-Type": "application/json"}), b"{}")
        self.assertEqual(status, 415)

    def test_cross_site_file_upload_blocked(self):
        headers = self.local(Origin="https://attacker.example", **{"Content-Type": "application/octet-stream"})
        status, _ = self.request("POST", "/api/transcribe-file", headers, b"data")
        self.assertEqual(status, 403)

    def test_empty_file_upload_refused(self):
        status, body = self.request("POST", "/api/transcribe-file", self.local(**{"Content-Type": "application/octet-stream"}), b"")
        self.assertEqual(status, 413)
        self.assertIn(b"empty", body)

    def test_batch_needs_json(self):
        status, _ = self.request("POST", "/api/batch", self.local(**{"Content-Type": "text/plain"}), b"x")
        self.assertEqual(status, 415)

    def test_batch_needs_links(self):
        body = json.dumps({"links": [], "label": "nasa"}).encode()
        status, _ = self.request("POST", "/api/batch", self.local(**{"Content-Type": "application/json"}), body)
        self.assertEqual(status, 400)

    def test_batch_size_is_capped(self):
        body = json.dumps({"links": ["https://youtu.be/x"] * (app.MAX_BATCH + 1), "label": "x"}).encode()
        status, _ = self.request("POST", "/api/batch", self.local(**{"Content-Type": "application/json"}), body)
        self.assertEqual(status, 400)

    def test_bad_link_streams_an_error(self):
        body = json.dumps({"url": "not a link"}).encode()
        headers = self.local(Origin=f"http://127.0.0.1:{self.port}", **{"Content-Type": "application/json"})
        status, data = self.request("POST", "/api/transcribe", headers, body)
        self.assertEqual(status, 200)
        event = json.loads(data.decode().strip().splitlines()[-1])
        self.assertEqual(event, {"stage": "error", "message": "Paste a link that starts with https://"})


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(shutil.which("ffprobe") or os.path.exists("/opt/homebrew/bin/ffprobe"), "needs ffprobe")
class FileJobTests(unittest.TestCase):
    def test_non_media_upload_is_explained(self):
        tools = app.Tools(ffmpeg=app.need("ffmpeg", ""), ffprobe=app.need("ffprobe", ""),
                          whisper="/usr/bin/false", model=Path("/dev/null"))
        server = app.Server(0, tools)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
            conn.request("POST", "/api/transcribe-file", body=b"this is not a video", headers={
                "Host": f"127.0.0.1:{port}",
                "Content-Type": "application/octet-stream",
                "X-Filename": "notes%20draft.mov",
            })
            lines = conn.getresponse().read().decode().strip().splitlines()
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
        events = [json.loads(line) for line in lines]
        self.assertIn("notes draft.mov", events[0]["message"])
        self.assertEqual(events[-1], {"stage": "error", "message": "That file is not a video or audio file."})


class BatchHelperTests(unittest.TestCase):
    def test_post_ids(self):
        self.assertEqual(app.post_id("https://www.instagram.com/nasa/reel/DdPsDCWRT-u/"), "DdPsDCWRT-u")
        self.assertEqual(app.post_id("https://www.instagram.com/p/BQ0eAlwhDrw/"), "BQ0eAlwhDrw")
        self.assertEqual(app.post_id("https://www.youtube.com/watch?v=RU-i523fflU&t=3"), "RU-i523fflU")
        self.assertEqual(app.post_id("https://youtu.be/RU-i523fflU"), "RU-i523fflU")
        self.assertEqual(app.post_id("https://www.youtube.com/shorts/myZ9kn9MIWQ"), "myZ9kn9MIWQ")
        self.assertIsNone(app.post_id("https://vimeo.com/1"))

    def test_folder_names_are_safe(self):
        self.assertEqual(app.folder_name("nasa"), "nasa")
        self.assertEqual(app.folder_name("a/b:c*?"), "a b c")
        self.assertEqual(app.folder_name("..."), "Batch")

    def test_batch_file_name(self):
        result = {"published": "2026-09-12", "title": "Today's flyover: part 1/2", "id": "DdPsDCWRT-u"}
        self.assertEqual(app.batch_file_name(result, "x"), "2026-09-12 Today's flyover part 1 2 [DdPsDCWRT-u].txt")
        self.assertEqual(app.batch_file_name({}, "abc"), "undated [abc].txt")
        long = {"published": "2026-09-13", "title": "Today's flyover in Pittsburgh is just the beginning of a season", "id": "X"}
        self.assertEqual(app.batch_file_name(long, "x"), "2026-09-13 Today's flyover in Pittsburgh is just the [X].txt")


def fake_run_job(outcomes):
    """A stand-in run_job: each call pops the next outcome, a result title or a Failure message."""
    calls = []

    def run_job(link, tools, emit):
        calls.append(link)
        emit({"stage": "fetching", "message": "Fetching audio from instagram.com…"})
        outcome = outcomes.pop(0)
        if outcome.startswith("FAIL:"):
            raise app.Failure(outcome[5:])
        ident = app.post_id(link)
        return {"title": outcome, "id": ident, "published": "2026-09-0" + str(len(calls)), "words": 10,
                "header": outcome, "plain": f"words of {outcome}"}

    return run_job, calls


class RunBatchTests(unittest.TestCase):
    LOGIN = "FAIL:Instagram would not share this video without a login. It may be private."

    def batch(self, links, outcomes, root):
        run_job, calls = fake_run_job(outcomes)
        events = []
        with mock.patch.object(app, "run_job", run_job), mock.patch.object(app, "SAVE_ROOT", root):
            done = app.run_batch(links, "nasa", None, events.append, pause=(0, 0))
        return done, events, calls

    def links(self, *ids):
        return [f"https://www.instagram.com/reel/{i}/" for i in ids]

    def test_saves_each_transcript_and_a_combined_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            done, events, calls = self.batch(self.links("A1", "B2"), ["First", "Second"], root)
            folder = root / "nasa"
            names = sorted(f.name for f in folder.iterdir())
            combined = (folder / "nasa - all transcripts.txt").read_text()
        self.assertEqual(names, ["2026-09-01 First [A1].txt", "2026-09-02 Second [B2].txt", "nasa - all transcripts.txt"])
        self.assertLess(combined.index("words of First"), combined.index("words of Second"))
        self.assertEqual((done["saved"], done["skipped"], done["failed"], done["stopped"]), (2, 0, 0, None))
        self.assertEqual(events[0]["stage"], "batch")
        self.assertIn("1 of 2 · Fetching audio", [e.get("message", "") for e in events if e["stage"] == "progress"][0])

    def test_already_saved_posts_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "nasa").mkdir()
            (root / "nasa" / "2026-01-01 Old [A1].txt").write_text("old")
            done, events, calls = self.batch(self.links("A1", "B2"), ["Second"], root)
        self.assertEqual(calls, self.links("B2"))
        self.assertEqual((done["saved"], done["skipped"]), (1, 1))

    def test_two_login_refusals_in_a_row_stop_the_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            done, events, calls = self.batch(self.links("A1", "B2", "C3", "D4"), [self.LOGIN, self.LOGIN, "never"], Path(tmp))
        self.assertEqual(len(calls), 2)
        self.assertEqual(done["failed"], 2)
        self.assertIn("asking for a login", done["stopped"])

    def test_one_failure_does_not_stop_the_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            done, events, calls = self.batch(self.links("A1", "B2", "C3"), ["FAIL:This post has no video.", "Two", "Three"], Path(tmp))
        self.assertEqual((done["saved"], done["failed"], done["stopped"]), (2, 1, None))
