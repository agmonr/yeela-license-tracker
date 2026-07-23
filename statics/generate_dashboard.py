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
import configparser
import html
import json
import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import quote_plus

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_DIR = REPO_ROOT / "archive"
REPORTS_DIR = REPO_ROOT / "statics" / "reports"
TREND_CACHE = REPORTS_DIR / "trend_data.csv"
BASE_URL = "https://agmonr.github.io/yeela-license-tracker/statics/reports/"
CONFIG_PATH = REPO_ROOT / "config.ini"

DATE_RE = re.compile(r"^full_licenses_(\d{4}-\d{2}-\d{2})\.csv$")

CUT_COL = "סה'כ לכריתה"
MOVE_COL = "סה'כ להעתקה"
KEEP_COL = "סה'כ לשימור"
CITY_COL = "ישוב"
SPECIES_COL = "מין העץ"
STATUS_COL = "סטטוס רישיון"
CANCELED_STATUS = "בוטל בעקבות השגה"
DENIED_STATUS = "בקשה נדחתה"
OPEN_STATUS = "מושהה ופתוח להגשת השגה"
DEADLINE_COL = "תאריך אחרון להגשת השגה"
APPLICANT_COL = "מבקש"
LICENSE_COL = "מספר רישיון"
STREET_COL = "רחוב ומספר בית"
REASON_COL = "סיבת בקשה"
GUSH_COL = "גוש"
HELKA_COL = "חלקה"
PLAN_NUMBER_COL = "מספר תכנית"
PLAN_URL_COL = "קישור לתכנית"
GOVMAP_URL_COL = "קישור ל-GovMap"

COLORS = ["#4caf50", "#e05353", "#2ba8e0", "#f5a623", "#a1725c", "#2e8b57"]

# Warm, high-contrast "tree lovers" theme: forest greens, sunny amber and a
# cream paper background, bigger type and rounder shapes than a typical
# admin dashboard so it's comfortable for older/less tech-savvy readers.
# Loaded once in <head> on every page for a consistent, friendly typeface
# with solid Hebrew glyph coverage.
FONT_LINKS = """<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Rubik:wght@400;500;600;700&display=swap" rel="stylesheet">"""

# Shared visual theme for every report page. Each build_* function embeds
# this once via an f-string {BASE_CSS} placeholder, then layers a handful
# of page-specific rules on top (container width, sticky header/table
# variants) - see individual style blocks below.
BASE_CSS = """
    :root {
        --forest-dark: #1b5e34;
        --forest: #2e7d46;
        --leaf: #4caf50;
        --leaf-light: #a5d6a7;
        --sun: #f5a623;
        --sky: #2ba8e0;
        --berry: #e05353;
        --bark: #a1725c;
        --terracotta: #e08130;
        --bg: #f2f7ee;
        --card: #ffffff;
        --ink: #24331f;
        --ink-soft: #5b6f56;
        --border: #dfe9d8;
        --shadow: rgba(27, 94, 52, 0.16);
        --shadow-soft: rgba(27, 94, 52, 0.10);
    }
    * { box-sizing: border-box; }
    body { font-family: 'Rubik', 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: var(--bg); color: var(--ink); margin: 0; padding: 20px; font-size: 17px; line-height: 1.65; }
    .container { max-width: 1100px; margin: 0 auto; }
    .portal-bar { text-align: center; margin-bottom: 10px; }
    .portal-bar a { color: var(--forest-dark); font-size: 13px; font-weight: 500; text-decoration: none; background: #eaf3e6; padding: 6px 16px; border-radius: 999px; display: inline-block; }
    .portal-bar a:hover { background: var(--leaf-light); }
    header { background: linear-gradient(135deg, var(--forest-dark), var(--forest) 60%, var(--leaf)); color: #fff; padding: 22px 26px; border-radius: 18px; margin-bottom: 25px; box-shadow: 0 8px 20px var(--shadow); }
    header a { color: #eafbe7; font-weight: 500; }
    header a:hover { color: #fff; }
    h1 { margin: 0; font-size: 28px; font-weight: 700; }
    .subtitle { margin: 6px 0 0; font-size: 14px; color: #d7ecd2; }
    h2 { color: var(--forest-dark); border-bottom: 3px solid var(--leaf-light); padding-bottom: 8px; font-size: 20px; }
    .tabs { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0 15px; }
    .tab-btn { background: #eaf3e6; border: none; border-radius: 999px; padding: 9px 18px; font-size: 14px; font-weight: 500; color: var(--forest-dark); cursor: pointer; }
    .tab-btn:hover { background: var(--leaf-light); }
    .tab-btn.active { background: var(--forest); color: #fff; }
    .tab-panel { display: none; }
    .tab-panel.active { display: block; }
    .period-note { color: var(--ink-soft); font-size: 13px; margin: 0 0 10px; }
    .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 30px; }
    .card { background: var(--card); padding: 22px; border-radius: 16px; box-shadow: 0 6px 16px var(--shadow-soft); text-align: center; border-top: 5px solid var(--leaf); }
    .card.cut { border-top-color: var(--berry); }
    .card.move { border-top-color: var(--sky); }
    .card.keep { border-top-color: var(--sun); }
    .card.meta { border-top-color: var(--bark); }
    .card.canceled { border-top-color: var(--terracotta); }
    .card-val { font-size: 30px; font-weight: 700; margin: 10px 0; color: var(--forest-dark); }
    .card-lbl { font-size: 14px; color: var(--ink-soft); }
    .grid-2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(480px, 100%), 1fr)); gap: 25px; margin-bottom: 30px; }
    .panel { background: var(--card); padding: 22px; border-radius: 16px; box-shadow: 0 6px 16px var(--shadow-soft); margin-bottom: 30px; }
    .panel.explain h2 { color: var(--forest-dark); margin-top: 0; font-size: 19px; }
    .panel.explain ul { margin: 10px 0; padding-right: 20px; }
    .panel.explain li { margin-bottom: 8px; }
    .explain-header { display: flex; justify-content: space-between; align-items: baseline; gap: 10px; flex-wrap: wrap; }
    .created-at { color: var(--ink-soft); font-size: 12px; white-space: nowrap; }
    .note { color: var(--ink-soft); font-size: 14px; margin: 0 0 15px; }
    .toolbar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 12px; }
    #citySearch { padding: 10px 14px; border: 2px solid var(--border); border-radius: 10px; font-size: 15px; width: 260px; max-width: 100%; }
    #cityFilter { padding: 10px 14px; border: 2px solid var(--border); border-radius: 10px; font-size: 15px; max-width: 100%; }
    #citySearch:focus, #cityFilter:focus { outline: 3px solid var(--leaf-light); border-color: var(--forest); }
    #cityCount { color: var(--ink-soft); font-size: 14px; }
    .toolbar-actions { display: flex; gap: 8px; flex-wrap: wrap; }
    .export-btn { background: var(--sky); color: #fff; border: none; border-radius: 10px; padding: 10px 18px; font-size: 14px; font-weight: 500; cursor: pointer; }
    .export-btn:hover { background: #218cb8; }
    .table-scroll { overflow: auto; -webkit-overflow-scrolling: touch; position: relative; max-height: 70vh; }
    .table-scroll.is-scrollable::before { content: "👉 אפשר להחליק את הטבלה כדי לראות עוד עמודות"; display: none; }
    table { width: 100%; border-collapse: collapse; margin-top: 15px; }
    th, td { padding: 12px 14px; text-align: right; border-bottom: 1px solid var(--border); font-size: 15.5px; }
    th { background-color: #eef5ea; color: var(--forest-dark); font-weight: 600; white-space: nowrap; }
    th[onclick] { cursor: pointer; user-select: none; }
    th.sort-asc::after { content: " \\25B2"; font-size: 10px; }
    th.sort-desc::after { content: " \\25BC"; font-size: 10px; }
    #cityTable th:nth-child(3), #cityTable td:nth-child(3) { min-width: 120px; white-space: nowrap; }
    .map-icon { text-decoration: none; margin-inline-start: 2px; font-size: 18px; vertical-align: middle; }
    tr:hover { background-color: #f7fbf4; }
    .chart-img { max-width: 100%; height: auto; border-radius: 12px; }
    footer { text-align: center; color: var(--ink-soft); font-size: 13px; margin: 30px 0 10px; }
    .print-btn { background: var(--forest); color: #fff; border: none; border-radius: 999px; padding: 10px 20px; font-size: 14px; font-weight: 500; cursor: pointer; margin-top: 12px; }
    .print-btn:hover { background: var(--forest-dark); }
    a { color: var(--sky); }
    @media print {
        .print-btn, .portal-bar { display: none !important; }
        body { background: #fff; padding: 0; }
        .card, .panel { box-shadow: none; }
    }
    @media (max-width: 640px) {
        body { padding: 10px; font-size: 16px; }
        header { padding: 16px 18px; border-radius: 14px; }
        .panel, .card { padding: 14px; border-radius: 12px; }
        .cards { gap: 12px; }
        th, td { padding: 8px; }
        th { white-space: normal; }
        .table-scroll.is-scrollable::before {
            display: block; background: var(--sun); color: #3a2a00; font-weight: 600;
            font-size: 13px; padding: 8px 12px; border-radius: 10px; margin-bottom: 8px; text-align: center;
        }
    }
"""

