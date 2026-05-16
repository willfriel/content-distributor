#!/usr/bin/env python3
"""
Lumi Tales episode runner — executes on GitHub Actions (7 GB RAM, no Flask/DB).

Reads job parameters from environment variables set by the workflow.
Produces the final MP4, uploads it to Render, then POSTs a callback
so Render can create the ContentQueue item and trigger social posting.
"""
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

import requests

# Make project root importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from integrations.lumi_builder import (
    _make_placeholder_image,
    assemble_episode,
    generate_scene_image,
    generate_script,
    generate_voiceover,
    make_scene_clip,
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
    print(f"Uploading {path.stat().st_size // 1024 // 1024} MB to Render...")
    try:
        with open(path, "rb") as f:
            resp = requests.post(
                f"{CALLBACK_URL}/api/internal/video-upload",
                headers=AUTH_HEADERS,
                files={"video": (f"lumi_{run_id}.mp4", f, "video/mp4")},
                data={"run_id": run_id, "type": "lumi"},
                timeout=300,
            )
        resp.raise_for_status()
        url = resp.json()["video_url"]
        print(f"Uploaded: {url}")
        return url
    except Exception as e:
        print(f"Upload failed: {e}")
        return None


def upload_image(path: Path, run_id: str, suffix: str) -> str | None:
    """POST an image file to Render and get back a public URL."""
    try:
        with open(path, "rb") as f:
            resp = requests.post(
                f"{CALLBACK_URL}/api/internal/image-upload",
                headers=AUTH_HEADERS,
                files={"image": (f"lumi_{run_id}_{suffix}.jpg", f, "image/jpeg")},
                data={"run_id": run_id, "suffix": suffix},
                timeout=60,
            )
        resp.raise_for_status()
        return resp.json()["image_url"]
    except Exception as e:
        print(f"Image upload failed: {e}")
        return None


