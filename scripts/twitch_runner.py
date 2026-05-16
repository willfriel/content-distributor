#!/usr/bin/env python3
"""
Twitch clip processor — executes on GitHub Actions (7 GB RAM, no Flask/DB).

Downloads a Twitch clip, formats it to 9:16 vertical with hook/CTA/captions,
uploads the result to Render, then POSTs a callback so Render can post it
to Instagram / YouTube.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

import requests

# Make project root importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from integrations.twitch_eventsub import (
    _download_twitch_clip,
    _format_vertical,
    _generate_hook,
    _transcribe_clip,
)

# ---------------------------------------------------------------------------
# Config from env
# ---------------------------------------------------------------------------

CALLBACK_URL = os.environ.get("CALLBACK_URL", "").rstrip("/")
SECRET       = os.environ.get("CALLBACK_SECRET", "")
AUTH_HEADERS = {"Authorization": f"Bearer {SECRET}"}


# ---------------------------------------------------------------------------
# Render communication helpers
# ---------------------------------------------------------------------------

def upload_video(path: Path, run_id: str) -> str | None:
    """POST the finished MP4 to Render and get back a public video_url."""
    size_mb = path.stat().st_size / 1024 / 1024
    print(f"Uploading {size_mb:.1f} MB to Render...")
    try:
        with open(path, "rb") as f:
            resp = requests.post(
                f"{CALLBACK_URL}/api/internal/video-upload",
                headers=AUTH_HEADERS,
                files={"video": (f"twitch_{run_id}.mp4", f, "video/mp4")},
                data={"run_id": run_id, "type": "twitch"},
                timeout=300,
            )
        resp.raise_for_status()
        url = resp.json()["video_url"]
        print(f"Uploaded: {url}")
        return url
    except Exception as e:
        print(f"Upload failed: {e}")
        return None


def notify(payload: dict):
    """POST the final status callback to Render."""
    try:
        requests.post(
            f"{CALLBACK_URL}/api/internal/clip-done",
            headers={**AUTH_HEADERS, "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        print(f"Callback sent: status={payload.get('status')}")
    except Exception as e:
        print(f"Callback failed: {e}")


def fail(run_id: str, note: str):
    notify({"run_id": run_id, "status": "failed", "note": note})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    clip_id    = os.environ["CLIP_ID"]
    clip_title = os.environ["CLIP_TITLE"]
    streamer   = os.environ["STREAMER"]
    run_id     = os.environ["RUN_ID"]

    print(f"=== Twitch Runner: {streamer} / {clip_id} (run_id={run_id}) ===")

    work_dir = Path(tempfile.mkdtemp(prefix="twitch_"))
    try:
        # ----- 1. Download -----
        print(f"Downloading clip {clip_id}...")
        raw_path = _download_twitch_clip(clip_id)
        if not raw_path:
            fail(run_id, "clip download failed")
            return

        raw_dest = work_dir / "raw.mp4"
        shutil.move(raw_path, str(raw_dest))
        print(f"Downloaded: {raw_dest.stat().st_size / 1024 / 1024:.1f} MB")

        # ----- 2. Transcribe -----
        print("Transcribing audio...")
        srt = _transcribe_clip(str(raw_dest))
        if srt:
            print(f"Transcribed {len(srt)} chars of captions")
        else:
            print("No captions (Whisper unavailable — continuing without)")

        # ----- 3. Hook -----
        print("Generating hook text...")
        hook = _generate_hook(streamer, clip_title)
        cta  = f"To keep watching {streamer} clips follow for more!"
        print(f"Hook: '{hook}'")

        # ----- 4. Format 9:16 vertical -----
        print("Formatting to 9:16 vertical...")
        fmt_path, bg = _format_vertical(str(raw_dest), hook, cta, srt=srt)

        final_path = work_dir / "final.mp4"
        shutil.move(fmt_path, str(final_path))
        if raw_dest.exists():
            raw_dest.unlink()
        print(f"Formatted ({bg} bg): {final_path.stat().st_size / 1024 / 1024:.1f} MB")

        # ----- 5. Upload -----
        video_url = upload_video(final_path, run_id)
        if not video_url:
            fail(run_id, "video upload failed")
            return

        # ----- 6. Callback -----
        notify({
            "run_id":    run_id,
            "status":    "success",
            "video_url": video_url,
            "streamer":  streamer,
            "title":     f"{streamer}: {clip_title}"[:100],
            "hook":      hook,
            "bg_style":  bg,
        })
        print(f"=== Done: {streamer} clip ({bg} bg) → {video_url} ===")

    except Exception as e:
        import traceback
        traceback.print_exc()
        fail(run_id, str(e))
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