# Flags any .table-scroll wrapper whose table is actually wider than its
# viewport with .is-scrollable, so the CSS-only "swipe to see more" hint
# (see BASE_CSS) only ever shows up on tables that truly need it, on
# screens narrow enough (see the 640px breakpoint) that it matters.
SCROLL_HINT_SCRIPT = """
function markScrollableTables() {
    document.querySelectorAll('.table-scroll').forEach(el => {
        el.classList.toggle('is-scrollable', el.scrollWidth > el.clientWidth + 2);
    });
}
markScrollableTables();
window.addEventListener('resize', markScrollableTables);
"""

# Shared client-side export logic for sortable/filterable city tables.
# Operates on currently visible (filtered) rows, in current DOM (sorted)
# order, so exports match whatever the user has on screen. downloadExcel
# writes SpreadsheetML 2003 XML (a native .xls Excel understands directly,
# no ZIP/OOXML involved) rather than embedding a JS library, keeping every
# report page dependency-free and self-contained.
EXPORT_SCRIPT = """
function tableVisibleRows(tableId) {
    const table = document.getElementById(tableId);
    const headers = Array.from(table.querySelectorAll('thead th')).map(th => th.textContent.trim());
    const rows = Array.from(table.querySelectorAll('tbody tr'))
        .filter(tr => tr.style.display !== 'none')
        .map(tr => Array.from(tr.children).map(td => td.textContent.trim()));
    return {headers, rows};
}

function triggerDownload(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

function downloadCSV(tableId, filename) {
    const {headers, rows} = tableVisibleRows(tableId);
    const escCsv = v => '"' + String(v).replace(/"/g, '""') + '"';
    const lines = [headers, ...rows].map(r => r.map(escCsv).join(','));
    const blob = new Blob(['\\ufeff' + lines.join('\\r\\n')], {type: 'text/csv;charset=utf-8;'});
    triggerDownload(blob, filename);
}

function downloadExcel(tableId, filename) {
    const {headers, rows} = tableVisibleRows(tableId);
    const escXml = v => String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const cell = v => `<Cell><Data ss:Type="String">${escXml(v)}</Data></Cell>`;
    const row = cells => `<Row>${cells.map(cell).join('')}</Row>`;
    const xml = `<?xml version="1.0"?>
<?mso-application progid="Excel.Sheet"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
 xmlns:o="urn:schemas-microsoft-com:office:office"
 xmlns:x="urn:schemas-microsoft-com:office:excel"
 xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
 <Worksheet ss:Name="Sheet1">
  <Table>
   ${row(headers)}
   ${rows.map(row).join('')}
  </Table>
 </Worksheet>
</Workbook>`;
    const blob = new Blob([xml], {type: 'application/vnd.ms-excel;charset=utf-8;'});
    triggerDownload(blob, filename);
}
"""

# (key, label, lookback in days; None = full history). Selectable as tabs
# on the trend panel. Early on, snapshots don't span a year yet, so the
# longer tabs just show everything available until more weeks accumulate.
TREND_PERIODS = [
    ("month", "חודש אחרון", 30),
    ("q", "3 חודשים", 91),
    ("half", "6 חודשים", 182),
    ("year", "שנה", 365),
    ("all", "כל ההיסטוריה", None),
]


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
    ax.grid(axis="y", color="#dfe9d8", linewidth=1)
    ax.set_axisbelow(True)


def make_trend_chart(trend):
    fig, ax1 = plt.subplots(figsize=(10, 4.5))
    ax1.plot(trend.index, trend["licenses"], marker="o", color="#2ba8e0", label="סה\"כ רישיונות")
    ax1.set_ylabel("רישיונות", color="#2ba8e0")
    ax1.tick_params(axis="y", labelcolor="#2ba8e0")
    plt.setp(ax1.get_xticklabels(), rotation=45, ha="right")

    ax2 = ax1.twinx()
    ax2.plot(trend.index, trend["cut"], marker="s", color="#e05353", label="עצים לכריתה")
    ax2.set_ylabel("עצים לכריתה", color="#e05353")
    ax2.tick_params(axis="y", labelcolor="#e05353")

    ax1.set_title("מגמת רישיונות ועצים מאושרים לכריתה לאורך זמן")
    style_axes(ax1)
    fig.tight_layout()
    return chart_to_data_uri(fig)


def make_barh_chart(series, title, color):
    fig, ax = plt.subplots(figsize=(6, max(4.5, 0.35 * len(series))))
    ordered = series.sort_values(ascending=True)
    ax.barh(ordered.index, ordered.values, color=color)
    ax.set_title(title)
    style_axes(ax)
    ax.grid(axis="x", color="#dfe9d8", linewidth=1)
    fig.tight_layout()
    return chart_to_data_uri(fig)


