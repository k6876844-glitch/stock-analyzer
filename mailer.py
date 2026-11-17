"""Outgoing email over SMTP (e.g. Gmail with an app password).

Configure with environment variables; email is simply skipped when they're unset:
    SMTP_USER       the Gmail address that sends alerts
    SMTP_PASSWORD   a Gmail *app password* (Google Account -> Security -> App passwords)
    SMTP_HOST       default smtp.gmail.com
    SMTP_PORT       default 587 (STARTTLS)
    MAIL_FROM       default SMTP_USER
    APP_URL         public site URL used in email links
"""
import os
import smtplib
import ssl
import sys
import threading
from email.message import EmailMessage

HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
PORT = int(os.environ.get("SMTP_PORT", "587"))
USER = os.environ.get("SMTP_USER", "")
PASSWORD = os.environ.get("SMTP_PASSWORD", "")
FROM = os.environ.get("MAIL_FROM", USER)
APP_URL = os.environ.get("APP_URL", "https://nse-stock-analyzer-6dwk.onrender.com").rstrip("/")


def enabled():
    return bool(USER and PASSWORD)


def send(to, subject, text, html=None):
    """Send one email. Returns True on success; never raises."""
    if not enabled():
        return False
    msg = EmailMessage()
    msg["From"] = f"Kenya Stock Analyzer <{FROM}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    try:
        with smtplib.SMTP(HOST, PORT, timeout=20) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(USER, PASSWORD)
            s.send_message(msg)
        return True
    except Exception as e:
        print(f"email to {to} failed: {e!r}", file=sys.stderr, flush=True)
        return False


def send_async(to, subject, text, html=None):
    threading.Thread(target=send, args=(to, subject, text, html), daemon=True).start()
