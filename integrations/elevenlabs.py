"""
ElevenLabs text-to-speech integration.
Uses epsilon-greedy learning: exploits the best-performing voice per niche
70% of the time, explores a random voice 30% of the time.
"""

import os
import random
import tempfile
from pathlib import Path


# All pre-made ElevenLabs voices available on every plan
VOICE_POOL = [
    {"id": "21m00Tcm4TlvDq8ikWAM", "name": "Rachel",   "style": "calm, clear"},
    {"id": "pNInz6obpgDQGcFmaJgB", "name": "Adam",     "style": "neutral, professional"},
    {"id": "ErXwobaYiN019PkySvjV", "name": "Antoni",   "style": "calm, authoritative"},
    {"id": "VR6AewLTigWG4xSOukaG", "name": "Arnold",   "style": "deep, energetic"},
    {"id": "N2lVS1w4EtoT3dr4eOWO", "name": "Callum",   "style": "expressive, dynamic"},
    {"id": "IKne3meq5aSn9XLyUdCD", "name": "Charlie",  "style": "Australian, casual"},
    {"id": "XB0fDUnXU5powFXDhCwa", "name": "Charlotte","style": "smooth, engaging"},
    {"id": "2EiwWnXFnvU5JabPnv8n", "name": "Clyde",    "style": "gravelly, storytelling"},
    {"id": "onwK4e9ZLuTAKqWW03F9", "name": "Daniel",   "style": "deep, serious, British"},
    {"id": "CYw3kZ78EXmEB2oT2iI3", "name": "Dave",     "style": "conversational, warm"},
    {"id": "AZnzlk1XvdvUeBnXmlld", "name": "Domi",     "style": "strong, confident"},
    {"id": "ThT5KcBeYPX3keUQqHPh", "name": "Dorothy",  "style": "pleasant, friendly"},
    {"id": "29vD33N1edd1MtEPfGiM", "name": "Drew",     "style": "journalist, trustworthy"},
    {"id": "LcfcDJNUP1GQjkzn1xUU", "name": "Emily",    "style": "calm, soothing"},
    {"id": "D38z5RcWu1voky8WS1ja", "name": "Fin",      "style": "rugged, grounded"},
    {"id": "jsCqWAovK2LkecY7zXl4", "name": "Freya",    "style": "American, upbeat"},
    {"id": "jBpfuIE2acCO8z3wKNLl", "name": "Gigi",     "style": "bright, friendly"},
    {"id": "oWAxZDx7w5VEj9dCyTzz", "name": "Grace",    "style": "Southern, warm"},
    {"id": "SOYHLrjzK2X1ezoPC6cr", "name": "Harry",    "style": "intense, dramatic"},
    {"id": "bVMeCyTHy58xNoL34h3p", "name": "Jeremy",   "style": "Irish, charming"},
    {"id": "TxGEqnHWrfWFTfGW9XjX", "name": "Josh",     "style": "deep, commanding"},
    {"id": "TX3LPaxmHKxFdv7VOQHJ", "name": "Liam",     "style": "narrative, thoughtful"},
    {"id": "pFZP5JQG7iQjIQuC4Bku", "name": "Lily",     "style": "British, refined"},
    {"id": "XrExE9yKIg1WjnnlVkGX", "name": "Matilda",  "style": "American, versatile"},
    {"id": "flq6f7yl4zymIzvniBLd", "name": "Michael",  "style": "rich, orotund"},
    {"id": "piTKgcLEGmPE4e6mEKli", "name": "Nicole",   "style": "soft, intimate"},
    {"id": "yoZ06aMxZJJ28mfd3POQ", "name": "Sam",      "style": "raspy, edgy"},
    {"id": "EXAVITQu4vr4xnSDxMaL", "name": "Sarah",    "style": "gentle, warm"},
    {"id": "pMsXgVXv3BLzUgSXRplE", "name": "Serena",   "style": "American, polished"},
    {"id": "GBv7mTt0atIp3Br8iCZE", "name": "Thomas",   "style": "meditative, calm"},
]

MODEL_ID      = "eleven_turbo_v2_5"   # fast + cheap; swap to eleven_multilingual_v2 for quality
EXPLOIT_RATE  = 0.70                   # use best known voice this % of the time
MIN_SAMPLES   = 3                      # min posts before trusting a voice's score


