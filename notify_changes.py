import configparser
import glob
import html
import os
import re
import pandas as pd
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus
import time
from sheet_subscribers import get_subscribers
from mailer import send_html_mail, render_pdf, EMAIL_STYLE
from config import ADMIN_EMAILS

DATE_RE = re.compile(r"full_licenses_(\d{4}-\d{2}-\d{2})\.csv$")
REPORT_RE = re.compile(r"report_(\d{4}-\d{2}-\d{2})\.html$")
REPORTS_BASE_URL = "https://agmonr.github.io/yeela-license-tracker/statics/reports/"

REPO_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = REPO_ROOT / "config.ini"

# Raw CSV column names, matching statics/generate_dashboard.py.
LICENSE_COL = "מספר רישיון"
CITY_COL = "ישוב"
STREET_COL = "רחוב ומספר בית"
GUSH_COL = "גוש"
HELKA_COL = "חלקה"
SPECIES_COL = "מין העץ"
APPLICANT_COL = "מבקש"
REASON_COL = "סיבת בקשה"
CUT_COL = "סה'כ לכריתה"
DEADLINE_COL = "תאריך אחרון להגשת השגה"
PLAN_URL_COL = "קישור לתכנית"
GOVMAP_URL_COL = "קישור ל-GovMap"
CHANGE_TYPE_COL = "סוג שינוי"

# PDF-card layout: same forest-green theme as EMAIL_STYLE, but stacked
# label/value fields in a card instead of a table row, so ~20 fields per
# license don't force one giant wide row. Cards stack one after another
# (not side by side) on a standard narrow A4 page (see mailer.py's
# render_pdf), with a large font size for on-paper readability.
CARD_STYLE = """
    body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; direction: rtl; text-align: right; background-color: #fff; padding: 12px; color: #24331f; }
    h2 { color: #1b5e34; border-bottom: 3px solid #a5d6a7; padding-bottom: 8px; font-size: 22px; }
    .card { width: 100%; border: 1px solid #dfe9d8; border-radius: 8px; padding: 8px 12px; margin-bottom: 10px; box-sizing: border-box; break-inside: avoid; page-break-inside: avoid; }
    .field { padding: 4px 0; border-bottom: 1px solid #f0f0f0; break-inside: avoid; page-break-inside: avoid; }
    .field:last-child { border-bottom: none; }
    .field-label { display: block; color: #5b6f56; font-weight: 600; font-size: 12px; }
    .field-value { display: block; font-size: 15px; overflow-wrap: anywhere; }
    .field-value a { color: #2ba8e0; }
    .status-new { color: #2e7d46; font-weight: bold; }
    .status-removed { color: #e05353; font-weight: bold; }
"""


def load_ai_prompt_lines():
    """Same objection-help AI prompt used in statics/reports/open_for_objection.html."""
    config = configparser.ConfigParser()
    config.read(CONFIG_PATH, encoding="utf-8")
    return [line.strip() for line in config["objection_help"]["prompt"].splitlines() if line.strip()]


def maps_link(row, label="🗺️ מפה"):
    """Static (non-JS) Google Maps link, for the same address shown on the dashboard."""
    street = str(row.get(STREET_COL, "") or "").strip()
    city = str(row.get(CITY_COL, "") or "").strip()
    address = f"{street}, {city}" if street else city
    if not address:
        return ""
    query = quote_plus(f"{address}, ישראל")
    url = f"https://www.google.com/maps/search/?api=1&query={query}"
    return f'<a href="{url}" target="_blank" rel="noopener">{label}</a>'


def address_cell(row):
    """Visible address text followed by Google Maps + GovMap (aerial photo)
    icon links, for the slim email table. The city name itself links to the
    מנהל התכנון plan (when the row has one), so this one cell replaces the
    separate ישוב/מפה/תכנית columns."""
    street = str(row.get(STREET_COL, "") or "").strip()
    city = str(row.get(CITY_COL, "") or "").strip()
    if not street and not city:
        return ""
    plan_url = str(row.get(PLAN_URL_COL, "") or "").strip()
    city_html = url_link(plan_url, html.escape(city)) if plan_url and city else html.escape(city)
    address_html = f"{html.escape(street)}, {city_html}" if street else city_html
    icons = maps_link(row, label="🗺️")
    govmap_url = str(row.get(GOVMAP_URL_COL, "") or "").strip()
    if govmap_url:
        icons += " " + url_link(govmap_url, "🛰️")
    return f'{address_html} {icons}'


