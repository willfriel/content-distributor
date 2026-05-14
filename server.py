import base64
import json
import secrets
import time
from datetime import datetime
from urllib.parse import urlencode
from dotenv import load_dotenv
load_dotenv()

import requests as http_requests
from flask import Flask, redirect, render_template_string, request, jsonify, session, url_for
from flask_sqlalchemy import SQLAlchemy
from google_auth_oauthlib.flow import Flow

from config import Config
from models import db, Niche, SocialAccount, ContentQueue, PostMetrics, ABTest, TrackedLink, LinkClick, CreditBudget, PipelineRun, LumiStory, LongFormVideo
from integrations.opusclip import OpusClipClient
from integrations import youtube as yt_integration
from integrations import instagram as ig_integration
from integrations import tiktok as tt_integration
from integrations import analytics as analytics_integration

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _telegram_notify(message: str):
    """Fire-and-forget Telegram notification. Silently fails if not configured."""
    token = Config.TELEGRAM_BOT_TOKEN
    chat_id = Config.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        return
    try:
        http_requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception:
        pass


def _error(msg: str, code: int = 400):
    return jsonify({"error": msg}), code


# ---------------------------------------------------------------------------
# Content endpoints
# ---------------------------------------------------------------------------

@app.route("/api/content/submit", methods=["POST"])
def submit_content():
    data = request.get_json(force=True)
    niche_name = data.get("niche")
    video_url = data.get("video_url")
    title = data.get("title", "")
    description = data.get("description", "")
    platforms = data.get("platforms", ["youtube", "instagram", "tiktok"])
    use_opusclip = bool(data.get("use_opusclip", False))

    if not niche_name or not video_url:
        return _error("niche and video_url are required")

    niche = Niche.query.filter_by(name=niche_name, is_active=True).first()
    if not niche:
        return _error(f"Niche '{niche_name}' not found or inactive", 404)

    item = ContentQueue(
        niche_id=niche.id,
        video_url=video_url,
        title=title,
        description=description,
        platforms=platforms,
        use_opusclip=use_opusclip,
        status="pending",
        upload_results={},
        clipped_urls=[],
    )
    db.session.add(item)
    db.session.commit()

    return jsonify({"content_id": item.id, "status": item.status}), 201


@app.route("/api/content/process/<int:content_id>", methods=["POST"])
def process_content(content_id):
    item = ContentQueue.query.get_or_404(content_id)
    if item.status in ("completed", "processing"):
        return jsonify({"message": f"Already {item.status}", "content_id": content_id})

    item.status = "processing"
    db.session.commit()

    errors = []

    # --- Step 1: OpusClip (only if requested and API key is configured) ---
    if item.use_opusclip and Config.OPUSCLIP_API_KEY:
        try:
            item.status = "clipping"
            db.session.commit()

            opus = OpusClipClient()
            job = opus.create_clip_job(item.video_url, item.title)
            item.opusclip_job_id = job.get("job_id") or job.get("id")
            db.session.commit()

            for _ in range(60):
                time.sleep(10)
                status_data = opus.get_job_status(item.opusclip_job_id)
                if status_data.get("status") == "completed":
                    item.clipped_urls = [c["url"] for c in status_data.get("clips", [])]
                    db.session.commit()
                    break
                if status_data.get("status") == "failed":
                    raise RuntimeError("OpusClip job failed")
        except Exception as e:
            errors.append(f"opusclip: {e}")
            item.status = "failed"
            item.error_message = str(e)
            db.session.commit()
            return _error(str(e), 500)

    video_urls_to_upload = item.clipped_urls or [item.video_url]

    # --- Step 2: Upload to each platform ---
    item.status = "uploading"
    db.session.commit()

    accounts = SocialAccount.query.filter_by(niche_id=item.niche_id, is_active=True).all()
    results = {}

    for platform in item.platforms:
        platform_accounts = [a for a in accounts if a.platform == platform]
        results[platform] = {}

        for account in platform_accounts:
            creds = account.get_credentials()
            account_results = []

            for clip_url in video_urls_to_upload:
                try:
                    if platform == "youtube":
                        r = yt_integration.upload_video(creds, clip_url, item.title, item.description)
                    elif platform == "instagram":
                        r = ig_integration.upload_reel(creds, clip_url, item.description or item.title)
                    elif platform == "tiktok":
                        r = tt_integration.upload_video(creds, clip_url, item.title, item.description)
                    else:
                        r = {"error": f"Unknown platform: {platform}"}
                    account_results.append(r)
                except Exception as e:
                    account_results.append({"error": str(e)})
                    errors.append(f"{platform}/{account.account_name}: {e}")

            results[platform][account.account_name] = account_results

    item.upload_results = results
    item.status = "partial" if errors else "completed"
    item.completed_at = datetime.utcnow()
    db.session.commit()

    _telegram_notify(
        f"<b>Content Distributor</b>\n"
        f"Job #{item.id} ({item.niche.name}) — <b>{item.status.upper()}</b>\n"
        f"Platforms: {', '.join(item.platforms)}\n"
        + (f"Errors: {len(errors)}" if errors else "All uploads succeeded")
    )

    return jsonify(item.to_dict())


@app.route("/api/content/status/<int:content_id>", methods=["GET"])
def content_status(content_id):
    item = ContentQueue.query.get_or_404(content_id)
    return jsonify(item.to_dict())


# ---------------------------------------------------------------------------
# Account management
# ---------------------------------------------------------------------------

@app.route("/api/accounts/connect", methods=["POST"])
def connect_account():
    data = request.get_json(force=True)
    niche_name = data.get("niche")
    platform = data.get("platform")
    account_name = data.get("account_name")
    account_id = data.get("account_id", "")
    credentials = data.get("credentials", {})

    if not all([niche_name, platform, account_name]):
        return _error("niche, platform, and account_name are required")

    if platform not in ("youtube", "instagram", "tiktok"):
        return _error("platform must be youtube, instagram, or tiktok")

    niche = Niche.query.filter_by(name=niche_name).first()
    if not niche:
        return _error(f"Niche '{niche_name}' not found", 404)

    account = SocialAccount(
        niche_id=niche.id,
        platform=platform,
        account_name=account_name,
        account_id=account_id,
    )
    account.set_credentials(credentials)
    db.session.add(account)
    db.session.commit()

    return jsonify(account.to_dict()), 201


@app.route("/api/accounts/list", methods=["GET"])
def list_accounts():
    niche_filter = request.args.get("niche")
    platform_filter = request.args.get("platform")

    query = SocialAccount.query
    if niche_filter:
        niche = Niche.query.filter_by(name=niche_filter).first()
        if niche:
            query = query.filter_by(niche_id=niche.id)
    if platform_filter:
        query = query.filter_by(platform=platform_filter)

    accounts = query.all()
    return jsonify([a.to_dict() for a in accounts])


@app.route("/api/accounts/<int:account_id>", methods=["DELETE"])
def delete_account(account_id):
    account = SocialAccount.query.get_or_404(account_id)
    db.session.delete(account)
    db.session.commit()
    return jsonify({"deleted": account_id})


@app.route("/api/accounts/update-bio", methods=["POST"])
def update_bio():
    """Update bio/description for all accounts in a niche, or a single account.
    Body: { niche OR account_id, bio }
    """
    from integrations import youtube as yt_integration
    from integrations import instagram as ig_integration

    data      = request.get_json(force=True)
    bio       = data.get("bio", "").strip()
    niche_name  = data.get("niche", "").lower().strip()
    account_id  = data.get("account_id")

    if not bio:
        return _error("bio is required")

    if account_id:
        accounts = SocialAccount.query.filter_by(id=account_id, is_active=True).all()
    elif niche_name:
        niche    = Niche.query.filter_by(name=niche_name).first()
        accounts = SocialAccount.query.filter_by(niche_id=niche.id, is_active=True).all() if niche else []
    else:
        accounts = SocialAccount.query.filter_by(is_active=True).all()

    results = []
    for account in accounts:
        creds = account.get_credentials()
        try:
            if account.platform == "youtube":
                r = yt_integration.update_channel_description(creds, account.account_id, bio)
            elif account.platform == "instagram":
                r = ig_integration.update_bio(creds, bio)
            else:
                r = {"skipped": True}
            results.append({"account": account.account_name, "platform": account.platform, "result": r})
        except Exception as e:
            results.append({"account": account.account_name, "platform": account.platform, "error": str(e)})

    return jsonify(results)


@app.route("/api/accounts/update-banner", methods=["POST"])
def update_banner():
    """Update YouTube channel banner for all accounts in a niche, or a single account.
    Body: { niche OR account_id, banner_url }
    """
    from integrations import youtube as yt_integration

    data       = request.get_json(force=True)
    banner_url = data.get("banner_url", "").strip()
    niche_name = data.get("niche", "").lower().strip()
    account_id = data.get("account_id")

    if not banner_url:
        return _error("banner_url is required")

    if account_id:
        accounts = SocialAccount.query.filter_by(id=account_id, is_active=True, platform="youtube").all()
    elif niche_name:
        niche    = Niche.query.filter_by(name=niche_name).first()
        accounts = SocialAccount.query.filter_by(niche_id=niche.id, is_active=True, platform="youtube").all() if niche else []
    else:
        accounts = SocialAccount.query.filter_by(is_active=True, platform="youtube").all()

    results = []
    for account in accounts:
        creds = account.get_credentials()
        try:
            r = yt_integration.update_channel_banner(creds, banner_url)
            results.append({"account": account.account_name, "result": r})
        except Exception as e:
            results.append({"account": account.account_name, "error": str(e)})

    return jsonify(results)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.route("/api/dashboard/stats", methods=["GET"])
