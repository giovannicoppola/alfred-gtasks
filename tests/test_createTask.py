"""
Offline tests for source/createTask.py against a fake Google API/OAuth server.

Run with the same interpreter Alfred uses:
    /usr/bin/python3 -B tests/test_createTask.py
"""
import contextlib
import datetime
import http.server
import io
import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request

sys.dont_write_bytecode = True  # keep __pycache__ out of the workflow folder
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "source"))
import createTask as ct  # noqa: E402

NOW = datetime.datetime(2026, 10, 6, 14, 30)  # a Tuesday


class ParseDateTests(unittest.TestCase):
    def parse(self, text):
        return ct.parse_date_from_string(text, now=NOW)

    def test_cases(self):
        d = datetime.datetime
        cases = {
            "Call plumber": ("Call plumber", None),
            "Call plumber tomorrow": ("Call plumber", d(2026, 10, 7)),
            "Call plumber today": ("Call plumber", d(2026, 10, 6)),
            "Order today's paper": ("Order today's paper", None),
            "Read chapters 3-5 tomorrow": ("Read chapters 3-5", d(2026, 10, 7)),
            "Buy 2-3 apples": ("Buy 2-3 apples", None),
            "Dentist 12/15": ("Dentist", d(2026, 12, 15)),
            "Dentist 12-15:09:30": ("Dentist", d(2026, 12, 15, 9, 30)),
            "Dentist 10-06": ("Dentist", d(2026, 10, 6)),          # today, not next year
            "Dentist 10-05": ("Dentist", d(2027, 10, 5)),          # passed -> next year
            "Standup 10-06:09:00": ("Standup", d(2027, 10, 6, 9, 0)),  # time passed today
            "Report 2026-12-03 draft": ("Report draft", d(2026, 12, 3)),
            "Report 2026-13-03": ("Report 2026-13-03", None),      # invalid date stays in title
            "Party next friday": ("Party", d(2026, 10, 9)),
            "Party tuesday": ("Party", d(2026, 10, 13)),           # today's weekday -> +7
            "Pay rent in 3 days": ("Pay rent", d(2026, 10, 9)),
            "Renew in 2 weeks": ("Renew", d(2026, 10, 20)),
            "Renew next month": ("Renew", d(2026, 11, 6)),
            "Mondays are hard": ("Mondays are hard", None),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.parse(text), expected)

    def test_add_months_clamps_day(self):
        self.assertEqual(ct._add_months(datetime.datetime(2026, 1, 31), 1), datetime.datetime(2026, 2, 28))