def objection_search_url(row, prompt_lines):
    """Google search URL bundling the AI objection-help prompt with this row's
    details, mirroring the AI-prompt links on open_for_objection.html (which
    build the same query client-side since that page's prompt is editable)."""
    gush = str(row.get(GUSH_COL, "") or "").strip()
    helka = str(row.get(HELKA_COL, "") or "").strip()
    terms = [
        f"רישיון כריתה מספר {row.get(LICENSE_COL, '')}",
        row.get(CITY_COL, ""),
        row.get(STREET_COL, ""),
        f"גוש {gush} חלקה {helka}" if gush and helka else "",
        row.get(SPECIES_COL, ""),
        row.get(APPLICANT_COL, ""),
        row.get(REASON_COL, ""),
    ]
    clean_terms = [str(t).strip() for t in terms if str(t).strip()]
    query = quote_plus(" ".join(prompt_lines + clean_terms))
    return f"https://www.google.com/search?q={query}"


def objection_search_link(row, prompt_lines, label="🔍 עזרה בהגשת השגה"):
    return f'<a href="{objection_search_url(row, prompt_lines)}" target="_blank" rel="noopener">{label}</a>'


def reason_cell(row, prompt_lines):
    """Reason text itself as the AI-objection-help search link, for the slim email table."""
    reason = str(row.get(REASON_COL, "") or "").strip()
    if not reason:
        return "—"
    return objection_search_link(row, prompt_lines, label=html.escape(reason))


def url_link(url, label):
    """Turns a bare URL into a fixed-label link, so raw mavat.iplan.gov.il/
    govmap.gov.il URLs don't force layouts wider than they need to be."""
    url = str(url or "").strip()
    if not url:
        return ""
    return f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">{label}</a>'


def status_cell(row):
    """Colored חדש/עודכן vs הוסר מהמערכת badge (see .status-new/.status-removed
    in EMAIL_STYLE/CARD_STYLE) - without this the slim table has no way to
    tell an added license from a removed one."""
    status = str(row.get(CHANGE_TYPE_COL, "") or "").strip()
    css_class = "status-new" if status == "חדש/עודכן" else "status-removed"
    return f'<span class="{css_class}">{html.escape(status)}</span>' if status else ""


def build_email_table_html(city_diff, prompt_lines):
    """Slim 5-column table for the email body itself: סטטוס, כתובת (city
    name + street, itself carrying the map/GovMap/plan links), סיבת בקשה
    (as the AI-objection-help link), מספר עצים לכריתה, מועד אחרון להשגה.
    The full per-license detail lives in the PDF card attachment instead
    (see build_pdf_cards_document) - the table stays narrow enough to
    render properly in Gmail."""
    rows_html = "".join(
        "<tr>"
        f"<td>{status_cell(row)}</td>"
        f"<td>{address_cell(row)}</td>"
        f"<td>{reason_cell(row, prompt_lines)}</td>"
        f"<td>{html.escape(str(row.get(CUT_COL, '')))}</td>"
        f"<td>{html.escape(str(row.get(DEADLINE_COL, '')))}</td>"
        "</tr>"
        for _, row in city_diff.iterrows()
    )
    return f"""<table class="diff-table">
    <thead><tr><th>סטטוס</th><th>כתובת</th><th>סיבת בקשה</th><th>מספר<br>עצים<br>לכריתה</th><th>מועד<br>אחרון<br>להשגה</th></tr></thead>
    <tbody>{rows_html}</tbody>
</table>"""


def build_card_html(row, prompt_lines):
    """One license's full raw data as a stack of label/value fields (see
    CARD_STYLE), plus the same map/plan/GovMap/objection-help links as the
    dashboard/email - all of it, but vertical instead of one wide row."""
    fields = []
    for col in row.index:
        if col in (PLAN_URL_COL, GOVMAP_URL_COL):
            continue  # relabeled below instead of shown as a raw URL
        value = str(row[col]).strip()
        if value:
            fields.append((col, html.escape(value)))

    plan_url = str(row.get(PLAN_URL_COL, "") or "").strip()
    if plan_url:
        fields.append(("תכנית", url_link(plan_url, "קישור למנהל התכנון")))
    govmap_url = str(row.get(GOVMAP_URL_COL, "") or "").strip()
    if govmap_url:
        fields.append(("תצלום אוויר", url_link(govmap_url, "קישור לתצלום אוויר")))

    map_html = maps_link(row)
    if map_html:
        fields.append(("מפה", map_html))
    fields.append(("עזרה בהגשת השגה", objection_search_link(row, prompt_lines)))

    fields_html = "".join(
        f'<div class="field"><span class="field-label">{html.escape(label)}</span>'
        f'<span class="field-value">{value}</span></div>'
        for label, value in fields
    )
    return f'<div class="card">{fields_html}</div>'