def dashboard_stats():
    total_content = ContentQueue.query.count()
    by_status = db.session.query(ContentQueue.status, db.func.count()).group_by(ContentQueue.status).all()
    niches = Niche.query.filter_by(is_active=True).all()

    return jsonify({
        "total_content": total_content,
        "by_status": {s: c for s, c in by_status},
        "niches": [n.to_dict() for n in niches],
        "total_accounts": SocialAccount.query.filter_by(is_active=True).count(),
    })


# ---------------------------------------------------------------------------
# Niche management
# ---------------------------------------------------------------------------

@app.route("/api/niches", methods=["GET"])
def list_niches():
    return jsonify([n.to_dict() for n in Niche.query.filter_by(is_active=True).all()])


@app.route("/api/niches", methods=["POST"])
def create_niche():
    data = request.get_json(force=True)
    name = data.get("name", "").lower().strip()
    display_name = data.get("display_name", name.title())

    if not name:
        return _error("name is required")
    if Niche.query.filter_by(name=name).first():
        return _error(f"Niche '{name}' already exists")

    niche = Niche(name=name, display_name=display_name)
    db.session.add(niche)
    db.session.commit()
    return jsonify(niche.to_dict()), 201


# ---------------------------------------------------------------------------
# YouTube OAuth  (browser-based, saves encrypted tokens per channel)
# ---------------------------------------------------------------------------

def _yt_flow(state=None):
    """Build a google_auth_oauthlib Flow from env config."""
    client_config = {
        "web": {
            "client_id": Config.YOUTUBE_CLIENT_ID,
            "client_secret": Config.YOUTUBE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [Config.YOUTUBE_REDIRECT_URI],
        }
    }
    flow = Flow.from_client_config(
        client_config,
        scopes=["https://www.googleapis.com/auth/youtube"],
        redirect_uri=Config.YOUTUBE_REDIRECT_URI,
        state=state,
    )
    return flow


import os as _os
_os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")


@app.route("/auth/youtube/connect")
def youtube_auth_start():
    """
    Opens Google OAuth. After login, shows ALL channels on the account
    so they can be assigned to niches in one step.
    Just open: http://localhost:5000/auth/youtube/connect
    """
    flow = _yt_flow()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return redirect(auth_url)


@app.route("/auth/youtube/callback")
def youtube_auth_callback():
    """Google redirects here after the user grants access."""
    state = request.args.get("state", "")
    code = request.args.get("code")
    error = request.args.get("error")

    if error:
        return f"<h2>OAuth error: {error}</h2>", 400
    if not code:
        return "<h2>No code returned from Google.</h2>", 400

    # Exchange code for tokens
    flow = _yt_flow(state=state)
    flow.fetch_token(code=code)
    creds = flow.credentials

    # Store credentials temporarily in the session so the assign step can use them
    from flask import session
    session["yt_creds"] = {
        "access_token": creds.token,
        "refresh_token": creds.refresh_token,
        "client_id": Config.YOUTUBE_CLIENT_ID,
        "client_secret": Config.YOUTUBE_CLIENT_SECRET,
    }

    # Fetch ALL channels this Google account owns or manages
    channels = []
    try:
        from googleapiclient.discovery import build
        svc = build("youtube", "v3", credentials=creds)
        # mine=True gets the default channel
        r1 = svc.channels().list(part="id,snippet", mine=True).execute()
        for ch in r1.get("items", []):
            channels.append({"id": ch["id"], "title": ch["snippet"]["title"],
                             "thumb": ch["snippet"].get("thumbnails", {}).get("default", {}).get("url", "")})
        # managedByMe=True picks up brand accounts on the same Google account
        try:
            r2 = svc.channels().list(part="id,snippet", managedByMe=True).execute()
            existing_ids = {c["id"] for c in channels}
            for ch in r2.get("items", []):
                if ch["id"] not in existing_ids:
                    channels.append({"id": ch["id"], "title": ch["snippet"]["title"],
                                     "thumb": ch["snippet"].get("thumbnails", {}).get("default", {}).get("url", "")})
        except Exception:
            pass  # managedByMe needs content-owner scope — silently skip if unavailable
    except Exception as e:
        return f"<h2>Could not fetch channels: {e}</h2>", 500

    niches = Niche.query.filter_by(is_active=True).all()

    return render_template_string(CHANNEL_PICKER_HTML,
                                  channels=channels, niches=niches)