def filter_trend_period(trend, days):
    if days is None or len(trend) == 0:
        return trend
    latest = pd.to_datetime(trend.index.max())
    cutoff = (latest - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
    return trend[trend.index >= cutoff]


def build_trend_rows(trend):
    rows = ""
    prev = None
    for date_str, row in trend.iterrows():
        new_licenses = int(row["licenses"] - prev["licenses"]) if prev is not None else 0
        new_cut = int(row["cut"] - prev["cut"]) if prev is not None else 0
        rows += (
            f"<tr><td>{date_str}</td><td>{int(row['licenses']):,}</td>"
            f"<td>{new_licenses:+,}</td><td>{int(row['cut']):,}</td><td>{new_cut:+,}</td></tr>"
        )
        prev = row
    return rows


def esc(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_nav_links(latest_date):
    """Identical, identically-ordered cross-links for every report page's
    header, including a (harmless) link to the current page itself - so
    the nav looks the same everywhere instead of each page hand-omitting
    itself in a different order."""
    return (
        f'<a href="open_for_objection.html">פתוחים להגשת השגה</a> &middot; '
        f'<a href="report_{latest_date}.html">הדוח המלא</a> &middot; '
        f'<a href="objections.html">דו"ח היענות הרשות</a> &middot; '
        f'<a href="by_city.html">דוח לפי יישוב</a> &middot; '
        f'<a href="orphaned_cities.html">יישובים "יתומים"</a> &middot; '
        f'<a href="index.html">כל הדוחות</a>'
    )


def table_rows(pairs):
    return "".join(f"<tr><td>{esc(a)}</td><td>{b:,}</td></tr>" for a, b in pairs)


def build_report(latest_date, df, trend):
    total_licenses = len(df)
    total_cut = int(df[CUT_COL].sum())
    total_move = int(df[MOVE_COL].sum())
    total_keep = int(df[KEEP_COL].sum())
    n_cities = df[CITY_COL].nunique()
    n_species = df[SPECIES_COL].nunique()
    n_canceled = int((df[STATUS_COL] == CANCELED_STATUS).sum())

    top_species = df.groupby(SPECIES_COL)[CUT_COL].sum().sort_values(ascending=False).head(20)
    top_cities = df.groupby(CITY_COL)[CUT_COL].sum().sort_values(ascending=False).head(20)
    status_counts = df[STATUS_COL].value_counts().head(8)

    species_chart = make_barh_chart(top_species, "20 מיני העצים המובילים בכריתה", "#e05353")
    cities_chart = make_barh_chart(top_cities, "20 היישובים המובילים בכריתה", "#4caf50")
    status_chart = make_barh_chart(status_counts, "התפלגות סטטוס רישיונות", "#f5a623")

    tab_buttons = ""
    tab_panels = ""
    for i, (key, label, days) in enumerate(TREND_PERIODS):
        ptrend = filter_trend_period(trend, days)
        active = " active" if i == len(TREND_PERIODS) - 1 else ""  # default: כל ההיסטוריה
        tab_buttons += f'<button class="tab-btn{active}" id="btn-{key}" onclick="showTrendTab(\'{key}\')">{label}</button>'

        if len(ptrend) > 1:
            chart_img = f'<img class="chart-img" src="{make_trend_chart(ptrend)}" alt="גרף מגמות - {label}">'
        else:
            chart_img = "<p>אין עדיין מספיק תמונות היסטוריות לתקופה זו.</p>"
        period_first = ptrend.index[0] if len(ptrend) else latest_date

        tab_panels += f"""<div class="tab-panel{active}" id="tab-{key}">
            <p class="period-note">מאז {period_first}</p>
            {chart_img}
            <div class="table-scroll">
            <table>
                <thead><tr><th>תאריך</th><th>סה"כ רישיונות</th><th>שינוי רישיונות</th><th>סה"כ עצים לכריתה</th><th>שינוי עצים לכריתה</th></tr></thead>
                <tbody>{build_trend_rows(ptrend)}</tbody>
            </table>
            </div>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>דוח סטטיסטיקה ומגמות - רישיונות כריתה ({latest_date})</title>
{FONT_LINKS}
<style>
{BASE_CSS}
    .container {{ max-width: 1400px; }}
    @media print {{ .tabs {{ display: none !important; }} }}
</style>
</head>
<body>
<div class="container">
    <div class="portal-bar"><a href="https://agmonr.github.io/govapiportal/" target="_blank" rel="noopener">🏛️ חלק מ-GovAPIPortal</a></div>
    <header>
        <h1>🌳 דוח מגמות וסטטיסטיקה: רישיונות כריתה והעתקה</h1>
        <p class="subtitle">פרויקט של רם אגמון, הוד השרון, עבור נאמני העצים, הצטרפו לנאמני העצים</p>
        <p class="subtitle">האתר בהרצה, עלולות להיות טעויות</p>
        <p>נתונים נכון לתאריך {latest_date} &middot; {build_nav_links(latest_date)}</p>
        <button class="print-btn" onclick="window.print()">ייצוא כ-PDF (הדפסה)</button>
    </header>

    <div class="cards">
        <div class="card canceled"><div class="card-val">{n_canceled:,}</div><div class="card-lbl">בוטל בעקבות השגה</div></div>
        <div class="card"><div class="card-val">{total_licenses:,}</div><div class="card-lbl">סך הכל רישיונות במערכת</div></div>
        <div class="card cut"><div class="card-val">{total_cut:,}</div><div class="card-lbl">עצים מאושרים לכריתה</div></div>
        <div class="card move"><div class="card-val">{total_move:,}</div><div class="card-lbl">עצים מאושרים להעתקה</div></div>
        <div class="card keep"><div class="card-val">{total_keep:,}</div><div class="card-lbl">עצים לשימור</div></div>
        <div class="card meta"><div class="card-val">{n_cities:,}</div><div class="card-lbl">יישובים</div></div>
        <div class="card meta"><div class="card-val">{n_species:,}</div><div class="card-lbl">מיני עצים</div></div>
    </div>

    <div class="panel">
        <h2>מגמות לאורך זמן</h2>
        <div class="tabs">{tab_buttons}</div>
        {tab_panels}
    </div>

    <div class="grid-2">
        <div class="panel">
            <img class="chart-img" src="{species_chart}" alt="גרף מיני עצים">
            <div class="table-scroll">
            <table>
                <thead><tr><th>מין העץ</th><th>סה"כ עצים לכריתה</th></tr></thead>
                <tbody>{table_rows(top_species.items())}</tbody>
            </table>
            </div>
        </div>
        <div class="panel">
            <img class="chart-img" src="{cities_chart}" alt="גרף יישובים">
            <div class="table-scroll">
            <table>
                <thead><tr><th>ישוב</th><th>סה"כ עצים לכריתה</th></tr></thead>
                <tbody>{table_rows(top_cities.items())}</tbody>
            </table>
            </div>
        </div>
    </div>

    <div class="panel">
        <h2>סטטוס רישיונות</h2>
        <img class="chart-img" src="{status_chart}" alt="גרף סטטוס רישיונות">
        <div class="table-scroll">
        <table>
            <thead><tr><th>סטטוס רישיון</th><th>כמות רישיונות</th></tr></thead>
            <tbody>{table_rows(status_counts.items())}</tbody>
        </table>
        </div>
    </div>

    <footer>
        נוצר אוטומטית ב-{datetime.now(timezone.utc).astimezone().strftime('%d/%m/%Y %H:%M')} מתוך {len(trend)} תמונות ארכיון היסטוריות.<br>
        נוצר על ידי רם אגמון, הוד השרון.
    </footer>
</div>
<script>
{SCROLL_HINT_SCRIPT}
function showTrendTab(key) {{
    document.querySelectorAll('.tab-panel').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
    document.getElementById('tab-' + key).classList.add('active');
    document.getElementById('btn-' + key).classList.add('active');
    markScrollableTables();
}}
</script>
</body>
</html>
"""


def build_city_report(latest_date, df):
    city_stats = (
        df.groupby(CITY_COL)
        .agg(
            licenses=(CITY_COL, "size"),
            cut=(CUT_COL, "sum"),
            move=(MOVE_COL, "sum"),
            keep=(KEEP_COL, "sum"),
            species=(SPECIES_COL, "nunique"),
        )
        .sort_values("licenses", ascending=False)
    )

    def format_ratio(cut, move):
        if move == 0:
            return "∞" if cut > 0 else "—"
        return f"{cut / move:.1f}"

    rows = "".join(
        f"<tr><td>{esc(city)}</td>"
        f"<td>{int(row.licenses):,}</td>"
        f"<td>{int(row.cut):,}</td>"
        f"<td>{int(row.move):,}</td>"
        f"<td>{format_ratio(row.cut, row.move)}</td>"
        f"<td>{int(row.keep):,}</td>"
        f"<td>{int(row.species):,}</td></tr>"
        for city, row in city_stats.iterrows()
    )

    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>דוח לפי יישוב - רישיונות כריתה ({latest_date})</title>
{FONT_LINKS}
<style>
{BASE_CSS}
    .container {{ max-width: 1300px; }}
    thead th {{ position: sticky; top: 0; z-index: 15; box-shadow: 0 2px 2px -1px var(--shadow-soft); }}
    @media print {{
        .toolbar {{ display: none !important; }}
        thead th {{ position: static; box-shadow: none; }}
    }}
</style>
</head>
<body>
<div class="container">
    <div class="portal-bar"><a href="https://agmonr.github.io/govapiportal/" target="_blank" rel="noopener">🏛️ חלק מ-GovAPIPortal</a></div>
    <header>
        <h1>🌳 דוח לפי יישוב: רישיונות כריתה והעתקה</h1>
        <p class="subtitle">פרויקט של רם אגמון, הוד השרון, עבור נאמני העצים, הצטרפו לנאמני העצים</p>
        <p class="subtitle">האתר בהרצה, עלולות להיות טעויות</p>
        <p>נתונים נכון לתאריך {latest_date} &middot; {build_nav_links(latest_date)}</p>
        <button class="print-btn" onclick="window.print()">ייצוא כ-PDF (הדפסה)</button>
    </header>

    <div class="panel">
        <div class="toolbar">
            <input type="text" id="citySearch" placeholder="חיפוש יישוב..." oninput="filterCities()">
            <span id="cityCount"></span>
            <div class="toolbar-actions">
                <button class="export-btn" onclick="downloadCSV('cityTable', 'by_city_{latest_date}.csv')">הורדה כ-CSV</button>
                <button class="export-btn" onclick="downloadExcel('cityTable', 'by_city_{latest_date}.xls')">הורדה כ-Excel</button>
            </div>
        </div>
        <div class="table-scroll">
        <table id="cityTable">
            <thead>
                <tr>
                    <th data-col="0" onclick="sortCities(0, 'string')">ישוב</th>
                    <th data-col="1" onclick="sortCities(1, 'number')">רישיונות</th>
                    <th data-col="2" onclick="sortCities(2, 'number')">לכריתה</th>
                    <th data-col="3" onclick="sortCities(3, 'number')">להעתקה</th>
                    <th data-col="4" onclick="sortCities(4, 'number')">יחס כריתה/העתקה</th>
                    <th data-col="5" onclick="sortCities(5, 'number')">לשימור</th>
                    <th data-col="6" onclick="sortCities(6, 'number')">מיני עצים</th>
                </tr>
            </thead>
            <tbody id="cityBody">{rows}</tbody>
        </table>
        </div>
    </div>

    <footer>
        נוצר אוטומטית ב-{datetime.now(timezone.utc).astimezone().strftime('%d/%m/%Y %H:%M')}.<br>
        נוצר על ידי רם אגמון, הוד השרון.
    </footer>
</div>
<script>
{SCROLL_HINT_SCRIPT}
{EXPORT_SCRIPT}
function sortCities(col, type) {{
    const table = document.getElementById('cityTable');
    const tbody = document.getElementById('cityBody');
    const rows = Array.from(tbody.querySelectorAll('tr'));
    const dir = (table.dataset.sortCol == col && table.dataset.sortDir === 'asc') ? 'desc' : 'asc';

    rows.sort((a, b) => {{
        let va = a.children[col].textContent.trim();
        let vb = b.children[col].textContent.trim();
        if (type === 'number') {{
            va = parseFloat(va.replace(/,/g, '')) || 0;
            vb = parseFloat(vb.replace(/,/g, '')) || 0;
            return dir === 'asc' ? va - vb : vb - va;
        }}
        return dir === 'asc' ? va.localeCompare(vb, 'he') : vb.localeCompare(va, 'he');
    }});
    rows.forEach(r => tbody.appendChild(r));

    table.dataset.sortCol = col;
    table.dataset.sortDir = dir;
    document.querySelectorAll('#cityTable th').forEach(th => th.classList.remove('sort-asc', 'sort-desc'));
    document.querySelector(`#cityTable th[data-col="${{col}}"]`).classList.add(dir === 'asc' ? 'sort-asc' : 'sort-desc');
}}

function filterCities() {{
    const q = document.getElementById('citySearch').value.trim();
    const rows = document.querySelectorAll('#cityBody tr');
    let shown = 0;
    rows.forEach(r => {{
        const match = r.children[0].textContent.includes(q);
        r.style.display = match ? '' : 'none';
        if (match) shown++;
    }});
    document.getElementById('cityCount').textContent = `מציג ${{shown}} מתוך ${{rows.length}} ישובים`;
}}
filterCities();

</script>
</body>
</html>
"""


def build_objections_report(latest_date, df):
    """Effectiveness of התנגדות (objection) per city, measured in trees
    (CUT_COL) rather than license counts: trees that ended up saved because
    the license was cancelled following an objection (CANCELED_STATUS) or
    the original request was denied (DENIED_STATUS), against that city's
    total trees across all licenses. Only cities with at least one tree
    saved this way are listed, sorted by that combined tree count (the
    "successful" objection outcomes) descending."""
    canceled_trees = df[df[STATUS_COL] == CANCELED_STATUS].groupby(CITY_COL)[CUT_COL].sum()
    denied_trees = df[df[STATUS_COL] == DENIED_STATUS].groupby(CITY_COL)[CUT_COL].sum()
    total_trees = df.groupby(CITY_COL)[CUT_COL].sum()

    city_stats = pd.DataFrame({
        "canceled": canceled_trees,
        "denied": denied_trees,
        "total": total_trees,
    }).fillna(0).astype(int)
    city_stats["saved"] = city_stats["canceled"] + city_stats["denied"]
    city_stats = city_stats[city_stats["saved"] > 0].sort_values("saved", ascending=False)

    def denied_pct(denied, total):
        return f"{denied / total:.1%}" if total else "—"

    rows = "".join(
        f"<tr><td>{esc(city)}</td>"
        f"<td>{int(row.total):,}</td>"
        f"<td>{int(row.denied):,}</td>"
        f"<td>{int(row.canceled):,}</td>"
        f"<td>{int(row.saved):,}</td>"
        f"<td>{denied_pct(row.denied, row.total)}</td></tr>"
        for city, row in city_stats.iterrows()
    )

    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>דו"ח היענות הרשות - עצים שניצלו ({latest_date})</title>
{FONT_LINKS}
<style>
{BASE_CSS}
    .container {{ max-width: 1300px; }}
    thead th {{ position: sticky; top: 0; z-index: 15; box-shadow: 0 2px 2px -1px var(--shadow-soft); }}
    @media print {{
        .toolbar {{ display: none !important; }}
        thead th {{ position: static; box-shadow: none; }}
    }}
</style>
</head>
<body>
<div class="container">
    <div class="portal-bar"><a href="https://agmonr.github.io/govapiportal/" target="_blank" rel="noopener">🏛️ חלק מ-GovAPIPortal</a></div>
    <header>
        <h1>🌳 דו"ח היענות הרשות</h1>
        <p class="subtitle">פרויקט של רם אגמון, הוד השרון, עבור נאמני העצים, הצטרפו לנאמני העצים</p>
        <p class="subtitle">האתר בהרצה, עלולות להיות טעויות</p>
        <p>נתונים נכון לתאריך {latest_date} &middot; {build_nav_links(latest_date)}</p>
        <button class="print-btn" onclick="window.print()">ייצוא כ-PDF (הדפסה)</button>
    </header>

    <div class="panel explain">
        <h2>מה מציג הדוח</h2>
        <p>הדוח בודק כמה עצים שהיו אמורים להיכרת בסופו של דבר ניצלו, מתוך כלל העצים שהוגשה עליהם בקשה לכריתה בכל יישוב. עץ נחשב "ניצל" כאשר הרישיון שלו הגיע לאחת משתי תוצאות:</p>
        <ul>
            <li><strong>עצים - בוטל בעקבות השגה</strong> &ndash; הרישיון בוטל כי מישהו הגיש השגה (התנגדות) עליו, וההשגה התקבלה. זו ההשפעה הישירה והמדידה של הגשת התנגדות.</li>
            <li><strong>עצים - בקשה נדחתה</strong> &ndash; הבקשה המקורית לכריתה נדחתה, בדרך כלל על ידי פקיד היערות, עוד לפני שהגיעה לשלב ההשגה הציבורית. העצים ניצלו, אך לא בהכרח בזכות השגה &ndash; במידע הקיים אין דרך לדעת אם הוגשה השגה על בקשות אלו.</li>
        </ul>
        <p><strong>סה"כ עצים שניצלו</strong> הוא הסכום של שתי הקטגוריות הנ"ל, כלומר כלל העצים שלא נכרתו מכל סיבה חוסמת &ndash; ואילו <strong>עצים - בוטל בעקבות השגה</strong> מבודד את תת-הקבוצה שבה השגה היא הסיבה המתועדת לביטול.</p>
    </div>

    <div class="panel">
        <p class="note">מציג יישובים שבהם ניצל לפחות עץ אחד בעקבות רישיון שבוטל עקב השגה או שבקשתו נדחתה. מיון ברירת מחדל: מספר העצים שניצלו.</p>
        <div class="toolbar">
            <input type="text" id="citySearch" placeholder="חיפוש יישוב..." oninput="filterCities()">
            <span id="cityCount"></span>
            <div class="toolbar-actions">
                <button class="export-btn" onclick="downloadCSV('cityTable', 'objections_{latest_date}.csv')">הורדה כ-CSV</button>
                <button class="export-btn" onclick="downloadExcel('cityTable', 'objections_{latest_date}.xls')">הורדה כ-Excel</button>
            </div>
        </div>
        <div class="table-scroll">
        <table id="cityTable" data-sort-col="4" data-sort-dir="desc">
            <thead>
                <tr>
                    <th data-col="0" onclick="sortCities(0, 'string')">ישוב</th>
                    <th data-col="1" onclick="sortCities(1, 'number')">סה"כ עצים לכריתה ביישוב</th>
                    <th data-col="2" onclick="sortCities(2, 'number')">עצים - בקשה נדחתה</th>
                    <th data-col="3" onclick="sortCities(3, 'number')">עצים - בוטל בעקבות השגה</th>
                    <th data-col="4" class="sort-desc" onclick="sortCities(4, 'number')">סה"כ עצים שניצלו</th>
                    <th data-col="5" onclick="sortCities(5, 'number')">% בקשה נדחתה</th>
                </tr>
            </thead>
            <tbody id="cityBody">{rows}</tbody>
        </table>
        </div>
    </div>

    <footer>
        נוצר אוטומטית ב-{datetime.now(timezone.utc).astimezone().strftime('%d/%m/%Y %H:%M')}.<br>
        נוצר על ידי רם אגמון, הוד השרון.
    </footer>
</div>
<script>
{SCROLL_HINT_SCRIPT}
{EXPORT_SCRIPT}
function sortCities(col, type) {{
    const table = document.getElementById('cityTable');
    const tbody = document.getElementById('cityBody');
    const rows = Array.from(tbody.querySelectorAll('tr'));
    const dir = (table.dataset.sortCol == col && table.dataset.sortDir === 'asc') ? 'desc' : 'asc';

    rows.sort((a, b) => {{
        let va = a.children[col].textContent.trim();
        let vb = b.children[col].textContent.trim();
        if (type === 'number') {{
            va = parseFloat(va.replace(/,/g, '')) || 0;
            vb = parseFloat(vb.replace(/,/g, '')) || 0;
            return dir === 'asc' ? va - vb : vb - va;
        }}
        return dir === 'asc' ? va.localeCompare(vb, 'he') : vb.localeCompare(va, 'he');
    }});
    rows.forEach(r => tbody.appendChild(r));

    table.dataset.sortCol = col;
    table.dataset.sortDir = dir;
    document.querySelectorAll('#cityTable th').forEach(th => th.classList.remove('sort-asc', 'sort-desc'));
    document.querySelector(`#cityTable th[data-col="${{col}}"]`).classList.add(dir === 'asc' ? 'sort-asc' : 'sort-desc');
}}

function filterCities() {{
    const q = document.getElementById('citySearch').value.trim();
    const rows = document.querySelectorAll('#cityBody tr');
    let shown = 0;
    rows.forEach(r => {{
        const match = r.children[0].textContent.includes(q);
        r.style.display = match ? '' : 'none';
        if (match) shown++;
    }});
    document.getElementById('cityCount').textContent = `מציג ${{shown}} מתוך ${{rows.length}} ישובים`;
}}
filterCities();

</script>
</body>
</html>
"""


def build_orphaned_cities_report(latest_date, df):
    """Complement of build_objections_report: cities where, as far as
    license outcomes show, no objection has ever succeeded - zero licenses
    ever reached CANCELED_STATUS or DENIED_STATUS (the only two outcomes
    that indicate a blocked cutting; see build_objections_report's caveat
    that the raw data can't show whether an objection was filed and lost).
    Restricted to cities with at least one tree ever up for cutting, so
    cities with no cutting activity at all aren't listed as neglected.
    Sorted by trees at risk (total cut) descending, to surface where
    organizing would matter most."""
    canceled_trees = df[df[STATUS_COL] == CANCELED_STATUS].groupby(CITY_COL)[CUT_COL].sum()
    denied_trees = df[df[STATUS_COL] == DENIED_STATUS].groupby(CITY_COL)[CUT_COL].sum()
    total_trees = df.groupby(CITY_COL)[CUT_COL].sum()
    license_count = df.groupby(CITY_COL).size()
    open_count = df[df[STATUS_COL] == OPEN_STATUS].groupby(CITY_COL).size()

    city_stats = pd.DataFrame({
        "canceled": canceled_trees,
        "denied": denied_trees,
        "total": total_trees,
        "licenses": license_count,
        "open": open_count,
    }).fillna(0).astype(int)
    city_stats["saved"] = city_stats["canceled"] + city_stats["denied"]
    city_stats = city_stats[(city_stats["saved"] == 0) & (city_stats["total"] > 0)]
    city_stats = city_stats.sort_values("total", ascending=False)

    rows = "".join(
        f"<tr><td>{esc(city)}</td>"
        f"<td>{int(row.licenses):,}</td>"
        f"<td>{int(row.total):,}</td>"
        f"<td>{int(row.open):,}</td></tr>"
        for city, row in city_stats.iterrows()
    )

    n_cities = len(city_stats)
    n_trees = int(city_stats["total"].sum())

    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>יישובים "יתומים" - ללא השגה שהצליחה ({latest_date})</title>
{FONT_LINKS}
<style>
{BASE_CSS}
    .container {{ max-width: 1300px; }}
    .cards {{ grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }}
    .card {{ border-top-color: var(--berry); }}
    thead th {{ position: sticky; top: 0; z-index: 15; box-shadow: 0 2px 2px -1px var(--shadow-soft); }}
    @media print {{
        .toolbar {{ display: none !important; }}
        thead th {{ position: static; box-shadow: none; }}
    }}
</style>
</head>
<body>
<div class="container">
    <div class="portal-bar"><a href="https://agmonr.github.io/govapiportal/" target="_blank" rel="noopener">🏛️ חלק מ-GovAPIPortal</a></div>
    <header>
        <h1>🌳 יישובים "יתומים" - ללא השגה שהצליחה</h1>
        <p class="subtitle">פרויקט של רם אגמון, הוד השרון, עבור נאמני העצים, הצטרפו לנאמני העצים</p>
        <p class="subtitle">האתר בהרצה, עלולות להיות טעויות</p>
        <p>נתונים נכון לתאריך {latest_date} &middot; {build_nav_links(latest_date)}</p>
        <button class="print-btn" onclick="window.print()">ייצוא כ-PDF (הדפסה)</button>
    </header>

    <div class="panel explain">
        <h2>מה מציג הדוח</h2>
        <p>הדוח מציג יישובים שבהם, למרות שהוגשו בהם בקשות לכריתת עצים, <strong>אף רישיון מעולם לא הגיע לסטטוס "{CANCELED_STATUS}" או "{DENIED_STATUS}"</strong> &ndash; כלומר, ככל הידוע מהנתונים הגלויים, אף עץ ביישוב לא ניצל בעקבות השגה או דחייה. אלו יישובים שנראה כי אין בהם כרגע מי שעוקב ומגיש השגות, ולכן העצים בהם "יתומים".</p>
        <p>לתשומת לב: היעדר עצים שניצלו אינו הוכחה חד-משמעית שאף אחד מעולם לא הגיש השגה ביישוב &ndash; ייתכן שהוגשו השגות שנדחו, ואין דרך לדעת זאת מתוך הנתונים הגלויים לציבור (ר' גם ההסבר <a href="objections.html">בדו"ח היענות הרשות</a>). זהו אינדיקטור, לא קביעה סופית.</p>
        <p class="note">מיון ברירת מחדל: סה"כ עצים לכריתה ביישוב (מהגדול לקטן), כדי להעלות קודם את היישובים שבהם ההיעדרות משמעותית ביותר.</p>
    </div>

    <div class="cards">
        <div class="card"><div class="card-val">{n_cities:,}</div><div class="card-lbl">יישובים יתומים</div></div>
        <div class="card"><div class="card-val">{n_trees:,}</div><div class="card-lbl">עצים לכריתה ביישובים אלו</div></div>
    </div>

    <div class="panel">
        <div class="toolbar">
            <input type="text" id="citySearch" placeholder="חיפוש יישוב..." oninput="filterCities()">
            <span id="cityCount"></span>
            <div class="toolbar-actions">
                <button class="export-btn" onclick="downloadCSV('cityTable', 'orphaned_cities_{latest_date}.csv')">הורדה כ-CSV</button>
                <button class="export-btn" onclick="downloadExcel('cityTable', 'orphaned_cities_{latest_date}.xls')">הורדה כ-Excel</button>
            </div>
        </div>
        <div class="table-scroll">
        <table id="cityTable" data-sort-col="2" data-sort-dir="desc">
            <thead>
                <tr>
                    <th data-col="0" onclick="sortCities(0, 'string')">ישוב</th>
                    <th data-col="1" onclick="sortCities(1, 'number')">סה"כ רישיונות</th>
                    <th data-col="2" class="sort-desc" onclick="sortCities(2, 'number')">סה"כ עצים לכריתה</th>
                    <th data-col="3" onclick="sortCities(3, 'number')">רישיונות פתוחים כרגע להשגה</th>
                </tr>
            </thead>
            <tbody id="cityBody">{rows}</tbody>
        </table>
        </div>
    </div>

    <footer>
        נוצר אוטומטית ב-{datetime.now(timezone.utc).astimezone().strftime('%d/%m/%Y %H:%M')}.<br>
        נוצר על ידי רם אגמון, הוד השרון.
    </footer>
</div>
<script>
{SCROLL_HINT_SCRIPT}
{EXPORT_SCRIPT}
function sortCities(col, type) {{
    const table = document.getElementById('cityTable');
    const tbody = document.getElementById('cityBody');
    const rows = Array.from(tbody.querySelectorAll('tr'));
    const dir = (table.dataset.sortCol == col && table.dataset.sortDir === 'asc') ? 'desc' : 'asc';

    rows.sort((a, b) => {{
        let va = a.children[col].textContent.trim();
        let vb = b.children[col].textContent.trim();
        if (type === 'number') {{
            va = parseFloat(va.replace(/,/g, '')) || 0;
            vb = parseFloat(vb.replace(/,/g, '')) || 0;
            return dir === 'asc' ? va - vb : vb - va;
        }}
        return dir === 'asc' ? va.localeCompare(vb, 'he') : vb.localeCompare(va, 'he');
    }});
    rows.forEach(r => tbody.appendChild(r));

    table.dataset.sortCol = col;
    table.dataset.sortDir = dir;
    document.querySelectorAll('#cityTable th').forEach(th => th.classList.remove('sort-asc', 'sort-desc'));
    document.querySelector(`#cityTable th[data-col="${{col}}"]`).classList.add(dir === 'asc' ? 'sort-asc' : 'sort-desc');
}}

function filterCities() {{
    const q = document.getElementById('citySearch').value.trim();
    const rows = document.querySelectorAll('#cityBody tr');
    let shown = 0;
    rows.forEach(r => {{
        const match = r.children[0].textContent.includes(q);
        r.style.display = match ? '' : 'none';
        if (match) shown++;
    }});
    document.getElementById('cityCount').textContent = `מציג ${{shown}} מתוך ${{rows.length}} ישובים`;
}}
filterCities();

</script>
</body>
</html>
"""


def build_open_objections_report(latest_date, df):
    """Live actionable list: every license currently open for a new
    objection to be filed (OPEN_STATUS), sorted by nearest deadline first.
    Unlike the other reports (retrospective statistics), this one exists to
    drive real-time action - so days-left is computed against latest_date
    (the snapshot's own date), not wall-clock "today", keeping the report
    reproducible on rebuild.

    A single license spans one row per tree species, with city/applicant/
    deadline repeated identically across those rows - so this groups by
    LICENSE_COL first and sums CUT_COL per license, rather than listing
    species-line rows directly. Without that grouping, a license covering
    many species would show a misleadingly small per-row tree count (often
    0, e.g. for a species that's only being moved/preserved) instead of the
    real total trees at stake for that license."""
    open_df = df[df[STATUS_COL] == OPEN_STATUS].copy()
    open_df["_deadline_dt"] = pd.to_datetime(open_df[DEADLINE_COL], format="%d/%m/%Y", errors="coerce")
    ref_date = pd.to_datetime(latest_date)
    open_df["_days_left"] = (open_df["_deadline_dt"] - ref_date).dt.days

    def join_species(species):
        uniq = sorted(set(species))
        shown = ", ".join(uniq[:3])
        if len(uniq) > 3:
            shown += f" ועוד {len(uniq) - 3}"
        return shown

    has_reason = REASON_COL in open_df.columns
    has_gush = GUSH_COL in open_df.columns and HELKA_COL in open_df.columns
    has_plan = PLAN_NUMBER_COL in open_df.columns and PLAN_URL_COL in open_df.columns
    has_govmap = GOVMAP_URL_COL in open_df.columns
    licenses = (
        open_df.groupby(LICENSE_COL)
        .agg(
            city=(CITY_COL, "first"),
            street=(STREET_COL, "first"),
            applicant=(APPLICANT_COL, "first"),
            deadline=(DEADLINE_COL, "first"),
            deadline_dt=("_deadline_dt", "first"),
            days_left=("_days_left", "first"),
            trees_to_cut=(CUT_COL, "sum"),
            species=(SPECIES_COL, join_species),
            **({"reason": (REASON_COL, "first")} if has_reason else {}),
            **({"gush": (GUSH_COL, "first"), "helka": (HELKA_COL, "first")} if has_gush else {}),
            **({"plan_number": (PLAN_NUMBER_COL, "first"), "plan_url": (PLAN_URL_COL, "first")} if has_plan else {}),
            **({"govmap_url": (GOVMAP_URL_COL, "first")} if has_govmap else {}),
        )
        .sort_values("days_left", ascending=True, na_position="last")
    )
    # Older archive snapshots (fetched before request-reason enrichment was
    # added to fetch_data.py) won't have REASON_COL at all - fall back to
    # blank rather than a KeyError, since this report only ever runs
    # against the latest snapshot but might be re-run against one fetched
    # before this feature existed.
    if "reason" not in licenses.columns:
        licenses["reason"] = ""
    licenses["reason"] = licenses["reason"].fillna("")
    if "gush" not in licenses.columns:
        licenses["gush"] = pd.NA
        licenses["helka"] = pd.NA
    if "plan_number" not in licenses.columns:
        licenses["plan_number"] = pd.NA
        licenses["plan_url"] = pd.NA
    if "govmap_url" not in licenses.columns:
        licenses["govmap_url"] = pd.NA

    def is_construction_reason(reason):
        text = str(reason) if pd.notna(reason) else ""
        return "בנייה" in text or "ופיתוח" in text

    def format_gush_helka(gush, helka):
        if pd.isna(gush) or pd.isna(helka):
            return "—"
        return esc(f"{gush}/{helka}")

    def format_days_left(days):
        if pd.isna(days):
            return "—"
        days = int(days)
        if days < 0:
            return "עבר המועד"
        if days == 0:
            return "היום<br>האחרון"
        return f"{days:,}"

    def iso_or_sentinel(dt):
        return dt.strftime("%Y-%m-%d") if pd.notna(dt) else "9999-12-31"

    def plan_icon_link(plan_url, plan_number, reason):
        # מידע תכנוני (the plan link) only makes sense when the request
        # itself is for בנייה/פיתוח - a safety/disease/infrastructure
        # felling reason has no planning angle worth surfacing here.
        if pd.isna(plan_url) or not is_construction_reason(reason):
            return ""
        title = f' title="תוכנית {plan_number}"' if pd.notna(plan_number) else ' title="קישור למנהל התכנון"'
        return f' <a href="{esc(plan_url)}" target="_blank" rel="noopener" class="map-icon"{title}>📋</a>'

    def maps_link(row, license_id):
        street = str(row.street).strip() if pd.notna(row.street) else ""
        city = row.city
        display_address = street if street else city
        full_address = f"{street}, {city}" if street else city
        query = quote_plus(f"{full_address}, ישראל")
        google_url = f"https://www.google.com/maps/search/?api=1&query={query}"
        icons = (
            f'<a href="{google_url}" target="_blank" rel="noopener" '
            f'class="map-icon" title="פתח ב-Google Maps">🗺️</a>'
        )
        if pd.notna(row.govmap_url):
            icons += (
                f' <a href="{esc(row.govmap_url)}" target="_blank" rel="noopener" '
                f'class="map-icon" title="פתח ב-GovMap (תצלום אוויר)">🛰️</a>'
            )
        icons += plan_icon_link(row.plan_url, row.plan_number, row.reason)
        share_icons = f'{whatsapp_share_link(license_id, row)} {share_link(license_id)}'
        city_span = f' <span class="city-inline">({esc(city)})</span>' if street else ""
        addr_break = '<br class="addr-break">' if street else "<br>"
        return (
            f'{esc(display_address)}{city_span}{addr_break}{icons}'
            f'<br>{share_icons}<br>{ai_icon_link(license_id, row)}'
        )

    config = configparser.ConfigParser()
    config.read(CONFIG_PATH, encoding="utf-8")
    prompt_lines = [line.strip() for line in config["objection_help"]["prompt"].splitlines() if line.strip()]
    default_prompt_text = "\n".join(prompt_lines)

    # Per-row search terms travel to the browser as a data-terms attribute
    # rather than being baked into a fixed href, so the AI-prompt field at
    # the top of the page can be edited live and every row's link picks up
    # the edit on click (see openObjectionSearch in the page script).
    def data_terms(license_id, row):
        street = str(row.street).strip() if pd.notna(row.street) else ""
        gush_helka = f"גוש {row.gush} חלקה {row.helka}" if pd.notna(row.gush) and pd.notna(row.helka) else ""
        data_lines = [
            f"רישיון כריתה מספר {int(license_id)}",
            row.city if pd.notna(row.city) else "",
            street,
            gush_helka,
            row.species if pd.notna(row.species) else "",
            row.applicant if pd.notna(row.applicant) else "",
            row.reason if pd.notna(row.reason) else "",
        ]
        clean_terms = [str(t).strip() for t in data_lines if str(t).strip()]
        return html.escape(json.dumps(clean_terms, ensure_ascii=False), quote=True)

    def ai_icon_link(license_id, row):
        return (
            f'<a href="#" class="ai-prompt-link share-icon" data-terms="{data_terms(license_id, row)}" '
            f'title="עזרה בהגשת השגה (AI)" onclick="return openObjectionSearch(this)">🤖</a>'
        )

    def deadline_bg(days_left, trees_to_cut):
        has_deadline = pd.notna(days_left)
        many_trees = pd.notna(trees_to_cut) and trees_to_cut > 3
        if not has_deadline and not many_trees:
            return None
        if has_deadline:
            days_left = max(0, int(days_left))
            max_days = 30
            t = min(days_left, max_days) / max_days
            grey = (225, 225, 225)
            white = (255, 255, 255)
            r = grey[0] + (white[0] - grey[0]) * t
            g = grey[1] + (white[1] - grey[1]) * t
            b = grey[2] + (white[2] - grey[2]) * t
        else:
            r, g, b = 255, 255, 255
        if many_trees:
            max_trees = 20
            brown = (181, 136, 99)
            extra = min(trees_to_cut - 3, max_trees - 3) / (max_trees - 3) * 0.6
            r += (brown[0] - r) * extra
            g += (brown[1] - g) * extra
            b += (brown[2] - b) * extra
        return f"rgb({round(r)},{round(g)},{round(b)})"

    def trees_font_scale(trees_to_cut):
        if pd.isna(trees_to_cut) or trees_to_cut <= 3:
            return None
        max_trees = 20
        t = min(trees_to_cut - 3, max_trees - 3) / (max_trees - 3)
        return round(1 + t, 2)

    def share_link(license_id):
        return (
            f'<a href="#" class="share-icon" title="העתקת קישור לרישיון זה" '
            f'onclick="return copyShareLink({int(license_id)})">🔗</a>'
        )

    def whatsapp_lines(license_id, row):
        street = str(row.street).strip() if pd.notna(row.street) else ""
        lines = [f"רישיון כריתה מספר {int(license_id)}", f"ישוב: {row.city}"]
        if street:
            lines.append(f"כתובת: {street}")
        if pd.notna(row.gush) and pd.notna(row.helka):
            lines.append(f"גוש/חלקה: {row.gush}/{row.helka}")
        if row.reason:
            lines.append(f"סיבת בקשה: {row.reason}")
        if row.species:
            lines.append(f"מיני עצים: {row.species}")
        lines.append(f"עצים לכריתה: {int(row.trees_to_cut)}")
        if row.applicant:
            lines.append(f"מבקש: {row.applicant}")
        if row.deadline:
            lines.append(f"מועד אחרון להשגה: {row.deadline}")
        return lines

    def whatsapp_share_link(license_id, row):
        lines_json = html.escape(json.dumps(whatsapp_lines(license_id, row), ensure_ascii=False), quote=True)
        return (
            f'<a href="#" class="share-icon" title="שיתוף בוואטסאפ" data-wa-lines="{lines_json}" '
            f'onclick="return shareToWhatsapp(this, {int(license_id)})">💬</a>'
        )

    def build_row(license_id, row):
        bg = deadline_bg(row.days_left, row.trees_to_cut)
        font_scale = trees_font_scale(row.trees_to_cut)
        style_parts = []
        if bg:
            style_parts.append(f"background-color:{bg}")
        if font_scale:
            style_parts.append(f"font-size:{font_scale}em")
        row_style = f' style="{";".join(style_parts)}"' if style_parts else ""
        cell_style = row_style
        font_style = f' style="font-size:{font_scale}em"' if font_scale else ""
        return (
            f'<tr id="license-{int(license_id)}"{row_style}>'
            f'<td class="frozen-col frozen-col-1"{cell_style}>{esc(row.city)}</td>'
            f'<td class="frozen-col frozen-col-2"{cell_style}>{maps_link(row, license_id)}</td>'
            f'<td class="trees-col"{font_style}>{int(row.trees_to_cut):,}</td>'
            f"<td{font_style}>{format_days_left(row.days_left)}</td>"
            f"<td{font_style}>{esc(row.species)}</td>"
            f"<td{font_style}>{esc(row.reason) if row.reason else '—'}</td>"
            f'<td class="gush-helka-col"{font_style}>{format_gush_helka(row.gush, row.helka)}</td>'
            f"<td{font_style}>{int(license_id):,}</td>"
            f"<td{font_style}>{esc(row.applicant)}</td>"
            f"<td data-sort=\"{iso_or_sentinel(row.deadline_dt)}\"{font_style}>{esc(row.deadline)}</td></tr>"
        )

    rows = "".join(build_row(license_id, row) for license_id, row in licenses.iterrows())

    city_options = "".join(
        f'<option value="{esc(city)}">{esc(city)}</option>'
        for city in sorted(licenses["city"].dropna().unique(), key=str)
    )

    n_open = len(licenses)
    n_cities = licenses["city"].nunique()
    n_trees = int(licenses["trees_to_cut"].sum())

    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>דוח רישיונות פתוחים להגשת השגה ({latest_date})</title>
{FONT_LINKS}
<style>
{BASE_CSS}
    .container {{ max-width: 1600px; }}
    .cards {{ grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }}
    .card {{ border-top-color: var(--terracotta); }}
    thead th {{ position: sticky; top: 0; z-index: 15; box-shadow: 0 2px 2px -1px var(--shadow-soft); }}
    .ai-prompt-field {{ width: 100%; box-sizing: border-box; font-family: inherit; font-size: 14px; padding: 10px 12px; border: 2px solid var(--border); border-radius: 10px; resize: vertical; margin-bottom: 10px; }}
    /* Frozen ישוב/כתובת columns: pinned to the physical right edge (where
       they render first in RTL) so they stay visible while scrolling
       through the rest of this wide table horizontally. */
    .frozen-col {{ position: sticky; background-color: var(--card); }}
    .frozen-col-1 {{ right: 0; width: 100px; min-width: 100px; max-width: 100px; }}
    .frozen-col-2 {{ right: 100px; width: 13ch; min-width: 13ch; max-width: 13ch; white-space: normal; overflow-wrap: anywhere; word-break: break-word; box-shadow: -2px 0 2px -1px var(--shadow-soft); }}
    .city-inline {{ display: none; }}
    @media (min-width: 900px) {{
        .table-scroll {{ max-height: 85vh; }}
        .table-panel {{ position: sticky; top: 0; z-index: 25; background-color: var(--card); }}
    }}
    @media (max-width: 640px) {{
        .frozen-col-1 {{ display: none; }}
        .frozen-col-2 {{ right: 0; width: 10ch; min-width: 10ch; max-width: 10ch; }}
        .city-inline {{ display: block; }}
        .addr-break {{ display: none; }}
        #cityTable th:not(.frozen-col), #cityTable td:not(.frozen-col) {{ width: 8ch; min-width: 8ch; max-width: 8ch; white-space: normal; overflow-wrap: anywhere; word-break: break-word; }}
    }}
    thead .frozen-col {{ z-index: 16; }}
    tbody .frozen-col {{ z-index: 5; }}
    tr:hover .frozen-col {{ background-color: #f7fbf4; }}
    .page-created {{ color: var(--border); font-size: 10px; }}
    .gush-helka-col {{ width: 70px; min-width: 70px; max-width: 70px; text-align: center; white-space: normal; overflow-wrap: anywhere; word-break: break-word; }}
    .trees-col {{ text-align: center; }}
    #cityTable td {{ border-bottom-color: #aaa; }}
    #cityTable th {{ text-align: center; }}
    .share-icon {{ text-decoration: none; cursor: pointer; }}
    .collapsible summary {{ cursor: pointer; }}
    .collapsible summary h2 {{ display: inline; }}
    tr.link-copied {{ background-color: #fff3cd; transition: background-color 0.3s; }}
    tr.link-copied .frozen-col {{ background-color: #fff3cd; }}
    tr.shared-target {{ outline: 3px solid #ff9800; outline-offset: -2px; }}
    tr.shared-target td {{ background-color: #fff3cd !important; }}
    @media print {{
        .toolbar {{ display: none !important; }}
        thead th {{ position: static; box-shadow: none; }}
        .frozen-col {{ position: static; box-shadow: none; }}
    }}
</style>
</head>
<body>
<div class="container">
    <div class="portal-bar"><a href="https://agmonr.github.io/govapiportal/" target="_blank" rel="noopener">🏛️ חלק מ-GovAPIPortal</a></div>
    <header>
        <h1>🌳 רישיונות פתוחים להגשת השגה</h1>
        <p class="subtitle">פרויקט של רם אגמון, הוד השרון, עבור נאמני העצים, הצטרפו לנאמני העצים</p>
        <p class="subtitle">האתר בהרצה, עלולות להיות טעויות</p>
        <p>נתונים נכון לתאריך {latest_date} &middot; {build_nav_links(latest_date)}</p>
        <p class="page-created">דף נוצר ב-{datetime.now(timezone.utc).astimezone().strftime('%d/%m/%Y %H:%M')}</p>
    </header>

    <div class="panel explain">
        <details class="collapsible">
            <summary><h2>מה מציג הדוח</h2></summary>
            <p>הדוח הזה מציג כל רישיון שנמצא כרגע בסטטוס "{OPEN_STATUS}", כלומר עדיין ניתן להגיש עליו השגה. המיון המחדל הוא לפי מספר הימים שנותרו עד למועד האחרון להגשת השגה, מהקרוב ביותר לרחוק ביותר, כך שהיישובים והעצים הדחופים ביותר מופיעים ראשונים.</p>
            <p>לתשומת לב: הנתונים הגולמיים של מערכת יעל"ה אינם כוללים שדה שמציין אם כבר הוגשה השגה על רישיון מסוים. הסטטוס היחיד הרלוונטי הוא סטטוס הרישיון עצמו - וכל הרישיונות בדוח זה נמצאים בסטטוס "{OPEN_STATUS}" בדיוק משום שטרם הוגשה עליהם השגה שהמערכת כבר עיבדה (ברגע שהשגה מתקבלת לטיפול, הרישיון עובר לסטטוס "בתהליך בחינת השגות שהוגשו" ויוצא מהדוח הזה). כלומר, מבחינת הנתונים הרשמיים - כל רישיון המופיע כאן עדיין ללא השגה שטופלה. אם השגה כבר הוגשה על ידי מישהו אך טרם עובדה במערכת, אין כרגע דרך לדעת זאת מתוך הנתונים הגלויים לציבור.</p>
        </details>

        <details class="collapsible">
            <summary><h2>הנחיה ל-AI לעזרה בכתיבת השגה</h2></summary>
            <p class="note">זו ההנחיה שנשלחת יחד עם פרטי הרישיון כשלוחצים על מספר הרישיון או על סיבת הבקשה בטבלה למטה. אפשר לערוך אותה כאן - השינוי ישפיע מיד על כל הקישורים בטבלה, כל עוד הדף פתוח (העריכה לא נשמרת אחרי רענון הדף).</p>
            <textarea id="aiPromptField" class="ai-prompt-field" dir="rtl" rows="4">{esc(default_prompt_text)}</textarea>
            <button class="export-btn" onclick="resetAiPrompt()">איפוס לברירת מחדל</button>
        </details>
    </div>

    <div class="panel explain">
        <div class="explain-header">
            <h2>איך משתמשים בקישורים בטבלה</h2>
        </div>
        <p>בשורת ה<strong>כתובת</strong> שבטבלה: 🗺️ פותח את המיקום ב-Google Maps, 🛰️ פותח תצלום אוויר ב-GovMap, 📋 פותח את התוכנית ב-מנהל התכנון (כשקיימת), 🔗 מעתיק קישור ישיר לרישיון הזה, 💬 משתף אותו בוואטסאפ, ו-🤖 פותח חיפוש בגוגל כולל ההנחיה שלמעלה לעזרה בהגשת השגה. אם אתם מחוברים לחשבון גוגל, לחצו על הלשונית "AI" בתוצאות החיפוש ופעלו לפי ההנחיות שם.</p>
    </div>

    <div class="cards">
        <div class="card"><div class="card-val">{n_open:,}</div><div class="card-lbl">רישיונות פתוחים להשגה</div></div>
        <div class="card"><div class="card-val">{n_cities:,}</div><div class="card-lbl">יישובים</div></div>
        <div class="card"><div class="card-val">{n_trees:,}</div><div class="card-lbl">עצים בסכנת כריתה</div></div>
    </div>

    <div class="panel table-panel">
        <div class="toolbar">
            <input type="text" id="citySearch" placeholder="חיפוש לפי יישוב, סיבת בקשה, מין עץ או מבקש..." oninput="filterCities()">
            <select id="cityFilter" onchange="document.getElementById('citySearch').value = this.value; filterCities();">
                <option value="">כל היישובים</option>
                {city_options}
            </select>
            <span id="cityCount"></span>
            <div class="toolbar-actions">
                <button class="export-btn" onclick="downloadCSV('cityTable', 'open_for_objection_{latest_date}.csv')">הורדה כ-CSV</button>
                <button class="export-btn" onclick="downloadExcel('cityTable', 'open_for_objection_{latest_date}.xls')">הורדה כ-Excel</button>
            </div>
        </div>
        <div class="table-scroll">
        <table id="cityTable" data-sort-col="3" data-sort-dir="asc">
            <thead>
                <tr>
                    <th data-col="0" class="frozen-col frozen-col-1" onclick="sortCities(0, 'string')">ישוב</th>
                    <th data-col="1" class="frozen-col frozen-col-2" onclick="sortCities(1, 'string')">כתובת</th>
                    <th data-col="2" class="trees-col" onclick="sortCities(2, 'number')">עצים<br>לכריתה</th>
                    <th data-col="3" class="sort-asc" onclick="sortCities(3, 'number')">ימים<br>שנותרו</th>
                    <th data-col="4" onclick="sortCities(4, 'string')">מיני עצים</th>
                    <th data-col="5" onclick="sortCities(5, 'string')">סיבת בקשה</th>
                    <th data-col="6" class="gush-helka-col" onclick="sortCities(6, 'string')">גוש/<br>חלקה</th>
                    <th data-col="7" onclick="sortCities(7, 'number')">מספר רישיון</th>
                    <th data-col="8" onclick="sortCities(8, 'string')">מבקש</th>
                    <th data-col="9" onclick="sortCities(9, 'string')">מועד<br>אחרון<br>להשגה</th>
                </tr>
            </thead>
            <tbody id="cityBody">{rows}</tbody>
        </table>
        </div>
    </div>

    <footer>
        נוצר אוטומטית ב-{datetime.now(timezone.utc).astimezone().strftime('%d/%m/%Y %H:%M')}.<br>
        נוצר על ידי רם אגמון, הוד השרון.
    </footer>
</div>
<script>
{SCROLL_HINT_SCRIPT}
{EXPORT_SCRIPT}
const DEFAULT_AI_PROMPT = {json.dumps(default_prompt_text, ensure_ascii=False)};

function resetAiPrompt() {{
    document.getElementById('aiPromptField').value = DEFAULT_AI_PROMPT;
}}

function copyShareLink(id) {{
    const url = window.location.href.split('#')[0] + '#license-' + id;
    navigator.clipboard.writeText(url).then(() => {{
        highlightLicenseRow(id);
    }});
    return false;
}}

function shareToWhatsapp(link, id) {{
    const url = window.location.href.split('#')[0] + '#license-' + id;
    const lines = JSON.parse(link.dataset.waLines);
    lines.push('קישור: ' + url);
    window.open('https://wa.me/?text=' + encodeURIComponent(lines.join('\\n')), '_blank', 'noopener');
    return false;
}}

function highlightLicenseRow(id) {{
    const row = document.getElementById('license-' + id);
    if (!row) return;
    row.classList.add('link-copied');
    setTimeout(() => row.classList.remove('link-copied'), 1500);
}}

function openObjectionSearch(link) {{
    const promptLines = document.getElementById('aiPromptField').value
        .split('\\n').map(s => s.trim()).filter(Boolean);
    const rowTerms = JSON.parse(link.dataset.terms);
    const query = encodeURIComponent(promptLines.concat(rowTerms).join(' ')).replace(/%20/g, '+');
    window.open('https://www.google.com/search?q=' + query, '_blank', 'noopener');
    return false;
}}

function sortCities(col, type) {{
    const table = document.getElementById('cityTable');
    const tbody = document.getElementById('cityBody');
    const rows = Array.from(tbody.querySelectorAll('tr'));
    const dir = (table.dataset.sortCol == col && table.dataset.sortDir === 'asc') ? 'desc' : 'asc';

    const getVal = (tr) => {{
        const td = tr.children[col];
        return td.dataset.sort !== undefined ? td.dataset.sort : td.textContent.trim();
    }};

    rows.sort((a, b) => {{
        let va = getVal(a);
        let vb = getVal(b);
        if (type === 'number') {{
            va = parseFloat(String(va).replace(/,/g, '')) || 0;
            vb = parseFloat(String(vb).replace(/,/g, '')) || 0;
            return dir === 'asc' ? va - vb : vb - va;
        }}
        return dir === 'asc' ? String(va).localeCompare(String(vb), 'he') : String(vb).localeCompare(String(va), 'he');
    }});
    rows.forEach(r => tbody.appendChild(r));

    table.dataset.sortCol = col;
    table.dataset.sortDir = dir;
    document.querySelectorAll('#cityTable th').forEach(th => th.classList.remove('sort-asc', 'sort-desc'));
    document.querySelector(`#cityTable th[data-col="${{col}}"]`).classList.add(dir === 'asc' ? 'sort-asc' : 'sort-desc');
}}

function filterCities() {{
    const q = document.getElementById('citySearch').value.trim();
    const rows = document.querySelectorAll('#cityBody tr');
    let shown = 0;
    rows.forEach(r => {{
        const match = [0, 4, 5, 8].some(i => r.children[i].textContent.includes(q));
        r.style.display = match ? '' : 'none';
        if (match) shown++;
    }});
    document.getElementById('cityCount').textContent = `מציג ${{shown}} מתוך ${{rows.length}} רישיונות`;
}}
filterCities();

if (location.hash.startsWith('#license-')) {{
    const row = document.querySelector(location.hash);
    if (row) {{
        row.scrollIntoView({{behavior: 'smooth', block: 'center'}});
        row.classList.add('shared-target');
    }}
}}
</script>
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
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ארכיון דוחות שבועיים - רישיונות כריתה</title>
{FONT_LINKS}
<style>
{BASE_CSS}
    .container {{ max-width: 640px; }}
    .nav-list {{ display: flex; flex-direction: column; gap: 10px; margin: 0; padding: 0; list-style: none; }}
    .nav-list a {{ display: block; background: #eaf3e6; color: var(--forest-dark); text-decoration: none; font-weight: 500; font-size: 16px; padding: 14px 18px; border-radius: 12px; }}
    .nav-list a:hover {{ background: var(--leaf-light); }}
    .archive-list {{ line-height: 2.2; padding-right: 20px; margin: 0; }}
</style>
</head>
<body>
<div class="container">
    <div class="portal-bar"><a href="https://agmonr.github.io/govapiportal/" target="_blank" rel="noopener">🏛️ חלק מ-GovAPIPortal</a></div>
    <header>
        <h1>🌳 ארכיון דוחות שבועיים</h1>
        <p class="subtitle">רישיונות כריתה והעתקה &middot; פרויקט של רם אגמון, הוד השרון, עבור נאמני העצים</p>
        <p class="subtitle">האתר בהרצה, עלולות להיות טעויות</p>
    </header>

    <div class="panel">
        <ul class="nav-list">
            <li><a href="open_for_objection.html">🍃 רישיונות פתוחים להגשת השגה (מיון וסינון)</a></li>
            <li><a href="current.html">📋 הדוח האחרון</a></li>
            <li><a href="objections.html">✅ דו"ח היענות הרשות</a></li>
            <li><a href="by_city.html">🏘️ דוח לפי יישוב (מיון וסינון)</a></li>
            <li><a href="orphaned_cities.html">🌱 יישובים "יתומים" - ללא השגה שהצליחה</a></li>
            <li><a href="llms.txt">📄 רשימת קישורים לכל הדוחות (טקסט פשוט)</a></li>
        </ul>
    </div>

    <div class="panel">
        <h2>דוחות היסטוריים לפי תאריך</h2>
        <ul class="archive-list">{rows}</ul>
    </div>

    <footer>
        נוצר אוטומטית ב-{datetime.now(timezone.utc).astimezone().strftime('%d/%m/%Y %H:%M')}.<br>
        נוצר על ידי רם אגמון, הוד השרון.
    </footer>
</div>
</body>
</html>
"""


def build_ai_index(snapshots):
    lines = [
        f"# נוצר אוטומטית ב-{datetime.now(timezone.utc).astimezone().strftime('%d/%m/%Y %H:%M')}",
        f"{BASE_URL}index.html",
        f"{BASE_URL}open_for_objection.html",
        f"{BASE_URL}by_city.html",
        f"{BASE_URL}objections.html",
        f"{BASE_URL}orphaned_cities.html",
    ]
    lines += [f"{BASE_URL}report_{date_str}.html" for date_str in sorted(snapshots)]
    return "\n".join(lines) + "\n"


def build_current_redirect(latest_date):
    target = f"report_{latest_date}.html"
    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="0; url={target}">
<link rel="canonical" href="{target}">
<title>הדוח האחרון - רישיונות כריתה</title>
</head>
<body>
<p>מעביר לדוח האחרון: <a href="{target}">{target}</a></p>
<p>נוצר אוטומטית ב-{datetime.now(timezone.utc).astimezone().strftime('%d/%m/%Y %H:%M')}.</p>
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
    latest_df = pd.read_csv(snapshots[latest_date])
    # The index links to every date in the trend cache, so every dated
    # snapshot needs its own report file or those links 404 - but only the
    # latest one actually changes day to day, so only it (plus any date
    # that's never been reported at all, e.g. a backfilled snapshot) gets
    # rebuilt. Historical report_<date>.html pages are left as-is; if the
    # template/style changes, those pages simply won't reflect it until
    # something else regenerates them.
    for date_str, path in snapshots.items():
        report_path = REPORTS_DIR / f"report_{date_str}.html"
        if date_str != latest_date and report_path.exists():
            continue
        df = latest_df if date_str == latest_date else pd.read_csv(path)
        report_html = build_report(date_str, df, trend.loc[:date_str])
        report_path.write_text(report_html, encoding="utf-8")
        print(f"Report written to {report_path}")

    index_path = REPORTS_DIR / "index.html"
    index_path.write_text(build_index(trend), encoding="utf-8")
    print(f"Index written to {index_path}")

    current_path = REPORTS_DIR / "current.html"
    current_path.write_text(build_current_redirect(latest_date), encoding="utf-8")
    print(f"Current-report redirect written to {current_path}")

    city_report_path = REPORTS_DIR / "by_city.html"
    city_report_path.write_text(build_city_report(latest_date, latest_df), encoding="utf-8")
    print(f"City report written to {city_report_path}")

    objections_report_path = REPORTS_DIR / "objections.html"
    objections_report_path.write_text(build_objections_report(latest_date, latest_df), encoding="utf-8")
    print(f"Objections report written to {objections_report_path}")

    open_objections_report_path = REPORTS_DIR / "open_for_objection.html"
    open_objections_report_path.write_text(build_open_objections_report(latest_date, latest_df), encoding="utf-8")
    print(f"Open-for-objection report written to {open_objections_report_path}")

    orphaned_cities_report_path = REPORTS_DIR / "orphaned_cities.html"
    orphaned_cities_report_path.write_text(build_orphaned_cities_report(latest_date, latest_df), encoding="utf-8")
    print(f"Orphaned-cities report written to {orphaned_cities_report_path}")

    ai_index_path = REPORTS_DIR / "llms.txt"
    ai_index_path.write_text(build_ai_index(snapshots), encoding="utf-8")
    print(f"AI index written to {ai_index_path}")


if __name__ == "__main__":
    main()
