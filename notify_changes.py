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

# Small inline-SVG brand icons for the PDF (Chromium-rendered via
# mailer.render_pdf, so raw <svg> works natively) - same icons as
# statics/generate_dashboard.py. The email body stays on emoji (💬/🗺️/📋)
# since most mail clients strip inline SVG/data-URI images.
WHATSAPP_ICON_SVG = (
    '<svg width="18" height="18" viewBox="0 0 24 24" style="vertical-align:middle">'
    '<circle cx="12" cy="12" r="12" fill="#25D366"/>'
    '<path fill="#fff" d="M12 5.5a6.5 6.5 0 0 0-5.6 9.8L5.5 18.5l3.3-.9a6.5 6.5 0 1 0 3.2-12.1zm0 1.2a5.3 5.3 0 1 1 0 10.6 5.2 5.2 0 0 1-2.7-.75l-.2-.1-1.9.5.5-1.85-.13-.2a5.3 5.3 0 0 1 4.43-8.2zm-2.9 2.9c-.13 0-.34.05-.52.25-.18.2-.68.66-.68 1.6s.7 1.86.8 2c.1.13 1.36 2.1 3.3 2.86 1.62.65 1.95.52 2.3.49.35-.03 1.13-.46 1.28-.9.15-.45.15-.83.1-.9-.05-.08-.18-.13-.38-.23-.2-.1-1.13-.56-1.3-.62-.18-.06-.3-.1-.44.1-.13.2-.5.62-.6.75-.1.13-.22.14-.4.05-.2-.1-.83-.3-1.58-.98-.58-.52-.98-1.15-1.1-1.35-.1-.2-.01-.3.09-.4.09-.1.2-.24.3-.36.1-.13.13-.2.2-.34.06-.13.03-.25-.02-.35-.05-.1-.44-1.08-.62-1.47-.16-.38-.32-.33-.44-.33h-.38z"/>'
    "</svg>"
)
GOOGLE_MAPS_ICON_SVG = (
    '<svg width="18" height="18" viewBox="0 0 24 24" style="vertical-align:middle">'
    '<path fill="#4285F4" d="M12 2C7.6 2 4 5.6 4 10c0 5.4 6.7 11 7.3 11.5.2.15.4.2.7.2s.5-.05.7-.2C13.3 21 20 15.4 20 10c0-4.4-3.6-8-8-8z"/>'
    '<circle cx="12" cy="10" r="3.2" fill="#fff"/>'
    "</svg>"
)
PLANNING_ICON_SVG = (
    '<svg width="18" height="18" viewBox="0 0 24 24" style="vertical-align:middle">'
    '<path fill="#6d4c2f" d="M12 2 2 8h20L12 2zM4 10v9H2v2h20v-2h-2v-9h-2v9h-3v-9h-2v9h-2v-9H9v9H6v-9H4z"/>'
    "</svg>"
)

# Same mobile table (כתובת/עצים לכריתה/ימים שנותרו, RTL) as the email body
# and statics/reports/open_for_objection.html, rendered to a standard A4
# page (see mailer.py's render_pdf) instead of the stacked field-cards
# this used to be.
CARD_STYLE = """
    body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; direction: rtl; text-align: right; background-color: #fff; padding: 12px; color: #24331f; }
    h2 { color: #1b5e34; border-bottom: 3px solid #a5d6a7; padding-bottom: 8px; font-size: 22px; }
    table, .diff-table { border-collapse: collapse; width: 100%; margin-top: 15px; font-size: 14px; direction: rtl; }
    th, td, .diff-table th, .diff-table td { border: 1px solid #dfe9d8; padding: 8px; text-align: right; direction: rtl; overflow-wrap: anywhere; }
    th, .diff-table th { background-color: #2e7d46; color: #fff; }
    .diff-table th:nth-child(1), .diff-table td:nth-child(1) { width: 20ch; max-width: 20ch; white-space: normal; word-break: break-word; }
    tr { break-inside: avoid; page-break-inside: avoid; }
    a { color: #2ba8e0; }
"""


def load_ai_prompt_lines():
    """Same objection-help AI prompt used in statics/reports/open_for_objection.html."""
    config = configparser.ConfigParser()
    config.read(CONFIG_PATH, encoding="utf-8")
    return [line.strip() for line in config["objection_help"]["prompt"].splitlines() if line.strip()]