CHANNEL_PICKER_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"><title>Assign YouTube Channels</title>
  <style>
    * { box-sizing: border-box; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           background: #0f172a; color: #e2e8f0; margin: 0; padding: 40px 24px; }
    h1 { color: #f8fafc; margin-bottom: 4px; }
    .sub { color: #94a3b8; margin-bottom: 32px; font-size: 14px; }
    .channel-row { display: flex; align-items: center; gap: 16px; background: #1e293b;
                   border: 1px solid #334155; border-radius: 10px; padding: 16px; margin-bottom: 12px; }
    .thumb { width: 48px; height: 48px; border-radius: 50%; object-fit: cover; background: #334155; }
    .channel-name { font-weight: 600; font-size: 15px; flex: 1; }
    select { background: #0f172a; color: #f1f5f9; border: 1px solid #475569;
             border-radius: 8px; padding: 8px 12px; font-size: 14px; min-width: 180px; }
    button { background: #2563eb; color: white; border: none; padding: 14px 32px;
             border-radius: 8px; cursor: pointer; font-size: 15px; font-weight: 600;
             margin-top: 24px; width: 100%; }
    button:hover { background: #1d4ed8; }
    .note { font-size: 13px; color: #64748b; margin-top: 8px; }
  </style>
</head>
<body>
  <h1>Assign YouTube Channels to Niches</h1>
  <p class="sub">We found {{ channels|length }} channel(s) on your Google account. Assign each one to a niche.</p>

  <form action="/auth/youtube/assign" method="post">
    {% for ch in channels %}
    <div class="channel-row">
      {% if ch.thumb %}
        <img class="thumb" src="{{ ch.thumb }}" alt="">
      {% else %}
        <div class="thumb"></div>
      {% endif %}
      <div class="channel-name">{{ ch.title }}</div>
      <input type="hidden" name="channel_id_{{ loop.index }}" value="{{ ch.id }}">
      <input type="hidden" name="channel_title_{{ loop.index }}" value="{{ ch.title }}">
      <select name="niche_{{ loop.index }}">
        <option value="">— skip —</option>
        {% for n in niches %}
          <option value="{{ n.name }}">{{ n.display_name }}</option>
        {% endfor %}
      </select>
    </div>
    {% endfor %}
    <input type="hidden" name="count" value="{{ channels|length }}">
    <button type="submit">Save assignments &rarr;</button>
    <p class="note">Channels set to "skip" won't be saved. You can come back and reassign anytime.</p>
  </form>
</body>
</html>
"""


@app.route("/auth/youtube/assign", methods=["POST"])
def youtube_assign_channels():
    """Save channel→niche assignments submitted from the picker page."""
    from flask import session
    creds = session.get("yt_creds")
    if not creds:
        return "<h2>Session expired. Please <a href='/auth/youtube/connect'>reconnect</a>.</h2>", 400

    count = int(request.form.get("count", 0))
    saved = []

    for i in range(1, count + 1):
        channel_id = request.form.get(f"channel_id_{i}", "")
        channel_title = request.form.get(f"channel_title_{i}", "")
        niche_name = request.form.get(f"niche_{i}", "")

        if not niche_name or not channel_id:
            continue

        niche = Niche.query.filter_by(name=niche_name, is_active=True).first()
        if not niche:
            continue

        account = SocialAccount.query.filter_by(
            niche_id=niche.id, platform="youtube", account_id=channel_id
        ).first()
        if not account:
            account = SocialAccount(niche_id=niche.id, platform="youtube",
                                    account_name=channel_title, account_id=channel_id)
            db.session.add(account)
        else:
            account.account_name = channel_title

        account.is_active = True
        account.needs_reauth = False
        account.set_credentials(creds)
        saved.append(f"{channel_title} → {niche_name}")

    db.session.commit()
    session.pop("yt_creds", None)

    return render_template_string("""
    <!doctype html><html><head><title>Channels Saved</title>
    <style>body{font-family:sans-serif;max-width:520px;margin:80px auto;text-align:center;background:#0f172a;color:#e2e8f0;}
    .card{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:32px;}
    h2{color:#34d399;} li{text-align:left;margin:8px 0;font-size:15px;} a{color:#60a5fa;}</style></head>
    <body><div class="card">
    <h2>&#10003; Channels connected</h2>
    <ul>{% for s in saved %}<li>{{ s }}</li>{% endfor %}</ul>
    <p><a href="/">Back to dashboard</a></p>
    </div></body></html>
    """, saved=saved)


# ---------------------------------------------------------------------------
# Instagram OAuth  (browser-based, via Meta/Facebook Login)
# ---------------------------------------------------------------------------

_IG_AUTH  = "https://www.instagram.com/oauth/authorize"
_IG_TOKEN = "https://api.instagram.com/oauth/access_token"
_IG_GRAPH = "https://graph.instagram.com"


@app.route("/auth/instagram/connect")
def instagram_auth_start():
    state = secrets.token_urlsafe(16)
    session["ig_state"] = state
    params = urlencode({
        "client_id":     Config.INSTAGRAM_APP_ID,
        "redirect_uri":  Config.INSTAGRAM_REDIRECT_URI,
        "scope":         "instagram_business_basic,instagram_business_content_publish",
        "response_type": "code",
        "state":         state,
    })
    return redirect(f"{_IG_AUTH}?{params}")


@app.route("/auth/instagram/callback")
def instagram_auth_callback():
    error = request.args.get("error")
    if error:
        return f"<h2>OAuth error: {request.args.get('error_description', error)}</h2>", 400

    code  = request.args.get("code")
    state = request.args.get("state")

    if not code:
        return "<h2>No code returned from Instagram.</h2>", 400
    if state != session.get("ig_state"):
        return "<h2>State mismatch — please try again.</h2>", 400

    # Exchange code → short-lived token
    r = http_requests.post(_IG_TOKEN, data={
        "client_id":     Config.INSTAGRAM_APP_ID,
        "client_secret": Config.INSTAGRAM_APP_SECRET,
        "grant_type":    "authorization_code",
        "redirect_uri":  Config.INSTAGRAM_REDIRECT_URI,
        "code":          code,
    })
    if not r.ok:
        return f"<h2>Token exchange failed: {r.text}</h2>", 400
    short_data  = r.json()
    short_token = short_data.get("access_token")
    ig_user_id  = str(short_data.get("user_id", ""))

    # Upgrade to long-lived token (~60 days)
    r2 = http_requests.get(f"{_IG_GRAPH}/access_token", params={
        "grant_type":       "ig_exchange_token",
        "client_id":        Config.INSTAGRAM_APP_ID,
        "client_secret":    Config.INSTAGRAM_APP_SECRET,
        "access_token":     short_token,
    })
    if not r2.ok:
        return f"<h2>Long-lived token exchange failed: {r2.text}</h2>", 400
    long_token = r2.json().get("access_token")

    # Get username
    me_r = http_requests.get(f"{_IG_GRAPH}/me", params={
        "fields":       "id,username",
        "access_token": long_token,
    })
    username = me_r.json().get("username", f"ig_{ig_user_id}") if me_r.ok else f"ig_{ig_user_id}"

    ig_accounts = [{"id": ig_user_id, "username": username}]

    if not ig_accounts:
        return render_template_string("""
        <!doctype html><html><head><title>No IG Accounts</title>
        <style>body{font-family:sans-serif;max-width:520px;margin:80px auto;text-align:center;background:#0f172a;color:#e2e8f0;}
        .card{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:32px;}
        h2{color:#f87171;}p{color:#94a3b8;}a{color:#60a5fa;}</style></head>
        <body><div class="card">
        <h2>No Instagram Business Accounts Found</h2>
        <p>Make sure your Instagram accounts are set to <strong>Business</strong> or <strong>Creator</strong>
        and are connected to a Facebook Page in Meta Business Suite.</p>
        <p><a href="/auth/instagram/connect">Try again</a> &nbsp;·&nbsp; <a href="/">Dashboard</a></p>
        </div></body></html>"""), 200

    session["ig_creds"] = {"access_token": long_token}
    niches = Niche.query.filter_by(is_active=True).all()
    return render_template_string(INSTAGRAM_PICKER_HTML, accounts=ig_accounts, niches=niches)


INSTAGRAM_PICKER_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"><title>Assign Instagram Accounts</title>
  <style>
    * { box-sizing: border-box; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           background: #0f172a; color: #e2e8f0; margin: 0; padding: 40px 24px; }
    h1 { color: #f8fafc; margin-bottom: 4px; }
    .sub { color: #94a3b8; margin-bottom: 32px; font-size: 14px; }
    .row { display: flex; align-items: center; gap: 16px; background: #1e293b;
           border: 1px solid #334155; border-radius: 10px; padding: 16px; margin-bottom: 12px; }
    .ig-icon { width: 48px; height: 48px; border-radius: 50%;
               background: linear-gradient(45deg,#f09433,#e6683c,#dc2743,#cc2366,#bc1888);
               display:flex;align-items:center;justify-content:center;font-size:22px;flex-shrink:0; }
    .name { font-weight: 600; font-size: 15px; flex: 1; }
    select { background: #0f172a; color: #f1f5f9; border: 1px solid #475569;
             border-radius: 8px; padding: 8px 12px; font-size: 14px; min-width: 180px; }
    button { background: #2563eb; color: white; border: none; padding: 14px 32px;
             border-radius: 8px; cursor: pointer; font-size: 15px; font-weight: 600;
             margin-top: 24px; width: 100%; }
    button:hover { background: #1d4ed8; }
    .note { font-size: 13px; color: #64748b; margin-top: 8px; }
  </style>
</head>
<body>
  <h1>Assign Instagram Accounts to Niches</h1>
  <p class="sub">Found {{ accounts|length }} Instagram Business account(s). Assign each to a niche.</p>
  <form action="/auth/instagram/assign" method="post">
    {% for acc in accounts %}
    <div class="row">
      <div class="ig-icon">📷</div>
      <div class="name">@{{ acc.username }}</div>
      <input type="hidden" name="ig_id_{{ loop.index }}" value="{{ acc.id }}">
      <input type="hidden" name="ig_username_{{ loop.index }}" value="{{ acc.username }}">
      <select name="niche_{{ loop.index }}">
        <option value="">— skip —</option>
        {% for n in niches %}
          <option value="{{ n.name }}">{{ n.display_name }}</option>
        {% endfor %}
      </select>
    </div>
    {% endfor %}
    <input type="hidden" name="count" value="{{ accounts|length }}">
    <button type="submit">Save assignments &rarr;</button>
    <p class="note">Tokens expire in ~60 days — you'll see a reconnect prompt when they do.</p>
  </form>
</body>
</html>
"""


@app.route("/auth/instagram/assign", methods=["POST"])
def instagram_assign_accounts():
    creds_base = session.get("ig_creds")
    if not creds_base:
        return "<h2>Session expired. Please <a href='/auth/instagram/connect'>reconnect</a>.</h2>", 400

    count  = int(request.form.get("count", 0))
    saved  = []

    for i in range(1, count + 1):
        ig_id       = request.form.get(f"ig_id_{i}", "")
        ig_username = request.form.get(f"ig_username_{i}", "")
        niche_name  = request.form.get(f"niche_{i}", "")

        if not niche_name or not ig_id:
            continue

        niche = Niche.query.filter_by(name=niche_name, is_active=True).first()
        if not niche:
            continue

        account = SocialAccount.query.filter_by(
            niche_id=niche.id, platform="instagram", account_id=ig_id
        ).first()
        if not account:
            account = SocialAccount(niche_id=niche.id, platform="instagram",
                                    account_name=ig_username, account_id=ig_id)
            db.session.add(account)
        else:
            account.account_name = ig_username

        from datetime import timedelta
        account.is_active        = True
        account.needs_reauth     = False
        account.token_expires_at = datetime.utcnow() + timedelta(days=58)  # refresh before 60-day expiry
        account.set_credentials({"access_token": creds_base["access_token"], "instagram_user_id": ig_id})
        saved.append(f"@{ig_username} → {niche_name}")

    db.session.commit()
    session.pop("ig_creds", None)

    return render_template_string("""
    <!doctype html><html><head><title>Instagram Connected</title>
    <style>body{font-family:sans-serif;max-width:520px;margin:80px auto;text-align:center;background:#0f172a;color:#e2e8f0;}
    .card{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:32px;}
    h2{color:#34d399;}li{text-align:left;margin:8px 0;font-size:15px;}a{color:#60a5fa;}
    p{color:#94a3b8;font-size:13px;}</style></head>
    <body><div class="card">
    <h2>&#10003; Instagram accounts connected</h2>
    <ul>{% for s in saved %}<li>{{ s }}</li>{% endfor %}</ul>
    <p>Tokens expire in ~60 days. You'll need to reconnect after that.</p>
    <p><a href="/">Back to dashboard</a></p>
    </div></body></html>
    """, saved=saved)


# ---------------------------------------------------------------------------
# Token auto-refresh
# ---------------------------------------------------------------------------

_IG_GRAPH = "https://graph.instagram.com"

def refresh_instagram_tokens():
    """
    Refresh all Instagram long-lived tokens that expire within 30 days.
    Instagram tokens last 60 days and can be refreshed anytime after day 1.
    Called by the scheduler weekly.
    """
    with app.app_context():
        from datetime import timedelta
        cutoff   = datetime.utcnow() + timedelta(days=30)
        accounts = SocialAccount.query.filter(
            SocialAccount.platform    == "instagram",
            SocialAccount.is_active   == True,
            SocialAccount.needs_reauth == False,
        ).filter(
            db.or_(
                SocialAccount.token_expires_at == None,
                SocialAccount.token_expires_at <= cutoff,
            )
        ).all()

        refreshed = 0
        for account in accounts:
            try:
                creds = account.get_credentials()
                token = creds.get("access_token")
                if not token:
                    continue
                r = http_requests.get(
                    f"{_IG_GRAPH}/refresh_access_token",
                    params={"grant_type": "ig_refresh_token", "access_token": token},
                    timeout=15,
                )
                if r.ok:
                    new_token = r.json().get("access_token", token)
                    creds["access_token"]    = new_token
                    account.set_credentials(creds)
                    account.token_expires_at = datetime.utcnow() + timedelta(days=58)
                    refreshed += 1
                    print(f"[tokens] Refreshed Instagram token for @{account.account_name}")
                else:
                    account.needs_reauth = True
                    _telegram_notify(f"⚠️ Instagram token expired for @{account.account_name} — needs reconnect")
                    print(f"[tokens] Refresh failed for @{account.account_name}: {r.text}")
            except Exception as e:
                print(f"[tokens] Error refreshing @{account.account_name}: {e}")

        db.session.commit()
        print(f"[tokens] Instagram refresh complete — {refreshed}/{len(accounts)} refreshed")


def refresh_youtube_tokens():
    """
    Touch all YouTube accounts to trigger google-auth refresh and save updated tokens back to DB.
    Called by the scheduler weekly.
    """
    with app.app_context():
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        accounts = SocialAccount.query.filter_by(
            platform="youtube", is_active=True, needs_reauth=False
        ).all()

        for account in accounts:
            try:
                creds_dict = account.get_credentials()
                creds = Credentials(
                    token         = creds_dict.get("access_token"),
                    refresh_token = creds_dict.get("refresh_token"),
                    token_uri     = "https://oauth2.googleapis.com/token",
                    client_id     = creds_dict.get("client_id"),
                    client_secret = creds_dict.get("client_secret"),
                )
                if creds.refresh_token:
                    creds.refresh(Request())
                    creds_dict["access_token"] = creds.token
                    account.set_credentials(creds_dict)
                    print(f"[tokens] Refreshed YouTube token for {account.account_name}")
            except Exception as e:
                account.needs_reauth = True
                _telegram_notify(f"⚠️ YouTube token expired for {account.account_name} — needs reconnect")
                print(f"[tokens] YouTube refresh failed for {account.account_name}: {e}")

        db.session.commit()


# ---------------------------------------------------------------------------
# Quick upload — the endpoint Claude calls on your behalf
# ---------------------------------------------------------------------------

@app.route("/api/upload/quick", methods=["POST"])
def quick_upload():
    """
    Single endpoint for natural-language-driven uploads.
    Claude calls this when you say things like:
      "Upload [url] to my Trading YouTube channel"
      "Post this to all fitness accounts"

    Body params:
      video_url   (required)
      title       (required)
      channel     — account_name substring OR niche name (case-insensitive)
      niche       — niche slug (trading / fitness / crime / sports / anatomy)
      platform    — youtube / instagram / tiktok (omit = all)
      description — optional
      clip        — true/false, default true (run through OpusClip first)
      wait        — true/false, default false (if true, block until upload done)
    """
    data = request.get_json(force=True)
    video_url        = data.get("video_url")
    title            = data.get("title", "")
    description      = data.get("description", "")
    channel_hint     = (data.get("channel") or "").lower().strip()
    niche_hint       = (data.get("niche") or "").lower().strip()
    platform_filter  = (data.get("platform") or "").lower().strip()
    should_clip      = bool(data.get("use_opusclip", data.get("clip", False)))
    wait             = data.get("wait", False)
    content_type     = (data.get("content_type") or "general").strip()
    caption_variants = data.get("caption_variants")  # optional list of caption strings

    if not video_url:
        return _error("video_url is required")

    # Resolve which accounts to target
    query = SocialAccount.query.filter_by(is_active=True)

    if channel_hint:
        # Match by account_name substring first, then fall back to niche name
        accounts_by_name = SocialAccount.query.filter(
            SocialAccount.is_active == True,
            SocialAccount.account_name.ilike(f"%{channel_hint}%")
        ).all()
        if accounts_by_name:
            accounts = accounts_by_name
        else:
            # Try as a niche slug
            niche = Niche.query.filter(Niche.name.ilike(f"%{channel_hint}%")).first()
            accounts = SocialAccount.query.filter_by(niche_id=niche.id, is_active=True).all() if niche else []
    elif niche_hint:
        niche = Niche.query.filter(Niche.name.ilike(f"%{niche_hint}%")).first()
        accounts = SocialAccount.query.filter_by(niche_id=niche.id, is_active=True).all() if niche else []
    else:
        accounts = SocialAccount.query.filter_by(is_active=True).all()

    if platform_filter:
        accounts = [a for a in accounts if a.platform == platform_filter]

    if not accounts:
        return _error(
            f"No active accounts found matching channel='{channel_hint}' niche='{niche_hint}' platform='{platform_filter}'. "
            "Connect a channel first via /auth/youtube"
        ), 404

    platforms = list({a.platform for a in accounts})
    niche_id = accounts[0].niche_id  # use first match's niche for the queue entry

    # Create the content queue entry
    item = ContentQueue(
        niche_id=niche_id,
        video_url=video_url,
        title=title,
        description=description,
        platforms=platforms,
        use_opusclip=should_clip,
        status="pending",
        upload_results={},
        clipped_urls=[],
    )
    db.session.add(item)
    db.session.commit()

    # Build A/B test and per-account caption map if variants provided
    ab_test_id     = None
    account_caps   = {}   # {account.id: caption}
    account_vars   = {}   # {account.id: variant_label}
    variant_labels = list("ABCDEFGH")

    if caption_variants and len(caption_variants) >= 2:
        niche_label = niche_hint or (accounts[0].niche.name if accounts else "unknown")
        ab = ABTest(
            niche=niche_label,
            content_type=content_type,
            variants={variant_labels[i]: v for i, v in enumerate(caption_variants)},
            status="running",
        )
        db.session.add(ab)
        db.session.flush()
        ab_test_id = ab.id
        for idx, account in enumerate(accounts):
            label = variant_labels[idx % len(caption_variants)]
            account_caps[account.id] = caption_variants[ord(label) - ord("A")]
            account_vars[account.id] = label
        db.session.commit()

    if not wait:
        import threading
        threading.Thread(
            target=_run_job,
            args=(item.id, accounts, should_clip),
            kwargs=dict(content_type=content_type, ab_test_id=ab_test_id,
                        account_caps=account_caps, account_vars=account_vars),
            daemon=True,
        ).start()
        return jsonify({
            "content_id": item.id,
            "status": "queued",
            "targets": [{"account": a.account_name, "platform": a.platform} for a in accounts],
            "poll_url": f"/api/content/status/{item.id}",
            "ab_test_id": ab_test_id,
        }), 202

    # wait=true: block until done (useful for testing)
    _run_job(item.id, accounts, should_clip,
             content_type=content_type, ab_test_id=ab_test_id,
             account_caps=account_caps, account_vars=account_vars)
    db.session.refresh(item)
    return jsonify(item.to_dict())


def _run_job(content_id: int, accounts: list, should_clip: bool,
             content_type: str = "general", ab_test_id: int = None,
             account_caps: dict = None, account_vars: dict = None,
             voice_id: str = None, voice_name: str = None):
    """Execute OpusClip + uploads for a content item. Runs in a thread or inline."""
    with app.app_context():
        item   = ContentQueue.query.get(content_id)
        errors = []

        # Step 1: OpusClip
        clip_urls = [item.video_url]
        if should_clip and Config.OPUSCLIP_API_KEY:
            try:
                item.status = "clipping"
                db.session.commit()
                opus = OpusClipClient()
                job  = opus.create_clip_job(item.video_url, item.title)
                item.opusclip_job_id = job.get("job_id") or job.get("id")
                db.session.commit()

                for _ in range(60):
                    time.sleep(10)
                    status_data = opus.get_job_status(item.opusclip_job_id)
                    if status_data.get("status") == "completed":
                        clip_urls = [c["url"] for c in status_data.get("clips", [])]
                        item.clipped_urls = clip_urls
                        db.session.commit()
                        break
                    if status_data.get("status") == "failed":
                        raise RuntimeError("OpusClip job failed")
            except Exception as e:
                errors.append(f"opusclip: {e}")

        # Step 2: Upload to each account
        item.status = "uploading"
        db.session.commit()
        results = {}

        niche_name = item.niche.name if item.niche else ""
        base_description = _inject_affiliate_links(item.description or "", niche_name, content_id)

        for account in accounts:
            creds   = account.get_credentials()
            caption = (account_caps or {}).get(account.id, base_description) or base_description
            # For A/B tests, still inject affiliate links into the variant caption
            if account_caps and account.id in account_caps:
                caption = _inject_affiliate_links(account_caps[account.id], niche_name, content_id)
            variant = (account_vars or {}).get(account.id)
            account_results = []

            is_short      = content_type not in ("longform", "lumi_full")
            made_for_kids = content_type in ("lumi_full", "lumi_short")

            for clip_url in clip_urls:
                try:
                    if account.platform == "youtube":
                        r = yt_integration.upload_video(
                            creds, clip_url, item.title, caption,
                            niche         = niche_name,
                            is_short      = is_short,
                            made_for_kids = made_for_kids,
                        )
                    elif account.platform == "instagram":
                        r = ig_integration.upload_reel(creds, clip_url, caption or item.title)
                    elif account.platform == "tiktok":
                        r = tt_integration.upload_video(creds, clip_url, item.title, caption)
                    else:
                        r = {"error": f"Unknown platform: {account.platform}"}
                    account_results.append(r)

                    # Track this post for metrics collection
                    post_id = r.get("video_id") or r.get("media_id")
                    if post_id and "error" not in r:
                        pm = PostMetrics(
                            content_id   = content_id,
                            account_id   = account.id,
                            niche        = item.niche.name if item.niche else None,
                            platform     = account.platform,
                            post_id      = post_id,
                            caption      = caption,
                            content_type = content_type,
                            ab_test_id   = ab_test_id,
                            ab_variant   = variant,
                            voice_id     = voice_id,
                            voice_name   = voice_name,
                        )
                        db.session.add(pm)

                except Exception as e:
                    account_results.append({"error": str(e)})
                    errors.append(f"{account.platform}/{account.account_name}: {e}")

            results.setdefault(account.platform, {})[account.account_name] = account_results

        item.upload_results = results
        item.status    = "partial" if errors else "completed"
        item.completed_at = datetime.utcnow()
        db.session.commit()

        _telegram_notify(
            f"<b>Content Distributor</b>\n"
            f"Job #{item.id} ({item.niche.name}) — <b>{item.status.upper()}</b>\n"
            f"Accounts: {', '.join(a.account_name for a in accounts)}\n"
            + (f"Errors: {len(errors)}" if errors else "All uploads succeeded")
        )


# ---------------------------------------------------------------------------
# HTML Dashboard
# ---------------------------------------------------------------------------

DASHBOARD_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Content Distributor</title>
  <style>
    * { box-sizing: border-box; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           background: #0f172a; color: #e2e8f0; margin: 0; padding: 24px; }
    h1 { color: #f8fafc; margin-bottom: 4px; }
    .sub { color: #94a3b8; margin-bottom: 32px; font-size: 14px; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; margin-bottom: 32px; }
    .card { background: #1e293b; border-radius: 12px; padding: 20px; border: 1px solid #334155; }
    .card h3 { margin: 0 0 12px; color: #f1f5f9; font-size: 15px; text-transform: uppercase; letter-spacing: .5px; }
    .stat { font-size: 36px; font-weight: 700; color: #38bdf8; }
    .account { display: flex; align-items: center; gap: 10px; padding: 8px 0;
               border-bottom: 1px solid #334155; font-size: 14px; }
    .account:last-child { border-bottom: none; }
    .badge { font-size: 11px; padding: 2px 8px; border-radius: 9999px; font-weight: 600; }
    .yt { background: #fee2e2; color: #991b1b; }
    .ig { background: #fce7f3; color: #9d174d; }
    .tt { background: #f0fdf4; color: #166534; }
    form { margin-top: 16px; }
    label { font-size: 13px; color: #94a3b8; display: block; margin-bottom: 4px; }
    input, select { width: 100%; padding: 8px 10px; border-radius: 8px;
                    border: 1px solid #475569; background: #0f172a; color: #f1f5f9;
                    font-size: 14px; margin-bottom: 10px; }
    button { background: #2563eb; color: white; border: none; padding: 10px 20px;
             border-radius: 8px; cursor: pointer; font-size: 14px; font-weight: 600; width: 100%; }
    button:hover { background: #1d4ed8; }
    .empty { color: #64748b; font-size: 14px; }
    .niche-label { font-size: 11px; color: #64748b; }
  </style>
</head>
<body>
  <nav style="display:flex;gap:16px;margin-bottom:20px;">
    <a href="/" style="color:#60a5fa;text-decoration:none;font-weight:600;font-size:14px;
       border-bottom:2px solid #38bdf8;padding-bottom:2px;">Dashboard</a>
    <a href="/insights" style="color:#60a5fa;text-decoration:none;font-weight:600;font-size:14px;">Insights</a>
  </nav>
  <h1>Content Distributor</h1>
  <p class="sub">Multi-account video distribution across YouTube, Instagram & TikTok</p>

  <div class="grid">
    <div class="card">
      <h3>Total Jobs</h3>
      <div class="stat">{{ stats.total_content }}</div>
    </div>
    <div class="card">
      <h3>Connected Accounts</h3>
      <div class="stat">{{ stats.total_accounts }}</div>
    </div>
    {% for status, count in stats.by_status.items() %}
    <div class="card">
      <h3>{{ status }}</h3>
      <div class="stat">{{ count }}</div>
    </div>
    {% endfor %}
  </div>

  <div class="grid">
    <div class="card" style="grid-column: span 2">
      <h3>Connected YouTube Channels</h3>
      {% if accounts %}
        {% for a in accounts %}
        <div class="account">
          <span class="badge yt">YT</span>
          <div>
            <div>{{ a.account_name }}</div>
            <div class="niche-label">{{ a.niche_name }} &nbsp;·&nbsp; {{ a.account_id or 'no channel ID' }}</div>
          </div>
        </div>
        {% endfor %}
      {% else %}
        <p class="empty">No channels connected yet. Use the form below to add one.</p>
      {% endif %}
    </div>

    <div class="card">
      <h3>Connect YouTube Channels</h3>
      <p style="font-size:13px;color:#94a3b8;margin-bottom:16px;">
        Signs in once and shows all your channels so you can assign them to niches in one step.
      </p>
      <a href="/auth/youtube/connect">
        <button style="margin-top:0">Connect with Google &rarr;</button>
      </a>
    </div>
  </div>

  <div class="grid">
    <div class="card" style="grid-column: span 2">
      <h3>Connected Instagram Accounts</h3>
      {% if ig_accounts %}
        {% for a in ig_accounts %}
        <div class="account">
          <span class="badge ig">IG</span>
          <div>
            <div>{{ a.account_name }}</div>
            <div class="niche-label">{{ a.niche_name }} &nbsp;·&nbsp; {{ a.account_id or 'no account ID' }}</div>
          </div>
        </div>
        {% endfor %}
      {% else %}
        <p class="empty">No Instagram accounts connected yet.</p>
      {% endif %}
    </div>

    <div class="card">
      <h3>Connect Instagram Accounts</h3>
      <p style="font-size:13px;color:#94a3b8;margin-bottom:16px;">
        Requires Business/Creator accounts connected to a Facebook Page.
      </p>
      <a href="/auth/instagram/connect">
        <button style="margin-top:0;background:#9333ea;">Connect with Meta &rarr;</button>
      </a>
    </div>
  </div>

  <div class="card">
    <h3>Recent Jobs</h3>
    {% if jobs %}
    <table style="width:100%;font-size:13px;border-collapse:collapse;">
      <tr style="color:#64748b;text-align:left;">
        <th style="padding:6px 8px">#</th><th>Niche</th><th>Title</th>
        <th>Platforms</th><th>Status</th><th>Created</th>
      </tr>
      {% for j in jobs %}
      <tr style="border-top:1px solid #334155">
        <td style="padding:6px 8px">{{ j.id }}</td>
        <td>{{ j.niche_name }}</td>
        <td>{{ (j.title or '')[:50] }}</td>
        <td>{{ j.platforms | join(', ') }}</td>
        <td>{{ j.status }}</td>
        <td>{{ j.created_at[:16] }}</td>
      </tr>
      {% endfor %}
    {% else %}
      <p class="empty">No jobs yet.</p>
    {% endif %}
    </table>
  </div>
</body>
</html>
"""


@app.route("/")
def dashboard():
    stats_resp  = dashboard_stats().get_json()
    yt_accounts = SocialAccount.query.filter_by(platform="youtube",    is_active=True).all()
    ig_accounts = SocialAccount.query.filter_by(platform="instagram",  is_active=True).all()
    niches      = Niche.query.filter_by(is_active=True).all()
    jobs        = ContentQueue.query.order_by(ContentQueue.id.desc()).limit(20).all()
    return render_template_string(
        DASHBOARD_HTML,
        stats=stats_resp,
        accounts=[a.to_dict() for a in yt_accounts],
        ig_accounts=[a.to_dict() for a in ig_accounts],
        niches=[n.to_dict() for n in niches],
        jobs=[j.to_dict() for j in jobs],
    )


# ---------------------------------------------------------------------------
# Metrics collection
# ---------------------------------------------------------------------------

@app.route("/api/metrics/collect", methods=["POST"])
def collect_metrics():
    """
    Pull latest stats from YouTube and Instagram for every tracked post.
    Also auto-concludes any A/B test where all posts are older than 48 hours.
    """
    from datetime import timedelta

    cutoff   = datetime.utcnow() - timedelta(hours=6)  # skip recently refreshed
    posts    = PostMetrics.query.filter(
        db.or_(PostMetrics.metrics_fetched_at == None,
               PostMetrics.metrics_fetched_at < cutoff)
    ).all()

    updated  = 0
    failed   = 0

    for pm in posts:
        try:
            account = SocialAccount.query.get(pm.account_id)
            if not account:
                continue
            creds = account.get_credentials()

            if pm.platform == "youtube":
                data = analytics_integration.fetch_youtube_metrics(creds, pm.post_id)
            elif pm.platform == "instagram":
                data = analytics_integration.fetch_instagram_metrics(creds, pm.post_id)
            else:
                continue

            if not data:
                continue

            pm.views    = data.get("views", pm.views)
            pm.likes    = data.get("likes", pm.likes)
            pm.comments = data.get("comments", pm.comments)
            pm.shares   = data.get("shares", pm.shares)
            pm.saves    = data.get("saves", pm.saves)
            pm.reach    = data.get("reach", pm.reach)
            pm.compute_engagement()
            pm.metrics_fetched_at = datetime.utcnow()
            updated += 1
        except Exception:
            failed += 1

    db.session.commit()

    # Conclude A/B tests where all posts are > 48h old
    concluded = _conclude_ab_tests()

    return jsonify({"updated": updated, "failed": failed, "ab_tests_concluded": concluded})


def _conclude_ab_tests():
    from datetime import timedelta
    concluded = 0
    running_tests = ABTest.query.filter_by(status="running").all()
    cutoff = datetime.utcnow() - timedelta(hours=48)

    for test in running_tests:
        posts = PostMetrics.query.filter_by(ab_test_id=test.id).all()
        if not posts:
            continue
        if any(p.posted_at > cutoff for p in posts):
            continue  # still too fresh

        # Group by variant and average engagement_score
        variant_scores = {}
        for p in posts:
            if not p.ab_variant:
                continue
            variant_scores.setdefault(p.ab_variant, []).append(p.engagement_score or 0)

        if len(variant_scores) < 2:
            continue

        averages = {v: sum(s) / len(s) for v, s in variant_scores.items()}
        winner   = max(averages, key=averages.get)

        test.winner       = winner
        test.status       = "concluded"
        test.concluded_at = datetime.utcnow()
        concluded += 1

    db.session.commit()
    return concluded


# ---------------------------------------------------------------------------
# Insights API + dashboard
# ---------------------------------------------------------------------------

@app.route("/api/insights", methods=["GET"])
def insights_api():
    from sqlalchemy import func

    total_posts = PostMetrics.query.count()
    avg_engagement = db.session.query(func.avg(PostMetrics.engagement_score)).scalar() or 0

    # Per-niche averages
    niche_rows = db.session.query(
        PostMetrics.niche,
        func.count(PostMetrics.id).label("posts"),
        func.avg(PostMetrics.views).label("avg_views"),
        func.avg(PostMetrics.likes).label("avg_likes"),
        func.avg(PostMetrics.engagement_score).label("avg_engagement"),
    ).group_by(PostMetrics.niche).all()

    niche_stats = [
        {
            "niche":          r.niche or "unknown",
            "posts":          r.posts,
            "avg_views":      round(r.avg_views or 0),
            "avg_likes":      round(r.avg_likes or 0),
            "avg_engagement": round(r.avg_engagement or 0, 4),
        }
        for r in niche_rows
    ]
    best_niche = max(niche_stats, key=lambda x: x["avg_engagement"])["niche"] if niche_stats else "—"

    # Top 10 posts by engagement, enriched with affiliate click counts
    top_posts_raw = (PostMetrics.query
                     .order_by(PostMetrics.engagement_score.desc())
                     .limit(10).all())

    top_posts = []
    for p in top_posts_raw:
        d = p.to_dict()
        clicks = LinkClick.query.filter_by(content_id=p.content_id).count() if p.content_id else 0
        d["affiliate_clicks"] = clicks
        d["ctr"] = round(clicks / max(p.views, 1) * 100, 2) if p.views else 0
        top_posts.append(d)

    # A/B tests
    ab_tests = ABTest.query.order_by(ABTest.started_at.desc()).limit(20).all()

    # Best content types
    type_rows = db.session.query(
        PostMetrics.content_type,
        func.avg(PostMetrics.engagement_score).label("avg_eng"),
        func.count(PostMetrics.id).label("count"),
    ).group_by(PostMetrics.content_type).all()

    content_types = sorted(
        [{"type": r.content_type, "avg_engagement": round(r.avg_eng or 0, 4), "count": r.count}
         for r in type_rows],
        key=lambda x: x["avg_engagement"], reverse=True
    )

    # Voice performance per niche
    from integrations.elevenlabs import get_voice_insights
    voice_insights = {
        niche: get_voice_insights(niche, db.session)
        for niche in [r.niche for r in niche_rows if r.niche]
    }

    return jsonify({
        "total_posts":     total_posts,
        "avg_engagement":  round(float(avg_engagement), 4),
        "best_niche":      best_niche,
        "active_ab_tests": ABTest.query.filter_by(status="running").count(),
        "niche_stats":     niche_stats,
        "top_posts":       top_posts,
        "ab_tests":        [t.to_dict() for t in ab_tests],
        "content_types":   content_types,
        "voice_insights":  voice_insights,
    })


INSIGHTS_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Insights — Content Distributor</title>
  <style>
    * { box-sizing: border-box; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           background: #0f172a; color: #e2e8f0; margin: 0; padding: 24px; }
    nav  { display:flex; gap:16px; margin-bottom:28px; }
    nav a { color:#60a5fa; text-decoration:none; font-weight:600; font-size:14px; }
    nav a.active { color:#f8fafc; border-bottom:2px solid #38bdf8; padding-bottom:2px; }
    h1   { color: #f8fafc; margin-bottom: 4px; }
    h2   { color: #cbd5e1; font-size:15px; text-transform:uppercase; letter-spacing:.5px;
           margin: 28px 0 12px; }
    .sub { color: #94a3b8; margin-bottom: 24px; font-size: 14px; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
            gap: 14px; margin-bottom: 8px; }
    .card { background: #1e293b; border-radius: 12px; padding: 18px;
            border: 1px solid #334155; }
    .card h3 { margin:0 0 8px; color:#94a3b8; font-size:12px; text-transform:uppercase;
               letter-spacing:.5px; }
    .stat { font-size: 32px; font-weight: 700; color: #38bdf8; }
    table { width:100%; border-collapse:collapse; font-size:13px;
            background:#1e293b; border-radius:10px; overflow:hidden;
            border:1px solid #334155; margin-bottom:8px; }
    th    { background:#0f172a; color:#64748b; padding:10px 12px; text-align:left;
            font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.4px; }
    td    { padding:9px 12px; border-top:1px solid #1e293b; vertical-align:top; }
    tr:hover td { background:#233047; }
    .badge { font-size:11px; padding:2px 8px; border-radius:9999px; font-weight:600;
             display:inline-block; }
    .yt   { background:#fee2e2; color:#991b1b; }
    .ig   { background:#fce7f3; color:#9d174d; }
    .win  { background:#dcfce7; color:#166534; }
    .lose { background:#fef2f2; color:#991b1b; }
    .run  { background:#fef9c3; color:#854d0e; }
    .caption { max-width:260px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    button { background:#2563eb; color:white; border:none; padding:10px 24px;
             border-radius:8px; cursor:pointer; font-size:14px; font-weight:600; }
    button:hover { background:#1d4ed8; }
    button:disabled { background:#334155; color:#64748b; cursor:default; }
    #refresh-status { font-size:13px; color:#94a3b8; margin-left:12px; }
    .empty { color:#64748b; font-size:14px; padding:16px; }
  </style>
</head>
<body>
  <nav>
    <a href="/">Dashboard</a>
    <a href="/insights" class="active">Insights</a>
  </nav>
  <h1>Insights</h1>
  <p class="sub">Performance tracking and A/B test results across all niches</p>

  <!-- Overview stats -->
  <div class="grid" id="overview">
    <div class="card"><h3>Tracked Posts</h3><div class="stat" id="total-posts">—</div></div>
    <div class="card"><h3>Avg Engagement</h3><div class="stat" id="avg-eng">—</div></div>
    <div class="card"><h3>Best Niche</h3><div class="stat" id="best-niche" style="font-size:22px">—</div></div>
    <div class="card"><h3>Active A/B Tests</h3><div class="stat" id="active-ab">—</div></div>
  </div>

  <div style="margin:20px 0 4px">
    <button id="refresh-btn" onclick="refreshMetrics()">Refresh Metrics</button>
    <span id="refresh-status"></span>
  </div>

  <!-- Top posts -->
  <h2>Top Posts by Engagement</h2>
  <table>
    <thead>
      <tr><th>#</th><th>Platform</th><th>Niche</th><th>Type</th>
          <th>Caption</th><th>Views</th><th>Likes</th><th>Eng Score</th>
          <th>Affiliate Clicks</th><th>CTR</th></tr>
    </thead>
    <tbody id="top-posts-body"><tr><td colspan="10" class="empty">Loading…</td></tr></tbody>
  </table>

  <!-- Niche breakdown -->
  <h2>Performance by Niche</h2>
  <table>
    <thead>
      <tr><th>Niche</th><th>Posts</th><th>Avg Views</th><th>Avg Likes</th><th>Avg Engagement</th></tr>
    </thead>
    <tbody id="niche-body"><tr><td colspan="5" class="empty">Loading…</td></tr></tbody>
  </table>

  <!-- Content types -->
  <h2>Content Type Performance</h2>
  <table>
    <thead><tr><th>Type</th><th>Posts</th><th>Avg Engagement</th></tr></thead>
    <tbody id="type-body"><tr><td colspan="3" class="empty">Loading…</td></tr></tbody>
  </table>

  <!-- A/B Tests -->
  <h2>A/B Tests</h2>
  <table>
    <thead>
      <tr><th>ID</th><th>Niche</th><th>Status</th><th>Winner</th>
          <th>Caption A</th><th>Caption B</th><th>Started</th></tr>
    </thead>
    <tbody id="ab-body"><tr><td colspan="7" class="empty">Loading…</td></tr></tbody>
  </table>

  <!-- Link click tracker -->
  <h2>Link Click Tracker</h2>
  <p style="font-size:13px;color:#94a3b8;margin-bottom:12px;">
    Use these tracking URLs in your bios and video descriptions instead of direct links.
    Every click is logged with its source platform.
  </p>

  <!-- Add link form -->
  <div style="background:#1e293b;border:1px solid #334155;border-radius:10px;padding:16px;margin-bottom:16px;">
    <div style="font-size:13px;font-weight:600;color:#f1f5f9;margin-bottom:12px;">Add New Link</div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr auto;gap:10px;align-items:end;">
      <div>
        <label style="font-size:11px;color:#64748b;display:block;margin-bottom:4px;">SLUG (short name)</label>
        <input id="new-slug" placeholder="e.g. amazon-fitness" style="width:100%;padding:8px;border-radius:6px;border:1px solid #475569;background:#0f172a;color:#f1f5f9;font-size:13px;">
      </div>
      <div>
        <label style="font-size:11px;color:#64748b;display:block;margin-bottom:4px;">LABEL</label>
        <input id="new-label" placeholder="Amazon Fitness" style="width:100%;padding:8px;border-radius:6px;border:1px solid #475569;background:#0f172a;color:#f1f5f9;font-size:13px;">
      </div>
      <div>
        <label style="font-size:11px;color:#64748b;display:block;margin-bottom:4px;">DESTINATION URL</label>
        <input id="new-dest" placeholder="https://..." style="width:100%;padding:8px;border-radius:6px;border:1px solid #475569;background:#0f172a;color:#f1f5f9;font-size:13px;">
      </div>
      <div>
        <label style="font-size:11px;color:#64748b;display:block;margin-bottom:4px;">NICHE</label>
        <input id="new-niche" placeholder="trading" style="width:100%;padding:8px;border-radius:6px;border:1px solid #475569;background:#0f172a;color:#f1f5f9;font-size:13px;">
      </div>
      <button onclick="addLink()" style="padding:8px 16px;white-space:nowrap">+ Add</button>
    </div>
    <div id="add-link-status" style="font-size:12px;color:#94a3b8;margin-top:8px;"></div>
  </div>

  <table>
    <thead>
      <tr><th>Slug</th><th>Label</th><th>Niche</th><th>Total Clicks</th>
          <th>YouTube</th><th>Instagram</th><th>Linktree</th><th>Direct</th>
          <th>Tracking URL (copy & paste into bios)</th></tr>
    </thead>
    <tbody id="links-body"><tr><td colspan="9" class="empty">Loading…</td></tr></tbody>
  </table>

<script>
async function load() {
  const r = await fetch('/api/insights');
  const d = await r.json();

  document.getElementById('total-posts').textContent = d.total_posts;
  document.getElementById('avg-eng').textContent     = d.avg_engagement.toFixed(2) + '%';
  document.getElementById('best-niche').textContent  = d.best_niche;
  document.getElementById('active-ab').textContent   = d.active_ab_tests;

  // Top posts
  const tb = document.getElementById('top-posts-body');
  if (!d.top_posts.length) { tb.innerHTML = '<tr><td colspan="10" class="empty">No posts tracked yet. Posts appear here after uploading.</td></tr>'; }
  else tb.innerHTML = d.top_posts.map((p, i) => {
    const clickColor = p.affiliate_clicks > 0 ? 'color:#34d399;font-weight:700' : 'color:#64748b';
    const ctrColor   = p.ctr > 1 ? 'color:#34d399;font-weight:700' : p.ctr > 0 ? 'color:#fbbf24' : 'color:#64748b';
    return `<tr>
      <td>${i+1}</td>
      <td><span class="badge ${p.platform === 'youtube' ? 'yt' : 'ig'}">${p.platform.toUpperCase()}</span></td>
      <td>${p.niche || '—'}</td>
      <td>${p.content_type || '—'}</td>
      <td class="caption" title="${(p.caption||'').replace(/"/g,'&quot;')}">${p.caption || '—'}</td>
      <td>${p.views.toLocaleString()}</td>
      <td>${p.likes.toLocaleString()}</td>
      <td><b>${p.engagement_score.toFixed(3)}%</b></td>
      <td style="${clickColor}">${p.affiliate_clicks}</td>
      <td style="${ctrColor}">${p.ctr.toFixed(2)}%</td>
    </tr>`;
  }).join('');

  // Niche breakdown
  const nb = document.getElementById('niche-body');
  if (!d.niche_stats.length) { nb.innerHTML = '<tr><td colspan="5" class="empty">No data yet.</td></tr>'; }
  else nb.innerHTML = d.niche_stats.map(n => `
    <tr>
      <td><b>${n.niche}</b></td>
      <td>${n.posts}</td>
      <td>${n.avg_views.toLocaleString()}</td>
      <td>${n.avg_likes.toLocaleString()}</td>
      <td>${n.avg_engagement.toFixed(3)}%</td>
    </tr>`).join('');

  // Content types
  const tyb = document.getElementById('type-body');
  if (!d.content_types.length) { tyb.innerHTML = '<tr><td colspan="3" class="empty">No data yet.</td></tr>'; }
  else tyb.innerHTML = d.content_types.map(t => `
    <tr><td>${t.type}</td><td>${t.count}</td><td>${t.avg_engagement.toFixed(3)}%</td></tr>`
  ).join('');

  // A/B Tests
  const ab = document.getElementById('ab-body');
  if (!d.ab_tests.length) { ab.innerHTML = '<tr><td colspan="7" class="empty">No A/B tests yet. Pass caption_variants when uploading to start one.</td></tr>'; }
  else ab.innerHTML = d.ab_tests.map(t => {
    const statusClass = t.status === 'concluded' ? 'win' : 'run';
    const winnerBadge = t.winner
      ? `<span class="badge win">Variant ${t.winner}</span>`
      : '<span class="badge run">Running</span>';
    const vars = t.variants || {};
    return `<tr>
      <td>${t.id}</td>
      <td>${t.niche || '—'}</td>
      <td><span class="badge ${statusClass}">${t.status}</span></td>
      <td>${winnerBadge}</td>
      <td class="caption">${vars.A || '—'}</td>
      <td class="caption">${vars.B || '—'}</td>
      <td>${(t.started_at||'').slice(0,10)}</td>
    </tr>`;
  }).join('');
}

async function loadLinks() {
  const r = await fetch('/api/links');
  const links = await r.json();
  const BASE = window.location.origin;
  const lb = document.getElementById('links-body');
  if (!links.length) {
    lb.innerHTML = '<tr><td colspan="9" class="empty">No links yet.</td></tr>';
    return;
  }
  lb.innerHTML = links.map(l => {
    const url = `${BASE}/r/${l.slug}`;
    const src = l.sources || {};
    return `<tr>
      <td><code style="font-size:12px">${l.slug}</code></td>
      <td>${l.label || '—'}</td>
      <td>${l.niche || '—'}</td>
      <td><b>${l.total_clicks}</b></td>
      <td>${src.youtube || 0}</td>
      <td>${src.instagram || 0}</td>
      <td>${src.linktree || 0}</td>
      <td>${src.direct || 0}</td>
      <td>
        <code style="font-size:11px;color:#38bdf8">${url}</code>
        <button onclick="navigator.clipboard.writeText('${url}')"
          style="padding:2px 8px;font-size:11px;margin-left:6px;width:auto">Copy</button>
      </td>
    </tr>`;
  }).join('');
}

async function addLink() {
  const slug  = document.getElementById('new-slug').value.trim();
  const label = document.getElementById('new-label').value.trim();
  const dest  = document.getElementById('new-dest').value.trim();
  const niche = document.getElementById('new-niche').value.trim();
  const status = document.getElementById('add-link-status');
  if (!slug || !dest) { status.textContent = 'Slug and destination URL are required.'; return; }
  const r = await fetch('/api/links', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({slug, label, destination: dest, niche}),
  });
  const d = await r.json();
  if (!r.ok) { status.textContent = 'Error: ' + (d.error || 'unknown'); return; }
  status.textContent = 'Added! Tracking URL: ' + d.tracking_url;
  ['new-slug','new-label','new-dest','new-niche'].forEach(id => document.getElementById(id).value = '');
  loadLinks();
}

async function refreshMetrics() {
  const btn = document.getElementById('refresh-btn');
  const status = document.getElementById('refresh-status');
  btn.disabled = true;
  btn.textContent = 'Refreshing…';
  status.textContent = '';
  try {
    const r = await fetch('/api/metrics/collect', {method:'POST'});
    const d = await r.json();
    status.textContent = `Updated ${d.updated} posts, ${d.failed} failed, ${d.ab_tests_concluded} A/B tests concluded.`;
    await load();
  } catch(e) {
    status.textContent = 'Error: ' + e.message;
  }
  btn.disabled = false;
  btn.textContent = 'Refresh Metrics';
}

load();
loadLinks();
</script>
</body>
</html>
"""


@app.route("/insights")
def insights_dashboard():
    return render_template_string(INSIGHTS_HTML)


# ---------------------------------------------------------------------------
# Pipeline API
# ---------------------------------------------------------------------------

@app.route("/api/pipeline/status", methods=["GET"])
def pipeline_status():
    from pipeline.scheduler import get_scheduler, NICHE_POST_TIMES
    scheduler = get_scheduler()
    jobs = []
    if scheduler:
        for job in scheduler.get_jobs():
            next_run = job.next_run_time
            jobs.append({
                "id":       job.id,
                "niche":    job.id.replace("pipeline_", ""),
                "next_run": next_run.isoformat() if next_run else None,
            })

    recent_runs = PipelineRun.query.order_by(PipelineRun.started_at.desc()).limit(20).all()
    budgets     = CreditBudget.query.all()

    return jsonify({
        "scheduler_running": bool(scheduler and scheduler.running),
        "jobs":              jobs,
        "recent_runs":       [r.to_dict() for r in recent_runs],
        "budgets":           [b.to_dict() for b in budgets],
        "post_times_utc":    {n: f"{h:02d}:{m:02d}" for n, (h, m) in NICHE_POST_TIMES.items()},
    })


@app.route("/api/pipeline/run-now/<niche>", methods=["POST"])
def pipeline_run_now(niche):
    from pipeline.scheduler import trigger_now
    niche_obj = Niche.query.filter_by(name=niche, is_active=True).first()
    if not niche_obj:
        return _error(f"Niche '{niche}' not found", 404)
    trigger_now(niche, app)
    return jsonify({"status": "triggered", "niche": niche})


@app.route("/api/pipeline/budget", methods=["POST"])
def set_budget():
    """Set monthly credit quota. Body: {service, niche, monthly_limit}"""
    data    = request.get_json(force=True)
    service = data.get("service", "posts_per_day")
    niche   = data.get("niche")
    limit   = int(data.get("monthly_limit", 30))

    budget = CreditBudget.query.filter_by(service=service, niche=niche).first()
    if budget:
        budget.monthly_limit = limit
    else:
        budget = CreditBudget(service=service, niche=niche, monthly_limit=limit)
        db.session.add(budget)
    db.session.commit()
    return jsonify(budget.to_dict())


@app.route("/api/pipeline/budget/reset", methods=["POST"])
def reset_budget():
    """Reset monthly usage counters (run on the 1st of each month)."""
    CreditBudget.query.update({"current_usage": 0})
    db.session.commit()
    return jsonify({"reset": True})


# ---------------------------------------------------------------------------
# Reference account scraper + style learning
# ---------------------------------------------------------------------------

from models import ReferenceAccount, ScrapedPost, StyleGuide

@app.route("/api/scraper/accounts", methods=["GET"])
def list_reference_accounts():
    accounts = ReferenceAccount.query.order_by(ReferenceAccount.created_at.desc()).all()
    return jsonify([a.to_dict() for a in accounts])


@app.route("/api/scraper/accounts", methods=["POST"])
def add_reference_account():
    data   = request.get_json(force=True)
    handle = data.get("handle", "").lstrip("@").strip()
    if not handle:
        return _error("handle is required")
    if ReferenceAccount.query.filter_by(handle=handle).first():
        return _error(f"@{handle} already exists")
    account = ReferenceAccount(
        handle     = handle,
        platform   = data.get("platform", "instagram"),
        niche_hint = data.get("niche_hint"),
    )
    db.session.add(account)
    db.session.commit()
    return jsonify(account.to_dict()), 201


@app.route("/api/scraper/accounts/<int:account_id>", methods=["DELETE"])
def delete_reference_account(account_id):
    account = ReferenceAccount.query.get_or_404(account_id)
    db.session.delete(account)
    db.session.commit()
    return jsonify({"deleted": True})


@app.route("/api/scraper/run", methods=["POST"])
def run_scraper():
    """Trigger a full scrape + style learning run in the background."""
    import threading
    def _run():
        from integrations.instagram_scraper import scrape_all_accounts
        from pipeline.style_learner import learn_all_niches
        scrape_all_accounts(app)
        learn_all_niches(app)
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "scrape started"})


@app.route("/api/scraper/style-guides", methods=["GET"])
def get_style_guides():
    guides = StyleGuide.query.all()
    return jsonify([g.to_dict() for g in guides])


# ---------------------------------------------------------------------------
# Lumi Tales story management
# ---------------------------------------------------------------------------

from models import LumiStory

@app.route("/api/lumi/stories", methods=["GET"])
def list_lumi_stories():
    status  = request.args.get("status")
    query   = LumiStory.query.order_by(LumiStory.generated_at.desc())
    if status:
        query = query.filter_by(status=status)
    stories = query.limit(50).all()
    return jsonify([s.to_dict() for s in stories])


@app.route("/api/lumi/generate", methods=["POST"])
def generate_lumi_story():
    """Manually trigger a story generation."""
    from pipeline.lumi_tales import generate_and_store
    import threading
    threading.Thread(target=generate_and_store, args=[app], daemon=True).start()
    return jsonify({"status": "generating"})


@app.route("/api/lumi/stories/<int:story_id>", methods=["GET"])
def get_lumi_story(story_id):
    story = LumiStory.query.get_or_404(story_id)
    return jsonify(story.to_dict())


@app.route("/api/lumi/stories/<int:story_id>", methods=["DELETE"])
def delete_lumi_story(story_id):
    story = LumiStory.query.get_or_404(story_id)
    db.session.delete(story)
    db.session.commit()
    return jsonify({"deleted": True})


# ---------------------------------------------------------------------------
# Long-form video management
# ---------------------------------------------------------------------------

@app.route("/api/longform/videos", methods=["GET"])
def list_longform_videos():
    niche  = request.args.get("niche")
    status = request.args.get("status")
    q = LongFormVideo.query.order_by(LongFormVideo.generated_at.desc())
    if niche:
        q = q.filter_by(niche=niche)
    if status:
        q = q.filter_by(status=status)
    videos = q.limit(50).all()
    return jsonify([v.to_dict() for v in videos])


@app.route("/api/longform/videos/<int:video_id>", methods=["GET"])
def get_longform_video(video_id):
    video = LongFormVideo.query.get_or_404(video_id)
    return jsonify(video.to_dict())


@app.route("/api/longform/generate", methods=["POST"])
def generate_longform_video():
    """Manually trigger long-form generation for a niche."""
    data  = request.get_json(force=True)
    niche = data.get("niche", "everything")
    topic = data.get("topic")
    import threading
    from pipeline.longform import run_longform_for_niche
    threading.Thread(target=run_longform_for_niche, args=[niche, app], daemon=True).start()
    return jsonify({"status": "generating", "niche": niche, "topic": topic})


@app.route("/api/longform/videos/<int:video_id>", methods=["DELETE"])
def delete_longform_video(video_id):
    video = LongFormVideo.query.get_or_404(video_id)
    db.session.delete(video)
    db.session.commit()
    return jsonify({"deleted": video_id})


@app.route("/api/longform/latest", methods=["GET"])
def latest_longform_per_niche():
    """Return the most recently posted long-form video for each niche (for Short CTAs)."""
    niches = ["trading", "fitness", "crime", "sports", "anatomy", "everything", "kids"]
    result = {}
    for n in niches:
        v = LongFormVideo.query.filter_by(niche=n, status="posted")\
                               .order_by(LongFormVideo.posted_at.desc()).first()
        result[n] = {"title": v.title, "youtube_url": v.youtube_url} if v else None
    return jsonify(result)


def _seed_reference_accounts():
    """Seed the initial reference accounts for style training."""
    defaults = [
        # Sports
        ("lamarsnackson",        "instagram", "sports"),
        ("definingsportsmoments","instagram", "sports"),
        ("courtlinemedia",       "instagram", "sports"),
        # Everything / viral
        ("bxllertoonz",          "instagram", None),
        ("blackgreninja1",       "instagram", None),
        ("technerd_stewie",      "instagram", None),
        ("bestofkick_",          "instagram", None),
        ("bleacherreport",       "instagram", None),
        ("houseofhighlights",    "instagram", None),
        ("passionbeam",          "instagram", None),
        ("thelighthatburnsthesky","instagram", None),
        # Trading
        ("mrkt_ai",              "instagram", "trading"),
    ]
    for handle, platform, niche_hint in defaults:
        if not ReferenceAccount.query.filter_by(handle=handle).first():
            db.session.add(ReferenceAccount(
                handle=handle, platform=platform, niche_hint=niche_hint
            ))
    db.session.commit()


# ---------------------------------------------------------------------------
# Link click tracking
# ---------------------------------------------------------------------------

def _inject_affiliate_links(description: str, niche: str, content_id: int) -> str:
    """Append tracked affiliate links for the niche to the video description."""
    links = TrackedLink.query.filter_by(niche=niche, is_active=True).all()
    if not links:
        return description
    base = Config.BASE_URL.rstrip("/")
    lines = "\n".join(f"{l.label}: {base}/r/{l.slug}?job={content_id}" for l in links)
    return f"{description}\n\n{lines}" if description else lines


def _detect_source(referrer: str) -> str:
    if not referrer:
        return "direct"
    r = referrer.lower()
    if "youtube" in r or "youtu.be" in r:   return "youtube"
    if "instagram" in r:                     return "instagram"
    if "tiktok" in r:                        return "tiktok"
    if "linktr.ee" in r or "linktree" in r:  return "linktree"
    if "twitter" in r or "t.co" in r:        return "twitter"
    return "other"


@app.route("/r/<slug>")
def link_redirect(slug):
    link = TrackedLink.query.filter_by(slug=slug, is_active=True).first()
    if not link:
        return "Link not found.", 404
    referrer   = request.headers.get("Referer", "")
    source     = _detect_source(referrer)
    content_id = request.args.get("job", type=int)
    click = LinkClick(
        link_id    = link.id,
        content_id = content_id,
        source     = source,
        referrer   = referrer[:500],
        user_agent = request.headers.get("User-Agent", "")[:500],
    )
    db.session.add(click)
    db.session.commit()
    return redirect(link.destination, code=302)


@app.route("/api/links", methods=["GET"])
def list_links():
    links = TrackedLink.query.filter_by(is_active=True).order_by(TrackedLink.niche).all()
    return jsonify([l.to_dict(include_stats=True) for l in links])


@app.route("/api/links", methods=["POST"])
def create_link():
    data = request.get_json(force=True)
    slug = data.get("slug", "").strip().lower().replace(" ", "-")
    if not slug or not data.get("destination"):
        return _error("slug and destination are required")
    if TrackedLink.query.filter_by(slug=slug).first():
        return _error(f"Slug '{slug}' already exists")
    link = TrackedLink(
        slug        = slug,
        label       = data.get("label", slug),
        destination = data["destination"],
        niche       = data.get("niche", ""),
        link_type   = data.get("link_type", "affiliate"),
    )
    db.session.add(link)
    db.session.commit()
    base = Config.BASE_URL.rstrip("/")
    return jsonify({**link.to_dict(), "tracking_url": f"{base}/r/{slug}"}), 201


@app.route("/api/links/<int:link_id>", methods=["DELETE"])
def delete_link(link_id):
    link = TrackedLink.query.get_or_404(link_id)
    link.is_active = False
    db.session.commit()
    return jsonify({"deleted": link_id})


# ---------------------------------------------------------------------------
# DB init + seed
# ---------------------------------------------------------------------------

@app.cli.command("init-db")
def init_db():
    db.create_all()
    _seed_niches()
    _seed_links()
    print("Database initialised.")


def _seed_niches():
    defaults = [
        ("trading",    "Trading"),
        ("fitness",    "Fitness"),
        ("crime",      "Crime / Horror / Mystery"),
        ("sports",     "Sports"),
        ("anatomy",    "Anatomy & Physiology"),
        ("everything", "Everything"),
        ("kids",       "Kids"),
    ]
    for name, display in defaults:
        if not Niche.query.filter_by(name=name).first():
            db.session.add(Niche(name=name, display_name=display))
    db.session.commit()


def _seed_links():
    defaults = [
        ("tradingview", "TradingView Affiliate",
         "https://www.tradingview.com/?aff_id=166596", "trading", "affiliate"),
        ("usetradingbot", "AI Trading Bot",
         "https://usetradingbot.com/", "trading", "website"),
        ("nordvpn", "NordVPN Affiliate",
         "https://nordvpn.com/", "crime", "affiliate"),
    ]
    for slug, label, dest, niche, ltype in defaults:
        if not TrackedLink.query.filter_by(slug=slug).first():
            db.session.add(TrackedLink(slug=slug, label=label, destination=dest,
                                       niche=niche, link_type=ltype))
    db.session.commit()


def _migrate():
    """Add columns that were introduced after initial table creation."""
    import sqlalchemy as sa
    inspector = sa.inspect(db.engine)
    migrations = [
        ("link_clicks",    "content_id",      "ALTER TABLE link_clicks ADD COLUMN content_id INTEGER REFERENCES content_queue(id)"),
        ("post_metrics",   "voice_id",         "ALTER TABLE post_metrics ADD COLUMN voice_id VARCHAR(100)"),
        ("post_metrics",   "voice_name",        "ALTER TABLE post_metrics ADD COLUMN voice_name VARCHAR(200)"),
        ("social_accounts","token_expires_at",  "ALTER TABLE social_accounts ADD COLUMN token_expires_at TIMESTAMP"),
    ]
    with db.engine.connect() as conn:
        for table, col, sql in migrations:
            try:
                existing = [c["name"] for c in inspector.get_columns(table)]
            except Exception:
                continue
            if col not in existing:
                conn.execute(sa.text(sql))
                conn.commit()


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        _migrate()
        _seed_links()
    from pipeline.scheduler import init_scheduler
    init_scheduler(app)
    app.run(debug=True, port=5000, use_reloader=False)
