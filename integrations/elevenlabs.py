"""
ElevenLabs text-to-speech integration.
Generates AI voiceovers for pipeline videos.
"""

import os
import tempfile
from pathlib import Path


VOICE_IDS = {
    "trading":    "ErXwobaYiN019PkySvjV",  # Antoni — calm, authoritative
    "fitness":    "VR6AewLTigWG4xSOukaG",  # Arnold — energetic
    "crime":      "onwK4e9ZLuTAKqWW03F9",  # Daniel — deep, serious
    "sports":     "N2lVS1w4EtoT3dr4eOWO",  # Callum — excited
    "anatomy":    "ErXwobaYiN019PkySvjV",  # Antoni — clear, educational
    "everything": "pNInz6obpgDQGcFmaJgB",  # Adam — neutral
    "kids":       "jBpfuIE2acCO8z3wKNLl",  # Gigi — friendly
}

MODEL_ID = "eleven_turbo_v2_5"  # fast + cheap; swap to eleven_multilingual_v2 for quality


def generate_voiceover(text: str, niche: str = "everything", output_path: str = None) -> str | None:
    """
    Generate a voiceover MP3 for the given text.
    Returns the file path, or None on failure.
    """
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        print("[elevenlabs] ELEVENLABS_API_KEY not set — skipping voiceover")
        return None

    try:
        from elevenlabs.client import ElevenLabs
        from elevenlabs import save

        client    = ElevenLabs(api_key=api_key)
        voice_id  = VOICE_IDS.get(niche, VOICE_IDS["everything"])

        audio = client.text_to_speech.convert(
            voice_id        = voice_id,
            text            = text,
            model_id        = MODEL_ID,
            output_format   = "mp3_44100_128",
        )

        if output_path is None:
            tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            tmp.close()
            output_path = tmp.name

        save(audio, output_path)
        print(f"[elevenlabs] Voiceover saved to {output_path}")
        return output_path

    except Exception as e:
        print(f"[elevenlabs] Error generating voiceover: {e}")
        return None


def overlay_voiceover(video_path: str, audio_path: str, output_path: str = None) -> str | None:
    """
    Overlay a voiceover MP3 onto a video file using moviepy.
    Returns the output file path, or None on failure.
    """
    try:
        from moviepy.editor import VideoFileClip, AudioFileClip, CompositeAudioClip

        if output_path is None:
            tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
            tmp.close()
            output_path = tmp.name

        video     = VideoFileClip(video_path)
        voiceover = AudioFileClip(audio_path).subclip(0, min(AudioFileClip(audio_path).duration, video.duration))
        video     = video.set_audio(voiceover)
        video.write_videofile(output_path, codec="libx264", audio_codec="aac", logger=None)
        video.close()

        print(f"[elevenlabs] Video with voiceover saved to {output_path}")
        return output_path

    except Exception as e:
        print(f"[elevenlabs] Error overlaying voiceover: {e}")
        return None