def is_construction_reason(reason):
    text = str(reason or "")
    return "בנייה" in text or "ופיתוח" in text


def parse_days_left(deadline_str):
    """Days between today and DEADLINE_COL's date, or None if unparsable - mirrors
    the _days_left column statics/generate_dashboard.py derives for the same report."""
    try:
        deadline_dt = datetime.strptime(str(deadline_str).strip(), "%d/%m/%Y")
    except (ValueError, TypeError):
        return None
    return (deadline_dt.date() - datetime.now().date()).days


def deadline_bg(days_left, trees_to_cut):
    """Same grey-to-white deadline shading (darker = more urgent) plus a brown
    tint for >3 trees, as statics/generate_dashboard.py's open_for_objection
    table - keeps the email/PDF rows visually consistent with the website."""
    has_deadline = days_left is not None
    many_trees = trees_to_cut is not None and trees_to_cut > 3
    if not has_deadline and not many_trees:
        return None
    if has_deadline:
        days_left = max(0, days_left)
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


def whatsapp_message_lines(row, license_id, share_url):
    street = str(row.get(STREET_COL, "") or "").strip()
    lines = [f"רישיון כריתה מספר {license_id}", f"ישוב: {row.get(CITY_COL, '')}"]
    if street:
        lines.append(f"כתובת: {street}")
    gush = str(row.get(GUSH_COL, "") or "").strip()
    helka = str(row.get(HELKA_COL, "") or "").strip()
    if gush and helka:
        lines.append(f"גוש/חלקה: {gush}/{helka}")
    reason = str(row.get(REASON_COL, "") or "").strip()
    if reason:
        lines.append(f"סיבת בקשה: {reason}")
    species = str(row.get(SPECIES_COL, "") or "").strip()
    if species:
        lines.append(f"מיני עצים: {species}")
    cut = str(row.get(CUT_COL, "") or "").strip()
    if cut:
        lines.append(f"עצים לכריתה: {cut}")
    applicant = str(row.get(APPLICANT_COL, "") or "").strip()
    if applicant:
        lines.append(f"מבקש: {applicant}")
    deadline = str(row.get(DEADLINE_COL, "") or "").strip()
    if deadline:
        lines.append(f"מועד אחרון להשגה: {deadline}")
    lines.append(f"קישור: {share_url}")
    return lines


def whatsapp_icon_link(row, license_id, share_url, use_svg_icons=False):
    text = quote_plus("\n".join(whatsapp_message_lines(row, license_id, share_url)))
    label = WHATSAPP_ICON_SVG if use_svg_icons else "💬"
    return f'<a href="https://wa.me/?text={text}" target="_blank" rel="noopener">{label}</a>'


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


def address_cell(row, license_id, prompt_lines, use_svg_icons=False):
    """Mirrors the כתובת cell on statics/reports/open_for_objection.html:
    address text (+ city in parens when there's a street), map/GovMap/plan
    icons, a link that searches the report page by ישוב name (diff rows
    aren't necessarily "open for objection" status, so a #license-<id>
    deep link could point at a row that isn't even on that page), a
    WhatsApp share carrying the full license details, and the AI
    objection-help search - all in one cell instead of spread across
    separate columns/links. use_svg_icons swaps emoji for the real
    WhatsApp/Google-Maps/planning SVG icons (PDF only - email clients
    strip inline SVG, so the email body stays on emoji)."""
    street = str(row.get(STREET_COL, "") or "").strip()
    city = str(row.get(CITY_COL, "") or "").strip()
    if not street and not city:
        return ""
    display_address = street if street else city
    city_span = f" ({html.escape(city)})" if street else ""

    maps_icon = GOOGLE_MAPS_ICON_SVG if use_svg_icons else "🗺️"
    icons_line1 = maps_link(row, label=maps_icon)
    govmap_url = str(row.get(GOVMAP_URL_COL, "") or "").strip()
    if govmap_url:
        icons_line1 += " " + url_link(govmap_url, "🛰️")
    plan_url = str(row.get(PLAN_URL_COL, "") or "").strip()
    reason = str(row.get(REASON_COL, "") or "").strip()
    if plan_url and is_construction_reason(reason):
        plan_icon = PLANNING_ICON_SVG if use_svg_icons else "📋"
        icons_line1 += " " + url_link(plan_url, plan_icon)

    share_url = f"{REPORTS_BASE_URL}open_for_objection.html#search-{quote_plus(city)}"
    icons_line2 = (
        f'{url_link(share_url, "🔗")} '
        f"{whatsapp_icon_link(row, license_id, share_url, use_svg_icons=use_svg_icons)}"
    )
    ai_line = objection_search_link(row, prompt_lines, label="🤖")

    return f'{html.escape(display_address)}{city_span}<br>{icons_line1}<br>{icons_line2}<br>{ai_line}'


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


