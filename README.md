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
Type the keyword `gt_new` (or a hotkey) followed by the task title and, optionally, a due date:

```
gt_new Call the plumber tomorrow
gt_new Submit report next friday
gt_new Pay rent in 3 days
gt_new Dentist 12/15
```

The due date is parsed out of the title, so the task is saved as *"Call the plumber"* due
tomorrow. Recognised date expressions include:

- `today`, `tomorrow`
- `next <weekday>` (e.g. `next monday`)
- `in N days`
- explicit dates like `12/15` (optionally with a time)

If no date is recognised, the task is created with no due date.

### Browse / complete / delete — `gt_tasks`
Type `gt_tasks` to list your open tasks. From the list you can mark a task **complete** or
**delete** it (see the modifier subtitles in Alfred).

> Google Tasks stores only the calendar **date** of a due date (the time is ignored). The
> workflow saves the date so it is preserved in every timezone.

## ⚙️ Setting up

This workflow talks to the Google Tasks API on your behalf, so it needs a Google OAuth
**client credentials** file (this is free).

1. In the [Google Cloud Console](https://console.cloud.google.com/), create (or reuse) a
   project and **enable the Google Tasks API**.
2. Under **APIs & Services → Credentials**, create an **OAuth client ID** of type
   *Desktop app*, and download the JSON.
3. In the workflow's **Configuration** panel, set `KEYFILE` to the path of that downloaded
   JSON (it is copied in as `credentials.json`).
4. The first time you create a task, a browser window opens to authorise access. After you
   approve, a `token.json` is stored inside the workflow so you won't be asked again.

> 🔒 `credentials.json`, `token.json`, and `prefs.plist` are **git-ignored and are never
> bundled in the released workflow** — your account stays private to your machine.

## 🧰 Changelog

- 2026-08-09: version 0.1.1 — fixed a due-date off-by-one (a local-midnight→UTC conversion
  could shift the date a day in timezones east of UTC; the date is now emitted at midnight
  UTC so it is preserved everywhere). Rewrote this README (it previously described a different
  workflow).

## 🧐 Feedback

Feedback welcome! If you notice a bug or have an idea, please open an issue.
