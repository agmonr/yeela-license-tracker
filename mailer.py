import os
import subprocess
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

SENDMAIL = "/usr/sbin/sendmail"


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
