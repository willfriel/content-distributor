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

def _download_twitch_clip(clip_id: str) -> str | None:
    """
    Download a Twitch clip directly via GQL API — bypasses yt-dlp which fails
    on Twitch's HLS-based clip delivery.
    Returns temp file path or None.
    """
    import tempfile
    from pathlib import Path

    client_id = os.environ.get("TWITCH_CLIENT_ID", "")
    if not client_id:
        print("[eventsub] TWITCH_CLIENT_ID not set")
        return None

    # GQL persisted query for clip access token + video qualities
    gql_payload = [{
        "operationName": "VideoAccessToken_Clip",
        "variables":     {"slug": clip_id},
        "extensions": {
            "persistedQuery": {
                "version":    1,
                "sha256Hash": "36b89d2507fce29e5ca551df756d27c1cfe079e2609642b4390aa4c35796eb11",
            }
        },
    }]

    try:
        r = requests.post(
            "https://gql.twitch.tv/gql",
            json=gql_payload,
            headers={"Client-Id": client_id},
            timeout=15,
        )
        r.raise_for_status()
        clip_data = r.json()[0].get("data", {}).get("clip", {}) or {}
        qualities  = clip_data.get("videoQualities", [])

        if not qualities:
            print(f"[eventsub] GQL returned no video qualities for {clip_id}")
            return None

        # Pick highest quality source URL
        best      = max(qualities, key=lambda q: int(q.get("quality", "0")))
        video_url = best.get("sourceURL")
        if not video_url:
            return None

        # Download the MP4 directly
        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        tmp.close()
        with requests.get(video_url, stream=True, timeout=120) as resp:
            resp.raise_for_status()
            with open(tmp.name, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)

        size = Path(tmp.name).stat().st_size
        if size == 0:
            print(f"[eventsub] Downloaded 0 bytes for {clip_id}")
            return None

        print(f"[eventsub] Downloaded {clip_id}: {size / 1024 / 1024:.1f} MB")
        return tmp.name

    except Exception as e:
        print(f"[eventsub] GQL download failed for {clip_id}: {e}")
        return None


def _collect_and_post(login: str, started_at: str | None, app, all_time_only: bool = False):
    """
    Fetch up to 3 clips for a streamer:
      1. Top clips from the last 24 hours by view count (skipped if all_time_only=True)
      2. Pad with all-time top clips if fewer than 3 found in 24h
    Uses clips.twitch.tv URLs for reliable yt-dlp downloads.
    """
    from datetime import timedelta
    print(f"[eventsub] Fetching clips for {login}...")

    headers = _twitch_headers()
    if not headers:
        return

    broadcaster_id = _get_user_id(login)
    if not broadcaster_id:
        return

    try:
        selected = []

        # --- Last 24 hours (EventSub flow only) ---
        if not all_time_only:
            since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
            r = requests.get(f"{_BASE}/clips",
                             params={"broadcaster_id": broadcaster_id, "first": 20, "started_at": since},
                             headers=headers, timeout=15)
            r.raise_for_status()
            recent = sorted(r.json().get("data", []), key=lambda c: c.get("view_count", 0), reverse=True)
            # Only include 24h clips that have at least 500 views — avoids brand-new zero-view clips
            selected = [c for c in recent if c.get("view_count", 0) >= 500][:3]

        # --- All-time top clips (always used to pad, or as primary for manual trigger) ---
        if len(selected) < 3:
            seen = {c["id"] for c in selected}
            r2 = requests.get(f"{_BASE}/clips",
                              params={"broadcaster_id": broadcaster_id, "first": 20},
                              headers=headers, timeout=15)
            r2.raise_for_status()
            all_time = sorted(r2.json().get("data", []), key=lambda c: c.get("view_count", 0), reverse=True)
            for clip in all_time:
                if clip["id"] not in seen:
                    selected.append(clip)
                    seen.add(clip["id"])
                    if len(selected) == 3:
                        break

        if not selected:
            print(f"[eventsub] No clips found for {login}")
            return

        print(f"[eventsub] Posting {len(selected)} clip(s) for {login}")
        for clip in selected:
            print(f"[eventsub]   → '{clip['title']}' ({clip['view_count']} views)")
            _post_clip(clip["id"], clip.get("title", f"{login} clip"), login, app)

    except Exception as e:
        print(f"[eventsub] Clip collection failed for {login}: {e}")


