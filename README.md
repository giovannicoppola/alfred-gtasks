# alfred-gTasks ✅

An [Alfred](https://www.alfredapp.com/) workflow to create and manage your **Google Tasks**
— add a task (with a natural-language due date), then browse, complete, or delete your tasks,
without leaving Alfred.

<a href="https://github.com/giovannicoppola/alfred-gTasks/releases/latest/">
<img alt="Downloads"
src="https://img.shields.io/github/downloads/giovannicoppola/alfred-gTasks/total?color=purple&label=Downloads"><br/>
</a>

## 🚀 Usage

### Create a task — `gt_new`
Type the keyword `gt_new` (or set a hotkey) followed by the task title and, optionally, a due date:

```
gt_new Call the plumber tomorrow
gt_new Submit report next friday
gt_new Pay rent in 3 days
gt_new Dentist 12/15
```

The due date is parsed out of the title and shown in the preview before you press ↩, so the
first example is saved as *"Call the plumber"* due tomorrow. Recognised date expressions:

- `today`, `tomorrow`
- a weekday (`friday` = the next Friday) or `next <weekday>`
- `next week`, `next month`, `in N days|weeks|months`
- `2026-12-03` anywhere in the text
- `12-15` or `12/15` as the **last word** (so "pages 3-5 tomorrow" keeps "3-5" in the title)
- numeric dates take an optional time, e.g. `12-15:09:30`. Google Tasks can't store a time,
  so it is added to the title ("Dentist at 09:30")

If no date is recognised, the task has **no due date**. If the preview picked up a date you
didn't mean, press **⌥↩** to create the task exactly as typed, with no due date.

### Browse / complete / delete — `gt_tasks`
Type `gt_tasks` to list your open tasks, then:

- **⌘↩** — mark the task complete
- **⌘⌥↩** — delete the task

> Google Tasks stores only the calendar **date** of a due date. The workflow saves that date
> so it is the same in every timezone.

## ⚙️ Setting up

Nothing to install: the workflow uses only the macOS system Python (`/usr/bin/python3`). If
macOS offers to install the Command Line Developer Tools the first time, accept.

It talks to the Google Tasks API on your behalf, so it needs a Google OAuth **client ID**
file (this is free).

1. In the [Google Cloud Console](https://console.cloud.google.com/), create (or reuse) a
   project and **enable the Google Tasks API**.
2. Set up the **OAuth consent screen** (User type *External*) and add your Google account
   as a **test user**.
3. Under **APIs & Services → Credentials**, create an **OAuth client ID** of type
   *Desktop app*, and download the JSON. (A *service account* key will not work.)
4. In the workflow's **Configure Workflow** panel, set **Key File** to that JSON.
5. Type `gt_tasks` and press ↩ on **Sign in to Google**, or just create a task. A browser
   window opens; approve access and you're done.

> ⏳ While the consent screen's publishing status is **Testing**, Google expires the sign-in
> after **7 days**. The workflow then shows *"Google sign-in expired — press ↩ to sign in
> again"*. To stop that, set the publishing status to **In production**. For an app only you
> use, no verification is needed; just click through the "Google hasn't verified this app"
> warning when you sign in.

> 🔒 The sign-in token is stored in Alfred's Workflow Data folder
> (`~/Library/Application Support/Alfred/Workflow Data/giovanni-gTasks/token.json`, readable
> only by you). It is **not** inside the workflow, so it isn't synced with your Alfred
> preferences and survives workflow updates. To sign out, delete that file and revoke access
> at [myaccount.google.com/permissions](https://myaccount.google.com/permissions). The Key
> File is only read when you sign in.

## 🧪 Tests

```
/usr/bin/python3 -B tests/test_createTask.py
```

The tests run offline against a fake Google server.

## 🧰 Changelog

- 2026-10-06: version 0.2.0
  - **No dependencies.** The workflow now uses only the standard library, so it runs on the
    system Python with nothing to `pip install`. 0.1.x failed on any Mac without the Google
    client libraries installed.
  - The **Key File** setting is now actually used (0.1.x ignored it).
  - `gt_tasks` now works: it was bound to the same keyword as `gt_new`. The hotkey trigger
    now opens `gt_new`.
  - Sign-in problems are reported in Alfred instead of silently showing nothing. An expired
    or revoked sign-in offers **↩ to sign in again**, and the browser sign-in times out after
    3 minutes instead of hanging.
  - The token moved out of the workflow folder (see *Setting up*). It is migrated
    automatically, and written atomically with owner-only permissions.
  - Completing a task changes only its status, so edits made meanwhile on your phone are no
    longer overwritten. The workflow always uses your default task list, lists beyond 100
    tasks, and finds tasks completed in Google's mobile apps.
  - 8-second network timeout, with retries on Google rate limits and server errors (never
    for task creation, so no duplicates).
  - A task with no date in the text now gets **no** due date. It used to be due tomorrow,
    despite the preview saying "No due date".
  - Date parsing: `12/15` works; `today's`, `Mondays` and mid-title ranges like `3-5` are no
    longer read as dates; `10-06` typed on Oct 6 means today, not next year. ⌥↩ creates a
    task exactly as typed.
- 2026-08-09: version 0.1.1 — fixed a due-date off-by-one (a local-midnight→UTC conversion
  could shift the date a day in timezones east of UTC; the date is now emitted at midnight
  UTC so it is preserved everywhere). Rewrote this README (it previously described a different
  workflow).

## 🧐 Feedback

Feedback welcome! If you notice a bug or have an idea, please open an issue.
