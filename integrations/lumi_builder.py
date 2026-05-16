"""
Lumi Tales episode builder.
Generates a complete kids YouTube video from a title + moral lesson.

Pipeline:
  Claude Haiku  -> episode script (JSON with scenes)
  DALL-E 3      -> scene illustrations (consistent Lumi character)
  ElevenLabs    -> voiceover per scene
  ffmpeg        -> Ken Burns animation (image + audio -> scene clip)
  moviepy       -> stitch all scene clips into final episode video
  LumiStory     -> DB record
  ContentQueue  -> queue for YouTube upload
"""

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

LUMI_W   = 1280
LUMI_H   = 720
LUMI_FPS = 25

# ElevenLabs voice IDs — override via env vars in Render dashboard
_VOICES = {
    "lumi":     os.environ.get("LUMI_VOICE_ID",          "jBpfuIE2acCO8z3wKNLl"),  # Lily - child
    "mom":      os.environ.get("LUMI_MOM_VOICE_ID",      "EXAVITQu4vr4xnSDxMaL"),  # Sarah - warm adult
    "narrator": os.environ.get("LUMI_NARRATOR_VOICE_ID", "9BWtsMINqrJLrRacOk9x"),  # Aria - narrator
}

# DALL-E style prefix applied to every scene — ensures visual consistency across episodes
_STYLE = (
    "2D flat vector illustration for a children's YouTube show. Soft pastel color palette, "
    "rounded shapes, no sharp edges, warm cozy lighting, simple clean backgrounds. "
    "No text, no words, no signs, no letters anywhere in the image. "
    "Main character Lumi: a sweet 4-year-old girl, warm golden shoulder-length hair, "
    "big expressive brown eyes, small gold star-shaped hair clip, soft yellow top, light blue pants. "
    "Art style: educational kids animation — friendly, wholesome, safe for toddlers. "
    "Full scene composition showing the setting and characters. "
)

# One ffmpeg job at a time — prevents OOM on Render Starter (512 MB)
_process_lock = threading.Semaphore(1)


# ---------------------------------------------------------------------------
# 1. Script generation
# ---------------------------------------------------------------------------

def generate_script(title: str, moral: str) -> dict | None:
    """Use Claude Haiku to write a structured episode script as JSON."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[lumi] ANTHROPIC_API_KEY not set")
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        prompt = f"""You are a scriptwriter for Lumi Tales, a children's YouTube show for ages 2-6.

Write a complete episode script as valid JSON only — no explanation, no markdown.

Episode title: "{title}"
Lesson: "{moral}"

Return exactly this structure:
{{
  "title": "...",
  "scenes": [
    {{
      "id": 1,
      "image_prompt": "detailed description of what we SEE — setting, characters, action, mood",
      "dialogue": "the words spoken aloud (1-2 short sentences, simple vocabulary for toddlers)",
      "speaker": "lumi or mom or narrator",
      "motion": "zoom_in or zoom_out or pan_left or pan_right"
    }}
  ],
  "lesson_line": "one short sentence reinforcing the moral, spoken by Lumi at the end"
}}

Rules:
- 10 to 13 scenes total
- Simple vocabulary — 3-year-olds must understand every word
- No scary content, conflict is only a small solvable problem
- image_prompt must NOT include any text, signs, or writing in the scene
- image_prompt must include Lumi in almost every scene
- First scene: Lumi in a cozy indoor setting, happy and energetic
- Last 2 scenes: Lumi smiling, problem solved, lesson learned
- Vary motion: mix zoom_in, zoom_out, pan_left, pan_right across scenes
- Narrator introduces and wraps up; Lumi and Mom carry the middle"""

        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        print(f"[lumi] Script generation failed: {e}")
        return None


# ---------------------------------------------------------------------------
# 2. Scene image generation
# ---------------------------------------------------------------------------

def generate_scene_image(image_prompt: str, scene_id: int, output_dir: Path) -> Path | None:
    """Generate a scene illustration with DALL-E 3."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("[lumi] OPENAI_API_KEY not set — using placeholder images")
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        full_prompt = _STYLE + image_prompt
        resp = client.images.generate(
            model="dall-e-3",
            prompt=full_prompt[:4000],
            size="1792x1024",
            quality="standard",
            n=1,
        )
        img_url  = resp.data[0].url
        img_resp = requests.get(img_url, timeout=60)
        img_resp.raise_for_status()
        img_path = output_dir / f"scene_{scene_id:02d}.png"
        img_path.write_bytes(img_resp.content)
        print(f"[lumi] Scene {scene_id} image: {len(img_resp.content) // 1024} KB")
        return img_path
    except Exception as e:
        print(f"[lumi] DALL-E scene {scene_id} failed: {e}")
        return None


