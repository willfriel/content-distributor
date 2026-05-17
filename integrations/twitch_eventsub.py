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

# streamer_login -> UTC timestamp of last offline dispatch (dedup repeated webhook retries)
_last_offline: dict[str, float] = {}

# Only one video processing job at a time — Render Starter plan has 512MB RAM
_process_lock = threading.Semaphore(1)


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

    # Cooldown: ignore duplicate offline events for the same streamer within 30 min
    # (Twitch retries webhooks when our server is slow/crashing — this prevents mass re-dispatch)
    now = time.time()
    if now - _last_offline.get(login, 0) < 1800:
        print(f"[eventsub] 🔴 {login} offline ignored — cooldown active")
        return
    _last_offline[login] = now

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
    Download a Twitch clip directly via GQL — bypasses yt-dlp HLS issues.
    Tries our app client ID first, then falls back to Twitch's public website
    client ID which works for public clips without a persisted query hash.
    """
    import tempfile
    from pathlib import Path

    from urllib.parse import quote

    # GQL query — requests both video qualities AND a signed playback token
    # The /nauth/ CloudFront URLs require ?sig=...&token=... to download
    gql_query = (
        '{ clip(slug: "' + clip_id + '") {'
        '  videoQualities { frameRate quality sourceURL } '
        '  playbackAccessToken(params: { platform: "web" playerBackend: "mediaplayer" playerType: "site" }) {'
        '    signature value'
        '  }'
        '} }'
    )

    # Try our app client ID first; fall back to Twitch's public website client ID
    client_ids = [
        os.environ.get("TWITCH_CLIENT_ID", ""),
        "kimne78kx3ncx6brgo4mv6wki5h1ko",
    ]

    for client_id in client_ids:
        if not client_id:
            continue
        try:
            r = requests.post(
                "https://gql.twitch.tv/gql",
                json={"query": gql_query},
                headers={"Client-Id": client_id, "Content-Type": "application/json"},
                timeout=15,
            )
            if r.status_code != 200:
                print(f"[eventsub] GQL {r.status_code} with client {client_id[:8]}...")
                continue

            clip_data = (r.json().get("data", {}).get("clip") or {})
            qualities  = clip_data.get("videoQualities", [])
            token_data = clip_data.get("playbackAccessToken") or {}

            if not qualities:
                print(f"[eventsub] No qualities for {clip_id} ({client_id[:8]}...)")
                continue

            # Cap at 720p to keep file size manageable on 512MB Render instance
            target = next((q for q in sorted(qualities, key=lambda q: int(q.get("quality","0")), reverse=True)
                           if int(q.get("quality", "0")) <= 720), qualities[0])
            video_url = target.get("sourceURL")
            if not video_url:
                continue

            # Append signed token if present (required for /nauth/ CloudFront URLs)
            sig   = token_data.get("signature", "")
            token = token_data.get("value", "")
            if sig and token:
                video_url = f"{video_url}?sig={sig}&token={quote(token)}"
                print(f"[eventsub] Using signed URL for {clip_id}")

            tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
            tmp.close()
            with requests.get(video_url, stream=True, timeout=120) as resp:
                resp.raise_for_status()
                with open(tmp.name, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=65536):
                        f.write(chunk)

            size = Path(tmp.name).stat().st_size
            if size == 0:
                print(f"[eventsub] 0 bytes for {clip_id}")
                continue

            print(f"[eventsub] Downloaded {clip_id}: {size / 1024 / 1024:.1f} MB")
            return tmp.name

        except Exception as e:
            print(f"[eventsub] GQL failed ({client_id[:8]}...): {e}")
            continue

    print(f"[eventsub] All download methods failed for {clip_id}")
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

        print(f"[eventsub] Dispatching {len(selected)} clip(s) for {login}")
        for i, clip in enumerate(selected):
            if i > 0:
                time.sleep(2)  # small delay to avoid GitHub API burst limit
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


def _transcribe_clip(video_path: str) -> str | None:
    """
    Transcribe clip audio via OpenAI Whisper API.
    Returns SRT string or None if unavailable.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        for attempt in range(3):
            try:
                with open(video_path, "rb") as f:
                    transcript = client.audio.transcriptions.create(
                        model="whisper-1",
                        file=f,
                        response_format="srt",
                    )
                print(f"[eventsub] Transcribed {video_path} ({len(transcript)} chars)")
                return transcript
            except Exception as e:
                if "rate_limit" in str(e).lower() and attempt < 2:
                    print(f"[eventsub] Whisper rate limit — retrying in 25s")
                    time.sleep(25)
                else:
                    raise
    except Exception as e:
        print(f"[eventsub] Whisper failed: {e}")
        return None