def pick_voice(niche: str, db_session=None) -> dict:
    """
    Epsilon-greedy voice selection.
    Exploits the top-performing voice for this niche (if we have enough data),
    otherwise explores a random voice from the full pool.
    """
    if db_session is not None and random.random() < EXPLOIT_RATE:
        try:
            from sqlalchemy import func
            from models import PostMetrics

            best = (
                db_session.query(
                    PostMetrics.voice_id,
                    PostMetrics.voice_name,
                    func.avg(PostMetrics.engagement_score).label("avg_eng"),
                    func.count(PostMetrics.id).label("cnt"),
                )
                .filter(
                    PostMetrics.niche == niche,
                    PostMetrics.voice_id.isnot(None),
                )
                .group_by(PostMetrics.voice_id, PostMetrics.voice_name)
                .having(func.count(PostMetrics.id) >= MIN_SAMPLES)
                .order_by(func.avg(PostMetrics.engagement_score).desc())
                .first()
            )
            if best:
                print(f"[elevenlabs] Exploiting best voice for {niche}: {best.voice_name} (avg_eng={best.avg_eng:.4f})")
                return {"id": best.voice_id, "name": best.voice_name}
        except Exception as e:
            print(f"[elevenlabs] pick_voice DB query failed: {e}")

    # Explore: random voice from pool
    voice = random.choice(VOICE_POOL)
    print(f"[elevenlabs] Exploring random voice for {niche}: {voice['name']}")
    return voice


def generate_voiceover(text: str, niche: str = "everything",
                       voice_id: str = None, db_session=None) -> tuple[str | None, str | None, str | None]:
    """
    Generate a voiceover MP3.
    Returns (file_path, voice_id, voice_name), or (None, None, None) on failure.
    """
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        print("[elevenlabs] ELEVENLABS_API_KEY not set — skipping voiceover")
        return None, None, None

    if voice_id:
        voice = next((v for v in VOICE_POOL if v["id"] == voice_id), {"id": voice_id, "name": "custom"})
    else:
        voice = pick_voice(niche, db_session=db_session)

    try:
        from elevenlabs.client import ElevenLabs

        client = ElevenLabs(api_key=api_key)
        audio  = client.text_to_speech.convert(
            voice_id      = voice["id"],
            text          = text,
            model_id      = MODEL_ID,
            output_format = "mp3_44100_128",
        )

        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.close()
        with open(tmp.name, "wb") as f:
            for chunk in audio:
                if chunk:
                    f.write(chunk)

        print(f"[elevenlabs] Voiceover saved: voice={voice['name']}, path={tmp.name}")
        return tmp.name, voice["id"], voice["name"]

    except Exception as e:
        print(f"[elevenlabs] Error generating voiceover ({type(e).__name__}): {e}")
        return None, None, None


def overlay_voiceover(video_path: str, audio_path: str, output_path: str = None) -> str | None:
    """
    Overlay a voiceover MP3 onto a video file using moviepy.
    Returns the output file path, or None on failure.
    """
    try:
        from moviepy.editor import VideoFileClip, AudioFileClip

        if output_path is None:
            tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
            tmp.close()
            output_path = tmp.name

        video     = VideoFileClip(video_path)
        voiceover = AudioFileClip(audio_path)
        voiceover = voiceover.subclip(0, min(voiceover.duration, video.duration))
        video     = video.set_audio(voiceover)
        video.write_videofile(output_path, codec="libx264", audio_codec="aac", logger=None)
        video.close()
        voiceover.close()

        print(f"[elevenlabs] Video with voiceover saved: {output_path}")
        return output_path

    except Exception as e:
        print(f"[elevenlabs] Error overlaying voiceover: {e}")
        return None


def get_voice_insights(niche: str, db_session) -> list[dict]:
    """
    Return voice performance ranking for a niche (for the insights dashboard).
    """
    try:
        from sqlalchemy import func
        from models import PostMetrics

        rows = (
            db_session.query(
                PostMetrics.voice_id,
                PostMetrics.voice_name,
                func.avg(PostMetrics.engagement_score).label("avg_eng"),
                func.avg(PostMetrics.views).label("avg_views"),
                func.count(PostMetrics.id).label("posts"),
            )
            .filter(PostMetrics.niche == niche, PostMetrics.voice_id.isnot(None))
            .group_by(PostMetrics.voice_id, PostMetrics.voice_name)
            .order_by(func.avg(PostMetrics.engagement_score).desc())
            .all()
        )
        return [
            {
                "voice_id":      r.voice_id,
                "voice_name":    r.voice_name,
                "avg_engagement": round(r.avg_eng or 0, 4),
                "avg_views":     round(r.avg_views or 0),
                "posts":         r.posts,
            }
            for r in rows
        ]
    except Exception as e:
        print(f"[elevenlabs] get_voice_insights error: {e}")
        return []