def _generate_hook(streamer: str, clip_title: str) -> str:
    """Generate a short punchy 5-8 word hook for the video overlay text."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return clip_title[:60]
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=60,
            messages=[{"role": "user", "content":
                f"Write ONE punchy 5-8 word hook for a Twitch clip video overlay. "
                f"Streamer: {streamer}. Clip: {clip_title}. "
                f"Capitalize each word. 1 emoji max. No quotes. No hashtags. No explanation."
            }],
        )
        return msg.content[0].text.strip()[:70]
    except Exception:
        return clip_title[:60]


def _format_vertical(input_path: str, hook: str, cta: str) -> tuple[str, str]:
    """
    Convert a horizontal clip to 9:16 vertical (1080x1920) with text overlays.
    Randomly picks black or blur background — tagged for A/B learning.
    Returns (output_path, bg_style).
    """
    import subprocess
    import random

    bg_style = random.choice(["black", "blur"])
    out_path = input_path.replace(".mp4", "_v.mp4")

    def esc(t: str) -> str:
        return t.replace("\\", "\\\\").replace("'", "’").replace(":", "\\:").replace("%", "\\%")

    hook_esc = esc(hook[:70])
    cta_esc  = esc(cta[:90])

    dt_hook = (
        f"drawtext=text='{hook_esc}':fontsize=90:fontcolor=white"
        f":x=(w-text_w)/2:y=250:shadowcolor=black:shadowx=4:shadowy=4"
    )
    dt_cta = (
        f"drawtext=text='{cta_esc}':fontsize=55:fontcolor=white"
        f":x=(w-text_w)/2:y=h-220:shadowcolor=black:shadowx=3:shadowy=3"
    )

    try:
        if bg_style == "blur":
            fc = (
                f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
                f"crop=1080:1920,boxblur=25:25[bg];"
                f"[0:v]scale=1080:-2[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2[comp];"
                f"[comp]{dt_hook}[h];"
                f"[h]{dt_cta}[out]"
            )
            cmd = ["ffmpeg", "-i", input_path, "-filter_complex", fc,
                   "-map", "[out]", "-map", "0:a?",
                   "-c:v", "libx264", "-c:a", "aac", "-shortest", "-y", out_path]
        else:
            vf = f"scale=1080:-2,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,{dt_hook},{dt_cta}"
            cmd = ["ffmpeg", "-i", input_path, "-vf", vf,
                   "-c:v", "libx264", "-c:a", "aac", "-y", out_path]

        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        print(f"[eventsub] Formatted vertical ({bg_style} bg): {out_path}")
        return out_path, bg_style

    except Exception as e:
        print(f"[eventsub] ffmpeg format failed ({e}), using original")
        return input_path, "original"


def _post_clip(clip_id: str, clip_title: str, streamer: str, app):
    """Download clip via GQL, format to vertical 9:16, post to all twitch niche accounts."""
    from pathlib import Path
    from pipeline.captions import generate_captions

    with app.app_context():
        from models import db, Niche, SocialAccount, PipelineRun, ContentQueue
        from server  import _run_job
        from datetime import datetime

        print(f"[eventsub] Posting clip from {streamer}: {clip_id}")

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
            video_path = _download_twitch_clip(clip_id)
            if not video_path:
                run.status = "failed"; run.note = f"eventsub download failed: {clip_id}"
                db.session.commit(); return

            static_dir = Path(app.root_path) / "static" / "videos"
            static_dir.mkdir(parents=True, exist_ok=True)
            raw_dest = static_dir / f"pipeline_twitch_event_{run.id}_raw.mp4"
            Path(video_path).rename(raw_dest)

            # Generate hook and format to 9:16 vertical
            hook         = _generate_hook(streamer, clip_title)
            cta          = f"To keep watching {streamer} clips follow for more!"
            fmt_path, bg = _format_vertical(str(raw_dest), hook, cta)

            # Move formatted file to final destination
            dest = static_dir / f"pipeline_twitch_event_{run.id}.mp4"
            Path(fmt_path).rename(dest)
            if raw_dest.exists():
                raw_dest.unlink(missing_ok=True)

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
                upload_results = {"bg_style": bg, "hook": hook},
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
            print(f"[eventsub] ✅ Posted {streamer} clip ({bg} bg) to {len(accounts)} accounts")

        except Exception as e:
            run.status = "failed"; run.note = str(e)
            db.session.commit()
            print(f"[eventsub] Post failed: {e}")