def url_link(url, label):
    """Turns a bare URL into a fixed-label link, so raw mavat.iplan.gov.il/
    govmap.gov.il URLs don't force layouts wider than they need to be."""
    url = str(url or "").strip()
    if not url:
        return ""
    return f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">{label}</a>'


def email_days_left_text(days_left):
    if days_left is None:
        return "—"
    if days_left < 0:
        return "עבר המועד"
    if days_left == 0:
        return "היום האחרון"
    return f"{days_left:,}"


def build_email_row_html(row, prompt_lines, use_svg_icons=False):
    license_id = str(row.get(LICENSE_COL, "")).strip()
    days_left = parse_days_left(row.get(DEADLINE_COL, ""))
    try:
        trees_to_cut = float(row.get(CUT_COL, "") or 0)
    except ValueError:
        trees_to_cut = None

    bg = deadline_bg(days_left, trees_to_cut)
    row_style = f' style="background-color:{bg}"' if bg else ""

    return (
        f"<tr{row_style}>"
        f"<td>{address_cell(row, license_id, prompt_lines, use_svg_icons=use_svg_icons)}</td>"
        f'<td>{html.escape(str(row.get(CUT_COL, "")))}</td>'
        f"<td>{email_days_left_text(days_left)}</td>"
        f"<td>{html.escape(str(row.get(REASON_COL, '')) or '—')}</td>"
        f"</tr>"
    )


def build_email_table_html(city_diff, prompt_lines, use_svg_icons=False):
    """כתובת (carries all the icons - map/GovMap/plan/share/WhatsApp/AI),
    עצים לכריתה, ימים שנותרו and סיבת כריתה, right-to-left - the columns
    asked for in the email/PDF specifically (the website keeps the full
    column set)."""
    rows_html = "".join(
        build_email_row_html(row, prompt_lines, use_svg_icons=use_svg_icons) for _, row in city_diff.iterrows()
    )
    return f"""<table class="diff-table" dir="rtl">
    <thead><tr>
        <th>כתובת</th><th>עצים<br>לכריתה</th><th>ימים<br>שנותרו</th><th>סיבת כריתה</th>
    </tr></thead>
    <tbody>{rows_html}</tbody>
</table>"""


def build_pdf_cards_document(city_diff, prompt_lines, city_key):
    """Same כתובת/עצים לכריתה/ימים שנותרו table (RTL, with the same shading)
    as the email body, rendered as a standalone PDF page - with real
    WhatsApp/Google-Maps/planning SVG icons instead of emoji, since
    Chromium (which renders this PDF) supports inline SVG natively."""
    table_html = build_email_table_html(city_diff, prompt_lines, use_svg_icons=True)
    return f"""<!DOCTYPE html>
<html dir="rtl" lang="he">
<head>
<meta charset="UTF-8">
<style>{CARD_STYLE}</style>
</head>
<body>
    <h2>עדכון רישיונות כריתה - {html.escape(city_key)}</h2>
    {table_html}
</body>
</html>"""

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
            בשורת ה<strong>כתובת</strong> שבטבלה: 🗺️ פותח את המיקום ב-Google Maps, 🛰️ פותח תצלום אוויר ב-GovMap,
            📋 פותח את התוכנית ב-מנהל התכנון (כשקיימת), 🔗 מקשר ישירות לרישיון הזה בדוח באתר,
            💬 משתף אותו בוואטסאפ, ו-🤖 פותח חיפוש בגוגל לעזרה בהגשת השגה.<br>
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
            time.sleep(30)  # 30 sec between sends to avoid Gmail rate-limiting/blocking

    send_admin_summary([(email, city_key, len(city_diff)) for email, city_key, city_diff in to_send])

if __name__ == "__main__":
    diff = get_diff()
    send_notifications(diff)