def notify(payload: dict):
    """POST the final status callback to Render."""
    try:
        requests.post(
            f"{CALLBACK_URL}/api/internal/lumi-done",
            headers={**AUTH_HEADERS, "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        print(f"Callback sent: status={payload.get('status')}")
    except Exception as e:
        print(f"Callback failed: {e}")


# ---------------------------------------------------------------------------
# Shorts creation
# ---------------------------------------------------------------------------

def make_shorts_clip(clip_paths: list, output_path: Path, target_secs: int = 45) -> bool:
    """
    Take the first N scene clips (up to target_secs total) and reformat
    to 9:16 vertical for YouTube Shorts.
    """
    import subprocess, json as _json
    selected = []
    total    = 0.0

    for p in clip_paths:
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(p)],
                capture_output=True, text=True, timeout=10,
            )
            dur = 0.0
            for s in _json.loads(probe.stdout).get("streams", []):
                if s.get("duration"):
                    dur = float(s["duration"]); break
            if total + dur <= target_secs:
                selected.append(str(p))
                total += dur
            else:
                break
        except Exception:
            continue

    if not selected:
        return False

    # Write concat list
    concat_txt = output_path.parent / "shorts_concat.txt"
    concat_txt.write_text("\n".join(f"file '{p}'" for p in selected))

    # Concat → vertical 9:16 (720x1280) with black bars
    cmd = [
        "ffmpeg",
        "-f", "concat", "-safe", "0", "-i", str(concat_txt),
        "-vf", (
            f"scale=720:-2:force_original_aspect_ratio=decrease,"
            f"pad=720:1280:(ow-iw)/2:(oh-ih)/2:black,"
            f"drawtext=text='Watch the full episode on @LumiTales':fontsize=28:fontcolor=white"
            f":x=(w-text_w)/2:y=h-80:shadowcolor=black:shadowx=2:shadowy=2"
        ),
        "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1",
        "-c:a", "aac", "-y", str(output_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=120)
        concat_txt.unlink(missing_ok=True)
        if result.returncode != 0:
            print(f"Shorts ffmpeg failed: {result.stderr.decode()[-200:]}")
            return False
        print(f"Shorts created: {total:.1f}s from {len(selected)} scenes")
        return True
    except Exception as e:
        print(f"Shorts creation error: {e}")
        return False


# ---------------------------------------------------------------------------
# Thumbnail generation
# ---------------------------------------------------------------------------

def generate_thumbnail(title: str, moral: str, output_path: Path) -> bool:
    """
    Generate a YouTube thumbnail: DALL-E close-up of Lumi + PIL text overlay.
    Output: 1280x720 JPEG.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return False
    try:
        from openai import OpenAI
        from PIL import Image, ImageDraw, ImageFilter
        import io

        client = OpenAI(api_key=api_key)
        prompt = (
            "2D flat vector illustration for a children's YouTube thumbnail. "
            "Close-up portrait of Lumi — a 4-year-old girl with golden hair, big bright brown eyes, "
            "gold star hair clip, yellow top — with a huge joyful expressive smile, "
            "looking directly at the viewer. Soft pastel background with subtle sparkles. "
            "No text anywhere in the image. Bright, vibrant, eye-catching. "
            f"Mood: {moral[:60]}"
        )
        resp = client.images.generate(
            model="dall-e-3",
            prompt=prompt[:4000],
            size="1792x1024",
            quality="standard",
            n=1,
        )
        img_bytes = requests.get(resp.data[0].url, timeout=60).content
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB").resize((1280, 720))

        # Dark gradient bar at bottom for text readability
        draw    = ImageDraw.Draw(img, "RGBA")
        for y in range(480, 720):
            alpha = int(200 * (y - 480) / 240)
            draw.line([(0, y), (1280, y)], fill=(0, 0, 0, alpha))

        # Episode title
        draw2 = ImageDraw.Draw(img)
        draw2.text((640, 610), title[:45], fill="white",   anchor="mm")
        draw2.text((640, 670), "✨ Lumi Tales ✨", fill="#FFD700", anchor="mm")

        img.save(str(output_path), "JPEG", quality=92)
        print(f"Thumbnail generated: {output_path.name}")
        return True
    except Exception as e:
        print(f"Thumbnail generation failed: {e}")
        return False


def fail(run_id: str, story_id: str, note: str):
    notify({"run_id": run_id, "story_id": story_id, "status": "failed", "note": note})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    title    = os.environ["LUMI_TITLE"]
    moral    = os.environ["LUMI_MORAL"]
    run_id   = os.environ["RUN_ID"]
    story_id = os.environ.get("STORY_ID", "0")

    print(f"=== Lumi Runner: '{title}' (run_id={run_id}) ===")

    work_dir = Path(tempfile.mkdtemp(prefix="lumi_"))
    try:
        # ----- 1. Script -----
        print("Generating script...")
        script = generate_script(title, moral)
        if not script or not script.get("scenes"):
            fail(run_id, story_id, "script generation failed")
            return

        scenes = script["scenes"]
        print(f"Script ready: {len(scenes)} scenes")

        # ----- 2. Per-scene: image + voiceover (parallel) → clip -----
        clip_paths: list[Path] = []

        for scene in scenes:
            sid        = scene["id"]
            dialogue   = scene.get("dialogue", "")
            speaker    = scene.get("speaker", "narrator")
            motion     = scene.get("motion", "zoom_in")
            img_prompt = scene.get("image_prompt", "")

            img_path   = work_dir / f"scene_{sid:02d}.png"
            audio_path = work_dir / f"scene_{sid:02d}.mp3"
            clip_path  = work_dir / f"clip_{sid:02d}.mp4"

            print(f"  Scene {sid}: '{dialogue[:40]}...'")

            # Image + audio in parallel — DALL-E ~10s, ElevenLabs ~3s
            results: dict = {}

            def _gen_img(p=img_prompt, s=sid, d=work_dir, r=results):
                r["img"] = generate_scene_image(p, s, d)

            def _gen_audio(t=dialogue, sp=speaker, out=audio_path, r=results):
                r["audio"] = generate_voiceover(t, sp, out)

            t1 = threading.Thread(target=_gen_img,   daemon=True)
            t2 = threading.Thread(target=_gen_audio, daemon=True)
            t1.start(); t2.start()
            t1.join(); t2.join()

            if results.get("img") is None:
                print(f"  Scene {sid}: image failed — using placeholder")
                _make_placeholder_image(img_path, sid)

            if not results.get("audio"):
                print(f"  Scene {sid}: audio failed — skipping scene")
                time.sleep(12)
                continue

            if make_scene_clip(img_path, audio_path, motion, clip_path):
                clip_paths.append(clip_path)
            else:
                print(f"  Scene {sid}: clip render failed — skipping")

            # Stay under DALL-E rate limit (5 req/min on standard tier)
            time.sleep(12)

        if not clip_paths:
            fail(run_id, story_id, "no scene clips generated")
            return

        print(f"Generated {len(clip_paths)}/{len(scenes)} clips")

        # ----- 3. Assemble -----
        print("Assembling final video...")
        out_path = work_dir / "episode.mp4"
        if not assemble_episode(clip_paths, out_path):
            fail(run_id, story_id, "video assembly failed")
            return

        size_mb = out_path.stat().st_size / 1024 / 1024
        print(f"Episode assembled: {size_mb:.1f} MB")

        # ----- 4. Shorts + thumbnail (parallel with main upload) -----
        shorts_path    = work_dir / "shorts.mp4"
        thumbnail_path = work_dir / "thumbnail.jpg"

        shorts_ok    = make_shorts_clip(clip_paths, shorts_path)
        thumbnail_ok = generate_thumbnail(title, moral, thumbnail_path)

        # ----- 5. Upload everything -----
        video_url     = upload_video(out_path, run_id)
        shorts_url    = upload_video(shorts_path, f"{run_id}_short") if shorts_ok else None
        thumbnail_url = upload_image(thumbnail_path, run_id, "thumb") if thumbnail_ok else None

        if not video_url:
            fail(run_id, story_id, "video upload failed")
            return

        # ----- 6. Callback -----
        notify({
            "run_id":        run_id,
            "story_id":      story_id,
            "status":        "success",
            "video_url":     video_url,
            "shorts_url":    shorts_url,
            "thumbnail_url": thumbnail_url,
            "title":         title,
            "moral":         moral,
            "lesson_line":   script.get("lesson_line", ""),
            "scenes_count":  len(scenes),
        })
        print(f"=== Done: {len(clip_paths)} scenes → {video_url} ===")

    except Exception as e:
        import traceback
        traceback.print_exc()
        fail(run_id, story_id, str(e))
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