def build_pdf_cards_document(city_diff, prompt_lines, city_key):
    cards_html = "".join(build_card_html(row, prompt_lines) for _, row in city_diff.iterrows())
    doc = f"""<!DOCTYPE html>
<html dir="rtl" lang="he">
<head>
<meta charset="UTF-8">
<style>{CARD_STYLE}</style>
</head>
<body>
    <h2>עדכון רישיונות כריתה - {html.escape(city_key)}</h2>
    <div class="cards">{cards_html}</div>
</body>
</html>"""
    doc = doc.replace("חדש/עודכן", '<span class="status-new">חדש/עודכן</span>')
    doc = doc.replace("הוסר מהמערכת", '<span class="status-removed">הוסר מהמערכת</span>')
    return doc

def get_latest_report_url():
    """Latest published weekly report URL, or None if none exist yet."""
    dates = [m.group(1) for path in glob.glob("statics/reports/report_*.html")
             if (m := REPORT_RE.search(path))]
    if not dates:
        return None
    return f"{REPORTS_BASE_URL}report_{max(dates)}.html"

def get_dated_snapshots():
    """Returns (date_str, path) for each archive/full_licenses_<date>.csv, sorted ascending."""
    snapshots = []
    for path in glob.glob("archive/full_licenses_*.csv"):
        m = DATE_RE.search(path)
        if m:
            snapshots.append((m.group(1), path))
    snapshots.sort()
    return snapshots

def get_diff():
    """
    Compares the two most recent dated snapshots and returns a DataFrame
    of both added and removed rows.
    """
    snapshots = get_dated_snapshots()
    if len(snapshots) < 2:
        print("Need at least two dated snapshots in archive/ to compare.")
        return None

    (_, v2_path), (_, v1_path) = snapshots[-2], snapshots[-1]

    print(f"Detecting all changes (additions and deletions) between {v2_path} and {v1_path}...")
    # Load and normalize
    df_new = pd.read_csv(v1_path, dtype=str).fillna('').map(lambda x: str(x).strip())
    df_old = pd.read_csv(v2_path, dtype=str).fillna('').map(lambda x: str(x).strip())
    
    def normalize_nums(val):
        if val.endswith('.0'): return val[:-2]
        return val
    
    df_new = df_new.map(normalize_nums)
    df_old = df_old.map(normalize_nums)

    # Use merge to find differences
    merged = df_new.merge(df_old, how='outer', indicator=True)
    
    # Rows in v1 but not v2
    added = merged[merged['_merge'] == 'left_only'].copy()
    added['סוג שינוי'] = 'חדש/עודכן'
    
    # Rows in v2 but not v1
    removed = merged[merged['_merge'] == 'right_only'].copy()
    removed['סוג שינוי'] = 'הוסר מהמערכת'

    all_changes = pd.concat([added, removed], ignore_index=True).drop('_merge', axis=1)
    
    return all_changes

def send_admin_summary(sent):
    """Emails ADMIN_EMAILS a brief recap of the per-city mails sent today."""
    if sent:
        rows = "".join(
            f"<tr><td>{email}</td><td>{city_key}</td><td>{count}</td></tr>"
            for email, city_key, count in sent
        )
        body_note = f"<p>נשלחו <strong>{len(sent)}</strong> מיילים היום.</p>"
        table_html = f"""
        <table>
            <tr><th>נמען</th><th>ישוב</th><th>מספר שינויים</th></tr>
            {rows}
        </table>
        """
    else:
        body_note = "<p>לא נשלחו מיילים היום (לא נמצאו שינויים רלוונטיים למנויים).</p>"
        table_html = ""

    html_body = f"""
<!DOCTYPE html>
<html dir="rtl" lang="he">
<head>
<meta charset="UTF-8">
<style>
{EMAIL_STYLE}
</style>
</head>
<body>
    <div class="container">
        <h2>🌳 סיכום מיילים יומי - רישיונות כריתה</h2>
        {body_note}
        {table_html}
        <div class="footer">
            הודעה זו נשלחה באופן אוטומטי על ידי בוט מעקב רישיונות כריתה.<br>
            תאריך הפקה: {datetime.now().strftime('%d/%m/%Y %H:%M')}
        </div>
    </div>
</body>
</html>
"""
    send_html_mail(ADMIN_EMAILS, "סיכום מיילים יומי - רישיונות כריתה", html_body)


