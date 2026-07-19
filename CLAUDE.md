# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A cron-driven bot that scrapes Israel's tree-felling license portal
(yeela-trees.moag.gov.il), diffs the results against previous snapshots, and
emails Hebrew/RTL HTML reports to per-city subscribers and admins. It's a
flat collection of standalone Python scripts, no package/module structure,
no test suite, no linter.

## Setup and running

```bash
./install.sh          # venv, pip deps, Playwright's Chromium, config.py/install.ini bootstrap
./set_cron.sh          # (re)installs the two cron lines below from install.ini's path
./run_bot.sh            # daily: scrape + per-city diff notification
./run_weekly_report.sh  # weekly: admin summary report + archive push
./push_archive.sh       # snapshot archive/*.csv into dated git commits on main
```

There is no build, lint, or test command — verify changes by running the
relevant script directly against the venv (`source venv/bin/activate`).

## Configuration (gitignored, must be created locally)

- `config.py` (copy from `config.example.py`): `SHEET_ID` of the subscriber
  Google Sheet, `OWNER_SUBSCRIPTIONS` (email, city) pairs that can't be read
  from the sheet itself, `ADMIN_EMAILS` for the weekly report.
- `install.ini` (copy from `install.ini.example`, or auto-written by
  `install.sh`): holds the absolute deployment path (`[project] dir = ...`)
  so that path — which contains the local username — never has to be
  hardcoded in a script committed to GitHub. `run_bot.sh` and
  `run_weekly_report.sh` source it via `project_dir.sh` before `cd`-ing in.

## Pipeline architecture

The daily flow (`run_bot.sh`) is: **scrape → diff → notify → push archive**.

1. **`fetch_data.py`** drives Playwright (headless Chromium) against the
   portal, exports an Excel file, and converts it straight to
   `archive/full_licenses_<YYYY-MM-DD>.csv`, dated by the day it was
   downloaded (overwriting if run again the same day). There's no
   numbered rotation — each day gets its own permanent filename, so
   nothing needs to be capped or shifted.
2. **`notify_changes.py`** diffs the two most recent dated snapshots in
   `archive/` (found by globbing `full_licenses_*.csv` and sorting by the
   date in the filename) with a pandas outer-merge, tags rows
   `חדש/עודכן`/`הוסר מהמערכת`, then for each `(email, city)` subscriber from
   `sheet_subscribers.py` filters the diff by city substring match and
   emails an HTML table via `mailer.py`. Debug copies land in `tmp/`.
3. **`push_archive.sh`** commits/pushes any new or changed
   `archive/full_licenses_*.csv` to `main` — since `fetch_data.py` already
   writes date-named files directly, this is just a `git add` + commit +
   push, and it's idempotent (no-op if nothing changed). This step is
   what keeps the archive updated in git every day; it does not run from
   the weekly flow.

**`download_trees.py` is a second, self-contained implementation of the same
scrape→rotate→diff→notify flow** (own Playwright download, own rotation, own
per-city emailing) but is not wired into any `run_*.sh` or cron entry —
treat it as an alternate/legacy path, not the live one, unless a task says
otherwise. Don't assume changes to `fetch_data.py`/`notify_changes.py` need
mirroring there or vice versa without checking which is actually in use.

The weekly flow (`run_weekly_report.sh`) runs two steps in order — archive
management is exclusively a daily concern (see above), so this flow only
covers the admin email and the dashboard:

1. `weekly_report.py` diffs the latest dated snapshot against whichever
   dated snapshot is closest to 7 days before it (by filename date, not
   mtime — snapshots aren't written on a strict daily cadence), and emails
   `ADMIN_EMAILS` a summary + full detail table.
2. **`statics/generate_report.sh`** runs `statics/generate_dashboard.py`
   and commits/pushes the result. It relies on `run_bot.sh`'s daily
   `push_archive.sh` having already committed that day's snapshot — since
   both flows run from the same cron user against the same repo, by the
   time the weekly report runs the archive is already current. The Python
   script reads *only* the dated `archive/full_licenses_YYYY-MM-DD.csv`
   files, for reproducibility from the repo alone, builds a self-contained
   HTML dashboard (summary cards + matplotlib charts embedded as base64
   PNGs: trend over time, top species/cities for cutting, status
   breakdown) into `statics/reports/report_<date>.html`, and updates
   `statics/reports/index.html` and `statics/reports/trend_data.csv` (a
   cache of per-date aggregates, extended incrementally so old snapshot
   CSVs don't get re-read on every run). Idempotent.

## Mail delivery

`mailer.py` builds MIME messages and pipes them to `/usr/sbin/sendmail -t
-oi` directly, deliberately bypassing `mail -a` (GNU Mailutils' `mail`
refuses to override `Content-Type`, so HTML sends silently degrade to
visible raw HTML in plain text).

## Data conventions

- All diffing logic normalizes with `.fillna('').strip()` plus a
  `normalize_nums` pass that strips trailing `.0` (Excel exports numeric IDs
  as floats; without this every row would show as changed on every diff).
- City matching is substring-based (`str.contains`) against the Hebrew
  `ישוב` (settlement/city) column, not exact match.
