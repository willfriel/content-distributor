"""
Horror / True-Crime story generator.
Builds a 9:16 short by compositing:
  - A brainrot background clip (looped, darkened, desaturated) from Reddit
  - ElevenLabs narration of an AI-written scary story
  - Subtitle word overlay synced to the narration
"""

import os
import subprocess
import tempfile


def generate_story_text(target_seconds: int = 75) -> tuple[str, str]:
    """Return (title, body) for a ~target_seconds horror/true-crime story."""
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    target_words = int(target_seconds * 2.3)
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=700,
        messages=[{
            "role": "user",
            "content": (
                f"Write a {target_words}-word true-crime or horror story for a social media short. "
                "The first sentence must be a hook that stops someone mid-scroll — visceral and shocking. "
                "Build suspense throughout. End on a chilling, unsettling note. "
                "Dark, specific, real-sounding details. No filler, no moralizing. "
                "Return ONLY the story text — no title, no headers, no quotation marks."
            ),
        }],
    )
    body  = resp.content[0].text.strip()
    title = body.split(".")[0].strip()[:80]
    return title, body


def _srt_timestamp(seconds: float) -> str:
    h  = int(seconds // 3600)
    m  = int((seconds % 3600) // 60)
    s  = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def story_to_srt(text: str, duration: float) -> str:
    """Divide story text into 2.5-second subtitle chunks timed to audio duration."""
    words      = text.split()
    chunk_size = max(4, int(len(words) / max(duration / 2.5, 1)))
    chunks     = [" ".join(words[i:i + chunk_size]) for i in range(0, len(words), chunk_size)]
    t_per      = duration / max(len(chunks), 1)
    srt        = ""
    for i, chunk in enumerate(chunks):
        s, e = i * t_per, (i + 1) * t_per
        srt += f"{i + 1}\n{_srt_timestamp(s)} --> {_srt_timestamp(e)}\n{chunk}\n\n"
    return srt


def get_media_duration(path: str) -> float:
    """Return duration in seconds via ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, timeout=15,
    )
    try:
        return float(result.stdout.strip())
    except Exception:
        return 60.0


def build_crime_short(brainrot_path: str, narration_path: str, story_text: str) -> str | None:
    """
    Composite a 9:16 horror short:
      - Background: brainrot clip looped, cropped to 720x1280, darkened + desaturated + vignette
      - Audio: narration at full volume + brainrot ambient at 8%
      - Subtitles: bold white text, centered vertically, synced to narration timing
    Returns output MP4 path or None on failure.
    """
    audio_dur = get_media_duration(narration_path)
    srt_text  = story_to_srt(story_text, audio_dur)

    pid       = os.getpid()
    tmp_dir   = tempfile.gettempdir()
    base_path = os.path.join(tmp_dir, f"crime_base_{pid}.mp4")
    srt_path  = os.path.join(tmp_dir, f"crime_subs_{pid}.srt")
    out_path  = os.path.join(tmp_dir, f"crime_short_{pid}.mp4")

    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(srt_text)

    try:
        # Pass 1: loop brainrot → 9:16 canvas → darken → mix audio
        cmd1 = [
            "ffmpeg",
            "-stream_loop", "-1", "-i", brainrot_path,   # 0: looped brainrot
            "-i", narration_path,                          # 1: narration
            "-filter_complex", (
                "[0:v]scale=720:1280:force_original_aspect_ratio=increase,"
                "crop=720:1280,"
                "eq=brightness=-0.3:saturation=0.45,"
                "vignette=PI/4[bg];"
                "[0:a]volume=0.08[bga];"
                "[1:a]volume=1.0[nar];"
                "[bga][nar]amix=inputs=2:duration=first[outa]"
            ),
            "-map", "[bg]", "-map", "[outa]",
            "-t", str(audio_dur + 0.5),
            "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1",
            "-b:v", "2000k", "-c:a", "aac", "-shortest", "-y", base_path,
        ]
        subprocess.run(cmd1, check=True, capture_output=True, timeout=180)

        # Pass 2: burn subtitles centered in frame
        cmd2 = [
            "ffmpeg", "-i", base_path,
            "-vf", (
                f"subtitles={srt_path}:force_style='"
                "Bold=1,FontSize=20,PrimaryColour=&H00FFFFFF,"
                "OutlineColour=&H00000000,BorderStyle=1,Outline=3,"
                "MarginV=490,Alignment=2'"
            ),
            "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1",
            "-c:a", "aac", "-y", out_path,
        ]
        subprocess.run(cmd2, check=True, capture_output=True, timeout=180)

        print(f"[crime_story] Built: {out_path}")
        return out_path

    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"").decode("utf-8", errors="ignore")[-400:]
        print(f"[crime_story] ffmpeg failed: {err}")
        return None
    except Exception as e:
        print(f"[crime_story] Build failed: {e}")
        return None
    finally:
        for p in [srt_path, base_path]:
            try:
                if os.path.exists(p):
                    os.unlink(p)
            except Exception:
                pass
