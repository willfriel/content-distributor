"""
Twitch EventSub integration.
Subscribes to stream.online / stream.offline events for all twitch-niche streamers.
When a stream ends, waits 5 minutes then fetches the best clip and posts it immediately.
"""

import os
import hmac
import hashlib
import threading
import time
import requests
from datetime import datetime, timezone

_BASE         = "https://api.twitch.tv/helix"
_EVENTSUB_URL = f"{_BASE}/eventsub/subscriptions"

# streamer_login -> ISO stream start time (set on stream.online, cleared on post)
_stream_start_times: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------

def verify_signature(body: bytes, headers: dict) -> bool:
    secret    = os.environ.get("TWITCH_WEBHOOK_SECRET", "")
    msg_id    = headers.get("Twitch-Eventsub-Message-Id", "")
    timestamp = headers.get("Twitch-Eventsub-Message-Timestamp", "")
    signature = headers.get("Twitch-Eventsub-Message-Signature", "")
    if not secret or not signature:
        return False
    hmac_msg  = (msg_id + timestamp + body.decode("utf-8")).encode("utf-8")
    expected  = "sha256=" + hmac.new(secret.encode("utf-8"), hmac_msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Subscription management
# ---------------------------------------------------------------------------

def _twitch_headers():
    from integrations.twitch import _headers
    return _headers()


def _get_user_id(login: str) -> str | None:
    from integrations.twitch import _get_user_id
    return _get_user_id(login)


def _subscribe(login: str, event_type: str, callback_url: str, secret: str) -> bool:
    headers = _twitch_headers()
    if not headers:
        return False
    broadcaster_id = _get_user_id(login)
    if not broadcaster_id:
        print(f"[eventsub] User not found: {login}")
        return False
    payload = {
        "type":      event_type,
        "version":   "1",
        "condition": {"broadcaster_user_id": broadcaster_id},
        "transport": {
            "method":   "webhook",
            "callback": callback_url,
            "secret":   secret,
        },
    }
    try:
        r = requests.post(_EVENTSUB_URL, json=payload, headers=headers, timeout=15)
        if r.status_code in (200, 202):
            print(f"[eventsub] ✅ Subscribed {login} → {event_type}")
            return True
        elif r.status_code == 409:
            print(f"[eventsub] Already subscribed: {login} → {event_type}")
            return True
        else:
            print(f"[eventsub] Failed {login} → {event_type}: {r.status_code} {r.text[:200]}")
            return False
    except Exception as e:
        print(f"[eventsub] Error subscribing {login}: {e}")
        return False


def subscribe_all_streamers(app):
    """Subscribe to stream.online + stream.offline for every twitch niche streamer."""
    from pipeline.sources import TWITCH_NICHE_STREAMERS
    base_url    = os.environ.get("BASE_URL", "https://content-distributor.onrender.com").rstrip("/")
    callback    = f"{base_url}/webhook/twitch"
    secret      = os.environ.get("TWITCH_WEBHOOK_SECRET", "")
    if not secret:
        print("[eventsub] TWITCH_WEBHOOK_SECRET not set — skipping subscriptions")
        return

    print(f"[eventsub] Subscribing {len(TWITCH_NICHE_STREAMERS)} streamers to EventSub...")
    for login in TWITCH_NICHE_STREAMERS:
        _subscribe(login, "stream.online",  callback, secret)
        _subscribe(login, "stream.offline", callback, secret)
        time.sleep(0.3)
    print("[eventsub] Done subscribing.")


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------

def handle_online(event: dict):
    login = event.get("broadcaster_user_login", "").lower()
    started_at = datetime.now(timezone.utc).isoformat()
    _stream_start_times[login] = started_at
    print(f"[eventsub] 🟢 {login} went LIVE — tracking stream start")


def handle_offline(event: dict, app):
    login = event.get("broadcaster_user_login", "").lower()
    started_at = _stream_start_times.get(login)
    print(f"[eventsub] 🔴 {login} went OFFLINE — collecting clips in 5 min")

    def delayed():
        time.sleep(300)  # wait 5 min for Twitch to process clips
        _collect_and_post(login, started_at, app)
        _stream_start_times.pop(login, None)

    t = threading.Thread(target=delayed, daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# Clip collection + posting
# ---------------------------------------------------------------------------

def _collect_and_post(login: str, started_at: str | None, app):
    """Fetch the top clip from a just-ended stream and post it to all twitch accounts."""
    print(f"[eventsub] Fetching clips for {login}...")

    headers = _twitch_headers()
    if not headers:
        return

    broadcaster_id = _get_user_id(login)
    if not broadcaster_id:
        return

    try:
        params = {"broadcaster_id": broadcaster_id, "first": 10}
        if started_at:
            params["started_at"] = started_at

        r = requests.get(f"{_BASE}/clips", params=params, headers=headers, timeout=15)
        r.raise_for_status()
        clips = r.json().get("data", [])

        if not clips:
            print(f"[eventsub] No clips found for {login}")
            return

        clips.sort(key=lambda c: c.get("view_count", 0), reverse=True)
        best = clips[0]
        print(f"[eventsub] Best clip: '{best['title']}' ({best['view_count']} views)")

        _post_clip(best["url"], best.get("title", f"{login} clip"), login, app)

    except Exception as e:
        print(f"[eventsub] Clip collection failed for {login}: {e}")


def _post_clip(clip_url: str, clip_title: str, streamer: str, app):
    """Download clip and post to all twitch niche accounts immediately."""
    import random
    from pathlib import Path
    from pipeline.scheduler import _download_video
    from pipeline.captions  import generate_captions

    with app.app_context():
        from models import db, Niche, SocialAccount, PipelineRun, ContentQueue
        from server  import _run_job
        from datetime import datetime

        print(f"[eventsub] Posting clip from {streamer}: {clip_url}")

        niche_obj = Niche.query.filter_by(name="twitch", is_active=True).first()
        if not niche_obj:
            print("[eventsub] twitch niche not found in DB")
            return

        accounts = SocialAccount.query.filter_by(niche_id=niche_obj.id, is_active=True).all()
        if not accounts:
            print("[eventsub] No active twitch accounts")
            return

        run = PipelineRun(niche="twitch", status="running", started_at=datetime.utcnow(),
                          note=f"eventsub: {streamer}")
        db.session.add(run)
        db.session.commit()

        try:
            video_path = _download_video(clip_url, max_duration=60)
            if not video_path:
                run.status = "failed"; run.note = f"eventsub download failed: {clip_url}"
                db.session.commit(); return

            static_dir = Path(app.root_path) / "static" / "videos"
            static_dir.mkdir(parents=True, exist_ok=True)
            dest = static_dir / f"pipeline_twitch_event_{run.id}.mp4"
            Path(video_path).rename(dest)

            base_url  = os.environ.get("BASE_URL", "https://content-distributor.onrender.com").rstrip("/")
            video_url = f"{base_url}/static/videos/{dest.name}"
            title     = f"{streamer}: {clip_title}"[:100]

            cap_a, cap_b = generate_captions("twitch", title)

            item = ContentQueue(
                niche_id       = niche_obj.id,
                video_url      = video_url,
                title          = title,
                description    = cap_a,
                platforms      = list({a.platform for a in accounts}),
                use_opusclip   = False,
                status         = "pending",
                upload_results = {},
                clipped_urls   = [],
            )
            db.session.add(item)
            db.session.commit()

            caption_variants = [cap_a, cap_b]
            _run_job(
                item.id, accounts, False,
                content_type="twitch_clip",
                account_caps={a.id: caption_variants[i % 2] for i, a in enumerate(accounts)},
            )

            run.status = "completed"
            db.session.commit()
            print(f"[eventsub] ✅ Posted {streamer} clip to {len(accounts)} accounts")

        except Exception as e:
            run.status = "failed"; run.note = str(e)
            db.session.commit()
            print(f"[eventsub] Post failed: {e}")
