"""
Builds a weekly self-contained HTML dashboard (stats + embedded charts)
from the dated license snapshots in archive/, and writes it to
statics/reports/. Run via statics/generate_report.sh, which also commits
and pushes the result - see run_weekly_report.sh.

Only the git-tracked dated snapshots (full_licenses_YYYY-MM-DD.csv,
written weekly by push_archive.sh) are used, not the rotating
full_licenses_v*.csv working files - those are gitignored and get
pruned, so a report built from them wouldn't be reproducible from the
repo alone.
"""
import base64
import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_DIR = REPO_ROOT / "archive"
REPORTS_DIR = REPO_ROOT / "statics" / "reports"
TREND_CACHE = REPORTS_DIR / "trend_data.csv"

DATE_RE = re.compile(r"^full_licenses_(\d{4}-\d{2}-\d{2})\.csv$")

CUT_COL = "סה'כ לכריתה"
MOVE_COL = "סה'כ להעתקה"
KEEP_COL = "סה'כ לשימור"
CITY_COL = "ישוב"
SPECIES_COL = "מין העץ"
STATUS_COL = "סטטוס רישיון"

COLORS = ["#2ecc71", "#e74c3c", "#3498db", "#f1c40f", "#9b59b6", "#1abc9c"]


def find_dated_snapshots():
    files = {}
    for path in ARCHIVE_DIR.glob("full_licenses_*.csv"):
        m = DATE_RE.match(path.name)
        if m:
            files[m.group(1)] = path
    return dict(sorted(files.items()))


def load_trend_cache():
    if TREND_CACHE.exists():
        return pd.read_csv(TREND_CACHE, dtype={"date": str}).set_index("date")
    return pd.DataFrame(columns=["date", "licenses", "cut", "move", "keep", "cities", "species"]).set_index("date")


def update_trend_cache(snapshots):
    """Computes one summary row per dated snapshot, reusing cached rows so
    old CSVs aren't re-read every week."""
    cache = load_trend_cache()
    new_rows = {}
    for date_str, path in snapshots.items():
        if date_str in cache.index:
            continue
        df = pd.read_csv(path)
        new_rows[date_str] = {
            "licenses": len(df),
            "cut": int(df[CUT_COL].sum()),
            "move": int(df[MOVE_COL].sum()),
            "keep": int(df[KEEP_COL].sum()),
            "cities": int(df[CITY_COL].nunique()),
            "species": int(df[SPECIES_COL].nunique()),
        }
    if new_rows:
        cache = pd.concat([cache, pd.DataFrame.from_dict(new_rows, orient="index")])
    cache = cache.sort_index()
    cache.index.name = "date"
    cache.to_csv(TREND_CACHE)
    return cache


def chart_to_data_uri(fig):
    buf = BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=110)
    buf.seek(0)
    encoded = base64.b64encode(buf.read()).decode("utf-8")
    plt.close(fig)
    return f"data:image/png;base64,{encoded}"


