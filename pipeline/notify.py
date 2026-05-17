"""
Notification helpers — Telegram + email.
Both channels are fire-and-forget: silently skip if not configured.

Telegram env vars (recommended):
  TELEGRAM_BOT_TOKEN  — from @BotFather
  TELEGRAM_CHAT_ID    — your personal chat ID (message @userinfobot to find it)

Email env vars (optional backup):
  NOTIFY_EMAIL_FROM     — Gmail address sending FROM
  NOTIFY_EMAIL_PASSWORD — Gmail App Password (not your login password)
  NOTIFY_EMAIL_TO       — address to receive alerts
"""

import os
import smtplib
from email.mime.text import MIMEText


# ---------------------------------------------------------------------------
# Core senders
# ---------------------------------------------------------------------------

def _send_telegram(message: str):
    import requests as _req
    token   = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        _req.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"[notify] Telegram failed: {e}")


def _send_email(subject: str, body: str):
    from_addr = os.environ.get("NOTIFY_EMAIL_FROM")
    password  = os.environ.get("NOTIFY_EMAIL_PASSWORD")
    to_addr   = os.environ.get("NOTIFY_EMAIL_TO", from_addr)
    if not from_addr or not password or not to_addr:
        return
    try:
        msg            = MIMEText(body, "plain")
        msg["Subject"] = f"[Content Distributor] {subject}"
        msg["From"]    = from_addr
        msg["To"]      = to_addr
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as srv:
            srv.login(from_addr, password)
            srv.sendmail(from_addr, to_addr, msg.as_string())
    except Exception as e:
        print(f"[notify] Email failed: {e}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def notify(subject: str, body: str):
    """Send via both Telegram and email (whichever is configured)."""
    _send_telegram(f"<b>{subject}</b>\n\n{body}")
    _send_email(subject, body)


def send_email(subject: str, body: str):
    """Email only — for callers that existed before Telegram was added."""
    _send_email(subject, body)
    _send_telegram(f"<b>{subject}</b>\n\n{body}")


# ---------------------------------------------------------------------------
# Viral milestone detection — called from pipeline/learning.py after harvest
# ---------------------------------------------------------------------------

_VIRAL_TIERS = [
    (100_000, 2, "💥 MEGA VIRAL"),
    ( 10_000, 1, "🔥 GOING VIRAL"),
]


def check_viral_posts(app):
    """
    After metrics are harvested, check every post for viral milestones.
    Alerts once per tier (10k views → tier 1, 100k views → tier 2).
    """
    with app.app_context():
        from models import db, PostMetrics, SocialAccount

        candidates = PostMetrics.query.filter(
            PostMetrics.views > 0,
            PostMetrics.viral_milestone < 2,   # not yet at top tier
        ).all()

        alerted = 0
        for pm in candidates:
            for threshold, tier, label in _VIRAL_TIERS:
                if pm.views >= threshold and pm.viral_milestone < tier:
                    account = SocialAccount.query.get(pm.account_id)
                    handle  = f"@{account.account_name}" if account else "unknown"
                    platform = (pm.platform or "").capitalize()
                    hook    = pm.hook_text or pm.caption or "—"
                    hook    = hook[:80] + "…" if len(hook) > 80 else hook

                    subject = f"{label} — {pm.views:,} views on {handle}"
                    body    = (
                        f"{label}\n\n"
                        f"Account: {handle} ({platform})\n"
                        f"Niche:   {pm.niche or '—'}\n"
                        f"Views:   {pm.views:,}\n"
                        f"Likes:   {pm.likes:,}\n"
                        f"Hook:    {hook}\n\n"
                        f"Posted: {pm.posted_at.strftime('%Y-%m-%d %H:%M') if pm.posted_at else '—'} UTC"
                    )
                    notify(subject, body)
                    pm.viral_milestone = tier
                    alerted += 1
                    break  # one alert per post per harvest cycle

        if alerted:
            db.session.commit()
            print(f"[notify] Sent {alerted} viral alert(s)")