class FakeGoogle(http.server.BaseHTTPRequestHandler):
    """Records requests; responses are scripted per test via FakeGoogle.script."""
    requests = []
    script = {}  # (method, path-prefix) -> list of (status, body) consumed in order

    def _handle(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        parsed = urllib.parse.urlparse(self.path)
        FakeGoogle.requests.append({
            "method": self.command, "path": parsed.path,
            "query": dict(urllib.parse.parse_qsl(parsed.query)),
            "auth": self.headers.get("Authorization"), "body": raw.decode(),
        })
        for (method, prefix), responses in FakeGoogle.script.items():
            if method == self.command and parsed.path.startswith(prefix) and responses:
                status, body = responses.pop(0)
                if status == "hang":
                    time.sleep(body)
                    return
                payload = json.dumps(body).encode() if body is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
        self.send_response(500)
        self.end_headers()

    do_GET = do_POST = do_PATCH = do_DELETE = _handle

    def log_message(self, format, *args):
        pass


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeGoogle)
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeGoogle.requests, FakeGoogle.script = [], {}
        self.tmp = tempfile.TemporaryDirectory()
        self.workflow = os.path.join(self.tmp.name, "workflow")
        os.makedirs(self.workflow)
        self.saved = {k: getattr(ct, k) for k in (
            "API_BASE", "DATA_DIR", "TOKEN_PATH", "LEGACY_TOKEN_PATH", "LEGACY_CLIENT_PATH",
            "HTTP_TIMEOUT", "UNDER_ALFRED")}
        ct.API_BASE = self.base + "/tasks/v1"
        ct.DATA_DIR = os.path.join(self.tmp.name, "data")
        ct.TOKEN_PATH = os.path.join(ct.DATA_DIR, "token.json")
        ct.LEGACY_TOKEN_PATH = os.path.join(self.workflow, "token.json")
        ct.LEGACY_CLIENT_PATH = os.path.join(self.workflow, "credentials.json")
        ct.HTTP_TIMEOUT = 1

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(ct, k, v)
        self.tmp.cleanup()

    def token(self, expired=False):
        expiry = ct._utcnow() + datetime.timedelta(hours=-1 if expired else 1)
        return {"token": "access-1", "refresh_token": "refresh-1", "expiry": expiry.isoformat() + "Z",
                "token_uri": self.base + "/token", "client_id": "cid", "client_secret": "secret"}

    def write_token(self, **kw):
        ct._write_token(self.token(**kw))

    def on(self, method, prefix, *responses):
        FakeGoogle.script.setdefault((method, prefix), []).extend(responses)

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = ct.main(list(argv))
        return code, out.getvalue()

    def alfred_items(self, *argv):
        code, out = self.run_cli(*argv)
        self.assertEqual(code, 0)
        return json.loads(out)["items"]

    # --- list (Script Filter) -------------------------------------------------

    def test_list_signed_out_offers_sign_in_without_opening_browser(self):
        items = self.alfred_items("list", "--alfred")
        self.assertEqual(items[0]["arg"], "auth")
        self.assertTrue(items[0]["valid"])
        self.assertEqual(FakeGoogle.requests, [])

    def test_list_paginates_shows_mods_and_uses_default_list(self):
        self.write_token()
        self.on("GET", "/tasks/v1/lists/@default/tasks",
                (200, {"items": [{"id": "a", "title": "One"}], "nextPageToken": "p2"}),
                (200, {"items": [{"id": "b", "title": "Two", "due": "2026-10-07T00:00:00.000Z"}]}))
        items = self.alfred_items("list", "--alfred")
        self.assertEqual([i["title"] for i in items], ["One", "Two"])
        self.assertFalse(items[0]["valid"])
        self.assertEqual(items[0]["mods"]["cmd"]["arg"], "a")
        self.assertEqual(items[1]["mods"]["cmd+alt"]["arg"], "b")
        self.assertIn("Due: 2026-10-07", items[1]["subtitle"])
        self.assertEqual(FakeGoogle.requests[1]["query"]["pageToken"], "p2")
        self.assertEqual(FakeGoogle.requests[0]["auth"], "Bearer access-1")

    def test_list_refreshes_expired_token_and_stores_it(self):
        self.write_token(expired=True)
        self.on("POST", "/token", (200, {"access_token": "access-2", "expires_in": 3600}))
        self.on("GET", "/tasks/v1/", (200, {"items": []}))
        items = self.alfred_items("list", "--alfred")
        self.assertEqual(items[0]["title"], "No open tasks 🎉")
        self.assertEqual(FakeGoogle.requests[1]["auth"], "Bearer access-2")
        with open(ct.TOKEN_PATH) as f:
            saved = json.load(f)
        self.assertEqual((saved["token"], saved["refresh_token"]), ("access-2", "refresh-1"))

    def test_list_revoked_token_asks_to_sign_in_and_forgets_token(self):
        self.write_token(expired=True)
        self.on("POST", "/token", (400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}))
        items = self.alfred_items("list", "--alfred")
        self.assertEqual(items[0]["arg"], "auth")
        self.assertFalse(os.path.exists(ct.TOKEN_PATH))

    def test_list_retries_401_once_after_refresh(self):
        self.write_token()
        self.on("GET", "/tasks/v1/", (401, {"error": {"message": "stale"}}), (200, {"items": []}))
        self.on("POST", "/token", (200, {"access_token": "access-2", "expires_in": 3600}))
        self.alfred_items("list", "--alfred")
        self.assertEqual(FakeGoogle.requests[-1]["auth"], "Bearer access-2")

    def test_list_retries_503_then_succeeds(self):
        self.write_token()
        self.on("GET", "/tasks/v1/", (503, None), (200, {"items": [{"id": "a", "title": "One"}]}))
        items = self.alfred_items("list", "--alfred")
        self.assertEqual(items[0]["title"], "One")

    def test_list_timeout_is_an_error_item_not_a_hang(self):
        self.write_token()
        self.on("GET", "/tasks/v1/", ("hang", 3))
        start = time.monotonic()
        items = self.alfred_items("list", "--alfred")
        self.assertLess(time.monotonic() - start, 2.5)
        self.assertEqual(items[0]["title"], "Couldn't load your tasks")
        self.assertFalse(items[0]["valid"])

    def test_list_unreachable_host_is_an_error_item(self):
        self.write_token()
        ct.API_BASE = "http://127.0.0.1:9/tasks/v1"  # discard port: connection refused
        items = self.alfred_items("list", "--alfred")
        self.assertEqual(items[0]["title"], "Couldn't load your tasks")

    def test_list_requests_hidden_when_showing_completed(self):
        self.write_token()
        self.on("GET", "/tasks/v1/", (200, {"items": []}))
        ct.fetch_tasks(interactive=False, show_completed=True)
        self.assertEqual(FakeGoogle.requests[0]["query"]["showHidden"], "true")

    # --- token storage ------------------------------------------------------------

    def test_legacy_token_is_moved_out_of_workflow_folder_with_0600(self):
        with open(ct.LEGACY_TOKEN_PATH, "w") as f:
            json.dump(self.token(), f)
        self.on("GET", "/tasks/v1/", (200, {"items": []}))
        self.alfred_items("list", "--alfred")
        self.assertFalse(os.path.exists(ct.LEGACY_TOKEN_PATH))
        self.assertEqual(stat.S_IMODE(os.stat(ct.TOKEN_PATH).st_mode), 0o600)

    def test_corrupt_token_means_signed_out(self):
        os.makedirs(ct.DATA_DIR)
        with open(ct.TOKEN_PATH, "w") as f:
            f.write('{"refresh_tok')
        items = self.alfred_items("list", "--alfred")
        self.assertEqual(items[0]["arg"], "auth")

    # --- actions --------------------------------------------------------------------

    def test_create_without_date_sends_no_due(self):
        self.write_token()
        self.on("POST", "/tasks/v1/", (200, {"id": "n", "title": "Call plumber"}))
        code, out = self.run_cli("create", json.dumps({"title": "Call plumber", "due_datetime": None}))
        self.assertEqual(code, 0)
        self.assertNotIn("due", json.loads(FakeGoogle.requests[0]["body"]))
        self.assertEqual(out.strip(), "✅ Created: Call plumber")

    def test_create_with_time_keeps_date_and_puts_time_in_title(self):
        self.write_token()
        self.on("POST", "/tasks/v1/", (200, {"id": "n", "title": "Dentist at 09:30", "due": "2026-12-15T00:00:00.000Z"}))
        self.run_cli("create", json.dumps({"title": "Dentist", "due_datetime": "2026-12-15T09:30:00"}))
        body = json.loads(FakeGoogle.requests[0]["body"])
        self.assertEqual(body["due"], "2026-12-15T00:00:00.000Z")
        self.assertEqual(body["title"], "Dentist at 09:30")

    def test_create_is_not_retried(self):
        self.write_token()
        self.on("POST", "/tasks/v1/", (503, None), (200, {"id": "dup"}))
        code, out = self.run_cli("create", json.dumps({"title": "X", "due_datetime": None}))
        self.assertEqual(len(FakeGoogle.requests), 1)
        self.assertTrue(out.startswith("❌"))
        self.assertEqual(code, 1)

    def test_errors_exit_zero_under_alfred_so_notification_shows(self):
        ct.UNDER_ALFRED = True
        code, out = self.run_cli("create", "not json")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("❌ Invalid task data"))

    def test_complete_by_id_patches_only_status(self):
        self.write_token()
        task_id = "MDIzODQ2NzI2ODE4NDE4ODUyNzA6MTIzNDU2Nzg5MA"
        self.on("GET", "/tasks/v1/", (200, {"id": task_id, "title": "One", "notes": "edited on phone"}))
        self.on("PATCH", "/tasks/v1/", (200, {"id": task_id, "title": "One", "status": "completed"}))
        code, out = self.run_cli("complete", task_id)
        patch = FakeGoogle.requests[-1]
        self.assertEqual((patch["method"], json.loads(patch["body"])), ("PATCH", {"status": "completed"}))
        self.assertEqual(out.strip(), "🎯 Task completed: One")

    def test_delete_missing_id_reports_cleanly(self):
        self.write_token()
        task_id = "MDIzODQ2NzI2ODE4NDE4ODUyNzA6MTIzNDU2Nzg5MA"
        self.on("GET", f"/tasks/v1/lists/@default/tasks/{task_id}", (404, {"error": {"message": "Not Found"}}))
        self.on("GET", "/tasks/v1/lists/@default/tasks", (200, {"items": []}))
        code, out = self.run_cli("delete", task_id)
        self.assertTrue(out.startswith("❌ No task matches"))

    def test_title_match_ignores_empty_titles_and_needs_uniqueness(self):
        self.write_token()
        tasks = {"items": [{"id": "e", "title": ""}, {"id": "a", "title": "Pay rent"}, {"id": "b", "title": "Pay taxes"}]}
        self.on("GET", "/tasks/v1/", (200, tasks), (200, tasks))
        with self.assertRaises(ct.WorkflowError):
            ct.find_task("pay", include_completed=False)
        self.assertEqual(ct.find_task("rent", include_completed=False)["id"], "a")


    # --- interactive sign-in --------------------------------------------------------

    def fake_browser(self, tamper=None):
        """Stand in for /usr/bin/open: follow the consent redirect like a browser would."""
        def run(cmd, check=False):
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(cmd[1]).query))
            self.auth_query = query
            params = {"code": "the-code", "state": query["state"]}
            if tamper:
                params = tamper(params)
            url = query["redirect_uri"] + "?" + urllib.parse.urlencode(params)
            threading.Thread(target=urllib.request.urlopen, args=(url,), daemon=True).start()
        return run

    def authorize_with(self, browser):
        with open(ct.LEGACY_CLIENT_PATH, "w") as f:
            json.dump({"installed": {"client_id": "cid", "client_secret": "secret",
                                     "token_uri": self.base + "/token"}}, f)
        saved = ct.subprocess.run
        ct.subprocess.run = browser
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                return ct.authorize()
        finally:
            ct.subprocess.run = saved

    def test_authorize_exchanges_code_with_pkce_and_stores_token(self):
        self.on("POST", "/token", (200, {"access_token": "a", "refresh_token": "r", "expires_in": 3600}))
        token = self.authorize_with(self.fake_browser())
        form = dict(urllib.parse.parse_qsl(FakeGoogle.requests[0]["body"]))
        self.assertEqual(form["code"], "the-code")
        self.assertEqual(form["redirect_uri"], self.auth_query["redirect_uri"])
        self.assertTrue(form["code_verifier"])
        self.assertEqual(self.auth_query["code_challenge_method"], "S256")
        self.assertEqual((self.auth_query["access_type"], self.auth_query["prompt"]), ("offline", "consent"))
        self.assertEqual(token["refresh_token"], "r")
        self.assertEqual(stat.S_IMODE(os.stat(ct.TOKEN_PATH).st_mode), 0o600)

    def test_authorize_rejects_state_mismatch(self):
        with self.assertRaisesRegex(ct.AuthRequired, "did not match"):
            self.authorize_with(self.fake_browser(lambda p: dict(p, state="forged")))
        self.assertEqual(FakeGoogle.requests, [])

    def test_authorize_reports_denied_consent(self):
        with self.assertRaisesRegex(ct.AuthRequired, "access_denied"):
            self.authorize_with(self.fake_browser(lambda p: {"error": "access_denied", "state": p["state"]}))

    def test_authorize_times_out(self):
        saved = ct.AUTH_TIMEOUT
        ct.AUTH_TIMEOUT = 1.5
        try:
            with self.assertRaisesRegex(ct.AuthRequired, "timed out"):
                self.authorize_with(lambda cmd, check=False: None)  # user never finishes
        finally:
            ct.AUTH_TIMEOUT = saved


class ClientFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = ct.LEGACY_CLIENT_PATH
        ct.LEGACY_CLIENT_PATH = os.path.join(self.tmp.name, "credentials.json")

    def tearDown(self):
        ct.LEGACY_CLIENT_PATH = self.saved
        self.tmp.cleanup()
        os.environ.pop("KEYFILE", None)

    def write(self, name, data):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w") as f:
            json.dump(data, f)
        return path

    def test_keyfile_is_used(self):
        os.environ["KEYFILE"] = self.write("client.json", {"installed": {"client_id": "k"}})
        self.assertEqual(ct.load_client()["client_id"], "k")

    def test_service_account_is_rejected(self):
        os.environ["KEYFILE"] = self.write("sa.json", {"type": "service_account"})
        with self.assertRaisesRegex(ct.ConfigError, "service-account"):
            ct.load_client()

    def test_missing_keyfile_falls_back_to_legacy_credentials(self):
        os.environ["KEYFILE"] = os.path.join(self.tmp.name, "gone.json")
        self.write("credentials.json", {"installed": {"client_id": "legacy"}})
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(ct.load_client()["client_id"], "legacy")

    def test_nothing_configured(self):
        with self.assertRaisesRegex(ct.ConfigError, "Key File"):
            ct.load_client()


class ParseFilterTests(unittest.TestCase):
    def items(self, text):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ct.main(["parse", text])
        return json.loads(out.getvalue())["items"]

    def test_no_date_preview_and_alt_escape(self):
        item = self.items("Read chapters 3-5")[0]
        self.assertTrue(item["subtitle"].startswith("📅 Due:"))  # a trailing 3-5 reads as a date…
        alt = json.loads(item["mods"]["alt"]["arg"])
        self.assertEqual(alt, {"title": "Read chapters 3-5", "due_datetime": None})  # …⌥↩ keeps it literal

    def test_empty_input(self):
        self.assertFalse(self.items("   ")[0]["valid"])


if __name__ == "__main__":
    unittest.main()
