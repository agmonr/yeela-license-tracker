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

The daily flow (`run_bot.sh`) is: **scrape → rotate snapshots → diff →
notify**.

1. **`fetch_data.py`** drives Playwright (headless Chromium) against the
   portal, exports an Excel file, converts it to
   `archive/full_licenses_v1.csv`, and rotates older snapshots
   (`v1→v2→v3→...`, oldest dropped) via `rotate_files()`.
2. **`notify_changes.py`** diffs `archive/full_licenses_v1.csv` (new) against
   `v2.csv` (previous) with a pandas outer-merge, tags rows
   `חדש/עודכן`/`הוסר מהמערכת`, then for each `(email, city)` subscriber from
   `sheet_subscribers.py` filters the diff by city substring match and
   emails an HTML table via `mailer.py`. Debug copies land in `tmp/`.

**`download_trees.py` is a second, self-contained implementation of the same
scrape→rotate→diff→notify flow** (own Playwright download, own rotation, own
per-city emailing) but is not wired into any `run_*.sh` or cron entry —
treat it as an alternate/legacy path, not the live one, unless a task says
otherwise. Don't assume changes to `fetch_data.py`/`notify_changes.py` need
mirroring there or vice versa without checking which is actually in use.

The weekly flow (`run_weekly_report.sh`) runs `weekly_report.py` (diffs
current `v1.csv` against whichever snapshot's mtime is closest to 7 days
ago, since rotation isn't strictly daily, then emails `ADMIN_EMAILS` a
summary + full detail table) followed by `push_archive.sh`.

**`push_archive.sh`** copies each `archive/full_licenses_v*.csv` to a
date-named sibling (`full_licenses_YYYY-MM-DD.csv`, derived from the file's
mtime) and commits/pushes those to `main`. The `v*` files stay gitignored
(they're the live rotation state the other scripts depend on); only the
dated copies — which fall outside the `full_licenses_v*.csv` gitignore
pattern — are meant to be tracked. The script is idempotent: unchanged
content produces no commit.

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