def _make_placeholder_image(output_path: Path, scene_id: int):
    """Solid-color placeholder when DALL-E fails."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        palette = ["#FFD700", "#FFB6C1", "#87CEEB", "#98FB98", "#DDA0DD", "#FFDAB9"]
        img  = Image.new("RGB", (1792, 1024), palette[scene_id % len(palette)])
        draw = ImageDraw.Draw(img)
        draw.text((896, 512), f"Scene {scene_id}", fill="white", anchor="mm")
        img.save(str(output_path))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 3. Voiceover generation
# ---------------------------------------------------------------------------

def generate_voiceover(text: str, speaker: str, output_path: Path) -> bool:
    """Generate voiceover audio via ElevenLabs and save to output_path (.mp3)."""
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        print("[lumi] ELEVENLABS_API_KEY not set")
        return False
    voice_id = _VOICES.get(speaker, _VOICES["narrator"])
    try:
        from elevenlabs.client import ElevenLabs
        client = ElevenLabs(api_key=api_key)
        audio = client.text_to_speech.convert(
            voice_id=voice_id,
            text=text,
            model_id="eleven_turbo_v2_5",
            output_format="mp3_44100_128",
        )
        with open(output_path, "wb") as f:
            for chunk in audio:
                f.write(chunk)
        print(f"[lumi] Voiceover ({speaker}): '{text[:40]}...'")
        return True
    except Exception as e:
        print(f"[lumi] ElevenLabs failed: {e}")
        return False


# ---------------------------------------------------------------------------
# 4. Scene clip (image + Ken Burns + audio -> MP4)
# ---------------------------------------------------------------------------

def _get_audio_duration(audio_path: Path) -> float:
    """Return audio duration in seconds via ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(audio_path)],
            capture_output=True, text=True, timeout=15,
        )
        info = json.loads(result.stdout)
        for stream in info.get("streams", []):
            dur = stream.get("duration")
            if dur:
                return float(dur)
    except Exception:
        pass
    return 4.0