def _format_vertical(input_path: str, hook: str, cta: str, srt: str | None = None) -> tuple[str, str]:
    """
    Compose a 9:16 canvas (720x1280) with three zones:
      Top third    (y 0–426):   hook text
      Middle third (y 427–841): original 16:9 clip at full width (720x405), end-to-end
      Bottom third (y 842–1280): CTA text
    Background randomly chosen from 4 styles for A/B learning.
    Returns (output_path, bg_style).
    """
    import subprocess
    import random

    CANVAS_W, CANVAS_H = 720, 1280
    VID_W,    VID_H    = 720, 405           # 16:9 at full canvas width
    VID_Y              = (CANVAS_H - VID_H) // 2   # 437 — centers in canvas / middle third
    HOOK_Y             = 90                 # top of hook text, sits in top third
    CTA_Y              = VID_Y + VID_H + 55 # top of CTA text, sits in bottom third
    # Subtitle MarginV: distance from canvas bottom to bottom of video = 1280 - 842 = 438
    SUB_MARGIN_V       = CANVAS_H - (VID_Y + VID_H) + 30

    bg_style = random.choice(["black", "blur", "dark_purple", "dark_blue"])
    out_path = input_path.replace(".mp4", "_v.mp4")

    def esc(t: str) -> str:
        return t.replace("\\", "\\\\").replace("’", "\\’").replace(":", "\\:").replace("%", "\\%")

    def wrap_esc(text: str, max_chars: int) -> str:
        """Word-wrap text and escape each line for ffmpeg drawtext (\\n = newline)."""
        words = text.split()
        lines, cur = [], ""
        for w in words:
            if cur and len(cur) + 1 + len(w) > max_chars:
                lines.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}".strip()
        if cur:
            lines.append(cur)
        return r"\n".join(esc(line) for line in lines)

    # fontsize 48 @ 20 chars/line → max ~530px, safely within 720px canvas
    # fontsize 36 @ 28 chars/line → max ~555px, safely within 720px canvas
    hook_esc = wrap_esc(hook[:120], 20)
    cta_esc  = wrap_esc(cta[:120], 28)

    dt_hook = (
        f"drawtext=text=’{hook_esc}’:fontsize=48:fontcolor=white"
        f":x=(w-text_w)/2:y={HOOK_Y}:shadowcolor=black:shadowx=4:shadowy=4"
        f":line_spacing=8"
    )
    dt_cta = (
        f"drawtext=text=’{cta_esc}’:fontsize=36:fontcolor=white"
        f":x=(w-text_w)/2:y={CTA_Y}:shadowcolor=black:shadowx=3:shadowy=3"
        f":line_spacing=6"
    )

    bitrate_flags = ["-b:v", "2500k", "-maxrate", "3000k", "-bufsize", "5000k"]

    try:
        if bg_style == "blur":
            fc = (
                f"[0:v]scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=increase,"
                f"crop={CANVAS_W}:{CANVAS_H},boxblur=25:25[bg];"
                f"[0:v]scale={VID_W}:{VID_H}[vid];"
                f"[bg][vid]overlay=0:{VID_Y}[comp];"
                f"[comp]{dt_hook}[h];"
                f"[h]{dt_cta}[out]"
            )
        else:
            bg_color = {"black": "0x000000", "dark_purple": "0x1a0a2e", "dark_blue": "0x0a0a1a"}.get(bg_style, "0x000000")
            fc = (
                f"color=c={bg_color}:size={CANVAS_W}x{CANVAS_H}:rate=25[bg];"
                f"[0:v]scale={VID_W}:{VID_H}[vid];"
                f"[bg][vid]overlay=0:{VID_Y}[comp];"
                f"[comp]{dt_hook}[h];"
                f"[h]{dt_cta}[out]"
            )

        cmd1 = ["ffmpeg", "-i", input_path, "-filter_complex", fc,
                "-map", "[out]", "-map", "0:a?",
                "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1",
                *bitrate_flags,
                "-c:a", "aac", "-shortest", "-y", out_path]
        subprocess.run(cmd1, check=True, capture_output=True, timeout=180)
        print(f"[eventsub] Formatted vertical ({bg_style} bg): {out_path}")

    except Exception as e:
        print(f"[eventsub] ffmpeg pass 1 failed ({e}), using original")
        return input_path, "original"

    # Pass 2: burn subtitles onto the video portion (middle third)
    if srt:
        srt_path = out_path.replace(".mp4", ".srt")
        cap_path = out_path.replace(".mp4", "_cap.mp4")
        try:
            with open(srt_path, "w", encoding="utf-8") as f:
                f.write(srt)
            cmd2 = [
                "ffmpeg", "-i", out_path,
                "-vf", f"subtitles={srt_path}:force_style=’Bold=1,FontSize=16,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,MarginV={SUB_MARGIN_V},Alignment=2’",
                "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1",
                "-c:a", "aac", "-y", cap_path,
            ]
            result = subprocess.run(cmd2, capture_output=True, timeout=180)
            if result.returncode == 0:
                import os as _os
                _os.replace(cap_path, out_path)
                print(f"[eventsub] Captions burned in: {out_path}")
            else:
                err = result.stderr.decode("utf-8", errors="ignore")[-300:]
                print(f"[eventsub] Caption burn failed: {err}")
        except Exception as e:
            print(f"[eventsub] ffmpeg pass 2 failed ({e}), posting without captions")
        finally:
            for p in [srt_path, cap_path]:
                try:
                    import os as _os
                    if _os.path.exists(p):
                        _os.unlink(p)
                except Exception:
                    pass

    return out_path, bg_style


