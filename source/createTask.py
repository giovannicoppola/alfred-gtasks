"""
gTasks — create and manage Google Tasks from Alfred.

Standard library only, so it runs on the macOS system /usr/bin/python3 with nothing to
install. Talks to the Google Tasks REST API directly and does the OAuth "installed app"
loopback flow itself.

Commands (the Alfred objects call these; they also work from a terminal):
  parse <text>          Script Filter: preview the task that <text> would create
  create <json>         Action: create the task described by the parse output
  list [--alfred|--json] [--completed]
  complete <id|title|number>
  delete <id|title|number>
  auth                  Sign in to Google (opens the browser)
  <text>                Create a task directly from <text>

Under Alfred, stdout carries only Alfred JSON (Script Filters) or the one-line message
for the notification (actions); diagnostics go to stderr (Alfred's debugger).
"""
import base64
import calendar
import datetime
import hashlib
import http.client
import http.server
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

SCOPES = ["https://www.googleapis.com/auth/tasks"]
API_BASE = "https://tasks.googleapis.com/tasks/v1"
TASKLIST = "@default"   # the user's default list (documented alias)
HTTP_TIMEOUT = 8        # seconds per request; a Script Filter must never hang
AUTH_TIMEOUT = 180      # seconds to wait for the user to finish signing in
RETRY_STATUSES = (429, 500, 502, 503, 504)
NOTES = "Added automatically via Python script from Mac."

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Workflow Data is local to this Mac (not synced with Alfred's preferences) and survives
# workflow updates, unlike the workflow folder itself.
DATA_DIR = os.getenv("alfred_workflow_data") or os.path.expanduser(
    "~/Library/Application Support/Alfred/Workflow Data/giovanni-gTasks")
TOKEN_PATH = os.path.join(DATA_DIR, "token.json")
LEGACY_TOKEN_PATH = os.path.join(SCRIPT_DIR, "token.json")
LEGACY_CLIENT_PATH = os.path.join(SCRIPT_DIR, "credentials.json")
UNDER_ALFRED = bool(os.getenv("alfred_version"))


def log(msg):
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# Errors: each carries a message that is safe to show the user.
# ---------------------------------------------------------------------------

class WorkflowError(Exception):
    pass


class ConfigError(WorkflowError):
    """The OAuth client file (KEYFILE) is missing or the wrong kind."""


class AuthRequired(WorkflowError):
    """No usable sign-in: the user must (re-)authorize in the browser."""


class NetworkError(WorkflowError):
    pass


class ApiError(WorkflowError):
    def __init__(self, status, reason, message):
        super().__init__(f"Google returned {status}: {message}")
        self.status = status
        self.reason = reason


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _error_details(err):
    """(reason, message) from a Google error body (API or OAuth token endpoint)."""
    try:
        body = json.loads(err.read() or b"{}")
    except ValueError:
        return "", err.reason
    error = body.get("error")
    if isinstance(error, dict):  # API: {"error": {"code", "message", "status"}}
        return error.get("status", ""), error.get("message", err.reason)
    if isinstance(error, str):   # OAuth: {"error": "invalid_grant", "error_description"}
        return error, body.get("error_description", error)
    return "", err.reason


def http_request(method, url, *, params=None, body=None, form=None, token=None, retries=0):
    """Send one JSON request. Retries only fast failures (429/5xx), never timeouts."""
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                raw = resp.read()
            return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as err:
            reason, message = _error_details(err)
            if err.code in RETRY_STATUSES and attempt < retries:
                time.sleep(0.5 * 2 ** attempt)
                continue
            raise ApiError(err.code, reason, message)
        except (OSError, http.client.HTTPException) as err:  # URLError, timeout, reset
            raise NetworkError(f"Can't reach Google ({getattr(err, 'reason', err)})")
    raise AssertionError("unreachable: the last attempt returns or raises")


# ---------------------------------------------------------------------------
# Token storage
# ---------------------------------------------------------------------------

def _write_token(token):
    """Write the token atomically, readable only by this user (mkstemp makes it 0600)."""
    os.makedirs(DATA_DIR, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DATA_DIR, prefix=".token-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(token, f)
        os.replace(tmp, TOKEN_PATH)
    except BaseException:
        os.unlink(tmp)
        raise


def _read_token_file(path):
    try:
        with open(path) as f:
            token = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(token, dict) or not token.get("refresh_token"):
        return None
    return token


def _load_token():
    """The stored token, or None if signed out (missing or corrupt file)."""
    if not os.path.exists(TOKEN_PATH) and os.path.exists(LEGACY_TOKEN_PATH):
        # v0.1 kept token.json in the workflow folder (synced, wiped on update): move it.
        legacy = _read_token_file(LEGACY_TOKEN_PATH)
        if legacy:
            _write_token(legacy)
            log(f"Moved token from {LEGACY_TOKEN_PATH} to {TOKEN_PATH}")
        os.remove(LEGACY_TOKEN_PATH)
    if not os.path.exists(TOKEN_PATH):
        return None
    token = _read_token_file(TOKEN_PATH)
    if token is None:
        log(f"Ignoring unreadable token at {TOKEN_PATH}")
    return token


def _forget_token():
    try:
        os.remove(TOKEN_PATH)
    except FileNotFoundError:
        pass


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _expired(token):
    expiry = token.get("expiry")
    if not token.get("token") or not expiry:
        return True
    try:
        expires = datetime.datetime.fromisoformat(expiry.rstrip("Z"))
    except ValueError:
        return True
    # Stored as naive UTC (same format google-auth uses); refresh a minute early.
    return _utcnow() >= expires - datetime.timedelta(seconds=60)


def _apply_grant(token, grant):
    token["token"] = grant["access_token"]
    expires = _utcnow() + datetime.timedelta(seconds=int(grant.get("expires_in", 3600)))
    token["expiry"] = expires.isoformat() + "Z"
    if grant.get("refresh_token"):
        token["refresh_token"] = grant["refresh_token"]
    return token


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------

def _read_client_file(path):
    """The 'installed'/'web' section of an OAuth client JSON; raises ConfigError."""
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        raise ConfigError(f"Key file not found: {path}")
    except (OSError, ValueError):
        raise ConfigError(f"Key file is not valid JSON: {path}")
    if data.get("type") == "service_account":
        raise ConfigError("Key file is a service-account key; Google Tasks needs an OAuth "
                          "client ID of type 'Desktop app'")
    client = data.get("installed") or data.get("web")
    if not client or not client.get("client_id"):
        raise ConfigError("Key file is not an OAuth client ID (Desktop app) JSON")
    return client


def load_client():
    """The OAuth client from KEYFILE, falling back to credentials.json from v0.1."""
    keyfile = os.path.expanduser(os.getenv("KEYFILE", "").strip())
    if keyfile:
        try:
            return _read_client_file(keyfile)
        except ConfigError as err:
            if not os.path.exists(LEGACY_CLIENT_PATH):
                raise
            log(f"{err}; using {LEGACY_CLIENT_PATH} instead")
    if os.path.exists(LEGACY_CLIENT_PATH):
        return _read_client_file(LEGACY_CLIENT_PATH)
    raise ConfigError("Set 'Key File' in the workflow configuration (see 'Setting up')")


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def authorize():
    """Interactive sign-in: loopback redirect with PKCE. Returns and stores the token."""
    client = load_client()
    verifier = _b64url(secrets.token_bytes(32))
    state = secrets.token_urlsafe(16)
    result = {}

    class Callback(http.server.BaseHTTPRequestHandler):
        timeout = 5  # drop idle browser preconnects instead of blocking on them

        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" not in query and "error" not in query:
                self.send_error(404)  # e.g. /favicon.ico
                return
            result.update({k: v[0] for k, v in query.items()})
            ok = "code" in query and query.get("state", [""])[0] == state
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(("<h3>gTasks: signed in — you can close this tab.</h3>" if ok else
                              "<h3>gTasks: sign-in failed — see Alfred.</h3>").encode())

        def log_message(self, format, *args):  # keep the request log out of stderr
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Callback)
    redirect_uri = f"http://127.0.0.1:{server.server_port}/"
    auth_url = client.get("auth_uri", "https://accounts.google.com/o/oauth2/auth") + "?" + \
        urllib.parse.urlencode({
            "client_id": client["client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "state": state,
            "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()),
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",  # always issue a refresh token, also on re-authorization
        })
    log("Opening browser for Google sign-in")
    subprocess.run(["/usr/bin/open", auth_url], check=False)

    server.timeout = 1
    deadline = time.monotonic() + AUTH_TIMEOUT
    try:
        while not result and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()

    if not result:
        raise AuthRequired("Sign-in timed out — try again")
    if "error" in result:
        raise AuthRequired(f"Sign-in was not completed ({result['error']}). Check that your "
                           "account is a test user on the OAuth consent screen")
    if result.get("state") != state:
        raise AuthRequired("Sign-in response did not match this request — try again")

    token_uri = client.get("token_uri", "https://oauth2.googleapis.com/token")
    try:
        grant = http_request("POST", token_uri, form={
            "grant_type": "authorization_code",
            "code": result["code"],
            "code_verifier": verifier,
            "client_id": client["client_id"],
            "client_secret": client.get("client_secret", ""),
            "redirect_uri": redirect_uri,
        })
    except ApiError as err:
        raise AuthRequired(f"Google refused the sign-in: {err}")
    token = _apply_grant({
        "token_uri": token_uri,
        "client_id": client["client_id"],
        "client_secret": client.get("client_secret", ""),
        "scopes": SCOPES,
    }, grant)
    _write_token(token)
    return token


def _refresh(token):
    try:
        grant = http_request("POST", token.get("token_uri", "https://oauth2.googleapis.com/token"), form={
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
            "client_id": token.get("client_id", ""),
            "client_secret": token.get("client_secret", ""),
        })
    except ApiError as err:
        if err.status in (400, 401):
            # invalid_grant: revoked, or the 7-day expiry Google applies to OAuth apps
            # whose consent screen is still in "Testing".
            _forget_token()
            raise AuthRequired("Google sign-in expired — press ↩ to sign in again")
        raise
    token = _apply_grant(token, grant)
    _write_token(token)
    return token


def access_token(interactive, force_refresh=False):
    """A valid access token. Non-interactive callers (Script Filters) never open a browser."""
    token = _load_token()
    if token is None:
        if not interactive:
            raise AuthRequired("Sign in to Google — press ↩")
        token = authorize()
    elif force_refresh or _expired(token):
        try:
            token = _refresh(token)
        except AuthRequired:
            if not interactive:
                raise
            token = authorize()
    return token["token"]


def api(method, path, *, interactive, params=None, body=None, retries=0):
    """Authorized Tasks API call; refreshes once if Google says the token is stale."""
    url = API_BASE + path
    token = access_token(interactive)
    try:
        return http_request(method, url, params=params, body=body, token=token, retries=retries)
    except ApiError as err:
        if err.status != 401:
            raise
    token = access_token(interactive, force_refresh=True)
    return http_request(method, url, params=params, body=body, token=token, retries=retries)


# ---------------------------------------------------------------------------
# Google Tasks
# ---------------------------------------------------------------------------

def _task_path(task_id=""):
    path = f"/lists/{urllib.parse.quote(TASKLIST, safe='@')}/tasks"
    return f"{path}/{urllib.parse.quote(task_id, safe='')}" if task_id else path


def fetch_tasks(interactive, show_completed=False):
    """All tasks in the default list, following pagination."""
    tasks, page = [], None
    while True:
        resp = api("GET", _task_path(), interactive=interactive, retries=2, params={
            "showCompleted": str(show_completed).lower(),
            # Tasks completed in Google's own apps (incl. the iPhone app) are "hidden".
            "showHidden": str(show_completed).lower(),
            "maxResults": 100,
            "pageToken": page,
        })
        tasks += resp.get("items", [])
        page = resp.get("nextPageToken")
        if not page:
            return tasks


def get_task(task_id):
    return api("GET", _task_path(task_id), interactive=True, retries=2)


def insert_task(title, due=None):
    """Create a task. No retries: a retried insert could create a duplicate."""
    body = {"title": title, "notes": NOTES}
    if due is not None:
        # Google Tasks keeps only the DATE. Send it at midnight UTC so it is the same
        # calendar date in every timezone.
        body["due"] = due.strftime("%Y-%m-%dT00:00:00.000Z")
    return api("POST", _task_path(), interactive=True, body=body)


def complete_task_by_id(task_id):
    # PATCH only the status (not GET + full PUT), so edits made meanwhile on another
    # device are not overwritten. Idempotent, so safe to retry.
    return api("PATCH", _task_path(task_id), interactive=True, retries=2,
               body={"status": "completed"})


def delete_task_by_id(task_id):
    api("DELETE", _task_path(task_id), interactive=True, retries=2)


def due_date(task):
    """'YYYY-MM-DD' from a task's due timestamp, or ''."""
    return task.get("due", "").split("T")[0]


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
# Whole words only: "today's" or "Mondays" are not dates.
_L, _R = r"(?<![\w'’])", r"(?![\w'’])"
_TIME = r"(?::(\d{1,2}):(\d{2}))?"
# yyyy-mm-dd[:hh:mm] anywhere in the text.
FULL_DATE_RE = re.compile(r"(?<![\w-])(\d{4})-(\d{1,2})-(\d{1,2})" + _TIME + r"(?![\w:-])")
# mm-dd or mm/dd [:hh:mm] only as the LAST word, so "pages 3-5 tomorrow" or
# "buy 2-3 apples" are not read as dates.
SHORT_DATE_RE = re.compile(r"(?:^|\s)(\d{1,2})[-/](\d{1,2})" + _TIME + r"\s*$")


def _midnight(dt):
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _add_months(dt, months):
    month0 = dt.month - 1 + months
    year, month = dt.year + month0 // 12, month0 % 12 + 1
    return dt.replace(year=year, month=month, day=min(dt.day, calendar.monthrange(year, month)[1]))


def _next_weekday(now, weekday):
    """The next occurrence of weekday, always in the future (today's weekday -> +7)."""
    days_ahead = (WEEKDAYS.index(weekday) - now.weekday()) % 7 or 7
    return _midnight(now + datetime.timedelta(days=days_ahead))


def _with_time(date, hour, minute):
    if hour is None:
        return date
    return date.replace(hour=int(hour), minute=int(minute))


def _remove(text, match):
    return re.sub(r"\s+", " ", text[:match.start()] + " " + text[match.end():]).strip()


def parse_date_from_string(text, now=None):
    """
    Split text into (title, due_datetime). due_datetime is None when no date is found, and
    has hour/minute set only when a time was given. Recognised:
      today, tomorrow, monday..sunday, next monday..sunday, next week, next month,
      in N days|weeks|months, yyyy-mm-dd anywhere, mm-dd or mm/dd as the last word;
      numeric dates take an optional :hh:mm (24h).
    """
    now = now or datetime.datetime.now()

    m = FULL_DATE_RE.search(text)
    if m:
        try:
            due = _with_time(datetime.datetime(int(m[1]), int(m[2]), int(m[3])), m[4], m[5])
            return _remove(text, m), due
        except ValueError:
            pass  # not a real date/time: leave it in the title

    m = SHORT_DATE_RE.search(text)
    if m:
        try:
            month, day = int(m[1]), int(m[2])
            due = _with_time(datetime.datetime(now.year, month, day), m[3], m[4])
            # Next occurrence: today still counts unless its time has already passed.
            in_past = due < now.replace(second=0, microsecond=0) if m[3] else due.date() < now.date()
            if in_past:
                due = due.replace(year=now.year + 1)
            return _remove(text, m), due
        except ValueError:
            pass

    m = re.search(_L + r"today" + _R, text, re.I)
    if m:
        return _remove(text, m), _midnight(now)

    m = re.search(_L + r"tomorrow" + _R, text, re.I)
    if m:
        return _remove(text, m), _midnight(now + datetime.timedelta(days=1))

    m = re.search(_L + r"next\s+(" + "|".join(WEEKDAYS) + r")" + _R, text, re.I)
    if m:
        return _remove(text, m), _next_weekday(now, m[1].lower())

    m = re.search(_L + r"(" + "|".join(WEEKDAYS) + r")" + _R, text, re.I)
    if m:
        return _remove(text, m), _next_weekday(now, m[1].lower())

    m = re.search(_L + r"in\s+(\d+)\s+(days?|weeks?|months?)" + _R, text, re.I)
    if m:
        amount, unit = int(m[1]), m[2].lower()
        if unit.startswith("month"):
            due = _add_months(now, amount)
        else:
            due = now + datetime.timedelta(days=amount * (7 if unit.startswith("week") else 1))
        return _remove(text, m), _midnight(due)

    m = re.search(_L + r"next\s+(week|month)" + _R, text, re.I)
    if m:
        due = now + datetime.timedelta(weeks=1) if m[1].lower() == "week" else _add_months(now, 1)
        return _remove(text, m), _midnight(due)

    return text.strip(), None


def _has_time(dt):
    return dt.hour != 0 or dt.minute != 0


def create_task(title, due=None):
    """Create a task; a time of day (which Google Tasks can't store) goes in the title."""
    notes_time = ""
    if due is not None and _has_time(due):
        notes_time = f"{due.hour:02d}:{due.minute:02d}"
        title = f"{title} at {notes_time}"
    result = insert_task(title, _midnight(due) if due is not None else None)
    when = f" — due {due_date(result)}" if result.get("due") else ""
    return f"✅ Created: {result.get('title', title)}{when}"


# ---------------------------------------------------------------------------
# Finding a task from the CLI (Alfred always passes the task ID)
# ---------------------------------------------------------------------------

def _looks_like_id(identifier):
    return len(identifier) > 20 and re.fullmatch(r"[A-Za-z0-9_-]+", identifier) is not None


def find_task(identifier, include_completed):
    """Resolve a task ID, a number from `list`, or a unique part of a title."""
    if _looks_like_id(identifier):
        try:
            return get_task(identifier)
        except ApiError as err:
            if err.status not in (400, 404):
                raise  # otherwise fall through: maybe it's a long one-word title

    if identifier.isdigit():
        tasks = fetch_tasks(interactive=True)  # numbers refer to `list` (open tasks)
        number = int(identifier)
        if not 1 <= number <= len(tasks):
            raise WorkflowError(f"Task number {number} is out of range (1-{len(tasks)})")
        return tasks[number - 1]

    query = identifier.lower()
    tasks = fetch_tasks(interactive=True, show_completed=include_completed)
    matches = [t for t in tasks if query in t.get("title", "").lower()]
    exact = [t for t in matches if t.get("title", "").lower() == query]
    if len(exact) == 1:
        return exact[0]
    if not matches:
        raise WorkflowError(f"No task matches '{identifier}'")
    if len(matches) > 1:
        titles = ", ".join(f"'{t.get('title', '')}'" for t in matches[:5])
        raise WorkflowError(f"{len(matches)} tasks match '{identifier}' ({titles}); be more specific")
    return matches[0]


def complete_task(identifier):
    task = find_task(identifier, include_completed=False)
    result = complete_task_by_id(task["id"])
    return f"🎯 Task completed: {result.get('title') or task.get('title') or '(No title)'}"


def delete_task(identifier):
    task = find_task(identifier, include_completed=True)
    delete_task_by_id(task["id"])
    return f"🗑️ Task deleted: {task.get('title') or '(No title)'}"


# ---------------------------------------------------------------------------
# Alfred output
# ---------------------------------------------------------------------------

def alfred(items):
    print(json.dumps({"items": items}))


def alfred_message(title, subtitle="", arg=None):
    item = {"title": title, "subtitle": subtitle, "valid": arg is not None}
    if arg is not None:
        item["arg"] = arg
    alfred([item])


def alfred_parse(text):
    """Script Filter: preview the task to be created. ⌥↩ creates it with no date parsing."""
    text = text.strip()
    if not text:
        return alfred([{"title": "Type a task…", "subtitle": "e.g. 'Call the plumber tomorrow'",
                        "valid": False}])
    title, due = parse_date_from_string(text)
    if due is None:
        subtitle = "No due date"
    elif _has_time(due):
        subtitle = f"📆 Due: {due:%Y-%m-%d} at {due:%H:%M}"
    elif due.date() == datetime.date.today():
        subtitle = "📅 Due: today"
    elif due.date() == datetime.date.today() + datetime.timedelta(days=1):
        subtitle = "📅 Due: tomorrow"
    else:
        subtitle = f"📅 Due: {due:%Y-%m-%d}"
    alfred([{
        "uid": "new_task",
        "title": title or "(No title)",
        "subtitle": subtitle,
        "arg": json.dumps({"title": title, "due_datetime": due.isoformat() if due else None}),
        "valid": bool(title),
        "icon": {"path": "icon.png"},
        "mods": {"alt": {
            "valid": True,
            "arg": json.dumps({"title": text, "due_datetime": None}),
            "subtitle": f"Create “{text}” exactly as typed, with no due date",
        }},
    }])


def alfred_list():
    """Script Filter: open tasks. Always prints valid Alfred JSON, errors included."""
    try:
        tasks = fetch_tasks(interactive=False)
    except AuthRequired as err:
        return alfred_message(str(err), "Opens your browser to authorize gTasks", arg="auth")
    except ConfigError as err:
        return alfred_message(str(err), "Alfred Preferences → Workflows → gTask → Configure Workflow")
    except WorkflowError as err:
        return alfred_message("Couldn't load your tasks", str(err))
    if not tasks:
        return alfred_message("No open tasks 🎉")

    items = []
    for task in tasks:
        parts = ["✅ Completed" if task.get("status") == "completed" else "⏳ Pending"]
        if task.get("due"):
            parts.append(f"Due: {due_date(task)}")
        notes = task.get("notes", "").split("\n")[0]
        if notes:
            parts.append(notes if len(notes) <= 50 else notes[:47] + "...")
        items.append({
            "uid": task["id"],
            "title": task.get("title") or "(No title)",
            "subtitle": " | ".join(parts),
            # Plain ↩ is reserved for the sign-in item; tasks act on ⌘↩ / ⌘⌥↩ only.
            "valid": False,
            "mods": {
                "cmd": {"valid": True, "arg": task["id"], "subtitle": "✅️ Complete this task"},
                "cmd+alt": {"valid": True, "arg": task["id"], "subtitle": "Delete this task 🗑️"},
            },
            "icon": {"path": "checklist.png" if task.get("status") == "completed" else "icon.png"},
        })
    alfred(items)


def print_list(show_completed, as_json):
    tasks = fetch_tasks(interactive=True, show_completed=show_completed)
    if as_json:
        print(json.dumps({"task_list_id": TASKLIST, "count": len(tasks), "tasks": tasks}, indent=2))
        return
    if not tasks:
        print("No tasks found.")
        return
    for i, task in enumerate(tasks, 1):
        icon = "✅" if task.get("status") == "completed" else "⏳"
        due = f" | Due: {due_date(task)}" if task.get("due") else ""
        print(f"{i}. {icon} {task.get('title') or '(No title)'}{due}")


def create_from_json(raw):
    try:
        data = json.loads(raw)
        title = data.get("title", "").strip()
        due = data.get("due_datetime")
        due = datetime.datetime.fromisoformat(due) if due else None
    except (ValueError, AttributeError) as err:
        raise WorkflowError(f"Invalid task data: {err}")
    if not title:
        raise WorkflowError("Task title is required")
    return create_task(title, due)


def create_from_text(text):
    title, due = parse_date_from_string(text)
    if not title:
        raise WorkflowError("Task title cannot be empty after removing the date")
    return create_task(title, due)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

USAGE = """Usage:
  createTask.py 'Task title [date]'           create a task
  createTask.py list [--completed] [--json]   list tasks
  createTask.py complete <id|title|number>
  createTask.py delete <id|title|number>
  createTask.py auth                          sign in to Google
Dates: today, tomorrow, monday, next friday, next week, in 3 days, 2026-12-03,
       and 12-15 or 12/15 as the last word; add :hh:mm for a time (e.g. 12-15:09:30)."""


def main(argv):
    if not argv:
        print(USAGE)
        return 1
    cmd, rest = argv[0].lower(), " ".join(argv[1:])

    # Script Filters: always exit 0 with Alfred JSON.
    if cmd in ("parse", "--parse", "-p"):
        alfred_parse(rest)
        return 0
    if cmd in ("list", "--list", "-l") and ("--alfred" in argv or "-a" in argv):
        alfred_list()
        return 0

    # Actions / CLI: one line of output (the Alfred notification text).
    try:
        if cmd in ("list", "--list", "-l"):
            print_list("--completed" in argv or "-c" in argv, "--json" in argv or "-j" in argv)
            return 0
        if cmd == "auth":
            authorize()
            message = "🔑 Signed in to Google Tasks"
        elif cmd in ("create", "--create"):
            message = create_from_json(rest)
        elif cmd in ("complete", "done", "--complete", "--done"):
            message = complete_task(rest.strip()) if rest.strip() else None
        elif cmd in ("delete", "remove", "--delete", "--remove", "-d"):
            message = delete_task(rest.strip()) if rest.strip() else None
        else:
            message = create_from_text(" ".join(argv))
        if message is None:
            print(USAGE)
            return 1
        print(message)
        return 0
    except WorkflowError as err:
        log(f"{type(err).__name__}: {err}")
        print(f"❌ {err}")
    except Exception as err:  # never leave Alfred with no feedback
        log(f"Unexpected error: {err!r}")
        print(f"❌ Unexpected error: {err}")
    # Under Alfred, exit 0 so the error message still reaches the notification.
    return 0 if UNDER_ALFRED else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