def make_scene_clip(image_path: Path, audio_path: Path, motion: str, output_path: Path) -> bool:
    """
    Combine a still image (Ken Burns zoom/pan) with audio into a single scene MP4.
    Duration is driven by the audio length + 0.3s tail for natural pacing.
    """
    duration = _get_audio_duration(audio_path) + 0.3
    frames   = int(duration * LUMI_FPS)

    # Build zoompan expression — subtle motion keeps toddler attention without distraction
    if motion == "zoom_in":
        z = "'min(zoom+0.0010,1.08)'"
        x = "'iw/2-(iw/zoom/2)'"
        y = "'ih/2-(ih/zoom/2)'"
    elif motion == "zoom_out":
        z = f"'if(eq(on,1),1.08,max(zoom-0.0010,1.0))'"
        x = "'iw/2-(iw/zoom/2)'"
        y = "'ih/2-(ih/zoom/2)'"
    elif motion == "pan_right":
        z = "'1.05'"
        x = f"'min(on*2,iw/zoom-ow)'"
        y = "'ih/2-(ih/zoom/2)'"
    else:  # pan_left
        z = "'1.05'"
        x = f"'max(iw/zoom-on*2,0)'"
        y = "'ih/2-(ih/zoom/2)'"

    vf = (
        f"scale={LUMI_W * 2}:{LUMI_H * 2}:force_original_aspect_ratio=fill,"
        f"crop={LUMI_W * 2}:{LUMI_H * 2},"
        f"zoompan=z={z}:x={x}:y={y}:d={frames}:s={LUMI_W}x{LUMI_H}:fps={LUMI_FPS}"
    )

    cmd = [
        "ffmpeg",
        "-loop", "1", "-i", str(image_path),
        "-i",           str(audio_path),
        "-vf",          vf,
        "-map",         "0:v",
        "-map",         "1:a",
        "-c:v",         "libx264",
        "-preset",      "ultrafast",
        "-threads",     "1",
        "-c:a",         "aac",
        "-t",           str(duration),
        "-shortest",
        "-pix_fmt",     "yuv420p",
        "-y",           str(output_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=180)
        if result.returncode != 0:
            err = result.stderr.decode("utf-8", errors="ignore")[-400:]
            print(f"[lumi] ffmpeg scene {output_path.name} failed: {err}")
            return False
        print(f"[lumi] Scene clip: {output_path.name} ({duration:.1f}s, {motion})")
        return True
    except subprocess.TimeoutExpired:
        print(f"[lumi] ffmpeg timed out on {output_path.name}")
        return False
    except Exception as e:
        print(f"[lumi] Scene clip error: {e}")
        return False


# ---------------------------------------------------------------------------
# 5. Final assembly
# ---------------------------------------------------------------------------

def assemble_episode(clip_paths: list, output_path: Path) -> bool:
    """Concatenate all scene clips into the final episode MP4 via moviepy."""
    if not clip_paths:
        return False
    try:
        from moviepy.editor import VideoFileClip, concatenate_videoclips

        clips = []
        for p in clip_paths:
            try:
                clips.append(VideoFileClip(str(p)))
            except Exception as e:
                print(f"[lumi] Skipping {p}: {e}")

        if not clips:
            return False

        final = concatenate_videoclips(clips, method="compose")

        bg_music = os.environ.get("LUMI_BG_MUSIC_PATH", "")
        if bg_music and Path(bg_music).exists():
            try:
                from moviepy.editor import AudioFileClip, CompositeAudioClip
                import moviepy.audio.fx.all as afx
                music = AudioFileClip(bg_music).volumex(0.12)
                music = afx.audio_loop(music, duration=final.duration)
                final = final.set_audio(CompositeAudioClip([final.audio, music]))
                print("[lumi] Background music added")
            except Exception as e:
                print(f"[lumi] Music overlay skipped: {e}")

        final.write_videofile(
            str(output_path),
            fps=LUMI_FPS,
            codec="libx264",
            audio_codec="aac",
            preset="ultrafast",
            threads=1,
            logger=None,
        )
        for c in clips:
            c.close()
        print(f"[lumi] Episode assembled: {output_path.name}")
        return True
    except Exception as e:
        print(f"[lumi] Assembly failed: {e}")
        return False


# ---------------------------------------------------------------------------
# 6. Full pipeline
# ---------------------------------------------------------------------------

def build_episode(title: str, moral: str, app) -> bool:
    """
    Full Lumi Tales pipeline:
      script -> images + audio (parallel per scene) -> clips -> final video -> DB + upload queue
    """
    with app.app_context():
        from datetime import datetime
        from models import db, LumiStory, Niche, SocialAccount, ContentQueue, PipelineRun

        print(f"[lumi] ▶ Building episode: '{title}'")

        niche_obj = Niche.query.filter_by(name="kids", is_active=True).first()

        run = PipelineRun(
            niche="kids", status="running",
            started_at=datetime.utcnow(), note=f"lumi: {title}",
        )
        db.session.add(run)
        db.session.commit()

        story = LumiStory(
            character="Lumi", style="A",
            title=title, moral=moral, status="draft",
        )
        db.session.add(story)
        db.session.commit()

        work_dir = Path(tempfile.mkdtemp(prefix="lumi_"))
        try:
            # --- Script ---
            print("[lumi] Generating script...")
            script = generate_script(title, moral)
            if not script or not script.get("scenes"):
                _fail(run, story, db, "script generation failed")
                return False

            scenes = script["scenes"]
            story.script = scenes
            db.session.commit()
            print(f"[lumi] {len(scenes)} scenes planned")

            # --- Images + voiceovers (parallel per scene, DALL-E rate-limited) ---
            clip_paths = []
            for scene in scenes:
                sid        = scene["id"]
                dialogue   = scene.get("dialogue", "")
                speaker    = scene.get("speaker", "narrator")
                motion     = scene.get("motion", "zoom_in")
                img_prompt = scene.get("image_prompt", "")

                img_path   = work_dir / f"scene_{sid:02d}.png"
                audio_path = work_dir / f"scene_{sid:02d}.mp3"
                clip_path  = work_dir / f"clip_{sid:02d}.mp4"

                # Kick off DALL-E and ElevenLabs simultaneously
                results: dict = {}

                def _gen_img(prompt=img_prompt, s_id=sid, out_dir=work_dir, res=results):
                    res["img"] = generate_scene_image(prompt, s_id, out_dir)

                def _gen_audio(text=dialogue, spk=speaker, out=audio_path, res=results):
                    res["audio"] = generate_voiceover(text, spk, out)

                t1 = threading.Thread(target=_gen_img,   daemon=True)
                t2 = threading.Thread(target=_gen_audio, daemon=True)
                t1.start(); t2.start()
                t1.join(); t2.join()

                if results.get("img") is None:
                    print(f"[lumi] Scene {sid}: image failed — using placeholder")
                    _make_placeholder_image(img_path, sid)
                if not results.get("audio", False):
                    print(f"[lumi] Scene {sid}: audio failed — skipping scene")
                    time.sleep(12)
                    continue

                with _process_lock:
                    ok = make_scene_clip(img_path, audio_path, motion, clip_path)

                if ok:
                    clip_paths.append(clip_path)
                else:
                    print(f"[lumi] Scene {sid}: clip generation failed — skipping")

                # DALL-E standard tier: 5 requests/min — stay safely under
                time.sleep(12)

            if not clip_paths:
                _fail(run, story, db, "no scene clips generated")
                return False

            print(f"[lumi] {len(clip_paths)}/{len(scenes)} clips ready — assembling...")

            # --- Final assembly ---
            static_dir = Path(app.root_path) / "static" / "videos" / "lumi"
            static_dir.mkdir(parents=True, exist_ok=True)
            out_name = f"lumi_ep_{run.id}.mp4"
            out_path = static_dir / out_name

            with _process_lock:
                ok = assemble_episode(clip_paths, out_path)

            if not ok:
                _fail(run, story, db, "assembly failed")
                return False

            # --- Queue for upload ---
            base_url  = os.environ.get("BASE_URL", "https://content-distributor.onrender.com").rstrip("/")
            video_url = f"{base_url}/static/videos/lumi/{out_name}"
            lesson    = script.get("lesson_line", "")
            yt_desc   = (
                f"{title}\n\n{lesson}\n\n"
                "#LumiTales #KidsTV #LearnWithLumi #EducationalKids #Toddlers"
            )

            item = ContentQueue(
                niche_id       = niche_obj.id if niche_obj else None,
                video_url      = video_url,
                title          = title[:100],
                description    = yt_desc,
                platforms      = ["youtube"],
                use_opusclip   = False,
                status         = "pending",
                upload_results = {"moral": moral, "scenes": len(scenes), "clips_used": len(clip_paths)},
                clipped_urls   = [],
            )
            db.session.add(item)
            db.session.commit()

            story.status     = "ready"
            story.content_id = item.id
            run.status       = "completed"
            db.session.commit()

            # Trigger upload if accounts are configured
            if niche_obj:
                accounts = SocialAccount.query.filter_by(niche_id=niche_obj.id, is_active=True).all()
                if accounts:
                    from server import _run_job
                    _run_job(item.id, accounts, False, content_type="kids_video")

            print(f"[lumi] ✅ Episode complete — {len(clip_paths)} scenes, video: {out_name}")
            return True

        except Exception as e:
            _fail(run, story, db, str(e))
            print(f"[lumi] Build failed: {e}")
            return False
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


def _fail(run, story, db, note: str):
    run.status   = "failed"
    run.note     = note
    story.status = "failed"
    db.session.commit()


# ---------------------------------------------------------------------------
# 7. Non-blocking trigger
# ---------------------------------------------------------------------------

def trigger_episode(title: str, moral: str, app):
    """Start episode build in a background thread. Returns immediately."""
    t = threading.Thread(target=build_episode, args=(title, moral, app), daemon=True)
    t.start()
    return t
