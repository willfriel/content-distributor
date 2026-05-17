import os
from cryptography.fernet import Fernet


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Keep connection pool small to stay within Render Starter 512MB RAM.
    # pool_pre_ping recycles dead connections after server restarts.
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_size":    3,
        "max_overflow": 5,
        "pool_timeout": 20,
        "pool_pre_ping": True,
    }

    _raw_db_url = os.environ.get("DATABASE_URL", "sqlite:///content_distributor.db")
    if _raw_db_url.startswith("postgres://"):
        _raw_db_url = "postgresql+pg8000://" + _raw_db_url[len("postgres://"):]
    elif _raw_db_url.startswith("postgresql://"):
        _raw_db_url = "postgresql+pg8000://" + _raw_db_url[len("postgresql://"):]
    SQLALCHEMY_DATABASE_URI = _raw_db_url
    del _raw_db_url

    # Fernet key for encrypting stored credentials
    FERNET_KEY = os.environ.get("FERNET_KEY")

    # OpusClip
    OPUSCLIP_API_KEY = os.environ.get("OPUSCLIP_API_KEY")
    OPUSCLIP_BASE_URL = "https://api.opus.pro/v1"

    # YouTube
    YOUTUBE_CLIENT_ID = os.environ.get("YOUTUBE_CLIENT_ID")
    YOUTUBE_CLIENT_SECRET = os.environ.get("YOUTUBE_CLIENT_SECRET")
    # Set to your Render URL in production: https://your-app.onrender.com/auth/youtube/callback
    YOUTUBE_REDIRECT_URI = os.environ.get("YOUTUBE_REDIRECT_URI", "http://localhost:5000/auth/youtube/callback")

    # Instagram / Meta
    INSTAGRAM_APP_ID = os.environ.get("INSTAGRAM_APP_ID")
    INSTAGRAM_APP_SECRET = os.environ.get("INSTAGRAM_APP_SECRET")
    INSTAGRAM_REDIRECT_URI = os.environ.get("INSTAGRAM_REDIRECT_URI", "http://localhost:5000/auth/instagram/callback")

    # TikTok
    TIKTOK_CLIENT_KEY = os.environ.get("TIKTOK_CLIENT_KEY")
    TIKTOK_CLIENT_SECRET = os.environ.get("TIKTOK_CLIENT_SECRET")

    # Twitch
    TWITCH_CLIENT_ID = os.environ.get("TWITCH_CLIENT_ID")
    TWITCH_CLIENT_SECRET = os.environ.get("TWITCH_CLIENT_SECRET")

    # Pexels (free stock video)
    PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")

    # Google Cloud TTS (free longform narration — 1M chars/month free)
    GOOGLE_TTS_API_KEY = os.environ.get("GOOGLE_TTS_API_KEY")

    # Telegram (optional notifications)
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

    # Public base URL (used to generate /r/<slug> click-tracking links)
    BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000")

    @staticmethod
    def get_fernet():
        key = os.environ.get("FERNET_KEY")
        if not key:
            raise RuntimeError("FERNET_KEY environment variable is not set")
        return Fernet(key.encode() if isinstance(key, str) else key)

    @staticmethod
    def generate_fernet_key():
        """Run once to generate a key: python -c 'from config import Config; print(Config.generate_fernet_key())'"""
        return Fernet.generate_key().decode()