def send_notifications(diff_df):
    if diff_df is None or diff_df.empty:
        print("No changes detected.")
        send_admin_summary([])
        return

    subscribers = get_subscribers()
    if not subscribers:
        print("Error: could not load subscribers from Google Sheet.")
        send_admin_summary([])
        return

    print(f"Found {len(diff_df)} total row changes. Checking against {len(subscribers)} subscriber(s)...")

    report_url = get_latest_report_url()
    report_link_html = (
        f'<p><a href="{report_url}">קישור לדוח הכריתות השבועי</a></p>' if report_url else ""
    )

    prompt_lines = load_ai_prompt_lines()

    # Resolve matches upfront so the inter-send sleep only runs *between*
    # actual sends, never after the last one (or when nothing matches).
    to_send = []
    for email, city_raw in subscribers:
        city_key = city_raw.replace("'", "").replace('"', "")
        city_diff = diff_df[diff_df['ישוב'].str.contains(city_key, na=False)]
        if not city_diff.empty:
            to_send.append((email, city_key, city_diff))
        else:
            print(f"-> No relevant changes for {city_key} ({email}).")

    for i, (email, city_key, city_diff) in enumerate(to_send):
        print(f"-> Sending {len(city_diff)} changes to {email} for {city_key}")

        # Slim 5-column table for the email body itself - see
        # build_email_table_html's docstring for why. Full per-license
        # detail (all raw columns + map/plan/GovMap/objection-help links)
        # goes into the PDF card attachment below instead.
        html_table = build_email_table_html(city_diff, prompt_lines)

        # Full HTML structure with RTL support for Hebrew
        html_body = f"""
<!DOCTYPE html>
<html dir="rtl" lang="he">
<head>
<meta charset="UTF-8">
<style>
{EMAIL_STYLE}
</style>
</head>
<body>
    <div class="container">
        {report_link_html}
        <h2>🌳 עדכון רישיונות כריתה - {city_key}</h2>
        <p>שלום,</p>
        <p>נמצאו <strong>{len(city_diff)}</strong> שינויים ברשימת הרישיונות עבור היישוב <strong>{city_key}</strong>.</p>
        <p>
            הקלקה על סיבת הבקשה תפתח את גוגל עם אפשרות לעזרה מבינה מלאכותי.<br>
            בשדה הכתובת יש צלמיות להצגת הכתובת בגוגל מפות וצלמית להצגה באתר המפות הלאומי.<br>
            הקלקה על שם העיר תפתח עם קצת מזל את התוכנית במנהל התכנון.<br>
            כמו כן, מצורף קובץ עם פירוט גדול יותר של השינויים.
        </p>

        {html_table}

        <div class="footer">
            הודעה זו נשלחה באופן אוטומטי על ידי בוט מעקב רישיונות כריתה.<br>
            תאריך הפקה: {datetime.now().strftime('%d/%m/%Y %H:%M')}
        </div>
    </div>
</body>
</html>
"""

        # Save local copy for debugging
        os.makedirs("tmp", exist_ok=True)
        debug_filename = f"tmp/last_mail_{city_key.replace(' ', '_')}.html"
        with open(debug_filename, "w", encoding="utf-8") as df:
            df.write(html_body)
        print(f"   Debug HTML saved to: {debug_filename}")

        # Full per-license detail (every raw column + map/plan/GovMap/
        # objection-help links) as one card per license, attached as a PDF -
        # the email body itself only shows the slim 5-column table.
        pdf_document = build_pdf_cards_document(city_diff, prompt_lines, city_key)
        pdf_filename = f"tmp/last_mail_{city_key.replace(' ', '_')}.pdf"
        render_pdf(pdf_document, pdf_filename)
        print(f"   Debug PDF saved to: {pdf_filename}")

        # Send mail with HTML content
        subject = f"שינויים ברישיונות כריתה - {city_key}"
        send_html_mail([email], subject, html_body, attachments=[pdf_filename])

        if i < len(to_send) - 1:
            time.sleep(1800)  # 30 min between sends to avoid Gmail rate-limiting/blocking

    send_admin_summary([(email, city_key, len(city_diff)) for email, city_key, city_diff in to_send])

if __name__ == "__main__":
    diff = get_diff()
    send_notifications(diff)
