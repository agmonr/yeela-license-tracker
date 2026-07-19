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

COLORS = ["#2ecc71", "#e74c3c", "#3498db", "#f1c40f", "#9b59b6", "#1abc9c"]

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
    fig, ax = plt.subplots(figsize=(6, max(4.5, 0.35 * len(series))))
    ordered = series.sort_values(ascending=True)
    ax.barh(ordered.index, ordered.values, color=color)
    ax.set_title(title)
    style_axes(ax)
    ax.grid(axis="x", color="#ecf0f1", linewidth=1)
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
        f'<a href="report_{latest_date}.html">הדוח המלא</a> &middot; '
        f'<a href="objections.html">דו"ח היענות הרשות</a> &middot; '
        f'<a href="by_city.html">דוח לפי יישוב</a> &middot; '
        f'<a href="open_for_objection.html">פתוחים להגשת השגה</a> &middot; '
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

    species_chart = make_barh_chart(top_species, "20 מיני העצים המובילים בכריתה", "#e74c3c")
    cities_chart = make_barh_chart(top_cities, "20 היישובים המובילים בכריתה", "#3498db")
    status_chart = make_barh_chart(status_counts, "התפלגות סטטוס רישיונות", "#9b59b6")

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
            <table>
                <thead><tr><th>תאריך</th><th>סה"כ רישיונות</th><th>שינוי רישיונות</th><th>סה"כ עצים לכריתה</th><th>שינוי עצים לכריתה</th></tr></thead>
                <tbody>{build_trend_rows(ptrend)}</tbody>
            </table>
        </div>"""

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
    .subtitle {{ margin: 6px 0 0; font-size: 13px; color: #bdc3c7; }}
    h2 {{ color: #2c3e50; border-bottom: 2px solid #bdc3c7; padding-bottom: 8px; }}
    .tabs {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0 15px; }}
    .tab-btn {{ background: #ecf0f1; border: none; border-radius: 20px; padding: 8px 16px; font-size: 13px; color: #2c3e50; cursor: pointer; }}
    .tab-btn:hover {{ background: #dfe6e9; }}
    .tab-btn.active {{ background: #2c3e50; color: #fff; }}
    .tab-panel {{ display: none; }}
    .tab-panel.active {{ display: block; }}
    .period-note {{ color: #7f8c8d; font-size: 13px; margin: 0 0 10px; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 30px; }}
    .card {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); text-align: center; border-top: 4px solid #2ecc71; }}
    .card.cut {{ border-top-color: #e74c3c; }}
    .card.move {{ border-top-color: #3498db; }}
    .card.keep {{ border-top-color: #f1c40f; }}
    .card.meta {{ border-top-color: #9b59b6; }}
    .card.canceled {{ border-top-color: #d35400; }}
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
    .print-btn {{ background: #27ae60; color: #fff; border: none; border-radius: 6px; padding: 8px 16px; font-size: 13px; cursor: pointer; margin-top: 10px; }}
    .print-btn:hover {{ background: #219150; }}
    @media print {{
        .print-btn, .tabs {{ display: none !important; }}
        body {{ background: #fff; padding: 0; }}
        .card, .panel {{ box-shadow: none; }}
    }}
</style>
</head>
<body>
<div class="container">
    <header>
        <h1>דוח מגמות וסטטיסטיקה: רישיונות כריתה והעתקה</h1>
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
        נוצר אוטומטית ב-{datetime.now(timezone.utc).astimezone().strftime('%d/%m/%Y %H:%M')} מתוך {len(trend)} תמונות ארכיון היסטוריות.<br>
        נוצר על ידי רם אגמון, הוד השרון.
    </footer>
</div>
<script>
function showTrendTab(key) {{
    document.querySelectorAll('.tab-panel').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
    document.getElementById('tab-' + key).classList.add('active');
    document.getElementById('btn-' + key).classList.add('active');
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
<style>
    body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f6f9; color: #333; margin: 0; padding: 20px; }}
    .container {{ max-width: 900px; margin: 0 auto; }}
    header {{ background-color: #2c3e50; color: #fff; padding: 20px; border-radius: 8px; margin-bottom: 25px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); position: sticky; top: 0; z-index: 20; }}
    header a {{ color: #ecf0f1; }}
    h1 {{ margin: 0; font-size: 24px; }}
    .subtitle {{ margin: 6px 0 0; font-size: 13px; color: #bdc3c7; }}
    .panel {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); margin-bottom: 30px; }}
    .toolbar {{ display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 12px; }}
    #citySearch {{ padding: 8px 12px; border: 1px solid #dfe6e9; border-radius: 6px; font-size: 14px; width: 260px; max-width: 100%; }}
    #cityCount {{ color: #7f8c8d; font-size: 13px; }}
    .toolbar-actions {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    .export-btn {{ background: #3498db; color: #fff; border: none; border-radius: 6px; padding: 8px 14px; font-size: 13px; cursor: pointer; }}
    .export-btn:hover {{ background: #2980b9; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 10px 12px; text-align: right; border-bottom: 1px solid #ecf0f1; font-size: 14px; }}
    th {{ background-color: #f8f9fa; color: #2c3e50; cursor: pointer; user-select: none; white-space: nowrap; }}
    thead th {{ position: sticky; top: var(--header-h, 0px); z-index: 15; box-shadow: 0 2px 2px -1px rgba(0,0,0,0.1); }}
    th.sort-asc::after {{ content: " \\25B2"; font-size: 10px; }}
    th.sort-desc::after {{ content: " \\25BC"; font-size: 10px; }}
    tr:hover {{ background-color: #fcfcfc; }}
    footer {{ text-align: center; color: #95a5a6; font-size: 12px; margin: 30px 0 10px; }}
    .print-btn {{ background: #27ae60; color: #fff; border: none; border-radius: 6px; padding: 8px 16px; font-size: 13px; cursor: pointer; margin-top: 10px; }}
    .print-btn:hover {{ background: #219150; }}
    @media print {{
        .print-btn, .toolbar {{ display: none !important; }}
        body {{ background: #fff; padding: 0; }}
        header {{ position: static; box-shadow: none; }}
        .panel {{ box-shadow: none; }}
        thead th {{ position: static; box-shadow: none; }}
    }}
</style>
</head>
<body>
<div class="container">
    <header>
        <h1>דוח לפי יישוב: רישיונות כריתה והעתקה</h1>
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

    <footer>
        נוצר אוטומטית ב-{datetime.now(timezone.utc).astimezone().strftime('%d/%m/%Y %H:%M')}.<br>
        נוצר על ידי רם אגמון, הוד השרון.
    </footer>
</div>
<script>
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

function updateStickyOffsets() {{
    const headerEl = document.querySelector('header');
    document.documentElement.style.setProperty('--header-h', headerEl.getBoundingClientRect().height + 'px');
}}
updateStickyOffsets();
window.addEventListener('resize', updateStickyOffsets);
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
<style>
    body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f6f9; color: #333; margin: 0; padding: 20px; }}
    .container {{ max-width: 900px; margin: 0 auto; }}
    header {{ background-color: #2c3e50; color: #fff; padding: 20px; border-radius: 8px; margin-bottom: 25px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); position: sticky; top: 0; z-index: 20; }}
    header a {{ color: #ecf0f1; }}
    h1 {{ margin: 0; font-size: 24px; }}
    .subtitle {{ margin: 6px 0 0; font-size: 13px; color: #bdc3c7; }}
    .panel {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); margin-bottom: 30px; }}
    .panel.explain h2 {{ color: #2c3e50; margin-top: 0; font-size: 18px; }}
    .panel.explain ul {{ margin: 10px 0; padding-right: 20px; }}
    .panel.explain li {{ margin-bottom: 8px; }}
    .note {{ color: #7f8c8d; font-size: 13px; margin: 0 0 15px; }}
    .toolbar {{ display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 12px; }}
    #citySearch {{ padding: 8px 12px; border: 1px solid #dfe6e9; border-radius: 6px; font-size: 14px; width: 260px; max-width: 100%; }}
    #cityCount {{ color: #7f8c8d; font-size: 13px; }}
    .toolbar-actions {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    .export-btn {{ background: #3498db; color: #fff; border: none; border-radius: 6px; padding: 8px 14px; font-size: 13px; cursor: pointer; }}
    .export-btn:hover {{ background: #2980b9; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 10px 12px; text-align: right; border-bottom: 1px solid #ecf0f1; font-size: 14px; }}
    th {{ background-color: #f8f9fa; color: #2c3e50; cursor: pointer; user-select: none; white-space: nowrap; }}
    thead th {{ position: sticky; top: var(--header-h, 0px); z-index: 15; box-shadow: 0 2px 2px -1px rgba(0,0,0,0.1); }}
    th.sort-asc::after {{ content: " \\25B2"; font-size: 10px; }}
    th.sort-desc::after {{ content: " \\25BC"; font-size: 10px; }}
    tr:hover {{ background-color: #fcfcfc; }}
    footer {{ text-align: center; color: #95a5a6; font-size: 12px; margin: 30px 0 10px; }}
    .print-btn {{ background: #27ae60; color: #fff; border: none; border-radius: 6px; padding: 8px 16px; font-size: 13px; cursor: pointer; margin-top: 10px; }}
    .print-btn:hover {{ background: #219150; }}
    @media print {{
        .print-btn, .toolbar {{ display: none !important; }}
        body {{ background: #fff; padding: 0; }}
        header {{ position: static; box-shadow: none; }}
        .panel {{ box-shadow: none; }}
        thead th {{ position: static; box-shadow: none; }}
    }}
</style>
</head>
<body>
<div class="container">
    <header>
        <h1>דו"ח היענות הרשות</h1>
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

    <footer>
        נוצר אוטומטית ב-{datetime.now(timezone.utc).astimezone().strftime('%d/%m/%Y %H:%M')}.<br>
        נוצר על ידי רם אגמון, הוד השרון.
    </footer>
</div>
<script>
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

function updateStickyOffsets() {{
    const headerEl = document.querySelector('header');
    document.documentElement.style.setProperty('--header-h', headerEl.getBoundingClientRect().height + 'px');
}}
updateStickyOffsets();
window.addEventListener('resize', updateStickyOffsets);
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

    def format_days_left(days):
        if pd.isna(days):
            return "—"
        days = int(days)
        if days < 0:
            return "עבר המועד"
        if days == 0:
            return "היום האחרון"
        return f"{days:,}"

    def iso_or_sentinel(dt):
        return dt.strftime("%Y-%m-%d") if pd.notna(dt) else "9999-12-31"

    def maps_link(street, city):
        street = str(street).strip() if pd.notna(street) else ""
        address = f"{street}, {city}" if street else city
        query = quote_plus(f"{address}, ישראל")
        url = f"https://www.google.com/maps/search/?api=1&query={query}"
        return f'<a href="{url}" target="_blank" rel="noopener">{esc(address)}</a>'

    def objection_help_link(license_id, row):
        street = str(row.street).strip() if pd.notna(row.street) else ""
        terms = [
            f"רישיון כריתה מספר {int(license_id)}",
            row.city,
            street,
            row.species,
            row.applicant,
            row.reason,
            "כיצד לכתוב התנגדות לרישיון הכריתה הזה",
            "חפש ממקורות גלויים התנגדויות דומות והשלם בהתאם",
        ]
        clean_terms = [str(t).strip() for t in terms if pd.notna(t) and str(t).strip()]
        query = quote_plus(" ".join(clean_terms))
        url = f"https://www.google.com/search?q={query}"
        return f'<a href="{url}" target="_blank" rel="noopener">{int(license_id):,}</a>'

    rows = "".join(
        f"<tr><td>{esc(row.city)}</td>"
        f"<td>{maps_link(row.street, row.city)}</td>"
        f"<td>{esc(row.reason) if row.reason else '—'}</td>"
        f"<td>{esc(row.species)}</td>"
        f"<td>{int(row.trees_to_cut):,}</td>"
        f"<td>{esc(row.applicant)}</td>"
        f"<td data-sort=\"{iso_or_sentinel(row.deadline_dt)}\">{esc(row.deadline)}</td>"
        f"<td>{format_days_left(row.days_left)}</td>"
        f"<td>{objection_help_link(license_id, row)}</td></tr>"
        for license_id, row in licenses.iterrows()
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
<style>
    body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f6f9; color: #333; margin: 0; padding: 20px; }}
    .container {{ max-width: 1100px; margin: 0 auto; }}
    header {{ background-color: #2c3e50; color: #fff; padding: 20px; border-radius: 8px; margin-bottom: 25px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); position: sticky; top: 0; z-index: 20; }}
    header a {{ color: #ecf0f1; }}
    h1 {{ margin: 0; font-size: 24px; }}
    .subtitle {{ margin: 6px 0 0; font-size: 13px; color: #bdc3c7; }}
    .panel {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); margin-bottom: 30px; }}
    .panel.explain h2 {{ color: #2c3e50; margin-top: 0; font-size: 18px; }}
    .note {{ color: #7f8c8d; font-size: 13px; margin: 0 0 15px; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 20px; margin-bottom: 30px; }}
    .card {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); text-align: center; border-top: 4px solid #e67e22; }}
    .card-val {{ font-size: 26px; font-weight: bold; margin: 10px 0; color: #2c3e50; }}
    .card-lbl {{ font-size: 13px; color: #7f8c8d; }}
    .toolbar {{ display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 12px; }}
    #citySearch {{ padding: 8px 12px; border: 1px solid #dfe6e9; border-radius: 6px; font-size: 14px; width: 260px; max-width: 100%; }}
    #cityCount {{ color: #7f8c8d; font-size: 13px; }}
    .toolbar-actions {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    .export-btn {{ background: #3498db; color: #fff; border: none; border-radius: 6px; padding: 8px 14px; font-size: 13px; cursor: pointer; }}
    .export-btn:hover {{ background: #2980b9; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 10px 12px; text-align: right; border-bottom: 1px solid #ecf0f1; font-size: 14px; }}
    th {{ background-color: #f8f9fa; color: #2c3e50; cursor: pointer; user-select: none; white-space: nowrap; }}
    thead th {{ position: sticky; top: var(--header-h, 0px); z-index: 15; box-shadow: 0 2px 2px -1px rgba(0,0,0,0.1); }}
    th.sort-asc::after {{ content: " \\25B2"; font-size: 10px; }}
    th.sort-desc::after {{ content: " \\25BC"; font-size: 10px; }}
    tr:hover {{ background-color: #fcfcfc; }}
    footer {{ text-align: center; color: #95a5a6; font-size: 12px; margin: 30px 0 10px; }}
    .print-btn {{ background: #27ae60; color: #fff; border: none; border-radius: 6px; padding: 8px 16px; font-size: 13px; cursor: pointer; margin-top: 10px; }}
    .print-btn:hover {{ background: #219150; }}
    @media print {{
        .print-btn, .toolbar {{ display: none !important; }}
        body {{ background: #fff; padding: 0; }}
        header {{ position: static; box-shadow: none; }}
        .panel {{ box-shadow: none; }}
        thead th {{ position: static; box-shadow: none; }}
    }}
</style>
</head>
<body>
<div class="container">
    <header>
        <h1>רישיונות פתוחים להגשת השגה</h1>
        <p class="subtitle">פרויקט של רם אגמון, הוד השרון, עבור נאמני העצים, הצטרפו לנאמני העצים</p>
        <p class="subtitle">האתר בהרצה, עלולות להיות טעויות</p>
        <p>נתונים נכון לתאריך {latest_date} &middot; {build_nav_links(latest_date)}</p>
        <button class="print-btn" onclick="window.print()">ייצוא כ-PDF (הדפסה)</button>
    </header>

    <div class="panel explain">
        <h2>מה מציג הדוח</h2>
        <p>הדוח הזה מציג כל רישיון שנמצא כרגע בסטטוס "{OPEN_STATUS}", כלומר עדיין ניתן להגיש עליו השגה. המיון המחדל הוא לפי מספר הימים שנותרו עד למועד האחרון להגשת השגה, מהקרוב ביותר לרחוק ביותר, כך שהיישובים והעצים הדחופים ביותר מופיעים ראשונים.</p>
        <p>לתשומת לב: הנתונים הגולמיים של מערכת יעל"ה אינם כוללים שדה שמציין אם כבר הוגשה השגה על רישיון מסוים. הסטטוס היחיד הרלוונטי הוא סטטוס הרישיון עצמו - וכל הרישיונות בדוח זה נמצאים בסטטוס "{OPEN_STATUS}" בדיוק משום שטרם הוגשה עליהם השגה שהמערכת כבר עיבדה (ברגע שהשגה מתקבלת לטיפול, הרישיון עובר לסטטוס "בתהליך בחינת השגות שהוגשו" ויוצא מהדוח הזה). כלומר, מבחינת הנתונים הרשמיים - כל רישיון המופיע כאן עדיין ללא השגה שטופלה. אם השגה כבר הוגשה על ידי מישהו אך טרם עובדה במערכת, אין כרגע דרך לדעת זאת מתוך הנתונים הגלויים לציבור.</p>
    </div>

    <div class="cards">
        <div class="card"><div class="card-val">{n_open:,}</div><div class="card-lbl">רישיונות פתוחים להשגה</div></div>
        <div class="card"><div class="card-val">{n_cities:,}</div><div class="card-lbl">יישובים</div></div>
        <div class="card"><div class="card-val">{n_trees:,}</div><div class="card-lbl">עצים בסכנת כריתה</div></div>
    </div>

    <div class="panel">
        <div class="toolbar">
            <input type="text" id="citySearch" placeholder="חיפוש לפי יישוב, סיבת בקשה, מין עץ או מבקש..." oninput="filterCities()">
            <span id="cityCount"></span>
            <div class="toolbar-actions">
                <button class="export-btn" onclick="downloadCSV('cityTable', 'open_for_objection_{latest_date}.csv')">הורדה כ-CSV</button>
                <button class="export-btn" onclick="downloadExcel('cityTable', 'open_for_objection_{latest_date}.xls')">הורדה כ-Excel</button>
            </div>
        </div>
        <table id="cityTable" data-sort-col="7" data-sort-dir="asc">
            <thead>
                <tr>
                    <th data-col="0" onclick="sortCities(0, 'string')">ישוב</th>
                    <th data-col="1" onclick="sortCities(1, 'string')">כתובת</th>
                    <th data-col="2" onclick="sortCities(2, 'string')">סיבת בקשה</th>
                    <th data-col="3" onclick="sortCities(3, 'string')">מיני עצים</th>
                    <th data-col="4" onclick="sortCities(4, 'number')">עצים לכריתה</th>
                    <th data-col="5" onclick="sortCities(5, 'string')">מבקש</th>
                    <th data-col="6" onclick="sortCities(6, 'string')">מועד אחרון להשגה</th>
                    <th data-col="7" class="sort-asc" onclick="sortCities(7, 'number')">ימים שנותרו</th>
                    <th data-col="8" onclick="sortCities(8, 'number')">מספר רישיון</th>
                </tr>
            </thead>
            <tbody id="cityBody">{rows}</tbody>
        </table>
    </div>

    <footer>
        נוצר אוטומטית ב-{datetime.now(timezone.utc).astimezone().strftime('%d/%m/%Y %H:%M')}.<br>
        נוצר על ידי רם אגמון, הוד השרון.
    </footer>
</div>
<script>
{EXPORT_SCRIPT}
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
        const match = [0, 2, 3, 5].some(i => r.children[i].textContent.includes(q));
        r.style.display = match ? '' : 'none';
        if (match) shown++;
    }});
    document.getElementById('cityCount').textContent = `מציג ${{shown}} מתוך ${{rows.length}} רישיונות`;
}}
filterCities();

function updateStickyOffsets() {{
    const headerEl = document.querySelector('header');
    document.documentElement.style.setProperty('--header-h', headerEl.getBoundingClientRect().height + 'px');
}}
updateStickyOffsets();
window.addEventListener('resize', updateStickyOffsets);
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
    <p>האתר בהרצה, עלולות להיות טעויות</p>
    <p><a href="current.html">הדוח האחרון</a></p>
    <p><a href="objections.html">דו"ח היענות הרשות</a></p>
    <p><a href="by_city.html">דוח לפי יישוב (מיון וסינון)</a></p>
    <p><a href="open_for_objection.html">רישיונות פתוחים להגשת השגה (מיון וסינון)</a></p>
    <p><a href="llms.txt">רשימת קישורים לכל הדוחות (טקסט פשוט)</a></p>
    <ul>{rows}</ul>
</div>
</body>
</html>
"""


def build_ai_index(snapshots):
    lines = [
        f"{BASE_URL}index.html",
        f"{BASE_URL}by_city.html",
        f"{BASE_URL}objections.html",
        f"{BASE_URL}open_for_objection.html",
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
    # The index links to every date in the trend cache, so every dated
    # snapshot needs its own report file or those links 404. Rebuilt every
    # run (not just missing/latest) so template/style changes propagate to
    # every historical page instead of only the newest one - these are
    # regenerated views, not literal point-in-time snapshots.
    latest_df = None
    for date_str, path in snapshots.items():
        df = pd.read_csv(path)
        if date_str == latest_date:
            latest_df = df
        report_html = build_report(date_str, df, trend.loc[:date_str])
        report_path = REPORTS_DIR / f"report_{date_str}.html"
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

    ai_index_path = REPORTS_DIR / "llms.txt"
    ai_index_path.write_text(build_ai_index(snapshots), encoding="utf-8")
    print(f"AI index written to {ai_index_path}")


if __name__ == "__main__":
    main()
