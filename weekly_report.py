import os
import glob
import re
from datetime import datetime, timedelta
import pandas as pd
from config import ADMIN_EMAILS
from mailer import send_html_mail

DATE_RE = re.compile(r"full_licenses_(\d{4}-\d{2}-\d{2})\.csv$")


def normalize_nums(val):
    if val.endswith('.0'):
        return val[:-2]
    return val


def load_normalized(path):
    df = pd.read_csv(path, dtype=str).fillna('').map(lambda x: str(x).strip())
    return df.map(normalize_nums)


def get_dated_snapshots():
    """Returns (date, path) for each archive/full_licenses_<date>.csv, sorted ascending."""
    snapshots = []
    for path in glob.glob("archive/full_licenses_*.csv"):
        m = DATE_RE.search(path)
        if m:
            snapshots.append((datetime.strptime(m.group(1), "%Y-%m-%d"), path))
    snapshots.sort()
    return snapshots


def find_week_ago_file():
    """
    Picks the dated snapshot whose date is closest to (but not newer than)
    7 days before the latest snapshot, to use as the baseline for the
    weekly diff. Snapshots aren't written on a strict daily cadence, so the
    latest date (not "today") is the reference point.
    """
    snapshots = get_dated_snapshots()
    if len(snapshots) < 2:
        return None
    cutoff = snapshots[-1][0] - timedelta(days=7)
    best_path, best_date = None, None
    for snap_date, path in snapshots[:-1]:
        if snap_date <= cutoff and (best_date is None or snap_date > best_date):
            best_path, best_date = path, snap_date
    return best_path


def get_weekly_diff():
    """
    Compares the current snapshot against the ~week-old baseline and
    returns a DataFrame of both added and removed rows across all cities.
    """
    snapshots = get_dated_snapshots()
    if not snapshots:
        print("Error: no dated snapshots found in archive/.")
        return None
    current_path = snapshots[-1][1]

    baseline_path = find_week_ago_file()
    if baseline_path is None:
        print("No snapshot old enough yet for a weekly comparison.")
        return None

    print(f"Comparing {current_path} against {baseline_path} (weekly baseline)...")
    df_new = load_normalized(current_path)
    df_old = load_normalized(baseline_path)

    merged = df_new.merge(df_old, how='outer', indicator=True)

    added = merged[merged['_merge'] == 'left_only'].copy()
    added['סוג שינוי'] = 'חדש/עודכן'

    removed = merged[merged['_merge'] == 'right_only'].copy()
    removed['סוג שינוי'] = 'הוסר מהמערכת'

    return pd.concat([added, removed], ignore_index=True).drop('_merge', axis=1)


def build_summary_table(diff_df):
    return (
        diff_df.groupby(['ישוב', 'סוג שינוי']).size()
        .unstack(fill_value=0)
        .reset_index()
        .sort_values('ישוב')
    )


def send_weekly_report(diff_df):
    if diff_df is None:
        return

    if diff_df.empty:
        body_note = "<p>לא נמצאו שינויים בשבוע האחרון.</p>"
        summary_html = ""
        detail_html = ""
    else:
        summary_df = build_summary_table(diff_df)
        summary_html = "<h3>סיכום לפי ישוב</h3>" + summary_df.to_html(index=False, border=0)
        detail_html = "<h3>פירוט מלא</h3>" + diff_df.to_html(index=False, border=0)
        body_note = (
            f"<p>נמצאו <strong>{len(diff_df)}</strong> שינויים בשבוע האחרון, "
            f"ב-<strong>{diff_df['ישוב'].nunique()}</strong> ישובים.</p>"
        )

    html_body = f"""
<!DOCTYPE html>
<html dir="rtl" lang="he">
<head>
<meta charset="UTF-8">
<style>
    body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; direction: rtl; text-align: right; background-color: #f4f7f6; padding: 20px; }}
    .container {{ background-color: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
    h2 {{ color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 15px; font-size: 13px; }}
    th, td {{ border: 1px solid #e0e0e0; padding: 10px 8px; text-align: right; }}
    th {{ background-color: #3498db; color: white; white-space: nowrap; }}
    tr:nth-child(even) {{ background-color: #f2f2f2; }}
    .status-new {{ color: #27ae60; font-weight: bold; }}
    .status-removed {{ color: #c0392b; font-weight: bold; }}
    .footer {{ margin-top: 30px; font-size: 12px; color: #7f8c8d; border-top: 1px solid #eee; padding-top: 10px; }}
</style>
</head>
<body>
    <div class="container">
        <h2>דוח שבועי - רישיונות כריתה</h2>
        <p><a href="https://agmonr.github.io/yeela-license-tracker/statics/reports/objections.html">דוח אפקטיביות השגות/התנגדויות לפי יישוב</a>
        &middot; <a href="https://agmonr.github.io/yeela-license-tracker/statics/reports/open_for_objection.html">רישיונות פתוחים כרגע להגשת השגה</a>
        &middot; <a href="https://agmonr.github.io/yeela-license-tracker/statics/reports/applicants.html">דוח מבקשים לפי סך עצים לכריתה</a>
        &middot; <a href="https://agmonr.github.io/yeela-license-tracker/statics/reports/approvers.html">דוח מאשרים לפי פקיד יערות</a></p>
        {body_note}
        {summary_html}
        {detail_html}
        <div class="footer">
            הודעה זו נשלחה באופן אוטומטי על ידי בוט מעקב רישיונות כריתה.<br>
            תאריך הפקה: {datetime.now().strftime('%d/%m/%Y %H:%M')}
        </div>
    </div>
</body>
</html>
"""
    html_body = html_body.replace('חדש/עודכן', '<span class="status-new">חדש/עודכן</span>')
    html_body = html_body.replace('הוסר מהמערכת', '<span class="status-removed">הוסר מהמערכת</span>')

    debug_filename = "last_weekly_report.html"
    with open(debug_filename, "w", encoding="utf-8") as f:
        f.write(html_body)
    print(f"Debug HTML saved to: {debug_filename}")

    attachments = []
    if diff_df is not None and not diff_df.empty:
        diff_filename = f"weekly_diff_{datetime.now().strftime('%Y%m%d')}.csv"
        diff_df.to_csv(diff_filename, index=False, encoding='utf-8-sig')
        attachments.append(diff_filename)

    send_html_mail(ADMIN_EMAILS, "דוח שבועי - רישיונות כריתה", html_body, attachments)


if __name__ == "__main__":
    diff = get_weekly_diff()
    send_weekly_report(diff)