def _post_clip(clip_id: str, clip_title: str, streamer: str, app):
    """Dispatch clip processing to GitHub Actions (download + ffmpeg runs on 7 GB runner)."""
    from integrations.github_actions import trigger_workflow

    with app.app_context():
        from models import db, Niche, SocialAccount, PipelineRun
        from datetime import datetime, timedelta

        niche_obj = Niche.query.filter_by(name="twitch", is_active=True).first()
        if not niche_obj:
            print("[eventsub] twitch niche not found in DB")
            return

        accounts = SocialAccount.query.filter_by(niche_id=niche_obj.id, is_active=True).all()
        if not accounts:
            print("[eventsub] No active twitch accounts")
            return

        # Deduplication guard — never dispatch the same clip twice within 24 hours
        clip_note = f"clip:{clip_id}"
        already   = PipelineRun.query.filter(
            PipelineRun.note       == clip_note,
            PipelineRun.started_at >= datetime.utcnow() - timedelta(hours=2),
        ).first()
        if already:
            print(f"[eventsub] Skipping {clip_id} — already dispatched (run #{already.id})")
            return

        run = PipelineRun(
            niche="twitch", status="dispatched",
            started_at=datetime.utcnow(), note=clip_note,
        )
        db.session.add(run)
        db.session.commit()

        base_url = os.environ.get("BASE_URL", "https://content-distributor.onrender.com").rstrip("/")

        ok = trigger_workflow("twitch_clip.yml", {
            "clip_id":      clip_id,
            "clip_title":   clip_title[:200],
            "streamer":     streamer,
            "run_id":       str(run.id),
            "callback_url": base_url,
        })

        if ok:
            print(f"[eventsub] ✅ Dispatched {streamer}/{clip_id} to GitHub Actions (run_id={run.id})")
        else:
            run.status = "failed"
            run.note   = f"dispatch failed for {clip_note}"
            db.session.commit()
