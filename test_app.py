"""Unit tests. Standard library only: python3 -m unittest -v"""

import http.client
import json
import threading
import unittest

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

    def test_bad_link_streams_an_error(self):
        body = json.dumps({"url": "not a link"}).encode()
        headers = self.local(Origin=f"http://127.0.0.1:{self.port}", **{"Content-Type": "application/json"})
        status, data = self.request("POST", "/api/transcribe", headers, body)
        self.assertEqual(status, 200)
        event = json.loads(data.decode().strip().splitlines()[-1])
        self.assertEqual(event, {"stage": "error", "message": "Paste a link that starts with https://"})


if __name__ == "__main__":
    unittest.main()
