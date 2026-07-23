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
    body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; direction: rtl; text-align: right; background-color: #fff; padding: 12px; color: #24331f; font-size: 22px; }
    .brand { text-align: center; font-size: 26px; font-weight: bold; color: #1b5e34; letter-spacing: 1px; margin-bottom: 4px; }
    .generated-at { color: #5b6f56; font-size: 16px; margin: 0 0 8px; }
    h2 { color: #1b5e34; border-bottom: 3px solid #a5d6a7; padding-bottom: 8px; font-size: 26px; }
    a { color: #2ba8e0; }
    /* License card: header (address + license number + key facts), then 3
       grouped sections - מידע, הפצה, פעולה - same idea as the site's icon
       dashboard, sized up for on-paper readability. */
    .cards { display: block; margin-top: 15px; }
    .lic-card { border: 1px solid #dfe9d8; border-radius: 12px; overflow: hidden; margin-bottom: 20px; break-inside: avoid; page-break-inside: avoid; }
    .lic-header { padding: 14px 16px; background: #fff; border-bottom: 2px solid #dfe9d8; }
    .lic-header .addr { font-size: 24px; font-weight: 700; color: #1b5e34; }
    .lic-header .sub { font-size: 17px; color: #5b6f56; margin-top: 2px; }
    .header-facts { margin-top: 10px; line-height: 1.9; }
    .field-label { color: #5b6f56; font-size: 16px; margin-inline-end: 4px; }
    .field-value { font-weight: 600; }
    .section { padding: 12px 16px; border-bottom: 1px solid #dfe9d8; break-inside: avoid; page-break-inside: avoid; }
    .section:last-child { border-bottom: none; }
    .tag { display: inline-block; font-weight: 700; font-size: 16px; padding: 4px 12px; border-radius: 999px; background: rgba(255,255,255,0.7); margin-bottom: 4px; }
    .fields { display: block; }
    .field-row { display: block; padding: 3px 0; }
    .field-row a { text-decoration: none; }
    .section-info { background-color: #eaf2fe; }
    .section-spread { background-color: #e6f7ee; }
    .section-action { background-color: #fff2dc; }
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


def whatsapp_icon_link(row, license_id, share_url, use_svg_icons=False, label_text="שיתוף בוואטסאפ"):
    text = quote_plus("\n".join(whatsapp_message_lines(row, license_id, share_url)))
    icon = WHATSAPP_ICON_SVG if use_svg_icons else "💬"
    cls_attr = ' class="share-icon"' if use_svg_icons else ""
    return f'<a href="https://wa.me/?text={text}" target="_blank" rel="noopener"{cls_attr}>{icon} {html.escape(label_text)}</a>'


def maps_link(row, label="🗺️ מפה", cls=""):
    """Static (non-JS) Google Maps link, for the same address shown on the dashboard."""
    street = str(row.get(STREET_COL, "") or "").strip()
    city = str(row.get(CITY_COL, "") or "").strip()
    address = f"{street}, {city}" if street else city
    if not address:
        return ""
    query = quote_plus(f"{address}, ישראל")
    url = f"https://www.google.com/maps/search/?api=1&query={query}"
    cls_attr = f' class="{cls}"' if cls else ""
    return f'<a href="{url}" target="_blank" rel="noopener"{cls_attr}>{label}</a>'




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


def objection_search_link(row, prompt_lines, label="🔍 עזרה בהגשת השגה", cls=""):
    cls_attr = f' class="{cls}"' if cls else ""
    return f'<a href="{objection_search_url(row, prompt_lines)}" target="_blank" rel="noopener"{cls_attr}>{label}</a>'


def url_link(url, label, cls=""):
    """Turns a bare URL into a fixed-label link, so raw mavat.iplan.gov.il/
    govmap.gov.il URLs don't force layouts wider than they need to be."""
    url = str(url or "").strip()
    if not url:
        return ""
    cls_attr = f' class="{cls}"' if cls else ""
    return f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener"{cls_attr}>{label}</a>'


def email_days_left_text(days_left):
    if days_left is None:
        return "—"
    if days_left < 0:
        return "עבר המועד"
    if days_left == 0:
        return "היום האחרון"
    return f"{days_left:,}"


def build_license_card_html(row, prompt_lines, use_svg_icons=False):
    """One license as a header (address, license number, and the
    גוש/חלקה, סיבת כריתה, עצים לכריתה, ימים שנותרו facts) plus 3 grouped
    sections below it: מידע (map/GovMap/plan links), הפצה (share link +
    WhatsApp) and פעולה (AI objection-help search) - same idea on the
    website's icon dashboard, just laid out as a card instead of a table
    row. use_svg_icons swaps emoji for the real WhatsApp/Google-Maps/
    planning SVG icons (PDF only - email clients strip inline SVG)."""
    license_id = str(row.get(LICENSE_COL, "")).strip()
    street = str(row.get(STREET_COL, "") or "").strip()
    city = str(row.get(CITY_COL, "") or "").strip()
    display_address = street if street else city
    city_suffix = f" ({html.escape(city)})" if street and city else ""

    days_left = parse_days_left(row.get(DEADLINE_COL, ""))
    try:
        trees_to_cut = float(row.get(CUT_COL, "") or 0)
    except ValueError:
        trees_to_cut = None
    bg = deadline_bg(days_left, trees_to_cut)
    card_style = f' style="background-color:{bg}"' if bg else ""

    gush = str(row.get(GUSH_COL, "") or "").strip()
    helka = str(row.get(HELKA_COL, "") or "").strip()
    gush_helka = f"{gush}/{helka}" if gush and helka else "—"
    reason = str(row.get(REASON_COL, "") or "").strip() or "—"

    facts = "&nbsp;&nbsp;&nbsp;·&nbsp;&nbsp;&nbsp;".join(
        f'<span class="field-label">{label}:</span>&nbsp;<span class="field-value">{value}</span>'
        for label, value in [
            ("גוש/חלקה", html.escape(gush_helka)),
            ("סיבת כריתה", html.escape(reason)),
            ("עצים לכריתה", html.escape(str(row.get(CUT_COL, "")))),
            ("ימים שנותרו", email_days_left_text(days_left)),
        ]
    )

    icon_cls = "map-icon" if use_svg_icons else ""
    maps_icon = GOOGLE_MAPS_ICON_SVG if use_svg_icons else "🗺️"
    info_links = [maps_link(row, label=f"{maps_icon} Google Maps", cls=icon_cls)]
    govmap_url = str(row.get(GOVMAP_URL_COL, "") or "").strip()
    if govmap_url:
        govmap_icon = "🛰️"
        info_links.append(url_link(govmap_url, f"{govmap_icon} GovMap", cls=icon_cls))
    plan_url = str(row.get(PLAN_URL_COL, "") or "").strip()
    if plan_url and is_construction_reason(row.get(REASON_COL, "")):
        plan_icon = PLANNING_ICON_SVG if use_svg_icons else "📋"
        info_links.append(url_link(plan_url, f"{plan_icon} תוכנית במנהל התכנון", cls=icon_cls))
    info_html = "".join(f'<div class="field-row">{lnk}</div>' for lnk in info_links)

    share_cls = "share-icon" if use_svg_icons else ""
    share_url = f"{REPORTS_BASE_URL}open_for_objection.html#search-{quote_plus(city)}"
    spread_html = (
        f'<div class="field-row">{url_link(share_url, "🔗 קישור לרישיון בדוח", cls=share_cls)}</div>'
        f'<div class="field-row">{whatsapp_icon_link(row, license_id, share_url, use_svg_icons=use_svg_icons)}</div>'
    )

    action_cls = "action-icon" if use_svg_icons else ""
    action_html = (
        f'<div class="field-row">'
        f'{objection_search_link(row, prompt_lines, label="🤖 עזרה בהגשת השגה", cls=action_cls)}'
        f"</div>"
    )

    return f"""<div class="lic-card"{card_style}>
    <div class="lic-header">
        <div class="addr">📍 {html.escape(display_address)}{city_suffix}</div>
        <div class="sub">מספר רישיון {html.escape(license_id)}</div>
        <div class="header-facts">{facts}</div>
    </div>
    <div class="section section-info"><span class="tag">מידע</span><div class="fields">{info_html}</div></div>
    <div class="section section-spread"><span class="tag">הפצה</span><div class="fields">{spread_html}</div></div>
    <div class="section section-action"><span class="tag">פעולה</span><div class="fields">{action_html}</div></div>
</div>"""


def build_license_cards_html(city_diff, prompt_lines, use_svg_icons=False):
    cards = "".join(
        build_license_card_html(row, prompt_lines, use_svg_icons=use_svg_icons) for _, row in city_diff.iterrows()
    )
    return f'<div class="cards">{cards}</div>'


def build_pdf_cards_document(city_diff, prompt_lines, city_key):
    """Same license cards as the email body, rendered as a standalone PDF
    page - with real WhatsApp/Google-Maps/planning SVG icons instead of
    emoji, since Chromium (which renders this PDF) supports inline SVG
    natively."""
    cards_html = build_license_cards_html(city_diff, prompt_lines, use_svg_icons=True)
    generated_at = datetime.now().strftime("%d/%m/%Y %H:%M")
    return f"""<!DOCTYPE html>
<html dir="rtl" lang="he">
<head>
<meta charset="UTF-8">
<style>{CARD_STYLE}</style>
</head>
<body>
    <div class="brand">🌳🌿🌲 נאמני העצים 🌲🌿🌳</div>
    <h2>עדכון רישיונות כריתה - {html.escape(city_key)}</h2>
    <p class="generated-at">הופק בתאריך {generated_at}</p>
    {cards_html}
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
<body dir="rtl">
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

        cards_html = build_license_cards_html(city_diff, prompt_lines)

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
<body dir="rtl">
    <div class="container">
        {report_link_html}
        <h2>🌳 עדכון רישיונות כריתה - {city_key}</h2>
        <p>שלום,</p>
        <p>נמצאו <strong>{len(city_diff)}</strong> שינויים ברשימת הרישיונות עבור היישוב <strong>{city_key}</strong>.</p>
        <p>
            נסו אותנו: כל רישיון מוצג ככרטיס עם כותרת הכתובת והפרטים העיקריים, ומתחתיה 3 מקטעים -
            <strong>מידע</strong> (קישורים למפה/GovMap/תוכנית), <strong>הפצה</strong> (קישור לרישיון בדוח ושיתוף בוואטסאפ)
            ו<strong>פעולה</strong> (עזרה בהגשת השגה מבינה מלאכותית).<br>
            כמו כן, מצורף קובץ עם אותם כרטיסים בפורמט PDF.
        </p>

        {cards_html}

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