def style_axes(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", color="#ecf0f1", linewidth=1)
    ax.set_axisbelow(True)


def make_trend_chart(trend):
    fig, ax1 = plt.subplots(figsize=(10, 4.5))
    ax1.plot(trend.index, trend["licenses"], marker="o", color="#3498db", label="סה\"כ רישיונות")
    ax1.set_ylabel("רישיונות", color="#3498db")
    ax1.tick_params(axis="y", labelcolor="#3498db")
    plt.setp(ax1.get_xticklabels(), rotation=45, ha="right")

    ax2 = ax1.twinx()
    ax2.plot(trend.index, trend["cut"], marker="s", color="#e74c3c", label="עצים לכריתה")
    ax2.set_ylabel("עצים לכריתה", color="#e74c3c")
    ax2.tick_params(axis="y", labelcolor="#e74c3c")

    ax1.set_title("מגמת רישיונות ועצים מאושרים לכריתה לאורך זמן")
    style_axes(ax1)
    fig.tight_layout()
    return chart_to_data_uri(fig)


def make_barh_chart(series, title, color):
    fig, ax = plt.subplots(figsize=(6, 4.5))
    ordered = series.sort_values(ascending=True)
    ax.barh(ordered.index, ordered.values, color=color)
    ax.set_title(title)
    style_axes(ax)
    ax.grid(axis="x", color="#ecf0f1", linewidth=1)
    fig.tight_layout()
    return chart_to_data_uri(fig)


def esc(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def table_rows(pairs):
    return "".join(f"<tr><td>{esc(a)}</td><td>{b:,}</td></tr>" for a, b in pairs)


def build_report(latest_date, df, trend):
    total_licenses = len(df)
    total_cut = int(df[CUT_COL].sum())
    total_move = int(df[MOVE_COL].sum())
    total_keep = int(df[KEEP_COL].sum())
    n_cities = df[CITY_COL].nunique()
    n_species = df[SPECIES_COL].nunique()

    top_species = df.groupby(SPECIES_COL)[CUT_COL].sum().sort_values(ascending=False).head(10)
    top_cities = df.groupby(CITY_COL)[CUT_COL].sum().sort_values(ascending=False).head(10)
    status_counts = df[STATUS_COL].value_counts().head(8)

    trend_chart = make_trend_chart(trend) if len(trend) > 1 else None
    species_chart = make_barh_chart(top_species, "עשרת מיני העצים המובילים בכריתה", "#e74c3c")
    cities_chart = make_barh_chart(top_cities, "עשרת היישובים המובילים בכריתה", "#3498db")
    status_chart = make_barh_chart(status_counts, "התפלגות סטטוס רישיונות", "#9b59b6")

    trend_rows = ""
    prev = None
    for date_str, row in trend.iterrows():
        new_licenses = int(row["licenses"] - prev["licenses"]) if prev is not None else 0
        new_cut = int(row["cut"] - prev["cut"]) if prev is not None else 0
        trend_rows += (
            f"<tr><td>{date_str}</td><td>{int(row['licenses']):,}</td>"
            f"<td>{new_licenses:+,}</td><td>{int(row['cut']):,}</td><td>{new_cut:+,}</td></tr>"
        )
        prev = row

    first_date = trend.index[0] if len(trend) else latest_date

    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>דוח סטטיסטיקה ומגמות - רישיונות כריתה ({latest_date})</title>
<style>
    body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f6f9; color: #333; margin: 0; padding: 20px; }}
    .container {{ max-width: 1200px; margin: 0 auto; }}
    header {{ background-color: #2c3e50; color: #fff; padding: 20px; border-radius: 8px; margin-bottom: 25px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
    header a {{ color: #ecf0f1; }}
    h1 {{ margin: 0; font-size: 24px; }}
    h2 {{ color: #2c3e50; border-bottom: 2px solid #bdc3c7; padding-bottom: 8px; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 30px; }}
    .card {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); text-align: center; border-top: 4px solid #2ecc71; }}
    .card.cut {{ border-top-color: #e74c3c; }}
    .card.move {{ border-top-color: #3498db; }}
    .card.keep {{ border-top-color: #f1c40f; }}
    .card.meta {{ border-top-color: #9b59b6; }}
    .card-val {{ font-size: 26px; font-weight: bold; margin: 10px 0; color: #2c3e50; }}
    .card-lbl {{ font-size: 13px; color: #7f8c8d; }}
    .grid-2 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(480px, 1fr)); gap: 25px; margin-bottom: 30px; }}
    .panel {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); margin-bottom: 30px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 15px; }}
    th, td {{ padding: 10px 12px; text-align: right; border-bottom: 1px solid #ecf0f1; font-size: 14px; }}
    th {{ background-color: #f8f9fa; color: #2c3e50; }}
    tr:hover {{ background-color: #fcfcfc; }}
    .chart-img {{ max-width: 100%; height: auto; border-radius: 8px; }}
    footer {{ text-align: center; color: #95a5a6; font-size: 12px; margin: 30px 0 10px; }}
</style>
</head>
<body>
<div class="container">
    <header>
        <h1>דוח מגמות וסטטיסטיקה: רישיונות כריתה והעתקה</h1>
        <p>נתונים נכון לתאריך {latest_date} &middot; <a href="index.html">כל הדוחות</a></p>
    </header>

    <div class="cards">
        <div class="card"><div class="card-val">{total_licenses:,}</div><div class="card-lbl">סך הכל רישיונות במערכת</div></div>
        <div class="card cut"><div class="card-val">{total_cut:,}</div><div class="card-lbl">עצים מאושרים לכריתה</div></div>
        <div class="card move"><div class="card-val">{total_move:,}</div><div class="card-lbl">עצים מאושרים להעתקה</div></div>
        <div class="card keep"><div class="card-val">{total_keep:,}</div><div class="card-lbl">עצים לשימור</div></div>
        <div class="card meta"><div class="card-val">{n_cities:,}</div><div class="card-lbl">יישובים</div></div>
        <div class="card meta"><div class="card-val">{n_species:,}</div><div class="card-lbl">מיני עצים</div></div>
    </div>

    <div class="panel">
        <h2>מגמות לאורך זמן (מאז {first_date})</h2>
        {"<img class='chart-img' src='" + trend_chart + "' alt='גרף מגמות'>" if trend_chart else "<p>אין עדיין מספיק תמונות היסטוריות למגמה.</p>"}
        <table>
            <thead><tr><th>תאריך</th><th>סה"כ רישיונות</th><th>שינוי רישיונות</th><th>סה"כ עצים לכריתה</th><th>שינוי עצים לכריתה</th></tr></thead>
            <tbody>{trend_rows}</tbody>
        </table>
    </div>

    <div class="grid-2">
        <div class="panel">
            <img class="chart-img" src="{species_chart}" alt="גרף מיני עצים">
            <table>
                <thead><tr><th>מין העץ</th><th>סה"כ עצים לכריתה</th></tr></thead>
                <tbody>{table_rows(top_species.items())}</tbody>
            </table>
        </div>
        <div class="panel">
            <img class="chart-img" src="{cities_chart}" alt="גרף יישובים">
            <table>
                <thead><tr><th>ישוב</th><th>סה"כ עצים לכריתה</th></tr></thead>
                <tbody>{table_rows(top_cities.items())}</tbody>
            </table>
        </div>
    </div>

    <div class="panel">
        <h2>סטטוס רישיונות</h2>
        <img class="chart-img" src="{status_chart}" alt="גרף סטטוס רישיונות">
        <table>
            <thead><tr><th>סטטוס רישיון</th><th>כמות רישיונות</th></tr></thead>
            <tbody>{table_rows(status_counts.items())}</tbody>
        </table>
    </div>

    <footer>
        נוצר אוטומטית ב-{datetime.now(timezone.utc).astimezone().strftime('%d/%m/%Y %H:%M')} מתוך {len(trend)} תמונות ארכיון היסטוריות.
    </footer>
</div>
</body>
</html>
"""


def build_index(trend):
    rows = "".join(
        f'<li><a href="report_{date_str}.html">{date_str}</a></li>'
        for date_str in reversed(trend.index)
    )
    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<title>ארכיון דוחות שבועיים</title>
<style>
    body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f6f9; color: #333; padding: 20px; }}
    .container {{ max-width: 600px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); }}
    h1 {{ color: #2c3e50; font-size: 20px; }}
    ul {{ line-height: 2; }}
    a {{ color: #3498db; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<div class="container">
    <h1>ארכיון דוחות שבועיים - רישיונות כריתה</h1>
    <ul>{rows}</ul>
</div>
</body>
</html>
"""


def main():
    snapshots = find_dated_snapshots()
    if not snapshots:
        print("No dated archive/full_licenses_YYYY-MM-DD.csv snapshots found, nothing to report on.")
        return

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    trend = update_trend_cache(snapshots)

    latest_date = max(snapshots)
    df = pd.read_csv(snapshots[latest_date])

    report_html = build_report(latest_date, df, trend)
    report_path = REPORTS_DIR / f"report_{latest_date}.html"
    report_path.write_text(report_html, encoding="utf-8")
    print(f"Report written to {report_path}")

    index_path = REPORTS_DIR / "index.html"
    index_path.write_text(build_index(trend), encoding="utf-8")
    print(f"Index written to {index_path}")


if __name__ == "__main__":
    main()
