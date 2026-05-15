"""
Google Cloud Text-to-Speech integration.
Uses the REST API with an API key — no service account needed.
Free tier: 1M characters/month for Neural2 voices.
Used for long-form narration to avoid burning ElevenLabs credits.
"""

import os
import base64
import tempfile
import requests

# Neural2 voices — high quality, natural sounding
VOICES = {
    "male":   "en-US-Neural2-D",
    "female": "en-US-Neural2-F",
}

# Niche → voice gender preference
NICHE_VOICE = {
    "trading":    "male",
    "fitness":    "male",
    "crime":      "female",
    "sports":     "male",
    "gaming":     "male",
    "everything": "male",
    "kids":       "female",
}


def generate_narration(text: str, niche: str = "everything") -> str | None:
    """
    Convert text to speech using Google Cloud TTS Neural2.
    Returns path to a temp MP3 file, or None on failure.
    """
    api_key = os.environ.get("GOOGLE_TTS_API_KEY")
    if not api_key:
        print("[google_tts] GOOGLE_TTS_API_KEY not set")
        return None

    gender    = NICHE_VOICE.get(niche, "male")
    voice_name = VOICES[gender]

    # Google TTS has a 5000 char limit per request — chunk if needed
    chunks     = _chunk_text(text, max_chars=4800)
    audio_data = b""

    for chunk in chunks:
        try:
            r = requests.post(
                f"https://texttospeech.googleapis.com/v1/text:synthesize?key={api_key}",
                json={
                    "input": {"text": chunk},
                    "voice": {
                        "languageCode": "en-US",
                        "name":         voice_name,
                    },
                    "audioConfig": {
                        "audioEncoding": "MP3",
                        "speakingRate":  1.0,
                        "pitch":         0.0,
                    },
                },
                timeout=30,
            )
            r.raise_for_status()
            audio_data += base64.b64decode(r.json()["audioContent"])
        except Exception as e:
            print(f"[google_tts] Chunk synthesis failed: {e}")
            return None

    if not audio_data:
        return None

    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp.write(audio_data)
    tmp.close()
    return tmp.name


def _chunk_text(text: str, max_chars: int = 4800) -> list[str]:
    """Split text into chunks at sentence boundaries."""
    if len(text) <= max_chars:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_chars:
            chunks.append(text)
            break
        # Find last sentence boundary before max_chars
        cut = text.rfind(". ", 0, max_chars)
        if cut == -1:
            cut = max_chars
        else:
            cut += 1  # include the period
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()
    return chunks
