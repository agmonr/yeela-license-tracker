import os
import subprocess
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

SENDMAIL = "/usr/sbin/sendmail"

# Shared email theme, matching the statics/reports dashboard's forest-green
# palette. Email clients routinely strip CSS custom properties and <link>
# web fonts from <head>, so unlike the dashboard's BASE_CSS this uses plain
# hex colors and only the system font stack.
EMAIL_STYLE = """
    body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; direction: rtl; text-align: right; background-color: #f2f7ee; padding: 20px; color: #24331f; }
    .container { background-color: #fff; padding: 24px; border-radius: 12px; box-shadow: 0 2px 8px rgba(27,94,52,0.12); border-top: 6px solid #2e7d46; }
    h2 { color: #1b5e34; border-bottom: 3px solid #a5d6a7; padding-bottom: 10px; }
    h3 { color: #1b5e34; margin-top: 25px; }
    table, .diff-table { border-collapse: collapse; width: 100%; margin-top: 15px; font-size: 13px; direction: rtl; }
    th, td, .diff-table th, .diff-table td { border: 1px solid #dfe9d8; padding: 10px 8px; text-align: right; direction: rtl; }
    th, .diff-table th { background-color: #2e7d46; color: #fff; white-space: nowrap; }
    .diff-table th:nth-child(1), .diff-table td:nth-child(1) { width: 20ch; max-width: 20ch; white-space: normal; overflow-wrap: anywhere; word-break: break-word; }
    tr:nth-child(even) { background-color: #f5f9f1; }
    .status-new { color: #2e7d46; font-weight: bold; }
    .status-removed { color: #e05353; font-weight: bold; }
    .footer { margin-top: 30px; font-size: 12px; color: #5b6f56; border-top: 1px solid #dfe9d8; padding-top: 10px; }
    a { color: #2ba8e0; }
"""


def render_pdf(html_body, output_path):
    """
    Renders an HTML document to a narrow A4 PDF via the same headless
    Chromium Playwright already installs for scraping (fetch_data.py) -
    avoids adding a new PDF-rendering dependency. Content (see
    notify_changes.py's build_pdf_cards_document) is a single column of
    stacked cards, so a standard portrait page is enough - no custom wide
    page or print-only shrink rules needed.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html_body, wait_until="networkidle")
        page.pdf(
            path=str(output_path),
            format="A4",
            print_background=True,
            margin={"top": "10mm", "bottom": "10mm", "left": "10mm", "right": "10mm"},
        )
        browser.close()


def send_html_mail(to_addrs, subject, html_body, attachments=None):
    """
    Sends an HTML email via sendmail. GNU Mailutils' `mail -a` refuses to
    override the Content-Type header, so `mail`-based HTML sends silently
    degrade to plain text with raw HTML source visible. Building a proper
    MIME message avoids that.
    """
    msg = MIMEMultipart('mixed')
    msg['Subject'] = subject
    msg['To'] = ", ".join(to_addrs)
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    for path in attachments or []:
        with open(path, 'rb') as f:
            part = MIMEApplication(f.read(), Name=os.path.basename(path))
        part['Content-Disposition'] = f'attachment; filename="{os.path.basename(path)}"'
        msg.attach(part)

    subprocess.run([SENDMAIL, "-t", "-oi"], input=msg.as_bytes())
